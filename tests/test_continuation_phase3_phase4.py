"""
Unit tests for Phase 3 (Tier 2 Criteria Matching + TaskClassifier)
and Phase 4 (Drift Monitoring).
"""

import time
from typing import AsyncGenerator, List
from unittest.mock import MagicMock

import pytest

from app.agent.continuation.completion_detector import CompletionDetector
from app.agent.continuation.completion_signals import (
    match_completion_signal,
    get_signals_for_task,
)
from app.agent.continuation.controller import ContinuationController
from app.agent.continuation.drift_monitor import DriftMonitor
from app.agent.continuation.models import (
    CompletionReason,
    ContinuationConfig,
    DriftAssessment,
    DriftRiskLevel,
    IterationState,
    TaskType,
)
from app.agent.continuation.scorers import (
    score_context_coherence,
    score_goal_alignment,
    score_token_burn_rate,
    score_tool_efficiency,
)
from app.agent.continuation.scorers.token_burn import is_abnormal_burn
from app.agent.continuation.task_classifier import TaskClassifier, classify_task
from app.agent.orchestration.types import AgentEvent, AgentEventType


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3: TaskClassifier Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestTaskClassifier:
    def setup_method(self):
        self.classifier = TaskClassifier()

    def test_empty_message(self):
        task_type, conf = self.classifier.classify("")
        assert task_type == TaskType.GENERIC
        assert conf == 0.0

    def test_search_task_english(self):
        task_type, conf = self.classifier.classify("Find the main class in the project")
        assert task_type == TaskType.SEARCH
        assert conf > 0.0

    def test_search_task_german(self):
        task_type, conf = self.classifier.classify("Finde die Hauptklasse im Projekt")
        assert task_type == TaskType.SEARCH
        assert conf > 0.0

    def test_read_task(self):
        task_type, conf = self.classifier.classify("Lies mir die Datei config.yaml vor")
        assert task_type == TaskType.READ

    def test_analysis_task_english(self):
        task_type, conf = self.classifier.classify("Analyze the performance bottleneck")
        # "performance" keyword is in OPTIMIZATION, "analyze" is in ANALYSIS
        # Both may match; OPTIMIZATION has higher priority in rules order
        assert task_type in (TaskType.ANALYSIS, TaskType.OPTIMIZATION)

    def test_analysis_task_german(self):
        task_type, conf = self.classifier.classify("Untersuche den Code nach Problemen")
        assert task_type == TaskType.ANALYSIS

    def test_optimization_task(self):
        task_type, conf = self.classifier.classify("Optimiere die Build-Pipeline für schneller Kompilierung")
        assert task_type == TaskType.OPTIMIZATION
        # Multiple hits should give high confidence
        assert conf >= 0.5

    def test_generation_task(self):
        task_type, conf = self.classifier.classify("Schreibe mir einen Unit-Test für MyClass")
        assert task_type == TaskType.GENERATION

    def test_unrelated_message_falls_to_generic(self):
        task_type, conf = self.classifier.classify("xyz foo bar baz")
        assert task_type == TaskType.GENERIC
        assert conf == 0.0

    def test_classify_task_function(self):
        """Convenience function should work the same as class method."""
        task_type, conf = classify_task("Find the config file")
        assert task_type == TaskType.SEARCH

    def test_multiple_keywords_higher_confidence(self):
        """Multiple matching keywords should give higher confidence than single."""
        _, conf_single = self.classifier.classify("Finde X")
        _, conf_multi = self.classifier.classify("Finde und suche X in der Liste")
        assert conf_multi >= conf_single


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3: Completion Signals Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestCompletionSignals:
    def test_search_signal_english(self):
        evidence = match_completion_signal("I found 5 results matching your query.", TaskType.SEARCH)
        assert evidence != ""
        assert "found" in evidence.lower()

    def test_search_signal_german(self):
        evidence = match_completion_signal("3 Ergebnisse gefunden in src/main/java", TaskType.SEARCH)
        assert evidence != ""

    def test_search_no_results_is_valid_completion(self):
        evidence = match_completion_signal("No results found for that query.", TaskType.SEARCH)
        assert evidence != ""

    def test_read_signal_with_code_fence(self):
        response = "Here is the content:\n```python\ndef foo(): pass\n```"
        evidence = match_completion_signal(response, TaskType.READ)
        assert evidence != ""

    def test_analysis_signal_findings(self):
        evidence = match_completion_signal("Analysis shows 3 critical issues.\nFindings: ...", TaskType.ANALYSIS)
        assert evidence != ""

    def test_analysis_signal_conclusion(self):
        evidence = match_completion_signal("Conclusion: the code is well-structured.", TaskType.ANALYSIS)
        assert evidence != ""

    def test_optimization_signal_reduction(self):
        evidence = match_completion_signal("Reduced build time from 45s to 12s", TaskType.OPTIMIZATION)
        assert evidence != ""

    def test_optimization_signal_improvement(self):
        evidence = match_completion_signal("Improvement: 30% faster builds", TaskType.OPTIMIZATION)
        assert evidence != ""

    def test_generation_signal(self):
        response = "Here is the generated test:\n```java\n@Test void test() {}\n```"
        # Need at least 50 chars in code block, so pad it
        response = "Here is the generated test:\n```java\n@Test\npublic void testMyMethod() {\n  assertEquals(1, 1);\n}\n```"
        evidence = match_completion_signal(response, TaskType.GENERATION)
        assert evidence != ""

    def test_generic_fallback_catches_task_complete(self):
        """Generic signals should work as fallback across task types."""
        evidence = match_completion_signal("Alles erledigt.", TaskType.SEARCH)
        # Generic signal should catch this even with SEARCH task_type
        assert evidence != ""

    def test_no_match_returns_empty(self):
        evidence = match_completion_signal("Still working on the task...", TaskType.SEARCH)
        assert evidence == ""

    def test_empty_response_returns_empty(self):
        evidence = match_completion_signal("", TaskType.SEARCH)
        assert evidence == ""


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3: CompletionDetector Tier 2 Tests
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def detector():
    return CompletionDetector()


