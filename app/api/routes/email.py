"""
API-Routes für Exchange E-Mail Integration.

Endpoints für Verbindungstest, Suche, Lesen, Entwürfe,
Ordner-Verwaltung, Attachment-Download, Regeln, Todos und Automation.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email", tags=["email"])


# ── Request/Response Models ────────────────────────────────────────────────────

class EmailSearchRequest(BaseModel):
    query: str = ""
    sender: str = ""
    subject: str = ""
    folder: str = "inbox"
    date_from: str = ""
    date_to: str = ""
    limit: int = Field(default=20, ge=1, le=100)


class EmailDraftRequest(BaseModel):
    to: str
    subject: str
    body: str
    reply_to_id: str = ""


class RuleCreateRequest(BaseModel):
    name: str
    description: str
    sender_filter: str = ""
    enabled: bool = True


class RuleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sender_filter: Optional[str] = None
    enabled: Optional[bool] = None


class TodoStatusRequest(BaseModel):
    status: str  # new, read, done


class DraftReplyRequest(BaseModel):
    instructions: str = ""


class RuleTestRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


# ══════════════════════════════════════════════════════════════════════════════
# E-Mail Basis-Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """Testet die Verbindung zum Exchange Server."""
    from app.core.config import settings

    if not settings.email.enabled:
        return {"success": False, "error": "E-Mail-Integration ist nicht aktiviert."}
    if not settings.email.ews_url:
        return {"success": False, "error": "EWS-URL ist nicht konfiguriert."}

    try:
        from app.services.email_client import get_email_client
        client = get_email_client()
        result = await client.test_connection()
        return result
    except Exception as e:
        logger.error("Email Verbindungstest Fehler: %s", e)
        return {"success": False, "error": str(e)}


@router.get("/folders")
async def list_folders() -> Dict[str, Any]:
    """Listet alle E-Mail-Ordner auf."""
    _check_enabled()

    try:
        from app.services.email_client import get_email_client
        client = get_email_client()
        folders = await client.list_folders()
        return {"success": True, "folders": folders}
    except Exception as e:
        logger.error("Email Ordner-Listing Fehler: %s", e)
        return {"success": False, "error": str(e)}


@router.post("/search")
async def search_emails(request: EmailSearchRequest) -> Dict[str, Any]:
    """Durchsucht E-Mails mit Filtern."""
    _check_enabled()

    try:
        from app.services.email_client import get_email_client
        client = get_email_client()
        results, total = await client.search_emails(
            query=request.query,
            sender=request.sender,
            subject=request.subject,
            folder=request.folder,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
        )
        return {"success": True, "results": results, "total": total}
    except Exception as e:
        logger.error("Email Suche Fehler: %s", e)
        return {"success": False, "error": str(e), "results": [], "total": 0}


@router.get("/read/{email_id}")
async def read_email(email_id: str, folder: str = Query(default="inbox")) -> Dict[str, Any]:
    """Liest eine einzelne E-Mail mit vollem Body."""
    _check_enabled()

    try:
        from app.services.email_client import get_email_client
        client = get_email_client()
        email_data = await client.read_email(email_id=email_id, folder=folder)
        return {"success": True, "email": email_data}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error("Email Lesen Fehler: %s", e)
        return {"success": False, "error": str(e)}


@router.get("/attachment/{email_id}/{attachment_name}")
async def download_attachment(
    email_id: str,
    attachment_name: str,
    folder: str = Query(default="inbox"),
) -> Response:
    """Lädt ein E-Mail-Attachment herunter."""
    _check_enabled()

    try:
        from app.services.email_client import get_email_client
        client = get_email_client()
        content, content_type = await client.get_attachment(
            email_id=email_id,
            attachment_name=attachment_name,
            folder=folder,
        )
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{attachment_name}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Attachment Download Fehler: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft")
async def create_draft(request: EmailDraftRequest) -> Dict[str, Any]:
    """Erstellt einen E-Mail-Entwurf im Drafts-Ordner."""
    _check_enabled()

    if not request.to or not request.subject:
        return {"success": False, "error": "Empfänger und Betreff sind erforderlich."}

    try:
        from app.services.email_client import get_email_client
        client = get_email_client()
        result = await client.create_draft(
            to=request.to,
            subject=request.subject,
            body=request.body,
            reply_to_id=request.reply_to_id,
        )
        return result
    except Exception as e:
        logger.error("Draft Erstellen Fehler: %s", e)
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# Regeln (CRUD)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/rules")
async def get_rules() -> Dict[str, Any]:
    """Alle E-Mail-Automation-Regeln."""
    from app.services.email_automation import get_email_automation
    automation = get_email_automation()
    rules = automation.get_rules()
    return {"rules": [r.model_dump() for r in rules]}


@router.post("/rules")
async def create_rule(request: RuleCreateRequest) -> Dict[str, Any]:
    """Neue Regel erstellen."""
    from app.services.email_automation import get_email_automation
    from app.models.email_models import EmailRule

    rule = EmailRule(
        name=request.name,
        description=request.description,
        sender_filter=request.sender_filter,
        enabled=request.enabled,
    )
    automation = get_email_automation()
    created = automation.add_rule(rule)
    return {"success": True, "rule": created.model_dump()}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, request: RuleUpdateRequest) -> Dict[str, Any]:
    """Regel aktualisieren."""
    from app.services.email_automation import get_email_automation

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    automation = get_email_automation()
    updated = automation.update_rule(rule_id, updates)

    if updated is None:
        raise HTTPException(status_code=404, detail=f"Regel '{rule_id}' nicht gefunden.")

    return {"success": True, "rule": updated.model_dump()}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str) -> Dict[str, Any]:
    """Regel löschen."""
    from app.services.email_automation import get_email_automation

    automation = get_email_automation()
    if automation.delete_rule(rule_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Regel '{rule_id}' nicht gefunden.")


@router.post("/rules/{rule_id}/test")
async def test_rule(rule_id: str, request: RuleTestRequest = RuleTestRequest()) -> Dict[str, Any]:
    """Testlauf einer Regel gegen die letzten N Mails."""
    _check_enabled()

    from app.services.email_automation import get_email_automation

    automation = get_email_automation()
    if not automation.get_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"Regel '{rule_id}' nicht gefunden.")

    matches = await automation.test_rule(rule_id, limit=request.limit)
    return {"success": True, "matches": matches, "tested": request.limit}


# ══════════════════════════════════════════════════════════════════════════════
# Todos (CRUD + SSE)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/todos")
async def get_todos(status: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """Alle Todos, optional nach Status gefiltert."""
    from app.services.todo_store import get_todo_store

    store = get_todo_store()
    todos = store.get_all(status=status)
    counts = store.get_counts()

    return {
        "todos": [t.model_dump() for t in todos],
        "counts": counts,
    }


@router.get("/todos/stream")
async def todos_stream():
    """SSE-Stream für Todo-Updates (new_todo, todo_count Events)."""
    from app.services.todo_store import get_todo_store

    store = get_todo_store()
    queue = store.subscribe()

    async def event_generator():
        try:
            # Initial: sende aktuelle Counts
            counts = store.get_counts()
            yield f"event: todo_count\ndata: {json.dumps(counts)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    event_type = event.get("event", "message")
                    data = json.dumps(event.get("data", {}))
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            store.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/todos/{todo_id}")
async def get_todo(todo_id: str) -> Dict[str, Any]:
    """Ein Todo nach ID."""
    from app.services.todo_store import get_todo_store

    store = get_todo_store()
    todo = store.get_by_id(todo_id)

    if todo is None:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' nicht gefunden.")

    return {"todo": todo.model_dump()}


@router.put("/todos/{todo_id}/status")
async def update_todo_status(todo_id: str, request: TodoStatusRequest) -> Dict[str, Any]:
    """Status eines Todos ändern."""
    from app.services.todo_store import get_todo_store

    store = get_todo_store()
    if store.update_status(todo_id, request.status):
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' nicht gefunden.")


@router.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str) -> Dict[str, Any]:
    """Todo löschen."""
    from app.services.todo_store import get_todo_store

    store = get_todo_store()
    if store.delete(todo_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' nicht gefunden.")


@router.post("/todos/{todo_id}/draft-reply")
async def create_todo_draft_reply(todo_id: str, request: DraftReplyRequest = DraftReplyRequest()) -> Dict[str, Any]:
    """Generiert einen KI-Antwort-Entwurf basierend auf einem Todo."""
    _check_enabled()
    from app.services.todo_store import get_todo_store
    from app.services.llm_client import llm_client

    store = get_todo_store()
    todo = store.get_by_id(todo_id)

    if todo is None:
        raise HTTPException(status_code=404, detail=f"Todo '{todo_id}' nicht gefunden.")

    # LLM-Prompt für Antwort-Entwurf
    system_prompt = (
        "Erstelle einen professionellen Antwort-Entwurf für die folgende E-Mail. "
        "Berücksichtige das erkannte Todo und den Kontext."
    )
    if request.instructions:
        system_prompt += f"\n\nZusätzliche Anweisungen: {request.instructions}"

    system_prompt += (
        "\n\nAntworte NUR im folgenden JSON-Format (kein anderer Text):\n"
        '{"subject": "Re: ...", "body": "Antwort-Text"}'
    )

    mail = todo.mail_snapshot
    user_prompt = (
        f"Original-Mail:\n"
        f"Von: {mail.sender} ({mail.sender_name})\n"
        f"Betreff: {mail.subject}\n"
        f"Inhalt: {mail.body_text[:2000]}\n\n"
        f"Erkanntes Todo: {todo.todo_text}\n"
        f"KI-Analyse: {todo.ai_analysis}"
    )

    try:
        response = await llm_client.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        # JSON aus Antwort extrahieren
        import re
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r'\{[^{}]*"subject"[^{}]*"body"[^{}]*\}', response, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                result = {
                    "subject": f"Re: {mail.subject}",
                    "body": response,
                }

        return {
            "success": True,
            "draft_subject": result.get("subject", f"Re: {mail.subject}"),
            "draft_body": result.get("body", ""),
        }
    except Exception as e:
        logger.error("Antwort-Entwurf Fehler: %s", e)
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# Automation Control
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/automation/start")
async def start_automation() -> Dict[str, Any]:
    """Startet das E-Mail-Polling."""
    _check_enabled()

    from app.services.email_automation import get_email_automation
    automation = get_email_automation()
    await automation.start()
    return {"success": True, "message": "E-Mail-Automation gestartet."}


@router.post("/automation/stop")
async def stop_automation() -> Dict[str, Any]:
    """Stoppt das E-Mail-Polling."""
    from app.services.email_automation import get_email_automation
    automation = get_email_automation()
    await automation.stop()
    return {"success": True, "message": "E-Mail-Automation gestoppt."}


@router.get("/automation/status")
async def automation_status() -> Dict[str, Any]:
    """Status der E-Mail-Automation."""
    from app.services.email_automation import get_email_automation
    automation = get_email_automation()
    return automation.get_status()


# ══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════════════

def _check_enabled():
    """Prüft ob E-Mail-Integration aktiviert ist."""
    from app.core.config import settings
    if not settings.email.enabled:
        raise HTTPException(status_code=400, detail="E-Mail-Integration ist nicht aktiviert.")
