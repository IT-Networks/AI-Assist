"""
Tests fuer PromptEnhancer - Kontext-Anreicherung.

Testet:
- EnhancementDetector: Erkennung des Enhancement-Typs
- EnhancementCache: Caching und TTL
- PromptEnhancer: Kontext-Sammlung und User-Bestaetigung

MIGRATION (2026-03-23):
Capabilities wurden zu Skills migriert. Der Enhancer liefert nun
Skill-Hinweise statt eigener Kontext-Sammlung.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.prompt_enhancer import (
    EnhancementType,
    ConfirmationStatus,
    ContextItem,
    EnrichedPrompt,
    EnhancementCache,
    EnhancementDetector,
    PromptEnhancer,
    get_enhancement_cache,
    get_prompt_enhancer,
)


class TestContextItem:
    """Tests fuer ContextItem."""

    def test_to_dict_truncates_long_content(self):
        """Lange Inhalte sollten in to_dict gekuerzt werden."""
        item = ContextItem(
            source="test",
            title="Test Item",
            content="x" * 1000,
            relevance=0.8
        )
        result = item.to_dict()

        assert len(result["content"]) <= 503  # 500 + "..."
        assert result["content"].endswith("...")

    def test_to_context_string_with_file_path(self):
        """to_context_string sollte Dateipfad anzeigen."""
        item = ContextItem(
            source="code",
            title="MyClass",
            content="class MyClass: pass",
            file_path="src/myclass.py"
        )
        result = item.to_context_string()

        assert "MyClass" in result
        assert "src/myclass.py" in result
        assert "class MyClass: pass" in result

    def test_to_context_string_with_url(self):
        """to_context_string sollte URL anzeigen."""
        item = ContextItem(
            source="wiki",
            title="API Docs",
            content="REST API documentation",
            url="https://wiki.example.com/api"
        )
        result = item.to_context_string()

        assert "API Docs" in result
        assert "wiki.example.com" in result


class TestEnrichedPrompt:
    """Tests fuer EnrichedPrompt."""

    def test_total_context_length(self):
        """total_context_length sollte Gesamtlaenge berechnen."""
        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH,
            context_items=[
                ContextItem(source="a", title="A", content="12345"),
                ContextItem(source="b", title="B", content="67890"),
            ]
        )
        assert enriched.total_context_length == 10

    def test_context_sources_unique(self):
        """context_sources sollte unique Quellen liefern."""
        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH,
            context_items=[
                ContextItem(source="wiki", title="A", content="x"),
                ContextItem(source="code", title="B", content="y"),
                ContextItem(source="wiki", title="C", content="z"),  # Duplicate
            ]
        )
        assert set(enriched.context_sources) == {"wiki", "code"}

    def test_get_context_for_planner_empty(self):
        """Leerer Kontext sollte leeren String liefern."""
        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.NONE
        )
        assert enriched.get_context_for_planner() == ""

    def test_get_context_for_planner_formatted(self):
        """Kontext sollte formatiert sein fuer Planner."""
        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH,
            context_items=[
                ContextItem(source="wiki", title="API", content="API docs", relevance=0.9),
                ContextItem(source="code", title="Handler", content="def handler():", relevance=0.7),
            ]
        )
        result = enriched.get_context_for_planner()

        assert "Gesammelter Kontext" in result
        assert "wiki" in result.lower() or "code" in result.lower()
        assert "API" in result
        # Hoechste Relevanz sollte zuerst kommen
        assert result.index("API") < result.index("Handler")

    def test_get_confirmation_message(self):
        """Bestaetigugnsnachricht sollte formatiert sein."""
        enriched = EnrichedPrompt(
            original_query="Implementiere Feature basierend auf Wiki",
            enhancement_type=EnhancementType.RESEARCH,
            context_items=[
                ContextItem(source="wiki", title="Feature Spec", content="...", relevance=0.9),
            ],
            summary="1 relevanter Kontext-Eintrag gefunden"
        )
        msg = enriched.get_confirmation_message()

        assert "Kontext-Sammlung abgeschlossen" in msg
        assert "Feature Spec" in msg
        assert "Ja" in msg
        assert "Nein" in msg


class TestEnhancementCache:
    """Tests fuer EnhancementCache."""

    def test_cache_set_and_get(self):
        """Cache sollte setzen und abrufen koennen."""
        cache = EnhancementCache(ttl_seconds=60)

        enriched = EnrichedPrompt(
            original_query="test query",
            enhancement_type=EnhancementType.RESEARCH
        )

        key = cache.set(enriched)
        assert key is not None

        retrieved = cache.get("test query", EnhancementType.RESEARCH)
        assert retrieved is not None
        assert retrieved.original_query == "test query"
        assert retrieved.cache_hit is True

    def test_cache_miss(self):
        """Nicht vorhandener Key sollte None liefern."""
        cache = EnhancementCache()

        result = cache.get("nonexistent", EnhancementType.RESEARCH)
        assert result is None

    def test_cache_ttl_expiry(self):
        """Abgelaufene Eintraege sollten nicht zurueckgegeben werden."""
        cache = EnhancementCache(ttl_seconds=1)  # 1 Sekunde TTL

        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH
        )
        cache.set(enriched)

        # Vor Ablauf
        assert cache.get("test", EnhancementType.RESEARCH) is not None

        # Nach Ablauf
        time.sleep(1.1)
        assert cache.get("test", EnhancementType.RESEARCH) is None

    def test_cache_lru_eviction(self):
        """Bei voller Kapazitaet sollte aeltester Eintrag entfernt werden."""
        cache = EnhancementCache(max_entries=2)

        # Drei Eintraege setzen
        for i in range(3):
            enriched = EnrichedPrompt(
                original_query=f"query{i}",
                enhancement_type=EnhancementType.RESEARCH
            )
            cache.set(enriched)

        # Erster sollte verdraengt sein
        assert cache.get("query0", EnhancementType.RESEARCH) is None
        # Letzter sollte noch da sein
        assert cache.get("query2", EnhancementType.RESEARCH) is not None

    def test_cache_key_normalized(self):
        """Keys sollten normalisiert werden (case-insensitive, whitespace)."""
        cache = EnhancementCache()

        enriched = EnrichedPrompt(
            original_query="Test Query",
            enhancement_type=EnhancementType.RESEARCH
        )
        cache.set(enriched)

        # Sollte auch mit anderer Gross/Kleinschreibung gefunden werden
        result = cache.get("test query", EnhancementType.RESEARCH)
        assert result is not None

    def test_cache_invalidate_single(self):
        """Einzelner Eintrag sollte invalidiert werden koennen."""
        cache = EnhancementCache()

        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH
        )
        key = cache.set(enriched)

        assert cache.invalidate(key) == 1
        assert cache.get("test", EnhancementType.RESEARCH) is None

    def test_cache_invalidate_all(self):
        """Alle Eintraege sollten invalidiert werden koennen."""
        cache = EnhancementCache()

        for i in range(5):
            enriched = EnrichedPrompt(
                original_query=f"query{i}",
                enhancement_type=EnhancementType.RESEARCH
            )
            cache.set(enriched)

        count = cache.invalidate()
        assert count == 5


class TestEnhancementDetector:
    """Tests fuer EnhancementDetector."""

    def test_detect_research_keywords(self):
        """Research-Keywords sollten erkannt werden."""
        detector = EnhancementDetector()

        assert detector.detect("Implementiere wie im Wiki beschrieben") == EnhancementType.RESEARCH
        assert detector.detect("Schau in der Dokumentation nach") == EnhancementType.RESEARCH
        assert detector.detect("as described in the README") == EnhancementType.RESEARCH

    def test_detect_sequential_keywords(self):
        """Sequential/Debug-Keywords sollten erkannt werden."""
        detector = EnhancementDetector()

        assert detector.detect("Warum funktioniert das nicht?") == EnhancementType.SEQUENTIAL
        assert detector.detect("Debug diesen Fehler") == EnhancementType.SEQUENTIAL
        assert detector.detect("Analysiere die Exception") == EnhancementType.SEQUENTIAL

    def test_detect_analyze_keywords(self):
        """Analyze-Keywords sollten erkannt werden."""
        detector = EnhancementDetector()

        assert detector.detect("Refactor diese Klasse") == EnhancementType.ANALYZE
        assert detector.detect("Erweitere den bestehenden Service") == EnhancementType.ANALYZE
        assert detector.detect("Was passiert wenn ich das aendere?") == EnhancementType.ANALYZE

    def test_detect_none_for_simple_queries(self):
        """Einfache Queries sollten NONE sein."""
        detector = EnhancementDetector()

        assert detector.detect("Schreibe eine Fibonacci-Funktion") == EnhancementType.NONE
        assert detector.detect("Fix typo in file.py") == EnhancementType.NONE

    def test_should_enhance_short_query(self):
        """Kurze Queries sollten nicht enhanced werden."""
        detector = EnhancementDetector()

        assert detector.should_enhance("hi") is False
        assert detector.should_enhance("ok") is False

    def test_should_enhance_skip_markers(self):
        """Skip-Marker sollten Enhancement verhindern."""
        detector = EnhancementDetector()

        assert detector.should_enhance("[DIRECT] Implementiere wie im Wiki") is False
        assert detector.should_enhance("[NO_ENHANCE] Debug den Fehler") is False
        assert detector.should_enhance("[SKIP_MCP] Analysiere Code") is False

    def test_should_enhance_true_for_relevant(self):
        """Relevante Queries sollten enhanced werden."""
        detector = EnhancementDetector()

        assert detector.should_enhance("Implementiere das Feature wie im Wiki beschrieben") is True
        assert detector.should_enhance("Warum funktioniert der Login nicht mehr?") is True


class TestPromptEnhancer:
    """Tests fuer PromptEnhancer."""

    @pytest.mark.asyncio
    async def test_enhance_none_type_returns_confirmed(self):
        """NONE-Typ sollte sofort bestaetigten EnrichedPrompt liefern."""
        enhancer = PromptEnhancer()

        result = await enhancer.enhance("Schreibe Hello World")

        assert result.enhancement_type == EnhancementType.NONE
        assert result.confirmation_status == ConfirmationStatus.CONFIRMED
        assert result.context_items == []

    @pytest.mark.asyncio
    async def test_enhance_uses_cache(self):
        """Wiederholte Anfragen sollten Cache nutzen."""
        cache = EnhancementCache()
        enhancer = PromptEnhancer(cache=cache)

        # Erste Anfrage
        enriched1 = EnrichedPrompt(
            original_query="Wie im Wiki beschrieben",
            enhancement_type=EnhancementType.RESEARCH,
            context_items=[ContextItem(source="wiki", title="Test", content="cached")]
        )
        cache.set(enriched1)

        # Zweite Anfrage sollte Cache nutzen
        result = await enhancer.enhance("Wie im Wiki beschrieben")

        assert result.cache_hit is True
        assert len(result.context_items) == 1
        assert result.context_items[0].content == "cached"

    @pytest.mark.asyncio
    async def test_enhance_skip_cache(self):
        """skip_cache sollte Cache ignorieren."""
        cache = EnhancementCache()
        enhancer = PromptEnhancer(cache=cache)

        # Cache fuellen
        enriched = EnrichedPrompt(
            original_query="Wiki test",
            enhancement_type=EnhancementType.RESEARCH,
            context_items=[ContextItem(source="wiki", title="Old", content="old")]
        )
        cache.set(enriched)

        # Mit skip_cache sollte Cache ignoriert werden
        result = await enhancer.enhance("Wiki test", skip_cache=True)

        # Kein Cache-Hit
        assert result.cache_hit is False

    @pytest.mark.asyncio
    async def test_enhance_force_type(self):
        """force_type sollte Erkennung ueberschreiben."""
        enhancer = PromptEnhancer()

        # Einfache Query, aber RESEARCH erzwingen
        result = await enhancer.enhance(
            "Hello World",
            force_type=EnhancementType.RESEARCH
        )

        assert result.enhancement_type == EnhancementType.RESEARCH

    @pytest.mark.asyncio
    async def test_enhance_event_callback(self):
        """Event-Callbacks sollten aufgerufen werden."""
        events = []

        async def event_handler(event_type, data):
            events.append((event_type, data))

        enhancer = PromptEnhancer(event_callback=event_handler)

        await enhancer.enhance("Wie im Wiki beschrieben")

        # Start und Complete Events
        assert len(events) >= 2
        assert events[0][0] == "enhancement_start"
        assert events[-1][0] == "enhancement_complete"

    def test_confirm_accepted(self):
        """Bestaetigung sollte Status aendern."""
        enhancer = PromptEnhancer()

        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH,
            confirmation_status=ConfirmationStatus.PENDING
        )

        result = enhancer.confirm(enriched, True)

        assert result.confirmation_status == ConfirmationStatus.CONFIRMED

    def test_confirm_rejected(self):
        """Ablehnung sollte Status aendern."""
        enhancer = PromptEnhancer()

        enriched = EnrichedPrompt(
            original_query="test",
            enhancement_type=EnhancementType.RESEARCH,
            confirmation_status=ConfirmationStatus.PENDING
        )

        result = enhancer.confirm(enriched, False, feedback="Nicht relevant")

        assert result.confirmation_status == ConfirmationStatus.REJECTED
        assert result.user_feedback == "Nicht relevant"


class TestSingletonAccess:
    """Tests fuer Singleton-Zugriff."""

    def test_get_enhancement_cache_singleton(self):
        """get_enhancement_cache sollte Singleton liefern."""
        cache1 = get_enhancement_cache()
        cache2 = get_enhancement_cache()

        assert cache1 is cache2

    def test_get_prompt_enhancer_singleton(self):
        """get_prompt_enhancer sollte Singleton liefern."""
        # Reset singleton fuer Test
        import app.agent.prompt_enhancer as pe
        pe._prompt_enhancer = None

        enhancer1 = get_prompt_enhancer()
        enhancer2 = get_prompt_enhancer()

        assert enhancer1 is enhancer2


class TestSkillHints:
    """Tests fuer Skill-basierte Hints (nach Migration von Capabilities)."""

    def test_lazy_load_sequential_thinking(self):
        """SequentialThinking sollte lazy geladen werden."""
        enhancer = PromptEnhancer()

        assert enhancer._sequential_thinking is None
        sequential = enhancer._get_sequential_thinking()
        # Kann None sein wenn Import fehlschlaegt

    @pytest.mark.asyncio
    async def test_collect_research_returns_skill_hint(self):
        """Research-Sammlung sollte Skill-Hint liefern."""
        enhancer = PromptEnhancer()

        items = await enhancer._collect_research_hints("test query")

        assert len(items) == 1
        assert items[0].source == "skill_hint"
        assert "/research" in items[0].content or "/sc:research" in items[0].content

    @pytest.mark.asyncio
    async def test_collect_analyze_returns_skill_hint(self):
        """Analyze-Sammlung sollte Skill-Hint liefern."""
        enhancer = PromptEnhancer()

        items = await enhancer._collect_analyze_hints("refactor this class")

        assert len(items) == 1
        assert items[0].source == "skill_hint"
        assert "/analyze" in items[0].content or "/sc:analyze" in items[0].content

    @pytest.mark.asyncio
    async def test_collect_sequential_with_hint_detection(self):
        """Sequential-Sammlung sollte Hints basierend auf Keywords liefern.

        NOTE: Fuer Performance-Gruende wird kein LLM-Call waehrend Enhancement
        gemacht. Stattdessen werden heuristische Hints basierend auf Keywords
        geliefert. Das vollstaendige Sequential Thinking laeuft spaeter.
        """
        enhancer = PromptEnhancer()

        # Test mit Debug-Keywords
        items = await enhancer._collect_sequential_context("debug diesen fehler bitte")

        assert len(items) >= 1
        # Sollte Hints fuer Fehleranalyse enthalten
        sources = [item.source for item in items]
        assert "sequential_hint" in sources

        # Test mit Warum-Frage
        items = await enhancer._collect_sequential_context("warum funktioniert das nicht")

        assert len(items) >= 1
        assert "sequential_hint" in [item.source for item in items]

    @pytest.mark.asyncio
    async def test_enhancement_with_research_type_uses_hints(self):
        """RESEARCH-Enhancement sollte Skill-Hints verwenden."""
        enhancer = PromptEnhancer()

        result = await enhancer.enhance(
            "Wie im Wiki beschrieben implementieren",
            force_type=EnhancementType.RESEARCH
        )

        assert result.enhancement_type == EnhancementType.RESEARCH
        # Sollte Skill-Hints enthalten
        assert len(result.context_items) >= 1
        assert any(item.source == "skill_hint" for item in result.context_items)

    @pytest.mark.asyncio
    async def test_enhancement_with_analyze_type_uses_hints(self):
        """ANALYZE-Enhancement sollte Skill-Hints verwenden."""
        enhancer = PromptEnhancer()

        result = await enhancer.enhance(
            "Refactor diese Klasse",
            force_type=EnhancementType.ANALYZE
        )

        assert result.enhancement_type == EnhancementType.ANALYZE
        # Sollte Skill-Hints enthalten
        assert len(result.context_items) >= 1
        assert any(item.source == "skill_hint" for item in result.context_items)
