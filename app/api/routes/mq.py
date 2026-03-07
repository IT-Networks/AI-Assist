"""
MQ-Series API – Queue-Definitionen verwalten und Nachrichten lesen/einspielen.

Routes:
  GET    /api/mq/queues              – Alle Queues auflisten
  POST   /api/mq/queues              – Queue hinzufügen
  PUT    /api/mq/queues/{id}         – Queue aktualisieren
  DELETE /api/mq/queues/{id}         – Queue löschen
  POST   /api/mq/queues/{id}/test    – Verbindungstest
  POST   /api/mq/queues/{id}/get     – Nachricht lesen (GET-Request)
  POST   /api/mq/queues/{id}/put     – Nachricht einspielen (POST/PUT-Request)
"""

import uuid
import httpx
import json

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings, MQQueue

router = APIRouter(prefix="/api/mq", tags=["mq"])


# ── Request Models ────────────────────────────────────────────────────────────

class QueueRequest(BaseModel):
    name: str
    description: str = ""
    url: str
    method: str = "GET"
    service: str = ""
    role: str = "read"
    headers: Dict[str, str] = {}
    body_template: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = 30


class PutMessageRequest(BaseModel):
    """Body für das Einspielen einer Nachricht."""
    body: Optional[str] = None          # Roher JSON-String oder beliebiger Text
    params: Dict[str, Any] = {}         # Variablen für body_template-Ersetzung
    extra_headers: Dict[str, str] = {}  # KI-übersteuerte oder ad-hoc Header


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_headers(queue: MQQueue, extra: Dict[str, str] = {}) -> Dict[str, str]:
    """Zusammengeführte Header: Queue-Default + extra (Übersteurung)."""
    merged = dict(queue.headers)
    merged.update(extra)
    return merged


async def _http_request(
    queue: MQQueue,
    method: str,
    body: Optional[str] = None,
    extra_headers: Dict[str, str] = {},
) -> Dict[str, Any]:
    headers = _build_headers(queue, extra_headers)
    timeout = httpx.Timeout(queue.timeout_seconds)
    content = body.encode() if body else None

    async with httpx.AsyncClient(verify=queue.verify_ssl, timeout=timeout) as client:
        resp = await client.request(
            method=method,
            url=queue.url,
            headers=headers,
            content=content,
        )

    text = resp.text
    try:
        data = resp.json()
    except Exception:
        data = text

    return {
        "status_code": resp.status_code,
        "ok": resp.is_success,
        "headers": dict(resp.headers),
        "body": data,
        "raw_body": text[:4000] if len(text) > 4000 else text,
    }


# ── GET /queues ───────────────────────────────────────────────────────────────

@router.get("/queues")
async def list_queues() -> Dict[str, Any]:
    return {
        "queues": [q.model_dump() for q in settings.mq.queues],
        "enabled": settings.mq.enabled,
    }


# ── POST /queues ──────────────────────────────────────────────────────────────

@router.post("/queues")
async def add_queue(req: QueueRequest) -> Dict[str, Any]:
    new_q = MQQueue(
        id=str(uuid.uuid4())[:8],
        **req.model_dump(),
    )
    settings.mq.queues.append(new_q)
    return {"added": new_q.model_dump(), "total": len(settings.mq.queues)}


# ── PUT /queues/{id} ──────────────────────────────────────────────────────────

@router.put("/queues/{queue_id}")
async def update_queue(queue_id: str, req: QueueRequest) -> Dict[str, Any]:
    for i, q in enumerate(settings.mq.queues):
        if q.id == queue_id:
            updated = MQQueue(id=queue_id, **req.model_dump())
            settings.mq.queues[i] = updated
            return {"updated": updated.model_dump()}
    raise HTTPException(status_code=404, detail=f"Queue '{queue_id}' nicht gefunden")


# ── DELETE /queues/{id} ───────────────────────────────────────────────────────

@router.delete("/queues/{queue_id}")
async def delete_queue(queue_id: str) -> Dict[str, Any]:
    before = len(settings.mq.queues)
    settings.mq.queues = [q for q in settings.mq.queues if q.id != queue_id]
    if len(settings.mq.queues) == before:
        raise HTTPException(status_code=404, detail=f"Queue '{queue_id}' nicht gefunden")
    return {"deleted": queue_id, "remaining": len(settings.mq.queues)}


# ── POST /queues/{id}/test ────────────────────────────────────────────────────

@router.post("/queues/{queue_id}/test")
async def test_queue(queue_id: str) -> Dict[str, Any]:
    queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
    if not queue:
        raise HTTPException(status_code=404, detail=f"Queue '{queue_id}' nicht gefunden")

    try:
        result = await _http_request(queue, queue.method)
        return {
            "success": result["ok"],
            "status_code": result["status_code"],
            "body_preview": str(result["raw_body"])[:500],
            "error": None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── POST /queues/{id}/get ─────────────────────────────────────────────────────

@router.post("/queues/{queue_id}/get")
async def get_message(queue_id: str, extra_headers: Dict[str, str] = {}) -> Dict[str, Any]:
    """Liest eine Nachricht von der Queue (HTTP GET)."""
    queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
    if not queue:
        raise HTTPException(status_code=404, detail=f"Queue '{queue_id}' nicht gefunden")

    try:
        result = await _http_request(queue, "GET", extra_headers=extra_headers)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── POST /queues/{id}/put ─────────────────────────────────────────────────────

@router.post("/queues/{queue_id}/put")
async def put_message(queue_id: str, req: PutMessageRequest) -> Dict[str, Any]:
    """Spielt eine Nachricht in die Queue ein."""
    queue = next((q for q in settings.mq.queues if q.id == queue_id), None)
    if not queue:
        raise HTTPException(status_code=404, detail=f"Queue '{queue_id}' nicht gefunden")

    # Body bestimmen: explizit > Template + Params > leer
    body = req.body
    if not body and queue.body_template:
        body = queue.body_template
        for k, v in req.params.items():
            body = body.replace(f"{{{{{k}}}}}", str(v))

    method = queue.method if queue.method in ("POST", "PUT", "PATCH") else "POST"

    try:
        result = await _http_request(queue, method, body=body, extra_headers=req.extra_headers)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
