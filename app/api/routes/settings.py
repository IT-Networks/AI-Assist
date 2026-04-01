"""
Settings API Routes - Konfiguration über das Frontend verwalten.

Features:
- Alle Einstellungen lesen
- Einzelne Sections aktualisieren
- Config-Datei speichern
- Sensitive Werte maskieren
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings, Settings, load_settings


router = APIRouter(prefix="/api/settings", tags=["settings"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class SettingsUpdateRequest(BaseModel):
    """Update für eine Settings-Section."""
    section: str = Field(..., description="Section-Name (llm, java, python, etc.)")
    values: Dict[str, Any] = Field(..., description="Neue Werte für die Section")


class SettingsSaveRequest(BaseModel):
    """Anfrage zum Speichern der Settings in config.yaml."""
    backup: bool = Field(True, description="Backup der alten config.yaml erstellen")


class ModelEntryRequest(BaseModel):
    """Model-Eintrag für die Models-Liste."""
    id: str = Field(..., max_length=100)
    display_name: str = Field(..., max_length=200)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

SENSITIVE_FIELDS = {"api_key", "password", "api_token", "secret", "token"}


def mask_sensitive(data: Dict[str, Any], unmask: bool = False) -> Dict[str, Any]:
    """Maskiert sensitive Felder in einem Dictionary."""
    if unmask:
        return data

    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = mask_sensitive(value, unmask)
        elif key in SENSITIVE_FIELDS and value:
            # Zeige nur ob ein Wert gesetzt ist
            result[key] = "********" if value and value != "none" else ""
        else:
            result[key] = value
    return result


def get_settings_dict(unmask: bool = False) -> Dict[str, Any]:
    """Konvertiert Settings zu Dictionary mit optionaler Maskierung."""
    data = settings.model_dump()
    return mask_sensitive(data, unmask)


def get_section_schema(section: str) -> Dict[str, Any]:
    """Gibt das Schema einer Settings-Section zurück."""
    section_classes = {
        "credentials": "CredentialsConfig",  # Zentrale Credentials
        "proxy": "ProxyConfig",  # Globale Proxy-Konfiguration
        "llm": "LLMConfig",
        "java": "JavaConfig",
        "python": "PythonConfig",
        "sub_agents": "SubAgentsConfig",
        "task_agents": "TaskAgentConfig",
        "tools": "ToolsConfig",
        "confluence": "ConfluenceConfig",
        "context": "ContextConfig",
        "uploads": "UploadsConfig",
        "server": "ServerConfig",
        "index": "IndexConfig",
        "handbook": "HandbookConfig",
        "skills": "SkillsConfig",
        "file_operations": "FileOperationsConfig",
        "database": "DatabaseConfig",
        "jira": "JiraConfig",
        "jenkins": "JenkinsConfig",
        "github": "GitHubConfig",
        "internal_fetch": "InternalFetchConfig",
        "docker_sandbox": "DockerSandboxConfig",
        "servicenow": "ServiceNowConfig",
        "test_tool": "TestToolConfig",
        "alm": "ALMConfig",
        "script_execution": "ScriptExecutionConfig",
    }

    if section not in section_classes:
        return {}

    from app.core import config as config_module
    config_class = getattr(config_module, section_classes[section], None)

    if not config_class:
        return {}

    # Pydantic v2 Schema
    schema = config_class.model_json_schema()
    return schema.get("properties", {})


# ══════════════════════════════════════════════════════════════════════════════
# GET Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("")
async def get_all_settings() -> Dict[str, Any]:
    """
    Gibt alle Settings zurück (sensitive Werte maskiert).

    Returns:
        Dict mit allen Settings-Sections
    """
    return {
        "settings": get_settings_dict(unmask=False),
        "sections": list(get_settings_dict().keys())
    }


@router.get("/section/{section}")
async def get_section_settings(section: str) -> Dict[str, Any]:
    """
    Gibt eine einzelne Settings-Section zurück.

    Args:
        section: Section-Name (llm, java, python, etc.)

    Returns:
        Dict mit Section-Werten und Schema
    """
    all_settings = get_settings_dict(unmask=False)

    if section not in all_settings:
        raise HTTPException(
            status_code=404,
            detail=f"Section '{section}' nicht gefunden. Verfügbar: {list(all_settings.keys())}"
        )

    return {
        "section": section,
        "values": all_settings[section],
        "schema": get_section_schema(section)
    }


@router.get("/schema")
async def get_settings_schema() -> Dict[str, Any]:
    """
    Gibt das vollständige Settings-Schema zurück.

    Nützlich für dynamische Form-Generierung im Frontend.
    """
    schema = Settings.model_json_schema()

    # Vereinfachte Section-Infos
    sections = {}
    for section in get_settings_dict().keys():
        sections[section] = {
            "schema": get_section_schema(section),
            "description": _get_section_description(section)
        }

    return {
        "sections": sections,
        "full_schema": schema
    }


def _get_section_description(section: str) -> str:
    """Gibt eine Beschreibung für eine Section zurück."""
    descriptions = {
        "credentials": "Zentrale Credentials-Verwaltung für alle Services",
        "llm": "LLM-Verbindung und Modell-Einstellungen",
        "models": "Verfügbare LLM-Modelle",
        "java": "Java-Repository-Einstellungen",
        "python": "Python-Repository-Einstellungen",
        "tools": "Pfade zu Entwickler-Tools (flake8, ruff, mypy, pytest)",
        "confluence": "Confluence-Verbindung",
        "context": "Kontext-Limits für LLM-Anfragen",
        "uploads": "Upload-Verzeichnis und Limits",
        "server": "Server-Konfiguration (Host, Port)",
        "index": "Such-Index-Einstellungen",
        "handbook": "HTML-Handbuch auf Netzlaufwerk",
        "skills": "Skill-System-Konfiguration",
        "file_operations": "Datei-Operationen (Read/Write/Edit)",
        "database": "DB2-Datenbankverbindung für Abfragen",
        "jira": "Jira-Anbindung für Issue-Suche und -Abruf",
        "sub_agents": "Parallele Sub-Agenten für Datenquellen-Recherche",
        "task_agents": "Task-Decomposition Agent System mit spezialisierten Agenten",
        "jenkins": "Jenkins CI/CD Server (intern gehostet)",
        "github": "GitHub Enterprise Server (intern gehostet)",
        "internal_fetch": "Intranet-URLs abrufen (internes HTTP-Fetch-Tool)",
        "docker_sandbox": "Container-Sandbox für sichere Code-Ausführung (Docker/Podman)",
        "alm": "HP ALM/Quality Center Testmanagement-Integration",
    }
    return descriptions.get(section, "")


# ══════════════════════════════════════════════════════════════════════════════
# Credentials API - Zentrale Credentials-Verwaltung
# ══════════════════════════════════════════════════════════════════════════════

class CredentialRequest(BaseModel):
    """Request für ein neues oder aktualisiertes Credential."""
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field("basic", pattern="^(basic|bearer|api_key)$")
    username: str = ""
    password: str = ""
    token: str = ""
    description: str = ""


@router.get("/credentials")
async def list_credentials() -> Dict[str, Any]:
    """
    Listet alle verfügbaren Credentials (nur Namen und Typen, keine Secrets).

    Für Dropdown-Listen in Service-Konfigurationen.
    """
    creds = []
    for cred in settings.credentials.credentials:
        creds.append({
            "name": cred.name,
            "type": cred.type,
            "description": cred.description,
            "has_username": bool(cred.username),
            "has_password": bool(cred.password),
            "has_token": bool(cred.token),
        })
    return {"credentials": creds}


@router.get("/credentials/{name}")
async def get_credential(name: str) -> Dict[str, Any]:
    """
    Gibt Details eines Credentials zurück (Secrets maskiert).
    """
    cred = settings.credentials.get(name)
    if not cred:
        raise HTTPException(status_code=404, detail=f"Credential '{name}' nicht gefunden")

    return {
        "name": cred.name,
        "type": cred.type,
        "username": cred.username,
        "password": "********" if cred.password else "",
        "token": "********" if cred.token else "",
        "description": cred.description,
    }


@router.post("/credentials")
async def create_credential(request: CredentialRequest) -> Dict[str, Any]:
    """
    Erstellt ein neues Credential.

    Änderungen nur im Speicher - verwende POST /save zum Persistieren.
    """
    # Prüfen ob Name bereits existiert
    if settings.credentials.get(request.name):
        raise HTTPException(status_code=400, detail=f"Credential '{request.name}' existiert bereits")

    from app.core.config import CredentialEntry
    new_cred = CredentialEntry(
        name=request.name,
        type=request.type,
        username=request.username,
        password=request.password,
        token=request.token,
        description=request.description,
    )
    settings.credentials.credentials.append(new_cred)

    return {
        "success": True,
        "message": f"Credential '{request.name}' erstellt",
        "credential": {
            "name": new_cred.name,
            "type": new_cred.type,
            "description": new_cred.description,
        }
    }


@router.put("/credentials/{name}")
async def update_credential(name: str, request: CredentialRequest) -> Dict[str, Any]:
    """
    Aktualisiert ein bestehendes Credential.

    Hinweis: Wenn password/token "********" ist, wird der alte Wert beibehalten.
    """
    cred = settings.credentials.get(name)
    if not cred:
        raise HTTPException(status_code=404, detail=f"Credential '{name}' nicht gefunden")

    # Name-Änderung erlauben
    cred.name = request.name
    cred.type = request.type
    cred.username = request.username
    cred.description = request.description

    # Secrets nur updaten wenn nicht maskiert
    if request.password and request.password != "********":
        cred.password = request.password
    if request.token and request.token != "********":
        cred.token = request.token

    return {
        "success": True,
        "message": f"Credential '{name}' aktualisiert",
    }


@router.delete("/credentials/{name}")
async def delete_credential(name: str) -> Dict[str, Any]:
    """
    Löscht ein Credential.

    Warnung: Services die dieses Credential referenzieren verlieren ihren Zugang!
    """
    cred = settings.credentials.get(name)
    if not cred:
        raise HTTPException(status_code=404, detail=f"Credential '{name}' nicht gefunden")

    settings.credentials.credentials.remove(cred)

    return {
        "success": True,
        "message": f"Credential '{name}' gelöscht",
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUT/POST Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.put("/section/{section}")
async def update_section_settings(
    section: str,
    values: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Aktualisiert eine Settings-Section (nur im Speicher).

    Hinweis: Änderungen sind nur bis zum Neustart aktiv.
    Verwende POST /save um in config.yaml zu speichern.

    Args:
        section: Section-Name
        values: Neue Werte (nur geänderte Felder nötig)

    Returns:
        Aktualisierte Section-Werte
    """
    if not hasattr(settings, section):
        raise HTTPException(
            status_code=404,
            detail=f"Section '{section}' nicht gefunden"
        )

    current_section = getattr(settings, section)

    # Models ist eine Liste, spezielle Behandlung
    if section == "models":
        if not isinstance(values, list):
            raise HTTPException(
                status_code=400,
                detail="models muss eine Liste sein"
            )
        settings.models = [
            type(settings.models[0])(**m) if settings.models else m
            for m in values
        ]
        return {
            "section": section,
            "values": [m.model_dump() if hasattr(m, 'model_dump') else m for m in settings.models],
            "saved": False,
            "message": "Änderungen nur im Speicher. POST /save zum Persistieren."
        }

    # Maskierte Werte nicht überschreiben
    for key, value in list(values.items()):
        if value == "********":
            del values[key]

    # Update der Section
    try:
        current_dict = current_section.model_dump()
        current_dict.update(values)

        # Neue Section-Instanz erstellen
        section_class = type(current_section)
        new_section = section_class(**current_dict)
        setattr(settings, section, new_section)

        # Service-Clients zurücksetzen wenn deren Config geändert wurde
        if section == "confluence":
            from app.services.confluence_client import reset_confluence_client
            reset_confluence_client()
        elif section == "jira":
            from app.services.jira_client import reset_jira_client
            reset_jira_client()
        elif section == "servicenow":
            from app.services.servicenow_client import reset_servicenow_client
            reset_servicenow_client()
        elif section == "alm":
            from app.services.alm_client import reset_alm_client
            reset_alm_client()
        elif section == "script_execution":
            # Invalidate ScriptManager singleton cache when config changes
            from app.services.script_manager import ScriptManager
            ScriptManager.invalidate_cache()

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültige Werte: {str(e)}"
        )

    return {
        "section": section,
        "values": mask_sensitive(getattr(settings, section).model_dump()),
        "saved": False,
        "message": "Änderungen nur im Speicher. POST /save zum Persistieren."
    }


