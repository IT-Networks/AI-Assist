"""
Tool Executor - Executes tools in parallel or sequential mode.

Handles:
- Parallel execution of read-only tools
- Sequential execution for write tools and MCP
- Tool result caching
- Loop prevention (read/edit/write limits)
- Entity tracking from results
- Stuck detection
- Result validation
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.agent.orchestration.types import (
    AgentEvent,
    AgentEventType,
    AgentState,
    ToolCall,
)
from app.agent.tools import ToolResult
from app.core.config import settings

logger = logging.getLogger(__name__)

# Parallelizable tools (read-only, no side effects)
PARALLELIZABLE_TOOL_PREFIXES = (
    "search_",      # search_code, search_confluence, search_jira, etc.
    "read_",        # read_file, read_confluence_page, etc.
    "get_",         # get_active_repositories, etc.
    "list_",        # list_files, list_database_tables, etc.
    "glob_",        # glob_files
    "github_",      # github_search_code, github_get_file, github_pr_diff, etc.
    "describe_",    # describe_database_table, etc.
    "grep_",        # grep_content
    "batch_read_",  # batch_read_files (read-only meta tool)
    "combined_",    # combined_search (read-only meta tool)
)

# Tools that must NOT be parallelized (writing, confirmations, MCP)
SEQUENTIAL_ONLY_TOOLS = {
    "write_file", "edit_file", "create_file", "batch_write_files",
    "execute_command", "run_sql_query",
    "suggest_answers",  # Requires user interaction
    "sequential_thinking", "seq_think", "analyze",  # Skills
    "research_topic",  # Knowledge Collector (streaming tool, long-running)
    "run_team",        # Multi-Agent Team (streaming tool, long-running)
}

# MCP capability tools
MCP_CAPABILITY_TOOLS = {
    "sequential_thinking", "seq_think",
    "analyze",  # Code analysis as MCP capability
}


def is_parallelizable_tool(tool_name: str) -> bool:
    """Check if a tool can be executed in parallel."""
    if tool_name in SEQUENTIAL_ONLY_TOOLS:
        return False
    if tool_name.startswith("mcp_"):
        return False  # MCP tools always sequential
    return tool_name.startswith(PARALLELIZABLE_TOOL_PREFIXES)


def parse_tool_calls(raw_tool_calls: List[Dict], state: AgentState) -> List[ToolCall]:
    """
    Parse raw tool call dicts into ToolCall objects.

    Args:
        raw_tool_calls: List of raw tool call dicts from LLM
        state: Agent state for ID generation

    Returns:
        List of parsed ToolCall objects
    """
    parsed = []
    for tc in raw_tool_calls:
        raw_args = tc["function"]["arguments"]
        if isinstance(raw_args, str):
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {}
        else:
            parsed_args = raw_args

        parsed.append(ToolCall(
            id=tc.get("id", f"call_{len(state.tool_calls_history)}"),
            name=tc["function"]["name"],
            arguments=parsed_args
        ))

    return parsed


def check_loop_prevention(
    tool_call: ToolCall,
    state: AgentState
) -> Optional[str]:
    """
    Check for loop prevention limits.

    Args:
        tool_call: The tool call to check
        state: Agent state with counters

    Returns:
        Warning message if limit reached, None otherwise
    """
    # read_file max 2x per file
    if tool_call.name == "read_file":
        file_path = tool_call.arguments.get("path", "")
        read_count = state.read_files_this_request.get(file_path, 0)
        if read_count >= 2:
            return (
                f"[HINWEIS] Die Datei '{file_path}' wurde bereits {read_count}x gelesen. "
                "Bitte nutze den bereits erhaltenen Inhalt aus dem Kontext weiter oder "
                "verwende search_code fuer gezielte Suchen."
            )
        state.read_files_this_request[file_path] = read_count + 1

    # edit_file max 2x per file
    if tool_call.name == "edit_file":
        file_path = tool_call.arguments.get("path", "")
        edit_count = state.edit_files_this_request.get(file_path, 0)
        if edit_count >= 2:
            return (
                f"[STOP] Die Datei '{file_path}' wurde bereits {edit_count}x bearbeitet. "
                "Die Aufgabe scheint abgeschlossen zu sein. "
                "Bitte fasse zusammen was du geaendert hast und warte auf weitere Anweisungen vom User."
            )
        state.edit_files_this_request[file_path] = edit_count + 1

    # write_file max 1x per file
    if tool_call.name == "write_file":
        file_path = tool_call.arguments.get("path", "")
        write_count = state.write_files_this_request.get(file_path, 0)
        if write_count >= 1:
            return (
                f"[STOP] Die Datei '{file_path}' wurde bereits geschrieben. "
                "Weitere Schreibvorgaenge sind nicht erlaubt ohne explizite User-Anweisung. "
                "Bitte fasse zusammen was du gemacht hast."
            )
        state.write_files_this_request[file_path] = write_count + 1

    return None


async def execute_tools_parallel(
    tool_calls: List[ToolCall],
    state: AgentState,
    tool_registry: Any,
    tool_cache: Any,
) -> List[ToolResult]:
    """
    Execute multiple read-only tools in parallel.

    Args:
        tool_calls: List of ToolCall objects (all must be parallelizable)
        state: Agent state for caching/budget
        tool_registry: Tool registry for execution
        tool_cache: Tool result cache

    Returns:
        List of ToolResult in same order as tool_calls
    """
    async def execute_single(tc: ToolCall) -> ToolResult:
        # Check cache
        cached = tool_cache.get(tc.name, tc.arguments)
        if cached is not None:
            logger.debug(f"[tool_executor] Cache HIT: {tc.name}")
            if state.tool_budget:
                state.tool_budget.record_tool_call(tc.name, duration_ms=0, cached=True)
            return cached

        # Execute tool
        start = time.time()
        try:
            result = await tool_registry.execute(tc.name, **tc.arguments)
        except Exception as e:
            logger.warning(f"[tool_executor] Tool {tc.name} failed: {e}")
            result = ToolResult(success=False, error=str(e))

        duration_ms = int((time.time() - start) * 1000)

        # Cache and track budget
        if result.success:
            tool_cache.set(tc.name, tc.arguments, result)
        if state.tool_budget:
            state.tool_budget.record_tool_call(tc.name, duration_ms=duration_ms, cached=False)

        logger.debug(f"[tool_executor] {tc.name} completed in {duration_ms}ms")
        return result

    # Execute all in parallel
    logger.info(f"[tool_executor] Executing {len(tool_calls)} tools in parallel: {[tc.name for tc in tool_calls]}")
    batch_start = time.time()

    results = await asyncio.gather(*[execute_single(tc) for tc in tool_calls])

    batch_duration = int((time.time() - batch_start) * 1000)
    logger.info(f"[tool_executor] Batch completed in {batch_duration}ms")

    return list(results)


async def execute_tool_sequential(
    tool_call: ToolCall,
    state: AgentState,
    tool_registry: Any,
    tool_cache: Any,
    mcp_bridge: Any = None,
    event_bridge: Any = None,
    analytics: Any = None,
) -> Tuple[ToolResult, AsyncGenerator[AgentEvent, None]]:
    """
    Execute a single tool sequentially.

    Args:
        tool_call: The tool call to execute
        state: Agent state
        tool_registry: Tool registry
        tool_cache: Tool result cache
        mcp_bridge: MCP bridge for MCP tools
        event_bridge: Event bridge for MCP events
        analytics: Analytics logger

    Returns:
        Tuple of (result, event_generator)
    """
    events = []

    # MCP tools
    if tool_call.name.startswith("mcp_") or tool_call.name in MCP_CAPABILITY_TOOLS:
        if mcp_bridge is None:
            return ToolResult(success=False, error="MCP bridge not available"), _empty_generator()

        # Subscribe to event bridge
        mcp_queue = event_bridge.subscribe()

        try:
            # Execute in separate task for live streaming
            tool_task = asyncio.create_task(
                mcp_bridge.call_tool(tool_call.name, tool_call.arguments)
            )

            # Stream events while tool runs
            while not tool_task.done():
                async for event in _drain_mcp_events(mcp_queue):
                    events.append(event)
                await asyncio.sleep(0.05)

            mcp_result = await tool_task

            # Final events
            async for event in _drain_mcp_events(mcp_queue, timeout=0.1):
                events.append(event)

        finally:
            event_bridge.unsubscribe(mcp_queue)

        result = ToolResult(
            success=mcp_result.get("success", False),
            data=mcp_result.get("result") or mcp_result.get("formatted_output") or mcp_result,
            error=mcp_result.get("error")
        )
        return result, _list_to_generator(events)

    # Standard tools with caching
    cached = tool_cache.get(tool_call.name, tool_call.arguments)
    if cached is not None:
        logger.debug(f"[tool_executor] Cache HIT: {tool_call.name}")
        if state.tool_budget:
            state.tool_budget.record_tool_call(tool_call.name, duration_ms=0, cached=True)
        return cached, _empty_generator()

    # Execute tool
    start = time.time()
    result = await tool_registry.execute(tool_call.name, **tool_call.arguments)
    duration_ms = int((time.time() - start) * 1000)

    # Cache successful results
    if result.success:
        tool_cache.set(tool_call.name, tool_call.arguments, result)

    # Budget tracking
    if state.tool_budget:
        state.tool_budget.record_tool_call(tool_call.name, duration_ms=duration_ms, cached=False)

    # Analytics
    if analytics and analytics.enabled:
        try:
            await analytics.log_tool_execution(
                tool_name=tool_call.name,
                success=result.success,
                duration_ms=duration_ms,
                error=result.error,
                result_size=len(str(result.data or "")),
            )
        except Exception:
            pass

    return result, _empty_generator()


def truncate_result(raw: str, max_chars: int = 20000, tool_name: str = "") -> str:
    """
    Truncate a tool result to fit context limits.

    Handles special cases:
    - PR tools: Minimal info for main LLM (analysis runs in workspace)
    - General tools: Keep beginning and end for context

    Args:
        raw: Raw result string
        max_chars: Maximum characters
        tool_name: Tool name for context-aware truncation

    Returns:
        Truncated string
    """
    if not raw:
        return raw

    # Streaming tools (run_team, research_topic): Kompaktere Ergebnisse
    # Diese Tools liefern bereits synthetisierte Ergebnisse, brauchen weniger Context
    if tool_name in ("run_team", "research_topic"):
        if len(raw) > 5000:
            return raw[:4500] + f"\n\n[...{len(raw) - 4500} Zeichen gekuerzt ({tool_name})...]"
        return raw

    # PR tools: Minimal info for main LLM (analysis runs in workspace panel)
    if tool_name in ("github_pr_details", "github_pr_diff"):
        lines = raw.split("\n")[:15]  # First 15 lines (metadata only)
        summary = "\n".join(lines)
        return summary + "\n\n[INFO: PR-Diff wird im Workspace-Panel analysiert. Keine Chat-Analyse nötig.]"

    # No truncation needed
    if len(raw) <= max_chars:
        return raw

    # Keep beginning and end for context
    keep_start = int(max_chars * 0.7)
    keep_end = int(max_chars * 0.2)
    truncated_chars = len(raw) - keep_start - keep_end

    return (
        raw[:keep_start] +
        f"\n\n[... {truncated_chars} Zeichen gekuerzt ({tool_name}) ...]\n\n" +
        raw[-keep_end:]
    )


async def _drain_mcp_events(
    queue: asyncio.Queue,
    timeout: float = 0.01
) -> AsyncGenerator[AgentEvent, None]:
    """Drain MCP events from queue."""
    while True:
        try:
            event_data = queue.get_nowait()
            event_type_str = event_data.get("type", "")
            try:
                event_type = AgentEventType(event_type_str)
                yield AgentEvent(event_type, event_data.get("data", {}))
            except ValueError:
                logger.debug(f"[tool_executor] Unknown event type: {event_type_str}")
        except asyncio.QueueEmpty:
            break


async def _empty_generator() -> AsyncGenerator[AgentEvent, None]:
    """Empty async generator."""
    return
    yield  # Makes this a generator


async def _list_to_generator(events: List[AgentEvent]) -> AsyncGenerator[AgentEvent, None]:
    """Convert list of events to async generator."""
    for event in events:
        yield event
