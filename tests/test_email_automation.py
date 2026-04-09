"""Tests für app.services.email_automation — Regel-CRUD und Automation-Logik."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from app.models.email_models import EmailRule, EmailRulesStore
from app.services.email_automation import EmailAutomationService


@pytest.fixture
def automation(tmp_path):
    """Erstellt einen EmailAutomationService mit temporärem Dateipfad."""
    svc = EmailAutomationService()
    svc._rules = None
    with patch("app.services.email_automation._RULES_FILE", tmp_path / "email_rules.json"):
        yield svc


class TestRuleCRUD:
    def test_load_empty(self, automation):
        rules = automation.get_rules()
        assert rules == []

    def test_add_rule(self, automation):
        rule = EmailRule(name="Test", description="Prüfe auf Aufgaben")
        result = automation.add_rule(rule)
        assert result.name == "Test"
        assert len(automation.get_rules()) == 1

    def test_get_rule(self, automation):
        rule = EmailRule(name="Test", description="Desc")
        automation.add_rule(rule)
        found = automation.get_rule(rule.id)
        assert found is not None
        assert found.name == "Test"

    def test_get_rule_not_found(self, automation):
        assert automation.get_rule("nonexistent") is None

    def test_update_rule(self, automation):
        rule = EmailRule(name="Original", description="Desc")
        automation.add_rule(rule)
        updated = automation.update_rule(rule.id, {"name": "Updated", "sender_filter": "boss@co.com"})
        assert updated is not None
        assert updated.name == "Updated"
        assert updated.sender_filter == "boss@co.com"

    def test_update_rule_preserves_id(self, automation):
        rule = EmailRule(name="Test", description="Desc")
        automation.add_rule(rule)
        updated = automation.update_rule(rule.id, {"id": "hacked", "created_at": "hacked"})
        assert updated.id == rule.id  # ID darf nicht geändert werden

    def test_update_rule_not_found(self, automation):
        assert automation.update_rule("nonexistent", {"name": "X"}) is None

    def test_delete_rule(self, automation):
        rule = EmailRule(name="Test", description="Desc")
        automation.add_rule(rule)
        assert automation.delete_rule(rule.id) is True
        assert len(automation.get_rules()) == 0

    def test_delete_rule_not_found(self, automation):
        assert automation.delete_rule("nonexistent") is False


class TestRulePersistence:
    def test_save_and_reload(self, automation, tmp_path):
        with patch("app.services.email_automation._RULES_FILE", tmp_path / "email_rules.json"):
            rule = EmailRule(name="Persistent", description="D")
            automation.add_rule(rule)
            # Force reload
            automation._rules = None
            rules = automation.get_rules()
            assert len(rules) == 1
            assert rules[0].name == "Persistent"


class TestAutomationControl:
    def test_initial_state(self, automation):
        assert automation.is_running is False

    async def test_start_stop(self, automation):
        with patch.object(automation, '_poll_loop', new_callable=AsyncMock):
            await automation.start()
            assert automation.is_running is True
            await automation.stop()
            assert automation.is_running is False

    async def test_double_start(self, automation):
        with patch.object(automation, '_poll_loop', new_callable=AsyncMock):
            await automation.start()
            await automation.start()  # Sollte Warning loggen, nicht crashen
            assert automation.is_running is True
            await automation.stop()

    def test_get_status(self, automation):
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.email.polling_interval_minutes = 5
            status = automation.get_status()
            assert "running" in status
            assert "rules_count" in status
            assert status["running"] is False


class TestEmailEvaluation:
    async def test_evaluate_email_todo_found(self, automation):
        """LLM gibt is_todo=true zurück."""
        mock_response = json.dumps({
            "is_todo": True,
            "todo_text": "Bericht erstellen",
            "analysis": "Klare Arbeitsanweisung",
            "priority": "high",
            "deadline": "2026-04-15",
        })

        rule = EmailRule(name="Test", description="Prüfe auf Aufgaben")
        email_data = {
            "sender": "chef@example.com",
            "sender_name": "Chef",
            "subject": "Bitte Bericht",
            "date": "2026-04-09",
            "body_text": "Bitte den Bericht bis Freitag erstellen.",
            "attachments": [],
        }

        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            result = await automation._evaluate_email(email_data, rule)

        assert result is not None
        assert result["is_todo"] is True
        assert result["todo_text"] == "Bericht erstellen"
        assert result["priority"] == "high"

    async def test_evaluate_email_no_todo(self, automation):
        """LLM gibt is_todo=false zurück."""
        mock_response = json.dumps({
            "is_todo": False,
            "todo_text": "",
            "analysis": "Nur Info-Mail",
            "priority": "low",
            "deadline": None,
        })

        rule = EmailRule(name="Test", description="Prüfe auf Aufgaben")
        email_data = {
            "sender": "info@newsletter.com",
            "sender_name": "",
            "subject": "Newsletter",
            "date": "2026-04-09",
            "body_text": "Neues aus der Firma...",
            "attachments": [],
        }

        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            result = await automation._evaluate_email(email_data, rule)

        assert result is not None
        assert result["is_todo"] is False

    async def test_evaluate_email_invalid_json(self, automation):
        """LLM gibt ungültiges JSON zurück."""
        rule = EmailRule(name="Test", description="Desc")
        email_data = {
            "sender": "a@b.com", "sender_name": "", "subject": "X",
            "date": "", "body_text": "Y", "attachments": [],
        }

        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value="Das ist kein JSON")
            result = await automation._evaluate_email(email_data, rule)

        assert result is None

    async def test_evaluate_email_json_in_text(self, automation):
        """LLM gibt JSON eingebettet in Text zurück."""
        rule = EmailRule(name="Test", description="Desc")
        email_data = {
            "sender": "a@b.com", "sender_name": "", "subject": "X",
            "date": "", "body_text": "Y", "attachments": [],
        }

        response = 'Hier meine Analyse: {"is_todo": true, "todo_text": "Task", "analysis": "A", "priority": "low", "deadline": null} Ende.'
        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=response)
            result = await automation._evaluate_email(email_data, rule)

        assert result is not None
        assert result["is_todo"] is True


class TestSenderFilter:
    async def test_sender_filter_match(self, automation):
        """Sender-Filter matched → Mail wird ausgewertet."""
        rule = EmailRule(name="Chef", description="Aufgaben", sender_filter="chef@example.com")
        automation.add_rule(rule)

        email_data = {
            "email_id": "e1",
            "sender": "chef@example.com",
            "sender_name": "Chef",
            "subject": "Aufgabe",
            "date": "2026-04-09",
            "body_text": "Mach das bitte",
            "body_html": "",
            "to": [],
            "cc": [],
            "attachments": [],
        }

        llm_response = json.dumps({
            "is_todo": True, "todo_text": "Aufgabe machen",
            "analysis": "A", "priority": "medium", "deadline": None,
        })

        with patch("app.services.llm_client.llm_client") as mock_llm, \
             patch("app.services.email_client.get_email_client") as mock_client, \
             patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_llm.chat = AsyncMock(return_value=llm_response)
            mock_client_inst = AsyncMock()
            mock_client_inst.get_new_emails_since = AsyncMock(return_value=[email_data])
            mock_client.return_value = mock_client_inst

            mock_store_inst = MagicMock()
            mock_store_inst.load.return_value = MagicMock(
                last_poll=None, processed_email_ids=[], todos=[]
            )
            mock_store_inst.is_processed.return_value = False
            mock_store_inst.get_counts.return_value = {"new": 0, "read": 0, "done": 0, "total": 0}
            mock_store.return_value = mock_store_inst

            await automation._process_new_emails()

            # add() sollte aufgerufen worden sein
            mock_store_inst.add.assert_called_once()