@router.post("/save")
async def save_settings(request: SettingsSaveRequest = None) -> Dict[str, Any]:
    """
    Speichert die aktuellen Settings in config.yaml UND lädt sie neu.

    Wichtig: Nach dem Speichern werden die Settings aus config.yaml neu geladen,
    damit alle Änderungen sofort wirksam sind (keine Neustart nötig).

    Args:
        request: Optional - ob Backup erstellt werden soll

    Returns:
        Status der Speicherung
    """
    config_path = Path("config.yaml")
    backup_path = None

    # Backup erstellen
    if request and request.backup and config_path.exists():
        import shutil
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = Path(f"config.yaml.{timestamp}.bak")
        shutil.copy(config_path, backup_path)

    # Settings zu YAML konvertieren
    settings_dict = settings.model_dump()

    # Kommentare für bessere Lesbarkeit
    yaml_content = _generate_yaml_with_comments(settings_dict)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Speichern: {str(e)}"
        )

    # WICHTIG: Settings aus config.yaml neu laden
    # Dies stellt sicher, dass die in-memory settings mit config.yaml synchron sind
    # Besonders wichtig für pip_allowed_packages und allowed_imports
    try:
        from app.core.config import load_settings as reload_settings
        import app.core.config as config_module

        new_settings = reload_settings()
        config_module.settings = new_settings

    except Exception as e:
        # Fehler beim Neuladen sind nicht kritisch - Settings sind in YAML gespeichert
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Settings-Reload nach Save fehlgeschlagen (aber config.yaml wurde gespeichert): {e}")

    return {
        "saved": True,
        "path": str(config_path.absolute()),
        "backup_path": str(backup_path) if backup_path else None,
        "message": "Settings gespeichert und neu geladen. Änderungen sind sofort wirksam."
    }


