"""
SQLJ Validator - Validierung von SQLJ-Dateien.

SQLJ kombiniert Java mit eingebettetem SQL. Prüfungen:
1. Java-Syntax des Host-Codes
2. SQL-Syntax der eingebetteten Statements
3. SQLJ Translator (wenn verfügbar)

SQLJ Statement Format:
  #sql { SELECT * FROM table WHERE col = :hostVar };
  #sql iter = { SELECT col FROM table };
"""

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.validators.base import (
    BaseValidator,
    Severity,
    ValidationIssue,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class SQLJValidator(BaseValidator):
    """Validator für SQLJ-Dateien."""

    file_extensions = [".sqlj"]
    name = "sqlj"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        # Konfiguration
        self.sqlj_path = self.config.get("sqlj_path", "")
        self.validate_sql = self.config.get("validate_sql", True)
        self.sql_dialect = self.config.get("sql_dialect", "db2")

        # SQLJ Translator suchen
        self._sqlj_cmd = self._find_sqlj()

    def _find_sqlj(self) -> Optional[str]:
        """Findet den SQLJ Translator."""
        if self.sqlj_path and Path(self.sqlj_path).exists():
            return self.sqlj_path

        # Im PATH suchen
        return shutil.which("sqlj")

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """Validiert eine SQLJ-Datei."""
        start = time.time()
        issues: List[ValidationIssue] = []

        # Datei existiert?
        path = Path(file_path)
        if not path.exists():
            return ValidationResult(
                file_path=file_path,
                file_type=self.name,
                success=False,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    message=f"File not found: {file_path}",
                )],
            )

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ValidationResult(
                file_path=file_path,
                file_type=self.name,
                success=False,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    message=f"Cannot read file: {e}",
                )],
            )

        # 1. SQLJ Statements extrahieren und prüfen
        sql_issues = self._validate_sqlj_statements(content, file_path)
        issues.extend(sql_issues)

        # 2. Java-Syntax prüfen (ohne SQL)
        java_issues = await self._validate_java_syntax(content, file_path)
        issues.extend(java_issues)

        # 3. SQLJ Translator (wenn verfügbar)
        if self._sqlj_cmd:
            translator_issues = await self._run_sqlj_translator(file_path)
            issues.extend(translator_issues)

        return self._create_result(file_path, issues, start, strict)

    def _validate_sqlj_statements(
        self,
        content: str,
        file_path: str
    ) -> List[ValidationIssue]:
        """Validiert eingebettete SQL-Statements."""
        issues = []

        # SQLJ Statement Pattern: #sql [iter =] { ... };
        # Multiline möglich
        pattern = re.compile(
            r"#sql\s+(?:(\w+)\s*=\s*)?\{([^}]*)\}\s*;",
            re.DOTALL | re.MULTILINE
        )

        for match in pattern.finditer(content):
            sql_content = match.group(2).strip()
            start_pos = match.start()
            line_num = content[:start_pos].count("\n") + 1

            # SQL-Statement validieren
            sql_issues = self._validate_sql_statement(sql_content, line_num)
            issues.extend(sql_issues)

        # Ungeschlossene #sql Blöcke finden
        open_pattern = re.compile(r"#sql\s+(?:\w+\s*=\s*)?\{(?![^}]*\})", re.DOTALL)
        for match in open_pattern.finditer(content):
            line_num = content[:match.start()].count("\n") + 1
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message="Unclosed #sql block - missing closing '}'",
                line=line_num,
                source="sqlj",
            ))

        return issues

    def _validate_sql_statement(
        self,
        sql: str,
        line_num: int
    ) -> List[ValidationIssue]:
        """Validiert ein einzelnes SQL-Statement."""
        issues = []
        sql_upper = sql.upper().strip()

        # Leeres Statement
        if not sql_upper:
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                message="Empty SQL statement",
                line=line_num,
                source="sqlj",
            ))
            return issues

        # Basis-SQL-Keywords prüfen
        valid_starts = (
            "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE",
            "CALL", "VALUES", "WITH", "FETCH", "OPEN", "CLOSE",
        )
        if not any(sql_upper.startswith(kw) for kw in valid_starts):
            # Könnte ein Iterator-Context sein
            if not sql_upper.startswith(("DECLARE", "BEGIN", "END")):
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message=f"SQL statement doesn't start with known keyword",
                    line=line_num,
                    source="sqlj",
                ))

        # Klammer-Balance
        if sql.count("(") != sql.count(")"):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message="Unbalanced parentheses in SQL statement",
                line=line_num,
                source="sqlj",
            ))

        # String-Literal-Balance
        if sql.count("'") % 2 != 0:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message="Unclosed string literal in SQL statement",
                line=line_num,
                source="sqlj",
            ))

        # Host-Variablen Syntax (:var oder :IN var)
        host_var_pattern = re.compile(r":(\w+)")
        for match in host_var_pattern.finditer(sql):
            var_name = match.group(1)
            # Bekannte Schlüsselwörter ausschließen
            if var_name.upper() in ("IN", "OUT", "INOUT"):
                continue

        # SELECT * Warnung
        if "SELECT *" in sql_upper or "SELECT  *" in sql_upper:
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                message="SELECT * is not recommended - specify columns explicitly",
                line=line_num,
                rule="select-star",
                source="sqlj",
            ))

        return issues

    async def _validate_java_syntax(
        self,
        content: str,
        file_path: str
    ) -> List[ValidationIssue]:
        """Validiert Java-Syntax (nach Entfernen der SQL-Blöcke)."""
        issues = []

        # SQL-Blöcke durch Platzhalter ersetzen
        java_content = re.sub(
            r"#sql\s+(?:\w+\s*=\s*)?\{[^}]*\}\s*;",
            "/* SQLJ_PLACEHOLDER */;",
            content,
            flags=re.DOTALL
        )

        # Basis-Java-Syntax-Checks
        # Klammer-Balance
        if java_content.count("{") != java_content.count("}"):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message="Unbalanced curly braces in Java code",
                source="sqlj-java",
            ))

        if java_content.count("(") != java_content.count(")"):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message="Unbalanced parentheses in Java code",
                source="sqlj-java",
            ))

        # Class/Interface-Definition vorhanden?
        if not re.search(r"\b(class|interface|enum)\s+\w+", java_content):
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                message="No class, interface or enum definition found",
                source="sqlj-java",
            ))

        return issues

    async def _run_sqlj_translator(self, file_path: str) -> List[ValidationIssue]:
        """Führt den SQLJ Translator im Check-Modus aus."""
        if not self._sqlj_cmd:
            return []

        # SQLJ nur für Syntax-Check (keine Serialized Profile Generation)
        cmd = [
            self._sqlj_cmd,
            "-compile=false",      # Nicht kompilieren
            "-ser2class=false",    # Keine .ser Konvertierung
            "-status=false",       # Keine Status-Meldungen
            file_path,
        ]

        returncode, stdout, stderr = await self._run_command(cmd, timeout=60)

        if returncode == 0:
            return []

        # Fehler parsen
        return self._parse_sqlj_output(stderr or stdout, file_path)

    def _parse_sqlj_output(self, output: str, file_path: str) -> List[ValidationIssue]:
        """Parst SQLJ Translator Ausgabe."""
        issues = []

        # SQLJ Fehler-Format variiert je nach Version
        # Typisch: Error at line X, column Y: message
        pattern = re.compile(
            r"(?:Error|Warning) at line (\d+)(?:, column (\d+))?: (.+)",
            re.IGNORECASE
        )

        for match in pattern.finditer(output):
            line = int(match.group(1))
            column = int(match.group(2)) if match.group(2) else None
            message = match.group(3)

            severity = Severity.ERROR if "error" in match.group(0).lower() else Severity.WARNING

            issues.append(ValidationIssue(
                severity=severity,
                message=message,
                line=line,
                column=column,
                source="sqlj-translator",
            ))

        # Generische Fehlermeldungen
        if not issues and returncode != 0:
            # Erste Zeile als Fehler nehmen
            first_line = output.strip().split("\n")[0] if output.strip() else "SQLJ translation failed"
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=first_line,
                source="sqlj-translator",
            ))

        return issues
