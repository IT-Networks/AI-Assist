"""
Tests fuer ServiceNow Integration.

Testet:
- Config
- Client (ohne echte Verbindung)
- Tools Definition
- API Routes
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestServiceNowConfig:
    """Tests fuer ServiceNowConfig."""

    def test_config_exists(self):
        """Prueft ob ServiceNowConfig existiert."""
        from app.core.config import ServiceNowConfig
        config = ServiceNowConfig()

        assert config.enabled == False
        assert config.auth_type == "basic"
        assert config.instance_url == ""

    def test_config_in_settings(self):
        """Prueft ob servicenow in Settings ist."""
        from app.core.config import settings

        assert hasattr(settings, "servicenow")
        assert settings.servicenow.enabled == False

    def test_get_api_url(self):
        """Prueft get_api_url Methode."""
        from app.core.config import ServiceNowConfig

        config = ServiceNowConfig(instance_url="http://localhost:8080")
        assert config.get_api_url() == "http://localhost:8080/api/now"
        assert config.get_api_url("table/incident") == "http://localhost:8080/api/now/table/incident"


class TestServiceNowClient:
    """Tests fuer ServiceNowClient."""

    def test_client_import(self):
        """Prueft ob Client importierbar ist."""
        from app.services.servicenow_client import ServiceNowClient, get_servicenow_client

        assert ServiceNowClient is not None
        assert get_servicenow_client is not None

    def test_client_singleton(self):
        """Prueft Singleton-Pattern."""
        from app.services.servicenow_client import get_servicenow_client

        client1 = get_servicenow_client()
        client2 = get_servicenow_client()
        assert client1 is client2

    def test_basic_auth_header(self):
        """Prueft Basic Auth Header Generation."""
        from app.services.servicenow_client import ServiceNowClient

        client = ServiceNowClient()
        # Mock config
        client._config = MagicMock()
        client._config.username = "admin"
        client._config.password = "password123"

        header = client._get_basic_auth_header()
        assert header.startswith("Basic ")
        # admin:password123 base64 encoded
        import base64
        expected = base64.b64encode(b"admin:password123").decode()
        assert header == f"Basic {expected}"


class TestServiceNowTools:
    """Tests fuer ServiceNow Tool-Definitionen."""

    def test_tools_defined(self):
        """Prueft ob alle Tools definiert sind."""
        from app.agent.servicenow_tools import ALL_SERVICENOW_TOOLS

        assert len(ALL_SERVICENOW_TOOLS) == 6

        tool_names = [t.name for t in ALL_SERVICENOW_TOOLS]
        assert "search_servicenow_applications" in tool_names
        assert "get_servicenow_app_details" in tool_names
        assert "query_servicenow_changes" in tool_names
        assert "search_servicenow_knowledge" in tool_names
        assert "query_servicenow_cmdb" in tool_names
        assert "query_servicenow_incidents" in tool_names

    def test_tool_has_handler(self):
        """Prueft ob alle Tools Handler haben."""
        from app.agent.servicenow_tools import ALL_SERVICENOW_TOOLS

        for tool in ALL_SERVICENOW_TOOLS:
            assert tool.handler is not None
            assert callable(tool.handler)

    def test_tool_has_parameters(self):
        """Prueft ob alle Tools Parameter definiert haben."""
        from app.agent.servicenow_tools import ALL_SERVICENOW_TOOLS

        for tool in ALL_SERVICENOW_TOOLS:
            assert hasattr(tool, "parameters")
            # Jedes Tool sollte mindestens einen Parameter haben
            assert len(tool.parameters) > 0

    def test_tool_category(self):
        """Prueft ob Tools die richtige Kategorie haben."""
        from app.agent.servicenow_tools import ALL_SERVICENOW_TOOLS
        from app.agent.tools import ToolCategory

        for tool in ALL_SERVICENOW_TOOLS:
            assert tool.category == ToolCategory.KNOWLEDGE

    def test_register_tools_disabled(self):
        """Prueft dass Tools nicht registriert werden wenn disabled."""
        from app.agent.servicenow_tools import register_servicenow_tools
        from app.agent.tools import ToolRegistry

        registry = ToolRegistry()

        # Mock settings mit disabled
        with patch("app.agent.servicenow_tools.settings") as mock_settings:
            mock_settings.servicenow.enabled = False
            register_servicenow_tools(registry)

        # Keine Tools sollten registriert sein
        assert len(registry.tools) == 0


class TestServiceNowAPIRoutes:
    """Tests fuer ServiceNow API Routes."""

    def test_router_import(self):
        """Prueft ob Router importierbar ist."""
        from app.api.routes.servicenow import router
        assert router is not None

    def test_router_prefix(self):
        """Prueft Router Prefix."""
        from app.api.routes.servicenow import router
        assert router.prefix == "/api/servicenow"

    def test_status_endpoint_exists(self):
        """Prueft ob Status-Endpoint definiert ist."""
        from app.api.routes.servicenow import get_servicenow_status
        assert get_servicenow_status is not None

    def test_test_connection_endpoint_exists(self):
        """Prueft ob Test-Connection-Endpoint definiert ist."""
        from app.api.routes.servicenow import test_servicenow_connection
        assert test_servicenow_connection is not None


class TestServiceNowQueryResult:
    """Tests fuer SNowQueryResult."""

    def test_query_result_creation(self):
        """Prueft SNowQueryResult Erstellung."""
        from app.services.servicenow_client import SNowQueryResult

        result = SNowQueryResult(
            records=[{"sys_id": "123", "name": "Test"}],
            total_count=1,
            query_time_ms=50
        )

        assert len(result.records) == 1
        assert result.total_count == 1
        assert result.query_time_ms == 50
        assert result.from_cache == False

    def test_query_result_from_cache(self):
        """Prueft from_cache Flag."""
        from app.services.servicenow_client import SNowQueryResult

        result = SNowQueryResult(
            records=[],
            total_count=0,
            query_time_ms=0,
            from_cache=True
        )

        assert result.from_cache == True


class TestFormatDisplayValue:
    """Tests fuer _format_display_value Helper."""

    def test_format_dict_value(self):
        """Prueft Formatierung von Dict-Werten."""
        from app.agent.servicenow_tools import _format_display_value

        value = {"display_value": "Active", "value": "1"}
        assert _format_display_value(value) == "Active"

    def test_format_dict_value_fallback(self):
        """Prueft Fallback auf value wenn kein display_value."""
        from app.agent.servicenow_tools import _format_display_value

        value = {"value": "1"}
        assert _format_display_value(value) == "1"

    def test_format_string_value(self):
        """Prueft Formatierung von String-Werten."""
        from app.agent.servicenow_tools import _format_display_value

        assert _format_display_value("Test") == "Test"
        assert _format_display_value("") == ""
        assert _format_display_value(None) == ""
