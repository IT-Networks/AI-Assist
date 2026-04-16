"""
Generic Command-Execution Tool fuer Projekt-Interaktion.

Token-Budget fuer LLM-Antwort:
- Bei Erfolg (exit=0): kurze stdout_tail (max 1 KB) + Status-Felder
- Bei Fehler (exit!=0): stderr_tail (bis 2 KB) + kurze stdout_tail (max 500 B)
Voller Output flieesst eh ueber SSE-Streaming-Events ans Frontend, das LLM
braucht ihn nur fuer Diagnose.

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
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)

# Token-Budgets fuer LLM-Payload (Tail-Bytes pro Stream).
LLM_STDOUT_TAIL_SUCCESS = 1024   # ~250 Tokens
LLM_STDOUT_TAIL_FAILED = 512     # bei Fehler ist stderr wichtiger
LLM_STDERR_TAIL_FAILED = 2048    # ~500 Tokens
LLM_STDERR_TAIL_SUCCESS = 256    # nur kurzer Hint wenn vorhanden


def _tail(text: str, max_bytes: int) -> str:
    """Nimmt die letzten max_bytes eines Strings (UTF-8-Byte-Count).

    Vermeidet Mid-Char Splits. Wenn gekuerzt: praefix mit '...[gekuerzt]...'.
    """
    if not text:
        return ""
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    cut = raw[-max_bytes:].decode("utf-8", errors="replace")
    return f"...[gekürzt]...\n{cut}"


def _build_llm_payload(result: Any, command_str: str) -> Dict[str, Any]:
    """Baut die kompakte Payload, die ans LLM zurueckgegeben wird.

    NICHT enthalten (gegenueber result.to_dict()):
    - command (kennt das LLM bereits aus seinem Tool-Call)
    - workspace (kennt das LLM bereits)
    - stdout_preview/stderr_preview (durch tail ersetzt)

    Enthalten:
    - execution_status, exit_code, duration_ms
    - stdout_tail / stderr_tail (gebudgetiert)
    - error (falls vorhanden)
    - truncated, total_bytes (compact stats)
    """
    success = result.exit_code == 0
    payload: Dict[str, Any] = {
        "execution_status": "success" if success else "failed",
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
    }
    if result.command_id:
        payload["command_id"] = result.command_id
    if result.truncated:
        payload["truncated"] = True
    if result.total_stdout_bytes or result.total_stderr_bytes:
        payload["total_bytes"] = {
            "stdout": result.total_stdout_bytes,
            "stderr": result.total_stderr_bytes,
        }

    if success:
        payload["stdout_tail"] = _tail(result.stdout_preview, LLM_STDOUT_TAIL_SUCCESS)
        if result.stderr_preview:
            payload["stderr_tail"] = _tail(result.stderr_preview, LLM_STDERR_TAIL_SUCCESS)
        payload["message"] = f"OK (exit=0, {result.duration_ms}ms)"
    else:
        payload["stderr_tail"] = _tail(result.stderr_preview, LLM_STDERR_TAIL_FAILED)
        if result.stdout_preview:
            payload["stdout_tail"] = _tail(result.stdout_preview, LLM_STDOUT_TAIL_FAILED)
        payload["message"] = (
            f"FEHLGESCHLAGEN (exit={result.exit_code}, {result.duration_ms}ms). "
            f"Siehe stderr_tail."
        )
    return payload


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
        resolve_timeout,
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

    # Timeout-Aufloesung: explizit > per-binary-config > global default
    if timeout_seconds is not None:
        timeout = timeout_seconds
    else:
        default_timeout = 120
        per_binary = None
        if cfg is not None:
            default_timeout = getattr(cfg, "timeout_seconds", 120)
            per_binary = getattr(cfg, "timeout_per_binary", None)
        timeout = resolve_timeout(command, default_timeout, per_binary)

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
    # Session-ID + Registry fuer Cancel-Support (Phase 2 v2.37.32)
    # Streaming-Output via EventBridge (Phase 3 v2.37.33)
    from app.agent.agent_context import current_session_id
    from app.services.process_registry import get_process_registry
    from app.mcp.event_bridge import get_event_bridge
    from app.agent.orchestration.types import AgentEventType

    session_id = current_session_id()
    registry = get_process_registry() if session_id else None
    bridge = get_event_bridge() if session_id else None

    chunk_cb = None
    command_id_holder = {"id": None}  # closure captures for command_started event

    if bridge is not None:
        async def _chunk_cb(stream_name: str, line: str, seq: int) -> None:
            await bridge.emit(
                AgentEventType.COMMAND_OUTPUT_CHUNK.value,
                {
                    "session_id": session_id,
                    "command_id": command_id_holder["id"],
                    "stream": stream_name,
                    "data": line,
                    "seq": seq,
                },
            )
        chunk_cb = _chunk_cb

        # command_started Event (Frontend kann Live-Card aufbauen)
        await bridge.emit(
            AgentEventType.COMMAND_STARTED.value,
            {
                "session_id": session_id,
                "command": command,
                "command_preview": cmd_str,
                "workspace": path,
                "timeout_seconds": timeout,
            },
        )

    result = await run_workspace_command(
        workspace_path=path,
        command=command,
        timeout_seconds=timeout,
        whitelist=whitelist,
        session_id=session_id,
        registry=registry,
        chunk_cb=chunk_cb,
    )

    # command_id fuer evtl. spaetere Events nachreichen
    if result.command_id:
        command_id_holder["id"] = result.command_id

    # Done/Cancelled Event (mit tail summary fuer Frontend)
    if bridge is not None:
        if result.error and "abgebrochen" in result.error.lower():
            event_name = AgentEventType.COMMAND_CANCELLED.value
        else:
            event_name = AgentEventType.COMMAND_DONE.value
        await bridge.emit(
            event_name,
            {
                "session_id": session_id,
                "command_id": result.command_id,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "error": result.error,
                "truncated": result.truncated,
                "total_bytes": {
                    "stdout": result.total_stdout_bytes,
                    "stderr": result.total_stderr_bytes,
                },
            },
        )

    if not result.success:
        return ToolResult(
            success=False,
            error=result.error or "Unbekannter Ausfuehrungsfehler",
            data=result.to_dict(),
        )

    # Tool-Call war erfolgreich (Prozess hat sauber beendet).
    # Tail-basierte LLM-Payload statt 5 KB Preview (Token-Sparsamkeit).
    payload = _build_llm_payload(result, cmd_str)
    return ToolResult(success=True, data=payload)


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
