"""
Test-Execution Tools fuer den Agent.

Wrapper um app/services/test_runner.py. Stellt run_pytest und run_npm_tests
als Tools fuer LLM-Agents bereit (primaer Implementation-Team reviewer +
test-engineer).

Hinweis: app/agent/test_tools.py ist etwas anderes (fachliche Multi-Institut
Testing-Domain). Diese Datei ist fuer die Ausfuehrung von Software-Tests.

Aktivierung: settings.test_exec.enabled (default True).
Registrierung: register_test_exec_tools(registry) in main.py lifespan.
"""

import logging
from typing import Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


async def _handle_run_pytest(
    path: str,
    test_path: str = "tests",
    pattern: Optional[str] = None,
    coverage: bool = False,
) -> ToolResult:
    """Handler fuer run_pytest."""
    from app.core.config import settings
    from app.services.test_runner import run_pytest

    cfg = getattr(settings, "test_exec", None)
    if cfg is not None and not getattr(cfg, "enabled", True):
        return ToolResult(
            success=False,
            error="Test-Execution-Tools sind deaktiviert (test_exec.enabled=false)",
        )

    timeout = getattr(cfg, "timeout_seconds", 120) if cfg else 120
    result = await run_pytest(
        workspace_path=path,
        test_path=test_path,
        pattern=pattern,
        coverage=coverage,
        timeout_seconds=timeout,
    )

    if not result.success:
        return ToolResult(
            success=False,
            error=result.error or "Unbekannter Fehler",
            data=result.to_dict(),
        )

    return ToolResult(success=True, data=result.to_dict())


async def _handle_run_npm_tests(
    path: str,
    framework: str = "auto",
    coverage: bool = False,
) -> ToolResult:
    """Handler fuer run_npm_tests."""
    from app.core.config import settings
    from app.services.test_runner import run_npm_tests

    cfg = getattr(settings, "test_exec", None)
    if cfg is not None and not getattr(cfg, "enabled", True):
        return ToolResult(
            success=False,
            error="Test-Execution-Tools sind deaktiviert (test_exec.enabled=false)",
        )

    timeout = getattr(cfg, "timeout_seconds", 120) if cfg else 120
    result = await run_npm_tests(
        workspace_path=path,
        framework=framework,
        coverage=coverage,
        timeout_seconds=timeout,
    )

    if not result.success:
        return ToolResult(
            success=False,
            error=result.error or "Unbekannter Fehler",
            data=result.to_dict(),
        )

    return ToolResult(success=True, data=result.to_dict())


def register_test_exec_tools(registry: ToolRegistry) -> int:
    """Registriert Test-Execution-Tools im ToolRegistry.

    Returns:
        Anzahl registrierter Tools.
    """
    from app.core.config import settings

    cfg = getattr(settings, "test_exec", None)
    if cfg is not None and not getattr(cfg, "enabled", True):
        logger.info("[TestExec] Deaktiviert (test_exec.enabled=false)")
        return 0

    registry.register(Tool(
        name="run_pytest",
        description=(
            "Fuehrt pytest-Tests im angegebenen Workspace aus und liefert strukturiertes "
            "Ergebnis (passed/failed/skipped/coverage). Nutzt pytest-json-report falls "
            "installiert, sonst Text-Parsing. Abbruch nach Timeout. "
            "Nutze dieses Tool NACH dem Schreiben von Test-Dateien, um echte Ergebnisse "
            "statt Spekulation zu bekommen."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                "path", "string",
                "Absoluter Pfad zum Projekt-Workspace (wo pytest laufen soll, Projekt-Root)",
                required=True,
            ),
            ToolParameter(
                "test_path", "string",
                "Test-Ordner oder -Datei relativ zum Workspace (default 'tests')",
                required=False, default="tests",
            ),
            ToolParameter(
                "pattern", "string",
                "Optionales pytest -k Pattern (z.B. 'test_auth')",
                required=False,
            ),
            ToolParameter(
                "coverage", "boolean",
                "--cov hinzufuegen (default false). Setzt pytest-cov voraus.",
                required=False, default=False,
            ),
        ],
        is_write_operation=False,
        handler=_handle_run_pytest,
    ))

    registry.register(Tool(
        name="run_npm_tests",
        description=(
            "Fuehrt 'npm test' im angegebenen Workspace aus. Erkennt jest/vitest automatisch "
            "via package.json. Liefert strukturiertes Ergebnis (passed/failed/coverage). "
            "Abbruch nach Timeout. Nutze NACH dem Schreiben von Frontend-Tests."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                "path", "string",
                "Absoluter Pfad zum Frontend-Workspace (wo package.json liegt)",
                required=True,
            ),
            ToolParameter(
                "framework", "string",
                "Test-Framework: 'auto' (default, erkennt aus package.json), 'jest' oder 'vitest'",
                required=False, default="auto",
            ),
            ToolParameter(
                "coverage", "boolean",
                "--coverage hinzufuegen (default false)",
                required=False, default=False,
            ),
        ],
        is_write_operation=False,
        handler=_handle_run_npm_tests,
    ))

    logger.info("[TestExec] run_pytest und run_npm_tests registriert")
    return 2
