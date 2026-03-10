"""
Python Validator - Syntax-Check, Linting und Type-Checking.

Prüfungen:
1. Syntax (py_compile) - Pflicht
2. Linting (ruff oder flake8) - Optional
3. Type-Check (mypy) - Optional

Auto-Fix:
- ruff --fix
"""

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.validators.base import (
    BaseValidator,
    Severity,
    ValidationIssue,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class PythonValidator(BaseValidator):
    """Validator für Python-Dateien."""

    file_extensions = [".py"]
    name = "python"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        # Konfiguration mit Defaults
        self.linter = self.config.get("linter", "ruff")  # ruff | flake8 | none
        self.type_checker = self.config.get("type_checker", "none")  # mypy | none
        self.auto_fix_tool = self.config.get("auto_fix_tool", "ruff")
        self.ignore_rules: List[str] = self.config.get("ignore_rules", [])

        # Tool-Pfade ermitteln
        self._ruff_path = shutil.which("ruff")
        self._flake8_path = shutil.which("flake8")
        self._mypy_path = shutil.which("mypy")

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """Validiert eine Python-Datei."""
        start = time.time()
        issues: List[ValidationIssue] = []

        # Datei existiert?
        if not Path(file_path).exists():
            return ValidationResult(
                file_path=file_path,
                file_type=self.name,
                success=False,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    message=f"File not found: {file_path}",
                )],
            )

        # 1. Syntax Check (immer)
        syntax_issues = await self._check_syntax(file_path)
        issues.extend(syntax_issues)

        # Bei Syntax-Fehlern: Sofort abbrechen (Linter würden auch fehlschlagen)
        if any(i.severity == Severity.ERROR for i in syntax_issues):
            return self._create_result(file_path, issues, start, strict)

        # 2. Auto-Fix (vor Linting)
        if fix and self._can_autofix():
            await self._run_autofix(file_path)

        # 3. Linting
        if self.linter != "none":
            lint_issues = await self._run_linter(file_path)
            issues.extend(lint_issues)

        # 4. Type Checking
        if self.type_checker != "none" and self._mypy_path:
            type_issues = await self._run_type_checker(file_path)
            issues.extend(type_issues)

        # Ignore-Rules filtern
        if self.ignore_rules:
            issues = [i for i in issues if i.rule not in self.ignore_rules]

        return self._create_result(file_path, issues, start, strict)

    async def _check_syntax(self, file_path: str) -> List[ValidationIssue]:
        """Prüft Python-Syntax mit py_compile."""
        import py_compile

        try:
            py_compile.compile(file_path, doraise=True)
            return []
        except py_compile.PyCompileError as e:
            # Fehlerdetails extrahieren
            line = e.lineno if hasattr(e, 'lineno') else None
            msg = str(e.msg) if hasattr(e, 'msg') else str(e)

            return [ValidationIssue(
                severity=Severity.ERROR,
                message=msg,
                line=line,
                source="py_compile",
            )]
        except SyntaxError as e:
            return [ValidationIssue(
                severity=Severity.ERROR,
                message=str(e.msg) if e.msg else str(e),
                line=e.lineno,
                column=e.offset,
                source="python",
            )]
        except Exception as e:
            return [ValidationIssue(
                severity=Severity.ERROR,
                message=f"Syntax check failed: {e}",
                source="py_compile",
            )]

    def _can_autofix(self) -> bool:
        """Prüft ob Auto-Fix verfügbar ist."""
        if self.auto_fix_tool == "ruff":
            return self._ruff_path is not None
        return False

    async def _run_autofix(self, file_path: str) -> bool:
        """Führt Auto-Fix aus."""
        if self.auto_fix_tool == "ruff" and self._ruff_path:
            cmd = [self._ruff_path, "check", "--fix", "--quiet", file_path]
            returncode, _, _ = await self._run_command(cmd)
            return returncode == 0
        return False

    async def _run_linter(self, file_path: str) -> List[ValidationIssue]:
        """Führt den konfigurierten Linter aus."""
        if self.linter == "ruff" and self._ruff_path:
            return await self._run_ruff(file_path)
        elif self.linter == "flake8" and self._flake8_path:
            return await self._run_flake8(file_path)
        return []

    async def _run_ruff(self, file_path: str) -> List[ValidationIssue]:
        """Führt ruff aus und parst die Ausgabe."""
        cmd = [
            self._ruff_path,
            "check",
            "--output-format=text",
            file_path,
        ]

        returncode, stdout, stderr = await self._run_command(cmd)

        if returncode == 0:
            return []

        issues = []

        # Ruff Output Format: file.py:line:col: CODE message
        pattern = re.compile(r"^(.+):(\d+):(\d+): ([A-Z]\d+) (.+)$", re.MULTILINE)

        for match in pattern.finditer(stdout):
            rule = match.group(4)
            message = match.group(5)

            # Severity basierend auf Rule-Prefix
            if rule.startswith("E") or rule.startswith("F"):
                severity = Severity.ERROR
            else:
                severity = Severity.WARNING

            issues.append(ValidationIssue(
                severity=severity,
                message=message,
                line=int(match.group(2)),
                column=int(match.group(3)),
                rule=rule,
                fixable=rule.startswith(("I", "UP", "B")),  # Import, Upgrade, Bugbear
                source="ruff",
            ))

        return issues

    async def _run_flake8(self, file_path: str) -> List[ValidationIssue]:
        """Führt flake8 aus und parst die Ausgabe."""
        cmd = [
            self._flake8_path,
            "--format=%(path)s:%(row)d:%(col)d: %(code)s %(text)s",
            file_path,
        ]

        returncode, stdout, stderr = await self._run_command(cmd)

        if returncode == 0:
            return []

        issues = []

        # Flake8 Output Format: file.py:line:col: CODE message
        pattern = re.compile(r"^(.+):(\d+):(\d+): ([A-Z]\d+) (.+)$", re.MULTILINE)

        for match in pattern.finditer(stdout):
            rule = match.group(4)
            message = match.group(5)

            # E = Error, W = Warning, F = Fatal, C = Convention
            if rule.startswith(("E", "F")):
                severity = Severity.ERROR
            else:
                severity = Severity.WARNING

            issues.append(ValidationIssue(
                severity=severity,
                message=message,
                line=int(match.group(2)),
                column=int(match.group(3)),
                rule=rule,
                source="flake8",
            ))

        return issues

    async def _run_type_checker(self, file_path: str) -> List[ValidationIssue]:
        """Führt mypy aus und parst die Ausgabe."""
        if not self._mypy_path:
            return []

        cmd = [
            self._mypy_path,
            "--no-error-summary",
            "--no-color-output",
            "--show-column-numbers",
            file_path,
        ]

        returncode, stdout, stderr = await self._run_command(cmd, timeout=60)

        if returncode == 0:
            return []

        issues = []

        # Mypy Output: file.py:line:col: severity: message
        pattern = re.compile(
            r"^(.+):(\d+):(\d+): (error|warning|note): (.+)$",
            re.MULTILINE
        )

        for match in pattern.finditer(stdout):
            severity_str = match.group(4)
            message = match.group(5)

            if severity_str == "error":
                severity = Severity.ERROR
            elif severity_str == "warning":
                severity = Severity.WARNING
            else:
                severity = Severity.INFO

            issues.append(ValidationIssue(
                severity=severity,
                message=message,
                line=int(match.group(2)),
                column=int(match.group(3)),
                source="mypy",
            ))

        return issues
