"""
Agent-Tools für SOAP und REST API Calls.

Bietet:
- wsdl_info: WSDL analysieren und Operationen auflisten
- soap_request: SOAP-Calls mit automatischer Envelope-Generierung
- rest_api: Verbesserter REST-Client mit Path-Variablen

Diese Tools erweitern den generischen http_request mit spezialisierten
Funktionen für SOAP-Webservices und REST-APIs.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.utils.soap_utils import (
    WSDLParser,
    SOAPEnvelopeBuilder,
    SOAPResponseParser,
    get_cached_wsdl,
    format_wsdl_info,
    format_soap_response,
    ZEEP_AVAILABLE,
    LXML_AVAILABLE,
)

logger = logging.getLogger(__name__)


def register_api_tools(registry: ToolRegistry) -> int:
    """
    Registriert die SOAP und REST API Tools.

    Returns:
        Anzahl der registrierten Tools
    """
    from app.core.config import settings

    count = 0

    # ── wsdl_info ───────────────────────────────────────────────────────────────

    async def wsdl_info(**kwargs: Any) -> ToolResult:
        """Analysiert eine WSDL und gibt Informationen über verfügbare Operationen zurück."""
        wsdl_url: str = kwargs.get("wsdl_url", "").strip()
        operation: str = kwargs.get("operation", "").strip()
        show_types: bool = kwargs.get("show_types", False)

        if not wsdl_url:
            return ToolResult(
                success=False,
                error="wsdl_url ist erforderlich. Beispiel: wsdl_info(wsdl_url=\"https://api.example.com/service?wsdl\")"
            )

        # Config prüfen
        if not settings.api_tools.enabled:
            return ToolResult(
                success=False,
                error="API Tools sind deaktiviert. Aktiviere sie in Settings → API Tools."
            )

        try:
            # WSDL parsen (mit Caching)
            timeout = settings.api_tools.soap.default_timeout
            verify_ssl = settings.api_tools.soap.verify_ssl

            service = get_cached_wsdl(wsdl_url, timeout, verify_ssl)

            # Formatierte Ausgabe
            output = format_wsdl_info(service, operation, show_types)

            return ToolResult(success=True, data=output)

        except ValueError as e:
            return ToolResult(success=False, error=f"WSDL-Fehler: {e}")
        except Exception as e:
            logger.exception(f"Fehler beim Parsen der WSDL: {e}")
            return ToolResult(success=False, error=f"Fehler beim Parsen der WSDL: {e}")

    registry.register(Tool(
        name="wsdl_info",
        description=(
            "Analysiert eine WSDL-Datei und zeigt verfügbare SOAP-Operationen. "
            "Gibt Informationen über Operationsnamen, Input-Parameter, Output-Typen "
            "und SOAPAction-Header zurück. "
            "Nutze dies VOR soap_request um die korrekte Operation und Parameter zu ermitteln."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="wsdl_url",
                type="string",
                description="URL zur WSDL-Datei (z.B. https://api.example.com/service?wsdl)",
                required=True,
            ),
            ToolParameter(
                name="operation",
                type="string",
                description="Optionaler Operationsname für detaillierte Informationen",
                required=False,
            ),
            ToolParameter(
                name="show_types",
                type="boolean",
                description="Komplexe Typen mit Feldern anzeigen (default: false)",
                required=False,
                default=False,
            ),
        ],
        handler=wsdl_info,
    ))
    count += 1

    # ── soap_request ────────────────────────────────────────────────────────────

    async def soap_request(**kwargs: Any) -> ToolResult:
        """Führt einen SOAP-Request mit automatischer Envelope-Generierung aus."""
        wsdl_url: str = kwargs.get("wsdl_url", "").strip()
        operation_name: str = kwargs.get("operation", "").strip()
        params_str: str = kwargs.get("params", "")
        endpoint_override: str = kwargs.get("endpoint", "").strip()
        raw_body: str = kwargs.get("raw_body", "").strip()
        timeout: int = int(kwargs.get("timeout", 0))
        include_raw: bool = kwargs.get("include_raw", False)

        if not wsdl_url:
            return ToolResult(
                success=False,
                error="wsdl_url ist erforderlich."
            )

        if not operation_name:
            return ToolResult(
                success=False,
                error="operation ist erforderlich. Nutze wsdl_info() um verfügbare Operationen zu sehen."
            )

        # Config prüfen
        if not settings.api_tools.enabled:
            return ToolResult(
                success=False,
                error="API Tools sind deaktiviert."
            )

        soap_cfg = settings.api_tools.soap
        if not timeout:
            timeout = soap_cfg.default_timeout

        try:
            # WSDL parsen
            service = get_cached_wsdl(wsdl_url, timeout, soap_cfg.verify_ssl)

            # Operation finden
            operation = None
            for op in service.operations:
                if op.name.lower() == operation_name.lower():
                    operation = op
                    break

            if not operation:
                available = ", ".join(o.name for o in service.operations)
                return ToolResult(
                    success=False,
                    error=f"Operation '{operation_name}' nicht gefunden. Verfügbar: {available}"
                )

            # Parameter parsen
            params: Dict[str, Any] = {}
            if params_str:
                params_str = params_str.strip()
                if params_str.startswith("{"):
                    try:
                        params = json.loads(params_str)
                    except json.JSONDecodeError as e:
                        return ToolResult(success=False, error=f"Ungültiges Parameter-JSON: {e}")
                else:
                    # Key=Value Format
                    for part in params_str.split(","):
                        if "=" in part:
                            key, val = part.split("=", 1)
                            params[key.strip()] = val.strip()

            # Envelope bauen oder raw_body verwenden
            if raw_body:
                # Manueller Body - in Envelope wrappen
                soap_ns = "http://www.w3.org/2003/05/soap-envelope" if service.soap_version == "1.2" \
                         else "http://schemas.xmlsoap.org/soap/envelope/"
                envelope = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="{soap_ns}">
  <soap:Body>
    {raw_body}
  </soap:Body>
</soap:Envelope>'''
            else:
                # Automatische Envelope-Generierung
                builder = SOAPEnvelopeBuilder(service)
                envelope = builder.build_envelope(operation, params)

            # Endpoint
            endpoint = endpoint_override or service.endpoint
            if not endpoint:
                return ToolResult(
                    success=False,
                    error="Kein Endpoint in WSDL gefunden. Bitte endpoint-Parameter angeben."
                )

            # HTTP-Header
            builder = SOAPEnvelopeBuilder(service)
            http_headers = builder.get_soap_headers(operation)

            # Request ausführen (defensive type coercion)
            verify_val = bool(soap_cfg.verify_ssl) if soap_cfg.verify_ssl is not None else True
            timeout_val = int(timeout) if timeout else 30

            async with httpx.AsyncClient(
                timeout=timeout_val,
                verify=verify_val,
                follow_redirects=True,
            ) as client:
                response = await client.post(
                    endpoint,
                    content=envelope.encode("utf-8"),
                    headers=http_headers,
                )

            # Response parsen
            parser = SOAPResponseParser()
            soap_response = parser.parse(response.text, response.status_code)

            # Output formatieren
            output = f"=== SOAP Request: {operation.name} ===\n"
            output += f"Endpoint: {endpoint}\n"
            output += f"Status: {response.status_code}\n\n"

            output += format_soap_response(soap_response, include_raw)

            # Bei Fehler: Request-Body für Debugging anzeigen
            if not soap_response.success:
                output += f"\n\n--- Request Body (für Debugging) ---\n{envelope}"

            return ToolResult(success=soap_response.success, data=output)

        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error=f"Timeout nach {timeout} Sekunden"
            )
        except httpx.ConnectError as e:
            error_str = str(e).lower()
            if "ssl" in error_str or "certificate" in error_str:
                return ToolResult(
                    success=False,
                    error="SSL-Fehler. Falls selbstsigniertes Zertifikat: verify_ssl in Config auf false setzen."
                )
            return ToolResult(success=False, error=f"Verbindungsfehler: {e}")
        except Exception as e:
            logger.exception(f"SOAP-Request Fehler: {e}")
            return ToolResult(success=False, error=f"Fehler: {e}")

    registry.register(Tool(
        name="soap_request",
        description=(
            "Führt SOAP-Webservice-Calls aus mit automatischer Envelope-Generierung. "
            "Die WSDL wird geparst um die korrekte SOAP-Struktur zu generieren. "
            "Nutze wsdl_info() zuerst um verfügbare Operationen und Parameter zu sehen. "
            "Parameter können als JSON oder Key=Value übergeben werden. "
            "Für komplexe Fälle kann raw_body für manuellen XML-Body genutzt werden."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="wsdl_url",
                type="string",
                description="URL zur WSDL-Datei",
                required=True,
            ),
            ToolParameter(
                name="operation",
                type="string",
                description="Name der aufzurufenden SOAP-Operation",
                required=True,
            ),
            ToolParameter(
                name="params",
                type="string",
                description=(
                    "Parameter als JSON oder Key=Value. "
                    "Beispiel JSON: {\"userId\": 123, \"includeDetails\": true} "
                    "Beispiel Key=Value: userId=123, includeDetails=true"
                ),
                required=False,
            ),
            ToolParameter(
                name="endpoint",
                type="string",
                description="Überschreibt den WSDL-Endpoint (optional)",
                required=False,
            ),
            ToolParameter(
                name="raw_body",
                type="string",
                description=(
                    "Manueller SOAP-Body XML (überschreibt params). "
                    "Nutze dies für komplexe Strukturen die nicht automatisch generiert werden können."
                ),
                required=False,
            ),
            ToolParameter(
                name="timeout",
                type="integer",
                description="Timeout in Sekunden (default: 30)",
                required=False,
                default=30,
            ),
            ToolParameter(
                name="include_raw",
                type="boolean",
                description="Raw XML Response einschließen (default: false)",
                required=False,
                default=False,
            ),
        ],
        handler=soap_request,
    ))
    count += 1

    # ── rest_api ────────────────────────────────────────────────────────────────

    async def rest_api(**kwargs: Any) -> ToolResult:
        """Führt REST-API Calls mit verbessertem Parameter-Handling aus."""
        url: str = kwargs.get("url", "").strip()
        method: str = kwargs.get("method", "GET").upper().strip()
        path_params_str: str = kwargs.get("path_params", "")
        query_params_str: str = kwargs.get("query_params", "")
        body_str: str = kwargs.get("body", "")
        headers_str: str = kwargs.get("headers", "")
        auth: str = kwargs.get("auth", "").strip()
        format_response: bool = kwargs.get("format_response", True)
        timeout: int = int(kwargs.get("timeout", 0))

        if not url:
            return ToolResult(
                success=False,
                error="url ist erforderlich. Beispiel: rest_api(url=\"https://api.example.com/users/{id}\", path_params='{\"id\": 123}')"
            )

        # Config prüfen
        if not settings.api_tools.enabled:
            return ToolResult(
                success=False,
                error="API Tools sind deaktiviert."
            )

        rest_cfg = settings.api_tools.rest
        if not timeout:
            timeout = rest_cfg.default_timeout

        # Methode validieren
        valid_methods = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
        if method not in valid_methods:
            return ToolResult(
                success=False,
                error=f"Ungültige Methode: {method}. Erlaubt: {', '.join(valid_methods)}"
            )

        try:
            # Path-Parameter ersetzen
            final_url = url
            if path_params_str:
                path_params = _parse_params(path_params_str)
                if isinstance(path_params, str):
                    return ToolResult(success=False, error=path_params)

                for key, value in path_params.items():
                    placeholder = "{" + key + "}"
                    if placeholder in final_url:
                        final_url = final_url.replace(placeholder, str(value))

                # Prüfen ob alle Platzhalter ersetzt wurden
                remaining = re.findall(r"\{(\w+)\}", final_url)
                if remaining:
                    return ToolResult(
                        success=False,
                        error=f"Fehlende path_params: {', '.join(remaining)}"
                    )

            # Query-Parameter hinzufügen
            if query_params_str:
                query_params = _parse_params(query_params_str)
                if isinstance(query_params, str):
                    return ToolResult(success=False, error=query_params)

                query_string = urlencode(query_params)
                separator = "&" if "?" in final_url else "?"
                final_url = f"{final_url}{separator}{query_string}"

            # Headers parsen
            headers: Dict[str, str] = {}
            if headers_str:
                parsed_headers = _parse_params(headers_str)
                if isinstance(parsed_headers, str):
                    return ToolResult(success=False, error=parsed_headers)
                headers = {str(k): str(v) for k, v in parsed_headers.items()}

            # Auth hinzufügen
            if auth:
                if auth.startswith("bearer:"):
                    token = auth[7:]
                    headers["Authorization"] = f"Bearer {token}"
                elif auth.startswith("basic:"):
                    import base64
                    credentials = auth[6:]
                    encoded = base64.b64encode(credentials.encode()).decode()
                    headers["Authorization"] = f"Basic {encoded}"
                else:
                    # Annahme: Bearer Token ohne Prefix
                    headers["Authorization"] = f"Bearer {auth}"

            # Body parsen
            body: Optional[Any] = None
            if body_str:
                body_str = body_str.strip()
                if body_str.startswith("{") or body_str.startswith("["):
                    try:
                        body = json.loads(body_str)
                        if "Content-Type" not in headers:
                            headers["Content-Type"] = "application/json"
                    except json.JSONDecodeError:
                        body = body_str
                else:
                    body = body_str

            # Request ausführen (defensive type coercion)
            verify_val = bool(rest_cfg.verify_ssl) if hasattr(rest_cfg, 'verify_ssl') and rest_cfg.verify_ssl is not None else True
            timeout_val = int(timeout) if timeout else 30

            async with httpx.AsyncClient(
                timeout=timeout_val,
                verify=verify_val,
                follow_redirects=True,
            ) as client:
                if isinstance(body, (dict, list)):
                    response = await client.request(
                        method=method,
                        url=final_url,
                        headers=headers,
                        json=body,
                    )
                else:
                    response = await client.request(
                        method=method,
                        url=final_url,
                        headers=headers,
                        content=body.encode() if body else None,
                    )

            # Response formatieren
            elapsed_ms = int(response.elapsed.total_seconds() * 1000)
            parsed_url = urlparse(final_url)
            path_display = parsed_url.path + (f"?{parsed_url.query}" if parsed_url.query else "")

            output = f"=== REST API: {method} {path_display} ===\n"
            output += f"Status: {response.status_code} {response.reason_phrase}\n"
            output += f"Time: {elapsed_ms}ms\n\n"

            # Response Content
            content_type = response.headers.get("content-type", "")
            response_text = response.text

            if format_response and "application/json" in content_type:
                try:
                    response_json = response.json()
                    output += "Response:\n"
                    output += json.dumps(response_json, ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    output += f"Response:\n{response_text}"
            elif format_response and ("text/xml" in content_type or "application/xml" in content_type):
                # XML formatieren wenn lxml verfügbar
                if LXML_AVAILABLE:
                    try:
                        from lxml import etree
                        root = etree.fromstring(response_text.encode())
                        output += "Response:\n"
                        output += etree.tostring(root, encoding="unicode", pretty_print=True)
                    except Exception:
                        output += f"Response:\n{response_text}"
                else:
                    output += f"Response:\n{response_text}"
            else:
                # Plain text
                max_len = rest_cfg.max_response_size_kb * 1024 if hasattr(rest_cfg, 'max_response_size_kb') else 500 * 1024
                if len(response_text) > max_len:
                    output += f"Response (truncated):\n{response_text[:max_len]}"
                    output += f"\n\n... [+{len(response_text) - max_len} bytes truncated]"
                else:
                    output += f"Response:\n{response_text}"

            # Wichtige Response-Headers anzeigen
            important_headers = ["content-type", "x-ratelimit-remaining", "x-request-id", "location"]
            resp_headers = {
                k: v for k, v in response.headers.items()
                if k.lower() in important_headers
            }
            if resp_headers:
                output += f"\n\nHeaders:\n"
                for k, v in resp_headers.items():
                    output += f"  {k}: {v}\n"

            success = 200 <= response.status_code < 400
            return ToolResult(success=success, data=output)

        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error=f"Timeout nach {timeout} Sekunden"
            )
        except httpx.ConnectError as e:
            return ToolResult(success=False, error=f"Verbindungsfehler: {e}")
        except Exception as e:
            logger.exception(f"REST-API Fehler: {e}")
            return ToolResult(success=False, error=f"Fehler: {e}")

    registry.register(Tool(
        name="rest_api",
        description=(
            "Verbesserter REST-API Client mit Path-Variablen, Query-Parametern und Auth-Shortcuts. "
            "URLs können Platzhalter enthalten: /users/{userId}/orders/{orderId}. "
            "Auth kann als 'bearer:TOKEN' oder 'basic:user:pass' übergeben werden. "
            "JSON-Bodies werden automatisch erkannt und Content-Type gesetzt. "
            "Ideal für: REST-APIs, authentifizierte Endpoints, API-Tests."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="url",
                type="string",
                description="URL mit optionalen Platzhaltern wie {id}. Beispiel: https://api.example.com/users/{userId}",
                required=True,
            ),
            ToolParameter(
                name="method",
                type="string",
                description="HTTP-Methode (default: GET)",
                required=False,
                default="GET",
                enum=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            ),
            ToolParameter(
                name="path_params",
                type="string",
                description="Path-Variablen als JSON zum Ersetzen von {placeholders}. Beispiel: {\"userId\": 123}",
                required=False,
            ),
            ToolParameter(
                name="query_params",
                type="string",
                description="Query-Parameter als JSON. Beispiel: {\"limit\": 10, \"offset\": 0}",
                required=False,
            ),
            ToolParameter(
                name="body",
                type="string",
                description="Request-Body. JSON wird automatisch erkannt.",
                required=False,
            ),
            ToolParameter(
                name="headers",
                type="string",
                description="Custom Headers als JSON. Beispiel: {\"X-API-Key\": \"abc123\"}",
                required=False,
            ),
            ToolParameter(
                name="auth",
                type="string",
                description="Auth-Shortcut: 'bearer:TOKEN' oder 'basic:user:pass'",
                required=False,
            ),
            ToolParameter(
                name="format_response",
                type="boolean",
                description="JSON/XML Response formatieren (default: true)",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="timeout",
                type="integer",
                description="Timeout in Sekunden (default: 30)",
                required=False,
                default=30,
            ),
        ],
        handler=rest_api,
    ))
    count += 1

    return count


def _parse_params(params_str: str) -> Dict[str, Any] | str:
    """
    Parst Parameter-String zu Dictionary.

    Unterstützt:
    - JSON: {"key": "value"}
    - Key=Value: key1=value1, key2=value2

    Returns:
        Dictionary oder Error-String
    """
    if not params_str:
        return {}

    params_str = params_str.strip()

    # JSON Format
    if params_str.startswith("{"):
        try:
            return json.loads(params_str)
        except json.JSONDecodeError as e:
            return f"Ungültiges JSON: {e}"

    # Key=Value Format
    result = {}
    for part in params_str.split(","):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.strip()
            val = val.strip()

            # Versuche Wert zu konvertieren
            if val.lower() == "true":
                result[key] = True
            elif val.lower() == "false":
                result[key] = False
            elif val.isdigit():
                result[key] = int(val)
            else:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val

    return result
