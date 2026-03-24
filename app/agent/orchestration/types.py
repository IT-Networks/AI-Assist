"""
Shared types for Agent Orchestration.

Contains enums, dataclasses, and type definitions used across orchestration modules.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.prompt_enhancer import EnrichedPrompt
    from app.agent.entity_tracker import EntityTracker
    from app.core.token_budget import TokenBudget
    from app.agent.tool_budget import ToolBudget
    from app.agent.tools import ToolResult


class AgentMode(str, Enum):
    """Betriebsmodus des Agents."""
    READ_ONLY = "read_only"           # Nur Lese-Operationen
    WRITE_WITH_CONFIRM = "write_with_confirm"  # Schreiben mit Bestaetigung
    AUTONOMOUS = "autonomous"          # Schreiben ohne Bestaetigung (gefaehrlich)
    PLAN_THEN_EXECUTE = "plan_then_execute"    # Erst planen, dann mit Bestaetigung ausfuehren
    DEBUG = "debug"                    # Fehler-Analyse: Rueckfragen + Tools zum Nachstellen


class AgentEventType(str, Enum):
    """Typen von Agent-Events."""
    TOKEN = "token"                    # Streaming-Token
    TOOL_START = "tool_start"          # Tool wird ausgefuehrt
    TOOL_RESULT = "tool_result"        # Tool-Ergebnis
    CONFIRM_REQUIRED = "confirm_required"  # User-Bestaetigung benoetigt
    CONFIRMED = "confirmed"            # User hat bestaetigt
    CANCELLED = "cancelled"            # User hat abgelehnt
    ERROR = "error"                    # Fehler
    USAGE = "usage"                    # Token-Nutzung
    COMPACTION = "compaction"          # Context wurde komprimiert
    CONTEXT_STATUS = "context_status"  # Kontext-Auslastung fuer UI
    DONE = "done"                      # Fertig
    # Sub-Agent Events
    SUBAGENT_START = "subagent_start"      # Sub-Agent-Phase beginnt (Routing laeuft)
    SUBAGENT_ROUTING = "subagent_routing"  # Routing fertig - ausgewaehlte Agenten bekannt
    SUBAGENT_DONE = "subagent_done"        # Ein Sub-Agent hat Ergebnis geliefert
    SUBAGENT_ERROR = "subagent_error"      # Sub-Agent fehlgeschlagen
    # Planning Events
    PLAN_READY = "plan_ready"              # Plan erstellt - wartet auf User-Genehmigung
    PLAN_APPROVED = "plan_approved"        # Plan genehmigt, Ausfuehrung startet
    PLAN_REJECTED = "plan_rejected"        # Plan abgelehnt
    # Debug-Modus Events
    QUESTION = "question"                  # Agent stellt Rueckfrage mit Vorschlaegen
    # MCP Progress Events
    MCP_START = "mcp_start"                # MCP-Tool startet (z.B. Sequential Thinking)
    MCP_STEP = "mcp_step"                  # Einzelner Denkschritt mit Details
    MCP_PROGRESS = "mcp_progress"          # Fortschritts-Update (Prozent)
    MCP_COMPLETE = "mcp_complete"          # MCP-Tool fertig mit Zusammenfassung
    MCP_ERROR = "mcp_error"                # Fehler waehrend MCP-Verarbeitung
    # v2: Extended MCP Events
    MCP_BRANCH_START = "mcp_branch_start"  # Branch in Sequential Thinking gestartet
    MCP_BRANCH_END = "mcp_branch_end"      # Branch merged oder abandoned
    MCP_ASSUMPTION = "mcp_assumption_created"  # Neue Assumption erstellt
    MCP_TOOL_REC = "mcp_tool_recommendation"   # Tool-Empfehlung
    # Reasoning Events (GPT-OSS, o1, o3)
    REASONING_STATUS = "reasoning_status"  # Reasoning-Modus aktiv/inaktiv
    # Task-Decomposition Events
    TASK_PLAN_CREATED = "task_plan_created"          # TaskPlan erstellt
    TASK_STARTED = "task_started"                    # Task-Ausfuehrung gestartet
    TASK_PROGRESS = "task_progress"                  # Task-Fortschritt
    TASK_COMPLETED = "task_completed"                # Task erfolgreich
    TASK_FAILED = "task_failed"                      # Task fehlgeschlagen
    TASK_CLARIFICATION = "task_clarification_needed" # Klaerungsfragen noetig
    TASK_EXECUTION_DONE = "task_execution_complete"  # Alle Tasks fertig
    # Prompt Enhancement Events
    ENHANCEMENT_START = "enhancement_start"          # MCP-Kontext-Sammlung startet
    ENHANCEMENT_PROGRESS = "enhancement_progress"    # Fortschritt bei Kontext-Sammlung
    ENHANCEMENT_COMPLETE = "enhancement_complete"    # Kontext gesammelt, Bestaetigung anfordern
    ENHANCEMENT_CONFIRMED = "enhancement_confirmed"  # User hat Kontext bestaetigt
    ENHANCEMENT_REJECTED = "enhancement_rejected"    # User hat Kontext abgelehnt
    # Web Fallback Events
    WEB_FALLBACK_REQUIRED = "web_fallback_required"  # Web-Suche braucht Bestaetigung
    WEB_FALLBACK_CONFIRMED = "web_fallback_confirmed"  # User hat Web-Suche bestaetigt
    WEB_FALLBACK_REJECTED = "web_fallback_rejected"    # User hat Web-Suche abgelehnt
    # Workspace Events
    WORKSPACE_CODE_CHANGE = "workspace_code_change"  # Code-Aenderung fuer Workspace Panel
    WORKSPACE_SQL_RESULT = "workspace_sql_result"    # SQL-Abfrage-Ergebnis fuer Workspace Panel
    WORKSPACE_FILE = "workspace_file"                # Gelesene Datei fuer Workspace Panel
    WORKSPACE_RESEARCH = "workspace_research"        # Research-Ergebnis fuer Workspace Panel
    WORKSPACE_PR = "workspace_pr"                    # PR-Daten fuer Workspace Panel
    WORKSPACE_PR_ANALYSIS = "workspace_pr_analysis"  # PR-Analyse-Ergebnisse fuer Badges
    PR_OPENED_HINT = "pr_opened_hint"                # Kurzer Chat-Hinweis: "PR im Workspace"
    # Progress & Stuck Detection Events
    STUCK_DETECTED = "stuck_detected"                # Agent dreht sich im Kreis
    PROGRESS_UPDATE = "progress_update"              # Neues Wissen gewonnen


# MCP Event Type Mapping - Maps string event types to AgentEventType
MCP_EVENT_TYPE_MAPPING: Dict[str, "AgentEventType"] = {
    # Standard MCP events (uppercase)
    "MCP_START": AgentEventType.MCP_START,
    "MCP_STEP": AgentEventType.MCP_STEP,
    "MCP_PROGRESS": AgentEventType.MCP_PROGRESS,
    "MCP_COMPLETE": AgentEventType.MCP_COMPLETE,
    "MCP_ERROR": AgentEventType.MCP_ERROR,
    # Lowercase variants (from SequentialThinking)
    "mcp_start": AgentEventType.MCP_START,
    "mcp_step": AgentEventType.MCP_STEP,
    "mcp_progress": AgentEventType.MCP_PROGRESS,
    "mcp_complete": AgentEventType.MCP_COMPLETE,
    "mcp_error": AgentEventType.MCP_ERROR,
    # v2: Extended events
    "mcp_branch_start": AgentEventType.MCP_BRANCH_START,
    "mcp_branch_end": AgentEventType.MCP_BRANCH_END,
    "mcp_assumption_created": AgentEventType.MCP_ASSUMPTION,
    "mcp_tool_recommendation": AgentEventType.MCP_TOOL_REC,
    # Workspace events
    "workspace_code_change": AgentEventType.WORKSPACE_CODE_CHANGE,
    "workspace_sql_result": AgentEventType.WORKSPACE_SQL_RESULT,
    "workspace_file": AgentEventType.WORKSPACE_FILE,
    "workspace_research": AgentEventType.WORKSPACE_RESEARCH,
    "workspace_pr": AgentEventType.WORKSPACE_PR,
    "workspace_pr_analysis": AgentEventType.WORKSPACE_PR_ANALYSIS,
}


@dataclass
class TokenUsage:
    """Token-Nutzung einer LLM-Anfrage."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""  # stop, length, tool_calls, etc.
    model: str = ""
    truncated: bool = False  # True wenn wegen max_tokens abgebrochen


