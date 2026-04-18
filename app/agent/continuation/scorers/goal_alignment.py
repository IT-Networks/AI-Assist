"""
Goal Alignment Scorer - Measures how well current response relates to original goal.

Phase 4 MVP: Token-overlap heuristic (deterministic, no ML).
- Extract meaningful tokens (length > 3) from both goal and response
- Compute Jaccard-like similarity: |goal ∩ response| / |goal|
- Returns score in [0.0, 1.0]

Future enhancement: Embedding-based cosine similarity.
"""

import re
from typing import Set

# Stopwords to exclude from token matching (German + English common words)
_STOPWORDS = frozenset({
    # German
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "und", "oder", "aber", "wenn", "dann", "dass", "weil", "für", "von", "mit", "auf", "aus",
    "ist", "sind", "war", "waren", "wird", "werden", "hat", "haben", "kann", "sollte", "muss",
    "nicht", "auch", "noch", "nur", "sehr", "mehr", "alle", "alles",
    # English
    "the", "and", "or", "but", "if", "then", "that", "because", "for", "with", "from", "to",
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "do", "does", "did",
    "not", "also", "only", "very", "more", "all", "some", "any",
})


def _extract_tokens(text: str) -> Set[str]:
    """Extract meaningful tokens from text (lowercase, len>3, non-stopword)."""
    if not text:
        return set()
    # Split on non-word characters, keep Unicode letters/digits
    words = re.findall(r"[a-zA-ZäöüÄÖÜßа-яА-Я0-9_]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def score_goal_alignment(original_goal: str, current_response: str) -> float:
    """
    Score how well current response aligns with the original goal.

    Args:
        original_goal: The user's original request
        current_response: The agent's current iteration response

    Returns:
        Score in [0.0, 1.0]. 1.0 = perfect alignment (all goal tokens in response).
        0.5 is a reasonable threshold; below suggests drift.

        Special cases:
        - Empty goal → 1.0 (no drift possible)
        - Empty response → 0.0 (no content to assess)
    """
    if not original_goal or not original_goal.strip():
        return 1.0
    if not current_response or not current_response.strip():
        return 0.0

    goal_tokens = _extract_tokens(original_goal)
    response_tokens = _extract_tokens(current_response)

    if not goal_tokens:
        return 1.0  # Goal has no meaningful tokens, can't score drift

    overlap = goal_tokens & response_tokens
    # Jaccard-ish: how many goal tokens appear in response?
    score = len(overlap) / len(goal_tokens)
    return min(score, 1.0)
