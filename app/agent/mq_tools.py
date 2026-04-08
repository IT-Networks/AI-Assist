"""
Agent-Tools für MQ-Series-Integration (Message Queues per HTTP).
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
                "method": q.method,
                "description": q.description,
                "url": q.url,
                "has_body_template": bool(q.body_template),
                "body_template_preview": q.body_template[:200] if q.body_template else "",
            }
            for q in settings.mq.queues
        ]
        return ToolResult(success=True, data={"queues": queues, "count": len(queues)})

    registry.register(Tool(
        name="mq_list_queues",
        description=(
            "Listet alle konfigurierten MQ-Queues auf. IMMER zuerst aufrufen bevor mq_read_queue "
            "oder mq_trigger_queue verwendet werden. Zeigt pro Queue: "
            "ID (für Folge-Aufrufe), Name, role (read=auslesen, trigger=einspielen, both=beides), "
            "method (GET/POST/PUT), Service-Beschreibung und ob ein Body-Template hinterlegt ist. "
            "Authentifizierung und Header sind pro Queue vorkonfiguriert – keine Zugangsdaten nötig."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=mq_list_queues,
    ))
    count += 1

    # ── mq_read_queue ─────────────────────────────────────────────────────────
    async def mq_read_queue(**kwargs: Any) -> ToolResult:
        import json, logging
        logger = logging.getLogger(__name__)

        queue_id: str = kwargs.get("queue_id", "")
        extra_headers: dict = kwargs.get("extra_headers", {})
        if isinstance(extra_headers, str):
            try:
                extra_headers = json.loads(extra_headers) if extra_headers else {}
            except json.JSONDecodeError as e:
                return ToolResult(success=False, error=f"Ungültige extra_headers JSON: {e}")

        queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
        if not queue:
            return ToolResult(success=False, error=f"Queue '{queue_id}' nicht gefunden. Nutze mq_list_queues für verfügbare IDs.")

        merged_headers = dict(queue.headers)
        merged_headers.update(extra_headers)
        try:
            client = get_mq_client(queue.verify_ssl, queue.timeout_seconds)
            resp = await client.get(queue.effective_url, headers=merged_headers)
            text = resp.text
            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = text
            return ToolResult(
                success=resp.is_success,
                data={
                    "queue": queue.name,
                    "service": queue.service,
                    "status_code": resp.status_code,
                    "body": data,
                    "raw": text[:5000],
                },
                error=None if resp.is_success else f"HTTP {resp.status_code}: {text[:200]}",
            )
        except httpx.TimeoutException:
            return ToolResult(success=False, error=f"Timeout nach {queue.timeout_seconds}s für Queue '{queue.name}'")
        except Exception as e:
            logger.warning(f"MQ read_queue Fehler: {e}")
            return ToolResult(success=False, error=f"Verbindungsfehler: {e}")

    registry.register(Tool(
        name="mq_read_queue",
        description=(
            "Liest den aktuellen Inhalt einer MQ-Queue per HTTP GET aus. "
            "Nutze dies um zu prüfen welche Nachrichten in einer Queue liegen, "
            "z.B. nach einem Testlauf oder um den Queue-Status zu kontrollieren. "
            "Authentifizierung erfolgt automatisch – keine Zugangsdaten nötig. "
            "Voraussetzung: mq_list_queues aufrufen um die queue_id zu erhalten. "
            "Verwende nur Queues mit role 'read' oder 'both'."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="queue_id", type="string", description="ID der Queue (aus mq_list_queues)", required=True),
            ToolParameter(name="extra_headers", type="string", description='Zusätzliche Header als JSON, z.B. {"Accept": "application/xml"}', required=False),
        ],
        handler=mq_read_queue,
    ))
    count += 1

    # ── mq_trigger_queue ──────────────────────────────────────────────────────
    async def mq_trigger_queue(**kwargs: Any) -> ToolResult:
        import json, logging
        logger = logging.getLogger(__name__)

        queue_id: str = kwargs.get("queue_id", "")
        body: str = kwargs.get("body", "")
        params_str: str = kwargs.get("template_params", "{}")
        extra_headers_str: str = kwargs.get("extra_headers", "{}")

        try:
            template_params = json.loads(params_str) if params_str else {}
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"Ungültige template_params JSON: {e}")
        try:
            extra_headers = json.loads(extra_headers_str) if extra_headers_str else {}
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"Ungültige extra_headers JSON: {e}")

        queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
        if not queue:
            return ToolResult(success=False, error=f"Queue '{queue_id}' nicht gefunden. Nutze mq_list_queues für verfügbare IDs.")

        # Body: explizit übergeben oder aus Template generieren
        effective_body = body
        if not effective_body and queue.body_template:
            effective_body = queue.body_template
            for k, v in template_params.items():
                effective_body = effective_body.replace(f"{{{{{k}}}}}", str(v))

        if not effective_body:
            return ToolResult(success=False, error="Kein Body angegeben und kein body_template in der Queue konfiguriert.")

        merged_headers = dict(queue.headers)
        merged_headers.update(extra_headers)
        method = queue.method if queue.method in ("POST", "PUT", "PATCH") else "POST"

        try:
            client = get_mq_client(queue.verify_ssl, queue.timeout_seconds)
            resp = await client.request(
                method=method,
                url=queue.effective_url,
                headers=merged_headers,
                content=effective_body.encode() if effective_body else None,
            )
            text = resp.text
            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = text
            return ToolResult(
                success=resp.is_success,
                data={
                    "queue": queue.name,
                    "service": queue.service,
                    "method": method,
                    "status_code": resp.status_code,
                    "body_sent": effective_body[:500],
                    "response": data,
                },
                error=None if resp.is_success else f"HTTP {resp.status_code}: {text[:200]}",
            )
        except httpx.TimeoutException:
            return ToolResult(success=False, error=f"Timeout nach {queue.timeout_seconds}s für Queue '{queue.name}'")
        except Exception as e:
            logger.warning(f"MQ trigger_queue Fehler: {e}")
            return ToolResult(success=False, error=f"Verbindungsfehler: {e}")

    registry.register(Tool(
        name="mq_trigger_queue",
        description=(
            "Sendet eine Nachricht an eine MQ-Queue per HTTP POST/PUT (Schreiboperation). "
            "Nutze dies um einen Service zu triggern, eine Nachricht einzuspielen oder "
            "einen Verarbeitungsprozess anzustoßen. "
            "Wenn die Queue ein body_template hat, können template_params übergeben werden "
            "um Platzhalter wie {{orderId}} zu ersetzen. Alternativ kann ein eigener body "
            "direkt angegeben werden. "
            "Authentifizierung erfolgt automatisch – keine Zugangsdaten nötig. "
            "Voraussetzung: mq_list_queues aufrufen um die queue_id zu erhalten. "
            "Verwende nur Queues mit role 'trigger' oder 'both'."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(name="queue_id", type="string", description="ID der Queue (aus mq_list_queues)", required=True),
            ToolParameter(name="body", type="string", description="Nachrichten-Body als JSON oder Text. Wenn leer, wird das body_template der Queue verwendet.", required=False),
            ToolParameter(name="template_params", type="string", description='Parameter für body_template als JSON, z.B. {"orderId": "42", "amount": "100.00"}', required=False),
            ToolParameter(name="extra_headers", type="string", description='Zusätzliche Header als JSON, z.B. {"Content-Type": "application/xml"}', required=False),
        ],
        handler=mq_trigger_queue,
    ))
    count += 1

    return count
