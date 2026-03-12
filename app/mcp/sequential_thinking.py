"""
Sequential Thinking - Lokale Implementation für strukturiertes Denken.

Implementiert das Sequential-Thinking-Pattern lokal, ohne externen MCP-Server.
Wird für komplexe Planungsaufgaben und Fehleranalysen verwendet.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class ThinkingType(Enum):
    """Typ des Denkschritts."""
    ANALYSIS = "analysis"           # Problemanalyse
    HYPOTHESIS = "hypothesis"       # Hypothese aufstellen
    VERIFICATION = "verification"   # Hypothese prüfen
    PLANNING = "planning"           # Schritte planen
    DECISION = "decision"           # Entscheidung treffen
    REVISION = "revision"           # Überarbeitung
    CONCLUSION = "conclusion"       # Schlussfolgerung


@dataclass
class ThinkingStep:
    """Ein einzelner Denkschritt."""
    step_number: int
    type: ThinkingType
    title: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    confidence: float = 0.5         # 0.0 - 1.0
    dependencies: List[int] = field(default_factory=list)  # Abhängige Schritte
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "dependencies": self.dependencies,
            "metadata": self.metadata
        }


@dataclass
class ThinkingSession:
    """Eine komplette Thinking-Session."""
    session_id: str
    query: str
    steps: List[ThinkingStep] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    final_conclusion: Optional[str] = None
    total_branches: int = 0

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def current_step(self) -> int:
        return len(self.steps)

    def add_step(self, step: ThinkingStep) -> None:
        self.steps.append(step)

    def get_context(self, max_steps: int = 5) -> str:
        """Gibt den Kontext der letzten Schritte zurück."""
        recent = self.steps[-max_steps:] if len(self.steps) > max_steps else self.steps
        lines = []
        for step in recent:
            lines.append(f"[Schritt {step.step_number}] {step.type.value}: {step.title}")
            lines.append(f"  {step.content[:200]}...")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "query": self.query,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "final_conclusion": self.final_conclusion,
            "total_branches": self.total_branches
        }


class SequentialThinking:
    """
    Lokale Implementation von Sequential Thinking.

    Ermöglicht strukturiertes, schrittweises Denken für:
    - Komplexe Fehleranalysen
    - Planungsaufgaben
    - Multi-Step Problemlösung

    Funktioniert ohne externen MCP-Server.
    """

    def __init__(
        self,
        llm_callback: Optional[Callable] = None,
        event_callback: Optional[Callable] = None
    ):
        """
        Args:
            llm_callback: Optional - Callback für LLM-Aufrufe.
                          Signatur: async def callback(prompt: str) -> str
            event_callback: Optional - Callback für Progress-Events.
                           Signatur: async def callback(event_type: str, data: dict) -> None
        """
        self.llm_callback = llm_callback
        self.event_callback = event_callback
        self._sessions: Dict[str, ThinkingSession] = {}
        self._current_session: Optional[ThinkingSession] = None
        self._session_start_times: Dict[str, float] = {}

    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Sendet ein Progress-Event an den registrierten Callback."""
        if self.event_callback:
            try:
                if asyncio.iscoroutinefunction(self.event_callback):
                    await self.event_callback(event_type, data)
                else:
                    self.event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"[SeqThink] Error emitting event: {e}")

    @property
    def is_enabled(self) -> bool:
        """Prüft ob Sequential Thinking aktiviert ist."""
        return settings.mcp.sequential_thinking_enabled

    @property
    def max_steps(self) -> int:
        return settings.mcp.max_thinking_steps

    def create_session(self, query: str, estimated_steps: Optional[int] = None) -> ThinkingSession:
        """Erstellt eine neue Thinking-Session."""
        import uuid
        session_id = str(uuid.uuid4())[:12]
        session = ThinkingSession(session_id=session_id, query=query)
        self._sessions[session_id] = session
        self._current_session = session
        self._session_start_times[session_id] = time.monotonic()

        if settings.mcp.debug_logging:
            logger.debug(f"[SeqThink] New session: {session_id} for query: {query[:50]}...")

        return session

    async def create_session_async(self, query: str, estimated_steps: Optional[int] = None) -> ThinkingSession:
        """Erstellt eine neue Thinking-Session mit Event-Emission."""
        session = self.create_session(query, estimated_steps)

        # MCP Start Event emittieren
        await self._emit_event("mcp_start", {
            "tool_name": "sequential_thinking",
            "session_id": session.session_id,
            "query": query[:200] if len(query) > 200 else query,
            "estimated_steps": estimated_steps or self.max_steps
        })

        return session

    def get_session(self, session_id: str) -> Optional[ThinkingSession]:
        """Gibt eine Session zurück."""
        return self._sessions.get(session_id)

    def add_step(
        self,
        session_id: str,
        step_type: ThinkingType,
        title: str,
        content: str,
        confidence: float = 0.5,
        dependencies: List[int] = None
    ) -> ThinkingStep:
        """Fügt einen Denkschritt zu einer Session hinzu."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        if session.current_step >= self.max_steps:
            raise ValueError(f"Max steps ({self.max_steps}) reached")

        step = ThinkingStep(
            step_number=session.current_step + 1,
            type=step_type,
            title=title,
            content=content,
            confidence=confidence,
            dependencies=dependencies or []
        )

        session.add_step(step)

        if settings.mcp.debug_logging:
            logger.debug(f"[SeqThink:{session_id}] Step {step.step_number}: {step_type.value} - {title}")

        return step

    async def add_step_async(
        self,
        session_id: str,
        step_type: ThinkingType,
        title: str,
        content: str,
        confidence: float = 0.5,
        dependencies: List[int] = None
    ) -> ThinkingStep:
        """Fügt einen Denkschritt hinzu und emittiert Event."""
        step = self.add_step(session_id, step_type, title, content, confidence, dependencies)

        # MCP Step Event emittieren
        await self._emit_event("mcp_step", {
            "tool_name": "sequential_thinking",
            "session_id": session_id,
            "step_number": step.step_number,
            "step_type": step_type.value,
            "title": title,
            "content": content[:300] if len(content) > 300 else content,
            "confidence": confidence,
            "is_final": step_type == ThinkingType.CONCLUSION
        })

        return step

    def complete_session(self, session_id: str, conclusion: str) -> ThinkingSession:
        """Schließt eine Session mit einer Schlussfolgerung ab."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        session.completed_at = datetime.utcnow().isoformat()
        session.final_conclusion = conclusion

        # Füge Conclusion als letzten Schritt hinzu
        self.add_step(
            session_id,
            ThinkingType.CONCLUSION,
            "Schlussfolgerung",
            conclusion,
            confidence=0.8
        )

        if settings.mcp.debug_logging:
            logger.debug(f"[SeqThink:{session_id}] Completed with {len(session.steps)} steps")

        return session

    async def complete_session_async(self, session_id: str, conclusion: str) -> ThinkingSession:
        """Schließt eine Session ab und emittiert Event."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        session.completed_at = datetime.utcnow().isoformat()
        session.final_conclusion = conclusion

        # Füge Conclusion als letzten Schritt hinzu
        await self.add_step_async(
            session_id,
            ThinkingType.CONCLUSION,
            "Schlussfolgerung",
            conclusion,
            confidence=0.8
        )

        # Dauer berechnen
        start_time = self._session_start_times.get(session_id, time.monotonic())
        duration_ms = int((time.monotonic() - start_time) * 1000)

        # MCP Complete Event emittieren
        await self._emit_event("mcp_complete", {
            "tool_name": "sequential_thinking",
            "session_id": session_id,
            "total_steps": len(session.steps),
            "final_conclusion": conclusion[:500] if len(conclusion) > 500 else conclusion,
            "duration_ms": duration_ms
        })

        if settings.mcp.debug_logging:
            logger.debug(f"[SeqThink:{session_id}] Completed with {len(session.steps)} steps in {duration_ms}ms")

        return session

    async def think(
        self,
        query: str,
        context: Optional[str] = None,
        max_steps: Optional[int] = None,
        emit_events: bool = True
    ) -> ThinkingSession:
        """
        Führt strukturiertes Denken durch.

        Args:
            query: Die zu analysierende Frage/Problem
            context: Optional zusätzlicher Kontext
            max_steps: Optional - Override für max_steps
            emit_events: Ob Progress-Events emittiert werden sollen

        Returns:
            ThinkingSession mit allen Schritten
        """
        effective_max = max_steps or self.max_steps

        if not self.is_enabled:
            # Fallback: Einfache Session ohne LLM
            session = self.create_session(query)
            self.add_step(
                session.session_id,
                ThinkingType.ANALYSIS,
                "Direkte Analyse",
                f"Sequential Thinking deaktiviert. Query: {query}",
                confidence=0.5
            )
            return self.complete_session(session.session_id, "Sequential Thinking ist deaktiviert.")

        # Session erstellen mit Event
        if emit_events and self.event_callback:
            session = await self.create_session_async(query, effective_max)
        else:
            session = self.create_session(query)

        try:
            # Schritt 1: Problemanalyse
            if emit_events and self.event_callback:
                await self.add_step_async(
                    session.session_id,
                    ThinkingType.ANALYSIS,
                    "Problemanalyse",
                    f"Analysiere: {query}\n\nKontext: {context or 'Kein zusätzlicher Kontext'}",
                    confidence=0.6
                )
            else:
                self.add_step(
                    session.session_id,
                    ThinkingType.ANALYSIS,
                    "Problemanalyse",
                    f"Analysiere: {query}\n\nKontext: {context or 'Kein zusätzlicher Kontext'}",
                    confidence=0.6
                )

            # Wenn LLM-Callback verfügbar, nutze ihn für weitere Schritte
            if self.llm_callback:
                await self._think_with_llm(session, effective_max, emit_events)
            else:
                # Ohne LLM: Grundlegende Strukturierung
                await self._think_without_llm_async(session, query, emit_events)

        except asyncio.TimeoutError:
            await self._emit_event("mcp_error", {
                "tool_name": "sequential_thinking",
                "session_id": session.session_id,
                "error": "Timeout erreicht"
            })
            self.add_step(
                session.session_id,
                ThinkingType.CONCLUSION,
                "Timeout",
                "Thinking-Prozess hat Timeout erreicht.",
                confidence=0.3
            )
            session.completed_at = datetime.utcnow().isoformat()
            session.final_conclusion = "Prozess durch Timeout beendet."

        except Exception as e:
            logger.error(f"[SeqThink] Error in think(): {e}")
            await self._emit_event("mcp_error", {
                "tool_name": "sequential_thinking",
                "session_id": session.session_id,
                "error": str(e)
            })
            self.add_step(
                session.session_id,
                ThinkingType.CONCLUSION,
                "Fehler",
                f"Fehler im Thinking-Prozess: {str(e)}",
                confidence=0.2
            )
            session.completed_at = datetime.utcnow().isoformat()
            session.final_conclusion = f"Fehler: {str(e)}"

        return session

    async def _think_with_llm(self, session: ThinkingSession, max_steps: int, emit_events: bool = True) -> None:
        """Thinking mit LLM-Unterstützung."""
        thinking_prompt = self._build_thinking_prompt(session)

        for step_num in range(2, max_steps + 1):  # Step 1 ist bereits Analyse
            if session.is_complete:
                break

            # Progress Event
            if emit_events and self.event_callback:
                progress_percent = int((step_num / max_steps) * 100)
                await self._emit_event("mcp_progress", {
                    "tool_name": "sequential_thinking",
                    "session_id": session.session_id,
                    "progress_percent": progress_percent,
                    "current_phase": f"Schritt {step_num}/{max_steps}",
                    "message": "Denke nach..."
                })

            # LLM für nächsten Schritt befragen
            response = await asyncio.wait_for(
                self.llm_callback(thinking_prompt),
                timeout=settings.mcp.thinking_timeout_seconds
            )

            # Response parsen
            step_type, title, content, should_continue = self._parse_thinking_response(response)

            # Step hinzufügen mit Event
            if emit_events and self.event_callback:
                await self.add_step_async(
                    session.session_id,
                    step_type,
                    title,
                    content,
                    confidence=0.7
                )
            else:
                self.add_step(
                    session.session_id,
                    step_type,
                    title,
                    content,
                    confidence=0.7
                )

            if not should_continue or step_type == ThinkingType.CONCLUSION:
                if emit_events and self.event_callback:
                    await self.complete_session_async(session.session_id, content)
                else:
                    session.completed_at = datetime.utcnow().isoformat()
                    session.final_conclusion = content
                break

            # Prompt für nächsten Schritt aktualisieren
            thinking_prompt = self._build_continuation_prompt(session)

    def _think_without_llm(self, session: ThinkingSession, query: str) -> None:
        """Grundlegende Strukturierung ohne LLM."""
        # Hypothese
        self.add_step(
            session.session_id,
            ThinkingType.HYPOTHESIS,
            "Initiale Hypothese",
            f"Basierend auf der Anfrage '{query}' werden mögliche Ansätze identifiziert.",
            confidence=0.5
        )

        # Planung
        self.add_step(
            session.session_id,
            ThinkingType.PLANNING,
            "Lösungsansatz",
            "Empfohlene Vorgehensweise:\n1. Kontext analysieren\n2. Relevante Informationen sammeln\n3. Lösung entwickeln",
            confidence=0.5
        )

        # Conclusion
        self.complete_session(
            session.session_id,
            "Strukturierte Analyse abgeschlossen. Für detailliertere Ergebnisse wird LLM-Integration empfohlen."
        )

    async def _think_without_llm_async(self, session: ThinkingSession, query: str, emit_events: bool = True) -> None:
        """Grundlegende Strukturierung ohne LLM mit Event-Emission."""
        if emit_events and self.event_callback:
            # Hypothese
            await self.add_step_async(
                session.session_id,
                ThinkingType.HYPOTHESIS,
                "Initiale Hypothese",
                f"Basierend auf der Anfrage '{query}' werden mögliche Ansätze identifiziert.",
                confidence=0.5
            )

            # Planung
            await self.add_step_async(
                session.session_id,
                ThinkingType.PLANNING,
                "Lösungsansatz",
                "Empfohlene Vorgehensweise:\n1. Kontext analysieren\n2. Relevante Informationen sammeln\n3. Lösung entwickeln",
                confidence=0.5
            )

            # Conclusion
            await self.complete_session_async(
                session.session_id,
                "Strukturierte Analyse abgeschlossen. Für detailliertere Ergebnisse wird LLM-Integration empfohlen."
            )
        else:
            self._think_without_llm(session, query)

    def _build_thinking_prompt(self, session: ThinkingSession) -> str:
        """Erstellt den Prompt für den Thinking-Prozess."""
        return f"""Du bist ein strukturierter Denker. Analysiere das folgende Problem schrittweise.

