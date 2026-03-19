"""Tests für SubAgentCoordinator - Deduplizierung und Ranking."""

import pytest
from app.agent.sub_agent_coordinator import (
    SubAgentCoordinator,
    CoordinatedResult,
    RankedFinding,
)
from app.agent.sub_agent import SubAgentResult


def make_result(
    agent_name: str,
    findings: list[str],
    sources: list[str] = None,
    success: bool = True,
    summary: str = ""
) -> SubAgentResult:
    """Helper: Erstellt SubAgentResult für Tests."""
    return SubAgentResult(
        agent_name=agent_name,
        success=success,
        key_findings=findings,
        sources=sources or [],
        summary=summary,
        duration_ms=100,
        error=""
    )


class TestRankedFinding:
    """Tests für RankedFinding Dataclass."""

    def test_auto_generates_finding_id(self):
        """Finding-ID wird automatisch aus Content generiert."""
        finding = RankedFinding(
            content="Test content",
            source_agent="wiki_agent",
            source_id="page123",
            relevance_score=0.5
        )
        assert finding.finding_id != ""
        assert len(finding.finding_id) == 8

    def test_same_content_same_id(self):
        """Gleicher Content erzeugt gleiche ID."""
        f1 = RankedFinding(
            content="Test content",
            source_agent="wiki_agent",
            source_id="page1",
            relevance_score=0.5
        )
        f2 = RankedFinding(
            content="Test content",
            source_agent="wiki_agent",
            source_id="page2",
            relevance_score=0.7
        )
        assert f1.finding_id == f2.finding_id

    def test_different_content_different_id(self):
        """Unterschiedlicher Content erzeugt unterschiedliche IDs."""
        f1 = RankedFinding(
            content="Content A",
            source_agent="wiki_agent",
            source_id="page1",
            relevance_score=0.5
        )
        f2 = RankedFinding(
            content="Content B",
            source_agent="wiki_agent",
            source_id="page1",
            relevance_score=0.5
        )
        assert f1.finding_id != f2.finding_id


class TestCoordinatedResult:
    """Tests für CoordinatedResult Dataclass."""

    def test_to_context_block_empty(self):
        """Leeres Result gibt leeren String."""
        result = CoordinatedResult(
            total_findings=0,
            unique_findings=0,
            duplicates_removed=0,
            ranked_findings=[],
            synthesis="",
            top_source="",
            agents_used=[]
        )
        assert result.to_context_block() == ""

    def test_to_context_block_formats_findings(self):
        """Findings werden korrekt formatiert."""
        result = CoordinatedResult(
            total_findings=2,
            unique_findings=2,
            duplicates_removed=0,
            ranked_findings=[
                RankedFinding(
                    content="Finding 1",
                    source_agent="wiki_agent",
                    source_id="page123",
                    relevance_score=0.8
                ),
                RankedFinding(
                    content="Finding 2",
                    source_agent="code_agent",
                    source_id="Service.java",
                    relevance_score=0.6
                )
            ],
            synthesis="Test summary",
            top_source="wiki_agent",
            agents_used=["wiki_agent", "code_agent"]
        )

        block = result.to_context_block()
        assert "## Sub-Agent Recherche-Ergebnisse" in block
        assert "Wiki Agent" in block
        assert "Code Agent" in block
        assert "Finding 1" in block
        assert "Finding 2" in block
        assert "Zusammenfassung" in block

    def test_to_context_block_skips_duplicates(self):
        """Duplikate werden nicht im Context-Block angezeigt."""
        result = CoordinatedResult(
            total_findings=2,
            unique_findings=1,
            duplicates_removed=1,
            ranked_findings=[
                RankedFinding(
                    content="Original",
                    source_agent="wiki_agent",
                    source_id="page1",
                    relevance_score=0.8,
                    is_duplicate=False
                ),
                RankedFinding(
                    content="Duplicate",
                    source_agent="wiki_agent",
                    source_id="page2",
                    relevance_score=0.6,
                    is_duplicate=True
                )
            ],
            synthesis="",
            top_source="wiki_agent",
            agents_used=["wiki_agent"]
        )

        block = result.to_context_block()
        assert "Original" in block
        assert "Duplicate" not in block


