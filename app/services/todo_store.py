"""
Todo Store - CRUD für todos.json mit SSE-Benachrichtigung.

Verwaltet erkannte Todos aus der E-Mail-Automation.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.email_models import TodoItem, TodoStore

logger = logging.getLogger(__name__)

# Pfad relativ zum Projekt-Root
_TODO_FILE = Path(__file__).parent.parent.parent / "todos.json"


class TodoStoreService:
    """CRUD-Service für todos.json mit SSE-Support."""

    def __init__(self):
        self._data: Optional[TodoStore] = None
        self._sse_subscribers: List[asyncio.Queue] = []

    def load(self) -> TodoStore:
        """Lade todos.json (erstelle leer wenn nicht vorhanden)."""
        if self._data is not None:
            return self._data

        if _TODO_FILE.exists():
            try:
                raw = json.loads(_TODO_FILE.read_text(encoding="utf-8"))
                self._data = TodoStore(**raw)
                logger.debug("Todo-Store geladen: %d Todos", len(self._data.todos))
            except Exception as e:
                logger.error("Fehler beim Laden von todos.json: %s", e)
                self._data = TodoStore()
        else:
            self._data = TodoStore()

        return self._data

    def save(self) -> None:
        """Speichere todos.json."""
        if self._data is None:
            return
        try:
            _TODO_FILE.write_text(
                self._data.model_dump_json(indent=2),
                encoding="utf-8",
            )
            logger.debug("Todo-Store gespeichert: %d Todos", len(self._data.todos))
        except Exception as e:
            logger.error("Fehler beim Speichern von todos.json: %s", e)

    def get_all(self, status: Optional[str] = None) -> List[TodoItem]:
        """Alle Todos, optional nach Status gefiltert."""
        store = self.load()
        if status:
            return [t for t in store.todos if t.status == status]
        return list(store.todos)

    def get_by_id(self, todo_id: str) -> Optional[TodoItem]:
        """Ein Todo nach ID."""
        store = self.load()
        for todo in store.todos:
            if todo.id == todo_id:
                return todo
        return None

    def add(self, todo: TodoItem) -> TodoItem:
        """Neues Todo hinzufügen und SSE-Event senden."""
        store = self.load()
        store.todos.insert(0, todo)  # Neueste zuerst
        self.save()

        self._notify_safe("new_todo", {
            "id": todo.id,
            "subject": todo.subject,
            "sender": todo.sender,
            "todo_text": todo.todo_text,
            "counts": self.get_counts(),
        })

        logger.info("Neues Todo erstellt: %s (Regel: %s)", todo.id, todo.rule_name)
        return todo

    def update_status(self, todo_id: str, status: str) -> bool:
        """Status eines Todos ändern."""
        if status not in ("new", "read", "done"):
            return False

        store = self.load()
        for todo in store.todos:
            if todo.id == todo_id:
                todo.status = status
                self.save()

                self._notify_safe("todo_count", self.get_counts())
                return True

        return False

    def delete(self, todo_id: str) -> bool:
        """Todo löschen und process_key aus processed-Liste entfernen (erlaubt Re-Erkennung)."""
        store = self.load()
        # Finde das Todo um email_id und rule_id zu bekommen
        deleted_todo = None
        for todo in store.todos:
            if todo.id == todo_id:
                deleted_todo = todo
                break

        before = len(store.todos)
        store.todos = [t for t in store.todos if t.id != todo_id]
        if len(store.todos) < before:
            # process_key aus processed entfernen damit die Mail erneut erkannt werden kann
            if deleted_todo:
                process_key = f"{deleted_todo.email_id}:{deleted_todo.rule_id}"
                if process_key in store.processed_email_ids:
                    store.processed_email_ids.remove(process_key)
                # Auch alte email_id-only Keys entfernen (Migration)
                if deleted_todo.email_id in store.processed_email_ids:
                    store.processed_email_ids.remove(deleted_todo.email_id)
            self.save()
            asyncio.ensure_future(self.notify("todo_count", self.get_counts()))
            return True
        return False

    def get_counts(self) -> Dict[str, int]:
        """Zähler nach Status."""
        store = self.load()
        counts = {"new": 0, "read": 0, "done": 0, "total": len(store.todos)}
        for todo in store.todos:
            if todo.status in counts:
                counts[todo.status] += 1
        return counts

    def is_processed(self, process_key: str) -> bool:
        """Prüft ob ein process_key (email_id:rule_id) bereits verarbeitet wurde."""
        store = self.load()
        return process_key in store.processed_email_ids

    def mark_processed(self, process_key: str) -> None:
        """Markiert einen process_key als verarbeitet."""
        store = self.load()
        if process_key not in store.processed_email_ids:
            store.processed_email_ids.append(process_key)
            # Begrenze die Liste auf die letzten 10000 Keys
            if len(store.processed_email_ids) > 10000:
                store.processed_email_ids = store.processed_email_ids[-10000:]
            self.save()

    def update_last_poll(self, timestamp: str) -> None:
        """Aktualisiert den letzten Poll-Zeitstempel."""
        store = self.load()
        store.last_poll = timestamp
        self.save()

    def _notify_safe(self, event: str, data: Any) -> None:
        """SSE-Benachrichtigung senden (ignoriert Fehler wenn kein Event-Loop aktiv)."""
        try:
            asyncio.ensure_future(self.notify(event, data))
        except RuntimeError:
            pass

    # ── SSE ────────────────────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Registriert einen SSE-Subscriber."""
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_subscribers.append(queue)
        logger.debug("SSE-Subscriber hinzugefügt (%d aktiv)", len(self._sse_subscribers))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Entfernt einen SSE-Subscriber."""
        if queue in self._sse_subscribers:
            self._sse_subscribers.remove(queue)
            logger.debug("SSE-Subscriber entfernt (%d aktiv)", len(self._sse_subscribers))

    async def notify(self, event: str, data: Any) -> None:
        """Sendet SSE-Event an alle Subscriber."""
        dead = []
        for queue in self._sse_subscribers:
            try:
                queue.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:
                dead.append(queue)

        for q in dead:
            self._sse_subscribers.remove(q)


# ── Singleton ──────────────────────────────────────────────────────────────────

_todo_store: Optional[TodoStoreService] = None


def get_todo_store() -> TodoStoreService:
    """Gibt den Singleton Todo-Store zurück."""
    global _todo_store
    if _todo_store is None:
        _todo_store = TodoStoreService()
    return _todo_store
