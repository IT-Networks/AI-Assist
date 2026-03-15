"""
Tests fuer das Dashboard API.

Testet:
- GET /api/analytics/dashboard - Dashboard-Metriken
- Response-Modelle und Felder
- Time Range Parameter Validierung
"""

import os
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_analytics_logger():
    """Erstellt gemockten AnalyticsLogger."""
    mock_logger = MagicMock()
    mock_logger.enabled = True
    mock_logger.get_summary = AsyncMock(return_value={
        "total_chains": 100,
        "tools_used": {
            "read_file": 50,
            "edit_file": 30,
            "search_code": 20,
        },
        "tool_success_rate": {
            "read_file": 0.98,
            "edit_file": 0.95,
            "search_code": 0.90,
        },
        "error_types": {
            "FileNotFoundError": 5,
            "ValidationError": 3,
        },
    })
    return mock_logger


@pytest.fixture
def client(mock_analytics_logger):
    """Erstellt Test-Client mit gemocktem AnalyticsLogger."""
    from main import app
    from app.api.routes import analytics

    with patch.object(analytics, 'get_analytics_logger', return_value=mock_analytics_logger):
        with TestClient(app) as client:
            yield client, mock_analytics_logger


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Endpoint Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardEndpoint:
    """Tests fuer GET /api/analytics/dashboard."""

    def test_dashboard_default_timerange(self, client):
        """Dashboard mit Default-Zeitraum 'week'."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()
        assert data["timeRange"] == "week"

    def test_dashboard_day_timerange(self, client):
        """Dashboard mit Zeitraum 'day'."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard?timeRange=day")

        assert response.status_code == 200
        data = response.json()
        assert data["timeRange"] == "day"

    def test_dashboard_week_timerange(self, client):
        """Dashboard mit Zeitraum 'week'."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard?timeRange=week")

        assert response.status_code == 200
        data = response.json()
        assert data["timeRange"] == "week"

    def test_dashboard_month_timerange(self, client):
        """Dashboard mit Zeitraum 'month'."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard?timeRange=month")

        assert response.status_code == 200
        data = response.json()
        assert data["timeRange"] == "month"

    def test_dashboard_invalid_timerange(self, client):
        """Ungueltiger Zeitraum wird abgelehnt."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard?timeRange=year")

        assert response.status_code == 400
        assert "timeRange" in response.json()["detail"]

    def test_dashboard_invalid_timerange_empty(self, client):
        """Leerer Zeitraum wird abgelehnt."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard?timeRange=")

        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Response Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardResponse:
    """Tests fuer Dashboard-Response Struktur."""

    def test_response_has_all_kpis(self, client):
        """Response enthaelt alle KPI-Felder."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        # KPIs
        assert "totalRequests" in data
        assert "requestsTrend" in data
        assert "avgResponseTime" in data
        assert "responseTrend" in data
        assert "successRate" in data
        assert "successTrend" in data

    def test_response_has_tool_usage(self, client):
        """Response enthaelt Tool-Usage Liste."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert "toolUsage" in data
        assert isinstance(data["toolUsage"], list)

        # Verify tool usage entry structure
        if data["toolUsage"]:
            tool_entry = data["toolUsage"][0]
            assert "tool" in tool_entry
            assert "count" in tool_entry
            assert "successRate" in tool_entry
            assert "avgDuration" in tool_entry

    def test_response_has_activity_heatmap(self, client):
        """Response enthaelt Activity-Heatmap."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert "activityHeatmap" in data
        assert isinstance(data["activityHeatmap"], list)

        # Verify heatmap entry structure
        if data["activityHeatmap"]:
            entry = data["activityHeatmap"][0]
            assert "date" in entry
            assert "hour" in entry
            assert "count" in entry

    def test_response_has_recent_errors(self, client):
        """Response enthaelt Recent-Errors Liste."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert "recentErrors" in data
        assert isinstance(data["recentErrors"], list)

        # Verify error entry structure
        if data["recentErrors"]:
            error_entry = data["recentErrors"][0]
            assert "timestamp" in error_entry
            assert "tool" in error_entry
            assert "errorType" in error_entry
            assert "message" in error_entry
            assert "count" in error_entry

    def test_response_has_token_usage(self, client):
        """Response enthaelt Token-Usage."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert "tokenUsage" in data
        token_usage = data["tokenUsage"]
        assert "input" in token_usage
        assert "output" in token_usage
        assert "total" in token_usage
        assert "limit" in token_usage


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard KPI Values Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardKPIs:
    """Tests fuer Dashboard-KPI Werte."""

    def test_total_requests(self, client):
        """Total Requests wird korrekt berechnet."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        # From mock: total_chains = 100
        assert data["totalRequests"] == 100

    def test_success_rate_range(self, client):
        """Success Rate ist im gueltigen Bereich."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert 0 <= data["successRate"] <= 100

    def test_trends_are_numbers(self, client):
        """Trend-Werte sind numerisch."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data["requestsTrend"], (int, float))
        assert isinstance(data["responseTrend"], (int, float))
        assert isinstance(data["successTrend"], (int, float))


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Disabled Analytics Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardDisabledAnalytics:
    """Tests wenn Analytics deaktiviert ist."""

    def test_disabled_returns_empty_dashboard(self):
        """Deaktivierte Analytics gibt leeres Dashboard zurueck."""
        from main import app
        from app.api.routes import analytics

        mock_logger = MagicMock()
        mock_logger.enabled = False

        with patch.object(analytics, 'get_analytics_logger', return_value=mock_logger):
            with TestClient(app) as test_client:
                response = test_client.get("/api/analytics/dashboard")

                assert response.status_code == 200
                data = response.json()

                assert data["totalRequests"] == 0
                assert data["successRate"] == 0.0
                assert data["toolUsage"] == []
                assert data["activityHeatmap"] == []
                assert data["recentErrors"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Tool Usage Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardToolUsage:
    """Tests fuer Tool-Usage Daten."""

    def test_tool_usage_sorted_by_count(self, client):
        """Tool-Usage ist nach Count sortiert."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        tool_usage = data["toolUsage"]
        if len(tool_usage) >= 2:
            counts = [t["count"] for t in tool_usage]
            assert counts == sorted(counts, reverse=True)

    def test_tool_usage_limited_to_10(self, client):
        """Tool-Usage ist auf 10 Eintraege begrenzt."""
        test_client, mock_logger = client

        # Mock with many tools
        mock_logger.get_summary = AsyncMock(return_value={
            "total_chains": 100,
            "tools_used": {f"tool_{i}": i + 1 for i in range(20)},
            "tool_success_rate": {f"tool_{i}": 0.9 for i in range(20)},
            "error_types": {},
        })

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert len(data["toolUsage"]) <= 10


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Activity Heatmap Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardActivityHeatmap:
    """Tests fuer Activity-Heatmap Daten."""

    def test_heatmap_has_valid_hours(self, client):
        """Heatmap-Stunden sind im gueltigen Bereich."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        for entry in data["activityHeatmap"]:
            assert 0 <= entry["hour"] <= 23

    def test_heatmap_has_valid_dates(self, client):
        """Heatmap-Daten haben gueltiges Datumsformat."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        for entry in data["activityHeatmap"]:
            # Should be YYYY-MM-DD format
            date = entry["date"]
            assert len(date) == 10
            assert date[4] == "-"
            assert date[7] == "-"

    def test_heatmap_counts_non_negative(self, client):
        """Heatmap-Counts sind nicht negativ."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        for entry in data["activityHeatmap"]:
            assert entry["count"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Token Usage Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardTokenUsage:
    """Tests fuer Token-Usage Daten."""

    def test_token_total_equals_input_plus_output(self, client):
        """Token-Total ist ungefaehr Input + Output."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        token_usage = data["tokenUsage"]
        # Allow small rounding differences
        expected_total = token_usage["input"] + token_usage["output"]
        assert abs(token_usage["total"] - expected_total) < 10

    def test_token_limit_set(self, client):
        """Token-Limit ist gesetzt."""
        test_client, mock_logger = client

        response = test_client.get("/api/analytics/dashboard")

        assert response.status_code == 200
        data = response.json()

        assert data["tokenUsage"]["limit"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Error Handling Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardErrorHandling:
    """Tests fuer Error-Handling."""

    def test_empty_tools_used(self):
        """Leere Tool-Usage wird behandelt."""
        from main import app
        from app.api.routes import analytics

        mock_logger = MagicMock()
        mock_logger.enabled = True
        mock_logger.get_summary = AsyncMock(return_value={
            "total_chains": 0,
            "tools_used": {},
            "tool_success_rate": {},
            "error_types": {},
        })

        with patch.object(analytics, 'get_analytics_logger', return_value=mock_logger):
            with TestClient(app) as test_client:
                response = test_client.get("/api/analytics/dashboard")

                assert response.status_code == 200
                data = response.json()

                assert data["toolUsage"] == []
                assert data["avgResponseTime"] == 0

    def test_missing_summary_fields(self):
        """Fehlende Summary-Felder werden behandelt."""
        from main import app
        from app.api.routes import analytics

        mock_logger = MagicMock()
        mock_logger.enabled = True
        mock_logger.get_summary = AsyncMock(return_value={})

        with patch.object(analytics, 'get_analytics_logger', return_value=mock_logger):
            with TestClient(app) as test_client:
                response = test_client.get("/api/analytics/dashboard")

                assert response.status_code == 200
                data = response.json()

                assert data["totalRequests"] == 0
