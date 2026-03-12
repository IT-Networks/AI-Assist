"""
Shell Execution Tools - Sichere Shell-Befehlsausführung.

Container-First Ansatz:
1. Befehle werden zuerst im Container getestet
2. Bei Erfolg: Option zur lokalen Ausführung mit Bestätigung
3. Git-Befehle sind ausgeschlossen (nutze git_* Tools)

Unterstützte Befehle:
- Build-Tools: mvn, gradle, npm, pip
- Test-Ausführung: pytest, npm test, mvn test
- Server/Prozesse: uvicorn, python -m http.server
- Utilities: curl (GET), ls, cat, grep, find
"""

import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tools import ToolDefinition, ToolRegistry, ToolResult
from app.core.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Safety Classification
# ══════════════════════════════════════════════════════════════════════════════

class SafetyLevel(Enum):
    """Sicherheitsstufe eines Shell-Befehls."""
    READ_ONLY = 0        # Nur lesen - sicher im Container
    LOCAL_WRITE = 1      # Lokale Änderungen - Container-Test möglich
    SYSTEM_WRITE = 2     # System-Änderungen - Bestätigung erforderlich
    BLOCKED = 99         # Verboten


@dataclass
class CommandClassification:
    """Ergebnis der Befehlsklassifizierung."""
    command: str
    level: SafetyLevel
    category: str  # build, test, server, utility, blocked
    can_container_test: bool
    requires_confirmation: bool
    block_reason: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Pre-compiled Regex Patterns (Performance: avoid re-compilation on each call)
# ══════════════════════════════════════════════════════════════════════════════

# BLOCKED - Immer verboten (compiled pattern, reason)
_BLOCKED_PATTERNS = [
    (re.compile(r"^git\s+", re.IGNORECASE), "Git-Befehle: Nutze die git_* Tools stattdessen"),
    (re.compile(r"^rm\s+-rf\s+[/~]", re.IGNORECASE), "Gefährlicher rm-Befehl"),
    (re.compile(r"^rm\s+-rf\s+\*", re.IGNORECASE), "Gefährlicher rm-Befehl"),
    (re.compile(r"^sudo\b", re.IGNORECASE), "sudo nicht erlaubt"),
    (re.compile(r"^su\s+", re.IGNORECASE), "su nicht erlaubt"),
    (re.compile(r"^chmod\s+777", re.IGNORECASE), "chmod 777 nicht erlaubt"),
    (re.compile(r"\|\s*(bash|sh|zsh|cmd)", re.IGNORECASE), "Pipe zu Shell nicht erlaubt"),
    (re.compile(r">\s*/dev/sd", re.IGNORECASE), "Schreiben auf Blockdevice nicht erlaubt"),
    (re.compile(r"^mkfs\b", re.IGNORECASE), "Filesystem-Kommandos nicht erlaubt"),
    (re.compile(r"^dd\s+if=", re.IGNORECASE), "dd nicht erlaubt"),
    (re.compile(r"^format\b", re.IGNORECASE), "format nicht erlaubt"),
    (re.compile(r"curl.*\|\s*(bash|sh)", re.IGNORECASE), "curl | bash nicht erlaubt"),
    (re.compile(r"wget.*\|\s*(bash|sh)", re.IGNORECASE), "wget | sh nicht erlaubt"),
]

# LOCAL_ONLY_SAFE - Sichere System-Befehle die lokal ohne Bestätigung laufen
# NUR read-only Operationen wie Versionsabfragen und Status-Checks
_LOCAL_ONLY_SAFE_PATTERNS = [
    # Podman - nur sichere read-only Befehle
    re.compile(r"^podman\s+(--version|-v|version)$", re.IGNORECASE),
    re.compile(r"^podman\s+(ps|images|info|system\s+info)(\s|$)", re.IGNORECASE),
    re.compile(r"^podman\s+machine\s+(list|ls|info|inspect)(\s|$)", re.IGNORECASE),
    # Docker - nur sichere read-only Befehle
    re.compile(r"^docker\s+(--version|-v|version)$", re.IGNORECASE),
    re.compile(r"^docker\s+(ps|images|info|system\s+info)(\s|$)", re.IGNORECASE),
    # WSL - nur sichere read-only Befehle
    re.compile(r"^wsl\s+(--list|--status|-l)(\s|$)", re.IGNORECASE),
    re.compile(r"^wsl\s+--version$", re.IGNORECASE),
    # Kubectl - nur sichere read-only Befehle
    re.compile(r"^kubectl\s+(version|get|describe|logs)(\s|$)", re.IGNORECASE),
    # Journalctl - read-only
    re.compile(r"^journalctl\b", re.IGNORECASE),
]

