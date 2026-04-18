"""
Task Decomposer - Heuristically splits user messages into parallel subtasks.

Phase 5 MVP: Pattern-based decomposition (no LLM call).

Signals we look for:
1. Explicit numbered/bulleted lists: "1) Do X, 2) Do Y"
2. Conjunction separators: "Do X AND do Y AND do Z"
3. Imperative verb lists with commas: "Search X, read Y, analyze Z"
4. "sowie", "außerdem" (German "also", "additionally")

We DO NOT decompose when:
- Message is short (< 40 chars) — likely single task
- Only one imperative verb detected
- Chunks are too short to be independent tasks (< 15 chars)

Conservative bias: Better to miss a decomposition (fall back to normal
continuation loop) than to break a single task into incoherent fragments.
"""

import logging
import re
from typing import List, Optional

from app.agent.subagents.models import SubAgentTask

logger = logging.getLogger(__name__)


# Explicit numbered/bulleted item patterns.
# Matches numbered items at start of line, after colon, or after whitespace
# following a sentence-ending punctuation. Examples of matches:
#   "1. Do X"
#   "Tasks: 1. Do X"
#   "Do things: - Step 1"
#   "Do these: 1) First 2) Second"
_NUMBERED_ITEM_PATTERN = re.compile(
    r"(?:^|\n|:\s|\.\s+|\s)(\d+[.)]\s+|[-*]\s+)",
    re.MULTILINE,
)

# Conjunction separators (German + English)
# Note: "and" alone is too noisy (part of normal sentences). Require
# "and then" / "and also" patterns OR verb-and-verb pattern.
_CONJUNCTION_PATTERNS = [
    re.compile(r"\bund\s+(?:dann|außerdem|danach|anschließend)\b", re.IGNORECASE),
    re.compile(r"\band\s+(?:then|also|afterwards?|additionally)\b", re.IGNORECASE),
    re.compile(r"\bsowie\b", re.IGNORECASE),
    re.compile(r"\bas\s+well\s+as\b", re.IGNORECASE),
]

# Imperative verbs (German + English) — signal independent action
_IMPERATIVE_VERBS = frozenset({
    # German
    "finde", "suche", "lies", "öffne", "analysiere", "untersuche", "prüfe",
    "optimiere", "verbessere", "refaktoriere", "schreibe", "erstelle", "generiere",
    "zeige", "erkläre", "dokumentiere", "teste", "baue", "kompiliere",
    # English
    "find", "search", "read", "open", "analyze", "investigate", "check",
    "optimize", "improve", "refactor", "write", "create", "generate",
    "show", "explain", "document", "test", "build", "compile",
})


def _has_multiple_imperatives(text: str) -> int:
    """Count distinct imperative verbs at clause boundaries."""
    if not text:
        return 0
    # Find verbs at start of sentence/clause or after conjunctions
    tokens = re.findall(r"[a-zäöüA-ZÄÖÜ]+", text.lower())
    count = sum(1 for t in tokens if t in _IMPERATIVE_VERBS)
    return count


def _split_by_numbered_items(text: str) -> Optional[List[str]]:
    """Split on numbered/bulleted item boundaries. Returns None if no split possible."""
    matches = list(_NUMBERED_ITEM_PATTERN.finditer(text))
    if len(matches) < 2:
        return None
    # Split at each match position
    chunks: List[str] = []
    last_end = 0
    for m in matches:
        prefix = text[last_end:m.start()].strip()
        if prefix:
            chunks.append(prefix)
        last_end = m.end()
    tail = text[last_end:].strip()
    if tail:
        chunks.append(tail)
    # Remove the item marker (the first part is typically preamble)
    # Drop first chunk if it's just preamble (no imperative)
    if chunks and _has_multiple_imperatives(chunks[0]) == 0:
        chunks = chunks[1:]
    return chunks if len(chunks) >= 2 else None


def _split_by_conjunctions(text: str) -> Optional[List[str]]:
    """Split on conjunction patterns. Returns None if no split possible."""
    # Try each conjunction pattern, return first match
    for pattern in _CONJUNCTION_PATTERNS:
        parts = pattern.split(text)
        if len(parts) >= 2:
            chunks = [p.strip() for p in parts if p.strip()]
            if len(chunks) >= 2:
                return chunks
    return None