def _save_config() -> bool:
    """
    Speichert die aktuellen Settings in config.yaml (ohne Backup).

    Diese Funktion wird von anderen Modulen aufgerufen (z.B. docker_sandbox).

    Returns:
        True bei Erfolg
    """
    config_path = Path("config.yaml")
    settings_dict = settings.model_dump()
    yaml_content = _generate_yaml_with_comments(settings_dict)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        return True
    except Exception as e:
        print(f"[settings] Fehler beim Speichern: {e}")
        return False


async def save_config_setting(section: str, key: str, value: Any) -> bool:
    """
    Speichert eine einzelne Setting in config.yaml.

    Wird von Orchestrator verwendet um Pfade zu whitelisten ohne kompletten Save-Flow.

    Args:
        section: Settings-Section (z.B. "script_execution")
        key: Setting-Name (z.B. "allowed_file_paths")
        value: Neuer Wert

    Returns:
        True bei Erfolg
    """
    try:
        # Current settings auslesen
        settings_dict = settings.model_dump()

        # Pfad zu Setting navigieren
        if section not in settings_dict:
            raise KeyError(f"Section '{section}' nicht gefunden")

        # Value aktualisieren
        if isinstance(settings_dict[section], dict):
            settings_dict[section][key] = value
        else:
            raise ValueError(f"Section '{section}' ist kein Dictionary")

        # Config-Datei speichern
        config_path = Path("config.yaml")
        yaml_content = _generate_yaml_with_comments(settings_dict)

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        # Settings neu laden
        from app.core.config import load_settings as reload_settings
        import app.core.config as config_module

        new_settings = reload_settings()
        config_module.settings = new_settings

        # Local settings aktualisieren
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Setting saved: {section}.{key}")

        return True

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to save setting {section}.{key}: {e}")
        return False


