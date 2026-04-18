"""
Task Classifier - Classifies user messages into TaskType for criteria-based
completion matching.

Phase 3 MVP: Keyword-based classification (deterministic, fast, no LLM call).

Classification is used by Tier 2 completion detection to select the right
completion signal patterns. Misclassification degrades to GENERIC which
still matches common completion phrases.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

from app.agent.continuation.models import TaskType

logger = logging.getLogger(__name__)


# Keyword → TaskType mapping.
# Keywords are lowercase-matched against the user message.
# Order matters: more-specific categories (optimization) before generic (analysis).
_KEYWORD_RULES: List[Tuple[TaskType, List[str]]] = [
    (TaskType.OPTIMIZATION, [
        "optim", "verbess", "beschleunig", "faster", "schneller",
        "performance", "speed up", "reduce time", "zeit reduzier",
        "refactor", "refaktor",
    ]),
    (TaskType.GENERATION, [
        "generier", "generate", "schreib", "write", "erstell", "create",
        "baue", "build", "implement", "implementier",
        "test schreiben", "write tests", "neue datei",
    ]),
    (TaskType.ANALYSIS, [
        "analysier", "analyze", "untersuch", "review", "prüf",
        "check", "audit", "evaluate", "bewert", "verstehen",
        "erkläre", "explain", "wie funktioniert", "how does",
        "finde probleme", "identify issues",
    ]),
    (TaskType.SEARCH, [
        "find", "finde", "search", "such", "locate", "wo ist",
        "where is", "grep", "zeige alle", "show all",
        "liste alle", "list all",
    ]),
    (TaskType.READ, [
        "lies", "read", "öffne", "open", "zeig mir", "show me",
        "display", "anzeige", "inhalt von", "content of",
    ]),
]


class TaskClassifier:
    """
    Classifies user messages into TaskType via keyword matching.

    Stateless: Pure function from message → TaskType + confidence.
    """

    def classify(self, user_message: str) -> Tuple[TaskType, float]:
        """
        Classify a user message.

        Args:
            user_message: The user's request

        Returns:
            Tuple of (TaskType, confidence in [0.0, 1.0])
        """
        if not user_message or not user_message.strip():
            return TaskType.GENERIC, 0.0

        # Normalize: lowercase, strip punctuation
        normalized = user_message.lower().strip()

        # Count hits per task type
        hits: Dict[TaskType, int] = {}
        for task_type, keywords in _KEYWORD_RULES:
            count = sum(1 for kw in keywords if kw in normalized)
            if count > 0:
                hits[task_type] = count

        if not hits:
            logger.debug(f"[classifier] No keywords matched, defaulting to GENERIC: {user_message[:60]!r}")
            return TaskType.GENERIC, 0.0

        # Pick task type with most hits (first rule wins on tie due to dict iteration order)
        best_type = max(hits, key=lambda k: hits[k])
        best_count = hits[best_type]

        # Confidence heuristic:
        # - 1 match: moderate confidence 0.5
        # - 2+ matches: high confidence 0.8-0.95
        # - hits dominate other task types: +0.05 bonus
        confidence = 0.5 if best_count == 1 else min(0.8 + 0.05 * (best_count - 1), 0.95)
        if len(hits) == 1:
            confidence = min(confidence + 0.05, 1.0)

        logger.debug(
            f"[classifier] Classified as {best_type.value} (conf={confidence:.2f}) "
            f"hits={ {k.value: v for k, v in hits.items()} } msg={user_message[:60]!r}"
        )

        return best_type, confidence


_singleton: Optional[TaskClassifier] = None


def get_classifier() -> TaskClassifier:
    """Get singleton TaskClassifier instance."""
    global _singleton
    if _singleton is None:
        _singleton = TaskClassifier()
    return _singleton


def classify_task(user_message: str) -> Tuple[TaskType, float]:
    """Convenience function using the singleton classifier."""
    return get_classifier().classify(user_message)
