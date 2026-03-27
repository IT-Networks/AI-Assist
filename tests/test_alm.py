"""
Tests fuer HP ALM/Quality Center Integration.

Testet:
- ALMConfig
- ALMClient (ohne echte Verbindung)
- ALM Tools Definition
- ALM API Routes
- Data Models
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestALMConfig:
    """Tests fuer ALMConfig."""

    def test_config_exists(self):
        """Prueft ob ALMConfig existiert."""
        from app.core.config import ALMConfig
        config = ALMConfig()

        assert config.enabled == False
        assert config.base_url == ""
        assert config.domain == ""
        assert config.project == ""
        assert config.verify_ssl == True
        assert config.require_confirmation == True
        assert config.default_test_type == "MANUAL"
        assert config.timeout_seconds == 30
        assert config.session_cache_ttl == 3600

    def test_config_in_settings(self):
        """Prueft ob alm in Settings registriert ist."""
        from app.core.config import settings

        assert hasattr(settings, "alm")
        assert settings.alm.enabled == False

    def test_config_with_values(self):
        """Prueft Config mit gesetzten Werten."""
        from app.core.config import ALMConfig

        config = ALMConfig(
            enabled=True,
            base_url="https://alm.example.com/qcbin",
            domain="DEFAULT",
            project="TestProject",
            username="testuser",
            password="secret",
        )

        assert config.enabled == True
        assert config.base_url == "https://alm.example.com/qcbin"
        assert config.domain == "DEFAULT"
        assert config.project == "TestProject"


class TestALMException:
    """Tests fuer ALMError Exception."""

    def test_exception_exists(self):
        """Prueft ob ALMError existiert."""
        from app.core.exceptions import ALMError, AIAssistError

        assert issubclass(ALMError, AIAssistError)

    def test_exception_message(self):
        """Prueft Exception-Message."""
        from app.core.exceptions import ALMError

        exc = ALMError("Test error message")
        assert str(exc) == "Test error message"


class TestALMDataModels:
    """Tests fuer ALM Data Models."""

    def test_alm_session(self):
        """Prueft ALMSession Dataclass."""
        from app.services.alm_client import ALMSession

        session = ALMSession(
            lwsso_cookie="test_lwsso",
            qc_session="test_qc",
            alm_user="testuser",
            xsrf_token="test_xsrf",
        )

        assert session.lwsso_cookie == "test_lwsso"
        assert session.qc_session == "test_qc"
        assert session.alm_user == "testuser"
        assert session.xsrf_token == "test_xsrf"
        assert session.ttl_seconds == 3600

    def test_alm_session_is_valid(self):
        """Prueft Session Gueltigkeitspruefung."""
        from app.services.alm_client import ALMSession
        from datetime import timedelta

        # Frische Session sollte gueltig sein
        session = ALMSession(ttl_seconds=3600)
        assert session.is_valid() == True

        # Abgelaufene Session
        old_session = ALMSession(
            created_at=datetime.now() - timedelta(hours=2),
            ttl_seconds=3600
        )
        assert old_session.is_valid() == False

    def test_alm_session_get_cookies(self):
        """Prueft Cookie-Dict Generation."""
        from app.services.alm_client import ALMSession

        session = ALMSession(
            lwsso_cookie="lwsso_val",
            qc_session="qc_val",
            alm_user="user_val",
            xsrf_token="xsrf_val",
        )

        cookies = session.get_cookies()
        assert cookies["LWSSO_COOKIE_KEY"] == "lwsso_val"
        assert cookies["QCSession"] == "qc_val"
        assert cookies["ALM_USER"] == "user_val"
        assert cookies["XSRF-TOKEN"] == "xsrf_val"

    def test_alm_test_model(self):
        """Prueft ALMTest Dataclass."""
        from app.services.alm_client import ALMTest

        test = ALMTest(
            id=123,
            name="Login Test",
            description="Test the login functionality",
            folder_id=10,
            folder_path="Root/Module/Auth",
            test_type="MANUAL",
            status="Ready",
            owner="tester1",
        )

        assert test.id == 123
        assert test.name == "Login Test"
        assert test.test_type == "MANUAL"
        assert test.folder_path == "Root/Module/Auth"

    def test_alm_test_to_markdown(self):
        """Prueft Markdown-Generierung."""
        from app.services.alm_client import ALMTest, ALMTestStep

        test = ALMTest(
            id=123,
            name="Login Test",
            description="Test description",
            folder_path="Root/Auth",
            test_type="MANUAL",
            status="Ready",
            owner="tester1",
            steps=[
                ALMTestStep(id=1, step_order=1, name="Step 1",
                           description="Enter username", expected_result="Field accepts input"),
                ALMTestStep(id=2, step_order=2, name="Step 2",
                           description="Click login", expected_result="User logged in"),
            ]
        )

        md = test.to_markdown()
        assert "## Testfall: Login Test" in md
        assert "(ID: 123)" in md
        assert "**Folder:** Root/Auth" in md
        assert "**Typ:** MANUAL" in md
        assert "### Test-Schritte" in md
        assert "Enter username" in md
        assert "Field accepts input" in md

    def test_alm_folder_model(self):
        """Prueft ALMFolder Dataclass."""
        from app.services.alm_client import ALMFolder

        folder = ALMFolder(id=10, name="Module", parent_id=5, path="Root/Module")
        assert folder.id == 10
        assert folder.name == "Module"
        assert folder.parent_id == 5

    def test_alm_test_step_model(self):
        """Prueft ALMTestStep Dataclass."""
        from app.services.alm_client import ALMTestStep

        step = ALMTestStep(
            id=1,
            step_order=1,
            name="Login Step",
            description="Enter credentials",
            expected_result="Login successful",
        )

        assert step.id == 1
        assert step.step_order == 1
        assert step.description == "Enter credentials"

    def test_alm_run_model(self):
        """Prueft ALMRun Dataclass."""
        from app.services.alm_client import ALMRun

        run = ALMRun(
            id=456,
            test_instance_id=789,
            status="Passed",
            comment="All checks passed",
            executor="tester1",
        )

        assert run.id == 456
        assert run.status == "Passed"
        assert run.test_instance_id == 789


class TestALMClient:
    """Tests fuer ALMClient."""

    def test_client_import(self):
        """Prueft ob Client importierbar ist."""
        from app.services.alm_client import ALMClient, get_alm_client

        assert ALMClient is not None
        assert get_alm_client is not None

    def test_client_singleton(self):
        """Prueft Singleton-Pattern."""
        from app.services.alm_client import get_alm_client

        client1 = get_alm_client()
        client2 = get_alm_client()
        assert client1 is client2

    def test_client_check_configured_raises(self):
        """Prueft ob _check_configured bei fehlender Config Fehler wirft."""
        from app.services.alm_client import ALMClient
        from app.core.exceptions import ALMError

        client = ALMClient()
        client.base_url = ""
        client.domain = ""
        client.project = ""

        with pytest.raises(ALMError) as exc_info:
            client._check_configured()

        assert "nicht konfiguriert" in str(exc_info.value)

    def test_client_rest_url(self):
        """Prueft REST URL Generierung."""
        from app.services.alm_client import ALMClient

        client = ALMClient()
        client.base_url = "https://alm.example.com/qcbin"
        client.domain = "PROD"
        client.project = "TestProj"

        url = client._rest_url("/tests/123")
        assert url == "https://alm.example.com/qcbin/rest/domains/PROD/projects/TestProj/tests/123"

    def test_client_auth_headers(self):
        """Prueft Auth Header Generation (JSON-basiert)."""
        from app.services.alm_client import ALMClient

        client = ALMClient()
        client.username = "testuser"
        client.password = "testpass"

        headers = client._auth_headers()
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/json"
        assert headers["cache-control"] == "no-cache"

    def test_client_auth_body(self):
        """Prueft Auth Body Generation."""
        from app.services.alm_client import ALMClient

        client = ALMClient()
        client.username = "testuser"
        client.password = "testpass"

        body = client._auth_body()
        assert "alm-authentication" in body
        assert body["alm-authentication"]["user"] == "testuser"
        assert body["alm-authentication"]["password"] == "testpass"

    def test_client_session_headers(self):
        """Prueft Session Header Generation."""
        from app.services.alm_client import ALMClient, ALMSession

        client = ALMClient()
        client._session = ALMSession(xsrf_token="test_xsrf")

        headers = client._session_headers()
        assert headers["X-XSRF-TOKEN"] == "test_xsrf"
        assert headers["Accept"] == "application/xml"
        assert headers["Content-Type"] == "application/xml"

    def test_client_build_entity_xml(self):
        """Prueft XML Entity Generation."""
        from app.services.alm_client import ALMClient

        client = ALMClient()
        xml = client._build_entity_xml("test", {
            "name": "My Test",
            "parent-id": 10,
            "description": "Test <desc>",
        })

        assert '<?xml version="1.0"' in xml
        assert 'Type="test"' in xml
        assert '<Field Name="name"><Value>My Test</Value></Field>' in xml
        assert '<Field Name="parent-id"><Value>10</Value></Field>' in xml
        # Check escaping
        assert '&lt;desc&gt;' in xml

    @pytest.mark.asyncio
    async def test_client_test_connection_disabled(self):
        """Prueft test_connection wenn ALM deaktiviert."""
        from app.services.alm_client import ALMClient

        client = ALMClient()
        client.base_url = ""

        result = await client.test_connection()
        assert result["success"] == False
        assert "nicht konfiguriert" in result["error"]


class TestALMTools:
    """Tests fuer ALM Tool-Definitionen."""

    def test_tools_registered(self):
        """Prueft ob ALM Tools in Registry registriert werden."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        count = register_alm_tools(registry)

        # Test Plan: test_connection, search, read, create, update, folders (6)
        # Test Lab: test_lab_folders, test-sets, search_instances, run_history, create_run (5)
        assert count == 11

    def test_tool_names(self):
        """Prueft Tool-Namen."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        register_alm_tools(registry)

        tool_names = [t.name for t in registry.list_tools()]
        # Test Plan Tools
        assert "alm_test_connection" in tool_names
        assert "alm_search_tests" in tool_names
        assert "alm_read_test" in tool_names
        assert "alm_create_test" in tool_names
        assert "alm_update_test" in tool_names
        assert "alm_list_folders" in tool_names
        # Test Lab Tools
        assert "alm_list_test_lab_folders" in tool_names
        assert "alm_list_test_sets" in tool_names
        assert "alm_search_test_instances" in tool_names
        assert "alm_get_run_history" in tool_names
        assert "alm_create_run" in tool_names

    def test_write_tools_marked(self):
        """Prueft ob Schreib-Tools als is_write_operation markiert sind."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        register_alm_tools(registry)

        write_tools = ["alm_create_test", "alm_update_test", "alm_create_run"]
        for tool_name in write_tools:
            tool = registry.get(tool_name)
            assert tool is not None, f"Tool {tool_name} nicht gefunden"
            assert tool.is_write_operation == True, f"{tool_name} sollte is_write_operation=True haben"

    def test_read_tools_not_write(self):
        """Prueft ob Lese-Tools NICHT als is_write_operation markiert sind."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        register_alm_tools(registry)

        read_tools = [
            "alm_test_connection", "alm_search_tests", "alm_read_test",
            "alm_list_folders", "alm_list_test_sets",
            "alm_list_test_lab_folders", "alm_search_test_instances", "alm_get_run_history"
        ]
        for tool_name in read_tools:
            tool = registry.get(tool_name)
            assert tool is not None, f"Tool {tool_name} nicht gefunden"
            assert tool.is_write_operation == False, f"{tool_name} sollte is_write_operation=False haben"

    def test_tools_have_handlers(self):
        """Prueft ob alle Tools Handler haben."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        register_alm_tools(registry)

        for tool in registry.list_tools():
            if tool.name.startswith("alm_"):
                assert tool.handler is not None, f"{tool.name} hat keinen Handler"


