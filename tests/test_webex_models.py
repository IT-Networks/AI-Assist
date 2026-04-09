"""Tests für app.models.webex_models — Datenmodelle der Webex-Integration."""

import pytest
from app.models.webex_models import WebexRule, WebexRulesStore
from app.models.email_models import TodoItem, TodoStore, MailSnapshot


class TestWebexRule:
    def test_auto_id(self):
        rule = WebexRule(name="Test", description="Prüfe X")
        assert rule.id.startswith("wxr-")
        assert len(rule.id) == 10  # "wxr-" + 6 hex chars

    def test_defaults(self):
        rule = WebexRule(name="Test", description="Desc")
        assert rule.enabled is True
        assert rule.room_filter == ""
        assert rule.sender_filter == ""
        assert rule.created_at  # nicht leer

    def test_custom_values(self):
        rule = WebexRule(
            name="Team-Chat",
            description="Prüfe auf Aufgaben im Team-Chat",
            room_filter="Projekt Alpha",
            sender_filter="chef@example.com",
            enabled=False,
        )
        assert rule.room_filter == "Projekt Alpha"
        assert rule.sender_filter == "chef@example.com"
        assert rule.enabled is False


class TestWebexRulesStore:
    def test_empty(self):
        store = WebexRulesStore()
        assert store.rules == []
        assert store.version == 1

    def test_serialization_roundtrip(self):
        rule = WebexRule(name="R1", description="D1")
        store = WebexRulesStore(rules=[rule])
        data = store.model_dump_json()
        restored = WebexRulesStore.model_validate_json(data)
        assert len(restored.rules) == 1
        assert restored.rules[0].name == "R1"

    def test_multiple_rules(self):
        rules = [
            WebexRule(name=f"R{i}", description=f"D{i}")
            for i in range(5)
        ]
        store = WebexRulesStore(rules=rules)
        assert len(store.rules) == 5
        # Alle IDs sind unique
        ids = [r.id for r in store.rules]
        assert len(set(ids)) == 5


class TestTodoItemWithSource:
    def test_default_source_is_email(self):
        todo = TodoItem(
            rule_id="rule-1", rule_name="R", email_id="e1",
            subject="S", sender="s@b.com", todo_text="T",
            mail_snapshot=MailSnapshot(subject="S", sender="s@b.com"),
        )
        assert todo.source == "email"

    def test_webex_source(self):
        todo = TodoItem(
            rule_id="wxr-1", rule_name="Webex-Regel", email_id="msg123",
            subject="[Webex] Team Chat", sender="user@example.com",
            todo_text="Aufgabe erledigen", source="webex",
            mail_snapshot=MailSnapshot(subject="[Webex] Team Chat", sender="user@example.com"),
        )
        assert todo.source == "webex"

    def test_invalid_source_rejected(self):
        with pytest.raises(Exception):
            TodoItem(
                rule_id="r", rule_name="R", email_id="e",
                subject="S", sender="s", todo_text="T",
                source="invalid",
                mail_snapshot=MailSnapshot(subject="S", sender="s"),
            )


class TestTodoStoreWithWebexPoll:
    def test_last_webex_poll_default(self):
        store = TodoStore()
        assert store.last_webex_poll is None

    def test_serialization_with_webex_poll(self):
        store = TodoStore(
            last_poll="2026-04-09T10:00:00",
            last_webex_poll="2026-04-09T11:00:00",
        )
        data = store.model_dump_json()
        restored = TodoStore.model_validate_json(data)
        assert restored.last_poll == "2026-04-09T10:00:00"
        assert restored.last_webex_poll == "2026-04-09T11:00:00"

    def test_mixed_source_todos(self):
        email_todo = TodoItem(
            rule_id="rule-1", rule_name="Email", email_id="e1",
            subject="Email-Betreff", sender="a@b.com", todo_text="Email-Aufgabe",
            source="email",
            mail_snapshot=MailSnapshot(subject="S", sender="a@b.com"),
        )
        webex_todo = TodoItem(
            rule_id="wxr-1", rule_name="Webex", email_id="msg1",
            subject="[Webex] Chat", sender="c@d.com", todo_text="Webex-Aufgabe",
            source="webex",
            mail_snapshot=MailSnapshot(subject="[Webex] Chat", sender="c@d.com"),
        )
        store = TodoStore(todos=[email_todo, webex_todo])
        assert len(store.todos) == 2
        assert store.todos[0].source == "email"
        assert store.todos[1].source == "webex"
