"""
Tests for Token Tracker Service and API.

Tests cover:
- Token usage logging
- Usage aggregation (day, week, month)
- Budget configuration and alerts
- Cost calculation
- Export functionality
- API endpoints
"""

import json
import os
import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.token_tracker import (
    TokenTracker,
    TokenUsage,
    TokenBreakdown,
    HourlyUsage,
    UsageSummary,
    BudgetConfig,
    BudgetAlert,
    MODEL_PRICING,
    get_token_tracker,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tokens.db")
        yield db_path


@pytest.fixture
def tracker(temp_db):
    """Create a TokenTracker with temporary database."""
    return TokenTracker(db_path=temp_db)


@pytest.fixture
def tracker_with_data(tracker):
    """Tracker with some test data."""
    # Log some usage records
    tracker.log_usage(
        session_id="session-1",
        model="gpt-4",
        input_tokens=1000,
        output_tokens=500,
        request_type="chat",
        user_id="user-1"
    )
    tracker.log_usage(
        session_id="session-1",
        model="gpt-4",
        input_tokens=2000,
        output_tokens=1000,
        request_type="tool",
        user_id="user-1",
        tool_name="search_code"
    )
    tracker.log_usage(
        session_id="session-2",
        model="claude-3-sonnet",
        input_tokens=500,
        output_tokens=250,
        request_type="chat",
        user_id="user-2"
    )
    return tracker


# ═══════════════════════════════════════════════════════════════════════════════
# Data Class Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenUsage:
    """Tests for TokenUsage dataclass."""

    def test_create_token_usage(self):
        """Test TokenUsage creation."""
        usage = TokenUsage(
            id="test-id",
            timestamp=1234567890000,
            session_id="session-1",
            user_id="user-1",
            request_type="chat",
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cost_usd=0.075
        )

        assert usage.id == "test-id"
        assert usage.session_id == "session-1"
        assert usage.total_tokens == 1500
        assert usage.cost_usd == 0.075

    def test_to_dict(self):
        """Test TokenUsage serialization."""
        usage = TokenUsage(
            id="test-id",
            timestamp=1234567890000,
            session_id="session-1",
            user_id="user-1",
            request_type="chat",
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cost_usd=0.075
        )

        data = usage.to_dict()
        assert data["id"] == "test-id"
        assert data["total_tokens"] == 1500

    def test_from_dict(self):
        """Test TokenUsage deserialization."""
        data = {
            "id": "test-id",
            "timestamp": 1234567890000,
            "session_id": "session-1",
            "user_id": "user-1",
            "request_type": "chat",
            "model": "gpt-4",
            "input_tokens": 1000,
            "output_tokens": 500,
            "total_tokens": 1500,
            "cost_usd": 0.075
        }

        usage = TokenUsage.from_dict(data)
        assert usage.id == "test-id"
        assert usage.total_tokens == 1500


class TestTokenBreakdown:
    """Tests for TokenBreakdown dataclass."""

    def test_add_usage(self):
        """Test adding usage to breakdown."""
        breakdown = TokenBreakdown()

        usage = TokenUsage(
            id="test-id",
            timestamp=1234567890000,
            session_id="session-1",
            user_id="user-1",
            request_type="chat",
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cost_usd=0.075
        )

        breakdown.add(usage)

        assert breakdown.requests == 1
        assert breakdown.input_tokens == 1000
        assert breakdown.output_tokens == 500
        assert breakdown.total_tokens == 1500
        assert breakdown.cost_usd == 0.075

    def test_add_multiple_usages(self):
        """Test adding multiple usages to breakdown."""
        breakdown = TokenBreakdown()

        for i in range(3):
            usage = TokenUsage(
                id=f"test-{i}",
                timestamp=1234567890000,
                session_id="session-1",
                user_id="user-1",
                request_type="chat",
                model="gpt-4",
                input_tokens=1000,
                output_tokens=500,
                total_tokens=1500,
                cost_usd=0.075
            )
            breakdown.add(usage)

        assert breakdown.requests == 3
        assert breakdown.total_tokens == 4500
        assert breakdown.cost_usd == pytest.approx(0.225, rel=1e-6)


class TestUsageSummary:
    """Tests for UsageSummary dataclass."""

    def test_to_dict(self):
        """Test UsageSummary serialization."""
        summary = UsageSummary(
            period="day",
            start_date="2026-03-15T00:00:00",
            end_date="2026-03-16T00:00:00",
            total_requests=10,
            total_tokens=15000,
            input_tokens=10000,
            output_tokens=5000,
            estimated_cost_usd=0.75,
            budget_limit=100.0,
            budget_used=0.75,
            budget_remaining=99.25
        )

        data = summary.to_dict()

        assert data["period"] == "day"
        assert data["totalRequests"] == 10
        assert data["totalTokens"] == 15000
        assert data["budgetRemaining"] == 99.25


# ═══════════════════════════════════════════════════════════════════════════════
# TokenTracker Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenTrackerInit:
    """Tests for TokenTracker initialization."""

    def test_creates_database(self, temp_db):
        """Test database is created on init."""
        tracker = TokenTracker(db_path=temp_db)
        assert Path(temp_db).exists()

    def test_creates_tables(self, tracker):
        """Test required tables are created."""
        conn = tracker._get_conn()
        cursor = conn.cursor()

        # Check token_usage table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
        )
        assert cursor.fetchone() is not None

        # Check budget_config table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_config'"
        )
        assert cursor.fetchone() is not None

        # Check budget_alerts table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_alerts'"
        )
        assert cursor.fetchone() is not None

        conn.close()


