"""
Unit tests for Phase 5 Sub-Agent Teams.

Tests:
- Models (SubAgentTask, SubAgentResult, SubAgentConfig)
- TaskDecomposer (heuristic decomposition)
- SubAgentWorker (single task execution)
- SubAgentCoordinator (parallel orchestration)
- ResultAggregator (structured + narrative styles)
- Controller integration
"""

import asyncio
import time
from typing import AsyncGenerator, List
from unittest.mock import MagicMock

import pytest

from app.agent.continuation.controller import ContinuationController
from app.agent.continuation.models import ContinuationConfig
from app.agent.orchestration.types import AgentEvent, AgentEventType
from app.agent.subagents.aggregator import ResultAggregator
from app.agent.subagents.coordinator import SubAgentCoordinator
from app.agent.subagents.models import (
    SubAgentConfig,
    SubAgentResult,
    SubAgentStatus,
    SubAgentTask,
)
from app.agent.subagents.task_decomposer import TaskDecomposer, decompose_task
from app.agent.subagents.worker import SubAgentWorker


# ═════════════════════════════════════════════════════════════════════════════
# Model Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestSubAgentTask:
    def test_auto_generated_id(self):
        t = SubAgentTask(description="do something")
        assert t.task_id.startswith("sat_")
        assert len(t.task_id) == 12  # "sat_" + 8 hex chars
        assert t.status == SubAgentStatus.PENDING

    def test_worker_session_id_isolation(self):
        t = SubAgentTask(description="x", parent_session_id="main_chat")
        worker_session = t.worker_session_id()
        assert worker_session.startswith("main_chat::sat_")

    def test_different_tasks_have_different_ids(self):
        t1 = SubAgentTask(description="task one")
        t2 = SubAgentTask(description="task two")
        assert t1.task_id != t2.task_id


class TestSubAgentResult:
    def test_is_success(self):
        r = SubAgentResult(
            task_id="t1", description="d", status=SubAgentStatus.COMPLETED, response="ok"
        )
        assert r.is_success is True

    def test_is_not_success_on_failure(self):
        r = SubAgentResult(
            task_id="t1", description="d", status=SubAgentStatus.FAILED, error="boom"
        )
        assert r.is_success is False

    def test_summary_line_success(self):
        r = SubAgentResult(
            task_id="t1", description="search for X", status=SubAgentStatus.COMPLETED,
            response="Found 3 matches",
        )
        s = r.summary_line()
        assert "✓" in s
        assert "t1" in s

    def test_summary_line_failure(self):
        r = SubAgentResult(
            task_id="t1", description="search", status=SubAgentStatus.TIMEOUT,
            error="timed out",
        )
        s = r.summary_line()
        assert "✗" in s


class TestSubAgentConfig:
    def test_defaults(self):
        c = SubAgentConfig()
        assert c.enabled is False
        assert c.max_workers == 3
        assert c.worker_timeout_seconds == 60.0
        assert c.min_subtasks == 2
        assert c.aggregate_style == "structured"

    def test_validation_bounds(self):
        with pytest.raises(ValueError):
            SubAgentConfig(max_workers=0)
        with pytest.raises(ValueError):
            SubAgentConfig(max_workers=100)
        with pytest.raises(ValueError):
            SubAgentConfig(worker_timeout_seconds=1.0)


