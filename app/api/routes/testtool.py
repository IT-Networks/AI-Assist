"""
Test-Tool API – Services per HTTP aufrufen und lokale Services ausführen.

Routes:
  GET    /api/testtool/stages               – Stages auflisten
  POST   /api/testtool/stages               – Stage hinzufügen
  PUT    /api/testtool/stages/{id}          – Stage aktualisieren
  DELETE /api/testtool/stages/{id}          – Stage löschen
  PUT    /api/testtool/stages/active        – Aktive Stage setzen

  GET    /api/testtool/services             – Services auflisten
  POST   /api/testtool/services             – Service hinzufügen
  PUT    /api/testtool/services/{id}        – Service aktualisieren
  DELETE /api/testtool/services/{id}        – Service löschen

  POST   /api/testtool/execute/{svc_id}     – Service per HTTP aufrufen
  POST   /api/testtool/local/{svc_id}       – Service lokal ausführen (Subprocess)
"""

import asyncio
import json
import uuid
import httpx
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings, TestStage, TestStageUrl, TestService, TestServiceParam

router = APIRouter(prefix="/api/testtool", tags=["testtool"])


# ── Request Models ─────────────────────────────────────────────────────────────

class StageRequest(BaseModel):
    name: str
    urls: List[Dict[str, str]] = []  # [{url, description}]


class ServiceRequest(BaseModel):
    name: str
    description: str = ""
    endpoint: str
    method: str = "POST"
    content_type: str = "application/json"
    parameters: List[Dict[str, Any]] = []
    headers: Dict[str, str] = {}
    local_script: str = ""
    local_interpreter: str = ""


class ExecuteRequest(BaseModel):
    params: Dict[str, Any] = {}        # Eingabe-Parameter
    stage_url: Optional[str] = None    # Überschreibt aktive Stage-URL
    extra_headers: Dict[str, str] = {}
    timeout_seconds: int = 60
    use_local_wlp: bool = False        # Weiterleitung an lokalen WLP-Server


class LocalWLPRequest(BaseModel):
    url: str                           # Lokale WLP-Basis-URL (z.B. http://localhost:9080)


# ── Stage Management ──────────────────────────────────────────────────────────

@router.get("/stages")
async def list_stages() -> Dict[str, Any]:
    return {
        "stages": [s.model_dump() for s in settings.test_tool.stages],
        "active_stage": settings.test_tool.active_stage,
    }


@router.post("/stages")
async def add_stage(req: StageRequest) -> Dict[str, Any]:
    urls = [TestStageUrl(**u) for u in req.urls]
    stage = TestStage(id=str(uuid.uuid4())[:8], name=req.name, urls=urls)
    settings.test_tool.stages.append(stage)
    if not settings.test_tool.active_stage:
        settings.test_tool.active_stage = stage.id
    return {"added": stage.model_dump()}


@router.put("/stages/active")
async def set_active_stage(stage_id: str) -> Dict[str, Any]:
    ids = [s.id for s in settings.test_tool.stages]
    if stage_id not in ids:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' nicht gefunden")
    settings.test_tool.active_stage = stage_id
    return {"active_stage": stage_id}


@router.put("/stages/{stage_id}")
async def update_stage(stage_id: str, req: StageRequest) -> Dict[str, Any]:
    for i, s in enumerate(settings.test_tool.stages):
        if s.id == stage_id:
            urls = [TestStageUrl(**u) for u in req.urls]
            settings.test_tool.stages[i] = TestStage(id=stage_id, name=req.name, urls=urls)
            return {"updated": settings.test_tool.stages[i].model_dump()}
    raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' nicht gefunden")


@router.delete("/stages/{stage_id}")
async def delete_stage(stage_id: str) -> Dict[str, Any]:
    before = len(settings.test_tool.stages)
    settings.test_tool.stages = [s for s in settings.test_tool.stages if s.id != stage_id]
    if len(settings.test_tool.stages) == before:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' nicht gefunden")
    if settings.test_tool.active_stage == stage_id:
        settings.test_tool.active_stage = settings.test_tool.stages[0].id if settings.test_tool.stages else ""
    return {"deleted": stage_id}


