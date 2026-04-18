"""
Completion Signals (Tier 2) - Task-type-specific regex patterns.

When agent doesn't emit explicit Promise Tag but the response clearly
indicates task completion (e.g. "found 5 results", "analysis complete"),
these patterns detect it.

Patterns are pre-compiled for performance. Order within each list matters:
most-specific patterns first.
"""

import re
from typing import Dict, List, Pattern

from app.agent.continuation.models import TaskType

# ═════════════════════════════════════════════════════════════════════════════
# Per-TaskType regex patterns
# Must be lowercase — response is normalized before matching.
# Patterns cover both German and English common agent outputs.
# ═════════════════════════════════════════════════════════════════════════════

_PATTERNS_RAW: Dict[TaskType, List[str]] = {
    TaskType.SEARCH: [
        r"found\s+\d+\s+(?:results?|matches?|files?|entries)",
        r"gefunden:?\s+\d+",
        r"\d+\s+(?:ergebnisse|treffer|dateien)\s+gefunden",
        r"(?:located|found)\s+at\s+[/\\]",
        r"(?:liegt|befindet sich)\s+(?:unter|in)\s+[/\\]",
        r"no\s+(?:results?|matches?)\s+found",
        r"keine\s+(?:ergebnisse|treffer)\s+gefunden",
        r"search\s+returned\s+\d+",
    ],
    TaskType.READ: [
        r"(?:file|datei|code)\s+(?:contains|enthält|shows|zeigt)",
        r"(?:content|inhalt)(?:\s+of\s+|s?:\s+)```",
        r"here\s+(?:is|are|'s)\s+the\s+(?:content|file|code)",
        r"hier\s+(?:ist|sind)\s+(?:der\s+inhalt|die\s+datei|der\s+code)",
        r"```[a-z]*\n",  # Code fence — strong signal of content being shown
    ],
    TaskType.ANALYSIS: [
        r"analysis\s+(?:shows|reveals|indicates|complete)",
        r"analyse\s+(?:zeigt|ergibt|abgeschlossen)",
        r"(?:findings?|ergebnisse?)\s*:\s*",
        r"(?:conclusion|fazit|zusammenfassung)\s*:\s*",
        r"(?:summary|überblick)\s*:\s*",
        r"(?:key\s+findings|wichtigste\s+erkenntnisse)",
        r"(?:identified|identifiziert)\s+\d+\s+(?:issues?|probleme?)",
    ],
    TaskType.OPTIMIZATION: [
        r"optimiz(?:ed|ation)\s+(?:by|to|results?\s+in)",
        r"optimier(?:t|ung)\s+(?:um|auf|durch)",
        r"(?:improvement|verbesserung)\s*:\s*",
        r"reduced?\s+(?:from\s+)?[\d.]+\s*(?:s|ms|%)\s+to\s+[\d.]+",
        r"(?:von|from)\s+[\d.]+\s*(?:s|ms|%)\s+(?:auf|to)\s+[\d.]+",
        r"(?:\d+%\s+faster)|(?:\d+%\s+schneller)",
        r"performance\s+(?:improved|verbessert)",
    ],
    TaskType.GENERATION: [
        r"here\s+(?:is|are|'s)\s+the\s+(?:generated|created|written)",
        r"hier\s+(?:ist|sind)\s+(?:der\s+generierte|die\s+erstellte)",
        r"(?:generated|created|written)\s+(?:file|class|function|test|method)",
        r"(?:generiert|erstellt|geschrieben):\s+",
        r"```(?:java|python|javascript|typescript|go|rust)\n[\s\S]{50,}",  # substantial code block
    ],
    TaskType.GENERIC: [
        r"(?:that'?s|that\s+is)\s+(?:complete|done|finished|all)",
        r"(?:task|aufgabe)\s+(?:is\s+)?(?:complete|abgeschlossen|erledigt)",
        r"(?:all\s+done|alles\s+erledigt|fertig)",
    ],
}


def _compile_patterns(raw: Dict[TaskType, List[str]]) -> Dict[TaskType, List[Pattern[str]]]:
    """Pre-compile all regex patterns for fast repeated matching."""
    compiled: Dict[TaskType, List[Pattern[str]]] = {}
    for task_type, patterns in raw.items():
        compiled[task_type] = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns]
    return compiled


# Compile once at module load
COMPLETION_SIGNALS: Dict[TaskType, List[Pattern[str]]] = _compile_patterns(_PATTERNS_RAW)


def get_signals_for_task(task_type: TaskType) -> List[Pattern[str]]:
    """
    Return compiled regex patterns for a task type, including generic fallback.

    Generic patterns are always included as a fallback to catch common
    completion phrases regardless of task type.
    """
    specific = COMPLETION_SIGNALS.get(task_type, [])
    generic = COMPLETION_SIGNALS[TaskType.GENERIC] if task_type != TaskType.GENERIC else []
    return specific + generic


def match_completion_signal(response: str, task_type: TaskType) -> str:
    """
    Check if response matches any completion signal for the given task type.

    Args:
        response: Agent response text
        task_type: Classified task type

    Returns:
        Matched pattern string (evidence) if found, empty string otherwise
    """
    if not response:
        return ""

    patterns = get_signals_for_task(task_type)
    for pattern in patterns:
        match = pattern.search(response)
        if match:
            return match.group(0)[:100]  # Cap evidence length
    return ""
