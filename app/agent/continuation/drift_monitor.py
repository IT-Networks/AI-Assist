"""
Drift Monitor - Composes scorers into a unified DriftAssessment.

Phase 4 MVP:
- Runs all 4 scorers (goal_alignment, tool_efficiency, token_burn, context_coherence)
- Aggregates into DriftRiskLevel (LOW/MEDIUM/HIGH) based on score thresholds
- Returns recommendation ("continue" | "warn" | "stop")

Observer pattern: Does NOT block iteration unless risk=HIGH AND config allows.
Primary purpose is observability, not gating.
"""

import logging
from typing import List, Optional

from app.agent.continuation.models import (
    DriftAssessment,
    DriftRiskLevel,
    IterationState,
)
from app.agent.continuation.scorers import (
    score_context_coherence,
    score_goal_alignment,
    score_token_burn_rate,
    score_tool_efficiency,
)
from app.agent.continuation.scorers.token_burn import is_abnormal_burn

logger = logging.getLogger(__name__)


class DriftMonitor:
    """
    Composes drift scorers and produces a DriftAssessment.

    Thresholds are tuned to be conservative (avoid false positives):
    - HIGH risk: Only if multiple strong signals
    - MEDIUM risk: Single concerning signal
    - LOW risk: All healthy
    """

    # Individual scorer thresholds
    GOAL_ALIGNMENT_LOW = 0.75       # Below = concerning
    GOAL_ALIGNMENT_CRITICAL = 0.40  # Below = critical
    TOOL_EFFICIENCY_LOW = 0.60
    TOOL_EFFICIENCY_CRITICAL = 0.30
    CONTEXT_COHERENCE_LOW = 0.60
    CONTEXT_COHERENCE_CRITICAL = 0.30

    def __init__(self) -> None:
        pass

    def evaluate(
        self,
        state: IterationState,
        current_response: str,
    ) -> DriftAssessment:
        """
        Evaluate drift based on current state and response.

        Args:
            state: Iteration state with accumulated metrics
            current_response: Latest iteration response

        Returns:
            DriftAssessment with risk_level and recommendation
        """
        # Compute all scores (all defensive — return neutral on empty data)
        goal_alignment = score_goal_alignment(state.original_goal, current_response)
        tool_efficiency = score_tool_efficiency(
            tool_call_signatures=state.tool_call_signatures,
            failed_tool_calls=state.failed_tool_calls,
            total_tool_calls=state.tool_calls_count,
        )
        token_burn_rate = score_token_burn_rate(
            responses=state.responses,
            iteration_count=state.iteration,
        )
        context_coherence = score_context_coherence(state.responses)

        # Collect reasons for each concerning score
        reasons: List[str] = []
        critical_signals = 0
        medium_signals = 0

        if goal_alignment < self.GOAL_ALIGNMENT_CRITICAL:
            reasons.append(f"goal_alignment critical: {goal_alignment:.2f}")
            critical_signals += 1
        elif goal_alignment < self.GOAL_ALIGNMENT_LOW:
            reasons.append(f"goal_alignment low: {goal_alignment:.2f}")
            medium_signals += 1

        if tool_efficiency < self.TOOL_EFFICIENCY_CRITICAL:
            reasons.append(f"tool_efficiency critical: {tool_efficiency:.2f}")
            critical_signals += 1
        elif tool_efficiency < self.TOOL_EFFICIENCY_LOW:
            reasons.append(f"tool_efficiency low: {tool_efficiency:.2f}")
            medium_signals += 1

        if is_abnormal_burn(token_burn_rate):
            reasons.append(f"token_burn anomaly: {token_burn_rate:.2f}x expected")
            medium_signals += 1

        if context_coherence < self.CONTEXT_COHERENCE_CRITICAL:
            reasons.append(f"context_coherence critical: {context_coherence:.2f}")
            critical_signals += 1
        elif context_coherence < self.CONTEXT_COHERENCE_LOW:
            reasons.append(f"context_coherence low: {context_coherence:.2f}")
            medium_signals += 1

        # Risk level aggregation:
        # - HIGH: >=1 critical OR >=3 medium signals
        # - MEDIUM: 1-2 medium signals
        # - LOW: no signals
        if critical_signals >= 1 or medium_signals >= 3:
            risk_level = DriftRiskLevel.HIGH
            recommendation = "stop"
        elif medium_signals >= 1:
            risk_level = DriftRiskLevel.MEDIUM
            recommendation = "warn"
        else:
            risk_level = DriftRiskLevel.LOW
            recommendation = "continue"

        assessment = DriftAssessment(
            risk_level=risk_level,
            goal_alignment=goal_alignment,
            tool_efficiency=tool_efficiency,
            token_burn_rate=token_burn_rate,
            context_coherence=context_coherence,
            recommendation=recommendation,
            reasons=reasons,
        )

        if risk_level != DriftRiskLevel.LOW:
            logger.info(
                f"[drift] session={state.session_id} iter={state.iteration} "
                f"risk={risk_level.value} reasons={reasons}"
            )

        return assessment


_singleton: Optional[DriftMonitor] = None


def get_drift_monitor() -> DriftMonitor:
    """Get singleton DriftMonitor instance."""
    global _singleton
    if _singleton is None:
        _singleton = DriftMonitor()
    return _singleton