# LOCAL_ONLY_DANGEROUS - System-Befehle die Bestätigung erfordern
# Diese können Daten löschen oder Systeme beeinflussen
_LOCAL_ONLY_DANGEROUS_PATTERNS = [
    # Podman - gefährliche Operationen
    (re.compile(r"^podman\s+(rm|rmi|kill|stop|prune|system\s+prune)", re.IGNORECASE),
     "Podman: Kann Container/Images löschen"),
    (re.compile(r"^podman\s+machine\s+(rm|stop|reset)", re.IGNORECASE),
     "Podman Machine: Kann VM stoppen/löschen"),
    # Docker - gefährliche Operationen
    (re.compile(r"^docker\s+(rm|rmi|kill|stop|prune|system\s+prune)", re.IGNORECASE),
     "Docker: Kann Container/Images löschen"),
    # WSL - gefährliche Operationen
    (re.compile(r"^wsl\s+--(shutdown|terminate|unregister)", re.IGNORECASE),
     "WSL: Kann Distributionen beenden/löschen"),
    # Kubectl - gefährliche Operationen
    (re.compile(r"^kubectl\s+(delete|apply|create|patch|edit)", re.IGNORECASE),
     "Kubectl: Kann Kubernetes-Ressourcen ändern/löschen"),
    # Systemctl - gefährliche Operationen
    (re.compile(r"^systemctl\s+(stop|restart|disable|mask)", re.IGNORECASE),
     "Systemctl: Kann Services stoppen/deaktivieren"),
    # Cloud CLIs - generell gefährlich
    (re.compile(r"^az\b", re.IGNORECASE), "Azure CLI: Kann Cloud-Ressourcen ändern"),
    (re.compile(r"^aws\b", re.IGNORECASE), "AWS CLI: Kann Cloud-Ressourcen ändern"),
    (re.compile(r"^gcloud\b", re.IGNORECASE), "GCloud CLI: Kann Cloud-Ressourcen ändern"),
]

# READ_ONLY - Sicher, keine Änderungen
_READ_ONLY_PATTERNS = [
    re.compile(r"^ls\b", re.IGNORECASE),
    re.compile(r"^cat\b", re.IGNORECASE),
    re.compile(r"^head\b", re.IGNORECASE),
    re.compile(r"^tail\b", re.IGNORECASE),
    re.compile(r"^grep\b", re.IGNORECASE),
    re.compile(r"^find\b.*-type", re.IGNORECASE),
    re.compile(r"^wc\b", re.IGNORECASE),
    re.compile(r"^file\b", re.IGNORECASE),
    re.compile(r"^which\b", re.IGNORECASE),
    re.compile(r"^where\b", re.IGNORECASE),
    re.compile(r"^type\b", re.IGNORECASE),
    re.compile(r"^echo\b", re.IGNORECASE),
    re.compile(r"^pwd\b", re.IGNORECASE),
    re.compile(r"^env\b", re.IGNORECASE),
    re.compile(r"^printenv\b", re.IGNORECASE),
    # Maven read-only
    re.compile(r"^mvn\s+dependency:(tree|list|analyze)", re.IGNORECASE),
    re.compile(r"^mvn\s+help:", re.IGNORECASE),
    re.compile(r"^mvn\s+-v", re.IGNORECASE),
    re.compile(r"^mvn\s+--version", re.IGNORECASE),
    # NPM read-only
    re.compile(r"^npm\s+(list|ls|outdated|audit|view|info)", re.IGNORECASE),
    re.compile(r"^npm\s+-v", re.IGNORECASE),
    re.compile(r"^npm\s+--version", re.IGNORECASE),
    # Pip read-only
    re.compile(r"^pip\s+(list|show|freeze|check)", re.IGNORECASE),
    re.compile(r"^pip\s+-V", re.IGNORECASE),
    re.compile(r"^pip\s+--version", re.IGNORECASE),
    # Python version
    re.compile(r"^python\s+--version", re.IGNORECASE),
    re.compile(r"^python3\s+--version", re.IGNORECASE),
    # Curl GET (ohne -X POST etc.)
    re.compile(r"^curl\s+(?!.*-X\s*(POST|PUT|DELETE|PATCH))(?!.*--data)(?!.*-d\s)", re.IGNORECASE),
]

