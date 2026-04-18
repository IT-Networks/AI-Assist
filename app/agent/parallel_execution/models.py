"""Data models for parallel tool execution planning."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.orchestration.types import ToolCall


class GroupKind(str, Enum):
    """Execution mode for a group of tool calls."""

    PARALLEL = "parallel"      # Tools in group run concurrently via asyncio.gather
    SEQUENTIAL = "sequential"  # Tools in group must run one after another


@dataclass
class ExecutionGroup:
    """
    A group of tool calls that can be executed together.

    PARALLEL groups contain only parallelizable tools (read-only, no side effects).
    SEQUENTIAL groups may contain a single write/MCP/streaming tool that must
    complete before the next group starts.
    """

    kind: GroupKind
    tool_calls: List["ToolCall"] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_names(self) -> List[str]:
        return [tc.name for tc in self.tool_calls]

    @property
    def is_parallel(self) -> bool:
        return self.kind == GroupKind.PARALLEL and self.size > 1

    def __repr__(self) -> str:
        kind_label = self.kind.value
        return f"ExecutionGroup({kind_label}, {self.tool_names})"


@dataclass
class ExecutionPlan:
    """
    Ordered sequence of ExecutionGroups representing how to run a batch of
    tool calls optimally.

    Groups are processed in order; within a PARALLEL group, tools run
    concurrently; within a SEQUENTIAL group (always size 1 for Phase 2),
    tools run one at a time.
    """

    groups: List[ExecutionGroup] = field(default_factory=list)

    @property
    def total_tools(self) -> int:
        return sum(g.size for g in self.groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def parallel_groups(self) -> int:
        """Number of groups that have real parallelism (size > 1)."""
        return sum(1 for g in self.groups if g.is_parallel)

    @property
    def has_parallel_savings(self) -> bool:
        """
        True if the plan offers time savings vs pure sequential execution.

        A plan has savings if any group contains 2+ parallelizable tools.
        Fully sequential plans (all singletons) have no savings.
        """
        return any(g.is_parallel for g in self.groups)

    @property
    def all_parallel(self) -> bool:
        """True if the plan is one single parallel group (fast path)."""
        return self.group_count == 1 and self.groups[0].is_parallel

    @property
    def all_sequential(self) -> bool:
        """True if every group is sequential/singleton (no parallelism benefit)."""
        return not self.has_parallel_savings

    def summary(self) -> str:
        """Human-readable summary for logging."""
        parts = []
        for i, g in enumerate(self.groups):
            label = "||" if g.is_parallel else "→"
            parts.append(f"G{i+1}[{label}]({','.join(g.tool_names)})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return f"ExecutionPlan(groups={self.group_count}, tools={self.total_tools}, savings={self.has_parallel_savings})"
