"""
Token Tracker Service - Tracks LLM token usage per request, session, and model.

Features:
- Per-request token logging
- Aggregated usage statistics (hourly, daily, weekly, monthly)
- Budget tracking with alerts
- Cost estimation based on model pricing
- Export functionality (JSON/CSV)
"""

import logging
import sqlite3
import uuid

from app.utils.json_utils import json_loads, json_dumps
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Model Pricing (USD per 1M tokens)
# ═══════════════════════════════════════════════════════════════════════════════

MODEL_PRICING = {
    # Default/fallback pricing
    "default": {"input": 0.50, "output": 1.50},

    # Common models
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},

    # Local/Custom models (zero cost)
    "mistral": {"input": 0.00, "output": 0.00},
    "llama": {"input": 0.00, "output": 0.00},
    "local": {"input": 0.00, "output": 0.00},
}


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Column Constants (Performance: avoid SELECT *)
# ═══════════════════════════════════════════════════════════════════════════════

_TOKEN_USAGE_COLUMNS = """id, timestamp, session_id, user_id, request_type, model,
    input_tokens, output_tokens, total_tokens, cost_usd, tool_name, chain_id"""

_BUDGET_CONFIG_COLUMNS = "id, enabled, limit_tokens, limit_usd, alert_threshold, alert_email"

_BUDGET_ALERTS_COLUMNS = "id, timestamp, alert_type, current_usage, limit_value, message, acknowledged"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TokenUsage:
    """Single token usage record."""
    id: str
    timestamp: int  # Unix timestamp ms
    session_id: str
    user_id: str

    # Request details
    request_type: str  # chat, tool, enhancement, review
    model: str

    # Token counts
    input_tokens: int
    output_tokens: int
    total_tokens: int

    # Cost
    cost_usd: float

    # Context
    tool_name: Optional[str] = None
    chain_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenUsage":
        return cls(**data)


@dataclass
class TokenBreakdown:
    """Aggregated token breakdown."""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, usage: TokenUsage):
        self.requests += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.total_tokens += usage.total_tokens
        self.cost_usd += usage.cost_usd

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HourlyUsage:
    """Hourly aggregated usage with model breakdown."""
    hour: str  # ISO format: "2026-03-15T14:00"
    tokens: int = 0
    requests: int = 0
    by_model: Dict[str, int] = field(default_factory=dict)  # model -> tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hour": self.hour,
            "tokens": self.tokens,
            "requests": self.requests,
            "byModel": self.by_model
        }


@dataclass
class UsageSummary:
    """Complete usage summary for a period."""
    period: str  # day, week, month
    start_date: str
    end_date: str

    # Aggregated values
    total_requests: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    # Breakdowns
    by_model: Dict[str, TokenBreakdown] = field(default_factory=dict)
    by_request_type: Dict[str, TokenBreakdown] = field(default_factory=dict)
    by_hour: List[HourlyUsage] = field(default_factory=list)

    # Budget
    budget_limit: Optional[float] = None
    budget_used: float = 0.0
    budget_remaining: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period": self.period,
            "startDate": self.start_date,
            "endDate": self.end_date,
            "totalRequests": self.total_requests,
            "totalTokens": self.total_tokens,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "estimatedCostUsd": round(self.estimated_cost_usd, 4),
            "byModel": {k: v.to_dict() for k, v in self.by_model.items()},
            "byRequestType": {k: v.to_dict() for k, v in self.by_request_type.items()},
            "byHour": [h.to_dict() for h in self.by_hour],
            "budgetLimit": self.budget_limit,
            "budgetUsed": round(self.budget_used, 4),
            "budgetRemaining": round(self.budget_remaining, 4) if self.budget_limit else None,
        }


@dataclass
class BudgetConfig:
    """Budget configuration."""
    enabled: bool = False
    limit_tokens: Optional[int] = None  # Monthly token limit
    limit_usd: Optional[float] = None   # Monthly cost limit
    alert_threshold: float = 0.8        # Alert at 80% usage
    alert_email: Optional[str] = None


@dataclass
class BudgetAlert:
    """Budget alert record."""
    id: str
    timestamp: int
    alert_type: str  # threshold_reached, limit_exceeded
    current_usage: float
    limit: float
    message: str
    acknowledged: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Token Tracker Service
# ═══════════════════════════════════════════════════════════════════════════════

