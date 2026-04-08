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




# ── Global Config ────────────────────────────────────────────────────────────

@router.put("/config")
async def update_config(req: LogServerConfigRequest) -> Dict[str, Any]:
    """Globale Log-Server-Konfiguration aktualisieren (credential_ref, default_tail)."""
    settings.log_servers.credential_ref = req.credential_ref
    settings.log_servers.default_tail = max(0, min(4, req.default_tail))

    # Config persistieren
    from app.api.routes.settings import _save_config
    _save_config()

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
        self.log_href: Optional[str] = None

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
            self.log_href = self._current_href


def _strip_html(text: str) -> str:
    """Extrahiert Log-Einträge aus der HTML-Log-Seite.

    Die log.jsp liefert eine HTML-Seite mit einem <p class="logfilecontent">
    Element. Darin sind Log-Einträge durch <br> getrennt.
    """
    if not text or "<" not in text:
        return text

    # <p class="logfilecontent"> Inhalt extrahieren
    p_match = re.search(
        r'<p[^>]*class=["\']logfilecontent["\'][^>]*>(.*?)</p>',
        text, re.DOTALL | re.IGNORECASE,
    )
    if p_match:
        content = p_match.group(1)
    else:
        # Fallback: ganzen Body nehmen wenn kein logfilecontent gefunden
        body_match = re.search(r"<body[^>]*>(.*?)</body>", text, re.DOTALL | re.IGNORECASE)
        content = body_match.group(1) if body_match else text

    # <br> und <br/> als Zeilenumbrüche behandeln
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    # Restliche HTML-Tags entfernen
    content = re.sub(r"<[^>]+>", "", content)
    # HTML-Entities decodieren
    content = content.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ").replace("&quot;", '"')
    return content.strip()


class _FetchResult:
    """Ergebnis von _fetch_server_logs: entweder content oder error."""
    def __init__(self, content: Optional[str] = None, error: Optional[str] = None):
        self.content = content
        self.error = error
        self.success = content is not None


async def _fetch_server_logs(
    server: LogServer,
    tail: int,
    credentials: Optional[tuple] = None,
) -> _FetchResult:
    """
    Kompletter Login-Flow für einen Log-Server:
    1. POST /login mit form-urlencoded credentials → JSESSIONID-Cookie erhalten
    2. GET /jsp/ospe/debug/log.jsp mit Cookie → fileId aus href 'ospe_ope.log' parsen
    3. GET /jsp/ospe/debug/{href}&tail=tail{N} mit Cookie → Log-Inhalt

    credentials: (username, password) – wenn None, wird aus Config geladen.
    """
    base = server.effective_url.rstrip("/")

    if credentials:
        username, password = credentials
    else:
        try:
            username, password = _get_credentials()
        except ValueError as e:
            return _FetchResult(error=f"Credentials: {e}")

    try:
        jar = httpx.Cookies()
        async with httpx.AsyncClient(
            verify=server.verify_ssl,
            timeout=15,
            follow_redirects=True,
            cookies=jar,
        ) as client:
            # 1. Login – JSESSIONID-Cookie wird automatisch in jar gespeichert
            login_resp = await client.post(
                f"{base}/login",
                data={"username": username, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if login_resp.status_code >= 400:
                return _FetchResult(error=f"Login fehlgeschlagen: HTTP {login_resp.status_code} auf {base}/login")

            # Sicherstellen dass JSESSIONID vorhanden ist
            all_cookies = dict(client.cookies)
            if not any("JSESSIONID" in name.upper() for name in all_cookies):
                # Fallback: Cookies aus allen Responses der Redirect-Kette prüfen
                for resp in login_resp.history + [login_resp]:
                    for name, value in resp.cookies.items():
                        jar.set(name, value)
                        all_cookies[name] = value

            if not any("JSESSIONID" in name.upper() for name in all_cookies):
                return _FetchResult(error=f"Login OK aber kein JSESSIONID-Cookie erhalten. Cookies: {list(all_cookies.keys())}")

            # 2. Log-Seite laden und fileId parsen
            log_page_resp = await client.get(f"{base}/jsp/ospe/debug/log.jsp")
            if log_page_resp.status_code >= 400:
                return _FetchResult(error=f"Log-Seite nicht erreichbar: HTTP {log_page_resp.status_code} auf {base}/jsp/ospe/debug/log.jsp")

            parser = _LogLinkParser()
            parser.feed(log_page_resp.text)
            if not parser.log_href:
                # Ersten 500 Zeichen der Seite für Diagnose mitgeben
                preview = log_page_resp.text[:500].replace("\n", " ")
                return _FetchResult(error=f"fileId nicht gefunden: kein Link mit Text 'ospe_ope.log' auf log.jsp. Seiten-Preview: {preview}")

            # 3. Log-Datei downloaden – href ist relativ zu /jsp/ospe/debug/
            log_href = parser.log_href
            if log_href.startswith("http"):
                download_url = log_href
            else:
                download_url = f"{base}/jsp/ospe/debug/{log_href.lstrip('/')}"
            sep = "&" if "?" in download_url else "?"
            download_url = f"{download_url}{sep}tail=tail{tail}"

            log_resp = await client.get(download_url)
            if log_resp.status_code >= 400:
                return _FetchResult(error=f"Log-Download fehlgeschlagen: HTTP {log_resp.status_code} für {download_url}")

            # HTML-Response zu Plaintext konvertieren (log.jsp kann HTML-Wrapper liefern)
            content = _strip_html(log_resp.text)
            return _FetchResult(content=content)
    except httpx.ConnectError as e:
        return _FetchResult(error=f"Verbindung fehlgeschlagen: {base} – {e}")
    except httpx.TimeoutException:
        return _FetchResult(error=f"Timeout nach 30s: {base}")
    except Exception as e:
        return _FetchResult(error=f"Unerwarteter Fehler: {type(e).__name__}: {e}")


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
        try:
            result = await _fetch_server_logs(server, tail)
            if result.success:
                lines = result.content.splitlines()
                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": True,
                    "lines_count": len(lines),
                    "content": result.content,
                    "lines": lines,
                })
            else:
                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": False,
                    "error": result.error,
                })
        except Exception as e:
            results.append({
                "server_id": server.id,
                "server": server.name,
                "success": False,
                "error": f"Unerwarteter Fehler: {type(e).__name__}: {e}",
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


