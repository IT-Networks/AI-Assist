"""
ThinkingEngine - Orchestriert strukturiertes Denken mit UI-Integration.

Verbindet SequentialThinking mit dem Agent-Event-System für
Echtzeit-Visualisierung im Frontend.

Features:
- Automatische Thinking-Aktivierung basierend auf Komplexität
- SSE Events für Live-UI-Updates
- Thinking-Modes: QUICK, NORMAL, DEEP, ULTRA
- Integration mit Memory (zukünftig)
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.mcp.sequential_thinking import (
    get_sequential_thinking,
    SequentialThinking,
    ThinkingSession,
    ThinkingStep,
    ThinkingType
)

logger = logging.getLogger(__name__)


class ThinkingMode(Enum):
    """Thinking-Modi mit unterschiedlicher Tiefe."""
    QUICK = "quick"      # 1-2 Schritte, einfache Fragen
    NORMAL = "normal"    # 3-5 Schritte, Standard
    DEEP = "deep"        # 5-10 Schritte, komplexe Probleme
    ULTRA = "ultra"      # 10-15 Schritte, Architektur-Entscheidungen

    @property
    def max_steps(self) -> int:
        return {
            ThinkingMode.QUICK: 2,
            ThinkingMode.NORMAL: 5,
            ThinkingMode.DEEP: 10,
            ThinkingMode.ULTRA: 15
        }[self]

    @property
    def description(self) -> str:
        return {
            ThinkingMode.QUICK: "Schnelle Analyse (1-2 Schritte)",
            ThinkingMode.NORMAL: "Standard-Analyse (3-5 Schritte)",
            ThinkingMode.DEEP: "Tiefgehende Analyse (5-10 Schritte)",
            ThinkingMode.ULTRA: "Umfassende Analyse (10-15 Schritte)"
        }[self]


@dataclass
class ThinkingResult:
    """Ergebnis einer Thinking-Session."""
    session: ThinkingSession
    mode: ThinkingMode
    complexity_score: float
    duration_ms: int
    auto_activated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session.session_id,
            "mode": self.mode.value,
            "complexity_score": self.complexity_score,
            "duration_ms": self.duration_ms,
            "auto_activated": self.auto_activated,
            "steps_count": len(self.session.steps),
            "conclusion": self.session.final_conclusion,
            "is_complete": self.session.is_complete
        }


class ThinkingEngine:
    """
    Orchestriert den Thinking-Prozess mit Event-Integration.

    Verbindet:
    - SequentialThinking für strukturiertes Denken
    - Event-System für UI-Updates
    - Memory für Kontext-Persistenz (zukünftig)

    Usage:
        engine = ThinkingEngine(event_emitter=my_callback)
        result = await engine.think("Warum schlägt der Build fehl?")
    """

    def __init__(
        self,
        llm_callback: Optional[Callable] = None,
        event_emitter: Optional[Callable] = None
    ):
        """
        Args:
            llm_callback: Async function für LLM-Aufrufe.
                         Signatur: async def callback(prompt: str) -> str
            event_emitter: Async function für Event-Emission.
                          Signatur: async def callback(event_type: str, data: dict) -> None
        """
        self.llm_callback = llm_callback
        self.event_emitter = event_emitter
        self._sequential = get_sequential_thinking(llm_callback, self._handle_thinking_event)
        self._active_sessions: Dict[str, ThinkingResult] = {}
        self._start_times: Dict[str, float] = {}

    @property
    def is_enabled(self) -> bool:
        """Prüft ob Thinking aktiviert ist."""
        return settings.mcp.sequential_thinking_enabled

    @property
    def always_visible(self) -> bool:
        """Ob Thinking-UI immer sichtbar sein soll."""
        # Kann später aus Config kommen
        return True

    async def _handle_thinking_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Handler für Events vom SequentialThinking.
        Leitet an den externen Event-Emitter weiter.
        """
        if self.event_emitter:
            try:
                # Event-Typ zu MCP_* konvertieren für Agent-Kompatibilität
                mapped_type = self._map_event_type(event_type)

                if asyncio.iscoroutinefunction(self.event_emitter):
                    await self.event_emitter(mapped_type, data)
                else:
                    self.event_emitter(mapped_type, data)

            except Exception as e:
                logger.warning(f"[ThinkingEngine] Error emitting event: {e}")

    def _map_event_type(self, event_type: str) -> str:
        """Mappt interne Event-Typen zu Agent-Event-Typen."""
        mapping = {
            "mcp_start": "MCP_START",
            "mcp_step": "MCP_STEP",
            "mcp_progress": "MCP_PROGRESS",
            "mcp_complete": "MCP_COMPLETE",
            "mcp_error": "MCP_ERROR"
        }
        return mapping.get(event_type, event_type)

    def determine_mode(self, query: str, complexity: float) -> ThinkingMode:
        """
        Bestimmt den Thinking-Mode basierend auf Query und Komplexität.

        Args:
            query: Die Benutzeranfrage
            complexity: Berechneter Komplexitätsscore (0.0-1.0)

        Returns:
            Empfohlener ThinkingMode
        """
        # Keywords für tiefere Analyse
        deep_keywords = [
            "architektur", "design", "refactor", "migration",
            "performance", "security", "scalab",
            "architecture", "implementier", "system"
        ]
        query_lower = query.lower()
        has_deep_keyword = any(kw in query_lower for kw in deep_keywords)

        # Mode basierend auf Komplexität und Keywords
        if complexity >= 0.8 or has_deep_keyword:
            return ThinkingMode.DEEP if complexity < 0.9 else ThinkingMode.ULTRA
        elif complexity >= 0.5:
            return ThinkingMode.NORMAL
        else:
            return ThinkingMode.QUICK

    def should_auto_activate(self, query: str, is_error: bool = False) -> bool:
        """
        Prüft ob Thinking automatisch aktiviert werden sollte.

        Args:
            query: Die Benutzeranfrage
            is_error: True bei Fehleranalyse

        Returns:
            True wenn Thinking aktiviert werden soll
        """
        if not self.is_enabled:
            return False

        return self._sequential.should_auto_activate(query, is_error)

    def estimate_complexity(self, query: str) -> float:
        """Schätzt die Komplexität einer Anfrage (0.0-1.0)."""
        return self._sequential.estimate_complexity(query)

    async def think(
        self,
        query: str,
        context: Optional[str] = None,
        mode: Optional[ThinkingMode] = None,
        force: bool = False
    ) -> ThinkingResult:
        """
        Führt strukturiertes Denken durch.

        Args:
            query: Die zu analysierende Frage/Problem
            context: Optional zusätzlicher Kontext
            mode: Optional - Thinking-Mode (sonst auto-detect)
            force: True um Thinking auch bei niedriger Komplexität zu erzwingen

        Returns:
            ThinkingResult mit Session und Metadaten
        """
        start_time = time.monotonic()

        # Komplexität berechnen
        complexity = self.estimate_complexity(query)

        # Mode bestimmen
        if mode is None:
            mode = self.determine_mode(query, complexity)

        # Auto-Aktivierung prüfen
        auto_activated = False
        if not force:
            auto_activated = self.should_auto_activate(query)
            if not auto_activated and complexity < settings.mcp.min_complexity_score:
                # Zu einfach für Thinking - Fallback
                logger.debug(f"[ThinkingEngine] Skipping thinking (complexity={complexity:.2f})")
                session = self._create_simple_session(query, context)
                return ThinkingResult(
                    session=session,
                    mode=ThinkingMode.QUICK,
                    complexity_score=complexity,
                    duration_ms=0,
                    auto_activated=False
                )

        # Start-Event emittieren
        await self._emit_start_event(query, mode, complexity)

        try:
            # Thinking durchführen
            session = await self._sequential.think(
                query=query,
                context=context,
                max_steps=mode.max_steps,
                emit_events=True
            )

            duration_ms = int((time.monotonic() - start_time) * 1000)

            result = ThinkingResult(
                session=session,
                mode=mode,
                complexity_score=complexity,
                duration_ms=duration_ms,
                auto_activated=auto_activated
            )

            # In aktive Sessions speichern
            self._active_sessions[session.session_id] = result

            return result

        except Exception as e:
            logger.error(f"[ThinkingEngine] Error: {e}")
            await self._emit_error_event(str(e))
            raise

    def _create_simple_session(self, query: str, context: Optional[str]) -> ThinkingSession:
        """Erstellt eine minimale Session für einfache Anfragen."""
        session = self._sequential.create_session(query)
        self._sequential.add_step(
            session.session_id,
            ThinkingType.ANALYSIS,
            "Direkte Analyse",
            f"Anfrage ist einfach genug für direkte Bearbeitung.\n\nQuery: {query}",
            confidence=0.8
        )
        self._sequential.complete_session(
            session.session_id,
            "Direkte Bearbeitung ohne mehrstufige Analyse."
        )
        return session

    async def _emit_start_event(
        self,
        query: str,
        mode: ThinkingMode,
        complexity: float
    ) -> None:
        """Emittiert das Start-Event."""
        if self.event_emitter:
            await self._handle_thinking_event("mcp_start", {
                "tool_name": "thinking",
                "mode": mode.value,
                "mode_description": mode.description,
                "max_steps": mode.max_steps,
                "complexity_score": round(complexity, 2),
                "query": query[:200] if len(query) > 200 else query
            })

    async def _emit_error_event(self, error: str) -> None:
        """Emittiert ein Fehler-Event."""
        if self.event_emitter:
            await self._handle_thinking_event("mcp_error", {
                "tool_name": "thinking",
                "error": error
            })

    def get_session(self, session_id: str) -> Optional[ThinkingResult]:
        """Gibt eine aktive Session zurück."""
        return self._active_sessions.get(session_id)

    def get_active_sessions(self) -> List[ThinkingResult]:
        """Gibt alle aktiven Sessions zurück."""
        return list(self._active_sessions.values())

    def format_for_context(self, result: ThinkingResult) -> str:
        """Formatiert ein ThinkingResult für den LLM-Kontext."""
        return self._sequential.format_session_for_context(result.session)

    async def think_and_format(
        self,
        query: str,
        context: Optional[str] = None,
        mode: Optional[ThinkingMode] = None
    ) -> str:
        """
        Convenience-Methode: Thinking durchführen und formatiert zurückgeben.

        Args:
            query: Die Anfrage
            context: Optional zusätzlicher Kontext
            mode: Optional Thinking-Mode

        Returns:
            Formatierter String mit allen Thinking-Schritten
        """
        result = await self.think(query, context, mode, force=True)
        return self.format_for_context(result)


# Singleton
_thinking_engine: Optional[ThinkingEngine] = None


def get_thinking_engine(
    llm_callback: Optional[Callable] = None,
    event_emitter: Optional[Callable] = None
) -> ThinkingEngine:
    """Gibt die Singleton-Instanz zurück."""
    global _thinking_engine
    if _thinking_engine is None:
        _thinking_engine = ThinkingEngine(llm_callback, event_emitter)
    else:
        # Callbacks aktualisieren wenn übergeben
        if llm_callback and _thinking_engine.llm_callback is None:
            _thinking_engine.llm_callback = llm_callback
            _thinking_engine._sequential.llm_callback = llm_callback
        if event_emitter and _thinking_engine.event_emitter is None:
            _thinking_engine.event_emitter = event_emitter
            _thinking_engine._sequential.event_callback = _thinking_engine._handle_thinking_event
    return _thinking_engine


def set_event_emitter(emitter: Optional[Callable]) -> None:
    """Setzt den Event-Emitter für die bestehende Instanz."""
    global _thinking_engine
    if _thinking_engine:
        _thinking_engine.event_emitter = emitter
        _thinking_engine._sequential.event_callback = _thinking_engine._handle_thinking_event
