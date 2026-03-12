"""
LLM Response Caching - Proof of Concept

Nutzt LiteLLM's eingebautes Caching-System für:
- Komplexitäts-Checks (gleiche Anfrage = gleiche Komplexität)
- Sub-Agent Routing (gleiche Anfrage = gleiche Agenten)
- Schnelle Klassifikations-Calls

Features:
- In-Memory Cache (default, kein Redis nötig)
- Optional Redis für persistentes Caching
- TTL-basierte Invalidierung
- Cache-Hit-Statistiken

Usage:
    from app.services.llm_cache import cached_completion, get_cache_stats

    # Gecachter Call
    response = await cached_completion(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4",
        cache_key="my-operation"  # Optional: Custom cache key
    )

    # Statistiken
    stats = get_cache_stats()
    print(f"Hits: {stats['hits']}, Misses: {stats['misses']}")
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Cache Storage
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class CacheEntry:
    """Ein Cache-Eintrag mit TTL."""
    value: Any
    created_at: float
    ttl: int
    hits: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


@dataclass
class CacheStats:
    """Cache-Statistiken."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "size": self.size,
            "hit_rate": round(self.hits / max(1, self.hits + self.misses) * 100, 1)
        }


class LocalCache:
    """
    Einfacher In-Memory Cache mit TTL und LRU-Eviction.

    Thread-safe durch asyncio Lock.
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self._cache: Dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._stats = CacheStats()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Holt einen Wert aus dem Cache."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.misses += 1
                return None

            if entry.is_expired:
                del self._cache[key]
                self._stats.misses += 1
                self._stats.evictions += 1
                return None

            entry.hits += 1
            self._stats.hits += 1
            return entry.value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Speichert einen Wert im Cache."""
        async with self._lock:
            # Eviction wenn voll
            if len(self._cache) >= self._max_size:
                await self._evict_oldest()

            self._cache[key] = CacheEntry(
                value=value,
                created_at=time.time(),
                ttl=ttl or self._default_ttl
            )
            self._stats.size = len(self._cache)

    async def _evict_oldest(self) -> None:
        """Entfernt den ältesten Eintrag (LRU)."""
        if not self._cache:
            return

        # Finde ältesten Eintrag
        oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
        del self._cache[oldest_key]
        self._stats.evictions += 1

    async def clear(self) -> None:
        """Leert den Cache."""
        async with self._lock:
            self._cache.clear()
            self._stats.size = 0

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Cache-Statistiken zurück."""
        return self._stats.to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# Redis Cache (optional)
# ══════════════════════════════════════════════════════════════════════════════


class RedisCache:
    """
    Redis-basierter Cache für persistentes Caching.

    Benötigt: pip install redis
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: str = "",
        default_ttl: int = 300,
        prefix: str = "llm_cache:"
    ):
        self._host = host
        self._port = port
        self._password = password
        self._default_ttl = default_ttl
        self._prefix = prefix
        self._client = None
        self._stats = CacheStats()

    async def _get_client(self):
        """Lazy-initialisiert den Redis-Client."""
        if self._client is None:
            try:
                import redis.asyncio as redis
                self._client = redis.Redis(
                    host=self._host,
                    port=self._port,
                    password=self._password or None,
                    decode_responses=True
                )
                # Test connection
                await self._client.ping()
                logger.info(f"[cache] Redis connected: {self._host}:{self._port}")
            except ImportError:
                logger.warning("[cache] redis package not installed, falling back to local cache")
                raise
            except Exception as e:
                logger.warning(f"[cache] Redis connection failed: {e}")
                raise
        return self._client

    async def get(self, key: str) -> Optional[Any]:
        """Holt einen Wert aus Redis."""
        try:
            client = await self._get_client()
            value = await client.get(self._prefix + key)
            if value is None:
                self._stats.misses += 1
                return None

            self._stats.hits += 1
            return json.loads(value)
        except Exception as e:
            logger.debug(f"[cache] Redis get error: {e}")
            self._stats.misses += 1
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Speichert einen Wert in Redis."""
        try:
            client = await self._get_client()
            await client.setex(
                self._prefix + key,
                ttl or self._default_ttl,
                json.dumps(value)
            )
        except Exception as e:
            logger.debug(f"[cache] Redis set error: {e}")

    async def clear(self) -> None:
        """Löscht alle Cache-Einträge mit dem Prefix."""
        try:
            client = await self._get_client()
            keys = await client.keys(self._prefix + "*")
            if keys:
                await client.delete(*keys)
        except Exception as e:
            logger.debug(f"[cache] Redis clear error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Cache-Statistiken zurück."""
        return self._stats.to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# Cache Manager
# ══════════════════════════════════════════════════════════════════════════════


