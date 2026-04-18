"""
Continuation Controller - Wraps AgentOrchestrator in an iteration loop.

Executes multiple iterations of orchestrator.process() until completion
is detected (Promise Tag) or safety valves trigger (max_iter / timeout).

Phase 1 (MVP):
- Tier 1 (Promise Tag) + Tier 3 (Timeout/MaxIter) completion
- Stream-through: All AgentEvents are forwarded to caller unchanged
- Between iterations: Inject continuation message so LLM knows to continue

Phase 3+ will add:
- Tier 2 (Criteria matching via TaskClassifier)
- Drift Monitor integration

Key design properties:
- Orchestrator remains UNCHANGED (opt-in wrapper)
- Streaming: AgentEvents flow through without buffering
- Cancellation: Respects existing orchestrator.cancel_request()
- Safety: Hard limits prevent infinite loops
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

from app.agent.continuation.completion_detector import (
    CompletionDetector,
    get_completion_detector,
)
from app.agent.continuation.drift_monitor import DriftMonitor, get_drift_monitor
from app.agent.continuation.models import (
    CompletionReason,
    ContinuationConfig,
    DriftRiskLevel,
    IterationState,
    TaskType,
)
from app.agent.continuation.system_prompt import get_continuation_system_prompt
from app.agent.continuation.task_classifier import classify_task
from app.agent.orchestration.types import AgentEvent, AgentEventType

logger = logging.getLogger(__name__)


# Continuation nudge message sent to orchestrator between iterations.
# Kept short to minimize token waste.
_CONTINUATION_NUDGE = (
    "[SYSTEM: Continuation iteration] "
    "Task ist noch nicht abgeschlossen. Arbeite weiter. "
    "Wenn fertig: <promise>Task: ... Status: COMPLETE. Result: ...</promise>"
)


class ContinuationController:
    """
    Orchestrates multi-iteration agent loops with completion detection.

    Does NOT subclass AgentOrchestrator — wraps it. This keeps orchestrator
    unchanged and makes continuation a pure opt-in feature.
    """

    def __init__(
        self,
        orchestrator,  # AgentOrchestrator — typed as Any to avoid circular import
        completion_detector: Optional[CompletionDetector] = None,
        drift_monitor: Optional[DriftMonitor] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.completion_detector = completion_detector or get_completion_detector()
        self.drift_monitor = drift_monitor or get_drift_monitor()

    async def execute_with_continuation(
        self,
        session_id: str,
        user_message: str,
        config: ContinuationConfig,
        model: Optional[str] = None,
        context_selection=None,
        attachments=None,
        tts: Optional[bool] = None,
        channel_hint: Optional[str] = None,
        channel_context=None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Run orchestrator.process() in a loop until task completion detected.

        Streams all AgentEvents through to caller. After each iteration,
        checks completion via CompletionDetector. If not complete and limits
        not reached, injects nudge message and runs another iteration.

        Args:
            session_id: Session identifier
            user_message: Original user message (iteration 1)
            config: ContinuationConfig with limits and flags
            model: Optional LLM model
            context_selection, attachments, tts, channel_hint, channel_context:
                Passed through to orchestrator.process()

        Yields:
            AgentEvents from underlying orchestrator, plus continuation
            metadata events (iteration_started, iteration_complete,
            continuation_complete).
        """
        # Phase 3: Classify task type for Tier 2 criteria matching
        task_type, task_confidence = classify_task(user_message)

        state = IterationState(
            session_id=session_id,
            original_goal=user_message,
            task_type=task_type,
        )

        logger.info(
            f"[continuation] Classified task session={session_id} "
            f"type={task_type.value} confidence={task_confidence:.2f}"
        )

        # Phase 5: Try sub-agent decomposition if enabled
        subagent_cfg = self._parse_subagent_config(config)
        if subagent_cfg is not None and subagent_cfg.enabled:
            async for sub_event in self._try_subagent_path(
                session_id=session_id,
                user_message=user_message,
                subagent_config=subagent_cfg,
                model=model,
                state=state,
            ):
                yield sub_event
            if state.termination_reason is not None:
                # Sub-agent path handled the whole request; don't run main loop
                yield AgentEvent(
                    AgentEventType.MCP_COMPLETE,
                    {
                        "source": "continuation",
                        "event": "continuation_complete",
                        "total_iterations": 0,
                        "elapsed_seconds": state.elapsed_seconds,
                        "reason": state.termination_reason.value,
                        "tool_calls_count": state.tool_calls_count,
                        "subagents_used": True,
                    },
                )
                return

        # Inject Promise Tag instruction into first iteration message.
        # Uses SYSTEM-prefix so LLM treats it as meta-instruction, not user content.
        first_message = (
            f"{get_continuation_system_prompt()}\n\n"
            f"---\n\n"
            f"**USER-ANFRAGE:**\n{user_message}"
        )
        current_message = first_message
        completion = None

        logger.info(
            f"[continuation] Starting loop session={session_id} "
            f"max_iter={config.max_iterations} max_sec={config.max_seconds}"
        )

        try:
            while state.iteration < config.max_iterations:
                state.iteration += 1

                # Emit iteration-start event
                yield AgentEvent(
                    AgentEventType.MCP_PROGRESS,
                    {
                        "source": "continuation",
                        "event": "iteration_started",
                        "iteration": state.iteration,
                        "max_iterations": config.max_iterations,
                        "elapsed_seconds": state.elapsed_seconds,
                    },
                )

                # Check overall timeout BEFORE running iteration (avoid wasting call)
                if state.elapsed_seconds >= config.max_seconds:
                    logger.warning(
                        f"[continuation] Pre-iteration timeout session={session_id} "
                        f"elapsed={state.elapsed_seconds:.1f}s"
                    )
                    completion = self.completion_detector.check(
                        state.last_response(),
                        state,
                        max_iterations=config.max_iterations,
                        max_seconds=config.max_seconds,
                    )
                    break

                # Collect response tokens from this iteration for completion check
                iteration_response_parts: list[str] = []

                # Run one orchestrator iteration, forward all events
                try:
                    gen = self.orchestrator.process(
                        session_id=session_id,
                        user_message=current_message,
                        model=model,
                        context_selection=context_selection,
                        attachments=attachments,
                        tts=tts,
                        channel_hint=channel_hint,
                        channel_context=channel_context,
                    )

                    async for event in gen:
                        # Forward event unchanged
                        yield event

                        # Accumulate response text for completion check
                        if event.type == AgentEventType.TOKEN:
                            if isinstance(event.data, str):
                                iteration_response_parts.append(event.data)
                        elif event.type == AgentEventType.DONE:
                            if isinstance(event.data, dict):
                                resp = event.data.get("response", "")
                                if resp and not iteration_response_parts:
                                    # DONE may carry full response if TOKEN events were skipped
                                    iteration_response_parts.append(resp)

                        # Tool calls counter + signature tracking for drift analysis
                        if event.type == AgentEventType.TOOL_START:
                            tool_name = ""
                            tool_args: dict = {}
                            if isinstance(event.data, dict):
                                tool_name = event.data.get("name", "")
                                args_raw = event.data.get("arguments", {})
                                if isinstance(args_raw, dict):
                                    tool_args = args_raw
                            state.record_tool_call(tool_name or "unknown", tool_args, success=True)
                        elif event.type == AgentEventType.TOOL_RESULT:
                            # Update last call's success status if provided
                            if (
                                isinstance(event.data, dict)
                                and event.data.get("success") is False
                                and state.tool_calls_count > 0
                            ):
                                state.failed_tool_calls += 1

                        # If orchestrator needs confirmation, exit loop and return control
                        if event.type == AgentEventType.CONFIRM_REQUIRED:
                            logger.info(
                                f"[continuation] Orchestrator requires confirmation, "
                                f"exiting loop session={session_id} iter={state.iteration}"
                            )
                            state.termination_reason = CompletionReason.USER_INTERRUPT
                            return

                except asyncio.CancelledError:
                    logger.info(f"[continuation] Cancelled session={session_id}")
                    state.termination_reason = CompletionReason.USER_INTERRUPT
                    raise

                except Exception as e:  # noqa: BLE001 - need to catch all to emit error event
                    logger.exception(f"[continuation] Iteration error session={session_id}: {e}")
                    state.termination_reason = CompletionReason.ERROR
                    yield AgentEvent(
                        AgentEventType.ERROR,
                        {"error": f"Continuation iteration failed: {e}"},
                    )
                    return

                # Build full response from this iteration
                iteration_response = "".join(iteration_response_parts)
                state.add_response(iteration_response)

                # Phase 4: Drift evaluation (observer — non-blocking by default)
                drift_assessment = None
                if config.enable_drift_monitoring:
                    try:
                        drift_assessment = self.drift_monitor.evaluate(state, iteration_response)
                        state.last_drift_assessment = drift_assessment
                        if drift_assessment.risk_level != DriftRiskLevel.LOW:
                            state.drift_warnings += 1
                            yield AgentEvent(
                                AgentEventType.MCP_PROGRESS,
                                {
                                    "source": "continuation",
                                    "event": "drift_detected",
                                    "iteration": state.iteration,
                                    **drift_assessment.to_dict(),
                                },
                            )
                    except Exception as drift_err:
                        logger.warning(f"[continuation] Drift evaluation failed: {drift_err}")

                # Check completion (Tier 1 → Tier 2 → Tier 3)
                completion = self.completion_detector.check(
                    iteration_response,
                    state,
                    max_iterations=config.max_iterations,
                    max_seconds=config.max_seconds,
                    task_type=task_type,
                    require_promise_tag=config.require_promise_tag,
                )

                # Emit iteration-complete event with completion + drift status
                iteration_complete_data = {
                    "source": "continuation",
                    "event": "iteration_complete",
                    "iteration": state.iteration,
                    "task_type": task_type.value,
                    "is_complete": completion.is_complete,
                    "reason": completion.reason.value if completion.reason else None,
                    "confidence": completion.confidence,
                    "tier": completion.tier,
                    "elapsed_seconds": state.elapsed_seconds,
                }
                if drift_assessment is not None:
                    iteration_complete_data["drift"] = drift_assessment.to_dict()

                yield AgentEvent(AgentEventType.MCP_PROGRESS, iteration_complete_data)

                # Phase 4: Optional drift-stop gate
                if (
                    config.stop_on_high_drift
                    and drift_assessment is not None
                    and drift_assessment.risk_level == DriftRiskLevel.HIGH
                    and not completion.is_complete
                ):
                    logger.warning(
                        f"[continuation] Stopping on HIGH drift session={session_id} "
                        f"iter={state.iteration} reasons={drift_assessment.reasons}"
                    )
                    state.termination_reason = CompletionReason.DRIFT_STOP
                    state.final_response = iteration_response
                    break

                if completion.is_complete:
                    state.termination_reason = completion.reason
                    state.final_response = iteration_response
                    break

                # Not complete, prepare next iteration
                current_message = _CONTINUATION_NUDGE

                # Optional delay between iterations
                if config.iteration_delay_ms > 0:
                    await asyncio.sleep(config.iteration_delay_ms / 1000.0)

            # Loop exited — ensure completion is set (safety net)
            if completion is None:
                completion = self.completion_detector.check(
                    state.last_response(),
                    state,
                    max_iterations=config.max_iterations,
                    max_seconds=config.max_seconds,
                )
                state.termination_reason = completion.reason

        finally:
            # Always emit final summary event
            yield AgentEvent(
                AgentEventType.MCP_COMPLETE,
                {
                    "source": "continuation",
                    "event": "continuation_complete",
                    "total_iterations": state.iteration,
                    "elapsed_seconds": state.elapsed_seconds,
                    "reason": (
                        state.termination_reason.value
                        if state.termination_reason
                        else CompletionReason.MAX_ITERATIONS.value
                    ),
                    "tool_calls_count": state.tool_calls_count,
                },
            )

            logger.info(
                f"[continuation] Loop finished session={session_id} "
                f"iterations={state.iteration} elapsed={state.elapsed_seconds:.1f}s "
                f"reason={state.termination_reason.value if state.termination_reason else 'unknown'}"
            )

    # ── Phase 5: Sub-Agent Helpers ────────────────────────────────────────

    def _parse_subagent_config(self, config: ContinuationConfig):
        """Parse the optional subagents dict from ContinuationConfig into SubAgentConfig."""
        if not config.subagents:
            return None
        try:
            from app.agent.subagents.models import SubAgentConfig
            return SubAgentConfig(**config.subagents)
        except Exception as e:
            logger.warning(f"[continuation] Invalid subagents config ignored: {e}")
            return None

    async def _try_subagent_path(
        self,
        session_id: str,
        user_message: str,
        subagent_config,  # SubAgentConfig (typed as Any to avoid early import)
        model: Optional[str],
        state: IterationState,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Attempt sub-agent decomposition. If decomposable, runs coordinator
        and sets state.termination_reason. Else sets nothing and caller
        proceeds with normal loop.
        """
        from app.agent.subagents import (
            SubAgentCoordinator,
            decompose_task,
        )
        from app.agent.subagents.aggregator import get_aggregator

        tasks = decompose_task(user_message, parent_session_id=session_id)
        if len(tasks) < subagent_config.min_subtasks:
            logger.info(
                f"[continuation] Sub-agents not used session={session_id}: "
                f"{len(tasks)} tasks < min_subtasks={subagent_config.min_subtasks}"
            )
            return

        logger.info(
            f"[continuation] Using sub-agents session={session_id}: "
            f"{len(tasks)} parallel workers"
        )

        coordinator = SubAgentCoordinator(orchestrator=self.orchestrator)

        coordinator_results = None
        try:
            async for event in coordinator.coordinate(
                tasks=tasks,
                config=subagent_config,
                model=model,
            ):
                # Capture the final coordinator_done payload for aggregation
                if (
                    event.type == AgentEventType.MCP_COMPLETE
                    and isinstance(event.data, dict)
                    and event.data.get("event") == "coordinator_done"
                ):
                    coordinator_results = event.data.get("results", [])
                yield event
        except Exception as e:
            logger.exception(f"[continuation] Sub-agent coordination failed: {e}")
            # Fall through: let normal loop handle the request
            return

        if not coordinator_results:
            logger.warning("[continuation] Sub-agents produced no results, falling back to main loop")
            return

        # Aggregate the results and emit as a final token+done event
        from app.agent.subagents.models import SubAgentResult, SubAgentStatus
        result_objs = [
            SubAgentResult(
                task_id=r["task_id"],
                description=r["description"],
                status=SubAgentStatus(r["status"]),
                response=r.get("response", "") or "",
                error=r.get("error"),
                elapsed_seconds=r.get("elapsed_seconds", 0.0),
            )
            for r in coordinator_results
        ]
        aggregated = get_aggregator().aggregate(
            result_objs,
            style=subagent_config.aggregate_style,
        )

        # Emit as streaming response
        yield AgentEvent(AgentEventType.TOKEN, aggregated.response)
        yield AgentEvent(
            AgentEventType.DONE,
            {
                "response": aggregated.response,
                "subagents": {
                    "total": aggregated.total_tasks,
                    "successful": aggregated.successful_tasks,
                    "failed": aggregated.failed_tasks,
                    "elapsed_seconds": aggregated.total_elapsed_seconds,
                },
            },
        )

        # Mark state as complete so caller skips the main loop
        state.final_response = aggregated.response
        state.termination_reason = CompletionReason.PROMISE_TAG  # Treat as completed
