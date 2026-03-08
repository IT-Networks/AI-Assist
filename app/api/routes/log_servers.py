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
  POST   /api/log-servers/find-server        – Passenden Server sequentiell suchen (Early-Exit)
  POST   /api/log-servers/read-window        – Logs im Zeitfenster lesen (Early-Exit je Server)
"""

import uuid
import re
import httpx
from datetime import datetime, timedelta
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
    tail_lines: Optional[int] = None
    extra_headers: Dict[str, str] = {}


class FindServerRequest(BaseModel):
    """Sucht den passenden Server sequentiell – bricht ab sobald ein guter Treffer gefunden."""
    stage_id: str
    reference_time: str                # ISO-8601
    search_term: Optional[str] = None  # Optionaler Log-Inhalt zur Verifikation
    # Score ≥ min_score → sofortiger Abbruch (Early-Exit)
    min_score: float = 60.0
    tail_lines: Optional[int] = None


class ReadWindowRequest(BaseModel):
    """Liest Logs in einem Zeitfenster – probiert Server sequentiell, bricht bei Treffer ab."""
    stage_id: str
    time_start: str                    # ISO-8601 – Beginn des Zeitfensters
    time_end: str                      # ISO-8601 – Ende des Zeitfensters
    search_term: Optional[str] = None  # Muss im gefilterten Abschnitt vorkommen
    tail_lines: Optional[int] = None   # Anzahl Zeilen vom Server laden
    # Mindest-Anzahl passender Zeilen damit Server als Treffer gilt
    min_matching_lines: int = 1


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


# ── Timestamp Helpers ─────────────────────────────────────────────────────────

_TS_PATTERNS = [
    # (regex, strptime-Format)
    (r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S"),
    (r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "%Y-%m-%d %H:%M:%S"),
    (r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}",  "%d.%m.%Y %H:%M:%S"),
]


def _parse_line_timestamp(line: str) -> Optional[datetime]:
    """Extrahiert den ersten Zeitstempel aus einer Log-Zeile."""
    for pattern, fmt in _TS_PATTERNS:
        m = re.search(pattern, line)
        if m:
            try:
                raw = m.group().replace("T", " ")
                return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
            try:
                return datetime.strptime(m.group(), fmt)
            except ValueError:
                pass
    return None


def _extract_timestamps(text: str) -> List[datetime]:
    """Extrahiert alle Zeitstempel aus einem Log-Text (für find-server)."""
    results = []
    for line in text.splitlines():
        ts = _parse_line_timestamp(line)
        if ts:
            results.append(ts)
    return results


def _filter_lines_to_window(
    lines: List[str],
    time_start: datetime,
    time_end: datetime,
) -> List[str]:
    """
    Filtert Log-Zeilen auf ein Zeitfenster.
    Zeilen ohne Zeitstempel werden der zuletzt gesehenen Zeit zugeordnet.
    """
    result = []
    last_ts: Optional[datetime] = None

    for line in lines:
        ts = _parse_line_timestamp(line)
        if ts:
            last_ts = ts

        effective_ts = last_ts
        if effective_ts is None:
            # Noch kein Zeitstempel gesehen – Zeile überspringen
            continue

        if time_start <= effective_ts <= time_end:
            result.append(line)

    return result


async def _fetch_server_logs(server: LogServer, tail: int) -> Optional[str]:
    """Lädt Log-Inhalt von einem Server. Gibt None bei Fehler zurück."""
    try:
        headers = dict(server.headers)
        url = server.url
        sep = "&" if "?" in url else "?"
        url_with_tail = f"{url}{sep}tail={tail}"
        async with httpx.AsyncClient(verify=server.verify_ssl, timeout=30) as client:
            resp = await client.get(url_with_tail, headers=headers)
        if resp.is_success:
            return resp.text
    except Exception:
        pass
    return None


# ── Sequential Find-Server (Early-Exit) ───────────────────────────────────────

@router.post("/find-server")
async def find_server(req: FindServerRequest) -> Dict[str, Any]:
    """
    Sucht den passenden Log-Server SEQUENTIELL – bricht sofort ab wenn
    ein Server mit Score ≥ min_score gefunden wird.
    Server werden in konfigurierter Reihenfolge geprüft.
    """
    stage = _get_stage(req.stage_id)
    if not stage.servers:
        raise HTTPException(status_code=400, detail="Stage hat keine Server konfiguriert")

    try:
        ref_time = datetime.fromisoformat(req.reference_time.replace("Z", "+00:00"))
        ref_time = ref_time.replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Ungültiges Zeitformat: {req.reference_time}")

    tail = req.tail_lines or settings.log_servers.default_tail_lines
    tried = []

    for server in stage.servers:
        content = await _fetch_server_logs(server, tail)

        if content is None:
            tried.append({"server_id": server.id, "server": server.name, "score": -1, "error": "Download fehlgeschlagen"})
            continue

        timestamps = _extract_timestamps(content)
        if not timestamps:
            tried.append({"server_id": server.id, "server": server.name, "score": 0, "note": "Keine Zeitstempel"})
            continue

        closest = min(timestamps, key=lambda t: abs((t - ref_time).total_seconds()))
        delta_s = abs((closest - ref_time).total_seconds())
        content_match = req.search_term.lower() in content.lower() if req.search_term else True
        score = max(0, 100 - delta_s / 60) * (1.5 if content_match else 1.0)
        score = round(score, 1)

        entry = {
            "server_id": server.id,
            "server": server.name,
            "score": score,
            "closest_timestamp": closest.isoformat(),
            "delta_seconds": round(delta_s, 1),
            "content_match": content_match,
        }
        tried.append(entry)

        # Early-Exit: Guter Treffer gefunden
        if score >= req.min_score:
            return {
                "stage": stage.name,
                "reference_time": req.reference_time,
                "found": True,
                "early_exit": True,
                "best_match": entry,
                "servers_tried": tried,
                "servers_skipped": len(stage.servers) - len(tried),
            }

    # Kein Early-Exit – bestes Ergebnis aus allen Versuchen
    tried_valid = [r for r in tried if r.get("score", -1) >= 0]
    best = max(tried_valid, key=lambda r: r["score"]) if tried_valid else None

    return {
        "stage": stage.name,
        "reference_time": req.reference_time,
        "found": bool(best),
        "early_exit": False,
        "best_match": best,
        "servers_tried": tried,
        "servers_skipped": 0,
    }


# ── Read Window (Zeitfenster-Logs, Sequential + Early-Exit) ───────────────────

@router.post("/read-window")
async def read_window(req: ReadWindowRequest) -> Dict[str, Any]:
    """
    Lädt Logs von jedem Server der Stage SEQUENTIELL und filtert auf das
    angegebene Zeitfenster. Bricht ab, sobald ein Server passende Zeilen liefert.

    Rückgabe: Gefilterte Log-Zeilen des passenden Servers + Metadaten.
    """
    stage = _get_stage(req.stage_id)
    if not stage.servers:
        raise HTTPException(status_code=400, detail="Stage hat keine Server konfiguriert")

    # Zeitfenster parsen
    try:
        t_start = datetime.fromisoformat(req.time_start.replace("Z", "+00:00")).replace(tzinfo=None)
        t_end   = datetime.fromisoformat(req.time_end.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültiges Zeitformat (ISO-8601 erwartet)")

    if t_end < t_start:
        raise HTTPException(status_code=400, detail="time_end muss nach time_start liegen")

    tail = req.tail_lines or settings.log_servers.default_tail_lines
    tried = []

    for server in stage.servers:
        content = await _fetch_server_logs(server, tail)
        if content is None:
            tried.append({"server_id": server.id, "server": server.name, "result": "download_failed"})
            continue

        all_lines = content.splitlines()
        window_lines = _filter_lines_to_window(all_lines, t_start, t_end)

        # Suchbegriff innerhalb des Zeitfensters prüfen
        if req.search_term:
            term_lower = req.search_term.lower()
            matching = [l for l in window_lines if term_lower in l.lower()]
        else:
            matching = window_lines

        tried.append({
            "server_id": server.id,
            "server": server.name,
            "total_lines": len(all_lines),
            "window_lines": len(window_lines),
            "matching_lines": len(matching),
        })

        # Early-Exit: Genug passende Zeilen gefunden
        if len(matching) >= req.min_matching_lines:
            return {
                "stage": stage.name,
                "server_id": server.id,
                "server": server.name,
                "time_start": req.time_start,
                "time_end": req.time_end,
                "found": True,
                "early_exit": True,
                "window_lines": window_lines,
                "matching_lines": matching,
                "total_downloaded": len(all_lines),
                "servers_tried": tried,
                "servers_skipped": len(stage.servers) - len(tried),
                "content": "\n".join(window_lines),
            }

    # Kein Treffer – bestes Ergebnis zurückgeben (Server mit meisten Fenster-Zeilen)
    best_try = max(tried, key=lambda t: t.get("window_lines", 0)) if tried else None

    return {
        "stage": stage.name,
        "time_start": req.time_start,
        "time_end": req.time_end,
        "found": False,
        "early_exit": False,
        "window_lines": [],
        "matching_lines": [],
        "servers_tried": tried,
        "servers_skipped": 0,
        "best_partial": best_try,
    }
