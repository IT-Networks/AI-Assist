"""Tests für ResultValidator - Relevanz-Scoring und Source-Metadata."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.agent.result_validator import (
    ResultValidator,
    ValidationResult,
    SourceMetadata,
    get_result_validator,
    reset_result_validator,
)
from app.agent.tools import ToolResult


@pytest.fixture(autouse=True)
def reset_validator():
    """Reset singleton vor jedem Test."""
    reset_result_validator()
    yield
    reset_result_validator()


class TestSourceMetadata:
    """Tests für SourceMetadata Dataclass."""

    def test_format_citation_with_url(self):
        """Citation mit URL enthält Markdown-Link."""
        meta = SourceMetadata(
            source_type="confluence",
            source_id="12345",
            source_title="Test Page",
            source_url="https://confluence.example.com/pages/12345"
        )
        citation = meta.format_citation()
        assert "[CONFLUENCE: Test Page]" in citation
        assert "(https://confluence.example.com/pages/12345)" in citation

    def test_format_citation_without_url(self):
        """Citation ohne URL enthält ID."""
        meta = SourceMetadata(
            source_type="code",
            source_id="src/Service.java",
            source_title="Service.java"
        )
        citation = meta.format_citation()
        assert "[CODE: Service.java | src/Service.java]" in citation

    def test_format_header(self):
        """Header Format für Tool-Results."""
        meta = SourceMetadata(
            source_type="jira",
            source_id="PROJ-123",
            source_title="Bug in Authentication"
        )
        header = meta.format_header()
        assert "[QUELLE: jira | Bug in Authentication]" in header


class TestResultValidator:
    """Tests für ResultValidator Klasse."""

    def test_tokenize_removes_stopwords(self):
        """Tokenizer entfernt Stopwords."""
        validator = ResultValidator()
        tokens = validator._tokenize("The quick brown fox and the lazy dog")
        assert "the" not in tokens
        assert "and" not in tokens
        assert "quick" in tokens
        assert "brown" in tokens
        assert "fox" in tokens

    def test_tokenize_handles_empty(self):
        """Tokenizer bei leerem Text."""
        validator = ResultValidator()
        assert validator._tokenize("") == []
        assert validator._tokenize(None) == []

    def test_calculate_relevance_exact_match(self):
        """Exakter Query-Match im Content gibt hohen Score."""
        validator = ResultValidator()
        query = "OrderService implementation"
        content = "The OrderService implementation handles all order processing logic."

        score, matched = validator._calculate_relevance(query, content)
        assert score >= 0.5
        assert "orderservice" in matched
        assert "implementation" in matched

    def test_calculate_relevance_no_match(self):
        """Kein Match gibt Score 0."""
        validator = ResultValidator()
        query = "authentication login"
        content = "Database schema for product catalog management."

        score, matched = validator._calculate_relevance(query, content)
        assert score == 0.0
        assert len(matched) == 0

    def test_calculate_relevance_partial_match(self):
        """Teilmatch gibt mittleren Score."""
        validator = ResultValidator()
        query = "OrderService payment processing"
        content = "The payment module integrates with external APIs for transaction handling."

        score, matched = validator._calculate_relevance(query, content)
        assert 0 < score < 0.5
        assert "payment" in matched

    def test_calculate_relevance_phrase_bonus(self):
        """Exakter Phrasen-Match gibt Bonus."""
        validator = ResultValidator()
        query = "spring boot configuration"
        content = "How to setup spring boot configuration for microservices."

        score, _ = validator._calculate_relevance(query, content)
        # Phrase "spring boot configuration" sollte Bonus geben
        assert score >= 0.5

    @pytest.mark.asyncio
    async def test_validate_failed_result(self):
        """Fehlgeschlagenes Tool-Result bekommt Score 0."""
        validator = ResultValidator()
        result = ToolResult(success=False, error="Connection failed")

        validation = await validator.validate(
            tool_name="search_code",
            query="test query",
            result=result
        )
        assert validation.relevance_score == 0.0
        assert validation.should_use is False
        assert "Tool-Fehler" in validation.reason

    @pytest.mark.asyncio
    async def test_validate_empty_result(self):
        """Leeres Result bekommt Score 0."""
        validator = ResultValidator()
        result = ToolResult(success=True, data="")

        validation = await validator.validate(
            tool_name="search_code",
            query="test query",
            result=result
        )
        assert validation.relevance_score == 0.0
        assert validation.should_use is False

    @pytest.mark.asyncio
    async def test_validate_relevant_result(self):
        """Relevantes Result bekommt hohen Score und should_use=True."""
        validator = ResultValidator()
        result = ToolResult(
            success=True,
            data="""
            ── src/services/OrderService.java ──
            public class OrderService {
                public void processOrder(Order order) {
                    // Order processing logic
                }
            }
            """
        )

        validation = await validator.validate(
            tool_name="search_code",
            query="OrderService processOrder",
            result=result
        )
        assert validation.relevance_score >= 0.3
        assert validation.should_use is True
        assert validation.source_metadata is not None

    @pytest.mark.asyncio
    async def test_validate_extracts_code_source(self):
        """Code-Quelle wird korrekt extrahiert."""
        validator = ResultValidator()
        result = ToolResult(
            success=True,
            data="── src/main/Service.java ──\npublic class Service {}"
        )

        validation = await validator.validate(
            tool_name="search_code",
            query="Service class",
            result=result
        )

        assert validation.source_metadata is not None
        assert validation.source_metadata.source_type == "code"
        assert "Service.java" in validation.source_metadata.source_id

    @pytest.mark.asyncio
    async def test_validate_extracts_confluence_source(self):
        """Confluence-Quelle wird korrekt extrahiert."""
        validator = ResultValidator()
        result = ToolResult(
            success=True,
            data="""
            Title: "API Documentation Guide"
            ID: 123456
            Content: How to use the REST API...
            """
        )

        validation = await validator.validate(
            tool_name="read_confluence_page",
            query="API documentation",
            result=result
        )

        assert validation.source_metadata is not None
        assert validation.source_metadata.source_type == "confluence"
        assert validation.source_metadata.source_id == "123456"

    @pytest.mark.asyncio
    async def test_validate_extracts_jira_source(self):
        """Jira-Quelle wird korrekt extrahiert."""
        validator = ResultValidator()
        result = ToolResult(
            success=True,
            data="""
            PROJ-456: Fix login issue
            Summary: Users cannot login with SSO
            Status: In Progress
            """
        )

        validation = await validator.validate(
            tool_name="get_jira_issue",
            query="login SSO issue",
            result=result
        )

        assert validation.source_metadata is not None
        assert validation.source_metadata.source_type == "jira"
        assert validation.source_metadata.source_id == "PROJ-456"


class TestValidationResult:
    """Tests für ValidationResult Methoden."""

    def test_get_content_with_source_adds_header(self):
        """Content wird mit Source-Header versehen."""
        result = ValidationResult(
            relevance_score=0.8,
            should_use=True,
            source_metadata=SourceMetadata(
                source_type="code",
                source_id="Service.java",
                source_title="Service.java"
            )
        )

        content = result.get_content_with_source("public class Service {}")
        assert "[QUELLE: code | Service.java]" in content
        assert "public class Service {}" in content

    def test_get_content_with_summary(self):
        """Summary wird statt Original-Content verwendet."""
        result = ValidationResult(
            relevance_score=0.8,
            should_use=True,
            summary="Kurze Zusammenfassung",
            source_metadata=None
        )

        content = result.get_content_with_source("Langer Original-Content...")
        assert content == "Kurze Zusammenfassung"


class TestSingleton:
    """Tests für Singleton-Pattern."""

    def test_get_result_validator_returns_same_instance(self):
        """Singleton gibt immer dieselbe Instanz zurück."""
        v1 = get_result_validator()
        v2 = get_result_validator()
        assert v1 is v2

    def test_reset_clears_instance(self):
        """Reset löscht die Singleton-Instanz."""
        v1 = get_result_validator()
        reset_result_validator()
        v2 = get_result_validator()
        assert v1 is not v2