class TestTier2CriteriaMatching:
    def test_tier2_activates_after_first_iteration(self, detector):
        """Tier 2 should not trigger on iteration 1 (require iteration >= 1 actually means at iter 1 it's ok)."""
        state = IterationState(session_id="s1", original_goal="find config", task_type=TaskType.SEARCH)
        state.iteration = 1  # Matches _TIER_2_MIN_ITERATIONS
        response = "Found 3 matches in src/"
        result = detector.check(response, state, max_iterations=10, max_seconds=60, task_type=TaskType.SEARCH)
        assert result.is_complete is True
        assert result.reason == CompletionReason.CRITERIA_MATCH
        assert result.tier == 2
        assert result.confidence == 0.7

    def test_tier2_detects_optimization_completion(self, detector):
        state = IterationState(
            session_id="s1",
            original_goal="optimize build",
            task_type=TaskType.OPTIMIZATION,
        )
        state.iteration = 2
        response = "Reduced build time from 45s to 12s."
        result = detector.check(response, state, max_iterations=10, max_seconds=60, task_type=TaskType.OPTIMIZATION)
        assert result.is_complete is True
        assert result.tier == 2

    def test_tier1_wins_over_tier2(self, detector):
        """Promise Tag should always win over criteria match."""
        state = IterationState(session_id="s1", original_goal="test", task_type=TaskType.SEARCH)
        state.iteration = 1
        response = "Found 5 results. <promise>Task: X. Status: COMPLETE. Result: found</promise>"
        result = detector.check(response, state, max_iterations=10, max_seconds=60, task_type=TaskType.SEARCH)
        assert result.tier == 1  # Not 2
        assert result.reason == CompletionReason.PROMISE_TAG

    def test_require_promise_tag_disables_tier2(self, detector):
        """When require_promise_tag=True, Tier 2 should be skipped."""
        state = IterationState(session_id="s1", original_goal="find", task_type=TaskType.SEARCH)
        state.iteration = 1
        response = "Found 5 results in the codebase."
        result = detector.check(
            response,
            state,
            max_iterations=10,
            max_seconds=60,
            task_type=TaskType.SEARCH,
            require_promise_tag=True,
        )
        # Should NOT be complete — Tier 2 disabled
        assert result.is_complete is False

    def test_tier2_no_signal_continues(self, detector):
        state = IterationState(session_id="s1", original_goal="find", task_type=TaskType.SEARCH)
        state.iteration = 1
        response = "Still searching..."
        result = detector.check(response, state, max_iterations=10, max_seconds=60)
        assert result.is_complete is False