# ── Service Management ────────────────────────────────────────────────────────

@router.get("/services")
async def list_services() -> Dict[str, Any]:
    return {"services": [s.model_dump() for s in settings.test_tool.services]}


@router.post("/services")
async def add_service(req: ServiceRequest) -> Dict[str, Any]:
    from app.utils.path_validator import validate_path_within_base

    # local_script validieren wenn angegeben (Path-Traversal verhindern)
    if req.local_script:
        base_path = settings.java.get_active_path() or settings.wlp.repo_path
        if base_path:
            is_valid, _, error = validate_path_within_base(req.local_script, base_path)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"Ungültiger local_script: {error}")

    # local_interpreter auf Whitelist prüfen
    ALLOWED_INTERPRETERS = ("python", "python3", "bash", "sh", "java", "node")
    if req.local_interpreter and req.local_interpreter not in ALLOWED_INTERPRETERS:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger local_interpreter. Erlaubt: {', '.join(ALLOWED_INTERPRETERS)}"
        )

    params = [TestServiceParam(**p) for p in req.parameters]
    svc = TestService(
        id=str(uuid.uuid4())[:8],
        name=req.name,
        description=req.description,
        endpoint=req.endpoint,
        method=req.method,
        content_type=req.content_type,
        parameters=params,
        headers=req.headers,
        local_script=req.local_script,
        local_interpreter=req.local_interpreter,
    )
    settings.test_tool.services.append(svc)
    return {"added": svc.model_dump()}


@router.put("/services/{svc_id}")
async def update_service(svc_id: str, req: ServiceRequest) -> Dict[str, Any]:
    from app.utils.path_validator import validate_path_within_base

    # local_script validieren wenn angegeben (Path-Traversal verhindern)
    if req.local_script:
        base_path = settings.java.get_active_path() or settings.wlp.repo_path
        if base_path:
            is_valid, _, error = validate_path_within_base(req.local_script, base_path)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"Ungültiger local_script: {error}")

    # local_interpreter auf Whitelist prüfen
    ALLOWED_INTERPRETERS = ("python", "python3", "bash", "sh", "java", "node")
    if req.local_interpreter and req.local_interpreter not in ALLOWED_INTERPRETERS:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger local_interpreter. Erlaubt: {', '.join(ALLOWED_INTERPRETERS)}"
        )

    for i, s in enumerate(settings.test_tool.services):
        if s.id == svc_id:
            params = [TestServiceParam(**p) for p in req.parameters]
            settings.test_tool.services[i] = TestService(
                id=svc_id,
                name=req.name,
                description=req.description,
                endpoint=req.endpoint,
                method=req.method,
                content_type=req.content_type,
                parameters=params,
                headers=req.headers,
                local_script=req.local_script,
                local_interpreter=req.local_interpreter,
            )
            return {"updated": settings.test_tool.services[i].model_dump()}
    raise HTTPException(status_code=404, detail=f"Service '{svc_id}' nicht gefunden")


@router.delete("/services/{svc_id}")
async def delete_service(svc_id: str) -> Dict[str, Any]:
    before = len(settings.test_tool.services)
    settings.test_tool.services = [s for s in settings.test_tool.services if s.id != svc_id]
    if len(settings.test_tool.services) == before:
        raise HTTPException(status_code=404, detail=f"Service '{svc_id}' nicht gefunden")
    return {"deleted": svc_id}


# ── Execution ─────────────────────────────────────────────────────────────────

def _resolve_base_url(stage_url: Optional[str], use_local_wlp: bool = False) -> str:
    """Liefert die Basis-URL: lokaler WLP (wenn aktiviert), explizite URL oder aktive Stage."""
    if use_local_wlp:
        url = settings.test_tool.local_wlp_url
        if not url:
            raise HTTPException(status_code=400, detail="Kein lokaler WLP-Server konfiguriert (local_wlp_url fehlt)")
        return url.rstrip("/")
    if stage_url:
        return stage_url.rstrip("/")
    stage = next((s for s in settings.test_tool.stages if s.id == settings.test_tool.active_stage), None)
    if not stage or not stage.urls:
        raise HTTPException(status_code=400, detail="Keine aktive Stage oder URL konfiguriert")
    return stage.urls[0].url.rstrip("/")


