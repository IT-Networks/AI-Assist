"""
Tests fuer Tool Budget Manager.

Testet Budget-Tracking, Warnungen und Optimierungsvorschlaege.
"""

import pytest

from app.agent.tool_budget import (
    ToolBudget,
    BudgetLevel,
    ToolUsageStats,
    create_budget,
)


class TestToolBudget:
    """Tests fuer ToolBudget Klasse."""

    def test_init_with_defaults(self):
        """Budget sollte mit Defaults initialisiert werden."""
        budget = ToolBudget()
        assert budget.max_iterations == 30
        assert budget.max_tools_per_iteration == 10
        assert budget.current_iteration == 0
        assert budget.total_tools_used == 0
        assert budget.level == BudgetLevel.NORMAL

    def test_init_with_custom_values(self):
        """Budget sollte benutzerdefinierte Werte akzeptieren."""
        budget = ToolBudget(max_iterations=10, max_tools_per_iteration=5)
        assert budget.max_iterations == 10
        assert budget.max_tools_per_iteration == 5

    def test_remaining_iterations(self):
        """remaining_iterations sollte korrekt berechnet werden."""
        budget = ToolBudget(max_iterations=30)
        assert budget.remaining_iterations == 30

        budget.current_iteration = 10
        assert budget.remaining_iterations == 20

        budget.current_iteration = 35  # Ueber Limit
        assert budget.remaining_iterations == 0

    def test_level_normal(self):
        """Level sollte NORMAL bei genug Budget sein."""
        budget = ToolBudget(max_iterations=30)
        budget.current_iteration = 5
        assert budget.level == BudgetLevel.NORMAL

    def test_level_low(self):
        """Level sollte LOW bei wenig Budget sein."""
        budget = ToolBudget(max_iterations=30, low_threshold=10)
        budget.current_iteration = 21  # 9 verbleibend
        assert budget.level == BudgetLevel.LOW

    def test_level_critical(self):
        """Level sollte CRITICAL bei fast keinem Budget sein."""
        budget = ToolBudget(max_iterations=30, critical_threshold=5)
        budget.current_iteration = 26  # 4 verbleibend
        assert budget.level == BudgetLevel.CRITICAL

    def test_is_exhausted(self):
        """is_exhausted sollte True bei 0 Iterationen sein."""
        budget = ToolBudget(max_iterations=10)
        assert budget.is_exhausted is False

        budget.current_iteration = 10
        assert budget.is_exhausted is True

    def test_record_tool_call(self):
        """Tool-Call sollte korrekt aufgezeichnet werden."""
        budget = ToolBudget()

        budget.record_tool_call("search_code", duration_ms=100, cached=False)
        budget.record_tool_call("search_code", duration_ms=50, cached=True)

        assert budget.total_tools_used == 2
        assert budget.tools_this_iteration == 2
        assert len(budget.tool_history) == 2
        assert budget.cache_hits == 1
        assert budget.cache_misses == 1

        # Stats pruefen
        stats = budget.tool_stats["search_code"]
        assert stats.call_count == 2
        assert stats.total_duration_ms == 150
        assert stats.cache_hits == 1

    def test_next_iteration(self):
        """next_iteration sollte Iteration erhoehen und reset."""
        budget = ToolBudget()

        budget.record_tool_call("search_code")
        budget.record_tool_call("read_file")
        assert budget.tools_this_iteration == 2

        budget.next_iteration()

        assert budget.current_iteration == 1
        assert budget.tools_this_iteration == 0
        assert budget.total_tools_used == 2  # Bleibt erhalten
        assert len(budget.iteration_history) == 1
        assert budget.iteration_history[0] == 2

    def test_get_top_tools(self):
        """get_top_tools sollte haeufigste Tools zurueckgeben."""
        budget = ToolBudget()

        for _ in range(5):
            budget.record_tool_call("search_code")
        for _ in range(3):
            budget.record_tool_call("read_file")
        budget.record_tool_call("web_search")

        top = budget.get_top_tools(2)
        assert len(top) == 2
        assert top[0] == ("search_code", 5)
        assert top[1] == ("read_file", 3)

    def test_get_recent_tools(self):
        """get_recent_tools sollte letzte Tools zurueckgeben."""
        budget = ToolBudget()

        budget.record_tool_call("tool1")
        budget.record_tool_call("tool2")
        budget.record_tool_call("tool3")

        recent = budget.get_recent_tools(2)
        assert recent == ["tool2", "tool3"]


class TestBudgetHint:
    """Tests fuer Budget-Hinweise."""

    def test_no_hint_at_normal_level(self):
        """Kein Hint bei NORMAL-Level."""
        budget = ToolBudget(max_iterations=30)
        budget.current_iteration = 5
        assert budget.get_budget_hint() == ""

    def test_hint_at_low_level(self):
        """Hint bei LOW-Level."""
        budget = ToolBudget(max_iterations=30, low_threshold=10)
        budget.current_iteration = 21

        hint = budget.get_budget_hint()
        assert "Tool-Budget" in hint
        assert "Verbleibend" in hint
        assert "combined_search" in hint

    def test_hint_at_critical_level(self):
        """Ausfuehrlicher Hint bei CRITICAL-Level."""
        budget = ToolBudget(max_iterations=30, critical_threshold=5)
        budget.current_iteration = 26

        hint = budget.get_budget_hint()
        assert "KRITISCH" in hint
        assert "WICHTIG" in hint
        assert "combined_search" in hint
        assert "batch_read_files" in hint


