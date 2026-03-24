"""
Session Services - Sitzungs- und Kontext-Management.

Dieses Paket gruppiert Services für Session-Handling:
- ContextManager (Kontext-Aufbau für LLM)
- MemoryStore (hierarchisches Wissen)
- ChatStore (Chat-Persistenz)
- TaskTracker (Aufgaben-Tracking)
- TranscriptLogger (Konversations-Logging)

Verwendung:
    from app.services.session import get_memory_store

    store = get_memory_store()
    await store.remember("key", "value")
"""

from app.services.context_manager import (
    ContextManager,
    get_context_manager,
)

from app.services.memory_store import (
    MemoryStore,
    MemoryEntry,
    MemoryScope,
    MemoryCategory,
    MemorySource,
    get_memory_store,
)

from app.services.chat_store import (
    load_chat,
    save_chat,
    list_chats,
    delete_chat,
    update_title,
)

from app.services.task_tracker import (
    TaskTracker,
    TaskArtifact,
    get_task_tracker,
)

from app.services.transcript_logger import (
    TranscriptLogger,
    TranscriptEntry,
    get_transcript_logger,
)

__all__ = [
    # Context
    "ContextManager",
    "get_context_manager",
    # Memory
    "MemoryStore",
    "MemoryEntry",
    "MemoryScope",
    "MemoryCategory",
    "MemorySource",
    "get_memory_store",
    # Chat
    "load_chat",
    "save_chat",
    "list_chats",
    "delete_chat",
    "update_title",
    # Tasks
    "TaskTracker",
    "TaskArtifact",
    "get_task_tracker",
    # Transcript
    "TranscriptLogger",
    "TranscriptEntry",
    "get_transcript_logger",
]