class TestTokenLogging:
    """Tests for token usage logging."""

    def test_log_usage(self, tracker):
        """Test logging token usage."""
        usage = tracker.log_usage(
            session_id="session-1",
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            request_type="chat",
            user_id="user-1"
        )

        assert usage.id is not None
        assert usage.session_id == "session-1"
        assert usage.model == "gpt-4"
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.total_tokens == 1500

    def test_log_usage_with_tool(self, tracker):
        """Test logging tool usage."""
        usage = tracker.log_usage(
            session_id="session-1",
            model="gpt-4",
            input_tokens=500,
            output_tokens=100,
            request_type="tool",
            tool_name="search_code",
            chain_id="chain-123"
        )

        assert usage.tool_name == "search_code"
        assert usage.chain_id == "chain-123"

    def test_log_usage_calculates_cost(self, tracker):
        """Test cost calculation during logging."""
        usage = tracker.log_usage(
            session_id="session-1",
            model="gpt-4",
            input_tokens=1_000_000,  # 1M input tokens
            output_tokens=500_000,   # 500K output tokens
        )

        # GPT-4: $30/1M input, $60/1M output
        expected_cost = (1_000_000 / 1_000_000 * 30.00) + (500_000 / 1_000_000 * 60.00)
        assert abs(usage.cost_usd - expected_cost) < 0.01

    def test_log_usage_local_model_zero_cost(self, tracker):
        """Test local models have zero cost."""
        usage = tracker.log_usage(
            session_id="session-1",
            model="mistral-7b-local",
            input_tokens=10000,
            output_tokens=5000,
        )

        assert usage.cost_usd == 0.0


class TestCostCalculation:
    """Tests for cost calculation."""

    def test_gpt4_pricing(self, tracker):
        """Test GPT-4 pricing."""
        cost = tracker._calculate_cost("gpt-4", 1_000_000, 1_000_000)
        # $30/1M input + $60/1M output
        assert cost == 90.0

    def test_gpt4_turbo_pricing(self, tracker):
        """Test GPT-4 Turbo pricing."""
        cost = tracker._calculate_cost("gpt-4-turbo", 1_000_000, 1_000_000)
        # $10/1M input + $30/1M output
        assert cost == 40.0

    def test_claude_sonnet_pricing(self, tracker):
        """Test Claude Sonnet pricing."""
        cost = tracker._calculate_cost("claude-3-sonnet", 1_000_000, 1_000_000)
        # $3/1M input + $15/1M output
        assert cost == 18.0

    def test_unknown_model_default_pricing(self, tracker):
        """Test unknown models use default pricing."""
        cost = tracker._calculate_cost("unknown-model", 1_000_000, 1_000_000)
        # Default: $0.50/1M input + $1.50/1M output
        assert cost == 2.0