# ═════════════════════════════════════════════════════════════════════════════
# Phase 4: Scorer Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestGoalAlignmentScorer:
    def test_empty_goal_returns_1(self):
        assert score_goal_alignment("", "anything") == 1.0

    def test_empty_response_returns_0(self):
        assert score_goal_alignment("find main class", "") == 0.0

    def test_perfect_overlap(self):
        """All goal tokens present in response."""
        score = score_goal_alignment(
            "optimize build pipeline gradle",
            "The build pipeline has been optimized using gradle caching",
        )
        assert score >= 0.75  # Most tokens found

    def test_no_overlap(self):
        score = score_goal_alignment(
            "optimize build pipeline",
            "the weather is nice today",
        )
        assert score == 0.0

    def test_partial_overlap(self):
        score = score_goal_alignment(
            "find MyClass in project",
            "MyClass is located at src/",
        )
        assert 0.0 < score < 1.0


class TestToolEfficiencyScorer:
    def test_no_tool_calls_returns_1(self):
        assert score_tool_efficiency([], 0, 0) == 1.0

    def test_all_unique_successful(self):
        signatures = ["search_code|query=X", "read_file|path=a", "read_file|path=b"]
        score = score_tool_efficiency(signatures, 0, 3)
        assert score == 1.0

    def test_duplicate_calls_reduce_score(self):
        signatures = ["search_code|query=X", "search_code|query=X", "search_code|query=X"]
        score = score_tool_efficiency(signatures, 0, 3)
        # 3 calls, 1 unique → 2 duplicates wasted → 1/3 efficient
        assert score < 0.5

    def test_failed_calls_reduce_score(self):
        signatures = ["read_file|path=a", "read_file|path=b", "read_file|path=c"]
        score = score_tool_efficiency(signatures, failed_tool_calls=2, total_tool_calls=3)
        # 2 failures out of 3 → 1/3 effective
        assert score < 0.5

    def test_zero_score_clamped(self):
        signatures = ["x", "x"]
        score = score_tool_efficiency(signatures, failed_tool_calls=2, total_tool_calls=2)
        assert score == 0.0


class TestTokenBurnRateScorer:
    def test_no_responses_returns_1(self):
        assert score_token_burn_rate([], 0) == 1.0

    def test_normal_burn_rate(self):
        # ~400 tokens per iteration = 1600 chars
        normal = "x" * 1600
        rate = score_token_burn_rate([normal], iteration_count=1)
        assert 0.9 < rate < 1.1

    def test_high_burn_rate_detected(self):
        # 10x the expected amount
        verbose = "x" * 16000
        rate = score_token_burn_rate([verbose], iteration_count=1)
        assert rate > 2.0
        assert is_abnormal_burn(rate) is True

    def test_low_burn_rate_detected(self):
        tiny = "x" * 50
        rate = score_token_burn_rate([tiny], iteration_count=1)
        assert rate < 0.3
        assert is_abnormal_burn(rate) is True

    def test_normal_rate_not_abnormal(self):
        assert is_abnormal_burn(1.0) is False
        assert is_abnormal_burn(1.5) is False


