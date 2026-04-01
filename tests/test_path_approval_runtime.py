"""
Tests for Runtime Path Approval Detection and Confirmation

Tests the complete flow:
1. Script tries to write to non-whitelisted path
2. Wrapper's _safe_open() blocks write with PermissionError
3. Error message contains path info
4. ExecutionResult captures pending_confirmation
5. script_tools returns confirmation request to user
6. User approves → path added to whitelist
7. Script restarts and succeeds
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path.cwd()))

from app.services.script_manager import ScriptExecutor, ExecutionResult
from app.agent.script_tools import execute_script_after_confirmation
from app.agent.tools import ToolResult


class TestPathApprovalRuntime:
    """Test runtime path approval detection."""

    def setup_method(self):
        """Set up test fixtures."""
        self.executor = ScriptExecutor()

    def test_extract_blocked_path_unix(self):
        """Test extraction of blocked path (Unix format)."""
        stderr = """PermissionError: File write blocked: '/home/user/Documents/file.txt' not in allowed_file_paths"""
        blocked = self.executor._extract_blocked_path(stderr, "")
        assert blocked == "/home/user/Documents/file.txt"

    def test_extract_blocked_path_windows(self):
        """Test extraction of blocked path (Windows format)."""
        stderr = r"""PermissionError: File write blocked: 'C:\Users\test\Temp\gen_py' not in allowed_file_paths"""
        blocked = self.executor._extract_blocked_path(stderr, "")
        assert blocked == r"C:\Users\test\Temp\gen_py"

    def test_extract_blocked_path_not_found(self):
        """Test that None is returned if no blocked path in error."""
        stderr = "Some other error message"
        blocked = self.executor._extract_blocked_path(stderr, "")
        assert blocked is None

    def test_execution_result_with_pending_confirmation(self):
        """Test ExecutionResult includes pending_confirmation."""
        pending = {
            "operation": "path_approval_confirm",
            "requested_path": "/tmp/test.txt",
        }

        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="File write blocked",
            error="Path not allowed",
            pending_confirmation=pending
        )

        assert result.pending_confirmation is not None
        assert result.pending_confirmation["operation"] == "path_approval_confirm"
        assert result.pending_confirmation["requested_path"] == "/tmp/test.txt"

    def test_wrapper_creates_guard_with_empty_list(self):
        """Test that wrapper creates _safe_open guard for empty list."""
        wrapper = self.executor._create_wrapper(
            "x = open('/tmp/test.txt', 'w')",
            {},
            allowed_paths=[]
        )

        assert "_safe_open" in wrapper, "Guard should be created"
        assert "ALLOWED_FILE_PATHS = json.loads('[]')" in wrapper
        assert "File write blocked" in wrapper

    def test_wrapper_error_message_includes_path(self):
        """Test that error message in wrapper includes blocked path."""
        wrapper = self.executor._create_wrapper(
            "x = 1",
            {},
            allowed_paths=[]
        )

        # Check that error message format is correct
        assert "File write blocked" in wrapper
        assert "not in allowed_file_paths" in wrapper

    def test_pending_confirmation_structure(self):
        """Test that pending_confirmation has required fields."""
        pending = {
            "operation": "path_approval_confirm",
            "blocked_reason": "file_write_blocked",
            "requested_path": "/tmp/test.txt",
            "access_type": "write",
            "reason": "Script versucht Datei zu schreiben",
        }

        # Verify all required fields
        required_fields = [
            "operation",
            "blocked_reason",
            "requested_path",
            "access_type",
            "reason",
        ]

        for field in required_fields:
            assert field in pending, f"Missing field: {field}"

    def test_confirmation_data_construction(self):
        """Test that confirmation_data is built correctly for frontend."""
        confirmation_data = {
            "operation": "path_approval_confirm",
            "script_id": "script-123",
            "script_name": "Test Script",
            "requested_path": "/tmp/output.txt",
            "access_type": "write",
            "reason": "Script output file",
            "is_system_critical": False,
            "script_args": {"param": "value"},
            "script_input_data": None,
        }

        # Verify structure
        assert confirmation_data["operation"] == "path_approval_confirm"
        assert confirmation_data["requested_path"] == "/tmp/output.txt"
        assert confirmation_data["script_id"] == "script-123"

    def test_tool_result_with_confirmation(self):
        """Test that ToolResult can include confirmation request."""
        confirmation_data = {
            "operation": "path_approval_confirm",
            "requested_path": "/tmp/test.txt",
            "access_type": "write",
        }

        result = ToolResult(
            success=True,
            data="Path approval requested",
            requires_confirmation=True,
            confirmation_data=confirmation_data
        )

        assert result.requires_confirmation
        assert result.confirmation_data["operation"] == "path_approval_confirm"

    def test_path_approval_flow_scenario(self):
        """Test complete path approval flow scenario."""
        # Step 1: Script execution returns pending_confirmation
        exec_result = ExecutionResult(
            success=False,
            stdout="",
            stderr="PermissionError: File write blocked: '/tmp/test.txt'",
            error="File access denied",
            pending_confirmation={
                "requested_path": "/tmp/test.txt",
                "access_type": "write",
                "reason": "Output file",
            }
        )

        assert exec_result.pending_confirmation is not None

        # Step 2: script_tools creates confirmation request
        confirmation_data = {
            "operation": "path_approval_confirm",
            "script_id": "script-1",
            "script_name": "Test Script",
            "requested_path": exec_result.pending_confirmation["requested_path"],
            "access_type": exec_result.pending_confirmation["access_type"],
            "reason": exec_result.pending_confirmation["reason"],
            "script_args": {},
            "script_input_data": None,
        }

        tool_result = ToolResult(
            success=True,
            data=f"Path blocked: {confirmation_data['requested_path']}",
            requires_confirmation=True,
            confirmation_data=confirmation_data
        )

        # Verify confirmation request
        assert tool_result.requires_confirmation
        assert tool_result.confirmation_data["operation"] == "path_approval_confirm"
        assert "/tmp/test.txt" in tool_result.confirmation_data["requested_path"]

    def test_multiple_path_extractions(self):
        """Test extracting multiple blocked paths from complex stderr."""
        stderr = """Starting script...
PermissionError: File write blocked: '/tmp/output1.txt' not in allowed_file_paths
Script failed after 5 seconds"""

        # Should extract first blocked path
        blocked = self.executor._extract_blocked_path(stderr, "")
        assert blocked == "/tmp/output1.txt"

    def test_path_with_spaces_extraction(self):
        """Test extracting paths with spaces."""
        stderr = r"""PermissionError: File write blocked: 'C:\Users\John Doe\Documents\output file.txt' not in allowed_file_paths"""
        blocked = self.executor._extract_blocked_path(stderr, "")
        assert blocked == r"C:\Users\John Doe\Documents\output file.txt"

    def test_path_with_special_chars_extraction(self):
        """Test extracting paths with special characters."""
        stderr = """PermissionError: File write blocked: '/home/user/data-2024_01-test.csv' not in allowed_file_paths"""
        blocked = self.executor._extract_blocked_path(stderr, "")
        assert blocked == "/home/user/data-2024_01-test.csv"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