# ═════════════════════════════════════════════════════════════════════════════
# TaskDecomposer Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestTaskDecomposer:
    def setup_method(self):
        self.d = TaskDecomposer()

    def test_empty_message(self):
        assert self.d.decompose("") == []

    def test_too_short_message(self):
        # Under MIN_MESSAGE_LENGTH (40 chars)
        result = self.d.decompose("Find X")
        assert result == []

    def test_single_imperative_not_decomposed(self):
        """Single imperative verb should NOT be decomposed."""
        msg = "Search the codebase for references to MyClass across the entire project"
        # Only "search" is imperative
        result = self.d.decompose(msg)
        assert result == []

    def test_numbered_list_decomposition(self):
        msg = (
            "Please do these things: "
            "1. Analyze the build pipeline for bottlenecks "
            "2. Optimize the Gradle configuration settings "
            "3. Write documentation for the changes"
        )
        result = self.d.decompose(msg)
        assert len(result) >= 2

    def test_bulleted_list_decomposition(self):
        msg = (
            "Tasks to complete:\n"
            "- Analyze the authentication flow\n"
            "- Optimize the database queries\n"
            "- Write tests for the new endpoints"
        )
        result = self.d.decompose(msg)
        assert len(result) >= 2

    def test_conjunction_and_then(self):
        msg = "Analyze the config file and then optimize the build settings"
        result = self.d.decompose(msg)
        assert len(result) >= 2

    def test_conjunction_sowie(self):
        msg = "Untersuche den Code sowie analysiere die Performance-Metriken"
        result = self.d.decompose(msg)
        assert len(result) >= 2

    def test_semicolon_imperative_list(self):
        msg = "Analyze the java code; optimize the build; write unit tests for it"
        result = self.d.decompose(msg)
        assert len(result) >= 2

    def test_too_short_chunks_filtered(self):
        """Chunks below MIN_CHUNK_LENGTH should be filtered out."""
        msg = "Find X and then do Y"  # Very short chunks
        result = self.d.decompose(msg)
        # Either empty OR filtered — shouldn't produce malformed short tasks
        assert all(len(t.description) >= 15 for t in result)

    def test_parent_session_id_propagated(self):
        msg = "Analyze the pipeline and then optimize the Gradle build"
        result = self.d.decompose(msg, parent_session_id="parent_123")
        if result:
            for task in result:
                assert task.parent_session_id == "parent_123"

    def test_max_subtasks_cap(self):
        """Should cap at MAX_SUBTASKS = 10."""
        msg = "Tasks: " + " ".join(f"{i}. Analyze module number {i} very carefully" for i in range(1, 15))
        result = self.d.decompose(msg)
        assert len(result) <= 10

    def test_decompose_task_convenience(self):
        """Module-level function should work identically."""
        msg = "Analyze the system sowie optimize the build pipeline"
        result = decompose_task(msg)
        assert len(result) >= 2


# ═════════════════════════════════════════════════════════════════════════════
# ResultAggregator Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestResultAggregator:
    def setup_method(self):
        self.agg = ResultAggregator()

    def test_empty_results(self):
        agg = self.agg.aggregate([])
        assert agg.total_tasks == 0
        assert agg.successful_tasks == 0
        assert "No subtasks" in agg.response or "Keine" in agg.response

    def test_all_success_structured(self):
        results = [
            SubAgentResult("t1", "Analyze build", SubAgentStatus.COMPLETED, "Found issue A", elapsed_seconds=1.5),
            SubAgentResult("t2", "Write test", SubAgentStatus.COMPLETED, "Test written", elapsed_seconds=2.0),
        ]
        agg = self.agg.aggregate(results, style="structured")
        assert agg.total_tasks == 2
        assert agg.successful_tasks == 2
        assert agg.failed_tasks == 0
        assert "# Ergebnisse" in agg.response
        assert "✓" in agg.response
        assert "Found issue A" in agg.response
        assert "Test written" in agg.response

    def test_mixed_success_failure_structured(self):
        results = [
            SubAgentResult("t1", "Analyze build", SubAgentStatus.COMPLETED, "done"),
            SubAgentResult("t2", "Impossible task", SubAgentStatus.FAILED, error="permission denied"),
        ]
        agg = self.agg.aggregate(results, style="structured")
        assert agg.successful_tasks == 1
        assert agg.failed_tasks == 1
        assert "✓" in agg.response
        assert "✗" in agg.response
        assert "permission denied" in agg.response

    def test_narrative_style(self):
        results = [
            SubAgentResult("t1", "do x", SubAgentStatus.COMPLETED, "result from x"),
            SubAgentResult("t2", "do y", SubAgentStatus.COMPLETED, "result from y"),
        ]
        agg = self.agg.aggregate(results, style="narrative")
        assert "# Ergebnisse" not in agg.response  # No markdown heading
        assert "result from x" in agg.response
        assert "result from y" in agg.response
        assert "---" in agg.response  # Separator

    def test_narrative_with_failures(self):
        results = [
            SubAgentResult("t1", "do x", SubAgentStatus.COMPLETED, "ok"),
            SubAgentResult("t2", "fail", SubAgentStatus.TIMEOUT, error="timeout"),
        ]
        agg = self.agg.aggregate(results, style="narrative")
        assert "ok" in agg.response
        assert "fail" in agg.response.lower() or "timeout" in agg.response

    def test_total_elapsed(self):
        results = [
            SubAgentResult("t1", "x", SubAgentStatus.COMPLETED, elapsed_seconds=1.5),
            SubAgentResult("t2", "y", SubAgentStatus.COMPLETED, elapsed_seconds=2.5),
        ]
        agg = self.agg.aggregate(results)
        assert agg.total_elapsed_seconds == 4.0


