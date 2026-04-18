"""
Unit tests for Phase 2 Parallel Execution planner.

Tests:
- ExecutionGroup & ExecutionPlan models
- ToolBatchPlanner with various tool combinations
- Integration with real is_parallelizable_tool() predicate
"""

import pytest

from app.agent.orchestration.types import ToolCall
from app.agent.parallel_execution.models import (
    ExecutionGroup,
    ExecutionPlan,
    GroupKind,
)
from app.agent.parallel_execution.planner import (
    ToolBatchPlanner,
    build_execution_plan,
    get_planner,
)


# ═════════════════════════════════════════════════════════════════════════════
# Test Helpers
# ═════════════════════════════════════════════════════════════════════════════


def _tc(name: str, **kwargs) -> ToolCall:
    """Build a ToolCall quickly for tests."""
    return ToolCall(id=f"id_{name}", name=name, arguments=kwargs or {})


def _fake_is_parallelizable(tool_name: str) -> bool:
    """Predictable predicate for isolated planner tests."""
    # Parallelizable prefixes (read-only)
    parallel_prefixes = ("search_", "read_", "get_", "list_", "grep_")
    # Explicit sequential
    sequential = {"write_file", "edit_file", "execute_command"}

    if tool_name in sequential:
        return False
    return any(tool_name.startswith(p) for p in parallel_prefixes)


@pytest.fixture
def planner():
    return ToolBatchPlanner(is_parallelizable=_fake_is_parallelizable)


# ═════════════════════════════════════════════════════════════════════════════
# Model Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestExecutionGroup:
    def test_empty_group(self):
        g = ExecutionGroup(kind=GroupKind.PARALLEL)
        assert g.size == 0
        assert g.tool_names == []
        assert g.is_parallel is False  # empty can't be parallel

    def test_singleton_parallel_not_actually_parallel(self):
        g = ExecutionGroup(kind=GroupKind.PARALLEL, tool_calls=[_tc("search_code")])
        assert g.size == 1
        # is_parallel requires size > 1 to benefit from parallelism
        assert g.is_parallel is False

    def test_multi_tool_parallel_group(self):
        g = ExecutionGroup(
            kind=GroupKind.PARALLEL,
            tool_calls=[_tc("search_code"), _tc("read_file")],
        )
        assert g.size == 2
        assert g.is_parallel is True
        assert g.tool_names == ["search_code", "read_file"]

    def test_sequential_group_never_parallel(self):
        g = ExecutionGroup(
            kind=GroupKind.SEQUENTIAL,
            tool_calls=[_tc("write_file"), _tc("write_file")],
        )
        assert g.is_parallel is False


class TestExecutionPlan:
    def test_empty_plan(self):
        p = ExecutionPlan(groups=[])
        assert p.total_tools == 0
        assert p.group_count == 0
        assert p.parallel_groups == 0
        assert p.has_parallel_savings is False
        assert p.all_parallel is False
        assert p.all_sequential is True

    def test_single_parallel_group(self):
        p = ExecutionPlan(
            groups=[
                ExecutionGroup(
                    kind=GroupKind.PARALLEL,
                    tool_calls=[_tc("search_code"), _tc("read_file"), _tc("grep_content")],
                )
            ]
        )
        assert p.total_tools == 3
        assert p.group_count == 1
        assert p.parallel_groups == 1
        assert p.has_parallel_savings is True
        assert p.all_parallel is True
        assert p.all_sequential is False

    def test_only_sequential_groups(self):
        p = ExecutionPlan(
            groups=[
                ExecutionGroup(kind=GroupKind.SEQUENTIAL, tool_calls=[_tc("write_file")]),
                ExecutionGroup(kind=GroupKind.SEQUENTIAL, tool_calls=[_tc("edit_file")]),
            ]
        )
        assert p.total_tools == 2
        assert p.group_count == 2
        assert p.parallel_groups == 0
        assert p.has_parallel_savings is False
        assert p.all_parallel is False
        assert p.all_sequential is True

    def test_mixed_plan_with_savings(self):
        p = ExecutionPlan(
            groups=[
                ExecutionGroup(
                    kind=GroupKind.PARALLEL,
                    tool_calls=[_tc("search_code"), _tc("read_file")],
                ),
                ExecutionGroup(kind=GroupKind.SEQUENTIAL, tool_calls=[_tc("write_file")]),
            ]
        )
        assert p.total_tools == 3
        assert p.group_count == 2
        assert p.parallel_groups == 1
        assert p.has_parallel_savings is True
        assert p.all_parallel is False  # has sequential group
        assert p.all_sequential is False

    def test_plan_summary(self):
        p = ExecutionPlan(
            groups=[
                ExecutionGroup(
                    kind=GroupKind.PARALLEL,
                    tool_calls=[_tc("search_code"), _tc("read_file")],
                ),
                ExecutionGroup(kind=GroupKind.SEQUENTIAL, tool_calls=[_tc("write_file")]),
            ]
        )
        summary = p.summary()
        assert "||" in summary  # parallel marker
        assert "→" in summary  # sequential marker
        assert "search_code" in summary
        assert "write_file" in summary


