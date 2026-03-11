from typing import List, Optional


def estimate_tokens(text: Optional[str]) -> int:
    """Rough token estimate: ~4 characters per token."""
    if not text:
        return 1
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: List[dict]) -> int:
    """Estimate total tokens for a list of messages."""
    if not messages:
        return 0
    total = 0
    for msg in messages:
        content = msg.get("content") or ""  # Handles None explicitly
        total += estimate_tokens(content)
        total += 4  # overhead per message (role, formatting)
    return total


def truncate_text_to_tokens(text: Optional[str], max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    if not text:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[... Text gekürzt ...]"