class TestUsageQueries:
    """Tests for usage queries."""

    def test_get_usage_summary_day(self, tracker_with_data):
        """Test daily usage summary."""
        summary = tracker_with_data.get_usage_summary("day")

        assert summary.period == "day"
        assert summary.total_requests == 3
        assert summary.total_tokens > 0

    def test_get_usage_summary_week(self, tracker_with_data):
        """Test weekly usage summary."""
        summary = tracker_with_data.get_usage_summary("week")

        assert summary.period == "week"
        assert summary.total_requests >= 3

    def test_get_usage_summary_month(self, tracker_with_data):
        """Test monthly usage summary."""
        summary = tracker_with_data.get_usage_summary("month")

        assert summary.period == "month"
        assert summary.total_requests >= 3

    def test_summary_by_model(self, tracker_with_data):
        """Test summary breakdown by model."""
        summary = tracker_with_data.get_usage_summary("day")

        assert "gpt-4" in summary.by_model
        assert "claude-3-sonnet" in summary.by_model
        assert summary.by_model["gpt-4"].requests == 2

    def test_summary_by_request_type(self, tracker_with_data):
        """Test summary breakdown by request type."""
        summary = tracker_with_data.get_usage_summary("day")

        assert "chat" in summary.by_request_type
        assert "tool" in summary.by_request_type

    def test_get_recent_usage(self, tracker_with_data):
        """Test getting recent usage records."""
        recent = tracker_with_data.get_recent_usage(limit=10)

        assert len(recent) == 3
        # Should be ordered by timestamp descending
        assert recent[0].timestamp >= recent[-1].timestamp

    def test_get_usage_by_session(self, tracker_with_data):
        """Test getting usage for a session."""
        records = tracker_with_data.get_usage_by_session("session-1")

        assert len(records) == 2
        assert all(r.session_id == "session-1" for r in records)


class TestBudgetManagement:
    """Tests for budget management."""

    def test_set_budget_config(self, tracker):
        """Test setting budget config."""
        config = BudgetConfig(
            enabled=True,
            limit_usd=100.0,
            alert_threshold=0.8
        )

        result = tracker.set_budget_config(config)

        assert result.enabled == True
        assert result.limit_usd == 100.0
        assert result.alert_threshold == 0.8

    def test_get_budget_config(self, tracker):
        """Test getting budget config."""
        config = BudgetConfig(
            enabled=True,
            limit_usd=50.0,
            alert_threshold=0.9
        )
        tracker.set_budget_config(config)

        retrieved = tracker.get_budget_config()

        assert retrieved.enabled == True
        assert retrieved.limit_usd == 50.0
        assert retrieved.alert_threshold == 0.9

    def test_budget_in_summary(self, tracker_with_data):
        """Test budget info in usage summary."""
        config = BudgetConfig(
            enabled=True,
            limit_usd=100.0
        )
        tracker_with_data.set_budget_config(config)

        summary = tracker_with_data.get_usage_summary("month")

        assert summary.budget_limit == 100.0
        assert summary.budget_used > 0
        assert summary.budget_remaining < 100.0