class LLMCacheManager:
    """
    Zentraler Cache-Manager für LLM-Responses.

    Unterstützt verschiedene Cache-Backends und automatische Fallbacks.
    """

    def __init__(self):
        self._cache = None
        self._enabled = False
        self._initialized = False

    async def initialize(self) -> None:
        """Initialisiert den Cache basierend auf Konfiguration."""
        if self._initialized:
            return

        config = settings.llm.cache
        self._enabled = config.enabled

        if not self._enabled:
            logger.info("[cache] LLM caching disabled")
            self._initialized = True
            return

        if config.type == "redis":
            try:
                self._cache = RedisCache(
                    host=config.redis_host,
                    port=config.redis_port,
                    password=config.redis_password,
                    default_ttl=config.ttl_seconds
                )
                # Test connection
                await self._cache._get_client()
                logger.info("[cache] Using Redis cache")
            except Exception as e:
                logger.warning(f"[cache] Redis unavailable ({e}), falling back to local cache")
                self._cache = LocalCache(
                    max_size=config.max_size,
                    default_ttl=config.ttl_seconds
                )
        else:
            self._cache = LocalCache(
                max_size=config.max_size,
                default_ttl=config.ttl_seconds
            )
            logger.info("[cache] Using local in-memory cache")

        self._initialized = True

    @property
    def enabled(self) -> bool:
        return self._enabled and self._cache is not None

    async def get(self, key: str) -> Optional[Any]:
        """Holt einen Wert aus dem Cache."""
        if not self.enabled:
            return None
        return await self._cache.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Speichert einen Wert im Cache."""
        if not self.enabled:
            return
        await self._cache.set(key, value, ttl)

    async def clear(self) -> None:
        """Leert den Cache."""
        if self._cache:
            await self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Cache-Statistiken zurück."""
        if not self._cache:
            return {"enabled": False}
        stats = self._cache.get_stats()
        stats["enabled"] = self._enabled
        stats["type"] = settings.llm.cache.type
        return stats


# Singleton
_cache_manager: Optional[LLMCacheManager] = None


async def get_cache_manager() -> LLMCacheManager:
    """Gibt den Cache-Manager Singleton zurück."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = LLMCacheManager()
        await _cache_manager.initialize()
    return _cache_manager


def get_cache_stats() -> Dict[str, Any]:
    """Synchrone Funktion für Cache-Statistiken."""
    if _cache_manager is None:
        return {"enabled": False, "initialized": False}
    return _cache_manager.get_stats()


# ══════════════════════════════════════════════════════════════════════════════
# Cached Completion Functions
# ══════════════════════════════════════════════════════════════════════════════


def _create_cache_key(
    messages: List[Dict],
    model: str,
    temperature: float,
    category: str = "default"
) -> str:
    """
    Erstellt einen Cache-Key aus den Request-Parametern.

    Der Key basiert auf:
    - Message-Content (nur user messages)
    - Model
    - Temperature
    - Kategorie
    """
    # Nur relevante Teile für den Key
    key_data = {
        "messages": [
            {"role": m.get("role"), "content": m.get("content", "")[:500]}
            for m in messages
            if m.get("role") in ("user", "system")
        ],
        "model": model,
        "temp": round(temperature, 2),
        "cat": category
    }

    # Hash für kompakten Key
    key_json = json.dumps(key_data, sort_keys=True)
    return hashlib.sha256(key_json.encode()).hexdigest()[:32]


async def cached_completion(
    messages: List[Dict],
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 256,
    category: str = "default",
    ttl: Optional[int] = None,
    skip_cache: bool = False
) -> Optional[str]:
    """
    Führt einen gecachten LLM-Call durch.

    Args:
        messages: Chat-Nachrichten
        model: Modell-ID
        temperature: Temperature (niedrig = besser cachebar)
        max_tokens: Max Tokens
        category: Cache-Kategorie (für Statistiken)
        ttl: Custom TTL (sonst aus Config)
        skip_cache: True um Cache zu umgehen

    Returns:
        Response-Content oder None bei Fehler
    """
    cache = await get_cache_manager()

    # Cache-Key erstellen
    cache_key = _create_cache_key(messages, model, temperature, category)

    # Cache-Lookup (wenn nicht übersprungen)
    if not skip_cache and cache.enabled:
        cached = await cache.get(cache_key)
        if cached is not None:
            logger.debug(f"[cache] HIT for {category}: {cache_key[:8]}...")
            return cached

    # LLM-Call durchführen
    from app.services.llm_client import llm_client

    try:
        response = await llm_client.chat_quick(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Im Cache speichern
        if cache.enabled and response:
            await cache.set(cache_key, response, ttl)
            logger.debug(f"[cache] STORE for {category}: {cache_key[:8]}...")

        return response

    except Exception as e:
        logger.warning(f"[cache] LLM call failed: {e}")
        return None


async def cached_routing_classification(
    query: str,
    model: str,
    prompt_template: str
) -> Optional[str]:
    """
    Gecachte Routing-Klassifikation für Sub-Agents.

    Gleiche Anfragen werden zu den gleichen Agenten geroutet.
    """
    if not settings.llm.cache.cache_routing:
        return None

    messages = [{"role": "user", "content": prompt_template.replace("{query}", query[:500])}]

    return await cached_completion(
        messages=messages,
        model=model,
        temperature=0.0,
        max_tokens=150,
        category="routing",
        ttl=settings.llm.cache.ttl_seconds
    )
