"""
Conversation-Package (Sprint 3, B4+C2).

Stellt Multi-Conversation-Support bereit: pro (room_id, thread_id) eine
``WebexConversation`` mit eigener Policy (AllowFrom, Model, Cap) und
persistenter Session-Binding.
"""

from app.services.webex.conversation.binding_store import (
    ConversationBinding,
    ConversationBindingStore,
)
from app.services.webex.conversation.registry import (
    ConversationRegistry,
    WebexConversation,
)
from app.services.webex.conversation.scope import (
    ConversationKey,
    ConversationPolicy,
    Scope,
)

__all__ = [
    "ConversationBinding",
    "ConversationBindingStore",
    "ConversationKey",
    "ConversationPolicy",
    "ConversationRegistry",
    "Scope",
    "WebexConversation",
]
