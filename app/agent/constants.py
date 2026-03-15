"""
Shared Constants for Agent System.

Centralizes magic strings and markers used across agent components.
"""

from typing import Set


# ══════════════════════════════════════════════════════════════════════════════
# Control Message Markers
# ══════════════════════════════════════════════════════════════════════════════

class ControlMarkers:
    """
    Control markers sent by frontend or internally to modify processing flow.

    Usage:
        if user_message.strip() == ControlMarkers.CONTINUE:
            # Handle continue after file confirmation
    """

    # After write operation confirmation
    CONTINUE = "[CONTINUE]"

    # After enhancement confirmation (MCP context approved)
    CONTINUE_ENHANCED = "[CONTINUE_ENHANCED]"

    # Skip task decomposition, process directly
    SKIP_DECOMPOSITION = "[SKIP_DECOMPOSITION]"

    # Skip all enhancement/decomposition, direct LLM call
    DIRECT = "[DIRECT]"


# ══════════════════════════════════════════════════════════════════════════════
# Skip Markers for Enhancement Detection
# ══════════════════════════════════════════════════════════════════════════════

class EnhancementSkipMarkers:
    """
    Markers that cause enhancement detection to skip.

    These can be embedded in user messages to bypass MCP enhancement.
    """

    # Explicit skip of MCP enhancement
    NO_ENHANCE = "[NO_ENHANCE]"

    # Skip MCP calls
    SKIP_MCP = "[SKIP_MCP]"

    # Direct processing (alias)
    DIRECT = "[DIRECT]"

    @classmethod
    def all(cls) -> Set[str]:
        """Returns all skip markers as a set."""
        return {cls.NO_ENHANCE, cls.SKIP_MCP, cls.DIRECT}


# ══════════════════════════════════════════════════════════════════════════════
# Task System Skip Markers
# ══════════════════════════════════════════════════════════════════════════════

class TaskSkipMarkers:
    """
    Markers that cause task decomposition to skip.
    """

    # Continue after confirmation
    CONTINUE = "[CONTINUE]"

    # Explicit skip decomposition
    SKIP_DECOMPOSITION = "[SKIP_DECOMPOSITION]"

    # Direct processing
    DIRECT = "[DIRECT]"

    @classmethod
    def all(cls) -> Set[str]:
        """Returns all skip markers as a set."""
        return {cls.CONTINUE, cls.SKIP_DECOMPOSITION, cls.DIRECT}


# ══════════════════════════════════════════════════════════════════════════════
# Combined Check Functions
# ══════════════════════════════════════════════════════════════════════════════

def should_skip_enhancement(message: str) -> bool:
    """
    Check if message contains markers that should skip MCP enhancement.

    Args:
        message: The user message to check

    Returns:
        True if enhancement should be skipped
    """
    return any(marker in message for marker in EnhancementSkipMarkers.all())


def should_skip_decomposition(message: str) -> bool:
    """
    Check if message contains markers that should skip task decomposition.

    Args:
        message: The user message to check

    Returns:
        True if decomposition should be skipped
    """
    return any(marker in message for marker in TaskSkipMarkers.all())
