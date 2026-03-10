"""
Container Sandbox Tools - Sichere Python-Code-Ausführung in isolierten Containern.

Unterstützt Docker und Podman mit automatischer Erkennung.

Features:
- Stateless Execution: Einmalige Code-Ausführung ohne Session
- Session-basiert: Variablen bleiben zwischen Aufrufen erhalten
- Datei-Upload: Dateien zur Verarbeitung in Container laden
- Netzwerkzugriff: HTTP-Requests möglich (lesend)
- Ressourcen-Limits: CPU, Memory, Timeout

Sicherheit:
- Isolierter Container
- Ressourcen-Limits
- Automatisches Cleanup
- Keine Privilegien-Eskalation

Container Runtime:
- Automatische Erkennung von Docker oder Podman
- Podman ist portable (kein Daemon, kein Admin nötig)
- Konfigurierbar via backend: "auto" | "docker" | "podman"
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tool_registry import ToolDefinition, ToolRegistry
from app.core.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Container Runtime Detection (Docker / Podman)
# ══════════════════════════════════════════════════════════════════════════════

_container_runtime: Optional[str] = None  # Cache für erkannte Runtime
_runtime_version: Optional[str] = None


def _detect_container_runtime() -> Optional[str]:
    """
    Erkennt die verfügbare Container-Runtime.

    Reihenfolge bei "auto":
    1. Podman (bevorzugt - portable, daemonless)
    2. Docker

    Bei konfigurierten Pfaden werden diese verwendet.

    Returns:
        Vollständiger Pfad zur Runtime oder None wenn keine gefunden
    """
    global _container_runtime, _runtime_version

    # Cache nutzen
    if _container_runtime is not None:
        return _container_runtime if _container_runtime else None

    cfg = settings.docker_sandbox

    # Explizite Konfiguration mit Pfad
    if cfg.backend == "docker" and cfg.docker_path:
        if os.path.isfile(cfg.docker_path):
            _container_runtime = cfg.docker_path
            _runtime_version = _get_runtime_version(cfg.docker_path)
            logger.info("Sandbox: Using configured Docker: %s %s", cfg.docker_path, _runtime_version or "")
            return _container_runtime

    if cfg.backend == "podman" and cfg.podman_path:
        if os.path.isfile(cfg.podman_path):
            _container_runtime = cfg.podman_path
            _runtime_version = _get_runtime_version(cfg.podman_path)
            logger.info("Sandbox: Using configured Podman: %s %s", cfg.podman_path, _runtime_version or "")
            return _container_runtime

    # Explizite Konfiguration ohne Pfad (aus PATH suchen)
    if cfg.backend in ("docker", "podman"):
        # Zuerst konfigurierten Pfad prüfen, dann PATH
        custom_path = cfg.podman_path if cfg.backend == "podman" else cfg.docker_path
        runtime_path = custom_path if custom_path and os.path.isfile(custom_path) else shutil.which(cfg.backend)
        if runtime_path:
            _container_runtime = runtime_path
            _runtime_version = _get_runtime_version(runtime_path)
            logger.info("Sandbox: Using configured runtime: %s %s", runtime_path, _runtime_version or "")
            return _container_runtime
        else:
            logger.warning("Sandbox: Configured runtime '%s' not found!", cfg.backend)
            _container_runtime = ""  # Markiere als "nicht gefunden"
            return None

    # Auto-Detection: Podman bevorzugt (portable, daemonless)
    # Erst konfigurierte Pfade, dann PATH
    checks = [
        (cfg.podman_path, "podman"),
        (cfg.docker_path, "docker"),
        (shutil.which("podman"), "podman"),
        (shutil.which("docker"), "docker"),
    ]

    for path, name in checks:
        if path and (os.path.isfile(path) if not shutil.which(name) == path else True):
            actual_path = path if os.path.isfile(path) else shutil.which(name)
            if actual_path:
                _container_runtime = actual_path
                _runtime_version = _get_runtime_version(actual_path)
                logger.info("Sandbox: Auto-detected runtime: %s at %s %s", name, actual_path, _runtime_version or "")
                return _container_runtime

    logger.warning("Sandbox: No container runtime (docker/podman) found!")
    _container_runtime = ""  # Markiere als "nicht gefunden"
    return None


def _get_runtime_version(runtime: str) -> Optional[str]:
    """Holt die Version der Container-Runtime."""
    import subprocess
    try:
        result = subprocess.run(
            [runtime, "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # Erste Zeile, z.B. "podman version 4.5.0" oder "Docker version 24.0.2"
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass
    return None


def get_container_runtime() -> str:
    """
    Gibt die zu verwendende Container-Runtime zurück.

    Raises:
        RuntimeError wenn keine Runtime verfügbar
    """
    runtime = _detect_container_runtime()
    if not runtime:
        raise RuntimeError(
            "Keine Container-Runtime gefunden. "
            "Bitte Docker oder Podman installieren.\n"
            "Podman (empfohlen): https://podman.io/getting-started/installation\n"
            "Docker: https://docs.docker.com/get-docker/"
        )
    return runtime


def get_runtime_info() -> Dict[str, Any]:
    """Gibt Informationen zur Container-Runtime zurück."""
    runtime = _detect_container_runtime()
    return {
        "runtime": runtime or "none",
        "version": _runtime_version,
        "available": runtime is not None and runtime != "",
        "configured_backend": settings.docker_sandbox.backend
    }


# ══════════════════════════════════════════════════════════════════════════════
# Session Management
# ══════════════════════════════════════════════════════════════════════════════

class SandboxSession:
    """Eine Docker-Sandbox-Session mit persistentem Container."""

    def __init__(self, session_id: str, container_id: str):
        self.session_id = session_id
        self.container_id = container_id
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.execution_count = 0
        self.uploaded_files: List[str] = []

    def touch(self):
        """Aktualisiert den Last-Activity-Timestamp."""
        self.last_activity = datetime.now()

    def is_expired(self, timeout_minutes: int) -> bool:
        """Prüft, ob die Session abgelaufen ist."""
        return datetime.now() > self.last_activity + timedelta(minutes=timeout_minutes)


# Globaler Session-Store
_sessions: Dict[str, SandboxSession] = {}
_session_lock = asyncio.Lock()


async def _cleanup_expired_sessions():
    """Entfernt abgelaufene Sessions und ihre Container."""
    cfg = settings.docker_sandbox
    async with _session_lock:
        expired = [
            sid for sid, session in _sessions.items()
            if session.is_expired(cfg.session_timeout_minutes)
        ]
        for sid in expired:
            session = _sessions.pop(sid, None)
            if session:
                await _stop_container(session.container_id)
                logger.debug("Docker sandbox session %s expired and cleaned up", sid)


async def _stop_container(container_id: str):
    """Stoppt und entfernt einen Container."""
    try:
        runtime = get_container_runtime()
        proc = await asyncio.create_subprocess_exec(
            runtime, "rm", "-f", container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
    except Exception as e:
        logger.warning("Sandbox: Error stopping container %s: %s", container_id, e)


# ══════════════════════════════════════════════════════════════════════════════
# Container Helper Functions
# ══════════════════════════════════════════════════════════════════════════════

def _get_container_image() -> str:
    """Gibt das zu verwendende Container-Image zurück."""
    cfg = settings.docker_sandbox
    return cfg.custom_image if cfg.custom_image else cfg.image


def _build_container_run_args(
    container_name: Optional[str] = None,
    detach: bool = False,
    network: bool = True,
    workdir: str = "/workspace"
) -> List[str]:
    """Baut die Container-Run-Argumente auf (Docker/Podman kompatibel)."""
    cfg = settings.docker_sandbox
    runtime = get_container_runtime()
    args = [runtime, "run"]

    if not detach:
        args.append("--rm")
    else:
        args.extend(["-d", "--name", container_name])

    # Ressourcen-Limits
    args.extend(["-m", cfg.memory_limit])
    args.extend(["--cpus", str(cfg.cpu_limit)])

    # Sicherheit
    if cfg.drop_capabilities:
        args.extend(["--cap-drop", "ALL"])
    args.append("--no-new-privileges")

    if cfg.read_only_filesystem:
        args.append("--read-only")
        # Temp-Verzeichnis für pip etc. mounten
        args.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])

    # Netzwerk
    if not network or not cfg.network_enabled:
        args.append("--network=none")

    # Arbeitsverzeichnis
    args.extend(["-w", workdir])

    return args


async def _run_container_command(
    args: List[str],
    input_data: Optional[str] = None,
    timeout: Optional[int] = None
) -> tuple[int, str, str]:
    """Führt einen Container-Befehl aus und gibt Exit-Code, stdout, stderr zurück."""
    cfg = settings.docker_sandbox
    timeout = timeout or cfg.timeout_seconds

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input_data.encode() if input_data else None),
            timeout=timeout
        )

        # Output-Größe limitieren
        max_bytes = cfg.max_output_bytes
        stdout_str = stdout.decode("utf-8", errors="replace")[:max_bytes]
        stderr_str = stderr.decode("utf-8", errors="replace")[:max_bytes]

        return proc.returncode, stdout_str, stderr_str

    except asyncio.TimeoutError:
        # Container killen bei Timeout
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"Timeout nach {timeout} Sekunden"
    except Exception as e:
        return -1, "", str(e)


async def _ensure_packages_installed(container_id: str, packages: List[str]) -> bool:
    """Installiert Pakete in einem laufenden Container (falls nötig)."""
    if not packages:
        return True

    runtime = get_container_runtime()

    # Prüfen ob pip verfügbar
    check_cmd = [runtime, "exec", container_id, "pip", "--version"]
    code, _, _ = await _run_container_command(check_cmd, timeout=10)
    if code != 0:
        return False

    # Pakete installieren
    install_cmd = [runtime, "exec", container_id, "pip", "install", "-q"] + packages
    code, stdout, stderr = await _run_container_command(install_cmd, timeout=120)
    return code == 0


# ══════════════════════════════════════════════════════════════════════════════
# Tool Implementations
# ══════════════════════════════════════════════════════════════════════════════

async def docker_execute_python(
    code: str,
    packages: Optional[List[str]] = None,
    timeout: Optional[int] = None
) -> Dict[str, Any]:
    """
    Führt Python-Code in einem isolierten Docker-Container aus (stateless).

    Der Container wird nach Ausführung automatisch gelöscht.
    Für persistente Sessions verwende docker_session_create/docker_session_execute.

    Args:
        code: Python-Code zum Ausführen
        packages: Zusätzliche pip-Pakete (werden on-the-fly installiert)
        timeout: Timeout in Sekunden (optional, Default aus Config)

    Returns:
        Dict mit stdout, stderr, exit_code, execution_time

    Beispiel:
        docker_execute_python(code="import base64; print(base64.b64encode(b'Hello').decode())")
        → {"stdout": "SGVsbG8=", "exit_code": 0}
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert", "exit_code": -1}

    start_time = time.time()

    # Docker-Befehl aufbauen
    args = _build_container_run_args(network=cfg.network_enabled)

    # Image
    image = _get_container_image()

    # Pakete vorinstallieren?
    all_packages = list(cfg.preinstalled_packages)
    if packages:
        all_packages.extend(packages)

    # Code mit Paket-Installation wrappen
    if all_packages and not cfg.custom_image:
        # Pakete installieren vor Code-Ausführung
        install_code = f"""
import subprocess
import sys
packages = {all_packages!r}
subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + packages,
               capture_output=True, check=False)

# User Code
{code}
"""
    else:
        install_code = code

    # Python-Befehl
    args.extend([image, "python", "-c", install_code])

    # Ausführen
    exit_code, stdout, stderr = await _run_container_command(
        args,
        timeout=timeout or cfg.timeout_seconds
    )

    execution_time = time.time() - start_time

    return {
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "exit_code": exit_code,
        "execution_time_seconds": round(execution_time, 2),
        "success": exit_code == 0
    }


