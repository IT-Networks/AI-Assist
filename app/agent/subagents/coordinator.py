"""
Sub-Agent Coordinator - Runs multiple SubAgentWorkers in parallel.

Accepts a list of SubAgentTasks, spawns workers via asyncio.gather, collects
results, and propagates progress events through an optional callback channel.

Key guarantees:
- Failure of one worker does NOT cancel others (return_exceptions=True)
- Results returned in the same order as input tasks
- Global timeout (max_total_seconds) protects against runaway coordinators
- Concurrency limited by semaphore (max_workers)
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, List, Optional

from app.agent.orchestration.types import AgentEvent, AgentEventType
from app.agent.subagents.models import (
    SubAgentConfig,
    SubAgentResult,
    SubAgentStatus,
    SubAgentTask,
)
from app.agent.subagents.worker import SubAgentWorker

logger = logging.getLogger(__name__)


class SubAgentCoordinator:
    """
    Orchestrates parallel execution of SubAgentTasks via SubAgentWorkers.

    One coordinator instance per request (holds per-request config). Does NOT
    maintain state between calls — each call to coordinate() is independent.
    """

    def __init__(self, orchestrator) -> None:  # AgentOrchestrator typed as Any
        self.orchestrator = orchestrator

    async def coordinate(
        self,
        tasks: List[SubAgentTask],
        config: SubAgentConfig,
        model: Optional[str] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Execute all tasks in parallel and yield progress/completion events.

        Yields AgentEvents so callers (e.g. ContinuationController) can stream
        them to the UI. The final event is always SUBAGENT_DONE with the
        aggregated results.

        Args:
            tasks: List of tasks to execute in parallel
            config: Sub-agent configuration (max_workers, timeout, etc.)
            model: Optional LLM model for all workers

        Yields:
            AgentEvents — SUBAGENT_START, SUBAGENT_DONE, SUBAGENT_ERROR,
            and a final COORDINATOR_DONE with results.
        """
        if not tasks:
            logger.debug("[coordinator] No tasks to coordinate")
            yield AgentEvent(
                AgentEventType.MCP_COMPLETE,
                {
                    "source": "subagents",
                    "event": "coordinator_done",
                    "results": [],
                    "total": 0,
                },
            )
            return

        # Enforce max_workers cap
        effective_tasks = tasks[: config.max_workers]
        if len(effective_tasks) < len(tasks):
            logger.warning(
                f"[coordinator] Truncated {len(tasks)} tasks to {config.max_workers} workers"
            )

        # Emit subagent_start event with the task list (UI can render cards)
        yield AgentEvent(
            AgentEventType.SUBAGENT_START,
            {
                "source": "subagents",
                "event": "coordinator_started",
                "task_count": len(effective_tasks),
                "tasks": [
                    {"task_id": t.task_id, "description": t.description[:200]}
                    for t in effective_tasks
                ],
            },
        )

        # Semaphore limits concurrency (redundant with max_workers cap above, but
        # safe if caller bypasses cap)
        semaphore = asyncio.Semaphore(config.max_workers)
        worker = SubAgentWorker(orchestrator=self.orchestrator)

        async def _run_one(task: SubAgentTask) -> SubAgentResult:
            async with semaphore:
                return await worker.execute(
                    task=task,
                    timeout_seconds=config.worker_timeout_seconds,
                    model=model,
                )

        # Launch all tasks concurrently. return_exceptions=True ensures one
        # failure doesn't cancel others — each failure becomes a result.
        try:
            raw_results = await asyncio.gather(
                *[_run_one(t) for t in effective_tasks],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            logger.info("[coordinator] Coordination cancelled")
            raise

        # Normalize: convert exceptions into FAILED SubAgentResults
        results: List[SubAgentResult] = []
        for task, raw in zip(effective_tasks, raw_results):
            if isinstance(raw, SubAgentResult):
                results.append(raw)
            elif isinstance(raw, BaseException):
                logger.exception(f"[coordinator] Task {task.task_id} raised unexpected exception")
                results.append(
                    SubAgentResult(
                        task_id=task.task_id,
                        description=task.description,
                        status=SubAgentStatus.FAILED,
                        error=f"Unexpected exception: {raw!r}",
                    )
                )
            else:
                # Shouldn't happen — defensive
                results.append(
                    SubAgentResult(
                        task_id=task.task_id,
                        description=task.description,
                        status=SubAgentStatus.FAILED,
                        error=f"Unexpected result type: {type(raw).__name__}",
                    )
                )

        # Emit per-result events
        for r in results:
            event_type = (
                AgentEventType.SUBAGENT_DONE
                if r.is_success
                else AgentEventType.SUBAGENT_ERROR
            )
            yield AgentEvent(
                event_type,
                {
                    "source": "subagents",
                    "task_id": r.task_id,
                    "description": r.description[:200],
                    "status": r.status.value,
                    "elapsed_seconds": r.elapsed_seconds,
                    "tool_calls_count": r.tool_calls_count,
                    "error": r.error,
                    "response_preview": r.response[:300] if r.response else "",
                },
            )

        # Final coordinator_done event with all results
        success_count = sum(1 for r in results if r.is_success)
        yield AgentEvent(
            AgentEventType.MCP_COMPLETE,
            {
                "source": "subagents",
                "event": "coordinator_done",
                "total": len(results),
                "success": success_count,
                "failures": len(results) - success_count,
                "results": [
                    {
                        "task_id": r.task_id,
                        "description": r.description,
                        "status": r.status.value,
                        "response": r.response,
                        "error": r.error,
                        "elapsed_seconds": r.elapsed_seconds,
                    }
                    for r in results
                ],
            },
        )

        logger.info(
            f"[coordinator] Coordination complete: {success_count}/{len(results)} succeeded"
        )
