"""
Tests fuer PromptEnhancer - MCP-basierte Kontext-Anreicherung.

Testet:
- EnhancementDetector: Erkennung des Enhancement-Typs
- EnhancementCache: Caching und TTL
- PromptEnhancer: Kontext-Sammlung und User-Bestaetigung
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

    def test_detect_brainstorm_keywords(self):
        """Brainstorm-Keywords sollten erkannt werden."""
        detector = EnhancementDetector()

        assert detector.detect("Neues Feature fuer User-Management") == EnhancementType.BRAINSTORM
        assert detector.detect("Wie koennte ein System fuer X aussehen?") == EnhancementType.BRAINSTORM
        assert detector.detect("Idee: automatische Tests") == EnhancementType.BRAINSTORM

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

        # Kein Cache-Hit (da kein MCP konfiguriert, auch kein neuer Kontext)
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

    @pytest.mark.asyncio
    async def test_enhance_fallback_on_error(self):
        """Bei Fehlern in einzelnen Quellen sollte Fallback greifen."""
        enhancer = PromptEnhancer()

        # Research-Capability mocken um Fehler zu werfen
        enhancer._research_capability = MagicMock()
        enhancer._research_capability.search = AsyncMock(
            side_effect=Exception("Network error")
        )

        result = await enhancer.enhance(
            "Wie im Wiki beschrieben",
            force_type=EnhancementType.RESEARCH
        )

        # Sollte trotzdem einen EnrichedPrompt liefern (keine Items = auto-confirmed)
        assert result is not None
        assert result.confirmation_status == ConfirmationStatus.CONFIRMED
        # Kein Kontext gesammelt
        assert len(result.context_items) == 0

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


class TestMCPCapabilityWiring:
    """Tests fuer MCP Capability Integration."""

    def test_lazy_load_research_capability(self):
        """ResearchCapability sollte lazy geladen werden."""
        enhancer = PromptEnhancer()

        # Vor dem Laden sollte None sein
        assert enhancer._research_capability is None

        # Nach dem Laden sollte eine Instanz existieren (falls verfuegbar)
        research = enhancer._get_research_capability()
        # Kann None sein wenn Import fehlschlaegt, aber sollte nicht crashen
        # Der Test verifiziert, dass die Methode funktioniert

    def test_lazy_load_sequential_thinking(self):
        """SequentialThinking sollte lazy geladen werden."""
        enhancer = PromptEnhancer()

        assert enhancer._sequential_thinking is None
        sequential = enhancer._get_sequential_thinking()
        # Kann None sein wenn Import fehlschlaegt

    def test_lazy_load_analyze_capability(self):
        """AnalyzeCapability sollte lazy geladen werden."""
        enhancer = PromptEnhancer()

        assert enhancer._analyze_capability is None
        analyze = enhancer._get_analyze_capability()
        # Kann None sein wenn Import fehlschlaegt

    def test_lazy_load_brainstorm_capability(self):
        """BrainstormCapability sollte lazy geladen werden."""
        enhancer = PromptEnhancer()

        assert enhancer._brainstorm_capability is None
        brainstorm = enhancer._get_brainstorm_capability()
        # Kann None sein wenn Import fehlschlaegt

    @pytest.mark.asyncio
    async def test_collect_research_with_mock_capability(self):
        """Research-Sammlung sollte mit Mock-Capability funktionieren."""
        enhancer = PromptEnhancer()

        # Mock ResearchCapability
        mock_session = MagicMock()
        mock_session.artifacts = [
            MagicMock(
                artifact_type="research_report",
                content="Test research report content"
            )
        ]

        mock_capability = MagicMock()
        mock_capability.execute = AsyncMock(return_value=mock_session)
        enhancer._research_capability = mock_capability

        items = await enhancer._collect_research_context("test query")

        assert len(items) == 1
        assert items[0].source == "research_report"
        assert "Test research report" in items[0].content

    @pytest.mark.asyncio
    async def test_collect_sequential_with_mock_thinking(self):
        """Sequential-Sammlung sollte mit Mock-Thinking funktionieren."""
        enhancer = PromptEnhancer()

        # Mock SequentialThinking Session
        mock_step = MagicMock()
        mock_step.step_type = MagicMock(value="analysis")
        mock_step.title = "Test Step"
        mock_step.step_number = 1
        mock_step.content = "Test analysis content"
        mock_step.confidence = 0.8

        mock_session = MagicMock()
        mock_session.steps = [mock_step]
        mock_session.hypotheses = []
        mock_session.conclusion = "Test conclusion"

        mock_thinking = MagicMock()
        mock_thinking.think = AsyncMock(return_value=mock_session)
        enhancer._sequential_thinking = mock_thinking

        items = await enhancer._collect_sequential_context("debug problem")

        assert len(items) >= 1
        # Sollte Step und Conclusion enthalten
        sources = [item.source for item in items]
        assert "sequential_analysis" in sources or "sequential_conclusion" in sources

    @pytest.mark.asyncio
    async def test_collect_analyze_with_mock_capability(self):
        """Analyze-Sammlung sollte mit Mock-Capability funktionieren."""
        enhancer = PromptEnhancer()

        # Mock AnalyzeCapability Session
        mock_step = MagicMock()
        mock_step.phase = MagicMock(value="analyze")
        mock_step.title = "Code Quality Analysis"
        mock_step.content = "Found issues in the code"
        mock_step.insights = ["Issue 1", "Issue 2"]

        mock_session = MagicMock()
        mock_session.steps = [mock_step]

        mock_capability = MagicMock()
        mock_capability.execute = AsyncMock(return_value=mock_session)
        enhancer._analyze_capability = mock_capability

        items = await enhancer._collect_analyze_context("refactor this class")

        assert len(items) >= 1
        assert items[0].source == "code_analysis"

    @pytest.mark.asyncio
    async def test_collect_brainstorm_with_mock_capability(self):
        """Brainstorm-Sammlung sollte mit Mock-Capability funktionieren."""
        enhancer = PromptEnhancer()

        # Mock BrainstormCapability Session
        mock_step = MagicMock()
        mock_step.phase = MagicMock(value="explore")
        mock_step.title = "Requirements Exploration"
        mock_step.content = "Explored user requirements"
        mock_step.questions = ["What is the target audience?"]

        mock_artifact = MagicMock()
        mock_artifact.artifact_type = "requirements"
        mock_artifact.title = "Requirements Document"
        mock_artifact.content = "User requirements..."

        mock_session = MagicMock()
        mock_session.steps = [mock_step]
        mock_session.artifacts = [mock_artifact]

        mock_capability = MagicMock()
        mock_capability.execute = AsyncMock(return_value=mock_session)
        enhancer._brainstorm_capability = mock_capability

        items = await enhancer._collect_brainstorm_context("neues feature")

        assert len(items) >= 1
        sources = [item.source for item in items]
        assert "brainstorm_exploration" in sources or "brainstorm_requirements" in sources

    @pytest.mark.asyncio
    async def test_graceful_failure_when_capability_unavailable(self):
        """Enhancement sollte graceful failen wenn Capability nicht verfuegbar."""
        enhancer = PromptEnhancer()

        # Explizit None setzen um lazy-loading zu verhindern
        enhancer._research_capability = None
        enhancer._sequential_thinking = None
        enhancer._analyze_capability = None
        enhancer._brainstorm_capability = None

        # Getter patchen um None zurueckzugeben (simuliert fehlende Imports)
        with patch.object(enhancer, '_get_research_capability', return_value=None), \
             patch.object(enhancer, '_get_sequential_thinking', return_value=None), \
             patch.object(enhancer, '_get_analyze_capability', return_value=None), \
             patch.object(enhancer, '_get_brainstorm_capability', return_value=None):

            items = await enhancer._collect_research_context("test")
            assert items == []

            items = await enhancer._collect_sequential_context("test")
            assert items == []

            items = await enhancer._collect_analyze_context("test")
            assert items == []

            items = await enhancer._collect_brainstorm_context("test")
            assert items == []

    @pytest.mark.asyncio
    async def test_real_capability_integration_if_available(self):
        """Wenn echte Capabilities verfuegbar sind, sollten sie funktionieren."""
        enhancer = PromptEnhancer()

        # Versuche Research-Capability zu laden
        research = enhancer._get_research_capability()

        if research is not None:
            # Wenn verfuegbar, sollte Kontext gesammelt werden
            items = await enhancer._collect_research_context("python programming")
            # Kann leer sein bei Netzwerkproblemen, aber sollte nicht crashen
            assert isinstance(items, list)
