"""
E2E Tests for Path Approval Confirmation Flow

Tests the path validation logic in different scenarios.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.services.path_validator import PathValidator, PathValidationResult


class TestPathApprovalFlow:
    """Test path approval confirmation flow scenarios."""

    def test_scenario_system_path_blocked(self):
        """Scenario: System path cannot be approved."""
        validator = PathValidator()

        # Try to approve Windows system path
        result = validator.validate_approval(
            r"C:\Windows\System32\test.txt",
            access_type="write",
            whitelisted_paths=[]
        )

        # Should be blocked
        assert not result.approved
        assert result.reason == "system_critical"

    def test_scenario_user_path_needs_approval(self):
        """Scenario: User path not whitelisted, needs approval."""
        validator = PathValidator()
        user_path = str(Path.home() / "Documents" / "output.txt")

        result = validator.validate_approval(
            user_path,
            access_type="write",
            whitelisted_paths=[]
        )

        # Should need approval
        assert not result.approved
        assert result.reason == "not_whitelisted"

    def test_scenario_path_approved_and_whitelisted(self):
        """Scenario: Path is approved and added to whitelist."""
        validator = PathValidator()
        user_path = str(Path.home() / "Documents" / "output.txt")

        # Step 1: Initial request (not whitelisted)
        result = validator.validate_approval(
            user_path,
            access_type="write",
            whitelisted_paths=[]
        )
        assert not result.approved

        # Step 2: Add to whitelist
        whitelist = [user_path]

        # Step 3: Next request (whitelisted)
        result = validator.validate_approval(
            user_path,
            access_type="write",
            whitelisted_paths=whitelist
        )
        assert result.approved
        assert result.reason == "ok"

    def test_scenario_multiple_sequential_approvals(self):
        """Scenario: Multiple paths need approval sequentially."""
        validator = PathValidator()
        paths = [
            str(Path.home() / "file1.txt"),
            str(Path.home() / "file2.txt"),
            str(Path.home() / "file3.txt"),
        ]

        whitelist = []

        for path in paths:
            # Request approval for path
            result = validator.validate_approval(
                path,
                access_type="write",
                whitelisted_paths=whitelist
            )
            assert not result.approved
            assert result.reason == "not_whitelisted"

            # User approves - add to whitelist
            whitelist.append(path)

            # Verify it's now whitelisted
            result = validator.validate_approval(
                path,
                access_type="write",
                whitelisted_paths=whitelist
            )
            assert result.approved

    def test_scenario_win32com_temp_access(self):
        """Scenario: win32com.client needs Temp access (MVP use case)."""
        validator = PathValidator()

        # win32com needs to create temp files
        temp_path = r"C:\Users\testuser\AppData\Local\Temp\gen_py"

        # Step 1: First run - not whitelisted
        result = validator.validate_approval(
            temp_path,
            access_type="write",
            whitelisted_paths=[]
        )
        assert not result.approved
        assert result.reason == "not_whitelisted"

        # Step 2: User approves
        whitelist = [temp_path]

        # Step 3: Script restarts with whitelisted path
        result = validator.validate_approval(
            temp_path,
            access_type="write",
            whitelisted_paths=whitelist
        )
        assert result.approved
        assert result.reason == "ok"

    def test_scenario_path_traversal_attempt_blocked(self):
        """Scenario: Path traversal attempt is detected and blocked."""
        validator = PathValidator()

        # Attempt to escape Temp directory via traversal
        malicious = r"C:\Temp\..\Windows\System32\test.txt"

        # Should normalize to Windows path and detect as critical
        normalized = validator.normalize_path(malicious)
        is_critical = validator.is_system_critical(normalized)

        # Should be detected as critical (not whitelisted + malicious)
        assert is_critical

    def test_scenario_case_insensitive_matching_windows(self):
        """Scenario: Windows paths are case-insensitive."""
        validator = PathValidator()
        base_path = r"C:\Temp\myfile.txt"

        # Whitelist with one case
        whitelist = [base_path]

        # Request with different case
        result = validator.validate_approval(
            r"C:\temp\myfile.txt",  # lowercase 'temp'
            access_type="write",
            whitelisted_paths=whitelist
        )

        # Should be approved (case-insensitive on Windows)
        assert result.approved or result.reason == "not_whitelisted"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
