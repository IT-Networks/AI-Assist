"""
Tests für den Research Router.

Testet Query-Klassifikation, Sanitization und Multi-Source Research.
"""

import pytest
from app.services.research_router import (
    QueryClassifier,
    QuerySanitizer,
    ResearchRouter,
    QueryClassification,
    SourceType,
    ResearchResult,
    AggregatedContext,
)
from app.models.skill import ResearchConfig, ResearchScope


class TestQueryClassifier:
    """Tests für den Query Classifier."""

    @pytest.fixture
    def classifier(self):
        return QueryClassifier()

    def test_technical_query(self, classifier):
        """Technische Queries werden korrekt klassifiziert."""
        queries = [
            "How to implement REST API in Spring Boot",
            "Best practices for Java microservices",
            "React hooks tutorial",
            "Docker kubernetes deployment",
        ]
        for query in queries:
            classification, keywords = classifier.classify(query)
            assert classification == QueryClassification.TECHNICAL, f"Failed for: {query}"

    def test_business_query(self, classifier):
        """Business-Queries werden korrekt klassifiziert."""
        queries = [
            "Wie funktioniert der Bestellprozess",
            "Anforderungen für Kundenportal",
            "Use Case für Rechnungsstellung",
        ]
        for query in queries:
            classification, keywords = classifier.classify(query)
            assert classification in (QueryClassification.BUSINESS, QueryClassification.MIXED), f"Failed for: {query}"

    def test_internal_query(self, classifier):
        """Interne Queries werden korrekt klassifiziert."""
        queries = [
            "Wie rufe ich OrderService auf",
            "PROJ-12345 Status",
            "Verbindung zu 192.168.1.100",
            "intranet.company.com Dokumentation",
        ]
        for query in queries:
            classification, keywords = classifier.classify(query)
            assert classification == QueryClassification.INTERNAL, f"Failed for: {query}"

    def test_mixed_query(self, classifier):
        """Gemischte Queries werden erkannt."""
        query = "Best practices für unseren Bestellprozess mit Spring Boot"
        classification, keywords = classifier.classify(query)
        assert classification in (QueryClassification.MIXED, QueryClassification.TECHNICAL)

    def test_keyword_extraction(self, classifier):
        """Keywords werden korrekt extrahiert."""
        query = "How to implement authentication in Spring Boot"
        classification, keywords = classifier.classify(query)
        assert "implement" in keywords or "authentication" in keywords
        assert "to" not in keywords  # Stoppwort
        assert "in" not in keywords  # Stoppwort


class TestQuerySanitizer:
    """Tests für den Query Sanitizer."""

    @pytest.fixture
    def sanitizer(self):
        return QuerySanitizer()

    def test_service_name_sanitization(self, sanitizer):
        """Service-Namen werden ersetzt."""
        original = "Wie rufe ich OrderService und CustomerController auf?"
        sanitized = sanitizer.sanitize(original)
        assert "OrderService" not in sanitized
        assert "CustomerController" not in sanitized
        assert "Service" in sanitized or "Controller" in sanitized

    def test_project_code_removal(self, sanitizer):
        """Projekt-Codes werden entfernt."""
        original = "Status von PROJ-12345 und ABC-999"
        sanitized = sanitizer.sanitize(original)
        assert "PROJ-12345" not in sanitized
        assert "ABC-999" not in sanitized

    def test_ip_address_removal(self, sanitizer):
        """IP-Adressen werden entfernt."""
        original = "Verbindung zu 192.168.1.100 herstellen"
        sanitized = sanitizer.sanitize(original)
        assert "192.168.1.100" not in sanitized

    def test_environment_removal(self, sanitizer):
        """Umgebungsnamen werden entfernt."""
        original = "Deployment auf PROD und DEV"
        sanitized = sanitizer.sanitize(original)
        # PROD und DEV sollten entfernt sein
        assert "PROD" not in sanitized.upper() or "prod" not in sanitized.lower()

    def test_get_removed_terms(self, sanitizer):
        """Entfernte Begriffe werden korrekt identifiziert."""
        original = "OrderService PROJ-123"
        sanitized = sanitizer.sanitize(original)
        removed = sanitizer.get_removed_terms(original, sanitized)
        assert len(removed) > 0

    def test_safe_query_preserved(self, sanitizer):
        """Sichere Queries bleiben erhalten."""
        original = "How to implement REST API"
        sanitized = sanitizer.sanitize(original)
        assert "REST" in sanitized
        assert "API" in sanitized