# BUILD - Build-Operationen
_BUILD_PATTERNS = [
    re.compile(r"^mvn\s+(clean|compile|package|install|verify)", re.IGNORECASE),
    re.compile(r"^gradle\s+(clean|build|assemble|check)", re.IGNORECASE),
    re.compile(r"^npm\s+run\b", re.IGNORECASE),
    re.compile(r"^npm\s+build\b", re.IGNORECASE),
    re.compile(r"^pip\s+install\b", re.IGNORECASE),
    re.compile(r"^npm\s+install\b(?!.*-g)", re.IGNORECASE),  # Nicht global
    re.compile(r"^python\s+setup\.py\s+(build|install)", re.IGNORECASE),
]

# TEST - Test-Ausführung
_TEST_PATTERNS = [
    re.compile(r"^pytest\b", re.IGNORECASE),
    re.compile(r"^python\s+-m\s+pytest", re.IGNORECASE),
    re.compile(r"^npm\s+test\b", re.IGNORECASE),
    re.compile(r"^mvn\s+test\b", re.IGNORECASE),
    re.compile(r"^mvn\s+verify\b", re.IGNORECASE),
    re.compile(r"^gradle\s+test\b", re.IGNORECASE),
    re.compile(r"^python\s+-m\s+unittest", re.IGNORECASE),
]

# SERVER - Server/Prozesse
_SERVER_PATTERNS = [
    re.compile(r"^uvicorn\b", re.IGNORECASE),
    re.compile(r"^python\s+-m\s+uvicorn", re.IGNORECASE),
    re.compile(r"^python\s+-m\s+http\.server", re.IGNORECASE),
    re.compile(r"^flask\s+run", re.IGNORECASE),
    re.compile(r"^npm\s+start\b", re.IGNORECASE),
    re.compile(r"^node\b", re.IGNORECASE),
]


def classify_command(command: str) -> CommandClassification:
    """
    Klassifiziert einen Shell-Befehl nach Sicherheitsstufe.

    Args:
        command: Der zu klassifizierende Befehl

    Returns:
        CommandClassification mit Level und Details
    """
    command = command.strip()

    # BLOCKED prüfen (pre-compiled patterns)
    for pattern, reason in _BLOCKED_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.BLOCKED,
                category="blocked",
                can_container_test=False,
                requires_confirmation=False,
                block_reason=reason
            )

    # LOCAL_ONLY_SAFE prüfen - Sichere System-Befehle (read-only)
    for pattern in _LOCAL_ONLY_SAFE_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.READ_ONLY,
                category="local_system",
                can_container_test=False,  # NICHT im Container!
                requires_confirmation=False
            )

    # LOCAL_ONLY_DANGEROUS prüfen - Gefährliche System-Befehle (Bestätigung!)
    for pattern, reason in _LOCAL_ONLY_DANGEROUS_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.SYSTEM_WRITE,
                category="local_system_dangerous",
                can_container_test=False,  # NICHT im Container!
                requires_confirmation=True,
                block_reason=reason  # Wird als Warnung angezeigt
            )

    # READ_ONLY prüfen (pre-compiled patterns)
    for pattern in _READ_ONLY_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.READ_ONLY,
                category="utility",
                can_container_test=True,
                requires_confirmation=False
            )

    # BUILD prüfen (pre-compiled patterns)
    for pattern in _BUILD_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.LOCAL_WRITE,
                category="build",
                can_container_test=True,
                requires_confirmation=True
            )

    # TEST prüfen (pre-compiled patterns)
    for pattern in _TEST_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.LOCAL_WRITE,
                category="test",
                can_container_test=True,
                requires_confirmation=True
            )

    # SERVER prüfen (pre-compiled patterns)
    for pattern in _SERVER_PATTERNS:
        if pattern.search(command):
            return CommandClassification(
                command=command,
                level=SafetyLevel.LOCAL_WRITE,
                category="server",
                can_container_test=True,
                requires_confirmation=True
            )

    # Unbekannter Befehl - vorsichtshalber LOCAL_WRITE
    return CommandClassification(
        command=command,
        level=SafetyLevel.LOCAL_WRITE,
        category="unknown",
        can_container_test=True,
        requires_confirmation=True
    )