def _generate_yaml_with_comments(data: Dict[str, Any]) -> str:
    """Generiert YAML mit Kommentaren für bessere Lesbarkeit."""

    comments = {
        "llm": "# LLM-Verbindung (OpenAI-kompatibler Endpunkt)",
        "models": "# Verfügbare Modelle",
        "java": "# Java-Repository",
        "python": "# Python-Repository",
        "tools": "# Tool-Pfade (Linux/Docker)",
        "confluence": "# Confluence-Verbindung",
        "context": "# Kontext-Limits",
        "uploads": "# Upload-Einstellungen",
        "server": "# Server-Konfiguration",
        "index": "# Such-Index (SQLite FTS5)",
        "handbook": "\n# ═══════════════════════════════════════════════════════════════════\n# Handbuch-Integration (HTML auf Netzlaufwerk)",
        "skills": "# Skill-System",
        "file_operations": "# Datei-Operationen (Read/Write/Edit wie Claude Code)",
        "database": "# DB2-Datenbankverbindung",
        "jenkins": "\n# ═══════════════════════════════════════════════════════════════════\n# Jenkins CI/CD (intern gehostet)",
        "github": "# GitHub Enterprise Server (intern gehostet)",
        "internal_fetch": "# Internal Fetch (Intranet-URLs abrufen)",
        "docker_sandbox": "# Container Sandbox (Docker/Podman) - Sichere Code-Ausführung",
    }

    lines = []
    for section, values in data.items():
        if section in comments:
            lines.append(comments[section])

        # Section als YAML
        section_yaml = yaml.dump(
            {section: values},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False
        )
        lines.append(section_yaml)

    return "\n".join(lines)


