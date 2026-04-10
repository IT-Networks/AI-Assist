"""Tests für app.services.webex_automation — Regel-CRUD und Automation-Logik."""

import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.models.webex_models import WebexRule, WebexRulesStore
from app.services.webex_automation import WebexAutomationService


@pytest.fixture
def automation(tmp_path):
    """Erstellt einen WebexAutomationService mit temporärem Dateipfad."""
    svc = WebexAutomationService()
    svc._rules = None
    with patch("app.services.webex_automation._RULES_FILE", tmp_path / "webex_rules.json"):
        yield svc


class TestRuleCRUD:
    def test_load_empty(self, automation):
        rules = automation.get_rules()
        assert rules == []

    def test_add_rule(self, automation):
        rule = WebexRule(name="Test", description="Prüfe auf Aufgaben")
        result = automation.add_rule(rule)
        assert result.name == "Test"
        assert len(automation.get_rules()) == 1

    def test_get_rule(self, automation):
        rule = WebexRule(name="Test", description="Desc")
        automation.add_rule(rule)
        found = automation.get_rule(rule.id)
        assert found is not None
        assert found.name == "Test"

    def test_get_rule_not_found(self, automation):
        assert automation.get_rule("nonexistent") is None

    def test_update_rule(self, automation):
        rule = WebexRule(name="Original", description="Desc")
        automation.add_rule(rule)
        updated = automation.update_rule(rule.id, {
            "name": "Updated",
            "room_filter": "Team Chat",
            "sender_filter": "boss@co.com",
        })
        assert updated is not None
        assert updated.name == "Updated"
        assert updated.room_filter == "Team Chat"
        assert updated.sender_filter == "boss@co.com"

    def test_update_rule_preserves_id(self, automation):
        rule = WebexRule(name="Test", description="Desc")
        automation.add_rule(rule)
        updated = automation.update_rule(rule.id, {"id": "hacked", "created_at": "hacked"})
        assert updated.id == rule.id

    def test_update_rule_not_found(self, automation):
        assert automation.update_rule("nonexistent", {"name": "X"}) is None

    def test_delete_rule(self, automation):
        rule = WebexRule(name="Test", description="Desc")
        automation.add_rule(rule)
        assert automation.delete_rule(rule.id) is True
        assert len(automation.get_rules()) == 0

    def test_delete_rule_not_found(self, automation):
        assert automation.delete_rule("nonexistent") is False


class TestRulePersistence:
    def test_save_and_reload(self, automation, tmp_path):
        with patch("app.services.webex_automation._RULES_FILE", tmp_path / "webex_rules.json"):
            rule = WebexRule(name="Persistent", description="D")
            automation.add_rule(rule)
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
            mock_settings.webex.polling_enabled = False
            mock_settings.webex.polling_interval_minutes = 5
            status = automation.get_status()
            assert "running" in status
            assert "rules_count" in status
            assert status["running"] is False


class TestMessageEvaluation:
    async def test_evaluate_message_todo_found(self, automation):
        """LLM gibt is_todo=true zurück."""
        mock_response = json.dumps({
            "is_todo": True,
            "todo_text": "Präsentation vorbereiten",
            "analysis": "Klare Arbeitsanweisung",
            "priority": "high",
            "deadline": "2026-04-15",
        })

        rule = WebexRule(name="Test", description="Prüfe auf Aufgaben")
        msg = {
            "person_email": "chef@example.com",
            "person_display_name": "Chef",
            "room_title": "Projekt Alpha",
            "text": "Bitte die Präsentation bis Freitag vorbereiten.",
            "created": "2026-04-09T10:00:00Z",
            "has_files": False,
        }

        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            result = await automation._evaluate_message(msg, rule)

        assert result is not None
        assert result["is_todo"] is True
        assert result["todo_text"] == "Präsentation vorbereiten"
        assert result["priority"] == "high"

    async def test_evaluate_message_no_todo(self, automation):
        """LLM gibt is_todo=false zurück."""
        mock_response = json.dumps({
            "is_todo": False,
            "todo_text": "",
            "analysis": "Nur Info",
            "priority": "low",
            "deadline": None,
        })

        rule = WebexRule(name="Test", description="Prüfe auf Aufgaben")
        msg = {
            "person_email": "info@example.com",
            "person_display_name": "",
            "room_title": "Allgemein",
            "text": "Frohe Ostern!",
            "created": "2026-04-09",
            "has_files": False,
        }

        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            result = await automation._evaluate_message(msg, rule)

        assert result is not None
        assert result["is_todo"] is False

    async def test_evaluate_message_invalid_json(self, automation):
        """LLM gibt ungültiges JSON zurück."""
        rule = WebexRule(name="Test", description="Desc")
        msg = {
            "person_email": "a@b.com", "person_display_name": "",
            "room_title": "Chat", "text": "Hallo",
            "created": "", "has_files": False,
        }

        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value="Das ist kein JSON")
            result = await automation._evaluate_message(msg, rule)

        assert result is None

    async def test_evaluate_message_json_in_text(self, automation):
        """LLM gibt JSON eingebettet in Text zurück."""
        rule = WebexRule(name="Test", description="Desc")
        msg = {
            "person_email": "a@b.com", "person_display_name": "",
            "room_title": "Chat", "text": "Task machen",
            "created": "", "has_files": False,
        }

        response = 'Analyse: {"is_todo": true, "todo_text": "Task", "analysis": "A", "priority": "low", "deadline": null} Ende.'
        with patch("app.services.llm_client.llm_client") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=response)
            result = await automation._evaluate_message(msg, rule)

        assert result is not None
        assert result["is_todo"] is True


