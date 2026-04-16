"""
ProcessRegistry: Tracking laufender Subprocess-Commands pro Session.

Erlaubt dem User, einen laufenden Command via UI-Cancel-Button abzubrechen
(POST /api/agent/cancel/{session_id}). Single-Process pro Session: ein neuer
Command in derselben Session cancellt den alten.

Nutzung:
    registry = get_process_registry()
    entry = await registry.register(session_id, process, command, workspace)
    # ... später, von cancel-endpoint:
    await registry.cancel_session(session_id)
    # ... command_runner pruefte entry.cancel_event und beendete
    await registry.cleanup(session_id)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RunningProcess:
    """Repraesentiert einen aktuell laufenden Subprocess in einer Session."""
    command_id: str
    session_id: str
    process: asyncio.subprocess.Process
    command: List[str]
    workspace: str
    started_at: float
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled_by: Optional[str] = None  # "user" | "superseded" | None

    @property
    def duration_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def to_dict(self) -> Dict:
        return {
            "command_id": self.command_id,
            "session_id": self.session_id,
            "command": self.command,
            "workspace": self.workspace,
            "duration_ms": self.duration_ms,
            "pid": self.process.pid if self.process else None,
            "cancelled_by": self.cancelled_by,
        }


class ProcessRegistry:
    """In-Memory Registry: session_id -> RunningProcess.

    Single-Worker uvicorn -> kein verteiltes Locking noetig.
    asyncio.Lock fuer Coroutine-Safety.
    """

    def __init__(self) -> None:
        self._processes: Dict[str, RunningProcess] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        session_id: str,
        process: asyncio.subprocess.Process,
        command: List[str],
        workspace: str,
    ) -> RunningProcess:
        """Registriert einen neu gestarteten Subprocess.

        Wenn die Session bereits einen laufenden Command hat, wird dessen
        cancel_event gesetzt (superseded). Der Caller des alten Commands
        muss selbst Cleanup machen.
        """
        async with self._lock:
            existing = self._processes.get(session_id)
            if existing is not None and not existing.cancel_event.is_set():
                logger.warning(
                    f"[ProcessRegistry] Session {session_id}: alter Command "
                    f"{existing.command_id} wird verdraengt (superseded)"
                )
                existing.cancelled_by = "superseded"
                existing.cancel_event.set()

            entry = RunningProcess(
                command_id=str(uuid.uuid4()),
                session_id=session_id,
                process=process,
                command=list(command),
                workspace=workspace,
                started_at=time.time(),
            )
            self._processes[session_id] = entry
            logger.info(
                f"[ProcessRegistry] Registered {entry.command_id} "
                f"(session={session_id}, pid={process.pid}, "
                f"cmd={' '.join(command[:3])}...)"
            )
            return entry

    async def cancel_session(self, session_id: str) -> Optional[RunningProcess]:
        """Markiert den Command in der Session als cancelled.

        Setzt nur das Event - der command_runner-Loop entdeckt dies und
        terminiert/killed den Subprocess. Returns None wenn keiner laeuft.
        """
        async with self._lock:
            entry = self._processes.get(session_id)
            if entry is None:
                logger.info(
                    f"[ProcessRegistry] cancel_session({session_id}): "
                    f"kein laufender Command"
                )
                return None
            if entry.cancel_event.is_set():
                logger.info(
                    f"[ProcessRegistry] cancel_session({session_id}): "
                    f"bereits cancelled"
                )
                return entry
            entry.cancelled_by = "user"
            entry.cancel_event.set()
            logger.info(
                f"[ProcessRegistry] Cancelled {entry.command_id} "
                f"(session={session_id}, pid={entry.process.pid})"
            )
            return entry

    async def cleanup(self, session_id: str, command_id: Optional[str] = None) -> None:
        """Entfernt Eintrag - vom command_runner gerufen nachdem Subprocess beendet.

        Wenn command_id angegeben und nicht zur aktuellen Session-Entry passt,
        wird nichts entfernt (Race-Schutz: zwischenzeitlich neuer Command).
        """
        async with self._lock:
            entry = self._processes.get(session_id)
            if entry is None:
                return
            if command_id is not None and entry.command_id != command_id:
                logger.debug(
                    f"[ProcessRegistry] cleanup({session_id}): command_id "
                    f"mismatch ({command_id} vs {entry.command_id}), skip"
                )
                return
            self._processes.pop(session_id, None)
            logger.debug(
                f"[ProcessRegistry] Cleaned up {entry.command_id} "
                f"(session={session_id})"
            )

    def get(self, session_id: str) -> Optional[RunningProcess]:
        return self._processes.get(session_id)

    def list_sessions(self) -> List[str]:
        return list(self._processes.keys())

    def is_running(self, session_id: str) -> bool:
        entry = self._processes.get(session_id)
        return entry is not None and not entry.cancel_event.is_set()


_global_registry: Optional[ProcessRegistry] = None


def get_process_registry() -> ProcessRegistry:
    """Globaler Singleton - lazy initialisiert."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ProcessRegistry()
    return _global_registry


def reset_process_registry() -> None:
    """Nur fuer Tests - resettet den Singleton."""
    global _global_registry
    _global_registry = None
