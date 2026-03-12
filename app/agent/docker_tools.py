"""
Container Sandbox Tools - Sichere Python-Code-Ausführung in isolierten Containern.

Verwendet Podman in WSL2 Ubuntu (daemonless, native Linux-Container).

Features:
- Stateless Execution: Einmalige Code-Ausführung ohne Session
- Session-basiert: Variablen bleiben zwischen Aufrufen erhalten
- Datei-Upload: Dateien zur Verarbeitung in Container laden
- Netzwerkzugriff: HTTP-Requests möglich (lesend)
- Ressourcen-Limits: CPU, Memory, Timeout
- Image Building: Container-Images aus Dockerfile erstellen

Sicherheit:
- Isolierter Container
- Ressourcen-Limits
- Automatisches Cleanup
- Keine Privilegien-Eskalation

Container Runtime:
- Podman in WSL2 Ubuntu
- Native Linux-Container Performance
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

from app.agent.tools import ToolDefinition, ToolRegistry, ToolResult
from app.core.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# WSL Podman Runtime - Nur WSL Ubuntu mit Podman
# ══════════════════════════════════════════════════════════════════════════════

_wsl_validated: bool = False
_wsl_distro: Optional[str] = None
_podman_path_in_wsl: str = "/usr/bin/podman"
_runtime_version: Optional[str] = None


def _detect_wsl_podman() -> bool:
    """
    Erkennt WSL Ubuntu mit Podman.

    Returns:
        True wenn WSL Podman verfügbar ist
    """
    global _wsl_validated, _wsl_distro, _podman_path_in_wsl, _runtime_version
    import subprocess

    if _wsl_validated:
        return _wsl_distro is not None

    cfg = settings.docker_sandbox
    wsl_cfg = cfg.wsl_integration

    # Distro aus Config oder Standard
    distro = wsl_cfg.distro_name or "Ubuntu"
    podman_path = wsl_cfg.podman_path_in_wsl or "/usr/bin/podman"

    # 1. Prüfe ob WSL verfügbar
    try:
        result = subprocess.run(
            ["wsl", "--status"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            logger.warning("WSL nicht verfügbar")
            _wsl_validated = True
            return False
    except Exception as e:
        logger.warning("WSL Check fehlgeschlagen: %s", e)
        _wsl_validated = True
        return False

    # 2. Prüfe ob Distro existiert
    try:
        result = subprocess.run(
            ["wsl", "-d", distro, "echo", "ok"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            logger.warning("WSL Distro '%s' nicht gefunden", distro)
            _wsl_validated = True
            return False
    except Exception as e:
        logger.warning("WSL Distro Check fehlgeschlagen: %s", e)
        _wsl_validated = True
        return False

    # 3. Prüfe ob Podman in Distro installiert
    try:
        result = subprocess.run(
            ["wsl", "-d", distro, podman_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            logger.warning("Podman nicht in WSL '%s' gefunden. Installiere mit: wsl -d %s sudo apt install podman", distro, distro)
            _wsl_validated = True
            return False
        _runtime_version = result.stdout.strip().split("\n")[0]
    except Exception as e:
        logger.warning("Podman Check fehlgeschlagen: %s", e)
        _wsl_validated = True
        return False

    # Erfolgreich!
    _wsl_distro = distro
    _podman_path_in_wsl = podman_path
    _wsl_validated = True
    logger.info("WSL Podman verfügbar: %s in %s", _runtime_version, distro)
    return True


def get_wsl_command_prefix() -> List[str]:
    """
    Gibt das WSL Podman Befehlspräfix zurück.

    Returns:
        ['wsl', '-d', 'Ubuntu', '/usr/bin/podman']
    """
    if not _detect_wsl_podman():
        raise RuntimeError(
            "WSL Podman nicht verfügbar.\n"
            "1. WSL installieren: wsl --install\n"
            "2. Ubuntu installieren: wsl --install Ubuntu\n"
            "3. Podman installieren: wsl -d Ubuntu sudo apt update && sudo apt install -y podman"
        )
    return ["wsl", "-d", _wsl_distro, _podman_path_in_wsl]


def get_container_runtime() -> str:
    """
    Gibt 'wsl-podman' zurück wenn verfügbar.

    DEPRECATED: Nutze get_wsl_command_prefix() für Befehle.
    """
    if _detect_wsl_podman():
        return "wsl-podman"
    raise RuntimeError("WSL Podman nicht verfügbar")


def get_runtime_info() -> Dict[str, Any]:
    """Gibt Informationen zur WSL Podman-Installation zurück."""
    available = _detect_wsl_podman()
    return {
        "runtime": "wsl-podman",
        "distro": _wsl_distro,
        "podman_path": _podman_path_in_wsl,
        "version": _runtime_version,
        "available": available
    }


def _windows_to_wsl_path(windows_path: str) -> str:
    """
    Konvertiert Windows-Pfad zu WSL-Pfad.

    C:\\Users\\marku\\code -> /mnt/c/Users/marku/code
    """
    if windows_path.startswith("/"):
        return windows_path
    path = windows_path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        rest = path[2:].lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return path


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
        cmd = get_wsl_command_prefix() + ["rm", "-f", container_id]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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


async def _check_image_exists(image: str) -> bool:
    """Prüft ob ein Container-Image lokal vorhanden ist."""
    try:
        cmd = get_wsl_command_prefix() + ["image", "inspect", image]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception as e:
        logger.warning(f"[sandbox] Error checking image {image}: {e}")
        return False


async def _pull_image(image: str) -> tuple[bool, str]:
    """
    Zieht ein Container-Image herunter.

    Returns:
        Tuple (success, message)
    """
    cmd = get_wsl_command_prefix() + ["pull", image]
    logger.info(f"[sandbox] Pulling image: {image}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)  # 5 min timeout

        if proc.returncode == 0:
            logger.info(f"[sandbox] Image pulled successfully: {image}")
            return True, f"Image {image} erfolgreich heruntergeladen"
        else:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.error(f"[sandbox] Failed to pull image {image}: {error_msg}")
            return False, f"Fehler beim Herunterladen: {error_msg}"

    except asyncio.TimeoutError:
        return False, "Timeout beim Herunterladen des Images (5 Minuten)"
    except Exception as e:
        return False, f"Fehler: {str(e)}"


async def _ensure_image_available(image: str) -> tuple[bool, str]:
    """
    Stellt sicher dass ein Image verfügbar ist, lädt es ggf. herunter.

    Returns:
        Tuple (success, message)
    """
    # Prüfen ob Image existiert
    if await _check_image_exists(image):
        logger.debug(f"[sandbox] Image exists: {image}")
        return True, f"Image {image} ist verfügbar"

    # Image herunterladen
    logger.info(f"[sandbox] Image not found locally, pulling: {image}")
    return await _pull_image(image)


def _build_container_run_args(
    container_name: Optional[str] = None,
    detach: bool = False,
    network: bool = True,
    workdir: str = "/workspace"
) -> List[str]:
    """Baut die Container-Run-Argumente auf (WSL Podman)."""
    cfg = settings.docker_sandbox
    args = get_wsl_command_prefix() + ["run"]

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

    prefix = get_wsl_command_prefix()

    # Prüfen ob pip verfügbar
    check_cmd = prefix + ["exec", container_id, "pip", "--version"]
    code, _, _ = await _run_container_command(check_cmd, timeout=10)
    if code != 0:
        return False

    # Pakete installieren
    install_cmd = prefix + ["exec", container_id, "pip", "install", "-q"] + packages
    code, stdout, stderr = await _run_container_command(install_cmd, timeout=120)
    return code == 0


async def _test_container_basic() -> Dict[str, Any]:
    """
    Einfacher Container-Test OHNE Paket-Installation.

    Prüft ob die Container-Runtime funktioniert und das Image verfügbar ist.
    Lädt das Image automatisch herunter falls es nicht existiert.

    Returns:
        Dict mit success, stdout, stderr, command (für Debug)
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return {"error": "Docker Sandbox ist nicht aktiviert", "success": False}

    start_time = time.time()

    try:
        prefix = get_wsl_command_prefix()
    except RuntimeError as e:
        return {"error": str(e), "success": False}

    # Image prüfen und ggf. herunterladen
    image = _get_container_image()
    image_ok, image_msg = await _ensure_image_available(image)
    if not image_ok:
        return {
            "error": f"Image nicht verfügbar: {image_msg}",
            "success": False,
            "image": image,
            "execution_time_seconds": round(time.time() - start_time, 2)
        }

    # Minimale Container-Argumente (ohne Paket-Installation)
    args = prefix + [
        "run", "--rm",
        "-m", cfg.memory_limit,
        "--cpus", str(cfg.cpu_limit),
        "--no-new-privileges",
        image,
        "python", "-c", "import sys; print(f'Python {sys.version}')"
    ]

    # Debug: Befehl loggen
    command_str = " ".join(args)
    logger.info(f"[sandbox] Test command: {command_str}")

    exit_code, stdout, stderr = await _run_container_command(args, timeout=30)

    execution_time = time.time() - start_time

    return {
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "exit_code": exit_code,
        "execution_time_seconds": round(execution_time, 2),
        "success": exit_code == 0,
        "command": command_str  # Für Debug im Frontend
    }


