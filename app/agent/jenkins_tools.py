"""
Agent-Tools für Jenkins CI/CD Server (intern gehostet).

Tools:
- jenkins_list_jobs: Jobs auflisten (mit optionalem Filter)
- jenkins_job_status: Status eines Jobs (letzter Build, Health)
- jenkins_build_info: Details eines Builds (Konsole, Artefakte)
- jenkins_trigger_build: Build starten (mit Bestätigungspflicht)
- jenkins_queue_info: Build-Queue anzeigen
"""

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.core.http_client import get_jenkins_client

logger = logging.getLogger(__name__)

# Pending Build-Trigger für Bestätigung
_pending_builds: Dict[str, dict] = {}


def _get_auth_header(username: str, api_token: str) -> Dict[str, str]:
    """Erstellt Basic-Auth Header für Jenkins API."""
    if not username or not api_token:
        return {}
    credentials = f"{username}:{api_token}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


async def _jenkins_request(
    method: str,
    url: str,
    username: str,
    api_token: str,
    verify_ssl: bool = False,
    timeout: int = 30,
    params: Optional[dict] = None,
) -> Dict[str, Any]:
    """Führt einen Jenkins API Request aus (nutzt shared HTTP Client)."""
    headers = _get_auth_header(username, api_token)
    headers["Accept"] = "application/json"

    client = get_jenkins_client(verify_ssl, timeout)
    try:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
        )
        response.raise_for_status()

        # Jenkins gibt oft JSON zurück, manchmal auch Text
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return {"success": True, "data": response.json()}
        else:
            return {"success": True, "data": {"text": response.text}}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}
    except httpx.RequestError as e:
        return {"success": False, "error": f"Verbindungsfehler: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def register_jenkins_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    count = 0

    # ── jenkins_list_jobs ──────────────────────────────────────────────────────
    async def jenkins_list_jobs(**kwargs: Any) -> ToolResult:
        """Listet alle Jenkins-Jobs auf."""
        if not settings.jenkins.enabled:
            return ToolResult(success=False, error="Jenkins ist nicht aktiviert")

        filter_pattern: str = kwargs.get("filter", settings.jenkins.job_filter or "")
        base_url = settings.jenkins.base_url.rstrip("/")

        result = await _jenkins_request(
            method="GET",
            url=f"{base_url}/api/json",
            username=settings.jenkins.username,
            api_token=settings.jenkins.api_token,
            verify_ssl=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
            params={"tree": "jobs[name,url,color,healthReport[score,description]]"},
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        jobs = result["data"].get("jobs", [])

        # Filter anwenden
        if filter_pattern:
            jobs = [j for j in jobs if filter_pattern.lower() in j.get("name", "").lower()]

        # Aufbereiten
        job_list = []
        for job in jobs:
            color = job.get("color", "")
            status = "erfolgreich" if color == "blue" else "fehlgeschlagen" if "red" in color else "unbekannt"
            if "anime" in color:
                status = "läuft"

            health = job.get("healthReport", [{}])[0] if job.get("healthReport") else {}

            job_list.append({
                "name": job.get("name"),
                "status": status,
                "health_score": health.get("score"),
                "health_description": health.get("description"),
            })

        return ToolResult(
            success=True,
            data={
                "job_count": len(job_list),
                "jobs": job_list,
                "filter_applied": filter_pattern or None,
            },
        )

    registry.register(Tool(
        name="jenkins_list_jobs",
        description=(
            "Listet alle Jenkins-Jobs auf. Zeigt Name, Status (erfolgreich/fehlgeschlagen/läuft) "
            "und Health-Score. Verwende dies um einen Überblick über CI/CD-Pipelines zu bekommen "
            "oder um Job-Namen für weitere Abfragen zu finden."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="filter",
                type="string",
                description="Optionaler Filter für Job-Namen (case-insensitive Substring-Match)",
                required=False,
            ),
        ],
        handler=jenkins_list_jobs,
    ))
    count += 1

    # ── jenkins_job_status ─────────────────────────────────────────────────────
    async def jenkins_job_status(**kwargs: Any) -> ToolResult:
        """Holt detaillierten Status eines Jenkins-Jobs."""
        if not settings.jenkins.enabled:
            return ToolResult(success=False, error="Jenkins ist nicht aktiviert")

        job_name: str = kwargs.get("job_name", "").strip()
        if not job_name:
            job_name = settings.jenkins.default_job
        if not job_name:
            return ToolResult(success=False, error="job_name ist erforderlich")

        base_url = settings.jenkins.base_url.rstrip("/")

        result = await _jenkins_request(
            method="GET",
            url=f"{base_url}/job/{job_name}/api/json",
            username=settings.jenkins.username,
            api_token=settings.jenkins.api_token,
            verify_ssl=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
            params={"tree": "name,url,color,buildable,lastBuild[number,result,timestamp,duration],lastSuccessfulBuild[number,timestamp],lastFailedBuild[number,timestamp],healthReport[score,description]"},
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        data = result["data"]
        last_build = data.get("lastBuild") or {}
        last_success = data.get("lastSuccessfulBuild") or {}
        last_failed = data.get("lastFailedBuild") or {}
        health = data.get("healthReport", [{}])[0] if data.get("healthReport") else {}

        return ToolResult(
            success=True,
            data={
                "job_name": data.get("name"),
                "buildable": data.get("buildable"),
                "last_build": {
                    "number": last_build.get("number"),
                    "result": last_build.get("result"),
                    "duration_ms": last_build.get("duration"),
                },
                "last_successful_build": last_success.get("number"),
                "last_failed_build": last_failed.get("number"),
                "health_score": health.get("score"),
                "health_description": health.get("description"),
            },
        )

    registry.register(Tool(
        name="jenkins_job_status",
        description=(
            "Holt den detaillierten Status eines Jenkins-Jobs: Letzter Build, "
            "letzter erfolgreicher/fehlgeschlagener Build, Health-Score. "
            "Verwende dies um den aktuellen Zustand einer CI/CD-Pipeline zu prüfen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="job_name",
                type="string",
                description="Name des Jenkins-Jobs (leer = Standard-Job aus Konfiguration)",
                required=False,
            ),
        ],
        handler=jenkins_job_status,
    ))
    count += 1

    # ── jenkins_build_info ─────────────────────────────────────────────────────
    async def jenkins_build_info(**kwargs: Any) -> ToolResult:
        """Holt Details eines spezifischen Builds inkl. Konsolen-Auszug."""
        if not settings.jenkins.enabled:
            return ToolResult(success=False, error="Jenkins ist nicht aktiviert")

        job_name: str = kwargs.get("job_name", "").strip()
        build_number: str = kwargs.get("build_number", "lastBuild")

        if not job_name:
            job_name = settings.jenkins.default_job
        if not job_name:
            return ToolResult(success=False, error="job_name ist erforderlich")

        base_url = settings.jenkins.base_url.rstrip("/")

        # Build-Info holen
        result = await _jenkins_request(
            method="GET",
            url=f"{base_url}/job/{job_name}/{build_number}/api/json",
            username=settings.jenkins.username,
            api_token=settings.jenkins.api_token,
            verify_ssl=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        data = result["data"]

        # Konsolen-Output (letzte 100 Zeilen)
        console_result = await _jenkins_request(
            method="GET",
            url=f"{base_url}/job/{job_name}/{build_number}/consoleText",
            username=settings.jenkins.username,
            api_token=settings.jenkins.api_token,
            verify_ssl=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
        )

        console_text = ""
        if console_result["success"]:
            full_console = console_result["data"].get("text", "")
            # Letzte 100 Zeilen
            lines = full_console.strip().split("\n")
            console_text = "\n".join(lines[-100:])

        return ToolResult(
            success=True,
            data={
                "job_name": job_name,
                "build_number": data.get("number"),
                "result": data.get("result"),
                "building": data.get("building"),
                "duration_ms": data.get("duration"),
                "timestamp": data.get("timestamp"),
                "url": data.get("url"),
                "console_output_tail": console_text,
            },
        )

    registry.register(Tool(
        name="jenkins_build_info",
        description=(
            "Holt Details eines spezifischen Jenkins-Builds: Ergebnis, Dauer, Status, "
            "und die letzten 100 Zeilen der Konsolen-Ausgabe. "
            "Verwende dies um Build-Fehler zu analysieren oder den Fortschritt zu prüfen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="job_name",
                type="string",
                description="Name des Jenkins-Jobs",
                required=False,
            ),
            ToolParameter(
                name="build_number",
                type="string",
                description="Build-Nummer oder 'lastBuild', 'lastSuccessfulBuild', 'lastFailedBuild'",
                required=False,
            ),
        ],
        handler=jenkins_build_info,
    ))
    count += 1

    # ── jenkins_trigger_build ──────────────────────────────────────────────────
    async def jenkins_trigger_build(**kwargs: Any) -> ToolResult:
        """Startet einen Jenkins-Build (mit Bestätigungspflicht)."""
        if not settings.jenkins.enabled:
            return ToolResult(success=False, error="Jenkins ist nicht aktiviert")

        job_name: str = kwargs.get("job_name", "").strip()
        reason: str = kwargs.get("reason", "")

        if not job_name:
            job_name = settings.jenkins.default_job
        if not job_name:
            return ToolResult(success=False, error="job_name ist erforderlich")

        # Wenn Bestätigung erforderlich
        if settings.jenkins.require_build_confirmation:
            import uuid
            from datetime import datetime

            trigger_id = str(uuid.uuid4())[:8]
            _pending_builds[trigger_id] = {
                "id": trigger_id,
                "job_name": job_name,
                "reason": reason,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            }

            return ToolResult(
                success=True,
                data={
                    "trigger_id": trigger_id,
                    "job_name": job_name,
                    "status": "pending_confirmation",
                    "message": (
                        f"Build für '{job_name}' wurde zur Bestätigung vorgemerkt. "
                        f"Der Nutzer muss den Build im Frontend bestätigen. "
                        f"Trigger-ID: {trigger_id}"
                    ),
                },
            )

        # Direkt ausführen (keine Bestätigung nötig)
        base_url = settings.jenkins.base_url.rstrip("/")

        result = await _jenkins_request(
            method="POST",
            url=f"{base_url}/job/{job_name}/build",
            username=settings.jenkins.username,
            api_token=settings.jenkins.api_token,
            verify_ssl=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        return ToolResult(
            success=True,
            data={
                "job_name": job_name,
                "message": f"Build für '{job_name}' wurde gestartet",
            },
        )

    registry.register(Tool(
        name="jenkins_trigger_build",
        description=(
            "Startet einen Jenkins-Build. ACHTUNG: Je nach Konfiguration ist eine "
            "Bestätigung durch den Nutzer erforderlich. Verwende dies nur wenn der "
            "Nutzer explizit einen Build starten möchte."
        ),
        category=ToolCategory.DEVOPS,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="job_name",
                type="string",
                description="Name des Jenkins-Jobs",
                required=False,
            ),
            ToolParameter(
                name="reason",
                type="string",
                description="Grund für den Build (wird dem Nutzer angezeigt)",
                required=False,
            ),
        ],
        handler=jenkins_trigger_build,
    ))
    count += 1

    # ── jenkins_queue_info ─────────────────────────────────────────────────────
    async def jenkins_queue_info(**kwargs: Any) -> ToolResult:
        """Zeigt die aktuelle Jenkins Build-Queue."""
        if not settings.jenkins.enabled:
            return ToolResult(success=False, error="Jenkins ist nicht aktiviert")

        base_url = settings.jenkins.base_url.rstrip("/")

        result = await _jenkins_request(
            method="GET",
            url=f"{base_url}/queue/api/json",
            username=settings.jenkins.username,
            api_token=settings.jenkins.api_token,
            verify_ssl=settings.jenkins.verify_ssl,
            timeout=settings.jenkins.timeout_seconds,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        items = result["data"].get("items", [])

        queue_items = []
        for item in items:
            task = item.get("task", {})
            queue_items.append({
                "job_name": task.get("name"),
                "why": item.get("why"),
                "stuck": item.get("stuck"),
                "blocked": item.get("blocked"),
                "buildable": item.get("buildable"),
            })

        return ToolResult(
            success=True,
            data={
                "queue_length": len(queue_items),
                "items": queue_items,
            },
        )

    registry.register(Tool(
        name="jenkins_queue_info",
        description=(
            "Zeigt die aktuelle Jenkins Build-Queue: Welche Jobs warten auf Ausführung, "
            "warum sie warten (blocked, stuck), etc. Verwende dies um zu prüfen ob "
            "Builds in der Warteschlange hängen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[],
        handler=jenkins_queue_info,
    ))
    count += 1

    return count


def get_pending_builds() -> Dict[str, dict]:
    """Gibt pending Build-Trigger zurück (für API-Endpunkt)."""
    return _pending_builds


async def confirm_build(trigger_id: str) -> Dict[str, Any]:
    """Bestätigt und führt einen pending Build aus."""
    from app.core.config import settings

    if trigger_id not in _pending_builds:
        return {"success": False, "error": "Trigger-ID nicht gefunden"}

    entry = _pending_builds[trigger_id]
    if entry["status"] != "pending":
        return {"success": False, "error": f"Build ist bereits {entry['status']}"}

    job_name = entry["job_name"]
    base_url = settings.jenkins.base_url.rstrip("/")

    result = await _jenkins_request(
        method="POST",
        url=f"{base_url}/job/{job_name}/build",
        username=settings.jenkins.username,
        api_token=settings.jenkins.api_token,
        verify_ssl=settings.jenkins.verify_ssl,
        timeout=settings.jenkins.timeout_seconds,
    )

    if result["success"]:
        entry["status"] = "triggered"
        return {"success": True, "message": f"Build für '{job_name}' wurde gestartet"}
    else:
        entry["status"] = "failed"
        entry["error"] = result["error"]
        return {"success": False, "error": result["error"]}


def reject_build(trigger_id: str) -> Dict[str, Any]:
    """Lehnt einen pending Build ab."""
    if trigger_id not in _pending_builds:
        return {"success": False, "error": "Trigger-ID nicht gefunden"}

    entry = _pending_builds[trigger_id]
    entry["status"] = "rejected"
    return {"success": True, "message": "Build wurde abgelehnt"}