class TestContextCoherenceScorer:
    def test_no_responses_returns_1(self):
        assert score_context_coherence([]) == 1.0

    def test_coherent_responses(self):
        responses = [
            "Searching for the config file.",
            "Found it at src/config.yaml.",
            "The file contains valid YAML.",
        ]
        score = score_context_coherence(responses)
        assert score == 1.0

    def test_contradiction_signals_reduce_score(self):
        responses = [
            "The answer is X.",
            "Actually, wait, let me reconsider.",  # contradiction signals
        ]
        score = score_context_coherence(responses)
        assert score < 1.0

    def test_many_contradictions_low_score(self):
        responses = [
            "Starting over with a different approach.",
            "Actually no, let me retry that.",
            "Sorry, I was wrong, scratch that.",
        ]
        score = score_context_coherence(responses)
        assert score <= 0.5

    def test_german_contradiction_signals(self):
        responses = [
            "Moment, eigentlich war das falsch.",
            "Nochmal von vorne mit anderem Ansatz.",
        ]
        score = score_context_coherence(responses)
        assert score < 1.0


# ═════════════════════════════════════════════════════════════════════════════
# Phase 4: DriftMonitor Tests
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def monitor():
    return DriftMonitor()


class TestDriftMonitor:
    def test_healthy_state_returns_low_risk(self, monitor):
        state = IterationState(session_id="s1", original_goal="find MyClass config file parameters")
        state.iteration = 1
        # Response must be long enough to avoid low-burn-rate detection
        # (baseline ~400 tokens/iter = 1600 chars; below 30% is flagged abnormal)
        long_response = (
            "MyClass config file located at src/main/java/MyClass.java with valid parameters. "
            "The class contains properly formatted configuration entries and all required fields "
            "are present and correctly typed. No issues detected during verification. "
            "The parameters validate successfully against the expected schema. "
            "File size is appropriate and contents are well-structured with proper Java conventions. "
            "MyClass exposes the standard configuration interface and follows established patterns."
        ) * 3
        state.responses = [long_response]
        state.tool_calls_count = 2
        state.tool_call_signatures = ["search|q=MyClass", "read_file|path=src/MyClass.java"]
        state.failed_tool_calls = 0

        assessment = monitor.evaluate(state, state.responses[-1])
        assert assessment.risk_level == DriftRiskLevel.LOW
        assert assessment.recommendation == "continue"

    def test_redundant_tool_calls_trigger_medium(self, monitor):
        state = IterationState(session_id="s1", original_goal="find MyClass")
        state.iteration = 2
        state.responses = [
            "Searching... MyClass found at src/MyClass.java. Task identified.",
            "Still looking for MyClass at src/MyClass.java. Search ongoing.",
        ]
        # Many redundant signatures
        state.tool_call_signatures = [
            "search|q=MyClass", "search|q=MyClass", "search|q=MyClass", "search|q=MyClass",
        ]
        state.tool_calls_count = 4
        state.failed_tool_calls = 0

        assessment = monitor.evaluate(state, state.responses[-1])
        assert assessment.risk_level in (DriftRiskLevel.MEDIUM, DriftRiskLevel.HIGH)
        assert assessment.tool_efficiency < 0.5

    def test_high_drift_with_multiple_signals(self, monitor):
        state = IterationState(session_id="s1", original_goal="optimize build pipeline")
        state.iteration = 3
        state.responses = [
            "Actually, wait, let me reconsider. Starting over.",
            "Sorry, that was incorrect. Let me retry.",
            "The weather today is very nice.",  # Drifted from goal
        ]
        state.tool_call_signatures = ["x", "x", "x", "x"]
        state.tool_calls_count = 4
        state.failed_tool_calls = 3

        assessment = monitor.evaluate(state, state.responses[-1])
        assert assessment.risk_level == DriftRiskLevel.HIGH
        assert assessment.recommendation == "stop"
        assert len(assessment.reasons) >= 2

    def test_empty_state_is_healthy(self, monitor):
        state = IterationState(session_id="s1", original_goal="test")
        assessment = monitor.evaluate(state, "")
        # goal_alignment=0 (empty response) but other scorers return 1.0 (no data)
        # Empty response + non-empty goal → critical goal_alignment
        assert assessment.risk_level in (DriftRiskLevel.HIGH, DriftRiskLevel.MEDIUM)


