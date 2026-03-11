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


# Klassifizierungs-Regeln
COMMAND_PATTERNS = {
    # BLOCKED - Immer verboten
    "blocked": [
        (r"^git\s+", "Git-Befehle: Nutze die git_* Tools stattdessen"),
        (r"^rm\s+-rf\s+[/~]", "Gefährlicher rm-Befehl"),
        (r"^rm\s+-rf\s+\*", "Gefährlicher rm-Befehl"),
        (r"^sudo\b", "sudo nicht erlaubt"),
        (r"^su\s+", "su nicht erlaubt"),
        (r"^chmod\s+777", "chmod 777 nicht erlaubt"),
        (r"\|\s*(bash|sh|zsh|cmd)", "Pipe zu Shell nicht erlaubt"),
        (r">\s*/dev/sd", "Schreiben auf Blockdevice nicht erlaubt"),
        (r"^mkfs\b", "Filesystem-Kommandos nicht erlaubt"),
        (r"^dd\s+if=", "dd nicht erlaubt"),
        (r"^format\b", "format nicht erlaubt"),
        (r"curl.*\|\s*(bash|sh)", "curl | bash nicht erlaubt"),
        (r"wget.*\|\s*(bash|sh)", "wget | sh nicht erlaubt"),
    ],

    # READ_ONLY - Sicher, keine Änderungen
    "read_only": [
        r"^ls\b",
        r"^cat\b",
        r"^head\b",
        r"^tail\b",
        r"^grep\b",
        r"^find\b.*-type",
        r"^wc\b",
        r"^file\b",
        r"^which\b",
        r"^where\b",
        r"^type\b",
        r"^echo\b",
        r"^pwd\b",
        r"^env\b",
        r"^printenv\b",
        # Maven read-only
        r"^mvn\s+dependency:(tree|list|analyze)",
        r"^mvn\s+help:",
        r"^mvn\s+-v",
        r"^mvn\s+--version",
        # NPM read-only
        r"^npm\s+(list|ls|outdated|audit|view|info)",
        r"^npm\s+-v",
        r"^npm\s+--version",
        # Pip read-only
        r"^pip\s+(list|show|freeze|check)",
        r"^pip\s+-V",
        r"^pip\s+--version",
        # Python version
        r"^python\s+--version",
        r"^python3\s+--version",
        # Curl GET (ohne -X POST etc.)
        r"^curl\s+(?!.*-X\s*(POST|PUT|DELETE|PATCH))(?!.*--data)(?!.*-d\s)",
    ],

    # BUILD - Build-Operationen
    "build": [
        r"^mvn\s+(clean|compile|package|install|verify)",
        r"^gradle\s+(clean|build|assemble|check)",
        r"^npm\s+run\b",
        r"^npm\s+build\b",
        r"^pip\s+install\b",
        r"^npm\s+install\b(?!.*-g)",  # Nicht global
        r"^python\s+setup\.py\s+(build|install)",
    ],

    # TEST - Test-Ausführung
    "test": [
        r"^pytest\b",
        r"^python\s+-m\s+pytest",
        r"^npm\s+test\b",
        r"^mvn\s+test\b",
        r"^mvn\s+verify\b",
        r"^gradle\s+test\b",
        r"^python\s+-m\s+unittest",
    ],

    # SERVER - Server/Prozesse
    "server": [
        r"^uvicorn\b",
        r"^python\s+-m\s+uvicorn",
        r"^python\s+-m\s+http\.server",
        r"^flask\s+run",
        r"^npm\s+start\b",
        r"^node\b",
    ],
}


def classify_command(command: str) -> CommandClassification:
    """
    Klassifiziert einen Shell-Befehl nach Sicherheitsstufe.

    Args:
        command: Der zu klassifizierende Befehl

    Returns:
        CommandClassification mit Level und Details
    """
    command = command.strip()

    # BLOCKED prüfen
    for pattern, reason in COMMAND_PATTERNS["blocked"]:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandClassification(
                command=command,
                level=SafetyLevel.BLOCKED,
                category="blocked",
                can_container_test=False,
                requires_confirmation=False,
                block_reason=reason
            )

    # READ_ONLY prüfen
    for pattern in COMMAND_PATTERNS["read_only"]:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandClassification(
                command=command,
                level=SafetyLevel.READ_ONLY,
                category="utility",
                can_container_test=True,
                requires_confirmation=False
            )

    # BUILD prüfen
    for pattern in COMMAND_PATTERNS["build"]:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandClassification(
                command=command,
                level=SafetyLevel.LOCAL_WRITE,
                category="build",
                can_container_test=True,
                requires_confirmation=True
            )

    # TEST prüfen
    for pattern in COMMAND_PATTERNS["test"]:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandClassification(
                command=command,
                level=SafetyLevel.LOCAL_WRITE,
                category="test",
                can_container_test=True,
                requires_confirmation=True
            )

    # SERVER prüfen
    for pattern in COMMAND_PATTERNS["server"]:
        if re.search(pattern, command, re.IGNORECASE):
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
) -> Dict[str, Any]:
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
        Dict mit Ergebnis und execution_id für Eskalation
    """
    # Klassifizieren
    classification = classify_command(command)

    # Blockiert?
    if classification.level == SafetyLevel.BLOCKED:
        return {
            "success": False,
            "error": f"Befehl blockiert: {classification.block_reason}",
            "command": command,
            "safety_level": classification.level.name,
            "category": classification.category
        }

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

    # Ergebnis zusammenstellen
    return {
        **result,
        "execution_id": execution_id,
        "command": command,
        "safety_level": classification.level.name,
        "category": classification.category,
        "can_escalate_to_local": classification.requires_confirmation,
        "working_dir": working_dir,
        "hint": (
            "Nutze shell_execute_local mit dieser execution_id um den Befehl lokal auszuführen."
            if classification.requires_confirmation and result.get("success")
            else None
        )
    }


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
        new_result = await shell_execute(
            command=modified_command,
            working_dir=execution.container_result.get("working_dir") if execution.container_result else None,
            timeout=120
        )
        return ToolResult(
            success=new_result.get("success", False),
            data=new_result,
            error=new_result.get("error")
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


async def shell_list_executions() -> Dict[str, Any]:
    """
    Listet alle gecachten Shell-Ausführungen.

    Returns:
        Dict mit Liste der Ausführungen
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

    return {
        "count": len(executions),
        "executions": sorted(executions, key=lambda x: x["created_at"], reverse=True)
    }


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
