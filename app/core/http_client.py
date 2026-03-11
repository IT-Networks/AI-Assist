"""
Shared HTTP Client Pool - Zentrale Verwaltung von HTTP-Clients.

Eliminiert Duplikation und ermöglicht effizientes Connection-Pooling
über alle Tool-Module hinweg (GitHub, Jenkins, MQ, TestTool, etc.).

Performance-Vorteile:
- Vermeidet TCP/TLS-Handshake bei jedem Request (~200ms Ersparnis)
- Ermöglicht Keep-Alive-Verbindungen
- Zentrale Konfiguration von Timeouts und Limits

Features:
- Automatisches Access-Logging für Audit und Debugging
- Privacy-bewusst: Passwörter und Tokens werden geschwärzt
"""

import logging
import re
import time
from typing import Any, Dict, Optional, TYPE_CHECKING
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import httpx

if TYPE_CHECKING:
    from app.services.external_access_logger import ExternalAccessLogger

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# URL Sanitization (Privacy)
# ══════════════════════════════════════════════════════════════════════════════

# Patterns für sensible Daten in URLs
SENSITIVE_PATTERNS = [
    r'password[=:][^&\s]*',
    r'passwd[=:][^&\s]*',
    r'pwd[=:][^&\s]*',
    r'token[=:][^&\s]*',
    r'api[_-]?key[=:][^&\s]*',
    r'apikey[=:][^&\s]*',
    r'secret[=:][^&\s]*',
    r'auth[=:][^&\s]*',
    r'bearer[=:][^&\s]*',
    r'credential[s]?[=:][^&\s]*',
    r'access[_-]?token[=:][^&\s]*',
]

SENSITIVE_REGEX = re.compile(
    '|'.join(SENSITIVE_PATTERNS),
    re.IGNORECASE
)


def sanitize_url(url: str) -> str:
    """
    Entfernt sensible Daten aus URL für Logging.

    - Entfernt Query-Parameter komplett (können API-Keys enthalten)
    - Maskiert Passwörter in Basic-Auth URLs (user:pass@host)
    """
    try:
        parsed = urlparse(url)

        # Maskiere Passwort in Basic-Auth URL (user:pass@host)
        netloc = parsed.netloc
        if '@' in netloc and ':' in netloc.split('@')[0]:
            # Format: user:password@host
            auth_part, host_part = netloc.rsplit('@', 1)
            if ':' in auth_part:
                user, _ = auth_part.split(':', 1)
                netloc = f"{user}:****@{host_part}"

        # Nur Schema, Host und Pfad behalten (keine Query-Parameter)
        return f"{parsed.scheme}://{netloc}{parsed.path}"

    except Exception:
        # Fallback: Sensitive Patterns maskieren
        return SENSITIVE_REGEX.sub('****', url)


def extract_host(url: str) -> str:
    """Extrahiert Hostname aus URL."""
    try:
        parsed = urlparse(url)
        # Entferne Port und Auth
        host = parsed.netloc
        if '@' in host:
            host = host.split('@')[-1]
        if ':' in host:
            host = host.split(':')[0]
        return host
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Logging HTTP Client Wrapper
# ══════════════════════════════════════════════════════════════════════════════

