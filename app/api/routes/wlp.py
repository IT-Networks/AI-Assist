"""
WLP (WebSphere Liberty Profile) Server API.

Routes:
  GET    /api/wlp/servers                    – Konfigurierte Server auflisten
  POST   /api/wlp/servers                    – Server-Eintrag hinzufügen
  PUT    /api/wlp/servers/{id}               – Server aktualisieren
  DELETE /api/wlp/servers/{id}               – Server entfernen

  POST   /api/wlp/servers/{id}/validate      – server.xml + Artefakt prüfen
  POST   /api/wlp/servers/{id}/start         – Server starten (SSE-Stream)
  POST   /api/wlp/servers/{id}/stop          – Server stoppen
  GET    /api/wlp/servers/{id}/status        – Server-Status abfragen
  GET    /api/wlp/servers/{id}/logs          – Letzten Log-Auszug holen

  GET    /api/wlp/discover                   – WLP-Server im Repo finden
  POST   /api/wlp/import                     – Gefundene Server importieren
"""

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings, WLPServerEntry

router = APIRouter(prefix="/api/wlp", tags=["wlp"])

# Laufende Prozesse: server_id → asyncio.subprocess.Process
_running_processes: Dict[str, Any] = {}


# ── Request Models ─────────────────────────────────────────────────────────────

class WLPServerRequest(BaseModel):
    name: str
    description: str = ""
    wlp_path: str
    server_name: str = "defaultServer"
    start_timeout_seconds: int = 300
    extra_jvm_args: str = ""


# ── Server Management ─────────────────────────────────────────────────────────

@router.get("/servers")
async def list_servers() -> Dict[str, Any]:
    return {
        "servers": [s.model_dump() for s in settings.wlp.servers],
        "enabled": settings.wlp.enabled,
        "running": list(_running_processes.keys()),
    }


@router.post("/servers")
async def add_server(req: WLPServerRequest) -> Dict[str, Any]:
    from app.utils.path_validator import validate_identifier

    # wlp_path muss existieren
    wlp_path = Path(req.wlp_path)
    if not wlp_path.exists():
        raise HTTPException(status_code=400, detail=f"WLP-Pfad existiert nicht: {req.wlp_path}")

    # server_name validieren (keine Path-Traversal-Zeichen)
    is_valid, error = validate_identifier(req.server_name, max_length=64, allow_dots=False)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Ungültiger server_name: {error}")

    srv = WLPServerEntry(id=str(uuid.uuid4())[:8], **req.model_dump())
    settings.wlp.servers.append(srv)
    return {"added": srv.model_dump()}


@router.put("/servers/{server_id}")
async def update_server(server_id: str, req: WLPServerRequest) -> Dict[str, Any]:
    from app.utils.path_validator import validate_identifier

    # wlp_path muss existieren
    wlp_path = Path(req.wlp_path)
    if not wlp_path.exists():
        raise HTTPException(status_code=400, detail=f"WLP-Pfad existiert nicht: {req.wlp_path}")

    # server_name validieren
    is_valid, error = validate_identifier(req.server_name, max_length=64, allow_dots=False)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Ungültiger server_name: {error}")

    for i, s in enumerate(settings.wlp.servers):
        if s.id == server_id:
            settings.wlp.servers[i] = WLPServerEntry(id=server_id, **req.model_dump())
            return {"updated": settings.wlp.servers[i].model_dump()}
    raise HTTPException(status_code=404, detail=f"Server '{server_id}' nicht gefunden")


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str) -> Dict[str, Any]:
    before = len(settings.wlp.servers)
    settings.wlp.servers = [s for s in settings.wlp.servers if s.id != server_id]
    if len(settings.wlp.servers) == before:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' nicht gefunden")
    return {"deleted": server_id}


# ── Validation ────────────────────────────────────────────────────────────────

def _get_server(server_id: str) -> WLPServerEntry:
    srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' nicht gefunden")
    return srv