@router.post("/reload")
async def reload_settings() -> Dict[str, Any]:
    """
    Lädt Settings aus config.yaml neu.

    Hinweis: Überschreibt alle im Speicher gemachten Änderungen.
    """
    global settings

    try:
        from app.core import config as config_module
        new_settings = load_settings()
        config_module.settings = new_settings

        # Auch lokale Referenz aktualisieren
        import app.core.config
        app.core.config.settings = new_settings

        # Service-Clients zurücksetzen damit neue Config verwendet wird
        from app.services.confluence_client import reset_confluence_client
        from app.services.jira_client import reset_jira_client
        from app.services.servicenow_client import reset_servicenow_client
        reset_confluence_client()
        reset_jira_client()
        reset_servicenow_client()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Neuladen: {str(e)}"
        )

    return {
        "reloaded": True,
        "message": "Settings aus config.yaml neu geladen"
    }


# ══════════════════════════════════════════════════════════════════════════════
# Agent Tools Management (DEPRECATED - Use task_agents instead)
# ══════════════════════════════════════════════════════════════════════════════
# Diese Endpoints sind veraltet. Das neue Task-Decomposition Agent System
# verwendet spezialisierte Agenten mit eigenen Modellen statt Pro-Tool-Zuweisungen.
# Siehe: settings.task_agents

@router.get("/agent-tools", deprecated=True)
async def get_agent_tools() -> Dict[str, Any]:
    """
    [DEPRECATED] Gibt alle registrierten Agent-Tools zurueck.

    Hinweis: Diese API ist veraltet. Verwende stattdessen das Task-Agent-System
    unter settings.task_agents fuer spezialisierte Agent-Modelle.
    """
    from app.agent.tools import get_tool_registry

    registry = get_tool_registry()
    tools_list = []

    for tool in registry.list_tools():
        tools_list.append({
            "name": tool.name,
            "description": tool.description,
            "category": tool.category.value,
            "is_write_operation": tool.is_write_operation,
            "model": settings.llm.tool_models.get(tool.name, ""),
        })

    return {
        "tools": tools_list,
        "tool_models": settings.llm.tool_models,
        "available_models": [m.model_dump() for m in settings.models],
        "default_model": settings.llm.default_model,
        "tool_model": settings.llm.tool_model,
        "_deprecated": True,
        "_deprecated_message": "Use task_agents config instead. Per-tool models are no longer effective.",
    }


@router.put("/agent-tools/models", deprecated=True)
async def update_tool_models(tool_models: Dict[str, str]) -> Dict[str, Any]:
    """
    [DEPRECATED] Aktualisiert Pro-Tool Modell-Zuweisungen.

    Hinweis: Diese API ist veraltet. Pro-Tool-Modelle haben keine Wirkung,
    da Tools keine LLM-Calls machen. Verwende stattdessen task_agents.
    """
    # Leere Werte entfernen (= Default verwenden)
    cleaned = {k: v for k, v in tool_models.items() if v}
    settings.llm.tool_models = cleaned

    return {
        "tool_models": settings.llm.tool_models,
        "message": "Tool-Modelle aktualisiert (DEPRECATED - hat keine Wirkung).",
        "_deprecated": True,
        "_deprecated_message": "Use task_agents config instead.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# LLM Context Limits Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/context-limits")
async def get_context_limits() -> Dict[str, Any]:
    """
    Gibt die LLM-spezifischen Kontext-Limits zurück.

    Diese Limits verhindern 500-Fehler bei Modellen mit kleinerem Kontext-Fenster.
    Wenn der Kontext das Limit überschreitet, werden ältere Tool-Ergebnisse gekürzt.
    """
    return {
        "llm_context_limits": settings.llm.llm_context_limits,
        "default_context_limit": settings.llm.default_context_limit,
        "available_models": [m.model_dump() for m in settings.models],
        "description": "Kontext-Limits in Tokens pro Modell. Größere Werte = mehr Kontext, aber Risiko von 500-Fehlern."
    }


