"""
Log-Server API – Stage/Server-Konfiguration und Log-Download.

Routes:
  GET    /api/log-servers/stages             – Stages auflisten
  POST   /api/log-servers/stages             – Stage hinzufügen
  PUT    /api/log-servers/stages/{id}        – Stage aktualisieren
  DELETE /api/log-servers/stages/{id}        – Stage löschen

  POST   /api/log-servers/stages/{stage_id}/servers        – Server hinzufügen
  PUT    /api/log-servers/stages/{stage_id}/servers/{id}   – Server aktualisieren
  DELETE /api/log-servers/stages/{stage_id}/servers/{id}   – Server löschen

  POST   /api/log-servers/download           – Logs von einem Server herunterladen
  POST   /api/log-servers/find-server        – Passenden Server anhand Zeitstempel finden
"""

import uuid
import re
import httpx
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings, LogStage, LogServer

router = APIRouter(prefix="/api/log-servers", tags=["log_servers"])


# ── Request Models ─────────────────────────────────────────────────────────────

class StageRequest(BaseModel):
    name: str


class ServerRequest(BaseModel):
    name: str
    url: str
    description: str = ""
    headers: Dict[str, str] = {}
    verify_ssl: bool = True


class DownloadRequest(BaseModel):
    stage_id: str
    server_id: str
    tail_lines: Optional[int] = None   # None = Default aus Config
    extra_headers: Dict[str, str] = {}


class FindServerRequest(BaseModel):
    """Sucht den passenden Server anhand eines Zeitstempels und Inhalts."""
    stage_id: str
    reference_time: str            # ISO-8601, z.B. aus Test-Tool-Aufruf
    search_term: Optional[str] = None  # Optionaler Log-Inhalt zur Verifikation


# ── Stage Management ──────────────────────────────────────────────────────────

@router.get("/stages")
async def list_stages() -> Dict[str, Any]:
    return {
        "stages": [s.model_dump() for s in settings.log_servers.stages],
        "enabled": settings.log_servers.enabled,
        "default_tail_lines": settings.log_servers.default_tail_lines,
    }


@router.post("/stages")
async def add_stage(req: StageRequest) -> Dict[str, Any]:
    stage = LogStage(id=str(uuid.uuid4())[:8], name=req.name)
    settings.log_servers.stages.append(stage)
    return {"added": stage.model_dump()}


@router.put("/stages/{stage_id}")
async def update_stage(stage_id: str, req: StageRequest) -> Dict[str, Any]:
    for s in settings.log_servers.stages:
        if s.id == stage_id:
            s.name = req.name
            return {"updated": s.model_dump()}
    raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' nicht gefunden")


@router.delete("/stages/{stage_id}")
async def delete_stage(stage_id: str) -> Dict[str, Any]:
    before = len(settings.log_servers.stages)
    settings.log_servers.stages = [s for s in settings.log_servers.stages if s.id != stage_id]
    if len(settings.log_servers.stages) == before:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' nicht gefunden")
    return {"deleted": stage_id}


# ── Server Management ─────────────────────────────────────────────────────────

def _get_stage(stage_id: str) -> LogStage:
    stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
    if not stage:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' nicht gefunden")
    return stage


@router.post("/stages/{stage_id}/servers")
async def add_server(stage_id: str, req: ServerRequest) -> Dict[str, Any]:
    stage = _get_stage(stage_id)
    srv = LogServer(
        id=str(uuid.uuid4())[:8],
        name=req.name,
        url=req.url,
        description=req.description,
        headers=req.headers,
        verify_ssl=req.verify_ssl,
    )
    stage.servers.append(srv)
    return {"added": srv.model_dump()}


@router.put("/stages/{stage_id}/servers/{server_id}")
async def update_server(stage_id: str, server_id: str, req: ServerRequest) -> Dict[str, Any]:
    stage = _get_stage(stage_id)
    for i, srv in enumerate(stage.servers):
        if srv.id == server_id:
            stage.servers[i] = LogServer(
                id=server_id,
                name=req.name,
                url=req.url,
                description=req.description,
                headers=req.headers,
                verify_ssl=req.verify_ssl,
            )
            return {"updated": stage.servers[i].model_dump()}
    raise HTTPException(status_code=404, detail=f"Server '{server_id}' nicht gefunden")


