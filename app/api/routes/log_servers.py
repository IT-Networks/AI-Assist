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

  POST   /api/log-servers/download           – Logs von allen Servern einer Stage herunterladen
  POST   /api/log-servers/find-server        – Passenden Server sequentiell suchen (Early-Exit)
  POST   /api/log-servers/read-window        – Logs im Zeitfenster lesen (Early-Exit je Server)

  PUT    /api/log-servers/config             – Globale Log-Server-Config (credential_ref, default_tail)
"""

import uuid
import re
import httpx
from datetime import datetime
from typing import Any, Dict, List, Optional
from html.parser import HTMLParser

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
    verify_ssl: bool = True


class LogServerConfigRequest(BaseModel):
    credential_ref: str = ""
    default_tail: int = 4


class DownloadRequest(BaseModel):
    stage_id: str
    tail: Optional[int] = None  # 0-4, None = default_tail


class FindServerRequest(BaseModel):
    """Sucht den passenden Server sequentiell – bricht ab sobald ein guter Treffer gefunden."""
    stage_id: str
    reference_time: Optional[str] = None  # ISO-8601, leer = jetzt
    search_term: Optional[str] = None
    min_score: float = 60.0
    tail: Optional[int] = None


class ReadWindowRequest(BaseModel):
    """Liest Logs in einem Zeitfenster – probiert Server sequentiell, bricht bei Treffer ab."""
    stage_id: str
    time_start: str                    # ISO-8601 – Beginn des Zeitfensters
    time_end: str                      # ISO-8601 – Ende des Zeitfensters
    search_term: Optional[str] = None
    tail: Optional[int] = None
    min_matching_lines: int = 1


# ── Global Config ────────────────────────────────────────────────────────────

@router.put("/config")
async def update_config(req: LogServerConfigRequest) -> Dict[str, Any]:
    """Globale Log-Server-Konfiguration aktualisieren (credential_ref, default_tail)."""
    settings.log_servers.credential_ref = req.credential_ref
    settings.log_servers.default_tail = max(0, min(4, req.default_tail))
    return {
        "credential_ref": settings.log_servers.credential_ref,
        "default_tail": settings.log_servers.default_tail,
    }


# ── Stage Management ──────────────────────────────────────────────────────────

@router.get("/stages")
async def list_stages() -> Dict[str, Any]:
    return {
        "stages": [s.model_dump() for s in settings.log_servers.stages],
        "enabled": settings.log_servers.enabled,
        "credential_ref": settings.log_servers.credential_ref,
        "default_tail": settings.log_servers.default_tail,
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


# ── Login + Log-Fetch Helpers ────────────────────────────────────────────────

def _get_credentials() -> tuple:
    """Holt username/password aus der credential_ref der Log-Server-Config.
    Wirft ValueError statt HTTPException – sicher für Agent- und API-Kontext."""
    ref = settings.log_servers.credential_ref
    if not ref:
        raise ValueError("Kein credential_ref in Log-Server-Config gesetzt")
    cred = settings.credentials.get(ref)
    if not cred:
        raise ValueError(f"Credential '{ref}' nicht gefunden")
    if cred.type != "basic":
        raise ValueError(f"Credential '{ref}' muss Typ 'basic' sein (ist '{cred.type}')")
    return cred.username, cred.password


class _LogLinkParser(HTMLParser):
    """Parst die log.jsp-Seite und extrahiert den href des Links mit Text 'ospe_ope.log'."""
    def __init__(self):
        super().__init__()
        self._current_href: Optional[str] = None
        self._in_a = False
        self.file_id: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_a = True
            self._current_href = dict(attrs).get("href", "")

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_a = False
            self._current_href = None

    def handle_data(self, data):
        if self._in_a and "ospe_ope.log" in data.strip() and self._current_href:
            self.file_id = self._current_href


async def _fetch_server_logs(server: LogServer, tail: int) -> Optional[str]:
    """
    Kompletter Login-Flow für einen Log-Server:
    1. POST /login mit form-urlencoded credentials → JSESSIONID-Cookie erhalten
    2. GET /jsp/ospe/debug/log.jsp mit Cookie → fileId aus href 'ospe_ope.log' parsen
    3. GET /jsp/ospe/debug/log.jsp?file={fileId}&tail=tail{N} mit Cookie → Log-Inhalt
    """
    base = server.url.rstrip("/")

    try:
        username, password = _get_credentials()
        jar = httpx.Cookies()
        async with httpx.AsyncClient(
            verify=server.verify_ssl,
            timeout=30,
            follow_redirects=True,
            cookies=jar,
        ) as client:
            # 1. Login – JSESSIONID-Cookie wird automatisch in jar gespeichert
            login_resp = await client.post(
                f"{base}/login",
                data={"username": username, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            # Login kann 200 oder 302→200 sein, beides OK
            if login_resp.status_code >= 400:
                return None

            # Sicherstellen dass JSESSIONID vorhanden ist
            if not any("JSESSIONID" in name.upper() for name in client.cookies.keys()):
                # Fallback: Cookies aus allen Responses der Redirect-Kette prüfen
                for resp in login_resp.history + [login_resp]:
                    for name, value in resp.cookies.items():
                        jar.set(name, value)

            # 2. Log-Seite laden und fileId parsen
            log_page_resp = await client.get(f"{base}/jsp/ospe/debug/log.jsp")
            if log_page_resp.status_code >= 400:
                return None

            parser = _LogLinkParser()
            parser.feed(log_page_resp.text)
            if not parser.file_id:
                return None

            # 3. Log-Datei mit fileId und tail downloaden
            log_resp = await client.get(
                f"{base}/jsp/ospe/debug/log.jsp",
                params={"file": parser.file_id, "tail": f"tail{tail}"},
            )
            if log_resp.status_code >= 400:
                return None

            return log_resp.text
    except Exception:
        return None


# ── Log Download (alle Server einer Stage) ───────────────────────────────────

@router.post("/download")
async def download_logs(req: DownloadRequest) -> Dict[str, Any]:
    """Lädt Logs von ALLEN erreichbaren Servern einer Stage herunter."""
    stage = _get_stage(req.stage_id)
    if not stage.servers:
        raise HTTPException(status_code=400, detail="Stage hat keine Server konfiguriert")

    try:
        _get_credentials()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    tail = req.tail if req.tail is not None else settings.log_servers.default_tail
    tail = max(0, min(4, tail))

    results = []
    for server in stage.servers:
        content = await _fetch_server_logs(server, tail)
        if content is not None:
            lines = content.splitlines()
            results.append({
                "server_id": server.id,
                "server": server.name,
                "success": True,
                "lines_count": len(lines),
                "content": content,
                "lines": lines,
            })
        else:
            results.append({
                "server_id": server.id,
                "server": server.name,
                "success": False,
                "error": "Login oder Download fehlgeschlagen",
            })

    successful = [r for r in results if r["success"]]
    return {
        "stage": stage.name,
        "tail": tail,
        "servers_total": len(stage.servers),
        "servers_successful": len(successful),
        "servers_failed": len(results) - len(successful),
        "results": results,
    }


# ── Timestamp Helpers ─────────────────────────────────────────────────────────

_TS_PATTERNS = [
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
    """Extrahiert alle Zeitstempel aus einem Log-Text."""
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
            continue

        if time_start <= effective_ts <= time_end:
            result.append(line)

    return result


# ── Sequential Find-Server (Early-Exit) ───────────────────────────────────────

@router.post("/find-server")
async def find_server(req: FindServerRequest) -> Dict[str, Any]:
    """
    Sucht den passenden Log-Server SEQUENTIELL – bricht sofort ab wenn
    ein Server mit Score ≥ min_score gefunden wird.
    """
    stage = _get_stage(req.stage_id)
    if not stage.servers:
        raise HTTPException(status_code=400, detail="Stage hat keine Server konfiguriert")

    if req.reference_time:
        try:
            ref_time = datetime.fromisoformat(req.reference_time.replace("Z", "+00:00"))
            ref_time = ref_time.replace(tzinfo=None)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Ungültiges Zeitformat: {req.reference_time}")
    else:
        ref_time = datetime.now()

    tail = req.tail if req.tail is not None else settings.log_servers.default_tail
    tail = max(0, min(4, tail))
    tried = []

    for server in stage.servers:
        content = await _fetch_server_logs(server, tail)

        if content is None:
            tried.append({"server_id": server.id, "server": server.name, "score": -1, "error": "Login/Download fehlgeschlagen"})
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
    """
    stage = _get_stage(req.stage_id)
    if not stage.servers:
        raise HTTPException(status_code=400, detail="Stage hat keine Server konfiguriert")

    try:
        t_start = datetime.fromisoformat(req.time_start.replace("Z", "+00:00")).replace(tzinfo=None)
        t_end   = datetime.fromisoformat(req.time_end.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültiges Zeitformat (ISO-8601 erwartet)")

    if t_end < t_start:
        raise HTTPException(status_code=400, detail="time_end muss nach time_start liegen")

    tail = req.tail if req.tail is not None else settings.log_servers.default_tail
    tail = max(0, min(4, tail))
    tried = []

    for server in stage.servers:
        content = await _fetch_server_logs(server, tail)
        if content is None:
            tried.append({"server_id": server.id, "server": server.name, "result": "login_or_download_failed"})
            continue

        all_lines = content.splitlines()
        window_lines = _filter_lines_to_window(all_lines, t_start, t_end)

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