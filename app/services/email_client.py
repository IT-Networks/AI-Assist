"""
Exchange E-Mail Client - EWS-Anbindung via exchangelib (NTLM).

Bietet async Wrapper um die synchrone exchangelib-Bibliothek.
Alle EWS-Operationen laufen in run_in_executor um den Event-Loop nicht zu blockieren.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)

# Lazy imports für exchangelib (nur wenn benötigt)
_exchangelib_available = None


def _get_ews_timezone():
    """Robuste Ermittlung der lokalen EWS-Timezone mit Fallback."""
    from exchangelib import EWSTimeZone
    try:
        return EWSTimeZone.localzone()
    except Exception:
        pass
    # Fallback: Europe/Berlin oder UTC
    try:
        return EWSTimeZone('Europe/Berlin')
    except Exception:
        return EWSTimeZone('UTC')


def _check_exchangelib():
    global _exchangelib_available
    if _exchangelib_available is None:
        try:
            import exchangelib  # noqa: F401
            _exchangelib_available = True
        except ImportError:
            _exchangelib_available = False
    return _exchangelib_available


def _get_credentials():
    """
    Holt NTLM-Credentials aus Config (credential_ref oder direkt).

    Returns:
        (ntlm_username, password) — ntlm_username ist DOMAIN\\user Format
    """
    from app.core.config import settings

    cfg = settings.email
    username = cfg.username
    password = cfg.password
    domain = cfg.domain

    if cfg.credential_ref:
        cred = settings.credentials.get(cfg.credential_ref)
        if cred:
            if cred.username:
                username = cred.username
            password = cred.password or cred.token
            logger.debug("Email: Verwende credential_ref '%s'", cfg.credential_ref)

    # NTLM erwartet DOMAIN\username
    if domain and username:
        ntlm_user = f"{domain}\\{username}"
    elif username:
        ntlm_user = username
    else:
        ntlm_user = cfg.smtp_address  # Fallback: E-Mail als UPN

    return ntlm_user, password


class ExchangeEmailClient:
    """Wrapper um exchangelib für EWS-Operationen."""

    def __init__(self):
        self._account = None
        self._connected = False

    async def connect(self) -> bool:
        """Verbindung zum Exchange Server herstellen."""
        if not _check_exchangelib():
            raise RuntimeError("exchangelib ist nicht installiert. Bitte 'pip install exchangelib' ausführen.")

        from app.core.config import settings
        cfg = settings.email

        if not cfg.ews_url or not cfg.smtp_address:
            raise ValueError("EWS-URL und SMTP-Adresse müssen konfiguriert sein.")

        ntlm_user, password = _get_credentials()
        if not password:
            raise ValueError("Kein Passwort konfiguriert (weder credential_ref noch direkt).")

        loop = asyncio.get_event_loop()
        self._account = await loop.run_in_executor(None, lambda: self._connect_sync(
            ews_url=cfg.ews_url,
            smtp_address=cfg.smtp_address,
            ntlm_user=ntlm_user,
            password=password,
            verify_ssl=cfg.verify_ssl,
        ))
        self._connected = True
        logger.info("Email: Verbunden als %s (NTLM: %s)", cfg.smtp_address, ntlm_user)
        return True

    def _connect_sync(self, ews_url: str, smtp_address: str, ntlm_user: str, password: str, verify_ssl: bool):
        """Synchrone Verbindung (läuft in Executor)."""
        from exchangelib import (
            Credentials, Configuration, Account,
            DELEGATE, NTLM
        )
        from exchangelib.protocol import BaseProtocol
        import urllib3

        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            BaseProtocol.HTTP_ADAPTER_CLS = _get_no_verify_adapter()

        credentials = Credentials(username=ntlm_user, password=password)

        # exchangelib erlaubt nur server ODER service_endpoint, nicht beides.
        # Wenn die URL ein Pfad enthält (z.B. /EWS/Exchange.asmx) → service_endpoint
        # Wenn nur Hostname → server (exchangelib baut die URL selbst)
        from urllib.parse import urlparse
        parsed = urlparse(ews_url if '://' in ews_url else f'https://{ews_url}')
        has_path = parsed.path and parsed.path not in ('', '/')

        if has_path:
            # Vollständige EWS-URL angegeben
            config = Configuration(
                credentials=credentials,
                auth_type=NTLM,
                service_endpoint=ews_url if '://' in ews_url else f'https://{ews_url}',
            )
        else:
            # Nur Hostname angegeben — exchangelib baut /EWS/Exchange.asmx selbst
            config = Configuration(
                server=parsed.hostname,
                credentials=credentials,
                auth_type=NTLM,
            )
        account = Account(
            primary_smtp_address=smtp_address,
            config=config,
            autodiscover=False,
            access_type=DELEGATE,
        )
        return account

    async def _ensure_connected(self):
        """Stellt sicher, dass eine Verbindung besteht."""
        if not self._connected or self._account is None:
            await self.connect()

    async def test_connection(self) -> Dict[str, Any]:
        """Testet die Verbindung zum Exchange Server."""
        try:
            await self.connect()
            loop = asyncio.get_event_loop()
            inbox_count = await loop.run_in_executor(
                None, lambda: self._account.inbox.total_count
            )
            return {
                "success": True,
                "message": f"Verbunden als {self._account.primary_smtp_address}. Inbox: {inbox_count} E-Mails."
            }
        except Exception as e:
            logger.error("Email Verbindungstest fehlgeschlagen: %s", e)
            return {"success": False, "error": str(e)}

    async def list_folders(self) -> List[Dict[str, Any]]:
        """Listet alle E-Mail-Ordner auf."""
        await self._ensure_connected()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_folders_sync)

    def _list_folders_sync(self) -> List[Dict[str, Any]]:
        from exchangelib import Folder
        folders = []
        for folder in self._account.root.walk():
            if getattr(folder, 'folder_class', None) == 'IPF.Note':
                try:
                    folders.append({
                        "name": folder.name,
                        "path": str(folder),
                        "count": folder.total_count or 0,
                        "unread": folder.unread_count or 0,
                    })
                except Exception:
                    pass
        return folders

    async def search_emails(
        self,
        query: str = "",
        sender: str = "",
        subject: str = "",
        folder: str = "inbox",
        date_from: str = "",
        date_to: str = "",
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Sucht E-Mails mit Filtern."""
        await self._ensure_connected()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._search_emails_sync(query, sender, subject, folder, date_from, date_to, limit)
        )

    def _search_emails_sync(
        self, query, sender, subject, folder_name, date_from, date_to, limit
    ) -> Tuple[List[Dict[str, Any]], int]:
        from exchangelib import Q
        from exchangelib.items import Message

        target_folder = self._get_folder(folder_name)
        if target_folder is None:
            return [], 0

        # Filter aufbauen
        q_filter = None

        if sender:
            q_filter = self._and_q(q_filter, Q(sender__contains=sender))
        if subject:
            q_filter = self._and_q(q_filter, Q(subject__contains=subject))
        if date_from:
            try:
                dt = datetime.fromisoformat(date_from)
                from exchangelib import EWSDateTime
                tz = _get_ews_timezone()
                q_filter = self._and_q(q_filter, Q(datetime_received__gte=EWSDateTime.from_datetime(dt).astimezone(tz)))
            except (ValueError, TypeError):
                pass
        if date_to:
            try:
                dt = datetime.fromisoformat(date_to)
                from exchangelib import EWSDateTime
                tz = _get_ews_timezone()
                q_filter = self._and_q(q_filter, Q(datetime_received__lte=EWSDateTime.from_datetime(dt).astimezone(tz)))
            except (ValueError, TypeError):
                pass
        if query:
            q_filter = self._and_q(q_filter, Q(body__contains=query) | Q(subject__contains=query))

        if q_filter:
            qs = target_folder.filter(q_filter).only(
                'id', 'subject', 'sender', 'datetime_received',
                'has_attachments', 'attachments'
            ).order_by('-datetime_received')
        else:
            qs = target_folder.all().only(
                'id', 'subject', 'sender', 'datetime_received',
                'has_attachments', 'attachments'
            ).order_by('-datetime_received')

        total = qs.count()
        results = []
        for item in qs[:limit]:
            if not isinstance(item, Message):
                continue
            attachment_count = 0
            try:
                attachment_count = len(item.attachments) if item.attachments else 0
            except Exception:
                pass
            results.append({
                "email_id": item.id,
                "subject": item.subject or "(Kein Betreff)",
                "sender": str(item.sender.email_address) if item.sender else "",
                "sender_name": str(item.sender.name) if item.sender and item.sender.name else "",
                "date": item.datetime_received.isoformat() if item.datetime_received else "",
                "preview": self._get_preview(item),
                "folder": folder_name,
                "has_attachments": bool(item.has_attachments),
                "attachment_count": attachment_count,
            })

        return results, total

    def _get_preview(self, item, max_len: int = 150) -> str:
        """Extrahiert Text-Vorschau aus einer E-Mail."""
        try:
            if item.text_body:
                text = item.text_body.strip()
            elif item.body:
                from bs4 import BeautifulSoup
                text = BeautifulSoup(str(item.body), 'html.parser').get_text(separator=' ', strip=True)
            else:
                return ""
            return text[:max_len] + "..." if len(text) > max_len else text
        except Exception:
            return ""

    async def read_email(self, email_id: str, folder: str = "inbox") -> Dict[str, Any]:
        """Liest eine einzelne E-Mail mit vollem Body."""
        await self._ensure_connected()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._read_email_sync(email_id, folder)
        )

    def _read_email_sync(self, email_id: str, folder_name: str) -> Dict[str, Any]:
        from exchangelib.items import Message
        from exchangelib.properties import ItemId

        target_folder = self._get_folder(folder_name)

        # Per ItemId direkt laden (filter(id=) wird von EWS nicht unterstützt)
        items = list(self._account.fetch(ids=[ItemId(id=email_id)]))
        if not items or items[0] is None:
            raise ValueError(f"E-Mail mit ID '{email_id}' nicht gefunden.")

        item = items[0]

        # Attachments sammeln
        attachments = []
        if item.attachments:
            for att in item.attachments:
                attachments.append({
                    "name": att.name or "unnamed",
                    "size": att.size or 0,
                    "content_type": getattr(att, 'content_type', '') or "",
                })

        body_html = ""
        body_text = ""
        if item.body:
            body_html = str(item.body)
        if item.text_body:
            body_text = item.text_body
        elif body_html:
            from bs4 import BeautifulSoup
            body_text = BeautifulSoup(body_html, 'html.parser').get_text(separator='\n', strip=True)

        # Thread-Info
        search_folder = target_folder or self._account.inbox
        thread_info = self._get_thread_info(item, search_folder)

        return {
            "email_id": item.id,
            "subject": item.subject or "(Kein Betreff)",
            "sender": str(item.sender.email_address) if item.sender else "",
            "sender_name": str(item.sender.name) if item.sender and item.sender.name else "",
            "to": [str(r.email_address) for r in (item.to_recipients or [])],
            "cc": [str(r.email_address) for r in (item.cc_recipients or [])],
            "date": item.datetime_received.isoformat() if item.datetime_received else "",
            "body_html": body_html,
            "body_text": body_text,
            "folder": folder_name,
            "attachments": attachments,
            "is_read": item.is_read or False,
            "importance": str(item.importance) if item.importance else "normal",
            "thread": thread_info,
        }

    async def get_attachment(self, email_id: str, attachment_name: str, folder: str = "inbox") -> Tuple[bytes, str]:
        """Lädt ein Attachment herunter. Returns (content_bytes, content_type)."""
        await self._ensure_connected()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._get_attachment_sync(email_id, attachment_name, folder)
        )

    def _get_attachment_sync(self, email_id: str, attachment_name: str, folder_name: str) -> Tuple[bytes, str]:
        from exchangelib import FileAttachment
        from exchangelib.properties import ItemId

        items = list(self._account.fetch(ids=[ItemId(id=email_id)]))
        if not items or items[0] is None:
            raise ValueError(f"E-Mail mit ID '{email_id}' nicht gefunden.")

        item = items[0]
        if item.attachments:
            for att in item.attachments:
                if isinstance(att, FileAttachment) and att.name == attachment_name:
                    return att.content, getattr(att, 'content_type', 'application/octet-stream') or 'application/octet-stream'

        raise ValueError(f"Attachment '{attachment_name}' nicht gefunden.")

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to_id: str = "",
    ) -> Dict[str, Any]:
        """Erstellt einen Entwurf im Drafts-Ordner."""
        await self._ensure_connected()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._create_draft_sync(to, subject, body, reply_to_id)
        )

    def _create_draft_sync(self, to: str, subject: str, body: str, reply_to_id: str) -> Dict[str, Any]:
        from exchangelib import Message as EWSMessage, Mailbox, HTMLBody

        recipients = [Mailbox(email_address=addr.strip()) for addr in to.split(",") if addr.strip()]

        msg = EWSMessage(
            account=self._account,
            subject=subject,
            body=HTMLBody(body) if "<" in body and ">" in body else body,
            to_recipients=recipients,
        )

        if reply_to_id:
            try:
                from exchangelib.properties import ItemId
                items = list(self._account.fetch(ids=[ItemId(id=reply_to_id)]))
                if items and items[0] is not None:
                    msg.in_reply_to = items[0].message_id
            except Exception:
                pass

        msg.folder = self._account.drafts
        msg.save()

        return {
            "success": True,
            "draft_id": msg.id,
            "message": f"Entwurf '{subject}' erstellt ({len(recipients)} Empfänger)."
        }

    async def get_new_emails_since(
        self, since: datetime, folder: str = "all", limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Holt neue E-Mails seit einem Zeitstempel (für Polling).
        folder='all' durchsucht alle Mail-Ordner (für Exchange-Regeln die Mails verschieben).
        """
        await self._ensure_connected()
        loop = asyncio.get_event_loop()
        if folder == "all":
            return await loop.run_in_executor(
                None, lambda: self._get_new_emails_all_folders_sync(since, limit)
            )
        return await loop.run_in_executor(
            None, lambda: self._get_new_emails_since_sync(since, folder, limit)
        )

    def _get_new_emails_since_sync(
        self, since: datetime, folder_name: str, limit: int
    ) -> List[Dict[str, Any]]:
        target_folder = self._get_folder(folder_name)
        if target_folder is None:
            return []
        return self._fetch_emails_from_folder(target_folder, since, limit)

    def _fetch_emails_from_folder(self, target_folder, since: datetime, limit: int) -> List[Dict[str, Any]]:
        """Holt E-Mails (inkl. gelesene) aus einem Folder seit einem Zeitstempel."""
        from exchangelib import Q, EWSDateTime
        from exchangelib.items import Message

        tz = _get_ews_timezone()
        # Sicherstellen dass since timezone-aware ist
        if since.tzinfo is None:
            from exchangelib import EWSTimeZone
            since = since.replace(tzinfo=tz)
        since_ews = EWSDateTime.from_datetime(since).astimezone(tz)

        qs = target_folder.filter(
            Q(datetime_received__gte=since_ews)
        ).order_by('-datetime_received')[:limit]

        results = []
        for item in qs:
            if not isinstance(item, Message):
                continue

            body_text = ""
            try:
                if item.text_body:
                    body_text = item.text_body
                elif item.body:
                    from bs4 import BeautifulSoup
                    body_text = BeautifulSoup(str(item.body), 'html.parser').get_text(separator='\n', strip=True)
            except Exception:
                pass

            attachments = []
            if item.attachments:
                for att in item.attachments:
                    attachments.append({
                        "name": att.name or "unnamed",
                        "size": att.size or 0,
                        "content_type": getattr(att, 'content_type', '') or "",
                    })

            # Thread-Info für Automation
            thread_info = self._get_thread_info(item, target_folder)

            results.append({
                "email_id": item.id,
                "subject": item.subject or "(Kein Betreff)",
                "sender": str(item.sender.email_address) if item.sender else "",
                "sender_name": str(item.sender.name) if item.sender and item.sender.name else "",
                "date": item.datetime_received.isoformat() if item.datetime_received else "",
                "body_text": body_text,
                "body_html": str(item.body) if item.body else "",
                "to": [str(r.email_address) for r in (item.to_recipients or [])],
                "cc": [str(r.email_address) for r in (item.cc_recipients or [])],
                "attachments": attachments,
                "thread": thread_info,
            })

        return results

    def _get_new_emails_all_folders_sync(self, since: datetime, limit: int) -> List[Dict[str, Any]]:
        """Durchsucht alle Mail-Ordner nach neuen Mails seit einem Zeitstempel."""
        all_results = []
        seen_ids = set()

        logger.info("Durchsuche alle Ordner nach Mails seit %s (Limit: %d)", since.isoformat(), limit)

        for folder in self._account.root.walk():
            # Nur Mail-Ordner (IPF.Note), keine Kalender/Kontakte/Aufgaben
            if getattr(folder, 'folder_class', None) != 'IPF.Note':
                continue
            try:
                # total_count kann None sein bei manchen Ordnern — trotzdem durchsuchen
                count = getattr(folder, 'total_count', None)
                if count is not None and count == 0:
                    continue
                # Folder-Objekt direkt nutzen (nicht per Name suchen)
                remaining = limit - len(all_results)
                if remaining <= 0:
                    break
                results = self._fetch_emails_from_folder(folder, since, remaining)
                logger.debug("Ordner '%s': %d Mails gefunden (total_count=%s)", folder.name, len(results), count)
                for r in results:
                    eid = r.get("email_id", "")
                    if eid and eid not in seen_ids:
                        seen_ids.add(eid)
                        all_results.append(r)
                if len(all_results) >= limit:
                    break
            except Exception as e:
                logger.debug("Ordner '%s' übersprungen: %s", folder.name, e)
                continue

        # Nach Datum sortieren (neueste zuerst)
        all_results.sort(key=lambda x: x.get("date", ""), reverse=True)
        logger.info("Alle Ordner durchsucht: %d Mails gefunden", len(all_results))
        return all_results[:limit]

    # ── Hilfsmethoden ──────────────────────────────────────────────────────────

    # Betreff-Prefixes für Antworten und Weiterleitungen (international)
    _REPLY_PREFIXES = ("re:", "aw:", "re[", "aw[", "sv:", "odp:", "ref:")
    _FORWARD_PREFIXES = ("fw:", "fwd:", "wg:", "wg[", "tr:", "rv:", "enc:", "i:", "fs:", "vl:")

    def _get_thread_info(self, item, folder) -> Dict[str, Any]:
        """Prüft ob es im selben Thread Antworten oder Weiterleitungen gibt.
        Durchsucht den aktuellen Ordner UND den Gesendet-Ordner."""
        from exchangelib.items import Message

        info = {
            "has_replies": False,
            "has_forwards": False,
            "reply_count": 0,
            "forward_count": 0,
            "thread_messages": [],
        }

        try:
            conv_id = getattr(item, 'conversation_id', None)
            if not conv_id:
                return info

            # Durchsuche mehrere Ordner: aktueller Ordner + Gesendet
            folders_to_search = [folder]
            try:
                sent = self._account.sent
                if sent and sent != folder:
                    folders_to_search.append(sent)
            except Exception:
                pass

            seen_ids = {item.id}

            for search_folder in folders_to_search:
                try:
                    thread = search_folder.filter(conversation_id=conv_id).only(
                        'id', 'subject', 'sender', 'datetime_received'
                    ).order_by('datetime_received')

                    for msg in thread:
                        if not isinstance(msg, Message) or msg.id in seen_ids:
                            continue
                        seen_ids.add(msg.id)

                        subj = (msg.subject or "").lower().strip()
                        sender = str(msg.sender.email_address) if msg.sender else ""
                        entry = {
                            "subject": msg.subject or "",
                            "sender": sender,
                            "date": msg.datetime_received.isoformat() if msg.datetime_received else "",
                        }

                        if subj.startswith(self._REPLY_PREFIXES):
                            info["has_replies"] = True
                            info["reply_count"] += 1
                            entry["type"] = "reply"
                        elif subj.startswith(self._FORWARD_PREFIXES):
                            info["has_forwards"] = True
                            info["forward_count"] += 1
                            entry["type"] = "forward"
                        else:
                            entry["type"] = "related"

                        info["thread_messages"].append(entry)
                except Exception as e:
                    logger.debug("Thread-Suche in '%s' fehlgeschlagen: %s",
                                 getattr(search_folder, 'name', '?'), e)
        except Exception as e:
            logger.debug("Thread-Info Fehler: %s", e)

        return info

    def _get_folder(self, folder_name: str):
        """Gibt den Ordner anhand des Namens zurück."""
        name = folder_name.lower().strip()
        mapping = {
            "inbox": self._account.inbox,
            "posteingang": self._account.inbox,
            "sent": self._account.sent,
            "gesendet": self._account.sent,
            "drafts": self._account.drafts,
            "entwürfe": self._account.drafts,
            "trash": self._account.trash,
            "papierkorb": self._account.trash,
            "junk": self._account.junk,
        }
        if name in mapping:
            return mapping[name]

        # Suche in allen Ordnern
        for folder in self._account.root.walk():
            if folder.name and folder.name.lower() == name:
                return folder

        return None

    @staticmethod
    def _and_q(existing, new_q):
        """Verknüpft zwei Q-Objekte mit AND."""
        if existing is None:
            return new_q
        return existing & new_q


def _get_no_verify_adapter():
    """Erstellt einen HTTP-Adapter der SSL-Prüfung deaktiviert."""
    from exchangelib.protocol import NoVerifyHTTPAdapter
    return NoVerifyHTTPAdapter


# ── Singleton ──────────────────────────────────────────────────────────────────

_email_client: Optional[ExchangeEmailClient] = None


def get_email_client() -> ExchangeEmailClient:
    """Gibt den Singleton Email-Client zurück."""
    global _email_client
    if _email_client is None:
        _email_client = ExchangeEmailClient()
    return _email_client


async def close_email_client():
    """Schließt den Email-Client (für Shutdown)."""
    global _email_client
    if _email_client is not None:
        _email_client._account = None
        _email_client._connected = False
        _email_client = None
        logger.info("Email-Client geschlossen")
