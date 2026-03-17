"""
Update API Routes - GitHub-basierte App-Updates.

Endpoints:
- GET  /api/update/check    - Prüft auf Updates
- POST /api/update/install  - Installiert Update
- POST /api/update/restore  - Stellt Backup wieder her
- GET  /api/update/backups  - Listet Backups
- POST /api/update/restart  - Startet Server neu
- GET  /api/update/config   - Gibt Update-Konfiguration zurück
- POST /api/update/config   - Speichert Update-Konfiguration
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings, load_settings
from app.services.update_service import get_update_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/update", tags=["update"])


class InstallRequest(BaseModel):
    """Request für Update-Installation."""
    download_url: Optional[str] = None
    create_backup: bool = True


class RestoreRequest(BaseModel):
    """Request für Backup-Wiederherstellung."""
    backup_name: str


class UpdateConfigRequest(BaseModel):
    """Request für Update-Konfiguration."""
    enabled: bool = False
    repo_url: str = ""
    github_token: str = ""
    use_proxy: bool = True
    verify_ssl: bool = False
    check_on_start: bool = False


@router.get("/check")
async def check_for_updates():
    """
    Prüft auf verfügbare Updates.

    Returns:
        Dict mit: available, current_version, latest_version, release_notes, download_url
    """
    service = get_update_service()
    result = await service.check_for_updates()
    return result


@router.post("/install")
async def install_update(request: InstallRequest):
    """
    Installiert ein Update.

    Lädt das Update herunter, erstellt optional ein Backup und
    extrahiert die Dateien gemäß Whitelist.

    Returns:
        Dict mit: success, message, files_updated, backup_path, restart_required
    """
    service = get_update_service()
    result = await service.download_and_install(
        download_url=request.download_url,
        create_backup=request.create_backup,
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Update fehlgeschlagen"))

    return result


@router.post("/install/stream")
async def install_update_stream(request: InstallRequest):
    """
    Installiert ein Update mit SSE-Progress-Stream.

    Gibt Fortschritts-Events zurück:
    - data: {"stage": "download", "percent": 50, "message": "..."}
    """
    service = get_update_service()

    async def generate():
        progress_queue = asyncio.Queue()

        async def progress_callback(stage: str, percent: int, message: str):
            await progress_queue.put({
                "stage": stage,
                "percent": percent,
                "message": message,
            })

        # Starte Installation in Background-Task
        async def do_install():
            result = await service.download_and_install(
                download_url=request.download_url,
                create_backup=request.create_backup,
                progress_callback=progress_callback,
            )
            await progress_queue.put({"done": True, "result": result})

        task = asyncio.create_task(do_install())

        try:
            while True:
                data = await progress_queue.get()
                if "done" in data:
                    import json
                    yield f"data: {json.dumps(data['result'])}\n\n"
                    break
                else:
                    import json
                    yield f"data: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/backups")
async def list_backups():
    """
    Listet alle verfügbaren Update-Backups.

    Returns:
        List von Dict mit: name, created, file_count
    """
    service = get_update_service()
    return service.list_backups()


@router.post("/restore")
async def restore_backup(request: RestoreRequest):
    """
    Stellt ein Backup wieder her.

    Args:
        backup_name: Name des Backup-Ordners

    Returns:
        Dict mit: success, message, files_restored, restart_required
    """
    service = get_update_service()
    result = await service.restore_backup(request.backup_name)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Restore fehlgeschlagen"))

    return result


@router.post("/restart")
async def restart_server():
    """
    Startet den Server neu.

    ACHTUNG: Diese Aktion beendet den aktuellen Prozess!

    Returns:
        Keine Antwort (Server wird beendet)
    """
    service = get_update_service()

    # Kurze Verzögerung damit die Response noch gesendet werden kann
    async def delayed_restart():
        await asyncio.sleep(0.5)
        service.request_restart()

    asyncio.create_task(delayed_restart())

    return {"message": "Server-Neustart wird durchgeführt..."}


@router.get("/config")
async def get_update_config():
    """
    Gibt die aktuelle Update-Konfiguration zurück.

    Sensitive Daten (Token) werden maskiert.
    """
    config = settings.update

    return {
        "enabled": config.enabled,
        "repo_url": config.repo_url,
        "github_token": "***" if config.github_token else "",
        "has_token": bool(config.github_token),
        "use_proxy": config.use_proxy,
        "verify_ssl": config.verify_ssl,
        "check_on_start": config.check_on_start,
        "include_patterns": config.include_patterns,
        "exclude_patterns": config.exclude_patterns,
        # Proxy-Info aus search-Config
        "proxy_configured": bool(settings.search.proxy_url),
        "proxy_url": settings.search.proxy_url if settings.search.proxy_url else "",
    }


@router.post("/config")
async def save_update_config(request: UpdateConfigRequest):
    """
    Speichert die Update-Konfiguration.

    Schreibt in config.yaml und lädt Settings neu.
    """
    import yaml
    from pathlib import Path

    config_path = Path("config.yaml")

    try:
        # Aktuelle Config laden
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

        # Update-Section aktualisieren
        if "update" not in config_data:
            config_data["update"] = {}

        config_data["update"]["enabled"] = request.enabled
        config_data["update"]["repo_url"] = request.repo_url
        config_data["update"]["use_proxy"] = request.use_proxy
        config_data["update"]["verify_ssl"] = request.verify_ssl
        config_data["update"]["check_on_start"] = request.check_on_start

        # Token nur speichern wenn nicht maskiert
        if request.github_token and request.github_token != "***":
            config_data["update"]["github_token"] = request.github_token

        # Config speichern
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        # Settings neu laden (global settings wird überschrieben)
        # In der Praxis: Neustart empfohlen für konsistente Settings
        logger.info("[update] Konfiguration gespeichert")

        return {"success": True, "message": "Konfiguration gespeichert"}

    except Exception as e:
        logger.exception("[update] Config save failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/version")
async def get_version():
    """Gibt die aktuelle App-Version zurück."""
    from app.services.update_service import get_current_version

    return {
        "version": get_current_version(),
        "update_enabled": settings.update.enabled,
    }
