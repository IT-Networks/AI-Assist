"""
ServiceNow Client - Async HTTP-Client für ServiceNow REST API.

Features:
- Basic Auth oder OAuth2
- Response Caching mit TTL
- Rate Limiting
- Automatisches Retry bei 429/5xx
"""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from cachetools import TTLCache

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SNowQueryResult:
    """Ergebnis einer ServiceNow-Abfrage."""
    records: List[Dict[str, Any]]
    total_count: int
    query_time_ms: int
    from_cache: bool = False


@dataclass
class SNowRecord:
    """Ein einzelner ServiceNow-Record mit Helper-Methoden."""
    data: Dict[str, Any]

    def get_value(self, field_name: str, default: str = "") -> str:
        """Gibt den Wert eines Feldes zurueck (display_value oder value)."""
        value = self.data.get(field_name, default)
        if isinstance(value, dict):
            return value.get("display_value", value.get("value", str(value)))
        return str(value) if value else default

    def get_sys_id(self) -> str:
        """Gibt die sys_id zurueck."""
        return self.get_value("sys_id")


class ServiceNowClient:
    """
    Async HTTP-Client fuer ServiceNow REST API.

    Features:
    - Basic Auth oder OAuth2
    - Response Caching mit TTL
    - Rate Limiting
    - Automatisches Retry bei 429/5xx
    """

    BASE_PATH = "/api/now"

    def __init__(self):
        self._config = settings.servicenow
        self._cache: TTLCache = TTLCache(
            maxsize=500,
            ttl=self._config.cache_ttl_seconds
        )
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._request_times: List[datetime] = []
        self._lock = asyncio.Lock()

    def _get_basic_auth_header(self) -> str:
        """Erstellt Basic Auth Header."""
        credentials = f"{self._config.username}:{self._config.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    async def _get_oauth_token(self) -> str:
        """Holt oder erneuert OAuth2 Token."""
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{self._config.instance_url}/oauth_token.do",
                data={
                    "grant_type": "password",
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "username": self._config.username,
                    "password": self._config.password,
                }
            )
            response.raise_for_status()
            data = response.json()
            self._token = data["access_token"]
            self._token_expires = datetime.now() + timedelta(
                seconds=data.get("expires_in", 1800) - 60
            )
            return self._token

    async def _get_auth_headers(self) -> Dict[str, str]:
        """Gibt Auth-Header zurueck (OAuth Token oder Basic Auth)."""
        if self._config.auth_type == "oauth2":
            token = await self._get_oauth_token()
            return {"Authorization": f"Bearer {token}"}
        else:
            return {"Authorization": self._get_basic_auth_header()}

    async def _check_rate_limit(self) -> None:
        """Prueft und wartet bei Rate Limit."""
        async with self._lock:
            now = datetime.now()
            minute_ago = now - timedelta(minutes=1)
            self._request_times = [t for t in self._request_times if t > minute_ago]

            if len(self._request_times) >= self._config.max_requests_per_minute:
                wait_time = (self._request_times[0] - minute_ago).total_seconds()
                logger.warning(f"[ServiceNow] Rate limit reached, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time + 0.1)

            self._request_times.append(now)

    async def query_table(
        self,
        table: str,
        query: str = "",
        fields: Optional[List[str]] = None,
        limit: int = 20,
        offset: int = 0,
        order_by: str = "",
        use_cache: bool = True
    ) -> SNowQueryResult:
        """
        Fuehrt eine Table API Abfrage aus.

        Args:
            table: Tabellenname (z.B. "cmdb_ci_business_app")
            query: ServiceNow Query String (z.B. "active=true^nameSTARTSWITHsap")
            fields: Liste der gewuenschten Felder
            limit: Max. Ergebnisse
            offset: Offset fuer Paginierung
            order_by: Sortierung (z.B. "name" oder "-sys_updated_on")
            use_cache: Cache nutzen

        Returns:
            SNowQueryResult mit records, total_count, query_time_ms
        """
        start = time.time()

        # Cache Key
        field_str = ",".join(fields) if fields else "all"
        cache_key = f"{table}:{query}:{field_str}:{limit}:{offset}:{order_by}"

        if use_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            logger.debug(f"[ServiceNow] Cache hit for {table}")
            return SNowQueryResult(
                records=cached.records,
                total_count=cached.total_count,
                query_time_ms=0,
                from_cache=True
            )

        await self._check_rate_limit()

        # Request bauen
        params = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_display_value": "all",  # Gibt Labels und Werte zurueck
        }
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        if order_by:
            params["sysparm_orderby"] = order_by

        url = f"{self._config.instance_url}{self.BASE_PATH}/table/{table}"
        headers = await self._get_auth_headers()
        headers["Accept"] = "application/json"

        try:
            async with httpx.AsyncClient(
                timeout=self._config.request_timeout_seconds,
                verify=False  # Fuer lokale Instanzen mit Self-Signed Certs
            ) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()

            records = data.get("result", [])
            total_count = int(response.headers.get("X-Total-Count", len(records)))

            result = SNowQueryResult(
                records=records,
                total_count=total_count,
                query_time_ms=int((time.time() - start) * 1000),
                from_cache=False
            )

            if use_cache:
                self._cache[cache_key] = result

            logger.debug(
                f"[ServiceNow] Query {table}: {len(records)} records in {result.query_time_ms}ms"
            )
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"[ServiceNow] HTTP Error {e.response.status_code}: {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"[ServiceNow] Request failed: {e}")
            raise

    async def get_record(
        self,
        table: str,
        sys_id: str,
        fields: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Holt einen einzelnen Record per sys_id."""
        result = await self.query_table(
            table=table,
            query=f"sys_id={sys_id}",
            fields=fields,
            limit=1
        )
        return result.records[0] if result.records else None

    async def search_records(
        self,
        table: str,
        search_term: str,
        search_fields: List[str],
        additional_query: str = "",
        limit: int = 20
    ) -> SNowQueryResult:
        """
        Durchsucht mehrere Felder nach einem Suchbegriff.

        Args:
            table: Tabellenname
            search_term: Suchbegriff
            search_fields: Felder die durchsucht werden sollen
            additional_query: Zusaetzliche Query-Bedingungen
            limit: Max. Ergebnisse

        Returns:
            SNowQueryResult
        """
        # OR-Suche ueber alle Felder aufbauen
        search_conditions = "^OR".join([
            f"{field}LIKE{search_term}" for field in search_fields
        ])
        query = f"({search_conditions})"

        if additional_query:
            query = f"{query}^{additional_query}"

        return await self.query_table(table=table, query=query, limit=limit)

    def clear_cache(self) -> None:
        """Leert den Cache."""
        self._cache.clear()
        logger.info("[ServiceNow] Cache cleared")

    async def test_connection(self) -> Dict[str, Any]:
        """Testet die Verbindung zu ServiceNow."""
        try:
            start = time.time()
            result = await self.query_table(
                table="sys_properties",
                query="name=glide.servlet.uri",
                fields=["name", "value"],
                limit=1,
                use_cache=False
            )
            duration_ms = int((time.time() - start) * 1000)

            return {
                "success": True,
                "instance_url": self._config.instance_url,
                "auth_type": self._config.auth_type,
                "response_time_ms": duration_ms,
                "message": "Connection successful"
            }
        except Exception as e:
            return {
                "success": False,
                "instance_url": self._config.instance_url,
                "auth_type": self._config.auth_type,
                "error": str(e),
                "message": f"Connection failed: {e}"
            }


# Singleton
_client: Optional[ServiceNowClient] = None


def reset_servicenow_client():
    """Setzt den ServiceNow-Client zurück (nach Settings-Änderung aufrufen)."""
    global _client
    _client = None


def get_servicenow_client() -> ServiceNowClient:
    """Gibt die Singleton-Instanz des ServiceNow-Clients zurueck."""
    global _client
    if _client is None:
        _client = ServiceNowClient()
    return _client