class TokenTracker:
    """
    Service for tracking and analyzing LLM token usage.

    Features:
    - Real-time token logging
    - Usage aggregation by period
    - Budget management
    - Cost estimation
    """

    def __init__(self, db_path: str = "./data/tokens.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._budget_config: Optional[BudgetConfig] = None

    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Token usage table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                user_id TEXT,
                request_type TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                tool_name TEXT,
                chain_id TEXT
            )
        """)

        # Indexes for efficient querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_timestamp ON token_usage(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_session ON token_usage(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_model ON token_usage(model)")

        # Budget config table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS budget_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 0,
                limit_tokens INTEGER,
                limit_usd REAL,
                alert_threshold REAL DEFAULT 0.8,
                alert_email TEXT
            )
        """)

        # Budget alerts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                current_usage REAL NOT NULL,
                limit_value REAL NOT NULL,
                message TEXT NOT NULL,
                acknowledged INTEGER DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost based on model pricing."""
        # Find matching pricing - check longer keys first for more specific matches
        pricing = MODEL_PRICING.get("default")
        model_lower = model.lower()

        # Sort keys by length (longest first) to match more specific models first
        # e.g., "gpt-4-turbo" should match before "gpt-4"
        sorted_keys = sorted(
            [k for k in MODEL_PRICING.keys() if k != "default"],
            key=len,
            reverse=True
        )

        for model_key in sorted_keys:
            if model_key in model_lower:
                pricing = MODEL_PRICING[model_key]
                break

        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    # ═══════════════════════════════════════════════════════════════════════════
    # Token Logging
    # ═══════════════════════════════════════════════════════════════════════════

    def log_usage(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        request_type: str = "chat",
        user_id: str = "default",
        tool_name: Optional[str] = None,
        chain_id: Optional[str] = None
    ) -> TokenUsage:
        """Log a token usage record."""
        usage = TokenUsage(
            id=str(uuid.uuid4()),
            timestamp=int(datetime.now().timestamp() * 1000),
            session_id=session_id,
            user_id=user_id,
            request_type=request_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=self._calculate_cost(model, input_tokens, output_tokens),
            tool_name=tool_name,
            chain_id=chain_id
        )

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO token_usage
            (id, timestamp, session_id, user_id, request_type, model,
             input_tokens, output_tokens, total_tokens, cost_usd, tool_name, chain_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            usage.id, usage.timestamp, usage.session_id, usage.user_id,
            usage.request_type, usage.model, usage.input_tokens, usage.output_tokens,
            usage.total_tokens, usage.cost_usd, usage.tool_name, usage.chain_id
        ))

        conn.commit()
        conn.close()

        # Check budget alerts
        self._check_budget_alerts()

        logger.debug(f"Logged token usage: {usage.total_tokens} tokens, ${usage.cost_usd:.4f}")
        return usage

    # ═══════════════════════════════════════════════════════════════════════════
    # Usage Queries
    # ═══════════════════════════════════════════════════════════════════════════

    def get_usage_summary(self, period: str = "day") -> UsageSummary:
        """Get usage summary for a period (day, week, month)."""
        now = datetime.now()

        if period == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif period == "week":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                end = start.replace(year=now.year + 1, month=1)
            else:
                end = start.replace(month=now.month + 1)
        else:
            raise ValueError(f"Invalid period: {period}")

        start_ts = int(start.timestamp() * 1000)
        end_ts = int(end.timestamp() * 1000)

        conn = self._get_conn()
        cursor = conn.cursor()

        # Get all usage records for period
        cursor.execute(f"""
            SELECT {_TOKEN_USAGE_COLUMNS} FROM token_usage
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp
        """, (start_ts, end_ts))

        rows = cursor.fetchall()
        conn.close()

        # Build summary
        summary = UsageSummary(
            period=period,
            start_date=start.isoformat(),
            end_date=end.isoformat()
        )

        hourly_map: Dict[str, HourlyUsage] = {}

        for row in rows:
            usage = TokenUsage(
                id=row[0], timestamp=row[1], session_id=row[2], user_id=row[3],
                request_type=row[4], model=row[5], input_tokens=row[6],
                output_tokens=row[7], total_tokens=row[8], cost_usd=row[9],
                tool_name=row[10], chain_id=row[11]
            )

            # Total aggregation
            summary.total_requests += 1
            summary.total_tokens += usage.total_tokens
            summary.input_tokens += usage.input_tokens
            summary.output_tokens += usage.output_tokens
            summary.estimated_cost_usd += usage.cost_usd

            # By model
            if usage.model not in summary.by_model:
                summary.by_model[usage.model] = TokenBreakdown()
            summary.by_model[usage.model].add(usage)

            # By request type
            if usage.request_type not in summary.by_request_type:
                summary.by_request_type[usage.request_type] = TokenBreakdown()
            summary.by_request_type[usage.request_type].add(usage)

            # By hour (with model breakdown)
            hour_dt = datetime.fromtimestamp(usage.timestamp / 1000)
            hour_key = hour_dt.strftime("%Y-%m-%dT%H:00")
            if hour_key not in hourly_map:
                hourly_map[hour_key] = HourlyUsage(hour=hour_key)
            hourly_map[hour_key].tokens += usage.total_tokens
            hourly_map[hour_key].requests += 1
            # Model breakdown per hour
            if usage.model not in hourly_map[hour_key].by_model:
                hourly_map[hour_key].by_model[usage.model] = 0
            hourly_map[hour_key].by_model[usage.model] += usage.total_tokens

        # Convert hourly map to sorted list
        summary.by_hour = sorted(hourly_map.values(), key=lambda h: h.hour)

        # Add budget info
        budget = self.get_budget_config()
        if budget and budget.enabled:
            summary.budget_limit = budget.limit_usd or (budget.limit_tokens * 0.000001 if budget.limit_tokens else None)
            summary.budget_used = summary.estimated_cost_usd
            if summary.budget_limit:
                summary.budget_remaining = max(0, summary.budget_limit - summary.budget_used)

        return summary

    def get_recent_usage(self, limit: int = 100) -> List[TokenUsage]:
        """Get recent usage records."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT {_TOKEN_USAGE_COLUMNS} FROM token_usage
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [
            TokenUsage(
                id=row[0], timestamp=row[1], session_id=row[2], user_id=row[3],
                request_type=row[4], model=row[5], input_tokens=row[6],
                output_tokens=row[7], total_tokens=row[8], cost_usd=row[9],
                tool_name=row[10], chain_id=row[11]
            )
            for row in rows
        ]

    def get_usage_by_session(self, session_id: str) -> List[TokenUsage]:
        """Get usage for a specific session."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT {_TOKEN_USAGE_COLUMNS} FROM token_usage
            WHERE session_id = ?
            ORDER BY timestamp
        """, (session_id,))

        rows = cursor.fetchall()
        conn.close()

        return [
            TokenUsage(
                id=row[0], timestamp=row[1], session_id=row[2], user_id=row[3],
                request_type=row[4], model=row[5], input_tokens=row[6],
                output_tokens=row[7], total_tokens=row[8], cost_usd=row[9],
                tool_name=row[10], chain_id=row[11]
            )
            for row in rows
        ]

    # ═══════════════════════════════════════════════════════════════════════════
    # Budget Management
    # ═══════════════════════════════════════════════════════════════════════════

    def get_budget_config(self) -> Optional[BudgetConfig]:
        """Get current budget configuration."""
        if self._budget_config:
            return self._budget_config

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"SELECT {_BUDGET_CONFIG_COLUMNS} FROM budget_config WHERE id = 1")
        row = cursor.fetchone()
        conn.close()

        if row:
            self._budget_config = BudgetConfig(
                enabled=bool(row[1]),
                limit_tokens=row[2],
                limit_usd=row[3],
                alert_threshold=row[4] or 0.8,
                alert_email=row[5]
            )
            return self._budget_config
        return None

    def set_budget_config(self, config: BudgetConfig) -> BudgetConfig:
        """Set budget configuration."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO budget_config
            (id, enabled, limit_tokens, limit_usd, alert_threshold, alert_email)
            VALUES (1, ?, ?, ?, ?, ?)
        """, (
            1 if config.enabled else 0,
            config.limit_tokens,
            config.limit_usd,
            config.alert_threshold,
            config.alert_email
        ))

        conn.commit()
        conn.close()

        self._budget_config = config
        return config

    def _check_budget_alerts(self):
        """Check if budget alerts should be triggered."""
        budget = self.get_budget_config()
        if not budget or not budget.enabled:
            return

        # Get current month usage
        summary = self.get_usage_summary("month")

        if budget.limit_usd:
            usage_ratio = summary.estimated_cost_usd / budget.limit_usd

            if usage_ratio >= 1.0:
                self._create_alert(
                    "limit_exceeded",
                    summary.estimated_cost_usd,
                    budget.limit_usd,
                    f"Monthly budget exceeded: ${summary.estimated_cost_usd:.2f} / ${budget.limit_usd:.2f}"
                )
            elif usage_ratio >= budget.alert_threshold:
                self._create_alert(
                    "threshold_reached",
                    summary.estimated_cost_usd,
                    budget.limit_usd,
                    f"Budget at {usage_ratio*100:.0f}%: ${summary.estimated_cost_usd:.2f} / ${budget.limit_usd:.2f}"
                )

    def _create_alert(self, alert_type: str, current: float, limit: float, message: str):
        """Create a budget alert if not already exists for today."""
        today = datetime.now().strftime("%Y-%m-%d")

        conn = self._get_conn()
        cursor = conn.cursor()

        # Check if alert already exists today
        cursor.execute("""
            SELECT id FROM budget_alerts
            WHERE alert_type = ? AND date(timestamp/1000, 'unixepoch') = ?
        """, (alert_type, today))

        if cursor.fetchone():
            conn.close()
            return  # Already alerted today

        alert = BudgetAlert(
            id=str(uuid.uuid4()),
            timestamp=int(datetime.now().timestamp() * 1000),
            alert_type=alert_type,
            current_usage=current,
            limit=limit,
            message=message
        )

        cursor.execute("""
            INSERT INTO budget_alerts
            (id, timestamp, alert_type, current_usage, limit_value, message, acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.id, alert.timestamp, alert.alert_type,
            alert.current_usage, alert.limit, alert.message, 0
        ))

        conn.commit()
        conn.close()

        logger.warning(f"Budget alert: {message}")

    def get_alerts(self, include_acknowledged: bool = False) -> List[BudgetAlert]:
        """Get budget alerts."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if include_acknowledged:
            cursor.execute(f"SELECT {_BUDGET_ALERTS_COLUMNS} FROM budget_alerts ORDER BY timestamp DESC")
        else:
            cursor.execute(f"SELECT {_BUDGET_ALERTS_COLUMNS} FROM budget_alerts WHERE acknowledged = 0 ORDER BY timestamp DESC")

        rows = cursor.fetchall()
        conn.close()

        return [
            BudgetAlert(
                id=row[0], timestamp=row[1], alert_type=row[2],
                current_usage=row[3], limit=row[4], message=row[5],
                acknowledged=bool(row[6])
            )
            for row in rows
        ]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge a budget alert."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE budget_alerts SET acknowledged = 1 WHERE id = ?
        """, (alert_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    # ═══════════════════════════════════════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════════════════════════════════════

    def export_usage(
        self,
        format: str = "json",
        period: str = "month",
        output_path: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Export usage data.

        Returns:
            Tuple of (content, filename)
        """
        summary = self.get_usage_summary(period)
        recent = self.get_recent_usage(limit=1000)

        now = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format == "json":
            data = {
                "exported_at": datetime.now().isoformat(),
                "summary": summary.to_dict(),
                "records": [u.to_dict() for u in recent]
            }
            content = json_dumps(data, indent=True)
            filename = f"token_usage_{now}.json"
        elif format == "csv":
            lines = ["id,timestamp,session_id,request_type,model,input_tokens,output_tokens,total_tokens,cost_usd"]
            for u in recent:
                lines.append(f"{u.id},{u.timestamp},{u.session_id},{u.request_type},{u.model},{u.input_tokens},{u.output_tokens},{u.total_tokens},{u.cost_usd:.6f}")
            content = "\n".join(lines)
            filename = f"token_usage_{now}.csv"
        else:
            raise ValueError(f"Unsupported format: {format}")

        if output_path:
            with open(output_path, "w") as f:
                f.write(content)

        return content, filename


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_token_tracker: Optional[TokenTracker] = None


def get_token_tracker() -> TokenTracker:
    """Get the singleton TokenTracker instance."""
    global _token_tracker
    if _token_tracker is None:
        _token_tracker = TokenTracker()
    return _token_tracker
