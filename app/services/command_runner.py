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
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_PREVIEW = 5000
DEFAULT_RINGBUFFER_BYTES = 2 * 1024 * 1024  # 2 MB tail per stream

# Callback-Signatur fuer streaming Output-Chunks.
# stream_name: "stdout" | "stderr"
# line: dekodierte Zeile (inklusive trailing newline falls vorhanden)
# seq: monoton steigender Sequenz-Counter (Reordering / Backfill)
ChunkCallback = Callable[[str, str, int], Awaitable[None]]

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
    # Streaming-Felder (v2.37.33+) - nur gesetzt im Streaming-Mode
    command_id: Optional[str] = None       # eindeutig pro Run, fuer SSE-Korrelation
    truncated: bool = False                # True wenn Ringbuffer Bytes verworfen hat
    total_stdout_bytes: int = 0
    total_stderr_bytes: int = 0

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
        d = {
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
        if self.command_id:
            d["command_id"] = self.command_id
        if self.truncated:
            d["truncated"] = True
        if self.total_stdout_bytes or self.total_stderr_bytes:
            d["total_bytes"] = {
                "stdout": self.total_stdout_bytes,
                "stderr": self.total_stderr_bytes,
            }
        return d


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


class _Ringbuffer:
    """Byte-basierter Ringbuffer: speichert die letzten N Bytes als Chunks.

    Verhindert OOM bei Outputs die Hunderte MB liefern (Maven, npm install
    mit verbose, docker build etc.). Wenn Limit ueberschritten wird, faellt
    der aelteste Chunk raus und truncated=True wird gesetzt.
    """

    __slots__ = ("max_bytes", "_chunks", "_size", "truncated", "total_bytes")

    def __init__(self, max_bytes: int = DEFAULT_RINGBUFFER_BYTES):
        self.max_bytes = max_bytes
        self._chunks: deque = deque()
        self._size = 0
        self.truncated = False
        self.total_bytes = 0  # gesamt gesehene Bytes (auch verworfene)

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)
        self._chunks.append(chunk)
        self._size += len(chunk)
        # Trimmen bis wir <= max_bytes sind, mindestens 1 Chunk behalten
        while self._size > self.max_bytes and len(self._chunks) > 1:
            removed = self._chunks.popleft()
            self._size -= len(removed)
            self.truncated = True
        # Single-Chunk groesser als max_bytes: hart kuerzen
        if self._size > self.max_bytes and len(self._chunks) == 1:
            only = self._chunks.popleft()
            cut = only[-self.max_bytes:]
            self._chunks.append(cut)
            self._size = len(cut)
            self.truncated = True

    def to_bytes(self) -> bytes:
        return b"".join(self._chunks)

    def to_text(self) -> str:
        return self.to_bytes().decode("utf-8", errors="replace")


async def _drain_stream(
    stream: Optional[asyncio.StreamReader],
    stream_name: str,
    ringbuffer: _Ringbuffer,
    seq_counter: List[int],
    chunk_cb: Optional[ChunkCallback],
) -> None:
    """Liest line-by-line aus einem Subprocess-Stream.

    Jede Zeile wird in den Ringbuffer geschrieben und (optional) per
    chunk_cb emittiert. Beendet wenn EOF.
    """
    if stream is None:
        return
    while True:
        try:
            line_bytes = await stream.readline()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[command_runner] readline {stream_name} fehlgeschlagen: {e}")
            return
        if not line_bytes:
            return  # EOF
        ringbuffer.append(line_bytes)
        if chunk_cb is None:
            continue
        seq_counter[0] += 1
        seq = seq_counter[0]
        try:
            line = line_bytes.decode("utf-8", errors="replace")
            await chunk_cb(stream_name, line, seq)
        except Exception as e:
            logger.warning(f"[command_runner] chunk_cb {stream_name} #{seq}: {e}")


def resolve_timeout(
    command: List[str],
    default_timeout: int,
    per_binary: Optional[Dict[str, int]] = None,
) -> int:
    """Bestimmt das Timeout fuer einen Command.

    Lookup-Reihenfolge:
    1. per_binary[binary_name_lower] (ohne Extension)
    2. per_binary[binary_name_lower_with_ext] (z.B. 'npm.cmd')
    3. default_timeout

    Beispiel:
        resolve_timeout(["npm","install"], 120, {"npm": 600}) -> 600
        resolve_timeout(["python","app.py"], 120, {"npm": 600}) -> 120
    """
    if not per_binary or not command:
        return default_timeout
    binary = _binary_name(command[0]).lower()
    if binary in per_binary:
        return per_binary[binary]
    # auch ohne Extension probieren (npm.cmd -> npm)
    stem = binary.rsplit(".", 1)[0] if "." in binary else binary
    if stem in per_binary:
        return per_binary[stem]
    return default_timeout


