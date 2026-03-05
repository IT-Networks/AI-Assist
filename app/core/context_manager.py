from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.config import settings
from app.services.llm_client import SYSTEM_PROMPT
from app.utils.token_counter import estimate_messages_tokens, estimate_tokens, truncate_text_to_tokens


@dataclass
class ContextAttachment:
    label: str
    content: str
    priority: int = 5  # lower = higher priority; kept when trimming


# In-memory session store: session_id -> list of {role, content} messages
_sessions: Dict[str, List[dict]] = {}


def get_history(session_id: str) -> List[dict]:
    return _sessions.get(session_id, [])


def add_message(session_id: str, role: str, content: str) -> None:
    if session_id not in _sessions:
        _sessions[session_id] = []
    _sessions[session_id].append({"role": role, "content": content})
    # Keep at most N messages to avoid unbounded growth
    max_msgs = settings.context.max_tokens // 200  # rough cap
    if len(_sessions[session_id]) > max_msgs:
        _sessions[session_id] = _sessions[session_id][-max_msgs:]


def clear_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def build_messages(
    session_id: str,
    user_message: str,
    attachments: Optional[List[ContextAttachment]] = None,
) -> List[dict]:
    """
    Assemble the full messages list for the LLM.
    Priority (kept last when trimming):
      1. System prompt
      2. User message (current)
      3. Attachments (sorted by priority)
      4. Conversation history (oldest trimmed first)
    """
    max_tokens = settings.context.max_tokens
    attachments = sorted(attachments or [], key=lambda a: a.priority)

    # Build context block from attachments
    context_block = _build_context_block(attachments, max_tokens // 2)

    messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if context_block:
        messages.append({"role": "system", "content": context_block})

    # Add conversation history
    history = get_history(session_id)
    messages.extend(history)

    # Add current user message
    messages.append({"role": "user", "content": user_message})

    # Trim if over budget
    messages = _trim_to_budget(messages, max_tokens)

    return messages


def _build_context_block(attachments: List[ContextAttachment], max_tokens: int) -> str:
    if not attachments:
        return ""

    parts = ["=== BEIGEFÜGTER KONTEXT ===\n"]
    used_tokens = estimate_tokens(parts[0])
    max_per_item = max_tokens // max(len(attachments), 1)

    for att in attachments:
        content = truncate_text_to_tokens(att.content, max_per_item)
        section = f"\n[{att.label}]\n{content}\n"
        used_tokens += estimate_tokens(section)
        if used_tokens > max_tokens:
            break
        parts.append(section)

    parts.append("\n=== ENDE KONTEXT ===")
    return "".join(parts)


def _trim_to_budget(messages: List[dict], max_tokens: int) -> List[dict]:
    """Remove oldest history messages (but never system or last user) until within budget."""
    while estimate_messages_tokens(messages) > max_tokens and len(messages) > 3:
        # Find first non-system message that isn't the last one
        for i, msg in enumerate(messages):
            if msg["role"] != "system" and i < len(messages) - 1:
                messages.pop(i)
                break
        else:
            break
    return messages
