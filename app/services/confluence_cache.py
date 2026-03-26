"""
Confluence Cache - TTL-basierter Cache für Confluence API-Aufrufe.

Cached werden:
- Suchergebnisse (search_confluence)
- Seiteninhalte (read_confluence_page)
- PDF-Attachment-Listen (list_confluence_pdfs)

Session-Tracking:
- Welche Seiten wurden in der aktuellen Session bereits gelesen?
- Verhindert Schleifen wo der Agent dieselbe Seite mehrfach liest

Nicht gecached:
- PDF-Downloads (zu groß, werden nur temporär verwendet)
"""

import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from cachetools import TTLCache

from app.core.config import settings

# ContextVar für aktuelle Session-ID (threadsafe für async)
_current_session_id: ContextVar[str] = ContextVar('confluence_session_id', default='')


def set_current_session(session_id: str) -> None:
    """Setzt die aktuelle Session-ID für Confluence-Tracking."""
    _current_session_id.set(session_id)


def get_current_session() -> str:
    """Gibt die aktuelle Session-ID zurück."""
    return _current_session_id.get()

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """Statistiken für den Cache."""
    hits: int = 0
    misses: int = 0
    size: int = 0
    max_size: int = 0

    @property
    def hit_rate(self) -> float:
        """Trefferquote in Prozent."""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


