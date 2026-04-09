"""Tests für app.models.email_models — Datenmodelle der Email-Integration."""

import pytest
from app.models.email_models import (
    EmailAttachmentInfo, MailSnapshot, EmailRule, EmailRulesStore,
    TodoItem, TodoStore,
)


class TestEmailAttachmentInfo:
    def test_defaults(self):
        att = EmailAttachmentInfo(name="test.pdf")
        assert att.name == "test.pdf"
        assert att.size == 0
        assert att.content_type == ""

    def test_full(self):
        att = EmailAttachmentInfo(name="report.xlsx", size=45200, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert att.size == 45200


class TestMailSnapshot:
    def test_defaults(self):
        snap = MailSnapshot(subject="Test", sender="a@b.com")
        assert snap.subject == "Test"
        assert snap.to == []
        assert snap.attachments == []

    def test_with_attachments(self):
        snap = MailSnapshot(
            subject="Test",
            sender="a@b.com",
            attachments=[EmailAttachmentInfo(name="f.pdf", size=100)],
        )
        assert len(snap.attachments) == 1
        assert snap.attachments[0].name == "f.pdf"


class TestEmailRule:
    def test_auto_id(self):
        rule = EmailRule(name="Test", description="Prüfe X")
        assert rule.id.startswith("rule-")
        assert len(rule.id) == 11  # "rule-" + 6 hex chars

    def test_defaults(self):
        rule = EmailRule(name="Test", description="Desc")
        assert rule.enabled is True
        assert rule.sender_filter == ""
        assert rule.created_at  # nicht leer

    def test_custom_values(self):
        rule = EmailRule(
            name="Aufgaben",
            description="Prüfe auf Aufgaben",
            sender_filter="chef@example.com",
            enabled=False,
        )
        assert rule.sender_filter == "chef@example.com"
        assert rule.enabled is False


class TestEmailRulesStore:
    def test_empty(self):
        store = EmailRulesStore()
        assert store.rules == []
        assert store.version == 1

    def test_serialization_roundtrip(self):
        rule = EmailRule(name="R1", description="D1")
        store = EmailRulesStore(rules=[rule])
        data = store.model_dump_json()
        restored = EmailRulesStore.model_validate_json(data)
        assert len(restored.rules) == 1
        assert restored.rules[0].name == "R1"


class TestTodoItem:
    def test_auto_id(self):
        todo = TodoItem(
            rule_id="rule-abc",
            rule_name="Test",
            email_id="EWS123",
            subject="Betreff",
            sender="a@b.com",
            todo_text="Aufgabe erledigen",
            mail_snapshot=MailSnapshot(subject="Betreff", sender="a@b.com"),
        )
        assert todo.id.startswith("todo-")
        assert todo.status == "new"
        assert todo.priority == "medium"
        assert todo.deadline is None

    def test_all_statuses(self):
        for status in ("new", "read", "done"):
            todo = TodoItem(
                rule_id="r", rule_name="R", email_id="e",
                subject="S", sender="s", todo_text="T",
                status=status,
                mail_snapshot=MailSnapshot(subject="S", sender="s"),
            )
            assert todo.status == status

    def test_invalid_status_rejected(self):
        with pytest.raises(Exception):
            TodoItem(
                rule_id="r", rule_name="R", email_id="e",
                subject="S", sender="s", todo_text="T",
                status="invalid",
                mail_snapshot=MailSnapshot(subject="S", sender="s"),
            )


class TestTodoStore:
    def test_empty(self):
        store = TodoStore()
        assert store.todos == []
        assert store.last_poll is None
        assert store.processed_email_ids == []

    def test_serialization_roundtrip(self):
        todo = TodoItem(
            rule_id="rule-1", rule_name="R", email_id="e1",
            subject="S", sender="s@b.com", todo_text="T",
            mail_snapshot=MailSnapshot(subject="S", sender="s@b.com"),
        )
        store = TodoStore(
            todos=[todo],
            last_poll="2026-04-09T10:00:00",
            processed_email_ids=["e1"],
        )
        data = store.model_dump_json()
        restored = TodoStore.model_validate_json(data)
        assert len(restored.todos) == 1
        assert restored.last_poll == "2026-04-09T10:00:00"
        assert "e1" in restored.processed_email_ids
