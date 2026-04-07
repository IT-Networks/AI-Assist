"""
Phase Runner - Runs agent phases before main loop.

Handles:
- Research phase (parallel source search)
- Sub-agent phase (specialized agents)
- Task decomposition phase
- Forced capability execution
"""

import asyncio
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.agent.orchestration.types import (
    AgentEvent,
    AgentEventType,
    AgentState,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

# Research trigger patterns
_RE_QUESTION = re.compile(r'\?|wie\s|was\s|warum\s|wo\s|wer\s|wann\s', re.IGNORECASE)
_RE_KEYWORDS = re.compile(
    r'erkl[aä]r|beschreib|zeig|find|such|analys|vergleich|'
    r'unterschied|zusammenfass|dokumentation|spezifikation',
    re.IGNORECASE
)


def should_auto_research(query: str) -> bool:
    """
    Check if a query should trigger auto-research.

    Args:
        query: User query to check

    Returns:
        True if research should be triggered
    """
    # Sub-Agents deaktiviert in v2.31.5 — ersetzt durch Multi-Agent Teams
    return False
    if not settings.sub_agents.enabled:
        return False

    # Check for question marks or question words
    if _RE_QUESTION.search(query):
        return True

    # Check for research-triggering keywords
    if _RE_KEYWORDS.search(query):
        return True

    return False


async def run_research_phase(
    user_message: str,
    messages: List[Dict],
    budget: Any,
    research_capability: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Run the research phase for parallel source search.

    Args:
        user_message: User's message
        messages: Current message list
        budget: Token budget
        research_capability: Research capability instance

    Yields:
        AgentEvent objects during research
    """
    if not research_capability:
        return

    try:
        logger.debug("[phase_runner] Starting research phase")

        yield AgentEvent(AgentEventType.MCP_START, {
            "mode": "research",
            "message": "Suche in Quellen...",
        })

        # Run research
        result = await research_capability.research(
            query=user_message,
            max_results=5,
            include_web=False  # Only internal sources initially
        )

        if result.get("findings"):
            # Add findings to context
            findings_text = "\n".join([
                f"- {f.get('title', '')}: {f.get('summary', '')}"
                for f in result["findings"][:3]
            ])
            messages.append({
                "role": "system",
                "content": f"## Research-Ergebnisse\n{findings_text}"
            })

            yield AgentEvent(AgentEventType.MCP_COMPLETE, {
                "mode": "research",
                "count": len(result["findings"]),
            })
        else:
            yield AgentEvent(AgentEventType.MCP_COMPLETE, {
                "mode": "research",
                "count": 0,
                "message": "Keine relevanten Quellen gefunden"
            })

    except Exception as e:
        logger.warning(f"[phase_runner] Research phase failed: {e}")
        yield AgentEvent(AgentEventType.MCP_ERROR, {
            "mode": "research",
            "error": str(e)
        })


async def run_sub_agents_phase(
    user_message: str,
    model: Optional[str],
    messages: List[Dict],
    budget: Any,
    sub_agent_coordinator: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Run the sub-agent phase for specialized exploration.

    Args:
        user_message: User's message
        model: LLM model to use
        messages: Current message list
        budget: Token budget
        sub_agent_coordinator: Sub-agent coordinator instance

    Yields:
        AgentEvent objects during sub-agent execution
    """
    if not sub_agent_coordinator:
        return

    try:
        logger.debug("[phase_runner] Starting sub-agent phase")

        yield AgentEvent(AgentEventType.SUBAGENT_START, {
            "message": "Starte spezialisierte Agenten...",
        })

        # Route to appropriate sub-agents
        routing = await sub_agent_coordinator.route(user_message)

        if routing.agents:
            yield AgentEvent(AgentEventType.SUBAGENT_ROUTING, {
                "agents": [a.name for a in routing.agents],
                "reasoning": routing.reasoning,
            })

            # Execute sub-agents
            results = await sub_agent_coordinator.execute(
                user_message,
                routing.agents,
                model=model
            )

            for result in results:
                if result.success:
                    yield AgentEvent(AgentEventType.SUBAGENT_DONE, {
                        "agent": result.agent_name,
                        "summary": result.summary[:200] if result.summary else "",
                    })

                    # Add to context if relevant
                    if result.context_injection:
                        messages.append({
                            "role": "system",
                            "content": result.context_injection
                        })
                else:
                    yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {
                        "agent": result.agent_name,
                        "error": result.error,
                    })

    except Exception as e:
        logger.warning(f"[phase_runner] Sub-agent phase failed: {e}")
        yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {
            "error": str(e),
        })