class TestDriftAssessmentModel:
    def test_healthy_factory(self):
        h = DriftAssessment.healthy()
        assert h.risk_level == DriftRiskLevel.LOW
        assert h.goal_alignment == 1.0
        assert h.recommendation == "continue"

    def test_to_dict(self):
        a = DriftAssessment(
            risk_level=DriftRiskLevel.MEDIUM,
            goal_alignment=0.7,
            tool_efficiency=0.6,
            token_burn_rate=1.5,
            context_coherence=0.8,
            recommendation="warn",
            reasons=["goal_alignment low"],
        )
        d = a.to_dict()
        assert d["risk_level"] == "medium"
        assert d["goal_alignment"] == 0.7
        assert d["recommendation"] == "warn"
        assert d["reasons"] == ["goal_alignment low"]


class TestIterationStateRecordToolCall:
    def test_record_tool_call_tracks_signature(self):
        state = IterationState(session_id="s1", original_goal="test")
        state.record_tool_call("read_file", {"path": "/tmp/x.txt"}, success=True)
        assert state.tool_calls_count == 1
        assert state.failed_tool_calls == 0
        assert len(state.tool_call_signatures) == 1
        assert "read_file" in state.tool_call_signatures[0]
        assert "/tmp/x.txt" in state.tool_call_signatures[0]

    def test_record_failed_tool_call(self):
        state = IterationState(session_id="s1", original_goal="test")
        state.record_tool_call("read_file", {"path": "/missing"}, success=False)
        assert state.failed_tool_calls == 1


# ═════════════════════════════════════════════════════════════════════════════
# Controller Integration Tests (Phase 3 + 4 together)
# ═════════════════════════════════════════════════════════════════════════════


def _make_mock_orchestrator(event_sequences: List[List[AgentEvent]]):
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


async def _collect_events(async_gen: AsyncGenerator[AgentEvent, None]) -> List[AgentEvent]:
    events = []
    async for e in async_gen:
        events.append(e)
    return events


