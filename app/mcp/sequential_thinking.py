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
    # v2: Branch Support
    BRANCH_START = "branch_start"   # Beginn eines alternativen Pfads
    BRANCH_MERGE = "branch_merge"   # Zusammenführung eines Branches


class BranchStatus(Enum):
    """Status eines Branches."""
    ACTIVE = "active"
    MERGED = "merged"
    ABANDONED = "abandoned"


class AssumptionStatus(Enum):
    """Status einer Annahme."""
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FALSIFIED = "falsified"


# ═══════════════════════════════════════════════════════════════════════════
# v2: Assumption Tracking
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Assumption:
    """Eine Annahme mit Tracking-Informationen."""
    id: str                                    # z.B. "A1", "A2"
    text: str                                  # Beschreibung der Annahme
    confidence: float = 0.5                    # 0.0-1.0
    critical: bool = False                     # Kann alles ändern?
    status: AssumptionStatus = AssumptionStatus.UNVERIFIED
    created_in_step: int = 0                   # In welchem Schritt erstellt
    evidence_for: List[str] = field(default_factory=list)
    evidence_against: List[str] = field(default_factory=list)
    dependent_steps: List[int] = field(default_factory=list)
    falsified_in_step: Optional[int] = None   # Wann widerlegt

    @property
    def risk_score(self) -> float:
        """Berechnet Risiko-Score: kritisch + niedrige Confidence = hohes Risiko."""
        if not self.critical:
            return 0.0
        if self.status == AssumptionStatus.VERIFIED:
            return 0.0
        if self.status == AssumptionStatus.FALSIFIED:
            return 1.0
        return (1.0 - self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "confidence": self.confidence,
            "critical": self.critical,
            "status": self.status.value,
            "created_in_step": self.created_in_step,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "dependent_steps": self.dependent_steps,
            "falsified_in_step": self.falsified_in_step,
            "risk_score": self.risk_score
        }


# ═══════════════════════════════════════════════════════════════════════════
# v2: Tool Recommendations
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolRecommendation:
    """Eine Tool-Empfehlung basierend auf Gedankeninhalt."""
    tool_name: str
    confidence: float                          # 0.0-1.0
    reason: str                                # Warum empfohlen
    suggested_args: Dict[str, Any] = field(default_factory=dict)
    triggered_by_pattern: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested_args": self.suggested_args
        }


# ═══════════════════════════════════════════════════════════════════════════
# v2: Branching
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ThinkingBranch:
    """Ein alternativer Denkpfad."""
    branch_id: str                             # z.B. "alt-db", "approach-b"
    branched_from_step: int                    # Von welchem Hauptpfad-Step
    description: str                           # Kurzbeschreibung des Branch-Ziels
    status: BranchStatus = BranchStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    merged_at: Optional[str] = None
    merge_summary: Optional[str] = None
    abandoned_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "branched_from_step": self.branched_from_step,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "merged_at": self.merged_at,
            "merge_summary": self.merge_summary,
            "abandoned_reason": self.abandoned_reason
        }


