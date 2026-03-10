"""
SQL Validator - Syntax-Check für SQL-Dateien.

Prüfungen (ohne DB-Verbindung):
1. Grundlegende SQL-Grammatik
2. Statement-Terminierung (;)
3. Klammer-Balance
4. String-Literal-Balance
5. Best Practices (SELECT *, fehlende WHERE bei UPDATE/DELETE)

Unterstützte Dialekte:
- DB2 (default)
- PostgreSQL
- MySQL
- ANSI SQL
"""

import logging
import re
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


class SQLValidator(BaseValidator):
    """Validator für SQL-Dateien."""

    file_extensions = [".sql", ".ddl"]
    name = "sql"

    # SQL Keywords pro Dialekt
    KEYWORDS = {
        "common": {
            "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "EXISTS",
            "LIKE", "BETWEEN", "IS", "NULL", "AS", "ON", "JOIN", "LEFT",
            "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "UNION", "EXCEPT",
            "INTERSECT", "GROUP", "BY", "HAVING", "ORDER", "ASC", "DESC",
            "LIMIT", "OFFSET", "FETCH", "FIRST", "NEXT", "ROWS", "ONLY",
            "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
            "CREATE", "ALTER", "DROP", "TABLE", "INDEX", "VIEW", "SCHEMA",
            "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "UNIQUE", "CHECK",
            "DEFAULT", "CONSTRAINT", "CASCADE", "RESTRICT", "NULLS",
            "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "COALESCE",
            "DISTINCT", "ALL", "ANY", "SOME", "TRUE", "FALSE",
        },
        "db2": {
            "FETCH", "WITH", "UR", "CS", "RS", "RR", "OPTIMIZE", "FOR",
            "CURRENT", "DATE", "TIME", "TIMESTAMP", "TIMEZONE",
            "DECLARE", "CURSOR", "OPEN", "CLOSE", "CONTINUE",
            "SQLCODE", "SQLSTATE", "ROWSET", "SENSITIVE", "INSENSITIVE",
            "SCROLL", "HOLD", "RETURN", "RESULT", "SETS", "DYNAMIC",
            "LOCATOR", "ASSOCIATE", "ALLOCATE", "DESCRIBE", "PREPARE",
        },
        "postgres": {
            "RETURNING", "CONFLICT", "NOTHING", "EXCLUDED", "ILIKE",
            "ARRAY", "JSONB", "JSON", "LATERAL", "MATERIALIZED",
            "CONCURRENTLY", "TABLESPACE", "EXTENSION", "SEQUENCE",
        },
        "mysql": {
            "AUTO_INCREMENT", "ENGINE", "CHARSET", "COLLATE", "UNSIGNED",
            "ZEROFILL", "BINARY", "VARBINARY", "BLOB", "TEXT", "ENUM",
            "IGNORE", "REPLACE", "DUPLICATE", "STRAIGHT_JOIN", "USE",
        },
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        # Konfiguration
        self.dialect = self.config.get("dialect", "db2")
        self.check_best_practices = self.config.get("check_best_practices", True)
        self.require_semicolon = self.config.get("require_semicolon", True)

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """Validiert eine SQL-Datei."""
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

        # Kommentare für Analyse entfernen (aber Positionen merken)
        clean_content = self._remove_comments(content)

        # 1. Klammer-Balance
        balance_issues = self._check_bracket_balance(clean_content, content)
        issues.extend(balance_issues)

        # 2. String-Literal-Balance
        string_issues = self._check_string_literals(clean_content, content)
        issues.extend(string_issues)

        # 3. Statement-Analyse
        statements = self._split_statements(clean_content)
        for stmt, line_num in statements:
            stmt_issues = self._validate_statement(stmt, line_num)
            issues.extend(stmt_issues)

        # 4. Best Practices
        if self.check_best_practices:
            bp_issues = self._check_best_practices_all(clean_content, content)
            issues.extend(bp_issues)

        return self._create_result(file_path, issues, start, strict)

    def _remove_comments(self, content: str) -> str:
        """Entfernt SQL-Kommentare aber behält Zeilenstruktur."""
        # Einzeilige Kommentare: -- ...
        content = re.sub(r"--.*$", "", content, flags=re.MULTILINE)

        # Block-Kommentare: /* ... */
        content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)

        return content

    def _check_bracket_balance(
        self,
        clean_content: str,
        original: str
    ) -> List[ValidationIssue]:
        """Prüft Klammer-Balance."""
        issues = []

        brackets = {"(": ")", "[": "]", "{": "}"}
        stack: List[Tuple[str, int]] = []

        line_num = 1
        for i, char in enumerate(clean_content):
            if char == "\n":
                line_num += 1
            elif char in brackets:
                stack.append((char, line_num))
            elif char in brackets.values():
                expected = [k for k, v in brackets.items() if v == char][0]
                if not stack:
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        message=f"Unmatched closing bracket '{char}'",
                        line=line_num,
                        source="sql",
                    ))
                elif stack[-1][0] != expected:
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        message=f"Mismatched brackets: expected '{brackets[stack[-1][0]]}', found '{char}'",
                        line=line_num,
                        source="sql",
                    ))
                else:
                    stack.pop()

        # Nicht geschlossene Klammern
        for bracket, line in stack:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=f"Unclosed bracket '{bracket}'",
                line=line,
                source="sql",
            ))

        return issues

    def _check_string_literals(
        self,
        clean_content: str,
        original: str
    ) -> List[ValidationIssue]:
        """Prüft String-Literal-Balance."""
        issues = []

        in_string = False
        string_char = None
        string_start_line = 0
        line_num = 1

        i = 0
        while i < len(clean_content):
            char = clean_content[i]

            if char == "\n":
                line_num += 1
            elif not in_string and char in ("'", '"'):
                in_string = True
                string_char = char
                string_start_line = line_num
            elif in_string and char == string_char:
                # Prüfe auf Escape (doppeltes Quote)
                if i + 1 < len(clean_content) and clean_content[i + 1] == string_char:
                    i += 1  # Skip escaped quote
                else:
                    in_string = False
                    string_char = None

            i += 1

        if in_string:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=f"Unclosed string literal starting with '{string_char}'",
                line=string_start_line,
                source="sql",
            ))

        return issues

    def _split_statements(self, content: str) -> List[Tuple[str, int]]:
        """Teilt Content in Statements auf."""
        statements = []
        current = []
        line_num = 1
        stmt_start_line = 1

        for line in content.split("\n"):
            stripped = line.strip()

            if not stripped:
                line_num += 1
                continue

            if not current:
                stmt_start_line = line_num

            current.append(line)

            # Statement endet mit ;
            if stripped.endswith(";"):
                stmt = "\n".join(current)
                statements.append((stmt.strip(), stmt_start_line))
                current = []

            line_num += 1

        # Letztes Statement ohne ;
        if current:
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append((stmt, stmt_start_line))

        return statements

    def _validate_statement(
        self,
        stmt: str,
        line_num: int
    ) -> List[ValidationIssue]:
        """Validiert ein einzelnes SQL-Statement."""
        issues = []
        stmt_upper = stmt.upper().strip()

        # Leeres Statement
        if not stmt_upper or stmt_upper == ";":
            return []

        # Statement-Typ erkennen
        valid_starts = (
            "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE",
            "CREATE", "ALTER", "DROP", "TRUNCATE",
            "GRANT", "REVOKE", "COMMIT", "ROLLBACK",
            "BEGIN", "END", "DECLARE", "SET", "CALL",
            "WITH", "COMMENT", "ANALYZE", "EXPLAIN",
            "LOCK", "UNLOCK", "USE", "SHOW", "DESCRIBE",
        )

        first_word = stmt_upper.split()[0] if stmt_upper.split() else ""

        if first_word not in valid_starts:
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                message=f"Statement starts with unknown keyword: {first_word}",
                line=line_num,
                source="sql",
            ))

        # Semicolon am Ende
        if self.require_semicolon and not stmt.strip().endswith(";"):
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                message="Statement missing semicolon at end",
                line=line_num,
                rule="missing-semicolon",
                source="sql",
            ))

        return issues

    def _check_best_practices_all(
        self,
        clean_content: str,
        original: str
    ) -> List[ValidationIssue]:
        """Prüft Best Practices über gesamten Content."""
        issues = []
        upper_content = clean_content.upper()

        # SELECT *
        for match in re.finditer(r"\bSELECT\s+\*", upper_content):
            line_num = clean_content[:match.start()].count("\n") + 1
            issues.append(ValidationIssue(
                severity=Severity.WARNING,
                message="SELECT * is not recommended - specify columns explicitly",
                line=line_num,
                rule="select-star",
                source="sql",
            ))

        # UPDATE ohne WHERE
        for match in re.finditer(r"\bUPDATE\s+\w+\s+SET\s+[^;]+(?:;|$)", upper_content, re.DOTALL):
            stmt = match.group(0)
            if "WHERE" not in stmt:
                line_num = clean_content[:match.start()].count("\n") + 1
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message="UPDATE without WHERE clause - will affect all rows",
                    line=line_num,
                    rule="update-no-where",
                    source="sql",
                ))

        # DELETE ohne WHERE
        for match in re.finditer(r"\bDELETE\s+FROM\s+\w+[^;]*(?:;|$)", upper_content, re.DOTALL):
            stmt = match.group(0)
            if "WHERE" not in stmt:
                line_num = clean_content[:match.start()].count("\n") + 1
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message="DELETE without WHERE clause - will delete all rows",
                    line=line_num,
                    rule="delete-no-where",
                    source="sql",
                ))

        return issues