@dataclass
class AgentEvent:
    """Ein Event das vom Agent emittiert wird."""
    type: AgentEventType
    data: Any = None

    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "data": self.data
        }


@dataclass
class ToolCall:
    """Ein Tool-Call vom LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]
    result: Optional["ToolResult"] = None
    confirmed: Optional[bool] = None


@dataclass
class AgentState:
    """Zustand einer Agent-Session."""
    session_id: str
    project_id: Optional[str] = None  # Projekt-ID fuer Context-System
    project_path: Optional[str] = None  # Projekt-Pfad fuer Context-Dateien
    mode: AgentMode = AgentMode.READ_ONLY
    active_skill_ids: Set[str] = field(default_factory=set)
    pending_confirmation: Optional[ToolCall] = None
    tool_calls_history: List[ToolCall] = field(default_factory=list)
    context_items: List[str] = field(default_factory=list)
    # Konversations-Historie fuer Multi-Turn Chats
    messages_history: List[Dict[str, str]] = field(default_factory=list)
    max_history_messages: int = 50  # Erhoeht - Summarizer kuemmert sich um Kompression
    # Token-Tracking fuer aktuelle Anfrage
    current_usage: Optional[TokenUsage] = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    # Token Budget Management
    token_budget: Optional["TokenBudget"] = None
    # Compaction Stats
    compaction_count: int = 0
    last_compaction_savings: int = 0
    compaction_attempted_while_full: bool = False
    # Loop-Praevention: Zaehlt wie oft eine Datei pro Request bearbeitet wurde
    read_files_this_request: Dict[str, int] = field(default_factory=dict)
    edit_files_this_request: Dict[str, int] = field(default_factory=dict)
    write_files_this_request: Dict[str, int] = field(default_factory=dict)
    # Abbruch-Flag fuer laufende Anfragen
    cancelled: bool = False
    # Entity Tracker: Verfolgt gefundene Entitaeten und ihre Quellen
    entity_tracker: Optional["EntityTracker"] = None
    # Pending Enhancement: Wartet auf User-Bestaetigung
    pending_enhancement: Optional["EnrichedPrompt"] = None
    # Confirmed Enhancement Context: Gesammelter Kontext nach Bestaetigung
    confirmed_enhancement_context: Optional[str] = None
    # Original query fuer Enhancement (fuer Task-Decomposition nach Bestaetigung)
    enhancement_original_query: Optional[str] = None
    # Chat-Titel (wird aus erster User-Nachricht abgeleitet oder manuell gesetzt)
    title: str = ""
    # Planungsphase (PLAN_THEN_EXECUTE-Modus)
    pending_plan: Optional[str] = None    # Erstellter Plan, wartet auf Genehmigung
    plan_approved: bool = False           # True wenn User den Plan genehmigt hat
    # Tool-Budget-Tracking (fuer Effizienz-Optimierung)
    tool_budget: Optional["ToolBudget"] = None
    # Web-Fallback-Bestaetigung (fuer Research mit leerem internen Ergebnis)
    web_fallback_approved: bool = False   # True wenn User Web-Suche genehmigt hat
    # Pending PR-Analyse (laeuft im Hintergrund)
    pending_pr_analysis: Optional[asyncio.Task] = None
    pending_pr_number: Optional[int] = None
    pending_pr_state: Optional[str] = None

    def __post_init__(self):
        """Initialize entity_tracker if not provided."""
        if self.entity_tracker is None:
            from app.agent.entity_tracker import EntityTracker
            self.entity_tracker = EntityTracker()