@dataclass
class ThinkingStep:
    """Ein einzelner Denkschritt mit v2 Features."""
    step_number: int
    type: ThinkingType
    title: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    confidence: float = 0.5         # 0.0 - 1.0
    dependencies: List[int] = field(default_factory=list)  # Abhängige Schritte
    metadata: Dict[str, Any] = field(default_factory=dict)

    # v2: Revision Tracking
    is_revision: bool = False
    revises_step: Optional[int] = None         # Welchen Schritt revidiert dieser
    revision_reason: Optional[str] = None
    superseded_by: Optional[int] = None        # Wenn dieser Step revidiert wurde

    # v2: Branch Support
    branch_id: Optional[str] = None            # Zu welchem Branch gehört dieser Step
    branch_from_step: Optional[int] = None     # Von welchem Step gebranched

    # v2: Assumptions (nur IDs, Details in Session)
    assumptions: List[str] = field(default_factory=list)
    invalidated_by_assumption: Optional[str] = None  # Falls durch Assumption ungültig

    # v2: Tool Recommendations (werden zur Laufzeit befüllt)
    tool_recommendations: List["ToolRecommendation"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        base = {
            "step_number": self.step_number,
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "dependencies": self.dependencies,
            "metadata": self.metadata,
        }
        # v2 fields - nur wenn gesetzt (sparse serialization)
        if self.is_revision:
            base["is_revision"] = True
            base["revises_step"] = self.revises_step
            if self.revision_reason:
                base["revision_reason"] = self.revision_reason
        if self.superseded_by is not None:
            base["superseded_by"] = self.superseded_by
        if self.branch_id:
            base["branch_id"] = self.branch_id
            base["branch_from_step"] = self.branch_from_step
        if self.assumptions:
            base["assumptions"] = self.assumptions
        if self.invalidated_by_assumption:
            base["invalidated_by_assumption"] = self.invalidated_by_assumption
        if self.tool_recommendations:
            base["tool_recommendations"] = [r.to_dict() for r in self.tool_recommendations]
        return base


@dataclass
class ThinkingSession:
    """Eine komplette Thinking-Session mit v2 Features."""
    session_id: str
    query: str
    steps: List[ThinkingStep] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    final_conclusion: Optional[str] = None

    # Dynamische Tiefensteuerung
    max_steps: int = 10                # Aktuelle max. Schritte
    initial_max_steps: int = 10        # Ursprüngliche Schätzung
    steps_added: int = 0               # Dynamisch hinzugefügte Schritte

    # v2: Branching
    branches: Dict[str, ThinkingBranch] = field(default_factory=dict)
    active_branch: Optional[str] = None   # None = Hauptpfad

    # v2: Assumptions
    assumptions: Dict[str, Assumption] = field(default_factory=dict)
    _assumption_counter: int = field(default=0, repr=False)

    # v2: Revision Statistics
    revision_count: int = 0

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def current_step(self) -> int:
        return len(self.steps)

    @property
    def total_branches(self) -> int:
        """Anzahl aller Branches (Kompatibilität mit v1)."""
        return len(self.branches)

    @property
    def main_path_steps(self) -> List[ThinkingStep]:
        """Nur Steps im Hauptpfad (ohne Branches)."""
        return [s for s in self.steps if s.branch_id is None]

    @property
    def risk_score(self) -> float:
        """Gesamtrisiko basierend auf kritischen unverifizierten Assumptions."""
        if not self.assumptions:
            return 0.0
        critical_assumptions = [a for a in self.assumptions.values() if a.critical]
        if not critical_assumptions:
            return 0.0
        return sum(a.risk_score for a in critical_assumptions) / len(critical_assumptions)

    def add_step(self, step: ThinkingStep) -> None:
        """Fügt einen Step hinzu, respektiert aktiven Branch."""
        if self.active_branch and not step.branch_id:
            step.branch_id = self.active_branch
        self.steps.append(step)

    def get_branch_steps(self, branch_id: str) -> List[ThinkingStep]:
        """Alle Steps eines bestimmten Branches."""
        return [s for s in self.steps if s.branch_id == branch_id]

    def add_assumption(self, text: str, confidence: float = 0.5, critical: bool = False) -> Assumption:
        """Erstellt und registriert eine neue Assumption."""
        self._assumption_counter += 1
        assumption = Assumption(
            id=f"A{self._assumption_counter}",
            text=text,
            confidence=confidence,
            critical=critical,
            created_in_step=self.current_step
        )
        self.assumptions[assumption.id] = assumption
        return assumption

    def get_context(self, max_steps: int = 5, include_branch: bool = True) -> str:
        """Gibt den Kontext der letzten Schritte zurück."""
        if include_branch and self.active_branch:
            # Kontext bis Branch-Punkt + Branch-Steps
            branch = self.branches.get(self.active_branch)
            if branch:
                main_context = [s for s in self.main_path_steps
                               if s.step_number <= branch.branched_from_step]
                branch_steps = self.get_branch_steps(self.active_branch)
                relevant = (main_context + branch_steps)[-max_steps:]
            else:
                relevant = self.steps[-max_steps:]
        else:
            relevant = self.main_path_steps[-max_steps:]

        lines = []
        for step in relevant:
            branch_marker = f" [{step.branch_id}]" if step.branch_id else ""
            revision_marker = f" [REV→{step.revises_step}]" if step.is_revision else ""
            lines.append(f"[{step.step_number}]{branch_marker}{revision_marker} {step.type.value}: {step.title}")
            lines.append(f"  {step.content[:200]}...")
        return "\n".join(lines)

    def get_step_tree(self) -> Dict[str, Any]:
        """Gibt die Baumstruktur für Frontend zurück."""
        tree = {"main": [], "branches": {}}

        for step in self.steps:
            step_dict = step.to_dict()
            if step.branch_id is None:
                tree["main"].append(step_dict)
            else:
                if step.branch_id not in tree["branches"]:
                    branch_info = self.branches.get(step.branch_id)
                    tree["branches"][step.branch_id] = {
                        "info": branch_info.to_dict() if branch_info else {},
                        "steps": []
                    }
                tree["branches"][step.branch_id]["steps"].append(step_dict)

        return tree

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "query": self.query,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "final_conclusion": self.final_conclusion,
            "max_steps": self.max_steps,
            "steps_added": self.steps_added,
            # v2 fields
            "branches": {k: v.to_dict() for k, v in self.branches.items()},
            "active_branch": self.active_branch,
            "assumptions": {k: v.to_dict() for k, v in self.assumptions.items()},
            "revision_count": self.revision_count,
            "risk_score": self.risk_score,
            "total_branches": self.total_branches,
            "step_tree": self.get_step_tree()
        }


