"""
Agent-Tools zum Abrufen interner/Intranet-URLs.

Diese Tools erlauben dem Agent, interne HTTP-Endpunkte abzurufen,
z.B. interne APIs, Wikis, oder andere Intranet-Ressourcen.

Sicherheit: URLs werden gegen konfigurierte base_urls validiert.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def _validate_url(url: str, allowed_prefixes: List[str]) -> tuple[bool, str]:
    """
    Validiert eine URL gegen die erlaubten Prefixe.
    Wenn keine Prefixe konfiguriert sind, werden alle URLs erlaubt.

    Returns:
        Tuple (is_valid, error_message)
    """
    if not url:
        return False, "URL darf nicht leer sein"

    # Keine Base URLs konfiguriert = alle URLs erlaubt
    if not allowed_prefixes:
        return True, ""

    # URL normalisieren
    url_lower = url.lower().strip()

    # Prüfen ob URL mit einem erlaubten Prefix beginnt
    for prefix in allowed_prefixes:
        prefix_lower = prefix.lower().strip().rstrip("/")
        if url_lower.startswith(prefix_lower):
            return True, ""

    return False, (
        f"URL '{url}' ist nicht erlaubt. "
        f"Erlaubte Prefixe: {', '.join(allowed_prefixes)}"
    )


def _get_auth_headers(config) -> Dict[str, str]:
    """Erstellt Auth-Header basierend auf der Konfiguration."""
    headers = {}

    if config.auth_type == "basic" and config.auth_username and config.auth_password:
        import base64
        credentials = f"{config.auth_username}:{config.auth_password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"

    elif config.auth_type == "bearer" and config.auth_token:
        headers["Authorization"] = f"Bearer {config.auth_token}"

    return headers


def _get_proxy_config(config) -> Dict[str, Any]:
    """Erstellt Proxy-Konfiguration für httpx."""
    if not config.proxy_url:
        return {}

    proxy_url = config.proxy_url.strip()
    if not proxy_url.startswith(("http://", "https://")):
        proxy_url = f"http://{proxy_url}"

    return {"proxy": proxy_url}


async def _fetch_url(
    url: str,
    config,
    method: str = "GET",
    body: Optional[str] = None,
    custom_headers: Optional[Dict[str, str]] = None,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Führt den eigentlichen HTTP-Request aus.

    Args:
        url: Ziel-URL
        config: InternalFetchConfig
        method: HTTP-Methode (GET, POST, PUT, DELETE, PATCH)
        body: Request-Body (für POST/PUT/PATCH)
        custom_headers: Zusätzliche Header
        content_type: Content-Type für Body

    Returns:
        Dict mit status_code, content_type, content, error
    """
    headers = _get_auth_headers(config)
    headers["User-Agent"] = "AI-Assist-InternalFetch/1.0"

    # Custom Headers hinzufügen
    if custom_headers:
        headers.update(custom_headers)

    # Content-Type für Body
    if body and content_type:
        headers["Content-Type"] = content_type
    elif body and "Content-Type" not in headers:
        # Auto-detect: JSON wenn Body mit { oder [ beginnt
        body_stripped = body.strip()
        if body_stripped.startswith(("{", "[")):
            headers["Content-Type"] = "application/json"
        else:
            headers["Content-Type"] = "text/plain"

    proxy_config = _get_proxy_config(config)

    try:
        async with httpx.AsyncClient(
            timeout=config.timeout_seconds,
            verify=config.verify_ssl,
            follow_redirects=True,
            **proxy_config,
        ) as client:
            # Request ausführen
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=headers,
                content=body if body else None,
            )

            resp_content_type = response.headers.get("content-type", "")

            # Content lesen (Text oder JSON)
            if "application/json" in resp_content_type:
                try:
                    content = response.json()
                except Exception:
                    content = response.text
            else:
                content = response.text

            return {
                "status_code": response.status_code,
                "content_type": resp_content_type,
                "content": content,
                "url": str(response.url),
                "headers": dict(response.headers),
                "error": None,
            }

    except httpx.TimeoutException:
        return {
            "status_code": 0,
            "content_type": "",
            "content": "",
            "url": url,
            "error": f"Timeout nach {config.timeout_seconds} Sekunden",
        }
    except httpx.ConnectError as e:
        error_str = str(e).lower()
        if "ssl" in error_str or "certificate" in error_str:
            ssl_hint = (
                "SSL-Fehler. Falls selbstsigniertes Zertifikat: "
                "Settings → Internal Fetch → SSL-Verifizierung deaktivieren."
            )
            return {
                "status_code": 0,
                "content_type": "",
                "content": "",
                "url": url,
                "error": ssl_hint,
            }
        return {
            "status_code": 0,
            "content_type": "",
            "content": "",
            "url": url,
            "error": f"Verbindungsfehler: {e}",
        }
    except Exception as e:
        return {
            "status_code": 0,
            "content_type": "",
            "content": "",
            "url": url,
            "error": str(e),
        }


