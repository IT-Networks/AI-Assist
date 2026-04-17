"""
Webex Automation Service - Background-Worker für Todo-Erkennung.

Pollt neue Webex-Nachrichten, wendet konfigurierte Regeln an,
ruft LLM auf und erstellt Todos.
"""

import asyncio
import json
import logging
from fnmatch import fnmatch
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.webex_models import WebexRule, WebexRulesStore

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).parent.parent.parent / "webex_rules.json"


class WebexAutomationService:
    """Background-Worker: Pollt Webex-Nachrichten, wendet Regeln an, erstellt Todos."""

    def __init__(self):
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._rules: Optional[WebexRulesStore] = None

    # ── Regel-Management ───────────────────────────────────────────────────────

    def load_rules(self) -> WebexRulesStore:
        """Lade webex_rules.json."""
        if self._rules is not None:
            return self._rules

        if _RULES_FILE.exists():
            try:
                raw = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
                self._rules = WebexRulesStore(**raw)
            except Exception as e:
                logger.error("Fehler beim Laden von webex_rules.json: %s", e)
                self._rules = WebexRulesStore()
        else:
            self._rules = WebexRulesStore()

        return self._rules

    def save_rules(self) -> None:
        """Speichere webex_rules.json."""
        if self._rules is None:
            return
        try:
            _RULES_FILE.write_text(
                self._rules.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Fehler beim Speichern von webex_rules.json: %s", e)

    def get_rules(self) -> List[WebexRule]:
        """Alle Regeln."""
        return list(self.load_rules().rules)

    def get_rule(self, rule_id: str) -> Optional[WebexRule]:
        """Eine Regel nach ID."""
        for rule in self.load_rules().rules:
            if rule.id == rule_id:
                return rule
        return None

    def add_rule(self, rule: WebexRule) -> WebexRule:
        """Neue Regel hinzufügen."""
        store = self.load_rules()
        store.rules.append(rule)
        self.save_rules()
        logger.info("Neue Webex-Regel erstellt: %s (%s)", rule.name, rule.id)
        return rule

    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[WebexRule]:
        """Regel aktualisieren."""
        store = self.load_rules()
        for rule in store.rules:
            if rule.id == rule_id:
                for key, value in updates.items():
                    if hasattr(rule, key) and key not in ("id", "created_at"):
                        setattr(rule, key, value)
                rule.updated_at = datetime.now().isoformat()
                self.save_rules()
                return rule
        return None

    def delete_rule(self, rule_id: str) -> bool:
        """Regel löschen."""
        store = self.load_rules()
        before = len(store.rules)
        store.rules = [r for r in store.rules if r.id != rule_id]
        if len(store.rules) < before:
            self.save_rules()
            return True
        return False

    # ── Automation Control ─────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Startet den Polling-Loop als asyncio.Task."""
        if self.is_running:
            logger.warning("Webex-Automation läuft bereits")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Webex-Automation gestartet")

    async def stop(self) -> None:
        """Stoppt den Polling-Loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Webex-Automation gestoppt")

    def get_status(self) -> Dict[str, Any]:
        """Status der Automation."""
        from app.core.config import settings
        from app.services.todo_store import get_todo_store

        store = get_todo_store()
        data = store.load()
        rules = self.get_rules()

        return {
            "running": self.is_running,
            "polling_enabled": settings.webex.polling_enabled,
            "last_poll": data.last_webex_poll,
            "polling_interval_minutes": settings.webex.polling_interval_minutes,
            "rules_count": len(rules),
            "active_rules": sum(1 for r in rules if r.enabled),
        }

    # ── Polling Loop ───────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Endlos-Loop: poll → evaluate → sleep."""
        from app.core.config import settings

        logger.info("Webex-Polling gestartet (Intervall: %d Min)", settings.webex.polling_interval_minutes)

        while self._running:
            try:
                await self._process_new_messages()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Webex-Poll-Fehler: %s", e, exc_info=True)

            try:
                await asyncio.sleep(settings.webex.polling_interval_minutes * 60)
            except asyncio.CancelledError:
                break

    async def _process_new_messages(self) -> None:
        """Neue Nachrichten holen → gegen aktive Regeln prüfen."""
        from app.core.config import settings
        from app.services.webex_client import get_webex_client
        from app.services.todo_store import get_todo_store

        client = get_webex_client()
        store = get_todo_store()
        data = store.load()

        # Zeitfenster bestimmen
        if data.last_webex_poll:
            since = datetime.fromisoformat(data.last_webex_poll)
        else:
            since = datetime.now() - timedelta(hours=24)

        logger.debug("Webex-Poll: Suche Nachrichten seit %s", since.isoformat())

        active_rules = [r for r in self.get_rules() if r.enabled]
        if not active_rules:
            store.update_last_webex_poll(datetime.now().isoformat())
            return

        # Räume für Polling bestimmen
        room_ids = await self._get_poll_room_ids(client, active_rules)
        if not room_ids:
            store.update_last_webex_poll(datetime.now().isoformat())
            return

        # Bot-Room aus Todo-Verarbeitung ausklammern (wird von AssistRoomHandler bedient)
        bot_room_id = (settings.webex.bot.room_id or "") if settings.webex.bot.enabled else ""
        if bot_room_id:
            room_ids = [rid for rid in room_ids if rid != bot_room_id]
            if not room_ids:
                store.update_last_webex_poll(datetime.now().isoformat())
                return

        messages = await client.get_new_messages_since(
            room_ids=room_ids,
            since=since,
            max_per_room=settings.webex.max_messages_per_poll,
        )

        # Sicherheitsnetz: falls die Room-ID zur Laufzeit gesetzt wurde,
        # auch nachtraeglich alle Bot-Room-Msgs droppen
        if bot_room_id and messages:
            messages = [m for m in messages if m.get("room_id") != bot_room_id]

        if not messages:
            store.update_last_webex_poll(datetime.now().isoformat())
            return

        logger.info("Webex-Poll: %d neue Nachrichten, %d aktive Regeln", len(messages), len(active_rules))

        # Raum-Titel laden für Kontext
        room_titles = {}
        try:
            rooms = await client.list_rooms(max_rooms=100)
            room_titles = {r["id"]: r["title"] for r in rooms}
        except Exception:
            pass

        # User-Anzeigename für namentliche Erkennung im LLM-Prompt
        my_display_name = ""
        try:
            await client.get_my_email()  # Cached, lädt auch Display-Name
            my_display_name = getattr(client, "_my_display_name", "")
        except Exception:
            pass

        for msg in messages:
            msg_id = msg.get("id", "")
            msg["room_title"] = room_titles.get(msg.get("room_id", ""), "")
            msg["_my_display_name"] = my_display_name

            for rule in active_rules:
                process_key = f"wx:{msg_id}:{rule.id}"
                if store.is_processed(process_key):
                    continue

                # Room-Filter prüfen (Wildcard mit * und ? oder Substring)
                if rule.room_filter:
                    room_id = msg.get("room_id", "")
                    room_title = msg.get("room_title", "").lower()
                    rf = rule.room_filter.lower()
                    if any(c in rf for c in ('*', '?')):
                        if not fnmatch(room_title, rf) and rule.room_filter != room_id:
                            continue
                    elif rf not in room_title and rule.room_filter != room_id:
                        continue

                # Sender-Filter prüfen (Wildcard mit * und ? oder Substring)
                if rule.sender_filter:
                    sender = msg.get("person_email", "").lower()
                    sf = rule.sender_filter.lower()
                    if any(c in sf for c in ('*', '?')):
                        if not fnmatch(sender, sf):
                            continue
                    elif sf not in sender:
                        continue

                # Thread-Kontext anreichern
                try:
                    msg = await client.enrich_with_thread_context(msg)
                except Exception as e:
                    logger.debug("Thread-Kontext laden fehlgeschlagen: %s", e)

                # LLM-Auswertung
                try:
                    result = await self._evaluate_message(msg, rule)
                    if result and result.get("is_todo"):
                        self._create_todo(store, msg, rule, result)
                except Exception as e:
                    logger.error("LLM-Auswertung fehlgeschlagen für Webex-Nachricht '%s' mit Regel '%s': %s",
                                 msg.get("text", "?")[:40], rule.name, e)

                store.mark_processed(process_key)

        store.update_last_webex_poll(datetime.now().isoformat())
        logger.info("Webex-Poll abgeschlossen: %d Nachrichten verarbeitet", len(messages))

    async def _get_poll_room_ids(self, client, rules: List[WebexRule]) -> List[str]:
        """Bestimmt die Räume für das Polling basierend auf den Regeln."""
        # Wenn Regeln mit Room-Filter existieren, nur diese Räume
        specific_rooms = set()
        has_wildcard = False

        for rule in rules:
            if rule.room_filter:
                specific_rooms.add(rule.room_filter)
            else:
                has_wildcard = True

        if has_wildcard:
            # Alle Räume
            return await client.get_rooms_for_polling()
        elif specific_rooms:
            # Nur spezifische Räume - prüfe ob IDs oder Namen
            all_rooms = await client.list_rooms(max_rooms=100)
            room_ids = []
            for room in all_rooms:
                for filt in specific_rooms:
                    if filt == room["id"] or filt.lower() in room["title"].lower():
                        room_ids.append(room["id"])
                        break
            return room_ids

        return []

    # ── Todo-Erstellung ───────────────────────────────────────────────────────

    def _create_todo(self, store, msg: Dict, rule: WebexRule, result: Dict):
        """Erstellt ein TodoItem aus einer Webex-Nachricht und LLM-Ergebnis."""
        from app.models.email_models import TodoItem, MailSnapshot, EmailAttachmentInfo

        # Datei-URLs als Attachments speichern
        file_urls = msg.get("file_urls", [])
        attachments = [
            EmailAttachmentInfo(
                name=url.split("/")[-1] if "/" in url else "attachment",
                size=0,
                content_type="webex-file",  # Marker für Webex-Dateien
            )
            for url in file_urls
        ]

        snapshot = MailSnapshot(
            subject=f"[Webex] {msg.get('room_title', 'Direktnachricht')}",
            sender=msg.get("person_email", ""),
            sender_name=msg.get("person_display_name", ""),
            to=[],
            cc=[],
            date=msg.get("created", ""),
            body_text=msg.get("text", "")[:5000],
            body_html=msg.get("html", "")[:10000],
            attachments=attachments,
            file_urls=file_urls,
        )

        todo = TodoItem(
            rule_id=rule.id,
            rule_name=rule.name,
            email_id=msg.get("id", ""),  # Message-ID als email_id
            subject=f"[Webex] {msg.get('room_title', 'Nachricht')}",
            sender=msg.get("person_email", ""),
            sender_name=msg.get("person_display_name", ""),
            received_at=msg.get("created", ""),
            todo_text=result.get("todo_text", ""),
            ai_analysis=result.get("analysis", ""),
            priority=result.get("priority", "medium"),
            deadline=result.get("deadline"),
            source="webex",
            mail_snapshot=snapshot,
        )
        store.add(todo)
        return todo

    # ── LLM-Auswertung ────────────────────────────────────────────────────────

    async def _evaluate_message(self, msg: Dict, rule: WebexRule) -> Optional[Dict]:
        """LLM-Aufruf: Prüfe Webex-Nachricht gegen Regel-Beschreibung."""
        from app.services.llm_client import llm_client

        system_prompt = (
            "Du bist ein Nachrichten-Analyse-Assistent. Prüfe die folgende Webex-Nachricht "
            "anhand der Regel-Beschreibung und entscheide, ob ein Todo vorliegt.\n\n"
            f"Regel: \"{rule.description}\"\n"
        )
        if rule.room_filter:
            system_prompt += f"Raum-Filter: {rule.room_filter}\n"
        if rule.sender_filter:
            system_prompt += f"Absender-Filter: {rule.sender_filter}\n"

        # User-Name für namentliche Erkennung
        my_name = msg.get("_my_display_name", "")

        system_prompt += (
            "\nWICHTIG - Empfänger-Erkennung:\n"
            "- Primär: Nutze die technischen Felder 'Empfänger-Status' im Kontext unten.\n"
            "- 'Direktnachricht' oder '@erwähnt' = sicher an den User gerichtet.\n"
        )
        if my_name:
            system_prompt += (
                f"- Der User heißt \"{my_name}\". Wenn dieser vollständige Name (Vor- oder Nachname) "
                "im Text namentlich genannt wird, darfst du daraus ableiten dass der User gemeint ist.\n"
            )
        system_prompt += (
            "- Kürzel, Abkürzungen, Codes, Gebäudenummern oder ähnliche kurze Zeichenfolgen "
            "im Text sind KEINE Hinweise auf den Empfänger. Leite daraus NIEMALS ab, "
            "dass der User gemeint sein könnte.\n"
            "\nWICHTIG - Thread-Status:\n"
            "- Wenn es bereits Thread-Antworten gibt, ist das Todo möglicherweise "
            "schon bearbeitet. Setze is_todo auf false wenn die Antworten darauf "
            "hindeuten dass das Thema bereits erledigt ist.\n"
            "- Wenn die Nachricht selbst eine Antwort (Reply) ist, "
            "prüfe ob sie eine neue Aufgabe enthält oder nur eine Antwort ist.\n"
            "\nAntworte NUR im folgenden JSON-Format (kein anderer Text):\n"
            '{"is_todo": true/false, "todo_text": "Kurze Zusammenfassung der Aufgabe (1-2 Sätze)", '
            '"analysis": "Begründung", "priority": "high/medium/low", "deadline": "YYYY-MM-DD oder null"}'
        )

        text = msg.get("text", "")[:3000]

        # Kontext-Info aufbauen
        context_parts = []

        # Empfänger-Status (eindeutig technisch bestimmt)
        if msg.get("is_direct"):
            context_parts.append("Empfänger-Status: DIREKTNACHRICHT an dich persönlich")
        elif msg.get("mentions_me"):
            context_parts.append("Empfänger-Status: Du wirst per @mention direkt angesprochen")
        else:
            context_parts.append("Empfänger-Status: Gruppennachricht OHNE @mention an dich")

        if msg.get("is_reply"):
            context_parts.append("Thread-Status: Dies ist eine Antwort in einem Thread")
        if msg.get("mentioned_groups"):
            context_parts.append(f"Erwähnte Gruppen: {', '.join(msg['mentioned_groups'])}")

        # Thread-Antworten als Kontext
        thread_text = ""
        if msg.get("thread_replies"):
            replies = msg["thread_replies"]
            thread_text = f"\nThread-Antworten ({len(replies)}):"
            for reply in replies[:5]:
                thread_text += (
                    f"\n  - {reply.get('person_display_name', reply.get('person_email', '?'))}: "
                    f"{reply.get('text', '')[:200]} ({reply.get('created', '')})"
                )

        user_prompt = (
            f"Von: {msg.get('person_email', '')} ({msg.get('person_display_name', '')})\n"
            f"Raum: {msg.get('room_title', 'Direktnachricht')}\n"
            f"Datum: {msg.get('created', '')}\n"
            f"Dateien: {'Ja' if msg.get('has_files') else 'Keine'}\n"
        )
        if context_parts:
            user_prompt += f"Kontext: {'; '.join(context_parts)}\n"
        if thread_text:
            user_prompt += thread_text + "\n"
        user_prompt += f"\nInhalt:\n{text}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.debug("LLM-Auswertung: Regel='%s', Nachricht von '%s'",
                     rule.name, msg.get("person_email", "?"))

        response = await llm_client.chat(messages)
        logger.debug("LLM-Antwort: %s", response[:300] if response else "(leer)")

        # JSON aus Antwort extrahieren
        try:
            result = json.loads(response)
            logger.info("Regel '%s' → Nachricht von '%s': is_todo=%s",
                        rule.name, msg.get("person_email", "?")[:40],
                        result.get("is_todo"))
            return result
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[^{}]*"is_todo"[^{}]*\}', response, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                    return result
                except json.JSONDecodeError:
                    pass
            logger.warning("LLM-Antwort enthält kein gültiges JSON: %s", response[:200])
            return None

    # ── Regel-Test ─────────────────────────────────────────────────────────────

    async def test_rule(self, rule_id: str, limit: int = 50, create_todos: bool = False) -> List[Dict[str, Any]]:
        """Testlauf einer Regel gegen die letzten Nachrichten. Optional Todos erstellen."""
        rule = self.get_rule(rule_id)
        if not rule:
            return []

        from app.services.webex_client import get_webex_client
        from app.services.todo_store import get_todo_store
        client = get_webex_client()
        store = get_todo_store()

        # Räume bestimmen
        if rule.room_filter:
            rooms = await client.list_rooms(max_rooms=100)
            room_ids = [r["id"] for r in rooms
                        if rule.room_filter == r["id"]
                        or rule.room_filter.lower() in r["title"].lower()]
        else:
            room_ids = await client.get_rooms_for_polling()

        if not room_ids:
            logger.info("Regel-Test '%s': Keine passenden Räume gefunden", rule.name)
            return []

        # Raum-Titel laden
        room_titles = {}
        try:
            all_rooms = await client.list_rooms(max_rooms=100)
            room_titles = {r["id"]: r["title"] for r in all_rooms}
        except Exception:
            pass

        since = datetime.now() - timedelta(days=7)
        messages = await client.get_new_messages_since(
            room_ids=room_ids[:10],  # Max 10 Räume testen
            since=since,
            max_per_room=limit,
        )
        logger.info("Regel-Test '%s': %d Nachrichten geladen (seit %s)",
                     rule.name, len(messages), since.isoformat())

        matches = []
        for msg in messages:
            msg["room_title"] = room_titles.get(msg.get("room_id", ""), "")
            msg_id = msg.get("id", "")
            process_key = f"wx:{msg_id}:{rule.id}"

            # Sender-Filter
            if rule.sender_filter:
                sender = msg.get("person_email", "").lower()
                if rule.sender_filter.lower() not in sender:
                    continue

            try:
                result = await self._evaluate_message(msg, rule)
                if result and result.get("is_todo"):
                    match_info = {
                        "text": msg.get("text", "")[:200],
                        "sender": msg.get("person_email", ""),
                        "room": msg.get("room_title", ""),
                        "date": msg.get("created", ""),
                        "todo_text": result.get("todo_text", ""),
                        "priority": result.get("priority", "medium"),
                    }
                    matches.append(match_info)

                    if create_todos and not store.is_processed(process_key):
                        self._create_todo(store, msg, rule, result)
                        store.mark_processed(process_key)
                        match_info["todo_created"] = True

                    if len(matches) >= 5:
                        logger.info("Regel-Test '%s': 5 Treffer erreicht, beende früh", rule.name)
                        break
            except Exception as e:
                logger.error("Test-Regel Fehler: %s", e)

            if len(matches) >= 5:
                break

        return matches


# ── Singleton ──────────────────────────────────────────────────────────────────

_automation: Optional[WebexAutomationService] = None


def get_webex_automation() -> WebexAutomationService:
    """Gibt den Singleton Automation-Service zurück."""
    global _automation
    if _automation is None:
        _automation = WebexAutomationService()
    return _automation
