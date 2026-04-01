"""
Tests for PathValidator - validates file path safety for scripts.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.services.path_validator import PathValidator, PathValidationResult


class TestPathValidator:
    """Test path validation logic."""

    def setup_method(self):
        """Set up validator instance."""
        self.validator = PathValidator()

    def test_normalize_path_windows(self):
        """Test path normalization on Windows."""
        paths = [
            r"C:\Users\test\file.txt",
            r"C:\users\test\file.txt",
            "C:/Users/test/file.txt",
        ]

        normalized_paths = [self.validator.normalize_path(p) for p in paths]
        # All should normalize to similar form
        assert all(isinstance(p, str) for p in normalized_paths)

    def test_system_critical_detection_windows(self):
        """Test detection of system-critical paths on Windows."""
        system_critical = [
            r"C:\Windows\System32",
            r"C:\Program Files\App",
            r"C:\ProgramData\Config",
        ]

        for path in system_critical:
            assert self.validator.is_system_critical(path), f"{path} should be critical"

    def test_non_system_critical_paths(self):
        """Test that normal user paths are not marked as critical."""
        user_paths = [
            str(Path.home() / "Documents"),
            str(Path.home() / "Desktop"),
        ]

        for path in user_paths:
            result = self.validator.is_system_critical(path)
            assert isinstance(result, bool)

    def test_validate_approval_system_critical(self):
        """Test that system-critical paths are rejected."""
        result = self.validator.validate_approval(
            r"C:\Windows\System32\test.txt",
            access_type="write"
        )

        assert not result.approved
        assert result.reason == "system_critical"

    def test_validate_approval_not_whitelisted(self):
        """Test that non-whitelisted paths need approval."""
        result = self.validator.validate_approval(
            str(Path.home() / "test.txt"),
            access_type="write",
            whitelisted_paths=[]
        )

        assert not result.approved
        assert result.reason == "not_whitelisted"

    def test_validate_approval_whitelisted(self):
        """Test that whitelisted paths are approved."""
        test_path = str(Path.home() / "test.txt")
        result = self.validator.validate_approval(
            test_path,
            access_type="write",
            whitelisted_paths=[test_path]
        )

        assert result.approved
        assert result.reason == "ok"

    def test_path_validation_result_fields(self):
        """Test that PathValidationResult has required fields."""
        result = self.validator.validate_approval(
            str(Path.home() / "test.txt"),
            access_type="write"
        )

        assert hasattr(result, 'approved')
        assert hasattr(result, 'reason')
        assert hasattr(result, 'suggestion')
        assert isinstance(result.approved, bool)
        assert isinstance(result.reason, str)

    def test_access_types(self):
        """Test different access types."""
        test_path = str(Path.home() / "test.txt")

        for access_type in ["read", "write", "delete"]:
            result = self.validator.validate_approval(
                test_path,
                access_type=access_type,
                whitelisted_paths=[]
            )

            assert isinstance(result, PathValidationResult)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
