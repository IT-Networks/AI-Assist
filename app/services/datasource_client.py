"""
HTTP-Client für interne Datenquellen.
Unterstützt Basic Auth, Bearer Token, API-Key, optionale SSL-Deaktivierung.
"""

import json
from typing import Any, Dict, Optional

import httpx

from app.core.config import DataSourceConfig


async def make_datasource_request(
    source: DataSourceConfig,
    path: str = "",
    method: str = "",
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Führt einen HTTP-Request gegen eine konfigurierte Datenquelle aus.

    Returns:
        {"success": True, "data": ..., "status": int}
        {"success": False, "error": str}
    """
    effective_method = (method or source.method or "GET").upper()

    # URL zusammenbauen
    base = source.base_url.rstrip("/")
    effective_path = path or source.endpoint_path or ""
    if effective_path and not effective_path.startswith("/"):
        effective_path = "/" + effective_path
    url = base + effective_path

    # Headers
    headers: Dict[str, str] = dict(source.custom_headers)

    # Authentifizierung
    auth = None
    if source.auth.type == "basic" and source.auth.username:
        auth = (source.auth.username, source.auth.password)
    elif source.auth.type == "bearer" and source.auth.bearer_token:
        headers["Authorization"] = f"Bearer {source.auth.bearer_token}"
    elif source.auth.type == "api_key" and source.auth.api_key_value:
        headers[source.auth.api_key_header] = source.auth.api_key_value

    try:
        async with httpx.AsyncClient(
            verify=source.verify_ssl,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            if effective_method == "GET":
                response = await client.get(url, params=params, headers=headers, auth=auth)
            elif effective_method == "POST":
                response = await client.post(url, params=params, json=body, headers=headers, auth=auth)
            elif effective_method == "PUT":
                response = await client.put(url, params=params, json=body, headers=headers, auth=auth)
            elif effective_method == "DELETE":
                response = await client.delete(url, params=params, headers=headers, auth=auth)
            else:
                response = await client.request(
                    effective_method, url, params=params, json=body, headers=headers, auth=auth
                )

        status = response.status_code
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type:
            try:
                data = response.json()
            except Exception:
                data = response.text
        else:
            data = response.text

        if response.is_error:
            preview = response.text[:500] if isinstance(data, str) else json.dumps(data)[:500]
            return {
                "success": False,
                "error": f"HTTP {status}: {preview}",
                "status": status,
            }

        return {"success": True, "data": data, "status": status}

    except httpx.SSLError as e:
        return {
            "success": False,
            "error": (
                f"SSL-Fehler: {e}. "
                "Tipp: 'SSL-Verifizierung deaktivieren' in der Datenquellen-Konfiguration."
            ),
        }
    except httpx.ConnectError as e:
        return {"success": False, "error": f"Verbindungsfehler zu {url}: {e}"}
    except httpx.TimeoutException:
        return {"success": False, "error": f"Timeout nach {timeout}s beim Verbinden mit {url}"}
    except Exception as e:
        return {"success": False, "error": f"Unbekannter Fehler: {e}"}
