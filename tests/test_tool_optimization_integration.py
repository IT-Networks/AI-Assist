"""
Integrationstests fuer Tool-Optimierung mit LLM-Mocks.

Testet das Zusammenspiel von:
- Meta-Tools (combined_search, batch_read_files)
- Tool-Cache
- Tool-Budget
- Orchestrator
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Dict, Any

from app.agent.tools import ToolResult, ToolRegistry, get_tool_registry
from app.agent.tool_cache import ToolResultCache, reset_tool_cache
from app.agent.tool_budget import ToolBudget, BudgetLevel, create_budget
from app.agent.meta_tools import combined_search, batch_read_files


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_search_code():
    """Mock fuer search_code Tool."""
    async def _search(**kwargs):
        query = kwargs.get("query", "")
        return ToolResult(
            success=True,
            data=f"Code-Ergebnis fuer '{query}':\n  UserService.java:42 - public void {query}()"
        )
    return _search


@pytest.fixture
def mock_search_handbook():
    """Mock fuer search_handbook Tool."""
    async def _search(**kwargs):
        query = kwargs.get("query", "")
        return ToolResult(
            success=True,
            data=f"Handbuch-Ergebnis fuer '{query}':\n  [Service] Dokumentation zu {query}"
        )
    return _search


@pytest.fixture
def mock_search_skills():
    """Mock fuer search_skills Tool."""
    async def _search(**kwargs):
        query = kwargs.get("query", "")
        return ToolResult(
            success=True,
            data=f"Skill-Ergebnis fuer '{query}':\n  [Skill] Wissen ueber {query}"
        )
    return _search


@pytest.fixture
def mock_read_file():
    """Mock fuer read_file Tool."""
    async def _read(**kwargs):
        path = kwargs.get("path", "unknown")
        return ToolResult(
            success=True,
            data=f"=== Datei: {path} ===\n1: public class Test {{\n2:   // Content\n3: }}"
        )
    return _read


@pytest.fixture
def fresh_cache():
    """Frischer Cache fuer jeden Test."""
    reset_tool_cache()
    cache = ToolResultCache(ttl_seconds=60, max_entries=50)
    yield cache
    reset_tool_cache()


@pytest.fixture
def fresh_budget():
    """Frisches Budget fuer jeden Test."""
    return create_budget(max_iterations=30, max_tools_per_iteration=10)


# ══════════════════════════════════════════════════════════════════════════════
# Meta-Tools Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedSearchIntegration:
    """Integrationstests fuer combined_search."""

    @pytest.mark.asyncio
    async def test_combined_search_all_sources(
        self, mock_search_code, mock_search_handbook, mock_search_skills
    ):
        """combined_search sollte alle Quellen parallel durchsuchen."""
        with patch("app.agent.tools.search_code", mock_search_code), \
             patch("app.agent.tools.search_handbook", mock_search_handbook), \
             patch("app.agent.tools.search_skills", mock_search_skills):

            result = await combined_search(
                query="getUserById",
                sources="code,handbook,skills",
                max_per_source=5
            )

            assert result.success
            assert "Code" in result.data
            assert "Handbuch" in result.data
            assert "Skill" in result.data
            assert "getUserById" in result.data

    @pytest.mark.asyncio
    async def test_combined_search_with_include_content(
        self, mock_search_code, mock_read_file
    ):
        """combined_search mit include_content sollte read_files=True setzen."""
        call_args = {}

        async def capture_search(**kwargs):
            call_args.update(kwargs)
            return ToolResult(success=True, data="Found")

        with patch("app.agent.tools.search_code", capture_search):
            await combined_search(
                query="test",
                sources="code",
                include_content=True
            )

            assert call_args.get("read_files") is True

    @pytest.mark.asyncio
    async def test_combined_search_partial_failure(
        self, mock_search_code
    ):
        """combined_search sollte bei Teilfehlern trotzdem Ergebnisse liefern."""
        async def failing_search(**kwargs):
            return ToolResult(success=False, error="Index nicht verfuegbar")

        with patch("app.agent.tools.search_code", mock_search_code), \
             patch("app.agent.tools.search_handbook", failing_search):

            result = await combined_search(
                query="test",
                sources="code,handbook"
            )

            assert result.success  # Weil Code erfolgreich war
            assert "Code" in result.data
            assert "nicht verfuegbar" in result.data or "Fehler" in result.data


class TestBatchReadFilesIntegration:
    """Integrationstests fuer batch_read_files."""

    @pytest.mark.asyncio
    async def test_batch_read_multiple_files(self, mock_read_file):
        """batch_read_files sollte mehrere Dateien parallel lesen."""
        with patch("app.agent.tools.read_file", mock_read_file):
            result = await batch_read_files(
                paths="UserService.java, UserRepository.java, User.java"
            )

            assert result.success
            assert "UserService.java" in result.data
            assert "UserRepository.java" in result.data
            assert "User.java" in result.data
            assert "3/3" in result.data

    @pytest.mark.asyncio
    async def test_batch_read_with_failures(self, mock_read_file):
        """batch_read_files sollte Teilerfolge melden."""
        async def partial_read(**kwargs):
            path = kwargs.get("path", "")
            if "bad" in path:
                return ToolResult(success=False, error="Datei nicht gefunden")
            return await mock_read_file(**kwargs)

        with patch("app.agent.tools.read_file", partial_read):
            result = await batch_read_files(
                paths="good.java, bad.java, another_good.java"
            )

            assert result.success
            assert "2/3" in result.data

    @pytest.mark.asyncio
    async def test_batch_read_respects_limit(self, mock_read_file):
        """batch_read_files sollte max_lines_per_file weitergeben."""
        captured_limits = []

        async def capture_read(**kwargs):
            captured_limits.append(kwargs.get("limit", 0))
            return await mock_read_file(**kwargs)

        with patch("app.agent.tools.read_file", capture_read):
            await batch_read_files(
                paths="file1.java, file2.java",
                max_lines_per_file=50
            )

            assert all(limit == 50 for limit in captured_limits)


# ══════════════════════════════════════════════════════════════════════════════
# Cache Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCacheIntegration:
    """Integrationstests fuer Tool-Cache."""

    @pytest.mark.asyncio
    async def test_cache_prevents_duplicate_searches(self, fresh_cache, mock_search_code):
        """Cache sollte doppelte Suchen verhindern."""
        call_count = 0

        async def counting_search(**kwargs):
            nonlocal call_count
            call_count += 1
            return await mock_search_code(**kwargs)

        with patch("app.agent.tools.search_code", counting_search):
            # Erste Suche
            result1 = await counting_search(query="test")
            fresh_cache.set("search_code", {"query": "test"}, result1)

            # Zweite Suche - sollte aus Cache kommen
            cached = fresh_cache.get("search_code", {"query": "test"})

            assert cached is not None
            assert cached.data == result1.data
            assert call_count == 1  # Nur einmal aufgerufen

    @pytest.mark.asyncio
    async def test_cache_different_queries(self, fresh_cache, mock_search_code):
        """Cache sollte unterschiedliche Queries unterscheiden."""
        with patch("app.agent.tools.search_code", mock_search_code):
            result1 = await mock_search_code(query="foo")
            result2 = await mock_search_code(query="bar")

            fresh_cache.set("search_code", {"query": "foo"}, result1)
            fresh_cache.set("search_code", {"query": "bar"}, result2)

            cached1 = fresh_cache.get("search_code", {"query": "foo"})
            cached2 = fresh_cache.get("search_code", {"query": "bar"})

            assert "foo" in cached1.data
            assert "bar" in cached2.data

    def test_cache_stats_tracking(self, fresh_cache):
        """Cache sollte Statistiken tracken."""
        result = ToolResult(success=True, data="test")

        # Set
        fresh_cache.set("search_code", {"query": "test"}, result)

        # Hit
        fresh_cache.get("search_code", {"query": "test"})
        fresh_cache.get("search_code", {"query": "test"})

        # Miss
        fresh_cache.get("search_code", {"query": "other"})

        stats = fresh_cache.get_stats()
        assert stats.total_hits == 2
        assert stats.total_misses == 1
        assert stats.total_sets == 1


# ══════════════════════════════════════════════════════════════════════════════
# Budget Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestBudgetIntegration:
    """Integrationstests fuer Tool-Budget."""

    def test_budget_tracks_tool_calls(self, fresh_budget):
        """Budget sollte Tool-Aufrufe tracken."""
        fresh_budget.record_tool_call("search_code", duration_ms=100)
        fresh_budget.record_tool_call("search_code", duration_ms=50, cached=True)
        fresh_budget.record_tool_call("read_file", duration_ms=30)

        assert fresh_budget.total_tools_used == 3
        assert fresh_budget.cache_hits == 1
        assert fresh_budget.cache_misses == 2

        top = fresh_budget.get_top_tools(2)
        assert top[0] == ("search_code", 2)

    def test_budget_level_changes(self, fresh_budget):
        """Budget-Level sollte sich bei Verbrauch aendern."""
        assert fresh_budget.level == BudgetLevel.NORMAL

        # Simuliere viele Iterationen
        for _ in range(21):
            fresh_budget.next_iteration()

        assert fresh_budget.level == BudgetLevel.LOW

        for _ in range(5):
            fresh_budget.next_iteration()

        assert fresh_budget.level == BudgetLevel.CRITICAL

    def test_budget_hint_generation(self, fresh_budget):
        """Budget sollte Hints bei niedrigem Level generieren."""
        # Bei NORMAL: kein Hint
        assert fresh_budget.get_budget_hint() == ""

        # Auf LOW bringen
        fresh_budget.current_iteration = 22
        hint = fresh_budget.get_budget_hint()
        assert "combined_search" in hint

        # Auf CRITICAL bringen
        fresh_budget.current_iteration = 27
        hint = fresh_budget.get_budget_hint()
        assert "KRITISCH" in hint
        assert "batch_read_files" in hint

    def test_optimization_suggestions(self, fresh_budget):
        """Budget sollte Optimierungsvorschlaege generieren."""
        # Viele einzelne Suchen
        for _ in range(4):
            fresh_budget.record_tool_call("search_code")
        for _ in range(3):
            fresh_budget.record_tool_call("search_handbook")

        suggestions = fresh_budget.get_optimization_suggestions()
        assert any("combined_search" in s for s in suggestions)

    def test_efficiency_score(self, fresh_budget):
        """Effizienz-Score sollte Nutzungsverhalten reflektieren."""
        # Gute Nutzung: Meta-Tools + Cache
        fresh_budget.record_tool_call("combined_search", cached=False)
        fresh_budget.record_tool_call("combined_search", cached=True)
        fresh_budget.record_tool_call("batch_read_files", cached=False)

        score = fresh_budget.get_efficiency_score()
        assert score >= 70  # Guter Score

        # Schlechte Nutzung simulieren
        bad_budget = create_budget()
        for _ in range(10):
            bad_budget.record_tool_call("search_code", cached=False)
        for _ in range(10):
            bad_budget.record_tool_call("search_handbook", cached=False)

        bad_score = bad_budget.get_efficiency_score()
        assert bad_score < score  # Schlechterer Score


# ══════════════════════════════════════════════════════════════════════════════
# Full Pipeline Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFullPipelineIntegration:
    """End-to-End Tests der gesamten Optimierungs-Pipeline."""

    @pytest.mark.asyncio
    async def test_meta_tool_with_cache_and_budget(
        self, fresh_cache, fresh_budget, mock_search_code, mock_search_handbook
    ):
        """Test der kompletten Pipeline: Meta-Tool -> Cache -> Budget."""
        with patch("app.agent.tools.search_code", mock_search_code), \
             patch("app.agent.tools.search_handbook", mock_search_handbook):

            # 1. combined_search ausfuehren
            result = await combined_search(
                query="getUserById",
                sources="code,handbook"
            )

            # 2. Im Cache speichern
            fresh_cache.set("combined_search", {
                "query": "getUserById",
                "sources": "code,handbook"
            }, result)

            # 3. Im Budget tracken
            fresh_budget.record_tool_call("combined_search", duration_ms=150, cached=False)

            # Verifizieren
            assert result.success
            assert fresh_cache.get("combined_search", {
                "query": "getUserById",
                "sources": "code,handbook"
            }) is not None
            assert fresh_budget.total_tools_used == 1

            # 4. Cache-Hit simulieren
            cached = fresh_cache.get("combined_search", {
                "query": "getUserById",
                "sources": "code,handbook"
            })
            fresh_budget.record_tool_call("combined_search", duration_ms=1, cached=True)

            assert fresh_budget.cache_hits == 1
            assert fresh_budget.total_tools_used == 2

    @pytest.mark.asyncio
    async def test_efficiency_improvement_scenario(
        self, fresh_budget, mock_search_code, mock_search_handbook, mock_read_file
    ):
        """Simuliert ein Szenario wo Optimierung hilft."""
        # SCHLECHT: Einzelne Tools
        bad_budget = create_budget()

        # Simuliere unoptimierte Nutzung
        for _ in range(5):
            bad_budget.record_tool_call("search_code", cached=False)
        for _ in range(3):
            bad_budget.record_tool_call("search_handbook", cached=False)
        for _ in range(4):
            bad_budget.record_tool_call("read_file", cached=False)

        bad_score = bad_budget.get_efficiency_score()
        bad_total = bad_budget.total_tools_used

        # GUT: Meta-Tools + Cache
        good_budget = create_budget()

        # Simuliere optimierte Nutzung
        good_budget.record_tool_call("combined_search", cached=False)  # Ersetzt 8 Suchen
        good_budget.record_tool_call("batch_read_files", cached=False)  # Ersetzt 4 reads
        good_budget.record_tool_call("combined_search", cached=True)   # Cache-Hit

        good_score = good_budget.get_efficiency_score()
        good_total = good_budget.total_tools_used

        # Vergleich
        assert good_total < bad_total  # Weniger Tool-Aufrufe
        assert good_score > bad_score  # Besserer Effizienz-Score

        # Konkret: 12 -> 3 Tools = 75% Reduktion
        reduction = 1 - (good_total / bad_total)
        assert reduction >= 0.70


# ══════════════════════════════════════════════════════════════════════════════
# LLM Mock Simulation Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMMockSimulation:
    """Simuliert LLM-Verhalten mit Tool-Aufrufen."""

    @pytest.fixture
    def mock_llm_responses(self):
        """Simulierte LLM-Antworten mit Tool-Calls."""
        return [
            # Runde 1: LLM entscheidet sich fuer combined_search
            {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "combined_search",
                        "arguments": '{"query": "getUserById", "sources": "code,handbook"}'
                    }
                }]
            },
            # Runde 2: LLM liest gefundene Dateien
            {
                "content": "",
                "tool_calls": [{
                    "id": "call_2",
                    "function": {
                        "name": "batch_read_files",
                        "arguments": '{"paths": "UserService.java, UserRepository.java"}'
                    }
                }]
            },
            # Runde 3: LLM antwortet
            {
                "content": "Basierend auf meiner Analyse...",
                "tool_calls": []
            }
        ]

    @pytest.mark.asyncio
    async def test_simulated_llm_workflow(
        self,
        mock_llm_responses,
        fresh_budget,
        mock_search_code,
        mock_search_handbook,
        mock_read_file
    ):
        """Simuliert einen kompletten LLM-Workflow mit Tool-Optimization."""
        with patch("app.agent.tools.search_code", mock_search_code), \
             patch("app.agent.tools.search_handbook", mock_search_handbook), \
             patch("app.agent.tools.read_file", mock_read_file):

            # Simuliere 3 Runden
            for i, response in enumerate(mock_llm_responses):
                fresh_budget.next_iteration()

                for tc in response.get("tool_calls", []):
                    tool_name = tc["function"]["name"]
                    import json
                    args = json.loads(tc["function"]["arguments"])

                    # Tool ausfuehren (simuliert)
                    if tool_name == "combined_search":
                        result = await combined_search(**args)
                    elif tool_name == "batch_read_files":
                        result = await batch_read_files(**args)

                    # Budget tracken
                    fresh_budget.record_tool_call(tool_name, duration_ms=100)

            # Verifizieren
            assert fresh_budget.current_iteration == 3
            assert fresh_budget.total_tools_used == 2  # combined_search + batch_read
            assert fresh_budget.level == BudgetLevel.NORMAL

            # Effizienz pruefen
            top_tools = fresh_budget.get_top_tools(2)
            tool_names = [t[0] for t in top_tools]
            assert "combined_search" in tool_names
            assert "batch_read_files" in tool_names

    @pytest.mark.asyncio
    async def test_budget_warning_injected(self, fresh_budget):
        """Budget-Warnung sollte bei niedrigem Budget injiziert werden."""
        # Simuliere viele Iterationen
        for i in range(25):
            fresh_budget.next_iteration()
            fresh_budget.record_tool_call("search_code")

        # Budget sollte LOW sein
        assert fresh_budget.level == BudgetLevel.LOW

        # Hint sollte generiert werden
        hint = fresh_budget.get_budget_hint()
        assert len(hint) > 0
        assert "Verbleibend" in hint

        # Bei CRITICAL noch ausfuehrlicher
        for i in range(3):
            fresh_budget.next_iteration()

        assert fresh_budget.level == BudgetLevel.CRITICAL
        critical_hint = fresh_budget.get_budget_hint()
        assert "KRITISCH" in critical_hint
        assert "combined_search" in critical_hint
