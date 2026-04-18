"""
Tool Batch Planner - Analyzes tool calls and builds optimal execution plan.

Phase 2 (MVP): Order-preserving greedy batching.
- Consecutive parallelizable tools → one PARALLEL group
- Non-parallelizable tool → breaks the current group into a SEQUENTIAL singleton
- Order is preserved (no reordering) — LLM may have intentional ordering

Does NOT attempt semantic dependency analysis (e.g. "tool B uses output of A").
Relies on the existing is_parallelizable_tool() classification which is
based on read-only vs. write/MCP/streaming categorization.
"""

import logging
from typing import Callable, List, Optional, TYPE_CHECKING

from app.agent.parallel_execution.models import (
    ExecutionGroup,
    ExecutionPlan,
    GroupKind,
)

if TYPE_CHECKING:
    from app.agent.orchestration.types import ToolCall

logger = logging.getLogger(__name__)


# Default parallelizability predicate — uses existing tool_executor logic.
# Injected as callable to avoid hard dependency and enable testing with fakes.
def _default_is_parallelizable(tool_name: str) -> bool:
    from app.agent.orchestration.tool_executor import is_parallelizable_tool

    return is_parallelizable_tool(tool_name)


class ToolBatchPlanner:
    """
    Analyzes a list of tool calls and groups them for optimal execution.

    Strategy (Phase 2):
    - Walk tool_calls in order
    - Collect consecutive parallelizable tools into a PARALLEL group
    - A non-parallelizable tool flushes the current group (if any) and forms
      its own SEQUENTIAL singleton group
    - Order preserved — no reordering

    Thread-safe: Pure function over inputs, no mutable state.
    """

    def __init__(self, is_parallelizable: Optional[Callable[[str], bool]] = None) -> None:
        self._is_parallelizable = is_parallelizable or _default_is_parallelizable

    def analyze(self, tool_calls: List["ToolCall"]) -> ExecutionPlan:
        """
        Build an ExecutionPlan from a list of tool calls.

        Args:
            tool_calls: List of ToolCall objects (order matters)

        Returns:
            ExecutionPlan with groups preserving original order
        """
        if not tool_calls:
            return ExecutionPlan(groups=[])

        groups: List[ExecutionGroup] = []
        current_parallel_batch: List["ToolCall"] = []

        for tc in tool_calls:
            if self._is_parallelizable(tc.name):
                current_parallel_batch.append(tc)
            else:
                # Flush any accumulated parallelizable batch first
                if current_parallel_batch:
                    groups.append(
                        ExecutionGroup(
                            kind=GroupKind.PARALLEL,
                            tool_calls=list(current_parallel_batch),
                        )
                    )
                    current_parallel_batch = []
                # Add the non-parallelizable tool as its own sequential group
                groups.append(
                    ExecutionGroup(kind=GroupKind.SEQUENTIAL, tool_calls=[tc])
                )

        # Flush remaining parallelizable batch at end
        if current_parallel_batch:
            groups.append(
                ExecutionGroup(
                    kind=GroupKind.PARALLEL,
                    tool_calls=list(current_parallel_batch),
                )
            )

        plan = ExecutionPlan(groups=groups)
        logger.debug(f"[planner] Built plan: {plan.summary()}")
        return plan


_singleton: Optional[ToolBatchPlanner] = None


def get_planner() -> ToolBatchPlanner:
    """Get singleton ToolBatchPlanner instance (uses default parallelizability check)."""
    global _singleton
    if _singleton is None:
        _singleton = ToolBatchPlanner()
    return _singleton


def build_execution_plan(tool_calls: List["ToolCall"]) -> ExecutionPlan:
    """
    Convenience function: Build ExecutionPlan using default planner.

    Args:
        tool_calls: Tool calls to plan

    Returns:
        ExecutionPlan
    """
    return get_planner().analyze(tool_calls)
