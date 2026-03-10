"""
Basis-Klassen für das Validator-System.

Enthält:
- Severity: Schweregrad von Issues
- ValidationIssue: Einzelnes Problem
- ValidationResult: Ergebnis einer Datei-Validierung
- CompileResult: Gesamtergebnis
- BaseValidator: Abstrakte Basis-Klasse
- ValidatorRegistry: Registry für alle Validatoren
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """Schweregrad eines Validierungs-Problems."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    def __str__(self) -> str:
        return self.value


@dataclass
class ValidationIssue:
    """Ein einzelnes Validierungs-Problem."""
    severity: Severity
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    rule: Optional[str] = None
    fixable: bool = False
    source: Optional[str] = None  # Quell-Tool (z.B. "ruff", "javac")

    def __str__(self) -> str:
        parts = []

        # Location
        if self.line:
            loc = f"Line {self.line}"
            if self.column:
                loc += f":{self.column}"
            parts.append(loc)

        # Severity + Rule
        severity_str = self.severity.value.upper()
        if self.rule:
            severity_str += f" [{self.rule}]"
        parts.append(severity_str)

        # Message
        parts.append(self.message)

        return ": ".join(parts) if parts else self.message

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary."""
        return {
            "severity": self.severity.value,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "rule": self.rule,
            "fixable": self.fixable,
            "source": self.source,
        }


@dataclass
class ValidationResult:
    """Ergebnis einer einzelnen Datei-Validierung."""
    file_path: str
    file_type: str
    success: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    time_ms: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def errors(self) -> List[ValidationIssue]:
        """Gibt alle Errors zurück."""
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        """Gibt alle Warnings zurück."""
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def format(self, verbose: bool = False) -> str:
        """Formatiert das Ergebnis für Ausgabe."""
        # Status-Symbol
        if self.skipped:
            symbol = "⊘"
            status = f"Skipped: {self.skip_reason}"
        elif not self.issues:
            symbol = "✓"
            status = "OK"
        elif self.success:
            symbol = "⚠"
            status = f"{self.warning_count} warning{'s' if self.warning_count != 1 else ''}"
        else:
            symbol = "✗"
            status = f"{self.error_count} error{'s' if self.error_count != 1 else ''}"
            if self.warning_count:
                status += f", {self.warning_count} warning{'s' if self.warning_count != 1 else ''}"

        output = f"  {symbol} {Path(self.file_path).name} - {status}"

        # Details bei Fehlern/Warnungen
        if verbose or not self.success:
            for issue in self.issues:
                output += f"\n    {issue}"

        return output


@dataclass
class CompileResult:
    """Gesamtergebnis aller Validierungen."""
    repo_path: str
    files_checked: int
    results: List[ValidationResult] = field(default_factory=list)
    total_errors: int = 0
    total_warnings: int = 0
    time_ms: int = 0
    skipped_count: int = 0

    @property
    def success(self) -> bool:
        return self.total_errors == 0

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.success and not r.skipped)

    def format(self, verbose: bool = False) -> str:
        """Formatiert das Gesamtergebnis."""
        output = "=== Compile/Validate Results ===\n"
        output += f"Repository: {self.repo_path}\n"
        output += f"Files checked: {self.files_checked}\n"
        output += f"Time: {self.time_ms / 1000:.1f}s\n\n"

        # Nach Typ gruppieren
        by_type: Dict[str, List[ValidationResult]] = {}
        for result in self.results:
            if result.file_type not in by_type:
                by_type[result.file_type] = []
            by_type[result.file_type].append(result)

        # Pro Typ ausgeben
        for file_type, results in by_type.items():
            type_errors = sum(r.error_count for r in results)
            type_warnings = sum(r.warning_count for r in results)
            type_success = all(r.success for r in results)

            symbol = "✓" if type_success else ("⚠" if type_errors == 0 else "✗")
            output += f"{symbol} {file_type.capitalize()} ({len(results)} files)\n"

            for result in results:
                output += result.format(verbose) + "\n"

            output += "\n"

        # Summary
        output += "═" * 45 + "\n"
        summary_parts = [f"{self.passed_count} passed"]
        if self.total_warnings:
            summary_parts.append(f"{self.total_warnings} warning{'s' if self.total_warnings != 1 else ''}")
        if self.total_errors:
            summary_parts.append(f"{self.total_errors} error{'s' if self.total_errors != 1 else ''}")
        if self.skipped_count:
            summary_parts.append(f"{self.skipped_count} skipped")

        output += f"Summary: {', '.join(summary_parts)}\n"
        output += "═" * 45

        return output


class BaseValidator(ABC):
    """
    Abstrakte Basis-Klasse für alle Validatoren.

    Jeder Validator muss implementieren:
    - file_extensions: Liste unterstützter Dateiendungen
    - name: Eindeutiger Name des Validators
    - validate(): Async Validierungsmethode
    """

    file_extensions: List[str] = []
    name: str = "base"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: Validator-spezifische Konfiguration
        """
        self.config = config or {}

    def can_validate(self, file_path: str) -> bool:
        """Prüft ob dieser Validator die Datei verarbeiten kann."""
        ext = Path(file_path).suffix.lower()
        return ext in self.file_extensions

    @abstractmethod
    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """
        Validiert eine Datei.

        Args:
            file_path: Pfad zur Datei
            fix: Auto-Fix anwenden wenn möglich
            strict: Warnings als Errors behandeln

        Returns:
            ValidationResult mit allen gefundenen Issues
        """
        pass

    def _create_result(
        self,
        file_path: str,
        issues: List[ValidationIssue],
        start_time: float,
        strict: bool = False,
    ) -> ValidationResult:
        """
        Erstellt ein ValidationResult.

        Args:
            file_path: Pfad zur Datei
            issues: Gefundene Issues
            start_time: Startzeit (time.time())
            strict: Bei True werden Warnings zu Errors
        """
        # Strict Mode: Warnings → Errors
        if strict:
            for issue in issues:
                if issue.severity == Severity.WARNING:
                    issue.severity = Severity.ERROR

        has_errors = any(i.severity == Severity.ERROR for i in issues)

        return ValidationResult(
            file_path=file_path,
            file_type=self.name,
            success=not has_errors,
            issues=issues,
            time_ms=int((time.time() - start_time) * 1000),
        )

    def _create_skipped_result(
        self,
        file_path: str,
        reason: str,
    ) -> ValidationResult:
        """Erstellt ein übersprungenes Result."""
        return ValidationResult(
            file_path=file_path,
            file_type=self.name,
            success=True,
            skipped=True,
            skip_reason=reason,
        )

    async def _run_command(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        timeout: int = 30,
    ) -> tuple[int, str, str]:
        """
        Führt einen Shell-Befehl aus.

        Returns:
            Tuple (return_code, stdout, stderr)
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            return (
                process.returncode or 0,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            if process:
                process.kill()
            return (-1, "", f"Timeout after {timeout}s")
        except FileNotFoundError:
            return (-1, "", f"Command not found: {cmd[0]}")
        except Exception as e:
            return (-1, "", str(e))


class ValidatorRegistry:
    """
    Registry für alle verfügbaren Validatoren.
    """

    def __init__(self):
        self._validators: Dict[str, BaseValidator] = {}
        self._extension_map: Dict[str, str] = {}  # ext -> validator name

    def register(self, validator: BaseValidator) -> None:
        """Registriert einen Validator."""
        self._validators[validator.name] = validator

        # Extension-Mapping aktualisieren
        for ext in validator.file_extensions:
            self._extension_map[ext.lower()] = validator.name

    def get(self, name: str) -> Optional[BaseValidator]:
        """Gibt einen Validator nach Name zurück."""
        return self._validators.get(name)

    def get_for_file(self, file_path: str) -> Optional[BaseValidator]:
        """Gibt den passenden Validator für eine Datei zurück."""
        ext = Path(file_path).suffix.lower()
        validator_name = self._extension_map.get(ext)
        if validator_name:
            return self._validators.get(validator_name)
        return None

    def list_validators(self) -> List[BaseValidator]:
        """Listet alle registrierten Validatoren."""
        return list(self._validators.values())

    def get_supported_extensions(self) -> List[str]:
        """Gibt alle unterstützten Dateiendungen zurück."""
        return list(self._extension_map.keys())

    async def validate_file(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> Optional[ValidationResult]:
        """
        Validiert eine einzelne Datei mit dem passenden Validator.

        Returns:
            ValidationResult oder None wenn kein Validator gefunden
        """
        validator = self.get_for_file(file_path)
        if not validator:
            return None

        return await validator.validate(file_path, fix=fix, strict=strict)

    async def validate_files(
        self,
        file_paths: List[str],
        fix: bool = False,
        strict: bool = False,
        types: Optional[List[str]] = None,
    ) -> List[ValidationResult]:
        """
        Validiert mehrere Dateien parallel.

        Args:
            file_paths: Liste der Dateipfade
            fix: Auto-Fix anwenden
            strict: Warnings als Errors
            types: Nur diese Validator-Typen verwenden (None = alle)

        Returns:
            Liste von ValidationResults
        """
        tasks = []

        for file_path in file_paths:
            validator = self.get_for_file(file_path)
            if not validator:
                continue

            # Type-Filter
            if types and validator.name not in types:
                continue

            tasks.append(validator.validate(file_path, fix=fix, strict=strict))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Exceptions in Fehler-Results umwandeln
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(ValidationResult(
                    file_path=file_paths[i] if i < len(file_paths) else "unknown",
                    file_type="unknown",
                    success=False,
                    issues=[ValidationIssue(
                        severity=Severity.ERROR,
                        message=str(result),
                    )],
                ))
            else:
                final_results.append(result)

        return final_results
