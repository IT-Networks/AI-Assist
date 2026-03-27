"""
Sonatype IQ Server API Routes.

Routes:
  POST   /api/iq/test                              – Verbindung testen
  GET    /api/iq/applications                      – Applikationen auflisten
  GET    /api/iq/pending-waivers                   – Pending Waiver-Requests
  POST   /api/iq/pending-waivers/{id}/confirm      – Waiver bestätigen
  POST   /api/iq/pending-waivers/{id}/reject       – Waiver ablehnen
"""

import httpx
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/api/iq", tags=["iq-server"])


def _get_auth() -> Optional[tuple]:
    """Gibt Auth-Tuple zurück wenn Credentials konfiguriert."""
    if settings.iq_server.credential_ref:
        cred = settings.credentials.get(settings.iq_server.credential_ref)
        if cred:
            username = cred.username
            password = cred.password or cred.token
            if username and password:
                return (username, password)

    if settings.iq_server.username and settings.iq_server.api_token:
        return (settings.iq_server.username, settings.iq_server.api_token)
    return None


@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """
    Testet die Verbindung zum Sonatype IQ Server.

    Prüft:
    - Base URL erreichbar
    - Authentifizierung funktioniert
    - API-Zugriff möglich
    """
    if not settings.iq_server.base_url:
        return {"success": False, "error": "Base URL nicht konfiguriert"}

    try:
        async with httpx.AsyncClient(
            verify=settings.iq_server.verify_ssl,
            timeout=settings.iq_server.timeout_seconds,
            auth=_get_auth(),
        ) as client:
            base_url = settings.iq_server.base_url.rstrip("/")
            url = f"{base_url}/api/v2/applications"
            response = await client.get(url)

            if response.status_code == 401:
                return {"success": False, "error": "Authentifizierung fehlgeschlagen (401) - Username/Passcode prüfen"}
            elif response.status_code == 403:
                return {"success": False, "error": "Zugriff verweigert (403) - Berechtigungen prüfen"}
            elif response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}"}

            data = response.json()
            apps = data.get("applications", [])

            return {
                "success": True,
                "message": f"Verbunden mit IQ Server - {len(apps)} Applikation(en) gefunden",
                "application_count": len(apps),
            }

    except httpx.ConnectError as e:
        return {"success": False, "error": f"Verbindung fehlgeschlagen: {e}"}
    except httpx.TimeoutException:
        return {"success": False, "error": "Timeout - Server antwortet nicht"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/applications")
async def list_applications(filter: Optional[str] = None) -> Dict[str, Any]:
    """Listet alle Applikationen im IQ Server."""
    if not settings.iq_server.enabled:
        raise HTTPException(status_code=400, detail="Sonatype IQ Server nicht aktiviert")

    if not settings.iq_server.base_url:
        raise HTTPException(status_code=400, detail="IQ Server Base URL nicht konfiguriert")

    try:
        async with httpx.AsyncClient(
            verify=settings.iq_server.verify_ssl,
            timeout=settings.iq_server.timeout_seconds,
            auth=_get_auth(),
        ) as client:
            base_url = settings.iq_server.base_url.rstrip("/")
            response = await client.get(f"{base_url}/api/v2/applications")

            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="IQ Server API Fehler")

            data = response.json()
            apps = data.get("applications", [])

            if filter:
                apps = [a for a in apps if filter.lower() in (a.get("publicId", "") + a.get("name", "")).lower()]

            return {
                "applications": [
                    {
                        "publicId": a.get("publicId"),
                        "name": a.get("name"),
                        "id": a.get("id"),
                        "organizationId": a.get("organizationId"),
                    }
                    for a in apps
                ],
                "count": len(apps),
            }

    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"IQ Server nicht erreichbar: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="IQ Server Timeout")


@router.get("/pending-waivers")
async def get_pending_waivers() -> Dict[str, Any]:
    """Gibt alle pending Waiver-Requests zurück."""
    from app.agent.iq_tools import get_pending_waivers as _get_pending

    pending = _get_pending()
    return {
        "pending_count": len(pending),
        "waivers": list(pending.values()),
    }


@router.post("/pending-waivers/{waiver_id}/confirm")
async def confirm_pending_waiver(waiver_id: str) -> Dict[str, Any]:
    """Bestätigt einen pending Waiver und führt ihn aus."""
    from app.agent.iq_tools import confirm_waiver

    result = await confirm_waiver(waiver_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/pending-waivers/{waiver_id}/reject")
async def reject_pending_waiver(waiver_id: str) -> Dict[str, Any]:
    """Lehnt einen pending Waiver ab."""
    from app.agent.iq_tools import reject_waiver

    result = reject_waiver(waiver_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
