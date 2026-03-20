"""
Agent Orchestrator - Koordiniert den Agent-Loop mit Tool-Calls.

Der Orchestrator:
1. Nimmt User-Nachrichten entgegen
2. Baut den Kontext aus aktiven Skills
3. Ruft das LLM mit Tool-Definitionen auf
4. Führt Tool-Calls aus
5. Bei Schreib-Ops: Wartet auf User-Bestätigung
6. Wiederholt bis fertig oder max_iterations erreicht
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.prompt_enhancer import EnrichedPrompt

import httpx

from app.agent.constants import ControlMarkers
from app.agent.entity_tracker import EntityTracker

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Pre-compiled Regex Patterns (Performance: avoid re-compilation on each call)
# ══════════════════════════════════════════════════════════════════════════════
_RE_MISTRAL_COMPACT = re.compile(r'\[TOOL_CALLS\](\w+)(\{.*?\}|\[.*?\])', re.DOTALL)
_RE_MISTRAL_STANDARD = re.compile(r'\[TOOL_CALLS\]\s*(\[.*?\])', re.DOTALL)
_RE_XML_TOOL_CALL = re.compile(r'<tool_call>(.*?)</tool_call>', re.DOTALL)
_RE_XML_FUNCTIONCALL = re.compile(r'<functioncall>(.*?)</functioncall>', re.DOTALL)
_RE_XML_FUNCTION_CALLS = re.compile(r'<function_calls>(.*?)</function_calls>', re.DOTALL)
_RE_XML_INVOKE = re.compile(r'<invoke>(.*?)</invoke>', re.DOTALL)
_RE_JSON_BLOCK = re.compile(r'```(?:json)?\s*\n(.*?)\n```', re.DOTALL)
_RE_INLINE_NAME = re.compile(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*\}')
_RE_MCP_FORCE = re.compile(r'^\[MCP:(\w+)\]\s*(.+)$', re.DOTALL)
_RE_PLAN_BLOCK = re.compile(r'\[PLAN\](.*?)\[/PLAN\]', re.DOTALL)

# ══════════════════════════════════════════════════════════════════════════════
# Parallelisierbare Tools (Read-Only, keine Seiteneffekte)
# ══════════════════════════════════════════════════════════════════════════════
PARALLELIZABLE_TOOL_PREFIXES = (
    "search_",      # search_code, search_confluence, search_jira, etc.
    "read_",        # read_file, read_confluence_page, etc.
    "get_",         # get_active_repositories, etc.
    "list_",        # list_files, list_database_tables, etc.
    "glob_",        # glob_files
    "github_",      # github_search_code, github_get_file, github_pr_diff, etc.
    "describe_",    # describe_database_table, etc.
    "grep_",        # grep_content
)

# Tools die NICHT parallelisiert werden dürfen (Schreibend, Confirmations, MCP)
SEQUENTIAL_ONLY_TOOLS = {
    "write_file", "edit_file", "create_file", "batch_write_files",
    "execute_command", "run_sql_query",
    "suggest_answers",  # Benötigt User-Interaktion
    "sequential_thinking", "seq_think", "brainstorm", "design", "implement", "analyze",
}

def _is_parallelizable_tool(tool_name: str) -> bool:
    """Prüft ob ein Tool parallel ausgeführt werden kann."""
    if tool_name in SEQUENTIAL_ONLY_TOOLS:
        return False
    if tool_name.startswith("mcp_"):
        return False  # MCP-Tools immer sequentiell
    return tool_name.startswith(PARALLELIZABLE_TOOL_PREFIXES)

# Debug hint patterns
_RE_HINT_TOOL = re.compile(r'\[TOOL', re.IGNORECASE)
_RE_HINT_XML_TOOL = re.compile(r'<tool', re.IGNORECASE)
_RE_HINT_XML_FUNC = re.compile(r'<function', re.IGNORECASE)
_RE_HINT_NAME = re.compile(r'"name"\s*:')

# ══════════════════════════════════════════════════════════════════════════════
# PR-Context Detection - Filtert Tools bei GitHub PR-Analysen
# ══════════════════════════════════════════════════════════════════════════════
# Generisches Pattern für PR-URLs (funktioniert mit jedem Git-Server):
# - github.com/owner/repo/pull/123
# - github.intern/owner/repo/pull/456
# - git.example.com/owner/repo/pull/789
# - 192.168.1.100/owner/repo/pull/123
_RE_PR_URL = re.compile(
    r'(https?://[^\s/]+/[^\s/]+/[^\s/]+/pull/\d+)',
    re.IGNORECASE
)

# Tools die bei PR-Analysen erlaubt sind (GitHub-Tools + Basis-Infos)
PR_CONTEXT_ALLOWED_TOOLS = {
    # GitHub-Tools für PR-Analyse
    "github_pr_details",
    "github_pr_diff",
    "github_get_file",
    "github_search_code",
    "github_commit_diff",
    "github_recent_commits",
    "github_list_branches",
    # Allgemeine Hilfstools
    "sequential_thinking",
    "seq_think",
}

# Tools die bei PR-Context explizit VERBOTEN sind (lokale Dateien)
PR_CONTEXT_FORBIDDEN_TOOLS = {
    "search_code",          # Durchsucht lokales Dateisystem
    "read_file",            # Liest lokale Dateien
    "batch_read_files",     # Liest mehrere lokale Dateien
    "grep_content",         # Grep in lokalen Dateien
    "glob_files",           # Glob in lokalem Dateisystem
    "find_files",           # Findet lokale Dateien
    "search_java_class",    # Java-Suche lokal
    "trace_java_references",# Java-Referenzen lokal
    "search_python_class",  # Python-Suche lokal
}


def _detect_pr_context(user_message: str) -> Optional[str]:
    """
    Erkennt ob die User-Message eine PR-URL enthält.

    Unterstützt:
    - github.com/owner/repo/pull/123
    - github.intern/owner/repo/pull/456
    - IP-basierte URLs: 192.168.1.100/owner/repo/pull/789
    - Jede URL mit /pull/N im Pfad

    Args:
        user_message: Die Nachricht des Users

    Returns:
        Die PR-URL wenn gefunden, sonst None
    """
    match = _RE_PR_URL.search(user_message)
    if match:
        return match.group(1)
    return None


def _filter_tools_for_pr_context(
    tool_schemas: List[Dict[str, Any]],
    pr_url: str
) -> List[Dict[str, Any]]:
    """
    Filtert Tool-Schemas für PR-Context.

    Entfernt lokale Datei-Tools und behält nur GitHub-Tools.

    Args:
        tool_schemas: Alle verfügbaren Tool-Schemas
        pr_url: Die erkannte PR-URL

    Returns:
        Gefilterte Tool-Schemas (nur PR-relevante Tools)
    """
    filtered = []
    removed = []

    for schema in tool_schemas:
        tool_name = schema.get("function", {}).get("name", "")

        # Explizit verbotene Tools entfernen
        if tool_name in PR_CONTEXT_FORBIDDEN_TOOLS:
            removed.append(tool_name)
            continue

        # GitHub-Tools und erlaubte Tools behalten
        if (tool_name.startswith("github_") or
            tool_name in PR_CONTEXT_ALLOWED_TOOLS or
            tool_name.startswith("mcp_")):  # MCP-Tools für Reasoning behalten
            filtered.append(schema)
        else:
            # Andere Tools entfernen (z.B. search_confluence, search_jira)
            # Diese sind für PR-Analyse nicht relevant
            removed.append(tool_name)

    if removed:
        logger.info(
            f"[PR-Context] Erkannt: {pr_url[:60]}... | "
            f"Entfernte {len(removed)} lokale Tools, {len(filtered)} Tools verfügbar"
        )
        logger.debug(f"[PR-Context] Entfernte Tools: {removed[:10]}...")

    return filtered
_RE_HINT_TOOL_KEY = re.compile(r'"tool"\s*:')

from app.agent.tools import ToolRegistry, ToolResult, get_tool_registry
from app.agent.tool_cache import ToolResultCache, get_tool_cache
from app.agent.tool_budget import ToolBudget, BudgetLevel, create_budget
from app.agent.tool_progress import (
    ToolProgressTracker,
    get_progress_tracker,
    reset_progress_tracker,
    StuckDetectionResult,
)
from app.agent.result_validator import (
    ResultValidator,
    get_result_validator,
    ValidationResult,
)
from app.agent.sub_agent_coordinator import (
    SubAgentCoordinator,
    CoordinatedResult,
)
from app.core.config import settings
from app.mcp.tool_bridge import get_tool_bridge, MCPToolBridge
from app.mcp.capabilities.research import get_research_capability, ResearchCapability
from app.mcp.event_bridge import MCPEventBridge, get_event_bridge, create_event_callback
from app.core.token_budget import TokenBudget, create_budget_from_config
from app.core.conversation_summarizer import get_summarizer
from app.services.llm_client import (
    SYSTEM_PROMPT,
    _get_http_client,
    _RETRY_DELAYS,
    _is_retryable,
    llm_client as central_llm_client,
    LLMResponse,
    TIMEOUT_TOOL,
    TIMEOUT_ANALYSIS,
)
from app.services.token_tracker import get_token_tracker
from app.services.memory_store import get_memory_store
from app.services.task_tracker import get_task_tracker, TaskTracker, TaskArtifact
from app.services.context_manager import get_context_manager, ContextManager
from app.services.transcript_logger import get_transcript_logger, TranscriptLogger, TranscriptEntry
from app.services.auto_learner import get_auto_learner, AutoLearner
from app.services.analytics_logger import get_analytics_logger, AnalyticsLogger
from app.utils.token_counter import estimate_tokens, estimate_messages_tokens, truncate_text_to_tokens


def _get_model_context_limit(model_id: str) -> int:
    """
    Ermittelt das Kontext-Limit für ein Model.

    Unterstützt:
    - Exakte Matches: "mistral-678b" -> llm_context_limits["mistral-678b"]
    - Pfad-basierte IDs: "mistral/mistral_large" -> sucht nach "mistral" in keys
    - Fallback auf default_context_limit
    """
    limits = settings.llm.llm_context_limits or {}
    default = settings.llm.default_context_limit or 32000

    if not model_id:
        return default

    # 1. Exakter Match
    if model_id in limits:
        return limits[model_id]

    # 2. Normalisierter Match (lowercase)
    model_lower = model_id.lower()
    for key, value in limits.items():
        if key.lower() == model_lower:
            return value

    # 3. Partial Match - Model-ID enthält einen bekannten Key oder umgekehrt
    # z.B. "mistral/mistral_large" enthält "mistral"
    for key, value in limits.items():
        key_lower = key.lower()
        # Extrahiere Basis-Namen (ohne Pfad und Größenangaben)
        model_base = model_lower.replace("/", "-").replace("_", "-")
        key_base = key_lower.replace("/", "-").replace("_", "-")

        # Prüfe ob key im model vorkommt oder umgekehrt
        if key_base in model_base or model_base in key_base:
            logger.debug(f"[context] Partial match: {model_id} -> {key} ({value} tokens)")
            return value

        # Prüfe einzelne Teile (z.B. "mistral" in "mistral/mistral_large")
        model_parts = set(model_base.split("-"))
        key_parts = set(key_base.split("-"))
        if model_parts & key_parts:  # Intersection
            logger.debug(f"[context] Partial match via parts: {model_id} -> {key} ({value} tokens)")
            return value

    logger.debug(f"[context] No match for {model_id}, using default: {default}")
    return default


def _trim_messages_to_limit(messages: List[Dict], max_tokens: int) -> List[Dict]:
    """
    Trimmt Message-Inhalte um Kontext-Limit einzuhalten.

    Priorität:
    1. System-Prompt bleibt unverändert
    2. Letzte User-Nachricht bleibt unverändert
    3. Tool-Ergebnisse werden gekürzt (älteste zuerst, größte zuerst)
    4. Ältere Nachrichten werden gekürzt
    """
    # Sicherheitsprüfung
    if not messages:
        return messages or []

    current_tokens = estimate_messages_tokens(messages)
    if current_tokens <= max_tokens:
        return messages

    logger.warning(f"[agent] Kontext zu groß ({current_tokens} > {max_tokens} tokens), trimme...")

    # Kopie erstellen
    trimmed = [dict(m) for m in messages]
    tokens_to_remove = current_tokens - max_tokens + 1000  # Puffer

    # Finde große Tool-Ergebnisse und kürze sie
    tool_results = []
    for i, msg in enumerate(trimmed):
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            content_tokens = estimate_tokens(content)
            if content_tokens > 500:  # Nur große Ergebnisse betrachten
                tool_results.append((i, content_tokens, content))

    # Sortiere nach Größe (größte zuerst) und trimme
    tool_results.sort(key=lambda x: x[1], reverse=True)

    for idx, orig_tokens, content in tool_results:
        if tokens_to_remove <= 0:
            break

        # Berechne wie viel vom Inhalt übrig bleiben soll
        target_tokens = max(200, orig_tokens - tokens_to_remove)
        truncated = truncate_text_to_tokens(content, target_tokens)
        trimmed[idx]["content"] = truncated

        removed = orig_tokens - estimate_tokens(truncated)
        tokens_to_remove -= removed
        logger.debug(f"[agent] Tool-Ergebnis {idx} gekürzt: -{removed} tokens")

    # Wenn immer noch zu groß: Ältere Assistant-Nachrichten kürzen
    if tokens_to_remove > 0:
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "assistant" and i < len(trimmed) - 2:
                content = msg.get("content", "")
                if content and len(content) > 500:
                    truncated = content[:300] + "\n[... gekürzt ...]"
                    removed = estimate_tokens(content) - estimate_tokens(truncated)
                    trimmed[i]["content"] = truncated
                    tokens_to_remove -= removed
                    if tokens_to_remove <= 0:
                        break

    final_tokens = estimate_messages_tokens(trimmed)
    logger.info(f"[agent] Kontext getrimmt: {current_tokens} -> {final_tokens} tokens")
    return trimmed


class AgentMode(str, Enum):
    """Betriebsmodus des Agents."""
    READ_ONLY = "read_only"           # Nur Lese-Operationen
    WRITE_WITH_CONFIRM = "write_with_confirm"  # Schreiben mit Bestätigung
    AUTONOMOUS = "autonomous"          # Schreiben ohne Bestätigung (gefährlich)
    PLAN_THEN_EXECUTE = "plan_then_execute"    # Erst planen, dann mit Bestätigung ausführen
    DEBUG = "debug"                    # Fehler-Analyse: Rückfragen + Tools zum Nachstellen


class AgentEventType(str, Enum):
    """Typen von Agent-Events."""
    TOKEN = "token"                    # Streaming-Token
    TOOL_START = "tool_start"          # Tool wird ausgeführt
    TOOL_RESULT = "tool_result"        # Tool-Ergebnis
    CONFIRM_REQUIRED = "confirm_required"  # User-Bestätigung benötigt
    CONFIRMED = "confirmed"            # User hat bestätigt
    CANCELLED = "cancelled"            # User hat abgelehnt
    ERROR = "error"                    # Fehler
    USAGE = "usage"                    # Token-Nutzung
    COMPACTION = "compaction"          # Context wurde komprimiert
    CONTEXT_STATUS = "context_status"  # Kontext-Auslastung für UI
    DONE = "done"                      # Fertig
    # Sub-Agent Events
    SUBAGENT_START = "subagent_start"      # Sub-Agent-Phase beginnt (Routing läuft)
    SUBAGENT_ROUTING = "subagent_routing"  # Routing fertig – ausgewählte Agenten bekannt
    SUBAGENT_DONE = "subagent_done"        # Ein Sub-Agent hat Ergebnis geliefert
    SUBAGENT_ERROR = "subagent_error"      # Sub-Agent fehlgeschlagen
    # Planning Events
    PLAN_READY = "plan_ready"              # Plan erstellt – wartet auf User-Genehmigung
    PLAN_APPROVED = "plan_approved"        # Plan genehmigt, Ausführung startet
    PLAN_REJECTED = "plan_rejected"        # Plan abgelehnt
    # Debug-Modus Events
    QUESTION = "question"                  # Agent stellt Rückfrage mit Vorschlägen
    # MCP Progress Events
    MCP_START = "mcp_start"                # MCP-Tool startet (z.B. Sequential Thinking)
    MCP_STEP = "mcp_step"                  # Einzelner Denkschritt mit Details
    MCP_PROGRESS = "mcp_progress"          # Fortschritts-Update (Prozent)
    MCP_COMPLETE = "mcp_complete"          # MCP-Tool fertig mit Zusammenfassung
    MCP_ERROR = "mcp_error"                # Fehler während MCP-Verarbeitung
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
    ENHANCEMENT_COMPLETE = "enhancement_complete"    # Kontext gesammelt, Bestätigung anfordern
    ENHANCEMENT_CONFIRMED = "enhancement_confirmed"  # User hat Kontext bestätigt
    ENHANCEMENT_REJECTED = "enhancement_rejected"    # User hat Kontext abgelehnt
    # Web Fallback Events
    WEB_FALLBACK_REQUIRED = "web_fallback_required"  # Web-Suche braucht Bestätigung (interne Quellen leer)
    WEB_FALLBACK_CONFIRMED = "web_fallback_confirmed"  # User hat Web-Suche bestätigt
    WEB_FALLBACK_REJECTED = "web_fallback_rejected"    # User hat Web-Suche abgelehnt
    # Workspace Events
    WORKSPACE_CODE_CHANGE = "workspace_code_change"  # Code-Änderung für Workspace Panel
    WORKSPACE_SQL_RESULT = "workspace_sql_result"    # SQL-Abfrage-Ergebnis für Workspace Panel
    WORKSPACE_FILE = "workspace_file"                # Gelesene Datei für Workspace Panel
    WORKSPACE_RESEARCH = "workspace_research"        # Research-Ergebnis für Workspace Panel
    WORKSPACE_PR = "workspace_pr"                    # PR-Daten für Workspace Panel
    WORKSPACE_PR_ANALYSIS = "workspace_pr_analysis"  # PR-Analyse-Ergebnisse für Badges
    PR_OPENED_HINT = "pr_opened_hint"                # Kurzer Chat-Hinweis: "PR im Workspace"
    # Progress & Stuck Detection Events
    STUCK_DETECTED = "stuck_detected"                # Agent dreht sich im Kreis
    PROGRESS_UPDATE = "progress_update"              # Neues Wissen gewonnen


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
    result: Optional[ToolResult] = None
    confirmed: Optional[bool] = None


@dataclass
class AgentState:
    """Zustand einer Agent-Session."""
    session_id: str
    project_id: Optional[str] = None  # Projekt-ID für Context-System
    project_path: Optional[str] = None  # Projekt-Pfad für Context-Dateien
    mode: AgentMode = AgentMode.READ_ONLY
    active_skill_ids: Set[str] = field(default_factory=set)
    pending_confirmation: Optional[ToolCall] = None
    tool_calls_history: List[ToolCall] = field(default_factory=list)
    context_items: List[str] = field(default_factory=list)
    # Konversations-Historie für Multi-Turn Chats
    messages_history: List[Dict[str, str]] = field(default_factory=list)
    max_history_messages: int = 50  # Erhöht - Summarizer kümmert sich um Kompression
    # Token-Tracking für aktuelle Anfrage
    current_usage: Optional[TokenUsage] = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    # Token Budget Management
    token_budget: Optional[TokenBudget] = None
    # Compaction Stats
    compaction_count: int = 0
    last_compaction_savings: int = 0
    compaction_attempted_while_full: bool = False  # Verhindert Spam wenn Komprimierung nicht hilft
    # Loop-Prävention: Zählt wie oft eine Datei pro Request bearbeitet wurde
    read_files_this_request: Dict[str, int] = field(default_factory=dict)
    edit_files_this_request: Dict[str, int] = field(default_factory=dict)
    write_files_this_request: Dict[str, int] = field(default_factory=dict)
    # Abbruch-Flag für laufende Anfragen
    cancelled: bool = False
    # Entity Tracker: Verfolgt gefundene Entitäten und ihre Quellen (Java ↔ Handbuch ↔ PDF)
    entity_tracker: EntityTracker = field(default_factory=EntityTracker)
    # Pending Enhancement: Wartet auf User-Bestätigung
    pending_enhancement: Optional["EnrichedPrompt"] = None
    # Confirmed Enhancement Context: Gesammelter Kontext nach Bestätigung
    confirmed_enhancement_context: Optional[str] = None
    # Original query für Enhancement (für Task-Decomposition nach Bestätigung)
    enhancement_original_query: Optional[str] = None
    # Chat-Titel (wird aus erster User-Nachricht abgeleitet oder manuell gesetzt)
    title: str = ""
    # Planungsphase (PLAN_THEN_EXECUTE-Modus)
    pending_plan: Optional[str] = None    # Erstellter Plan, wartet auf Genehmigung
    plan_approved: bool = False           # True wenn User den Plan genehmigt hat
    # Tool-Budget-Tracking (fuer Effizienz-Optimierung)
    tool_budget: Optional["ToolBudget"] = None
    # Web-Fallback-Bestätigung (für Research mit leerem internen Ergebnis)
    web_fallback_approved: bool = False   # True wenn User Web-Suche genehmigt hat
    # Pending PR-Analyse (läuft im Hintergrund)
    pending_pr_analysis: Optional[asyncio.Task] = None
    pending_pr_number: Optional[int] = None
    pending_pr_state: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Tool-Call Content-Bereinigung
# ══════════════════════════════════════════════════════════════════════════════

def _strip_tool_markers(content: str) -> str:
    """
    Entfernt Tool-Call-Marker aus dem Content nach dem Parsing.

    Verhindert dass [TOOL_CALLS]..., <tool_call>...</tool_call> etc.
    im finalen Output an den User erscheinen.

    Args:
        content: Roher Content mit möglichen Tool-Markern

    Returns:
        Bereinigter Content ohne Tool-Marker
    """
    if not content:
        return content

    clean = content

    # [TOOL_CALLS] mit JSON-Array: [TOOL_CALLS] [{"name": ...}]
    clean = re.sub(r'\[TOOL_CALLS\]\s*\[.*?\]', '', clean, flags=re.DOTALL)

    # [TOOL_CALLS] mit direktem JSON: [TOOL_CALLS]funcname{...}
    clean = re.sub(r'\[TOOL_CALLS\]\w*\{[^}]*\}', '', clean, flags=re.DOTALL)

    # Generisches [TOOL_CALLS] Cleanup (falls Reste)
    clean = re.sub(r'\[TOOL_CALLS\][^\[]*', '', clean, flags=re.DOTALL)

    # XML-Style Tool-Calls
    clean = re.sub(r'<tool_call>.*?</tool_call>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'<functioncall>.*?</functioncall>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'<function_calls>.*?</function_calls>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'<invoke>.*?</invoke>', '', clean, flags=re.DOTALL)

    # JSON-Blöcke mit Tool-Struktur (nur wenn sie wie Tool-Calls aussehen)
    # Vorsichtig: Nicht alle JSON-Blöcke entfernen!
    clean = re.sub(
        r'```(?:json)?\s*\n\s*\{\s*"(?:name|tool|function)"\s*:.*?\}\s*\n```',
        '',
        clean,
        flags=re.DOTALL
    )

    # Mehrfache Leerzeilen reduzieren
    clean = re.sub(r'\n{3,}', '\n\n', clean)

    return clean.strip()


# ══════════════════════════════════════════════════════════════════════════════
# Text-basierter Tool-Call-Parser (Fallback für Modelle ohne natives Tool-Calling)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_text_tool_calls(content: str, available_tools: List[Dict]) -> List[Dict]:
    """
    Parst Tool-Calls aus dem Text-Content von Modellen, die kein natives
    Tool-Calling unterstützen (z.B. Mistral, Qwen, OpenHermes).

    Unterstützte Formate:
    1. Mistral: [TOOL_CALLS] [{"name": "func", "arguments": {...}}]
    2. XML:     <tool_call>{"name": "func", "arguments": {...}}</tool_call>
    3. OpenHermes: <functioncall>{"name": "func", "arguments": {...}}</functioncall>
    4. JSON-Block: ```json\n{"tool": "func", ...}\n```
    """
    if not content:
        return []

    # PERFORMANCE: Schneller Check ob überhaupt Tool-Call-Marker vorhanden sind
    # Vermeidet teure Regex-Operationen wenn Content keine Tools enthält
    _HAS_TOOL_MARKERS = (
        "[TOOL_CALLS]" in content or
        "<tool_call>" in content or
        "<functioncall>" in content or
        "<function_calls>" in content or
        "<invoke>" in content or
        ('"name"' in content and ("arguments" in content or "parameters" in content)) or
        ("```" in content and '"tool"' in content)
    )
    if not _HAS_TOOL_MARKERS:
        return []

    tool_names = {t["function"]["name"] for t in available_tools} if available_tools else set()
    parsed_calls = []

    # Format 1a: Mistral 678B Compact Format
    # [TOOL_CALLS]funcname{"arg": "val"}  (kein Leerzeichen, kein JSON-Array)
    mistral_compact_matches = _RE_MISTRAL_COMPACT.findall(content)
    if mistral_compact_matches:
        for name, args_str in mistral_compact_matches:
            if not tool_names or name in tool_names:
                try:
                    args = json.loads(args_str)
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args_str
                        }
                    })
                except json.JSONDecodeError:
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": name, "arguments": args_str}
                    })
        if parsed_calls:
            logger.debug("Mistral 678B Compact Format erkannt: %d calls", len(parsed_calls))
            return parsed_calls

    # Format 1b: Mistral Standard Format
    # [TOOL_CALLS] [{"name": "...", "arguments": {...}}]
    mistral_match = _RE_MISTRAL_STANDARD.search(content)
    if mistral_match:
        try:
            calls = json.loads(mistral_match.group(1))
            if isinstance(calls, list):
                for call in calls:
                    name = call.get("name") or call.get("function")
                    args = call.get("arguments") or call.get("parameters") or {}
                    if name and (not tool_names or name in tool_names):
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
            if parsed_calls:
                logger.debug("[agent] Mistral Standard Format erkannt: {len(parsed_calls)} calls")
                return parsed_calls
        except (json.JSONDecodeError, KeyError):
            pass

    # Format 2: XML <tool_call> oder <functioncall>
    xml_patterns = [
        _RE_XML_TOOL_CALL,
        _RE_XML_FUNCTIONCALL,
        _RE_XML_FUNCTION_CALLS,
        _RE_XML_INVOKE,
    ]
    for pattern in xml_patterns:
        matches = pattern.findall(content)
        for match in matches:
            try:
                call = json.loads(match.strip())
                name = call.get("name") or call.get("function") or call.get("tool_name")
                args = call.get("arguments") or call.get("parameters") or call.get("kwargs") or {}
                if name and (not tool_names or name in tool_names):
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args
                        }
                    })
            except (json.JSONDecodeError, KeyError):
                continue
    if parsed_calls:
        logger.debug("[agent] XML Tool-Call Format erkannt: {len(parsed_calls)} calls")
        return parsed_calls

    # Format 3: JSON-Codeblock mit Tool-Call Struktur
    json_blocks = _RE_JSON_BLOCK.findall(content)
    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            # Prüfe ob es ein Tool-Call ist
            name = None
            args = {}
            if isinstance(data, dict):
                if "name" in data and ("arguments" in data or "parameters" in data):
                    name = data["name"]
                    args = data.get("arguments") or data.get("parameters") or {}
                elif "tool" in data:
                    name = data["tool"]
                    args = data.get("input") or data.get("arguments") or {}
            if name and (not tool_names or name in tool_names):
                parsed_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else args
                    }
                })
        except (json.JSONDecodeError, KeyError):
            continue
    if parsed_calls:
        logger.debug("[agent] JSON-Block Tool-Call Format erkannt: {len(parsed_calls)} calls")
        return parsed_calls

    # Format 4: Inline JSON mit bekanntem Tool-Namen
    # Suche nach {"name": "known_tool", ...} direkt im Text
    if tool_names:
        inline_matches = _RE_INLINE_NAME.findall(content)
        for match_name in inline_matches:
            if match_name in tool_names:
                # Versuche den vollständigen JSON-Block zu extrahieren
                pattern = r'\{[^{}]*"name"\s*:\s*"' + re.escape(match_name) + r'"[^{}]*\}'
                full_matches = re.findall(pattern, content, re.DOTALL)
                for fm in full_matches:
                    try:
                        call = json.loads(fm)
                        args = call.get("arguments") or call.get("parameters") or {}
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": match_name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
                    except (json.JSONDecodeError, KeyError):
                        continue
        if parsed_calls:
            logger.debug("[agent] Inline JSON Tool-Call Format erkannt: {len(parsed_calls)} calls")
            return parsed_calls

    # Debug: Wenn kein Tool-Call erkannt wurde, hilfreiche Info loggen
    if content and len(content) > 20:
        # Prüfe auf mögliche Tool-Call-Patterns die nicht gematcht wurden
        potential_patterns = [
            (_RE_HINT_TOOL, '[TOOL...'),
            (_RE_HINT_XML_TOOL, '<tool...'),
            (_RE_HINT_XML_FUNC, '<function...'),
            (_RE_HINT_NAME, '"name":'),
            (_RE_HINT_TOOL_KEY, '"tool":'),
        ]
        found_hints = []
        for pattern, hint in potential_patterns:
            if pattern.search(content):
                found_hints.append(hint)
        if found_hints:
            logger.debug("[agent] Text-Parser: Keine Tool-Calls erkannt, aber Hinweise gefunden: {found_hints}")
            logger.debug("[agent] Content-Anfang (100 chars): {content[:100]!r}")

    return []


class AgentOrchestrator:
    """
    Koordiniert den Agent-Loop ähnlich wie Claude Code.
    """

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        max_iterations: int = 30,  # Erhöht von 10 auf 30 für komplexe Aufgaben
        max_tool_calls_per_iteration: int = 10  # Erhöht von 5 auf 10
    ):
        self.tools = tool_registry or get_tool_registry()
        self.max_iterations = max_iterations
        self.max_tool_calls_per_iter = max_tool_calls_per_iteration
        self._states: Dict[str, AgentState] = {}
        # Token Management Components
        self.memory_store = get_memory_store()
        self.summarizer = get_summarizer()
        # Context System (3-Schichten: Global → Project → Session)
        self.context_manager = get_context_manager()
        self.transcript_logger = get_transcript_logger()
        # Auto-Learning (erkennt "Merke dir...", Problemlösungen, etc.)
        self.auto_learner = get_auto_learner()
        # MCP Tool Bridge (für Sequential Thinking und externe MCP-Server)
        self._mcp_bridge: Optional[MCPToolBridge] = None
        # Research Capability für parallele Quellensuche
        self._research_capability: Optional[ResearchCapability] = None
        # Event Bridge für Live-Streaming von MCP-Events
        self._event_bridge: MCPEventBridge = get_event_bridge()
        # Tool Result Cache (reduziert redundante Tool-Aufrufe)
        self._tool_cache: ToolResultCache = get_tool_cache(
            ttl_seconds=120,  # 2 Minuten TTL
            max_entries=100
        )
        # Analytics Logger (anonymisiertes Tool-Tracking)
        self._analytics: AnalyticsLogger = get_analytics_logger()

    async def _llm_callback_for_mcp(self, prompt: str, context: Optional[str] = None) -> str:
        """
        LLM-Callback für MCP Sequential Thinking.

        Ermöglicht echtes LLM-Denken statt Template-Fallback.
        WICHTIG: Verwendet längeren Timeout (60s) da Analyse-Schritte
        mehr Zeit benötigen als einfache Klassifikation.
        """
        system_prompt = """Du bist ein strukturierter analytischer Denker.
