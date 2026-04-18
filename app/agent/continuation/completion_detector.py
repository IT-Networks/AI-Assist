"""
Completion Detector - Multi-tier task completion detection.

Phase 1 (MVP): Tier 1 (Promise Tag) + Tier 3 (Timeout/MaxIter)
Phase 3: Tier 2 (Criteria Matching per TaskType)

Order of checks: Tier 1 > Tier 2 > Tier 3
- Tier 1 (Promise Tag): Explicit completion signal, confidence=1.0
- Tier 2 (Criteria Match): Task-type-specific regex patterns, confidence=0.7
- Tier 3 (Safety Valves): Max iterations / timeout, confidence=0.3

Stateless, thread-safe: Pure functions that check completion signals
without maintaining state between calls.
"""

import logging
import re
from typing import Optional

from app.agent.continuation.completion_signals import match_completion_signal
from app.agent.continuation.models import (
    CompletionResult,
    IterationState,
    TaskType,
)

logger = logging.getLogger(__name__)

# Pre-compiled regex for Promise Tag detection (Tier 1)
# Matches: <promise>content</promise> with DOTALL so content can span lines
_PROMISE_TAG_PATTERN = re.compile(
    r"<promise>\s*(.+?)\s*</promise>",
    re.DOTALL | re.IGNORECASE,
)


class CompletionDetector:
    """
    Detects task completion after each iteration.

    Checks (in order):
    - Tier 1: Promise Tag in response (confidence=1.0)
    - Tier 2: Task-type-specific criteria match (confidence=0.7)  [Phase 3]
    - Tier 3: Max iterations or timeout reached (confidence=0.3)
    """

    # Tier 2 requires at least this many iterations before activating.
    # Reason: Agent often produces preliminary output in iter 1 that looks
    # like completion but needs refinement. Require at least 1 iteration
    # to guard against premature Tier 2 triggering.
    _TIER_2_MIN_ITERATIONS = 1

    def check(
        self,
        response: str,
        state: IterationState,
        max_iterations: int,
        max_seconds: float,
        task_type: Optional[TaskType] = None,
        require_promise_tag: bool = False,
    ) -> CompletionResult:
        """
        Check if task is complete after an iteration.

        Order: Tier 1 → Tier 2 → Tier 3

        Args:
            response: The agent's response text from last iteration
            state: Current iteration state
            max_iterations: Hard iteration limit
            max_seconds: Hard time limit in seconds
            task_type: TaskType for Tier 2 matching (falls back to state.task_type)
            require_promise_tag: If True, skip Tier 2 (only Promise Tag or Safety Valves stop loop)

        Returns:
            CompletionResult with is_complete flag and reason
        """
        # Tier 1: Promise Tag (highest confidence)
        promise_result = self._check_promise_tag(response)
        if promise_result.is_complete:
            logger.info(
                f"[completion] Tier 1: Promise tag detected in session={state.session_id} "
                f"iter={state.iteration} evidence={promise_result.evidence[:80]!r}"
            )
            return promise_result

        # Tier 2: Criteria matching (Phase 3) — skipped when require_promise_tag=True
        if not require_promise_tag and state.iteration >= self._TIER_2_MIN_ITERATIONS:
            effective_task_type = task_type or state.task_type or TaskType.GENERIC
            tier2_result = self._check_criteria_match(response, effective_task_type)
            if tier2_result.is_complete:
                logger.info(
                    f"[completion] Tier 2: Criteria match in session={state.session_id} "
                    f"task_type={effective_task_type.value} "
                    f"iter={state.iteration} evidence={tier2_result.evidence[:80]!r}"
                )
                return tier2_result

        # Tier 3: Safety valves
        # Note: iteration limit checked AFTER current iteration ran (state.iteration is 1-based here)
        if state.iteration >= max_iterations:
            result = CompletionResult.max_iterations(state.iteration)
            logger.warning(
                f"[completion] Tier 3: Max iterations hit session={state.session_id} "
                f"iter={state.iteration}/{max_iterations}"
            )
            return result

        elapsed = state.elapsed_seconds
        if elapsed >= max_seconds:
            result = CompletionResult.timeout(elapsed)
            logger.warning(
                f"[completion] Tier 3: Timeout session={state.session_id} "
                f"elapsed={elapsed:.1f}s/{max_seconds:.1f}s"
            )
            return result

        return CompletionResult.not_complete()

    def _check_criteria_match(self, response: str, task_type: TaskType) -> CompletionResult:
        """
        Tier 2: Match response against task-type-specific completion patterns.

        Phase 3: Uses regex patterns from completion_signals module.
        Confidence: 0.7 (less certain than explicit Promise Tag).
        """
        if not response:
            return CompletionResult.not_complete()

        evidence = match_completion_signal(response, task_type)
        if evidence:
            return CompletionResult.criteria_match(
                evidence=f"[{task_type.value}] {evidence}"
            )
        return CompletionResult.not_complete()

    def _check_promise_tag(self, response: str) -> CompletionResult:
        """
        Tier 1: Check for explicit <promise>...</promise> completion signal.

        Agent is instructed (via system prompt) to emit this tag when done.
        Most reliable signal — confidence=1.0.
        """
        if not response:
            return CompletionResult.not_complete()

        match = _PROMISE_TAG_PATTERN.search(response)
        if not match:
            return CompletionResult.not_complete()

        content = match.group(1).strip()
        # Sanity check: promise content should be non-trivial
        if len(content) < 3:
            logger.debug(f"[completion] Ignoring trivial promise tag: {content!r}")
            return CompletionResult.not_complete()

        return CompletionResult.promise_tag(evidence=content)


_singleton: Optional[CompletionDetector] = None


def get_completion_detector() -> CompletionDetector:
    """Get singleton CompletionDetector instance."""
    global _singleton
    if _singleton is None:
        _singleton = CompletionDetector()
    return _singleton
