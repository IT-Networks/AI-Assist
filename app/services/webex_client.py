"""
Webex Messaging Client - HTTP-Client für die Webex REST API.

Nutzt httpx für async HTTP-Aufrufe mit Proxy- und Rate-Limit-Support.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class WebexClient:
    """Async HTTP-Client für Webex REST API."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Gibt den HTTP-Client zurück (Lazy Init)."""
        if self._client is None or self._client.is_closed:
            from app.core.config import settings

            # Token aus Config oder credential_ref
            token = self._get_token()
            if not token:
                raise ValueError("Kein Webex Access-Token konfiguriert")

            # Proxy-Konfiguration
            proxy = None
            if settings.proxy.enabled and settings.proxy.url:
                proxy = settings.proxy.url

            self._client = httpx.AsyncClient(
                base_url=settings.webex.base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=settings.webex.timeout_seconds,
                verify=True,
                proxy=proxy,
            )
        return self._client

    def _get_token(self) -> str:
        """Holt den Access-Token aus Config oder credential_ref."""
        from app.core.config import settings

        if settings.webex.access_token:
            return settings.webex.access_token

        if settings.webex.credential_ref:
            for cred in settings.credentials.entries:
                if cred.name == settings.webex.credential_ref:
                    return cred.password or cred.token or ""
        return ""

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Zentrale Request-Methode mit Rate-Limit-Retry."""
        client = self._get_client()
        max_retries = 3

        for attempt in range(max_retries):
            try:
                response = await client.request(method, path, **kwargs)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    logger.warning("Webex Rate-Limit, warte %d Sekunden", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json() if response.content else {}

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    await asyncio.sleep(5)
                    continue
                raise

        raise RuntimeError("Webex API: Max Retries erreicht")

    # ── API-Methoden ──────────────────────────────────────────────────────────

    async def test_connection(self) -> dict:
        """GET /people/me - Verbindungstest."""
        data = await self._request("GET", "/people/me")
        return {
            "success": True,
            "display_name": data.get("displayName", ""),
            "email": data.get("emails", [""])[0] if data.get("emails") else "",
            "org_id": data.get("orgId", ""),
        }

    async def list_rooms(self, room_type: str = "", max_rooms: int = 50) -> List[dict]:
        """GET /rooms - Räume auflisten."""
        params: Dict[str, Any] = {"sortBy": "lastactivity", "max": max_rooms}
        if room_type in ("group", "direct"):
            params["type"] = room_type

        data = await self._request("GET", "/rooms", params=params)
        rooms = []
        for item in data.get("items", []):
            rooms.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "last_activity": item.get("lastActivity", ""),
                "created": item.get("created", ""),
                "is_locked": item.get("isLocked", False),
            })
        return rooms

    async def get_messages(
        self, room_id: str, max_messages: int = 50, before: str = ""
    ) -> List[dict]:
        """GET /messages?roomId=... - Nachrichten eines Raums."""
        params: Dict[str, Any] = {"roomId": room_id, "max": max_messages}
        if before:
            params["before"] = before

        data = await self._request("GET", "/messages", params=params)
        return self._format_messages(data.get("items", []))

    async def get_direct_messages(self, person_email: str) -> List[dict]:
        """GET /messages/direct?personEmail=... - Direktnachrichten."""
        data = await self._request(
            "GET", "/messages/direct", params={"personEmail": person_email}
        )
        return self._format_messages(data.get("items", []))

    async def get_message(self, message_id: str) -> dict:
        """GET /messages/{id} - Einzelne Nachricht."""
        data = await self._request("GET", f"/messages/{message_id}")
        return self._format_message(data)

    async def get_new_messages_since(
        self, room_ids: List[str], since: datetime, max_per_room: int = 50
    ) -> List[dict]:
        """Neue Nachrichten aus mehreren Räumen seit Zeitpunkt."""
        all_messages = []
        since_iso = since.isoformat() + "Z" if not since.isoformat().endswith("Z") else since.isoformat()

        for room_id in room_ids:
            try:
                params: Dict[str, Any] = {
                    "roomId": room_id,
                    "max": max_per_room,
                }
                data = await self._request("GET", "/messages", params=params)
                for item in data.get("items", []):
                    created = item.get("created", "")
                    if created and created >= since_iso:
                        msg = self._format_message(item)
                        msg["room_id"] = room_id
                        all_messages.append(msg)
            except Exception as e:
                logger.error("Fehler beim Abrufen von Raum %s: %s", room_id[:20], e)

        return all_messages

    async def get_rooms_for_polling(self) -> List[str]:
        """Gibt alle Raum-IDs zurück die für Polling relevant sind."""
        rooms = await self.list_rooms(max_rooms=100)
        return [r["id"] for r in rooms]

    async def close(self) -> None:
        """HTTP-Client schließen."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _format_messages(self, items: List[dict]) -> List[dict]:
        """Formatiert eine Liste von Nachrichten."""
        return [self._format_message(item) for item in items]

    def _format_message(self, item: dict) -> dict:
        """Formatiert eine einzelne Nachricht."""
        return {
            "id": item.get("id", ""),
            "room_id": item.get("roomId", ""),
            "room_type": item.get("roomType", ""),
            "person_id": item.get("personId", ""),
            "person_email": item.get("personEmail", ""),
            "person_display_name": item.get("personDisplayName", ""),
            "text": item.get("text", ""),
            "html": item.get("html", ""),
            "created": item.get("created", ""),
            "updated": item.get("updated", ""),
            "parent_id": item.get("parentId", ""),
            "has_files": bool(item.get("files")),
            "file_count": len(item.get("files", [])),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_webex_client: Optional[WebexClient] = None


def get_webex_client() -> WebexClient:
    """Gibt den Singleton Webex-Client zurück."""
    global _webex_client
    if _webex_client is None:
        _webex_client = WebexClient()
    return _webex_client


async def close_webex_client() -> None:
    """Schließt den Singleton Webex-Client."""
    global _webex_client
    if _webex_client:
        await _webex_client.close()
        _webex_client = None
