"""
Internal Fetch API - Intranet-URLs abrufen.

Routes:
  POST   /api/internal-fetch/fetch    – URL direkt abrufen
  POST   /api/internal-fetch/test     – Verbindungstest
  GET    /api/internal-fetch/config   – Konfiguration abrufen
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings


router = APIRouter(prefix="/api/internal-fetch", tags=["internal-fetch"])


# ── Request Models ─────────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    url: str


class TestRequest(BaseModel):
    url: Optional[str] = None  # Optional: spezifische URL testen


# ── Helper Functions ───────────────────────────────────────────────────────────

def _validate_url(url: str) -> tuple[bool, str]:
    """Validiert URL gegen erlaubte Base URLs (falls konfiguriert)."""
    if not url:
        return False, "URL darf nicht leer sein"

    # Keine Base URLs = alle URLs erlaubt
    if not settings.internal_fetch.base_urls:
        return True, ""

    url_lower = url.lower().strip()
    for prefix in settings.internal_fetch.base_urls:
        prefix_lower = prefix.lower().strip().rstrip("/")
        if url_lower.startswith(prefix_lower):
            return True, ""

    return False, f"URL nicht erlaubt. Erlaubte Prefixe: {settings.internal_fetch.base_urls}"


async def _do_fetch(url: str) -> Dict[str, Any]:
    """Führt den HTTP-Request aus."""
    import httpx
    import base64

    headers = {"User-Agent": "AI-Assist-InternalFetch/1.0"}

    # Auth Header
    cfg = settings.internal_fetch
    if cfg.auth_type == "basic" and cfg.auth_username and cfg.auth_password:
        credentials = f"{cfg.auth_username}:{cfg.auth_password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    elif cfg.auth_type == "bearer" and cfg.auth_token:
        headers["Authorization"] = f"Bearer {cfg.auth_token}"

    # Proxy
    proxy_config = {}
    if cfg.proxy_url:
        proxy_url = cfg.proxy_url.strip()
        if not proxy_url.startswith(("http://", "https://")):
            proxy_url = f"http://{proxy_url}"
        proxy_config = {"proxy": proxy_url}

    try:
        async with httpx.AsyncClient(
            timeout=cfg.timeout_seconds,
            verify=cfg.verify_ssl,
            follow_redirects=True,
            **proxy_config,
        ) as client:
            response = await client.get(url, headers=headers)

            content_type = response.headers.get("content-type", "")

            # Content begrenzen für API-Antwort
            content = response.text
            if len(content) > 10000:
                content = content[:10000] + f"\n... [+{len(response.text) - 10000} Zeichen]"

            return {
                "success": True,
                "status_code": response.status_code,
                "content_type": content_type,
                "content": content,
                "url": str(response.url),
            }

    except httpx.TimeoutException:
        return {
            "success": False,
            "error": f"Timeout nach {cfg.timeout_seconds} Sekunden",
        }
    except httpx.ConnectError as e:
        error_str = str(e).lower()
        if "ssl" in error_str or "certificate" in error_str:
            return {
                "success": False,
                "error": "SSL-Zertifikatsfehler. SSL-Verifizierung deaktivieren?",
            }
        return {"success": False, "error": f"Verbindungsfehler: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config() -> Dict[str, Any]:
    """Gibt die aktuelle Konfiguration zurück (Passwörter maskiert)."""
    cfg = settings.internal_fetch
    return {
        "enabled": cfg.enabled,
        "base_urls": cfg.base_urls,
        "verify_ssl": cfg.verify_ssl,
        "timeout_seconds": cfg.timeout_seconds,
        "auth_type": cfg.auth_type,
        "auth_username": cfg.auth_username,
        "auth_password": "***" if cfg.auth_password else "",
        "auth_token": "***" if cfg.auth_token else "",
        "proxy_url": cfg.proxy_url,
    }


@router.post("/fetch")
async def fetch_url(request: FetchRequest) -> Dict[str, Any]:
    """
    Ruft eine URL ab und gibt den Inhalt zurück.

    Die URL muss mit einer konfigurierten Base URL beginnen.
    """
    if not settings.internal_fetch.enabled:
        raise HTTPException(
            status_code=403,
            detail="Internal Fetch ist deaktiviert"
        )

    # URL validieren
    is_valid, error = _validate_url(request.url)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Request ausführen
    result = await _do_fetch(request.url)

    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=result.get("error", "Unbekannter Fehler")
        )

    return result


@router.post("/test")
async def test_connection(request: TestRequest = None) -> Dict[str, Any]:
    """
    Testet die Verbindung zu einer internen URL.

    Wenn keine URL angegeben, wird die erste konfigurierte Base URL getestet.
    Ohne Base URLs und ohne explizite URL wird ein einfacher Konfig-Check gemacht.
    """
    if not settings.internal_fetch.enabled:
        return {
            "success": False,
            "error": "Internal Fetch ist deaktiviert",
        }

    # Test-URL bestimmen
    test_url = None
    if request and request.url:
        test_url = request.url.strip()
    elif settings.internal_fetch.base_urls:
        test_url = settings.internal_fetch.base_urls[0].strip()
    else:
        # Keine Base URLs und keine Test-URL - nur Konfig-Check
        return {
            "success": True,
            "message": "Internal Fetch ist aktiviert (keine Base URL-Einschränkung)",
        }

    # URL validieren
    is_valid, error = _validate_url(test_url)
    if not is_valid:
        return {"success": False, "error": error}

    # Test durchführen
    result = await _do_fetch(test_url)

    if result.get("success"):
        return {
            "success": True,
            "message": f"Verbindung erfolgreich (Status {result['status_code']})",
            "url": result.get("url"),
            "status_code": result.get("status_code"),
            "content_type": result.get("content_type"),
        }
    else:
        return {
            "success": False,
            "error": result.get("error", "Verbindung fehlgeschlagen"),
        }
