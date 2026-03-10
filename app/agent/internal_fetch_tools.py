"""
Agent-Tools zum Abrufen interner/Intranet-URLs.

Diese Tools erlauben dem Agent, interne HTTP-Endpunkte abzurufen,
z.B. interne APIs, Wikis, oder andere Intranet-Ressourcen.

Features:
- HTML-Parsing und Text-Extraktion
- Chunk-Verarbeitung für große Dokumente
- Section-Extraktion via CSS-Selektoren
- Suche auf geparstem Content

Sicherheit: URLs werden gegen konfigurierte base_urls validiert.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.core.http_client import get_internal_client
from app.utils.html_parser import (
    parse_html,
    chunk_content,
    extract_section,
    format_parsed_output,
    ParsedHTML,
    ContentChunk,
)

logger = logging.getLogger(__name__)


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
        # Shared Client nutzen wenn kein Proxy, sonst neuen Client erstellen
        if proxy_config:
            # Mit Proxy: neuer Client pro Request (Proxy-Einstellungen können variieren)
            async with httpx.AsyncClient(
                timeout=config.timeout_seconds,
                verify=config.verify_ssl,
                follow_redirects=True,
                **proxy_config,
            ) as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    content=body if body else None,
                )
        else:
            # Ohne Proxy: Shared Client für Connection-Pooling
            client = get_internal_client(config.timeout_seconds, config.verify_ssl)
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
        import json as json_module

        logger.debug("internal_fetch kwargs: %s", kwargs)
        url: str = kwargs.get("url", "").strip()
        headers_str: str = kwargs.get("headers", "")

        # Neue Parameter für HTML-Verarbeitung
        parse_html_param: bool = kwargs.get("parse_html", True)
        extract_mode: str = kwargs.get("extract_mode", "").strip() or \
            settings.internal_fetch.html_processing.default_extract_mode
        max_length: int = int(kwargs.get("max_length", 0)) or \
            settings.internal_fetch.html_processing.max_output_length
        chunk_index: int = int(kwargs.get("chunk_index", -1))

        if not url:
            return ToolResult(
                success=False,
                error="URL ist erforderlich. Beispiel: internal_fetch(url=\"https://example.com\")",
            )

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

        # Headers parsen (Format: "Header1: Value1\nHeader2: Value2" oder JSON)
        custom_headers = {}
        if headers_str:
            headers_str = headers_str.strip()
            if headers_str.startswith("{"):
                try:
                    custom_headers = json_module.loads(headers_str)
                except json_module.JSONDecodeError as e:
                    return ToolResult(success=False, error=f"Ungültiges Header-JSON: {e}")
            else:
                for line in headers_str.split("\n"):
                    line = line.strip()
                    if ":" in line:
                        key, val = line.split(":", 1)
                        custom_headers[key.strip()] = val.strip()

        # Request ausführen
        result = await _fetch_url(
            url=url,
            config=settings.internal_fetch,
            custom_headers=custom_headers if custom_headers else None,
        )

        if result["error"]:
            return ToolResult(success=False, error=result["error"])

        # Erfolgreiche Antwort formatieren
        output = f"=== Internal Fetch: {url} ===\n"
        output += f"Status: {result['status_code']}\n"
        output += f"Content-Type: {result['content_type']}\n"
        output += f"URL (nach Redirects): {result['url']}\n\n"

        content = result["content"]
        content_type = result.get("content_type", "")

        # JSON direkt ausgeben
        if isinstance(content, dict) or isinstance(content, list):
            import json
            output += "```json\n"
            output += json.dumps(content, ensure_ascii=False, indent=2)
            output += "\n```"
            return ToolResult(success=True, data=output)

        content_str = str(content)

        # HTML-Verarbeitung wenn aktiviert und Content ist HTML
        is_html = "text/html" in content_type or content_str.strip().startswith("<")
        html_cfg = settings.internal_fetch.html_processing

        if parse_html_param and html_cfg.enabled and is_html:
            try:
                # HTML parsen
                parsed = parse_html(
                    content_str,
                    extract_mode=extract_mode,
                    remove_navigation=html_cfg.remove_navigation,
                    remove_selectors=html_cfg.remove_selectors,
                    preserve_selectors=html_cfg.preserve_selectors if html_cfg.preserve_selectors else None,
                )

                # Bei großem Content: Chunking
                if parsed.char_count > html_cfg.chunk_size:
                    chunks = chunk_content(
                        parsed.text,
                        max_chunk_size=html_cfg.chunk_size,
                        overlap=html_cfg.chunk_overlap,
                        headings=parsed.headings,
                    )

                    total_chunks = len(chunks)

                    # Spezifischen Chunk ausgeben
                    if chunk_index >= 0:
                        if chunk_index >= total_chunks:
                            return ToolResult(
                                success=False,
                                error=f"Chunk {chunk_index} existiert nicht. Verfügbar: 0-{total_chunks-1}"
                            )
                        chunk = chunks[chunk_index]
                        output += f"[Chunk {chunk_index + 1}/{total_chunks}]\n"
                        if chunk.heading_context:
                            output += f"Kontext: {chunk.heading_context}\n"
                        output += f"\n{chunk.text}"
                    else:
                        # Alle Chunks mit Übersicht
                        formatted, remaining = format_parsed_output(
                            parsed,
                            max_length=max_length,
                            include_toc=(extract_mode != "text"),
                            include_links=(extract_mode in ("structured", "full")),
                        )
                        output += formatted

                        if remaining > 0:
                            output += f"\n\n[{total_chunks} Chunks verfügbar. "
                            output += f"Nutze chunk_index=0,1,... für weitere Inhalte]"
                else:
                    # Kleiner Content: Direkt ausgeben
                    formatted, _ = format_parsed_output(
                        parsed,
                        max_length=max_length,
                        include_toc=(extract_mode != "text"),
                        include_links=(extract_mode in ("structured", "full")),
                    )
                    output += formatted

                # Statistik
                output += f"\n\n[Parsed: {parsed.char_count:,} Zeichen, {parsed.word_count:,} Wörter"
                if parsed.headings:
                    output += f", {len(parsed.headings)} Überschriften"
                if parsed.links:
                    output += f", {len(parsed.links)} Links"
                output += "]"

            except Exception as e:
                logger.warning(f"HTML-Parsing fehlgeschlagen: {e}")
                # Fallback auf rohes HTML
                if len(content_str) > max_length:
                    output += content_str[:max_length]
                    output += f"\n\n... [+{len(content_str) - max_length} Zeichen abgeschnitten]"
                else:
                    output += content_str
        else:
            # Kein HTML oder Parsing deaktiviert
            if len(content_str) > max_length:
                output += content_str[:max_length]
                output += f"\n\n... [+{len(content_str) - max_length} Zeichen abgeschnitten]"
            else:
                output += content_str

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="internal_fetch",
        description=(
            "Ruft eine URL ab und gibt den Inhalt zurück. "
            "HTML wird automatisch geparst und in lesbaren Text umgewandelt. "
            "Für große Seiten wird Chunking verwendet - nutze chunk_index für weitere Inhalte. "
            "Unterstützt HTTP/HTTPS, HTML, JSON und Text. "
            "Ideal für: Interne APIs, Intranet-Seiten, Wikis, Dokumentationen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="url",
                type="string",
                description="Die abzurufende URL (HTTP oder HTTPS)",
                required=True,
            ),
            ToolParameter(
                name="headers",
                type="string",
                description=(
                    "Optionale HTTP-Header. "
                    "Format JSON: {\"Authorization\": \"Bearer xyz\"} "
                    "oder Zeilen: Authorization: Bearer xyz"
                ),
                required=False,
            ),
            ToolParameter(
                name="parse_html",
                type="boolean",
                description="HTML parsen und nur Text extrahieren (default: true)",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="extract_mode",
                type="string",
                description="Extraktionsmodus: text | structured (mit Headings/Links) | full (inkl. Tabellen)",
                required=False,
                enum=["text", "structured", "full"],
            ),
            ToolParameter(
                name="max_length",
                type="integer",
                description="Max. Zeichen im Output (default: 30000, 0 = unbegrenzt)",
                required=False,
            ),
            ToolParameter(
                name="chunk_index",
                type="integer",
                description="Welcher Chunk bei großen Seiten (-1 = erster mit Übersicht, 0,1,2... = spezifischer Chunk)",
                required=False,
                default=-1,
            ),
        ],
        handler=internal_fetch,
    ))
    count += 1

    # ── internal_search ────────────────────────────────────────────────────────

    async def internal_search(**kwargs: Any) -> ToolResult:
        """Ruft eine interne URL ab und durchsucht den Inhalt nach einem Pattern."""
        import json as json_module

        logger.debug("internal_search kwargs: %s", kwargs)
        url: str = kwargs.get("url", "").strip()
        pattern: str = kwargs.get("pattern", "").strip()
        context_lines: int = int(kwargs.get("context_lines", 3))
        headers_str: str = kwargs.get("headers", "")
        search_parsed: bool = kwargs.get("search_parsed", True)

        if not url:
            return ToolResult(
                success=False,
                error="URL ist erforderlich. Beispiel: internal_search(url=\"https://example.com\", pattern=\"suchtext\")",
            )

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

        # Headers parsen
        custom_headers = {}
        if headers_str:
            headers_str = headers_str.strip()
            if headers_str.startswith("{"):
                try:
                    custom_headers = json_module.loads(headers_str)
                except json_module.JSONDecodeError as e:
                    return ToolResult(success=False, error=f"Ungültiges Header-JSON: {e}")
            else:
                for line in headers_str.split("\n"):
                    line = line.strip()
                    if ":" in line:
                        key, val = line.split(":", 1)
                        custom_headers[key.strip()] = val.strip()

        # Request ausführen
        result = await _fetch_url(
            url=url,
            config=settings.internal_fetch,
            custom_headers=custom_headers if custom_headers else None,
        )

        if result["error"]:
            return ToolResult(success=False, error=result["error"])

        content = result["content"]
        content_type = result.get("content_type", "")

        if isinstance(content, dict) or isinstance(content, list):
            import json
            content = json.dumps(content, ensure_ascii=False, indent=2)
        else:
            content = str(content)

        # HTML parsen wenn aktiviert
        is_html = "text/html" in content_type or content.strip().startswith("<")
        html_cfg = settings.internal_fetch.html_processing

        if search_parsed and html_cfg.enabled and is_html:
            try:
                parsed = parse_html(
                    content,
                    extract_mode="text",
                    remove_navigation=html_cfg.remove_navigation,
                    remove_selectors=html_cfg.remove_selectors,
                )
                content = parsed.text
            except Exception as e:
                logger.debug(f"HTML-Parsing für Suche fehlgeschlagen: {e}")
                # Fallback auf rohen Content

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
        output += f"Status: {result['status_code']}\n"
        if is_html and search_parsed:
            output += "[Suche auf geparstem Text]\n"
        output += "\n"

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
            "HTML wird vor der Suche geparst (nur Text, keine Tags). "
            "Gibt passende Zeilen mit Kontext zurück. "
            "Ideal zum Suchen von Informationen in Webseiten und Dokumentationen."
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
            ToolParameter(
                name="search_parsed",
                type="boolean",
                description="Auf geparstem Text suchen statt rohem HTML (default: true)",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="headers",
                type="string",
                description=(
                    "Optionale HTTP-Header. "
                    "Format JSON: {\"Authorization\": \"Bearer xyz\"} "
                    "oder Zeilen: Authorization: Bearer xyz"
                ),
                required=False,
            ),
        ],
        handler=internal_search,
    ))
    count += 1

    # ── internal_fetch_section ─────────────────────────────────────────────────

    async def internal_fetch_section(**kwargs: Any) -> ToolResult:
        """Extrahiert einen bestimmten Abschnitt aus einer HTML-Seite."""
        import json as json_module

        logger.debug("internal_fetch_section kwargs: %s", kwargs)
        url: str = kwargs.get("url", "").strip()
        selector: str = kwargs.get("selector", "").strip()
        include_children: bool = kwargs.get("include_children", True)
        headers_str: str = kwargs.get("headers", "")

        if not url:
            return ToolResult(
                success=False,
                error="URL ist erforderlich.",
            )

        if not selector:
            return ToolResult(
                success=False,
                error="selector ist erforderlich. Beispiel: '#main-content', 'h2', '.article'",
            )

        if not settings.internal_fetch.enabled:
            return ToolResult(
                success=False,
                error="Internal Fetch ist deaktiviert.",
            )

        # URL validieren
        is_valid, error = _validate_url(url, settings.internal_fetch.base_urls)
        if not is_valid:
            return ToolResult(success=False, error=error)

        # Headers parsen
        custom_headers = {}
        if headers_str:
            headers_str = headers_str.strip()
            if headers_str.startswith("{"):
                try:
                    custom_headers = json_module.loads(headers_str)
                except json_module.JSONDecodeError as e:
                    return ToolResult(success=False, error=f"Ungültiges Header-JSON: {e}")
            else:
                for line in headers_str.split("\n"):
                    line = line.strip()
                    if ":" in line:
                        key, val = line.split(":", 1)
                        custom_headers[key.strip()] = val.strip()

        # Request ausführen
        result = await _fetch_url(
            url=url,
            config=settings.internal_fetch,
            custom_headers=custom_headers if custom_headers else None,
        )

        if result["error"]:
            return ToolResult(success=False, error=result["error"])

        content = result["content"]
        if not isinstance(content, str):
            return ToolResult(
                success=False,
                error="Content ist kein HTML. Nutze internal_fetch für JSON/Text.",
            )

        # Section extrahieren
        try:
            section = extract_section(content, selector, include_children)
        except Exception as e:
            return ToolResult(success=False, error=f"Section-Extraktion fehlgeschlagen: {e}")

        if section is None:
            return ToolResult(
                success=False,
                error=f"Abschnitt '{selector}' nicht gefunden. "
                      f"Versuche einen anderen Selektor (CSS, ID oder Heading-Text).",
            )

        # Output formatieren
        output = f"=== Section: {selector} ===\n"
        output += f"URL: {url}\n\n"

        formatted, remaining = format_parsed_output(
            section,
            max_length=settings.internal_fetch.html_processing.max_output_length,
            include_toc=True,
            include_links=True,
        )
        output += formatted

        output += f"\n\n[Section: {section.char_count:,} Zeichen, {section.word_count:,} Wörter]"

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="internal_fetch_section",
        description=(
            "Extrahiert einen bestimmten Abschnitt aus einer HTML-Seite. "
            "Kann nach CSS-Selektor (#id, .class), Heading-Text oder Element-ID suchen. "
            "Bei Headings werden alle Inhalte bis zum nächsten gleichen/höheren Heading extrahiert. "
            "Ideal für: Spezifische Dokumentations-Abschnitte, Artikel-Inhalte."
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
                name="selector",
                type="string",
                description=(
                    "CSS-Selektor, Element-ID oder Heading-Text. "
                    "Beispiele: '#main-content', '.article', 'h2', 'Installation'"
                ),
                required=True,
            ),
            ToolParameter(
                name="include_children",
                type="boolean",
                description="Bei Headings: Alle Unterabschnitte einschließen (default: true)",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="headers",
                type="string",
                description="Optionale HTTP-Header (JSON oder Zeilen-Format)",
                required=False,
            ),
        ],
        handler=internal_fetch_section,
    ))
    count += 1

    # ── http_request (curl-Ersatz) ────────────────────────────────────────────

    async def http_request(**kwargs: Any) -> ToolResult:
        """Führt einen HTTP-Request aus (curl-Ersatz)."""
        import json as json_module

        # Debug: Log welche Parameter übergeben wurden
        logger.debug("http_request kwargs: %s", kwargs)

        url: str = kwargs.get("url", "").strip()
        method: str = kwargs.get("method", "GET").upper().strip()
        body: str = kwargs.get("body", "")
        headers_str: str = kwargs.get("headers", "")
        content_type_param: str = kwargs.get("content_type", "")

        # URL ist Pflichtparameter
        if not url:
            return ToolResult(
                success=False,
                error=(
                    "URL ist erforderlich. Bitte gib die URL an: "
                    "http_request(url=\"https://example.com\", method=\"GET\")"
                ),
            )

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
            "Führt HTTP-Requests aus (vollwertiger curl-Ersatz). "
            "Unterstützt ALLE HTTP-Methoden: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS. "
            "WICHTIG: Custom HTTP-Headers werden vollständig unterstützt! "
            "Verwende den 'headers' Parameter für Authorization, API-Keys, Accept, "
            "X-Custom-Header oder beliebige andere HTTP-Header. "
            "Beispiel: headers='{\"Authorization\": \"Bearer token123\", \"X-API-Key\": \"abc\"}' "
            "Ideal für: REST-APIs, authentifizierte Endpoints, Webhooks, Service-Tests."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="url",
                type="string",
                description="Ziel-URL (HTTP oder HTTPS)",
                required=True,
            ),
            ToolParameter(
                name="method",
                type="string",
                description="HTTP-Methode: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS (default: GET)",
                required=False,
                default="GET",
                enum=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            ),
            ToolParameter(
                name="body",
                type="string",
                description="Request-Body für POST/PUT/PATCH. JSON-Objekte werden automatisch erkannt.",
                required=False,
            ),
            ToolParameter(
                name="headers",
                type="string",
                description=(
                    "Benutzerdefinierte HTTP-Header. "
                    "Format 1 (JSON): {\"Authorization\": \"Bearer xyz\", \"X-API-Key\": \"abc123\"} "
                    "Format 2 (Zeilen): Authorization: Bearer xyz\\nX-API-Key: abc123 "
                    "Beliebige Header erlaubt: Authorization, Accept, X-Custom-*, Cookie, etc."
                ),
                required=False,
            ),
            ToolParameter(
                name="content_type",
                type="string",
                description="Content-Type Header für den Body (default: auto-detect basierend auf Body-Format)",
                required=False,
            ),
        ],
        handler=http_request,
    ))
    count += 1

    return count
