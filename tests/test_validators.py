"""
Tests für app/utils/validators/ - Validierungsframework.

Testet die Validator-Basis-Klassen und spezifische Validatoren.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import List

import pytest

from app.utils.validators.base import (
    BaseValidator,
    ValidationResult,
    ValidationIssue,
    ValidatorRegistry,
    CompileResult,
    Severity,
)


class TestValidationIssue:
    """Tests für ValidationIssue Dataclass."""

    def test_create_error_issue(self):
        """Error-Issue erstellen."""
        issue = ValidationIssue(
            severity=Severity.ERROR,
            message="Syntax error",
            line=10,
            column=5,
        )
        assert issue.severity == Severity.ERROR
        assert issue.message == "Syntax error"
        assert issue.line == 10
        assert issue.column == 5
        assert issue.fixable is False

    def test_create_warning_issue(self):
        """Warning-Issue erstellen."""
        issue = ValidationIssue(
            severity=Severity.WARNING,
            message="Unused variable",
            rule="W001",
            fixable=True,
        )
        assert issue.severity == Severity.WARNING
        assert issue.rule == "W001"
        assert issue.fixable is True

    def test_issue_str_representation(self):
        """String-Darstellung eines Issues."""
        issue = ValidationIssue(
            severity=Severity.ERROR,
            message="Test error",
            line=5,
        )
        str_repr = str(issue)
        assert "ERROR" in str_repr
        assert "Test error" in str_repr
        assert "5" in str_repr


class TestValidationResult:
    """Tests für ValidationResult Dataclass."""

    def test_success_result(self):
        """Erfolgreiche Validierung."""
        result = ValidationResult(
            file_path="/test/file.py",
            file_type="python",
            success=True,
            issues=[],
            time_ms=50,
        )
        assert result.success is True
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_result_with_errors(self):
        """Ergebnis mit Fehlern."""
        issues = [
            ValidationIssue(Severity.ERROR, "Error 1"),
            ValidationIssue(Severity.ERROR, "Error 2"),
            ValidationIssue(Severity.WARNING, "Warning 1"),
        ]
        result = ValidationResult(
            file_path="/test/file.py",
            file_type="python",
            success=False,
            issues=issues,
            time_ms=100,
        )
        assert result.success is False
        assert result.error_count == 2
        assert result.warning_count == 1

    def test_skipped_result(self):
        """Übersprungene Datei."""
        result = ValidationResult(
            file_path="/test/file.py",
            file_type="python",
            success=True,
            issues=[],
            skipped=True,
            skip_reason="No validator available",
        )
        assert result.skipped is True
        assert result.skip_reason == "No validator available"


class TestCompileResult:
    """Tests für CompileResult Dataclass."""

    def test_compile_result_success(self):
        """Erfolgreiches Compile-Ergebnis."""
        result = CompileResult(
            repo_path="/test/repo",
            files_checked=5,
            results=[],
            total_errors=0,
            total_warnings=0,
            time_ms=200,
        )
        assert result.success is True
        assert result.files_checked == 5

    def test_compile_result_with_errors(self):
        """Compile-Ergebnis mit Fehlern."""
        result = CompileResult(
            repo_path="/test/repo",
            files_checked=3,
            results=[],
            total_errors=2,
            total_warnings=1,
            time_ms=150,
        )
        assert result.success is False

    def test_compile_result_format(self):
        """Formatierung des Compile-Ergebnisses."""
        result = CompileResult(
            repo_path="/test/repo",
            files_checked=2,
            results=[],
            total_errors=0,
            total_warnings=0,
            time_ms=1000,
        )
        output = result.format(verbose=False)
        assert "Files checked: 2" in output
        assert "1.0s" in output or "Time:" in output


class MockValidator(BaseValidator):
    """Mock-Validator für Tests."""

    file_extensions = [".mock"]
    name = "mock"

    def __init__(self, should_fail: bool = False, issues: List[ValidationIssue] = None):
        super().__init__({})
        self.should_fail = should_fail
        self.mock_issues = issues or []

    async def validate(
        self, file_path: str, fix: bool = False, strict: bool = False
    ) -> ValidationResult:
        return ValidationResult(
            file_path=file_path,
            file_type="mock",
            success=not self.should_fail,
            issues=self.mock_issues,
            time_ms=10,
        )


class TestValidatorRegistry:
    """Tests für ValidatorRegistry."""

    def test_register_validator(self):
        """Validator registrieren."""
        registry = ValidatorRegistry()
        validator = MockValidator()
        registry.register(validator)

        assert ".mock" in registry.get_supported_extensions()

    def test_get_for_file(self):
        """Validator für Datei finden."""
        registry = ValidatorRegistry()
        validator = MockValidator()
        registry.register(validator)

        found = registry.get_for_file("test.mock")
        assert found is validator

    def test_no_validator_for_unknown_file(self):
        """Kein Validator für unbekannte Dateiendung."""
        registry = ValidatorRegistry()
        found = registry.get_for_file("test.unknown")
        assert found is None

    @pytest.mark.asyncio
    async def test_validate_file(self):
        """Einzelne Datei validieren."""
        registry = ValidatorRegistry()
        registry.register(MockValidator())

        # Temporäre Datei erstellen
        with tempfile.NamedTemporaryFile(suffix=".mock", delete=False) as f:
            f.write(b"test content")
            temp_path = f.name

        try:
            result = await registry.validate_file(temp_path)
            assert result is not None
            assert result.success is True
            assert result.file_type == "mock"
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_validate_file_no_validator(self):
        """Datei ohne passenden Validator."""
        registry = ValidatorRegistry()

        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"test")
            temp_path = f.name

        try:
            result = await registry.validate_file(temp_path)
            assert result is None
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_validate_files_batch(self):
        """Mehrere Dateien validieren."""
        registry = ValidatorRegistry()
        registry.register(MockValidator())

        # Temporäre Dateien erstellen
        temp_files = []
        for i in range(3):
            with tempfile.NamedTemporaryFile(suffix=".mock", delete=False) as f:
                f.write(f"test {i}".encode())
                temp_files.append(f.name)

        try:
            results = await registry.validate_files(temp_files)
            assert len(results) == 3
            assert all(r.success for r in results)
        finally:
            for path in temp_files:
                Path(path).unlink(missing_ok=True)


class TestSeverity:
    """Tests für Severity Enum."""

    def test_severity_values(self):
        """Severity-Werte prüfen."""
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"

    def test_severity_comparison(self):
        """Severity-Vergleiche."""
        assert Severity.ERROR == Severity.ERROR
        assert Severity.ERROR != Severity.WARNING


class TestBaseValidatorAbstract:
    """Tests für BaseValidator abstrakte Klasse."""

    def test_cannot_instantiate_base_validator(self):
        """BaseValidator kann nicht direkt instanziiert werden."""
        with pytest.raises(TypeError):
            BaseValidator({})

    def test_mock_validator_inheritance(self):
        """Mock-Validator erbt korrekt."""
        validator = MockValidator()
        assert hasattr(validator, 'validate')
        assert hasattr(validator, 'file_extensions')
        assert validator.name == "mock"
