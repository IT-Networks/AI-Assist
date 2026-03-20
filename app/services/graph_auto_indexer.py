"""
Graph Auto-Indexer.

Automatisches Re-Indexing des Knowledge Graphs nach Code-Änderungen.

Wird nach Abschluss eines Prompts (nicht während Tool-Calls) aufgerufen
um geänderte Dateien neu zu indexieren.
"""

import logging
from pathlib import Path
from typing import Set, Optional

from app.services.file_change_tracker import (
    get_change_tracker,
    is_indexable_file,
)
from app.services.knowledge_graph import (
    get_knowledge_graph_store,
    get_graph_registry,
)

logger = logging.getLogger(__name__)


async def auto_index_session_changes(session_id: str) -> int:
    """
    Indexiert alle geänderten Dateien einer Session.

    Sollte aufgerufen werden NACH Abschluss aller Tool-Calls,
    typischerweise vor dem DONE-Event.

    Args:
        session_id: Session-ID

    Returns:
        Anzahl der indexierten Dateien
    """
    tracker = get_change_tracker()

    # Nur wenn nicht gesperrt (d.h. alle Tool-Calls fertig)
    if tracker.is_locked(session_id):
        logger.debug("[AutoIndex] Session noch gesperrt, überspringe")
        return 0

    # Pending Changes holen
    pending = tracker.get_pending_changes(session_id)
    if not pending:
        return 0

    # Nur indexierbare Dateien
    indexable = {f for f in pending if is_indexable_file(f)}
    if not indexable:
        tracker.clear_changes(session_id)
        return 0

    logger.info(f"[AutoIndex] {len(indexable)} Dateien zu re-indexieren")

    # Registry prüfen
    registry = get_graph_registry()
    active = registry.get_active()
    if not active:
        logger.debug("[AutoIndex] Kein aktiver Graph, überspringe")
        tracker.clear_changes(session_id)
        return 0

    try:
        from app.services.graph_builder import get_graph_builder

        store = get_knowledge_graph_store()
        indexed_count = 0

        for file_path in indexable:
            path = Path(file_path)

            if not path.exists():
                # Datei wurde gelöscht
                deleted = store.delete_by_file(str(path))
                if deleted:
                    logger.debug(f"[AutoIndex] Gelöschte Datei entfernt: {path.name} ({deleted} Nodes)")
                continue

            # Sprache aus Extension ableiten
            ext = path.suffix.lower()
            if ext == ".java":
                language = "java"
            elif ext == ".py":
                language = "python"
            elif ext in (".ts", ".tsx", ".js", ".jsx"):
                language = "typescript"
            else:
                language = "java"  # Fallback

            try:
                # Alte Einträge löschen
                store.delete_by_file(str(path))

                # Neu indexieren
                builder = get_graph_builder(language, store)
                await builder.index_file(path)
                indexed_count += 1

                logger.debug(f"[AutoIndex] Re-indexiert: {path.name}")

            except Exception as e:
                logger.warning(f"[AutoIndex] Fehler bei {path.name}: {e}")

        # Stats aktualisieren
        if indexed_count > 0:
            stats = store.get_stats()
            registry.update_stats(active.id, stats["total_nodes"], stats["total_edges"])

        # Changes als verarbeitet markieren
        tracker.clear_changes(session_id)

        logger.info(f"[AutoIndex] Fertig: {indexed_count} Dateien indexiert")
        return indexed_count

    except Exception as e:
        logger.exception(f"[AutoIndex] Fehler: {e}")
        tracker.clear_changes(session_id)
        return 0


def record_file_change(
    session_id: str,
    file_path: str,
    change_type: str = "modified",
    tool_name: Optional[str] = None
) -> None:
    """
    Zeichnet eine Dateiänderung auf (für Tool-Integration).

    Args:
        session_id: Session-ID
        file_path: Pfad zur geänderten Datei
        change_type: "created", "modified", oder "deleted"
        tool_name: Name des Tools das die Änderung gemacht hat
    """
    from app.services.file_change_tracker import ChangeType

    tracker = get_change_tracker()

    # Change-Type konvertieren
    ct = ChangeType.MODIFIED
    if change_type == "created":
        ct = ChangeType.CREATED
    elif change_type == "deleted":
        ct = ChangeType.DELETED

    tracker.record_change(session_id, file_path, ct, tool_name)


def start_tracking(session_id: str) -> None:
    """Startet Change-Tracking für eine Session."""
    tracker = get_change_tracker()
    tracker.track_session(session_id)


def lock_tracking(session_id: str) -> None:
    """Sperrt Tracking während Tool-Calls (verhindert Index-Updates)."""
    tracker = get_change_tracker()
    tracker.lock_session(session_id)


def unlock_tracking(session_id: str) -> None:
    """Entsperrt Tracking nach Tool-Calls."""
    tracker = get_change_tracker()
    tracker.unlock_session(session_id)
