"""
API-Endpunkte für Datenquellen-Verwaltung.

Datenquellen sind HTTP-Verbindungen zu internen Systemen (Jenkins, GitHub,
Log-Server, etc.). Jede Quelle bekommt ein dynamisch generiertes Tool.

Endpunkte:
    GET    /api/datasources              - Alle Datenquellen
    POST   /api/datasources              - Neue Datenquelle anlegen
    GET    /api/datasources/{id}         - Einzelne Datenquelle
    PUT    /api/datasources/{id}         - Datenquelle aktualisieren
    DELETE /api/datasources/{id}         - Datenquelle löschen
    POST   /api/datasources/{id}/test    - Verbindung testen
    POST   /api/datasources/{id}/explore - KI-gestützte Erkundung
"""

import json
import uuid
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import (
    DataSourceAuthConfig,
    DataSourceConfig,
    DataSourceParam,
    settings,
)

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _save_to_yaml() -> None:
    """Persistiert die aktuellen Datenquellen in config.yaml."""
    try:
        import os
        config_path = "config.yaml"
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Serialisieren (Pydantic v2)
        sources_data = [
            s.model_dump() for s in settings.data_sources.sources
        ]
        data["data_sources"] = {"sources": sources_data}

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern: {e}")


def _get_registry():
    from app.agent import get_tool_registry
    return get_tool_registry()


def _mask_source(source: DataSourceConfig) -> Dict:
    """Maskiert sensible Felder für die API-Ausgabe."""
    d = source.model_dump()
    auth = d.get("auth", {})
    for field in ("password", "bearer_token", "api_key_value"):
        if auth.get(field):
            auth[field] = "***"
    return d


# ── Request/Response Schemas ─────────────────────────────────────────────────

class DataSourceParamIn(BaseModel):
    name: str = ""
    type: str = "string"
    description: str = ""
    required: bool = False
    location: str = "query"


class DataSourceAuthIn(BaseModel):
    type: str = "none"
    username: str = ""
    password: str = ""
    bearer_token: str = ""
    api_key_header: str = "X-API-Key"
    api_key_value: str = ""


class DataSourceIn(BaseModel):
    name: str
    description: str = ""
    base_url: str
    verify_ssl: bool = True
    auth: DataSourceAuthIn = DataSourceAuthIn()
    custom_headers: Dict[str, str] = {}
    tool_description: str = ""
    tool_usage: str = ""
    endpoint_path: str = ""
    method: str = "GET"
    parameters: List[DataSourceParamIn] = []


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_url: Optional[str] = None
    verify_ssl: Optional[bool] = None
    auth: Optional[DataSourceAuthIn] = None
    custom_headers: Optional[Dict[str, str]] = None
    tool_description: Optional[str] = None
    tool_usage: Optional[str] = None
    endpoint_path: Optional[str] = None
    method: Optional[str] = None
    parameters: Optional[List[DataSourceParamIn]] = None


# ── CRUD Endpunkte ────────────────────────────────────────────────────────────

@router.get("")
async def list_datasources() -> Dict[str, Any]:
    """Gibt alle konfigurierten Datenquellen zurück (sensible Felder maskiert)."""
    return {
        "sources": [_mask_source(s) for s in settings.data_sources.sources],
        "count": len(settings.data_sources.sources),
    }


@router.post("")
async def create_datasource(body: DataSourceIn) -> Dict[str, Any]:
    """Legt eine neue Datenquelle an und registriert das zugehörige Tool."""
    from app.agent.datasource_tools import update_datasource_tool

    new_id = str(uuid.uuid4())[:8]

    auth = DataSourceAuthConfig(**body.auth.model_dump())
    params = [DataSourceParam(**p.model_dump()) for p in body.parameters]

    source = DataSourceConfig(
        id=new_id,
        name=body.name,
        description=body.description,
        base_url=body.base_url,
        verify_ssl=body.verify_ssl,
        auth=auth,
        custom_headers=body.custom_headers,
        tool_description=body.tool_description,
        tool_usage=body.tool_usage,
        endpoint_path=body.endpoint_path,
        method=body.method,
        parameters=params,
    )

    settings.data_sources.sources.append(source)
    _save_to_yaml()

    # Tool dynamisch registrieren
    if source.base_url:
        update_datasource_tool(_get_registry(), source)

    return {"source": _mask_source(source), "message": "Datenquelle angelegt"}