# ═══════════════════════════════════════════════════════════════════════════
# v2: Tool Recommender - Pattern-based Tool Recommendations
# ═══════════════════════════════════════════════════════════════════════════

class ToolRecommender:
    """Analysiert Gedankeninhalte und empfiehlt passende Tools."""

    # Pattern -> (tool_name, base_confidence, reason_template)
    PATTERNS: List[tuple] = [
        # File Operations
        (r"(lies|read|öffne|schau|check).*(datei|file|log|code|inhalt)",
         "read_file", 0.85, "Datei lesen erwähnt"),
        (r"(such|find|glob|finde).*(datei|file|class|function|klasse)",
         "glob_search", 0.90, "Dateisuche angefordert"),
        (r"(grep|such|find|finde).*(inhalt|content|text|string|pattern)",
         "grep_search", 0.85, "Inhaltssuche angefordert"),

        # Code Modification
        (r"(änder|modif|edit|fix|korrigier|anpass)",
         "edit_file", 0.70, "Code-Änderung geplant"),
        (r"(schreib|create|erstell|neu).*(datei|file|class|modul)",
         "write_file", 0.75, "Neue Datei erstellen"),

        # Analysis
        (r"(analys|untersu|debug|trace).*(fehler|error|bug|problem)",
         "analyze_error", 0.80, "Fehleranalyse benötigt"),
        (r"(test|prüf|verif|validier).*(funktion|code|änderung|feature)",
         "run_tests", 0.75, "Tests ausführen"),

        # External
        (r"(api|endpoint|request|fetch|http|call)",
         "http_request", 0.65, "API-Aufruf erwähnt"),
        (r"(git|commit|push|branch|merge|pull)",
         "git_operation", 0.80, "Git-Operation geplant"),
    ]

    def __init__(self, available_tools: List[str] = None):
        self.available_tools = available_tools or []
        self._compiled_patterns = [
            (re.compile(p, re.IGNORECASE), tool, conf, reason)
            for p, tool, conf, reason in self.PATTERNS
        ]

    def analyze(self, thought_content: str, step_type: str = None) -> List[ToolRecommendation]:
        """Analysiert einen Gedanken und gibt Tool-Empfehlungen zurück."""
        recommendations = []

        for pattern, tool_name, confidence, reason in self._compiled_patterns:
            if pattern.search(thought_content):
                # Confidence anpassen basierend auf Step-Type
                adjusted_confidence = confidence
                if step_type == "planning":
                    adjusted_confidence = min(1.0, confidence * 1.1)
                elif step_type == "hypothesis":
                    adjusted_confidence = confidence * 0.8

                recommendations.append(ToolRecommendation(
                    tool_name=tool_name,
                    confidence=round(adjusted_confidence, 2),
                    reason=reason,
                    triggered_by_pattern=pattern.pattern
                ))

        # Sortieren nach Confidence, maximal 3
        recommendations.sort(key=lambda r: r.confidence, reverse=True)
        return recommendations[:3]