@router.put("/context-limits")
async def update_context_limits(
    llm_context_limits: Dict[str, int],
    default_context_limit: Optional[int] = None
) -> Dict[str, Any]:
    """
    Aktualisiert die LLM-spezifischen Kontext-Limits.

    Args:
        llm_context_limits: Dict von Modell-ID zu Token-Limit (z.B. {"mistral-678b": 24000})
        default_context_limit: Optional - Standard-Limit für nicht aufgeführte Modelle

    Returns:
        Aktualisierte Limits
    """
    # Validierung: Alle Werte müssen positive Integers sein
    for model_id, limit in llm_context_limits.items():
        if not isinstance(limit, int) or limit < 1000:
            raise HTTPException(
                status_code=400,
                detail=f"Ungültiges Limit für '{model_id}': {limit}. Minimum ist 1000 Tokens."
            )

    settings.llm.llm_context_limits = llm_context_limits

    if default_context_limit is not None:
        if default_context_limit < 1000:
            raise HTTPException(
                status_code=400,
                detail=f"default_context_limit muss mindestens 1000 sein"
            )
        settings.llm.default_context_limit = default_context_limit

    return {
        "llm_context_limits": settings.llm.llm_context_limits,
        "default_context_limit": settings.llm.default_context_limit,
        "message": "Kontext-Limits aktualisiert. POST /save zum Persistieren."
    }


@router.put("/context-limits/{model_id}")
async def update_single_context_limit(model_id: str, limit: int) -> Dict[str, Any]:
    """
    Aktualisiert das Kontext-Limit für ein einzelnes Modell.

    Args:
        model_id: ID des Modells (z.B. "mistral-678b")
        limit: Token-Limit (mindestens 1000)
    """
    if limit < 1000:
        raise HTTPException(
            status_code=400,
            detail=f"Limit muss mindestens 1000 sein, nicht {limit}"
        )

    settings.llm.llm_context_limits[model_id] = limit

    return {
        "model_id": model_id,
        "limit": limit,
        "all_limits": settings.llm.llm_context_limits,
        "message": f"Kontext-Limit für '{model_id}' auf {limit} gesetzt. POST /save zum Persistieren."
    }


@router.delete("/context-limits/{model_id}")
async def delete_context_limit(model_id: str) -> Dict[str, Any]:
    """
    Entfernt das Kontext-Limit für ein Modell (nutzt dann default_context_limit).

    Args:
        model_id: ID des Modells
    """
    if model_id not in settings.llm.llm_context_limits:
        raise HTTPException(
            status_code=404,
            detail=f"Kein spezifisches Limit für '{model_id}' konfiguriert"
        )

    del settings.llm.llm_context_limits[model_id]

    return {
        "deleted": model_id,
        "remaining_limits": settings.llm.llm_context_limits,
        "will_use_default": settings.llm.default_context_limit,
        "message": f"Limit für '{model_id}' entfernt. Nutzt jetzt Default ({settings.llm.default_context_limit}). POST /save zum Persistieren."
    }


# ══════════════════════════════════════════════════════════════════════════════
# Models Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/models")
async def get_models() -> Dict[str, Any]:
    """Gibt die konfigurierten Modelle zurück."""
    return {
        "models": [m.model_dump() for m in settings.models],
        "default": settings.llm.default_model
    }


@router.post("/models")
async def add_model(model: ModelEntryRequest) -> Dict[str, Any]:
    """Fügt ein neues Modell hinzu."""
    from app.core.config import ModelEntry

    # Prüfen ob ID bereits existiert
    existing_ids = [m.id for m in settings.models]
    if model.id in existing_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Modell mit ID '{model.id}' existiert bereits"
        )

    new_model = ModelEntry(id=model.id, display_name=model.display_name)
    settings.models.append(new_model)

    return {
        "added": model.model_dump(),
        "total": len(settings.models),
        "message": "Modell hinzugefügt. POST /save zum Persistieren."
    }


@router.delete("/models/{model_id}")
async def delete_model(model_id: str) -> Dict[str, Any]:
    """Entfernt ein Modell aus der Liste."""
    original_count = len(settings.models)
    settings.models = [m for m in settings.models if m.id != model_id]

    if len(settings.models) == original_count:
        raise HTTPException(
            status_code=404,
            detail=f"Modell '{model_id}' nicht gefunden"
        )

    return {
        "deleted": model_id,
        "remaining": len(settings.models),
        "message": "Modell entfernt. POST /save zum Persistieren."
    }


# ══════════════════════════════════════════════════════════════════════════════
# Repository Management (Java/Python)
# ══════════════════════════════════════════════════════════════════════════════

class RepoRequest(BaseModel):
    """Repository-Eintrag."""
    name: str = Field(..., max_length=100, description="Anzeigename")
    path: str = Field(..., max_length=500, description="Pfad zum Repository")


