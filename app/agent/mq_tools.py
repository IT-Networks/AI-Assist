"""
Agent-Tools für MQ-Series-Integration.
Werden in der Tool-Registry des Orchestrators registriert.
"""

from typing import Any

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.core.http_client import get_mq_client


def register_mq_tools(registry: ToolRegistry) -> int:
    """Registriert alle MQ-Tools. Gibt Anzahl registrierter Tools zurück."""
    from app.core.config import settings

    if not settings.mq.enabled or not settings.mq.queues:
        return 0

    count = 0

    # ── mq_list_queues ─────────────────────────────────────────────────────────
    async def mq_list_queues(**kwargs: Any) -> ToolResult:
        queues = [
            {
                "id": q.id,
                "name": q.name,
                "service": q.service,
                "role": q.role,
                "description": q.description,
                "url": q.url,
            }
            for q in settings.mq.queues
        ]
        return ToolResult(success=True, data={"queues": queues, "count": len(queues)})

    registry.register(Tool(
        name="mq_list_queues",
        description=(
            "Listet alle konfigurierten MQ-Queues auf. "
            "Zeigt Queue-ID, Name, zugehörigen Service und Rolle (trigger/read/both)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=mq_list_queues,
    ))
    count += 1

    # ── mq_get_message ────────────────────────────────────────────────────────
    async def mq_get_message(**kwargs: Any) -> ToolResult:
        import json, logging
        logger = logging.getLogger(__name__)

        queue_id: str = kwargs.get("queue_id", "")
        extra_headers: dict = kwargs.get("extra_headers", {})
        if isinstance(extra_headers, str):
            try:
                extra_headers = json.loads(extra_headers)
            except json.JSONDecodeError as e:
                logger.warning(f"MQ: Ungültige extra_headers JSON: {e}")
                return ToolResult(success=False, error=f"Ungültige extra_headers JSON: {e}")

        queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
        if not queue:
            return ToolResult(success=False, error=f"Queue '{queue_id}' nicht gefunden")

        merged_headers = dict(queue.headers)
        merged_headers.update(extra_headers)
        try:
            client = get_mq_client(queue.verify_ssl, queue.timeout_seconds)
            resp = await client.get(queue.url, headers=merged_headers)
            text = resp.text
            try:
                data = resp.json()
            except json.JSONDecodeError:
                logger.debug("MQ: Response ist kein JSON")
                data = text
            return ToolResult(
                success=resp.is_success,
                data={"status_code": resp.status_code, "body": data, "raw": text[:2000]},
                error=None if resp.is_success else f"HTTP {resp.status_code}",
            )
        except httpx.TimeoutException:
            return ToolResult(success=False, error=f"Timeout nach {queue.timeout_seconds}s")
        except Exception as e:
            logger.warning(f"MQ get_message Fehler: {e}")
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="mq_get_message",
        description=(
            "Liest eine Nachricht von einer konfigurierten MQ-Queue per HTTP. "
            "Verwende mq_list_queues um verfügbare Queue-IDs zu finden. "
            "extra_headers überschreibt oder ergänzt die Queue-Header (als JSON-Objekt)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="queue_id", type="string", description="ID der Queue", required=True),
            ToolParameter(name="extra_headers", type="string", description='Zusätzliche Header als JSON-Objekt, z.B. {"X-Custom": "value"}', required=False),
        ],
        handler=mq_get_message,
    ))
    count += 1

    # ── mq_put_message ────────────────────────────────────────────────────────
    async def mq_put_message(**kwargs: Any) -> ToolResult:
        import json, logging
        logger = logging.getLogger(__name__)

        queue_id: str = kwargs.get("queue_id", "")
        body: str = kwargs.get("body", "")
        params_str: str = kwargs.get("template_params", "{}")
        extra_headers_str: str = kwargs.get("extra_headers", "{}")

        try:
            template_params = json.loads(params_str) if params_str else {}
        except json.JSONDecodeError as e:
            logger.warning(f"MQ: Ungültige template_params JSON: {e}")
            return ToolResult(success=False, error=f"Ungültige template_params JSON: {e}")
        try:
            extra_headers = json.loads(extra_headers_str) if extra_headers_str else {}
        except json.JSONDecodeError as e:
            logger.warning(f"MQ: Ungültige extra_headers JSON: {e}")
            return ToolResult(success=False, error=f"Ungültige extra_headers JSON: {e}")

        queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
        if not queue:
            return ToolResult(success=False, error=f"Queue '{queue_id}' nicht gefunden")

        # Body aus Template ableiten wenn kein expliziter Body
        if not body and queue.body_template:
            body = queue.body_template
            for k, v in template_params.items():
                body = body.replace(f"{{{{{k}}}}}", str(v))

        merged_headers = dict(queue.headers)
        merged_headers.update(extra_headers)
        method = queue.method if queue.method in ("POST", "PUT", "PATCH") else "POST"

        try:
            client = get_mq_client(queue.verify_ssl, queue.timeout_seconds)
            resp = await client.request(
                method=method,
                url=queue.url,
                headers=merged_headers,
                content=body.encode() if body else None,
            )
            text = resp.text
            try:
                data = resp.json()
            except json.JSONDecodeError:
                logger.debug("MQ: Response ist kein JSON")
                data = text
            return ToolResult(
                success=resp.is_success,
                data={"status_code": resp.status_code, "body": data},
                error=None if resp.is_success else f"HTTP {resp.status_code}: {text[:200]}",
            )
        except httpx.TimeoutException:
            return ToolResult(success=False, error=f"Timeout nach {queue.timeout_seconds}s")
        except Exception as e:
            logger.warning(f"MQ put_message Fehler: {e}")
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="mq_put_message",
        description=(
            "Spielt eine Nachricht in eine MQ-Queue ein (HTTP POST/PUT). "
            "Kann einen direkten Body oder ein Template mit Parametern verwenden. "
            "extra_headers erlaubt KI-seitige Header-Überschreibung."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(name="queue_id", type="string", description="ID der Queue", required=True),
            ToolParameter(name="body", type="string", description="Roher Nachrichten-Body (JSON oder Text)", required=False),
            ToolParameter(name="template_params", type="string", description='Parameter für body_template als JSON-Objekt, z.B. {"orderId": "42"}', required=False),
            ToolParameter(name="extra_headers", type="string", description='Zusätzliche oder überschreibende Header als JSON-Objekt', required=False),
        ],
        handler=mq_put_message,
    ))
    count += 1

    return count
