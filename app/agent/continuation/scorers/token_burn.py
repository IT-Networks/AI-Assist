"""
Token Burn Rate Scorer - Detects abnormal token consumption.

Phase 4 MVP:
- Track total response tokens across iterations
- Compare against expected baseline (based on iteration count + task complexity)
- Return ratio: 1.0 = normal, >2.0 = anomaly (burning 2x expected)

Abnormal token burn often indicates:
- Agent is verbose/repetitive (low-quality output)
- Agent is trying to brute-force the problem
- Drift — agent wandered into irrelevant content
"""

from typing import List

# Rough baseline: typical agent response per iteration is ~200-500 tokens.
# We approximate tokens as characters / 4 (standard English ratio).
_AVG_TOKENS_PER_ITERATION = 400
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate from character length."""
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


def score_token_burn_rate(responses: List[str], iteration_count: int) -> float:
    """
    Score token burn rate. Returns a ratio (not bounded [0,1]).

    Args:
        responses: All iteration responses so far
        iteration_count: Number of iterations completed

    Returns:
        Ratio of actual_tokens / expected_tokens.
        - ~1.0: Normal burn rate
        - >2.0: Abnormal (HIGH drift signal)
        - <0.5: Abnormally low (may indicate truncated/errored output)

        Returns 1.0 if no data (no iterations yet).
    """
    if iteration_count == 0 or not responses:
        return 1.0

    total_tokens = sum(_estimate_tokens(r) for r in responses)
    expected_tokens = _AVG_TOKENS_PER_ITERATION * iteration_count

    if expected_tokens == 0:
        return 1.0

    ratio = total_tokens / expected_tokens
    # Clamp to reasonable range for downstream consumers
    return max(0.1, min(ratio, 10.0))


def is_abnormal_burn(ratio: float) -> bool:
    """True if ratio indicates abnormal token consumption."""
    return ratio > 2.0 or ratio < 0.3
