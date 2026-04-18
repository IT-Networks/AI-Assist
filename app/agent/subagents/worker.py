"""
Sub-Agent Worker - Executes a single SubAgentTask via orchestrator.process().

Runs in isolated session (unique session_id) so parallel workers don't share
context or tool-call history. Collects streaming events and extracts final
response.

Design:
- One worker handles ONE task, then returns (no loop)
- Uses existing orchestrator with its own session_id → full tool access
- Respects timeout via asyncio.wait_for
- Returns SubAgentResult regardless of success/failure (no exceptions escape)
"""

import asyncio
import logging
import time
from typing import Any, Optional

from app.agent.orchestration.types import AgentEventType
from app.agent.subagents.models import SubAgentResult, SubAgentStatus, SubAgentTask

logger = logging.getLogger(__name__)


class SubAgentWorker:
    """
    Executes a single SubAgentTask using an AgentOrchestrator.

    Workers are stateless besides the injected orchestrator — reusable for
    multiple tasks in sequence, but each task should get a fresh worker call.
    """

    def __init__(self, orchestrator) -> None:  # AgentOrchestrator typed as Any to avoid circular import
        self.orchestrator = orchestrator

    async def execute(
        self,
        task: SubAgentTask,
        timeout_seconds: float = 60.0,
        model: Optional[str] = None,
    ) -> SubAgentResult:
        """
        Execute a single sub-agent task.

        Args:
            task: SubAgentTask to execute
            timeout_seconds: Hard timeout — worker cancelled if exceeded
            model: Optional LLM model override

        Returns:
            SubAgentResult with status, response, and metrics.
            Never raises — all exceptions captured into result.error.
        """
        task.status = SubAgentStatus.RUNNING
        worker_session_id = task.worker_session_id()
        start_time = time.time()
        logger.info(
            f"[subagent_worker] Starting task={task.task_id} "
            f"session={worker_session_id} desc={task.description[:60]!r}"
        )

        response_parts: list[str] = []
        event_count = 0
        tool_calls = 0
        error: Optional[str] = None
        status: SubAgentStatus = SubAgentStatus.RUNNING

        async def _run() -> None:
            """Inner coroutine that consumes orchestrator events."""
            nonlocal event_count, tool_calls
            gen = self.orchestrator.process(
                session_id=worker_session_id,
                user_message=task.description,
                model=model,
            )
            async for event in gen:
                event_count += 1
                if event.type == AgentEventType.TOKEN:
                    if isinstance(event.data, str):
                        response_parts.append(event.data)
                elif event.type == AgentEventType.TOOL_START:
                    tool_calls += 1
                elif event.type == AgentEventType.DONE:
                    if isinstance(event.data, dict):
                        final = event.data.get("response", "")
                        if final and not response_parts:
                            response_parts.append(final)
                elif event.type == AgentEventType.CONFIRM_REQUIRED:
                    # Workers can't confirm — abort this task
                    logger.warning(
                        f"[subagent_worker] Task {task.task_id} needs confirmation, aborting"
                    )
                    raise RuntimeError("Sub-agent task requires user confirmation (not supported)")
                elif event.type == AgentEventType.ERROR:
                    err_msg = (
                        event.data.get("error", "unknown error")
                        if isinstance(event.data, dict)
                        else str(event.data)
                    )
                    raise RuntimeError(f"Orchestrator error: {err_msg}")

        try:
            await asyncio.wait_for(_run(), timeout=timeout_seconds)
            status = SubAgentStatus.COMPLETED
        except asyncio.TimeoutError:
            status = SubAgentStatus.TIMEOUT
            error = f"Worker exceeded {timeout_seconds}s timeout"
            logger.warning(f"[subagent_worker] Task {task.task_id} timed out after {timeout_seconds}s")
            # Cancel the underlying orchestrator request
            try:
                self.orchestrator.cancel_request(worker_session_id)
            except Exception as cancel_err:  # noqa: BLE001
                logger.debug(f"[subagent_worker] Cancel failed: {cancel_err}")
        except asyncio.CancelledError:
            status = SubAgentStatus.CANCELLED
            error = "Worker cancelled"
            try:
                self.orchestrator.cancel_request(worker_session_id)
            except Exception:  # noqa: BLE001
                pass
            raise  # Propagate to coordinator
        except Exception as e:  # noqa: BLE001 - capture all errors
            status = SubAgentStatus.FAILED
            error = str(e)
            logger.exception(f"[subagent_worker] Task {task.task_id} failed")

        task.status = status
        elapsed = time.time() - start_time
        full_response = "".join(response_parts).strip()

        result = SubAgentResult(
            task_id=task.task_id,
            description=task.description,
            status=status,
            response=full_response,
            error=error,
            tool_calls_count=tool_calls,
            elapsed_seconds=elapsed,
            event_count=event_count,
        )

        logger.info(
            f"[subagent_worker] Task {task.task_id} finished status={status.value} "
            f"elapsed={elapsed:.1f}s tool_calls={tool_calls} event_count={event_count}"
        )

        return result
