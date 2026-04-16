"""
Generic Command-Runner fuer Projekt-Commands im Workspace.

Fuehrt User-initiierte Commands aus (python main.py, npm run dev, mvn package
etc.) via asyncio-subprocess. Binary-Whitelist + Timeout + No-Shell als
Security-Layer.

Abgrenzung:
- app/services/test_runner.py: nur pytest/npm-test (keine Confirmation noetig)
- app/agent/script_tools.py: AI-generierte One-Shot-Scripts mit pip-Whitelist
- command_runner HIER: User sagt "starte main.py" -> beliebiges Binary aus
  Whitelist, mit User-Confirmation vor Ausfuehrung.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_PREVIEW = 5000

# Default-Whitelist: Binaries die Projekt-Commands ausfuehren.
# Wird von CommandExecConfig ueberschrieben.
DEFAULT_BINARY_WHITELIST = {
    # Python
    "python", "python3", "python.exe", "python3.exe",
    "pip", "pip3", "pip.exe",
    "pytest", "pytest.exe",
    "uv", "uv.exe",
    "poetry", "poetry.exe",
    # Node
    "node", "node.exe",
    "npm", "npm.cmd",
    "npx", "npx.cmd",
    "yarn", "yarn.cmd",
    "pnpm", "pnpm.cmd",
    "tsc", "tsc.cmd",
    "vitest", "vitest.cmd",
    "jest", "jest.cmd",
    # Java/JVM
    "java", "java.exe",
    "javac", "javac.exe",
    "mvn", "mvn.cmd",
    "gradle", "gradle.bat",
    "gradlew", "gradlew.bat",
    # Rust/Go/etc
    "cargo", "cargo.exe",
    "go", "go.exe",
    "rustc", "rustc.exe",
    # Build
    "make", "make.exe",
    "cmake", "cmake.exe",
}


@dataclass
class CommandResult:
    """Ergebnis einer Command-Ausfuehrung."""
    success: bool = False
    exit_code: Optional[int] = None
    duration_ms: int = 0
    command: List[str] = None  # type: ignore
    workspace: str = ""
    stdout_preview: str = ""
    stderr_preview: str = ""
    error: Optional[str] = None

    def __post_init__(self):
        if self.command is None:
            self.command = []

    def summary(self) -> str:
        if self.error:
            return f"Fehler: {self.error}"
        cmd = " ".join(self.command[:3]) + (" ..." if len(self.command) > 3 else "")
        return (
            f"{cmd!r} exit={self.exit_code} dauer={self.duration_ms}ms "
            f"(workspace={self.workspace})"
        )

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "command": self.command,
            "workspace": self.workspace,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "error": self.error,
            "summary": self.summary(),
        }


def _validate_workspace(path: str) -> Path:
    """Validates workspace path."""
    if not path or not path.strip():
        raise ValueError("workspace-Pfad darf nicht leer sein")
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"workspace-Pfad existiert nicht: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"workspace-Pfad ist kein Verzeichnis: {resolved}")
    return resolved


def _binary_name(cmd_arg: str) -> str:
    """Extrahiert den Binary-Namen fuer Whitelist-Check.

    - Entfernt Pfad-Praefixe (nur das letzte Segment)
    - Behaelt Extension (python.exe bleibt python.exe)
    """
    if not cmd_arg:
        return ""
    name = os.path.basename(cmd_arg.strip())
    return name


def validate_command(command: List[str], whitelist: Optional[set] = None) -> Optional[str]:
    """Prueft ob ein Command in der Binary-Whitelist erlaubt ist.

    Returns:
        None wenn OK, sonst Error-Message.
    """
    if not command or not isinstance(command, list):
        return "command muss eine nicht-leere Liste sein"
    if not command[0]:
        return "command[0] (Binary) darf nicht leer sein"

    allowed = whitelist if whitelist is not None else DEFAULT_BINARY_WHITELIST

    binary = _binary_name(command[0])
    binary_lower = binary.lower()
    allowed_lower = {a.lower() for a in allowed}

    if binary_lower not in allowed_lower:
        return (
            f"Binary '{binary}' nicht in der Whitelist. "
            f"Erlaubt: {sorted(allowed)[:10]}..."
        )

    # Defensive: keine shell-metacharacters in Args
    for i, arg in enumerate(command):
        if not isinstance(arg, str):
            return f"command[{i}] muss ein string sein, nicht {type(arg).__name__}"
        # Keine control characters / NUL
        if "\x00" in arg:
            return f"command[{i}] enthaelt NUL-Zeichen (unerlaubt)"

    return None


def _truncate(text: str, max_len: int = MAX_OUTPUT_PREVIEW) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n...[truncated]..."


async def run_workspace_command(
    workspace_path: str,
    command: List[str],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    env: Optional[Dict[str, str]] = None,
    whitelist: Optional[set] = None,
) -> CommandResult:
    """Fuehrt einen Command im Workspace aus.

    Security:
    - workspace_path wird validiert + resolved (Path.resolve)
    - command[0] muss in Binary-Whitelist sein
    - kein shell=True (keine Command-Injection)
    - Timeout begrenzt Laufzeit
    - stdout/stderr auf Preview-Groesse begrenzt

    Args:
        workspace_path: Absoluter Pfad - wird als cwd verwendet
        command: Liste [binary, arg1, arg2, ...] - kein Shell-Parsing
        timeout_seconds: Abbruchlimit
        env: Optionale zusaetzliche Umgebungsvariablen
        whitelist: Optional Override der default Binary-Whitelist

    Returns:
        CommandResult mit success/exit_code/stdout/stderr
    """
    result = CommandResult(command=list(command))

    try:
        cwd = _validate_workspace(workspace_path)
        result.workspace = str(cwd)
    except ValueError as e:
        result.error = str(e)
        return result

    err = validate_command(command, whitelist=whitelist)
    if err:
        result.error = err
        return result

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    logger.info(
        f"[command_runner] Starte in {cwd}: {' '.join(command)} "
        f"(timeout={timeout_seconds}s)"
    )
    start = time.time()

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run_env,
        )
    except FileNotFoundError:
        result.error = (
            f"Binary nicht gefunden: '{command[0]}'. "
            f"Ist es im PATH installiert?"
        )
        logger.error(f"[command_runner] {result.error}")
        return result
    except Exception as e:
        result.error = f"Prozess-Start fehlgeschlagen: {e}"
        logger.exception(f"[command_runner] {result.error}")
        return result

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        result.error = f"Timeout nach {timeout_seconds}s - Ausfuehrung abgebrochen"
        result.duration_ms = int((time.time() - start) * 1000)
        logger.warning(f"[command_runner] {result.error}")
        return result

    result.duration_ms = int((time.time() - start) * 1000)
    result.exit_code = process.returncode
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    result.stdout_preview = _truncate(stdout)
    result.stderr_preview = _truncate(stderr)

    # success = Prozess zu Ende (egal ob exit_code 0 oder nicht)
    result.success = True

    logger.info(
        f"[command_runner] Fertig in {result.duration_ms}ms, exit={result.exit_code}"
    )
    return result