class LoggingAsyncClient:
    """
    Wrapper um httpx.AsyncClient mit automatischem Access-Logging.

    Alle HTTP-Requests werden transparent geloggt mit:
    - Timestamp, URL (sanitized), Method
    - Status Code, Response Size, Duration
    - Fehler-Messages bei Problemen

    Privacy:
    - Keine Auth-Header geloggt
    - Keine Request/Response-Bodies
    - Passwörter und Tokens in URLs maskiert
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        client_type: str,
        access_logger: "ExternalAccessLogger",
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ):
        self._client = client
        self._client_type = client_type
        self._access_logger = access_logger
        self._session_id = session_id or "unknown"
        self._tool_name = tool_name or client_type

    def set_session_id(self, session_id: str) -> None:
        """Setzt die aktuelle Session-ID."""
        self._session_id = session_id

    def set_tool_name(self, tool_name: str) -> None:
        """Setzt den aktuellen Tool-Namen für präziseres Logging."""
        self._tool_name = tool_name

    async def request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """Führt Request aus und loggt Zugriff."""
        start = time.monotonic()

        try:
            response = await self._client.request(method, url, **kwargs)
            duration = int((time.monotonic() - start) * 1000)

            # Async logging
            entry = self._access_logger.create_entry(
                session_id=self._session_id,
                tool_name=self._tool_name,
                client_type=self._client_type,
                method=method,
                url=url,
                status_code=response.status_code,
                success=response.is_success,
                response_size=len(response.content) if response.content else 0,
                duration_ms=duration,
                content_type=response.headers.get("content-type"),
                error_message=None,
            )
            await self._access_logger.log_access(entry)

            return response

        except httpx.RequestError as e:
            duration = int((time.monotonic() - start) * 1000)

            # Log auch fehlgeschlagene Requests
            entry = self._access_logger.create_entry(
                session_id=self._session_id,
                tool_name=self._tool_name,
                client_type=self._client_type,
                method=method,
                url=url,
                status_code=0,
                success=False,
                response_size=0,
                duration_ms=duration,
                content_type=None,
                error_message=str(e)[:200],  # Truncate für Logging
            )
            await self._access_logger.log_access(entry)

            raise

    # Delegate alle anderen Methoden an den wrapped Client
    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    async def head(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("OPTIONS", url, **kwargs)

    # Properties für Kompatibilität
    @property
    def headers(self):
        return self._client.headers

    @property
    def cookies(self):
        return self._client.cookies

    @property
    def timeout(self):
        return self._client.timeout

    async def aclose(self):
        await self._client.aclose()


class HttpClientPool:
    """
    Zentrale Verwaltung von HTTP-Clients mit Lazy-Initialization.

    Verwendung:
        client = HttpClientPool.get("github", verify_ssl=False, timeout=30)
        response = await client.get(url)

    Mit Access-Logging:
        HttpClientPool.enable_logging(access_logger, session_id)
        client = HttpClientPool.get("github")  # Automatisch geloggt

    Bei Application-Shutdown:
        await HttpClientPool.close_all()
    """

    _clients: Dict[str, httpx.AsyncClient] = {}
    _logging_clients: Dict[str, LoggingAsyncClient] = {}

    # Logging-Konfiguration
    _access_logger: Optional["ExternalAccessLogger"] = None
    _current_session_id: Optional[str] = None
    _logging_enabled: bool = False

    # Standard-Limits für alle Clients
    DEFAULT_MAX_CONNECTIONS = 10
    DEFAULT_KEEPALIVE_CONNECTIONS = 5
    DEFAULT_KEEPALIVE_EXPIRY = 30.0
    DEFAULT_TIMEOUT = 30

    @classmethod
    def enable_logging(
        cls,
        access_logger: "ExternalAccessLogger",
        session_id: Optional[str] = None
    ) -> None:
        """
        Aktiviert Access-Logging für alle HTTP-Clients.

        Args:
            access_logger: ExternalAccessLogger Instanz
            session_id: Aktuelle Session-ID
        """
        cls._access_logger = access_logger
        cls._current_session_id = session_id
        cls._logging_enabled = True
        logger.info("HTTP Access-Logging aktiviert")

    @classmethod
    def disable_logging(cls) -> None:
        """Deaktiviert Access-Logging."""
        cls._logging_enabled = False
        cls._logging_clients.clear()
        logger.info("HTTP Access-Logging deaktiviert")

    @classmethod
    def set_session_id(cls, session_id: str) -> None:
        """Aktualisiert die Session-ID für Logging."""
        cls._current_session_id = session_id
        # Update alle bestehenden Logging-Clients
        for client in cls._logging_clients.values():
            client.set_session_id(session_id)

    @classmethod
    def get(
        cls,
        name: str,
        verify_ssl: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_keepalive: int = DEFAULT_KEEPALIVE_CONNECTIONS,
        keepalive_expiry: float = DEFAULT_KEEPALIVE_EXPIRY,
    ) -> httpx.AsyncClient:
        """
        Gibt einen benannten HTTP-Client zurück (Lazy Init).

        Args:
            name: Eindeutiger Name des Clients (z.B. "github", "jenkins")
            verify_ssl: SSL-Zertifikate prüfen (False für Self-Signed)
            timeout: Request-Timeout in Sekunden
            max_connections: Max. gleichzeitige Verbindungen
            max_keepalive: Max. Keep-Alive-Verbindungen im Pool
            keepalive_expiry: Keep-Alive-Timeout in Sekunden

        Returns:
            Konfigurierter httpx.AsyncClient
        """
        if name not in cls._clients:
            # Defensive type coercion
            verify_val = bool(verify_ssl) if verify_ssl is not None else True
            timeout_val = int(timeout) if timeout else cls.DEFAULT_TIMEOUT

            logger.debug(f"HTTP-Client erstellt: {name} (ssl={verify_val}, timeout={timeout_val}s)")
            cls._clients[name] = httpx.AsyncClient(
                verify=verify_val,
                timeout=timeout_val,
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                    keepalive_expiry=keepalive_expiry,
                ),
            )

        # Wenn Logging aktiviert, Wrapper zurückgeben
        if cls._logging_enabled and cls._access_logger:
            if name not in cls._logging_clients:
                cls._logging_clients[name] = LoggingAsyncClient(
                    client=cls._clients[name],
                    client_type=name,
                    access_logger=cls._access_logger,
                    session_id=cls._current_session_id,
                )
            return cls._logging_clients[name]

        return cls._clients[name]

    @classmethod
    async def close(cls, name: str) -> bool:
        """
        Schließt einen spezifischen HTTP-Client.

        Args:
            name: Name des Clients

        Returns:
            True wenn Client geschlossen wurde, False wenn nicht vorhanden
        """
        if name in cls._clients:
            await cls._clients[name].aclose()
            del cls._clients[name]
            logger.debug(f"HTTP-Client geschlossen: {name}")
            return True
        return False

    @classmethod
    async def close_all(cls) -> int:
        """
        Schließt alle HTTP-Clients (für Application Shutdown).

        Returns:
            Anzahl geschlossener Clients
        """
        count = len(cls._clients)
        for name in list(cls._clients.keys()):
            await cls._clients[name].aclose()
            del cls._clients[name]

        # Logging-Clients auch leeren
        cls._logging_clients.clear()

        if count > 0:
            logger.info(f"{count} HTTP-Client(s) geschlossen")
        return count

    @classmethod
    def get_active_clients(cls) -> list:
        """Gibt Liste der aktiven Client-Namen zurück."""
        return list(cls._clients.keys())


# Convenience-Funktionen für häufige Client-Typen

def get_github_client(verify_ssl: bool = False, timeout: int = 30) -> httpx.AsyncClient:
    """GitHub Enterprise HTTP-Client."""
    return HttpClientPool.get("github", verify_ssl=verify_ssl, timeout=timeout)


def get_jenkins_client(verify_ssl: bool = False, timeout: int = 30) -> httpx.AsyncClient:
    """Jenkins CI/CD HTTP-Client."""
    return HttpClientPool.get("jenkins", verify_ssl=verify_ssl, timeout=timeout)


def get_mq_client(verify_ssl: bool = False, timeout: int = 30) -> httpx.AsyncClient:
    """MQ-Series HTTP-Client."""
    return HttpClientPool.get("mq", verify_ssl=verify_ssl, timeout=timeout)


def get_testtool_client(verify_ssl: bool = True, timeout: int = 30) -> httpx.AsyncClient:
    """TestTool HTTP-Client."""
    return HttpClientPool.get("testtool", verify_ssl=verify_ssl, timeout=timeout)


def get_internal_client(verify_ssl: bool = True, timeout: int = 30) -> httpx.AsyncClient:
    """Internal Fetch HTTP-Client."""
    return HttpClientPool.get("internal", verify_ssl=verify_ssl, timeout=timeout)


# Shutdown-Callback für Application-Lifecycle
async def close_all_http_clients() -> int:
    """Application Shutdown Callback - schließt alle HTTP-Clients."""
    return await HttpClientPool.close_all()