class TestBudgetAlerts:
    """Tests for budget alerts."""

    def test_threshold_alert(self, tracker):
        """Test threshold alert creation."""
        config = BudgetConfig(
            enabled=True,
            limit_usd=0.001,  # Very low limit
            alert_threshold=0.5
        )
        tracker.set_budget_config(config)

        # Log usage that exceeds threshold
        tracker.log_usage(
            session_id="session-1",
            model="gpt-4",
            input_tokens=100000,  # Should trigger alert
            output_tokens=50000,
        )

        alerts = tracker.get_alerts()
        assert len(alerts) > 0

    def test_acknowledge_alert(self, tracker):
        """Test acknowledging an alert."""
        # Create an alert manually
        tracker._create_alert(
            "threshold_reached",
            80.0,
            100.0,
            "Test alert message"
        )

        alerts = tracker.get_alerts(include_acknowledged=False)
        assert len(alerts) == 1

        # Acknowledge the alert
        tracker.acknowledge_alert(alerts[0].id)

        # Should not appear in unacknowledged list
        unack_alerts = tracker.get_alerts(include_acknowledged=False)
        assert len(unack_alerts) == 0

        # Should appear when including acknowledged
        all_alerts = tracker.get_alerts(include_acknowledged=True)
        assert len(all_alerts) == 1
        assert all_alerts[0].acknowledged == True


class TestExport:
    """Tests for export functionality."""

    def test_export_json(self, tracker_with_data):
        """Test JSON export."""
        content, filename = tracker_with_data.export_usage(format="json", period="month")

        assert filename.endswith(".json")
        data = json.loads(content)
        assert "summary" in data
        assert "records" in data

    def test_export_csv(self, tracker_with_data):
        """Test CSV export."""
        content, filename = tracker_with_data.export_usage(format="csv", period="month")

        assert filename.endswith(".csv")
        lines = content.strip().split("\n")
        assert len(lines) > 1  # Header + data rows
        assert "id,timestamp,session_id" in lines[0]

    def test_export_invalid_format(self, tracker_with_data):
        """Test invalid format raises error."""
        with pytest.raises(ValueError):
            tracker_with_data.export_usage(format="xml", period="month")


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_token_tracker_singleton(self):
        """Test singleton returns same instance."""
        # Reset singleton
        import app.services.token_tracker as module
        module._token_tracker = None

        tracker1 = get_token_tracker()
        tracker2 = get_token_tracker()

        assert tracker1 is tracker2


