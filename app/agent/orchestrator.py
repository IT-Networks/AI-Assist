"""
Agent Orchestrator - Koordiniert den Agent-Loop mit Tool-Calls.

Der Orchestrator:
1. Nimmt User-Nachrichten entgegen
2. Baut den Kontext aus aktiven Skills
3. Ruft das LLM mit Tool-Definitionen auf
4. Führt Tool-Calls aus
5. Bei Schreib-Ops: Wartet auf User-Bestätigung
6. Wiederholt bis fertig oder max_iterations erreicht

Refactored: Types, utilities and helpers are imported from app.agent.orchestration
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.prompt_enhancer import EnrichedPrompt

import httpx

from app.agent.constants import ControlMarkers
from app.agent.entity_tracker import EntityTracker

# ══════════════════════════════════════════════════════════════════════════════
# Import modular components from orchestration package
# ══════════════════════════════════════════════════════════════════════════════
from app.agent.orchestration.types import (
    AgentMode,
    AgentEventType,
    AgentEvent,
    AgentState,
    ToolCall,
    TokenUsage,
    MCP_EVENT_TYPE_MAPPING,
)
from app.agent.orchestration.utils import (
    get_model_context_limit as _get_model_context_limit,
    trim_messages_to_limit as _trim_messages_to_limit,
    detect_pr_context as _detect_pr_context,
    filter_tools_for_pr_context as _filter_tools_for_pr_context,
    analyze_pr_for_workspace as _analyze_pr_for_workspace,
)
from app.agent.orchestration.llm_caller import (
    call_llm_with_tools as _call_llm_with_tools,
    llm_callback_for_mcp as _llm_callback_for_mcp_impl,
)
from app.agent.orchestration.workspace_events import (
    build_code_change_event as _build_code_change_event,
    build_sql_result_event as _build_sql_result_event,
    format_sql_result_for_agent as _format_sql_result_for_agent,
)
from app.agent.orchestration.tool_executor import (
    is_parallelizable_tool as _is_parallelizable_tool,
    execute_tools_parallel as _execute_tools_parallel,
    PARALLELIZABLE_TOOL_PREFIXES,
    SEQUENTIAL_ONLY_TOOLS,
    truncate_result as _truncate_result,
)
from app.agent.orchestration.response_handler import (
    strip_tool_markers as _strip_tool_markers,
    extract_plan_block,
    build_usage_data as _build_usage_data,
    track_token_usage as _track_token_usage,
    stream_final_response_with_usage as _stream_final_response_with_usage,
)
from app.agent.orchestration.tool_parser import (
    parse_text_tool_calls as _parse_text_tool_calls,
    REGEX_PATTERNS as _TOOL_REGEX,
)
from app.agent.orchestration.command_parser import (
    parse_mcp_force_capability,
    parse_slash_command,
    check_continue_markers,
)
from app.agent.orchestration.context_builder import (
    extract_conversation_context as _extract_conversation_context,
    build_agent_instructions as _build_agent_instructions,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Regex patterns are now imported from orchestration.tool_parser as _TOOL_REGEX
# Additional patterns still needed locally (not in tool_parser module)
# ══════════════════════════════════════════════════════════════════════════════
_RE_MCP_FORCE = re.compile(r'^\[MCP:(\w+)\]\s*(.+)$', re.DOTALL)
_RE_PLAN_BLOCK = re.compile(r'\[PLAN\](.*?)\[/PLAN\]', re.DOTALL)

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


# ══════════════════════════════════════════════════════════════════════════════
# Functions imported from app.agent.orchestration:
# Types: AgentMode, AgentEventType, AgentEvent, AgentState, ToolCall, TokenUsage
# Utils: _get_model_context_limit, _trim_messages_to_limit, _detect_pr_context
# Tool Executor: _is_parallelizable_tool, _truncate_result
# Response Handler: _strip_tool_markers, extract_plan_block
# Tool Parser: _parse_text_tool_calls
# Command Parser: parse_mcp_force_capability, parse_slash_command, check_continue_markers
# ══════════════════════════════════════════════════════════════════════════════


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
        """LLM-Callback für MCP Sequential Thinking. Delegiert zu llm_caller."""
        session_id = getattr(self, '_current_mcp_session', 'mcp-default')
        return await _llm_callback_for_mcp_impl(prompt, context, session_id)

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
                    msg_count = len(state.messages_history)
                    logger.info(f"[orchestrator] Restored session {session_id}: {msg_count} messages, title='{state.title}'")
                    try:
                        state.mode = AgentMode(saved.get("mode", "read_only"))
                    except ValueError:
                        pass
                else:
                    logger.debug(f"[orchestrator] No saved chat found for session {session_id}")
            except Exception as e:
                logger.warning(f"[orchestrator] Failed to restore session {session_id}: {e}")
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
        """Emit workspace code change event for UI."""
        event_data = _build_code_change_event(
            file_path=file_path,
            original_content=original_content,
            modified_content=modified_content,
            tool_call=tool_call,
            description=description,
            is_new=is_new
        )
        await self._event_bridge.emit(
            AgentEventType.WORKSPACE_CODE_CHANGE.value,
            event_data
        )

    async def _execute_and_emit_sql_result(self, query: str, max_rows: int = 100) -> ToolResult:
        """Execute SQL query and emit workspace SQL result event."""
        import time
        start_time = time.time()

        try:
            from app.services.db_client import get_db_client
            client = get_db_client()

            if not client:
                return ToolResult(success=False, error="DB-Client nicht verfügbar")

            # Temporarily override max_rows
            original_max = client.max_rows
            client.max_rows = min(max_rows, settings.database.max_rows)
            result = await client.execute(query)
            client.max_rows = original_max

            execution_time_ms = int((time.time() - start_time) * 1000)

            if not result.success:
                event_data = _build_sql_result_event(
                    query=query,
                    database=settings.database.database or "DB2",
                    schema=client.schema if client else None,
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=execution_time_ms,
                    error=result.error
                )
                await self._event_bridge.emit(AgentEventType.WORKSPACE_SQL_RESULT.value, event_data)
                return ToolResult(success=False, error=result.error)

            # Emit success event
            event_data = _build_sql_result_event(
                query=query,
                database=settings.database.database or "DB2",
                schema=client.schema if client else None,
                columns=result.columns or [],
                rows=result.rows or [],
                row_count=result.row_count,
                execution_time_ms=execution_time_ms,
                truncated=result.truncated
            )
            await self._event_bridge.emit(AgentEventType.WORKSPACE_SQL_RESULT.value, event_data)

            # Format output for agent
            output = _format_sql_result_for_agent(
                columns=result.columns or [],
                rows=result.rows or [],
                row_count=result.row_count,
                truncated=result.truncated,
                max_rows=client.max_rows
            )
            return ToolResult(success=True, data=output)

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            event_data = _build_sql_result_event(
                query=query,
                database="DB2",
                schema=None,
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=execution_time_ms,
                error=str(e)
            )
            await self._event_bridge.emit(AgentEventType.WORKSPACE_SQL_RESULT.value, event_data)
            return ToolResult(success=False, error=str(e))

    async def _drain_mcp_events(self) -> AsyncGenerator[AgentEvent, None]:
        """
        Liefert alle wartenden MCP Events aus der Event Bridge.
        Mappt Event-Typen zu AgentEventType.
        """
        async for event in self._event_bridge.drain():
            event_type_enum = MCP_EVENT_TYPE_MAPPING.get(event.event_type, AgentEventType.MCP_STEP)
            yield AgentEvent(event_type_enum, event.data)

    async def _drain_mcp_events_from_queue(self, queue: asyncio.Queue, timeout: float = 0.01) -> AsyncGenerator[AgentEvent, None]:
        """
        Liefert alle wartenden MCP Events aus einer bestehenden Queue.
        Verwendet für persistente Subscriptions während Tool-Ausführung.
        """
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
                event_type_enum = MCP_EVENT_TYPE_MAPPING.get(event.event_type, AgentEventType.MCP_STEP)
                yield AgentEvent(event_type_enum, event.data)
            except asyncio.TimeoutError:
                break

    # Note: _extract_conversation_context is now imported from context_builder module

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
        conversation_context = _extract_conversation_context(messages)

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
        from app.agent.orchestration.command_parser import (
            activate_skills_for_command,
        )
        from app.agent.orchestration.phase_runner import (
            run_forced_capability as _run_forced_capability,
        )

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

        # Confluence Session-Tracking setzen (für Loop-Prävention bei Seiten-Lesen)
        from app.services.confluence_cache import set_current_session
        set_current_session(session_id)

        # ── MCP Force-Capability Detection ────────────────────────────────────
        # Format: [MCP:capability_name] actual query
        forced_capability, user_message = parse_mcp_force_capability(user_message)

        # ── Slash-Command Skill Activation ─────────────────────────────────────
        # Uses command_parser module for parsing
        parsed_command = None
        if not forced_capability:
            parsed_command = parse_slash_command(user_message)
            if parsed_command:
                skill_mgr = get_skill_manager()
                command_skills = activate_skills_for_command(parsed_command, state, skill_mgr)
                if command_skills:
                    user_message = parsed_command.get_transformed_message()
                    logger.debug(f"[agent] Slash-Command /{parsed_command.command_name} with {len(command_skills)} skills, flags: {parsed_command.flags}")

        # ── Continue-Handling (nach Bestätigung) ───────────────────────────────
        # Uses command_parser module for continue marker detection
        continue_result = check_continue_markers(user_message, state)
        is_continue = continue_result.is_continue
        is_continue_enhanced = continue_result.is_continue_enhanced
        is_retry_with_web = continue_result.is_retry_with_web
        is_continue_no_web = continue_result.is_continue_no_web

        if continue_result.transformed_message:
            user_message = continue_result.transformed_message

        if is_retry_with_web:
            state.web_fallback_approved = True
        if is_continue_no_web:
            state.web_fallback_approved = False
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

        # Agent-Instruktionen (from context_builder module)
        agent_instructions = _build_agent_instructions(state.mode, state.plan_approved)
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

        # === RESEARCH PHASE - DEPRECATED ===
        # Research functionality has been migrated to skills (/research, /sc:research).
        # The old _run_research_phase method is no longer called.
        # If enriched_context exists, it means MCP Enhancement already collected context.

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
        # Uses phase_runner.run_forced_capability for cleaner separation
        if forced_capability:
            # Initialize MCP bridge if needed
            if self._mcp_bridge is None:
                self._mcp_bridge = get_tool_bridge(
                    llm_callback=self._llm_callback_for_mcp,
                    event_callback=self._emit_mcp_event
                )

            # Execute via phase_runner module
            async for event in _run_forced_capability(
                capability=forced_capability,
                user_message=user_message,
                mcp_bridge=self._mcp_bridge,
                event_bridge=self._event_bridge,
            ):
                yield event

            return  # End here - no normal agent loop

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

                response = await _call_llm_with_tools(
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
                            plan_resp = await _call_llm_with_tools(
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

                        # Build usage data and track tokens using helpers
                        usage_data = _build_usage_data(
                            prompt_tokens=request_prompt_tokens,
                            completion_tokens=request_completion_tokens,
                            finish_reason=last_finish_reason,
                            model=last_model,
                            state=state,
                            budget=budget,
                        )
                        _track_token_usage(
                            session_id=session_id,
                            model=last_model,
                            input_tokens=request_prompt_tokens,
                            output_tokens=request_completion_tokens,
                            request_type="plan",
                        )

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
                            stream_result = await _stream_final_response_with_usage(messages)
                            async for token in stream_result["tokens"]:
                                assistant_response += token
                                yield AgentEvent(AgentEventType.TOKEN, token)
                            final_usage = stream_result["usage"].get("usage")
                        else:
                            analysis_response = await _call_llm_with_tools(
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
                        stream_result = await _stream_final_response_with_usage(messages, model)
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

                    # Build usage data and track tokens using helpers
                    usage_data = _build_usage_data(
                        prompt_tokens=request_prompt_tokens,
                        completion_tokens=request_completion_tokens,
                        finish_reason=last_finish_reason,
                        model=last_model,
                        state=state,
                        budget=budget,
                    )
                    _track_token_usage(
                        session_id=session_id,
                        model=last_model,
                        input_tokens=request_prompt_tokens,
                        output_tokens=request_completion_tokens,
                        request_type="chat",
                    )

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
                            # Warte max 30 Sekunden auf Abschluss
                            pr_result = await asyncio.wait_for(
                                state.pending_pr_analysis, timeout=30.0
                            )
                            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                                "prNumber": state.pending_pr_number,
                                **pr_result
                            })
                        except asyncio.TimeoutError:
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
                    tool_name = tc["function"]["name"]
                    parse_error = None

                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            # JSON-Parsing fehlgeschlagen - speichere Error für bessere Meldung
                            parsed_args = {"__parse_error__": str(e), "__raw_args__": raw_args[:500]}
                            parse_error = str(e)
                            logger.warning(f"[agent] JSON parse error for {tool_name}: {e}, raw: {raw_args[:200]}")
                    else:
                        parsed_args = raw_args

                    parsed_tool_calls.append(ToolCall(
                        id=tc.get("id", f"call_{len(state.tool_calls_history)}"),
                        name=tool_name,
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
                    parallel_results = await _execute_tools_parallel(
                        parsed_tool_calls, state, self.tools, self._tool_cache
                    )

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
                        # WICHTIG: Tool-Call-IDs müssen zwischen assistant.tool_calls und
                        # tool.tool_call_id übereinstimmen! Wir nutzen die originalen IDs aus
                        # tools_to_process (raw API Response) und updaten die Ergebnisse.
                        messages.append({
                            "role": "assistant",
                            "content": content if content else None,
                            "tool_calls": current_tool_calls_for_messages
                        })
                        # Tool-Responses mit IDs aus current_tool_calls_for_messages (nicht parsed_tool_calls!)
                        for i, raw_tc in enumerate(current_tool_calls_for_messages):
                            parsed_tc = parsed_tool_calls[i] if i < len(parsed_tool_calls) else None
                            if parsed_tc and parsed_tc.result:
                                # ID muss mit der ID in tool_calls übereinstimmen!
                                tc_id = raw_tc.get("id") or parsed_tc.id
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "content": _truncate_result(parsed_tc.result.to_context(), tool_name=parsed_tc.name)
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
                            tc_id = tc.get("id") or tool_call.id  # ID muss mit tc übereinstimmen
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
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
                            tc_id = tc.get("id") or tool_call.id  # ID muss mit tc übereinstimmen
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
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
                            tc_id = tc.get("id") or tool_call.id  # ID muss mit tc übereinstimmen
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
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
                        # WICHTIG: Tool-Call-ID muss mit der ID in tc übereinstimmen!
                        tc_id = tc.get("id") or tool_call.id
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": result.to_context()
                        })
                        state.tool_calls_history.append(tool_call)
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tc_id,
                            "name": tool_call.name,
                            "result": result.to_context()[:500],
                            "success": True,
                        })
                        continue  # Nächsten Tool-Call verarbeiten
                    # ────────────────────────────────────────────────────────

                    # Tool ausführen - MCP-Tools speziell behandeln
                    # Capabilities: analyze (Code-Analyse), sequential_thinking
                    # NOTE: brainstorm, design, implement wurden zu Skills migriert (siehe ~/.claude/commands/sc/)
                    MCP_CAPABILITY_TOOLS = {
                        "sequential_thinking", "seq_think",
                        "analyze",  # Code-Analyse bleibt als MCP-Capability
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
                    if result.requires_confirmation:
                        # Modus bestimmt ob Bestätigung angefordert oder auto-ausgeführt wird
                        should_auto_execute = (
                            # PLAN_THEN_EXECUTE mit genehmigtem Plan
                            (state.mode == AgentMode.PLAN_THEN_EXECUTE and state.plan_approved)
                            # AUTONOMOUS Modus (ohne Bestätigung)
                            or state.mode == AgentMode.AUTONOMOUS
                        )
                        confirmed = False  # Default: nicht bestätigt
                        blocked_by_mode = False  # Unterscheidung: User-Ablehnung vs Mode-Block

                        if state.mode == AgentMode.WRITE_WITH_CONFIRM:
                            # WRITE_WITH_CONFIRM: User-Bestätigung anfordern
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
                        elif should_auto_execute:
                            # PLAN_THEN_EXECUTE mit genehmigtem Plan: Auto-Ausführung
                            logger.info(f"[agent] Auto-executing {tool_call.name} (plan approved)")
                            confirmed = True
                            tool_call.confirmed = True
                        else:
                            # Anderer Modus (z.B. READ_ONLY): Nicht ausführen
                            logger.warning(f"[agent] Write operation {tool_call.name} blocked in mode {state.mode}")
                            blocked_by_mode = True
                            result = ToolResult(
                                success=False,
                                error=f"Schreiboperation nicht erlaubt im Modus {state.mode.value}"
                            )
                            tool_call.result = result
                            # TOOL_RESULT für blockierte Operation
                            yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "success": False,
                                "error": result.error
                            })

                        # Nur wenn Bestätigung erteilt wurde (user oder auto), Operation ausführen
                        if confirmed:
                            # Operation ausführen
                            exec_result = await self._execute_confirmed_operation(
                                result.confirmation_data
                            )

                            # WICHTIG: EventBridge drainten - _emit_workspace_code_change
                            # verwendet emit() was Events in die Queue steckt, nicht yield
                            async for workspace_event in self._drain_mcp_events():
                                yield workspace_event

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
                        elif not blocked_by_mode:
                            # User hat abgelehnt (nicht blockiert durch Mode)
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
                        # blocked_by_mode: Wurde bereits oben behandelt

                        state.pending_confirmation = None

                    else:
                        # Normales Tool-Ergebnis
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "success": result.success,
                            "data": result.to_context()[:2000]  # Truncate für Frontend
                        })

                        # TaskTracker: Tool-Ergebnis als Artifact hinzufügen
                        # Zeigt sinnvolle Zusammenfassung im Zwischenergebnisbereich
                        try:
                            artifact_summary = self._create_tool_result_summary(
                                tool_call.name, tool_call.arguments, result
                            )
                            if artifact_summary:
                                await task_tracker.add_artifact(
                                    processing_task_id,
                                    TaskArtifact(
                                        id=f"res_{tool_call.id[:8]}",
                                        type="tool_result",
                                        summary=artifact_summary,
                                        data={
                                            "tool": tool_call.name,
                                            "success": result.success,
                                        }
                                    )
                                )
                        except Exception:
                            pass  # Task-Tracking-Fehler nicht propagieren

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
                                # WICHTIG: Nur senden wenn KEINE Bestätigung nötig ist!
                                # Bei requires_confirmation=True wurde das Tool noch nicht ausgeführt,
                                # daher wäre der Workspace-Inhalt leer/falsch.
                                if not result.requires_confirmation:
                                    file_path = tool_call.arguments.get("path", "")
                                    # edit_file hat kein "content", nur old_string/new_string
                                    if tool_call.name == "edit_file":
                                        # Für edit_file: Diff aus confirmation_data verwenden falls vorhanden
                                        diff = result.confirmation_data.get("diff", "") if result.confirmation_data else ""
                                        modified_content = diff if diff else result.to_context()[:2000]
                                    else:
                                        content = tool_call.arguments.get("content", "")
                                        modified_content = content[:5000] if content else result.to_context()[:2000]
                                    yield AgentEvent(AgentEventType.WORKSPACE_CODE_CHANGE, {
                                        "filePath": file_path,
                                        "modifiedContent": modified_content,
                                        "toolCall": tool_call.name,
                                        "description": f"{tool_call.name}: {file_path}",
                                        "status": "applied",
                                        "isNew": tool_call.name == "create_file"
                                    })
                            elif tool_call.name in ("github_pr_details", "github_pr_diff"):
                                # PR-Daten für Workspace Panel
                                pr_number = tool_call.arguments.get("pr_number")

                                # Parse result data - handle various formats
                                result_data = {}
                                if hasattr(result, 'data'):
                                    if isinstance(result.data, dict):
                                        result_data = result.data
                                    elif isinstance(result.data, str):
                                        # Fallback: Versuche JSON zu parsen wenn String
                                        try:
                                            result_data = json.loads(result.data)
                                        except (json.JSONDecodeError, TypeError):
                                            logger.warning(f"[agent] PR #{pr_number}: data is string but not JSON")

                                # Repo aus result_data (resolved) oder fallback auf arguments (unresolved)
                                repo = result_data.get("repo") or tool_call.arguments.get("repo", "")

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
                                    # GitHub API kann null bei Berechnung zurückgeben
                                    "additions": result_data.get("additions") or 0,
                                    "deletions": result_data.get("deletions") or 0,
                                    "filesChanged": result_data.get("changed_files") or 0,
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

                                # PR-Analyse starten (läuft als Background-Task)
                                diff_content = result_data.get("diff", "")
                                if diff_content and len(diff_content) > 50:
                                    state.pending_pr_analysis = asyncio.create_task(
                                        _analyze_pr_for_workspace(
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
                # Note: _truncate_result is imported from tool_executor module
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

        # Build usage data and track tokens using helpers
        # Note: At max iterations, we report session totals as the request values
        usage_data = _build_usage_data(
            prompt_tokens=state.total_prompt_tokens,
            completion_tokens=state.total_completion_tokens,
            finish_reason="max_iterations",
            model=model or settings.llm.default_model,
            state=state,
            budget=budget,
        )
        # Override truncated to True for max_iterations
        usage_data["truncated"] = True
        _track_token_usage(
            session_id=session_id,
            model=model or settings.llm.default_model,
            input_tokens=state.total_prompt_tokens,
            output_tokens=state.total_completion_tokens,
            request_type="max_iterations",
        )

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

    def _create_tool_result_summary(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: ToolResult
    ) -> Optional[str]:
        """
        Erstellt eine kurze Zusammenfassung eines Tool-Ergebnisses für den Zwischenergebnisbereich.

        Returns:
            Zusammenfassung oder None wenn nicht sinnvoll
        """
        if not result.success:
            return f"❌ {tool_name}: {result.error[:100] if result.error else 'Fehler'}"

        content = result.to_context()
        content_len = len(content)

        # Such-Tools: Anzahl Treffer
        if tool_name in ("search_code", "search_confluence", "search_jira", "search_handbook"):
            # Versuche Trefferanzahl zu extrahieren
            import re
            match = re.search(r'(\d+)\s*(?:Treffer|Ergebnis|result)', content, re.IGNORECASE)
            if match:
                return f"🔍 {tool_name}: {match.group(1)} Treffer"
            elif "keine" in content.lower() or "not found" in content.lower() or content_len < 50:
                return f"🔍 {tool_name}: Keine Treffer"
            else:
                return f"🔍 {tool_name}: Ergebnisse gefunden"

        # Datei-Operationen
        if tool_name == "read_file":
            path = arguments.get("path", "")
            lines = content.count('\n')
            return f"📄 {path.split('/')[-1]}: {lines} Zeilen"

        if tool_name in ("write_file", "create_file"):
            path = arguments.get("path", "")
            return f"✏️ {path.split('/')[-1]}: Geschrieben"

        if tool_name == "edit_file":
            path = arguments.get("path", "")
            return f"✏️ {path.split('/')[-1]}: Bearbeitet"

        # Confluence/Jira: Zusammenfassung
        if tool_name in ("read_confluence_page", "get_jira_issue"):
            title = ""
            import re
            title_match = re.search(r'[Tt]itle[:\s]+([^\n]+)', content)
            if title_match:
                title = title_match.group(1)[:50]
                return f"📋 {title}"

        # Shell/Bash: Exit-Status
        if tool_name in ("run_shell", "run_bash", "execute_command"):
            if "error" in content.lower() or "failed" in content.lower():
                return f"⚠️ {tool_name}: Mit Warnungen"
            return f"✓ {tool_name}: Erfolgreich"

        # Generischer Fallback für andere Tools
        if content_len > 100:
            return f"✓ {tool_name}: {content_len} Zeichen"

        return None  # Keine Zusammenfassung für triviale Ergebnisse

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

        elif operation == "execute_script":
            # Python-Script ausführen
            from app.agent.script_tools import execute_script_after_confirmation

            script_id = confirmation_data.get("script_id")
            args = confirmation_data.get("args")
            input_data = confirmation_data.get("input_data")

            result = await execute_script_after_confirmation(script_id, args, input_data)

            # Emit workspace event für Script-Ergebnis
            if result.success:
                await self._emit_event(AgentEventType.WORKSPACE_CODE_CHANGE, {
                    "id": str(uuid.uuid4())[:8],
                    "toolCall": "execute_script",
                    "filePath": confirmation_data.get("file_path", f"script_{script_id}.py"),
                    "description": f"Script '{confirmation_data.get('script_name', script_id)}' ausgeführt",
                    "status": "applied",
                    "diff": "",
                    "originalContent": confirmation_data.get("code", ""),
                    "modifiedContent": result.data if result.success else "",
                    "language": "python"
                })

            return result

        else:
            return ToolResult(success=False, error=f"Unbekannte Operation: {operation}")

    # Note: _build_agent_instructions is now imported from context_builder module

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
