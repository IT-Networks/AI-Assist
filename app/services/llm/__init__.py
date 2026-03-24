"""
LLM Services - Language Model Integration.

Dieses Paket gruppiert LLM-bezogene Services:
- LLM Client (OpenAI-kompatibel)
- LLM Cache (Response-Caching)

Verwendung:
    from app.services.llm import llm_client

    response = await llm_client.chat(messages=[...])
"""

from app.services.llm_client import (
    llm_client,
    LLMClient,
    LLMResponse,
    close_http_client,
    SYSTEM_PROMPT,
    TIMEOUT_TOOL,
    TIMEOUT_ANALYSIS,
)

from app.services.llm_cache import (
    LLMCacheManager,
    get_cache_manager,
    get_cache_stats,
)

__all__ = [
    # Client
    "llm_client",
    "LLMClient",
    "LLMResponse",
    "close_http_client",
    "SYSTEM_PROMPT",
    "TIMEOUT_TOOL",
    "TIMEOUT_ANALYSIS",
    # Cache
    "LLMCacheManager",
    "get_cache_manager",
    "get_cache_stats",
]