async def run_task_decomposition(
    user_message: str,
    session_id: str,
    enriched_context: Optional[str],
    state: AgentState,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Run task decomposition for complex queries.

    Args:
        user_message: User's message
        session_id: Session ID
        enriched_context: Optional enriched context from enhancement
        state: Agent state

    Yields:
        AgentEvent objects during task decomposition
        Returns early if task decomposition handles the request
    """
    if not settings.task_agents.enabled:
        return

    try:
        from app.agent.task_integration import (
            should_use_task_decomposition,
            process_with_tasks,
            format_task_response_for_user,
            format_clarification_questions,
            TaskEventType
        )

        if not await should_use_task_decomposition(user_message):
            return

        logger.info("[phase_runner] Using Task-Decomposition for complex query")

        yield AgentEvent(AgentEventType.MCP_START, {
            "mode": "task_planning",
            "message": "Erstelle Ausfuehrungsplan...",
            "query_preview": user_message[:100]
        })

        # Process with tasks
        async for task_event in process_with_tasks(
            user_message=user_message,
            session_id=session_id,
            context=enriched_context,
            project_id=state.project_id if hasattr(state, 'project_id') else None,
            project_path=state.project_path if hasattr(state, 'project_path') else None
        ):
            event_type = task_event.get("type", "")
            event_data = task_event.get("data", {})

            if event_type == "planning_start":
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
                questions = event_data.get("questions", [])
                formatted = format_clarification_questions(questions)
                yield AgentEvent(AgentEventType.TOKEN, formatted)
                yield AgentEvent(AgentEventType.TASK_CLARIFICATION, event_data)
                yield AgentEvent(AgentEventType.DONE, None)
                return  # Early return - waiting for user

            elif event_type == TaskEventType.EXECUTION_COMPLETE:
                response = format_task_response_for_user(event_data)
                yield AgentEvent(AgentEventType.TOKEN, response)
                yield AgentEvent(AgentEventType.TASK_EXECUTION_DONE, event_data)
                state.messages_history.append({
                    "role": "assistant",
                    "content": response
                })
                yield AgentEvent(AgentEventType.DONE, None)
                return  # Early return - done

            elif event_type == "use_direct_processing":
                logger.debug("[phase_runner] Task count below threshold, using direct processing")
                return  # Continue with normal processing

            elif event_type == "error":
                yield AgentEvent(AgentEventType.ERROR, event_data)
                yield AgentEvent(AgentEventType.DONE, None)
                return  # Early return on error

    except ImportError as e:
        logger.warning(f"[phase_runner] Task-Decomposition not available: {e}")
    except asyncio.CancelledError:
        logger.info("[phase_runner] Task-Decomposition cancelled")
        raise
    except Exception as e:
        logger.error(f"[phase_runner] Task-Decomposition failed: {e}")
        yield AgentEvent(AgentEventType.ERROR, {
            "source": "task_decomposition",
            "error": str(e),
            "message": "Task-Zerlegung fehlgeschlagen - fahre mit direkter Verarbeitung fort"
        })


async def run_forced_capability(
    capability: str,
    user_message: str,
    mcp_bridge: Any,
    event_bridge: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Execute a forced MCP capability.

    Args:
        capability: Capability name to execute
        user_message: User's message
        mcp_bridge: MCP bridge instance
        event_bridge: Event bridge for streaming

    Yields:
        AgentEvent objects during execution
    """
    logger.debug(f"[phase_runner] Executing forced capability: {capability}")

    yield AgentEvent(AgentEventType.TOOL_START, {
        "id": f"forced_{capability}",
        "name": capability,
        "arguments": {"query": user_message},
        "model": "MCP"
    })

    try:
        # Subscribe to event bridge
        mcp_queue = event_bridge.subscribe()

        try:
            # Execute capability in separate task
            tool_task = asyncio.create_task(
                mcp_bridge.call_tool(
                    capability,
                    {"query": user_message, "context": None}
                )
            )

            # Stream events while running
            while not tool_task.done():
                async for event in _drain_mcp_events(mcp_queue):
                    yield event
                await asyncio.sleep(0.05)

            mcp_result = await tool_task

            # Final events
            async for event in _drain_mcp_events(mcp_queue, timeout=0.1):
                yield event

        finally:
            event_bridge.unsubscribe(mcp_queue)

        # Format result
        if mcp_result.get("success"):
            output = mcp_result.get("formatted_output") or mcp_result.get("result") or str(mcp_result)

            yield AgentEvent(AgentEventType.TOOL_RESULT, {
                "id": f"forced_{capability}",
                "name": capability,
                "success": True,
                "data": output[:500] if len(output) > 500 else output
            })

            # Stream output
            for chunk in output.split('\n'):
                yield AgentEvent(AgentEventType.TOKEN, chunk + '\n')

            # Handoff suggestion
            next_cap = mcp_result.get("next_capability")
            if next_cap:
                yield AgentEvent(AgentEventType.TOKEN, f"\n\n---\n-> **Naechster Schritt:** `/{next_cap}` fuer die Weiterfuehrung\n")

        else:
            error_msg = mcp_result.get("error", "Unbekannter Fehler")
            yield AgentEvent(AgentEventType.TOOL_RESULT, {
                "id": f"forced_{capability}",
                "name": capability,
                "success": False,
                "data": error_msg
            })
            yield AgentEvent(AgentEventType.ERROR, {
                "error": f"Capability {capability} fehlgeschlagen: {error_msg}"
            })

    except Exception as e:
        yield AgentEvent(AgentEventType.ERROR, {
            "error": f"Capability-Ausfuehrung fehlgeschlagen: {str(e)}"
        })

    yield AgentEvent(AgentEventType.DONE, {})


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
                logger.debug(f"[phase_runner] Unknown event type: {event_type_str}")
        except asyncio.QueueEmpty:
            break