Antworte IMMER im exakten Format das im Prompt angegeben ist.
Sei präzise und gib detaillierte Analyse-Schritte."""

        messages = [
            {"role": "system", "content": system_prompt}
        ]
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        try:
            # WICHTIG: Nicht chat_quick() verwenden (15s Timeout, 256 Tokens)!
            # Sequential Thinking braucht mehr Zeit und Tokens.
            response = await central_llm_client.chat_with_tools(
                messages=messages,
                temperature=0.2,  # Niedrig für konsistentes Format
                max_tokens=2048,  # Genug Tokens für detaillierte Schritte
                timeout=TIMEOUT_TOOL,  # 60s statt 15s
            )
            result = response.content or ""
            logger.debug(f"[MCP] LLM callback response length: {len(result)}")

            # Token-Tracking für MCP-Calls
            if hasattr(response, 'usage') and response.usage:
                try:
                    tracker = get_token_tracker()
                    # Session-ID aus _current_mcp_session oder Fallback
                    session_id = getattr(self, '_current_mcp_session', 'mcp-default')
                    model = getattr(response, 'model', None) or settings.llm.default_model
                    tracker.log_usage(
                        session_id=session_id,
                        model=model,
                        input_tokens=response.usage.prompt_tokens or 0,
                        output_tokens=response.usage.completion_tokens or 0,
                        request_type="mcp",
                    )
                except Exception as track_err:
                    logger.debug(f"[MCP] Token tracking failed: {track_err}")

            return result
        except Exception as e:
            logger.warning(f"[MCP] LLM callback failed: {e}")
            return ""

    def _get_state(self, session_id: str) -> AgentState:
        """Holt oder erstellt den State für eine Session. Stellt bei Bedarf vom Disk wieder her."""
        if session_id not in self._states:
            state = AgentState(session_id=session_id)
            # Tool-Budget initialisieren
            state.tool_budget = create_budget(
                max_iterations=self.max_iterations,
                max_tools_per_iteration=self.max_tool_calls_per_iter
            )
            # Gespeicherten Chat vom Disk laden (Server-Neustart)
            try:
                from app.services.chat_store import load_chat
                saved = load_chat(session_id)
                if saved:
                    state.messages_history = saved.get("messages_history", [])
                    state.title = saved.get("title", "")
                    try:
                        state.mode = AgentMode(saved.get("mode", "read_only"))
                    except ValueError:
                        pass
            except Exception:
                pass
            self._states[session_id] = state
        return self._states[session_id]

    def set_mode(self, session_id: str, mode: AgentMode) -> None:
        """Setzt den Modus für eine Session."""
        state = self._get_state(session_id)
        state.mode = mode

    def set_active_skills(self, session_id: str, skill_ids: List[str]) -> None:
        """Setzt die aktiven Skills für eine Session."""
        state = self._get_state(session_id)
        state.active_skill_ids = set(skill_ids)

    def set_project(
        self,
        session_id: str,
        project_id: Optional[str] = None,
        project_path: Optional[str] = None
    ) -> None:
        """
        Setzt Projekt-Informationen für eine Session.

        Args:
            session_id: Session-ID
            project_id: Projekt-Identifier für Memory-Scoping
            project_path: Dateisystem-Pfad für PROJECT_CONTEXT.md
        """
        state = self._get_state(session_id)
        state.project_id = project_id
        state.project_path = project_path

        # Transcript-Logger mit Projekt konfigurieren
        if project_id:
            self.transcript_logger.project_id = project_id

    async def _execute_tools_parallel_batch(
        self,
        tool_calls: List[ToolCall],
        state: "AgentState"
    ) -> List[ToolResult]:
        """
        Führt mehrere Read-Only-Tools parallel aus.

        Args:
            tool_calls: Liste von ToolCall-Objekten (alle müssen parallelisierbar sein)
            state: Aktueller Agent-State für Caching/Budget

        Returns:
            Liste von ToolResult in gleicher Reihenfolge wie tool_calls
        """
        import time as _time

        async def execute_single(tc: ToolCall) -> ToolResult:
            """Einzelnes Tool mit Caching ausführen."""
            # Cache prüfen
            cached = self._tool_cache.get(tc.name, tc.arguments)
            if cached is not None:
                logger.debug(f"[parallel] Cache HIT: {tc.name}")
                if state.tool_budget:
                    state.tool_budget.record_tool_call(tc.name, duration_ms=0, cached=True)
                return cached

            # Tool ausführen
            _start = _time.time()
            try:
                result = await self.tools.execute(tc.name, **tc.arguments)
            except Exception as e:
                logger.warning(f"[parallel] Tool {tc.name} failed: {e}")
                result = ToolResult(success=False, error=str(e))

            _duration_ms = int((_time.time() - _start) * 1000)

            # Cachen und Budget tracken
            if result.success:
                self._tool_cache.set(tc.name, tc.arguments, result)
            if state.tool_budget:
                state.tool_budget.record_tool_call(tc.name, duration_ms=_duration_ms, cached=False)

            logger.debug(f"[parallel] {tc.name} completed in {_duration_ms}ms")
            return result

        # Alle Tools parallel ausführen
        logger.info(f"[parallel] Executing {len(tool_calls)} tools in parallel: {[tc.name for tc in tool_calls]}")
        _batch_start = _time.time()

        results = await asyncio.gather(*[execute_single(tc) for tc in tool_calls])

        _batch_duration = int((_time.time() - _batch_start) * 1000)
        logger.info(f"[parallel] Batch completed in {_batch_duration}ms (vs sequential estimate: {sum(r.data.get('duration_ms', 500) if isinstance(r.data, dict) else 500 for r in results)}ms)")

        return list(results)

    async def _emit_mcp_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Callback für MCP/Thinking Events.
        Emittiert Events über die Event Bridge für Live-Streaming.
        """
        await self._event_bridge.emit(event_type, data)

    async def _emit_workspace_code_change(
        self,
        file_path: str,
        original_content: str,
        modified_content: str,
        tool_call: str,
        description: str = "",
        is_new: bool = False
    ) -> None:
        """
        Emittiert ein Workspace Code Change Event für das UI.

        Args:
            file_path: Pfad zur Datei
            original_content: Ursprünglicher Dateiinhalt
            modified_content: Neuer Dateiinhalt
            tool_call: Name des Tools (write_file, edit_file, batch_write_files)
            description: Beschreibung der Änderung
            is_new: True wenn neue Datei erstellt wurde
        """
        import difflib
        import uuid
        from pathlib import Path
        import time

        # Generate unified diff
        if is_new:
            diff = f"--- /dev/null\n+++ b/{file_path}\n@@ -0,0 +1,{len(modified_content.splitlines())} @@\n"
            for line in modified_content.splitlines():
                diff += f"+{line}\n"
        else:
            diff_lines = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                modified_content.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                lineterm=""
            )
            diff = "".join(diff_lines)

        # Detect language from file extension
        path_obj = Path(file_path)
        ext_to_lang = {
            ".py": "python", ".java": "java", ".js": "javascript",
            ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
            ".sql": "sql", ".html": "html", ".css": "css",
            ".json": "json", ".xml": "xml", ".yaml": "yaml",
            ".yml": "yaml", ".md": "markdown", ".sh": "bash",
            ".go": "go", ".rs": "rust", ".cpp": "cpp", ".c": "c",
            ".h": "c", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby",
            ".php": "php", ".swift": "swift", ".kt": "kotlin"
        }
        language = ext_to_lang.get(path_obj.suffix.lower(), "text")

        # Build event payload matching the CodeChange interface from design doc
        event_data = {
            "id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "filePath": str(file_path),
            "fileName": path_obj.name,
            "language": language,
            "originalContent": original_content,
            "modifiedContent": modified_content,
            "diff": diff,
            "toolCall": tool_call,
            "description": description,
            "status": "applied",  # Already applied when user confirmed
            "appliedAt": int(time.time() * 1000),
            "isNew": is_new
        }

        await self._event_bridge.emit(
            AgentEventType.WORKSPACE_CODE_CHANGE.value,
            event_data
        )

    async def _execute_and_emit_sql_result(self, query: str, max_rows: int = 100) -> ToolResult:
        """
        Führt eine SQL-Abfrage aus und emittiert ein Workspace SQL Result Event.

        Args:
            query: SQL-Abfrage
            max_rows: Maximale Anzahl Zeilen

        Returns:
            ToolResult mit formatierter Ausgabe
        """
        from app.core.config import settings
        import uuid
        import time

        start_time = time.time()

        try:
            from app.services.db_client import get_db_client
            client = get_db_client()

            if not client:
                return ToolResult(success=False, error="DB-Client nicht verfügbar")

            # Temporär max_rows überschreiben
            original_max = client.max_rows
            client.max_rows = min(max_rows, settings.database.max_rows)

            result = await client.execute(query)

            client.max_rows = original_max

            execution_time_ms = int((time.time() - start_time) * 1000)

            if not result.success:
                # Emit error event
                await self._event_bridge.emit(
                    AgentEventType.WORKSPACE_SQL_RESULT.value,
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": int(time.time() * 1000),
                        "query": query,
                        "database": settings.database.database or "DB2",
                        "schema": client.schema if client else None,
                        "columns": [],
                        "rows": [],
                        "rowCount": 0,
                        "executionTimeMs": execution_time_ms,
                        "toolCall": "query_database",
                        "truncated": False,
                        "error": result.error
                    }
                )
                return ToolResult(success=False, error=result.error)

            # Build column definitions
            columns = []
            if result.columns:
                for col_name in result.columns:
                    columns.append({
                        "name": col_name,
                        "type": "VARCHAR",  # DB2 doesn't provide type info in basic result
                        "nullable": True,
                        "visible": True
                    })

            # Emit success event
            await self._event_bridge.emit(
                AgentEventType.WORKSPACE_SQL_RESULT.value,
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": int(time.time() * 1000),
                    "query": query,
                    "database": settings.database.database or "DB2",
                    "schema": client.schema if client else None,
                    "columns": columns,
                    "rows": result.rows or [],
                    "rowCount": result.row_count,
                    "executionTimeMs": execution_time_ms,
                    "toolCall": "query_database",
                    "truncated": result.truncated,
                    "error": None
                }
            )

            # Formatierte Ausgabe für Agent
            output = f"=== Query-Ergebnis ===\n"
            output += f"Zeilen: {result.row_count}"
            if result.truncated:
                output += f" (begrenzt auf {client.max_rows})"
            output += "\n\n"

            if result.columns and result.rows:
                output += " | ".join(result.columns) + "\n"
                output += "-" * (len(" | ".join(result.columns))) + "\n"
                for row in result.rows:
                    output += " | ".join(str(v) if v is not None else "NULL" for v in row) + "\n"

            return ToolResult(success=True, data=output)

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            # Emit error event
            await self._event_bridge.emit(
                AgentEventType.WORKSPACE_SQL_RESULT.value,
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": int(time.time() * 1000),
                    "query": query,
                    "database": "DB2",
                    "schema": None,
                    "columns": [],
                    "rows": [],
                    "rowCount": 0,
                    "executionTimeMs": execution_time_ms,
                    "toolCall": "query_database",
                    "truncated": False,
                    "error": str(e)
                }
            )
            return ToolResult(success=False, error=str(e))

    def _get_mcp_event_type_mapping(self) -> Dict[str, AgentEventType]:
        """Mapping von MCP Event-Typen zu AgentEventType."""
        return {
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

    async def _drain_mcp_events(self) -> AsyncGenerator[AgentEvent, None]:
        """
        Liefert alle wartenden MCP Events aus der Event Bridge.
        Mappt Event-Typen zu AgentEventType.
        """
        type_mapping = self._get_mcp_event_type_mapping()
        async for event in self._event_bridge.drain():
            event_type_enum = type_mapping.get(event.event_type, AgentEventType.MCP_STEP)
            yield AgentEvent(event_type_enum, event.data)

    async def _drain_mcp_events_from_queue(self, queue: asyncio.Queue, timeout: float = 0.01) -> AsyncGenerator[AgentEvent, None]:
        """
        Liefert alle wartenden MCP Events aus einer bestehenden Queue.
        Verwendet für persistente Subscriptions während Tool-Ausführung.
        """
        type_mapping = self._get_mcp_event_type_mapping()
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
                event_type_enum = type_mapping.get(event.event_type, AgentEventType.MCP_STEP)
                yield AgentEvent(event_type_enum, event.data)
            except asyncio.TimeoutError:
                break

    def _get_research_capability(self) -> Optional["ResearchCapability"]:
        """Initialisiert und gibt die ResearchCapability zurück."""
        if self._research_capability is None:
            try:
                self._research_capability = get_research_capability()
            except Exception as e:
                logger.debug(f"[agent] ResearchCapability not available: {e}")
        return self._research_capability

    def _should_auto_research(self, query: str) -> bool:
        """
        Prüft ob automatische Research-Phase aktiviert werden soll.

        Basiert auf:
        - Config-Setting research_enabled
        - auto_research_on_question
        - Keyword-Matching aus auto_research_keywords
        - Ausschluss von Begrüßungen und Small-Talk
        """
        if not settings.mcp.research_enabled:
            return False
        if not settings.mcp.auto_research_on_question:
            return False

        query_lower = query.lower().strip()

        # Zu kurze Queries ignorieren (wahrscheinlich Begrüßung)
        if len(query_lower) < 15:
            return False

        # Begrüßungen und Small-Talk ausschließen
        greetings = [
            "hi", "hallo", "hey", "moin", "servus", "grüß", "guten tag",
            "guten morgen", "guten abend", "wie geht", "wie gehts",
            "was geht", "alles klar", "na du", "hello", "good morning",
            "how are you", "what's up", "danke", "bitte", "tschüss", "bye"
        ]
        for greeting in greetings:
            if greeting in query_lower:
                return False

        # Prüfe ob Query ein Keyword enthält
        for keyword in settings.mcp.auto_research_keywords:
            if keyword.lower() in query_lower:
                return True

        # Prüfe ob Query eine Frage ist (? oder Fragewörter)
        question_indicators = ["?", "wie ", "was ", "warum ", "wann ", "wo ", "wer ",
                               "how ", "what ", "why ", "when ", "where ", "who "]
        for indicator in question_indicators:
            if indicator in query_lower:
                return True

        return False

    async def _run_research_phase(
        self,
        query: str,
        messages: List[Dict],
        budget,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Führt die Research-Phase mit parallelen Quellen aus.

        Nutzt ResearchCapability um parallel in verschiedenen
        Quellen zu suchen (Memory, Code, Web, Docs).

        Yieldet RESEARCH_START / RESEARCH_PROGRESS / RESEARCH_DONE Events.
        """
        from app.utils.token_counter import estimate_tokens

        research = self._get_research_capability()
        if research is None:
            return

        try:
            # RESEARCH_START Event
            yield AgentEvent(AgentEventType.MCP_START, {
                "mode": "research",
                "query": query[:100],
                "message": "Research-Phase gestartet..."
            })

            # Research ausführen mit Timeout
            timeout = settings.mcp.research_timeout_seconds
            session = await asyncio.wait_for(
                research.execute(
                    query=query,
                    context=None,
                    max_results=settings.mcp.max_research_results
                ),
                timeout=timeout
            )

            # Progress-Event mit Ergebnissen
            if session.is_complete:
                result_count = len(session.artifacts) if session.artifacts else 0
                yield AgentEvent(AgentEventType.MCP_PROGRESS, {
                    "mode": "research",
                    "progress": 100,
                    "message": f"{result_count} Ergebnisse gefunden"
                })

                # Research-Ergebnis als Kontext injizieren
                if session.final_conclusion:
                    research_context = f"""## Research-Ergebnisse (automatisch)

{session.final_conclusion}
"""
                    research_tokens = estimate_tokens(research_context)
                    if budget.can_add("context", research_tokens):
                        messages.append({"role": "system", "content": research_context})
                        budget.add("context", research_tokens)
                        logger.debug(f"[agent] Research context injected: {research_tokens} tokens")

            # RESEARCH_DONE Event
            yield AgentEvent(AgentEventType.MCP_COMPLETE, {
                "mode": "research",
                "success": session.is_complete,
                "message": "Research-Phase abgeschlossen"
            })

            # Workspace Research Events für UI Panel
            if session.is_complete:
                # Emit einzelne Items für jedes Artifact
                if session.artifacts:
                    for artifact in session.artifacts:
                        yield AgentEvent(AgentEventType.WORKSPACE_RESEARCH, {
                            "source": artifact.source_name if hasattr(artifact, 'source_name') else "research",
                            "title": artifact.title if hasattr(artifact, 'title') else query[:50],
                            "snippet": artifact.content[:500] if hasattr(artifact, 'content') else "",
                            "url": artifact.url if hasattr(artifact, 'url') else None,
                            "relevance": artifact.relevance if hasattr(artifact, 'relevance') else 0.5
                        })
                # Falls keine Artifacts aber Conclusion: Zusammenfassung als Item
                elif session.final_conclusion:
                    yield AgentEvent(AgentEventType.WORKSPACE_RESEARCH, {
                        "source": "summary",
                        "title": f"Research: {query[:40]}",
                        "snippet": session.final_conclusion[:500],
                        "url": None,
                        "relevance": 1.0
                    })

        except asyncio.TimeoutError:
            logger.warning(f"[agent] Research phase timed out after {timeout}s")
            yield AgentEvent(AgentEventType.MCP_ERROR, {
                "mode": "research",
                "error": f"Timeout nach {timeout}s"
            })
        except Exception as e:
            logger.warning(f"[agent] Research phase failed: {e}")
            yield AgentEvent(AgentEventType.MCP_ERROR, {
                "mode": "research",
                "error": str(e)
            })

    def _extract_conversation_context(self, messages: List[Dict], max_messages: int = 4) -> Optional[str]:
        """
        Extrahiert einen kurzen Kontext aus den letzten Konversations-Nachrichten.

        Gibt den Sub-Agenten Kontext über die vorherige Konversation,
        sodass Follow-up-Fragen wie "aus meiner Eingangsfrage" verstanden werden.

        Args:
            messages: Die vollständige Message-Liste
            max_messages: Max Anzahl User/Assistant-Nachrichten zum Extrahieren

        Returns:
            Kontext-String oder None wenn keine relevanten Nachrichten
        """
        # Nur User/Assistant-Nachrichten extrahieren (keine System-Prompts)
        relevant = [
            m for m in messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

        if len(relevant) <= 1:
            return None  # Keine vorherige Konversation

        # Letzte N Nachrichten nehmen (ohne die aktuelle User-Nachricht, die ist die Query)
        recent = relevant[-(max_messages + 1):-1] if len(relevant) > max_messages else relevant[:-1]

        if not recent:
            return None

        # Als kompakten Kontext-String formatieren
        context_parts = []
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = str(msg.get("content", ""))[:300]  # Kürzen
            if len(str(msg.get("content", ""))) > 300:
                content += "..."
            context_parts.append(f"{role}: {content}")

        return "\n\n".join(context_parts)

    async def _run_sub_agents_phase(
        self,
        user_message: str,
        model: Optional[str],
        messages: List[Dict],
        budget,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Führt die parallele Sub-Agent-Erkundungsphase aus.

        Sub-Agenten durchsuchen ihre spezialisierten Datenquellen parallel
        und liefern komprimierte Zusammenfassungen zurück.
        Diese werden als System-Message in den Main-Context injiziert,
        bevor der Main-Agent-Loop startet.

        Yieldet SUBAGENT_START / SUBAGENT_DONE / SUBAGENT_ERROR Events.
        """
        from app.agent.sub_agents import get_sub_agent_dispatcher
        from app.agent.sub_agent import format_sub_agent_results
        from app.services.llm_client import llm_client
        from app.utils.token_counter import estimate_tokens

        try:
            dispatcher = get_sub_agent_dispatcher()
        except Exception as e:
            logger.debug("[sub_agents] Dispatcher nicht verfügbar: {e}")
            return

        routing_model = (
            settings.sub_agents.routing_model
            or settings.llm.tool_model
            or settings.llm.default_model
        )

        # Phase 1: Routing läuft – noch keine Agenten bekannt
        yield AgentEvent(AgentEventType.SUBAGENT_START, {
            "message": "Intent-Routing läuft...",
            "routing_model": routing_model,
        })

        # Routing: welche Agenten werden benötigt?
        try:
            selected_agents = await dispatcher.classify_intent(user_message, llm_client)
        except Exception as e:
            logger.debug("[sub_agents] Routing fehlgeschlagen: {e}")
            yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {"error": f"Routing: {e}"})
            return

        # Phase 2: Routing fertig – ausgewählte Agenten bekannt
        yield AgentEvent(AgentEventType.SUBAGENT_ROUTING, {
            "agents": selected_agents,
            "routing_model": routing_model,
        })

        if not selected_agents:
            logger.debug("[sub_agents] Keine relevanten Agenten ermittelt – überspringe Phase")
            return

        # Konversations-Kontext für Sub-Agenten extrahieren
        # Letzte 3-4 User/Assistant-Nachrichten als Kurzkontext
        conversation_context = self._extract_conversation_context(messages)

        # Phase 3: Ausgewählte Agenten parallel ausführen
        try:
            results = await dispatcher.dispatch_selected(
                query=user_message,
                agents=selected_agents,
                llm_client=llm_client,
                tool_registry=self.tools,
                conversation_context=conversation_context,
            )
        except Exception as e:
            logger.debug("[sub_agents] Dispatch fehlgeschlagen: {e}")
            yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {"error": str(e)})
            return

        # Events für jeden Sub-Agenten emittieren
        for result in results:
            if result.success:
                yield AgentEvent(AgentEventType.SUBAGENT_DONE, {
                    "agent": result.agent_name,
                    "findings_count": len(result.key_findings),
                    "sources_count": len(result.sources),
                    "duration_ms": result.duration_ms,
                })
            else:
                yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {
                    "agent": result.agent_name,
                    "error": result.error,
                })

        # ── SubAgentCoordinator: Deduplizierung und Ranking ──
        coordinator = SubAgentCoordinator()
        try:
            coordinated = await coordinator.process_results(results, user_message)
            logger.debug(
                f"[sub_agents] Coordination: {coordinated.total_findings} total → "
                f"{coordinated.unique_findings} unique ({coordinated.duplicates_removed} dupes removed)"
            )
            # Verwende koordinierten Context-Block
            context_block = coordinated.to_context_block()
        except Exception as e:
            logger.debug(f"[sub_agents] Coordination failed, using fallback: {e}")
            # Fallback auf einfache Formatierung
            context_block = format_sub_agent_results(results)
        if context_block:
            context_tokens = estimate_tokens(context_block)
            # Nur injizieren wenn Token-Budget ausreicht
            if budget.can_add("context", context_tokens):
                # Nach dem ersten System-Message (Haupt-Prompt) einfügen
                insert_pos = 1
                messages.insert(insert_pos, {
                    "role": "system",
                    "content": context_block,
                })
                budget.add("context", context_tokens)
                logger.debug("[sub_agents] Context injiziert: {context_tokens} Tokens")
            else:
                logger.debug("[sub_agents] Context zu groß ({context_tokens} Tokens), übersprungen")

    async def process(
        self,
        session_id: str,
        user_message: str,
        model: Optional[str] = None,
        context_selection: Optional[Any] = None,
    ) -> AsyncGenerator[AgentEvent, Optional[bool]]:
        """
        Verarbeitet eine User-Nachricht im Agent-Loop.

        Yieldet AgentEvents für das Frontend.
        Bei CONFIRM_REQUIRED wartet der Generator auf eine Antwort via send().

        Args:
            session_id: Session-ID
            user_message: Nachricht des Users
            model: Optional: LLM-Modell
            context_selection: Optional: Manuell ausgewählte Kontext-Elemente (ContextSelection)

        Yields:
            AgentEvent für jeden Schritt
        """
        from app.services.llm_client import llm_client
        from app.services.skill_manager import get_skill_manager
        import re

        state = self._get_state(session_id)
        # Session-ID für MCP Token-Tracking setzen
        self._current_mcp_session = session_id
        # Gelesene Dateien für diese Anfrage zurücksetzen (Loop-Prävention)
        state.read_files_this_request = {}
        # Abbruch-Flag zurücksetzen
        state.cancelled = False
        # Progress-Tracker für Stuck-Detection zurücksetzen
        reset_progress_tracker(session_id)
        progress_tracker = get_progress_tracker(session_id)

        # ── MCP Force-Capability Detection ────────────────────────────────────
        # Format: [MCP:capability_name] actual query
        forced_capability = None
        mcp_match = _RE_MCP_FORCE.match(user_message)
        if mcp_match:
            forced_capability = mcp_match.group(1)
            user_message = mcp_match.group(2).strip()
            logger.debug("[agent] Forced MCP capability: {forced_capability}")

        # ── Continue-Handling (nach Bestätigung) ───────────────────────────────
        # ControlMarkers.CONTINUE wird nach Schreibbestätigung gesendet
        is_continue = user_message.strip() == ControlMarkers.CONTINUE
        if is_continue:
            logger.debug("[agent] Continue nach Bestätigung erkannt")
            # Ersetze durch System-Hinweis statt User-Message
            user_message = (
                "Die letzte Datei-Operation wurde bestätigt und ausgeführt. "
                "Setze die Arbeit fort und führe die verbleibenden Schritte aus."
            )

        # ControlMarkers.CONTINUE_ENHANCED wird nach Enhancement-Bestätigung gesendet
        is_continue_enhanced = user_message.strip() == ControlMarkers.CONTINUE_ENHANCED
        if is_continue_enhanced:
            logger.debug("[agent] Continue after enhancement confirmation")
            # Hole originale Query zurück
            if state.enhancement_original_query:
                user_message = state.enhancement_original_query
                logger.info(f"[agent] Restored original query: {user_message[:50]}...")
            else:
                user_message = "Fahre mit der Anfrage fort."

        # ControlMarkers.RETRY_WITH_WEB wird nach Web-Fallback-Bestätigung gesendet
        is_retry_with_web = user_message.strip() == ControlMarkers.RETRY_WITH_WEB
        if is_retry_with_web:
            logger.debug("[agent] Retry with web search approved")
            state.web_fallback_approved = True
            # Hole originale Query zurück für Research-Retry
            if state.enhancement_original_query:
                user_message = state.enhancement_original_query
                logger.info(f"[agent] Retrying with web: {user_message[:50]}...")
            else:
                user_message = "Führe die Recherche mit Web-Suche durch."

        # ControlMarkers.CONTINUE_WITHOUT_WEB wird nach Web-Fallback-Ablehnung gesendet
        is_continue_no_web = user_message.strip() == ControlMarkers.CONTINUE_WITHOUT_WEB
        if is_continue_no_web:
            logger.debug("[agent] Continue without web search")
            state.web_fallback_approved = False
            if state.enhancement_original_query:
                user_message = state.enhancement_original_query
            else:
                user_message = "Fahre ohne Web-Ergebnisse fort."
        # ─────────────────────────────────────────────────────────────────────

        # ── MCP Prompt Enhancement Phase ────────────────────────────────────
        # Sammelt Kontext via MCP vor Task-Decomposition
        enriched_context: Optional[str] = None

        # Check if we have already confirmed enhancement context (from [CONTINUE_ENHANCED])
        if is_continue_enhanced and state.confirmed_enhancement_context:
            enriched_context = state.confirmed_enhancement_context
            logger.info(f"[agent] Using confirmed enhancement context: {len(enriched_context)} chars")
            # Clear after use
            state.confirmed_enhancement_context = None
            state.enhancement_original_query = None

        # ── FIX: User-Antwort auf Klärungsfragen mit bestehendem Kontext kombinieren ──
        elif state.pending_enhancement and state.pending_enhancement.context_items:
            # User hat auf eine Klärungsfrage geantwortet - Kontext beibehalten!
            from app.agent.prompt_enhancer import ContextItem
            pending = state.pending_enhancement

            logger.info(f"[agent] Combining clarification answer with existing enhancement context")

            # User-Antwort als zusätzlichen Kontext hinzufügen
            pending.context_items.append(ContextItem(
                source="user_clarification",
                title="User-Antwort auf Klärungsfrage",
                content=user_message,
                relevance=1.0
            ))

            # Kontext mit ursprünglicher Query und User-Antwort kombinieren
            enriched_context = pending.get_context_for_planner()

            # Ursprüngliche Query wiederherstellen und mit Antwort ergänzen
            original_query = pending.original_query
            user_message = f"{original_query}\n\nUser-Klarstellung: {user_message}"

            logger.info(f"[agent] Enhanced context with clarification: {len(enriched_context)} chars")

            # Enhancement als verwendet markieren
            state.pending_enhancement = None

        elif not is_continue and not is_continue_enhanced and settings.task_agents.enabled:
            try:
                from app.agent.prompt_enhancer import (
                    get_prompt_enhancer,
                    EnhancementType,
                    ConfirmationStatus
                )

                enhancer = get_prompt_enhancer(
                    event_callback=self._emit_mcp_event  # FIX: Enable live event streaming
                )

                # Prüfen ob Enhancement sinnvoll
                if enhancer.detector.should_enhance(user_message):
                    logger.info("[agent] Starting MCP prompt enhancement")

                    # ── TaskTracker: Kontext-Sammlung Task ──────────────────────
                    task_tracker = get_task_tracker(session_id)
                    enhancement_task_id = task_tracker.create_task(
                        title="Kontext-Sammlung",
                        steps=["Anfrage analysieren", "Quellen durchsuchen", "Kontext aufbereiten"],
                        metadata={"type": "enhancement", "query_preview": user_message[:100]}
                    )
                    await task_tracker.start_task(enhancement_task_id)
                    await task_tracker.start_step(enhancement_task_id, 0, "Analysiere Anfrage...")
                    # ────────────────────────────────────────────────────────────

                    # Enhancement-Start Event
                    yield AgentEvent(AgentEventType.ENHANCEMENT_START, {
                        "query_preview": user_message[:100],
                        "detection_type": enhancer.detector.detect(user_message).value
                    })

                    # Subscribe to event bridge for live streaming during enhancement
                    mcp_queue = self._event_bridge.subscribe()

                    try:
                        # TaskTracker: Schritt 1 abgeschlossen, Schritt 2 starten
                        await task_tracker.complete_step(enhancement_task_id, 0)
                        await task_tracker.start_step(enhancement_task_id, 1, "Durchsuche Quellen...")

                        # Kontext sammeln in separatem Task für Live-Event-Streaming
                        enhance_task = asyncio.create_task(
                            enhancer.enhance(user_message)
                        )

                        # Events live streamen während Enhancement läuft
                        while not enhance_task.done():
                            async for mcp_event in self._drain_mcp_events_from_queue(mcp_queue):
                                yield mcp_event
                            await asyncio.sleep(0.05)

                        enriched = await enhance_task

                        # TaskTracker: Schritt 2 abgeschlossen, Schritt 3 starten
                        await task_tracker.complete_step(enhancement_task_id, 1)
                        await task_tracker.start_step(enhancement_task_id, 2, "Bereite Kontext auf...")
                    finally:
                        self._event_bridge.unsubscribe(mcp_queue)

                    if enriched.context_items:
                        # Enhancement in State speichern für API-Zugriff
                        state.pending_enhancement = enriched

                        # ────────────────────────────────────────────────────────
                        # CHECK: Braucht dieser Enhancement-Typ User-Bestätigung?
                        # ────────────────────────────────────────────────────────
                        confirm_mode = settings.task_agents.enhancement_confirm_mode
                        always_confirm_types = settings.task_agents.enhancement_always_confirm
                        enhancement_type = enriched.enhancement_type.value

                        needs_confirmation = False
                        if confirm_mode == "all":
                            needs_confirmation = True
                        elif confirm_mode == "write_only":
                            # Nur bei Schreiboperationen (nicht bei Research)
                            needs_confirmation = False
                        elif enhancement_type in always_confirm_types:
                            needs_confirmation = True
                        # confirm_mode == "none" → needs_confirmation bleibt False

                        if needs_confirmation:
                            # Enhancement-Complete Event - User-Bestätigung anfordern
                            yield AgentEvent(AgentEventType.ENHANCEMENT_COMPLETE, {
                                "context_count": len(enriched.context_items),
                                "sources": enriched.context_sources,
                                "summary": enriched.summary,
                                "confirmation_message": enriched.get_confirmation_message(),
                                "context_items": [
                                    {
                                        "source": item.source,
                                        "title": item.title,
                                        "content_preview": item.content[:200] + "..." if len(item.content) > 200 else item.content,
                                        "relevance": item.relevance,
                                        "file_path": item.file_path,
                                        "url": item.url
                                    }
                                    for item in enriched.context_items
                                ]
                            })

                            # Auf User-Bestätigung warten
                            yield AgentEvent(AgentEventType.CONFIRM_REQUIRED, {
                                "type": "enhancement",
                                "message": enriched.get_confirmation_message()
                            })

                            # ────────────────────────────────────────────────────────
                            # LEGACY YIELD PATTERN - ONLY USED WITH CONFIRMATION
                            # ────────────────────────────────────────────────────────
                            # This yield/send pattern was designed for direct generator
                            # confirmation but is NOT used in the actual flow.
                            #
                            # Actual confirmation flow:
                            # 1. Frontend receives CONFIRM_REQUIRED event
                            # 2. User clicks confirm/reject in UI
                            # 3. API endpoint POST /enhancement/{session_id}/confirm
                            #    stores the confirmed context
                            # 4. Frontend sends [CONTINUE_ENHANCED] message
                            # 5. Orchestrator detects is_continue_enhanced=True (line ~995)
                            #    and retrieves stored context from state.pending_enhancement
                            # ────────────────────────────────────────────────────────
                            user_confirmed = yield
                            if user_confirmed is None:
                                user_confirmed = True  # Default: bestätigen

                            if user_confirmed:
                                enriched = enhancer.confirm(enriched, True)
                                enriched_context = enriched.get_context_for_planner()
                                state.pending_enhancement = None  # Clear after confirmation
                                yield AgentEvent(AgentEventType.ENHANCEMENT_CONFIRMED, {
                                    "context_length": len(enriched_context)
                                })
                                logger.info(f"[agent] Enhancement confirmed, context: {len(enriched_context)} chars")
                            else:
                                enriched = enhancer.confirm(enriched, False)
                                state.pending_enhancement = None  # Clear after rejection
                                yield AgentEvent(AgentEventType.ENHANCEMENT_REJECTED, {})
                                logger.info("[agent] Enhancement rejected by user")
                        else:
                            # Auto-Confirm: Direkt bestätigen ohne UI
                            enriched = enhancer.confirm(enriched, True)
                            enriched_context = enriched.get_context_for_planner()
                            state.pending_enhancement = None
                            logger.info(f"[agent] Enhancement auto-confirmed, context: {len(enriched_context)} chars")

                        # TaskTracker: Enhancement-Task abschließen (mit Artifact)
                        await task_tracker.complete_step(enhancement_task_id, 2, artifacts=[
                            TaskArtifact(
                                id=f"ctx_{enhancement_task_id[:8]}",
                                type="context",
                                summary=f"{len(enriched.context_items)} Kontext-Elemente gesammelt",
                                data={"context_count": len(enriched.context_items), "sources": enriched.context_sources}
                            )
                        ])
                        await task_tracker.complete_task(enhancement_task_id)

                    elif enriched.cache_hit:
                        # Cache-Hit ohne neue Items
                        enriched_context = enriched.get_context_for_planner()
                        logger.debug("[agent] Using cached enhancement context")
                        # TaskTracker: Steps überspringen und Task abschließen (Cache-Hit)
                        await task_tracker.skip_step(enhancement_task_id, 2, "Cache-Treffer")
                        await task_tracker.complete_task(enhancement_task_id)
                    else:
                        # Kein Kontext gefunden - Task trotzdem abschließen
                        await task_tracker.skip_step(enhancement_task_id, 2, "Kein Kontext gefunden")
                        await task_tracker.complete_task(enhancement_task_id)

            except ImportError as e:
                logger.debug(f"[agent] Prompt enhancement not available: {e}")
            except Exception as e:
                logger.warning(f"[agent] Prompt enhancement failed: {e}")
                # TaskTracker: Task als fehlgeschlagen markieren (falls erstellt)
                try:
                    if 'enhancement_task_id' in locals():
                        await task_tracker.fail_task(enhancement_task_id, str(e))
                except Exception:
                    pass  # Task-Tracking-Fehler nicht propagieren
                # Fehler-Event emittieren (nicht mehr silent!)
                yield AgentEvent(AgentEventType.MCP_ERROR, {
                    "mode": "enhancement",
                    "error": str(e),
                    "message": "Kontext-Sammlung fehlgeschlagen - fahre ohne Kontext fort"
                })
                # Bei Fehler: Direkt zu Tasks (Fallback)
        # ─────────────────────────────────────────────────────────────────────

        # ── Task-Decomposition Check ─────────────────────────────────────────
        # Bei komplexen Anfragen: Task-Decomposition-System verwenden
        if not is_continue and settings.task_agents.enabled:
            try:
                from app.agent.task_integration import (
                    should_use_task_decomposition,
                    process_with_tasks,
                    format_task_response_for_user,
                    format_clarification_questions,
                    TaskEventType
                )

                if await should_use_task_decomposition(user_message):
                    logger.info("[agent] Using Task-Decomposition for complex query")

                    # WICHTIG: Event SOFORT senden bevor LLM-Call blockiert!
                    yield AgentEvent(AgentEventType.MCP_START, {
                        "mode": "task_planning",
                        "message": "Erstelle Ausführungsplan...",
                        "query_preview": user_message[:100]
                    })

                    # Task-Events zu Agent-Events mappen und yielden
                    async for task_event in process_with_tasks(
                        user_message=user_message,
                        session_id=session_id,
                        context=enriched_context,  # MCP-angereicherter Kontext
                        project_id=state.project_id if hasattr(state, 'project_id') else None,
                        project_path=state.project_path if hasattr(state, 'project_path') else None
                    ):
                        event_type = task_event.get("type", "")
                        event_data = task_event.get("data", {})

                        # Event-Mapping
                        if event_type == "planning_start":
                            # Progress-Event für UI während LLM plant
                            yield AgentEvent(AgentEventType.MCP_PROGRESS, {
                                "mode": "task_planning",
                                "message": event_data.get("message", "Plane Tasks..."),
                                "progress": 10
                            })

                        elif event_type == TaskEventType.PLAN_CREATED:
                            yield AgentEvent(AgentEventType.TASK_PLAN_CREATED, event_data)

                        elif event_type == TaskEventType.TASK_STARTED:
                            yield AgentEvent(AgentEventType.TASK_STARTED, event_data)

                        elif event_type == TaskEventType.TASK_COMPLETED:
                            yield AgentEvent(AgentEventType.TASK_COMPLETED, event_data)

                        elif event_type == TaskEventType.TASK_FAILED:
                            yield AgentEvent(AgentEventType.TASK_FAILED, event_data)

                        elif event_type == TaskEventType.CLARIFICATION_NEEDED:
                            # Klaerungsfragen als Token-Stream senden
                            questions = event_data.get("questions", [])
                            formatted = format_clarification_questions(questions)
                            yield AgentEvent(AgentEventType.TOKEN, formatted)
                            yield AgentEvent(AgentEventType.TASK_CLARIFICATION, event_data)
                            yield AgentEvent(AgentEventType.DONE, None)
                            return

                        elif event_type == TaskEventType.EXECUTION_COMPLETE:
                            # Finale Antwort als Token-Stream
                            response = format_task_response_for_user(event_data)
                            yield AgentEvent(AgentEventType.TOKEN, response)
                            yield AgentEvent(AgentEventType.TASK_EXECUTION_DONE, event_data)
                            # In Historie speichern
                            state.messages_history.append({
                                "role": "assistant",
                                "content": response
                            })
                            yield AgentEvent(AgentEventType.DONE, None)
                            return

                        elif event_type == "use_direct_processing":
                            # Unter Threshold -> normale Verarbeitung fortsetzen
                            logger.debug("[agent] Task count below threshold, using direct processing")
                            break

                        elif event_type == "error":
                            yield AgentEvent(AgentEventType.ERROR, event_data)
                            yield AgentEvent(AgentEventType.DONE, None)
                            return

            except ImportError as e:
                logger.warning(f"[agent] Task-Decomposition not available: {e}")
            except asyncio.CancelledError:
                # Async task wurde abgebrochen - nicht als Fehler behandeln
                logger.info("[agent] Task-Decomposition cancelled")
                raise  # Re-raise damit der uebergeordnete Handler es behandelt
            except Exception as e:
                logger.error(f"[agent] Task-Decomposition failed: {e}")
                # Fehler-Event emittieren (nicht mehr silent!)
                yield AgentEvent(AgentEventType.ERROR, {
                    "source": "task_decomposition",
                    "error": str(e),
                    "message": "Task-Zerlegung fehlgeschlagen - fahre mit direkter Verarbeitung fort"
                })
                # Bei Fehler normal fortfahren
        # ─────────────────────────────────────────────────────────────────────

        # Schreib-Ops: In Planungsphase nur erlaubt wenn Plan bereits genehmigt
        if state.mode == AgentMode.PLAN_THEN_EXECUTE:
            include_write_ops = state.plan_approved
        elif state.mode in (AgentMode.READ_ONLY, AgentMode.DEBUG):
            include_write_ops = False
        else:
            include_write_ops = True

        # System-Prompt bauen
        system_prompt = SYSTEM_PROMPT

        # Skill-Prompts hinzufügen
        if state.active_skill_ids:
            try:
                skill_mgr = get_skill_manager()
                for sid in state.active_skill_ids:
                    skill_mgr.activate_skill(session_id, sid)
                skill_prompt = skill_mgr.build_system_prompt(session_id)
                if skill_prompt:
                    system_prompt += f"\n\n{skill_prompt}"
                # Skill erfordert Planungsphase → Modus automatisch setzen
                if (
                    skill_mgr.requires_plan(session_id)
                    and state.mode not in (AgentMode.PLAN_THEN_EXECUTE,)
                    and not state.plan_approved
                ):
                    state.mode = AgentMode.PLAN_THEN_EXECUTE
                    include_write_ops = False
                    logger.debug("[agent] Skill erfordert Planungsphase → Modus auf PLAN_THEN_EXECUTE gesetzt")
            except Exception:
                pass

        # Tool-Definitionen
        tool_schemas = self.tools.get_openai_schemas(include_write_ops=include_write_ops)

        # MCP-Tools hinzufügen (Sequential Thinking, etc.)
        if settings.mcp.sequential_thinking_enabled or settings.mcp.enabled:
            try:
                if self._mcp_bridge is None:
                    self._mcp_bridge = get_tool_bridge(
                        llm_callback=self._llm_callback_for_mcp,
                        event_callback=self._emit_mcp_event
                    )
                mcp_tools = self._mcp_bridge.get_tool_definitions()
                tool_schemas.extend(mcp_tools)
                if mcp_tools:
                    logger.debug("[agent] MCP tools added: {len(mcp_tools)}")
            except Exception as e:
                logger.debug("[agent] MCP bridge initialization failed: {e}")

        # PR-Context-Erkennung: Filtere Tools wenn PR-URL in der Nachricht
        pr_url = _detect_pr_context(user_message)
        if pr_url:
            tool_schemas = _filter_tools_for_pr_context(tool_schemas, pr_url)
            # Zusätzlicher Hinweis im System-Prompt
            system_prompt += (
                "\n\n## WICHTIG: PR-Analyse-Modus\n"
                f"Du analysierst den Pull Request: {pr_url}\n"
                "Verwende NUR GitHub-Tools (github_pr_diff, github_get_file, etc.).\n"
                "Lokale Tools wie read_file oder search_code sind NICHT verfügbar.\n"
                "Die PR-Analyse erscheint automatisch im Workspace-Panel."
            )

        # Agent-Instruktionen
        agent_instructions = self._build_agent_instructions(state.mode, state.plan_approved)
        system_prompt += f"\n\n{agent_instructions}"

        # Planungsphase: Plan als Kontext injizieren wenn genehmigt
        if state.mode == AgentMode.PLAN_THEN_EXECUTE and state.plan_approved and state.pending_plan:
            system_prompt += f"\n\n## Genehmigter Ausführungsplan\n\n{state.pending_plan}\n\nFühre diesen Plan jetzt Schritt für Schritt aus."

        # Token Budget initialisieren
        state.token_budget = create_budget_from_config()
        budget = state.token_budget

        # Messages aufbauen
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        budget.set("system", estimate_tokens(system_prompt))

        # === PROJECT CONTEXT (statisch, aus PROJECT_CONTEXT.md) ===
        try:
            project_context = await self.context_manager.build_full_context(
                project_path=state.project_path,
                project_id=state.project_id,
                max_tokens=min(500, budget.memory_limit // 3)
            )
            if project_context:
                messages.append({"role": "system", "content": project_context})
                budget.set("memory", estimate_tokens(project_context))
        except Exception as e:
            logger.debug(f"[agent] Project context loading failed: {e}")

        # === MEMORY CONTEXT (dynamisch, multi-scope) ===
        try:
            memory_context = await self.memory_store.get_context_injection(
                current_message=user_message,
                project_id=state.project_id,
                session_id=session_id,
                scopes=["global", "project", "session"],
                max_tokens=budget.memory_limit - budget.used_memory
            )
            if memory_context:
                messages.append({"role": "system", "content": memory_context})
                budget.set("memory", budget.used_memory + estimate_tokens(memory_context))
        except Exception as e:
            logger.debug(f"[agent] Memory loading failed: {e}")

        # Entity-Kontext: Bekannte Entitäten aus dieser Session (Java ↔ Handbuch ↔ PDF)
        entity_hint = state.entity_tracker.get_context_hint()
        if entity_hint:
            messages.append({"role": "system", "content": entity_hint})
            budget.set("memory", budget.used_memory + estimate_tokens(entity_hint))

        # Tool-Budget-Hinweis: Optimierungstipps bei niedrigem Budget
        if state.tool_budget:
            budget_hint = state.tool_budget.get_budget_hint()
            if budget_hint:
                messages.append({"role": "system", "content": budget_hint})
                logger.debug(f"[agent] Budget-Hint injiziert (Level: {state.tool_budget.level.value})")

        # Manuell vom Nutzer ausgewählte Kontext-Elemente (Explorer-Chips)
        if context_selection:
            hint_parts = []
            if getattr(context_selection, "java_files", None):
                paths = ", ".join(context_selection.java_files[:20])
                hint_parts.append(f"Java-Dateien: {paths}")
            if getattr(context_selection, "python_files", None):
                paths = ", ".join(context_selection.python_files[:20])
                hint_parts.append(f"Python-Dateien: {paths}")
            if getattr(context_selection, "pdf_ids", None):
                ids = ", ".join(context_selection.pdf_ids[:10])
                hint_parts.append(f"PDF-Dokumente (IDs): {ids}")
            if getattr(context_selection, "handbook_services", None):
                ids = ", ".join(context_selection.handbook_services[:10])
                hint_parts.append(f"Handbuch-Services: {ids}")
            if hint_parts:
                ctx_msg = (
                    "Der Nutzer hat folgende Elemente explizit als Kontext ausgewählt. "
                    "Beziehe dich bevorzugt auf diese Quellen:\n"
                    + "\n".join(f"- {p}" for p in hint_parts)
                )
                messages.append({"role": "system", "content": ctx_msg})
                budget.set("memory", budget.used_memory + estimate_tokens(ctx_msg))
                logger.debug("[agent] Nutzer-Kontext injiziert: {hint_parts}")

        # Konversations-Historie hinzufügen (für Multi-Turn)
        # context_items werden NICHT als separate System-Message eingefügt,
        # da Tool-Results bereits korrekt als role="tool" Messages im Verlauf erscheinen.
        # Das verhindert Dopplung und reduziert LLM-Loop-Verhalten.
        history_tokens = 0
        history_count = len(state.messages_history)
        history_to_add = state.messages_history[-state.max_history_messages:]
        logger.info(f"[agent] Conversation history: {history_count} total, adding {len(history_to_add)} messages")
        if history_to_add:
            # Log ersten und letzten Eintrag für Debug
            first_msg = history_to_add[0]
            last_msg = history_to_add[-1] if len(history_to_add) > 1 else first_msg
            logger.debug(f"[agent] History first: role={first_msg.get('role')}, content[:100]={str(first_msg.get('content', ''))[:100]}")
            logger.debug(f"[agent] History last: role={last_msg.get('role')}, content[:100]={str(last_msg.get('content', ''))[:100]}")
        for hist_msg in history_to_add:
            messages.append(hist_msg)
            history_tokens += estimate_tokens(hist_msg.get("content", ""))
        budget.set("conversation", history_tokens)

        # Aktuelle User-Nachricht hinzufügen
        messages.append({"role": "user", "content": user_message})

        # User-Nachricht in Historie speichern
        state.messages_history.append({"role": "user", "content": user_message})

        # === TRANSCRIPT LOGGING ===
        try:
            await self.transcript_logger.log_user_message(
                session_id=session_id,
                content=user_message,
                project_id=state.project_id
            )
        except Exception as e:
            logger.debug(f"[agent] Transcript logging failed: {e}")

        # === ANALYTICS CHAIN START ===
        if self._analytics.enabled:
            try:
                await self._analytics.start_chain(
                    query=user_message,
                    model=model or settings.llm.default_model,
                    model_settings={
                        "temperature": settings.llm.temperature,
                        "max_tokens": settings.llm.max_tokens,
                        "reasoning": settings.llm.reasoning_effort or None,
                        "tool_model": settings.llm.tool_model or None,
                    }
                )
            except Exception as e:
                logger.debug(f"[agent] Analytics start failed: {e}")

        # === AUTO-LEARNING (User-Message) ===
        try:
            user_candidates = await self.auto_learner.analyze_user_message(
                message=user_message,
                project_id=state.project_id,
                session_id=session_id
            )
            if user_candidates:
                saved = await self.auto_learner.save_candidates(
                    candidates=user_candidates,
                    project_id=state.project_id,
                    session_id=session_id,
                    project_path=state.project_path
                )
                if saved:
                    logger.debug(f"[agent] Auto-learned {len(saved)} items from user message")
        except Exception as e:
            logger.debug(f"[agent] Auto-learning failed: {e}")

        # Auto-Titel aus erster User-Nachricht ableiten
        if not state.title:
            state.title = user_message[:60] + ("…" if len(user_message) > 60 else "")

        # === COMPACTION CHECK ===
        # Wenn Budget zu voll (>80%), Konversation zusammenfassen.
        # Logik: Nur versuchen wenn noch nicht bei diesem Füllstand versucht wurde.
        # Nach erfolgreicher Komprimierung sinkt Budget unter 80% → Flag wird zurückgesetzt.
        if budget.needs_compaction():
            # Nur versuchen wenn nicht bereits bei vollem Budget versucht
            if not state.compaction_attempted_while_full:
                state.compaction_attempted_while_full = True
                try:
                    savings_estimate = self.summarizer.estimate_savings(messages)
                    if savings_estimate.get("would_summarize"):
                        old_tokens = estimate_messages_tokens(messages)
                        messages = await self.summarizer.summarize_if_needed(
                            messages,
                            target_tokens=budget.available_total
                        )
                        new_tokens = estimate_messages_tokens(messages)
                        savings = old_tokens - new_tokens

                        state.compaction_count += 1
                        state.last_compaction_savings = savings

                        # Budget aktualisieren
                        budget.set("conversation", estimate_messages_tokens([
                            m for m in messages if m.get("role") not in ("system",)
                        ]))

                        # Nur Event emittieren wenn tatsächlich Tokens gespart wurden
                        if savings > 100:
                            yield AgentEvent(AgentEventType.COMPACTION, {
                                "savings": savings,
                                "old_tokens": old_tokens,
                                "new_tokens": new_tokens,
                                "compaction_count": state.compaction_count
                            })

                        # Wenn Budget jetzt unter Threshold, Flag zurücksetzen
                        if not budget.needs_compaction():
                            state.compaction_attempted_while_full = False
                except Exception as e:
                    logger.debug("[agent] Compaction failed: {e}")
        else:
            # Budget unter 80% → bereit für nächste Komprimierung wenn nötig
            state.compaction_attempted_while_full = False

        # === RESEARCH PHASE (Parallele Quellensuche) ===
        # Aktiviert bei Fragen oder Keywords - sucht parallel in Memory, Code, Web, Docs
        # SKIP wenn Enhancement bereits Research durchgeführt hat (vermeidet Doppelarbeit)
        if (
            not forced_capability
            and not enriched_context  # Enhancement hat bereits Research gemacht
            and self._should_auto_research(user_message)
        ):
            try:
                logger.debug("[agent] Activating research phase...")
                async for event in self._run_research_phase(
                    user_message, messages, budget
                ):
                    yield event
            except Exception as e:
                logger.warning(f"[agent] Research phase failed: {e}")
                # Nicht kritisch - weiter mit normalem Flow
        elif enriched_context:
            logger.debug("[agent] Skipping research phase - enhancement already ran")

        # === SUB-AGENT PHASE ===
        # Spezialisierte Sub-Agenten erkunden Datenquellen parallel,
        # bevor der Main-Agent seinen Loop startet.
        if (
            settings.sub_agents.enabled
            and len(user_message) >= settings.sub_agents.min_query_length
            and not forced_capability  # Skip sub-agents when forcing capability
        ):
            async for event in self._run_sub_agents_phase(
                user_message, model, messages, budget
            ):
                yield event

        # === FORCED CAPABILITY EXECUTION ===
        # Wenn User /brainstorm, /design etc. nutzt, direkt ausführen
        if forced_capability:
            logger.debug("[agent] Executing forced capability: {forced_capability}")

            if self._mcp_bridge is None:
                self._mcp_bridge = get_tool_bridge(
                        llm_callback=self._llm_callback_for_mcp,
                        event_callback=self._emit_mcp_event
                    )

            # TOOL_START Event
            yield AgentEvent(AgentEventType.TOOL_START, {
                "id": f"forced_{forced_capability}",
                "name": forced_capability,
                "arguments": {"query": user_message},
                "model": "MCP"
            })

            try:
                # Persistente Subscription BEVOR Tool startet
                mcp_queue = self._event_bridge.subscribe()

                try:
                    # Capability in separatem Task ausführen für Live-Event-Streaming
                    tool_task = asyncio.create_task(
                        self._mcp_bridge.call_tool(
                            forced_capability,
                            {"query": user_message, "context": None}
                        )
                    )

                    # Events live streamen während Tool läuft
                    while not tool_task.done():
                        async for mcp_event in self._drain_mcp_events_from_queue(mcp_queue):
                            yield mcp_event
                        await asyncio.sleep(0.05)

                    # Tool-Result abholen
                    mcp_result = await tool_task

                    # Finale Events (falls noch welche da sind)
                    async for mcp_event in self._drain_mcp_events_from_queue(mcp_queue, timeout=0.1):
                        yield mcp_event
                finally:
                    # Subscription aufräumen
                    self._event_bridge.unsubscribe(mcp_queue)

                # Ergebnis formatieren
                if mcp_result.get("success"):
                    output = mcp_result.get("formatted_output") or mcp_result.get("result") or str(mcp_result)

                    # TOOL_RESULT Event
                    yield AgentEvent(AgentEventType.TOOL_RESULT, {
                        "id": f"forced_{forced_capability}",
                        "name": forced_capability,
                        "success": True,
                        "data": output[:500] if len(output) > 500 else output
                    })

                    # Streaming-Antwort mit formatiertem Output
                    for chunk in output.split('\n'):
                        yield AgentEvent(AgentEventType.TOKEN, chunk + '\n')

                    # Handoff-Vorschlag wenn vorhanden
                    next_cap = mcp_result.get("next_capability")
                    if next_cap:
                        yield AgentEvent(AgentEventType.TOKEN, f"\n\n---\n➡️ **Nächster Schritt:** `/{next_cap}` für die Weiterführung\n")

                else:
                    error_msg = mcp_result.get("error", "Unbekannter Fehler")
                    yield AgentEvent(AgentEventType.TOOL_RESULT, {
                        "id": f"forced_{forced_capability}",
                        "name": forced_capability,
                        "success": False,
                        "data": error_msg
                    })
                    yield AgentEvent(AgentEventType.ERROR, {"error": f"Capability {forced_capability} fehlgeschlagen: {error_msg}"})

            except Exception as e:
                yield AgentEvent(AgentEventType.ERROR, {"error": f"Capability-Ausführung fehlgeschlagen: {str(e)}"})

            yield AgentEvent(AgentEventType.DONE, {})
            return  # Beende hier - kein normaler Agent-Loop

        # Agent-Loop
        has_used_tools = False
        # Token-Tracking für diese Anfrage zurücksetzen
        state.current_usage = TokenUsage()
        request_prompt_tokens = 0
        request_completion_tokens = 0
        last_finish_reason = ""
        last_model = ""

        # ── TaskTracker: Anfrage-Verarbeitungs-Task ─────────────────────────
        task_tracker = get_task_tracker(session_id)
        processing_task_id = task_tracker.create_task(
            title="Anfrage verarbeiten",
            steps=["LLM-Analyse"],  # Wird dynamisch erweitert
            metadata={"type": "processing", "query_preview": user_message[:100]}
        )
        await task_tracker.start_task(processing_task_id)
        await task_tracker.start_step(processing_task_id, 0, "Analysiere Anfrage mit LLM...")
        tool_step_offset = 1  # Offset für dynamisch hinzugefügte Tool-Steps
        # ────────────────────────────────────────────────────────────────────

        # Debug: Tool-Schemas Anzahl loggen
        logger.debug("[agent] Starting with {len(tool_schemas)} tools, mode={state.mode.value}")

        for iteration in range(self.max_iterations):
            try:
                # Abbruch prüfen
                if state.cancelled:
                    # TaskTracker: Task abbrechen
                    try:
                        await task_tracker.cancel_task(processing_task_id)
                    except Exception:
                        pass
                    yield AgentEvent(AgentEventType.CANCELLED, {"message": "Anfrage wurde abgebrochen"})
                    return

                # ── Pending PR-Analyse prüfen ──
                # Wenn die Background-Task fertig ist, Event yielden
                if state.pending_pr_analysis is not None and state.pending_pr_analysis.done():
                    try:
                        pr_analysis_result = state.pending_pr_analysis.result()
                        yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                            "prNumber": state.pending_pr_number,
                            **pr_analysis_result
                        })
                        logger.debug(f"[agent] PR analysis completed for PR #{state.pending_pr_number}")
                    except Exception as e:
                        # Bei Fehler: Leeres Ergebnis senden damit Frontend Loading beendet
                        logger.warning(f"[agent] PR analysis failed: {e}")
                        yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                            "prNumber": state.pending_pr_number,
                            "error": str(e),
                            "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                            "verdict": "comment",
                            "findings": [],
                            "summary": "Analyse fehlgeschlagen",
                            "canApprove": state.pending_pr_state == "open"
                        })
                    finally:
                        # Task aufräumen
                        state.pending_pr_analysis = None
                        state.pending_pr_number = None
                        state.pending_pr_state = None

                # Tool-Budget: Neue Iteration starten
                if state.tool_budget and iteration > 0:
                    state.tool_budget.next_iteration()

                # === Kontext-Status prüfen und Summarizer aktivieren ===
                current_model = model or settings.llm.default_model
                model_limit = _get_model_context_limit(current_model)
                # Sicherheitsprüfung: mindestens 1000 Token Limit
                if not model_limit or model_limit < 1000:
                    model_limit = settings.llm.default_context_limit or 32000
                    logger.warning(f"[agent] Invalid context limit for {current_model}, using {model_limit}")

                try:
                    context_tokens = estimate_messages_tokens(messages)
                except Exception as e:
                    logger.warning(f"[agent] Token estimation failed: {e}")
                    context_tokens = 0

                context_percent = round(context_tokens / model_limit * 100, 1) if model_limit > 0 else 0

                # Kontext-Status an Frontend senden (non-blocking)
                try:
                    context_status_data = {
                        "current_tokens": context_tokens,
                        "limit_tokens": model_limit,
                        "percent": context_percent,
                        "iteration": iteration + 1,
                        "max_iterations": self.max_iterations,
                        "warning": context_percent > 80,
                        "critical": context_percent > 95
                    }
                    # Tool-Budget-Info hinzufuegen
                    if state.tool_budget:
                        tb = state.tool_budget
                        context_status_data["tool_budget"] = {
                            "level": tb.level.value,
                            "remaining_iterations": tb.remaining_iterations,
                            "total_tools_used": tb.total_tools_used,
                            "cache_hits": tb.cache_hits,
                            "efficiency_score": round(tb.get_efficiency_score(), 1)
                        }
                    yield AgentEvent(AgentEventType.CONTEXT_STATUS, context_status_data)
                except Exception as e:
                    logger.debug(f"[agent] Context status event failed: {e}")

                # Summarizer bei >75% Auslastung aktivieren
                if context_percent > 75 and len(messages) > 8:
                    old_tokens = context_tokens
                    try:
                        summarized = await self.summarizer.summarize_if_needed(
                            messages,
                            target_tokens=int(model_limit * 0.6)
                        )
                        if summarized and len(summarized) < len(messages):
                            messages = summarized
                            new_tokens = estimate_messages_tokens(messages)
                            state.compaction_count += 1
                            state.last_compaction_savings = old_tokens - new_tokens

                            # COMPACTION Event für UI
                            yield AgentEvent(AgentEventType.COMPACTION, {
                                "old_tokens": old_tokens,
                                "new_tokens": new_tokens,
                                "saved_tokens": old_tokens - new_tokens,
                                "compaction_count": state.compaction_count,
                                "model": current_model
                            })
                            logger.info(f"[agent] Summarizer: {old_tokens} -> {new_tokens} tokens (-{old_tokens - new_tokens})")
                    except Exception as e:
                        logger.warning(f"[agent] Summarizer Fehler: {e}")

                # LLM aufrufen (nicht-streamend für Tool-Calls)
                logger.debug("[agent] Iteration {iteration + 1}: Calling LLM with {len(tool_schemas)} tools")

                # Reasoning-Status Event VOR dem Call senden (zeigt User dass LLM arbeitet)
                # Bei Tool-Phase: tool_reasoning, bei Analysis: analysis_reasoning
                configured_reasoning = settings.llm.tool_reasoning or settings.llm.analysis_reasoning
                if configured_reasoning:
                    yield AgentEvent(AgentEventType.REASONING_STATUS, {
                        "active": True,
                        "level": configured_reasoning,
                        "phase": "processing"
                    })

                response = await self._call_llm_with_tools(
                    messages, tool_schemas, model, is_tool_phase=True
                )
                tool_calls = response.get("tool_calls", [])
                content = response.get("content", "")
                finish_reason = response.get("finish_reason", "")
                native_tools = response.get("native_tools", True)

                logger.debug(
                    "[agent] LLM response: finish_reason=%r, tool_calls=%d, content_len=%d",
                    finish_reason, len(tool_calls), len(content or '')
                )

                # Token-Nutzung akkumulieren
                usage = response.get("usage")
                if usage:
                    request_prompt_tokens += usage.prompt_tokens
                    request_completion_tokens += usage.completion_tokens
                    last_finish_reason = usage.finish_reason
                    last_model = usage.model

                # Fallback: Text-Tool-Call-Parser für Modelle ohne natives Tool-Calling
                # (z.B. Mistral-678B gibt Tool-Calls manchmal als Text zurück)
                if not tool_calls and content:
                    text_tool_calls = _parse_text_tool_calls(content, tool_schemas)
                    if text_tool_calls:
                        logger.debug("[agent] Text-Parser erkannte {len(text_tool_calls)} Tool-Calls im Content")
                        tool_calls = text_tool_calls
                        native_tools = False  # Text-geparst, kein natives tool_calls-Format
                        finish_reason = "tool_calls"
                        # Content bereinigen - Tool-Marker entfernen
                        content = _strip_tool_markers(content)
                        logger.debug("[agent] Content nach Tool-Marker-Bereinigung: %d Zeichen", len(content) if content else 0)

                # Tool-Calls verarbeiten
                if not tool_calls:
                    # Keine weiteren Tool-Calls -> Finale Antwort generieren
                    assistant_response = ""
                    final_usage = None

                    # Entscheiden ob wir streamen oder die vorhandene Antwort nutzen
                    should_stream = settings.llm.streaming
                    # Content bereinigen falls noch Tool-Marker vorhanden (Sicherheit)
                    existing_content = _strip_tool_markers(content) if content else content

                    # === PLANUNGSPHASE: Plan extrahieren ===
                    # In der Planungsphase (vor Genehmigung) kein Streaming –
                    # vollständige Antwort holen und [PLAN]...[/PLAN]-Block extrahieren.
                    if state.mode == AgentMode.PLAN_THEN_EXECUTE and not state.plan_approved:
                        plan_response = existing_content
                        # Nur neue Requests zählen – usage wurde im Loop bereits akkumuliert.
                        # Wenn existing_content leer ist, muss ein neuer LLM-Call gemacht werden.
                        extra_plan_usage: Optional[TokenUsage] = None
                        if not plan_response:
                            plan_resp = await self._call_llm_with_tools(
                                messages, [], None, is_tool_phase=False
                            )
                            plan_response = plan_resp.get("content", "")
                            extra_plan_usage = plan_resp.get("usage")  # Noch nicht gezählt
                            # Reasoning-Status Event senden (falls aktiv)
                            if plan_resp.get("reasoning"):
                                yield AgentEvent(AgentEventType.REASONING_STATUS, {
                                    "active": True,
                                    "level": plan_resp.get("reasoning"),
                                    "phase": "analysis"
                                })

                        # Token-Nutzung für neuen Call akkumulieren (nur wenn neu)
                        if extra_plan_usage and isinstance(extra_plan_usage, TokenUsage):
                            request_prompt_tokens += extra_plan_usage.prompt_tokens
                            request_completion_tokens += extra_plan_usage.completion_tokens
                            last_finish_reason = extra_plan_usage.finish_reason
                            last_model = extra_plan_usage.model

                        state.total_prompt_tokens += request_prompt_tokens
                        state.total_completion_tokens += request_completion_tokens

                        usage_data = {
                            "prompt_tokens": request_prompt_tokens,
                            "completion_tokens": request_completion_tokens,
                            "total_tokens": request_prompt_tokens + request_completion_tokens,
                            "finish_reason": last_finish_reason,
                            "model": last_model,
                            "truncated": last_finish_reason == "length",
                            "max_tokens": settings.llm.max_tokens,
                            "session_total_prompt": state.total_prompt_tokens,
                            "session_total_completion": state.total_completion_tokens,
                            "budget": budget.get_status() if budget else None,
                            "compaction_count": state.compaction_count,
                        }

                        # Token-Tracking für Statistiken
                        try:
                            tracker = get_token_tracker()
                            tracker.log_usage(
                                session_id=session_id,
                                model=last_model or settings.llm.default_model,
                                input_tokens=request_prompt_tokens,
                                output_tokens=request_completion_tokens,
                                request_type="plan",
                            )
                        except Exception as e:
                            logger.debug(f"[agent] Token-Tracking failed: {e}")

                        plan_match = _RE_PLAN_BLOCK.search(plan_response)
                        if plan_match:
                            plan_text = plan_match.group(1).strip()
                            state.pending_plan = plan_text

                            # In Konversations-Historie speichern
                            state.messages_history.append({
                                "role": "assistant",
                                "content": plan_response
                            })
                            try:
                                from app.services.chat_store import save_chat
                                save_chat(
                                    session_id=session_id,
                                    title=state.title or "Chat",
                                    messages_history=state.messages_history,
                                    mode=state.mode.value,
                                )
                            except Exception:
                                pass

                            yield AgentEvent(AgentEventType.PLAN_READY, {
                                "plan": plan_text,
                                "full_response": plan_response,
                            })
                            yield AgentEvent(AgentEventType.USAGE, usage_data)
                            yield AgentEvent(AgentEventType.DONE, {
                                "response": plan_response,
                                "is_plan": True,
                                "tool_calls_count": len(state.tool_calls_history),
                                "usage": usage_data,
                            })
                            return
                        # Kein [PLAN]-Block → als normale Antwort ausgeben (Fallback)
                        if plan_response:
                            yield AgentEvent(AgentEventType.TOKEN, plan_response)
                        state.messages_history.append({
                            "role": "assistant",
                            "content": plan_response
                        })
                        yield AgentEvent(AgentEventType.USAGE, usage_data)
                        yield AgentEvent(AgentEventType.DONE, {
                            "response": plan_response,
                            "tool_calls_count": len(state.tool_calls_history),
                            "usage": usage_data,
                        })
                        return

                    # Wenn ein separates Analyse-Modell konfiguriert ist, muss neu angefragt werden
                    needs_new_request = (has_used_tools and settings.llm.analysis_model and not model)

                    if needs_new_request:
                        # Neue Anfrage mit Analyse-Modell
                        if should_stream:
                            stream_result = await self._stream_final_response_with_usage(messages)
                            async for token in stream_result["tokens"]:
                                assistant_response += token
                                yield AgentEvent(AgentEventType.TOKEN, token)
                            final_usage = stream_result["usage"].get("usage")
                        else:
                            analysis_response = await self._call_llm_with_tools(
                                messages, [], None, is_tool_phase=False
                            )
                            assistant_response = analysis_response.get("content", "")
                            final_usage = analysis_response.get("usage")
                            # Reasoning-Status Event senden (falls aktiv)
                            if analysis_response.get("reasoning"):
                                yield AgentEvent(AgentEventType.REASONING_STATUS, {
                                    "active": True,
                                    "level": analysis_response.get("reasoning"),
                                    "phase": "analysis"
                                })
                            if assistant_response:
                                yield AgentEvent(AgentEventType.TOKEN, assistant_response)
                    elif should_stream and not existing_content:
                        # Keine Antwort vorhanden -> Stream anfordern
                        # Reasoning-Status Event senden (falls aktiv - Streaming nutzt analysis_reasoning)
                        if settings.llm.analysis_reasoning:
                            yield AgentEvent(AgentEventType.REASONING_STATUS, {
                                "active": True,
                                "level": settings.llm.analysis_reasoning,
                                "phase": "analysis"
                            })
                        stream_result = await self._stream_final_response_with_usage(messages, model)
                        async for token in stream_result["tokens"]:
                            assistant_response += token
                            yield AgentEvent(AgentEventType.TOKEN, token)
                        final_usage = stream_result["usage"].get("usage")
                    else:
                        # Vorhandene Antwort nutzen (LLM hat direkt geantwortet ohne Tools)
                        # Bei direkten Antworten: Reasoning-Status senden falls konfiguriert
                        # (Note: Der ursprüngliche Call war mit tool_reasoning, aber wir zeigen analysis_reasoning an)
                        if settings.llm.analysis_reasoning and not has_used_tools:
                            yield AgentEvent(AgentEventType.REASONING_STATUS, {
                                "active": True,
                                "level": settings.llm.analysis_reasoning,
                                "phase": "direct"
                            })
                        assistant_response = existing_content
                        final_usage = usage
                        if assistant_response:
                            yield AgentEvent(AgentEventType.TOKEN, assistant_response)

                    # Finale Token-Nutzung akkumulieren
                    if final_usage and isinstance(final_usage, TokenUsage):
                        request_prompt_tokens += final_usage.prompt_tokens
                        request_completion_tokens += final_usage.completion_tokens
                        last_finish_reason = final_usage.finish_reason
                        last_model = final_usage.model

                    # Gesamte Token-Nutzung aktualisieren
                    state.total_prompt_tokens += request_prompt_tokens
                    state.total_completion_tokens += request_completion_tokens

                    # Assistant-Antwort in Historie speichern
                    if assistant_response:
                        state.messages_history.append({
                            "role": "assistant",
                            "content": assistant_response
                        })
                        # Chat auf Disk persistieren
                        try:
                            from app.services.chat_store import save_chat
                            save_chat(
                                session_id=session_id,
                                title=state.title or "Chat",
                                messages_history=state.messages_history,
                                mode=state.mode.value,
                            )
                        except Exception:
                            pass

                    # Token-Nutzung als Event senden
                    usage_data = {
                        "prompt_tokens": request_prompt_tokens,
                        "completion_tokens": request_completion_tokens,
                        "total_tokens": request_prompt_tokens + request_completion_tokens,
                        "finish_reason": last_finish_reason,
                        "model": last_model,
                        "truncated": last_finish_reason == "length",
                        "max_tokens": settings.llm.max_tokens,
                        # Session-Gesamtwerte
                        "session_total_prompt": state.total_prompt_tokens,
                        "session_total_completion": state.total_completion_tokens,
                        # Budget-Status für Context-Management
                        "budget": budget.get_status() if budget else None,
                        "compaction_count": state.compaction_count
                    }

                    # Token-Tracking für Statistiken
                    try:
                        tracker = get_token_tracker()
                        tracker.log_usage(
                            session_id=session_id,
                            model=last_model or settings.llm.default_model,
                            input_tokens=request_prompt_tokens,
                            output_tokens=request_completion_tokens,
                            request_type="chat",
                        )
                    except Exception as e:
                        logger.debug(f"[agent] Token-Tracking failed: {e}")

                    yield AgentEvent(AgentEventType.USAGE, usage_data)

                    # Analytics: Chain beenden
                    if self._analytics.enabled:
                        try:
                            await self._analytics.end_chain(
                                status="resolved",
                                response=assistant_response[:500] if assistant_response else ""
                            )
                        except Exception:
                            pass

                    # TaskTracker: Anfrage-Task abschließen
                    try:
                        await task_tracker.complete_step(processing_task_id, 0)
                        await task_tracker.complete_task(processing_task_id)
                    except Exception:
                        pass  # Task-Tracking-Fehler nicht propagieren

                    # ── Pending PR-Analyse abschließen (vor DONE) ──
                    if state.pending_pr_analysis is not None:
                        try:
                            # Warte max 5 Sekunden auf Abschluss
                            pr_result = await asyncio.wait_for(
                                state.pending_pr_analysis, timeout=5.0
                            )
                            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                                "prNumber": state.pending_pr_number,
                                **pr_result
                            })
                        except asyncio.TimeoutError:
                            logger.debug("[agent] PR analysis timeout, sending fallback")
                            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                                "prNumber": state.pending_pr_number,
                                "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                                "verdict": "comment",
                                "findings": [],
                                "summary": "Analyse-Timeout",
                                "canApprove": state.pending_pr_state == "open"
                            })
                        except Exception as e:
                            logger.warning(f"[agent] PR analysis error: {e}")
                            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                                "prNumber": state.pending_pr_number,
                                "error": str(e),
                                "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                                "verdict": "comment",
                                "findings": [],
                                "canApprove": state.pending_pr_state == "open"
                            })
                        finally:
                            state.pending_pr_analysis = None

                    yield AgentEvent(AgentEventType.DONE, {
                        "response": assistant_response,
                        "tool_calls_count": len(state.tool_calls_history),
                        "usage": usage_data
                    })
                    return

                # Tools ausführen
                current_tool_calls_for_messages = []

                # ══════════════════════════════════════════════════════════════
                # PARALLELISIERUNG: Prüfe ob Tools parallel ausgeführt werden können
                # ══════════════════════════════════════════════════════════════
                tools_to_process = tool_calls[:self.max_tool_calls_per_iter]

                # Parse alle Tool-Calls vorab
                parsed_tool_calls: List[ToolCall] = []
                for tc in tools_to_process:
                    raw_args = tc["function"]["arguments"]
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            parsed_args = {}
                    else:
                        parsed_args = raw_args

                    parsed_tool_calls.append(ToolCall(
                        id=tc.get("id", f"call_{len(state.tool_calls_history)}"),
                        name=tc["function"]["name"],
                        arguments=parsed_args
                    ))

                # Prüfe ob parallele Ausführung möglich (alle Tools parallelisierbar)
                all_parallelizable = (
                    len(parsed_tool_calls) >= 2 and
                    all(_is_parallelizable_tool(tc.name) for tc in parsed_tool_calls)
                )

                if all_parallelizable:
                    # ══════════════════════════════════════════════════════════
                    # FAST PATH: Parallele Ausführung aller Tools
                    # ══════════════════════════════════════════════════════════
                    logger.info(f"[agent] Parallel execution: {len(parsed_tool_calls)} tools")

                    # TOOL_START Events für alle Tools emittieren
                    for tc in parsed_tool_calls:
                        is_pr_workspace_tool = tc.name in ("github_pr_details", "github_pr_diff")
                        yield AgentEvent(AgentEventType.TOOL_START, {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                            "model": settings.llm.tool_model or settings.llm.default_model,
                            "workspaceOnly": is_pr_workspace_tool,
                            "parallel": True  # Markiert als parallel
                        })

                    # Parallele Ausführung
                    parallel_results = await self._execute_tools_parallel_batch(parsed_tool_calls, state)

                    # Ergebnisse verarbeiten
                    for i, (tc, result) in enumerate(zip(parsed_tool_calls, parallel_results)):
                        tc.result = result
                        has_used_tools = True
                        current_tool_calls_for_messages.append(tools_to_process[i])

                        # TOOL_RESULT Event
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tc.id,
                            "name": tc.name,
                            "success": result.success,
                            "data": result.to_context()[:500] if result.success else (result.error or "")[:500],
                        })

                        # Entity Tracker
                        if result.success:
                            _source_path = tc.arguments.get("path", "") or tc.arguments.get("query", "")
                            state.entity_tracker.extract_from_tool_result(
                                tool_name=tc.name,
                                result_text=result.to_context(),
                                source_path=_source_path
                            )

                        # Stuck Detection
                        stuck_result = progress_tracker.record_call(
                            tool_name=tc.name,
                            args=tc.arguments,
                            result=result,
                            iteration=iteration
                        )
                        if stuck_result.is_stuck:
                            yield AgentEvent(AgentEventType.STUCK_DETECTED, {
                                "reason": stuck_result.reason.value if stuck_result.reason else "unknown",
                                "details": stuck_result.details,
                                "suggestion": stuck_result.suggestion,
                            })
                            messages.append({"role": "system", "content": stuck_result.get_hint()})

                        state.tool_calls_history.append(tc)

                    # Messages für nächste Iteration aufbauen (natives Format)
                    if native_tools:
                        messages.append({
                            "role": "assistant",
                            "content": content if content else None,
                            "tool_calls": current_tool_calls_for_messages
                        })
                        for tc in parsed_tool_calls:
                            if tc.result:
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": _truncate_result(tc.result.to_context(), tool_name=tc.name)
                                })
                    else:
                        messages.append({"role": "assistant", "content": content or ""})
                        results_parts = [
                            f"### Tool-Ergebnis: {tc.name}\n{_truncate_result(tc.result.to_context(), tool_name=tc.name)}"
                            for tc in parsed_tool_calls if tc.result
                        ]
                        if results_parts:
                            messages.append({
                                "role": "user",
                                "content": "Tool-Ergebnisse:\n\n" + "\n\n---\n\n".join(results_parts)
                            })

                    # Skip zum nächsten Iteration-Loop (keine sequentielle Verarbeitung nötig)
                    continue

                # ══════════════════════════════════════════════════════════════
                # STANDARD PATH: Sequentielle Ausführung (für Write-Tools, MCP, etc.)
                # ══════════════════════════════════════════════════════════════
                for idx, tc in enumerate(tools_to_process):
                    tool_call = parsed_tool_calls[idx]

                    # Loop-Prävention: read_file max 2x pro Datei erlauben
                    if tool_call.name == "read_file":
                        file_path = tool_call.arguments.get("path", "")
                        read_count = state.read_files_this_request.get(file_path, 0)
                        if read_count >= 2:
                            logger.debug("[agent] Loop-Prävention: {file_path} wurde bereits {read_count}x gelesen, überspringe")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": f"[HINWEIS] Die Datei '{file_path}' wurde bereits {read_count}x gelesen. Bitte nutze den bereits erhaltenen Inhalt aus dem Kontext weiter oder verwende search_code für gezielte Suchen."
                            })
                            current_tool_calls_for_messages.append(tc)
                            continue
                        state.read_files_this_request[file_path] = read_count + 1

                    # Loop-Prävention: edit_file max 2x pro Datei erlauben
                    if tool_call.name == "edit_file":
                        file_path = tool_call.arguments.get("path", "")
                        edit_count = state.edit_files_this_request.get(file_path, 0)
                        if edit_count >= 2:
                            logger.debug(f"[agent] Loop-Prävention: {file_path} wurde bereits {edit_count}x bearbeitet")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": f"[STOP] Die Datei '{file_path}' wurde bereits {edit_count}x bearbeitet. "
                                           "Die Aufgabe scheint abgeschlossen zu sein. "
                                           "Bitte fasse zusammen was du geändert hast und warte auf weitere Anweisungen vom User."
                            })
                            current_tool_calls_for_messages.append(tc)
                            continue
                        state.edit_files_this_request[file_path] = edit_count + 1

                    # Loop-Prävention: write_file max 1x pro Datei (außer mit Bestätigung)
                    if tool_call.name == "write_file":
                        file_path = tool_call.arguments.get("path", "")
                        write_count = state.write_files_this_request.get(file_path, 0)
                        if write_count >= 1:
                            logger.debug(f"[agent] Loop-Prävention: {file_path} wurde bereits geschrieben")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": f"[STOP] Die Datei '{file_path}' wurde bereits geschrieben. "
                                           "Weitere Schreibvorgänge sind nicht erlaubt ohne explizite User-Anweisung. "
                                           "Bitte fasse zusammen was du gemacht hast."
                            })
                            current_tool_calls_for_messages.append(tc)
                            continue
                        state.write_files_this_request[file_path] = write_count + 1

                    # Pro-Tool Modell ermitteln (Priorität: pro-Tool > tool_model > default)
                    tool_specific_model = settings.llm.tool_models.get(tool_call.name, "")
                    effective_model = tool_specific_model or settings.llm.tool_model or settings.llm.default_model

                    # PR-Tools: Card im Chat unterdrücken (gehen in Workspace)
                    is_pr_workspace_tool = tool_call.name in ("github_pr_details", "github_pr_diff")

                    yield AgentEvent(AgentEventType.TOOL_START, {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "model": effective_model,
                        "workspaceOnly": is_pr_workspace_tool  # Card im Chat unterdrücken
                    })

                    # TaskTracker: Tool-Ausführung als Artifact hinzufügen
                    try:
                        await task_tracker.add_artifact(
                            processing_task_id,
                            TaskArtifact(
                                id=tool_call.id,
                                type="tool_start",
                                summary=f"Tool: {tool_call.name}",
                                data={"name": tool_call.name, "arguments": tool_call.arguments}
                            )
                        )
                    except Exception:
                        pass  # Task-Tracking-Fehler nicht propagieren

                    # ── suggest_answers: Display-only Tool im Debug-Modus ────
                    if tool_call.name == "suggest_answers":
                        question = tool_call.arguments.get("question", "")
                        options = tool_call.arguments.get("options", [])
                        yield AgentEvent(AgentEventType.QUESTION, {
                            "question": question,
                            "options": options,
                        })
                        result = ToolResult(
                            success=True,
                            data={"status": "options_presented_to_user", "count": len(options)}
                        )
                        tool_call.result = result
                        has_used_tools = True
                        current_tool_calls_for_messages.append(tc)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result.to_context()
                        })
                        state.tool_calls_history.append(tool_call)
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "result": result.to_context()[:500],
                            "success": True,
                        })
                        continue  # Nächsten Tool-Call verarbeiten
                    # ────────────────────────────────────────────────────────

                    # Tool ausführen - MCP-Tools speziell behandeln
                    # Capabilities: brainstorm, design, implement, analyze, capability_handoff
                    MCP_CAPABILITY_TOOLS = {
                        "sequential_thinking", "seq_think",
                        "brainstorm", "design", "implement", "analyze",
                        "capability_handoff"
                    }
                    if tool_call.name.startswith("mcp_") or tool_call.name in MCP_CAPABILITY_TOOLS:
                        # MCP-Tool über Bridge ausführen mit Live-Event-Streaming
                        if self._mcp_bridge is None:
                            self._mcp_bridge = get_tool_bridge(
                        llm_callback=self._llm_callback_for_mcp,
                        event_callback=self._emit_mcp_event
                    )

                        # Persistente Subscription BEVOR Tool startet
                        mcp_queue = self._event_bridge.subscribe()

                        try:
                            # Tool in separatem Task ausführen
                            tool_task = asyncio.create_task(
                                self._mcp_bridge.call_tool(tool_call.name, tool_call.arguments)
                            )

                            # Events live streamen während Tool läuft
                            while not tool_task.done():
                                # Events pollen und yielden
                                async for mcp_event in self._drain_mcp_events_from_queue(mcp_queue):
                                    yield mcp_event
                                # Kurz warten bevor nächster Poll
                                await asyncio.sleep(0.05)

                            # Tool-Result abholen
                            mcp_result = await tool_task

                            # Finale Events (falls noch welche da sind)
                            async for mcp_event in self._drain_mcp_events_from_queue(mcp_queue, timeout=0.1):
                                yield mcp_event
                        finally:
                            # Subscription aufräumen
                            self._event_bridge.unsubscribe(mcp_queue)

                        result = ToolResult(
                            success=mcp_result.get("success", False),
                            data=mcp_result.get("result") or mcp_result.get("formatted_output") or mcp_result,
                            error=mcp_result.get("error")
                        )
                    else:
                        # Standard-Tool über ToolRegistry ausführen (mit Caching)
                        # Prüfe zuerst den Cache
                        cached_result = self._tool_cache.get(
                            tool_call.name,
                            tool_call.arguments
                        )
                        if cached_result is not None:
                            result = cached_result
                            logger.debug(f"[agent] Cache HIT: {tool_call.name}")
                            # Budget-Tracking: Cache-Hit
                            if state.tool_budget:
                                state.tool_budget.record_tool_call(
                                    tool_call.name, duration_ms=0, cached=True
                                )
                        else:
                            # Nicht im Cache - Tool ausführen
                            import time as _time
                            _start = _time.time()
                            result = await self.tools.execute(
                                tool_call.name,
                                **tool_call.arguments
                            )
                            _duration_ms = int((_time.time() - _start) * 1000)
                            # Erfolgreiches Ergebnis cachen
                            self._tool_cache.set(
                                tool_call.name,
                                tool_call.arguments,
                                result
                            )
                            # Budget-Tracking: Tool-Aufruf
                            if state.tool_budget:
                                state.tool_budget.record_tool_call(
                                    tool_call.name, duration_ms=_duration_ms, cached=False
                                )
                            # Analytics: Tool-Ausführung loggen
                            if self._analytics.enabled:
                                try:
                                    await self._analytics.log_tool_execution(
                                        tool_name=tool_call.name,
                                        success=result.success,
                                        duration_ms=_duration_ms,
                                        error=result.error,
                                        result_size=len(str(result.data or "")),
                                    )
                                except Exception:
                                    pass  # Analytics-Fehler nicht kritisch
                    tool_call.result = result
                    has_used_tools = True
                    current_tool_calls_for_messages.append(tc)

                    # Entity Tracker: Entitäten aus Tool-Result extrahieren
                    if result.success:
                        _source_path = (
                            tool_call.arguments.get("path", "")
                            or tool_call.arguments.get("query", "")
                        )
                        state.entity_tracker.extract_from_tool_result(
                            tool_name=tool_call.name,
                            result_text=result.to_context(),
                            source_path=_source_path
                        )

                    # ── Stuck-Detection: Prüfen ob Agent sich im Kreis dreht ──
                    stuck_result = progress_tracker.record_call(
                        tool_name=tool_call.name,
                        args=tool_call.arguments,
                        result=result,
                        iteration=iteration
                    )
                    if stuck_result.is_stuck:
                        # Event für Frontend
                        yield AgentEvent(AgentEventType.STUCK_DETECTED, {
                            "reason": stuck_result.reason.value if stuck_result.reason else "unknown",
                            "details": stuck_result.details,
                            "suggestion": stuck_result.suggestion,
                            "repeated_count": stuck_result.repeated_count,
                            "progress": progress_tracker.get_progress_summary()
                        })
                        # Hinweis ins LLM injizieren
                        messages.append({
                            "role": "system",
                            "content": stuck_result.get_hint()
                        })
                        logger.warning(
                            f"[agent] Stuck detected: {stuck_result.reason.value} - "
                            f"{stuck_result.details}"
                        )
                    # ────────────────────────────────────────────────────────────

                    # ── Result-Validierung: Relevanz prüfen und Source-Metadata ──
                    if result.success and tool_call.name not in ("write_file", "execute_command"):
                        try:
                            result_validator = get_result_validator()
                            validation = await result_validator.validate(
                                tool_name=tool_call.name,
                                query=user_message,
                                result=result,
                                create_summary=(estimate_tokens(result.to_context()) > 2000)
                            )
                            # Low-Relevanz Warnung ins Log
                            if not validation.should_use:
                                logger.debug(
                                    f"[agent] Low relevance result ({validation.relevance_score:.2f}): "
                                    f"{tool_call.name} - {validation.reason}"
                                )
                            # Source-Metadata für Attribution speichern
                            if validation.source_metadata:
                                state.entity_tracker.track_source(
                                    validation.source_metadata.source_type,
                                    validation.source_metadata.source_id,
                                    validation.source_metadata.source_title
                                )
                        except Exception as e:
                            logger.debug(f"[agent] Result validation failed: {e}")
                    # ────────────────────────────────────────────────────────────

                    # Bestätigung benötigt?
                    if result.requires_confirmation and state.mode == AgentMode.WRITE_WITH_CONFIRM:
                        state.pending_confirmation = tool_call

                        # Tool-Card auf "Wartet auf Bestätigung" setzen
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "success": True,
                            "data": f"⏳ Wartet auf Bestätigung: {result.confirmation_data.get('path', '')}"
                        })

                        yield AgentEvent(AgentEventType.CONFIRM_REQUIRED, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "confirmation_data": result.confirmation_data
                        })

                        # Warten auf Bestätigung
                        confirmed = yield
                        tool_call.confirmed = confirmed

                        if confirmed:
                            # Operation ausführen
                            exec_result = await self._execute_confirmed_operation(
                                result.confirmation_data
                            )
                            if exec_result.success:
                                # Spezifische Meldung je nach Operation
                                operation = result.confirmation_data.get("operation", "")
                                if operation == "batch_write_files":
                                    # Batch: Liste alle geschriebenen Dateien
                                    files = result.confirmation_data.get("files", [])
                                    file_paths = [f.get("path", "") for f in files]
                                    confirm_msg = f"✓ {len(files)} Dateien geschrieben"
                                    result_msg = exec_result.data  # Enthält bereits die Details
                                    context_msg = f"[{tool_call.name}] {len(files)} Dateien erstellt: {', '.join(file_paths[:5])}" + ("..." if len(file_paths) > 5 else "")
                                else:
                                    # Einzelne Datei
                                    path = result.confirmation_data.get('path', '')
                                    confirm_msg = f"✓ Datei geschrieben: {path}"
                                    result_msg = f"Datei erfolgreich geschrieben: {path}"
                                    context_msg = f"[{tool_call.name}] Ausgeführt: {path}"

                                yield AgentEvent(AgentEventType.CONFIRMED, {
                                    "id": tool_call.id,
                                    "message": confirm_msg
                                })
                                # Tool-Result aktualisieren auf Erfolg
                                result = ToolResult(
                                    success=True,
                                    data=result_msg
                                )
                                tool_call.result = result
                                state.context_items.append(context_msg)
                                # WICHTIG: Finales TOOL_RESULT senden um UI zu aktualisieren
                                yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                    "id": tool_call.id,
                                    "name": tool_call.name,
                                    "success": True,
                                    "data": confirm_msg
                                })
                            else:
                                yield AgentEvent(AgentEventType.ERROR, {
                                    "id": tool_call.id,
                                    "error": exec_result.error
                                })
                                # Tool-Result aktualisieren auf Fehler
                                result = ToolResult(
                                    success=False,
                                    error=exec_result.error
                                )
                                tool_call.result = result
                                # Finales TOOL_RESULT für UI
                                yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                    "id": tool_call.id,
                                    "name": tool_call.name,
                                    "success": False,
                                    "data": f"❌ Fehler: {exec_result.error}"
                                })
                        else:
                            yield AgentEvent(AgentEventType.CANCELLED, {
                                "id": tool_call.id,
                                "message": "Operation abgebrochen"
                            })
                            # Bei Ablehnung: Tool als abgebrochen markieren
                            result = ToolResult(
                                success=False,
                                data="Operation vom Benutzer abgebrochen"
                            )
                            tool_call.result = result
                            # Finales TOOL_RESULT für UI
                            yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "success": False,
                                "data": "⚠️ Operation abgebrochen"
                            })

                        state.pending_confirmation = None

                    else:
                        # Normales Tool-Ergebnis
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "success": result.success,
                            "data": result.to_context()[:2000]  # Truncate für Frontend
                        })

                        # Workspace Events für spezifische Tools
                        if result.success:
                            if tool_call.name == "read_file":
                                # Datei-Inhalt für Workspace Panel
                                file_path = tool_call.arguments.get("path", "")
                                yield AgentEvent(AgentEventType.WORKSPACE_FILE, {
                                    "filePath": file_path,
                                    "path": file_path,  # Fallback für Frontend
                                    "operation": "read"
                                })
                            elif tool_call.name in ("write_file", "edit_file", "create_file"):
                                # Code-Änderung für Workspace Panel
                                file_path = tool_call.arguments.get("path", "")
                                content = tool_call.arguments.get("content", "")
                                yield AgentEvent(AgentEventType.WORKSPACE_CODE_CHANGE, {
                                    "filePath": file_path,
                                    "modifiedContent": content[:5000] if content else result.to_context()[:2000],
                                    "toolCall": tool_call.name,
                                    "description": f"{tool_call.name}: {file_path}",
                                    "status": "applied",
                                    "isNew": tool_call.name == "create_file"
                                })
                            elif tool_call.name in ("github_pr_details", "github_pr_diff"):
                                # PR-Daten für Workspace Panel
                                pr_number = tool_call.arguments.get("pr_number")
                                repo = tool_call.arguments.get("repo", "")
                                # Parse result data
                                result_data = result.data if hasattr(result, 'data') and isinstance(result.data, dict) else {}

                                # Author kann String sein (von github_pr_details) oder Dict (von GitHub API direkt)
                                author = result_data.get("user", "")
                                if isinstance(author, dict):
                                    author = author.get("login", "")

                                # Author Name (vollständiger Name, falls verfügbar)
                                author_name = result_data.get("user_name", "") or author

                                # Zusätzliche PR-Metadaten
                                commits_count = result_data.get("commits", 0)
                                merged_by = result_data.get("merged_by", "")
                                merged_at = result_data.get("merged_at", "")

                                # Branches: Tool gibt "head_branch"/"base_branch" zurück (String)
                                # Fallback auf nested dict falls direkte API-Response
                                head_raw = result_data.get("head")
                                base_raw = result_data.get("base")
                                head_branch = (
                                    result_data.get("head_branch") or
                                    (head_raw.get("ref", "") if isinstance(head_raw, dict) else "") or
                                    "feature"
                                )
                                base_branch = (
                                    result_data.get("base_branch") or
                                    (base_raw.get("ref", "") if isinstance(base_raw, dict) else "") or
                                    "main"
                                )

                                # PR-Status bestimmen (open, closed, merged)
                                # merged=True oder merged_at gesetzt = gemerged
                                pr_state = result_data.get("state", "open")
                                is_merged = result_data.get("merged") is True or result_data.get("merged_at") is not None

                                # Debug-Logging für PR-Daten
                                logger.info(f"[agent] Emitting WORKSPACE_PR event: PR #{pr_number}, "
                                            f"title={result_data.get('title')}, author={author}, "
                                            f"head={head_branch}, base={base_branch}, "
                                            f"additions={result_data.get('additions')}, "
                                            f"deletions={result_data.get('deletions')}, "
                                            f"files={result_data.get('changed_files')}, "
                                            f"state={pr_state}, merged={is_merged}")

                                # Sende zuerst PR-Basisdaten mit loading=true
                                repo_owner = repo.split("/")[0] if "/" in repo else ""
                                repo_name = repo.split("/")[1] if "/" in repo else repo

                                yield AgentEvent(AgentEventType.WORKSPACE_PR, {
                                    "prNumber": pr_number,
                                    "repoOwner": repo_owner,
                                    "repoName": repo_name,
                                    "title": result_data.get("title", f"PR #{pr_number}"),
                                    "author": author or "unknown",
                                    "authorName": author_name or author or "unknown",  # NEU
                                    "baseBranch": base_branch or "main",
                                    "headBranch": head_branch or "feature",
                                    "additions": result_data.get("additions", 0),
                                    "deletions": result_data.get("deletions", 0),
                                    "filesChanged": result_data.get("changed_files", 0),
                                    "commits": commits_count,  # NEU
                                    "state": "merged" if is_merged else pr_state,
                                    "mergedAt": merged_at,  # NEU
                                    "mergedBy": merged_by,  # NEU
                                    "diff": result_data.get("diff", "")[:10000] if tool_call.name == "github_pr_diff" else "",
                                    "toolCall": tool_call.name,
                                    "loading": True  # Analyse läuft noch
                                })

                                # Kurzer Hinweis für Chat: "PR im Workspace geöffnet"
                                yield AgentEvent(AgentEventType.PR_OPENED_HINT, {
                                    "prNumber": pr_number,
                                    "repoOwner": repo_owner,
                                    "repoName": repo_name,
                                    "title": result_data.get("title", f"PR #{pr_number}"),
                                })

                                # PR-Analyse starten (läuft als Background-Task, Event wird
                                # über pending_pr_analysis im State gespeichert und am Ende
                                # jeder Iteration abgefragt)
                                diff_content = result_data.get("diff", "")
                                if diff_content and len(diff_content) > 50:
                                    state.pending_pr_analysis = asyncio.create_task(
                                        self._analyze_pr_for_workspace(
                                            pr_number=pr_number,
                                            title=result_data.get("title", ""),
                                            diff=diff_content[:15000],
                                            state="merged" if is_merged else pr_state
                                        )
                                    )
                                    state.pending_pr_number = pr_number
                                    state.pending_pr_state = "merged" if is_merged else pr_state
                                else:
                                    # Kein Diff vorhanden (z.B. bei github_pr_details ohne Diff)
                                    # → Sofort leeres Analyse-Event senden um Loading zu beenden
                                    logger.debug(f"[agent] No diff for PR #{pr_number}, sending empty analysis")
                                    yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                                        "prNumber": pr_number,
                                        "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                                        "verdict": "comment",
                                        "findings": [],
                                        "summary": "Kein Diff verfügbar - nutze github_pr_diff für Analyse",
                                        "canApprove": pr_state == "open"
                                    })

                    state.tool_calls_history.append(tool_call)

                # Messages für nächste Iteration aktualisieren
                def _truncate_result(raw: str, max_chars: int = 20000, tool_name: str = "") -> str:
                    # PR-Tools: Minimale Info für Haupt-LLM (Analyse läuft im Workspace)
                    if tool_name in ("github_pr_details", "github_pr_diff"):
                        # Extrahiere nur Metadaten, kein Diff
                        lines = raw.split("\n")[:15]  # Erste 15 Zeilen (Metadaten)
                        summary = "\n".join(lines)
                        return summary + "\n\n[INFO: PR-Diff wird im Workspace-Panel analysiert. Keine Chat-Analyse nötig.]"
                    if len(raw) > max_chars:
                        return raw[:max_chars] + f"\n\n[HINWEIS: Inhalt bei {max_chars} Zeichen abgeschnitten. Gesamtlänge: {len(raw)} Zeichen. Nutze read_file mit offset-Parameter für weitere Abschnitte.]"
                    return raw

                if native_tools:
                    # OpenAI-kompatibles Format: role="assistant" mit tool_calls + role="tool" Ergebnisse
                    # content muss None (nicht "") sein wenn tool_calls vorhanden - Mistral/OpenAI-Anforderung
                    messages.append({
                        "role": "assistant",
                        "content": content if content else None,
                        "tool_calls": current_tool_calls_for_messages
                    })
                    for tc in current_tool_calls_for_messages:
                        tc_id = tc.get("id")
                        tool_call_obj = next(
                            (t for t in state.tool_calls_history if t.id == tc_id),
                            None
                        )
                        if tool_call_obj and tool_call_obj.result:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": _truncate_result(tool_call_obj.result.to_context(), tool_name=tool_call_obj.name)
                            })
                else:
                    # Text-basiertes Format (Mistral-Compact, Qwen etc.):
                    # Kein tool_calls-Feld im assistant-Message, Ergebnisse als user-Message
                    messages.append({
                        "role": "assistant",
                        "content": content or ""
                    })
                    results_parts = []
                    for tc in current_tool_calls_for_messages:
                        tc_id = tc.get("id")
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        tool_call_obj = next(
                            (t for t in state.tool_calls_history if t.id == tc_id),
                            None
                        )
                        if tool_call_obj and tool_call_obj.result:
                            result_text = _truncate_result(tool_call_obj.result.to_context(), tool_name=tool_name)
                            results_parts.append(f"### Tool-Ergebnis: {tool_name}\n{result_text}")
                    if results_parts:
                        messages.append({
                            "role": "user",
                            "content": "Tool-Ergebnisse:\n\n" + "\n\n---\n\n".join(results_parts)
                        })

                # Proaktive Cross-Source-Anreicherung:
                # Wenn search_code eine Java-Klasse findet, automatisch search_handbook aufrufen
                # und umgekehrt — nur wenn die Quelle noch nicht bekannt ist.
                if current_tool_calls_for_messages:
                    last_tc = state.tool_calls_history[-1] if state.tool_calls_history else None
                    if last_tc and last_tc.result and last_tc.result.success:
                        enrichments = await self._enrich_from_entity_tracker(
                            state, last_tc.name, last_tc.result.to_context()
                        )
                        if enrichments:
                            messages.append({
                                "role": "system",
                                "content": "=== AUTO-QUERVERWEISE ===\n" + "\n\n".join(enrichments)
                            })

            except Exception as e:
                # TaskTracker: Task als fehlgeschlagen markieren
                try:
                    await task_tracker.fail_task(processing_task_id, str(e))
                except Exception:
                    pass
                # Analytics: Chain bei Fehler beenden
                if self._analytics.enabled:
                    try:
                        await self._analytics.end_chain(status="failed", response=str(e))
                    except Exception:
                        pass
                yield AgentEvent(AgentEventType.ERROR, {"error": str(e)})
                return

        # Max iterations erreicht
        # TaskTracker: Task abschließen (Max-Iterations)
        try:
            await task_tracker.complete_step(processing_task_id, 0)
            await task_tracker.complete_task(processing_task_id)
        except Exception:
            pass
        # Analytics: Chain bei Timeout beenden
        if self._analytics.enabled:
            try:
                await self._analytics.end_chain(status="timeout", response="Max iterations")
            except Exception:
                pass

        # Token-Nutzung auch bei max iterations senden (wichtig für korrekte Anzeige)
        usage_data = {
            "prompt_tokens": state.total_prompt_tokens,
            "completion_tokens": state.total_completion_tokens,
            "total_tokens": state.total_prompt_tokens + state.total_completion_tokens,
            "finish_reason": "max_iterations",
            "model": model or settings.llm.default_model,
            "truncated": True,
            "max_tokens": settings.llm.max_tokens,
            # Session-Gesamtwerte
            "session_total_prompt": state.total_prompt_tokens,
            "session_total_completion": state.total_completion_tokens,
            # Budget-Status
            "budget": budget.get_status() if budget else None,
            "compaction_count": state.compaction_count
        }

        # Token-Tracking für Statistiken
        try:
            tracker = get_token_tracker()
            tracker.log_usage(
                session_id=session_id,
                model=model or settings.llm.default_model,
                input_tokens=state.total_prompt_tokens,
                output_tokens=state.total_completion_tokens,
                request_type="max_iterations",
            )
        except Exception:
            pass

        yield AgentEvent(AgentEventType.USAGE, usage_data)

        # Status-Nachricht senden während LLM-Summary generiert wird
        yield AgentEvent(AgentEventType.TOKEN, f"⚠️ **Maximale Iterationen erreicht** ({self.max_iterations} Iterationen)\n\n")
        yield AgentEvent(AgentEventType.TOKEN, "🔄 *Generiere Zusammenfassung...*\n\n")

        # LLM-basierte Zusammenfassung für Folge-Aufruf generieren
        llm_summary = None
        try:
            llm_summary = await self.summarizer.create_max_iterations_summary(
                user_query=user_message,
                tool_calls_history=state.tool_calls_history,
                messages_history=state.messages_history,
                iterations=self.max_iterations
            )
        except Exception as e:
            logger.warning(f"[agent] Max-iterations summary failed: {e}")

        if llm_summary:
            # LLM-Zusammenfassung streamen
            summary_response = llm_summary
            yield AgentEvent(AgentEventType.TOKEN, summary_response)
        else:
            # Fallback: Statische Zusammenfassung
            summary_parts = []

            # Tool-Aufrufe zusammenfassen
            if state.tool_calls_history:
                tool_counts = {}
                for tc in state.tool_calls_history:
                    tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1

                summary_parts.append("**Ausgeführte Tools:**")
                for tool_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                    summary_parts.append(f"- {tool_name}: {count}x")
                summary_parts.append("")

            # Letzte Aktivität aus Messages extrahieren
            recent_activity = []
            for msg in reversed(state.messages_history[-6:]):
                if msg.get("role") == "assistant" and msg.get("content"):
                    content = msg["content"]
                    preview = content[:200].split("\n")[0]
                    if preview:
                        recent_activity.append(preview)
                        break

            if recent_activity:
                summary_parts.append("**Letzter Stand:**")
                summary_parts.append(recent_activity[0])
                if len(recent_activity[0]) >= 200:
                    summary_parts.append("...")
                summary_parts.append("")

            summary_parts.append("**Für Folgeaufruf:** Die bisherigen Ergebnisse sind im Chat-Kontext erhalten. Formuliere eine spezifischere Anfrage um fortzufahren.")

            summary_response = "\n".join(summary_parts)
            yield AgentEvent(AgentEventType.TOKEN, summary_response)

        yield AgentEvent(AgentEventType.DONE, {
            "response": summary_response,
            "tool_calls_count": len(state.tool_calls_history),
            "max_iterations_reached": True
        })

    async def _analyze_pr_for_workspace(
        self,
        pr_number: int,
        title: str,
        diff: str,
        state: str
    ) -> Dict[str, Any]:
        """
        Analysiert einen PR für das Workspace-Panel.

        Macht einen separaten LLM-Call mit strukturiertem Output-Format
        für die Anzeige im PR-Panel (Severity-Badges, Findings, Verdict).

        Returns:
            Dict mit bySeverity, verdict, findings, canApprove
        """
        prompt = f"""Analysiere diesen Pull Request und gib eine strukturierte Bewertung.

PR #{pr_number}: {title}
Status: {state}

DIFF:
```
{diff[:12000]}
```

Antworte NUR mit einem JSON-Objekt in diesem Format (keine Erklärungen):
{{
  "bySeverity": {{
    "critical": <Anzahl kritischer Issues>,
    "high": <Anzahl hoher Issues>,
    "medium": <Anzahl mittlerer Issues>,
    "low": <Anzahl niedriger Issues>,
    "info": <Anzahl Info-Hinweise>
  }},
  "verdict": "<approve|request_changes|comment>",
  "findings": [
    {{
      "severity": "<critical|high|medium|low|info>",
      "title": "<Kurztitel>",
      "file": "<Dateipfad>",
      "line": <Zeilennummer oder null>,
      "description": "<Kurze Beschreibung>"
    }}
  ],
  "summary": "<1-2 Sätze Zusammenfassung>"
}}

Bewertungskriterien:
- critical: Sicherheitslücken, Datenverlust-Risiko
- high: Bugs, Breaking Changes, Performance-Probleme
- medium: Code-Qualität, fehlende Tests, schlechte Patterns
- low: Style-Issues, Minor Improvements
- info: Dokumentation, Kommentare

Maximal 10 Findings. Bei closed/merged PRs: verdict="comment"."""

        try:
            # Schnelles Modell für Analyse
            model = settings.llm.tool_model or settings.llm.default_model
            base_url = settings.llm.base_url.rstrip("/")

            headers = {"Content-Type": "application/json"}
            if settings.llm.api_key and settings.llm.api_key != "none":
                headers["Authorization"] = f"Bearer {settings.llm.api_key}"

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 1500,
                "stream": False
            }

            async with httpx.AsyncClient(
                timeout=30,
                verify=settings.llm.verify_ssl
            ) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()

                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

                # JSON aus Response extrahieren
                import re
                json_match = re.search(r'\{[\s\S]*\}', content)
                if json_match:
                    result = json.loads(json_match.group())

                    # Validierung und Defaults
                    by_severity = result.get("bySeverity", {})
                    for sev in ["critical", "high", "medium", "low", "info"]:
                        by_severity[sev] = int(by_severity.get(sev, 0))

                    verdict = result.get("verdict", "comment")
                    if verdict not in ("approve", "request_changes", "comment"):
                        verdict = "comment"

                    # Bei closed/merged immer comment
                    if state in ("closed", "merged"):
                        verdict = "comment"

                    return {
                        "bySeverity": by_severity,
                        "verdict": verdict,
                        "findings": result.get("findings", [])[:10],
                        "summary": result.get("summary", ""),
                        "canApprove": state == "open"
                    }

        except Exception as e:
            logger.warning(f"[agent] PR workspace analysis failed: {e}")

        # Fallback bei Fehler
        return {
            "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            "verdict": "comment",
            "findings": [],
            "summary": "Analyse fehlgeschlagen",
            "canApprove": state == "open"
        }

    async def _stream_final_response(
        self,
        messages: List[Dict],
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Streamt die finale Antwort Token für Token.
        Verwendet das analysis_model wenn konfiguriert.
        """
        result = await self._stream_final_response_with_usage(messages, model)
        async for token in result["tokens"]:
            yield token

    async def _stream_final_response_with_usage(
        self,
        messages: List[Dict],
        model: Optional[str] = None
    ) -> Dict:
        """
        Streamt die finale Antwort und tracked Token-Nutzung.
        Mit Retry-Logik für Verbindungsabbrüche.

        Returns:
            Dict mit "tokens" (AsyncGenerator) und "usage" (TokenUsage nach Abschluss)
        """
        # Modell-Auswahl: User-Auswahl > Phase-spezifisch > Default
        if model:
            # User hat explizit ein Modell ausgewählt - das hat Vorrang
            selected_model = model
            logger.debug("[stream] Using user-selected model: {selected_model}")
        elif settings.llm.analysis_model:
            selected_model = settings.llm.analysis_model
            logger.debug("[stream] Using analysis_model: {selected_model}")
        else:
            selected_model = settings.llm.default_model
            logger.debug("[stream] Using default_model: {selected_model}")

        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        # Reasoning für Analyse-Phase (Streaming = finale Antwort)
        reasoning = settings.llm.analysis_reasoning
        stream_messages = messages
        if reasoning and reasoning in ("low", "medium", "high"):
            stream_messages = central_llm_client._inject_reasoning(messages, reasoning)
            logger.debug(f"[stream] Reasoning aktiviert: {reasoning}")

        payload = {
            "model": selected_model,
            "messages": stream_messages,
            "temperature": settings.llm.temperature,
            "max_tokens": settings.llm.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True}
        }

        # Container für Usage (wird während Streaming gefüllt)
        usage_container = {"usage": None, "finish_reason": ""}

        # Prompt-Tokens schätzen (ca. 4 Zeichen pro Token)
        prompt_text = "".join(m.get("content", "") or "" for m in messages)
        estimated_prompt_tokens = len(prompt_text) // 4

        async def token_generator():
            completion_tokens = 0
            completion_chars = 0
            last_exc = None
            for attempt, delay in enumerate([0] + _RETRY_DELAYS):
                if delay:
                    logger.debug("[agent] Stream Retry {attempt} nach {delay}s")
                    await asyncio.sleep(delay)
                try:
                    client = _get_http_client()
                    async with client.stream(
                        "POST",
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json=payload
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                import json as json_module
                                chunk = json_module.loads(raw)

                                # Check for usage in final chunk (OpenAI stream_options)
                                if "usage" in chunk and chunk["usage"]:
                                    usage_data = chunk["usage"]
                                    usage_container["usage"] = TokenUsage(
                                        prompt_tokens=usage_data.get("prompt_tokens", 0),
                                        completion_tokens=usage_data.get("completion_tokens", 0),
                                        total_tokens=usage_data.get("total_tokens", 0),
                                        finish_reason=usage_container["finish_reason"],
                                        model=selected_model,
                                        truncated=(usage_container["finish_reason"] == "length")
                                    )

                                choices = chunk.get("choices", [])
                                if choices:
                                    choice = choices[0]
                                    delta = choice.get("delta", {})
                                    token = delta.get("content", "")

                                    # finish_reason extrahieren
                                    if choice.get("finish_reason"):
                                        usage_container["finish_reason"] = choice["finish_reason"]

                                    if token:
                                        completion_chars += len(token)
                                        completion_tokens = completion_chars // 4  # ~4 chars per token
                                        yield token

                            except (ValueError, KeyError, IndexError):
                                continue
                    return  # Erfolgreich abgeschlossen
                except Exception as e:
                    last_exc = e
                    if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                        logger.debug("[agent] Stream unterbrochen: {e}, Retry {attempt + 1}")
                        continue
                    logger.debug("[agent] Stream Fehler (kein Retry): {e}")
                    break

            # Fallback: Wenn kein Usage vom Server, schätzen wir basierend auf Zeichenzahl
            if not usage_container["usage"]:
                usage_container["usage"] = TokenUsage(
                    prompt_tokens=estimated_prompt_tokens,  # Geschätzt aus Prompt-Länge
                    completion_tokens=completion_tokens,
                    total_tokens=estimated_prompt_tokens + completion_tokens,
                    finish_reason=usage_container["finish_reason"],
                    model=selected_model,
                    truncated=(usage_container["finish_reason"] == "length")
                )

        return {
            "tokens": token_generator(),
            "usage": usage_container  # Wird nach Streaming gefüllt
        }

    async def _call_llm_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        model: Optional[str] = None,
        is_tool_phase: bool = True
    ) -> Dict:
        """
        Ruft das LLM mit Tool-Definitionen auf.
        Mit Retry-Logik für Verbindungsabbrüche und 5xx Fehler.

        Args:
            messages: Chat-Nachrichten
            tools: Tool-Definitionen (leer für finale Antwort)
            model: Explizites Modell (überschreibt automatische Auswahl)
            is_tool_phase: True = Tool-Phase (schnelles Modell), False = Analyse-Phase (großes Modell)
        """

        # Modell-Auswahl: Pro-Tool > Phase-spezifisch > Explizit (Header-Dropdown) > Default
        selected_model = None

        # 1. Pro-Tool Modell prüfen
        if is_tool_phase and settings.llm.tool_models:
            for msg in reversed(messages):
                if msg.get("role") == "tool":
                    tool_call_id = msg.get("tool_call_id", "")
                    for prev_msg in messages:
                        for tc in prev_msg.get("tool_calls", []):
                            if tc.get("id") == tool_call_id:
                                tool_name = tc["function"]["name"]
                                if tool_name in settings.llm.tool_models:
                                    selected_model = settings.llm.tool_models[tool_name]
                                    break
                        if selected_model:
                            break
                    if selected_model:
                        break

        if not selected_model:
            # Priorität: User-Auswahl > Phase-spezifisch > Default
            if model:
                # User hat explizit ein Modell ausgewählt - das hat Vorrang
                selected_model = model
                logger.debug("[model] Using user-selected model: {selected_model}")
            elif is_tool_phase and tools and settings.llm.tool_model:
                selected_model = settings.llm.tool_model
                logger.debug("[model] Using tool_model: {selected_model}")
            elif not is_tool_phase and settings.llm.analysis_model:
                selected_model = settings.llm.analysis_model
                logger.debug("[model] Using analysis_model: {selected_model}")
            else:
                selected_model = settings.llm.default_model
                logger.debug("[model] Using default_model: {selected_model}")

        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        # Phase-spezifische Temperature: Tool-Phase deterministisch, Analyse-Phase konfigurierbar
        is_tool_phase = bool(tools)
        if is_tool_phase:
            effective_temperature = settings.llm.tool_temperature
        else:
            cfg_analysis_temp = settings.llm.analysis_temperature
            effective_temperature = cfg_analysis_temp if cfg_analysis_temp >= 0 else settings.llm.temperature

        # Modell-spezifisches Kontext-Limit anwenden
        model_limit = _get_model_context_limit(selected_model)
        # Sicherheitsprüfung: mindestens 1000 Token Limit
        if not model_limit or model_limit < 1000:
            model_limit = settings.llm.default_context_limit or 32000
            logger.warning(f"[agent] Invalid context limit for {selected_model}, using {model_limit}")

        # Bei langen Chats: Summarizer aufrufen (fasst ältere Messages zusammen)
        if messages:  # Sicherheitsprüfung
            try:
                current_tokens = estimate_messages_tokens(messages)
                if current_tokens > model_limit * 0.8:  # Ab 80% des Limits
                    summarizer = get_summarizer()
                    summarized = await summarizer.summarize_if_needed(
                        messages,
                        target_tokens=int(model_limit * 0.7)  # Ziel: 70% des Limits
                    )
                    # Nur verwenden wenn Summarizer etwas zurückgibt
                    if summarized:
                        messages = summarized
                        logger.info(f"[agent] Summarizer aktiv: {current_tokens} -> {estimate_messages_tokens(messages)} tokens")
            except Exception as e:
                logger.warning(f"[agent] Summarizer/Token estimation failed: {e}")

        # Falls immer noch zu groß: Trim anwenden
        trimmed_messages = _trim_messages_to_limit(messages, model_limit)

        # Kontextgröße schätzen für Logging
        estimated_tokens = estimate_messages_tokens(trimmed_messages)
        logger.debug(f"[agent] LLM Request: ~{estimated_tokens} tokens, model={selected_model}, limit={model_limit}")

        # Timeout basierend auf Phase
        timeout = TIMEOUT_TOOL if is_tool_phase else TIMEOUT_ANALYSIS

        # Reasoning basierend auf Phase (GPT-OSS, o1, o3-mini Support)
        reasoning = settings.llm.tool_reasoning if is_tool_phase else settings.llm.analysis_reasoning

        # Tool-Prefill: Prüfe ob für dieses Modell aktiviert
        # 1. Modell-spezifischer Override hat Priorität
        # 2. Fallback auf globale Einstellung
        use_prefill = False
        if tools and is_tool_phase:
            model_prefill = settings.llm.tool_prefill_models.get(selected_model)
            if model_prefill is not None:
                use_prefill = model_prefill
            else:
                use_prefill = settings.llm.use_tool_prefill

        # Zentraler LLM-Call mit Retry-Logik
        try:
            response: LLMResponse = await central_llm_client.chat_with_tools(
                messages=trimmed_messages,
                tools=tools if tools else None,
                model=selected_model,
                temperature=effective_temperature,
                max_tokens=settings.llm.max_tokens,
                timeout=timeout,
                reasoning=reasoning or None,
                use_tool_prefill=use_prefill,
            )
        except Exception as e:
            # Kontext-Info für bessere Fehlermeldungen hinzufügen
            raise RuntimeError(
                f"LLM-Fehler (Kontext: ~{estimated_tokens} Token, Modell: {selected_model}): {e}"
            ) from e

        # Debug für Modelle mit Text-basierten Tool-Calls
        if response.finish_reason == "tool_calls" and not response.tool_calls:
            logger.warning(
                "[agent] finish_reason='tool_calls' aber keine tool_calls im message-Objekt! "
                "Modell: %s, Content (erste 500 Zeichen): %s",
                selected_model, (response.content or '')[:500]
            )

        # TokenUsage aus LLMResponse erstellen
        usage = TokenUsage(
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.prompt_tokens + response.completion_tokens,
            finish_reason=response.finish_reason,
            model=selected_model,
            truncated=(response.finish_reason == "length")
        )

        return {
            "content": response.content or "",
            "tool_calls": response.tool_calls,
            "native_tools": bool(response.tool_calls),
            "usage": usage,
            "finish_reason": response.finish_reason,
            "reasoning": reasoning or None,  # Reasoning-Level falls aktiv
        }

    async def _enrich_from_entity_tracker(
        self,
        state: AgentState,
        last_tool_name: str,
        result_text: str
    ) -> List[str]:
        """
        Proaktive Cross-Source-Anreicherung nach einem Tool-Result.

        Wenn search_code eine Java-Klasse findet → search_handbook (wenn noch kein Eintrag)
        Wenn search_handbook einen Service findet → search_code (wenn noch kein Code)

        Gibt Anreicherungs-Texte zurück (werden als AUTO-QUERVERWEISE eingefügt).
        """
        # Nur für Code/Handbuch-Suchergebnisse relevant
        if last_tool_name not in ("search_code", "read_file", "search_handbook", "get_service_info"):
            return []

        candidates = state.entity_tracker.get_entities_for_enrichment(last_tool_name, result_text)
        if not candidates:
            return []

        enrichments = []
        for entity in candidates[:2]:  # Max 2 Entitäten pro Iteration
            try:
                if last_tool_name in ("search_code", "read_file") and "handbuch" not in entity.sources:
                    # Java-Klasse gefunden → Handbuch-Eintrag suchen
                    hw_result = await self.tools.execute("search_handbook", query=entity.name, top_k=1)
                    if hw_result.success and hw_result.data and len(hw_result.data) > 50:
                        entity.sources["handbuch"] = entity.name
                        enrichments.append(
                            f"[Handbuch-Querverweis für {entity.name}]\n{hw_result.data[:1500]}"
                        )

                elif last_tool_name in ("search_handbook", "get_service_info") and "java" not in entity.sources:
                    # Handbuch-Service gefunden → Java-Code suchen
                    java_result = await self.tools.execute("search_code", query=entity.name, top_k=1)
                    if java_result.success and java_result.data and len(java_result.data) > 50:
                        entity.sources["java"] = entity.name
                        enrichments.append(
                            f"[Java-Querverweis für {entity.name}]\n{java_result.data[:1500]}"
                        )
            except Exception as e:
                logger.debug("[entity_enrichment] Fehler bei Anreicherung für {entity.name}: {e}")

        return enrichments

    async def _execute_confirmed_operation(self, confirmation_data: Dict) -> ToolResult:
        """Führt eine bestätigte Operation aus."""
        from app.services.file_manager import get_file_manager
        import difflib
        import uuid
        from pathlib import Path

        operation = confirmation_data.get("operation")
        path = confirmation_data.get("path")

        if operation == "write_file":
            content = confirmation_data.get("content")
            manager = get_file_manager()

            # Read original content for diff
            original_content = ""
            is_new = True
            try:
                resolved_path = Path(path).resolve()
                if resolved_path.exists():
                    original_content = resolved_path.read_text(encoding="utf-8", errors="replace")
                    is_new = False
            except Exception:
                pass

            success = await manager.execute_write(path, content)

            # Emit workspace code change event
            if success:
                await self._emit_workspace_code_change(
                    file_path=path,
                    original_content=original_content,
                    modified_content=content,
                    tool_call="write_file",
                    description=confirmation_data.get("description", "File written"),
                    is_new=is_new
                )

            return ToolResult(success=success, data=f"Datei geschrieben: {path}")

        elif operation == "edit_file":
            old_string = confirmation_data.get("old_string")
            new_string = confirmation_data.get("new_string")
            manager = get_file_manager()

            # Read original content for diff
            original_content = ""
            try:
                resolved_path = Path(path).resolve()
                if resolved_path.exists():
                    original_content = resolved_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

            success = await manager.execute_edit(path, old_string, new_string)

            # Emit workspace code change event
            if success:
                # Generate modified content
                modified_content = original_content.replace(old_string, new_string, 1)
                await self._emit_workspace_code_change(
                    file_path=path,
                    original_content=original_content,
                    modified_content=modified_content,
                    tool_call="edit_file",
                    description=confirmation_data.get("description", "File edited"),
                    is_new=False
                )

            return ToolResult(success=success, data=f"Datei bearbeitet: {path}")

        elif operation == "query_database":
            query = confirmation_data.get("query")
            max_rows = confirmation_data.get("max_rows", 100)

            # Execute query and emit workspace event
            result = await self._execute_and_emit_sql_result(query, max_rows)
            return result

        elif operation == "batch_write_files":
            # Batch-Write: Alle Dateien auf einmal schreiben
            files = confirmation_data.get("files", [])
            manager = get_file_manager()

            success_count = 0
            errors = []
            written_paths = []

            for file_spec in files:
                file_path = file_spec.get("path")
                content = file_spec.get("content")
                try:
                    # Read original content for diff
                    original_content = ""
                    is_new = True
                    try:
                        resolved_path = Path(file_path).resolve()
                        if resolved_path.exists():
                            original_content = resolved_path.read_text(encoding="utf-8", errors="replace")
                            is_new = False
                    except Exception:
                        pass

                    success = await manager.execute_write(file_path, content)
                    if success:
                        success_count += 1
                        written_paths.append(file_path)

                        # Emit workspace code change event
                        await self._emit_workspace_code_change(
                            file_path=file_path,
                            original_content=original_content,
                            modified_content=content,
                            tool_call="batch_write_files",
                            description=confirmation_data.get("description", "Batch file written"),
                            is_new=is_new
                        )
                    else:
                        errors.append(f"{file_path}: Schreiben fehlgeschlagen")
                except Exception as e:
                    errors.append(f"{file_path}: {e}")

            total = len(files)
            if success_count == total:
                return ToolResult(
                    success=True,
                    data=f"Alle {total} Dateien erfolgreich geschrieben:\n" + "\n".join(f"  - {p}" for p in written_paths)
                )
            elif success_count > 0:
                return ToolResult(
                    success=True,
                    data=f"{success_count}/{total} Dateien geschrieben.\nFehler:\n" + "\n".join(errors)
                )
            else:
                return ToolResult(
                    success=False,
                    error=f"Alle Dateien fehlgeschlagen:\n" + "\n".join(errors)
                )

        else:
            return ToolResult(success=False, error=f"Unbekannte Operation: {operation}")

    def _build_agent_instructions(self, mode: AgentMode, plan_approved: bool = False) -> str:
        """Baut die Agent-Instruktionen für den System-Prompt."""
        db_available = settings.database.enabled
        handbook_available = settings.handbook.enabled

        base = """
## Agent-Anweisungen

Du bist ein intelligenter Assistent mit Zugriff auf Tools.

### Verfügbare Tools:

**Code-Suche:**
- search_code: Durchsuche Java/Python/SQL Code nach relevanten Dateien
- read_file: Lese den Inhalt einer Datei
- list_files: Liste Dateien in einem Verzeichnis auf
- trace_java_references: Verfolge Java-Klassenhierarchien (Interfaces, Parent-Klassen)
"""

        if handbook_available:
            base += """
**Handbuch:**
- search_handbook: Durchsuche das Handbuch nach Service-Dokumentation
- get_service_info: Hole Service-Details aus dem Handbuch
"""

        base += """
**Wissen & Dokumente:**
- search_skills: Durchsuche die Wissensbasen der aktiven Skills
- search_pdf: Durchsuche hochgeladene PDF-Dokumente
"""

        if settings.internal_fetch.enabled:
            base += """
**HTTP/URL-Abruf (Internal Fetch):**
- internal_fetch: Ruft eine URL ab und gibt den Inhalt zurück (HTML, JSON, Text)
- internal_search: Ruft eine URL ab und durchsucht den Inhalt nach einem Pattern
- http_request: Führt HTTP-Requests aus (wie curl) - GET, POST, PUT, DELETE, PATCH mit Body und Headers

Nutze diese Tools um:
- Interne/Intranet-Seiten abzurufen
- REST-APIs aufzurufen (GET, POST, etc.)
- Webseiten zu durchsuchen
- Daten von URLs zu holen
"""

        if settings.github.enabled:
            base += """
**GitHub (Code-Suche & Repository):**
- github_search_code: Durchsucht Code in ALLEN Repos nach Beispielen, Patterns, Funktionen
- github_list_repos: Listet Repositories einer Organisation
- github_list_prs: Pull Requests eines Repos auflisten
- github_pr_diff: Code-Änderungen eines PRs anzeigen
- github_get_file: Dateiinhalt von GitHub holen (aus Branch/Commit)
- github_recent_commits: Letzte Commits eines Branches

Nutze github_search_code wenn der User nach:
- Code-Beispielen sucht ("wie wird X implementiert", "Beispiele für Y")
- Patterns oder Best Practices sucht
- Wissen will wo eine Funktion/Klasse verwendet wird
- Nach ähnlichen Implementierungen sucht
"""

        # Git-Tools sind immer verfügbar (Git ist vorausgesetzt)
        base += """
**Git (Lokales Repository):**
- git_status: Zeigt geänderte/ungetrackte Dateien, aktueller Branch
- git_diff: Zeigt Code-Änderungen (Working Dir, Staged, zwischen Commits)
- git_log: Commit-Historie anzeigen (mit Filter nach Autor, Datei, Zeit)
- git_branch_list: Alle Branches auflisten
- git_blame: Wer hat welche Zeile geändert?
- git_show_commit: Vollständige Commit-Details mit Diff

WICHTIG: git_* Tools sind für LOKALE Repos. Für REMOTE GitHub: github_* Tools verwenden.
Nutze git_status/git_diff wenn der User wissen will was sich geändert hat.
Nutze git_blame um herauszufinden wer Code geschrieben hat.
"""

        if settings.docker_sandbox.enabled:
            base += """
**Docker Sandbox (Sichere Code-Ausführung):**
- docker_execute_python: Führt Python-Code in isoliertem Container aus (stateless)
- docker_session_create: Erstellt persistente Session (Variablen bleiben erhalten)
- docker_session_execute: Führt Code in Session aus (mit persistenten Variablen)
- docker_session_list: Listet aktive Sessions
- docker_session_close: Schließt eine Session
- docker_upload_file: Lädt Datei in Session hoch (Base64)
- docker_list_packages: Zeigt verfügbare Python-Pakete

WANN NUTZEN:
- Benutzer bittet um Code-Ausführung: "encodiere in Base64", "berechne SHA256 Hash"
- Datenverarbeitung: JSON parsen, CSV verarbeiten, Regex testen
- Mathematische Berechnungen
- Testen von Code-Snippets
- Daten transformieren oder konvertieren

BEISPIELE:
- "Encodiere 'Hello' in Base64" → docker_execute_python(code="import base64; print(base64.b64encode(b'Hello').decode())")
- "Berechne SHA256 von 'password'" → docker_execute_python(code="import hashlib; print(hashlib.sha256(b'password').hexdigest())")

FÜR MEHRERE OPERATIONEN: Erstelle eine Session mit docker_session_create, dann docker_session_execute für jeden Schritt.
Variablen bleiben zwischen Aufrufen erhalten!
"""

        if db_available:
            base += f"""
**Datenbank (DB2):**
Die DB2-Datenbank ist aktiviert und verbunden ({settings.database.host}:{settings.database.port}/{settings.database.database}).
- query_database: Führe eine SELECT-Abfrage aus (nur SELECT erlaubt, readonly)
- list_database_tables: Liste alle Tabellen im Schema auf
- describe_database_table: Zeige Spalten, Typen und Constraints einer Tabelle

WICHTIG: Nutze query_database um Daten abzufragen. Beispiel: query_database(query="SELECT * FROM tabelle FETCH FIRST 10 ROWS ONLY")
"""

        base += """
### Tool-Aufrufe

**WICHTIG - Tool-Aufruf-Format:**
Wenn du ein Tool aufrufen willst, formatiere es EXAKT so:

```
[TOOL_CALLS][{"name": "tool_name", "arguments": {"param1": "value1", "param2": "value2"}}]
```

Beispiele:
- `[TOOL_CALLS][{"name": "search_code", "arguments": {"query": "PaymentService", "language": "java"}}]`
- `[TOOL_CALLS][{"name": "read_file", "arguments": {"path": "src/Main.java"}}]`
- `[TOOL_CALLS][{"name": "http_request", "arguments": {"url": "https://api.example.com", "method": "POST", "body": "{\"key\": \"value\"}"}}]`

Rufe immer nur EIN Tool pro Nachricht auf. Warte auf das Ergebnis bevor du das nächste Tool aufrufst.

### Vorgehensweise bei jeder Anfrage:

1. **Verstehen**: Was genau wird gefragt? Welche Information fehlt mir?
2. **Planen**: Welche Tools brauche ich in welcher Reihenfolge?
3. **Ausführen**: Tools einzeln aufrufen und Ergebnis abwarten, bevor das nächste Tool gerufen wird.
4. **Antworten**: Erst wenn alle nötigen Infos vorliegen, die Antwort formulieren.

Verwende die passenden Tools um Informationen zu sammeln, bevor du antwortest.
- Bei Code-Fragen: Suche zuerst nach relevantem Code. Bei komplexen Klassen nutze trace_java_references.
- Bei Handbuch-Fragen: Suche zuerst im Handbuch.
- Bei PDF-Dokumenten: Durchsuche sie mit search_pdf.
- Bei Datenbank-Fragen: Nutze list_database_tables und describe_database_table um die Struktur zu verstehen.
- Lese jede Datei nur EINMAL - der Inhalt bleibt im Kontext verfügbar.

### Beispiel-Abläufe:

**Beispiel 1** — Benutzer: "Was macht die Klasse PaymentService?"
→ Plane: Ich brauche den Java-Code → search_code, dann ggf. read_file.
→ Tool: search_code(query="PaymentService", language="java", top_k=3)
→ Ergebnis zeigt Pfad: src/payment/PaymentService.java
→ Tool: read_file(path="src/payment/PaymentService.java")
→ Antwort: "PaymentService ist zuständig für..."

**Beispiel 2** — Benutzer: "Erstelle einen JUnit-Test für OrderValidator"
→ Plane: Erst Quellcode lesen, dann Test generieren.
→ Tool: search_code(query="OrderValidator", language="java")
→ Tool: read_file(path="src/order/OrderValidator.java")
→ Antwort mit vollständigem JUnit-5-Test in ```java Block.
"""

        if mode == AgentMode.READ_ONLY:
            return base + """
MODUS: Nur Lesen
Du kannst keine Dateien schreiben oder bearbeiten.
Gib Code-Vorschläge als Markdown-Codeblöcke aus.
Datenbank-Abfragen (SELECT) sind erlaubt.
"""

        elif mode == AgentMode.WRITE_WITH_CONFIRM:
            return base + """
MODUS: Schreiben mit Bestätigung
Zusätzliche Tools:
- write_file: Erstelle oder überschreibe eine DATEI (benötigt Bestätigung) - NUR für Dateien mit Endung!
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)
- create_directory: Erstelle einen ORDNER (benötigt Bestätigung) - NUR für Verzeichnisse!
- batch_write_files: WICHTIG! Schreibt MEHRERE Dateien mit EINER Bestätigung. Nutze wenn du 2+ Dateien erstellen musst!

**MEHRERE DATEIEN ERSTELLEN:**
Wenn du mehrere Dateien erstellen musst (z.B. bei einem Design-Konzept), nutze IMMER batch_write_files!
Format: batch_write_files(files='[{"path": "src/A.java", "content": "..."}, {"path": "src/B.java", "content": "..."}]')
→ User bestätigt EINMAL für alle Dateien

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file oder batch_write_files

Der User muss Datei-Operationen bestätigen bevor sie ausgeführt werden.
Datenbank-Abfragen (SELECT) sind ohne Bestätigung erlaubt.
"""

        elif mode == AgentMode.PLAN_THEN_EXECUTE and not plan_approved:
            return base + """
MODUS: Planungsphase
Du befindest dich in der Planungsphase. Datei-Änderungen sind noch NICHT erlaubt.

**Deine Aufgabe:**
1. Nutze Read-Tools (search_code, read_file, etc.) um den relevanten Code zu analysieren.
2. Erstelle einen strukturierten Implementierungsplan.
3. Schreibe deinen fertigen Plan EXAKT in folgendem Format:

[PLAN]
**Aufgabe:** <Kurzbeschreibung der Aufgabe>

**Analysierte Dateien:**
- `<Dateipfad>`: <Was wurde darin gefunden>

**Implementierungsschritte:**
1. **`<Dateipfad>`** – <Was wird geändert und warum>
2. ...

**Erwartete Auswirkungen:**
- <Auswirkung 1>
[/PLAN]

Schreibe NUR den [PLAN]-Block als deine finale Antwort. Führe keine Datei-Änderungen durch.
"""

        elif mode == AgentMode.PLAN_THEN_EXECUTE and plan_approved:
            return base + """
MODUS: Ausführungsphase (Plan genehmigt)
Der User hat deinen Plan genehmigt.
Zusätzliche Tools:
- write_file: Erstelle eine einzelne DATEI (benötigt Bestätigung)
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)
- create_directory: Erstelle einen ORDNER
- batch_write_files: BEVORZUGT! Schreibt MEHRERE Dateien mit EINER Bestätigung!

**WICHTIG - NUTZE BATCH_WRITE_FILES FÜR MEHRERE DATEIEN:**
Wenn dein Plan mehrere Dateien erstellt/ändert, nutze batch_write_files um sie ALLE auf einmal zu schreiben!
→ Statt 5x write_file (5 Bestätigungen) → 1x batch_write_files (1 Bestätigung)
Format: batch_write_files(files='[{"path": "...", "content": "..."}, ...]')

**VOLLSTÄNDIGE PLAN-AUSFÜHRUNG:**
Du MUSST ALLE Dateien des Plans erstellen - nicht nach der ersten aufhören!
- Sammle ALLE zu erstellenden Dateien
- Nutze batch_write_files um sie in EINEM Aufruf zu schreiben
- User bestätigt einmal, alle Dateien werden erstellt

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende batch_write_files (oder write_file für einzelne)
"""

        elif mode == AgentMode.DEBUG:
            return base + """
MODUS: Debug & Fehleranalyse
Du hilfst beim systematischen Verstehen und Lösen von Fehlern. Keine Datei-Änderungen erlaubt.

**Dein Vorgehen:**
1. **Verstehen**: Stelle gezielte Rückfragen bevor du analysierst. Nutze das Tool `suggest_answers` um dem User Antwort-Optionen anzubieten.
2. **Nachstellen**: Nutze Log-Tools, Code-Suche und Datenbank-Abfragen um den Fehler zu reproduzieren.
3. **Analysieren**: Suche nach Root-Cause im Code, Konfiguration und Logs.
4. **Lösungsvorschlag**: Erkläre die Ursache und schlage Korrekturen als Codeblöcke vor (keine Datei-Schreiboperationen).

**Rückfragen mit suggest_answers:**
Wenn du mehr Kontext brauchst, rufe `suggest_answers` auf BEVOR du mit der Analyse beginnst:
- Formuliere eine klare Frage
- Gib 3-5 konkrete Antwort-Optionen vor
- Der User kann eine Option wählen oder frei antworten

**Typische Rückfragen:**
- Wann tritt der Fehler auf? (immer / sporadisch / nach bestimmten Aktionen)
- In welcher Umgebung? (dev / test / prod)
- Gibt es eine Fehlermeldung/Exception? (ja, welche / nein, nur falsches Verhalten)
- Ist das Verhalten neu? (seit letztem Deployment / schon immer / nach Konfigurationsänderung)

Verfügbare Diagnose-Tools: search_code, read_file, search_handbook, Log-Tools, Datenbank-Abfragen (SELECT).
"""

        else:  # AUTONOMOUS
            return base + """
MODUS: Autonom
Zusätzliche Tools:
- write_file: Erstelle eine einzelne DATEI
- edit_file: Bearbeite eine Datei
- create_directory: Erstelle einen ORDNER
- batch_write_files: Schreibt mehrere Dateien in einem Aufruf (effizienter!)

**MEHRERE DATEIEN:**
Bei mehreren Dateien nutze batch_write_files für bessere Performance.
Format: batch_write_files(files='[{"path": "...", "content": "..."}, ...]')

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file oder batch_write_files

Du kannst Dateien ohne Bestätigung schreiben/bearbeiten.
Sei vorsichtig und mache nur notwendige Änderungen.
"""

    def confirm_operation(self, session_id: str, confirmed: bool) -> bool:
        """
        Bestätigt oder lehnt eine ausstehende Operation ab.

        Args:
            session_id: Session-ID
            confirmed: True wenn bestätigt, False wenn abgelehnt

        Returns:
            True wenn eine Operation wartete
        """
        state = self._get_state(session_id)
        if state.pending_confirmation:
            state.pending_confirmation.confirmed = confirmed
            return True
        return False

    def cancel_request(self, session_id: str) -> None:
        """Bricht die laufende Anfrage einer Session ab."""
        state = self._get_state(session_id)
        state.cancelled = True
        logger.debug("[agent] Anfrage für Session {session_id} abgebrochen")

    def clear_session(self, session_id: str) -> None:
        """Löscht den State einer Session (Speicher + Disk)."""
        self._states.pop(session_id, None)
        try:
            from app.services.chat_store import delete_chat
            delete_chat(session_id)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_orchestrator: Optional[AgentOrchestrator] = None


def get_agent_orchestrator() -> AgentOrchestrator:
    """Gibt die Singleton-Instanz des Agent Orchestrators zurück."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator()
    return _orchestrator