@router.get("/local-wlp")
async def get_local_wlp() -> Dict[str, Any]:
    """Gibt den konfigurierten lokalen WLP-Server zurück."""
    return {"local_wlp_url": settings.test_tool.local_wlp_url}


@router.put("/local-wlp")
async def set_local_wlp(req: LocalWLPRequest) -> Dict[str, Any]:
    """Setzt die URL des lokalen WLP-Servers für direkte Testweiterleitung."""
    settings.test_tool.local_wlp_url = req.url
    return {"local_wlp_url": settings.test_tool.local_wlp_url}


@router.post("/execute/{svc_id}")
async def execute_service(svc_id: str, req: ExecuteRequest) -> Dict[str, Any]:
    """Ruft einen Service per HTTP auf und gibt das Ergebnis zurück."""
    svc = next((s for s in settings.test_tool.services if s.id == svc_id), None)
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{svc_id}' nicht gefunden")

    base_url = _resolve_base_url(req.stage_url, req.use_local_wlp)
    endpoint = svc.endpoint
    # Path-Parameter ersetzen
    for k, v in req.params.items():
        endpoint = endpoint.replace(f"{{{k}}}", str(v))

    # Query vs Body Parameter aufteilen
    query_params = {}
    body_params = {}
    for p in svc.parameters:
        val = req.params.get(p.name)
        if val is None:
            continue
        if p.location == "query":
            query_params[p.name] = val
        else:
            body_params[p.name] = val

    # Nicht explizit parameterisierte Keys gehen in den Body
    param_names = {p.name for p in svc.parameters}
    for k, v in req.params.items():
        if k not in param_names:
            body_params[k] = v

    headers = {"Content-Type": svc.content_type}
    headers.update(svc.headers)
    headers.update(req.extra_headers)

    body = json.dumps(body_params) if body_params else None
    url = base_url + endpoint
    timeout = httpx.Timeout(req.timeout_seconds)

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            resp = await client.request(
                method=svc.method,
                url=url,
                headers=headers,
                params=query_params or None,
                content=body.encode() if body else None,
            )
        text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = text

        return {
            "success": resp.is_success,
            "status_code": resp.status_code,
            "url": str(resp.url),
            "method": svc.method,
            "request_body": body_params,
            "response": data,
            "raw": text[:5000] if len(text) > 5000 else text,
            "response_headers": dict(resp.headers),
            "elapsed_ms": int(resp.elapsed.total_seconds() * 1000) if resp.elapsed else None,
            "via_local_wlp": req.use_local_wlp,
        }
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Timeout nach {req.timeout_seconds}s")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/local/{svc_id}")
async def execute_local_service(svc_id: str, req: ExecuteRequest) -> StreamingResponse:
    """Führt einen lokalen Service (Skript im Repo) aus und streamt die Ausgabe."""
    svc = next((s for s in settings.test_tool.services if s.id == svc_id), None)
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{svc_id}' nicht gefunden")
    if not svc.local_script:
        raise HTTPException(status_code=400, detail="Kein lokales Skript für diesen Service konfiguriert")

    # Skript-Pfad auflösen (relativ zum aktiven Java- oder Python-Repo)
    repo_path = settings.java.get_active_path() or settings.python.get_active_path() or "."
    script_path = Path(repo_path) / svc.local_script
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"Skript nicht gefunden: {script_path}")

    interpreter = svc.local_interpreter or "python"
    cmd = [interpreter, str(script_path)]
    # Parameter als CLI-Argumente anhängen
    for k, v in req.params.items():
        cmd += [f"--{k}", str(v)]

    async def stream_output():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=repo_path,
            )
            yield f"data: {json.dumps({'type': 'start', 'cmd': ' '.join(cmd)})}\n\n"
            async for line in proc.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {json.dumps({'type': 'output', 'line': text})}\n\n"
            await proc.wait()
            yield f"data: {json.dumps({'type': 'done', 'exit_code': proc.returncode})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream_output(), media_type="text/event-stream")
