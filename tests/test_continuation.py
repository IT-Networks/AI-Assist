"""
Unit tests for Phase 1 Task Continuation system.

Tests:
- Models (IterationState, CompletionResult, ContinuationConfig)
- CompletionDetector (Tier 1: Promise Tag, Tier 3: MaxIter/Timeout)
- ContinuationController (iteration loop, event forwarding, cancellation)
"""

import asyncio
import time
from typing import AsyncGenerator, List
from unittest.mock import MagicMock

import pytest

from app.agent.continuation.completion_detector import CompletionDetector
from app.agent.continuation.controller import ContinuationController
from app.agent.continuation.models import (
    CompletionReason,
    CompletionResult,
    ContinuationConfig,
    IterationState,
    TaskType,
)
from app.agent.orchestration.types import AgentEvent, AgentEventType


# ═════════════════════════════════════════════════════════════════════════════
# Model Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestIterationState:
    def test_initial_state(self):
        state = IterationState(session_id="s1", original_goal="find main.py")
        assert state.iteration == 0
        assert state.task_type == TaskType.GENERIC
        assert state.responses == []
        assert state.elapsed_seconds < 0.1  # Just created

    def test_add_response(self):
        state = IterationState(session_id="s1", original_goal="test")
        state.add_response("Hello")
        state.add_response("World")
        assert state.responses == ["Hello", "World"]
        assert state.last_response() == "World"

    def test_last_response_empty(self):
        state = IterationState(session_id="s1", original_goal="test")
        assert state.last_response() == ""

    def test_elapsed_seconds(self):
        state = IterationState(session_id="s1", original_goal="test")
        state.start_time = time.time() - 5.0
        assert 4.9 < state.elapsed_seconds < 5.1


class TestCompletionResult:
    def test_not_complete(self):
        r = CompletionResult.not_complete()
        assert r.is_complete is False
        assert r.reason is None
        assert r.confidence == 0.0
        assert r.tier == 0

    def test_promise_tag(self):
        r = CompletionResult.promise_tag("Task X: done")
        assert r.is_complete is True
        assert r.reason == CompletionReason.PROMISE_TAG
        assert r.confidence == 1.0
        assert r.tier == 1
        assert "Task X" in r.evidence

    def test_max_iterations(self):
        r = CompletionResult.max_iterations(10)
        assert r.is_complete is True
        assert r.reason == CompletionReason.MAX_ITERATIONS
        assert r.tier == 3
        assert "10" in r.evidence

    def test_timeout(self):
        r = CompletionResult.timeout(65.5)
        assert r.is_complete is True
        assert r.reason == CompletionReason.TIMEOUT
        assert r.tier == 3
        assert "65.5" in r.evidence


class TestContinuationConfig:
    def test_defaults(self):
        c = ContinuationConfig()
        assert c.enabled is False
        assert c.max_iterations == 10
        assert c.max_seconds == 120.0
        assert c.iteration_delay_ms == 0

    def test_validation_bounds(self):
        # max_iterations must be in [1, 50]
        with pytest.raises(ValueError):
            ContinuationConfig(max_iterations=0)
        with pytest.raises(ValueError):
            ContinuationConfig(max_iterations=100)

        # max_seconds must be in [5, 600]
        with pytest.raises(ValueError):
            ContinuationConfig(max_seconds=1.0)
        with pytest.raises(ValueError):
            ContinuationConfig(max_seconds=1000.0)

    def test_enabled_opt_in(self):
        c = ContinuationConfig(enabled=True, max_iterations=5)
        assert c.enabled is True
        assert c.max_iterations == 5


# ═════════════════════════════════════════════════════════════════════════════
# CompletionDetector Tests (Tier 1 + Tier 3)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def detector():
    return CompletionDetector()


@pytest.fixture
def fresh_state():
    return IterationState(session_id="test", original_goal="find main.py")


