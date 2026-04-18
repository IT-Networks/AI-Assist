"""Data models for sub-agent teams."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SubAgentStatus(str, Enum):
    """Lifecycle status of a SubAgentTask."""

    PENDING = "pending"          # Not yet started
    RUNNING = "running"          # Worker is executing
    COMPLETED = "completed"      # Finished successfully
    FAILED = "failed"            # Error during execution
    TIMEOUT = "timeout"          # Exceeded worker_timeout_seconds
    CANCELLED = "cancelled"      # Coordinator cancelled this task


@dataclass
class SubAgentTask:
    """
    A single unit of work to be executed by a SubAgentWorker.

    Each task runs in its own isolated orchestrator session so that parallel
    workers don't pollute each other's context.
    """

    description: str
    task_id: str = field(default_factory=lambda: f"sat_{uuid.uuid4().hex[:8]}")
    parent_session_id: str = ""
    specialty: str = ""  # Optional hint about task focus (e.g. "search", "analysis")
    status: SubAgentStatus = SubAgentStatus.PENDING

    def worker_session_id(self) -> str:
        """Unique session_id for this task's isolated orchestrator context."""
        return f"{self.parent_session_id}::{self.task_id}"


@dataclass
class SubAgentResult:
    """Result of executing a single SubAgentTask."""

    task_id: str
    description: str
    status: SubAgentStatus
    response: str = ""
    error: Optional[str] = None
    tool_calls_count: int = 0
    elapsed_seconds: float = 0.0
    # Optional: captured events from the worker's orchestrator run
    event_count: int = 0

    @property
    def is_success(self) -> bool:
        return self.status == SubAgentStatus.COMPLETED

    def summary_line(self) -> str:
        """Short one-line summary for logging/aggregation."""
        if self.is_success:
            head = self.response[:80].replace("\n", " ")
            return f"✓ [{self.task_id}] {self.description[:50]}... → {head}"
        return f"✗ [{self.task_id}] {self.description[:50]}... → {self.status.value}: {self.error or 'no error'}"


class SubAgentConfig(BaseModel):
    """Per-request sub-agent configuration (part of ContinuationConfig)."""

    enabled: bool = Field(default=False, description="Opt-in: enable sub-agent decomposition for this request")
    max_workers: int = Field(default=3, ge=1, le=10, description="Max parallel workers per iteration")
    worker_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0, description="Timeout per worker")
    min_subtasks: int = Field(default=2, ge=2, le=10, description="Minimum subtasks needed to activate coordinator")
    aggregate_style: str = Field(
        default="structured",
        description="Aggregation style: 'structured' (sections) or 'narrative' (flowing text)",
    )


# Type alias for event callback — used by worker + coordinator to stream progress
CallbackFn = Any  # Callable[[str, Dict[str, Any]], Awaitable[None]] when asyncio
