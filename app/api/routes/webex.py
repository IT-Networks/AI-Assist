"""
API-Routes für Webex Messaging Integration.

Endpoints für Verbindungstest, Räume, Nachrichten, Regeln, Todos und Automation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webex", tags=["webex"])


# ── Request/Response Models ────────────────────────────────────────────────────

class WebexRuleCreateRequest(BaseModel):
    name: str
    description: str
    room_filter: str = ""
    sender_filter: str = ""
    enabled: bool = True


class WebexRuleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    room_filter: Optional[str] = None
    sender_filter: Optional[str] = None
    enabled: Optional[bool] = None


# ── Verbindungstest ────────────────────────────────────────────────────────────

@router.post("/test")
async def test_webex_connection():
    """Testet die Webex-Verbindung."""
    from app.services.webex_client import get_webex_client
    try:
        client = get_webex_client()
        result = await client.test_connection()
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Räume ──────────────────────────────────────────────────────────────────────

@router.get("/rooms")
async def list_rooms(type: str = Query("", description="group oder direct")):
    """Listet alle Webex-Räume."""
    from app.services.webex_client import get_webex_client
    try:
        client = get_webex_client()
        rooms = await client.list_rooms(room_type=type)
        return {"rooms": rooms, "count": len(rooms)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Nachrichten ────────────────────────────────────────────────────────────────

@router.get("/rooms/{room_id}/messages")
async def get_room_messages(
    room_id: str,
    limit: int = Query(50, ge=1, le=200),
    before: str = Query("", description="Nachrichten vor diesem Zeitpunkt"),
):
    """Nachrichten eines Raums."""
    from app.services.webex_client import get_webex_client
    try:
        client = get_webex_client()
        messages = await client.get_messages(room_id=room_id, max_messages=limit, before=before)
        return {"messages": messages, "count": len(messages)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages/{message_id}")
async def get_message(message_id: str):
    """Einzelne Nachricht lesen."""
    from app.services.webex_client import get_webex_client
    try:
        client = get_webex_client()
        msg = await client.get_message(message_id)
        return {"message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Regeln CRUD ────────────────────────────────────────────────────────────────

@router.get("/rules")
async def list_rules():
    """Alle Webex-Regeln."""
    from app.services.webex_automation import get_webex_automation
    automation = get_webex_automation()
    rules = automation.get_rules()
    return {"rules": [r.model_dump() for r in rules]}


@router.post("/rules")
async def create_rule(req: WebexRuleCreateRequest):
    """Neue Webex-Regel erstellen."""
    from app.services.webex_automation import get_webex_automation
    from app.models.webex_models import WebexRule

    automation = get_webex_automation()
    rule = WebexRule(
        name=req.name,
        description=req.description,
        room_filter=req.room_filter,
        sender_filter=req.sender_filter,
        enabled=req.enabled,
    )
    created = automation.add_rule(rule)
    return {"rule": created.model_dump()}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, req: WebexRuleUpdateRequest):
    """Webex-Regel aktualisieren."""
    from app.services.webex_automation import get_webex_automation

    automation = get_webex_automation()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = automation.update_rule(rule_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    return {"rule": updated.model_dump()}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Webex-Regel löschen."""
    from app.services.webex_automation import get_webex_automation

    automation = get_webex_automation()
    if not automation.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    return {"success": True}


@router.post("/rules/{rule_id}/test")
async def test_rule(rule_id: str, limit: int = 50):
    """Testlauf einer Regel gegen die letzten Nachrichten."""
    from app.services.webex_automation import get_webex_automation

    automation = get_webex_automation()
    matches = await automation.test_rule(rule_id, limit=limit, create_todos=True)
    created = sum(1 for m in matches if m.get("todo_created"))
    return {"matches": matches, "count": len(matches), "created": created}


# ── Automation Control ─────────────────────────────────────────────────────────

@router.get("/automation/status")
async def automation_status():
    """Status der Webex-Automation."""
    from app.services.webex_automation import get_webex_automation
    automation = get_webex_automation()
    return automation.get_status()


@router.post("/automation/start")
async def automation_start():
    """Webex-Automation starten."""
    from app.services.webex_automation import get_webex_automation
    automation = get_webex_automation()
    await automation.start()
    return {"success": True, "status": "running"}


@router.post("/automation/stop")
async def automation_stop():
    """Webex-Automation stoppen."""
    from app.services.webex_automation import get_webex_automation
    automation = get_webex_automation()
    await automation.stop()
    return {"success": True, "status": "stopped"}
