from typing import List


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: List[dict]) -> int:
    """Estimate total tokens for a list of messages."""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        total += 4  # overhead per message (role, formatting)
    return total


def truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[... Text gekürzt ...]"
