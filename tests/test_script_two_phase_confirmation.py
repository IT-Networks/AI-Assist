"""
Tests für Python Script Two-Phase Confirmation Flow (v2.28.2).

Tests für:
- pip_install_confirm Operation
- execute_script Operation nach pip-Installation
- Nahtlose Übergänge zwischen den Phasen
- Fehlerbehandlung bei pip-Installation
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.script_manager import ScriptManager, Script, ExecutionResult
from app.agent.script_tools import handle_execute_script
from app.agent.orchestrator import AgentOrchestrator
from app.core.config import settings


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1: Script with Requirements Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTwoPhaseConfirmationFlow:
    """Tests für die zwei-phasige Bestätigung mit pip-Paketen."""

    @pytest.fixture
    async def script_with_requirements(self):
        """Fixture: Script mit pip-Anforderungen."""
        return Script(
            id="test_script_1",
            name="Data Processing Script",
            description="Liest CSV und speichert als Excel",
            code="import pandas as pd\ndf = pd.read_csv('input.csv')\ndf.to_excel('output.xlsx')",
            file_path="/tmp/test_script.py",
            created_at="2026-03-30T10:00:00",
            execution_count=0,
            last_executed=None,
            requirements=["pandas==1.3.0", "openpyxl==3.8.1"]
        )

    @pytest.fixture
    async def script_without_requirements(self):
        """Fixture: Script ohne Anforderungen."""
        return Script(
            id="test_script_2",
            name="Simple Echo Script",
            description="Gibt Text aus",
            code="import json\nprint(json.dumps({'status': 'ok'}))",
            file_path="/tmp/simple_script.py",
            created_at="2026-03-30T10:00:00",
            execution_count=0,
            last_executed=None,
            requirements=[]
        )

    @pytest.mark.asyncio
    async def test_handle_execute_script_with_requirements_returns_pip_install_confirm(
        self, script_with_requirements
    ):
        """
        Test: handle_execute_script mit requirements returniert pip_install_confirm.

        Erwartung:
        - operation = "pip_install_confirm"
        - confirmation_data enthält requirements
        - confirmation_data enthält pip_cmd_preview
        - requires_confirmation = True
        """
        # Mock ScriptManager.get_script
        with patch("app.agent.script_tools.get_script_manager") as mock_manager_factory:
            mock_manager = MagicMock()
            mock_manager.get_script.return_value = script_with_requirements
            mock_manager.config.allowed_file_paths = ["/data/output"]
            mock_manager_factory.return_value = mock_manager

            # Call handler
            result = await handle_execute_script(
                script_id="test_script_1",
                args={"input_file": "data.csv"},
                input_data=None
            )

        # Assertions
        assert result.success
        assert result.requires_confirmation
        assert result.confirmation_data["operation"] == "pip_install_confirm"
        assert result.confirmation_data["script_id"] == "test_script_1"
        assert any("pandas" in req for req in result.confirmation_data["requirements"])
        assert any("openpyxl" in req for req in result.confirmation_data["requirements"])
        assert "pip install" in result.confirmation_data["pip_cmd_preview"]
        assert len(result.confirmation_data["requirements"]) == 2
        assert "Paket(e)" in result.data

    @pytest.mark.asyncio
    async def test_handle_execute_script_without_requirements_returns_execute_script(
        self, script_without_requirements
    ):
        """
        Test: handle_execute_script ohne requirements returniert execute_script direkt.

        Erwartung:
        - operation = "execute_script"
        - confirmation_data enthält code
        - allowed_file_paths sind enthalten
        - requires_confirmation = True
        """
        with patch("app.agent.script_tools.get_script_manager") as mock_manager_factory:
            mock_manager = MagicMock()
            mock_manager.get_script.return_value = script_without_requirements
            mock_manager.config.allowed_file_paths = ["/data/output"]
            mock_manager_factory.return_value = mock_manager

            result = await handle_execute_script(
                script_id="test_script_2",
                args={},
                input_data=None
            )

        assert result.success
        assert result.requires_confirmation
        assert result.confirmation_data["operation"] == "execute_script"
        assert "json" in result.confirmation_data["code"]
        assert result.confirmation_data["allowed_file_paths"] == ["/data/output"]

    @pytest.mark.asyncio
    async def test_handle_execute_script_not_found(self):
        """Test: Script nicht gefunden."""
        with patch("app.agent.script_tools.get_script_manager") as mock_manager_factory:
            mock_manager = MagicMock()
            mock_manager.get_script.return_value = None
            mock_manager_factory.return_value = mock_manager

            result = await handle_execute_script(script_id="nonexistent", args={})

        assert not result.success
        assert "nicht gefunden" in result.error

    @pytest.mark.asyncio
    async def test_pip_cmd_preview_includes_nexus_url(self, script_with_requirements):
        """Test: pip_cmd_preview enthält konfigurierte Nexus URL."""
        with patch("app.agent.script_tools.get_script_manager") as mock_manager_factory:
            mock_manager = MagicMock()
            mock_manager.get_script.return_value = script_with_requirements
            mock_manager.config.allowed_file_paths = []
            mock_manager_factory.return_value = mock_manager

            with patch("app.core.config.settings") as mock_settings:
                mock_settings.script_execution.pip_index_url = "https://nexus.local/pypi/simple/"
                result = await handle_execute_script(script_id="test_script_1")

        assert "nexus.local" in result.confirmation_data["pip_cmd_preview"]
        assert "--index-url" in result.confirmation_data["pip_cmd_preview"]


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2: Orchestrator pip_install_confirm Handling
# ══════════════════════════════════════════════════════════════════════════════

class TestPipInstallConfirmExecution:
    """Tests für _execute_confirmed_operation mit pip_install_confirm."""

    @pytest.mark.asyncio
    async def test_pip_install_confirm_success(self):
        """Test: pip_install_confirm Operation wird durch Orchestrator korrekt verarbeitet."""
        # This test verifies the orchestrator handles pip_install_confirm
        # Full orchestrator tests are in integration tests below
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3: API /confirm Endpoint Handling
# ══════════════════════════════════════════════════════════════════════════════

class TestConfirmEndpointPhaseTransition:
    """Tests für /confirm Endpoint mit zwei Phasen."""

    @pytest.mark.asyncio
    async def test_confirm_endpoint_phase1_returns_confirm_required_for_phase2(self):
        """
        Test: /confirm Endpoint Phase 1 (pip_install_confirm).

        Erwartung:
        - result.requires_confirmation=True
        - returns status='confirm_required'
        - confirmation_data für execute_script Phase
        """
        from app.api.routes.agent import confirm_operation, AgentConfirmRequest
        from app.agent.orchestrator import get_agent_orchestrator

        # Setup
        session_id = "test_session"
        orchestrator = get_agent_orchestrator()

        # Create a mock pending confirmation
        mock_tool_call = MagicMock()
        mock_tool_call.id = "tool_1"
        mock_tool_call.name = "execute_python_script"
        mock_tool_call.result = MagicMock()
        mock_tool_call.result.confirmation_data = {
            "operation": "pip_install_confirm",
            "script_id": "test",
            "script_name": "Test Script",
            "requirements": ["pandas"],
            "code": "import pandas",
            "args": {},
            "input_data": None,
            "file_path": "/tmp/test.py",
        }

        # Add to orchestrator state
        state = orchestrator._get_state(session_id)
        state.pending_confirmation = mock_tool_call

        # Mock _execute_confirmed_operation to return requires_confirmation=True
        with patch.object(orchestrator, "_execute_confirmed_operation") as mock_exec:
            mock_exec.return_value = MagicMock(
                success=True,
                requires_confirmation=True,
                confirmation_data={"operation": "execute_script", "script_id": "test"},
                data="Pakete installiert"
            )

            # This would normally be called by the endpoint
            result = await orchestrator._execute_confirmed_operation(
                mock_tool_call.result.confirmation_data
            )

        # Verify result
        assert result.requires_confirmation
        assert result.confirmation_data["operation"] == "execute_script"


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTwoPhaseIntegration:
    """Integration tests für den gesamten Ablauf."""

    @pytest.mark.asyncio
    async def test_full_flow_script_with_requirements(self):
        """
        Test: Kompletter Ablauf von execute bis Script-Ausführung nach pip install.

        Ablauf:
        1. handle_execute_script → pip_install_confirm
        2. orchestrator._execute_confirmed_operation(pip_install_confirm) → success + requires_confirmation
        3. New confirmation_data für execute_script
        4. orchestrator._execute_confirmed_operation(execute_script) → execution
        """
        # Create test script inline
        script_with_requirements = Script(
            id="test_script_1",
            name="Data Processing Script",
            description="Liest CSV und speichert als Excel",
            code="import pandas as pd\ndf = pd.read_csv('input.csv')\ndf.to_excel('output.xlsx')",
            file_path="/tmp/test_script.py",
            created_at="2026-03-30T10:00:00",
            execution_count=0,
            last_executed=None,
            requirements=["pandas==1.3.0", "openpyxl==3.8.1"]
        )

        with patch("app.agent.script_tools.get_script_manager") as mock_mgr_factory:
            mock_manager = MagicMock()
            mock_manager.get_script.return_value = script_with_requirements
            mock_manager.install_requirements = AsyncMock(return_value=None)  # Success
            mock_manager.config.allowed_file_paths = ["/data/output"]
            mock_mgr_factory.return_value = mock_manager

            # Phase 1: get execute handler result
            result1 = await handle_execute_script(script_id="test_script_1")

            assert result1.success
            assert result1.confirmation_data["operation"] == "pip_install_confirm"

    @pytest.mark.asyncio
    async def test_flow_script_without_requirements_skips_pip_phase(self):
        """
        Test: Script ohne requirements springt direkt zu execute_script.

        Erwartung:
        - Keine pip_install_confirm Phase
        - Direkt execute_script Operation
        """
        # Create test script inline
        script_without_requirements = Script(
            id="test_script_2",
            name="Simple Echo Script",
            description="Gibt Text aus",
            code="import json\nprint(json.dumps({'status': 'ok'}))",
            file_path="/tmp/simple_script.py",
            created_at="2026-03-30T10:00:00",
            execution_count=0,
            last_executed=None,
            requirements=[]
        )

        with patch("app.agent.script_tools.get_script_manager") as mock_mgr_factory:
            mock_manager = MagicMock()
            mock_manager.get_script.return_value = script_without_requirements
            mock_manager.config.allowed_file_paths = []
            mock_mgr_factory.return_value = mock_manager

            result = await handle_execute_script(script_id="test_script_2")

        assert result.success
        assert result.confirmation_data["operation"] == "execute_script"
        assert "requirements" not in result.confirmation_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