# ══════════════════════════════════════════════════════════════════════════════
# Execution State
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ShellExecution:
    """Speichert eine Shell-Ausführung für Eskalation."""
    execution_id: str
    command: str
    classification: CommandClassification
    container_result: Optional[Dict[str, Any]] = None
    local_result: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    escalated: bool = False


# Cache für Eskalation
_executions: Dict[str, ShellExecution] = {}
_max_cached_executions = 50


def _cleanup_old_executions():
    """Entfernt alte Execution-Einträge."""
    if len(_executions) > _max_cached_executions:
        # Älteste Hälfte entfernen
        sorted_ids = sorted(
            _executions.keys(),
            key=lambda k: _executions[k].created_at
        )
        for eid in sorted_ids[:len(sorted_ids) // 2]:
            del _executions[eid]


# ══════════════════════════════════════════════════════════════════════════════
# Container Execution
# ══════════════════════════════════════════════════════════════════════════════

def _get_container_runtime() -> Optional[str]:
    """Gibt die Container-Runtime zurück (docker oder podman)."""
    # Prüfe ob Docker-Sandbox aktiviert und Runtime verfügbar
    from app.agent.docker_tools import get_container_runtime
    return get_container_runtime()


def _get_shell_image() -> str:
    """Gibt das Shell-Image zurück."""
    cfg = settings.docker_sandbox
    # Nutze ein Image mit Shell-Utilities
    if cfg.custom_image:
        return cfg.custom_image
    # Default: Alpine mit bash für Shell-Befehle
    return "alpine:3.19"


async def _run_container_shell(
    command: str,
    working_dir: str = "/workspace",
    timeout: int = 120,
    mount_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Führt einen Shell-Befehl im Container aus.

    Args:
        command: Der auszuführende Shell-Befehl
        working_dir: Arbeitsverzeichnis im Container
        timeout: Timeout in Sekunden
        mount_path: Optionaler Pfad zum Mounten (read-only)

    Returns:
        Dict mit stdout, stderr, exit_code, duration
    """
    runtime = _get_container_runtime()
    if not runtime:
        return {
            "success": False,
            "error": "Keine Container-Runtime (Docker/Podman) verfügbar",
            "exit_code": -1
        }

    cfg = settings.docker_sandbox
    image = _get_shell_image()

    # Container-Argumente aufbauen
    args = [runtime, "run", "--rm"]

    # Ressourcen-Limits
    args.extend(["-m", cfg.memory_limit])
    args.extend(["--cpus", str(cfg.cpu_limit)])

    # Sicherheit
    args.append("--no-new-privileges")

    # Netzwerk (für curl etc.)
    if not cfg.network_enabled:
        args.append("--network=none")

    # Working Directory
    args.extend(["-w", working_dir])

    # Optional: Verzeichnis mounten (read-only für Sicherheit)
    if mount_path and os.path.isdir(mount_path):
        args.extend(["-v", f"{mount_path}:{working_dir}:ro"])

    # Image und Shell-Befehl
    args.extend([image, "/bin/sh", "-c", command])

    start_time = time.time()

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout
        )

        duration = time.time() - start_time

        # Output limitieren
        max_bytes = cfg.max_output_bytes
        stdout_str = stdout.decode("utf-8", errors="replace")[:max_bytes]
        stderr_str = stderr.decode("utf-8", errors="replace")[:max_bytes]

        return {
            "success": proc.returncode == 0,
            "stdout": stdout_str.strip(),
            "stderr": stderr_str.strip(),
            "exit_code": proc.returncode,
            "duration_seconds": round(duration, 2),
            "executed_in": "container"
        }

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "success": False,
            "error": f"Timeout nach {timeout} Sekunden",
            "exit_code": -1,
            "duration_seconds": timeout,
            "executed_in": "container"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "exit_code": -1,
            "executed_in": "container"
        }


# ══════════════════════════════════════════════════════════════════════════════
# Local Execution (mit Bestätigung)
# ══════════════════════════════════════════════════════════════════════════════

async def _run_local_shell(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = 300
) -> Dict[str, Any]:
    """
    Führt einen Shell-Befehl lokal aus.

    ACHTUNG: Diese Funktion sollte nur nach User-Bestätigung aufgerufen werden!

    Args:
        command: Der auszuführende Shell-Befehl
        working_dir: Arbeitsverzeichnis (optional)
        timeout: Timeout in Sekunden

    Returns:
        Dict mit stdout, stderr, exit_code, duration
    """
    start_time = time.time()

    # Shell ermitteln
    if os.name == "nt":
        shell = True  # Windows CMD
    else:
        shell = True  # Unix Shell

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout
        )

        duration = time.time() - start_time

        # Output limitieren
        max_bytes = 131072  # 128KB
        stdout_str = stdout.decode("utf-8", errors="replace")[:max_bytes]
        stderr_str = stderr.decode("utf-8", errors="replace")[:max_bytes]

        return {
            "success": proc.returncode == 0,
            "stdout": stdout_str.strip(),
            "stderr": stderr_str.strip(),
            "exit_code": proc.returncode,
            "duration_seconds": round(duration, 2),
            "executed_in": "local"
        }

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "success": False,
            "error": f"Timeout nach {timeout} Sekunden",
            "exit_code": -1,
            "duration_seconds": timeout,
            "executed_in": "local"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "exit_code": -1,
            "executed_in": "local"
        }


# ══════════════════════════════════════════════════════════════════════════════
# Tool Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def shell_execute(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = 120,
    mount_repo: bool = False
) -> ToolResult:
    """
    Führt einen Shell-Befehl im Container aus (Container-First).

    Der Befehl wird klassifiziert und im Container getestet.
    Bei Erfolg kann mit shell_execute_local() lokal eskaliert werden.

    Args:
        command: Der auszuführende Shell-Befehl
        working_dir: Arbeitsverzeichnis für lokale Eskalation
        timeout: Timeout in Sekunden
        mount_repo: True um Java/Python-Repo zu mounten (read-only)

    Returns:
        ToolResult mit Ergebnis und execution_id für Eskalation
    """
    # Klassifizieren
    classification = classify_command(command)

    # Blockiert?
    if classification.level == SafetyLevel.BLOCKED:
        return ToolResult(
            success=False,
            error=f"Befehl blockiert: {classification.block_reason}",
            data={
                "command": command,
                "safety_level": classification.level.name,
                "category": classification.category
            }
        )

    # LOCAL_ONLY Befehle (podman, docker, wsl, etc.)
    if not classification.can_container_test:
        # Gefährliche Befehle erfordern Bestätigung via shell_execute_local
        if classification.requires_confirmation:
            logger.debug("Gefährlicher lokaler Befehl, erfordert Bestätigung: %s", command)
            # Execution speichern für spätere Ausführung nach Bestätigung
            execution_id = str(uuid.uuid4())[:12]
            _cleanup_old_executions()
            execution = ShellExecution(
                execution_id=execution_id,
                command=command,
                classification=classification,
                container_result=None
            )
            _executions[execution_id] = execution

            return ToolResult(
                success=False,  # Noch nicht ausgeführt!
                data={
                    "execution_id": execution_id,
                    "command": command,
                    "safety_level": classification.level.name,
                    "category": classification.category,
                    "warning": classification.block_reason,
                    "requires_confirmation": True,
                    "executed_in": "pending",
                },
                error=f"⚠️ Gefährlicher Befehl: {classification.block_reason}. "
                      f"Nutze shell_execute_local(execution_id='{execution_id}') zur Bestätigung."
            )

        # Sichere lokale Befehle direkt ausführen
        logger.debug("Sicherer lokaler Befehl, führe aus: %s", command)
        result = await _run_local_shell(
            command=command,
            working_dir=working_dir,
            timeout=timeout
        )
        result["executed_in"] = "local"
    else:
        # Mount-Pfad ermitteln
        mount_path = None
        if mount_repo:
            # Versuche Java-Repo
            if settings.java.repo_path and os.path.isdir(settings.java.repo_path):
                mount_path = settings.java.repo_path
            # Fallback: Python-Repo
            elif settings.python.repo_path and os.path.isdir(settings.python.repo_path):
                mount_path = settings.python.repo_path

        # Im Container ausführen
        result = await _run_container_shell(
            command=command,
            timeout=timeout,
            mount_path=mount_path
        )

    # Execution speichern für Eskalation
    execution_id = str(uuid.uuid4())[:12]
    _cleanup_old_executions()

    execution = ShellExecution(
        execution_id=execution_id,
        command=command,
        classification=classification,
        container_result=result
    )
    _executions[execution_id] = execution

    # Hint für Eskalation
    hint = None
    if classification.requires_confirmation and result.get("success"):
        hint = "Nutze shell_execute_local mit dieser execution_id um den Befehl lokal auszuführen."

    # Ergebnis als ToolResult
    return ToolResult(
        success=result.get("success", False),
        data={
            "execution_id": execution_id,
            "command": command,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code", -1),
            "duration_seconds": result.get("duration_seconds", 0),
            "executed_in": result.get("executed_in", "container"),
            "safety_level": classification.level.name,
            "category": classification.category,
            "can_escalate_to_local": classification.requires_confirmation,
            "working_dir": working_dir,
            "hint": hint
        },
        error=result.get("error")
    )


async def shell_execute_local(
    execution_id: str,
    modified_command: Optional[str] = None
) -> ToolResult:
    """
    Führt einen zuvor getesteten Befehl lokal aus.

    ERFORDERT USER-BESTÄTIGUNG (is_write_operation=True)

    Wenn modified_command angegeben:
    → Befehl wird erst neu im Container getestet
    → Neue execution_id wird zurückgegeben

    Args:
        execution_id: ID einer vorherigen Container-Ausführung
        modified_command: Optional geänderter Befehl (führt zu neuem Container-Test)

    Returns:
        ToolResult mit Ausführungsergebnis
    """
    execution = _executions.get(execution_id)

    if not execution:
        return ToolResult(
            success=False,
            error=f"Execution '{execution_id}' nicht gefunden. Führe erst shell_execute aus."
        )

    # Befehl geändert? → Neuer Container-Test
    if modified_command and modified_command.strip() != execution.command:
        logger.debug("Befehl geändert - neuer Container-Test")
        # shell_execute returns ToolResult now
        return await shell_execute(
            command=modified_command,
            working_dir=execution.container_result.get("working_dir") if execution.container_result else None,
            timeout=120
        )

    # Bereits eskaliert?
    if execution.escalated:
        return ToolResult(
            success=False,
            error="Dieser Befehl wurde bereits lokal ausgeführt."
        )

    # Lokale Ausführung
    working_dir = None
    if execution.container_result:
        working_dir = execution.container_result.get("working_dir")

    # Versuche sinnvolles Working Directory
    if not working_dir:
        if settings.java.repo_path and os.path.isdir(settings.java.repo_path):
            working_dir = settings.java.repo_path
        elif settings.python.repo_path and os.path.isdir(settings.python.repo_path):
            working_dir = settings.python.repo_path

    result = await _run_local_shell(
        command=execution.command,
        working_dir=working_dir,
        timeout=300
    )

    # Execution aktualisieren
    execution.local_result = result
    execution.escalated = True

    return ToolResult(
        success=result.get("success", False),
        data={
            **result,
            "command": execution.command,
            "execution_id": execution_id,
            "working_dir": working_dir
        },
        error=result.get("error"),
        requires_confirmation=False  # Bestätigung war schon vorher
    )


async def shell_list_executions() -> ToolResult:
    """
    Listet alle gecachten Shell-Ausführungen.

    Returns:
        ToolResult mit Liste der Ausführungen
    """
    executions = []
    for eid, ex in _executions.items():
        executions.append({
            "execution_id": eid,
            "command": ex.command,
            "category": ex.classification.category,
            "safety_level": ex.classification.level.name,
            "container_success": ex.container_result.get("success") if ex.container_result else None,
            "escalated": ex.escalated,
            "created_at": ex.created_at.isoformat()
        })

    return ToolResult(
        success=True,
        data={
            "count": len(executions),
            "executions": sorted(executions, key=lambda x: x["created_at"], reverse=True)
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tool Registration
# ══════════════════════════════════════════════════════════════════════════════

def register_shell_tools(registry: ToolRegistry) -> int:
    """Registriert alle Shell-Tools."""

    # Prüfe ob Container-Runtime verfügbar
    if not settings.docker_sandbox.enabled:
        logger.info("Shell-Tools deaktiviert (docker_sandbox.enabled=false)")
        return 0

    tools = [
        ToolDefinition(
            name="shell_execute",
            description="""Führt einen Shell-Befehl sicher im Container aus (Container-First).

WANN VERWENDEN:
- Build-Befehle: mvn clean install, npm run build, gradle build
- Tests ausführen: pytest, npm test, mvn test
- Dependency-Infos: mvn dependency:tree, npm list, pip freeze
- Utilities: ls, cat, grep, curl (GET)

NICHT VERWENDEN FÜR:
- Git-Operationen → Nutze git_* Tools (git_status, git_commit, etc.)

WORKFLOW:
1. Befehl wird im Container getestet (sicher, isoliert)
2. Bei Erfolg: execution_id für lokale Eskalation
3. shell_execute_local() für lokale Ausführung (mit Bestätigung)

BEISPIELE:
- "mvn clean install" → Build im Container testen
- "npm test" → Tests im Container ausführen
- "pip freeze" → Installierte Pakete anzeigen""",
            parameters={
                "command": {
                    "type": "string",
                    "description": "Der auszuführende Shell-Befehl",
                    "required": True
                },
                "working_dir": {
                    "type": "string",
                    "description": "Arbeitsverzeichnis für lokale Eskalation",
                    "required": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Sekunden (default: 120)",
                    "required": False
                },
                "mount_repo": {
                    "type": "boolean",
                    "description": "Java/Python-Repo ins Container mounten (read-only)",
                    "required": False
                }
            },
            handler=shell_execute,
            is_write_operation=False  # Container-Test ist sicher
        ),

        ToolDefinition(
            name="shell_execute_local",
            description="""Führt einen zuvor getesteten Befehl LOKAL aus.

WICHTIG: Erfordert User-Bestätigung!

VORAUSSETZUNG:
- Vorheriger shell_execute() mit execution_id

WORKFLOW:
1. User sieht den Befehl und bestätigt
2. Befehl wird lokal ausgeführt
3. Ergebnis wird zurückgegeben

Wenn modified_command angegeben wird:
→ Neuer Container-Test wird durchgeführt
→ Neue execution_id wird zurückgegeben""",
            parameters={
                "execution_id": {
                    "type": "string",
                    "description": "ID einer vorherigen Container-Ausführung",
                    "required": True
                },
                "modified_command": {
                    "type": "string",
                    "description": "Optional: Geänderter Befehl (führt zu neuem Container-Test)",
                    "required": False
                }
            },
            handler=shell_execute_local,
            is_write_operation=True  # Lokale Ausführung erfordert Bestätigung
        ),

        ToolDefinition(
            name="shell_list_executions",
            description="Listet alle gecachten Shell-Ausführungen mit Status und execution_id.",
            parameters={},
            handler=shell_list_executions,
            is_write_operation=False
        ),
    ]

    for tool in tools:
        registry.register(tool)

    logger.info("Shell-Tools registriert: %d Tools", len(tools))
    return len(tools)
