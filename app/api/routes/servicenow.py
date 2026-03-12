"""
ServiceNow API Routes - ServiceNow Service Portal Integration.

Features:
- Connection Test
- Settings Update
- Cache Management
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/api/servicenow", tags=["servicenow"])


# ============================================================================
# Request/Response Models
# ============================================================================

class ServiceNowSettingsUpdate(BaseModel):
    """Update fuer ServiceNow Settings."""
    enabled: Optional[bool] = None
    instance_url: Optional[str] = None
    auth_type: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    cache_ttl_seconds: Optional[int] = None
    max_results_default: Optional[int] = None
    custom_app_tables: Optional[List[str]] = None


class ConnectionTestResponse(BaseModel):
    """Response fuer Connection Test."""
    success: bool
    instance_url: str
    auth_type: str
    response_time_ms: Optional[int] = None
    error: Optional[str] = None
    message: str


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/status")
async def get_servicenow_status() -> Dict[str, Any]:
    """
    Gibt den aktuellen Status der ServiceNow Integration zurueck.

    Returns:
        Dict mit Status und Konfiguration
    """
    config = settings.servicenow
    return {
        "enabled": config.enabled,
        "instance_url": config.instance_url,
        "auth_type": config.auth_type,
        "has_credentials": bool(config.username and config.password),
        "cache_ttl_seconds": config.cache_ttl_seconds,
        "max_results_default": config.max_results_default,
        "custom_app_tables": config.custom_app_tables,
        "tables": {
            "business_app": config.business_app_table,
            "incident": config.incident_table,
            "change": config.change_table,
            "knowledge": config.knowledge_table,
        }
    }


@router.post("/test-connection")
async def test_servicenow_connection() -> ConnectionTestResponse:
    """
    Testet die Verbindung zu ServiceNow.

    Returns:
        ConnectionTestResponse mit Ergebnis
    """
    if not settings.servicenow.enabled:
        return ConnectionTestResponse(
            success=False,
            instance_url=settings.servicenow.instance_url or "(not configured)",
            auth_type=settings.servicenow.auth_type,
            message="ServiceNow integration is disabled"
        )

    if not settings.servicenow.instance_url:
        return ConnectionTestResponse(
            success=False,
            instance_url="(not configured)",
            auth_type=settings.servicenow.auth_type,
            message="No instance URL configured"
        )

    try:
        from app.services.servicenow_client import get_servicenow_client
        client = get_servicenow_client()
        result = await client.test_connection()

        return ConnectionTestResponse(
            success=result.get("success", False),
            instance_url=result.get("instance_url", ""),
            auth_type=result.get("auth_type", ""),
            response_time_ms=result.get("response_time_ms"),
            error=result.get("error"),
            message=result.get("message", "")
        )
    except Exception as e:
        return ConnectionTestResponse(
            success=False,
            instance_url=settings.servicenow.instance_url,
            auth_type=settings.servicenow.auth_type,
            error=str(e),
            message=f"Connection test failed: {e}"
        )


@router.post("/clear-cache")
async def clear_servicenow_cache() -> Dict[str, str]:
    """
    Leert den ServiceNow Response Cache.

    Returns:
        Bestaetigung
    """
    try:
        from app.services.servicenow_client import get_servicenow_client
        client = get_servicenow_client()
        client.clear_cache()
        return {"message": "Cache cleared successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables")
async def list_common_tables() -> Dict[str, Any]:
    """
    Listet gaengige ServiceNow Tabellen fuer CMDB-Abfragen.

    Returns:
        Dict mit Tabellen-Kategorien
    """
    return {
        "applications": [
            {"table": "cmdb_ci_business_app", "description": "Business Applications"},
            {"table": "cmdb_ci_service", "description": "Business Services"},
            {"table": "cmdb_ci_service_discovered", "description": "Discovered Services"},
        ],
        "infrastructure": [
            {"table": "cmdb_ci_server", "description": "Server"},
            {"table": "cmdb_ci_vm_instance", "description": "Virtual Machines"},
            {"table": "cmdb_ci_linux_server", "description": "Linux Server"},
            {"table": "cmdb_ci_win_server", "description": "Windows Server"},
            {"table": "cmdb_ci_database", "description": "Databases"},
            {"table": "cmdb_ci_db_instance", "description": "Database Instances"},
        ],
        "network": [
            {"table": "cmdb_ci_ip_network", "description": "IP Networks"},
            {"table": "cmdb_ci_ip_router", "description": "Router"},
            {"table": "cmdb_ci_ip_switch", "description": "Switches"},
            {"table": "cmdb_ci_lb", "description": "Load Balancers"},
        ],
        "itsm": [
            {"table": "incident", "description": "Incidents"},
            {"table": "change_request", "description": "Change Requests"},
            {"table": "problem", "description": "Problems"},
            {"table": "sc_request", "description": "Service Requests"},
            {"table": "sc_req_item", "description": "Requested Items"},
        ],
        "knowledge": [
            {"table": "kb_knowledge", "description": "Knowledge Articles"},
            {"table": "kb_category", "description": "Knowledge Categories"},
        ],
        "custom": settings.servicenow.custom_app_tables or []
    }


@router.get("/query-help")
async def get_query_syntax_help() -> Dict[str, Any]:
    """
    Gibt Hilfe zur ServiceNow Query-Syntax zurueck.

    Returns:
        Dict mit Query-Syntax-Hilfe
    """
    return {
        "description": "ServiceNow Encoded Query Syntax",
        "operators": {
            "=": "Equals (exact match)",
            "!=": "Not equals",
            "LIKE": "Contains (case-insensitive)",
            "STARTSWITH": "Starts with",
            "ENDSWITH": "Ends with",
            "IN": "In list (comma-separated)",
            "NOT IN": "Not in list",
            ">": "Greater than",
            ">=": "Greater than or equal",
            "<": "Less than",
            "<=": "Less than or equal",
            "ISEMPTY": "Field is empty",
            "ISNOTEMPTY": "Field is not empty",
        },
        "logical": {
            "^": "AND",
            "^OR": "OR",
            "^NQ": "New Query (OR with different table)",
        },
        "examples": [
            {
                "query": "active=true",
                "description": "All active records"
            },
            {
                "query": "nameLIKEsap",
                "description": "Name contains 'sap'"
            },
            {
                "query": "priority=1^stateNOT IN6,7",
                "description": "Priority 1 AND state not 6 or 7"
            },
            {
                "query": "opened_at>=javascript:gs.beginningOfLast7Days()",
                "description": "Opened in last 7 days"
            },
            {
                "query": "sys_updated_on>2024-01-01",
                "description": "Updated after date"
            }
        ]
    }
