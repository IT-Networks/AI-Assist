"""
Agent-Tools für das Test-Tool (HTTP-Service-Ausführung + lokale Ausführung).
"""

import json
from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def register_testtool_tools(registry: ToolRegistry) -> int:
    """Registriert Test-Tool-Agents. Gibt Anzahl zurück."""
    from app.core.config import settings

    if not settings.test_tool.enabled:
        return 0

    count = 0

    # ── testtool_list_services ────────────────────────────────────────────────
    async def testtool_list_services(**kwargs: Any) -> ToolResult:
        services = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "endpoint": s.endpoint,
                "method": s.method,
                "params": [{"name": p.name, "required": p.required, "type": p.type} for p in s.parameters],
                "has_local": bool(s.local_script),
            }
            for s in settings.test_tool.services
        ]
        stages = [{"id": s.id, "name": s.name} for s in settings.test_tool.stages]
        return ToolResult(success=True, data={
            "services": services,
            "stages": stages,
            "active_stage": settings.test_tool.active_stage,
        })

    registry.register(Tool(
        name="testtool_list_services",
        description=(
            "Listet alle konfigurierten Test-Services und Stages auf. "
            "Zeigt Endpoints, Parameter und ob lokale Ausführung möglich ist."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=testtool_list_services,
    ))
    count += 1

    # ── testtool_execute_service ──────────────────────────────────────────────
    async def testtool_execute_service(**kwargs: Any) -> ToolResult:
        import httpx
        import logging
        logger = logging.getLogger(__name__)

        svc_id: str = kwargs.get("service_id", "")
        params_str: str = kwargs.get("params", "{}")
        stage_url: str = kwargs.get("stage_url", "")

        # Timeout mit Range-Validierung (1-300 Sekunden)
        try:
            timeout_raw = int(kwargs.get("timeout_seconds", settings.test_tool.default_timeout_seconds))
            timeout: int = max(1, min(timeout_raw, 300))
        except (ValueError, TypeError):
            timeout = settings.test_tool.default_timeout_seconds

        # JSON-Parsing mit Logging statt silent fail
        try:
            params = json.loads(params_str) if params_str else {}
        except json.JSONDecodeError as e:
            logger.warning(f"TestTool: Ungültige JSON-Parameter: {e}")
            return ToolResult(success=False, error=f"Ungültige JSON-Parameter: {e}")

        svc = next((s for s in settings.test_tool.services if s.id == svc_id), None)
        if not svc:
            return ToolResult(success=False, error=f"Service '{svc_id}' nicht gefunden")

        # Basis-URL ermitteln
        if not stage_url:
            stage = next((s for s in settings.test_tool.stages if s.id == settings.test_tool.active_stage), None)
            if not stage or not stage.urls:
                return ToolResult(success=False, error="Keine aktive Stage oder URL konfiguriert")
            stage_url = stage.urls[0].url

        base_url = stage_url.rstrip("/")
        endpoint = svc.endpoint
        for k, v in params.items():
            endpoint = endpoint.replace(f"{{{k}}}", str(v))

        query_params, body_params = {}, {}
        for p in svc.parameters:
            val = params.get(p.name)
            if val is None:
                continue
            if p.location == "query":
                query_params[p.name] = val
            else:
                body_params[p.name] = val

        param_names = {p.name for p in svc.parameters}
        for k, v in params.items():
            if k not in param_names:
                body_params[k] = v

        headers = {"Content-Type": svc.content_type}
        headers.update(svc.headers)
        body = json.dumps(body_params) if body_params else None
        url = base_url + endpoint

        try:
            # SSL-Verifizierung aus Config (nicht hardcoded False)
            verify_ssl = getattr(settings.test_tool, 'verify_ssl', True)
            async with httpx.AsyncClient(timeout=timeout, verify=verify_ssl) as client:
                resp = await client.request(
                    method=svc.method,
                    url=url,
                    headers=headers,
                    params=query_params or None,
                    content=body.encode() if body else None,
                )
            text = resp.text
            try:
                data = resp.json()
            except json.JSONDecodeError:
                logger.debug(f"TestTool: Response ist kein JSON, verwende raw text")
                data = text

            return ToolResult(
                success=resp.is_success,
                data={
                    "status_code": resp.status_code,
                    "url": str(resp.url),
                    "response": data,
                    "raw": text[:3000],
                    "elapsed_ms": int(resp.elapsed.total_seconds() * 1000) if resp.elapsed else None,
                },
                error=None if resp.is_success else f"HTTP {resp.status_code}: {text[:200]}",
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="testtool_execute_service",
        description=(
            "Führt einen konfigurierten Service per HTTP aus und gibt das Ergebnis zurück. "
            "Nutze testtool_list_services für verfügbare Service-IDs und Parameter. "
            "params ist ein JSON-Objekt mit den Eingabe-Parametern."
        ),
        category=ToolCategory.FILE,
        is_write_operation=False,
        parameters=[
            ToolParameter(name="service_id", type="string", description="ID des Services", required=True),
            ToolParameter(name="params", type="string", description='Eingabe-Parameter als JSON-Objekt, z.B. {"customerId": "123"}', required=False),
            ToolParameter(name="stage_url", type="string", description="Basis-URL der Stage (überschreibt aktive Stage)", required=False),
            ToolParameter(name="timeout_seconds", type="integer", description="Timeout in Sekunden", required=False),
        ],
        handler=testtool_execute_service,
    ))
    count += 1

    return count
