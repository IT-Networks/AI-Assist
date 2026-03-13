"""
Tests für Tool Result Cache.

Testet Caching, TTL, LRU-Eviction und Statistiken.
"""

import pytest
import time
from unittest.mock import patch

from app.agent.tools import ToolResult
from app.agent.tool_cache import (
    ToolResultCache,
    CacheEntry,
    CacheStats,
    get_tool_cache,
    reset_tool_cache,
)


class TestToolResultCache:
    """Tests für ToolResultCache Klasse."""

    def setup_method(self):
        """Reset vor jedem Test."""
        reset_tool_cache()

    def test_init_with_defaults(self):
        """Cache sollte mit Defaults initialisiert werden."""
        cache = ToolResultCache()
        assert cache.ttl == 120
        assert cache.max_entries == 100
        assert cache.enabled is True
        assert len(cache) == 0

    def test_init_with_custom_values(self):
        """Cache sollte benutzerdefinierte Werte akzeptieren."""
        cache = ToolResultCache(ttl_seconds=60, max_entries=50, enabled=False)
        assert cache.ttl == 60
        assert cache.max_entries == 50
        assert cache.enabled is False

    def test_cacheable_tools(self):
        """Cacheable Tools sollten korrekt erkannt werden."""
        cache = ToolResultCache()

        # Sollte cacheable sein
        assert cache.is_cacheable("search_code") is True
        assert cache.is_cacheable("search_handbook") is True
        assert cache.is_cacheable("read_file") is True
        assert cache.is_cacheable("combined_search") is True

        # Sollte NICHT cacheable sein
        assert cache.is_cacheable("web_search") is False
        assert cache.is_cacheable("rest_api") is False
        assert cache.is_cacheable("write_file") is False
        assert cache.is_cacheable("git_status") is False

    def test_cache_disabled(self):
        """Deaktivierter Cache sollte nichts cachen."""
        cache = ToolResultCache(enabled=False)
        result = ToolResult(success=True, data="test")

        cache.set("search_code", {"query": "test"}, result)
        assert len(cache) == 0

        cached = cache.get("search_code", {"query": "test"})
        assert cached is None

    def test_set_and_get(self):
        """Einfaches Set und Get sollte funktionieren."""
        cache = ToolResultCache()
        result = ToolResult(success=True, data="found something")

        # Set
        success = cache.set("search_code", {"query": "test"}, result)
        assert success is True
        assert len(cache) == 1

        # Get
        cached = cache.get("search_code", {"query": "test"})
        assert cached is not None
        assert cached.success is True
        assert cached.data == "found something"

    def test_get_miss(self):
        """Cache Miss sollte None zurückgeben."""
        cache = ToolResultCache()

        cached = cache.get("search_code", {"query": "nonexistent"})
        assert cached is None

    def test_different_args_different_keys(self):
        """Unterschiedliche Args sollten unterschiedliche Keys ergeben."""
        cache = ToolResultCache()

        result1 = ToolResult(success=True, data="result1")
        result2 = ToolResult(success=True, data="result2")

        cache.set("search_code", {"query": "foo"}, result1)
        cache.set("search_code", {"query": "bar"}, result2)

        assert len(cache) == 2

        cached1 = cache.get("search_code", {"query": "foo"})
        cached2 = cache.get("search_code", {"query": "bar"})

        assert cached1.data == "result1"
        assert cached2.data == "result2"

    def test_ttl_expiration(self):
        """Eintraege sollten nach TTL ablaufen."""
        cache = ToolResultCache(ttl_seconds=1)  # 1 Sekunde TTL
        result = ToolResult(success=True, data="test")

        cache.set("search_code", {"query": "test"}, result)
        assert cache.get("search_code", {"query": "test"}) is not None

        # Warten bis TTL abgelaufen
        time.sleep(1.1)

        cached = cache.get("search_code", {"query": "test"})
        assert cached is None

    def test_failed_results_not_cached(self):
        """Fehlgeschlagene Ergebnisse sollten nicht gecacht werden."""
        cache = ToolResultCache()
        result = ToolResult(success=False, error="Something went wrong")

        success = cache.set("search_code", {"query": "test"}, result)
        assert success is False
        assert len(cache) == 0

    def test_confirmation_results_not_cached(self):
        """Ergebnisse mit requires_confirmation sollten nicht gecacht werden."""
        cache = ToolResultCache()
        result = ToolResult(
            success=True,
            data="test",
            requires_confirmation=True,
            confirmation_data={"path": "/test"}
        )

        success = cache.set("write_file", {"path": "/test"}, result)
        assert success is False

    def test_lru_eviction(self):
        """LRU-Eviction sollte aelteste Eintraege entfernen."""
        cache = ToolResultCache(max_entries=3)

        for i in range(5):
            result = ToolResult(success=True, data=f"result{i}")
            cache.set("search_code", {"query": f"q{i}"}, result)

        # Nur die letzten 3 sollten im Cache sein
        assert len(cache) == 3

        # Die ersten beiden sollten evicted sein
        assert cache.get("search_code", {"query": "q0"}) is None
        assert cache.get("search_code", {"query": "q1"}) is None

        # Die letzten 3 sollten noch da sein
        assert cache.get("search_code", {"query": "q2"}) is not None
        assert cache.get("search_code", {"query": "q3"}) is not None
        assert cache.get("search_code", {"query": "q4"}) is not None

    def test_invalidate_all(self):
        """invalidate() ohne Parameter sollte alles loeschen."""
        cache = ToolResultCache()

        for i in range(5):
            result = ToolResult(success=True, data=f"result{i}")
            cache.set("search_code", {"query": f"q{i}"}, result)

        assert len(cache) == 5

        count = cache.invalidate()
        assert count == 5
        assert len(cache) == 0

    def test_invalidate_specific_tool(self):
        """invalidate(tool_name) sollte nur dieses Tool loeschen."""
        cache = ToolResultCache()

        cache.set("search_code", {"query": "test"}, ToolResult(success=True, data="code"))
        cache.set("search_handbook", {"query": "test"}, ToolResult(success=True, data="handbook"))
        cache.set("read_file", {"path": "/test"}, ToolResult(success=True, data="file"))

        assert len(cache) == 3

        count = cache.invalidate("search_code")
        assert count == 1
        assert len(cache) == 2

        # search_code sollte weg sein
        assert cache.get("search_code", {"query": "test"}) is None

        # Andere sollten noch da sein
        assert cache.get("search_handbook", {"query": "test"}) is not None
        assert cache.get("read_file", {"path": "/test"}) is not None

    def test_stats_tracking(self):
        """Statistiken sollten korrekt getrackt werden."""
        cache = ToolResultCache()
        result = ToolResult(success=True, data="test")

        # Set
        cache.set("search_code", {"query": "test"}, result)

        # Hit
        cache.get("search_code", {"query": "test"})
        cache.get("search_code", {"query": "test"})

        # Miss
        cache.get("search_code", {"query": "nonexistent"})

        stats = cache.get_stats()
        assert stats.total_sets == 1
        assert stats.total_hits == 2
        assert stats.total_misses == 1
        assert stats.hit_rate == pytest.approx(66.66, rel=0.1)

    def test_get_summary(self):
        """get_summary() sollte lesbaren String zurueckgeben."""
        cache = ToolResultCache()
        result = ToolResult(success=True, data="test")

        cache.set("search_code", {"query": "test"}, result)
        cache.get("search_code", {"query": "test"})

        summary = cache.get_summary()
        assert "Tool-Cache" in summary
        assert "Hits:" in summary
        assert "Hit-Rate:" in summary


