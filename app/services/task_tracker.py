"""
Task Progress Tracker Service.

Verfolgt den Fortschritt von Agent-Tasks und emittiert Events für Live-Updates.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, AsyncGenerator

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Status eines Tasks."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Status eines einzelnen Schritts."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskArtifact:
    """Ein Zwischenergebnis eines Tasks."""
    id: str
    type: str  # "code", "search_result", "analysis", "file", "error"
    summary: str
    data: Any = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class TaskStep:
    """Ein einzelner Schritt innerhalb eines Tasks."""
    id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: float = 0.0  # 0.0 - 1.0
    details: Optional[str] = None
    artifacts: List[TaskArtifact] = field(default_factory=list)
    sub_steps: List["TaskStep"] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "progress": self.progress,
            "details": self.details,
            "artifacts": [asdict(a) for a in self.artifacts],
            "sub_steps": [s.to_dict() for s in self.sub_steps],
            "error": self.error,
        }


@dataclass
class TaskProgress:
    """Gesamtfortschritt eines Tasks."""
    task_id: str
    session_id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Progress
    total_steps: int = 0
    completed_steps: int = 0
    current_step: Optional[str] = None
    current_step_index: int = -1
    progress_percent: float = 0.0
    estimated_remaining_seconds: Optional[int] = None

    # Steps
    steps: List[TaskStep] = field(default_factory=list)

    # Artifacts (Zwischenergebnisse)
    artifacts: List[TaskArtifact] = field(default_factory=list)

    # Error info
    error: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "title": self.title,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "current_step": self.current_step,
            "current_step_index": self.current_step_index,
            "progress_percent": self.progress_percent,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "steps": [s.to_dict() for s in self.steps],
            "artifacts": [asdict(a) for a in self.artifacts],
            "error": self.error,
            "metadata": self.metadata,
        }


class TaskTracker:
    """
    Verwaltet Task-Fortschritt und emittiert Events.

    Verwendung:
        tracker = TaskTracker(session_id)
        task_id = tracker.create_task("Analyse", ["Lesen", "Verarbeiten", "Speichern"])

        await tracker.start_task(task_id)
        await tracker.start_step(task_id, 0, "Lese Datei...")
        await tracker.add_artifact(task_id, TaskArtifact(...))
        await tracker.complete_step(task_id, 0)
        ...
        await tracker.complete_task(task_id)
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.tasks: Dict[str, TaskProgress] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._start_times: Dict[str, datetime] = {}  # task_id -> start time

    def create_task(self, title: str, steps: List[str], metadata: Dict[str, Any] = None) -> str:
        """
        Erstellt einen neuen Task mit definierten Schritten.

        Args:
            title: Titel des Tasks
            steps: Liste von Schritt-Namen
            metadata: Optionale Metadaten

        Returns:
            task_id: Eindeutige Task-ID
        """
        task_id = str(uuid.uuid4())
        task = TaskProgress(
            task_id=task_id,
            session_id=self.session_id,
            title=title,
            status=TaskStatus.PENDING,
            total_steps=len(steps),
            steps=[
                TaskStep(id=str(i), name=step_name)
                for i, step_name in enumerate(steps)
            ],
            metadata=metadata or {},
        )
        self.tasks[task_id] = task
        logger.debug(f"[TaskTracker] Created task {task_id}: {title} with {len(steps)} steps")
        return task_id

    async def start_task(self, task_id: str) -> None:
        """Startet einen Task."""
        task = self.tasks.get(task_id)
        if not task:
            logger.warning(f"[TaskTracker] Task {task_id} not found")
            return

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow().isoformat()
        self._start_times[task_id] = datetime.utcnow()

        await self._emit_event("task_started", task)

    async def start_step(self, task_id: str, step_index: int, details: str = None) -> None:
        """
        Startet einen Schritt.

        Args:
            task_id: Task-ID
            step_index: Index des Schritts (0-basiert)
            details: Optionale Details zum aktuellen Vorgang
        """
        task = self.tasks.get(task_id)
        if not task or step_index >= len(task.steps):
            return

        step = task.steps[step_index]
        step.status = StepStatus.RUNNING
        step.started_at = datetime.utcnow().isoformat()
        step.details = details

        task.current_step = step.name
        task.current_step_index = step_index

        await self._emit_event("step_started", {
            "task_id": task_id,
            "step_index": step_index,
            "step": step.to_dict(),
        })

    async def update_step_progress(
        self,
        task_id: str,
        step_index: int,
        progress: float,
        details: str = None
    ) -> None:
        """
        Aktualisiert den Fortschritt eines Schritts.

        Args:
            task_id: Task-ID
            step_index: Index des Schritts
            progress: Fortschritt (0.0 - 1.0)
            details: Optionale aktualisierte Details
        """
        task = self.tasks.get(task_id)
        if not task or step_index >= len(task.steps):
            return

        step = task.steps[step_index]
        step.progress = min(1.0, max(0.0, progress))
        if details:
            step.details = details

        # Gesamtfortschritt berechnen
        self._update_total_progress(task)

        await self._emit_event("step_progress", {
            "task_id": task_id,
            "step_index": step_index,
            "progress": step.progress,
            "details": step.details,
            "total_progress": task.progress_percent,
        })

    async def complete_step(
        self,
        task_id: str,
        step_index: int,
        artifacts: List[TaskArtifact] = None
    ) -> None:
        """
        Schließt einen Schritt ab.

        Args:
            task_id: Task-ID
            step_index: Index des Schritts
            artifacts: Optionale Artifacts die bei diesem Schritt entstanden
        """
        task = self.tasks.get(task_id)
        if not task or step_index >= len(task.steps):
            return

        step = task.steps[step_index]
        step.status = StepStatus.COMPLETED
        step.completed_at = datetime.utcnow().isoformat()
        step.progress = 1.0

        if artifacts:
            step.artifacts.extend(artifacts)
            task.artifacts.extend(artifacts)

        task.completed_steps += 1
        self._update_total_progress(task)
        self._estimate_remaining_time(task)

        await self._emit_event("step_completed", {
            "task_id": task_id,
            "step_index": step_index,
            "step": step.to_dict(),
            "total_progress": task.progress_percent,
            "estimated_remaining": task.estimated_remaining_seconds,
        })

    async def fail_step(self, task_id: str, step_index: int, error: str) -> None:
        """Markiert einen Schritt als fehlgeschlagen."""
        task = self.tasks.get(task_id)
        if not task or step_index >= len(task.steps):
            return

        step = task.steps[step_index]
        step.status = StepStatus.FAILED
        step.completed_at = datetime.utcnow().isoformat()
        step.error = error

        await self._emit_event("step_failed", {
            "task_id": task_id,
            "step_index": step_index,
            "step": step.to_dict(),
            "error": error,
        })

    async def skip_step(self, task_id: str, step_index: int, reason: str = None) -> None:
        """Überspringt einen Schritt."""
        task = self.tasks.get(task_id)
        if not task or step_index >= len(task.steps):
            return

        step = task.steps[step_index]
        step.status = StepStatus.SKIPPED
        step.details = reason or "Übersprungen"

        task.completed_steps += 1
        self._update_total_progress(task)

        await self._emit_event("step_skipped", {
            "task_id": task_id,
            "step_index": step_index,
            "step": step.to_dict(),
        })

    async def add_artifact(self, task_id: str, artifact: TaskArtifact) -> None:
        """
        Fügt ein Zwischenergebnis hinzu.

        Args:
            task_id: Task-ID
            artifact: Das Artifact
        """
        task = self.tasks.get(task_id)
        if not task:
            return

        task.artifacts.append(artifact)

        await self._emit_event("task_artifact", {
            "task_id": task_id,
            "artifact": asdict(artifact),
        })

    async def complete_task(self, task_id: str) -> None:
        """Schließt einen Task erfolgreich ab."""
        task = self.tasks.get(task_id)
        if not task:
            return

        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.utcnow().isoformat()
        task.progress_percent = 100.0
        task.current_step = None
        task.estimated_remaining_seconds = 0

        await self._emit_event("task_completed", task)

    async def fail_task(self, task_id: str, error: str) -> None:
        """Markiert einen Task als fehlgeschlagen."""
        task = self.tasks.get(task_id)
        if not task:
            return

        task.status = TaskStatus.FAILED
        task.completed_at = datetime.utcnow().isoformat()
        task.error = error

        await self._emit_event("task_failed", task)

    async def cancel_task(self, task_id: str) -> bool:
        """
        Bricht einen Task ab.

        Returns:
            True wenn erfolgreich abgebrochen
        """
        task = self.tasks.get(task_id)
        if not task or task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False

        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.utcnow().isoformat()

        await self._emit_event("task_cancelled", task)
        return True

    def get_task(self, task_id: str) -> Optional[TaskProgress]:
        """Gibt einen Task zurück."""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskProgress]:
        """Gibt alle Tasks der Session zurück."""
        return list(self.tasks.values())

    def get_active_tasks(self) -> List[TaskProgress]:
        """Gibt alle laufenden Tasks zurück."""
        return [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]

    async def get_events(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generator für Task-Events.

        Yields:
            Event-Dicts mit type und data
        """
        while True:
            event = await self._event_queue.get()
            yield event

    def _update_total_progress(self, task: TaskProgress) -> None:
        """Berechnet den Gesamtfortschritt."""
        if task.total_steps == 0:
            task.progress_percent = 0.0
            return

        # Gewichteter Fortschritt basierend auf Step-Progress
        total_progress = 0.0
        for step in task.steps:
            if step.status == StepStatus.COMPLETED:
                total_progress += 1.0
            elif step.status == StepStatus.SKIPPED:
                total_progress += 1.0
            elif step.status == StepStatus.RUNNING:
                total_progress += step.progress
            # PENDING und FAILED zählen als 0

        task.progress_percent = (total_progress / task.total_steps) * 100

    def _estimate_remaining_time(self, task: TaskProgress) -> None:
        """Schätzt die verbleibende Zeit."""
        if task.task_id not in self._start_times or task.completed_steps == 0:
            task.estimated_remaining_seconds = None
            return

        start_time = self._start_times[task.task_id]
        elapsed = (datetime.utcnow() - start_time).total_seconds()

        avg_per_step = elapsed / task.completed_steps
        remaining_steps = task.total_steps - task.completed_steps
        task.estimated_remaining_seconds = int(avg_per_step * remaining_steps)

    async def _emit_event(self, event_type: str, data: Any) -> None:
        """Emittiert ein Event."""
        event = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "session_id": self.session_id,
            "data": data.to_dict() if hasattr(data, "to_dict") else data,
        }
        await self._event_queue.put(event)
        logger.debug(f"[TaskTracker] Emitted event: {event_type}")


# ══════════════════════════════════════════════════════════════════════════════
# Singleton-Accessor
# ══════════════════════════════════════════════════════════════════════════════

_trackers: Dict[str, TaskTracker] = {}


def get_task_tracker(session_id: str) -> TaskTracker:
    """
    Gibt den TaskTracker für eine Session zurück.

    Erstellt einen neuen wenn nötig.
    """
    if session_id not in _trackers:
        _trackers[session_id] = TaskTracker(session_id)
    return _trackers[session_id]


def remove_task_tracker(session_id: str) -> None:
    """Entfernt den TaskTracker einer Session."""
    if session_id in _trackers:
        del _trackers[session_id]
