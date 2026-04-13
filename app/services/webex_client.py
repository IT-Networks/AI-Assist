"""
Webex Messaging Client - HTTP-Client für die Webex REST API.

Unterstützt OAuth2 Authorization Code Flow mit automatischem Token-Refresh.
Nutzt httpx für async HTTP-Aufrufe mit Proxy- und Rate-Limit-Support.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# Webex OAuth2 Endpoints
WEBEX_AUTH_URL = "https://webexapis.com/v1/authorize"
WEBEX_TOKEN_URL = "https://webexapis.com/v1/access_token"


_TOKEN_FILE = Path(__file__).parent.parent.parent / "webex_tokens.json"


def _apply_token_data(token_data: dict) -> None:
    """Wendet Token-Daten auf Settings an und speichert in webex_tokens.json."""
    from app.core.config import settings
    import json

    settings.webex.access_token = token_data["access_token"]
    if token_data.get("refresh_token"):
        settings.webex.refresh_token = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 1209600)  # Default 14 Tage
    settings.webex.token_expires_at = (
        datetime.now() + timedelta(seconds=expires_in)
    ).isoformat()

    # In separate Token-Datei speichern (nicht config.yaml überschreiben)
    try:
        _TOKEN_FILE.write_text(json.dumps({
            "access_token": settings.webex.access_token,
            "refresh_token": settings.webex.refresh_token,
            "token_expires_at": settings.webex.token_expires_at,
        }, indent=2), encoding="utf-8")
        logger.debug("Webex-Tokens in webex_tokens.json gespeichert")
    except Exception as e:
        logger.warning("Webex-Tokens in Memory gesetzt, Datei-Speichern fehlgeschlagen: %s", e)


def _load_persisted_tokens() -> None:
    """Lädt Token aus webex_tokens.json in Settings.

    webex_tokens.json ist die Quelle der Wahrheit für Tokens (nicht config.yaml),
    da config.yaml die Tokens nicht speichert um Überschreiben zu vermeiden.
    """
    import json
    from app.core.config import settings

    if not _TOKEN_FILE.exists():
        return

    try:
        data = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        # Token-Datei hat immer Vorrang (enthält die aktuellsten Tokens)
        if data.get("access_token"):
            settings.webex.access_token = data["access_token"]
        if data.get("refresh_token"):
            settings.webex.refresh_token = data["refresh_token"]
        if data.get("token_expires_at"):
            settings.webex.token_expires_at = data["token_expires_at"]
        logger.debug("Webex-Tokens aus webex_tokens.json geladen")
    except Exception as e:
        logger.warning("Webex-Tokens laden fehlgeschlagen: %s", e)


class WebexClient:
    """Async HTTP-Client für Webex REST API mit OAuth2-Support."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Gibt den HTTP-Client zurück (Lazy Init)."""
        if self._client is None or self._client.is_closed:
            from app.core.config import settings

            # Token holen (ggf. refreshen)
            token = self._get_token()
            if not token:
                raise ValueError(
                    "Kein Webex Access-Token vorhanden. "
                    "Bitte OAuth-Anmeldung über Settings durchführen."
                )

            # Proxy-Konfiguration (zentraler Proxy)
            proxy = None
            if settings.webex.use_proxy and settings.proxy.enabled:
                proxy = settings.proxy.get_proxy_url()

            self._client = httpx.AsyncClient(
                base_url=settings.webex.base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=settings.webex.timeout_seconds,
                verify=settings.webex.verify_ssl,
                proxy=proxy,
            )
        return self._client

    def _get_token(self) -> str:
        """Holt den Access-Token. Reihenfolge:

        1. webex_tokens.json (OAuth-Tokens, automatisch verwaltet)
        2. settings.webex.access_token (manuell eingetragener Bearer-Token)

        Bei OAuth-Tokens: Auto-Refresh wenn abgelaufen.
        """
        from app.core.config import settings

        # Persistierte OAuth-Tokens laden
        _load_persisted_tokens()

        # Prüfe ob OAuth-Token abgelaufen → Refresh
        if settings.webex.access_token and settings.webex.token_expires_at:
            try:
                expires = datetime.fromisoformat(settings.webex.token_expires_at)
                if datetime.now() >= expires - timedelta(minutes=10):
                    if settings.webex.refresh_token and settings.webex.client_id:
                        logger.info("Webex Access-Token abgelaufen, refreshe...")
                        self._refresh_token_sync()
            except (ValueError, TypeError):
                pass

        return settings.webex.access_token

    def _refresh_token_sync(self) -> bool:
        """Synchroner Token-Refresh (für Lazy-Init im _get_client).

        Nutzt sync httpx.Client in einem Thread, um den Event-Loop nicht zu blockieren.
        """
        from app.core.config import settings

        if not settings.webex.refresh_token or not settings.webex.client_id:
            return False

        proxy = None
        if settings.webex.use_proxy and settings.proxy.enabled:
            proxy = settings.proxy.get_proxy_url()

        try:
            with httpx.Client(
                timeout=30,
                verify=settings.webex.verify_ssl,
                proxy=proxy,
            ) as client:
                response = client.post(
                    WEBEX_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": settings.webex.client_id,
                        "client_secret": settings.webex.client_secret,
                        "refresh_token": settings.webex.refresh_token,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                token_data = response.json()

                _apply_token_data(token_data)

                # Alten Client synchron schließen (nicht fire-and-forget)
                if self._client and not self._client.is_closed:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._client.aclose())
                    except RuntimeError:
                        pass  # Kein laufender Loop → Client wird beim nächsten _get_client ersetzt
                    self._client = None

                logger.info("Webex Token erfolgreich erneuert (gültig %d Sek.)",
                            token_data.get("expires_in", 0))
                return True

        except Exception as e:
            logger.error("Webex Token-Refresh fehlgeschlagen: %s", e)
            return False

    async def refresh_token(self) -> bool:
        """Async Token-Refresh."""
        from app.core.config import settings

        if not settings.webex.refresh_token or not settings.webex.client_id:
            return False

        proxy = None
        if settings.webex.use_proxy and settings.proxy.enabled:
            proxy = settings.proxy.get_proxy_url()

        try:
            async with httpx.AsyncClient(
                timeout=30,
                verify=settings.webex.verify_ssl,
                proxy=proxy,
            ) as client:
                response = await client.post(
                    WEBEX_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": settings.webex.client_id,
                        "client_secret": settings.webex.client_secret,
                        "refresh_token": settings.webex.refresh_token,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                token_data = response.json()

                _apply_token_data(token_data)
                await self.close()  # Force reconnect with new token
                logger.info("Webex Token erfolgreich erneuert (gültig %d Sek.)",
                            token_data.get("expires_in", 0))
                return True

        except Exception as e:
            logger.error("Webex Token-Refresh fehlgeschlagen: %s", e)
            return False

    # ── OAuth2 Flow Helpers ───────────────────────────────────────────────────

    @staticmethod
    def get_auth_url() -> str:
        """Generiert die OAuth2 Authorization URL für den Browser."""
        from app.core.config import settings

        params = {
            "client_id": settings.webex.client_id,
            "response_type": "code",
            "redirect_uri": settings.webex.redirect_uri,
            "scope": settings.webex.scopes,
            "state": "ai-assist-webex",
        }
        return f"{WEBEX_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(code: str) -> dict:
        """Tauscht Authorization Code gegen Access + Refresh Token."""
        from app.core.config import settings

        proxy = None
        if settings.webex.use_proxy and settings.proxy.enabled:
            proxy = settings.proxy.get_proxy_url()

        async with httpx.AsyncClient(
            timeout=30,
            verify=settings.webex.verify_ssl,
            proxy=proxy,
        ) as client:
            response = await client.post(
                WEBEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.webex.client_id,
                    "client_secret": settings.webex.client_secret,
                    "code": code,
                    "redirect_uri": settings.webex.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()

            _apply_token_data(token_data)

            expires_in = token_data.get("expires_in", 1209600)
            logger.info("Webex OAuth erfolgreich: Token gültig %d Sek., Refresh %s",
                        expires_in, "vorhanden" if settings.webex.refresh_token else "fehlt")

            return {
                "success": True,
                "expires_in": expires_in,
                "has_refresh": bool(settings.webex.refresh_token),
            }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Zentrale Request-Methode mit Rate-Limit-Retry und Auto-Refresh."""
        client = self._get_client()
        max_retries = 3

        for attempt in range(max_retries):
            try:
                response = await client.request(method, path, **kwargs)

                # 401 → Token abgelaufen → Refresh versuchen
                if response.status_code == 401 and attempt == 0:
                    logger.info("Webex 401 - versuche Token-Refresh...")
                    refreshed = await self.refresh_token()
                    if refreshed:
                        client = self._get_client()
                        continue
                    raise httpx.HTTPStatusError(
                        "Token abgelaufen und Refresh fehlgeschlagen",
                        request=response.request,
                        response=response,
                    )

                if response.status_code == 429:
                    # Retry-After kann Sekunden (int) oder HTTP-Datum sein
                    retry_header = response.headers.get("Retry-After", "5")
                    try:
                        retry_after = int(retry_header)
                    except ValueError:
                        retry_after = 5  # Fallback bei Datum-Format
                    logger.warning("Webex Rate-Limit, warte %d Sekunden", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()

                if not response.content:
                    return {}
                try:
                    return response.json()
                except (ValueError, Exception) as e:
                    logger.error("Webex API: Ungültige JSON-Antwort für %s %s: %s",
                                 method, path, str(e)[:100])
                    return {}

            except httpx.HTTPStatusError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ProxyError) as e:
                if attempt < max_retries - 1:
                    logger.warning("Webex API Verbindungsfehler (Versuch %d/%d): %s",
                                   attempt + 1, max_retries, e)
                    await asyncio.sleep(2)
                    continue
                raise

        raise RuntimeError("Webex API: Max Retries erreicht")

    # ── API-Methoden ──────────────────────────────────────────────────────────

    async def test_connection(self) -> dict:
        """GET /people/me - Verbindungstest."""
        data = await self._request("GET", "/people/me")
        return {
            "success": True,
            "display_name": data.get("displayName", ""),
            "email": data.get("emails", [""])[0] if data.get("emails") else "",
            "org_id": data.get("orgId", ""),
        }

    async def list_rooms(self, room_type: str = "", max_rooms: int = 50) -> List[dict]:
        """GET /rooms - Räume auflisten."""
        params: Dict[str, Any] = {"sortBy": "lastactivity", "max": max_rooms}
        if room_type in ("group", "direct"):
            params["type"] = room_type

        data = await self._request("GET", "/rooms", params=params)
        rooms = []
        for item in data.get("items", []):
            rooms.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "last_activity": item.get("lastActivity", ""),
                "created": item.get("created", ""),
                "is_locked": item.get("isLocked", False),
            })
        return rooms

    async def get_messages(
        self, room_id: str, max_messages: int = 50, before: str = ""
    ) -> List[dict]:
        """GET /messages?roomId=... - Nachrichten eines Raums."""
        params: Dict[str, Any] = {"roomId": room_id, "max": max_messages}
        if before:
            params["before"] = before

        data = await self._request("GET", "/messages", params=params)
        return self._format_messages(data.get("items", []))

    async def list_all_rooms(
        self,
        room_type: str = "",
        name_contains: str = "",
        max_total: int = 2000,
        page_size: int = 100,
    ) -> List[dict]:
        """Paginiert über ALLE Räume via Webex Link-Header.

        Standardmäßig sortiert Webex /rooms nach lastactivity DESC. Diese Methode
        folgt dem 'Link: <...>; rel="next"' Header der Webex-API bis alle Räume
        abgerufen sind oder max_total erreicht wird.

        Args:
            room_type: 'group', 'direct' oder leer (alle)
            name_contains: optionaler Filter (case-insensitive Substring im Title)
            max_total: Hard-Cap gegen API-Spam (Standard 2000, max 10000)
            page_size: Räume pro API-Call (Standard und Max: 1000)

        Returns:
            Liste aller (gefilterten) Räume, formatiert wie list_rooms()
        """
        page_size = min(max(page_size, 1), 1000)
        max_total = min(max(max_total, 1), 10000)
        name_lower = (name_contains or "").strip().lower()

        client = self._get_client()
        params: Dict[str, Any] = {"sortBy": "lastactivity", "max": page_size}
        if room_type in ("group", "direct"):
            params["type"] = room_type

        all_rooms: List[dict] = []
        url: Optional[str] = "/rooms"
        seen_ids: set = set()
        page_count = 0
        max_pages = 50  # Schutz gegen Endlos-Loop bei API-Bugs

        while url and len(all_rooms) < max_total and page_count < max_pages:
            try:
                if url.startswith("http"):
                    # Folge-Seite aus Link-Header (volle URL, keine params)
                    response = await client.get(url)
                else:
                    response = await client.get(url, params=params)
                    params = {}  # nur erste Anfrage hat params
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.warning("list_all_rooms Pagination Seite %d abgebrochen: %s",
                               page_count + 1, e)
                break

            page_count += 1
            items = data.get("items", [])
            if not items:
                break

            for item in items:
                rid = item.get("id", "")
                if not rid or rid in seen_ids:
                    continue
                seen_ids.add(rid)

                title = item.get("title", "") or ""
                if name_lower and name_lower not in title.lower():
                    continue

                all_rooms.append({
                    "id": rid,
                    "title": title,
                    "type": item.get("type", ""),
                    "last_activity": item.get("lastActivity", ""),
                    "created": item.get("created", ""),
                    "is_locked": item.get("isLocked", False),
                })
                if len(all_rooms) >= max_total:
                    break

            # Next-Page aus Link-Header parsen
            link_header = response.headers.get("Link", "")
            url = self._parse_next_link(link_header)

        return all_rooms

    @staticmethod
    def _parse_next_link(link_header: str) -> Optional[str]:
        """Extrahiert die rel=next URL aus einem RFC-5988 Link-Header.

        Format: '<https://webexapis.com/v1/rooms?cursor=...>; rel="next"'
        """
        if not link_header:
            return None
        for part in link_header.split(","):
            segs = part.strip().split(";")
            if len(segs) < 2:
                continue
            url_part = segs[0].strip()
            rel_part = ";".join(segs[1:]).strip().lower()
            if 'rel="next"' in rel_part or "rel=next" in rel_part:
                if url_part.startswith("<") and url_part.endswith(">"):
                    return url_part[1:-1]
        return None

    async def find_person(self, query: str, limit: int = 10) -> List[dict]:
        """Sucht Personen via Webex /people API.

        Heuristik: enthält query '@' → Email-Suche, sonst displayName-Suche.
        Webex erlaubt nur EINEN Suchparameter pro Call.

        Args:
            query: Email oder Name(steil)
            limit: Max. Treffer (Webex-Default 100)

        Returns:
            Liste von {id, display_name, emails, primary_email, status}
        """
        query = (query or "").strip()
        if not query:
            return []

        params: Dict[str, Any] = {"max": min(max(limit, 1), 100)}
        if "@" in query:
            params["email"] = query
        else:
            params["displayName"] = query

        try:
            data = await self._request("GET", "/people", params=params)
        except Exception as e:
            logger.warning("find_person fehlgeschlagen für '%s': %s", query[:50], e)
            return []

        results = []
        for p in data.get("items", []):
            emails = p.get("emails", []) or []
            results.append({
                "id": p.get("id", ""),
                "display_name": p.get("displayName", ""),
                "emails": emails,
                "primary_email": emails[0] if emails else "",
                "department": p.get("department", ""),
                "status": p.get("status", ""),
            })
        return results

    async def find_direct_room_for_person(self, person_email: str) -> Optional[dict]:
        """Findet den 1:1-Raum mit einer Person über /messages/direct.

        Webex bindet Direkt-Chats an Personen, nicht an Raum-Listen-Position.
        Diese Methode findet den Raum auch wenn er Monate inaktiv war.

        Returns:
            {id, title, type, last_activity, person_email, last_message_at}
            oder None wenn kein Direkt-Chat existiert.
        """
        person_email = (person_email or "").strip()
        if not person_email:
            return None

        try:
            data = await self._request(
                "GET", "/messages/direct",
                params={"personEmail": person_email, "max": 1}
            )
        except Exception as e:
            logger.debug("find_direct_room_for_person: keine DMs mit %s: %s",
                         person_email[:50], e)
            return None

        items = data.get("items", [])
        if not items:
            return None
        room_id = items[0].get("roomId", "")
        if not room_id:
            return None

        # Raum-Metadaten holen
        try:
            room = await self._request("GET", f"/rooms/{room_id}")
            return {
                "id": room.get("id", ""),
                "title": room.get("title", ""),
                "type": room.get("type", "direct"),
                "last_activity": room.get("lastActivity", ""),
                "created": room.get("created", ""),
                "person_email": person_email,
                "last_message_at": items[0].get("created", ""),
            }
        except Exception as e:
            logger.debug("Raum-Metadaten für %s fehlgeschlagen: %s", room_id[:20], e)
            # Fallback: minimale Info aus der Message
            return {
                "id": room_id,
                "title": items[0].get("personDisplayName", ""),
                "type": "direct",
                "last_activity": items[0].get("created", ""),
                "person_email": person_email,
                "last_message_at": items[0].get("created", ""),
            }

    async def get_messages_paginated(
        self,
        room_id: str,
        max_pages: int = 10,
        page_size: int = 100,
        since: str = "",
        before: str = "",
    ) -> List[dict]:
        """Lädt Nachrichten eines Raums über mehrere Seiten (Pagination).

        Webex API gibt Nachrichten in absteigender Reihenfolge zurück (neueste zuerst).
        Diese Methode paginiert via `before`-Parameter rückwärts in die Vergangenheit,
        bis entweder `max_pages` erreicht ist, der Raum keine älteren Nachrichten mehr
        hat, oder eine Nachricht älter als `since` gefunden wird.

        Args:
            room_id: Webex Room-ID
            max_pages: Maximale Anzahl Seiten (Schutz vor Endlos-Paginierung)
            page_size: Nachrichten pro Seite (Webex-Limit: 100)
            since: ISO-Datum (z.B. "2026-02-01") - Stop wenn ältere Msgs erreicht werden
            before: ISO-Datum oder Message-ID als Startpunkt (optional)

        Returns:
            Liste aller geladenen Nachrichten (formatiert), absteigend nach Datum
        """
        page_size = min(max(page_size, 1), 100)
        max_pages = max(1, min(max_pages, 50))

        all_messages: List[dict] = []
        cursor = before
        seen_ids: set = set()

        for page_idx in range(max_pages):
            params: Dict[str, Any] = {"roomId": room_id, "max": page_size}
            if cursor:
                params["before"] = cursor

            try:
                data = await self._request("GET", "/messages", params=params)
            except Exception as e:
                logger.warning("Webex Pagination Seite %d abgebrochen: %s", page_idx + 1, e)
                break

            items = data.get("items", [])
            if not items:
                break

            stop_due_to_since = False
            for item in items:
                msg_id = item.get("id", "")
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                created = item.get("created", "")
                if since and created and created < since:
                    stop_due_to_since = True
                    continue

                all_messages.append(self._format_message(item))

            if stop_due_to_since:
                break

            # Nächste Seite: cursor = ältester Zeitstempel dieser Seite
            oldest_created = items[-1].get("created", "")
            if not oldest_created or oldest_created == cursor:
                break
            cursor = oldest_created

        return all_messages

    async def get_direct_messages(self, person_email: str) -> List[dict]:
        """GET /messages/direct?personEmail=... - Direktnachrichten."""
        data = await self._request(
            "GET", "/messages/direct", params={"personEmail": person_email}
        )
        return self._format_messages(data.get("items", []))

    async def get_message(self, message_id: str) -> dict:
        """GET /messages/{id} - Einzelne Nachricht."""
        data = await self._request("GET", f"/messages/{message_id}")
        return self._format_message(data)

    async def get_new_messages_since(
        self, room_ids: List[str], since: datetime, max_per_room: int = 50
    ) -> List[dict]:
        """Neue Nachrichten aus mehreren Räumen seit Zeitpunkt."""
        all_messages = []
        since_iso = since.isoformat() + "Z" if not since.isoformat().endswith("Z") else since.isoformat()

        for room_id in room_ids:
            try:
                params: Dict[str, Any] = {
                    "roomId": room_id,
                    "max": max_per_room,
                }
                data = await self._request("GET", "/messages", params=params)
                for item in data.get("items", []):
                    created = item.get("created", "")
                    if created and created >= since_iso:
                        msg = self._format_message(item)
                        msg["room_id"] = room_id
                        all_messages.append(msg)
            except Exception as e:
                logger.error("Fehler beim Abrufen von Raum %s: %s", room_id[:20], e)

        return all_messages

    async def get_rooms_for_polling(self) -> List[str]:
        """Gibt alle Raum-IDs zurück die für Polling relevant sind."""
        rooms = await self.list_rooms(max_rooms=100)
        return [r["id"] for r in rooms]

    async def close(self) -> None:
        """HTTP-Client schließen."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _format_messages(self, items: List[dict]) -> List[dict]:
        """Formatiert eine Liste von Nachrichten."""
        return [self._format_message(item) for item in items]

    async def get_thread_replies(self, room_id: str, parent_id: str, max_replies: int = 20) -> List[dict]:
        """Lade Thread-Antworten zu einer Nachricht."""
        params: Dict[str, Any] = {
            "roomId": room_id,
            "parentId": parent_id,
            "max": max_replies,
        }
        data = await self._request("GET", "/messages", params=params)
        return self._format_messages(data.get("items", []))

    async def get_messages_mentioning_me(self, room_id: str, max_messages: int = 50) -> List[dict]:
        """Nachrichten die den authentifizierten User @erwähnen."""
        params: Dict[str, Any] = {
            "roomId": room_id,
            "mentionedPeople": "me",
            "max": min(max_messages, 100),  # Webex-Limit bei mentionedPeople
        }
        data = await self._request("GET", "/messages", params=params)
        return self._format_messages(data.get("items", []))

    async def get_my_email(self) -> str:
        """Gibt die E-Mail des authentifizierten Users zurück (gecached)."""
        if not hasattr(self, "_my_email") or not self._my_email:
            try:
                data = await self._request("GET", "/people/me")
                self._my_email = (data.get("emails", [""])[0] if data.get("emails") else "")
                self._my_person_id = data.get("id", "")
                self._my_display_name = data.get("displayName", "")
            except Exception:
                self._my_email = ""
                self._my_person_id = ""
                self._my_display_name = ""
        return self._my_email

    async def enrich_with_thread_context(self, msg: dict) -> dict:
        """Reichert eine Nachricht mit Thread-Kontext und Mention-Info an.

        Fügt hinzu:
        - mentions_me: ob die Nachricht den Auth-User erwähnt
        - is_direct: ob es eine Direktnachricht ist
        - thread_replies: Antworten wenn die Nachricht ein Thread-Root ist
        - is_reply: ob die Nachricht selbst eine Antwort ist
        """
        my_email = await self.get_my_email()
        my_person_id = getattr(self, "_my_person_id", "")

        # Mention-Check
        mentioned_people = msg.get("mentioned_people", [])
        msg["mentions_me"] = (
            my_person_id in mentioned_people
            or msg.get("room_type") == "direct"
        )
        msg["is_direct"] = msg.get("room_type") == "direct"
        msg["is_reply"] = bool(msg.get("parent_id"))

        # Thread-Antworten laden (nur für Root-Nachrichten die kein Reply sind)
        msg["thread_replies"] = []
        msg["thread_reply_count"] = 0
        if not msg.get("parent_id") and msg.get("room_id") and msg.get("id"):
            try:
                replies = await self.get_thread_replies(
                    room_id=msg["room_id"],
                    parent_id=msg["id"],
                    max_replies=10,
                )
                msg["thread_replies"] = replies
                msg["thread_reply_count"] = len(replies)
            except Exception as e:
                logger.debug("Thread-Replies laden fehlgeschlagen: %s", e)

        return msg

    async def get_file_info(self, file_url: str) -> dict:
        """HEAD-Request auf Datei-URL um Metadaten zu holen."""
        client = self._get_client()
        response = await client.head(file_url)
        response.raise_for_status()

        content_disp = response.headers.get("Content-Disposition", "")
        filename = ""
        if "filename=" in content_disp:
            filename = content_disp.split("filename=")[-1].strip('"').strip("'")

        return {
            "filename": filename,
            "content_type": response.headers.get("Content-Type", ""),
            "size": int(response.headers.get("Content-Length", "0")),
        }

    async def download_file(self, file_url: str) -> tuple:
        """Lädt eine Datei herunter. Gibt (bytes, content_type, filename) zurück."""
        client = self._get_client()
        response = await client.get(file_url)
        response.raise_for_status()

        content_disp = response.headers.get("Content-Disposition", "")
        filename = "attachment"
        if "filename=" in content_disp:
            filename = content_disp.split("filename=")[-1].strip('"').strip("'")

        return (
            response.content,
            response.headers.get("Content-Type", "application/octet-stream"),
            filename,
        )

    def _format_message(self, item: dict) -> dict:
        """Formatiert eine einzelne Nachricht."""
        # Datei-URLs extrahieren
        files = item.get("files", [])
        file_urls = files if isinstance(files, list) else []

        return {
            "id": item.get("id", ""),
            "room_id": item.get("roomId", ""),
            "room_type": item.get("roomType", ""),
            "person_id": item.get("personId", ""),
            "person_email": item.get("personEmail", ""),
            "person_display_name": item.get("personDisplayName", ""),
            "text": item.get("text", ""),
            "html": item.get("html", ""),
            "created": item.get("created", ""),
            "updated": item.get("updated", ""),
            "parent_id": item.get("parentId", ""),
            "mentioned_people": item.get("mentionedPeople", []),
            "mentioned_groups": item.get("mentionedGroups", []),
            "has_files": bool(files),
            "file_count": len(file_urls),
            "file_urls": file_urls,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_webex_client: Optional[WebexClient] = None


def get_webex_client() -> WebexClient:
    """Gibt den Singleton Webex-Client zurück."""
    global _webex_client
    if _webex_client is None:
        _webex_client = WebexClient()
    return _webex_client


async def close_webex_client() -> None:
    """Schließt den Singleton Webex-Client."""
    global _webex_client
    if _webex_client:
        await _webex_client.close()
        _webex_client = None