@router.delete("/stages/{stage_id}/servers/{server_id}")
async def delete_server(stage_id: str, server_id: str) -> Dict[str, Any]:
    stage = _get_stage(stage_id)
    before = len(stage.servers)
    stage.servers = [s for s in stage.servers if s.id != server_id]
    if len(stage.servers) == before:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' nicht gefunden")
    return {"deleted": server_id}


# ── Log Download ──────────────────────────────────────────────────────────────

@router.post("/download")
async def download_logs(req: DownloadRequest) -> Dict[str, Any]:
    """Lädt Logs von einem konfigurierten Server herunter."""
    stage = _get_stage(req.stage_id)
    server = next((s for s in stage.servers if s.id == req.server_id), None)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{req.server_id}' nicht gefunden")

    tail = req.tail_lines or settings.log_servers.default_tail_lines
    headers = dict(server.headers)
    headers.update(req.extra_headers)

    # URL ggf. mit tail_lines als Query-Parameter erweitern
    url = server.url
    sep = "&" if "?" in url else "?"
    url_with_tail = f"{url}{sep}tail={tail}"

    try:
        async with httpx.AsyncClient(verify=server.verify_ssl, timeout=60) as client:
            resp = await client.get(url_with_tail, headers=headers)
        resp.raise_for_status()
        content = resp.text
        lines = content.splitlines()

        return {
            "success": True,
            "server": server.name,
            "stage": stage.name,
            "url": url_with_tail,
            "lines_count": len(lines),
            "content": content,
            "lines": lines[-tail:] if len(lines) > tail else lines,
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP-Fehler: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Smart Server Finder ───────────────────────────────────────────────────────

# Typische Log-Zeitstempel-Formate
_TS_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",  # ISO-8601 / Standard
    r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}",    # DD.MM.YYYY HH:MM:SS
    r"\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}",       # Apache-Style
]


def _extract_timestamps(text: str) -> List[datetime]:
    """Extrahiert Zeitstempel aus Log-Text."""
    results = []
    for pattern in _TS_PATTERNS:
        for m in re.finditer(pattern, text):
            raw = m.group()
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
                try:
                    results.append(datetime.strptime(raw, fmt))
                    break
                except ValueError:
                    pass
    return results


@router.post("/find-server")
async def find_server(req: FindServerRequest) -> Dict[str, Any]:
    """
    Versucht den passenden Log-Server für eine Stage zu finden,
    indem Logs heruntergeladen und der Zeitstempel verglichen wird.
    """
    stage = _get_stage(req.stage_id)
    if not stage.servers:
        raise HTTPException(status_code=400, detail="Stage hat keine Server konfiguriert")

    try:
        ref_time = datetime.fromisoformat(req.reference_time.replace("Z", "+00:00"))
        ref_time = ref_time.replace(tzinfo=None)  # naive für Vergleich
    except Exception:
        raise HTTPException(status_code=400, detail=f"Ungültiges Zeitformat: {req.reference_time}")

    results = []
    for server in stage.servers:
        try:
            headers = dict(server.headers)
            tail = settings.log_servers.default_tail_lines
            url = f"{server.url}{'&' if '?' in server.url else '?'}tail={tail}"
            async with httpx.AsyncClient(verify=server.verify_ssl, timeout=30) as client:
                resp = await client.get(url, headers=headers)
            if not resp.is_success:
                results.append({"server": server.name, "score": -1, "error": f"HTTP {resp.status_code}"})
                continue

            content = resp.text
            timestamps = _extract_timestamps(content)
            if not timestamps:
                results.append({"server": server.name, "score": 0, "note": "Keine Zeitstempel gefunden"})
                continue

            # Nächsten Zeitstempel zur Referenzzeit suchen
            closest = min(timestamps, key=lambda t: abs((t - ref_time).total_seconds()))
            delta_s = abs((closest - ref_time).total_seconds())

            # Optional: Suchbegriff prüfen
            content_match = req.search_term.lower() in content.lower() if req.search_term else True

            score = max(0, 100 - delta_s / 60) * (1.5 if content_match else 1.0)
            results.append({
                "server_id": server.id,
                "server": server.name,
                "score": round(score, 1),
                "closest_timestamp": closest.isoformat(),
                "delta_seconds": round(delta_s, 1),
                "content_match": content_match,
            })
        except Exception as e:
            results.append({"server": server.name, "score": -1, "error": str(e)})

    results.sort(key=lambda r: r.get("score", -1), reverse=True)
    best = results[0] if results else None

    return {
        "stage": stage.name,
        "reference_time": req.reference_time,
        "results": results,
        "best_match": best,
    }