# ═══════════════════════════════════════════════════════════════════════════════
# API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenAPI:
    """Tests for Token API endpoints."""

    @pytest.fixture
    def client(self, temp_db):
        """Create test client with temporary database."""
        # Mock the token tracker to use temp db
        import app.services.token_tracker as module
        original_tracker = module._token_tracker
        module._token_tracker = TokenTracker(db_path=temp_db)

        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        yield client

        # Restore original tracker
        module._token_tracker = original_tracker

    def test_get_usage_summary(self, client):
        """Test GET /api/tokens/usage endpoint."""
        response = client.get("/api/tokens/usage?period=day")

        assert response.status_code == 200
        data = response.json()
        assert "period" in data
        assert "totalTokens" in data

    def test_get_usage_summary_invalid_period(self, client):
        """Test invalid period parameter."""
        response = client.get("/api/tokens/usage?period=year")

        assert response.status_code == 422  # Validation error

    def test_log_usage(self, client):
        """Test POST /api/tokens/log endpoint."""
        response = client.post("/api/tokens/log", json={
            "sessionId": "test-session",
            "model": "gpt-4",
            "inputTokens": 1000,
            "outputTokens": 500,
            "requestType": "chat"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["sessionId"] == "test-session"
        assert data["totalTokens"] == 1500

    def test_get_recent_usage(self, client):
        """Test GET /api/tokens/recent endpoint."""
        # Log some usage first
        client.post("/api/tokens/log", json={
            "sessionId": "test-session",
            "model": "gpt-4",
            "inputTokens": 1000,
            "outputTokens": 500
        })

        response = client.get("/api/tokens/recent?limit=10")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_get_budget_config(self, client):
        """Test GET /api/tokens/budget endpoint."""
        response = client.get("/api/tokens/budget")

        assert response.status_code == 200
        # Initially null

    def test_set_budget_config(self, client):
        """Test PUT /api/tokens/budget endpoint."""
        response = client.put("/api/tokens/budget", json={
            "enabled": True,
            "limitUsd": 100.0,
            "alertThreshold": 0.8
        })

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] == True
        assert data["limitUsd"] == 100.0

    def test_export_json(self, client):
        """Test GET /api/tokens/export endpoint (JSON)."""
        response = client.get("/api/tokens/export?format=json&period=month")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

    def test_export_csv(self, client):
        """Test GET /api/tokens/export endpoint (CSV)."""
        response = client.get("/api/tokens/export?format=csv&period=month")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]

    def test_get_stats(self, client):
        """Test GET /api/tokens/stats endpoint."""
        response = client.get("/api/tokens/stats")

        assert response.status_code == 200
        data = response.json()
        assert "today" in data
        assert "thisWeek" in data
        assert "thisMonth" in data
        assert "averages" in data

    def test_get_breakdown_by_model(self, client):
        """Test GET /api/tokens/breakdown endpoint."""
        response = client.get("/api/tokens/breakdown?groupBy=model")

        assert response.status_code == 200
        data = response.json()
        assert "breakdown" in data
        assert "groupBy" in data
        assert data["groupBy"] == "model"

    def test_get_alerts(self, client):
        """Test GET /api/tokens/alerts endpoint."""
        response = client.get("/api/tokens/alerts")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Model Pricing Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelPricing:
    """Tests for model pricing data."""

    def test_default_pricing_exists(self):
        """Test default pricing is defined."""
        assert "default" in MODEL_PRICING
        assert "input" in MODEL_PRICING["default"]
        assert "output" in MODEL_PRICING["default"]

    def test_gpt4_pricing_exists(self):
        """Test GPT-4 pricing is defined."""
        assert "gpt-4" in MODEL_PRICING

    def test_claude_pricing_exists(self):
        """Test Claude pricing is defined."""
        assert "claude-3-opus" in MODEL_PRICING
        assert "claude-3-sonnet" in MODEL_PRICING
        assert "claude-3-haiku" in MODEL_PRICING

    def test_local_models_zero_cost(self):
        """Test local models have zero cost."""
        assert MODEL_PRICING["mistral"]["input"] == 0.0
        assert MODEL_PRICING["mistral"]["output"] == 0.0
        assert MODEL_PRICING["local"]["input"] == 0.0
        assert MODEL_PRICING["local"]["output"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_summary(self, tracker):
        """Test summary with no data."""
        summary = tracker.get_usage_summary("day")

        assert summary.total_requests == 0
        assert summary.total_tokens == 0
        assert summary.estimated_cost_usd == 0.0

    def test_zero_tokens(self, tracker):
        """Test logging zero tokens."""
        usage = tracker.log_usage(
            session_id="session-1",
            model="gpt-4",
            input_tokens=0,
            output_tokens=0,
        )

        assert usage.total_tokens == 0
        assert usage.cost_usd == 0.0

    def test_large_token_count(self, tracker):
        """Test logging large token counts."""
        usage = tracker.log_usage(
            session_id="session-1",
            model="gpt-4",
            input_tokens=10_000_000,  # 10M tokens
            output_tokens=5_000_000,
        )

        assert usage.total_tokens == 15_000_000
        assert usage.cost_usd > 0

    def test_invalid_period(self, tracker):
        """Test invalid period raises error."""
        with pytest.raises(ValueError):
            tracker.get_usage_summary("year")

    def test_concurrent_logging(self, tracker):
        """Test concurrent logging doesn't cause issues."""
        import concurrent.futures

        def log_usage(i):
            return tracker.log_usage(
                session_id=f"session-{i}",
                model="gpt-4",
                input_tokens=100 * i,
                output_tokens=50 * i,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(log_usage, i) for i in range(100)]
            results = [f.result() for f in futures]

        assert len(results) == 100
        assert all(r.id is not None for r in results)

        # Verify all records were logged
        recent = tracker.get_recent_usage(limit=200)
        assert len(recent) == 100
