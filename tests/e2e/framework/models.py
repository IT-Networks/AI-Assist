"""
Pydantic models for E2E test framework.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProxyMetrics(BaseModel):
    """Metrics from LLM-Test-Proxy."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens: int = 0
    avg_latency_ms: float = 0.0
    requests_with_tools: int = 0


class ChatResponse(BaseModel):
    """Response from AI-Assist /api/agent/chat/sync."""
    session_id: str
    events: List[Dict[str, Any]] = Field(default_factory=list)
    response: str = ""
    final_response: str = ""
    pending_confirmation: Optional[Dict] = None


@dataclass
class TrackedToolCall:
    """A tracked tool call with metadata."""
    name: str
    arguments: Dict[str, Any]
    status: str  # success, error, pending
    result_preview: str = ""
    order: int = 0
    duration_ms: int = 0
    error_message: Optional[str] = None

    def __repr__(self) -> str:
        return f"ToolCall({self.order}: {self.name}({self.arguments}) -> {self.status})"


@dataclass
class VerificationResult:
    """Result of tool call verification."""
    passed: bool
    expected_tools: List[str]
    actual_tools: List[str]
    missing_tools: List[str] = field(default_factory=list)
    unexpected_tools: List[str] = field(default_factory=list)
    order_correct: bool = True
    errors: List[str] = field(default_factory=list)

    def __repr__(self) -> str:
        if self.passed:
            return f"VerificationResult(PASSED: {self.actual_tools})"
        return f"VerificationResult(FAILED: missing={self.missing_tools}, unexpected={self.unexpected_tools})"


@dataclass
class TestResult:
    """Result of a single test scenario."""
    name: str
    passed: bool
    prompt: str
    expected_tools: List[str]
    actual_tools: List[TrackedToolCall]
    response_preview: str = ""
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)
    proxy_metrics_before: Optional[ProxyMetrics] = None
    proxy_metrics_after: Optional[ProxyMetrics] = None
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def tool_names(self) -> List[str]:
        return [t.name for t in self.actual_tools]


@dataclass
class TestSuiteResult:
    """Result of a complete test suite."""
    name: str
    tests: List[TestResult]
    total_duration_ms: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.passed)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if not t.passed)

    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def success_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total > 0 else 0.0
