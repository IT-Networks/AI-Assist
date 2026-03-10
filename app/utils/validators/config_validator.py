"""
Config Validator - Validierung von Konfigurationsdateien.

Unterstützte Formate:
- YAML (.yaml, .yml)
- JSON (.json)
- Properties (.properties)
- TOML (.toml)

Prüfungen:
1. Syntax/Parsing
2. Schema-Validierung (wenn JSON-Schema vorhanden)
3. Duplikat-Keys
"""

import json
import logging
import re
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


class ConfigValidator(BaseValidator):
    """Validator für Konfigurationsdateien."""

    file_extensions = [".yaml", ".yml", ".json", ".properties", ".toml", ".cfg", ".conf", ".ini"]
    name = "config"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        # Verfügbare Parser prüfen
        self._has_yaml = self._check_yaml()
        self._has_toml = self._check_toml()

    def _check_yaml(self) -> bool:
        """Prüft ob PyYAML installiert ist."""
        try:
            import yaml
            return True
        except ImportError:
            return False

    def _check_toml(self) -> bool:
        """Prüft ob TOML-Parser verfügbar ist."""
        try:
            import tomllib  # Python 3.11+
            return True
        except ImportError:
            try:
                import toml
                return True
            except ImportError:
                return False

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """Validiert eine Konfigurationsdatei."""
        start = time.time()
        issues: List[ValidationIssue] = []

        path = Path(file_path)
        ext = path.suffix.lower()

        # Datei existiert?
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

        # Format-spezifische Validierung
        if ext in (".yaml", ".yml"):
            issues = self._validate_yaml(content, file_path)
        elif ext == ".json":
            issues = self._validate_json(content, file_path)
        elif ext == ".properties":
            issues = self._validate_properties(content, file_path)
        elif ext == ".toml":
            issues = self._validate_toml(content, file_path)
        elif ext in (".cfg", ".conf", ".ini"):
            issues = self._validate_ini(content, file_path)
        else:
            issues = [ValidationIssue(
                severity=Severity.WARNING,
                message=f"Unknown config format: {ext}",
                source="config",
            )]

        return self._create_result(file_path, issues, start, strict)

    def _validate_yaml(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert YAML-Syntax."""
        if not self._has_yaml:
            return [ValidationIssue(
                severity=Severity.WARNING,
                message="PyYAML not installed - cannot validate YAML. Install with: pip install pyyaml",
                source="config",
            )]

        import yaml

        issues = []

        try:
            # Strict mode: Duplikate erkennen
            class DuplicateKeyChecker(yaml.SafeLoader):
                pass

            def check_duplicates(loader, node, deep=False):
                mapping = {}
                for key_node, value_node in node.value:
                    key = loader.construct_object(key_node, deep=deep)
                    if key in mapping:
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            message=f"Duplicate key: {key}",
                            line=key_node.start_mark.line + 1 if key_node.start_mark else None,
                            rule="duplicate-key",
                            source="yaml",
                        ))
                    mapping[key] = loader.construct_object(value_node, deep=deep)
                return mapping

            DuplicateKeyChecker.add_constructor(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                check_duplicates
            )

            # Parsen
            yaml.load(content, Loader=DuplicateKeyChecker)

        except yaml.YAMLError as e:
            line = None
            if hasattr(e, 'problem_mark') and e.problem_mark:
                line = e.problem_mark.line + 1

            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=str(e),
                line=line,
                source="yaml",
            ))

        return issues

    def _validate_json(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert JSON-Syntax."""
        issues = []

        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=e.msg,
                line=e.lineno,
                column=e.colno,
                source="json",
            ))

        # Duplikat-Keys (JSON erlaubt sie, aber warnen)
        dup_issues = self._check_json_duplicates(content)
        issues.extend(dup_issues)

        return issues

    def _check_json_duplicates(self, content: str) -> List[ValidationIssue]:
        """Prüft auf doppelte Keys in JSON."""
        issues = []

        # Einfache Regex-basierte Prüfung
        # Findet "key": Muster und prüft auf Duplikate pro Objekt-Ebene
        key_pattern = re.compile(r'"(\w+)"\s*:')

        # Stack für Objekt-Ebenen
        depth = 0
        keys_at_depth: Dict[int, set] = {0: set()}
        line_num = 1

        for line in content.split("\n"):
            for char in line:
                if char == "{":
                    depth += 1
                    keys_at_depth[depth] = set()
                elif char == "}":
                    if depth > 0:
                        del keys_at_depth[depth]
                        depth -= 1

            for match in key_pattern.finditer(line):
                key = match.group(1)
                if key in keys_at_depth.get(depth, set()):
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        message=f"Duplicate key: {key}",
                        line=line_num,
                        rule="duplicate-key",
                        source="json",
                    ))
                else:
                    keys_at_depth.setdefault(depth, set()).add(key)

            line_num += 1

        return issues

    def _validate_properties(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert Java Properties Format."""
        issues = []
        seen_keys = set()
        line_num = 0
        continuation = False

        for line in content.split("\n"):
            line_num += 1
            stripped = line.strip()

            # Leere Zeilen und Kommentare überspringen
            if not stripped or stripped.startswith(("#", "!")):
                continue

            # Fortsetzungszeile
            if continuation:
                if not stripped.endswith("\\"):
                    continuation = False
                continue

            if stripped.endswith("\\"):
                continuation = True

            # Key=Value oder Key:Value oder Key Value
            match = re.match(r"^([^=:\s]+)\s*[=:\s]\s*(.*)$", stripped)

            if not match:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message="Invalid property format - expected 'key=value' or 'key:value'",
                    line=line_num,
                    source="properties",
                ))
                continue

            key = match.group(1)

            # Duplikat-Check
            if key in seen_keys:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message=f"Duplicate property key: {key}",
                    line=line_num,
                    rule="duplicate-key",
                    source="properties",
                ))
            seen_keys.add(key)

        return issues

    def _validate_toml(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert TOML-Syntax."""
        if not self._has_toml:
            return [ValidationIssue(
                severity=Severity.WARNING,
                message="TOML parser not available. Python 3.11+ or 'pip install toml' required.",
                source="config",
            )]

        issues = []

        try:
            # Python 3.11+
            try:
                import tomllib
                tomllib.loads(content)
            except ImportError:
                import toml
                toml.loads(content)

        except Exception as e:
            # Zeilennummer aus Fehlermeldung extrahieren
            line = None
            error_str = str(e)
            line_match = re.search(r"line (\d+)", error_str, re.IGNORECASE)
            if line_match:
                line = int(line_match.group(1))

            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=error_str,
                line=line,
                source="toml",
            ))

        return issues

    def _validate_ini(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert INI-Format."""
        import configparser

        issues = []

        parser = configparser.ConfigParser()
        try:
            parser.read_string(content)
        except configparser.Error as e:
            # Zeilennummer extrahieren
            line = None
            error_str = str(e)
            line_match = re.search(r"line (\d+)", error_str, re.IGNORECASE)
            if line_match:
                line = int(line_match.group(1))

            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=error_str,
                line=line,
                source="ini",
            ))

        return issues
