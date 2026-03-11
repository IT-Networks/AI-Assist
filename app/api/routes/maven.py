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

  GET    /api/maven/discover           – Maven-Projekte + IntelliJ Run Configs finden
  POST   /api/maven/import             – Gefundene Builds importieren
"""

import asyncio
import sys
import traceback

# Windows: ProactorEventLoop für asyncio.create_subprocess_exec()
# Muss gesetzt werden BEVOR Subprozesse erstellt werden (auch bei uvicorn reload)
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
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
    from app.utils.path_validator import validate_path_within_base

    # Path-Validierung: pom_path muss innerhalb des Repos liegen
    base_path = settings.java.get_active_path() or settings.wlp.repo_path
    if base_path:
        is_valid, resolved_path, error = validate_path_within_base(
            req.pom_path, base_path, allow_absolute=True
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Ungültiger pom_path: {error}")
    else:
        # Prüfe ob Pfad existiert
        if not Path(req.pom_path).exists():
            raise HTTPException(status_code=400, detail=f"pom_path nicht gefunden: {req.pom_path}")

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
    from app.utils.path_validator import validate_path_within_base

    # Path-Validierung: pom_path muss innerhalb des Repos liegen
    base_path = settings.java.get_active_path() or settings.wlp.repo_path
    if base_path:
        is_valid, resolved_path, error = validate_path_within_base(
            req.pom_path, base_path, allow_absolute=True
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Ungültiger pom_path: {error}")
        pom = Path(resolved_path)
    else:
        # Fallback: nur prüfen ob Pfad existiert (für absolute Pfade ohne Repo)
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
    import shutil
    mvn = settings.maven.mvn_executable
    is_windows = os.name == 'nt'

    # Windows: Wenn mvn ohne Extension angegeben, nach mvn.cmd suchen
    if is_windows and mvn == "mvn":
        mvn_found = shutil.which("mvn.cmd") or shutil.which("mvn.bat") or shutil.which("mvn")
        if mvn_found:
            mvn = mvn_found

    # Windows: .cmd/.bat Dateien müssen über cmd.exe ausgeführt werden
    if is_windows and (mvn.endswith(".cmd") or mvn.endswith(".bat")):
        cmd = ["cmd.exe", "/c", mvn, "-f", str(pom)]
    else:
        cmd = [mvn, "-f", str(pom)]

    # Maven Settings und Local Repo
    if settings.maven.settings_file:
        cmd += ["-s", settings.maven.settings_file]
    if settings.maven.local_repo:
        cmd += [f"-Dmaven.repo.local={settings.maven.local_repo}"]

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

    # JAVA_HOME setzen wenn konfiguriert
    if settings.maven.java_home:
        env["JAVA_HOME"] = settings.maven.java_home

    cwd = str(pom.parent)

    async def stream_build():
        # Debug: Welche Java- und Maven-Version wird verwendet?
        java_home_val = env.get("JAVA_HOME", "nicht gesetzt (System-Default)")
        java_home_source = "Maven-Config" if settings.maven.java_home else "System"
        yield f"data: {json.dumps({'type': 'output', 'line': f'[DEBUG] JAVA_HOME ({java_home_source}): {java_home_val}', 'is_error': False, 'is_warning': False, 'is_success': False})}\n\n"
        yield f"data: {json.dumps({'type': 'output', 'line': f'[DEBUG] Maven: {mvn}', 'is_error': False, 'is_warning': False, 'is_success': False})}\n\n"

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
            error_msg = f"{type(e).__name__}: {e}"
            tb_str = traceback.format_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': error_msg, 'traceback': tb_str})}\n\n"

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


# ══════════════════════════════════════════════════════════════════════════════
# Discovery & Import - Maven-Projekte und IntelliJ Configs finden
# ══════════════════════════════════════════════════════════════════════════════

def _parse_pom_info(pom_path: Path) -> Dict[str, Any]:
    """Extrahiert Basis-Infos aus einer pom.xml."""
    try:
        tree = ET.parse(str(pom_path))
        root = tree.getroot()

        # Namespace-agnostisch parsen
        def get_text(tag: str) -> str:
            el = root.find(f"{{{_POM_NS}}}{tag}")
            if el is None:
                el = root.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        artifact_id = get_text("artifactId")
        group_id = get_text("groupId")
        name = get_text("name")
        packaging = get_text("packaging") or "jar"

        # Module für Multi-Module-Projekte
        modules = []
        modules_el = root.find(f"{{{_POM_NS}}}modules")
        if modules_el is None:
            modules_el = root.find("modules")
        if modules_el is not None:
            for mod in modules_el:
                if mod.text:
                    modules.append(mod.text.strip())

        return {
            "artifact_id": artifact_id,
            "group_id": group_id,
            "name": name or artifact_id,
            "packaging": packaging,
            "modules": modules,
            "is_multi_module": len(modules) > 0,
        }
    except Exception as e:
        return {"error": str(e)}


def _parse_intellij_maven_config(xml_path: Path, repo_path: Path) -> Optional[Dict[str, Any]]:
    """
    Parst eine IntelliJ Maven Run Configuration.

    Format: .idea/runConfigurations/*.xml
    Unterstützt verschiedene IntelliJ-Versionen mit unterschiedlichen Option-Namen.
    """
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        # Nur Maven-Configs verarbeiten
        config = root.find("configuration")
        if config is None:
            config = root.find(".//configuration")
        if config is None:
            return None

        config_type = config.get("type", "")
        factory_name = config.get("factoryName", "")
        # Verschiedene Möglichkeiten für Maven-Configs
        is_maven = (
            "Maven" in config_type or
            config_type == "MavenRunConfiguration" or
            factory_name == "Maven"
        )
        if not is_maven:
            return None

        name = config.get("name", xml_path.stem)

        # Maven-Optionen extrahieren
        goals = ""
        profiles = []
        pom_path = ""
        jvm_args = ""
        working_dir = ""

        # MavenSettings Block (neuere IntelliJ-Versionen)
        maven_settings = config.find(".//MavenSettings")
        if maven_settings is not None:
            for option in maven_settings.findall("option"):
                opt_name = option.get("name", "")
                opt_value = option.get("value", "")

                # Verschiedene Option-Namen für Working Directory
                if opt_name in ("myWorkingDirectoryPath", "workingDirectoryPath"):
                    working_dir = opt_value

        # MavenGeneralSettings (ältere Versionen)
        general_settings = config.find(".//MavenGeneralSettings")
        if general_settings is not None:
            wd = general_settings.get("pomXmlPath", "") or general_settings.get("workingDirectoryPath", "")
            if wd:
                working_dir = wd

        # Durch alle Optionen iterieren (verschiedene Namenskonventionen)
        for option in config.findall(".//option"):
            opt_name = option.get("name", "")
            opt_value = option.get("value", "")

            # Goals (verschiedene Namen)
            if opt_name in ("goals", "myGoals", "commandLine"):
                if opt_value:
                    goals = opt_value
            # Profiles
            elif opt_name in ("profiles", "myProfiles", "enabledProfiles"):
                if opt_value:
                    profiles = [p.strip() for p in opt_value.split(",") if p.strip()]
            # POM-Pfad
            elif opt_name in ("pomFileName", "myPomFileName", "pomFile"):
                if opt_value:
                    pom_path = opt_value
            # Working Directory
            elif opt_name in ("workingDirectory", "myWorkingDirectoryPath", "workingDirectoryPath"):
                if opt_value and not working_dir:
                    working_dir = opt_value
            # JVM Options
            elif opt_name in ("vmOptions", "myVmOptions", "jvmOptions"):
                if opt_value:
                    jvm_args = opt_value

        # Working Directory als Fallback für pom_path
        if not pom_path and working_dir:
            pom_path = working_dir

        # $PROJECT_DIR$ ersetzen
        if pom_path and "$PROJECT_DIR$" in pom_path:
            pom_path = pom_path.replace("$PROJECT_DIR$", str(repo_path))

        # pom.xml anhängen wenn nur Verzeichnis
        if pom_path and not pom_path.endswith(".xml"):
            pom_candidate = Path(pom_path) / "pom.xml"
            if pom_candidate.exists():
                pom_path = str(pom_candidate)
            elif Path(pom_path).exists() and Path(pom_path).is_file():
                # pom_path ist bereits eine Datei
                pass
            else:
                # Versuche pom.xml im Verzeichnis zu finden
                pom_candidate = Path(pom_path) / "pom.xml"
                pom_path = str(pom_candidate)  # Auch wenn nicht existent, für spätere Validierung

        # Auch ohne goals oder pom_path zurückgeben, wenn Name vorhanden
        # (goals können in der UI hinzugefügt werden)
        return {
            "source": "intellij",
            "config_file": str(xml_path),
            "name": name,
            "pom_path": pom_path,
            "goals": goals or "clean install",
            "profiles": profiles,
            "jvm_args": jvm_args,
            "skip_tests": "-DskipTests" in goals or "-Dmaven.test.skip" in goals,
        }
    except Exception:
        return None


def _discover_maven_projects(repo_path: Path) -> Dict[str, Any]:
    """
    Findet alle Maven-Projekte und IntelliJ Run Configurations.

    Returns:
        {
            "pom_projects": [...],
            "intellij_configs": [...],
        }
    """
    pom_projects = []
    intellij_configs = []

    # 1. pom.xml Dateien finden (max 5 Ebenen tief)
    for pom_file in repo_path.rglob("pom.xml"):
        rel = pom_file.relative_to(repo_path)
        depth = len(rel.parts)
        if depth > 5 or "target" in rel.parts or ".idea" in rel.parts:
            continue

        info = _parse_pom_info(pom_file)
        if "error" not in info:
            pom_projects.append({
                "pom_path": str(pom_file),
                "relative_path": str(rel),
                "depth": depth,
                **info,
                "suggested_goals": "clean install" if info.get("packaging") != "pom" else "clean install -N",
            })

    # Nach Tiefe sortieren (Root-POM zuerst)
    pom_projects.sort(key=lambda p: p["depth"])

    # 2. IntelliJ Run Configurations
    idea_dir = repo_path / ".idea" / "runConfigurations"
    if idea_dir.exists():
        for config_file in idea_dir.glob("*.xml"):
            config = _parse_intellij_maven_config(config_file, repo_path)
            if config:
                intellij_configs.append(config)

    return {
        "pom_projects": pom_projects,
        "intellij_configs": intellij_configs,
    }


@router.get("/discover")
async def discover_maven_projects() -> Dict[str, Any]:
    """
    Sucht nach Maven-Projekten und IntelliJ Run Configurations im aktiven Repository.

    Findet:
    - pom.xml Dateien mit Projekt-Infos (artifactId, groupId, modules)
    - IntelliJ Maven Run Configurations (.idea/runConfigurations/*.xml)

    Die gefundenen Projekte können dann mit POST /import importiert werden.
    """
    repo_path = settings.java.get_active_path() or settings.wlp.repo_path
    if not repo_path:
        return {
            "pom_projects": [],
            "intellij_configs": [],
            "repo_path": None,
            "message": "Kein aktives Repository konfiguriert.",
        }

    root = Path(repo_path)
    if not root.exists():
        return {
            "pom_projects": [],
            "intellij_configs": [],
            "repo_path": str(root),
            "message": f"Repository-Pfad existiert nicht: {root}",
        }

    result = _discover_maven_projects(root)

    # Bereits konfigurierte Builds markieren
    existing_poms = {b.pom_path for b in settings.maven.builds}
    for proj in result["pom_projects"]:
        proj["already_imported"] = proj["pom_path"] in existing_poms

    for conf in result["intellij_configs"]:
        conf["already_imported"] = conf.get("pom_path", "") in existing_poms

    total = len(result["pom_projects"]) + len(result["intellij_configs"])
    return {
        **result,
        "repo_path": str(root),
        "existing_count": len(settings.maven.builds),
        "message": f"{total} Maven-Konfigurationen gefunden" if total else "Keine Maven-Projekte gefunden",
    }


class MavenImportItem(BaseModel):
    """Ein zu importierender Maven-Build."""
    name: str
    pom_path: str
    goals: str = "clean install"
    profiles: List[str] = []
    skip_tests: bool = False
    jvm_args: str = ""
    description: str = ""


class MavenImportRequest(BaseModel):
    """Request zum Importieren von Maven-Builds."""
    builds: List[MavenImportItem]


@router.post("/import")
async def import_builds(req: MavenImportRequest) -> Dict[str, Any]:
    """
    Importiert gefundene Maven-Builds in die Konfiguration.

    Erwartet eine Liste von Build-Definitionen aus /discover.
    """
    imported = []
    errors = []

    for item in req.builds:
        pom = Path(item.pom_path)

        if not pom.exists():
            errors.append({"name": item.name, "error": f"pom.xml nicht gefunden: {item.pom_path}"})
            continue

        # Bereits vorhanden?
        exists = any(b.pom_path == item.pom_path for b in settings.maven.builds)
        if exists:
            errors.append({"name": item.name, "error": "Build bereits importiert"})
            continue

        # Build hinzufügen
        build = MavenBuild(
            id=str(uuid.uuid4())[:8],
            name=item.name,
            description=item.description,
            pom_path=item.pom_path,
            goals=item.goals,
            profiles=item.profiles,
            skip_tests=item.skip_tests,
            jvm_args=item.jvm_args,
        )
        settings.maven.builds.append(build)
        imported.append(build.model_dump())

    return {
        "imported": imported,
        "imported_count": len(imported),
        "errors": errors,
        "total_builds": len(settings.maven.builds),
    }
