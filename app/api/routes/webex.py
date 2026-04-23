"""
API-Routes für Webex Messaging Integration.

Endpoints für Verbindungstest, Räume, Nachrichten, Regeln, Todos und Automation.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
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


# ── OAuth2 Flow ───────────────────────────────────────────────────────────────

@router.get("/oauth/url")
async def get_oauth_url():
    """Generiert die OAuth2 Authorization URL für den Browser-Login."""
    from app.core.config import settings
    from app.services.webex_client import WebexClient

    if not settings.webex.client_id or not settings.webex.client_secret:
        return {"success": False, "error": "Client-ID und Client-Secret müssen in den Settings konfiguriert sein."}

    url = WebexClient.get_auth_url()
    return {"success": True, "auth_url": url}


@router.get("/oauth/callback")
async def oauth_callback(code: str = Query(""), state: str = Query(""), error: str = Query("")):
    """OAuth2 Callback - empfängt den Authorization Code und tauscht ihn gegen Tokens."""
    import html
    from fastapi.responses import HTMLResponse
    from app.services.webex_client import WebexClient

    if error:
        return HTMLResponse(f"""<html><body>
            <h2>Webex OAuth Fehler</h2><p>{html.escape(error)}</p>
            <p>Fenster kann geschlossen werden.</p>
        </body></html>""")

    if not code:
        return HTMLResponse("""<html><body>
            <h2>Fehler</h2><p>Kein Authorization Code erhalten.</p>
        </body></html>""")

    try:
        result = await WebexClient.exchange_code(code)
        days = result.get('expires_in', 0) // 86400
        has_refresh = 'vorhanden' if result.get('has_refresh') else 'nicht vorhanden'
        return HTMLResponse(f"""<html><body>
            <h2>Webex Verbindung erfolgreich!</h2>
            <p>Access-Token erhalten (g&uuml;ltig {days} Tage).</p>
            <p>Refresh-Token: {has_refresh}</p>
            <p><strong>Dieses Fenster kann geschlossen werden.</strong></p>
            <script>setTimeout(() => window.close(), 3000);</script>
        </body></html>""")
    except Exception as e:
        logger.error("Webex OAuth Token-Exchange fehlgeschlagen: %s", e)
        return HTMLResponse(f"""<html><body>
            <h2>Token-Exchange fehlgeschlagen</h2>
            <p>{html.escape(str(e))}</p>
        </body></html>""")


@router.get("/oauth/status")
async def oauth_status():
    """Prüft den OAuth-Token-Status (liest auch aus webex_tokens.json)."""
    from app.core.config import settings
    from app.services.webex_client import _load_persisted_tokens
    from datetime import datetime

    # Sicherstellen dass persistierte Tokens geladen sind
    if not settings.webex.access_token:
        _load_persisted_tokens()

    has_token = bool(settings.webex.access_token)
    has_refresh = bool(settings.webex.refresh_token)
    expires_at = settings.webex.token_expires_at

    expired = False
    if expires_at:
        try:
            expired = datetime.now() >= datetime.fromisoformat(expires_at)
        except (ValueError, TypeError):
            pass

    return {
        "has_token": has_token,
        "has_refresh": has_refresh,
        "expires_at": expires_at or "",
        "expired": expired,
        "has_client_credentials": bool(settings.webex.client_id and settings.webex.client_secret),
    }


# ── Verbindungstest ────────────────────────────────────────────────────────────

@router.post("/test")
async def test_webex_connection():
    """Testet die Webex-Verbindung (erstellt Client neu für aktuelle Settings)."""
    from app.services.webex_client import get_webex_client
    try:
        client = get_webex_client()
        # Client neu erstellen damit aktuelle Settings (verify_ssl etc.) wirken
        await client.close()
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


@router.get("/rooms/picker")
async def list_rooms_for_picker(
    q: str = Query("", description="Suchbegriff für Titel/ID (case-insensitive)"),
    type: str = Query("", description="Optional: group|direct"),
    limit: int = Query(200, ge=1, le=2000),
):
    """Schlanke Room-Liste für Settings-Dropdown mit Suchfunktion.

    Unterschied zu ``/rooms``:
    - Server-seitiger Substring-Filter über Titel + ID (``q``)
    - Gekappt auf ``limit`` Einträge (Default 200 — Dropdown-Performance)
    - Nur die vier UI-relevanten Felder (id/title/type/last_activity)
    """
    from app.services.webex_client import get_webex_client
    try:
        client = get_webex_client()
        rooms = await client.list_all_rooms(
            room_type=type or "",
            name_contains=q if q else "",
            max_total=max(limit, 200),
        )
    except Exception as e:
        logger.warning("[webex] rooms/picker failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    # Normalisieren + client-seitiger Fallback-Filter (für ID-Substring)
    q_lower = (q or "").strip().lower()
    out = []
    for r in rooms:
        title = str(r.get("title") or "")
        rid = str(r.get("id") or "")
        if q_lower and q_lower not in title.lower() and q_lower not in rid.lower():
            continue
        out.append({
            "id": rid,
            "title": title,
            "type": str(r.get("type") or r.get("room_type") or ""),
            "last_activity": r.get("last_activity") or r.get("lastActivity") or "",
        })
        if len(out) >= limit:
            break
    return {"rooms": out, "count": len(out), "query": q}


# ── Datei-Download (Proxy) ─────────────────────────────────────────────────────

@router.get("/file")
async def download_webex_file(url: str = Query(..., description="Webex-Datei-URL")):
    """Proxy-Download einer Webex-Datei (Bilder, Dokumente).

    Das Frontend kann Webex-Datei-URLs nicht direkt laden (Auth nötig).
    Diese Route fungiert als Proxy mit dem gespeicherten Access-Token.
    """
    from app.services.webex_client import get_webex_client

    if not url or "webexapis.com" not in url:
        raise HTTPException(status_code=400, detail="Ungültige Webex-Datei-URL")

    try:
        client = get_webex_client()
        content, content_type, filename = await client.download_file(url)
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "private, max-age=3600",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Nachrichten ────────────────────────────────────────────────────────────────

@router.get("/rooms/{room_id:path}/messages")
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


@router.get("/messages/{message_id:path}")
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


# ── AI-Assist Chat-Bot (dedizierter Webex-Room als Remote-Terminal) ──────────

@router.get("/bot/status")
async def bot_status():
    """Status des AI-Assist-Chat-Bots."""
    from app.services.webex_bot_service import get_assist_room_handler
    handler = get_assist_room_handler()
    return handler.get_status()


@router.post("/bot/start")
async def bot_start():
    """Startet den AI-Assist-Chat-Bot (resolved Room, postet Greeting, startet Poller)."""
    from app.services.webex_bot_service import get_assist_room_handler
    handler = get_assist_room_handler()
    try:
        status = await handler.start()
        return {"success": True, "status": status}
    except Exception as e:
        logger.error("Webex-Bot start fehlgeschlagen: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bot/stop")
async def bot_stop():
    """Stoppt den AI-Assist-Chat-Bot (Poller aus, Agent-Runs bleiben laufen)."""
    from app.services.webex_bot_service import get_assist_room_handler
    handler = get_assist_room_handler()
    await handler.stop()
    return {"success": True, "status": handler.get_status()}


@router.post("/bot/cancel")
async def bot_cancel(room_id: str = Query("", description="Room-ID oder leer fuer Default-Bot-Room")):
    """Bricht einen laufenden Agent-Run im Bot-Room ab."""
    from app.services.webex_bot_service import get_assist_room_handler
    handler = get_assist_room_handler()
    cancelled = await handler.cancel(room_id)
    return {"success": True, "cancelled": cancelled}


# ── Webhook-Endpoint + Webhook-Management (Phase 2) ──────────────────────────

@router.post("/webhooks/webex")
async def webex_webhook_receiver(request: Request) -> Response:
    """Empfaengt Webex-Webhook-Events (resource=messages, event=created).

    Verifiziert X-Spark-Signature (HMAC-SHA1 des Raw-Bodies mit webhook_secret)
    und antwortet sofort mit 200 OK. Die Verarbeitung laeuft asynchron im
    Hintergrund, damit Webex nicht in Timeouts laeuft.
    """
    from app.core.config import settings
    from app.services.webex_bot_service import get_assist_room_handler

    raw_body = await request.body()
    signature = request.headers.get("X-Spark-Signature", "") or request.headers.get("x-spark-signature", "")
    secret = settings.webex.bot.webhook_secret or ""

    # HMAC-Verifikation — bei gesetztem Secret PFLICHT
    if secret:
        if not AssistRoomHandler_verify(secret, raw_body, signature):
            logger.warning("[webex-bot] Webhook signature invalid (len=%d)", len(raw_body))
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        logger.warning("[webex-bot] webhook_secret not set — signature NOT verified")

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as e:
        logger.warning("[webex-bot] Webhook: ungueltiger JSON-Body: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Fire-and-forget Dispatch — keep reference to prevent GC
    handler = get_assist_room_handler()
    task = asyncio.create_task(handler.on_webhook_event(payload))
    _background_webhook_tasks.add(task)
    task.add_done_callback(_background_webhook_tasks.discard)

    return Response(status_code=200)


def AssistRoomHandler_verify(secret: str, body: bytes, signature: str) -> bool:
    """Lazy-dispatched HMAC-Verifikation (laed den Handler erst bei Bedarf)."""
    from app.services.webex_bot_service import AssistRoomHandler
    return AssistRoomHandler.verify_signature(secret, body, signature)


# Haelt Referenzen auf laufende Hintergrund-Tasks, damit sie nicht vom GC
# geraeumt werden bevor on_webhook_event fertig ist.
_background_webhook_tasks: "set[asyncio.Task]" = set()


@router.post("/webhooks/attachment-actions")
async def webex_attachment_actions_receiver(request: Request) -> Response:
    """Empfaengt Webex ``attachmentActions.created`` Webhook-Events (Sprint 2).

    Werden ausgeloest wenn ein User auf einen Adaptive-Card-Button klickt.
    Wir laden die Action-Details nach (inputs) und leiten sie an den
    AssistRoomHandler weiter, der die ApprovalBus aufloest.
    """
    from app.core.config import settings
    from app.services.webex_bot_service import get_assist_room_handler

    raw_body = await request.body()
    signature = request.headers.get("X-Spark-Signature", "") or request.headers.get("x-spark-signature", "")
    secret = settings.webex.bot.webhook_secret or ""

    if secret:
        if not AssistRoomHandler_verify(secret, raw_body, signature):
            logger.warning("[webex-bot] attachment-actions: signature invalid (len=%d)", len(raw_body))
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        logger.warning("[webex-bot] attachment-actions: webhook_secret not set — skipping verify")

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as e:
        logger.warning("[webex-bot] attachment-actions: invalid JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    handler = get_assist_room_handler()
    task = asyncio.create_task(handler.on_attachment_action_event(payload))
    _background_webhook_tasks.add(task)
    task.add_done_callback(_background_webhook_tasks.discard)
    return Response(status_code=200)


@router.post("/bot/register-webhook")
async def bot_register_webhook():
    """One-shot: Registriert / aktualisiert den Webex-Webhook fuer den Bot-Room.

    Erwartet dass der Bot bereits per /bot/start gestartet wurde (Room resolved).
    """
    from app.services.webex_bot_service import get_assist_room_handler
    handler = get_assist_room_handler()
    if not handler._room_id:
        raise HTTPException(
            status_code=400,
            detail="Bot nicht gestartet — zuerst POST /api/webex/bot/start aufrufen.",
        )
    try:
        webhook_id = await handler.ensure_webhook()
        return {"success": True, "webhook_id": webhook_id, "status": handler.get_status()}
    except Exception as e:
        logger.error("Webex-Bot ensure_webhook fehlgeschlagen: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bot/webhooks")
async def bot_list_webhooks():
    """Listet alle beim Webex-Account/Bot registrierten Webhooks auf."""
    from app.services.webex_client import get_webex_client
    client = get_webex_client()
    try:
        hooks = await client.list_webhooks(max_hooks=100)
        return {"success": True, "count": len(hooks), "webhooks": hooks}
    except Exception as e:
        logger.error("list_webhooks fehlgeschlagen: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/bot/webhooks/{webhook_id:path}")
async def bot_delete_webhook(webhook_id: str):
    """Entfernt einen Webex-Webhook anhand seiner ID."""
    from app.services.webex_client import get_webex_client
    from app.services.webex_bot_service import get_assist_room_handler
    client = get_webex_client()
    try:
        await client.delete_webhook(webhook_id)
        handler = get_assist_room_handler()
        if handler._webhook_id == webhook_id:
            handler._webhook_id = ""
        return {"success": True, "deleted": webhook_id}
    except Exception as e:
        logger.error("delete_webhook fehlgeschlagen: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