PROBLEM:
{session.query}

BISHERIGE SCHRITTE:
{session.get_context()}

Dein nächster Denkschritt sollte einer dieser Typen sein:
- HYPOTHESIS: Eine Vermutung aufstellen
- VERIFICATION: Eine Hypothese prüfen
- PLANNING: Konkrete Schritte planen
- DECISION: Eine Entscheidung treffen
- REVISION: Bisheriges überdenken
- CONCLUSION: Finale Schlussfolgerung (wenn fertig)

Antworte im Format:
TYPE: [typ]
TITLE: [kurzer Titel]
CONTENT: [Inhalt des Schritts]
CONTINUE: [yes/no]
"""

    def _build_continuation_prompt(self, session: ThinkingSession) -> str:
        """Erstellt den Prompt für die Fortsetzung."""
        return f"""Setze die strukturierte Analyse fort.

URSPRÜNGLICHES PROBLEM:
{session.query}

BISHERIGE SCHRITTE:
{session.get_context()}

Was ist der nächste logische Denkschritt?

Antworte im Format:
TYPE: [typ]
TITLE: [kurzer Titel]
CONTENT: [Inhalt des Schritts]
CONTINUE: [yes/no]
"""

    def _parse_thinking_response(self, response: str) -> tuple[ThinkingType, str, str, bool]:
        """Parst die LLM-Antwort."""
        # Defaults
        step_type = ThinkingType.ANALYSIS
        title = "Denkschritt"
        content = response
        should_continue = True

        # TYPE extrahieren
        type_match = re.search(r'TYPE:\s*(\w+)', response, re.IGNORECASE)
        if type_match:
            type_str = type_match.group(1).upper()
            type_mapping = {
                'ANALYSIS': ThinkingType.ANALYSIS,
                'HYPOTHESIS': ThinkingType.HYPOTHESIS,
                'VERIFICATION': ThinkingType.VERIFICATION,
                'PLANNING': ThinkingType.PLANNING,
                'DECISION': ThinkingType.DECISION,
                'REVISION': ThinkingType.REVISION,
                'CONCLUSION': ThinkingType.CONCLUSION
            }
            step_type = type_mapping.get(type_str, ThinkingType.ANALYSIS)

        # TITLE extrahieren
        title_match = re.search(r'TITLE:\s*(.+?)(?:\n|CONTENT:|$)', response, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()

        # CONTENT extrahieren
        content_match = re.search(r'CONTENT:\s*(.+?)(?:\nCONTINUE:|$)', response, re.IGNORECASE | re.DOTALL)
        if content_match:
            content = content_match.group(1).strip()

        # CONTINUE extrahieren
        continue_match = re.search(r'CONTINUE:\s*(yes|no)', response, re.IGNORECASE)
        if continue_match:
            should_continue = continue_match.group(1).lower() == 'yes'

        return step_type, title, content, should_continue

    def format_session_for_context(self, session: ThinkingSession) -> str:
        """Formatiert eine Session für den Agent-Kontext."""
        lines = [
            "=== SEQUENTIAL THINKING ===",
            f"Session: {session.session_id}",
            f"Query: {session.query}",
            ""
        ]

        for step in session.steps:
            lines.append(f"[{step.step_number}] {step.type.value.upper()}: {step.title}")
            lines.append(f"    {step.content}")
            lines.append("")

        if session.final_conclusion:
            lines.append("=== CONCLUSION ===")
            lines.append(session.final_conclusion)

        return "\n".join(lines)


# Singleton
_sequential_thinking: Optional[SequentialThinking] = None


def get_sequential_thinking(
    llm_callback: Optional[Callable] = None,
    event_callback: Optional[Callable] = None
) -> SequentialThinking:
    """Gibt die Singleton-Instanz zurück."""
    global _sequential_thinking
    if _sequential_thinking is None:
        _sequential_thinking = SequentialThinking(llm_callback, event_callback)
    else:
        # Callbacks aktualisieren wenn übergeben
        if llm_callback and _sequential_thinking.llm_callback is None:
            _sequential_thinking.llm_callback = llm_callback
        if event_callback and _sequential_thinking.event_callback is None:
            _sequential_thinking.event_callback = event_callback
    return _sequential_thinking


def set_event_callback(callback: Optional[Callable]) -> None:
    """Setzt den Event-Callback für die bestehende Instanz."""
    global _sequential_thinking
    if _sequential_thinking:
        _sequential_thinking.event_callback = callback
