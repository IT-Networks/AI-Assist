"""
Maven Build API – Builds konfigurieren und per SSE-Stream ausführen.

Routes:
  GET    /api/maven/builds             – Builds auflisten
  POST   /api/maven/builds             – Build hinzufügen
  PUT    /api/maven/builds/{id}        – Build aktualisieren
  DELETE /api/maven/builds/{id}        – Build löschen

  POST   /api/maven/builds/{id}/run    – Build ausführen (SSE-Stream)
  POST   /api/maven/builds/{id}/stop   – Laufenden Build abbrechen
  GET    /api/maven/detect             – pom.xml im aktiven Repo erkennen
  POST   /api/maven/pom/analyze        – Dependencies aus pom.xml lesen inkl. Exclusions
"""

import asyncio
import json
import os
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings, MavenBuild

router = APIRouter(prefix="/api/maven", tags=["maven"])

# Laufende Build-Prozesse: build_id → process
_running_builds: Dict[str, Any] = {}


# ── Request Models ─────────────────────────────────────────────────────────────

class BuildRequest(BaseModel):
    name: str
    description: str = ""
    pom_path: str
    goals: str = "clean install"
    profiles: List[str] = []
    skip_tests: bool = False
    jvm_args: str = ""
    extra_args: str = ""


class RunBuildRequest(BaseModel):
    profiles: Optional[List[str]] = None   # Überschreibt Build-Definition
    skip_tests: Optional[bool] = None
    extra_args: str = ""


# ── Build Management ──────────────────────────────────────────────────────────

@router.get("/builds")
async def list_builds() -> Dict[str, Any]:
    return {
        "builds": [b.model_dump() for b in settings.maven.builds],
        "enabled": settings.maven.enabled,
        "mvn_executable": settings.maven.mvn_executable,
        "running": list(_running_builds.keys()),
    }


@router.post("/builds")
async def add_build(req: BuildRequest) -> Dict[str, Any]:
    build = MavenBuild(id=str(uuid.uuid4())[:8], **req.model_dump())
    settings.maven.builds.append(build)
    return {"added": build.model_dump()}


@router.put("/builds/{build_id}")
async def update_build(build_id: str, req: BuildRequest) -> Dict[str, Any]:
    for i, b in enumerate(settings.maven.builds):
        if b.id == build_id:
            settings.maven.builds[i] = MavenBuild(id=build_id, **req.model_dump())
            return {"updated": settings.maven.builds[i].model_dump()}
    raise HTTPException(status_code=404, detail=f"Build '{build_id}' nicht gefunden")


@router.delete("/builds/{build_id}")
async def delete_build(build_id: str) -> Dict[str, Any]:
    before = len(settings.maven.builds)
    settings.maven.builds = [b for b in settings.maven.builds if b.id != build_id]
    if len(settings.maven.builds) == before:
        raise HTTPException(status_code=404, detail=f"Build '{build_id}' nicht gefunden")
    _running_builds.pop(build_id, None)
    return {"deleted": build_id}


# ── Detect pom.xml ────────────────────────────────────────────────────────────

@router.get("/detect")
async def detect_pom() -> Dict[str, Any]:
    """Sucht pom.xml-Dateien im aktiven Java-Repo."""
    repo_path = settings.java.get_active_path() or settings.wlp.repo_path
    if not repo_path:
        return {"found": [], "message": "Kein aktives Java-Repository konfiguriert"}

    root = Path(repo_path)
    poms = []
    # Nur in den ersten 4 Verzeichnisebenen suchen (Performance)
    for pom in root.rglob("pom.xml"):
        rel = pom.relative_to(root)
        depth = len(rel.parts)
        if depth <= 4 and "target" not in rel.parts:
            poms.append({
                "path": str(pom),
                "relative": str(rel),
                "depth": depth,
            })

    poms.sort(key=lambda p: p["depth"])
    return {"found": poms, "repo_path": str(root)}


# ── POM Dependency Analysis ───────────────────────────────────────────────────

_POM_NS = "http://maven.apache.org/POM/4.0.0"


def _tag(name: str) -> str:
    return f"{{{_POM_NS}}}{name}"


def _text(el: Any, tag: str) -> str:
    child = el.find(_tag(tag))
    return child.text.strip() if child is not None and child.text else ""


