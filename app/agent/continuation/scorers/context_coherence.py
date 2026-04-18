"""
Context Coherence Scorer - Detects contradiction/oscillation between iterations.

Phase 4 MVP: Pattern-based detection of "back-tracking" language.
- Check for contradiction markers ("actually", "wait", "no, actually")
- Check if agent mentions "starting over" / "retry" / "different approach"
- Score: lower = more incoherent (agent changing direction)

These signals often indicate the agent is confused or drifting.
"""

import re
from typing import List

# Pre-compiled patterns — contradiction / backtracking language
_CONTRADICTION_PATTERNS = [
    # English
    re.compile(r"\b(?:actually|wait|no,?\s+actually|sorry,?\s+i|let\s+me\s+reconsider)\b", re.IGNORECASE),
    re.compile(r"\b(?:starting\s+over|different\s+approach|try\s+again|let\s+me\s+retry)\b", re.IGNORECASE),
    re.compile(r"\b(?:i\s+was\s+wrong|that\s+was\s+incorrect|mistake|scratch\s+that)\b", re.IGNORECASE),
    # German
    re.compile(r"\b(?:moment|warte|eigentlich|korrigier|falsch|nochmal)\b", re.IGNORECASE),
    re.compile(r"\b(?:von\s+vorne|anderer\s+ansatz|neuer\s+versuch)\b", re.IGNORECASE),
    re.compile(r"\b(?:entschuldig(?:ung|ung,?\s+ich))\b", re.IGNORECASE),
]


def _count_contradiction_signals(text: str) -> int:
    """Count matches of contradiction patterns in text."""
    if not text:
        return 0
    return sum(1 for p in _CONTRADICTION_PATTERNS if p.search(text))


def score_context_coherence(responses: List[str]) -> float:
    """
    Score context coherence across iteration responses.

    Args:
        responses: All iteration responses so far

    Returns:
        Score in [0.0, 1.0]. 1.0 = fully coherent (no contradictions).
        Scoring:
        - 0 signals: 1.0 (coherent)
        - 1 signal: 0.8 (minor concern)
        - 2-3 signals: 0.5 (moderate drift)
        - 4+ signals: 0.2 (strong drift)

        Returns 1.0 if no responses yet.
    """
    if not responses:
        return 1.0

    # Only check the most recent 3 iterations (recent drift matters most)
    recent = responses[-3:]
    total_signals = sum(_count_contradiction_signals(r) for r in recent)

    if total_signals == 0:
        return 1.0
    if total_signals == 1:
        return 0.8
    if total_signals <= 3:
        return 0.5
    return 0.2