@router.get("/repos/{lang}")
async def get_repos(lang: str) -> Dict[str, Any]:
    """Gibt alle Repositories für eine Sprache zurück."""
    if lang not in ("java", "python"):
        raise HTTPException(status_code=400, detail="Sprache muss 'java' oder 'python' sein")

    config = getattr(settings, lang)
    return {
        "language": lang,
        "repos": [r.model_dump() for r in config.repos],
        "active_repo": config.active_repo,
        "active_path": config.get_active_path(),
        "legacy_path": config.repo_path  # Für Kompatibilität
    }


@router.post("/repos/{lang}")
async def add_repo(lang: str, repo: RepoRequest) -> Dict[str, Any]:
    """Fügt ein neues Repository hinzu."""
    from app.core.config import RepoEntry

    if lang not in ("java", "python"):
        raise HTTPException(status_code=400, detail="Sprache muss 'java' oder 'python' sein")

    config = getattr(settings, lang)

    # Prüfen ob Name bereits existiert
    existing_names = [r.name for r in config.repos]
    if repo.name in existing_names:
        raise HTTPException(status_code=400, detail=f"Repository '{repo.name}' existiert bereits")

    # Prüfen ob Pfad existiert (Backslashes normalisieren für Windows/UNC-Pfade)
    from pathlib import Path
    check_path = repo.path.replace('\\', '/')
    if not Path(check_path).exists():
        raise HTTPException(status_code=400, detail=f"Pfad existiert nicht: {repo.path}")

    # Pfad normalisieren: Backslashes → Forward-Slashes (Windows/UNC-Pfade)
    normalized_path = repo.path.replace('\\', '/')
    new_repo = RepoEntry(name=repo.name, path=normalized_path)
    config.repos.append(new_repo)

    # Wenn erstes Repo, automatisch aktivieren
    if len(config.repos) == 1:
        config.active_repo = repo.name

    return {
        "added": repo.model_dump(),
        "total": len(config.repos),
        "active": config.active_repo,
        "message": "Repository hinzugefügt. POST /save zum Persistieren."
    }


@router.put("/repos/{lang}/active")
async def set_active_repo(lang: str, name: str) -> Dict[str, Any]:
    """Setzt das aktive Repository."""
    if lang not in ("java", "python"):
        raise HTTPException(status_code=400, detail="Sprache muss 'java' oder 'python' sein")

    config = getattr(settings, lang)

    # Prüfen ob Repo existiert
    repo_names = [r.name for r in config.repos]
    if name not in repo_names:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' nicht gefunden. Verfügbar: {repo_names}")

    config.active_repo = name

    # Auch legacy repo_path setzen für Kompatibilität
    for repo in config.repos:
        if repo.name == name:
            config.repo_path = repo.path
            break

    return {
        "active_repo": name,
        "active_path": config.get_active_path(),
        "message": "Aktives Repository geändert. POST /save zum Persistieren."
    }


@router.delete("/repos/{lang}/{name}")
async def delete_repo(lang: str, name: str) -> Dict[str, Any]:
    """Entfernt ein Repository."""
    if lang not in ("java", "python"):
        raise HTTPException(status_code=400, detail="Sprache muss 'java' oder 'python' sein")

    config = getattr(settings, lang)
    original_count = len(config.repos)
    config.repos = [r for r in config.repos if r.name != name]

    if len(config.repos) == original_count:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' nicht gefunden")

    # Wenn aktives Repo gelöscht, erstes verbleibendes aktivieren
    if config.active_repo == name:
        if config.repos:
            config.active_repo = config.repos[0].name
            config.repo_path = config.repos[0].path
        else:
            config.active_repo = ""
            config.repo_path = ""

    return {
        "deleted": name,
        "remaining": len(config.repos),
        "active": config.active_repo,
        "message": "Repository entfernt. POST /save zum Persistieren."
    }


# ══════════════════════════════════════════════════════════════════════════════
# Prompt Templates
# ══════════════════════════════════════════════════════════════════════════════

class TemplateRequest(BaseModel):
    """Request für Template-CRUD."""
    id: str = Field(default="", max_length=50)
    name: str = Field(..., max_length=100)
    description: str = Field(default="", max_length=300)
    icon: str = Field(default="message-square", max_length=50)
    category: str = Field(default="general", max_length=50)
    prompt: str = Field(..., min_length=10)
    placeholders: List[str] = Field(default_factory=list)
    sort_order: int = Field(default=100)