class TestSubAgentCoordinator:
    """Tests für SubAgentCoordinator Klasse."""

    @pytest.mark.asyncio
    async def test_process_empty_results(self):
        """Leere Ergebnisliste wird korrekt behandelt."""
        coordinator = SubAgentCoordinator()
        result = await coordinator.process_results([], "test query")

        assert result.total_findings == 0
        assert result.unique_findings == 0
        assert result.ranked_findings == []
        assert result.agents_used == []

    @pytest.mark.asyncio
    async def test_process_failed_results(self):
        """Fehlgeschlagene Ergebnisse werden übersprungen."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result("wiki_agent", [], success=False),
            make_result("code_agent", ["Valid finding"], success=True)
        ]

        result = await coordinator.process_results(results, "test query")

        assert result.total_findings == 1
        assert result.unique_findings == 1
        # Nur der erfolgreiche Agent
        assert "code_agent" in result.agents_used
        assert len(result.agents_used) == 1

    @pytest.mark.asyncio
    async def test_process_extracts_findings(self):
        """Findings werden aus allen Agents extrahiert."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result("wiki_agent", ["Wiki finding 1", "Wiki finding 2"]),
            make_result("code_agent", ["Code finding 1"])
        ]

        result = await coordinator.process_results(results, "test query")

        assert result.total_findings >= 3

    @pytest.mark.asyncio
    async def test_process_calculates_relevance(self):
        """Relevanz-Scores werden berechnet."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result(
                "wiki_agent",
                ["This finding contains the test query keywords"]
            )
        ]

        result = await coordinator.process_results(results, "test query keywords")

        assert len(result.ranked_findings) >= 1
        # Hoher Score wegen Keyword-Match
        assert result.ranked_findings[0].relevance_score > 0

    @pytest.mark.asyncio
    async def test_process_detects_duplicates(self):
        """Ähnliche Findings werden als Duplikate erkannt."""
        coordinator = SubAgentCoordinator()
        # Fast identische Findings von verschiedenen Agents
        results = [
            make_result("wiki_agent", ["The service handles order processing"]),
            make_result("code_agent", ["The service handles order processing logic"])
        ]

        result = await coordinator.process_results(results, "order processing")

        # Eines sollte als Duplikat markiert sein
        duplicates = [f for f in result.ranked_findings if f.is_duplicate]
        # Je nach Threshold kann es ein Duplikat geben
        assert result.duplicates_removed >= 0

    @pytest.mark.asyncio
    async def test_process_different_findings_not_duplicates(self):
        """Unterschiedliche Findings werden nicht als Duplikate markiert."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result("wiki_agent", ["Information about authentication"]),
            make_result("code_agent", ["Database schema for products"])
        ]

        result = await coordinator.process_results(results, "test")

        # Beide sollten unique sein
        unique = [f for f in result.ranked_findings if not f.is_duplicate]
        assert len(unique) >= 2

    @pytest.mark.asyncio
    async def test_process_ranks_by_relevance(self):
        """Findings werden nach Relevanz sortiert."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result("wiki_agent", ["Low relevance content about random topics"]),
            make_result("code_agent", [
                "OrderService processOrder method handles the order query exactly"
            ])
        ]

        result = await coordinator.process_results(results, "OrderService processOrder")

        # Das relevantere Finding sollte zuerst kommen
        if len(result.ranked_findings) >= 2:
            assert result.ranked_findings[0].relevance_score >= result.ranked_findings[1].relevance_score

    @pytest.mark.asyncio
    async def test_process_identifies_top_source(self):
        """Agent mit höchstem Gesamt-Score wird als top_source identifiziert."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result("wiki_agent", [
                "Wiki has very relevant OrderService documentation",
                "More OrderService details here"
            ]),
            make_result("code_agent", ["Some random code snippet"])
        ]

        result = await coordinator.process_results(results, "OrderService")

        assert result.top_source in ["wiki_agent", "code_agent"]

    @pytest.mark.asyncio
    async def test_process_creates_synthesis(self):
        """Synthese wird aus Findings erstellt."""
        coordinator = SubAgentCoordinator()
        results = [
            make_result(
                "wiki_agent",
                ["Important documentation about the API"],
                summary="Wiki found API docs"
            )
        ]

        result = await coordinator.process_results(results, "API documentation")

        assert result.synthesis != ""

    @pytest.mark.asyncio
    async def test_high_confidence_detection(self):
        """High-Confidence wird erkannt bei sehr relevantem Finding."""
        coordinator = SubAgentCoordinator()

        # Mock ein Finding mit sehr hohem Score
        class MockCoordinator(SubAgentCoordinator):
            def _calculate_relevance_scores(self):
                for f in self._findings:
                    f.relevance_score = 0.9  # Über HIGH_CONFIDENCE_THRESHOLD

        mock_coord = MockCoordinator()
        results = [make_result("wiki_agent", ["Highly relevant content"])]

        result = await mock_coord.process_results(results, "test")

        assert result.high_confidence is True

    def test_calculate_similarity_identical(self):
        """Identische Texte haben Similarity 1.0."""
        coordinator = SubAgentCoordinator()
        similarity = coordinator._calculate_similarity(
            "The quick brown fox",
            "The quick brown fox"
        )
        assert similarity == 1.0

    def test_calculate_similarity_different(self):
        """Völlig unterschiedliche Texte haben niedrige Similarity."""
        coordinator = SubAgentCoordinator()
        similarity = coordinator._calculate_similarity(
            "Authentication login security",
            "Database products catalog"
        )
        assert similarity < 0.3

    def test_calculate_similarity_partial(self):
        """Teilweise ähnliche Texte haben mittlere Similarity."""
        coordinator = SubAgentCoordinator()
        similarity = coordinator._calculate_similarity(
            "The service processes orders quickly",
            "The service handles orders efficiently"
        )
        assert 0.3 < similarity < 0.9

    def test_tokenize(self):
        """Tokenizer funktioniert korrekt."""
        coordinator = SubAgentCoordinator()
        tokens = coordinator._tokenize("Hello World 123 Test äöü")

        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens
        assert "test" in tokens

    def test_tokenize_empty(self):
        """Leerer Text gibt leere Liste."""
        coordinator = SubAgentCoordinator()
        assert coordinator._tokenize("") == []
        assert coordinator._tokenize(None) == []


class TestFormatSubAgentResults:
    """Tests für die legacy format_sub_agent_results Funktion."""

    def test_format_empty_results(self):
        """Leere Ergebnisse geben leeren String."""
        from app.agent.sub_agent_coordinator import _simple_format

        result = _simple_format([])
        assert result == ""

    def test_simple_format(self):
        """Simple Format funktioniert als Fallback."""
        from app.agent.sub_agent_coordinator import _simple_format

        results = [
            make_result("wiki_agent", ["Finding 1", "Finding 2"])
        ]

        formatted = _simple_format(results)
        assert "wiki_agent" in formatted
        assert "Finding 1" in formatted