# ═════════════════════════════════════════════════════════════════════════════
# Planner Tests — isolated via fake predicate
# ═════════════════════════════════════════════════════════════════════════════


class TestToolBatchPlanner:
    def test_empty_input(self, planner):
        plan = planner.analyze([])
        assert plan.groups == []
        assert plan.total_tools == 0

    def test_single_parallelizable_tool(self, planner):
        plan = planner.analyze([_tc("search_code")])
        assert plan.group_count == 1
        assert plan.groups[0].kind == GroupKind.PARALLEL
        assert plan.groups[0].size == 1
        # Singleton group — no parallel savings
        assert plan.has_parallel_savings is False
        assert plan.all_parallel is False  # requires size > 1

    def test_single_non_parallelizable_tool(self, planner):
        plan = planner.analyze([_tc("write_file")])
        assert plan.group_count == 1
        assert plan.groups[0].kind == GroupKind.SEQUENTIAL
        assert plan.has_parallel_savings is False

    def test_two_parallelizable_tools(self, planner):
        plan = planner.analyze([_tc("search_code"), _tc("read_file")])
        assert plan.group_count == 1
        assert plan.groups[0].kind == GroupKind.PARALLEL
        assert plan.groups[0].size == 2
        assert plan.has_parallel_savings is True
        assert plan.all_parallel is True

    def test_three_parallelizable_tools(self, planner):
        plan = planner.analyze([_tc("search_code"), _tc("read_file"), _tc("grep_content")])
        assert plan.group_count == 1
        assert plan.groups[0].size == 3
        assert plan.all_parallel is True

    def test_parallel_then_sequential(self, planner):
        """Two reads followed by a write → 2 groups."""
        plan = planner.analyze([
            _tc("search_code"),
            _tc("read_file"),
            _tc("write_file"),
        ])
        assert plan.group_count == 2
        assert plan.groups[0].kind == GroupKind.PARALLEL
        assert plan.groups[0].size == 2
        assert plan.groups[1].kind == GroupKind.SEQUENTIAL
        assert plan.groups[1].tool_names == ["write_file"]
        assert plan.has_parallel_savings is True
        assert plan.all_parallel is False

    def test_sequential_then_parallel(self, planner):
        """Write first, then two reads → 2 groups."""
        plan = planner.analyze([
            _tc("write_file"),
            _tc("search_code"),
            _tc("read_file"),
        ])
        assert plan.group_count == 2
        assert plan.groups[0].kind == GroupKind.SEQUENTIAL
        assert plan.groups[0].tool_names == ["write_file"]
        assert plan.groups[1].kind == GroupKind.PARALLEL
        assert plan.groups[1].tool_names == ["search_code", "read_file"]
        assert plan.has_parallel_savings is True

    def test_alternating_parallel_sequential(self, planner):
        """read, write, read, write → 4 groups (no batching possible)."""
        plan = planner.analyze([
            _tc("read_file"),
            _tc("write_file"),
            _tc("search_code"),
            _tc("edit_file"),
        ])
        assert plan.group_count == 4
        # Each group is a singleton
        for g in plan.groups:
            assert g.size == 1
        # No parallel savings — every parallel group is a singleton
        assert plan.has_parallel_savings is False

    def test_write_sandwiched_between_reads(self, planner):
        """read, read, write, read, read → 3 groups."""
        plan = planner.analyze([
            _tc("search_code"),
            _tc("read_file"),
            _tc("write_file"),
            _tc("grep_content"),
            _tc("list_files"),
        ])
        assert plan.group_count == 3
        assert plan.groups[0].kind == GroupKind.PARALLEL
        assert plan.groups[0].tool_names == ["search_code", "read_file"]
        assert plan.groups[1].kind == GroupKind.SEQUENTIAL
        assert plan.groups[1].tool_names == ["write_file"]
        assert plan.groups[2].kind == GroupKind.PARALLEL
        assert plan.groups[2].tool_names == ["grep_content", "list_files"]
        assert plan.parallel_groups == 2
        assert plan.has_parallel_savings is True

    def test_order_preserved(self, planner):
        """Order of tool calls must be preserved across grouping."""
        input_order = [
            _tc("read_file", path="a"),
            _tc("read_file", path="b"),
            _tc("write_file", path="c"),
            _tc("search_code", query="x"),
        ]
        plan = planner.analyze(input_order)
        # Flatten plan back to tool calls and verify order
        flat = [tc for g in plan.groups for tc in g.tool_calls]
        assert [tc.arguments.get("path") or tc.arguments.get("query") for tc in flat] == ["a", "b", "c", "x"]