class TestALMRoutes:
    """Tests fuer ALM API Routes."""

    def test_router_exists(self):
        """Prueft ob ALM Router existiert."""
        from app.api.routes.alm import router

        assert router is not None
        assert router.prefix == "/api/alm"
        assert "alm" in router.tags

    def test_routes_defined(self):
        """Prueft ob alle Routes definiert sind."""
        from app.api.routes.alm import router

        route_paths = [r.path for r in router.routes]

        # Routes enthalten den vollen Pfad mit Prefix
        assert "/api/alm/status" in route_paths
        assert "/api/alm/test-connection" in route_paths
        assert "/api/alm/folders" in route_paths
        assert "/api/alm/tests" in route_paths
        assert "/api/alm/tests/{test_id}" in route_paths
        assert "/api/alm/test-sets" in route_paths

    @pytest.mark.asyncio
    async def test_status_route(self):
        """Prueft Status-Route Response."""
        from app.api.routes.alm import get_alm_status

        response = await get_alm_status()

        assert response.enabled == False
        assert response.configured == False


class TestALMIntegration:
    """Integrationstests fuer ALM (ohne echte Verbindung)."""

    @pytest.mark.asyncio
    async def test_search_tests_disabled(self):
        """Prueft alm_search_tests wenn ALM deaktiviert."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        register_alm_tools(registry)

        tool = registry.get("alm_search_tests")
        result = await tool.handler(query="test")

        assert result.success == False
        assert "nicht aktiviert" in result.error

    @pytest.mark.asyncio
    async def test_read_test_disabled(self):
        """Prueft alm_read_test wenn ALM deaktiviert."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        registry = ToolRegistry()
        register_alm_tools(registry)

        tool = registry.get("alm_read_test")
        result = await tool.handler(test_id=123)

        assert result.success == False
        assert "nicht aktiviert" in result.error

    @pytest.mark.asyncio
    async def test_create_test_missing_params(self):
        """Prueft alm_create_test mit fehlenden Parametern."""
        from app.agent.tools import ToolRegistry
        from app.agent.alm_tools import register_alm_tools

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.alm.enabled = True
            mock_settings.alm.require_confirmation = False

            registry = ToolRegistry()
            register_alm_tools(registry)

            tool = registry.get("alm_create_test")

            # Ohne name
            result = await tool.handler(folder_id=10)
            assert result.success == False
            assert "name" in result.error.lower()

            # Ohne folder_id
            result = await tool.handler(name="Test")
            assert result.success == False
            assert "folder_id" in result.error.lower()
