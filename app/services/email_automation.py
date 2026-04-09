"""
E-Mail Automation Service - Background-Worker für Todo-Erkennung.

Pollt neue E-Mails, wendet konfigurierte Regeln an,
ruft LLM auf und erstellt Todos.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.email_models import (
    EmailRule, EmailRulesStore, TodoItem, MailSnapshot, EmailAttachmentInfo
)

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).parent.parent.parent / "email_rules.json"


class EmailAutomationService:
    """Background-Worker: Pollt E-Mails, wendet Regeln an, erstellt Todos."""

    def __init__(self):
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._rules: Optional[EmailRulesStore] = None

    # ── Regel-Management ───────────────────────────────────────────────────────

    def load_rules(self) -> EmailRulesStore:
        """Lade email_rules.json."""
        if self._rules is not None:
            return self._rules

        if _RULES_FILE.exists():
            try:
                raw = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
                self._rules = EmailRulesStore(**raw)
            except Exception as e:
                logger.error("Fehler beim Laden von email_rules.json: %s", e)
                self._rules = EmailRulesStore()
        else:
            self._rules = EmailRulesStore()

        return self._rules

    def save_rules(self) -> None:
        """Speichere email_rules.json."""
        if self._rules is None:
            return
        try:
            _RULES_FILE.write_text(
                self._rules.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Fehler beim Speichern von email_rules.json: %s", e)

    def get_rules(self) -> List[EmailRule]:
        """Alle Regeln."""
        return list(self.load_rules().rules)

    def get_rule(self, rule_id: str) -> Optional[EmailRule]:
        """Eine Regel nach ID."""
        for rule in self.load_rules().rules:
            if rule.id == rule_id:
                return rule
        return None

    def add_rule(self, rule: EmailRule) -> EmailRule:
        """Neue Regel hinzufügen."""
        store = self.load_rules()
        store.rules.append(rule)
        self.save_rules()
        logger.info("Neue E-Mail-Regel erstellt: %s (%s)", rule.name, rule.id)
        return rule

    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[EmailRule]:
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
            logger.warning("Email-Automation läuft bereits")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Email-Automation gestartet")

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
        logger.info("Email-Automation gestoppt")

    def get_status(self) -> Dict[str, Any]:
        """Status der Automation."""
        from app.core.config import settings
        from app.services.todo_store import get_todo_store

        store = get_todo_store()
        data = store.load()
        rules = self.get_rules()

        return {
            "running": self.is_running,
            "polling_enabled": settings.email.polling_enabled,
            "last_poll": data.last_poll,
            "polling_interval_minutes": settings.email.polling_interval_minutes,
            "rules_count": len(rules),
            "active_rules": sum(1 for r in rules if r.enabled),
        }

    # ── Polling Loop ───────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Endlos-Loop: poll → evaluate → sleep."""
        from app.core.config import settings

        logger.info("Email-Polling gestartet (Intervall: %d Min)", settings.email.polling_interval_minutes)

        while self._running:
            try:
                await self._process_new_emails()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Email-Poll-Fehler: %s", e, exc_info=True)

            try:
                await asyncio.sleep(settings.email.polling_interval_minutes * 60)
            except asyncio.CancelledError:
                break

    async def _process_new_emails(self) -> None:
        """Neue Mails holen → gegen aktive Regeln prüfen."""
        from app.core.config import settings
        from app.services.email_client import get_email_client
        from app.services.todo_store import get_todo_store

        client = get_email_client()
        store = get_todo_store()
        data = store.load()

        # Zeitfenster bestimmen
        if data.last_poll:
            since = datetime.fromisoformat(data.last_poll)
        else:
            since = datetime.now() - timedelta(hours=24)

        logger.debug("Email-Poll: Suche Mails seit %s", since.isoformat())

        emails = await client.get_new_emails_since(
            since=since,
            limit=settings.email.max_emails_per_poll,
        )

        if not emails:
            store.update_last_poll(datetime.now().isoformat())
            return

        active_rules = [r for r in self.get_rules() if r.enabled]
        if not active_rules:
            store.update_last_poll(datetime.now().isoformat())
            return

        logger.info("Email-Poll: %d neue Mails, %d aktive Regeln", len(emails), len(active_rules))

        for email_data in emails:
            email_id = email_data.get("email_id", "")

            for rule in active_rules:
                # Regel-spezifischer Duplikat-Check: email_id:rule_id
                process_key = f"{email_id}:{rule.id}"
                if store.is_processed(process_key):
                    continue

                # Absender-Filter prüfen
                if rule.sender_filter:
                    sender = email_data.get("sender", "").lower()
                    if rule.sender_filter.lower() not in sender:
                        continue

                # LLM-Auswertung
                try:
                    result = await self._evaluate_email(email_data, rule)
                    if result and result.get("is_todo"):
                        self._create_todo(store, email_data, rule, result)
                except Exception as e:
                    logger.error("LLM-Auswertung fehlgeschlagen für Mail '%s' mit Regel '%s': %s",
                                 email_data.get("subject", "?"), rule.name, e)

                # Regel-spezifisch als verarbeitet markieren
                store.mark_processed(process_key)

        store.update_last_poll(datetime.now().isoformat())
        logger.info("Email-Poll abgeschlossen: %d Mails verarbeitet", len(emails))

    # ── Todo-Erstellung (gemeinsam für Polling und Test) ───────────────────────

    def _create_todo(
        self, store, email_data: Dict, rule: EmailRule, result: Dict
    ) -> TodoItem:
        """Erstellt ein TodoItem aus E-Mail-Daten und LLM-Ergebnis."""
        todo = TodoItem(
            rule_id=rule.id,
            rule_name=rule.name,
            email_id=email_data.get("email_id", ""),
            subject=email_data.get("subject", ""),
            sender=email_data.get("sender", ""),
            sender_name=email_data.get("sender_name", ""),
            received_at=email_data.get("date", ""),
            todo_text=result.get("todo_text", ""),
            ai_analysis=result.get("analysis", ""),
            priority=result.get("priority", "medium"),
            deadline=result.get("deadline"),
            mail_snapshot=MailSnapshot(
                subject=email_data.get("subject", ""),
                sender=email_data.get("sender", ""),
                sender_name=email_data.get("sender_name", ""),
                to=email_data.get("to", []),
                cc=email_data.get("cc", []),
                date=email_data.get("date", ""),
                body_text=email_data.get("body_text", "")[:5000],
                body_html=email_data.get("body_html", "")[:10000],
                attachments=[
                    EmailAttachmentInfo(**a) for a in email_data.get("attachments", [])
                ],
            ),
        )
        store.add(todo)
        return todo

    # ── LLM-Auswertung ────────────────────────────────────────────────────────

    async def _evaluate_email(self, email_data: Dict, rule: EmailRule) -> Optional[Dict]:
        """LLM-Aufruf: Prüfe E-Mail gegen Regel-Beschreibung."""
        from app.services.llm_client import llm_client

        # Attachments-Info
        attachments = email_data.get("attachments", [])
        att_text = ", ".join(f"{a['name']} ({a.get('size', 0)} Bytes)" for a in attachments) if attachments else "Keine"

        system_prompt = (
            "Du bist ein E-Mail-Analyse-Assistent. Prüfe die folgende E-Mail "
            "anhand der Regel-Beschreibung und entscheide, ob ein Todo vorliegt.\n\n"
            f"Regel: \"{rule.description}\"\n"
        )
        if rule.sender_filter:
            system_prompt += f"Absender-Filter: {rule.sender_filter}\n"

        system_prompt += (
            "\nWICHTIG: Berücksichtige den Thread-Status. Wenn bereits Antworten oder "
            "Weiterleitungen existieren, ist das Todo möglicherweise schon bearbeitet. "
            "Setze is_todo auf false wenn die Antworten darauf hindeuten dass das "
            "Thema bereits erledigt ist.\n"
            "\nAntworte NUR im folgenden JSON-Format (kein anderer Text):\n"
            '{"is_todo": true/false, "todo_text": "Kurze Zusammenfassung der Aufgabe (1-2 Sätze)", '
            '"analysis": "Begründung", "priority": "high/medium/low", "deadline": "YYYY-MM-DD oder null"}'
        )

        # Thread-Info aufbereiten
        thread = email_data.get("thread", {})
        thread_text = ""
        if thread.get("has_replies") or thread.get("has_forwards"):
            parts = []
            if thread.get("reply_count"):
                parts.append(f"{thread['reply_count']} Antwort(en)")
            if thread.get("forward_count"):
                parts.append(f"{thread['forward_count']} Weiterleitung(en)")
            thread_text = f"Thread-Status: {', '.join(parts)}"
            for msg in thread.get("thread_messages", [])[:5]:
                thread_text += f"\n  - {msg.get('type','')}: {msg.get('subject','')} von {msg.get('sender','')} ({msg.get('date','')})"

        body_text = email_data.get("body_text", "")[:3000]
        user_prompt = (
            f"Von: {email_data.get('sender', '')} ({email_data.get('sender_name', '')})\n"
            f"Betreff: {email_data.get('subject', '')}\n"
            f"Datum: {email_data.get('date', '')}\n"
            f"Anhänge: {att_text}\n"
        )
        if thread_text:
            user_prompt += f"\n{thread_text}\n"
        user_prompt += f"\nInhalt:\n{body_text}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.debug("LLM-Auswertung: Regel='%s', Mail='%s' von '%s'",
                     rule.name, email_data.get("subject", "?"), email_data.get("sender", "?"))

        response = await llm_client.chat(messages)
        logger.debug("LLM-Antwort: %s", response[:300] if response else "(leer)")

        # JSON aus Antwort extrahieren
        try:
            result = json.loads(response)
            logger.info("Regel '%s' → Mail '%s': is_todo=%s, todo='%s'",
                        rule.name, email_data.get("subject", "?")[:40],
                        result.get("is_todo"), result.get("todo_text", "")[:60])
            return result
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[^{}]*"is_todo"[^{}]*\}', response, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                    logger.info("Regel '%s' → Mail '%s': is_todo=%s (aus Text extrahiert)",
                                rule.name, email_data.get("subject", "?")[:40], result.get("is_todo"))
                    return result
                except json.JSONDecodeError:
                    pass
            logger.warning("LLM-Antwort enthält kein gültiges JSON: %s", response[:200])
            return None

    # ── Regel-Test ─────────────────────────────────────────────────────────────

    async def test_rule(self, rule_id: str, limit: int = 10, create_todos: bool = False) -> List[Dict[str, Any]]:
        """Testlauf einer Regel gegen die letzten N Mails. Optional Todos erstellen."""
        rule = self.get_rule(rule_id)
        if not rule:
            return []

        from app.services.email_client import get_email_client
        from app.services.todo_store import get_todo_store
        client = get_email_client()
        store = get_todo_store()

        since = datetime.now() - timedelta(days=7)
        # Mehr Mails laden als das Regel-Limit (limit ist Anzahl zu prüfender Mails)
        emails = await client.get_new_emails_since(since=since, limit=max(limit, 50))
        logger.info("Regel-Test '%s': %d Mails geladen (seit %s)", rule.name, len(emails), since.isoformat())

        matches = []
        for email_data in emails:
            email_id = email_data.get("email_id", "")
            process_key = f"{email_id}:{rule.id}"

            # Absender-Filter
            if rule.sender_filter:
                sender = email_data.get("sender", "").lower()
                if rule.sender_filter.lower() not in sender:
                    continue

            try:
                result = await self._evaluate_email(email_data, rule)
                if result and result.get("is_todo"):
                    match_info = {
                        "subject": email_data.get("subject", ""),
                        "sender": email_data.get("sender", ""),
                        "date": email_data.get("date", ""),
                        "todo_text": result.get("todo_text", ""),
                        "priority": result.get("priority", "medium"),
                    }
                    matches.append(match_info)

                    if create_todos and not store.is_processed(process_key):
                        self._create_todo(store, email_data, rule, result)
                        store.mark_processed(process_key)
                        match_info["todo_created"] = True

                    # Bei 5+ Treffern abbrechen (spart LLM-Aufrufe)
                    if len(matches) >= 5:
                        logger.info("Regel-Test '%s': 5 Treffer erreicht, beende früh", rule.name)
                        break
            except Exception as e:
                logger.error("Test-Regel Fehler: %s", e)

            if len(matches) >= 5:
                break

        return matches


# ── Singleton ──────────────────────────────────────────────────────────────────

_automation: Optional[EmailAutomationService] = None


def get_email_automation() -> EmailAutomationService:
    """Gibt den Singleton Automation-Service zurück."""
    global _automation
    if _automation is None:
        _automation = EmailAutomationService()
    return _automation