# ═════════════════════════════════════════════════════════════════════════════
# SubAgentWorker Tests
# ═════════════════════════════════════════════════════════════════════════════


def _make_mock_orchestrator(events: List[AgentEvent]):
    """Build a mock orchestrator whose process() yields given events."""
    def process(session_id, user_message, **kwargs):  # noqa: ARG001
        async def gen():
            for e in events:
                yield e
        return gen()

    mock = MagicMock()
    mock.process = process
    mock.cancel_request = MagicMock()
    return mock


def _make_hanging_orchestrator():
    """Build a mock orchestrator whose process() hangs forever (for timeout tests)."""
    def process(session_id, user_message, **kwargs):  # noqa: ARG001
        async def gen():
            await asyncio.sleep(10.0)  # Long sleep
            yield AgentEvent(AgentEventType.DONE, {"response": "unreachable"})
        return gen()

    mock = MagicMock()
    mock.process = process
    mock.cancel_request = MagicMock()
    return mock


@pytest.mark.asyncio
class TestSubAgentWorker:
    async def test_success_with_token_events(self):
        events = [
            AgentEvent(AgentEventType.TOKEN, "Hello "),
            AgentEvent(AgentEventType.TOKEN, "world"),
            AgentEvent(AgentEventType.DONE, {"response": "Hello world"}),
        ]
        orch = _make_mock_orchestrator(events)
        worker = SubAgentWorker(orchestrator=orch)
        task = SubAgentTask(description="say hello", parent_session_id="p")

        result = await worker.execute(task, timeout_seconds=5.0)

        assert result.is_success
        assert result.status == SubAgentStatus.COMPLETED
        assert result.response == "Hello world"
        assert result.event_count == 3

    async def test_tool_calls_counted(self):
        events = [
            AgentEvent(AgentEventType.TOOL_START, {"name": "search_code"}),
            AgentEvent(AgentEventType.TOOL_RESULT, {"success": True}),
            AgentEvent(AgentEventType.TOOL_START, {"name": "read_file"}),
            AgentEvent(AgentEventType.TOOL_RESULT, {"success": True}),
            AgentEvent(AgentEventType.TOKEN, "Done"),
        ]
        orch = _make_mock_orchestrator(events)
        worker = SubAgentWorker(orchestrator=orch)
        task = SubAgentTask(description="search and read")

        result = await worker.execute(task, timeout_seconds=5.0)

        assert result.is_success
        assert result.tool_calls_count == 2

    async def test_confirm_required_fails_task(self):
        events = [
            AgentEvent(AgentEventType.TOKEN, "Need confirmation..."),
            AgentEvent(AgentEventType.CONFIRM_REQUIRED, {"operation": "write_file"}),
        ]
        orch = _make_mock_orchestrator(events)
        worker = SubAgentWorker(orchestrator=orch)
        task = SubAgentTask(description="write a file")

        result = await worker.execute(task, timeout_seconds=5.0)

        assert not result.is_success
        assert result.status == SubAgentStatus.FAILED
        assert "confirmation" in (result.error or "").lower()

    async def test_orchestrator_error_event(self):
        events = [
            AgentEvent(AgentEventType.ERROR, {"error": "LLM timeout"}),
        ]
        orch = _make_mock_orchestrator(events)
        worker = SubAgentWorker(orchestrator=orch)
        task = SubAgentTask(description="do x")

        result = await worker.execute(task, timeout_seconds=5.0)

        assert not result.is_success
        assert result.status == SubAgentStatus.FAILED
        assert "LLM timeout" in (result.error or "")

    async def test_timeout_triggers_timeout_status(self):
        orch = _make_hanging_orchestrator()
        worker = SubAgentWorker(orchestrator=orch)
        task = SubAgentTask(description="long task")

        result = await worker.execute(task, timeout_seconds=0.2)

        assert result.status == SubAgentStatus.TIMEOUT
        assert not result.is_success
        # cancel_request should have been called on the worker session
        orch.cancel_request.assert_called_once()

    async def test_worker_session_id_isolated(self):
        """Worker should call process() with the task's isolated session_id."""
        captured = {}
        def process(session_id, user_message, **kwargs):  # noqa: ARG001
            captured["session_id"] = session_id
            async def gen():
                yield AgentEvent(AgentEventType.DONE, {"response": "ok"})
            return gen()

        orch = MagicMock()
        orch.process = process
        orch.cancel_request = MagicMock()
        worker = SubAgentWorker(orchestrator=orch)

        task = SubAgentTask(description="x", parent_session_id="parent_123")
        await worker.execute(task, timeout_seconds=5.0)

        assert captured["session_id"] == task.worker_session_id()
        assert "parent_123::" in captured["session_id"]


