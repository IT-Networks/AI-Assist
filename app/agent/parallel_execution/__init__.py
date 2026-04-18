"""
Parallel Execution Module - Smart Tool Batching.

Groups consecutive parallelizable tool calls into parallel execution batches
while preserving the original order for non-parallelizable (write/side-effect)
tools. Works on top of the existing tool_executor.execute_tools_parallel().

Phase 2 of the agentic system upgrade.

Example:
    Input:  [search_code, read_file, write_file, grep_content]
    Plan:   Group 1 (parallel):   [search_code, read_file]
            Group 2 (sequential): [write_file]
            Group 3 (parallel):   [grep_content]
    Runtime: max(T_search, T_read) + T_write + T_grep
             instead of T_search + T_read + T_write + T_grep
"""

from app.agent.parallel_execution.models import (
    ExecutionGroup,
    ExecutionPlan,
    GroupKind,
)
from app.agent.parallel_execution.planner import (
    ToolBatchPlanner,
    build_execution_plan,
)

__all__ = [
    "ExecutionGroup",
    "ExecutionPlan",
    "GroupKind",
    "ToolBatchPlanner",
    "build_execution_plan",
]