def register_internal_fetch_tools(registry: ToolRegistry) -> int:
    """
    Registriert die Internal Fetch Tools.

    Returns:
        Anzahl der registrierten Tools
    """
    from app.core.config import settings

    count = 0

    # ── internal_fetch ─────────────────────────────────────────────────────────

    async def internal_fetch(**kwargs: Any) -> ToolResult:
        """Ruft eine interne URL ab und gibt den Inhalt zurück."""
        url: str = kwargs.get("url", "").strip()

        if not settings.internal_fetch.enabled:
            return ToolResult(
                success=False,
                error=(
                    "Internal Fetch ist deaktiviert. "
                    "Aktiviere es in Settings → Internal Fetch."
                ),
            )

        # URL validieren
        is_valid, error = _validate_url(url, settings.internal_fetch.base_urls)
        if not is_valid:
            return ToolResult(success=False, error=error)

        # Request ausführen
        result = await _fetch_url(url, settings.internal_fetch)

        if result["error"]:
            return ToolResult(success=False, error=result["error"])

        # Erfolgreiche Antwort formatieren
        output = f"=== Internal Fetch: {url} ===\n"
        output += f"Status: {result['status_code']}\n"
        output += f"Content-Type: {result['content_type']}\n"
        output += f"URL (nach Redirects): {result['url']}\n\n"

        content = result["content"]
        if isinstance(content, dict) or isinstance(content, list):
            import json
            output += "```json\n"
            output += json.dumps(content, ensure_ascii=False, indent=2)
            output += "\n```"
        else:
            # HTML/Text - auf sinnvolle Länge kürzen
            content_str = str(content)
            if len(content_str) > 50000:
                output += content_str[:50000]
                output += f"\n\n... [+{len(content_str) - 50000} Zeichen abgeschnitten]"
            else:
                output += content_str

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="internal_fetch",
        description=(
            "Ruft eine beliebige URL ab und gibt den Inhalt zurück. "
            "Unterstützt HTTP/HTTPS, HTML, JSON und Text. "
            "Ideal für: Interne APIs, Intranet-Seiten, Wikis, externe Webseiten. "
            "Verwendet konfigurierten Proxy und Auth falls eingerichtet."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="url",
                type="string",
                description="Die abzurufende URL (HTTP oder HTTPS)",
                required=True,
            ),
        ],
        handler=internal_fetch,
    ))
    count += 1

    # ── internal_search ────────────────────────────────────────────────────────

    async def internal_search(**kwargs: Any) -> ToolResult:
        """Ruft eine interne URL ab und durchsucht den Inhalt nach einem Pattern."""
        url: str = kwargs.get("url", "").strip()
        pattern: str = kwargs.get("pattern", "").strip()
        context_lines: int = int(kwargs.get("context_lines", 3))

        if not settings.internal_fetch.enabled:
            return ToolResult(
                success=False,
                error=(
                    "Internal Fetch ist deaktiviert. "
                    "Aktiviere es in Settings → Internal Fetch."
                ),
            )

        if not pattern:
            return ToolResult(success=False, error="pattern darf nicht leer sein")

        # URL validieren
        is_valid, error = _validate_url(url, settings.internal_fetch.base_urls)
        if not is_valid:
            return ToolResult(success=False, error=error)

        # Request ausführen
        result = await _fetch_url(url, settings.internal_fetch)

        if result["error"]:
            return ToolResult(success=False, error=result["error"])

        content = result["content"]
        if isinstance(content, dict) or isinstance(content, list):
            import json
            content = json.dumps(content, ensure_ascii=False, indent=2)
        else:
            content = str(content)

        # Pattern suchen (case-insensitive)
        lines = content.splitlines()
        matches = []

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return ToolResult(success=False, error=f"Ungültiges Pattern: {e}")

        for i, line in enumerate(lines):
            if regex.search(line):
                # Kontext-Zeilen sammeln
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                context = lines[start:end]
                matches.append({
                    "line_number": i + 1,
                    "context": "\n".join(context),
                })

        output = f"=== Internal Search: {url} ===\n"
        output += f"Pattern: {pattern}\n"
        output += f"Status: {result['status_code']}\n\n"

        if not matches:
            output += f"Keine Treffer für '{pattern}' gefunden.\n"
        else:
            output += f"{len(matches)} Treffer gefunden:\n\n"
            for i, match in enumerate(matches[:20], 1):  # Max 20 Treffer
                output += f"--- Treffer {i} (Zeile {match['line_number']}) ---\n"
                output += match["context"]
                output += "\n\n"

            if len(matches) > 20:
                output += f"... und {len(matches) - 20} weitere Treffer"

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="internal_search",
        description=(
            "Ruft eine URL ab und durchsucht den Inhalt nach einem Regex-Pattern. "
            "Gibt passende Zeilen mit Kontext zurück. "
            "Ideal zum Suchen von Informationen in Webseiten, APIs, Dokumentationen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="url",
                type="string",
                description="Die abzurufende URL",
                required=True,
            ),
            ToolParameter(
                name="pattern",
                type="string",
                description="Regex-Pattern zum Suchen (case-insensitive)",
                required=True,
            ),
            ToolParameter(
                name="context_lines",
                type="integer",
                description="Anzahl Kontext-Zeilen vor/nach Treffer (default: 3)",
                required=False,
                default=3,
            ),
        ],
        handler=internal_search,
    ))
    count += 1

    # ── http_request (curl-Ersatz) ────────────────────────────────────────────

    async def http_request(**kwargs: Any) -> ToolResult:
        """Führt einen HTTP-Request aus (curl-Ersatz)."""
        import json as json_module

        url: str = kwargs.get("url", "").strip()
        method: str = kwargs.get("method", "GET").upper().strip()
        body: str = kwargs.get("body", "")
        headers_str: str = kwargs.get("headers", "")
        content_type_param: str = kwargs.get("content_type", "")

        if not settings.internal_fetch.enabled:
            return ToolResult(
                success=False,
                error=(
                    "Internal Fetch ist deaktiviert. "
                    "Aktiviere es in Settings → Internal Fetch."
                ),
            )

        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            return ToolResult(
                success=False,
                error=f"Ungültige HTTP-Methode: {method}. Erlaubt: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"
            )

        # URL validieren
        is_valid, error = _validate_url(url, settings.internal_fetch.base_urls)
        if not is_valid:
            return ToolResult(success=False, error=error)

        # Headers parsen (Format: "Header1: Value1\nHeader2: Value2" oder JSON)
        custom_headers = {}
        if headers_str:
            headers_str = headers_str.strip()
            if headers_str.startswith("{"):
                # JSON-Format
                try:
                    custom_headers = json_module.loads(headers_str)
                except json_module.JSONDecodeError as e:
                    return ToolResult(success=False, error=f"Ungültiges Header-JSON: {e}")
            else:
                # Zeilenformat: "Header: Value"
                for line in headers_str.split("\n"):
                    line = line.strip()
                    if ":" in line:
                        key, val = line.split(":", 1)
                        custom_headers[key.strip()] = val.strip()

        # Request ausführen
        result = await _fetch_url(
            url=url,
            config=settings.internal_fetch,
            method=method,
            body=body if body else None,
            custom_headers=custom_headers if custom_headers else None,
            content_type=content_type_param if content_type_param else None,
        )

        if result["error"]:
            return ToolResult(success=False, error=result["error"])

        # Erfolgreiche Antwort formatieren
        output = f"=== HTTP {method} {url} ===\n"
        output += f"Status: {result['status_code']}\n"
        output += f"Content-Type: {result['content_type']}\n"

        # Response Headers (optional, gekürzt)
        if result.get("headers"):
            important_headers = ["content-length", "server", "date", "location", "set-cookie"]
            resp_headers = {k: v for k, v in result["headers"].items() if k.lower() in important_headers}
            if resp_headers:
                output += f"Headers: {resp_headers}\n"

        output += f"\n"

        content = result["content"]
        if isinstance(content, dict) or isinstance(content, list):
            output += "```json\n"
            output += json_module.dumps(content, ensure_ascii=False, indent=2)
            output += "\n```"
        else:
            content_str = str(content)
            if len(content_str) > 50000:
                output += content_str[:50000]
                output += f"\n\n... [+{len(content_str) - 50000} Zeichen abgeschnitten]"
            else:
                output += content_str

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="http_request",
        description=(
            "Führt HTTP-Requests aus (curl-Ersatz). "
            "Unterstützt alle Methoden: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS. "
            "Mit Body (JSON/Text) und Custom Headers. "
            "Für: REST-APIs, Webhooks, Service-Tests, Daten senden/empfangen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="url",
                type="string",
                description="Ziel-URL",
                required=True,
            ),
            ToolParameter(
                name="method",
                type="string",
                description="HTTP-Methode: GET, POST, PUT, DELETE, PATCH (default: GET)",
                required=False,
                default="GET",
                enum=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            ),
            ToolParameter(
                name="body",
                type="string",
                description="Request-Body (für POST/PUT/PATCH). JSON wird automatisch erkannt.",
                required=False,
            ),
            ToolParameter(
                name="headers",
                type="string",
                description="Custom Headers als JSON ({\"Header\": \"Value\"}) oder zeilenweise (Header: Value)",
                required=False,
            ),
            ToolParameter(
                name="content_type",
                type="string",
                description="Content-Type für Body (default: auto-detect, application/json für JSON)",
                required=False,
            ),
        ],
        handler=http_request,
    ))
    count += 1

    return count
