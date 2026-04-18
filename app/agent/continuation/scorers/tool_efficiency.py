"""
Tool Efficiency Scorer - Detects redundant and failed tool calls.

Phase 4 MVP:
- Track tool call signatures (tool_name + key args)
- Count duplicates (same tool with same args → redundant)
- Count failures
- Score = (successful_unique_calls / total_calls)

Low score indicates agent is spinning wheels (redundant calls) or
encountering persistent errors (failed calls).
"""

from typing import List


def score_tool_efficiency(
    tool_call_signatures: List[str],
    failed_tool_calls: int,
    total_tool_calls: int,
) -> float:
    """
    Score tool call efficiency.

    Args:
        tool_call_signatures: List of signatures (tool_name|key=value pairs) for each call
        failed_tool_calls: Count of failed tool calls
        total_tool_calls: Total count of tool calls made

    Returns:
        Score in [0.0, 1.0]. 1.0 = all calls unique and successful.
        Returns 1.0 if no tool calls yet (no inefficiency possible).
    """
    if total_tool_calls == 0:
        return 1.0

    # Count unique signatures
    unique_count = len(set(tool_call_signatures))
    duplicates = total_tool_calls - unique_count

    # Failures and duplicates both hurt efficiency
    # Weight: duplicates 1x, failures 1x (equal concern)
    wasted = duplicates + failed_tool_calls

    # Score formula: fraction of calls that were unique AND successful
    successful_unique = total_tool_calls - wasted
    score = max(0.0, successful_unique / total_tool_calls)
    return min(score, 1.0)