def _validate_server_xml(wlp_path: str, server_name: str) -> Dict[str, Any]:
    """Prüft server.xml auf WAR/EAR-Deployment und ob das Artefakt existiert."""
    server_dir = Path(wlp_path) / "usr" / "servers" / server_name
    xml_path = server_dir / "server.xml"

    if not xml_path.exists():
        return {"valid": False, "error": f"server.xml nicht gefunden: {xml_path}"}

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError as e:
        return {"valid": False, "error": f"XML-Parsefehler: {e}"}

    # Namespace-agnostisches Suchen (WLP nutzt keinen NS, aber sicherheitshalber)
    apps = []
    artifact_checks = []

    for tag in ("application", "webApplication", "enterpriseApplication"):
        for el in root.iter(tag):
            loc = el.get("location", "")
            name = el.get("name", "")
            app_type = el.get("type", tag.replace("Application", "").lower())
            entry = {"tag": tag, "name": name, "location": loc, "type": app_type}

            # Artefakt-Pfad auflösen
            if loc:
                if Path(loc).is_absolute():
                    artifact_path = Path(loc)
                else:
                    # Relative zum apps-Verzeichnis des Servers oder dropins
                    artifact_path = server_dir / "apps" / loc
                    if not artifact_path.exists():
                        artifact_path = server_dir / "dropins" / loc

                entry["artifact_path"] = str(artifact_path)
                entry["artifact_exists"] = artifact_path.exists()
                artifact_checks.append(entry)
            apps.append(entry)

    return {
        "valid": True,
        "server_xml": str(xml_path),
        "server_dir": str(server_dir),
        "applications": apps,
        "artifact_checks": artifact_checks,
        "all_artifacts_present": all(a.get("artifact_exists", False) for a in artifact_checks),
    }


@router.post("/servers/{server_id}/validate")
async def validate_server(server_id: str) -> Dict[str, Any]:
    srv = _get_server(server_id)
    result = _validate_server_xml(srv.wlp_path, srv.server_name)

    # Auch Repo-Artefakt prüfen (aus aktivem Java-Repo)
    repo_path = settings.wlp.repo_path or settings.java.get_active_path()
    built_artifact = None
    if repo_path:
        for pattern in ["**/target/*.war", "**/target/*.ear", "**/build/*.war"]:
            matches = list(Path(repo_path).glob(pattern))
            if matches:
                newest = max(matches, key=lambda p: p.stat().st_mtime)
                built_artifact = {
                    "path": str(newest),
                    "size_kb": round(newest.stat().st_size / 1024, 1),
                    "modified": newest.stat().st_mtime,
                }
                break

    result["built_artifact"] = built_artifact
    return result


# ── Start / Stop / Status ────────────────────────────────────────────────────

@router.post("/servers/{server_id}/start")
async def start_server(server_id: str) -> StreamingResponse:
    """Startet den WLP-Server und streamt Ausgabe als SSE."""
    srv = _get_server(server_id)

    if server_id in _running_processes:
        proc = _running_processes[server_id]
        if proc.returncode is None:
            raise HTTPException(status_code=409, detail="Server läuft bereits")

    server_script = Path(srv.wlp_path) / "bin" / "server"
    if not server_script.exists():
        # Fallback für Windows
        server_script = Path(srv.wlp_path) / "bin" / "server.bat"
    if not server_script.exists():
        raise HTTPException(status_code=400, detail=f"WLP server-Skript nicht gefunden in: {srv.wlp_path}/bin/")

    cmd = [str(server_script), "run", srv.server_name]
    env_extra = {}
    if srv.extra_jvm_args:
        env_extra["JVM_ARGS"] = srv.extra_jvm_args

    async def stream_start():
        import os
        env = {**os.environ, **env_extra}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            _running_processes[server_id] = proc

            yield f"data: {json.dumps({'type': 'start', 'cmd': ' '.join(cmd), 'pid': proc.pid})}\n\n"

            # Ausgabe streamen; Fehler-Muster erkennen
            error_patterns = [
                r"CWWKZ0002E",   # App start failed
                r"CWWKE0701E",   # Server start timeout
                r"CWWKF0011E",   # Feature install failed
                r"java\.lang\.\w*Exception",
                r"ERROR",
                r"FAILED",
            ]
            ready_pattern = re.compile(r"CWWKF0011I|The server .* is ready to run a smarter planet")
            error_re = [re.compile(p, re.IGNORECASE) for p in error_patterns]

            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").rstrip()
                is_error = any(r.search(line) for r in error_re)
                is_ready = bool(ready_pattern.search(line))
                yield f"data: {json.dumps({'type': 'output', 'line': line, 'is_error': is_error, 'is_ready': is_ready})}\n\n"

                if is_error:
                    yield f"data: {json.dumps({'type': 'warning', 'message': f'Auffälliger Fehler erkannt: {line[:200]}'})}\n\n"
                if is_ready:
                    yield f"data: {json.dumps({'type': 'ready', 'message': 'Server bereit'})}\n\n"

            await proc.wait()
            _running_processes.pop(server_id, None)
            yield f"data: {json.dumps({'type': 'done', 'exit_code': proc.returncode})}\n\n"

        except Exception as e:
            _running_processes.pop(server_id, None)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream_start(), media_type="text/event-stream")