def _parse_dependencies(pom_path: str) -> List[Dict[str, Any]]:
    """Parst alle <dependency>-Blöcke aus einer pom.xml inkl. bestehender Exclusions."""
    try:
        tree = ET.parse(pom_path)
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"pom.xml konnte nicht geparst werden: {e}")

    root = tree.getroot()
    # Unterstütze pom.xml mit und ohne Namespace
    deps_containers = root.findall(f".//{_tag('dependencies')}")
    if not deps_containers:
        deps_containers = root.findall(".//dependencies")

    dependencies = []
    for container in deps_containers:
        dep_tag = _tag("dependency") if deps_containers == root.findall(f".//{_tag('dependencies')}") else "dependency"
        for dep in container.findall(dep_tag):
            group_id = _text(dep, "groupId") or (dep.findtext("groupId") or "")
            artifact_id = _text(dep, "artifactId") or (dep.findtext("artifactId") or "")
            version = _text(dep, "version") or (dep.findtext("version") or "")
            scope = _text(dep, "scope") or (dep.findtext("scope") or "compile")

            # Bestehende Exclusions auslesen
            existing_exclusions = []
            excl_container = dep.find(_tag("exclusions")) or dep.find("exclusions")
            if excl_container is not None:
                for excl in list(excl_container):
                    eg = excl.findtext(_tag("groupId")) or excl.findtext("groupId") or ""
                    ea = excl.findtext(_tag("artifactId")) or excl.findtext("artifactId") or ""
                    if eg or ea:
                        existing_exclusions.append({"groupId": eg.strip(), "artifactId": ea.strip()})

            if group_id or artifact_id:
                dependencies.append({
                    "groupId": group_id,
                    "artifactId": artifact_id,
                    "version": version,
                    "scope": scope,
                    "existing_exclusions": existing_exclusions,
                    "can_exclude_transitive": True,  # Immer möglich via <exclusions>
                    "safe_to_comment_out": scope in ("test", "provided", "optional"),
                })

    return dependencies


class PomAnalyzeRequest(BaseModel):
    pom_path: str


@router.post("/pom/analyze")
async def analyze_pom(req: PomAnalyzeRequest) -> Dict[str, Any]:
    """
    Liest alle Dependencies aus einer pom.xml.
    Gibt je Dependency an: groupId, artifactId, version, scope,
    bereits vorhandene Exclusions und ob Auskommentieren sicher ist.
    """
    pom = Path(req.pom_path)
    if not pom.exists():
        raise HTTPException(status_code=404, detail=f"pom.xml nicht gefunden: {pom}")

    deps = _parse_dependencies(str(pom))
    return {
        "pom_path": str(pom),
        "dependency_count": len(deps),
        "dependencies": deps,
        "hint": (
            "safe_to_comment_out=true: Dependency kann kommentiert werden (test/provided/optional). "
            "can_exclude_transitive=true: Subdependency via <exclusions> ausschließen möglich. "
            "Bevorzuge <exclusions> wenn nur eine transitiv eingezogene Library das Problem verursacht."
        ),
    }


# ── Run Build ─────────────────────────────────────────────────────────────────

@router.post("/builds/{build_id}/run")
async def run_build(build_id: str, req: RunBuildRequest = RunBuildRequest()) -> StreamingResponse:
    """Führt einen Maven-Build aus und streamt die Ausgabe als SSE."""
    build = next((b for b in settings.maven.builds if b.id == build_id), None)
    if not build:
        raise HTTPException(status_code=404, detail=f"Build '{build_id}' nicht gefunden")

    if build_id in _running_builds:
        proc = _running_builds[build_id]
        if proc.returncode is None:
            raise HTTPException(status_code=409, detail="Build läuft bereits")

    pom = Path(build.pom_path)
    if not pom.exists():
        raise HTTPException(status_code=400, detail=f"pom.xml nicht gefunden: {pom}")

    # Befehl zusammenbauen
    mvn = settings.maven.mvn_executable
    cmd = [mvn, "-f", str(pom)]
    cmd += build.goals.split()

    profiles = req.profiles if req.profiles is not None else build.profiles
    if profiles:
        cmd += ["-P", ",".join(profiles)]

    skip_tests = req.skip_tests if req.skip_tests is not None else build.skip_tests
    if skip_tests:
        cmd += ["-DskipTests=true"]

    if build.extra_args:
        cmd += build.extra_args.split()
    if req.extra_args:
        cmd += req.extra_args.split()

    # JVM-Args als Umgebungsvariable
    env = dict(os.environ)
    jvm_args = build.jvm_args
    if jvm_args:
        env["MAVEN_OPTS"] = jvm_args

    cwd = str(pom.parent)

    async def stream_build():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
            _running_builds[build_id] = proc

            yield f"data: {json.dumps({'type': 'start', 'cmd': ' '.join(cmd), 'pid': proc.pid, 'cwd': cwd})}\n\n"

            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                # Maven-Phasen und Fehler erkennen
                is_error = "[ERROR]" in line or "BUILD FAILURE" in line
                is_warning = "[WARNING]" in line
                is_info = "[INFO]" in line
                is_success = "BUILD SUCCESS" in line

                yield f"data: {json.dumps({'type': 'output', 'line': line, 'is_error': is_error, 'is_warning': is_warning, 'is_success': is_success})}\n\n"

            await proc.wait()
            _running_builds.pop(build_id, None)
            success = proc.returncode == 0
            yield f"data: {json.dumps({'type': 'done', 'exit_code': proc.returncode, 'success': success})}\n\n"

        except Exception as e:
            _running_builds.pop(build_id, None)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream_build(), media_type="text/event-stream")


@router.post("/builds/{build_id}/stop")
async def stop_build(build_id: str) -> Dict[str, Any]:
    """Bricht einen laufenden Build ab."""
    proc = _running_builds.pop(build_id, None)
    if not proc or proc.returncode is not None:
        return {"success": False, "message": "Kein laufender Build für diese ID"}
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
    return {"success": True, "message": "Build abgebrochen"}
