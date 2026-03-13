"""
Tool Result Cache - LRU-Cache für idempotente Tool-Ergebnisse.

Reduziert redundante Tool-Aufrufe durch Caching von Ergebnissen
innerhalb einer Session. Nur idempotente Tools werden gecacht.

Verwendung:
    cache = ToolResultCache(ttl_seconds=120)

    # Vor Tool-Ausführung prüfen
    cached = cache.get("search_code", {"query": "getUserById"})
    if cached:
        return cached

    # Nach Ausführung speichern
    result = await tool.execute(...)
    cache.set("search_code", {"query": "getUserById"}, result)
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.tools import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Ein einzelner Cache-Eintrag."""
    result: ToolResult
    timestamp: float
    hits: int = 0
    tool_name: str = ""
    key_hash: str = ""


@dataclass
class CacheStats:
    """Statistiken über Cache-Nutzung."""
    total_hits: int = 0
    total_misses: int = 0
    total_sets: int = 0
    total_evictions: int = 0
    tools_cached: Dict[str, int] = field(default_factory=dict)

    @property
    def hit_rate(self) -> float:
        """Cache-Hit-Rate in Prozent."""
        total = self.total_hits + self.total_misses
        if total == 0:
            return 0.0
        return (self.total_hits / total) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary für Serialisierung."""
        return {
            "total_hits": self.total_hits,
            "total_misses": self.total_misses,
            "total_sets": self.total_sets,
            "total_evictions": self.total_evictions,
            "hit_rate_percent": round(self.hit_rate, 1),
            "tools_cached": dict(self.tools_cached),
        }


class ToolResultCache:
    """
    LRU-Cache für idempotente Tool-Ergebnisse.

    Cacht nur Tools die als cacheable markiert sind (idempotent, keine Seiteneffekte).
    Verwendet TTL (Time-To-Live) für automatische Invalidierung.

    Attributes:
        ttl: Time-To-Live in Sekunden
        max_entries: Maximale Anzahl Cache-Einträge
    """

    # Tools die gecacht werden können (idempotent, keine Seiteneffekte)
    CACHEABLE_TOOLS: Set[str] = {
        # Such-Tools
        "search_code",
        "search_handbook",
        "search_skills",
        "combined_search",

        # Datei-Lese-Tools
        "read_file",
        "batch_read_files",
        "list_files",

        # Info-Tools
        "get_active_repositories",
        "get_service_info",

        # API-Analyse (WSDL ändert sich selten)
        "wsdl_info",

        # Git-Read-Only
        "git_log",
        "git_show",
    }

    # Tools die NIEMALS gecacht werden (Seiteneffekte oder zeitkritisch)
    NON_CACHEABLE: Set[str] = {
        # Externe Systeme
        "web_search",
        "fetch_webpage",
        "rest_api",
        "soap_request",

        # Git-Schreib-Operationen
        "git_status",  # Kann sich zwischen Aufrufen ändern
        "git_diff",
        "git_commit",
        "git_push",

        # Jenkins/CI
        "jenkins_build_info",
        "jenkins_build_log",
        "jenkins_trigger_build",

        # Datei-Schreib-Operationen
        "write_file",
        "edit_file",
        "create_directory",
        "batch_write_files",

        # Shell/Docker
        "shell_execute",
        "docker_run",
    }

    def __init__(
        self,
        ttl_seconds: int = 120,
        max_entries: int = 100,
        enabled: bool = True
    ):
        """
        Initialisiert den Cache.

        Args:
            ttl_seconds: Time-To-Live für Einträge (default: 2 Minuten)
            max_entries: Maximale Anzahl Einträge (default: 100)
            enabled: Cache aktiviert (default: True)
        """
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.enabled = enabled

        self._cache: Dict[str, CacheEntry] = {}
        self._stats = CacheStats()
        self._access_order: List[str] = []  # Für LRU

        logger.debug(f"[ToolCache] Initialisiert: TTL={ttl_seconds}s, max={max_entries}")

    def _make_key(self, tool_name: str, kwargs: Dict[str, Any]) -> str:
        """
        Erstellt einen eindeutigen Cache-Key.

        Args:
            tool_name: Name des Tools
            kwargs: Parameter des Tool-Aufrufs

        Returns:
            MD5-Hash des kombinierten Keys
        """
        # Sortiere kwargs für konsistente Keys
        # Konvertiere alle Werte zu Strings für JSON-Serialisierung
        def serialize_value(v):
            if isinstance(v, (list, tuple)):
                return [serialize_value(x) for x in v]
            elif isinstance(v, dict):
                return {k: serialize_value(val) for k, val in sorted(v.items())}
            else:
                return str(v) if v is not None else None

        serializable_kwargs = {k: serialize_value(v) for k, v in sorted(kwargs.items())}

        try:
            sorted_kwargs = json.dumps(serializable_kwargs, sort_keys=True)
        except (TypeError, ValueError):
            # Fallback: String-Repräsentation
            sorted_kwargs = str(sorted(kwargs.items()))

        raw_key = f"{tool_name}:{sorted_kwargs}"
        return hashlib.md5(raw_key.encode()).hexdigest()

    def is_cacheable(self, tool_name: str) -> bool:
        """
        Prüft ob ein Tool gecacht werden kann.

        Args:
            tool_name: Name des Tools

        Returns:
            True wenn das Tool cacheable ist
        """
        if not self.enabled:
            return False

        # Explizit nicht-cacheable?
        if tool_name in self.NON_CACHEABLE:
            return False

        # Explizit cacheable?
        if tool_name in self.CACHEABLE_TOOLS:
            return True

        # Wildcard-Patterns prüfen (z.B. "jenkins_*")
        for pattern in self.NON_CACHEABLE:
            if pattern.endswith("*") and tool_name.startswith(pattern[:-1]):
                return False

        # Default: nicht cachen (sicher)
        return False

    def get(self, tool_name: str, kwargs: Dict[str, Any]) -> Optional[ToolResult]:
        """
        Holt ein gecachtes Ergebnis.

        Args:
            tool_name: Name des Tools
            kwargs: Parameter des Tool-Aufrufs

        Returns:
            ToolResult wenn im Cache und nicht abgelaufen, sonst None
        """
        if not self.is_cacheable(tool_name):
            return None

        key = self._make_key(tool_name, kwargs)
        entry = self._cache.get(key)

        if entry is None:
            self._stats.total_misses += 1
            return None

        # TTL prüfen
        age = time.time() - entry.timestamp
        if age > self.ttl:
            # Abgelaufen - entfernen
            self._remove_entry(key)
            self._stats.total_misses += 1
            logger.debug(f"[ToolCache] EXPIRED: {tool_name} (age={age:.1f}s > TTL={self.ttl}s)")
            return None

        # Cache Hit!
        entry.hits += 1
        self._stats.total_hits += 1
        self._update_access_order(key)

        logger.debug(f"[ToolCache] HIT: {tool_name} (hits={entry.hits}, age={age:.1f}s)")
        return entry.result

    def set(
        self,
        tool_name: str,
        kwargs: Dict[str, Any],
        result: ToolResult
    ) -> bool:
        """
        Speichert ein Tool-Ergebnis im Cache.

        Args:
            tool_name: Name des Tools
            kwargs: Parameter des Tool-Aufrufs
            result: Das zu cachende Ergebnis

        Returns:
            True wenn erfolgreich gecacht
        """
        if not self.is_cacheable(tool_name):
            return False

        # Nur erfolgreiche Ergebnisse cachen
        if not result.success:
            logger.debug(f"[ToolCache] SKIP (failed): {tool_name}")
            return False

        # Keine Ergebnisse cachen die Bestätigung benötigen
        if result.requires_confirmation:
            logger.debug(f"[ToolCache] SKIP (requires_confirmation): {tool_name}")
            return False

        # LRU: Älteste Einträge entfernen wenn voll
        while len(self._cache) >= self.max_entries:
            self._evict_oldest()

        key = self._make_key(tool_name, kwargs)

        self._cache[key] = CacheEntry(
            result=result,
            timestamp=time.time(),
            hits=0,
            tool_name=tool_name,
            key_hash=key[:8]
        )

        self._access_order.append(key)
        self._stats.total_sets += 1
        self._stats.tools_cached[tool_name] = self._stats.tools_cached.get(tool_name, 0) + 1

        logger.debug(f"[ToolCache] SET: {tool_name} (entries={len(self._cache)})")
        return True

    def _update_access_order(self, key: str) -> None:
        """Aktualisiert die Zugriffs-Reihenfolge für LRU."""
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _remove_entry(self, key: str) -> None:
        """Entfernt einen Eintrag aus dem Cache."""
        if key in self._cache:
            del self._cache[key]
        if key in self._access_order:
            self._access_order.remove(key)

    def _evict_oldest(self) -> None:
        """Entfernt den ältesten (LRU) Eintrag."""
        if not self._access_order:
            return

        oldest_key = self._access_order.pop(0)
        if oldest_key in self._cache:
            entry = self._cache[oldest_key]
            logger.debug(f"[ToolCache] EVICT: {entry.tool_name} (hits={entry.hits})")
            del self._cache[oldest_key]
            self._stats.total_evictions += 1

    def invalidate(self, tool_name: Optional[str] = None) -> int:
        """
        Invalidiert Cache-Einträge.

        Args:
            tool_name: Optional - nur Einträge für dieses Tool.
                       Wenn None, wird der gesamte Cache geleert.

        Returns:
            Anzahl der entfernten Einträge
        """
        if tool_name is None:
            count = len(self._cache)
            self.clear()
            return count

        # Nur bestimmtes Tool invalidieren
        keys_to_remove = [
            key for key, entry in self._cache.items()
            if entry.tool_name == tool_name
        ]

        for key in keys_to_remove:
            self._remove_entry(key)

        logger.debug(f"[ToolCache] INVALIDATE: {tool_name} ({len(keys_to_remove)} entries)")
        return len(keys_to_remove)

    def clear(self) -> None:
        """Leert den gesamten Cache."""
        self._cache.clear()
        self._access_order.clear()
        logger.debug("[ToolCache] CLEARED")

    def get_stats(self) -> CacheStats:
        """
        Gibt Cache-Statistiken zurück.

        Returns:
            CacheStats-Objekt mit Nutzungsstatistiken
        """
        return self._stats

    def get_summary(self) -> str:
        """
        Gibt eine lesbare Zusammenfassung zurück.

        Returns:
            Formatierter String mit Cache-Statistiken
        """
        stats = self._stats
        lines = [
            "=== Tool-Cache Statistiken ===",
            f"Einträge: {len(self._cache)}/{self.max_entries}",
            f"Hits: {stats.total_hits}",
            f"Misses: {stats.total_misses}",
            f"Hit-Rate: {stats.hit_rate:.1f}%",
            f"Evictions: {stats.total_evictions}",
        ]

        if stats.tools_cached:
            lines.append("")
            lines.append("Gecachte Tools:")
            for tool, count in sorted(stats.tools_cached.items(), key=lambda x: -x[1]):
                lines.append(f"  {tool}: {count}x")

        return "\n".join(lines)

    def __len__(self) -> int:
        """Anzahl der Cache-Einträge."""
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Prüft ob ein Key im Cache ist (ohne TTL-Prüfung)."""
        return key in self._cache


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_default_cache: Optional[ToolResultCache] = None


def get_tool_cache(
    ttl_seconds: int = 120,
    max_entries: int = 100
) -> ToolResultCache:
    """
    Gibt die Singleton-Instanz des Tool-Caches zurück.

    Args:
        ttl_seconds: TTL für neue Instanz (nur bei Erstinitialisierung)
        max_entries: Max Einträge für neue Instanz

    Returns:
        ToolResultCache-Singleton
    """
    global _default_cache
    if _default_cache is None:
        _default_cache = ToolResultCache(
            ttl_seconds=ttl_seconds,
            max_entries=max_entries
        )
    return _default_cache


def reset_tool_cache() -> None:
    """Setzt den Singleton-Cache zurück (für Tests)."""
    global _default_cache
    if _default_cache:
        _default_cache.clear()
    _default_cache = None
