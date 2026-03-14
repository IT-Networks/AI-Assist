"""
Performance Tracker - Erfasst detaillierte Performance-Metriken.

Trackt:
- LLM-Antwortzeiten separat von Tool-Zeiten
- Token-Verbrauch (Input/Output)
- Kosten-Schaetzung pro Modell
- Parallelitaet von Tool-Aufrufen
"""

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


# Token-Preise pro 1M Tokens (Stand 2024, USD)
MODEL_PRICING = {
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-3-5-haiku": {"input": 0.25, "output": 1.25},
    # Fallback
    "default": {"input": 3.00, "output": 15.00},
}


@dataclass
class LLMCall:
    """Einzelner LLM-Aufruf."""
    call_id: int
    timestamp: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    purpose: str = ""  # "planning" | "tool_selection" | "response"


@dataclass
class ToolTiming:
    """Timing fuer einen Tool-Aufruf."""
    tool_name: str
    start_ms: int
    end_ms: int
    duration_ms: int
    parallel_with: List[str] = field(default_factory=list)


@dataclass
class PerformanceMetrics:
    """Aggregierte Performance-Metriken fuer eine Chain."""
    chain_id: str
    timestamp: str

    # LLM-Metriken
    llm_calls: int = 0
    llm_total_latency_ms: int = 0
    llm_avg_latency_ms: int = 0

    # Token-Verbrauch
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    # Tool-Metriken
    tool_calls: int = 0
    tool_total_time_ms: int = 0
    slowest_tool: str = ""
    slowest_tool_ms: int = 0

    # Parallelitaet
    parallel_tool_calls: int = 0
    max_parallel_tools: int = 0

    # Effizienz
    total_duration_ms: int = 0
    llm_time_percent: float = 0.0
    tool_time_percent: float = 0.0

    # Details
    llm_call_details: List[Dict] = field(default_factory=list)
    tool_timing_details: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary."""
        return asdict(self)


class PerformanceTracker:
    """
    Trackt detaillierte Performance-Metriken waehrend einer Chain.

    Usage:
        tracker = PerformanceTracker("chain_123")

        # LLM-Call tracken
        tracker.start_llm_call("claude-3-5-sonnet", "tool_selection")
        # ... LLM macht etwas ...
        tracker.end_llm_call(input_tokens=500, output_tokens=200)

        # Tool tracken
        tracker.start_tool("search_code")
        # ... Tool laeuft ...
        tracker.end_tool("search_code")

        # Metriken abrufen
        metrics = tracker.get_metrics()
    """

    def __init__(self, chain_id: str):
        self.chain_id = chain_id
        self.start_time = time.time()

        # LLM-Tracking
        self._llm_calls: List[LLMCall] = []
        self._current_llm_start: Optional[float] = None
        self._current_llm_model: str = ""
        self._current_llm_purpose: str = ""
        self._llm_call_counter = 0

        # Tool-Tracking
        self._tool_timings: List[ToolTiming] = []
        self._active_tools: Dict[str, float] = {}  # tool_name -> start_time

    # ═══════════════════════════════════════════════════════════════════════════
    # LLM-Tracking
    # ═══════════════════════════════════════════════════════════════════════════

    def start_llm_call(self, model: str, purpose: str = "") -> None:
        """Startet Tracking eines LLM-Aufrufs."""
        self._current_llm_start = time.time()
        self._current_llm_model = model
        self._current_llm_purpose = purpose
        self._llm_call_counter += 1

    def end_llm_call(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: Optional[str] = None,
    ) -> None:
        """Beendet Tracking eines LLM-Aufrufs."""
        if self._current_llm_start is None:
            return

        latency_ms = int((time.time() - self._current_llm_start) * 1000)
        actual_model = model or self._current_llm_model

        # Kosten berechnen
        cost = self._calculate_cost(actual_model, input_tokens, output_tokens)

        call = LLMCall(
            call_id=self._llm_call_counter,
            timestamp=datetime.utcnow().isoformat(),
            model=actual_model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            purpose=self._current_llm_purpose,
        )

        self._llm_calls.append(call)
        self._current_llm_start = None

    def log_llm_call(
        self,
        model: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        purpose: str = "",
    ) -> None:
        """Loggt einen LLM-Call direkt (wenn Start/End nicht moeglich)."""
        self._llm_call_counter += 1
        cost = self._calculate_cost(model, input_tokens, output_tokens)

        call = LLMCall(
            call_id=self._llm_call_counter,
            timestamp=datetime.utcnow().isoformat(),
            model=model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            purpose=purpose,
        )

        self._llm_calls.append(call)

    # ═══════════════════════════════════════════════════════════════════════════
    # Tool-Tracking
    # ═══════════════════════════════════════════════════════════════════════════

    def start_tool(self, tool_name: str) -> None:
        """Startet Tracking eines Tool-Aufrufs."""
        self._active_tools[tool_name] = time.time()

    def end_tool(self, tool_name: str) -> None:
        """Beendet Tracking eines Tool-Aufrufs."""
        if tool_name not in self._active_tools:
            return

        start_time = self._active_tools.pop(tool_name)
        end_time = time.time()

        start_ms = int((start_time - self.start_time) * 1000)
        end_ms = int((end_time - self.start_time) * 1000)
        duration_ms = end_ms - start_ms

        # Parallel laufende Tools finden
        parallel_with = list(self._active_tools.keys())

        timing = ToolTiming(
            tool_name=tool_name,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=duration_ms,
            parallel_with=parallel_with,
        )

        self._tool_timings.append(timing)

    def log_tool(self, tool_name: str, duration_ms: int) -> None:
        """Loggt einen Tool-Aufruf direkt."""
        current_ms = int((time.time() - self.start_time) * 1000)

        timing = ToolTiming(
            tool_name=tool_name,
            start_ms=current_ms - duration_ms,
            end_ms=current_ms,
            duration_ms=duration_ms,
            parallel_with=[],
        )

        self._tool_timings.append(timing)

    # ═══════════════════════════════════════════════════════════════════════════
    # Metriken
    # ═══════════════════════════════════════════════════════════════════════════

    def get_metrics(self) -> PerformanceMetrics:
        """Berechnet aggregierte Metriken."""
        total_duration_ms = int((time.time() - self.start_time) * 1000)

        # LLM-Aggregation
        llm_total_latency = sum(c.latency_ms for c in self._llm_calls)
        llm_avg_latency = (
            llm_total_latency // len(self._llm_calls)
            if self._llm_calls else 0
        )
        input_tokens = sum(c.input_tokens for c in self._llm_calls)
        output_tokens = sum(c.output_tokens for c in self._llm_calls)
        total_cost = sum(c.estimated_cost_usd for c in self._llm_calls)

        # Tool-Aggregation
        tool_total_time = sum(t.duration_ms for t in self._tool_timings)
        slowest_tool = ""
        slowest_tool_ms = 0

        for t in self._tool_timings:
            if t.duration_ms > slowest_tool_ms:
                slowest_tool_ms = t.duration_ms
                slowest_tool = t.tool_name

        # Parallelitaet
        parallel_count = sum(
            1 for t in self._tool_timings if t.parallel_with
        )
        max_parallel = max(
            (len(t.parallel_with) + 1 for t in self._tool_timings),
            default=1
        )

        # Zeit-Verteilung
        llm_percent = (
            (llm_total_latency / total_duration_ms * 100)
            if total_duration_ms > 0 else 0
        )
        tool_percent = (
            (tool_total_time / total_duration_ms * 100)
            if total_duration_ms > 0 else 0
        )

        return PerformanceMetrics(
            chain_id=self.chain_id,
            timestamp=datetime.utcnow().isoformat(),

            # LLM
            llm_calls=len(self._llm_calls),
            llm_total_latency_ms=llm_total_latency,
            llm_avg_latency_ms=llm_avg_latency,

            # Tokens
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost_usd=round(total_cost, 6),

            # Tools
            tool_calls=len(self._tool_timings),
            tool_total_time_ms=tool_total_time,
            slowest_tool=slowest_tool,
            slowest_tool_ms=slowest_tool_ms,

            # Parallelitaet
            parallel_tool_calls=parallel_count,
            max_parallel_tools=max_parallel,

            # Effizienz
            total_duration_ms=total_duration_ms,
            llm_time_percent=round(llm_percent, 1),
            tool_time_percent=round(tool_percent, 1),

            # Details
            llm_call_details=[asdict(c) for c in self._llm_calls],
            tool_timing_details=[asdict(t) for t in self._tool_timings],
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Hilfsmethoden
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Berechnet geschaetzte Kosten in USD."""
        # Modell-Pricing finden
        pricing = MODEL_PRICING.get("default")

        for model_key, prices in MODEL_PRICING.items():
            if model_key in model.lower():
                pricing = prices
                break

        # Kosten berechnen (Preise sind pro 1M Tokens)
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost
