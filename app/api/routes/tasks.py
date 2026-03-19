"""
Task Progress API Routes.

Endpoints für Task-Fortschrittsanzeige und -verwaltung.
"""

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.task_tracker import (
    get_task_tracker,
    TaskProgress,
    TaskStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class TaskListResponse(BaseModel):
    """Antwort für Task-Liste."""
    tasks: List[dict]
    total: int
    active: int


class TaskResponse(BaseModel):
    """Antwort für einzelnen Task."""
    task: dict


class CancelResponse(BaseModel):
    """Antwort für Task-Abbruch."""
    success: bool
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{session_id}")
async def get_tasks(
    session_id: str,
    status: Optional[str] = Query(None, description="Filter nach Status"),
    limit: int = Query(50, ge=1, le=200),
) -> TaskListResponse:
    """
    Alle Tasks einer Session abrufen.

    Args:
        session_id: Session-ID
        status: Optional - Filter nach Status (pending, running, completed, failed, cancelled)
        limit: Maximale Anzahl

    Returns:
        Liste der Tasks
    """
    tracker = get_task_tracker(session_id)
    tasks = tracker.get_all_tasks()

    # Filter nach Status
    if status:
        try:
            status_enum = TaskStatus(status)
            tasks = [t for t in tasks if t.status == status_enum]
        except ValueError:
            pass

    # Sortieren nach Erstellzeit (neueste zuerst)
    tasks.sort(key=lambda t: t.created_at, reverse=True)

    # Limit anwenden
    tasks = tasks[:limit]

    active_count = sum(1 for t in tracker.get_all_tasks() if t.status == TaskStatus.RUNNING)

    return TaskListResponse(
        tasks=[t.to_dict() for t in tasks],
        total=len(tracker.get_all_tasks()),
        active=active_count,
    )


@router.get("/{session_id}/active")
async def get_active_tasks(session_id: str) -> TaskListResponse:
    """
    Alle aktiven (laufenden) Tasks einer Session.

    Args:
        session_id: Session-ID

    Returns:
        Liste der aktiven Tasks
    """
    tracker = get_task_tracker(session_id)
    active = tracker.get_active_tasks()

    return TaskListResponse(
        tasks=[t.to_dict() for t in active],
        total=len(active),
        active=len(active),
    )


@router.get("/{session_id}/{task_id}")
async def get_task(session_id: str, task_id: str) -> TaskResponse:
    """
    Einzelnen Task abrufen.

    Args:
        session_id: Session-ID
        task_id: Task-ID

    Returns:
        Task-Details
    """
    tracker = get_task_tracker(session_id)
    task = tracker.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} nicht gefunden")

    return TaskResponse(task=task.to_dict())


@router.post("/{session_id}/{task_id}/cancel")
async def cancel_task(session_id: str, task_id: str) -> CancelResponse:
    """
    Bricht einen laufenden Task ab.

    Args:
        session_id: Session-ID
        task_id: Task-ID

    Returns:
        Erfolgsmeldung
    """
    tracker = get_task_tracker(session_id)
    task = tracker.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} nicht gefunden")

    if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
        return CancelResponse(
            success=False,
            message=f"Task kann nicht abgebrochen werden (Status: {task.status.value})"
        )

    success = await tracker.cancel_task(task_id)

    return CancelResponse(
        success=success,
        message="Task abgebrochen" if success else "Abbruch fehlgeschlagen"
    )


@router.get("/{session_id}/stream")
async def task_stream(session_id: str) -> StreamingResponse:
    """
    SSE Stream für Task-Updates.

    Streamt Events für alle Task-Änderungen der Session.

    Event-Typen:
    - task_started: Task wurde gestartet
    - step_started: Schritt wurde gestartet
    - step_progress: Schritt-Fortschritt aktualisiert
    - step_completed: Schritt abgeschlossen
    - step_failed: Schritt fehlgeschlagen
    - step_skipped: Schritt übersprungen
    - task_artifact: Neues Zwischenergebnis
    - task_completed: Task erfolgreich abgeschlossen
    - task_failed: Task fehlgeschlagen
    - task_cancelled: Task abgebrochen
    """
    tracker = get_task_tracker(session_id)

    async def event_generator():
        # Initial: Sende alle aktiven Tasks
        active_tasks = tracker.get_active_tasks()
        for task in active_tasks:
            yield f"event: task_snapshot\ndata: {_json_dumps(task.to_dict())}\n\n"

        # Dann: Stream neue Events
        try:
            async for event in tracker.get_events():
                event_type = event.get("type", "update")
                event_data = _json_dumps(event)
                yield f"event: {event_type}\ndata: {event_data}\n\n"
        except asyncio.CancelledError:
            logger.debug(f"[Tasks] SSE stream cancelled for session {session_id}")
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════════════════════════

def _json_dumps(data: dict) -> str:
    """JSON-Serialisierung mit orjson wenn verfügbar."""
    try:
        import orjson
        return orjson.dumps(data).decode("utf-8")
    except ImportError:
        import json
        return json.dumps(data, ensure_ascii=False)