@router.get("/{source_id}")
async def get_datasource(source_id: str) -> Dict[str, Any]:
    source = next((s for s in settings.data_sources.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail=f"Datenquelle '{source_id}' nicht gefunden")
    return {"source": _mask_source(source)}


@router.put("/{source_id}")
async def update_datasource(source_id: str, body: DataSourceUpdate) -> Dict[str, Any]:
    """Aktualisiert eine Datenquelle und erneuert das Tool in der Registry."""
    from app.agent.datasource_tools import update_datasource_tool

    source = next((s for s in settings.data_sources.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail=f"Datenquelle '{source_id}' nicht gefunden")

    # Nur gesetzte Felder überschreiben
    if body.name is not None:
        source.name = body.name
    if body.description is not None:
        source.description = body.description
    if body.base_url is not None:
        source.base_url = body.base_url
    if body.verify_ssl is not None:
        source.verify_ssl = body.verify_ssl
    if body.auth is not None:
        # Maskierte Passwörter nicht überschreiben
        auth_data = body.auth.model_dump()
        current_auth = source.auth.model_dump()
        for field in ("password", "bearer_token", "api_key_value"):
            if auth_data.get(field) == "***":
                auth_data[field] = current_auth.get(field, "")
        source.auth = DataSourceAuthConfig(**auth_data)
    if body.custom_headers is not None:
        source.custom_headers = body.custom_headers
    if body.tool_description is not None:
        source.tool_description = body.tool_description
    if body.tool_usage is not None:
        source.tool_usage = body.tool_usage
    if body.endpoint_path is not None:
        source.endpoint_path = body.endpoint_path
    if body.method is not None:
        source.method = body.method
    if body.parameters is not None:
        source.parameters = [DataSourceParam(**p.model_dump()) for p in body.parameters]
        source.explored = True  # Parameter wurden gesetzt

    _save_to_yaml()
    update_datasource_tool(_get_registry(), source)

    return {"source": _mask_source(source), "message": "Datenquelle aktualisiert"}


@router.delete("/{source_id}")
async def delete_datasource(source_id: str) -> Dict[str, Any]:
    """Löscht eine Datenquelle und entfernt das Tool aus der Registry."""
    from app.agent.datasource_tools import remove_datasource_tool

    source = next((s for s in settings.data_sources.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail=f"Datenquelle '{source_id}' nicht gefunden")

    remove_datasource_tool(_get_registry(), source)
    settings.data_sources.sources = [
        s for s in settings.data_sources.sources if s.id != source_id
    ]
    _save_to_yaml()

    return {"message": f"Datenquelle '{source.name}' gelöscht"}


# ── Test-Verbindung ───────────────────────────────────────────────────────────

@router.post("/{source_id}/test")
async def test_datasource(source_id: str) -> Dict[str, Any]:
    """
    Testet die Verbindung zur Datenquelle mit einem einfachen GET-Request
    auf die Base-URL (oder den konfigurierten Standard-Endpunkt).
    """
    from app.services.datasource_client import make_datasource_request

    source = next((s for s in settings.data_sources.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="Datenquelle nicht gefunden")

    result = await make_datasource_request(source, timeout=10)

    if result["success"]:
        data = result["data"]
        preview = (
            json.dumps(data, ensure_ascii=False)[:300]
            if isinstance(data, (dict, list))
            else str(data)[:300]
        )
        return {
            "success": True,
            "status": result.get("status"),
            "preview": preview,
            "message": f"Verbindung erfolgreich (HTTP {result.get('status')})",
        }
    else:
        return {"success": False, "error": result["error"]}


# ── KI-Erkundung ─────────────────────────────────────────────────────────────

@router.post("/{source_id}/explore")
async def explore_datasource(source_id: str, body: Dict[str, Any] = {}) -> Dict[str, Any]:
    """
    Lässt die KI die Datenquelle erkunden:
    1. Macht einen Test-Request (GET base_url + endpoint_path)
    2. Schickt Response + System-Info an Claude
    3. Claude generiert: tool_description, tool_usage, parameters, Endpunkt-Beispiele

    Optional: body kann {"path": "/custom/path"} enthalten für einen spezifischen Endpunkt.
    """
    from app.core.config import settings as app_settings
    from app.services.datasource_client import make_datasource_request
    from app.services.llm_client import get_llm_client
    from app.agent.datasource_tools import update_datasource_tool

    source = next((s for s in settings.data_sources.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="Datenquelle nicht gefunden")

    explore_path = body.get("path", source.endpoint_path or "")

    # Schritt 1: Request zur Datenquelle
    result = await make_datasource_request(source, path=explore_path, timeout=15)

    if not result["success"]:
        return {
            "success": False,
            "error": f"Erkundung fehlgeschlagen: {result['error']}",
        }

    # Response vorbereiten für KI
    data = result["data"]
    if isinstance(data, (dict, list)):
        response_preview = json.dumps(data, ensure_ascii=False, indent=2)[:3000]
    else:
        response_preview = str(data)[:3000]

    # Schritt 2: KI-Anfrage
    llm = get_llm_client()
    model = (
        app_settings.llm.analysis_model
        or app_settings.llm.default_model
    )

    prompt = f"""Du analysierst eine interne HTTP-Datenquelle und erstellst eine Tool-Definition für einen KI-Agenten.

**Datenquelle:**
- Name: {source.name}
- Beschreibung: {source.description or '(keine)'}
- Basis-URL: {source.base_url}
- Getesteter Endpunkt: {explore_path or '/'}
- HTTP-Status: {result.get('status', '?')}

**API-Response (Auszug):**
```
{response_preview}
```

Erstelle basierend auf dieser Response eine optimale Tool-Definition. Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor/danach):

{{
  "tool_description": "Präzise Beschreibung was dieses System ist und was das Tool zurückgibt (2-3 Sätze)",
  "tool_usage": "Konkrete Anleitung wann der Agent dieses Tool verwenden soll, welche Fragen damit beantwortet werden können",
  "endpoint_path": "Wichtigster Endpunkt (z.B. '/api/json' oder '/api/v1/builds')",
  "method": "GET",
  "suggested_endpoints": [
    {{"path": "/example/path", "description": "Was dieser Endpunkt liefert"}},
    {{"path": "/another/path", "description": "Was dieser Endpunkt liefert"}}
  ],
  "parameters": [
    {{
      "name": "param_name",
      "type": "string",
      "description": "Was dieser Parameter bewirkt",
      "required": false,
      "location": "query"
    }}
  ]
}}

Wichtig:
- tool_description und tool_usage auf Deutsch
- parameters nur wenn aus der Response ersichtlich dass sinnvolle Filter/Parameter existieren
- location: "query" für URL-Parameter, "body" für POST-Body, "path" für URL-Pfad-Segmente
- Erkenne ob es Jenkins, GitHub, Grafana, Log-API, REST-Service etc. ist und passe Beschreibung an"""

    try:
        messages = [{"role": "user", "content": prompt}]
        # Nutze chat() statt stream - Response wird eh gesammelt
        response_text = await llm.chat(messages=messages, model=model)

        # JSON aus Response extrahieren
        import re
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if not json_match:
            return {
                "success": False,
                "error": "KI hat kein gültiges JSON zurückgegeben",
                "raw_response": response_text[:500],
            }

        suggestion = json.loads(json_match.group())

        return {
            "success": True,
            "suggestion": suggestion,
            "response_preview": response_preview[:500],
            "message": "KI-Erkundung erfolgreich",
        }

    except json.JSONDecodeError as e:
        return {
            "success": False,
            "error": f"JSON-Parsing fehlgeschlagen: {e}",
            "raw_response": response_text[:500] if 'response_text' in dir() else "",
        }
    except Exception as e:
        return {"success": False, "error": f"KI-Fehler: {e}"}


@router.post("/{source_id}/apply-suggestion")
async def apply_suggestion(source_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Übernimmt den KI-Vorschlag in die Datenquellen-Konfiguration.
    Erwartet das 'suggestion'-Objekt aus /explore.
    """
    from app.agent.datasource_tools import update_datasource_tool

    source = next((s for s in settings.data_sources.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="Datenquelle nicht gefunden")

    suggestion = body.get("suggestion", {})

    if "tool_description" in suggestion:
        source.tool_description = suggestion["tool_description"]
    if "tool_usage" in suggestion:
        source.tool_usage = suggestion["tool_usage"]
    if "endpoint_path" in suggestion:
        source.endpoint_path = suggestion["endpoint_path"]
    if "method" in suggestion:
        source.method = suggestion["method"]
    if "parameters" in suggestion:
        source.parameters = [
            DataSourceParam(
                name=p.get("name", ""),
                type=p.get("type", "string"),
                description=p.get("description", ""),
                required=p.get("required", False),
                location=p.get("location", "query"),
            )
            for p in suggestion.get("parameters", [])
        ]
    source.explored = True

    _save_to_yaml()
    update_datasource_tool(_get_registry(), source)

    return {
        "source": _mask_source(source),
        "message": "KI-Vorschlag übernommen und Tool aktualisiert",
    }
