"""
API Routes für Docker Sandbox.

Endpoints für:
- Konfiguration abrufen/aktualisieren
- Code direkt ausführen (ohne Agent)
- Session-Management
- Datei-Upload
- Verbindungstest
"""

import base64
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.core.config import settings


router = APIRouter(prefix="/api/docker-sandbox", tags=["docker-sandbox"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class ExecuteRequest(BaseModel):
    """Request für Code-Ausführung."""
    code: str
    packages: Optional[List[str]] = None
    timeout: Optional[int] = None


class SessionCreateRequest(BaseModel):
    """Request für Session-Erstellung."""
    session_name: Optional[str] = None
    packages: Optional[List[str]] = None


class SessionExecuteRequest(BaseModel):
    """Request für Session-Ausführung."""
    session_id: str
    code: str
    timeout: Optional[int] = None


class SessionCloseRequest(BaseModel):
    """Request für Session-Schließung."""
    session_id: str


class ConfigUpdateRequest(BaseModel):
    """Request für Config-Update."""
    enabled: Optional[bool] = None
    backend: Optional[str] = None           # "auto" | "docker" | "podman"
    docker_path: Optional[str] = None       # Pfad zu docker.exe
    podman_path: Optional[str] = None       # Pfad zu podman.exe
    image: Optional[str] = None
    custom_image: Optional[str] = None
    memory_limit: Optional[str] = None
    cpu_limit: Optional[float] = None
    timeout_seconds: Optional[int] = None
    max_output_bytes: Optional[int] = None
    network_enabled: Optional[bool] = None
    session_enabled: Optional[bool] = None
    session_timeout_minutes: Optional[int] = None
    max_sessions: Optional[int] = None
    file_upload_enabled: Optional[bool] = None
    max_upload_size_mb: Optional[int] = None
    preinstalled_packages: Optional[List[str]] = None
    read_only_filesystem: Optional[bool] = None
    drop_capabilities: Optional[bool] = None


# ══════════════════════════════════════════════════════════════════════════════
# Config Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/config")
async def get_config() -> Dict[str, Any]:
    """Gibt die aktuelle Docker-Sandbox-Konfiguration zurück."""
    cfg = settings.docker_sandbox
    return {
        "enabled": cfg.enabled,
        "backend": cfg.backend,
        "docker_path": cfg.docker_path,
        "podman_path": cfg.podman_path,
        "image": cfg.image,
        "custom_image": cfg.custom_image,
        "memory_limit": cfg.memory_limit,
        "cpu_limit": cfg.cpu_limit,
        "timeout_seconds": cfg.timeout_seconds,
        "max_output_bytes": cfg.max_output_bytes,
        "network_enabled": cfg.network_enabled,
        "session_enabled": cfg.session_enabled,
        "session_timeout_minutes": cfg.session_timeout_minutes,
        "max_sessions": cfg.max_sessions,
        "file_upload_enabled": cfg.file_upload_enabled,
        "max_upload_size_mb": cfg.max_upload_size_mb,
        "upload_directory": cfg.upload_directory,
        "preinstalled_packages": cfg.preinstalled_packages,
        "read_only_filesystem": cfg.read_only_filesystem,
        "drop_capabilities": cfg.drop_capabilities,
    }


@router.put("/config")
async def update_config(request: ConfigUpdateRequest) -> Dict[str, Any]:
    """Aktualisiert die Docker-Sandbox-Konfiguration."""
    cfg = settings.docker_sandbox

    # Bei Änderung von backend/paths: Cache invalidieren
    paths_changed = False

    if request.enabled is not None:
        cfg.enabled = request.enabled
    if request.backend is not None:
        cfg.backend = request.backend
        paths_changed = True
    if request.docker_path is not None:
        cfg.docker_path = request.docker_path
        paths_changed = True
    if request.podman_path is not None:
        cfg.podman_path = request.podman_path
        paths_changed = True
    if request.image is not None:
        cfg.image = request.image
    if request.custom_image is not None:
        cfg.custom_image = request.custom_image
    if request.memory_limit is not None:
        cfg.memory_limit = request.memory_limit
    if request.cpu_limit is not None:
        cfg.cpu_limit = request.cpu_limit
    if request.timeout_seconds is not None:
        cfg.timeout_seconds = request.timeout_seconds
    if request.max_output_bytes is not None:
        cfg.max_output_bytes = request.max_output_bytes
    if request.network_enabled is not None:
        cfg.network_enabled = request.network_enabled
    if request.session_enabled is not None:
        cfg.session_enabled = request.session_enabled
    if request.session_timeout_minutes is not None:
        cfg.session_timeout_minutes = request.session_timeout_minutes
    if request.max_sessions is not None:
        cfg.max_sessions = request.max_sessions
    if request.file_upload_enabled is not None:
        cfg.file_upload_enabled = request.file_upload_enabled
    if request.max_upload_size_mb is not None:
        cfg.max_upload_size_mb = request.max_upload_size_mb
    if request.preinstalled_packages is not None:
        cfg.preinstalled_packages = request.preinstalled_packages
    if request.read_only_filesystem is not None:
        cfg.read_only_filesystem = request.read_only_filesystem
    if request.drop_capabilities is not None:
        cfg.drop_capabilities = request.drop_capabilities

    # Bei Pfad-Änderung: Runtime-Cache invalidieren
    if paths_changed:
        from app.agent.docker_tools import _container_runtime, _runtime_version
        import app.agent.docker_tools as docker_tools
        docker_tools._container_runtime = None
        docker_tools._runtime_version = None

    # Config speichern
    from app.api.routes.settings import _save_config
    _save_config()

    return {"status": "updated", "config": await get_config()}


# ══════════════════════════════════════════════════════════════════════════════
# Execution Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/execute")
async def execute_code(request: ExecuteRequest) -> Dict[str, Any]:
    """
    Führt Python-Code in einem isolierten Container aus (stateless).

    Der Container wird nach Ausführung automatisch gelöscht.
    """
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import docker_execute_python
    result = await docker_execute_python(
        code=request.code,
        packages=request.packages,
        timeout=request.timeout
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """
    Testet die Docker-Verbindung mit einem einfachen Python-Befehl.

    WICHTIG: Der Test überspringt die Paket-Installation um schneller zu sein.
    Für einen vollständigen Test mit Paketen nutze /execute mit packages=["requests"].
    """
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import _test_container_basic

    # Einfacher Test ohne Paket-Installation
    result = await _test_container_basic()

    return {
        "status": "ok" if result.get("success") else "error",
        "python_version": result.get("stdout", "").strip() if result.get("success") else None,
        "execution_time": result.get("execution_time_seconds"),
        "error": result.get("stderr") or result.get("error") if not result.get("success") else None,
        "command": result.get("command")  # Debug: Zeigt den ausgeführten Befehl
    }


@router.get("/runtime")
async def get_runtime_info() -> Dict[str, Any]:
    """
    Gibt Informationen zur erkannten Container-Runtime zurück.

    Zeigt ob Docker oder Podman verfügbar ist und welche Version.
    """
    from app.agent.docker_tools import get_runtime_info
    info = get_runtime_info()

    # Zusätzliche Info: Ist die Runtime erreichbar?
    if info["available"]:
        info["message"] = f"{info['runtime'].title()} ist verfügbar"
    else:
        info["message"] = "Keine Container-Runtime gefunden. Bitte Docker oder Podman installieren."
        info["help"] = {
            "podman": "https://podman.io/getting-started/installation (empfohlen, portable)",
            "docker": "https://docs.docker.com/get-docker/"
        }

    return info


@router.get("/image")
async def get_image_status() -> Dict[str, Any]:
    """
    Prüft ob das konfigurierte Container-Image verfügbar ist.
    """
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import _get_container_image, _check_image_exists

    image = _get_container_image()
    exists = await _check_image_exists(image)

    return {
        "image": image,
        "exists": exists,
        "message": f"Image '{image}' ist lokal verfügbar" if exists else f"Image '{image}' ist NICHT lokal vorhanden. Nutze POST /pull um es herunterzuladen."
    }


@router.post("/pull")
async def pull_image() -> Dict[str, Any]:
    """
    Lädt das konfigurierte Container-Image herunter.

    Dies kann einige Minuten dauern (je nach Image-Größe und Netzwerk).
    """
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import _get_container_image, _pull_image

    image = _get_container_image()
    success, message = await _pull_image(image)

    if not success:
        raise HTTPException(status_code=500, detail=message)

    return {
        "image": image,
        "success": True,
        "message": message
    }


# ══════════════════════════════════════════════════════════════════════════════
# Session Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/session/create")
async def create_session(request: SessionCreateRequest) -> Dict[str, Any]:
    """Erstellt eine neue Sandbox-Session."""
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import docker_session_create
    result = await docker_session_create(
        session_name=request.session_name,
        packages=request.packages
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@router.post("/session/execute")
async def execute_in_session(request: SessionExecuteRequest) -> Dict[str, Any]:
    """Führt Code in einer bestehenden Session aus."""
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import docker_session_execute
    result = await docker_session_execute(
        session_id=request.session_id,
        code=request.code,
        timeout=request.timeout
    )

    if "error" in result:
        raise HTTPException(status_code=404 if "nicht gefunden" in result["error"] else 500,
                          detail=result["error"])

    return result


@router.get("/session/list")
async def list_sessions() -> Dict[str, Any]:
    """Listet alle aktiven Sessions."""
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import docker_session_list
    return await docker_session_list()


@router.post("/session/close")
async def close_session(request: SessionCloseRequest) -> Dict[str, Any]:
    """Schließt eine Session."""
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import docker_session_close
    result = await docker_session_close(session_id=request.session_id)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result


# ══════════════════════════════════════════════════════════════════════════════
# File Upload Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/session/upload")
async def upload_file_to_session(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    target_path: str = Form("/workspace")
) -> Dict[str, Any]:
    """
    Lädt eine Datei in eine Sandbox-Session hoch.

    Die Datei wird automatisch Base64-encodiert.
    """
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    if not settings.docker_sandbox.file_upload_enabled:
        raise HTTPException(status_code=400, detail="Datei-Upload ist nicht aktiviert")

    # Datei lesen und Base64-encodieren
    content = await file.read()
    content_base64 = base64.b64encode(content).decode("utf-8")

    from app.agent.docker_tools import docker_upload_file
    result = await docker_upload_file(
        session_id=session_id,
        filename=file.filename,
        content_base64=content_base64,
        target_path=target_path
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Package Info Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/packages")
async def list_packages() -> Dict[str, Any]:
    """Listet verfügbare Python-Pakete."""
    if not settings.docker_sandbox.enabled:
        raise HTTPException(status_code=400, detail="Docker Sandbox ist nicht aktiviert")

    from app.agent.docker_tools import docker_list_packages
    return await docker_list_packages()
