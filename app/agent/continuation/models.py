"""Data models for task continuation system."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class CompletionReason(str, Enum):
    """Why the continuation loop terminated."""

    PROMISE_TAG = "promise_tag_detected"
    CRITERIA_MATCH = "criteria_match"
    MAX_ITERATIONS = "max_iterations_reached"
    TIMEOUT = "execution_timeout"
    DRIFT_STOP = "drift_detected_stopped"
    USER_INTERRUPT = "user_interrupted"
    ERROR = "execution_error"


class DriftRiskLevel(str, Enum):
    """Drift risk categorization."""

    LOW = "low"          # All metrics healthy, continue normally
    MEDIUM = "medium"    # Some concerning signals, warn but continue
    HIGH = "high"        # Strong drift signals, recommend stop


class TaskType(str, Enum):
    """Classified task type for criteria-based completion detection."""

    SEARCH = "search"
    READ = "read"
    ANALYSIS = "analysis"
    OPTIMIZATION = "optimization"
    GENERATION = "generation"
    GENERIC = "generic"


@dataclass
class CompletionResult:
    """Result of completion check after an iteration."""

    is_complete: bool
    reason: Optional[CompletionReason] = None
    evidence: str = ""
    confidence: float = 0.0
    tier: int = 0

    @classmethod
    def not_complete(cls) -> "CompletionResult":
        return cls(is_complete=False, reason=None, evidence="no completion signals", confidence=0.0, tier=0)

    @classmethod
    def promise_tag(cls, evidence: str) -> "CompletionResult":
        return cls(
            is_complete=True,
            reason=CompletionReason.PROMISE_TAG,
            evidence=evidence,
            confidence=1.0,
            tier=1,
        )

    @classmethod
    def criteria_match(cls, evidence: str) -> "CompletionResult":
        return cls(
            is_complete=True,
            reason=CompletionReason.CRITERIA_MATCH,
            evidence=evidence,
            confidence=0.7,
            tier=2,
        )

    @classmethod
    def max_iterations(cls, iteration: int) -> "CompletionResult":
        return cls(
            is_complete=True,
            reason=CompletionReason.MAX_ITERATIONS,
            evidence=f"reached max iterations: {iteration}",
            confidence=0.3,
            tier=3,
        )

    @classmethod
    def timeout(cls, elapsed: float) -> "CompletionResult":
        return cls(
            is_complete=True,
            reason=CompletionReason.TIMEOUT,
            evidence=f"execution timeout: {elapsed:.1f}s",
            confidence=0.3,
            tier=3,
        )


@dataclass
class IterationState:
    """Transient state tracked across continuation iterations."""

    session_id: str
    original_goal: str
    task_type: TaskType = TaskType.GENERIC
    iteration: int = 0
    start_time: float = field(default_factory=time.time)
    responses: List[str] = field(default_factory=list)
    tool_calls_count: int = 0
    tool_call_signatures: List[str] = field(default_factory=list)  # Phase 4: for redundancy detection
    failed_tool_calls: int = 0  # Phase 4: for efficiency scoring
    tokens_used: int = 0
    termination_reason: Optional[CompletionReason] = None
    final_response: Optional[str] = None
    # Phase 4: Drift tracking
    drift_warnings: int = 0
    last_drift_assessment: Optional["DriftAssessment"] = None

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    def add_response(self, response: str) -> None:
        self.responses.append(response)

    def last_response(self) -> str:
        return self.responses[-1] if self.responses else ""

    def record_tool_call(self, tool_name: str, arguments: dict, success: bool = True) -> None:
        """Track tool call for efficiency/redundancy analysis (Phase 4)."""
        self.tool_calls_count += 1
        # Build a signature: tool_name + hash of relevant args (path/query)
        signature_parts = [tool_name]
        for key in ("path", "query", "file_path", "pattern"):
            if key in arguments:
                signature_parts.append(f"{key}={arguments[key]}")
        signature = "|".join(signature_parts)
        self.tool_call_signatures.append(signature)
        if not success:
            self.failed_tool_calls += 1


@dataclass
class DriftAssessment:
    """Drift evaluation result after an iteration."""

    risk_level: DriftRiskLevel
    goal_alignment: float        # 0.0 - 1.0 (higher = better aligned)
    tool_efficiency: float       # 0.0 - 1.0 (higher = more efficient)
    token_burn_rate: float       # 1.0 = normal, higher = anomaly
    context_coherence: float     # 0.0 - 1.0 (higher = more coherent)
    recommendation: str          # "continue" | "warn" | "stop"
    reasons: List[str] = field(default_factory=list)

    @classmethod
    def healthy(cls) -> "DriftAssessment":
        """Default assessment when no iteration history yet."""
        return cls(
            risk_level=DriftRiskLevel.LOW,
            goal_alignment=1.0,
            tool_efficiency=1.0,
            token_burn_rate=1.0,
            context_coherence=1.0,
            recommendation="continue",
            reasons=[],
        )

    def to_dict(self) -> dict:
        return {
            "risk_level": self.risk_level.value,
            "goal_alignment": round(self.goal_alignment, 3),
            "tool_efficiency": round(self.tool_efficiency, 3),
            "token_burn_rate": round(self.token_burn_rate, 3),
            "context_coherence": round(self.context_coherence, 3),
            "recommendation": self.recommendation,
            "reasons": self.reasons,
        }


class ContinuationConfig(BaseModel):
    """Configuration for continuation loop - sent in API request."""

    enabled: bool = Field(default=False, description="Opt-in: False = existing behavior, True = continuation loop")
    max_iterations: int = Field(default=10, ge=1, le=50, description="Hard iteration limit")
    max_seconds: float = Field(default=120.0, ge=5.0, le=600.0, description="Hard time limit in seconds")
    iteration_delay_ms: int = Field(default=0, ge=0, le=5000, description="Delay between iterations")
    require_promise_tag: bool = Field(
        default=False,
        description="If True, only Promise Tag (Tier 1) stops loop; ignore Tier 2 criteria matching",
    )
    # Phase 4: Drift Monitoring
    enable_drift_monitoring: bool = Field(
        default=True,
        description="Monitor agent behavior for drift (goal alignment, tool efficiency)",
    )
    stop_on_high_drift: bool = Field(
        default=False,
        description="Stop loop if drift risk_level=HIGH (default: warn-only, don't stop)",
    )
    # Phase 5: Sub-Agent Teams — nested Any-typed to avoid circular dependency
    # (SubAgentConfig lives in app.agent.subagents.models)
    subagents: Optional[dict] = Field(
        default=None,
        description=(
            "Optional sub-agent config: {enabled: bool, max_workers: int, "
            "worker_timeout_seconds: float, min_subtasks: int, aggregate_style: str}. "
            "If enabled and user message is decomposable, spawns parallel workers."
        ),
    )