@router.post("/servers/{server_id}/stop")
async def stop_server(server_id: str) -> Dict[str, Any]:
    """Stoppt den laufenden WLP-Server."""
    srv = _get_server(server_id)

    # Prozess über WLP-Skript stoppen (bevorzugt)
    server_script = Path(srv.wlp_path) / "bin" / "server"
    if not server_script.exists():
        server_script = Path(srv.wlp_path) / "bin" / "server.bat"

    try:
        proc = await asyncio.create_subprocess_exec(
            str(server_script), "stop", srv.server_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate(timeout=30)
        output = stdout.decode(errors="replace") if stdout else ""

        # Auch laufenden Prozess beenden
        running = _running_processes.pop(server_id, None)
        if running and running.returncode is None:
            running.terminate()

        return {"success": proc.returncode == 0, "output": output}
    except asyncio.TimeoutError:
        running = _running_processes.pop(server_id, None)
        if running:
            running.kill()
        return {"success": True, "output": "Server-Prozess erzwungen beendet"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/servers/{server_id}/status")
async def server_status(server_id: str) -> Dict[str, Any]:
    """Prüft ob der Server läuft."""
    srv = _get_server(server_id)

    running_proc = _running_processes.get(server_id)
    is_running_proc = running_proc is not None and running_proc.returncode is None

    # WLP status-Befehl ausführen
    server_script = Path(srv.wlp_path) / "bin" / "server"
    if not server_script.exists():
        server_script = Path(srv.wlp_path) / "bin" / "server.bat"

    wlp_status = None
    if server_script.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                str(server_script), "status", srv.server_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            wlp_status = stdout.decode(errors="replace").strip() if stdout else ""
        except Exception:
            pass

    return {
        "server_id": server_id,
        "server_name": srv.server_name,
        "is_running": is_running_proc,
        "wlp_status_output": wlp_status,
        "pid": running_proc.pid if is_running_proc else None,
    }


@router.get("/servers/{server_id}/logs")
async def get_logs(server_id: str, lines: int = 200) -> Dict[str, Any]:
    """Liest die letzten Zeilen aus messages.log des WLP-Servers."""
    srv = _get_server(server_id)
    log_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "logs" / "messages.log"

    if not log_path.exists():
        return {"found": False, "log_path": str(log_path), "lines": []}

    with open(log_path, "r", errors="replace") as f:
        all_lines = f.readlines()

    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {
        "found": True,
        "log_path": str(log_path),
        "total_lines": len(all_lines),
        "lines": [l.rstrip() for l in tail],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Discovery & Import - Server aus Repo importieren
# ══════════════════════════════════════════════════════════════════════════════

def _parse_jvm_options(jvm_options_path: Path) -> str:
    """Liest jvm.options und gibt alle JVM-Args als String zurück."""
    if not jvm_options_path.exists():
        return ""
    try:
        lines = jvm_options_path.read_text(encoding="utf-8", errors="replace").splitlines()
        # Zeilen ohne Kommentare und leer
        args = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
        return " ".join(args)
    except Exception:
        return ""


def _parse_server_xml_features(server_xml_path: Path) -> list:
    """Extrahiert Features aus server.xml."""
    if not server_xml_path.exists():
        return []
    try:
        tree = ET.parse(str(server_xml_path))
        root = tree.getroot()
        features = []
        for fm in root.iter("featureManager"):
            for feature in fm.iter("feature"):
                if feature.text:
                    features.append(feature.text.strip())
        return features
    except Exception:
        return []


def _discover_wlp_servers(base_path: Path) -> list:
    """
    Sucht nach WLP-Servern in einem Verzeichnis.

    Suchpfade:
    - {base}/wlp/usr/servers/*/server.xml
    - {base}/liberty/usr/servers/*/server.xml
    - {base}/**/usr/servers/*/server.xml (max 5 Ebenen)
    """
    discovered = []
    search_patterns = [
        "wlp/usr/servers/*/server.xml",
        "liberty/usr/servers/*/server.xml",
        "*/wlp/usr/servers/*/server.xml",
        "*/liberty/usr/servers/*/server.xml",
    ]

    seen_servers = set()

    for pattern in search_patterns:
        for server_xml in base_path.glob(pattern):
            server_dir = server_xml.parent
            server_name = server_dir.name

            # WLP-Root ermitteln (3 Ebenen hoch: servers/{name}/server.xml → usr/servers/{name})
            wlp_root = server_dir.parent.parent.parent
            if not (wlp_root / "bin" / "server").exists() and not (wlp_root / "bin" / "server.bat").exists():
                # Kein gültiger WLP-Root
                continue

            # Duplikate vermeiden
            key = (str(wlp_root), server_name)
            if key in seen_servers:
                continue
            seen_servers.add(key)

            # JVM-Options lesen
            jvm_options_path = server_dir / "jvm.options"
            jvm_args = _parse_jvm_options(jvm_options_path)

            # Features aus server.xml
            features = _parse_server_xml_features(server_xml)

            # Beschreibung generieren
            desc_parts = []
            if features:
                desc_parts.append(f"Features: {', '.join(features[:5])}")
                if len(features) > 5:
                    desc_parts.append(f"(+{len(features) - 5} weitere)")

            discovered.append({
                "server_name": server_name,
                "wlp_path": str(wlp_root),
                "server_xml": str(server_xml),
                "server_dir": str(server_dir),
                "jvm_options": jvm_args,
                "features": features,
                "description": " ".join(desc_parts),
                "has_jvm_options": bool(jvm_args),
                "relative_path": str(server_xml.relative_to(base_path)),
            })

    return discovered


@router.get("/discover")
async def discover_servers(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Sucht nach WLP-Servern in einem Verzeichnis.

    Der WLP-Server kann in einem separaten Ordner liegen (nicht im Repo).
    Gib den WLP-Installationspfad als Query-Parameter an.

    Args:
        path: WLP-Installationspfad (z.B. C:/wlp oder /opt/ibm/wlp)
              Wenn leer, wird settings.wlp.repo_path verwendet.

    Findet server.xml Dateien und extrahiert:
    - Server-Name (aus Ordnername)
    - WLP-Installationspfad
    - JVM-Options (aus jvm.options)
    - Features (aus server.xml)
    """
    # Pfad bestimmen: Query-Param > wlp.repo_path > java.repo_path (Fallback)
    search_path = path or settings.wlp.repo_path or settings.java.get_active_path()

    if not search_path:
        return {
            "found": [],
            "search_path": None,
            "message": "Kein Suchpfad angegeben. Bitte 'path' Parameter setzen oder wlp.repo_path konfigurieren.",
            "hint": "Beispiel: /api/wlp/discover?path=C:/wlp oder /api/wlp/discover?path=/opt/ibm/wlp",
        }

    root = Path(search_path)
    if not root.exists():
        return {
            "found": [],
            "search_path": str(root),
            "message": f"Pfad existiert nicht: {root}",
        }

    # Prüfen ob der Pfad direkt ein WLP-Root ist (hat bin/server)
    servers = []
    if (root / "bin" / "server").exists() or (root / "bin" / "server.bat").exists():
        # Direkt der WLP-Root - suche in usr/servers/
        servers = _discover_wlp_in_installation(root)
    else:
        # Suche rekursiv nach WLP-Installationen
        servers = _discover_wlp_servers(root)

    # Bereits konfigurierte Server markieren
    existing_keys = {(s.wlp_path, s.server_name) for s in settings.wlp.servers}
    for srv in servers:
        srv["already_imported"] = (srv["wlp_path"], srv["server_name"]) in existing_keys

    return {
        "found": servers,
        "search_path": str(root),
        "existing_count": len(settings.wlp.servers),
        "message": f"{len(servers)} WLP-Server gefunden" if servers else "Keine WLP-Server gefunden",
    }


def _discover_wlp_in_installation(wlp_root: Path) -> list:
    """
    Findet alle Server in einer WLP-Installation.

    Sucht in: {wlp_root}/usr/servers/*/server.xml
    """
    discovered = []
    servers_dir = wlp_root / "usr" / "servers"

    if not servers_dir.exists():
        return []

    for server_xml in servers_dir.glob("*/server.xml"):
        server_dir = server_xml.parent
        server_name = server_dir.name

        # JVM-Options lesen
        jvm_options_path = server_dir / "jvm.options"
        jvm_args = _parse_jvm_options(jvm_options_path)

        # Features aus server.xml
        features = _parse_server_xml_features(server_xml)

        # Beschreibung generieren
        desc_parts = []
        if features:
            desc_parts.append(f"Features: {', '.join(features[:5])}")
            if len(features) > 5:
                desc_parts.append(f"(+{len(features) - 5} weitere)")

        discovered.append({
            "server_name": server_name,
            "wlp_path": str(wlp_root),
            "server_xml": str(server_xml),
            "server_dir": str(server_dir),
            "jvm_options": jvm_args,
            "features": features,
            "description": " ".join(desc_parts),
            "has_jvm_options": bool(jvm_args),
            "relative_path": f"usr/servers/{server_name}/server.xml",
        })

    return discovered


class WLPImportRequest(BaseModel):
    """Request zum Importieren gefundener WLP-Server."""
    servers: list  # Liste von {wlp_path, server_name, jvm_options?, description?}


@router.post("/import")
async def import_servers(req: WLPImportRequest) -> Dict[str, Any]:
    """
    Importiert gefundene WLP-Server in die Konfiguration.

    Erwartet eine Liste von Server-Definitionen aus /discover.
    """
    from app.utils.path_validator import validate_identifier

    imported = []
    errors = []

    for srv in req.servers:
        wlp_path = srv.get("wlp_path", "")
        server_name = srv.get("server_name", "defaultServer")
        jvm_options = srv.get("jvm_options", "")
        description = srv.get("description", "")

        # Validierung
        if not wlp_path:
            errors.append({"server_name": server_name, "error": "wlp_path fehlt"})
            continue

        wlp = Path(wlp_path)
        if not wlp.exists():
            errors.append({"server_name": server_name, "error": f"Pfad existiert nicht: {wlp_path}"})
            continue

        is_valid, error = validate_identifier(server_name, max_length=64, allow_dots=False)
        if not is_valid:
            errors.append({"server_name": server_name, "error": f"Ungültiger server_name: {error}"})
            continue

        # Bereits vorhanden?
        exists = any(
            s.wlp_path == wlp_path and s.server_name == server_name
            for s in settings.wlp.servers
        )
        if exists:
            errors.append({"server_name": server_name, "error": "Server bereits importiert"})
            continue

        # Server hinzufügen
        entry = WLPServerEntry(
            id=str(uuid.uuid4())[:8],
            name=f"{server_name} ({wlp.name})",
            description=description,
            wlp_path=wlp_path,
            server_name=server_name,
            extra_jvm_args=jvm_options,
        )
        settings.wlp.servers.append(entry)
        imported.append(entry.model_dump())

    return {
        "imported": imported,
        "imported_count": len(imported),
        "errors": errors,
        "total_servers": len(settings.wlp.servers),
    }