class TestTier1PromiseTag:
    """Tier 1: Promise Tag detection."""

    def test_simple_promise_tag(self, detector, fresh_state):
        fresh_state.iteration = 1
        response = "Searched and found it. <promise>Task: find main.py. Status: COMPLETE. Result: /src/main.py</promise>"
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.tier == 1
        assert result.reason == CompletionReason.PROMISE_TAG
        assert "/src/main.py" in result.evidence

    def test_multiline_promise_tag(self, detector, fresh_state):
        fresh_state.iteration = 1
        response = """
        Analysis complete.
        <promise>
        Task: Optimize build pipeline.
        Status: COMPLETE.
        Result: Reduced build time from 45s to 12s via Gradle cache.
        </promise>
        """
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.tier == 1

    def test_case_insensitive_promise_tag(self, detector, fresh_state):
        fresh_state.iteration = 1
        response = "Done. <PROMISE>Task: test. Status: COMPLETE.</PROMISE>"
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.tier == 1

    def test_no_promise_tag(self, detector, fresh_state):
        fresh_state.iteration = 1
        response = "Still working on it, need more info..."
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is False
        assert result.tier == 0

    def test_empty_response(self, detector, fresh_state):
        fresh_state.iteration = 1
        result = detector.check("", fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is False

    def test_trivial_promise_tag_ignored(self, detector, fresh_state):
        """Promise tags with <3 chars content should be ignored (not a real completion)."""
        fresh_state.iteration = 1
        response = "<promise>ok</promise>"
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        # 'ok' is 2 chars, should be ignored
        assert result.is_complete is False


class TestTier3SafetyValves:
    """Tier 3: MaxIterations and Timeout."""

    def test_max_iterations_reached(self, detector, fresh_state):
        fresh_state.iteration = 10  # == max
        response = "Still working..."  # No promise tag
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.tier == 3
        assert result.reason == CompletionReason.MAX_ITERATIONS

    def test_max_iterations_exceeded(self, detector, fresh_state):
        fresh_state.iteration = 15
        result = detector.check("still working", fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.reason == CompletionReason.MAX_ITERATIONS

    def test_iteration_below_max(self, detector, fresh_state):
        fresh_state.iteration = 5
        result = detector.check("working", fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is False

    def test_timeout_reached(self, detector, fresh_state):
        fresh_state.iteration = 1
        fresh_state.start_time = time.time() - 70.0  # 70s elapsed
        result = detector.check("working", fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.reason == CompletionReason.TIMEOUT

    def test_no_timeout(self, detector, fresh_state):
        fresh_state.iteration = 1
        fresh_state.start_time = time.time() - 10.0  # 10s elapsed
        result = detector.check("working", fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is False


class TestTierPriority:
    """Tier 1 should always beat Tier 3."""

    def test_promise_tag_wins_over_max_iter(self, detector, fresh_state):
        fresh_state.iteration = 999  # Way over max
        response = "Done. <promise>Task: X. Status: COMPLETE. Result: done</promise>"
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.reason == CompletionReason.PROMISE_TAG  # Not MAX_ITERATIONS!
        assert result.tier == 1

    def test_promise_tag_wins_over_timeout(self, detector, fresh_state):
        fresh_state.iteration = 1
        fresh_state.start_time = time.time() - 999.0  # Way over timeout
        response = "Done. <promise>Task: X. Status: COMPLETE. Result: ok</promise>"
        result = detector.check(response, fresh_state, max_iterations=10, max_seconds=60)
        assert result.is_complete is True
        assert result.reason == CompletionReason.PROMISE_TAG


# ═════════════════════════════════════════════════════════════════════════════
# ContinuationController Tests
# ═════════════════════════════════════════════════════════════════════════════


def _make_mock_orchestrator(event_sequences: List[List[AgentEvent]]):
    """
    Build a mock orchestrator whose process() returns a given sequence of events
    per iteration.

    event_sequences[i] = events for iteration i+1
    """
    iteration_counter = {"n": 0}

    def process(session_id, user_message, **kwargs):  # noqa: ARG001
        async def gen():
            idx = iteration_counter["n"]
            iteration_counter["n"] += 1
            events = event_sequences[idx] if idx < len(event_sequences) else []
            for event in events:
                yield event

        return gen()

    mock = MagicMock()
    mock.process = process
    return mock


async def _collect_events(
    async_gen: AsyncGenerator[AgentEvent, None],
) -> List[AgentEvent]:
    events = []
    async for e in async_gen:
        events.append(e)
    return events


@pytest.mark.asyncio
class TestContinuationController:
    async def test_stops_on_promise_tag_iteration_1(self):
        """Agent emits promise tag in iteration 1 → loop exits after 1 iteration."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOKEN, "Done. "),
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: find. Status: COMPLETE. Result: ok</promise>"),
            AgentEvent(AgentEventType.DONE, {"response": "final"}),
        ]
        orchestrator = _make_mock_orchestrator([events_iter1])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=5, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="find main.py", config=config,
            )
        )

        # Should have: iteration_started + forwarded events + iteration_complete + continuation_complete
        continuation_meta = [
            e for e in events
            if e.type in (AgentEventType.MCP_PROGRESS, AgentEventType.MCP_COMPLETE)
            and isinstance(e.data, dict)
            and e.data.get("source") == "continuation"
        ]
        started = [e for e in continuation_meta if e.data.get("event") == "iteration_started"]
        iteration_done = [e for e in continuation_meta if e.data.get("event") == "iteration_complete"]
        final = [e for e in continuation_meta if e.data.get("event") == "continuation_complete"]

        assert len(started) == 1  # Only 1 iteration started
        assert len(iteration_done) == 1
        assert iteration_done[0].data["is_complete"] is True
        assert iteration_done[0].data["reason"] == CompletionReason.PROMISE_TAG.value
        assert len(final) == 1
        assert final[0].data["total_iterations"] == 1

    async def test_multi_iteration_until_promise_tag(self):
        """Agent doesn't emit promise tag in iter 1, emits in iter 2 → 2 iterations."""
        events_iter1 = [AgentEvent(AgentEventType.TOKEN, "Working on it...")]
        events_iter2 = [
            AgentEvent(AgentEventType.TOKEN, "Now done. "),
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: X. Status: COMPLETE. Result: ok</promise>"),
        ]
        orchestrator = _make_mock_orchestrator([events_iter1, events_iter2])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=5, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="do work", config=config,
            )
        )

        started = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "iteration_started"
        ]
        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert len(started) == 2  # 2 iterations
        assert final[0].data["total_iterations"] == 2
        assert final[0].data["reason"] == CompletionReason.PROMISE_TAG.value

    async def test_max_iterations_safety_valve(self):
        """Agent never emits promise tag → loop stops at max_iterations."""
        # Always produce non-completing response
        event_seq = [[AgentEvent(AgentEventType.TOKEN, "still working...")] for _ in range(10)]
        orchestrator = _make_mock_orchestrator(event_seq)
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=3, max_seconds=60.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="impossible task", config=config,
            )
        )

        started = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "iteration_started"
        ]
        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert len(started) == 3
        assert final[0].data["total_iterations"] == 3
        assert final[0].data["reason"] == CompletionReason.MAX_ITERATIONS.value

    async def test_confirm_required_exits_loop(self):
        """If orchestrator emits CONFIRM_REQUIRED, controller should exit loop."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOKEN, "Need confirmation..."),
            AgentEvent(AgentEventType.CONFIRM_REQUIRED, {"operation": "write_file", "path": "/tmp/x"}),
        ]
        orchestrator = _make_mock_orchestrator([events_iter1])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=5, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="edit file", config=config,
            )
        )

        # Should see CONFIRM_REQUIRED event forwarded
        confirm_events = [e for e in events if e.type == AgentEventType.CONFIRM_REQUIRED]
        assert len(confirm_events) == 1

    async def test_events_forwarded_in_order(self):
        """All orchestrator events must flow through to caller unchanged."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOOL_START, {"tool": "search_code"}),
            AgentEvent(AgentEventType.TOOL_RESULT, {"result": "found"}),
            AgentEvent(AgentEventType.TOKEN, "Here is the result. "),
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: x. Status: COMPLETE. Result: found</promise>"),
            AgentEvent(AgentEventType.DONE, {"response": "done"}),
        ]
        orchestrator = _make_mock_orchestrator([events_iter1])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=2, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="search", config=config,
            )
        )

        # Tool events should appear in the stream
        tool_starts = [e for e in events if e.type == AgentEventType.TOOL_START]
        tool_results = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
        assert len(tool_starts) == 1
        assert len(tool_results) == 1
        assert tool_starts[0].data == {"tool": "search_code"}

    async def test_tool_calls_counted(self):
        """TOOL_START events should increment tool_calls_count in final metrics."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOOL_START, {"tool": "search"}),
            AgentEvent(AgentEventType.TOOL_START, {"tool": "read"}),
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: x. Status: COMPLETE. Result: ok</promise>"),
        ]
        orchestrator = _make_mock_orchestrator([events_iter1])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=5, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="task", config=config,
            )
        )

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert final[0].data["tool_calls_count"] == 2


# ═════════════════════════════════════════════════════════════════════════════
# Config Integration Test
# ═════════════════════════════════════════════════════════════════════════════


class TestConfigIntegration:
    def test_settings_have_continuation(self):
        from app.core.config import ContinuationSettings, settings

        assert hasattr(settings, "continuation")
        assert isinstance(settings.continuation, ContinuationSettings)
        assert settings.continuation.enabled_by_default is False  # Opt-in default
        assert settings.continuation.max_iterations == 10
        assert settings.continuation.max_seconds == 120.0
