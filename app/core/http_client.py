"""
Shared HTTP Client Pool - Zentrale Verwaltung von HTTP-Clients.

Eliminiert Duplikation und ermöglicht effizientes Connection-Pooling
über alle Tool-Module hinweg (GitHub, Jenkins, MQ, TestTool, etc.).

Performance-Vorteile:
- Vermeidet TCP/TLS-Handshake bei jedem Request (~200ms Ersparnis)
- Ermöglicht Keep-Alive-Verbindungen
- Zentrale Konfiguration von Timeouts und Limits
"""

import logging
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class HttpClientPool:
    """
    Zentrale Verwaltung von HTTP-Clients mit Lazy-Initialization.

    Verwendung:
        client = HttpClientPool.get("github", verify_ssl=False, timeout=30)
        response = await client.get(url)

    Bei Application-Shutdown:
        await HttpClientPool.close_all()
    """

    _clients: Dict[str, httpx.AsyncClient] = {}

    # Standard-Limits für alle Clients
    DEFAULT_MAX_CONNECTIONS = 10
    DEFAULT_KEEPALIVE_CONNECTIONS = 5
    DEFAULT_KEEPALIVE_EXPIRY = 30.0
    DEFAULT_TIMEOUT = 30

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
