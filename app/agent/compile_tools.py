"""
Agent-Tools für Compile/Validate Operationen.

Bietet:
- compile_files: Validiert/kompiliert geänderte Dateien
- validate_file: Validiert eine einzelne Datei

Unterstützte Dateitypen:
- Python (.py)
- Java (.java)
- SQL (.sql)
- SQLJ (.sqlj)
- XML (.xml)
- Config (.yaml, .yml, .json, .properties, .toml)
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.utils.validators.base import (
    CompileResult,
    ValidatorRegistry,
    ValidationResult,
    Severity,
)
from app.utils.validators.change_detector import get_changed_files

logger = logging.getLogger(__name__)


def _create_validator_registry(config: Dict[str, Any]) -> ValidatorRegistry:
    """Erstellt eine ValidatorRegistry mit allen Validatoren."""
    registry = ValidatorRegistry()

    # Python Validator
    if config.get("python", {}).get("enabled", True):
        from app.utils.validators.python_validator import PythonValidator
        registry.register(PythonValidator(config.get("python", {})))

    # Java Validator
    if config.get("java", {}).get("enabled", True):
        from app.utils.validators.java_validator import JavaValidator
        registry.register(JavaValidator(config.get("java", {})))

    # SQL Validator
    if config.get("sql", {}).get("enabled", True):
        from app.utils.validators.sql_validator import SQLValidator
        registry.register(SQLValidator(config.get("sql", {})))

    # SQLJ Validator
    if config.get("sqlj", {}).get("enabled", True):
        from app.utils.validators.sqlj_validator import SQLJValidator
        registry.register(SQLJValidator(config.get("sqlj", {})))

    # XML Validator
    if config.get("xml", {}).get("enabled", True):
        from app.utils.validators.xml_validator import XMLValidator
        registry.register(XMLValidator(config.get("xml", {})))

    # Config Validator
    if config.get("config", {}).get("enabled", True):
        from app.utils.validators.config_validator import ConfigValidator
        registry.register(ConfigValidator(config.get("config", {})))

    return registry


def register_compile_tools(registry: ToolRegistry) -> int:
    """
    Registriert die Compile/Validate Tools.

    Returns:
        Anzahl der registrierten Tools
    """
    from app.core.config import settings

    count = 0

    # ── compile_files ───────────────────────────────────────────────────────────

    async def compile_files(**kwargs: Any) -> ToolResult:
        """Validiert/kompiliert Dateien."""
        files_str: str = kwargs.get("files", "").strip()
        changed_only: bool = kwargs.get("changed_only", True)
        types_str: str = kwargs.get("types", "").strip()
        fix: bool = kwargs.get("fix", False)
        strict: bool = kwargs.get("strict", False)
        repo_path: str = kwargs.get("repo", "").strip()
        verbose: bool = kwargs.get("verbose", False)

        # Config prüfen
        if not settings.compile_tool.enabled:
            return ToolResult(
                success=False,
                error="Compile Tool ist deaktiviert. Aktiviere es in Settings → Compile Tool."
            )

        # Repository-Pfad ermitteln
        if not repo_path:
            # Java-Repo als Standard
            repo_path = settings.java.get_active_path()
            if not repo_path:
                repo_path = settings.python.get_active_path()

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository konfiguriert. Setze repo= Parameter oder konfiguriere ein Java/Python Repo."
            )

        repo = Path(repo_path)
        if not repo.exists():
            return ToolResult(
                success=False,
                error=f"Repository nicht gefunden: {repo_path}"
            )

        start_time = time.time()

        # Validator Registry erstellen
        validator_config = {
            "python": {
                "enabled": settings.compile_tool.python.enabled,
                "linter": settings.compile_tool.python.linter,
                "type_checker": settings.compile_tool.python.type_checker,
                "auto_fix_tool": settings.compile_tool.python.auto_fix_tool,
                "ignore_rules": settings.compile_tool.python.ignore_rules,
            },
            "java": {
                "enabled": settings.compile_tool.java.enabled,
                "mode": settings.compile_tool.java.mode,
                "java_home": settings.compile_tool.java.java_home,
                "javac_options": settings.compile_tool.java.javac_options,
            },
            "sql": {
                "enabled": settings.compile_tool.sql.enabled,
                "dialect": settings.compile_tool.sql.dialect,
                "check_best_practices": settings.compile_tool.sql.check_best_practices,
            },
            "sqlj": {
                "enabled": settings.compile_tool.sqlj.enabled,
                "sqlj_path": settings.compile_tool.sqlj.sqlj_path,
            },
            "xml": {
                "enabled": settings.compile_tool.xml.enabled,
                "validate_schemas": settings.compile_tool.xml.validate_schemas,
            },
            "config": {
                "enabled": settings.compile_tool.config.enabled,
            },
        }

        validator_registry = _create_validator_registry(validator_config)

        # Unterstützte Extensions
        supported_extensions = validator_registry.get_supported_extensions()

        # Dateien sammeln
        file_paths: List[str] = []

        if files_str:
            # Explizite Dateien/Patterns
            for pattern in files_str.split(","):
                pattern = pattern.strip()
                if "*" in pattern or "?" in pattern:
                    # Glob Pattern
                    for match in repo.glob(pattern):
                        if match.is_file():
                            file_paths.append(str(match))
                else:
                    # Einzelne Datei
                    full_path = repo / pattern if not Path(pattern).is_absolute() else Path(pattern)
                    if full_path.exists() and full_path.is_file():
                        file_paths.append(str(full_path))
        elif changed_only:
            # Nur geänderte Dateien
            changed = await get_changed_files(
                str(repo),
                use_git=True,
                extensions=supported_extensions,
            )
            file_paths = [cf.path for cf in changed if cf.status != "deleted"]
        else:
            # Alle Dateien mit unterstützten Extensions
            for ext in supported_extensions:
                for match in repo.rglob(f"*{ext}"):
                    # Exclude-Dirs überspringen
                    exclude_dirs = settings.java.exclude_dirs + settings.python.exclude_dirs
                    if not any(ex in str(match) for ex in exclude_dirs):
                        file_paths.append(str(match))

        if not file_paths:
            return ToolResult(
                success=True,
                data="Keine Dateien zum Validieren gefunden."
            )

        # Type-Filter
        types: Optional[List[str]] = None
        if types_str and types_str.lower() != "all":
            types = [t.strip().lower() for t in types_str.split(",")]

        # Validieren
        results = await validator_registry.validate_files(
            file_paths,
            fix=fix,
            strict=strict,
            types=types,
        )

        # CompileResult erstellen
        total_errors = sum(r.error_count for r in results)
        total_warnings = sum(r.warning_count for r in results)
        skipped_count = sum(1 for r in results if r.skipped)

        compile_result = CompileResult(
            repo_path=str(repo),
            files_checked=len(file_paths),
            results=results,
            total_errors=total_errors,
            total_warnings=total_warnings,
            time_ms=int((time.time() - start_time) * 1000),
            skipped_count=skipped_count,
        )

        output = compile_result.format(verbose=verbose)

        return ToolResult(
            success=compile_result.success,
            data=output,
        )

    registry.register(Tool(
        name="compile_files",
        description=(
            "Validiert und kompiliert Dateien im Repository. "
            "Unterstützt Python, Java, SQL, SQLJ, XML und Config-Dateien. "
            "Standardmäßig werden nur geänderte Dateien (Git) geprüft. "
            "Mit fix=true werden Auto-Fixes angewendet (z.B. Linting). "
            "Ideal für: Pre-Commit Checks, Code-Qualität, Syntax-Validierung."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="files",
                type="string",
                description=(
                    "Kommagetrennte Dateipfade oder Glob-Patterns. "
                    "Beispiel: 'src/**/*.java' oder 'src/main.py,src/utils.py'. "
                    "Leer = geänderte Dateien."
                ),
                required=False,
            ),
            ToolParameter(
                name="changed_only",
                type="boolean",
                description="Nur Git-geänderte Dateien prüfen (default: true)",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="types",
                type="string",
                description="Nur bestimmte Typen: python,java,sql,sqlj,xml,config,all (default: all)",
                required=False,
                enum=["python", "java", "sql", "sqlj", "xml", "config", "all"],
            ),
            ToolParameter(
                name="fix",
                type="boolean",
                description="Auto-Fix anwenden wenn möglich (z.B. ruff --fix)",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="strict",
                type="boolean",
                description="Strenger Modus: Warnings als Errors behandeln",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="repo",
                type="string",
                description="Repository-Pfad (default: aktives Java/Python Repo)",
                required=False,
            ),
            ToolParameter(
                name="verbose",
                type="boolean",
                description="Detaillierte Ausgabe mit allen Issues",
                required=False,
                default=False,
            ),
        ],
        handler=compile_files,
    ))
    count += 1

    # ── validate_file ───────────────────────────────────────────────────────────

    async def validate_file(**kwargs: Any) -> ToolResult:
        """Validiert eine einzelne Datei."""
        file_path: str = kwargs.get("file", "").strip()
        fix: bool = kwargs.get("fix", False)
        strict: bool = kwargs.get("strict", False)

        if not file_path:
            return ToolResult(
                success=False,
                error="file ist erforderlich. Beispiel: validate_file(file=\"src/main.py\")"
            )

        # Config prüfen
        if not settings.compile_tool.enabled:
            return ToolResult(
                success=False,
                error="Compile Tool ist deaktiviert."
            )

        path = Path(file_path)
        if not path.exists():
            return ToolResult(
                success=False,
                error=f"Datei nicht gefunden: {file_path}"
            )

        # Validator finden
        validator_config = {
            "python": {"enabled": True, "linter": settings.compile_tool.python.linter},
            "java": {"enabled": True, "mode": settings.compile_tool.java.mode},
            "sql": {"enabled": True, "dialect": settings.compile_tool.sql.dialect},
            "sqlj": {"enabled": True},
            "xml": {"enabled": True},
            "config": {"enabled": True},
        }

        validator_registry = _create_validator_registry(validator_config)
        result = await validator_registry.validate_file(file_path, fix=fix, strict=strict)

        if result is None:
            ext = path.suffix
            return ToolResult(
                success=False,
                error=f"Kein Validator für Dateityp '{ext}' verfügbar."
            )

        # Formatieren
        output = f"=== Validate: {path.name} ===\n"
        output += f"Type: {result.file_type}\n"
        output += f"Time: {result.time_ms}ms\n\n"

        if result.skipped:
            output += f"Skipped: {result.skip_reason}\n"
        elif result.success:
            if result.warnings:
                output += f"OK with {result.warning_count} warning(s):\n"
                for issue in result.warnings:
                    output += f"  {issue}\n"
            else:
                output += "OK - No issues found.\n"
        else:
            output += f"FAILED with {result.error_count} error(s):\n"
            for issue in result.errors:
                output += f"  {issue}\n"
            if result.warnings:
                output += f"\n{result.warning_count} warning(s):\n"
                for issue in result.warnings:
                    output += f"  {issue}\n"

        return ToolResult(success=result.success, data=output)

    registry.register(Tool(
        name="validate_file",
        description=(
            "Validiert eine einzelne Datei und gibt detaillierte Ergebnisse zurück. "
            "Unterstützt Python, Java, SQL, SQLJ, XML und Config-Dateien."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="file",
                type="string",
                description="Pfad zur Datei",
                required=True,
            ),
            ToolParameter(
                name="fix",
                type="boolean",
                description="Auto-Fix anwenden wenn möglich",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="strict",
                type="boolean",
                description="Warnings als Errors behandeln",
                required=False,
                default=False,
            ),
        ],
        handler=validate_file,
    ))
    count += 1

    return count