# ═════════════════════════════════════════════════════════════════════════════
# SubAgentCoordinator Tests
# ═════════════════════════════════════════════════════════════════════════════


async def _collect_events(gen: AsyncGenerator[AgentEvent, None]) -> List[AgentEvent]:
    events = []
    async for e in gen:
        events.append(e)
    return events


def _make_multi_orchestrator(per_task_events: dict):
    """Build an orchestrator that yields different events depending on user_message."""
    def process(session_id, user_message, **kwargs):  # noqa: ARG001
        events = per_task_events.get(user_message, [
            AgentEvent(AgentEventType.DONE, {"response": f"default for {user_message}"}),
        ])
        async def gen():
            for e in events:
                yield e
        return gen()

    mock = MagicMock()
    mock.process = process
    mock.cancel_request = MagicMock()
    return mock


@pytest.mark.asyncio
class TestSubAgentCoordinator:
    async def test_empty_task_list(self):
        orch = _make_mock_orchestrator([])
        coord = SubAgentCoordinator(orchestrator=orch)
        config = SubAgentConfig(enabled=True)

        events = await _collect_events(coord.coordinate(tasks=[], config=config))

        # Should emit coordinator_done with 0 results
        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "coordinator_done"
        ]
        assert len(final) == 1
        assert final[0].data["total"] == 0

    async def test_parallel_execution_order(self):
        """Results should come back in the same order as input tasks."""
        per_task = {
            "task A description": [AgentEvent(AgentEventType.TOKEN, "A result")],
            "task B description": [AgentEvent(AgentEventType.TOKEN, "B result")],
            "task C description": [AgentEvent(AgentEventType.TOKEN, "C result")],
        }
        orch = _make_multi_orchestrator(per_task)
        coord = SubAgentCoordinator(orchestrator=orch)
        config = SubAgentConfig(enabled=True, max_workers=3, worker_timeout_seconds=5.0)

        tasks = [
            SubAgentTask(description="task A description"),
            SubAgentTask(description="task B description"),
            SubAgentTask(description="task C description"),
        ]
        events = await _collect_events(coord.coordinate(tasks=tasks, config=config))

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "coordinator_done"
        ]
        assert len(final) == 1
        results = final[0].data["results"]
        assert len(results) == 3
        # Order preserved
        assert results[0]["description"] == "task A description"
        assert results[1]["description"] == "task B description"
        assert results[2]["description"] == "task C description"

    async def test_max_workers_caps_task_count(self):
        """If tasks > max_workers, extra tasks should be dropped."""
        per_task = {f"task {i}": [AgentEvent(AgentEventType.TOKEN, f"result {i}")] for i in range(5)}
        orch = _make_multi_orchestrator(per_task)
        coord = SubAgentCoordinator(orchestrator=orch)
        config = SubAgentConfig(enabled=True, max_workers=2, worker_timeout_seconds=5.0)

        tasks = [SubAgentTask(description=f"task {i}") for i in range(5)]
        events = await _collect_events(coord.coordinate(tasks=tasks, config=config))

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "coordinator_done"
        ]
        # Only 2 tasks should have been executed
        assert final[0].data["total"] == 2

    async def test_failure_does_not_cancel_others(self):
        """If one worker fails, others should still complete."""
        per_task = {
            "good task description here": [AgentEvent(AgentEventType.TOKEN, "good result")],
            "bad task description here": [AgentEvent(AgentEventType.ERROR, {"error": "bad"})],
        }
        orch = _make_multi_orchestrator(per_task)
        coord = SubAgentCoordinator(orchestrator=orch)
        config = SubAgentConfig(enabled=True, max_workers=2, worker_timeout_seconds=5.0)

        tasks = [
            SubAgentTask(description="good task description here"),
            SubAgentTask(description="bad task description here"),
        ]
        events = await _collect_events(coord.coordinate(tasks=tasks, config=config))

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "coordinator_done"
        ]
        assert final[0].data["success"] == 1
        assert final[0].data["failures"] == 1
        assert final[0].data["total"] == 2

    async def test_emits_start_event(self):
        orch = _make_mock_orchestrator([
            AgentEvent(AgentEventType.TOKEN, "r"),
        ])
        coord = SubAgentCoordinator(orchestrator=orch)
        config = SubAgentConfig(enabled=True, max_workers=2, worker_timeout_seconds=5.0)

        tasks = [SubAgentTask(description="task 1")]
        events = await _collect_events(coord.coordinate(tasks=tasks, config=config))

        start_events = [e for e in events if e.type == AgentEventType.SUBAGENT_START]
        assert len(start_events) == 1
        assert start_events[0].data["task_count"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# Controller Integration Tests
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestControllerSubAgentIntegration:
    async def test_subagents_activated_on_decomposable_message(self):
        """Controller should use sub-agents when decomposable message + enabled config."""
        per_task = {
            "Analyze the build pipeline configuration thoroughly": [
                AgentEvent(AgentEventType.TOKEN, "Analysis result"),
            ],
            "Optimize the Gradle cache settings properly": [
                AgentEvent(AgentEventType.TOKEN, "Optimization applied"),
            ],
        }
        orch = _make_multi_orchestrator(per_task)
        controller = ContinuationController(orchestrator=orch)

        config = ContinuationConfig(
            enabled=True,
            max_iterations=3,
            max_seconds=10.0,
            subagents={"enabled": True, "max_workers": 3, "worker_timeout_seconds": 5.0, "min_subtasks": 2},
        )

        msg = (
            "Analyze the build pipeline configuration thoroughly and then "
            "optimize the Gradle cache settings properly"
        )
        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test_sess", user_message=msg, config=config,
            )
        )

        # Should see sub-agent lifecycle events
        start_events = [e for e in events if e.type == AgentEventType.SUBAGENT_START]
        done_events = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "coordinator_done"
        ]
        assert len(start_events) >= 1
        assert len(done_events) == 1

        # Final continuation_complete should note subagents_used=True
        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert len(final) == 1
        assert final[0].data.get("subagents_used") is True

    async def test_subagents_disabled_uses_main_loop(self):
        """When subagents disabled, controller should skip decomposition."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: x. Status: COMPLETE. Result: ok</promise>"),
        ]
        orch = _make_mock_orchestrator(events_iter1)
        controller = ContinuationController(orchestrator=orch)

        config = ContinuationConfig(
            enabled=True,
            max_iterations=3,
            max_seconds=10.0,
            subagents=None,  # Disabled
        )

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test_sess",
                user_message="Analyze the build and then optimize it thoroughly please",
                config=config,
            )
        )

        # Should NOT see SUBAGENT_START
        start_events = [e for e in events if e.type == AgentEventType.SUBAGENT_START]
        assert len(start_events) == 0

    async def test_subagents_insufficient_decomposition_falls_back(self):
        """If decomposition yields < min_subtasks, fall back to main loop."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: x. Status: COMPLETE. Result: ok</promise>"),
        ]
        orch = _make_mock_orchestrator(events_iter1)
        controller = ContinuationController(orchestrator=orch)

        # Message with only 1 imperative — not decomposable
        config = ContinuationConfig(
            enabled=True,
            max_iterations=3,
            max_seconds=10.0,
            subagents={"enabled": True, "max_workers": 3, "min_subtasks": 2},
        )

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test_sess",
                user_message="Search for the main class file in the repository",  # 1 imperative
                config=config,
            )
        )

        # Should have run normal iteration
        start_events = [e for e in events if e.type == AgentEventType.SUBAGENT_START]
        assert len(start_events) == 0

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert len(final) == 1
        # subagents_used should not be True (either missing or False)
        assert not final[0].data.get("subagents_used", False)