class TestResearchRouter:
    """Tests für den Research Router."""

    @pytest.fixture
    def router(self):
        return ResearchRouter()

    def test_select_sources_internal_only(self, router):
        """INTERNAL_ONLY schließt Web aus."""
        config = ResearchConfig(
            scope=ResearchScope.INTERNAL_ONLY,
            allowed_sources=["skills", "handbook", "confluence", "web"]
        )
        sources = router._select_sources(
            QueryClassification.TECHNICAL,
            config
        )
        assert SourceType.WEB not in sources
        assert SourceType.SKILL in sources
        assert SourceType.HANDBOOK in sources

    def test_select_sources_external_safe_technical(self, router):
        """EXTERNAL_SAFE erlaubt Web für TECHNICAL."""
        config = ResearchConfig(
            scope=ResearchScope.EXTERNAL_SAFE,
            allowed_sources=["skills", "web"]
        )
        sources = router._select_sources(
            QueryClassification.TECHNICAL,
            config
        )
        assert SourceType.WEB in sources
        assert SourceType.SKILL in sources

    def test_select_sources_external_safe_internal(self, router):
        """EXTERNAL_SAFE verbietet Web für INTERNAL."""
        config = ResearchConfig(
            scope=ResearchScope.EXTERNAL_SAFE,
            allowed_sources=["skills", "web"]
        )
        sources = router._select_sources(
            QueryClassification.INTERNAL,
            config
        )
        assert SourceType.WEB not in sources

    def test_select_sources_all(self, router):
        """ALL erlaubt alle Quellen."""
        config = ResearchConfig(
            scope=ResearchScope.ALL,
            allowed_sources=["skills", "web"]
        )
        sources = router._select_sources(
            QueryClassification.INTERNAL,  # Auch bei INTERNAL
            config
        )
        assert SourceType.WEB in sources


class TestResearchResult:
    """Tests für ResearchResult."""

    def test_to_dict(self):
        """to_dict serialisiert korrekt."""
        result = ResearchResult(
            source=SourceType.SKILL,
            source_name="Test Skill",
            content="Test content",
            relevance_score=0.8,
            url="http://example.com"
        )
        data = result.to_dict()
        assert data["source"] == "skill"
        assert data["source_name"] == "Test Skill"
        assert data["relevance_score"] == 0.8


class TestAggregatedContext:
    """Tests für AggregatedContext."""

    def test_to_context_string(self):
        """to_context_string formatiert korrekt."""
        context = AggregatedContext(
            query="Test query",
            classification=QueryClassification.TECHNICAL,
            results=[
                ResearchResult(
                    source=SourceType.SKILL,
                    source_name="Test Skill",
                    content="Test content",
                    relevance_score=0.8
                )
            ],
            total_tokens=100,
            sources_used=[SourceType.SKILL]
        )
        context_str = context.to_context_string()
        assert "RECHERCHE-ERGEBNISSE" in context_str
        assert "Test query" in context_str
        assert "Test content" in context_str
        assert "SKILL" in context_str

    def test_empty_results(self):
        """Leere Ergebnisse werden korrekt behandelt."""
        context = AggregatedContext(
            query="Test",
            classification=QueryClassification.TECHNICAL,
            results=[],
            total_tokens=0,
            sources_used=[]
        )
        assert context.to_context_string() == ""


class TestRouterAggregation:
    """Tests für die Ergebnis-Aggregation."""

    @pytest.fixture
    def router(self):
        return ResearchRouter()

    def test_deduplication(self, router):
        """Duplicate Ergebnisse werden entfernt."""
        results = [
            ResearchResult(
                source=SourceType.SKILL,
                source_name="Skill 1",
                content="Same content here",
                relevance_score=0.8
            ),
            ResearchResult(
                source=SourceType.HANDBOOK,
                source_name="Handbook",
                content="Same content here",  # Duplikat
                relevance_score=0.7
            ),
            ResearchResult(
                source=SourceType.CONFLUENCE,
                source_name="Wiki",
                content="Different content",
                relevance_score=0.6
            ),
        ]
        aggregated = router._aggregate_results(
            query="test",
            classification=QueryClassification.TECHNICAL,
            results=results
        )
        # Nur 2 unique Ergebnisse
        assert len(aggregated.results) == 2

    def test_relevance_sorting(self, router):
        """Ergebnisse werden nach Relevanz sortiert."""
        results = [
            ResearchResult(
                source=SourceType.SKILL,
                source_name="Low",
                content="Content 1",
                relevance_score=0.3
            ),
            ResearchResult(
                source=SourceType.HANDBOOK,
                source_name="High",
                content="Content 2",
                relevance_score=0.9
            ),
            ResearchResult(
                source=SourceType.CONFLUENCE,
                source_name="Medium",
                content="Content 3",
                relevance_score=0.6
            ),
        ]
        aggregated = router._aggregate_results(
            query="test",
            classification=QueryClassification.TECHNICAL,
            results=results
        )
        assert aggregated.results[0].relevance_score == 0.9
        assert aggregated.results[-1].relevance_score == 0.3

    def test_max_results_limit(self, router):
        """Ergebnisse werden auf max_results beschränkt."""
        results = [
            ResearchResult(
                source=SourceType.SKILL,
                source_name=f"Skill {i}",
                content=f"Content {i}",
                relevance_score=0.5
            )
            for i in range(20)
        ]
        aggregated = router._aggregate_results(
            query="test",
            classification=QueryClassification.TECHNICAL,
            results=results,
            max_results=5
        )
        assert len(aggregated.results) == 5

    def test_token_estimation(self, router):
        """Token-Schätzung ist plausibel."""
        results = [
            ResearchResult(
                source=SourceType.SKILL,
                source_name="Test",
                content="A" * 400,  # 400 Zeichen ≈ 100 Tokens
                relevance_score=0.8
            )
        ]
        aggregated = router._aggregate_results(
            query="test",
            classification=QueryClassification.TECHNICAL,
            results=results
        )
        assert aggregated.total_tokens == 100  # 400 / 4
