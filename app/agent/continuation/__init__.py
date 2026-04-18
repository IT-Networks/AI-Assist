"""
Task Continuation Module - Agentic Loop with Completion Detection.

Provides opt-in continuation loop that wraps AgentOrchestrator.process()
and automatically continues iterations until task completion is detected
via Promise Tag (Tier 1) or timeout/max-iterations (Tier 3).

Phase 1 (MVP): Tier 1 + Tier 3 detection only.
Phase 2+: Parallel tool execution (separate module).
Phase 3: Tier 2 criteria matching + TaskClassifier.
Phase 4: DriftMonitor.
Phase 5: Sub-Agent Teams.
"""

from app.agent.continuation.models import (
    CompletionReason,
    CompletionResult,
    ContinuationConfig,
    DriftAssessment,
    DriftRiskLevel,
    IterationState,
    TaskType,
)
from app.agent.continuation.completion_detector import CompletionDetector
from app.agent.continuation.controller import ContinuationController
from app.agent.continuation.drift_monitor import DriftMonitor, get_drift_monitor
from app.agent.continuation.task_classifier import TaskClassifier, classify_task

__all__ = [
    "CompletionReason",
    "CompletionResult",
    "ContinuationConfig",
    "DriftAssessment",
    "DriftRiskLevel",
    "IterationState",
    "TaskType",
    "CompletionDetector",
    "ContinuationController",
    "DriftMonitor",
    "get_drift_monitor",
    "TaskClassifier",
    "classify_task",
]