class ConfluenceCache:
    """
    TTL-basierter Cache für Confluence API-Aufrufe.

    Verwendet separate Caches für verschiedene Datentypen:
    - search_cache: Suchergebnisse (kurzes TTL, häufig aktualisiert)
    - page_cache: Seiteninhalte (längeres TTL, seltener geändert)
    - attachment_cache: Attachment-Listen (mittleres TTL)

    Thread-safe durch cachetools Implementation.
    """

    _instance: Optional["ConfluenceCache"] = None

    def __init__(self):
        config = getattr(settings, 'confluence_cache', None)

        # Default-Werte wenn keine Konfiguration vorhanden
        search_ttl = getattr(config, 'search_ttl_seconds', 300) if config else 300  # 5 min
        page_ttl = getattr(config, 'page_ttl_seconds', 1800) if config else 1800  # 30 min
        attachment_ttl = getattr(config, 'attachment_ttl_seconds', 600) if config else 600  # 10 min
        max_search = getattr(config, 'max_search_entries', 200) if config else 200
        max_pages = getattr(config, 'max_page_entries', 100) if config else 100
        max_attachments = getattr(config, 'max_attachment_entries', 100) if config else 100

        self._search_cache: TTLCache = TTLCache(maxsize=max_search, ttl=search_ttl)
        self._page_cache: TTLCache = TTLCache(maxsize=max_pages, ttl=page_ttl)
        self._attachment_cache: TTLCache = TTLCache(maxsize=max_attachments, ttl=attachment_ttl)

        # Session-Tracking: Welche Seiten wurden in welcher Session bereits gelesen?
        # Verhindert Schleifen wo der Agent immer wieder dieselbe Seite liest
        self._session_read_pages: Dict[str, Dict[str, str]] = {}  # session_id -> {page_id: title}

        # Statistiken
        self._stats = CacheStats(max_size=max_search + max_pages + max_attachments)

        logger.info(
            f"ConfluenceCache initialisiert: "
            f"search={max_search}@{search_ttl}s, "
            f"pages={max_pages}@{page_ttl}s, "
            f"attachments={max_attachments}@{attachment_ttl}s"
        )

    @classmethod
    def get_instance(cls) -> "ConfluenceCache":
        """Singleton-Zugriff."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _make_key(*args, **kwargs) -> str:
        """Erstellt einen Hash-Key aus den Argumenten."""
        key_data = f"{args}:{sorted(kwargs.items())}"
        return hashlib.md5(key_data.encode()).hexdigest()

    # ── Search Cache ──

    def get_search(self, query: str, space: str = "", limit: int = 10) -> Optional[List[Dict]]:
        """Holt Suchergebnisse aus dem Cache."""
        key = self._make_key("search", query, space, limit)
        result = self._search_cache.get(key)

        if result is not None:
            self._stats.hits += 1
            logger.debug(f"Cache HIT: search '{query[:30]}...'")
        else:
            self._stats.misses += 1

        return result

    def set_search(self, query: str, space: str, limit: int, results: List[Dict]) -> None:
        """Speichert Suchergebnisse im Cache."""
        key = self._make_key("search", query, space, limit)
        self._search_cache[key] = results
        self._update_size()
        logger.debug(f"Cache SET: search '{query[:30]}...' ({len(results)} results)")

    # ── Page Cache ──

    def get_page(self, page_id: str) -> Optional[Dict]:
        """Holt Seiteninhalt aus dem Cache."""
        result = self._page_cache.get(page_id)

        if result is not None:
            self._stats.hits += 1
            logger.debug(f"Cache HIT: page {page_id}")
        else:
            self._stats.misses += 1

        return result

    def set_page(self, page_id: str, page_data: Dict) -> None:
        """Speichert Seiteninhalt im Cache."""
        self._page_cache[page_id] = page_data
        self._update_size()
        logger.debug(f"Cache SET: page {page_id}")

    # ── Attachment Cache ──

    def _attachment_key(self, page_id: str, media_type: Optional[str] = None) -> str:
        """Erstellt einen reproduzierbaren Key für Attachment-Cache."""
        return f"page:{page_id}:type:{media_type or 'all'}"

    def get_attachments(self, page_id: str, media_type: Optional[str] = None) -> Optional[List[Dict]]:
        """Holt Attachment-Liste aus dem Cache."""
        key = self._attachment_key(page_id, media_type)
        result = self._attachment_cache.get(key)

        if result is not None:
            self._stats.hits += 1
            logger.debug(f"Cache HIT: attachments page {page_id}")
        else:
            self._stats.misses += 1

        return result

    def set_attachments(self, page_id: str, media_type: Optional[str], attachments: List[Dict]) -> None:
        """Speichert Attachment-Liste im Cache."""
        key = self._attachment_key(page_id, media_type)
        self._attachment_cache[key] = attachments
        self._update_size()
        logger.debug(f"Cache SET: attachments page {page_id} ({len(attachments)} items)")

    # ── Session Read Tracking ──
    # Verhindert Schleifen wo der Agent immer wieder dieselbe Seite liest

    def mark_page_read(self, session_id: str, page_id: str, title: str) -> None:
        """Markiert eine Seite als in dieser Session gelesen."""
        if session_id not in self._session_read_pages:
            self._session_read_pages[session_id] = {}
        self._session_read_pages[session_id][page_id] = title
        logger.debug(f"Session {session_id[:8]}: Seite {page_id} als gelesen markiert")

    def was_page_read(self, session_id: str, page_id: str) -> bool:
        """Prüft ob eine Seite in dieser Session bereits gelesen wurde."""
        return page_id in self._session_read_pages.get(session_id, {})

    def get_read_pages(self, session_id: str) -> Dict[str, str]:
        """Gibt alle in dieser Session gelesenen Seiten zurück (page_id -> title)."""
        return self._session_read_pages.get(session_id, {}).copy()

    def clear_session(self, session_id: str) -> None:
        """Löscht das Session-Tracking für eine Session."""
        if session_id in self._session_read_pages:
            del self._session_read_pages[session_id]
            logger.debug(f"Session {session_id[:8]}: Read-Tracking gelöscht")

    # ── Cache Management ──

    def invalidate_page(self, page_id: str) -> None:
        """Invalidiert alle Cache-Einträge für eine Seite."""
        # Page Cache
        if page_id in self._page_cache:
            del self._page_cache[page_id]

        # Attachment Cache - alle Einträge für diese Seite
        prefix = f"page:{page_id}:"
        keys_to_remove = [k for k in self._attachment_cache.keys() if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._attachment_cache[key]

        self._update_size()
        logger.debug(f"Cache INVALIDATE: page {page_id}")

    def clear(self) -> None:
        """Leert alle Caches."""
        self._search_cache.clear()
        self._page_cache.clear()
        self._attachment_cache.clear()
        self._stats = CacheStats(max_size=self._stats.max_size)
        logger.info("Cache CLEARED: all caches")

    def _update_size(self) -> None:
        """Aktualisiert die Größenstatistik."""
        self._stats.size = (
            len(self._search_cache) +
            len(self._page_cache) +
            len(self._attachment_cache)
        )

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Cache-Statistiken zurück."""
        self._update_size()
        return {
            "hits": self._stats.hits,
            "misses": self._stats.misses,
            "hit_rate": f"{self._stats.hit_rate:.1f}%",
            "current_size": self._stats.size,
            "max_size": self._stats.max_size,
            "search_entries": len(self._search_cache),
            "page_entries": len(self._page_cache),
            "attachment_entries": len(self._attachment_cache),
        }


def get_confluence_cache() -> ConfluenceCache:
    """Factory-Funktion für ConfluenceCache-Instanz."""
    return ConfluenceCache.get_instance()