async def docker_session_create(
    session_name: Optional[str] = None,
    packages: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Erstellt eine neue Docker-Sandbox-Session.

    In einer Session bleiben Variablen zwischen Aufrufen erhalten.
    Sessions werden automatisch nach Inaktivität beendet.

    Args:
        session_name: Optionaler Name für die Session
        packages: Zusätzliche Pakete zum Vorinstallieren

    Returns:
        Dict mit session_id, container_id, status

    Beispiel:
        docker_session_create(session_name="meine-tests")
        → {"session_id": "abc123", "status": "created"}
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert"}

    if not cfg.session_enabled:
        return {"error": "Sessions sind nicht aktiviert"}

    # Cleanup abgelaufener Sessions
    await _cleanup_expired_sessions()

    # Max Sessions prüfen
    async with _session_lock:
        if len(_sessions) >= cfg.max_sessions:
            return {
                "error": f"Max. {cfg.max_sessions} Sessions erlaubt. Schließe alte Sessions mit docker_session_close."
            }

    # Session-ID generieren
    session_id = session_name or f"sandbox-{uuid.uuid4().hex[:8]}"
    container_name = f"ai-sandbox-{session_id}"

    # Container starten (detached)
    args = _build_container_run_args(
        container_name=container_name,
        detach=True,
        network=cfg.network_enabled
    )

    image = _get_container_image()

    # Container mit endlos laufendem Prozess starten
    args.extend([image, "python", "-c", "import time; time.sleep(86400)"])

    exit_code, stdout, stderr = await _run_container_command(args, timeout=30)

    if exit_code != 0:
        return {
            "error": f"Container-Start fehlgeschlagen: {stderr}",
            "exit_code": exit_code
        }

    container_id = stdout.strip()[:12]  # Erste 12 Zeichen der Container-ID

    # Pakete installieren
    all_packages = list(cfg.preinstalled_packages)
    if packages:
        all_packages.extend(packages)

    if all_packages:
        success = await _ensure_packages_installed(container_name, all_packages)
        if not success:
            await _stop_container(container_name)
            return {"error": "Paket-Installation fehlgeschlagen"}

    # Session speichern
    session = SandboxSession(session_id, container_name)
    async with _session_lock:
        _sessions[session_id] = session

    return {
        "session_id": session_id,
        "container_id": container_id,
        "status": "created",
        "packages_installed": all_packages,
        "timeout_minutes": cfg.session_timeout_minutes
    }


async def docker_session_execute(
    session_id: str,
    code: str,
    timeout: Optional[int] = None
) -> Dict[str, Any]:
    """
    Führt Python-Code in einer bestehenden Session aus.

    Variablen aus vorherigen Aufrufen sind verfügbar.

    Args:
        session_id: ID der Session
        code: Python-Code zum Ausführen
        timeout: Timeout in Sekunden (optional)

    Returns:
        Dict mit stdout, stderr, exit_code, execution_count

    Beispiel:
        # Erster Aufruf:
        docker_session_execute(session_id="abc123", code="x = 42")
        # Zweiter Aufruf (x ist noch verfügbar):
        docker_session_execute(session_id="abc123", code="print(x * 2)")
        → {"stdout": "84", "exit_code": 0}
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert"}

    async with _session_lock:
        session = _sessions.get(session_id)
        if not session:
            return {"error": f"Session '{session_id}' nicht gefunden"}

        if session.is_expired(cfg.session_timeout_minutes):
            _sessions.pop(session_id, None)
            await _stop_container(session.container_id)
            return {"error": f"Session '{session_id}' ist abgelaufen"}

        session.touch()
        session.execution_count += 1
        exec_count = session.execution_count
        container_id = session.container_id

    start_time = time.time()

    # Code in bestehendem Container ausführen
    # Wir nutzen ein Python-Skript das in einer persistenten Shell läuft
    # Um Variablen zu erhalten, speichern wir den Zustand in einer Pickle-Datei

    # Escape für Python 3.11+ (backslash nicht erlaubt in f-string expression)
    escaped_code = code.replace('"', '\\"')

    wrapper_code = f'''
import pickle
import sys
import os

# Zustand laden
state_file = "/tmp/session_state.pkl"
if os.path.exists(state_file):
    with open(state_file, "rb") as f:
        globals().update(pickle.load(f))

# User-Code ausführen
try:
    exec("""{escaped_code}""")
except Exception as e:
    print(f"Error: {{type(e).__name__}}: {{e}}", file=sys.stderr)
    sys.exit(1)

# Zustand speichern (nur serialisierbare Objekte)
state = {{k: v for k, v in globals().items()
         if not k.startswith("_") and k not in ["pickle", "sys", "os", "state_file", "state"]}}
try:
    with open(state_file, "wb") as f:
        pickle.dump(state, f)
except Exception:
    pass  # Nicht-serialisierbare Objekte ignorieren
'''

    runtime = get_container_runtime()
    args = [
        runtime, "exec", container_id,
        "python", "-c", wrapper_code
    ]

    exit_code, stdout, stderr = await _run_container_command(
        args,
        timeout=timeout or cfg.timeout_seconds
    )

    execution_time = time.time() - start_time

    return {
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "exit_code": exit_code,
        "execution_time_seconds": round(execution_time, 2),
        "execution_count": exec_count,
        "session_id": session_id,
        "success": exit_code == 0
    }


async def docker_session_list() -> Dict[str, Any]:
    """
    Listet alle aktiven Sandbox-Sessions auf.

    Returns:
        Dict mit Liste der Sessions und deren Status
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert"}

    # Cleanup abgelaufener Sessions
    await _cleanup_expired_sessions()

    sessions_info = []
    async with _session_lock:
        for sid, session in _sessions.items():
            sessions_info.append({
                "session_id": sid,
                "created_at": session.created_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
                "execution_count": session.execution_count,
                "uploaded_files": session.uploaded_files,
                "expires_in_minutes": max(0, round(
                    (session.last_activity + timedelta(minutes=cfg.session_timeout_minutes) - datetime.now()).total_seconds() / 60
                ))
            })

    return {
        "sessions": sessions_info,
        "count": len(sessions_info),
        "max_sessions": cfg.max_sessions
    }


async def docker_session_close(session_id: str) -> Dict[str, Any]:
    """
    Schließt eine Sandbox-Session und entfernt den Container.

    Args:
        session_id: ID der zu schließenden Session

    Returns:
        Dict mit Status
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert"}

    async with _session_lock:
        session = _sessions.pop(session_id, None)

    if not session:
        return {"error": f"Session '{session_id}' nicht gefunden"}

    await _stop_container(session.container_id)

    return {
        "session_id": session_id,
        "status": "closed",
        "execution_count": session.execution_count
    }


async def docker_upload_file(
    session_id: str,
    filename: str,
    content_base64: str,
    target_path: str = "/workspace"
) -> Dict[str, Any]:
    """
    Lädt eine Datei in eine Sandbox-Session hoch.

    Args:
        session_id: ID der Session
        filename: Dateiname
        content_base64: Dateiinhalt als Base64-String
        target_path: Ziel-Verzeichnis im Container (default: /workspace)

    Returns:
        Dict mit Status und Pfad

    Beispiel:
        # CSV-Datei hochladen
        docker_upload_file(
            session_id="abc123",
            filename="data.csv",
            content_base64="bmFtZSxhZ2UKQWxpY2UsMzAKQm9iLDI1",
            target_path="/workspace"
        )
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert"}

    if not cfg.file_upload_enabled:
        return {"error": "Datei-Upload ist nicht aktiviert"}

    async with _session_lock:
        session = _sessions.get(session_id)
        if not session:
            return {"error": f"Session '{session_id}' nicht gefunden"}
        session.touch()
        container_id = session.container_id

    # Base64 dekodieren
    try:
        content = base64.b64decode(content_base64)
    except Exception as e:
        return {"error": f"Base64-Dekodierung fehlgeschlagen: {e}"}

    # Größe prüfen
    max_bytes = cfg.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        return {"error": f"Datei zu groß (max. {cfg.max_upload_size_mb} MB)"}

    # Temporäre Datei erstellen und in Container kopieren
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        target_file = f"{target_path.rstrip('/')}/{filename}"
        runtime = get_container_runtime()

        # container cp tmp_path container:/workspace/filename
        proc = await asyncio.create_subprocess_exec(
            runtime, "cp", tmp_path, f"{container_id}:{target_file}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return {"error": f"Kopieren fehlgeschlagen: {stderr.decode()}"}

        # Session aktualisieren
        async with _session_lock:
            if session_id in _sessions:
                _sessions[session_id].uploaded_files.append(target_file)

        return {
            "status": "uploaded",
            "filename": filename,
            "path": target_file,
            "size_bytes": len(content),
            "session_id": session_id
        }

    finally:
        os.unlink(tmp_path)


async def docker_list_packages() -> Dict[str, Any]:
    """
    Listet die vorinstallierten Python-Pakete auf.

    Zeigt sowohl die konfigurierten als auch die Standard-Bibliothek-Module.

    Returns:
        Dict mit Paket-Listen
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert"}

    # Konfigurierte Pakete
    preinstalled = cfg.preinstalled_packages

    # Standard-Bibliothek-Module (immer verfügbar)
    stdlib_modules = [
        "base64", "hashlib", "json", "re", "os", "sys", "io",
        "datetime", "collections", "itertools", "functools",
        "math", "random", "string", "struct", "csv", "xml",
        "urllib", "http", "html", "email", "typing", "pathlib",
        "tempfile", "shutil", "glob", "fnmatch", "pickle",
        "gzip", "zipfile", "tarfile", "logging", "unittest",
        "argparse", "configparser", "textwrap", "difflib",
        "uuid", "secrets", "hmac", "copy", "pprint", "enum"
    ]

    return {
        "preinstalled_packages": preinstalled,
        "stdlib_modules": stdlib_modules,
        "image": _get_container_image(),
        "note": "Zusätzliche Pakete können bei docker_execute_python mit 'packages' Parameter installiert werden"
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tool Registration
# ══════════════════════════════════════════════════════════════════════════════

def register_docker_tools(registry: ToolRegistry) -> int:
    """Registriert alle Docker-Sandbox-Tools."""

    if not settings.docker_sandbox.enabled:
        return 0

    tools = [
        ToolDefinition(
            name="docker_execute_python",
            description="""Führt Python-Code sicher in einem isolierten Docker-Container aus.

WANN VERWENDEN:
- Benutzer bittet um Code-Ausführung (z.B. "encodiere in Base64", "berechne Hash")
- Datenverarbeitung (JSON parsen, CSV verarbeiten, etc.)
- Mathematische Berechnungen
- String-Manipulationen
- Testen von Code-Snippets

BEISPIELE:
- "Encodiere 'Hello' in Base64" → code="import base64; print(base64.b64encode(b'Hello').decode())"
- "Berechne SHA256 von 'password'" → code="import hashlib; print(hashlib.sha256(b'password').hexdigest())"
- "Parse dieses JSON und extrahiere alle Namen" → code mit json.loads()

SICHERHEIT: Code läuft isoliert, kein Zugriff auf Host-System.""",
            parameters={
                "code": {
                    "type": "string",
                    "description": "Python-Code zum Ausführen. print() für Output verwenden.",
                    "required": True
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Zusätzliche pip-Pakete (optional)",
                    "required": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Sekunden (optional)",
                    "required": False
                }
            },
            handler=docker_execute_python,
            is_write_operation=False
        ),
        ToolDefinition(
            name="docker_session_create",
            description="""Erstellt eine persistente Sandbox-Session.

WANN VERWENDEN:
- Benutzer möchte mehrere zusammenhängende Operationen ausführen
- Variablen sollen zwischen Aufrufen erhalten bleiben
- Iteratives Arbeiten mit Daten

Nach Erstellung: docker_session_execute für Code-Ausführung verwenden.""",
            parameters={
                "session_name": {
                    "type": "string",
                    "description": "Optionaler Name für die Session",
                    "required": False
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Zusätzliche pip-Pakete zum Vorinstallieren",
                    "required": False
                }
            },
            handler=docker_session_create,
            is_write_operation=True
        ),
        ToolDefinition(
            name="docker_session_execute",
            description="""Führt Code in einer bestehenden Session aus.

Variablen aus vorherigen Aufrufen bleiben erhalten!

Beispiel-Workflow:
1. docker_session_execute(session_id="abc", code="data = [1,2,3]")
2. docker_session_execute(session_id="abc", code="print(sum(data))")  → "6"
""",
            parameters={
                "session_id": {
                    "type": "string",
                    "description": "ID der Session",
                    "required": True
                },
                "code": {
                    "type": "string",
                    "description": "Python-Code zum Ausführen",
                    "required": True
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Sekunden (optional)",
                    "required": False
                }
            },
            handler=docker_session_execute,
            is_write_operation=False
        ),
        ToolDefinition(
            name="docker_session_list",
            description="Listet alle aktiven Sandbox-Sessions mit Status und Ablaufzeit.",
            parameters={},
            handler=docker_session_list,
            is_write_operation=False
        ),
        ToolDefinition(
            name="docker_session_close",
            description="Schließt eine Session und gibt Ressourcen frei.",
            parameters={
                "session_id": {
                    "type": "string",
                    "description": "ID der zu schließenden Session",
                    "required": True
                }
            },
            handler=docker_session_close,
            is_write_operation=True
        ),
        ToolDefinition(
            name="docker_upload_file",
            description="""Lädt eine Datei in eine Sandbox-Session hoch.

WANN VERWENDEN:
- Benutzer möchte Datei verarbeiten (CSV parsen, Bild analysieren, etc.)
- Datei muss Base64-encodiert übergeben werden

Beispiel: Nach Upload kann die Datei im Code mit open('/workspace/file.csv') gelesen werden.""",
            parameters={
                "session_id": {
                    "type": "string",
                    "description": "ID der Session",
                    "required": True
                },
                "filename": {
                    "type": "string",
                    "description": "Dateiname",
                    "required": True
                },
                "content_base64": {
                    "type": "string",
                    "description": "Dateiinhalt als Base64-String",
                    "required": True
                },
                "target_path": {
                    "type": "string",
                    "description": "Ziel-Verzeichnis (default: /workspace)",
                    "required": False
                }
            },
            handler=docker_upload_file,
            is_write_operation=True
        ),
        ToolDefinition(
            name="docker_list_packages",
            description="Zeigt verfügbare Python-Pakete in der Sandbox (vorinstalliert + Standard-Bibliothek).",
            parameters={},
            handler=docker_list_packages,
            is_write_operation=False
        ),
    ]

    for tool in tools:
        registry.register(tool)

    return len(tools)


# ══════════════════════════════════════════════════════════════════════════════
# Cleanup bei Shutdown
# ══════════════════════════════════════════════════════════════════════════════

async def cleanup_all_sessions():
    """Beendet alle aktiven Sessions (für Shutdown)."""
    async with _session_lock:
        for session_id, session in list(_sessions.items()):
            await _stop_container(session.container_id)
            logger.debug("Docker sandbox session %s cleaned up", session_id)
        _sessions.clear()