def _split_by_semicolons_or_imperative_commas(text: str) -> Optional[List[str]]:
    """
    Split on semicolons OR commas that appear between imperative verbs.

    Only activates when:
    - Text has 2+ imperative verbs
    - Commas separate segments starting with verbs
    """
    imperative_count = _has_multiple_imperatives(text)
    if imperative_count < 2:
        return None

    # First try semicolons (strong signal)
    if ";" in text:
        chunks = [p.strip() for p in text.split(";") if p.strip()]
        if len(chunks) >= 2:
            return chunks

    # Try comma-separated imperative phrases
    # Example: "Search the code, read the config, analyze performance"
    comma_chunks = [p.strip() for p in text.split(",") if p.strip()]
    if len(comma_chunks) >= 2:
        # Check that most chunks start with an imperative verb
        verb_starters = sum(
            1 for c in comma_chunks
            if c and c.split()[0].lower() in _IMPERATIVE_VERBS
        )
        if verb_starters >= 2 and verb_starters >= len(comma_chunks) // 2:
            return comma_chunks
    return None


def _too_short(chunks: List[str], min_chars: int = 15) -> bool:
    """Check if any chunk is too short to be a standalone task."""
    return any(len(c) < min_chars for c in chunks)


class TaskDecomposer:
    """
    Decomposes a user message into a list of SubAgentTasks.

    Stateless: pure function from message → list of tasks.
    """

    MIN_MESSAGE_LENGTH = 40          # Skip decomposition for short messages
    MIN_CHUNK_LENGTH = 15            # Minimum chars per chunk
    MAX_SUBTASKS = 10                # Safety cap

    def decompose(self, user_message: str, parent_session_id: str = "") -> List[SubAgentTask]:
        """
        Decompose a user message into subtasks.

        Args:
            user_message: The original user request
            parent_session_id: Parent session for generating worker session IDs

        Returns:
            List of SubAgentTasks. Empty list if not decomposable.
        """
        if not user_message or not user_message.strip():
            return []

        msg = user_message.strip()

        # Skip if message is too short
        if len(msg) < self.MIN_MESSAGE_LENGTH:
            logger.debug(f"[decomposer] Message too short ({len(msg)} chars), skipping")
            return []

        # Skip if only one imperative verb detected
        if _has_multiple_imperatives(msg) < 2:
            logger.debug("[decomposer] Fewer than 2 imperative verbs, skipping")
            return []

        # Try strategies in order of strongest signal
        chunks: Optional[List[str]] = (
            _split_by_numbered_items(msg)
            or _split_by_conjunctions(msg)
            or _split_by_semicolons_or_imperative_commas(msg)
        )

        if not chunks:
            logger.debug("[decomposer] No decomposition pattern matched")
            return []

        # Filter too-short chunks
        chunks = [c for c in chunks if len(c) >= self.MIN_CHUNK_LENGTH]
        if len(chunks) < 2:
            logger.debug(f"[decomposer] After filtering, <2 chunks remain: {len(chunks)}")
            return []

        # Cap at MAX_SUBTASKS
        chunks = chunks[: self.MAX_SUBTASKS]

        tasks = [
            SubAgentTask(
                description=chunk,
                parent_session_id=parent_session_id,
                specialty="",  # Phase 5 MVP: no specialty detection
            )
            for chunk in chunks
        ]

        logger.info(
            f"[decomposer] Decomposed into {len(tasks)} subtasks: "
            f"{[t.description[:40] for t in tasks]}"
        )

        return tasks


_singleton: Optional[TaskDecomposer] = None


def get_decomposer() -> TaskDecomposer:
    """Get singleton TaskDecomposer instance."""
    global _singleton
    if _singleton is None:
        _singleton = TaskDecomposer()
    return _singleton


def decompose_task(user_message: str, parent_session_id: str = "") -> List[SubAgentTask]:
    """Convenience function using the singleton decomposer."""
    return get_decomposer().decompose(user_message, parent_session_id)