class TestOptimizationSuggestions:
    """Tests fuer Optimierungsvorschlaege."""

    def test_no_suggestions_for_good_usage(self):
        """Keine Vorschlaege bei gutem Nutzungsverhalten."""
        budget = ToolBudget()
        budget.record_tool_call("combined_search")
        budget.record_tool_call("read_file")

        suggestions = budget.get_optimization_suggestions()
        assert len(suggestions) == 0

    def test_suggest_combined_search(self):
        """Vorschlag fuer combined_search bei vielen Suchen."""
        budget = ToolBudget()

        for _ in range(3):
            budget.record_tool_call("search_code")
        for _ in range(2):
            budget.record_tool_call("search_handbook")

        suggestions = budget.get_optimization_suggestions()
        assert len(suggestions) >= 1
        assert any("combined_search" in s for s in suggestions)

    def test_suggest_batch_read(self):
        """Vorschlag fuer batch_read_files bei vielen read_file."""
        budget = ToolBudget()

        for _ in range(5):
            budget.record_tool_call("read_file")

        suggestions = budget.get_optimization_suggestions()
        assert any("batch_read_files" in s for s in suggestions)

    def test_suggest_read_files_parameter(self):
        """Vorschlag fuer read_files=True bei search+read Kombination."""
        budget = ToolBudget()

        for _ in range(3):
            budget.record_tool_call("search_code")
        for _ in range(3):
            budget.record_tool_call("read_file")

        suggestions = budget.get_optimization_suggestions()
        assert any("read_files=True" in s for s in suggestions)


class TestEfficiencyScore:
    """Tests fuer Effizienz-Score."""

    def test_perfect_score(self):
        """Perfekte Nutzung sollte hohen Score haben."""
        budget = ToolBudget()
        budget.record_tool_call("combined_search", cached=True)
        budget.record_tool_call("batch_read_files", cached=True)

        score = budget.get_efficiency_score()
        assert score >= 80

    def test_low_cache_rate_reduces_score(self):
        """Niedrige Cache-Rate sollte Score reduzieren."""
        budget = ToolBudget()

        for _ in range(10):
            budget.record_tool_call("search_code", cached=False)

        score = budget.get_efficiency_score()
        # Score sollte unter 80 sein wegen niedriger Cache-Rate
        assert score < 80

    def test_many_searches_reduces_score(self):
        """Viele Suchen ohne Meta-Tools sollte Score reduzieren."""
        budget = ToolBudget()

        for _ in range(10):
            budget.record_tool_call("search_code")
        for _ in range(5):
            budget.record_tool_call("search_handbook")

        score = budget.get_efficiency_score()
        assert score < 70


class TestToolUsageStats:
    """Tests fuer ToolUsageStats."""

    def test_avg_duration(self):
        """avg_duration_ms sollte korrekt berechnet werden."""
        stats = ToolUsageStats(
            tool_name="search_code",
            call_count=4,
            total_duration_ms=400.0
        )
        assert stats.avg_duration_ms == 100.0

    def test_avg_duration_zero_calls(self):
        """avg_duration_ms sollte 0 bei keine Aufrufen sein."""
        stats = ToolUsageStats(tool_name="test")
        assert stats.avg_duration_ms == 0.0


class TestCreateBudget:
    """Tests fuer Factory-Funktion."""

    def test_create_budget_default(self):
        """create_budget sollte konfiguriertes Budget erstellen."""
        budget = create_budget()
        assert isinstance(budget, ToolBudget)
        assert budget.max_iterations == 30

    def test_create_budget_custom(self):
        """create_budget sollte benutzerdefinierte Werte akzeptieren."""
        budget = create_budget(
            max_iterations=50,
            max_tools_per_iteration=15,
            low_threshold=15,
            critical_threshold=7
        )
        assert budget.max_iterations == 50
        assert budget.max_tools_per_iteration == 15
        assert budget.low_threshold == 15
        assert budget.critical_threshold == 7


class TestSummaryAndDict:
    """Tests fuer Summary und Serialisierung."""

    def test_get_summary(self):
        """get_summary sollte lesbaren String zurueckgeben."""
        budget = ToolBudget()
        budget.record_tool_call("search_code", duration_ms=100, cached=True)
        budget.record_tool_call("read_file", duration_ms=50, cached=False)

        summary = budget.get_summary()
        assert "Tool-Budget" in summary
        assert "Iterationen" in summary
        assert "Effizienz" in summary

    def test_to_dict(self):
        """to_dict sollte alle wichtigen Felder enthalten."""
        budget = ToolBudget()
        budget.current_iteration = 5
        budget.record_tool_call("search_code", cached=True)

        d = budget.to_dict()
        assert d["level"] == "normal"
        assert d["current_iteration"] == 5
        assert d["remaining_iterations"] == 25
        assert d["cache_hits"] == 1
        assert "efficiency_score" in d