async def _terminate_then_kill(
    process: asyncio.subprocess.Process,
    grace_seconds: float = 2.0,
) -> None:
    """Sendet SIGTERM, wartet grace_seconds, dann SIGKILL.

    Cross-Plattform: terminate() mappt unter Windows auf TerminateProcess
    (das ist effektiv hart - kein "graceful" Konzept).
    """
    try:
        process.terminate()
    except ProcessLookupError:
        return
    except Exception as e:
        logger.warning(f"[command_runner] terminate fehlgeschlagen: {e}")
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass
    try:
        process.kill()
        await process.wait()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning(f"[command_runner] kill fehlgeschlagen: {e}")


async def _wait_for_process_or_cancel(
    process: asyncio.subprocess.Process,
    timeout: int,
    cancel_event: Optional[asyncio.Event],
) -> tuple[bytes, bytes, str]:
    """Wartet auf Subprocess-Ende mit Race gegen Timeout und cancel_event.

    Returns: (stdout_bytes, stderr_bytes, status)
        status: "ok" | "timeout" | "cancelled"

    Raises nichts - Status wird per Tuple kommuniziert. Bei timeout/cancelled
    wird der Process getoetet (SIGTERM->SIGKILL grace), partielle Ausgabe
    wird so gut wie moeglich gelesen.
    """
    if cancel_event is None:
        # Schneller Pfad ohne Cancel-Support
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return stdout_bytes, stderr_bytes, "ok"
        except asyncio.TimeoutError:
            await _terminate_then_kill(process)
            return b"", b"", "timeout"

    # Mit Cancel-Race
    comm_task = asyncio.create_task(process.communicate())
    cancel_task = asyncio.create_task(cancel_event.wait())

    done, pending = await asyncio.wait(
        {comm_task, cancel_task},
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel-Task immer aufraeumen (wenn noch nicht fertig)
    if not cancel_task.done():
        cancel_task.cancel()

    # Fall 1: Cancel-Event wurde gesetzt
    if cancel_event.is_set() and not comm_task.done():
        await _terminate_then_kill(process)
        # comm_task sollte jetzt mit EOF zurueckkommen
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(comm_task, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            comm_task.cancel()
            stdout_bytes, stderr_bytes = b"", b""
        return stdout_bytes, stderr_bytes, "cancelled"

    # Fall 2: Timeout (weder comm_task noch cancel_task fertig in time)
    if not comm_task.done():
        await _terminate_then_kill(process)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(comm_task, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            comm_task.cancel()
            stdout_bytes, stderr_bytes = b"", b""
        return stdout_bytes, stderr_bytes, "timeout"

    # Fall 3: Subprocess regulaer beendet
    stdout_bytes, stderr_bytes = comm_task.result()
    return stdout_bytes, stderr_bytes, "ok"


async def _stream_process_or_cancel(
    process: asyncio.subprocess.Process,
    timeout: int,
    cancel_event: Optional[asyncio.Event],
    chunk_cb: Optional[ChunkCallback],
    ringbuffer_bytes: int,
) -> tuple[_Ringbuffer, _Ringbuffer, str]:
    """Streaming-Variante von _wait_for_process_or_cancel.

    Liest line-by-line aus stdout/stderr (parallel), emittiert pro Zeile
    via chunk_cb, schreibt in Ringbuffer. Cancel/Timeout terminiert den
    Subprocess.

    Returns: (stdout_buf, stderr_buf, status)
    """
    stdout_buf = _Ringbuffer(ringbuffer_bytes)
    stderr_buf = _Ringbuffer(ringbuffer_bytes)
    seq = [0]  # mutable counter, geteilt zwischen beiden Streams

    out_task = asyncio.create_task(
        _drain_stream(process.stdout, "stdout", stdout_buf, seq, chunk_cb)
    )
    err_task = asyncio.create_task(
        _drain_stream(process.stderr, "stderr", stderr_buf, seq, chunk_cb)
    )
    wait_task = asyncio.create_task(process.wait())
    cancel_task: Optional[asyncio.Task] = None
    if cancel_event is not None:
        cancel_task = asyncio.create_task(cancel_event.wait())

    watch = {wait_task}
    if cancel_task is not None:
        watch.add(cancel_task)

    try:
        done, pending = await asyncio.wait(
            watch,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        await _terminate_then_kill(process)
        for t in (out_task, err_task, wait_task):
            t.cancel()
        if cancel_task is not None:
            cancel_task.cancel()
        raise

    # Status bestimmen
    status = "ok"
    if cancel_task is not None and cancel_event is not None and cancel_event.is_set():
        status = "cancelled"
    elif not wait_task.done():
        status = "timeout"

    if status != "ok":
        await _terminate_then_kill(process)

    # Auf wait_task warten (sollte nach Kill schnell zurueckkommen)
    try:
        await asyncio.wait_for(wait_task, timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        wait_task.cancel()

    # Stream-Drains zu Ende lesen (EOF nach Process-Ende)
    try:
        await asyncio.wait_for(out_task, timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        out_task.cancel()
    try:
        await asyncio.wait_for(err_task, timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        err_task.cancel()

    if cancel_task is not None and not cancel_task.done():
        cancel_task.cancel()

    return stdout_buf, stderr_buf, status


async def run_workspace_command(
    workspace_path: str,
    command: List[str],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    env: Optional[Dict[str, str]] = None,
    whitelist: Optional[set] = None,
    session_id: Optional[str] = None,
    registry=None,                     # ProcessRegistry, optional
    chunk_cb: Optional[ChunkCallback] = None,  # NEW: enable streaming
    ringbuffer_bytes: int = DEFAULT_RINGBUFFER_BYTES,
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
        session_id: Falls gesetzt + registry: Cancel-Support via Registry
        registry: ProcessRegistry-Instanz fuer Cancel-Tracking

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

    # Registry-Eintrag (falls aktiviert) - liefert cancel_event
    cancel_event: Optional[asyncio.Event] = None
    registry_entry = None
    if registry is not None and session_id:
        try:
            registry_entry = await registry.register(
                session_id=session_id,
                process=process,
                command=command,
                workspace=str(cwd),
            )
            cancel_event = registry_entry.cancel_event
        except Exception as e:
            logger.warning(f"[command_runner] Registry-Register fehlgeschlagen: {e}")

    if registry_entry is not None:
        result.command_id = registry_entry.command_id

    try:
        if chunk_cb is None:
            # Klassischer Pfad: communicate() bis Ende, dann decode+truncate.
            stdout_bytes, stderr_bytes, status = await _wait_for_process_or_cancel(
                process, timeout_seconds, cancel_event
            )
            stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            stdout_total = len(stdout_bytes) if stdout_bytes else 0
            stderr_total = len(stderr_bytes) if stderr_bytes else 0
            truncated = False
        else:
            # Streaming-Pfad: line-by-line, chunk_cb pro Zeile, Ringbuffer als tail.
            stdout_buf, stderr_buf, status = await _stream_process_or_cancel(
                process, timeout_seconds, cancel_event, chunk_cb, ringbuffer_bytes
            )
            stdout_text = stdout_buf.to_text()
            stderr_text = stderr_buf.to_text()
            stdout_total = stdout_buf.total_bytes
            stderr_total = stderr_buf.total_bytes
            truncated = stdout_buf.truncated or stderr_buf.truncated
    finally:
        if registry is not None and registry_entry is not None:
            await registry.cleanup(session_id, command_id=registry_entry.command_id)

    result.duration_ms = int((time.time() - start) * 1000)
    result.total_stdout_bytes = stdout_total
    result.total_stderr_bytes = stderr_total
    result.truncated = truncated

    if status == "timeout":
        result.error = f"Timeout nach {timeout_seconds}s - Ausfuehrung abgebrochen"
        result.stdout_preview = _truncate(stdout_text) if stdout_text else ""
        result.stderr_preview = _truncate(stderr_text) if stderr_text else ""
        logger.warning(f"[command_runner] {result.error}")
        return result

    if status == "cancelled":
        result.error = "Vom Benutzer abgebrochen"
        result.stdout_preview = _truncate(stdout_text) if stdout_text else ""
        result.stderr_preview = _truncate(stderr_text) if stderr_text else ""
        logger.info(f"[command_runner] Cancelled nach {result.duration_ms}ms")
        return result

    # status == "ok"
    result.exit_code = process.returncode
    result.stdout_preview = _truncate(stdout_text)
    result.stderr_preview = _truncate(stderr_text)
    result.success = True

    logger.info(
        f"[command_runner] Fertig in {result.duration_ms}ms, exit={result.exit_code}"
        f"{' (truncated)' if truncated else ''}"
    )
    return result