# ══════════════════════════════════════════════════════════════════════════════
# Image Building
# ══════════════════════════════════════════════════════════════════════════════

async def podman_build_image(
    context_path: str,
    image_name: str,
    dockerfile: str = "Dockerfile",
    build_args: Optional[Dict[str, str]] = None,
    no_cache: bool = False,
    timeout: int = 600
) -> ToolResult:
    """
    Baut ein Container-Image aus einem Dockerfile.

    Args:
        context_path: Pfad zum Build-Kontext (Verzeichnis mit Dockerfile)
        image_name: Name für das Image (z.B. "myapp:latest")
        dockerfile: Name des Dockerfiles (default: "Dockerfile")
        build_args: Build-Argumente (z.B. {"VERSION": "1.0"})
        no_cache: Wenn True, wird der Cache ignoriert
        timeout: Timeout in Sekunden (default: 600 = 10 Minuten)

    Returns:
        ToolResult mit Build-Output und Image-ID

    Beispiel:
        podman_build_image(
            context_path="/path/to/project",
            image_name="myapp:v1.0",
            build_args={"ENV": "production"}
        )
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Container Sandbox ist nicht aktiviert")

    # Pfad validieren
    context_path_obj = Path(context_path)
    if not context_path_obj.exists():
        return ToolResult(success=False, error=f"Build-Kontext nicht gefunden: {context_path}")

    if not context_path_obj.is_dir():
        return ToolResult(success=False, error=f"Build-Kontext muss ein Verzeichnis sein: {context_path}")

    # Dockerfile prüfen
    dockerfile_path = context_path_obj / dockerfile
    if not dockerfile_path.exists():
        return ToolResult(
            success=False,
            error=f"Dockerfile nicht gefunden: {dockerfile_path}\n"
                  f"Vorhandene Dateien: {', '.join(f.name for f in context_path_obj.iterdir() if f.is_file())[:200]}"
        )

    start_time = time.time()

    try:
        prefix = get_wsl_command_prefix()
    except RuntimeError as e:
        return ToolResult(success=False, error=str(e))

    # Build-Befehl zusammenbauen
    args = prefix + ["build"]

    # Image-Name
    args.extend(["-t", image_name])

    # Dockerfile (wenn nicht Standard)
    if dockerfile != "Dockerfile":
        args.extend(["-f", str(dockerfile_path)])

    # Build-Argumente
    if build_args:
        for key, value in build_args.items():
            args.extend(["--build-arg", f"{key}={value}"])

    # Cache
    if no_cache:
        args.append("--no-cache")

    # Kontext-Pfad
    args.append(str(context_path_obj))

    # Debug: Befehl loggen
    command_str = " ".join(args)
    logger.info(f"[sandbox] Build command: {command_str}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # Stderr in stdout umleiten für vollständiges Build-Log
            cwd=str(context_path_obj)
        )

        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        build_output = stdout.decode("utf-8", errors="replace")

        execution_time = time.time() - start_time

        if proc.returncode == 0:
            # Image-ID ermitteln
            image_id = None
            inspect_cmd = prefix + ["image", "inspect", image_name, "--format", "{{.Id}}"]
            inspect_proc = await asyncio.create_subprocess_exec(
                *inspect_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            inspect_stdout, _ = await inspect_proc.communicate()
            if inspect_proc.returncode == 0:
                image_id = inspect_stdout.decode().strip()[:12]

            # Build-Output auf relevante Zeilen reduzieren (letzte 50 Zeilen)
            output_lines = build_output.strip().split("\n")
            if len(output_lines) > 50:
                summary = f"[... {len(output_lines) - 50} Zeilen ausgelassen ...]\n"
                summary += "\n".join(output_lines[-50:])
            else:
                summary = build_output.strip()

            return ToolResult(
                success=True,
                data=f"Image '{image_name}' erfolgreich gebaut!\n\nImage-ID: {image_id or 'unbekannt'}\n\n```\n{summary}\n```",
                metadata={
                    "image_name": image_name,
                    "image_id": image_id,
                    "execution_time_seconds": round(execution_time, 2),
                    "context_path": str(context_path_obj),
                    "dockerfile": dockerfile
                }
            )
        else:
            # Build fehlgeschlagen - zeige vollständigen Output
            return ToolResult(
                success=False,
                error=f"Build fehlgeschlagen (Exit Code {proc.returncode})",
                data=f"```\n{build_output.strip()[-5000:]}\n```",  # Letzte 5000 Zeichen
                metadata={
                    "exit_code": proc.returncode,
                    "execution_time_seconds": round(execution_time, 2),
                    "command": command_str
                }
            )

    except asyncio.TimeoutError:
        return ToolResult(
            success=False,
            error=f"Build-Timeout nach {timeout} Sekunden",
            metadata={"timeout_seconds": timeout}
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Build-Fehler: {str(e)}",
            metadata={"exception": type(e).__name__}
        )


async def podman_list_images(
    filter_name: Optional[str] = None
) -> ToolResult:
    """
    Listet alle lokalen Container-Images auf.

    Args:
        filter_name: Optional Filter für Image-Namen (z.B. "myapp")

    Returns:
        ToolResult mit Image-Liste
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Container Sandbox ist nicht aktiviert")

    try:
        prefix = get_wsl_command_prefix()
    except RuntimeError as e:
        return ToolResult(success=False, error=str(e))

    args = prefix + ["images", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.Created}}"]

    if filter_name:
        args.extend(["--filter", f"reference=*{filter_name}*"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            return ToolResult(success=False, error=f"Fehler: {stderr.decode()}")

        output = stdout.decode().strip()
        if not output:
            return ToolResult(success=True, data="Keine Images gefunden.")

        # Formatierte Ausgabe
        lines = output.split("\n")
        formatted = "| Image | ID | Größe | Erstellt |\n|-------|----|----- |----------|\n"
        for line in lines:
            parts = line.split("\t")
            if len(parts) >= 4:
                formatted += f"| {parts[0]} | {parts[1][:12]} | {parts[2]} | {parts[3]} |\n"

        return ToolResult(
            success=True,
            data=f"**{len(lines)} Image(s) gefunden:**\n\n{formatted}",
            metadata={"count": len(lines)}
        )

    except asyncio.TimeoutError:
        return ToolResult(success=False, error="Timeout beim Auflisten der Images")
    except Exception as e:
        return ToolResult(success=False, error=f"Fehler: {str(e)}")


async def podman_remove_image(
    image_name: str,
    force: bool = False
) -> ToolResult:
    """
    Entfernt ein Container-Image.

    Args:
        image_name: Name oder ID des Images
        force: Erzwingt das Löschen (auch wenn Container existieren)

    Returns:
        ToolResult mit Status
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Container Sandbox ist nicht aktiviert")

    try:
        prefix = get_wsl_command_prefix()
    except RuntimeError as e:
        return ToolResult(success=False, error=str(e))

    args = prefix + ["rmi"]
    if force:
        args.append("-f")
    args.append(image_name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        if proc.returncode == 0:
            return ToolResult(
                success=True,
                data=f"Image '{image_name}' erfolgreich entfernt."
            )
        else:
            return ToolResult(
                success=False,
                error=f"Fehler beim Entfernen: {stderr.decode().strip()}"
            )

    except asyncio.TimeoutError:
        return ToolResult(success=False, error="Timeout beim Entfernen des Images")
    except Exception as e:
        return ToolResult(success=False, error=f"Fehler: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# Tool Implementations
# ══════════════════════════════════════════════════════════════════════════════

async def docker_execute_python(
    code: str,
    packages: Optional[List[str]] = None,
    timeout: Optional[int] = None
) -> ToolResult:
    """
    Führt Python-Code in einem isolierten Docker-Container aus (stateless).

    Der Container wird nach Ausführung automatisch gelöscht.
    Für persistente Sessions verwende docker_session_create/docker_session_execute.

    Args:
        code: Python-Code zum Ausführen
        packages: Zusätzliche pip-Pakete (werden on-the-fly installiert)
        timeout: Timeout in Sekunden (optional, Default aus Config)

    Returns:
        ToolResult mit stdout, stderr, exit_code, execution_time

    Beispiel:
        docker_execute_python(code="import base64; print(base64.b64encode(b'Hello').decode())")
        → {"stdout": "SGVsbG8=", "exit_code": 0}
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

    start_time = time.time()

    # Image prüfen und ggf. herunterladen
    image = _get_container_image()
    image_ok, image_msg = await _ensure_image_available(image)
    if not image_ok:
        return ToolResult(success=False, error=f"Image nicht verfügbar: {image_msg}")

    # Docker-Befehl aufbauen
    args = _build_container_run_args(network=cfg.network_enabled)

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

    output = stdout.strip()
    error_output = stderr.strip()

    if exit_code == 0:
        return ToolResult(
            success=True,
            data=f"```\n{output}\n```" if output else "(Keine Ausgabe)",
            metadata={
                "exit_code": exit_code,
                "execution_time_seconds": round(execution_time, 2),
                "stderr": error_output if error_output else None
            }
        )
    else:
        return ToolResult(
            success=False,
            error=f"Exit Code {exit_code}: {error_output or output}",
            data=output if output else None,
            metadata={
                "exit_code": exit_code,
                "execution_time_seconds": round(execution_time, 2)
            }
        )


async def docker_session_create(
    session_name: Optional[str] = None,
    packages: Optional[List[str]] = None
) -> ToolResult:
    """
    Erstellt eine neue Docker-Sandbox-Session.

    In einer Session bleiben Variablen zwischen Aufrufen erhalten.
    Sessions werden automatisch nach Inaktivität beendet.

    Args:
        session_name: Optionaler Name für die Session
        packages: Zusätzliche Pakete zum Vorinstallieren

    Returns:
        ToolResult mit session_id, container_id, status

    Beispiel:
        docker_session_create(session_name="meine-tests")
        → {"session_id": "abc123", "status": "created"}
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

    if not cfg.session_enabled:
        return ToolResult(success=False, error="Sessions sind nicht aktiviert")

    # Cleanup abgelaufener Sessions
    await _cleanup_expired_sessions()

    # Max Sessions prüfen
    async with _session_lock:
        if len(_sessions) >= cfg.max_sessions:
            return ToolResult(
                success=False,
                error=f"Max. {cfg.max_sessions} Sessions erlaubt. Schließe alte Sessions mit docker_session_close."
            )

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
        return ToolResult(success=False, error=f"Container-Start fehlgeschlagen: {stderr}")

    container_id = stdout.strip()[:12]  # Erste 12 Zeichen der Container-ID

    # Pakete installieren
    all_packages = list(cfg.preinstalled_packages)
    if packages:
        all_packages.extend(packages)

    if all_packages:
        pkg_success = await _ensure_packages_installed(container_name, all_packages)
        if not pkg_success:
            await _stop_container(container_name)
            return ToolResult(success=False, error="Paket-Installation fehlgeschlagen")

    # Session speichern
    session = SandboxSession(session_id, container_name)
    async with _session_lock:
        _sessions[session_id] = session

    return ToolResult(
        success=True,
        data=f"Session '{session_id}' erstellt. Container-ID: {container_id}",
        metadata={
            "session_id": session_id,
            "container_id": container_id,
            "packages_installed": all_packages,
            "timeout_minutes": cfg.session_timeout_minutes
        }
    )


async def docker_session_execute(
    session_id: str,
    code: str,
    timeout: Optional[int] = None
) -> ToolResult:
    """
    Führt Python-Code in einer bestehenden Session aus.

    Variablen aus vorherigen Aufrufen sind verfügbar.

    Args:
        session_id: ID der Session
        code: Python-Code zum Ausführen
        timeout: Timeout in Sekunden (optional)

    Returns:
        ToolResult mit stdout, stderr, exit_code, execution_count

    Beispiel:
        # Erster Aufruf:
        docker_session_execute(session_id="abc123", code="x = 42")
        # Zweiter Aufruf (x ist noch verfügbar):
        docker_session_execute(session_id="abc123", code="print(x * 2)")
        → {"stdout": "84", "exit_code": 0}
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

    async with _session_lock:
        session = _sessions.get(session_id)
        if not session:
            return ToolResult(success=False, error=f"Session '{session_id}' nicht gefunden")

        if session.is_expired(cfg.session_timeout_minutes):
            _sessions.pop(session_id, None)
            await _stop_container(session.container_id)
            return ToolResult(success=False, error=f"Session '{session_id}' ist abgelaufen")

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

    prefix = get_wsl_command_prefix()
    args = prefix + [
        "exec", container_id,
        "python", "-c", wrapper_code
    ]

    exit_code, stdout, stderr = await _run_container_command(
        args,
        timeout=timeout or cfg.timeout_seconds
    )

    execution_time = time.time() - start_time

    output = stdout.strip()
    error_output = stderr.strip()

    if exit_code == 0:
        return ToolResult(
            success=True,
            data=f"```\n{output}\n```" if output else "(Keine Ausgabe)",
            metadata={
                "exit_code": exit_code,
                "execution_time_seconds": round(execution_time, 2),
                "execution_count": exec_count,
                "session_id": session_id,
                "stderr": error_output if error_output else None
            }
        )
    else:
        return ToolResult(
            success=False,
            error=f"Exit Code {exit_code}: {error_output or output}",
            data=output if output else None,
            metadata={
                "exit_code": exit_code,
                "execution_time_seconds": round(execution_time, 2),
                "execution_count": exec_count,
                "session_id": session_id
            }
        )


async def docker_session_list() -> ToolResult:
    """
    Listet alle aktiven Sandbox-Sessions auf.

    Returns:
        Dict mit Liste der Sessions und deren Status
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

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

    if sessions_info:
        info_text = "\n".join([f"- {s['session_id']}: {s['execution_count']} Ausführungen" for s in sessions_info])
        return ToolResult(success=True, data=f"Aktive Sessions ({len(sessions_info)}/{cfg.max_sessions}):\n{info_text}")
    else:
        return ToolResult(success=True, data="Keine aktiven Sessions")


async def docker_session_close(session_id: str) -> ToolResult:
    """
    Schließt eine Sandbox-Session und entfernt den Container.

    Args:
        session_id: ID der zu schließenden Session

    Returns:
        ToolResult mit Status
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

    async with _session_lock:
        session = _sessions.pop(session_id, None)

    if not session:
        return ToolResult(success=False, error=f"Session '{session_id}' nicht gefunden")

    await _stop_container(session.container_id)

    return ToolResult(
        success=True,
        data=f"Session '{session_id}' geschlossen. {session.execution_count} Ausführungen waren aktiv."
    )


async def docker_upload_file(
    session_id: str,
    filename: str,
    content_base64: str,
    target_path: str = "/workspace"
) -> ToolResult:
    """
    Lädt eine Datei in eine Sandbox-Session hoch.

    Args:
        session_id: ID der Session
        filename: Dateiname
        content_base64: Dateiinhalt als Base64-String
        target_path: Ziel-Verzeichnis im Container (default: /workspace)

    Returns:
        ToolResult mit Status und Pfad

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
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

    if not cfg.file_upload_enabled:
        return ToolResult(success=False, error="Datei-Upload ist nicht aktiviert")

    async with _session_lock:
        session = _sessions.get(session_id)
        if not session:
            return ToolResult(success=False, error=f"Session '{session_id}' nicht gefunden")
        session.touch()
        container_id = session.container_id

    # Base64 dekodieren
    try:
        content = base64.b64decode(content_base64)
    except Exception as e:
        return ToolResult(success=False, error=f"Base64-Dekodierung fehlgeschlagen: {e}")

    # Größe prüfen
    max_bytes = cfg.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        return ToolResult(success=False, error=f"Datei zu groß (max. {cfg.max_upload_size_mb} MB)")

    # Temporäre Datei erstellen und in Container kopieren
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        target_file = f"{target_path.rstrip('/')}/{filename}"
        cmd = get_wsl_command_prefix() + ["cp", tmp_path, f"{container_id}:{target_file}"]

        # container cp tmp_path container:/workspace/filename
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return ToolResult(success=False, error=f"Kopieren fehlgeschlagen: {stderr.decode()}")

        # Session aktualisieren
        async with _session_lock:
            if session_id in _sessions:
                _sessions[session_id].uploaded_files.append(target_file)

        return ToolResult(
            success=True,
            data=f"Datei '{filename}' hochgeladen nach {target_file} ({len(content)} Bytes)"
        )

    finally:
        os.unlink(tmp_path)


async def docker_list_packages() -> ToolResult:
    """
    Listet die vorinstallierten Python-Pakete auf.

    Zeigt sowohl die konfigurierten als auch die Standard-Bibliothek-Module.

    Returns:
        ToolResult mit Paket-Listen
    """
    cfg = settings.docker_sandbox
    if not cfg.enabled:
        return ToolResult(success=False, error="Docker Sandbox ist nicht aktiviert")

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

    info = f"""**Image:** {_get_container_image()}

**Vorinstallierte Pakete:** {', '.join(preinstalled) if preinstalled else 'Keine'}

**Standard-Bibliothek:** {', '.join(stdlib_modules[:10])}... (und mehr)

_Zusätzliche Pakete können mit dem 'packages' Parameter bei docker_execute_python installiert werden._"""

    return ToolResult(success=True, data=info)


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
        # Image Building Tools
        ToolDefinition(
            name="podman_build_image",
            description="""Baut ein Container-Image aus einem Dockerfile.

WANN VERWENDEN:
- Benutzer möchte ein eigenes Container-Image erstellen
- Dockerfile liegt im Projekt vor
- Custom Image für Sandbox-Ausführung benötigt

BEISPIEL:
podman_build_image(
    context_path="/path/to/project",
    image_name="myapp:v1.0",
    build_args={"ENV": "production"}
)

Nach dem Build kann das Image mit docker_execute_python über custom_image verwendet werden.""",
            parameters={
                "context_path": {
                    "type": "string",
                    "description": "Pfad zum Build-Kontext (Verzeichnis mit Dockerfile)",
                    "required": True
                },
                "image_name": {
                    "type": "string",
                    "description": "Name für das Image (z.B. 'myapp:latest')",
                    "required": True
                },
                "dockerfile": {
                    "type": "string",
                    "description": "Name des Dockerfiles (default: 'Dockerfile')",
                    "required": False
                },
                "build_args": {
                    "type": "object",
                    "description": "Build-Argumente als Key-Value-Paare",
                    "required": False
                },
                "no_cache": {
                    "type": "boolean",
                    "description": "Wenn True, wird der Build-Cache ignoriert",
                    "required": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Sekunden (default: 600)",
                    "required": False
                }
            },
            handler=podman_build_image,
            is_write_operation=True
        ),
        ToolDefinition(
            name="podman_list_images",
            description="""Listet alle lokalen Container-Images auf.

Zeigt Name, ID, Größe und Erstellungsdatum.
Optional kann nach Namen gefiltert werden.""",
            parameters={
                "filter_name": {
                    "type": "string",
                    "description": "Optional: Filter für Image-Namen",
                    "required": False
                }
            },
            handler=podman_list_images,
            is_write_operation=False
        ),
        ToolDefinition(
            name="podman_remove_image",
            description="""Entfernt ein Container-Image.

WANN VERWENDEN:
- Nicht mehr benötigte Images aufräumen
- Speicherplatz freigeben
- Alte Versionen entfernen""",
            parameters={
                "image_name": {
                    "type": "string",
                    "description": "Name oder ID des zu löschenden Images",
                    "required": True
                },
                "force": {
                    "type": "boolean",
                    "description": "Erzwingt das Löschen (auch bei laufenden Containern)",
                    "required": False
                }
            },
            handler=podman_remove_image,
            is_write_operation=True
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