# ═══════════════════════════════════════════════════════════════════════════
# v2: Branch Manager - Manages alternative thinking paths
# ═══════════════════════════════════════════════════════════════════════════

class BranchManager:
    """Verwaltet alternative Denkpfade innerhalb einer Session."""

    MAX_BRANCHES = 5

    def __init__(self, session: ThinkingSession):
        self.session = session

    def create_branch(
        self,
        branch_id: str,
        from_step: int,
        description: str
    ) -> ThinkingBranch:
        """Erstellt einen neuen Branch."""
        if len(self.session.branches) >= self.MAX_BRANCHES:
            raise ValueError(f"Max branches ({self.MAX_BRANCHES}) erreicht")

        if branch_id in self.session.branches:
            raise ValueError(f"Branch '{branch_id}' existiert bereits")

        if from_step > self.session.current_step:
            raise ValueError(f"Branch-Punkt {from_step} existiert noch nicht")

        branch = ThinkingBranch(
            branch_id=branch_id,
            branched_from_step=from_step,
            description=description
        )
        self.session.branches[branch_id] = branch
        self.session.active_branch = branch_id

        logger.debug(f"[SeqThink] Branch '{branch_id}' erstellt von Step {from_step}")
        return branch

    def switch_branch(self, branch_id: Optional[str]) -> None:
        """Wechselt zum angegebenen Branch (None = Hauptpfad)."""
        if branch_id is not None and branch_id not in self.session.branches:
            raise ValueError(f"Branch '{branch_id}' nicht gefunden")
        self.session.active_branch = branch_id
        logger.debug(f"[SeqThink] Gewechselt zu: {branch_id or 'main'}")

    def merge_branch(self, branch_id: str, summary: str) -> None:
        """Merged einen Branch zurück zum Hauptpfad."""
        if branch_id not in self.session.branches:
            raise ValueError(f"Branch '{branch_id}' nicht gefunden")

        branch = self.session.branches[branch_id]
        branch.status = BranchStatus.MERGED
        branch.merged_at = datetime.utcnow().isoformat()
        branch.merge_summary = summary

        if self.session.active_branch == branch_id:
            self.session.active_branch = None

        logger.debug(f"[SeqThink] Branch '{branch_id}' gemerged: {summary[:50]}...")

    def abandon_branch(self, branch_id: str, reason: str) -> None:
        """Verwirft einen Branch."""
        if branch_id not in self.session.branches:
            raise ValueError(f"Branch '{branch_id}' nicht gefunden")

        branch = self.session.branches[branch_id]
        branch.status = BranchStatus.ABANDONED
        branch.abandoned_reason = reason

        if self.session.active_branch == branch_id:
            self.session.active_branch = None

        logger.debug(f"[SeqThink] Branch '{branch_id}' verworfen: {reason[:50]}...")

    def get_branch_context(self, branch_id: str) -> List[ThinkingStep]:
        """Gibt den Kontext für einen Branch zurück."""
        branch = self.session.branches.get(branch_id)
        if not branch:
            return self.session.main_path_steps

        # Steps bis zum Branch-Punkt + Branch-Steps
        main_context = [s for s in self.session.main_path_steps
                       if s.step_number <= branch.branched_from_step]
        branch_steps = self.session.get_branch_steps(branch_id)

        return main_context + branch_steps


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
        # v2: Tool Recommender für automatische Tool-Vorschläge
        self._tool_recommender = ToolRecommender()

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
        """Thinking mit LLM-Unterstützung und dynamischer Tiefensteuerung."""
        thinking_prompt = self._build_thinking_prompt(session)

        # Session mit initialen Werten konfigurieren
        session.max_steps = max_steps
        session.initial_max_steps = max_steps

        # Absolute Obergrenze um Endlos-Loops zu vermeiden
        ABSOLUTE_MAX_STEPS = 20

        step_num = 1  # Step 1 ist bereits Analyse
        while step_num < session.max_steps:
            step_num += 1

            if session.is_complete:
                break

            # Progress Event mit dynamischer Anzeige
            if emit_events and self.event_callback:
                progress_percent = int((step_num / session.max_steps) * 100)
                steps_added_info = f" (+{session.steps_added})" if session.steps_added > 0 else ""
                await self._emit_event("mcp_progress", {
                    "tool_name": "sequential_thinking",
                    "session_id": session.session_id,
                    "progress_percent": progress_percent,
                    "current_phase": f"Schritt {step_num}/{session.max_steps}{steps_added_info}",
                    "message": "Denke nach...",
                    "steps_added": session.steps_added,
                    "initial_max_steps": session.initial_max_steps,
                    "current_max_steps": session.max_steps
                })

            # LLM für nächsten Schritt befragen
            response = await asyncio.wait_for(
                self.llm_callback(thinking_prompt),
                timeout=settings.mcp.thinking_timeout_seconds
            )

            # Response parsen (jetzt als Dict mit v2 fields)
            parsed = self._parse_thinking_response(response)
            step_type = parsed["step_type"]
            title = parsed["title"]
            content = parsed["content"]
            should_continue = parsed["should_continue"]
            needs_more_steps = parsed["needs_more_steps"]
            estimated_remaining = parsed["estimated_remaining"]

            # Dynamische Tiefensteuerung: max_steps anpassen wenn LLM mehr braucht
            if needs_more_steps and estimated_remaining > 0:
                new_max = step_num + estimated_remaining
                if new_max > session.max_steps and new_max <= ABSOLUTE_MAX_STEPS:
                    old_max = session.max_steps
                    session.max_steps = new_max
                    session.steps_added += (new_max - old_max)
                    logger.info(
                        f"[SeqThink:{session.session_id}] Dynamische Anpassung: "
                        f"{old_max} -> {new_max} Schritte (+{new_max - old_max})"
                    )

            # ═══════════════════════════════════════════════════════════════
            # v2: Branch handling
            # ═══════════════════════════════════════════════════════════════
            branch_id = None
            if parsed["is_branch"] and parsed["branch_id"]:
                try:
                    branch_mgr = BranchManager(session)
                    branch_from = parsed["branch_from"] or session.current_step
                    branch_desc = parsed["branch_description"] or title
                    branch = branch_mgr.create_branch(
                        branch_id=parsed["branch_id"],
                        from_step=branch_from,
                        description=branch_desc
                    )
                    branch_id = branch.branch_id

                    if emit_events and self.event_callback:
                        await self._emit_event("mcp_branch_start", {
                            "tool_name": "sequential_thinking",
                            "session_id": session.session_id,
                            "branch_id": branch_id,
                            "from_step": branch_from,
                            "description": branch_desc
                        })
                except ValueError as e:
                    logger.warning(f"[SeqThink] Branch creation failed: {e}")

            # ═══════════════════════════════════════════════════════════════
            # v2: Revision handling
            # ═══════════════════════════════════════════════════════════════
            revises_step = None
            if parsed["is_revision"] and parsed["revises_step"]:
                revises_step = parsed["revises_step"]
                # Mark original step as superseded
                for step in session.steps:
                    if step.step_number == revises_step:
                        step.superseded_by = step_num
                        break
                session.revision_count += 1

            # ═══════════════════════════════════════════════════════════════
            # v2: Assumption handling
            # ═══════════════════════════════════════════════════════════════
            assumption_ids = []
            if parsed["assumption"]:
                assumption = session.add_assumption(
                    text=parsed["assumption"],
                    confidence=parsed["assumption_confidence"],
                    critical=parsed["assumption_critical"]
                )
                assumption.dependent_steps.append(step_num)
                assumption_ids.append(assumption.id)

                if emit_events and self.event_callback:
                    await self._emit_event("mcp_assumption_created", {
                        "tool_name": "sequential_thinking",
                        "session_id": session.session_id,
                        "assumption": assumption.to_dict()
                    })

            # ═══════════════════════════════════════════════════════════════
            # v2: Tool Recommendations
            # ═══════════════════════════════════════════════════════════════
            tool_recs = self._tool_recommender.analyze(content, step_type.value)
            tool_rec_dicts = [r.to_dict() for r in tool_recs]

            # Step hinzufügen mit Event (inkl. v2 Info)
            if emit_events and self.event_callback:
                event_data = {
                    "tool_name": "sequential_thinking",
                    "session_id": session.session_id,
                    "step_number": step_num,
                    "step_type": step_type.value,
                    "title": title,
                    "content": content[:300] if len(content) > 300 else content,
                    "confidence": 0.7,
                    "is_final": step_type == ThinkingType.CONCLUSION,
                    "needs_more_steps": needs_more_steps,
                    "estimated_remaining": estimated_remaining,
                    "total_steps": session.max_steps,
                    "steps_added": session.steps_added,
                }
                # v2 fields
                if parsed["is_revision"]:
                    event_data["is_revision"] = True
                    event_data["revises_step"] = revises_step
                    event_data["revision_reason"] = parsed["revision_reason"]
                if branch_id:
                    event_data["branch_id"] = branch_id
                    event_data["branch_from_step"] = parsed["branch_from"]
                if assumption_ids:
                    event_data["assumptions"] = assumption_ids
                if tool_rec_dicts:
                    event_data["tool_recommendations"] = tool_rec_dicts

                await self._emit_event("mcp_step", event_data)

            # Step zur Session hinzufügen mit v2 fields
            step = ThinkingStep(
                step_number=step_num,
                type=step_type,
                title=title,
                content=content,
                confidence=0.7,
                is_revision=parsed["is_revision"],
                revises_step=revises_step,
                revision_reason=parsed["revision_reason"],
                branch_id=branch_id or session.active_branch,
                branch_from_step=parsed["branch_from"],
                assumptions=assumption_ids,
                tool_recommendations=tool_recs
            )
            session.add_step(step)

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
        """Erstellt den Prompt für den Thinking-Prozess mit v2 Features."""
        # v2: Branch-Info falls aktiv
        branch_info = ""
        if session.active_branch:
            branch = session.branches.get(session.active_branch)
            if branch:
                branch_info = f"\n[AKTUELLER BRANCH: {session.active_branch} - {branch.description}]"

        # v2: Assumption-Info
        assumption_info = ""
        if session.assumptions:
            critical = [a for a in session.assumptions.values() if a.critical and a.status == AssumptionStatus.UNVERIFIED]
            if critical:
                assumption_info = "\n[OFFENE KRITISCHE ANNAHMEN: " + ", ".join(f"{a.id}:{a.text[:30]}..." for a in critical) + "]"

        return f"""Du bist ein strukturierter Denker. Analysiere das folgende Problem schrittweise.

PROBLEM:
{session.query}
{branch_info}{assumption_info}

BISHERIGE SCHRITTE:
{session.get_context()}

Dein nächster Denkschritt sollte einer dieser Typen sein:
- ANALYSIS: Problem analysieren
- HYPOTHESIS: Eine Vermutung aufstellen
- VERIFICATION: Eine Hypothese prüfen
- PLANNING: Konkrete Schritte planen
- DECISION: Eine Entscheidung treffen
- REVISION: Einen früheren Schritt korrigieren (gib REVISES_STEP an!)
- BRANCH: Alternativen Ansatz starten (gib BRANCH_ID und BRANCH_FROM an!)
- CONCLUSION: Finale Schlussfolgerung (wenn fertig)

Antworte im Format:
TYPE: [typ]
TITLE: [kurzer Titel]
CONTENT: [Inhalt des Schritts]
CONTINUE: [yes/no]
NEEDS_MORE_STEPS: [yes/no] (Brauchst du mehr Schritte?)
ESTIMATED_REMAINING: [Zahl 0-10]

Optional für REVISION:
REVISES_STEP: [Schritt-Nummer die korrigiert wird]
REVISION_REASON: [Grund für Korrektur]

Optional für BRANCH:
BRANCH_ID: [kurzer-id-name]
BRANCH_FROM: [Schritt-Nummer von der abgezweigt wird]
BRANCH_DESCRIPTION: [Was wird alternativ untersucht]

Optional - falls du eine wichtige ANNAHME machst:
ASSUMPTION: [Text der Annahme]
ASSUMPTION_CRITICAL: [yes/no - kann diese Annahme alles ändern?]
ASSUMPTION_CONFIDENCE: [0.0-1.0 - wie sicher bist du?]
"""

    def _build_continuation_prompt(self, session: ThinkingSession) -> str:
        """Erstellt den Prompt für die Fortsetzung mit v2 Features."""
        # v2: Status-Infos
        branch_info = ""
        if session.active_branch:
            branch = session.branches.get(session.active_branch)
            if branch:
                branch_info = f" [Branch: {session.active_branch}]"

        risk_info = ""
        if session.risk_score > 0.5:
            risk_info = f"\n⚠️ ACHTUNG: Hohes Risiko durch ungeprüfte kritische Annahmen (Risk-Score: {session.risk_score:.1%})"

        return f"""Setze die strukturierte Analyse fort.

URSPRÜNGLICHES PROBLEM:
{session.query}

BISHERIGE SCHRITTE:
{session.get_context()}

AKTUELLER STATUS: Schritt {session.current_step} von {session.max_steps}{branch_info}
{f"Revisionen: {session.revision_count}" if session.revision_count > 0 else ""}{risk_info}

Was ist der nächste logische Denkschritt?
- Bei Fehlern in früheren Schritten: TYPE: REVISION
- Um Alternative zu testen: TYPE: BRANCH
- Um kritische Annahmen zu validieren: TYPE: VERIFICATION
- Wenn fertig: TYPE: CONCLUSION

Antworte im Format:
TYPE: [typ]
TITLE: [kurzer Titel]
CONTENT: [Inhalt des Schritts]
CONTINUE: [yes/no]
NEEDS_MORE_STEPS: [yes/no]
ESTIMATED_REMAINING: [Zahl 0-10]

Optional für REVISION:
REVISES_STEP: [Schritt-Nummer]
REVISION_REASON: [Grund]

Optional für BRANCH:
BRANCH_ID: [kurzer-id-name]
BRANCH_FROM: [Schritt-Nummer]
BRANCH_DESCRIPTION: [Was wird alternativ untersucht]

Optional bei neuer ANNAHME:
ASSUMPTION: [Text]
ASSUMPTION_CRITICAL: [yes/no]
ASSUMPTION_CONFIDENCE: [0.0-1.0]
"""

    def _parse_thinking_response(self, response: str) -> dict:
        """
        Parst die LLM-Antwort mit v2 Features.

        Returns:
            Dict mit: step_type, title, content, should_continue,
                      needs_more_steps, estimated_remaining,
                      + v2: revision/branch/assumption fields
        """
        # Defaults
        result = {
            "step_type": ThinkingType.ANALYSIS,
            "title": "Denkschritt",
            "content": response,
            "should_continue": True,
            "needs_more_steps": False,
            "estimated_remaining": 0,
            # v2: Revision fields
            "is_revision": False,
            "revises_step": None,
            "revision_reason": None,
            # v2: Branch fields
            "is_branch": False,
            "branch_id": None,
            "branch_from": None,
            "branch_description": None,
            # v2: Assumption fields
            "assumption": None,
            "assumption_critical": False,
            "assumption_confidence": 0.5
        }

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
                'CONCLUSION': ThinkingType.CONCLUSION,
                # v2
                'BRANCH': ThinkingType.BRANCH_START,
                'BRANCH_START': ThinkingType.BRANCH_START,
                'BRANCH_MERGE': ThinkingType.BRANCH_MERGE
            }
            result["step_type"] = type_mapping.get(type_str, ThinkingType.ANALYSIS)

        # TITLE extrahieren
        title_match = re.search(r'TITLE:\s*(.+?)(?:\n|CONTENT:|$)', response, re.IGNORECASE)
        if title_match:
            result["title"] = title_match.group(1).strip()

        # CONTENT extrahieren - flexibleres Pattern
        content_match = re.search(r'CONTENT:\s*(.+?)(?:\nCONTINUE:|\nNEEDS_MORE|\nREVISES_STEP|\nBRANCH_|\nASSUMPTION:|$)', response, re.IGNORECASE | re.DOTALL)
        if content_match:
            result["content"] = content_match.group(1).strip()

        # CONTINUE extrahieren
        continue_match = re.search(r'CONTINUE:\s*(yes|no)', response, re.IGNORECASE)
        if continue_match:
            result["should_continue"] = continue_match.group(1).lower() == 'yes'

        # NEEDS_MORE_STEPS extrahieren
        more_steps_match = re.search(r'NEEDS_MORE_STEPS:\s*(yes|no)', response, re.IGNORECASE)
        if more_steps_match:
            result["needs_more_steps"] = more_steps_match.group(1).lower() == 'yes'

        # ESTIMATED_REMAINING extrahieren
        remaining_match = re.search(r'ESTIMATED_REMAINING:\s*(\d+)', response, re.IGNORECASE)
        if remaining_match:
            result["estimated_remaining"] = min(10, int(remaining_match.group(1)))

        # ═══════════════════════════════════════════════════════════════════
        # v2: REVISION fields
        # ═══════════════════════════════════════════════════════════════════
        if result["step_type"] == ThinkingType.REVISION:
            result["is_revision"] = True

            revises_match = re.search(r'REVISES_STEP:\s*(\d+)', response, re.IGNORECASE)
            if revises_match:
                result["revises_step"] = int(revises_match.group(1))

            reason_match = re.search(r'REVISION_REASON:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
            if reason_match:
                result["revision_reason"] = reason_match.group(1).strip()

        # ═══════════════════════════════════════════════════════════════════
        # v2: BRANCH fields
        # ═══════════════════════════════════════════════════════════════════
        if result["step_type"] == ThinkingType.BRANCH_START:
            result["is_branch"] = True

            branch_id_match = re.search(r'BRANCH_ID:\s*(\S+)', response, re.IGNORECASE)
            if branch_id_match:
                result["branch_id"] = branch_id_match.group(1).strip()

            branch_from_match = re.search(r'BRANCH_FROM:\s*(\d+)', response, re.IGNORECASE)
            if branch_from_match:
                result["branch_from"] = int(branch_from_match.group(1))

            branch_desc_match = re.search(r'BRANCH_DESCRIPTION:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
            if branch_desc_match:
                result["branch_description"] = branch_desc_match.group(1).strip()

        # ═══════════════════════════════════════════════════════════════════
        # v2: ASSUMPTION fields (kann bei jedem Step-Typ vorkommen)
        # ═══════════════════════════════════════════════════════════════════
        assumption_match = re.search(r'ASSUMPTION:\s*(.+?)(?:\nASSUMPTION_CRITICAL|\nASSUMPTION_CONFIDENCE|\n[A-Z_]+:|\n\n|$)', response, re.IGNORECASE | re.DOTALL)
        if assumption_match:
            result["assumption"] = assumption_match.group(1).strip()

            critical_match = re.search(r'ASSUMPTION_CRITICAL:\s*(yes|no)', response, re.IGNORECASE)
            if critical_match:
                result["assumption_critical"] = critical_match.group(1).lower() == 'yes'

            conf_match = re.search(r'ASSUMPTION_CONFIDENCE:\s*([\d.]+)', response, re.IGNORECASE)
            if conf_match:
                try:
                    result["assumption_confidence"] = min(1.0, max(0.0, float(conf_match.group(1))))
                except ValueError:
                    pass

        return result

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
