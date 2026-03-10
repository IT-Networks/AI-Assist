"""
Dynamische Tool-Generierung für konfigurierte HTTP-Datenquellen.

Für jede Datenquelle in settings.data_sources.sources wird automatisch
ein Tool registriert, das HTTP-Requests gegen das System ausführt.
Das Tool-Schema (Beschreibung, Parameter, Verwendungszweck) wird entweder
manuell oder per KI-Erkundung befüllt.
"""

import json
import logging
import re
from typing import Any, Dict

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult
from app.core.config import DataSourceConfig

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Wandelt einen Namen in einen gültigen Tool-Bezeichner um."""
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:40] or "unnamed"


def get_datasource_tool_name(source: DataSourceConfig) -> str:
    return f"ds_{_slugify(source.name)}"


def create_datasource_tool(source: DataSourceConfig) -> Tool:
    """Erstellt ein Tool-Objekt aus einer DataSourceConfig."""

    tool_name = get_datasource_tool_name(source)

    # Beschreibung zusammenbauen
    desc_parts = []
    if source.description:
        desc_parts.append(source.description)
    if source.tool_description:
        desc_parts.append(source.tool_description)
    if source.tool_usage:
        desc_parts.append(f"Wann verwenden: {source.tool_usage}")
    desc_parts.append(f"Basis-URL: {source.base_url}")
    full_description = "\n\n".join(desc_parts)

    # Parameter aufbauen
    params: list[ToolParameter] = []

    # Pfad-Parameter nur wenn kein fester Endpunkt konfiguriert
    if not source.endpoint_path:
        params.append(
            ToolParameter(
                "path",
                "string",
                "API-Endpunkt-Pfad (z.B. '/api/v1/builds', '/job/MyJob/api/json')",
                required=False,
                default="",
            )
        )

    # Konfigurierte Parameter aus der Tool-Definition
    for p in source.parameters:
        params.append(
            ToolParameter(
                name=p.name,
                type=p.type,
                description=p.description,
                required=p.required,
                default=None,
            )
        )

    # source_id in Closure capturen damit der Handler bei Settings-Änderungen
    # immer die aktuelle Konfiguration aus settings liest
    source_id = source.id

    async def handler(**kwargs: Any) -> ToolResult:
        from app.core.config import settings
        from app.services.datasource_client import make_datasource_request

        # Aktuelle Konfiguration holen (kann sich seit Tool-Erstellung geändert haben)
        src = next(
            (s for s in settings.data_sources.sources if s.id == source_id), None
        )
        if not src:
            return ToolResult(
                success=False,
                error=f"Datenquelle (id={source_id}) nicht mehr konfiguriert. "
                      "Bitte Einstellungen prüfen.",
            )

        path = kwargs.pop("path", src.endpoint_path or "")
        method = kwargs.pop("_method", src.method or "GET")

        # Parameter nach location aufteilen
        query_params: Dict[str, Any] = {}
        body_params: Dict[str, Any] = {}
        for p in src.parameters:
            if p.name in kwargs:
                val = kwargs.pop(p.name)
                if p.location == "body":
                    body_params[p.name] = val
                else:
                    query_params[p.name] = val

        # Übrige kwargs als Query-Parameter
        query_params.update(kwargs)

        result = await make_datasource_request(
            src,
            path=path,
            method=method,
            params=query_params or None,
            body=body_params or None,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        data = result["data"]
        if isinstance(data, (dict, list)):
            text = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            text = str(data)

        # Antwort auf sinnvolle Größe begrenzen
        if len(text) > 12000:
            text = text[:12000] + "\n\n[... Antwort gekürzt ...]"

        return ToolResult(success=True, data=text)

    return Tool(
        name=tool_name,
        description=full_description,
        category=ToolCategory.SEARCH,
        parameters=params,
        handler=handler,
    )


def register_datasource_tools(registry) -> int:
    """
    Registriert alle konfigurierten Datenquellen-Tools in der Registry.
    Wird beim Server-Start aufgerufen.
    Gibt die Anzahl erfolgreich registrierter Tools zurück.
    """
    from app.core.config import settings

    count = 0
    for source in settings.data_sources.sources:
        if source.id and source.name and source.base_url:
            try:
                tool = create_datasource_tool(source)
                registry.register(tool)
                count += 1
                logger.debug("Datasource-Tool registriert: %s (%s)", tool.name, source.base_url)
            except Exception as e:
                logger.warning("Datasource-Tool Fehler für '%s': %s", source.name, e)
    return count


def update_datasource_tool(registry, source: DataSourceConfig) -> None:
    """Fügt ein Datenquellen-Tool hinzu oder ersetzt es (nach Create/Update)."""
    if source.id and source.name and source.base_url:
        tool = create_datasource_tool(source)
        registry.register(tool)


def remove_datasource_tool(registry, source: DataSourceConfig) -> None:
    """Entfernt das Tool einer Datenquelle aus der Registry (nach Delete)."""
    tool_name = get_datasource_tool_name(source)
    registry._tools.pop(tool_name, None)