# ═════════════════════════════════════════════════════════════════════════════
# Integration with real is_parallelizable_tool()
# ═════════════════════════════════════════════════════════════════════════════


class TestRealPredicateIntegration:
    """Tests using the actual orchestrator's parallelizability rules."""

    def test_default_planner_uses_real_predicate(self):
        """Default planner should use the real is_parallelizable_tool()."""
        plan = build_execution_plan([
            _tc("search_code"),
            _tc("read_file"),
        ])
        assert plan.all_parallel is True

    def test_write_file_is_sequential(self):
        """write_file should always be sequential per real predicate."""
        plan = build_execution_plan([_tc("write_file")])
        assert plan.groups[0].kind == GroupKind.SEQUENTIAL

    def test_search_code_is_parallel(self):
        """search_code should be parallelizable."""
        plan = build_execution_plan([_tc("search_code"), _tc("search_confluence")])
        assert plan.all_parallel is True
        assert plan.groups[0].size == 2

    def test_execute_command_is_sequential(self):
        """execute_command has side effects and must be sequential."""
        plan = build_execution_plan([
            _tc("read_file"),
            _tc("execute_command"),
            _tc("read_file"),
        ])
        assert plan.group_count == 3  # Broken into 3 singletons
        assert plan.groups[1].kind == GroupKind.SEQUENTIAL
        assert plan.groups[1].tool_names == ["execute_command"]

    def test_mcp_tools_are_sequential(self):
        """Tools prefixed with mcp_ must be sequential per tool_executor rules."""
        plan = build_execution_plan([
            _tc("mcp_some_capability"),
            _tc("read_file"),
        ])
        # mcp_* is sequential, read_file is parallel → 2 groups
        assert plan.group_count == 2
        assert plan.groups[0].kind == GroupKind.SEQUENTIAL
        assert plan.groups[0].tool_names == ["mcp_some_capability"]

    def test_singleton_plannner_is_shared(self):
        """get_planner() should return the same instance across calls."""
        p1 = get_planner()
        p2 = get_planner()
        assert p1 is p2


# ═════════════════════════════════════════════════════════════════════════════
# Config Integration
# ═════════════════════════════════════════════════════════════════════════════


class TestConfigIntegration:
    def test_settings_have_parallel_execution(self):
        from app.core.config import ParallelExecutionSettings, settings

        assert hasattr(settings, "parallel_execution")
        assert isinstance(settings.parallel_execution, ParallelExecutionSettings)
        # Enabled by default (safe — falls back to legacy when no savings)
        assert settings.parallel_execution.enabled is True
        assert settings.parallel_execution.min_parallel_group_size == 2
        assert settings.parallel_execution.log_plans is False


# ═════════════════════════════════════════════════════════════════════════════
# Orchestrator Integration Smoke Test
# ═════════════════════════════════════════════════════════════════════════════


class TestOrchestratorIntegration:
    def test_orchestrator_imports_planner(self):
        """Orchestrator module should import the planner without errors."""
        from app.agent import orchestrator as orch_module

        # Verify the new imports exist
        assert hasattr(orch_module, "build_execution_plan")
        assert hasattr(orch_module, "ExecutionPlan")

    def test_all_parallel_plan_matches_legacy_check(self):
        """
        The new all_parallelizable logic (plan.all_parallel) must match
        legacy logic (all(is_parallelizable) AND len >= 2) for full parallel
        inputs — ensures Phase 2 integration is backward-compatible.
        """
        from app.agent.orchestration.tool_executor import is_parallelizable_tool

        test_cases = [
            # (tool_calls, expected_all_parallelizable)
            ([_tc("search_code"), _tc("read_file")], True),
            ([_tc("search_code")], False),  # singleton: both systems say False
            ([_tc("search_code"), _tc("write_file")], False),
            ([_tc("write_file"), _tc("edit_file")], False),
            ([], False),
        ]

        for tool_calls, expected in test_cases:
            plan = build_execution_plan(tool_calls)
            new_result = plan.all_parallel and plan.total_tools >= 2

            legacy_result = (
                len(tool_calls) >= 2
                and all(is_parallelizable_tool(tc.name) for tc in tool_calls)
            )
            assert new_result == legacy_result, (
                f"Mismatch for {[tc.name for tc in tool_calls]}: "
                f"plan={new_result} legacy={legacy_result}"
            )
            # Also validate expected
            assert new_result == expected, f"Expected {expected} for {[tc.name for tc in tool_calls]}"