class TestTodoCreation:
    def test_create_todo_from_webex(self, automation):
        """_create_todo erstellt korrekt ein Todo mit source=webex."""
        rule = WebexRule(name="Team-Regel", description="Aufgaben prüfen")
        msg = {
            "id": "msg-123",
            "room_id": "room-456",
            "room_title": "Projekt Alpha",
            "person_email": "user@example.com",
            "person_display_name": "Max Mustermann",
            "text": "Bitte Bericht erstellen",
            "html": "<p>Bitte Bericht erstellen</p>",
            "created": "2026-04-09T10:00:00Z",
            "has_files": False,
        }
        result = {
            "is_todo": True,
            "todo_text": "Bericht erstellen",
            "analysis": "Klare Aufgabe",
            "priority": "high",
            "deadline": "2026-04-15",
        }

        mock_store = MagicMock()
        mock_store.get_counts.return_value = {"new": 0, "read": 0, "done": 0, "total": 0}

        todo = automation._create_todo(mock_store, msg, rule, result)

        assert todo.source == "webex"
        assert todo.subject == "[Webex] Projekt Alpha"
        assert todo.sender == "user@example.com"
        assert todo.sender_name == "Max Mustermann"
        assert todo.todo_text == "Bericht erstellen"
        assert todo.priority == "high"
        assert todo.deadline == "2026-04-15"
        assert todo.mail_snapshot.body_text == "Bitte Bericht erstellen"
        mock_store.add.assert_called_once()


class TestSenderFilter:
    async def test_sender_filter_match(self, automation):
        """Sender-Filter matched → Nachricht wird ausgewertet."""
        rule = WebexRule(name="Chef", description="Aufgaben", sender_filter="chef@example.com")
        automation.add_rule(rule)

        msg = {
            "id": "msg-1",
            "room_id": "room-1",
            "room_title": "Team",
            "person_email": "chef@example.com",
            "person_display_name": "Chef",
            "text": "Bitte erledigen",
            "html": "",
            "created": "2026-04-09T10:00:00Z",
            "has_files": False,
        }

        llm_response = json.dumps({
            "is_todo": True, "todo_text": "Aufgabe erledigen",
            "analysis": "A", "priority": "medium", "deadline": None,
        })

        with patch("app.services.llm_client.llm_client") as mock_llm, \
             patch("app.services.webex_client.get_webex_client") as mock_client, \
             patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_llm.chat = AsyncMock(return_value=llm_response)

            mock_client_inst = AsyncMock()
            mock_client_inst.list_rooms = AsyncMock(return_value=[
                {"id": "room-1", "title": "Team", "type": "group", "last_activity": "", "created": "", "is_locked": False}
            ])
            mock_client_inst.get_rooms_for_polling = AsyncMock(return_value=["room-1"])
            mock_client_inst.get_new_messages_since = AsyncMock(return_value=[msg])
            # enrich_with_thread_context gibt die Nachricht mit Kontext zurück
            async def _enrich(m):
                m["mentions_me"] = False
                m["is_direct"] = False
                m["is_reply"] = False
                m["thread_replies"] = []
                m["thread_reply_count"] = 0
                return m
            mock_client_inst.enrich_with_thread_context = _enrich
            mock_client.return_value = mock_client_inst

            mock_store_inst = MagicMock()
            mock_store_inst.load.return_value = MagicMock(
                last_webex_poll=None, processed_email_ids=[], todos=[]
            )
            mock_store_inst.is_processed.return_value = False
            mock_store_inst.get_counts.return_value = {"new": 0, "read": 0, "done": 0, "total": 0}
            mock_store.return_value = mock_store_inst

            await automation._process_new_messages()

            mock_store_inst.add.assert_called_once()

    async def test_sender_filter_no_match(self, automation):
        """Sender-Filter matched nicht → Nachricht wird übersprungen."""
        rule = WebexRule(name="Chef", description="Aufgaben", sender_filter="chef@example.com")
        automation.add_rule(rule)

        msg = {
            "id": "msg-1",
            "room_id": "room-1",
            "room_title": "Team",
            "person_email": "other@example.com",
            "person_display_name": "Other",
            "text": "Hallo Welt",
            "created": "2026-04-09T10:00:00Z",
            "has_files": False,
        }

        with patch("app.services.webex_client.get_webex_client") as mock_client, \
             patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_client_inst = AsyncMock()
            mock_client_inst.list_rooms = AsyncMock(return_value=[
                {"id": "room-1", "title": "Team", "type": "group", "last_activity": "", "created": "", "is_locked": False}
            ])
            mock_client_inst.get_rooms_for_polling = AsyncMock(return_value=["room-1"])
            mock_client_inst.get_new_messages_since = AsyncMock(return_value=[msg])
            mock_client.return_value = mock_client_inst

            mock_store_inst = MagicMock()
            mock_store_inst.load.return_value = MagicMock(
                last_webex_poll=None, processed_email_ids=[], todos=[]
            )
            mock_store_inst.is_processed.return_value = False
            mock_store.return_value = mock_store_inst

            await automation._process_new_messages()

            # add() sollte NICHT aufgerufen worden sein
            mock_store_inst.add.assert_not_called()
