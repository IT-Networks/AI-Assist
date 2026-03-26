"""
AI-Assist API Client for E2E Testing.

Provides async methods to interact with AI-Assist API
and collect metrics from LLM-Test-Proxy.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from .models import ChatResponse, ProxyMetrics

logger = logging.getLogger(__name__)


class AIAssistClient:
    """
    Async client for AI-Assist API.

    Provides methods to:
    - Send chat requests via /api/agent/chat/sync
    - Collect metrics from LLM-Test-Proxy
    - Manage sessions
    """

    def __init__(
        self,
        ai_assist_url: str = "http://localhost:8000",
        proxy_url: str = "http://localhost:8080",
        timeout: float = 120.0,
    ):
        """
        Initialize AI-Assist client.

        Args:
            ai_assist_url: Base URL for AI-Assist server
            proxy_url: Base URL for LLM-Test-Proxy
            timeout: Request timeout in seconds
        """
        self.ai_assist_url = ai_assist_url.rstrip("/")
        self.proxy_url = proxy_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AIAssistClient":
        """Enter async context."""
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        """Exit async context."""
        await self.disconnect()

    async def connect(self) -> None:
        """Create HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
        )
        logger.debug(f"AIAssistClient connected to {self.ai_assist_url}")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("AIAssistClient disconnected")

    async def health_check(self) -> Dict[str, bool]:
        """
        Check if AI-Assist and Proxy are running.

        Returns:
            Dict with ai_assist and proxy health status
        """
        result = {"ai_assist": False, "proxy": False, "mode": "unknown"}

        try:
            resp = await self._client.get(f"{self.ai_assist_url}/")
            result["ai_assist"] = resp.status_code == 200
        except Exception as e:
            logger.warning(f"AI-Assist health check failed: {e}")

        # Proxy is optional in production mode
        if self.proxy_url:
            try:
                resp = await self._client.get(f"{self.proxy_url}/health")
                result["proxy"] = resp.status_code == 200
                result["mode"] = "test"
            except Exception as e:
                logger.warning(f"Proxy health check failed: {e}")
                result["mode"] = "production"
        else:
            result["proxy"] = True  # No proxy needed in production
            result["mode"] = "production"
            logger.info("Running in production mode (no proxy)")

        return result

    async def chat_sync(
        self,
        message: str,
        model: str = "gptoss120b",
        session_id: Optional[str] = None,
    ) -> ChatResponse:
        """
        Send chat request to AI-Assist.

        Args:
            message: User message
            model: LLM model to use
            session_id: Optional session ID (creates new if None)

        Returns:
            ChatResponse with events, tool calls, and response
        """
        payload = {
            "message": message,
            "model": model,
        }
        if session_id:
            payload["session_id"] = session_id

        logger.info(f"Sending chat request: {message[:50]}...")

        response = await self._client.post(
            f"{self.ai_assist_url}/api/agent/chat/sync",
            json=payload,
        )
        response.raise_for_status()

        data = response.json()

        # Extract final_response from events if not present
        final_response = data.get("final_response", data.get("response", ""))
        if not final_response:
            for event in data.get("events", []):
                if event.get("type") == "token":
                    final_response += event.get("data", "")

        return ChatResponse(
            session_id=data.get("session_id", ""),
            events=data.get("events", []),
            response=data.get("response", ""),
            final_response=final_response,
            pending_confirmation=data.get("pending_confirmation"),
        )

    async def get_proxy_metrics(self) -> ProxyMetrics:
        """
        Get current metrics from LLM-Test-Proxy.

        Returns:
            ProxyMetrics with request counts, tokens, latency
        """
        try:
            response = await self._client.get(f"{self.proxy_url}/metrics")
            response.raise_for_status()
            data = response.json()
            return ProxyMetrics(**data)
        except Exception as e:
            logger.warning(f"Failed to get proxy metrics: {e}")
            return ProxyMetrics()

    async def clear_proxy_metrics(self) -> int:
        """
        Clear all metrics in LLM-Test-Proxy.

        Returns:
            Number of deleted records
        """
        try:
            response = await self._client.delete(f"{self.proxy_url}/metrics")
            response.raise_for_status()
            data = response.json()
            return data.get("deleted", 0)
        except Exception as e:
            logger.warning(f"Failed to clear proxy metrics: {e}")
            return 0


# Convenience function
async def create_client(
    ai_assist_url: str = "http://localhost:8000",
    proxy_url: str = "http://localhost:8080",
) -> AIAssistClient:
    """Create and connect an AI-Assist client."""
    client = AIAssistClient(ai_assist_url, proxy_url)
    await client.connect()
    return client