class TestCacheStats:
    """Tests fuer CacheStats."""

    def test_hit_rate_zero_total(self):
        """Hit-Rate sollte 0 sein bei keinen Aufrufen."""
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        """Hit-Rate sollte korrekt berechnet werden."""
        stats = CacheStats(total_hits=75, total_misses=25)
        assert stats.hit_rate == 75.0

    def test_to_dict(self):
        """to_dict() sollte alle Felder enthalten."""
        stats = CacheStats(
            total_hits=10,
            total_misses=5,
            total_sets=15,
            total_evictions=2,
            tools_cached={"search_code": 5, "read_file": 10}
        )

        d = stats.to_dict()
        assert d["total_hits"] == 10
        assert d["total_misses"] == 5
        assert d["hit_rate_percent"] == pytest.approx(66.7, rel=0.1)
        assert "search_code" in d["tools_cached"]


class TestSingleton:
    """Tests fuer Singleton-Verhalten."""

    def setup_method(self):
        reset_tool_cache()

    def test_get_tool_cache_singleton(self):
        """get_tool_cache() sollte immer dieselbe Instanz zurueckgeben."""
        cache1 = get_tool_cache()
        cache2 = get_tool_cache()

        assert cache1 is cache2

    def test_reset_tool_cache(self):
        """reset_tool_cache() sollte Singleton zuruecksetzen."""
        cache1 = get_tool_cache()
        cache1.set("search_code", {"query": "test"}, ToolResult(success=True, data="test"))

        reset_tool_cache()

        cache2 = get_tool_cache()
        assert cache1 is not cache2
        assert len(cache2) == 0


class TestEdgeCases:
    """Tests fuer Edge Cases."""

    def test_complex_kwargs(self):
        """Komplexe kwargs sollten korrekt gehasht werden."""
        cache = ToolResultCache()
        result = ToolResult(success=True, data="test")

        kwargs = {
            "query": "test",
            "options": ["a", "b", "c"],
            "nested": {"key": "value", "num": 42},
            "flag": True
        }

        cache.set("search_code", kwargs, result)
        cached = cache.get("search_code", kwargs)

        assert cached is not None

    def test_none_values_in_kwargs(self):
        """None-Werte in kwargs sollten funktionieren."""
        cache = ToolResultCache()
        result = ToolResult(success=True, data="test")

        kwargs = {"query": "test", "filter": None}

        cache.set("search_code", kwargs, result)
        cached = cache.get("search_code", kwargs)

        assert cached is not None

    def test_unicode_in_kwargs(self):
        """Unicode in kwargs sollte funktionieren."""
        cache = ToolResultCache()
        result = ToolResult(success=True, data="test")

        kwargs = {"query": "Suche nach Umlauten: aou"}

        cache.set("search_code", kwargs, result)
        cached = cache.get("search_code", kwargs)

        assert cached is not None