@pytest.mark.asyncio
class TestControllerIntegration:
    async def test_task_classification_logged_in_events(self):
        """Iteration complete event should include task_type from classifier."""
        events_iter1 = [
            AgentEvent(AgentEventType.TOKEN, "Search result:"),
            AgentEvent(AgentEventType.TOKEN, "<promise>Task: find. Status: COMPLETE. Result: found</promise>"),
        ]
        orchestrator = _make_mock_orchestrator([events_iter1])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=3, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test",
                user_message="Find MyClass in the project",  # → SEARCH task type
                config=config,
            )
        )

        iteration_done = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "iteration_complete"
        ]
        assert len(iteration_done) >= 1
        assert iteration_done[0].data.get("task_type") == TaskType.SEARCH.value

    async def test_tier2_completion_without_promise_tag(self):
        """Agent emits criteria-matching response (no promise tag) → Tier 2 completes."""
        events_iter1 = [AgentEvent(AgentEventType.TOKEN, "Starting search...")]
        events_iter2 = [AgentEvent(AgentEventType.TOKEN, "I found 3 matches in src/main.")]
        orchestrator = _make_mock_orchestrator([events_iter1, events_iter2])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(enabled=True, max_iterations=5, max_seconds=30.0)

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test",
                user_message="Find all TODO markers",
                config=config,
            )
        )

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert len(final) == 1
        # Should stop at iter 2 via Tier 2
        assert final[0].data["total_iterations"] == 2
        assert final[0].data["reason"] == CompletionReason.CRITERIA_MATCH.value

    async def test_drift_events_emitted(self):
        """Controller should emit drift_detected events when drift risk is non-LOW."""
        # Drifted response: no goal keywords, has contradictions
        events_iter1 = [AgentEvent(AgentEventType.TOKEN, "Actually wait, let me reconsider. Starting over.")]
        events_iter2 = [AgentEvent(AgentEventType.TOKEN, "Sorry I was wrong. Something about weather.")]
        events_iter3 = [AgentEvent(AgentEventType.TOKEN, "<promise>Task: x. Status: COMPLETE. Result: ok</promise>")]
        orchestrator = _make_mock_orchestrator([events_iter1, events_iter2, events_iter3])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(
            enabled=True,
            max_iterations=5,
            max_seconds=30.0,
            enable_drift_monitoring=True,
        )

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test",
                user_message="Optimize database queries for performance",
                config=config,
            )
        )

        drift_events = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "drift_detected"
        ]
        # Should have at least one drift event given the drifted content
        assert len(drift_events) >= 1
        # Risk level should be medium or high
        assert drift_events[0].data["risk_level"] in ("medium", "high")

    async def test_drift_monitoring_disabled(self):
        """With enable_drift_monitoring=False, no drift events should be emitted."""
        events_iter1 = [AgentEvent(AgentEventType.TOKEN, "<promise>Task: x. Status: COMPLETE. Result: ok</promise>")]
        orchestrator = _make_mock_orchestrator([events_iter1])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(
            enabled=True,
            max_iterations=3,
            max_seconds=30.0,
            enable_drift_monitoring=False,  # disabled
        )

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test", user_message="do something", config=config,
            )
        )

        drift_events = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "drift_detected"
        ]
        assert len(drift_events) == 0

    async def test_stop_on_high_drift_exits_loop(self):
        """stop_on_high_drift=True should cause loop exit when risk=HIGH."""
        # Response triggers HIGH drift (contradictions + no goal tokens + failed tools)
        events_drifted = [
            AgentEvent(AgentEventType.TOOL_START, {"name": "read_file", "arguments": {"path": "x"}}),
            AgentEvent(AgentEventType.TOOL_RESULT, {"success": False, "name": "read_file"}),
            AgentEvent(AgentEventType.TOOL_START, {"name": "read_file", "arguments": {"path": "x"}}),
            AgentEvent(AgentEventType.TOOL_RESULT, {"success": False, "name": "read_file"}),
            AgentEvent(
                AgentEventType.TOKEN,
                "Actually wait, let me retry. Sorry I was wrong. Starting over. "
                "The weather is nice.",  # No goal tokens, contradictions
            ),
        ]
        # Would continue normally but HIGH drift detection should stop it
        orchestrator = _make_mock_orchestrator([events_drifted, events_drifted])
        controller = ContinuationController(orchestrator=orchestrator)
        config = ContinuationConfig(
            enabled=True,
            max_iterations=5,
            max_seconds=30.0,
            enable_drift_monitoring=True,
            stop_on_high_drift=True,  # Enable stop on HIGH
        )

        events = await _collect_events(
            controller.execute_with_continuation(
                session_id="test",
                user_message="optimize complex database query performance tuning",
                config=config,
            )
        )

        final = [
            e for e in events
            if isinstance(e.data, dict) and e.data.get("event") == "continuation_complete"
        ]
        assert len(final) == 1
        # Should stop early due to drift (not reach max_iterations)
        # Either DRIFT_STOP or PROMISE_TAG/CRITERIA_MATCH — but not MAX_ITERATIONS
        assert final[0].data["reason"] != CompletionReason.MAX_ITERATIONS.value
