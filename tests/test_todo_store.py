"""Tests für app.services.todo_store — Todo-CRUD und Persistenz."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.models.email_models import TodoItem, MailSnapshot, TodoStore
from app.services.todo_store import TodoStoreService


@pytest.fixture
def store(tmp_path):
    """Erstellt einen TodoStoreService mit temporärem Dateipfad."""
    svc = TodoStoreService()
    svc._data = None
    # Patche den Dateipfad auf tmp
    with patch("app.services.todo_store._TODO_FILE", tmp_path / "todos.json"):
        yield svc


def _make_todo(rule_id="rule-1", email_id="e1", subject="Test", **kwargs) -> TodoItem:
    """Hilfsfunktion zum Erstellen eines TodoItems."""
    defaults = dict(
        rule_id=rule_id,
        rule_name="Test-Regel",
        email_id=email_id,
        subject=subject,
        sender="test@example.com",
        todo_text="Aufgabe erledigen",
        mail_snapshot=MailSnapshot(subject=subject, sender="test@example.com"),
    )
    defaults.update(kwargs)
    return TodoItem(**defaults)


class TestTodoStoreLoad:
    def test_load_creates_empty_store(self, store):
        data = store.load()
        assert isinstance(data, TodoStore)
        assert data.todos == []

    def test_load_reads_existing_file(self, store, tmp_path):
        # Schreibe eine Datei manuell
        todo = _make_todo()
        content = TodoStore(todos=[todo]).model_dump_json(indent=2)
        with patch("app.services.todo_store._TODO_FILE", tmp_path / "todos.json"):
            (tmp_path / "todos.json").write_text(content, encoding="utf-8")
            store._data = None
            data = store.load()
            assert len(data.todos) == 1
            assert data.todos[0].subject == "Test"


class TestTodoStoreCRUD:
    def test_add_todo(self, store):
        todo = _make_todo()
        result = store.add(todo)
        assert result.id == todo.id
        assert len(store.get_all()) == 1

    def test_add_inserts_at_front(self, store):
        t1 = _make_todo(email_id="e1", subject="First")
        t2 = _make_todo(email_id="e2", subject="Second")
        store.add(t1)
        store.add(t2)
        all_todos = store.get_all()
        assert all_todos[0].subject == "Second"
        assert all_todos[1].subject == "First"

    def test_get_by_id(self, store):
        todo = _make_todo()
        store.add(todo)
        found = store.get_by_id(todo.id)
        assert found is not None
        assert found.subject == "Test"

    def test_get_by_id_not_found(self, store):
        assert store.get_by_id("nonexistent") is None

    def test_get_all_filtered(self, store):
        t1 = _make_todo(email_id="e1")
        t2 = _make_todo(email_id="e2")
        store.add(t1)
        store.add(t2)
        store.update_status(t2.id, "done")

        assert len(store.get_all()) == 2
        assert len(store.get_all(status="new")) == 1
        assert len(store.get_all(status="done")) == 1

    def test_update_status(self, store):
        todo = _make_todo()
        store.add(todo)
        assert store.update_status(todo.id, "read") is True
        assert store.get_by_id(todo.id).status == "read"

    def test_update_status_invalid(self, store):
        todo = _make_todo()
        store.add(todo)
        assert store.update_status(todo.id, "invalid_status") is False

    def test_update_status_not_found(self, store):
        assert store.update_status("nonexistent", "done") is False

    def test_delete(self, store):
        todo = _make_todo()
        store.add(todo)
        assert store.delete(todo.id) is True
        assert len(store.get_all()) == 0

    def test_delete_not_found(self, store):
        assert store.delete("nonexistent") is False


class TestTodoStoreCounts:
    def test_counts_empty(self, store):
        counts = store.get_counts()
        assert counts == {"new": 0, "read": 0, "done": 0, "total": 0}

    def test_counts_mixed(self, store):
        store.add(_make_todo(email_id="e1"))
        store.add(_make_todo(email_id="e2"))
        store.add(_make_todo(email_id="e3"))
        store.update_status(store.get_all()[0].id, "read")
        store.update_status(store.get_all()[1].id, "done")

        counts = store.get_counts()
        assert counts["new"] == 1
        assert counts["read"] == 1
        assert counts["done"] == 1
        assert counts["total"] == 3


class TestTodoStoreDuplicateProtection:
    def test_is_processed_false(self, store):
        assert store.is_processed("unknown_id") is False

    def test_mark_processed(self, store):
        store.mark_processed("email-123")
        assert store.is_processed("email-123") is True

    def test_mark_processed_idempotent(self, store):
        store.mark_processed("email-123")
        store.mark_processed("email-123")
        data = store.load()
        assert data.processed_email_ids.count("email-123") == 1

    def test_processed_list_limit(self, store):
        for i in range(10010):
            store.mark_processed(f"email-{i}")
        data = store.load()
        assert len(data.processed_email_ids) <= 10000


class TestTodoStorePersistence:
    def test_save_and_reload(self, store, tmp_path):
        todo = _make_todo()
        with patch("app.services.todo_store._TODO_FILE", tmp_path / "todos.json"):
            store.add(todo)
            # Force reload
            store._data = None
            data = store.load()
            assert len(data.todos) == 1

    def test_update_last_poll(self, store):
        store.update_last_poll("2026-04-09T12:00:00")
        data = store.load()
        assert data.last_poll == "2026-04-09T12:00:00"
