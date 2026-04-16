"""
Generic Command-Execution Tool fuer Projekt-Interaktion.

Stellt 'run_workspace_command' bereit: User kann im Chat "Starte main.py"
oder "baue das Projekt" sagen. LLM ruft dieses Tool mit dem Workspace-Pfad
und dem Command auf. User-Confirmation ist vor Ausfuehrung erforderlich.

Abgrenzung:
- run_pytest/run_npm_tests (test_exec_tools): spezialisiert fuer Tests
  ohne Confirmation (nur Lesen der Test-Ergebnisse).
- execute_python_script (script_tools): fuer AI-generierte One-Shot-Scripts
  mit pip-Whitelist und Script-Store.
- run_workspace_command HIER: beliebiges Projekt-Binary (python/npm/mvn/...)
  mit Binary-Whitelist + Confirm-Required.
"""

import logging
from typing import List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


async def _handle_run_workspace_command(
    path: str,
    command: List[str],
    timeout_seconds: Optional[int] = None,
    _confirmed: bool = False,
) -> ToolResult:
    """Handler fuer run_workspace_command.

    Zwei-Phasen-Flow:
    1. Erster Call (ohne _confirmed): validiert + liefert confirmation_data
    2. Nach User-Confirm: orchestrator ruft mit _confirmed=True erneut auf -> Ausfuehrung
    """
    from app.core.config import settings
    from app.services.command_runner import (
        run_workspace_command,
        validate_command,
        DEFAULT_BINARY_WHITELIST,
    )

    cfg = getattr(settings, "command_exec", None)
    if cfg is not None and not getattr(cfg, "enabled", True):
        return ToolResult(
            success=False,
            error="Command-Execution ist deaktiviert (command_exec.enabled=false)",
        )

    if not path or not isinstance(path, str):
        return ToolResult(success=False, error="path (Workspace) ist Pflicht")

    if not command or not isinstance(command, list):
        return ToolResult(
            success=False,
            error="command muss eine Liste sein (z.B. ['python','main.py'])",
        )

    # Whitelist aus Config laden, sonst Default
    allowed_binaries = None
    if cfg and getattr(cfg, "allowed_binaries", None):
        allowed_binaries = {b for b in cfg.allowed_binaries if b}
    whitelist = allowed_binaries or DEFAULT_BINARY_WHITELIST

    # Vorab-Validierung (schlaegt schon VOR Confirm fehl bei invalidem Command)
    err = validate_command(command, whitelist=whitelist)
    if err:
        return ToolResult(success=False, error=err)

    timeout = timeout_seconds
    if timeout is None and cfg is not None:
        timeout = getattr(cfg, "timeout_seconds", 120)
    if timeout is None:
        timeout = 120

    cmd_str = " ".join(command)

    # Phase 1: Confirmation anfordern
    if not _confirmed:
        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Bereit zum Ausfuehren: '{cmd_str}' in '{path}'",
            confirmation_data={
                "action": "run_workspace_command",
                "description": f"Befehl ausfuehren: {cmd_str} (in {path})",
                "params": {
                    "path": path,
                    "command": command,
                    "timeout_seconds": timeout,
                },
                # Zusaetzliche Felder fuer UI-Preview
                "command_preview": cmd_str,
                "workspace": path,
                "timeout_seconds": timeout,
            },
        )

    # Phase 2: Ausfuehren (nach Confirm)
    result = await run_workspace_command(
        workspace_path=path,
        command=command,
        timeout_seconds=timeout,
        whitelist=whitelist,
    )

    if not result.success:
        return ToolResult(
            success=False,
            error=result.error or "Unbekannter Ausfuehrungsfehler",
            data=result.to_dict(),
        )

    # Tool-Call war erfolgreich (Prozess hat sauber beendet).
    # exit_code != 0 ist KEIN Tool-Fehler, LLM kann die Meldung interpretieren.
    status_icon = "OK" if result.exit_code == 0 else f"exit={result.exit_code}"
    msg = f"Ausgefuehrt ({status_icon}, {result.duration_ms}ms)"

    return ToolResult(success=True, data={"message": msg, **result.to_dict()})


def register_command_tools(registry: ToolRegistry) -> int:
    """Registriert das generische run_workspace_command-Tool."""
    from app.core.config import settings

    cfg = getattr(settings, "command_exec", None)
    if cfg is not None and not getattr(cfg, "enabled", True):
        logger.info("[CommandExec] Deaktiviert (command_exec.enabled=false)")
        return 0

    registry.register(Tool(
        name="run_workspace_command",
        description=(
            "Fuehrt einen Command im Projekt-Workspace aus (z.B. python main.py, "
            "npm run dev, mvn package). Benoetigt USER-BESTAETIGUNG vor Ausfuehrung. "
            "Binary muss in der Whitelist sein (python, node, npm, npx, pytest, "
            "pip, uv, poetry, cargo, go, mvn, gradle, tsc, jest, vitest, java, make). "
            "Kein shell=True - command muss als Liste [binary, arg1, arg2, ...] uebergeben werden. "
            "Nutze dieses Tool wenn der User ein bestehendes Projekt STARTEN, BAUEN oder "
            "ein Script AUSFUEHREN will. Fuer Tests nutze run_pytest/run_npm_tests."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                "path", "string",
                "Absoluter Pfad zum Projekt-Workspace (wird als cwd verwendet)",
                required=True,
            ),
            ToolParameter(
                "command", "array",
                "Command als Liste: [binary, arg1, arg2, ...]. "
                "Beispiele: ['python','main.py'], ['npm','run','dev'], "
                "['mvn','package'], ['cargo','build']. "
                "KEIN Shell-String - jedes Argument separat in der Liste!",
                required=True,
            ),
            ToolParameter(
                "timeout_seconds", "integer",
                "Abbruch nach N Sekunden (default aus config, meist 120)",
                required=False,
            ),
        ],
        is_write_operation=True,  # Code-Execution braucht User-Confirm
        handler=_handle_run_workspace_command,
    ))

    logger.info("[CommandExec] run_workspace_command registriert")
    return 1
