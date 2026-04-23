"""AudioSessionWatcher — erkennt Webex-Call-Start/Ende via Windows-Audio-Sessions.

Pollt in Intervallen (default 2s) die WASAPI-Sessions via ``pycaw`` und
emittiert High-Level-Events wenn ein Webex-Prozess aktive Audio-Session
oeffnet bzw. schliesst.

Graceful Degradation: Wenn ``pycaw`` nicht importierbar ist (Non-Windows
oder Modul fehlt), wird der Watcher in einen Null-State versetzt. Alle
Aufrufer bekommen ``False`` auf ``is_available`` und koennen eine
nutzbare Fehlermeldung ausgeben.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


# Process-Namen, die als Webex-Call gelten.
# Beobachtet in Windows: Webex.exe (alter Client), WebexHost.exe (neuerer
# Teams-Client-Wrapper), atmgr.exe (Audio-Meeting-Manager).
WEBEX_PROCESS_NAMES: frozenset = frozenset({
    "webex.exe", "webexhost.exe", "atmgr.exe",
    # Optional: Teams als Fallback falls User mal Teams nutzt.
    # Standardmaessig deaktiviert, damit keine unerwarteten Records.
})


class CallState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"


@dataclass
class CallEvent:
    """Start- oder Ende-Event einer erkannten Webex-Call-Session."""
    kind: str  # "started" | "ended"
    pid: int
    process_name: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_display_name: str = ""


class AudioSessionWatcher:
    """Pollt Windows-Audio-Sessions und emittiert Webex-Call-Events.

    Wird asynchron via ``watch()`` (AsyncIterator) oder ``run_forever()``
    mit Callback verwendet.
    """

    def __init__(
        self,
        *,
        poll_interval_seconds: float = 2.0,
        process_names: Optional[frozenset] = None,
    ) -> None:
        self._poll_interval = max(0.5, float(poll_interval_seconds))
        self._process_names = process_names or WEBEX_PROCESS_NAMES
        self._active_pids: set = set()
        self._state: CallState = CallState.IDLE
        self._stop_evt = asyncio.Event()
        self._available = _probe_pycaw_available()

    @property
    def is_available(self) -> bool:
        """True wenn pycaw nutzbar ist (Windows + Modul installiert)."""
        return self._available

    @property
    def state(self) -> CallState:
        return self._state

    def stop(self) -> None:
        """Stoppt die ``run_forever()``-Loop (nicht ``watch()``)."""
        self._stop_evt.set()

    async def run_forever(
        self,
        on_event: Callable[[CallEvent], "asyncio.Future | None"],
    ) -> None:
        """Laeuft bis ``stop()`` gerufen wird, ruft ``on_event`` je Event.

        Fehler in ``on_event`` werden geloggt aber nicht propagiert,
        damit die Detection nicht bei User-Code-Fehler stirbt.
        """
        if not self._available:
            logger.warning(
                "[audio-detection] pycaw nicht verfuegbar — Watcher inaktiv (Non-Windows oder Modul fehlt)"
            )
            return
        self._stop_evt.clear()
        while not self._stop_evt.is_set():
            try:
                events = await asyncio.to_thread(self._poll_once)
                for evt in events:
                    try:
                        result = on_event(evt)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("[audio-detection] on_event handler failed: %s", e, exc_info=True)
            except Exception as e:
                logger.error("[audio-detection] poll failed: %s", e, exc_info=True)
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> List[CallEvent]:
        """Ein einzelner Poll-Cycle — fuer Tests / on-demand-Checks."""
        if not self._available:
            return []
        return await asyncio.to_thread(self._poll_once)

    # ── Internals ──────────────────────────────────────────────────────

    def _poll_once(self) -> List[CallEvent]:
        """Sync, laeuft in to_thread. Gibt neu erkannte Events zurueck."""
        current = _active_webex_sessions(self._process_names)
        events: List[CallEvent] = []
        current_pids = {s[0] for s in current}

        # Neue Sessions → "started"
        for pid, proc_name, display_name in current:
            if pid not in self._active_pids:
                events.append(CallEvent(
                    kind="started",
                    pid=pid,
                    process_name=proc_name,
                    session_display_name=display_name,
                ))

        # Entfallene Sessions → "ended"
        for pid in self._active_pids - current_pids:
            events.append(CallEvent(
                kind="ended",
                pid=pid,
                process_name="?",
            ))

        self._active_pids = current_pids
        self._state = CallState.ACTIVE if current_pids else CallState.IDLE
        return events


def _probe_pycaw_available() -> bool:
    """Prueft ob pycaw importierbar ist (Windows + installiert)."""
    try:
        import pycaw  # noqa: F401 — wir wollen nur den Import pruefen
        return True
    except Exception as e:
        logger.info("[audio-detection] pycaw nicht geladen: %s", e)
        return False


def _active_webex_sessions(process_names: frozenset) -> List[tuple]:
    """Sync-Query aller Audio-Sessions via pycaw, filtert nach Webex-Prozessen.

    Rueckgabe: Liste von ``(pid, process_name, display_name)``-Tupeln.
    Leer wenn pycaw nicht verfuegbar.
    """
    try:
        from pycaw.pycaw import AudioUtilities  # type: ignore[import-untyped]
    except Exception:
        return []

    result: List[tuple] = []
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as e:
        logger.debug("[audio-detection] GetAllSessions failed: %s", e)
        return []

    for session in sessions:
        try:
            proc = session.Process
            if not proc:
                continue
            pname = (proc.name() or "").lower()
            if pname not in process_names:
                continue
            pid = int(proc.pid)
            display = ""
            try:
                display = session.DisplayName or ""
            except Exception:
                pass
            result.append((pid, pname, display))
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("[audio-detection] session probe failed: %s", e)
            continue
    return result
