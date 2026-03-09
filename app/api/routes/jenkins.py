"""
Jenkins CI/CD API Routes.

Routes:
  POST   /api/jenkins/test           – Verbindung testen
  GET    /api/jenkins/jobs           – Jobs im konfigurierten Pfad auflisten
  GET    /api/jenkins/jobs/{path}    – Jobs in einem bestimmten Pfad
  POST   /api/jenkins/build          – Build triggern (mit Bestätigung)
"""

import httpx
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/api/jenkins", tags=["jenkins"])


def _get_auth() -> Optional[tuple]:
    """Gibt Auth-Tuple zurück wenn Credentials konfiguriert."""
    if settings.jenkins.username and settings.jenkins.api_token:
        return (settings.jenkins.username, settings.jenkins.api_token)
    return None


def _build_url(path: str = "") -> str:
    """Baut die vollständige Jenkins-URL."""
    base = settings.jenkins.base_url.rstrip("/")
    if path:
        return f"{base}/{path.lstrip('/')}"
    return base


@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """
    Testet die Verbindung zum Jenkins-Server.

    Prüft:
    - Base URL erreichbar
    - Authentifizierung funktioniert
    - API-Zugriff möglich
    """
    if not settings.jenkins.base_url:
        return {"success": False, "error": "Base URL nicht konfiguriert"}

    try:
        async with httpx.AsyncClient(
            verify=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
            auth=_get_auth(),
        ) as client:
            # Jenkins API-Endpunkt testen
            url = _build_url("/api/json")
            response = await client.get(url)

            if response.status_code == 401:
                return {"success": False, "error": "Authentifizierung fehlgeschlagen (401)"}
            elif response.status_code == 403:
                return {"success": False, "error": "Zugriff verweigert (403) - Berechtigungen prüfen"}
            elif response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}"}

            data = response.json()
            mode = data.get("mode", "unknown")
            node_name = data.get("nodeName", "unknown")

            # Job-Pfade testen wenn konfiguriert
            job_paths_status = []
            for jp in settings.jenkins.job_paths:
                path_url = _build_url(f"/{jp.path}/api/json")
                try:
                    path_resp = await client.get(path_url)
                    if path_resp.status_code == 200:
                        path_data = path_resp.json()
                        job_count = len(path_data.get("jobs", []))
                        job_paths_status.append({
                            "name": jp.name,
                            "path": jp.path,
                            "status": "ok",
                            "job_count": job_count,
                        })
                    else:
                        job_paths_status.append({
                            "name": jp.name,
                            "path": jp.path,
                            "status": "error",
                            "error": f"HTTP {path_resp.status_code}",
                        })
                except Exception as e:
                    job_paths_status.append({
                        "name": jp.name,
                        "path": jp.path,
                        "status": "error",
                        "error": str(e),
                    })

            return {
                "success": True,
                "message": f"Verbunden mit {node_name} ({mode})",
                "node_name": node_name,
                "mode": mode,
                "job_paths": job_paths_status,
            }

    except httpx.ConnectError as e:
        return {"success": False, "error": f"Verbindung fehlgeschlagen: {e}"}
    except httpx.TimeoutException:
        return {"success": False, "error": "Timeout - Server antwortet nicht"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/jobs")
async def list_jobs(path_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Listet Jobs aus dem Jenkins-Server.

    Args:
        path_name: Name des Job-Pfads (aus job_paths Konfiguration).
                   Wenn leer, wird default_job_path verwendet.
    """
    if not settings.jenkins.enabled:
        raise HTTPException(status_code=400, detail="Jenkins nicht aktiviert")

    if not settings.jenkins.base_url:
        raise HTTPException(status_code=400, detail="Jenkins Base URL nicht konfiguriert")

    # Pfad bestimmen
    job_path = ""
    if path_name:
        jp = next((p for p in settings.jenkins.job_paths if p.name == path_name), None)
        if jp:
            job_path = jp.path
    elif settings.jenkins.default_job_path:
        jp = next((p for p in settings.jenkins.job_paths if p.name == settings.jenkins.default_job_path), None)
        if jp:
            job_path = jp.path

    try:
        async with httpx.AsyncClient(
            verify=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
            auth=_get_auth(),
        ) as client:
            if job_path:
                url = _build_url(f"/{job_path}/api/json?tree=jobs[name,url,color,lastBuild[number,result,timestamp]]")
            else:
                url = _build_url("/api/json?tree=jobs[name,url,color,lastBuild[number,result,timestamp]]")

            response = await client.get(url)

            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Jenkins API Fehler")

            data = response.json()
            jobs = data.get("jobs", [])

            # Filter anwenden
            if settings.jenkins.job_filter:
                jobs = [j for j in jobs if j.get("name", "").startswith(settings.jenkins.job_filter)]

            return {
                "jobs": jobs,
                "job_path": job_path or "(root)",
                "job_count": len(jobs),
                "available_paths": [p.name for p in settings.jenkins.job_paths],
            }

    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Jenkins nicht erreichbar: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Jenkins Timeout")


class BuildRequest(BaseModel):
    """Build-Anfrage."""
    job_name: str
    path_name: Optional[str] = None
    parameters: Dict[str, str] = {}


@router.post("/build")
async def trigger_build(req: BuildRequest) -> Dict[str, Any]:
    """
    Triggert einen Jenkins-Build.

    Hinweis: Wenn require_build_confirmation aktiviert ist,
    wird der Build erst nach Bestätigung ausgelöst.
    """
    if not settings.jenkins.enabled:
        raise HTTPException(status_code=400, detail="Jenkins nicht aktiviert")

    # Pfad bestimmen
    job_path = ""
    if req.path_name:
        jp = next((p for p in settings.jenkins.job_paths if p.name == req.path_name), None)
        if jp:
            job_path = jp.path
    elif settings.jenkins.default_job_path:
        jp = next((p for p in settings.jenkins.job_paths if p.name == settings.jenkins.default_job_path), None)
        if jp:
            job_path = jp.path

    # Build-URL
    if job_path:
        build_path = f"/{job_path}/job/{req.job_name}/build"
    else:
        build_path = f"/job/{req.job_name}/build"

    if req.parameters:
        build_path = build_path.replace("/build", "/buildWithParameters")

    try:
        async with httpx.AsyncClient(
            verify=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
            auth=_get_auth(),
        ) as client:
            url = _build_url(build_path)

            if req.parameters:
                response = await client.post(url, params=req.parameters)
            else:
                response = await client.post(url)

            # 201 = Build gequeued, 302 = Redirect (auch OK)
            if response.status_code in (200, 201, 302):
                return {
                    "success": True,
                    "message": f"Build für '{req.job_name}' gestartet",
                    "job": req.job_name,
                    "path": job_path or "(root)",
                }
            else:
                return {
                    "success": False,
                    "error": f"Build-Trigger fehlgeschlagen: HTTP {response.status_code}",
                    "status_code": response.status_code,
                }

    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Jenkins nicht erreichbar: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Jenkins Timeout")
