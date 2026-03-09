"""
GitHub Enterprise API Routes.

Routes:
  POST   /api/github/test           – Verbindung testen
  GET    /api/github/repos          – Repositories der Organisation auflisten
"""

import httpx
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/api/github", tags=["github"])


def _parse_link_header(link_header: str) -> Dict[str, str]:
    """Parst den GitHub Link-Header für Pagination."""
    links = {}
    if not link_header:
        return links

    for part in link_header.split(","):
        match = re.match(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if match:
            links[match.group(2)] = match.group(1)
    return links


async def _github_request(
    method: str,
    url: str,
    token: str,
    verify_ssl: bool = False,
    timeout: int = 30,
    params: Optional[dict] = None,
) -> Dict[str, Any]:
    """Führt einen GitHub API Request aus."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
    }

    async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout) as client:
        try:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            return {
                "success": True,
                "data": response.json(),
                "status": response.status_code,
                "headers": dict(response.headers),
            }
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.json().get("message", "")
            except Exception:
                error_body = e.response.text[:200]
            return {"success": False, "error": f"HTTP {e.response.status_code}: {error_body}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Verbindungsfehler: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


async def _github_paginated_request(
    url: str,
    token: str,
    verify_ssl: bool = False,
    timeout: int = 30,
    params: Optional[dict] = None,
    max_items: int = 0,
) -> Dict[str, Any]:
    """
    Führt paginierte GitHub API Requests aus.

    Args:
        max_items: Maximale Anzahl Items (0 = alle)
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
    }

    all_data: List[Any] = []
    current_url = url
    page_count = 0
    max_pages = 100  # Sicherheitslimit

    async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout) as client:
        while current_url and page_count < max_pages:
            try:
                response = await client.get(
                    current_url,
                    headers=headers,
                    params=params if page_count == 0 else None,  # Params nur bei erster Seite
                )
                response.raise_for_status()

                data = response.json()
                if isinstance(data, list):
                    all_data.extend(data)
                else:
                    all_data.append(data)

                page_count += 1

                # Prüfen ob max_items erreicht
                if max_items > 0 and len(all_data) >= max_items:
                    all_data = all_data[:max_items]
                    break

                # Nächste Seite aus Link-Header
                link_header = response.headers.get("Link", "")
                links = _parse_link_header(link_header)
                current_url = links.get("next")

            except httpx.HTTPStatusError as e:
                error_body = ""
                try:
                    error_body = e.response.json().get("message", "")
                except Exception:
                    error_body = e.response.text[:200]
                return {"success": False, "error": f"HTTP {e.response.status_code}: {error_body}"}
            except httpx.RequestError as e:
                return {"success": False, "error": f"Verbindungsfehler: {e}"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    return {
        "success": True,
        "data": all_data,
        "pages_fetched": page_count,
        "total_items": len(all_data),
    }


@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """
    Testet die GitHub-Verbindung.

    Prüft:
    - Base URL erreichbar
    - Token gültig
    - Organisation existiert (wenn default_org gesetzt)
    """
    if not settings.github.base_url:
        return {"success": False, "error": "Base URL nicht konfiguriert"}

    if not settings.github.token:
        return {"success": False, "error": "Token nicht konfiguriert"}

    api_url = settings.github.get_api_url()

    # User-Info holen (prüft Token)
    user_result = await _github_request(
        method="GET",
        url=f"{api_url}/user",
        token=settings.github.token,
        verify_ssl=settings.github.verify_ssl,
        timeout=settings.github.timeout_seconds,
    )

    if not user_result["success"]:
        return {"success": False, "error": f"Token ungültig: {user_result['error']}"}

    user = user_result["data"]
    result = {
        "success": True,
        "message": f"Verbunden als {user.get('login', 'unknown')}",
        "user": user.get("login"),
        "api_url": api_url,
    }

    # Organisation testen wenn gesetzt
    if settings.github.default_org:
        org_result = await _github_request(
            method="GET",
            url=f"{api_url}/orgs/{settings.github.default_org}",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
        )

        if org_result["success"]:
            org = org_result["data"]
            result["org"] = {
                "name": org.get("login"),
                "description": org.get("description"),
                "public_repos": org.get("public_repos"),
                "status": "ok",
            }
        else:
            result["org"] = {
                "name": settings.github.default_org,
                "status": "error",
                "error": org_result["error"],
            }

    return result


@router.get("/repos")
async def list_repos(
    org: Optional[str] = None,
    max_repos: int = Query(0, description="Max. Repos (0 = alle)"),
    all_pages: bool = Query(True, description="Alle Seiten abrufen"),
) -> Dict[str, Any]:
    """
    Listet Repositories einer Organisation auf.

    Args:
        org: Organisation (wenn leer, wird default_org verwendet)
        max_repos: Maximale Anzahl Repos (0 = unbegrenzt)
        all_pages: Wenn True, werden alle Seiten abgerufen (Pagination)
    """
    if not settings.github.enabled:
        raise HTTPException(status_code=400, detail="GitHub nicht aktiviert")

    org_name = org or settings.github.default_org
    if not org_name:
        raise HTTPException(status_code=400, detail="Organisation nicht angegeben und default_org nicht konfiguriert")

    api_url = settings.github.get_api_url()

    if all_pages:
        # Paginierte Abfrage - alle Seiten
        result = await _github_paginated_request(
            url=f"{api_url}/orgs/{org_name}/repos",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": 100, "sort": "updated"},  # Max pro Seite für Effizienz
            max_items=max_repos,
        )
    else:
        # Einzelne Seite (wie bisher)
        result = await _github_request(
            method="GET",
            url=f"{api_url}/orgs/{org_name}/repos",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": max_repos or settings.github.max_items, "sort": "updated"},
        )

    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["error"])

    repos = []
    for repo in result["data"]:
        repos.append({
            "name": repo.get("name"),
            "full_name": repo.get("full_name"),
            "description": repo.get("description"),
            "private": repo.get("private"),
            "default_branch": repo.get("default_branch"),
            "open_issues_count": repo.get("open_issues_count"),
            "updated_at": repo.get("updated_at"),
        })

    response = {
        "org": org_name,
        "repo_count": len(repos),
        "repos": repos,
    }

    # Pagination-Info hinzufügen wenn verfügbar
    if "pages_fetched" in result:
        response["pages_fetched"] = result["pages_fetched"]

    return response
