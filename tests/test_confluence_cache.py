"""
Tests für Confluence Cache.
"""

import pytest
from app.services.confluence_cache import ConfluenceCache, get_confluence_cache


class TestConfluenceCache:
    """Tests für ConfluenceCache."""

    @pytest.fixture
    def cache(self):
        """Frischer Cache für jeden Test."""
        # Singleton zurücksetzen
        ConfluenceCache._instance = None
        return ConfluenceCache()

    def test_search_cache_miss(self, cache):
        """Test: Cache Miss bei Suche."""
        result = cache.get_search("test query", "", 10)
        assert result is None
        assert cache._stats.misses == 1

    def test_search_cache_hit(self, cache):
        """Test: Cache Hit bei Suche."""
        results = [{"id": "1", "title": "Test Page"}]
        cache.set_search("test query", "", 10, results)

        cached = cache.get_search("test query", "", 10)
        assert cached == results
        assert cache._stats.hits == 1

    def test_search_cache_different_params(self, cache):
        """Test: Unterschiedliche Parameter = unterschiedliche Cache-Keys."""
        results1 = [{"id": "1"}]
        results2 = [{"id": "2"}]

        cache.set_search("query", "SPACE1", 10, results1)
        cache.set_search("query", "SPACE2", 10, results2)

        assert cache.get_search("query", "SPACE1", 10) == results1
        assert cache.get_search("query", "SPACE2", 10) == results2

    def test_page_cache(self, cache):
        """Test: Seiten-Cache."""
        page = {"id": "123", "title": "Test", "content": "Hello"}
        cache.set_page("123", page)

        cached = cache.get_page("123")
        assert cached == page
        assert cache._stats.hits == 1

    def test_attachment_cache(self, cache):
        """Test: Attachment-Cache."""
        attachments = [
            {"id": "att1", "title": "doc.pdf"},
            {"id": "att2", "title": "spec.pdf"},
        ]
        cache.set_attachments("page123", "application/pdf", attachments)

        cached = cache.get_attachments("page123", "application/pdf")
        assert cached == attachments
        assert len(cached) == 2

    def test_invalidate_page(self, cache):
        """Test: Seiten-Invalidierung."""
        cache.set_page("123", {"title": "Test"})
        cache.set_attachments("123", "application/pdf", [{"id": "att1"}])

        cache.invalidate_page("123")

        assert cache.get_page("123") is None
        assert cache.get_attachments("123", "application/pdf") is None

    def test_clear(self, cache):
        """Test: Cache leeren."""
        cache.set_search("q", "", 10, [{"id": "1"}])
        cache.set_page("1", {"title": "P"})
        cache.set_attachments("1", None, [])

        cache.clear()

        # Stats wurden zurückgesetzt
        assert cache._stats.hits == 0
        assert cache._stats.misses == 0

        # Cache ist leer (get_* erhöht misses)
        assert cache.get_search("q", "", 10) is None
        assert cache.get_page("1") is None
        assert cache.get_attachments("1", None) is None

    def test_stats(self, cache):
        """Test: Statistiken."""
        cache.set_search("q1", "", 10, [])
        cache.set_search("q2", "", 10, [])
        cache.get_search("q1", "", 10)  # Hit
        cache.get_search("q3", "", 10)  # Miss

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "50.0%"
        assert stats["search_entries"] == 2

    def test_singleton(self):
        """Test: Singleton-Pattern."""
        ConfluenceCache._instance = None
        cache1 = get_confluence_cache()
        cache2 = get_confluence_cache()
        assert cache1 is cache2
