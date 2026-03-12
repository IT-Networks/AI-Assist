"""
MCP Event Bridge - Live-Streaming von MCP-Events zum Frontend.

Ermöglicht echtes Live-Streaming von Thinking-Schritten und anderen
MCP-Events während der Tool-Ausführung (nicht erst danach).

Features:
- Non-blocking Event-Emission
- Pub/Sub Pattern für multiple Consumer
- Automatisches Cleanup bei Session-Ende
- Buffer für langsame Consumer
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class MCPEvent:
    """Ein MCP-Event für Live-Streaming."""
    event_type: str  # MCP_START, MCP_STEP, MCP_PROGRESS, MCP_COMPLETE, MCP_ERROR
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class MCPEventBridge:
    """
    Brücke zwischen MCP-Tools und dem SSE-Stream.

    Ermöglicht echtes Live-Streaming von Events während der Tool-Ausführung.
    Events werden sofort an alle Subscriber gepusht, nicht in einer Queue
    für späteres Batch-Processing gesammelt.

    Usage:
        bridge = MCPEventBridge()

        # Als Event-Callback registrieren
        sequential_thinking = get_sequential_thinking(
            llm_callback=my_llm,
            event_callback=bridge.emit_sync
        )

        # Events konsumieren (async generator)
        async for event in bridge.stream():
            yield AgentEvent(event.event_type, event.data)
    """

    def __init__(self, max_buffer: int = 100):
        """
        Args:
            max_buffer: Maximale Anzahl Events im Buffer pro Subscriber
        """
        self._max_buffer = max_buffer
        self._subscribers: Set[asyncio.Queue] = set()
        self._event_history: deque[MCPEvent] = deque(maxlen=50)  # Letzte 50 Events
        self._closed = False
        self._lock = asyncio.Lock()

    async def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Emittiert ein Event an alle Subscriber (async).

        Args:
            event_type: Event-Typ (MCP_START, MCP_STEP, etc.)
            data: Event-Daten
        """
        if self._closed:
            return

        event = MCPEvent(event_type=event_type, data=data)
        self._event_history.append(event)

        logger.debug(f"[EventBridge] Emitting {event_type}: {str(data)[:100]}...")

        # Alle Subscriber benachrichtigen (non-blocking)
        async with self._lock:
            dead_subscribers = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("[EventBridge] Subscriber queue full, dropping event")
                except Exception as e:
                    logger.warning(f"[EventBridge] Error pushing to subscriber: {e}")
                    dead_subscribers.append(queue)

            # Tote Subscriber entfernen
            for queue in dead_subscribers:
                self._subscribers.discard(queue)

    def emit_sync(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Synchroner Wrapper für emit() - für Callbacks die nicht async sind.

        Scheduled das Event im Event-Loop (non-blocking).
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Event im laufenden Loop schedulen
                asyncio.create_task(self.emit(event_type, data))
            else:
                # Fallback: synchron ausführen
                loop.run_until_complete(self.emit(event_type, data))
        except RuntimeError:
            # Kein Event-Loop verfügbar - Event in History speichern
            event = MCPEvent(event_type=event_type, data=data)
            self._event_history.append(event)
            logger.debug(f"[EventBridge] Buffered {event_type} (no event loop)")

    def subscribe(self) -> asyncio.Queue:
        """
        Erstellt eine neue Subscription für Events.

        Returns:
            Queue die Events empfängt
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_buffer)
        self._subscribers.add(queue)
        logger.debug(f"[EventBridge] New subscriber (total: {len(self._subscribers)})")
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Entfernt eine Subscription."""
        self._subscribers.discard(queue)
        logger.debug(f"[EventBridge] Subscriber removed (total: {len(self._subscribers)})")

    async def stream(self, timeout: float = 0.1) -> AsyncGenerator[MCPEvent, None]:
        """
        Async Generator für Event-Streaming.

        Yields Events sobald sie ankommen. Timeout verhindert Blocking
        wenn keine Events vorhanden sind.

        Args:
            timeout: Max. Wartezeit pro Iteration (Sekunden)

        Yields:
            MCPEvent Objekte
        """
        queue = self.subscribe()
        try:
            while not self._closed:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield event
                except asyncio.TimeoutError:
                    # Kein Event im Timeout - weitermachen (erlaubt Abbruch-Check)
                    continue
                except asyncio.CancelledError:
                    break
        finally:
            self.unsubscribe(queue)

    async def drain(self, timeout: float = 0.01) -> AsyncGenerator[MCPEvent, None]:
        """
        Drain alle wartenden Events aus einer temporären Subscription.

        Nützlich für Batch-Processing nach Tool-Ausführung.

        Args:
            timeout: Wartezeit bevor Drain beendet wird

        Yields:
            Alle wartenden MCPEvent Objekte
        """
        queue = self.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield event
                except asyncio.TimeoutError:
                    # Keine weiteren Events
                    break
        finally:
            self.unsubscribe(queue)

    def get_recent_events(self, count: int = 10) -> list[MCPEvent]:
        """Gibt die letzten N Events zurück (für Replay/Debug)."""
        return list(self._event_history)[-count:]

    def close(self) -> None:
        """Schließt die Bridge und beendet alle Streams."""
        self._closed = True
        logger.debug("[EventBridge] Closed")

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# ══════════════════════════════════════════════════════════════════════════════
# Session-basierte Bridge-Verwaltung
# ══════════════════════════════════════════════════════════════════════════════

_bridges: Dict[str, MCPEventBridge] = {}
_default_bridge: Optional[MCPEventBridge] = None


def get_event_bridge(session_id: Optional[str] = None) -> MCPEventBridge:
    """
    Gibt die Event-Bridge für eine Session zurück.

    Args:
        session_id: Optionale Session-ID. Wenn None, wird die Default-Bridge verwendet.

    Returns:
        MCPEventBridge Instanz
    """
    global _default_bridge

    if session_id:
        if session_id not in _bridges:
            _bridges[session_id] = MCPEventBridge()
            logger.debug(f"[EventBridge] Created bridge for session: {session_id}")
        return _bridges[session_id]
    else:
        if _default_bridge is None:
            _default_bridge = MCPEventBridge()
            logger.debug("[EventBridge] Created default bridge")
        return _default_bridge


def cleanup_bridge(session_id: str) -> None:
    """Räumt eine Session-Bridge auf."""
    if session_id in _bridges:
        _bridges[session_id].close()
        del _bridges[session_id]
        logger.debug(f"[EventBridge] Cleaned up bridge for session: {session_id}")


def get_default_bridge() -> MCPEventBridge:
    """Gibt die Default-Bridge zurück (für globale Events)."""
    return get_event_bridge(None)


def create_event_callback(bridge: Optional[MCPEventBridge] = None) -> Callable:
    """
    Erstellt einen Event-Callback für MCP-Tools.

    Args:
        bridge: Optionale Bridge. Wenn None, wird die Default-Bridge verwendet.

    Returns:
        Async Callback-Funktion für event_emitter Parameter
    """
    if bridge is None:
        bridge = get_default_bridge()

    async def callback(event_type: str, data: Dict[str, Any]) -> None:
        await bridge.emit(event_type, data)

    return callback
