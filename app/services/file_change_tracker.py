"""
File Change Tracker.

Trackt Dateiänderungen während Tool-Ausführungen für automatisches
Re-Indexing des Knowledge Graphs nach Prompt-Abschluss.

Lifecycle:
1. Session startet → track_session(session_id)
2. Tool schreibt Datei → record_change(session_id, path)
3. Alle Tools fertig → unlock_session + get_pending_changes
4. Auto-Indexer läuft → clear_changes(session_id)
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class ChangeType(str, Enum):
    """Art der Dateiänderung."""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChange:
    """Eine einzelne Dateiänderung."""
    path: str
    change_type: ChangeType
    timestamp: datetime
    tool_name: Optional[str] = None


@dataclass
class SessionChanges:
    """Änderungen innerhalb einer Session."""
    session_id: str
    changes: Dict[str, FileChange] = field(default_factory=dict)
    is_locked: bool = False  # Während Tool-Calls gesperrt (kein Index-Update)

    def add_change(
        self,
        path: str,
        change_type: ChangeType,
        tool_name: Optional[str] = None
    ) -> None:
        """Fügt eine Änderung hinzu (nur wenn nicht gesperrt)."""
        # Normalisiere Pfad
        normalized = str(Path(path).resolve())

        # Änderung speichern (überschreibt vorherige für gleichen Pfad)
        self.changes[normalized] = FileChange(
            path=normalized,
            change_type=change_type,
            timestamp=datetime.now(),
            tool_name=tool_name,
        )

    def get_modified_files(self) -> Set[str]:
        """Gibt alle modifizierten/erstellten Dateien zurück."""
        return {
            c.path for c in self.changes.values()
            if c.change_type in (ChangeType.CREATED, ChangeType.MODIFIED)
        }

    def get_deleted_files(self) -> Set[str]:
        """Gibt alle gelöschten Dateien zurück."""
        return {
            c.path for c in self.changes.values()
            if c.change_type == ChangeType.DELETED
        }


class FileChangeTracker:
    """
    Singleton das Dateiänderungen pro Session trackt.

    Thread-safe für parallele Tool-Ausführungen.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._sessions: Dict[str, SessionChanges] = {}
                cls._instance._session_lock = threading.Lock()
            return cls._instance

    def track_session(self, session_id: str) -> None:
        """Startet Tracking für eine Session."""
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionChanges(session_id=session_id)
                logger.debug(f"[ChangeTracker] Session gestartet: {session_id}")

    def lock_session(self, session_id: str) -> None:
        """
        Sperrt Session während Tool-Calls.

        Während der Sperre werden Änderungen gesammelt aber
        kein Auto-Index ausgeführt.
        """
        with self._session_lock:
            if session_id in self._sessions:
                self._sessions[session_id].is_locked = True

    def unlock_session(self, session_id: str) -> None:
        """Entsperrt Session nach Tool-Calls."""
        with self._session_lock:
            if session_id in self._sessions:
                self._sessions[session_id].is_locked = False

    def record_change(
        self,
        session_id: str,
        file_path: str,
        change_type: ChangeType,
        tool_name: Optional[str] = None
    ) -> None:
        """
        Zeichnet eine Dateiänderung auf.

        Args:
            session_id: Session-ID
            file_path: Pfad zur geänderten Datei
            change_type: Art der Änderung
            tool_name: Name des Tools das die Änderung verursacht hat
        """
        with self._session_lock:
            if session_id not in self._sessions:
                self.track_session(session_id)

            session = self._sessions[session_id]
            session.add_change(file_path, change_type, tool_name)
            logger.debug(f"[ChangeTracker] {change_type.value}: {file_path} (Tool: {tool_name})")

    def get_pending_changes(self, session_id: str) -> Set[str]:
        """
        Gibt alle noch nicht indexierten geänderten Dateien zurück.

        Returns:
            Set von Dateipfaden die re-indexiert werden sollten
        """
        with self._session_lock:
            if session_id not in self._sessions:
                return set()
            return self._sessions[session_id].get_modified_files()

    def get_deleted_files(self, session_id: str) -> Set[str]:
        """Gibt alle gelöschten Dateien zurück."""
        with self._session_lock:
            if session_id not in self._sessions:
                return set()
            return self._sessions[session_id].get_deleted_files()

    def clear_changes(self, session_id: str) -> None:
        """Löscht alle getrackten Änderungen (nach erfolgreichem Index)."""
        with self._session_lock:
            if session_id in self._sessions:
                count = len(self._sessions[session_id].changes)
                self._sessions[session_id].changes.clear()
                logger.debug(f"[ChangeTracker] {count} Änderungen gelöscht für Session {session_id}")

    def is_locked(self, session_id: str) -> bool:
        """Prüft ob Session gesperrt ist."""
        with self._session_lock:
            if session_id not in self._sessions:
                return False
            return self._sessions[session_id].is_locked

    def has_pending_changes(self, session_id: str) -> bool:
        """Prüft ob es ausstehende Änderungen gibt."""
        with self._session_lock:
            if session_id not in self._sessions:
                return False
            return len(self._sessions[session_id].changes) > 0

    def cleanup_session(self, session_id: str) -> None:
        """Entfernt Session-Daten komplett."""
        with self._session_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]


# Indexierbare Dateiendungen
INDEXABLE_EXTENSIONS = {
    ".java", ".py", ".ts", ".js", ".tsx", ".jsx",
    ".kt", ".scala", ".go", ".rs", ".cs",
}


def is_indexable_file(file_path: str) -> bool:
    """Prüft ob eine Datei für den Knowledge Graph indexierbar ist."""
    return Path(file_path).suffix.lower() in INDEXABLE_EXTENSIONS


# ══════════════════════════════════════════════════════════════════════════════
# Singleton Accessor
# ══════════════════════════════════════════════════════════════════════════════

def get_change_tracker() -> FileChangeTracker:
    """Gibt die singleton FileChangeTracker-Instanz zurück."""
    return FileChangeTracker()
