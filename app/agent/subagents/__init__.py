"""
Sub-Agent Teams Module (Phase 5).

Lightweight sub-agent system for the continuation flow. When a user message
can be decomposed into independent subtasks, spawns parallel workers that
execute in isolated sessions, then aggregates their results.

Distinct from the existing `run_team`/`multi_agent` system which is designed
for heavyweight research/planning workflows. This module targets shorter
parallel subtask execution within continuation loops.

Key components:
- TaskDecomposer: Heuristically splits user message into SubAgentTasks
- SubAgentWorker: Executes one task via orchestrator.process()
- SubAgentCoordinator: Runs multiple workers concurrently with timeouts
- ResultAggregator: Merges worker results into cohesive final response
"""

from app.agent.subagents.aggregator import ResultAggregator
from app.agent.subagents.coordinator import SubAgentCoordinator
from app.agent.subagents.models import (
    SubAgentConfig,
    SubAgentResult,
    SubAgentStatus,
    SubAgentTask,
)
from app.agent.subagents.task_decomposer import (
    TaskDecomposer,
    decompose_task,
)
from app.agent.subagents.worker import SubAgentWorker

__all__ = [
    "ResultAggregator",
    "SubAgentConfig",
    "SubAgentCoordinator",
    "SubAgentResult",
    "SubAgentStatus",
    "SubAgentTask",
    "SubAgentWorker",
    "TaskDecomposer",
    "decompose_task",
]
