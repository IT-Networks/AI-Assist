"""
Tests fuer Workspace Events.

Testet:
- WORKSPACE_CODE_CHANGE Events
- WORKSPACE_SQL_RESULT Events
- Event-Payload Struktur
- Diff-Generierung
"""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_event_bridge():
    """Erstellt gemockte Event Bridge."""
    bridge = MagicMock()
    bridge.emit = AsyncMock()
    return bridge


@pytest.fixture
def orchestrator_with_mock_bridge(mock_event_bridge):
    """Erstellt Orchestrator mit gemockter Event Bridge."""
    from app.agent.orchestrator import AgentOrchestrator

    # Create mock config
    mock_config = MagicMock()
    mock_config.model = "test-model"
    mock_config.max_iterations = 10

    orchestrator = AgentOrchestrator(mock_config)
    orchestrator._event_bridge = mock_event_bridge

    return orchestrator


# ═══════════════════════════════════════════════════════════════════════════════
# Code Change Event Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkspaceCodeChangeEvent:
    """Tests fuer WORKSPACE_CODE_CHANGE Events."""

    @pytest.mark.asyncio
    async def test_emit_code_change_basic(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Basis Code Change Event wird emittiert."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="test/example.py",
            original_content="print('hello')",
            modified_content="print('hello world')",
            tool_call="edit_file",
            description="Added world"
        )

        mock_event_bridge.emit.assert_called_once()
        call_args = mock_event_bridge.emit.call_args

        # Check event type
        from app.agent.orchestrator import AgentEventType
        assert call_args[0][0] == AgentEventType.WORKSPACE_CODE_CHANGE

        # Check payload structure
        payload = call_args[0][1]
        assert "id" in payload
        assert "timestamp" in payload
        assert payload["filePath"] == "test/example.py"
        assert payload["fileName"] == "example.py"
        assert payload["language"] == "python"
        assert payload["toolCall"] == "edit_file"
        assert payload["description"] == "Added world"
        assert payload["status"] == "applied"

    @pytest.mark.asyncio
    async def test_emit_code_change_new_file(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Neue Datei erzeugt korrektes Diff."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="src/new_file.py",
            original_content="",
            modified_content="def hello():\n    pass",
            tool_call="write_file",
            description="Created new file",
            is_new=True
        )

        payload = mock_event_bridge.emit.call_args[0][1]

        assert payload["isNew"] is True
        assert "--- /dev/null" in payload["diff"]
        assert "+++ b/src/new_file.py" in payload["diff"]
        assert "+def hello():" in payload["diff"]

    @pytest.mark.asyncio
    async def test_emit_code_change_unified_diff(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Unified Diff wird korrekt generiert."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="test.py",
            original_content="line1\nline2\nline3",
            modified_content="line1\nmodified\nline3",
            tool_call="edit_file",
            description="Modified line 2"
        )

        payload = mock_event_bridge.emit.call_args[0][1]

        diff = payload["diff"]
        assert "--- a/test.py" in diff
        assert "+++ b/test.py" in diff
        assert "-line2" in diff
        assert "+modified" in diff

    @pytest.mark.asyncio
    async def test_language_detection_python(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Python-Dateien werden erkannt."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="src/module.py",
            original_content="",
            modified_content="pass",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["language"] == "python"

    @pytest.mark.asyncio
    async def test_language_detection_java(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Java-Dateien werden erkannt."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="src/Service.java",
            original_content="",
            modified_content="class Service {}",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["language"] == "java"

    @pytest.mark.asyncio
    async def test_language_detection_javascript(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """JavaScript-Dateien werden erkannt."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="app.js",
            original_content="",
            modified_content="const x = 1;",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["language"] == "javascript"

    @pytest.mark.asyncio
    async def test_language_detection_typescript(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """TypeScript-Dateien werden erkannt."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="component.tsx",
            original_content="",
            modified_content="const App: FC = () => null",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["language"] == "tsx"

    @pytest.mark.asyncio
    async def test_language_detection_sql(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """SQL-Dateien werden erkannt."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="migrations/001.sql",
            original_content="",
            modified_content="CREATE TABLE test;",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["language"] == "sql"

    @pytest.mark.asyncio
    async def test_language_detection_unknown(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Unbekannte Dateien bekommen 'text' als Sprache."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="readme.unknown",
            original_content="",
            modified_content="content",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["language"] == "text"

    @pytest.mark.asyncio
    async def test_event_has_timestamp(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Event hat gueltigen Timestamp."""
        orchestrator = orchestrator_with_mock_bridge

        import time
        before = int(time.time() * 1000)

        await orchestrator._emit_workspace_code_change(
            file_path="test.py",
            original_content="",
            modified_content="",
            tool_call="write_file"
        )

        after = int(time.time() * 1000)
        payload = mock_event_bridge.emit.call_args[0][1]

        assert before <= payload["timestamp"] <= after

    @pytest.mark.asyncio
    async def test_event_has_uuid(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Event hat gueltige UUID."""
        orchestrator = orchestrator_with_mock_bridge
        import uuid

        await orchestrator._emit_workspace_code_change(
            file_path="test.py",
            original_content="",
            modified_content="",
            tool_call="write_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]

        # Verify it's a valid UUID
        try:
            uuid.UUID(payload["id"])
        except ValueError:
            pytest.fail("Event ID is not a valid UUID")


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Result Event Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkspaceSqlResultEvent:
    """Tests fuer WORKSPACE_SQL_RESULT Events."""

    @pytest.mark.asyncio
    async def test_emit_sql_result_success(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """SQL Result Event wird bei Erfolg emittiert."""
        orchestrator = orchestrator_with_mock_bridge

        # Mock DB client
        mock_db_result = MagicMock()
        mock_db_result.success = True
        mock_db_result.columns = ["ID", "NAME", "VALUE"]
        mock_db_result.rows = [
            {"ID": 1, "NAME": "Test", "VALUE": 100},
            {"ID": 2, "NAME": "Example", "VALUE": 200},
        ]
        mock_db_result.row_count = 2

        mock_db_client = MagicMock()
        mock_db_client.execute = AsyncMock(return_value=mock_db_result)
        mock_db_client.max_rows = 100
        mock_db_client.schema = "TESTSCHEMA"

        with patch("app.services.db_client.get_db_client", return_value=mock_db_client):
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.database.database = "TESTDB"
                mock_settings.database.max_rows = 100

                result = await orchestrator._execute_and_emit_sql_result(
                    query="SELECT * FROM test"
                )

        assert result.success
        mock_event_bridge.emit.assert_called_once()

        from app.agent.orchestrator import AgentEventType
        call_args = mock_event_bridge.emit.call_args

        assert call_args[0][0] == AgentEventType.WORKSPACE_SQL_RESULT

        payload = call_args[0][1]
        assert payload["query"] == "SELECT * FROM test"
        assert payload["database"] == "TESTDB"
        assert payload["schema"] == "TESTSCHEMA"
        assert len(payload["columns"]) == 3
        assert len(payload["rows"]) == 2
        assert payload["rowCount"] == 2
        assert "executionTimeMs" in payload
        assert payload["error"] is None

    @pytest.mark.asyncio
    async def test_emit_sql_result_error(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """SQL Result Event wird bei Fehler emittiert."""
        orchestrator = orchestrator_with_mock_bridge

        mock_db_result = MagicMock()
        mock_db_result.success = False
        mock_db_result.error = "SQL Syntax Error"

        mock_db_client = MagicMock()
        mock_db_client.execute = AsyncMock(return_value=mock_db_result)
        mock_db_client.max_rows = 100
        mock_db_client.schema = "TESTSCHEMA"

        with patch("app.services.db_client.get_db_client", return_value=mock_db_client):
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.database.database = "TESTDB"
                mock_settings.database.max_rows = 100

                result = await orchestrator._execute_and_emit_sql_result(
                    query="SELECT * FROM nonexistent"
                )

        assert not result.success
        mock_event_bridge.emit.assert_called_once()

        payload = mock_event_bridge.emit.call_args[0][1]
        assert payload["error"] == "SQL Syntax Error"
        assert payload["rowCount"] == 0
        assert payload["rows"] == []

    @pytest.mark.asyncio
    async def test_emit_sql_no_db_client(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Fehler wenn DB Client nicht verfuegbar."""
        orchestrator = orchestrator_with_mock_bridge

        with patch("app.services.db_client.get_db_client", return_value=None):
            result = await orchestrator._execute_and_emit_sql_result(
                query="SELECT 1"
            )

        assert not result.success
        assert "nicht verfügbar" in result.error

    @pytest.mark.asyncio
    async def test_sql_result_has_columns(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """SQL Result hat Spalten-Definitionen."""
        orchestrator = orchestrator_with_mock_bridge

        mock_db_result = MagicMock()
        mock_db_result.success = True
        mock_db_result.columns = ["COL1", "COL2"]
        mock_db_result.rows = []
        mock_db_result.row_count = 0

        mock_db_client = MagicMock()
        mock_db_client.execute = AsyncMock(return_value=mock_db_result)
        mock_db_client.max_rows = 100
        mock_db_client.schema = "TEST"

        with patch("app.services.db_client.get_db_client", return_value=mock_db_client):
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.database.database = "DB"
                mock_settings.database.max_rows = 100

                await orchestrator._execute_and_emit_sql_result("SELECT 1")

        payload = mock_event_bridge.emit.call_args[0][1]

        for col in payload["columns"]:
            assert "name" in col
            assert "type" in col
            assert "nullable" in col
            assert "visible" in col


# ═══════════════════════════════════════════════════════════════════════════════
# Event Payload Structure Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventPayloadStructure:
    """Tests fuer Event-Payload Struktur."""

    @pytest.mark.asyncio
    async def test_code_change_payload_matches_interface(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Code Change Payload entspricht dem Design-Interface."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="test.py",
            original_content="old",
            modified_content="new",
            tool_call="edit_file",
            description="Test change"
        )

        payload = mock_event_bridge.emit.call_args[0][1]

        # Verify all required fields from design doc
        required_fields = [
            "id", "timestamp", "filePath", "fileName", "language",
            "originalContent", "modifiedContent", "diff", "toolCall",
            "description", "status", "appliedAt", "isNew"
        ]

        for field in required_fields:
            assert field in payload, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_sql_result_payload_matches_interface(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """SQL Result Payload entspricht dem Design-Interface."""
        orchestrator = orchestrator_with_mock_bridge

        mock_db_result = MagicMock()
        mock_db_result.success = True
        mock_db_result.columns = ["TEST"]
        mock_db_result.rows = [{"TEST": 1}]
        mock_db_result.row_count = 1

        mock_db_client = MagicMock()
        mock_db_client.execute = AsyncMock(return_value=mock_db_result)
        mock_db_client.max_rows = 100
        mock_db_client.schema = "TEST"

        with patch("app.services.db_client.get_db_client", return_value=mock_db_client):
            with patch("app.core.config.settings") as mock_settings:
                mock_settings.database.database = "DB"
                mock_settings.database.max_rows = 100

                await orchestrator._execute_and_emit_sql_result("SELECT 1")

        payload = mock_event_bridge.emit.call_args[0][1]

        # Verify all required fields from design doc
        required_fields = [
            "id", "timestamp", "query", "database", "schema",
            "columns", "rows", "rowCount", "executionTimeMs",
            "toolCall", "truncated"
        ]

        for field in required_fields:
            assert field in payload, f"Missing field: {field}"


# ═══════════════════════════════════════════════════════════════════════════════
# Diff Generation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiffGeneration:
    """Tests fuer Diff-Generierung."""

    @pytest.mark.asyncio
    async def test_diff_empty_to_content(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Diff von leer zu Inhalt wird korrekt generiert."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="new.py",
            original_content="",
            modified_content="line1\nline2",
            tool_call="write_file",
            is_new=True
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        diff = payload["diff"]

        assert "+line1" in diff
        assert "+line2" in diff

    @pytest.mark.asyncio
    async def test_diff_content_modification(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Diff bei Inhaltsaenderung wird korrekt generiert."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="test.py",
            original_content="a\nb\nc",
            modified_content="a\nx\nc",
            tool_call="edit_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        diff = payload["diff"]

        assert "-b" in diff
        assert "+x" in diff

    @pytest.mark.asyncio
    async def test_diff_multiline_addition(self, orchestrator_with_mock_bridge, mock_event_bridge):
        """Mehrzeilige Additions werden im Diff gezeigt."""
        orchestrator = orchestrator_with_mock_bridge

        await orchestrator._emit_workspace_code_change(
            file_path="test.py",
            original_content="start\nend",
            modified_content="start\nmiddle1\nmiddle2\nend",
            tool_call="edit_file"
        )

        payload = mock_event_bridge.emit.call_args[0][1]
        diff = payload["diff"]

        assert "+middle1" in diff
        assert "+middle2" in diff