@router.get("/templates")
async def get_templates() -> Dict[str, Any]:
    """
    Gibt alle Prompt-Templates zurück.

    Returns:
        Liste aller Templates, gruppiert nach Kategorie
    """
    templates = settings.prompt_templates.templates
    by_category = {}

    for t in templates:
        cat = t.category or "general"
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(t.model_dump())

    # Sortieren
    for cat in by_category:
        by_category[cat].sort(key=lambda x: x.get("sort_order", 100))

    return {
        "enabled": settings.prompt_templates.enabled,
        "show_in_chat_header": settings.prompt_templates.show_in_chat_header,
        "templates": [t.model_dump() for t in templates],
        "by_category": by_category,
        "categories": list(by_category.keys())
    }


@router.get("/templates/{template_id}")
async def get_template(template_id: str) -> Dict[str, Any]:
    """Gibt ein einzelnes Template zurück."""
    for t in settings.prompt_templates.templates:
        if t.id == template_id:
            return {"template": t.model_dump()}

    raise HTTPException(status_code=404, detail=f"Template '{template_id}' nicht gefunden")


@router.post("/templates")
async def create_template(request: TemplateRequest) -> Dict[str, Any]:
    """
    Erstellt ein neues Prompt-Template.

    Args:
        request: Template-Daten

    Returns:
        Erstelltes Template
    """
    from app.core.config import PromptTemplate
    import uuid

    # ID generieren wenn nicht angegeben
    template_id = request.id or f"custom_{uuid.uuid4().hex[:8]}"

    # Prüfen ob ID bereits existiert
    existing_ids = [t.id for t in settings.prompt_templates.templates]
    if template_id in existing_ids:
        raise HTTPException(status_code=400, detail=f"Template-ID '{template_id}' existiert bereits")

    # Placeholders aus Prompt extrahieren wenn nicht angegeben
    placeholders = request.placeholders
    if not placeholders:
        import re
        placeholders = list(set(re.findall(r'\{\{(\w+)\}\}', request.prompt)))

    new_template = PromptTemplate(
        id=template_id,
        name=request.name,
        description=request.description,
        icon=request.icon,
        category=request.category,
        prompt=request.prompt,
        placeholders=placeholders,
        is_builtin=False,
        sort_order=request.sort_order
    )

    settings.prompt_templates.templates.append(new_template)

    return {
        "created": new_template.model_dump(),
        "total": len(settings.prompt_templates.templates),
        "message": "Template erstellt. POST /save zum Persistieren."
    }


@router.put("/templates/{template_id}")
async def update_template(template_id: str, request: TemplateRequest) -> Dict[str, Any]:
    """
    Aktualisiert ein Prompt-Template.

    Args:
        template_id: ID des Templates
        request: Neue Template-Daten

    Returns:
        Aktualisiertes Template
    """
    for i, t in enumerate(settings.prompt_templates.templates):
        if t.id == template_id:
            if t.is_builtin:
                raise HTTPException(
                    status_code=400,
                    detail="Builtin-Templates können nicht geändert werden. Erstelle eine Kopie."
                )

            # Placeholders aus Prompt extrahieren wenn nicht angegeben
            placeholders = request.placeholders
            if not placeholders:
                import re
                placeholders = list(set(re.findall(r'\{\{(\w+)\}\}', request.prompt)))

            # Update
            t.name = request.name
            t.description = request.description
            t.icon = request.icon
            t.category = request.category
            t.prompt = request.prompt
            t.placeholders = placeholders
            t.sort_order = request.sort_order

            return {
                "updated": t.model_dump(),
                "message": "Template aktualisiert. POST /save zum Persistieren."
            }

    raise HTTPException(status_code=404, detail=f"Template '{template_id}' nicht gefunden")


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str) -> Dict[str, Any]:
    """
    Löscht ein Prompt-Template.

    Args:
        template_id: ID des Templates

    Returns:
        Status
    """
    for t in settings.prompt_templates.templates:
        if t.id == template_id:
            if t.is_builtin:
                raise HTTPException(
                    status_code=400,
                    detail="Builtin-Templates können nicht gelöscht werden."
                )
            break
    else:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' nicht gefunden")

    original_count = len(settings.prompt_templates.templates)
    settings.prompt_templates.templates = [
        t for t in settings.prompt_templates.templates if t.id != template_id
    ]

    return {
        "deleted": template_id,
        "remaining": len(settings.prompt_templates.templates),
        "message": "Template gelöscht. POST /save zum Persistieren."
    }
