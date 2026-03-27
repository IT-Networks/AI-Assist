"""
Agent-Tools für Sonatype IQ Server (Lifecycle).

Tools:
- iq_list_applications: Applikationen auflisten (mit optionalem Filter)
- iq_get_violations: Policy-Violations eines Reports abrufen
- iq_list_waivers: Bestehende Waivers einer App anzeigen
- iq_check_applicable_waivers: Applicable Waivers für eine Violation prüfen
- iq_get_waiver_reasons: Vordefinierte Waiver-Reasons abrufen
- iq_create_waiver: Waiver anlegen (Write-Operation mit Bestätigungspflicht)
- iq_component_details: Komponenten-Details abrufen
"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.core.http_client import get_iq_client

logger = logging.getLogger(__name__)

# Pending Waivers für Bestätigung
_pending_waivers: Dict[str, dict] = {}

# Cache: publicId → internalId
_app_id_cache: Dict[str, str] = {}

# Cache: Waiver-Reasons
_waiver_reasons_cache: Optional[List[dict]] = None


def _get_iq_credentials() -> Tuple[str, str]:
    """
    Gibt IQ Server Credentials zurück (username, api_token).

    Prüft zuerst credential_ref, dann direkte Werte.
    """
    from app.core.config import settings

    if settings.iq_server.credential_ref:
        cred = settings.credentials.get(settings.iq_server.credential_ref)
        if cred:
            return (cred.username, cred.password or cred.token)
    return (settings.iq_server.username, settings.iq_server.api_token)


def _get_auth_header() -> Dict[str, str]:
    """Erstellt Basic-Auth Header für IQ Server API."""
    username, api_token = _get_iq_credentials()
    if not username or not api_token:
        return {}
    credentials = f"{username}:{api_token}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


async def _iq_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Führt einen IQ Server API Request aus (nutzt shared HTTP Client).

    Args:
        method: HTTP-Methode (GET, POST, PUT, DELETE)
        path: API-Pfad (z.B. /api/v2/applications)
        params: Query-Parameter
        json_body: JSON Request Body
    """
    from app.core.config import settings

    base_url = settings.iq_server.base_url.rstrip("/")
    url = f"{base_url}{path}"

    headers = _get_auth_header()
    headers["Accept"] = "application/json"
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    client = get_iq_client(
        verify_ssl=settings.iq_server.verify_ssl,
        timeout=settings.iq_server.timeout_seconds,
    )

    try:
        kwargs: Dict[str, Any] = {"method": method, "url": url, "headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body

        response = await client.request(**kwargs)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if response.status_code == 204:
            return {"success": True, "data": None}
        if "application/json" in content_type:
            return {"success": True, "data": response.json()}
        return {"success": True, "data": {"text": response.text}}

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        detail = e.response.text[:500]
        if status == 401:
            return {"success": False, "error": "Authentifizierung fehlgeschlagen - Username/Passcode in Settings prüfen"}
        if status == 403:
            return {"success": False, "error": f"Keine Berechtigung für diese Operation: {detail}"}
        if status == 404:
            return {"success": False, "error": f"Ressource nicht gefunden: {path}"}
        if status == 409:
            return {"success": False, "error": f"Konflikt (Waiver existiert bereits?): {detail}"}
        return {"success": False, "error": f"HTTP {status}: {detail}"}
    except httpx.RequestError as e:
        return {"success": False, "error": f"Verbindung zu IQ Server fehlgeschlagen: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _resolve_app_id(public_id: str) -> Optional[str]:
    """Löst publicId → internalId auf (mit Cache)."""
    if public_id in _app_id_cache:
        return _app_id_cache[public_id]

    result = await _iq_request("GET", "/api/v2/applications", params={"publicId": public_id})
    if not result["success"]:
        return None

    apps = result["data"].get("applications", [])
    if not apps:
        return None

    internal_id = apps[0].get("id")
    if internal_id:
        _app_id_cache[public_id] = internal_id
    return internal_id


def register_iq_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    count = 0

    # ── iq_list_applications ───────────────────────────────────────────────────
    async def iq_list_applications(**kwargs: Any) -> ToolResult:
        """Listet alle Applikationen im IQ Server auf."""
        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert. Bitte in Settings > Integrationen > Sonatype IQ aktivieren.")

        filter_text: str = kwargs.get("filter", "")

        result = await _iq_request("GET", "/api/v2/applications")
        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        apps = result["data"].get("applications", [])

        # Filter anwenden
        if filter_text:
            apps = [a for a in apps if filter_text.lower() in (a.get("publicId", "") + a.get("name", "")).lower()]

        # Cache befüllen
        for app in apps:
            pid = app.get("publicId")
            iid = app.get("id")
            if pid and iid:
                _app_id_cache[pid] = iid

        app_list = []
        for app in apps:
            app_list.append({
                "publicId": app.get("publicId"),
                "name": app.get("name"),
                "id": app.get("id"),
                "organizationId": app.get("organizationId"),
            })

        return ToolResult(
            success=True,
            data={
                "app_count": len(app_list),
                "applications": app_list,
                "filter_applied": filter_text or None,
            },
        )

    registry.register(Tool(
        name="iq_list_applications",
        description=(
            "Listet alle Applikationen im Sonatype IQ Server auf. Zeigt publicId, Name und Organisation. "
            "Verwende dies um App-IDs für weitere Abfragen (Violations, Waivers) zu finden "
            "oder einen Überblick über verwaltete Applikationen zu bekommen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="filter",
                type="string",
                description="Optionaler Filter für App-Name oder publicId (case-insensitive Substring-Match)",
                required=False,
            ),
        ],
        handler=iq_list_applications,
    ))
    count += 1

    # ── iq_get_violations ──────────────────────────────────────────────────────
    async def iq_get_violations(**kwargs: Any) -> ToolResult:
        """Holt Policy-Violations (Findings) für eine Applikation."""
        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert")

        app_public_id: str = kwargs.get("app_public_id", "").strip()
        if not app_public_id:
            app_public_id = settings.iq_server.default_app
        if not app_public_id:
            return ToolResult(success=False, error="app_public_id ist erforderlich (oder default_app in Settings setzen)")

        report_id: str = kwargs.get("report_id", "")

        # 1. App internalId auflösen
        internal_id = await _resolve_app_id(app_public_id)
        if not internal_id:
            return ToolResult(success=False, error=f"Applikation '{app_public_id}' nicht gefunden")

        # 2. Letzten Report holen wenn keine report_id
        if not report_id:
            reports_result = await _iq_request("GET", f"/api/v2/reports/applications/{internal_id}")
            if not reports_result["success"]:
                return ToolResult(success=False, error=f"Reports konnten nicht geladen werden: {reports_result['error']}")

            reports = reports_result["data"]
            if isinstance(reports, list) and reports:
                # Letzten Report nehmen (nach Datum sortiert)
                report_id = reports[-1].get("reportDataUrl", "").split("/")[-1] if reports[-1].get("reportDataUrl") else ""
                if not report_id:
                    # Fallback: reportId direkt
                    report_id = str(reports[-1].get("reportId", ""))
            elif isinstance(reports, dict) and reports.get("reportDataUrl"):
                report_id = reports["reportDataUrl"].split("/")[-1]

            if not report_id:
                return ToolResult(success=False, error="Kein Report für diese Applikation gefunden. Wurde bereits eine Evaluation durchgeführt?")

        # 3. Policy-Report laden
        policy_result = await _iq_request(
            "GET",
            f"/api/v2/applications/{app_public_id}/reports/{report_id}/policy",
        )
        if not policy_result["success"]:
            return ToolResult(success=False, error=f"Policy-Report konnte nicht geladen werden: {policy_result['error']}")

        # 4. Violations extrahieren und aufbereiten
        report_data = policy_result["data"]
        components = report_data.get("components", [])

        violations = []
        for comp in components:
            comp_name = ""
            comp_version = ""
            identifier = comp.get("componentIdentifier", {})
            coords = identifier.get("coordinates", {})
            if coords:
                comp_name = f"{coords.get('groupId', '')}/{coords.get('artifactId', '')}" if coords.get("groupId") else coords.get("artifactId", coords.get("name", ""))
                comp_version = coords.get("version", "")

            for violation in comp.get("violations", []):
                constraints = violation.get("constraintViolations", [])
                constraint_name = constraints[0].get("constraintName", "") if constraints else ""

                # CVE/Vulnerability-Referenz extrahieren
                vuln_ref = ""
                for cv in constraints:
                    reasons = cv.get("reasons", [])
                    for reason in reasons:
                        ref = reason.get("reference", {})
                        if ref.get("value"):
                            vuln_ref = ref["value"]
                            break
                    if vuln_ref:
                        break

                violations.append({
                    "violationId": violation.get("policyViolationId"),
                    "policyName": violation.get("policyName"),
                    "threatLevel": violation.get("policyThreatLevel"),
                    "threatCategory": violation.get("policyThreatCategory"),
                    "componentName": comp_name,
                    "componentVersion": comp_version,
                    "format": identifier.get("format", ""),
                    "waived": violation.get("waived", False),
                    "constraintName": constraint_name,
                    "vulnerabilityRef": vuln_ref,
                })

        # Nach Threat-Level absteigend sortieren
        violations.sort(key=lambda v: v.get("threatLevel", 0), reverse=True)

        # Auf Top 50 beschränken
        total_count = len(violations)
        violations = violations[:50]

        return ToolResult(
            success=True,
            data={
                "app": app_public_id,
                "report_id": report_id,
                "total_violation_count": total_count,
                "shown_count": len(violations),
                "violations": violations,
                "hinweis": (
                    "Analysiere jedes Finding und frage den Benutzer was er tun möchte: "
                    "1) Komponente updaten, 2) Waiver anlegen, 3) Applicable Waivers prüfen, "
                    "4) Finding ignorieren. Kommentiere die Severity und Auswirkung."
                ) if violations else "Keine Violations gefunden - alle Policies erfüllt.",
            },
        )

    registry.register(Tool(
        name="iq_get_violations",
        description=(
            "Holt alle Policy-Violations (Findings) für eine Applikation aus dem letzten IQ Server Report. "
            "Zeigt Severity, Policy, Komponente, CVE-Referenz und ob bereits ein Waiver existiert. "
            "Sortiert nach Threat-Level (kritischste zuerst, max. 50). "
            "Analysiere jedes Finding mit KI-Kommentar und frage den Benutzer: "
            "Waiver anlegen, Komponente updaten, applicable Waivers prüfen, oder ignorieren?"
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="app_public_id",
                type="string",
                description="Public-ID der Applikation (leer = default_app aus Settings)",
                required=False,
            ),
            ToolParameter(
                name="report_id",
                type="string",
                description="Report-ID (leer = letzter Report)",
                required=False,
            ),
        ],
        handler=iq_get_violations,
    ))
    count += 1

    # ── iq_list_waivers ────────────────────────────────────────────────────────
    async def iq_list_waivers(**kwargs: Any) -> ToolResult:
        """Listet bestehende Waivers für eine Applikation."""
        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert")

        app_public_id: str = kwargs.get("app_public_id", "").strip()
        if not app_public_id:
            app_public_id = settings.iq_server.default_app
        if not app_public_id:
            return ToolResult(success=False, error="app_public_id ist erforderlich")

        internal_id = await _resolve_app_id(app_public_id)
        if not internal_id:
            return ToolResult(success=False, error=f"Applikation '{app_public_id}' nicht gefunden")

        result = await _iq_request("GET", f"/api/v2/policyWaivers/application/{internal_id}")
        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        waivers_data = result["data"]
        if isinstance(waivers_data, dict):
            waivers_data = waivers_data.get("waivers", [])
        if not isinstance(waivers_data, list):
            waivers_data = []

        waiver_list = []
        now = datetime.now(timezone.utc)
        for w in waivers_data:
            expiry = w.get("expiryTime")
            expiry_str = ""
            is_expired = False
            if expiry:
                try:
                    exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    expiry_str = exp_dt.strftime("%Y-%m-%d")
                    is_expired = exp_dt < now
                except (ValueError, TypeError):
                    expiry_str = str(expiry)

            waiver_list.append({
                "waiverId": w.get("policyWaiverId"),
                "policyName": w.get("policyName", ""),
                "comment": w.get("comment", ""),
                "matcherStrategy": w.get("matcherStrategy", ""),
                "expiryDate": expiry_str,
                "expired": is_expired,
                "createTime": w.get("createTime", ""),
                "componentName": w.get("componentName", ""),
            })

        return ToolResult(
            success=True,
            data={
                "app": app_public_id,
                "waiver_count": len(waiver_list),
                "waivers": waiver_list,
            },
        )

    registry.register(Tool(
        name="iq_list_waivers",
        description=(
            "Listet alle bestehenden Policy-Waivers für eine Applikation. Zeigt Waiver-ID, Policy, "
            "Kommentar, Strategie, Ablaufdatum und ob der Waiver abgelaufen ist. "
            "Verwende dies um einen Überblick über akzeptierte Risiken zu bekommen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="app_public_id",
                type="string",
                description="Public-ID der Applikation (leer = default_app aus Settings)",
                required=False,
            ),
        ],
        handler=iq_list_waivers,
    ))
    count += 1

    # ── iq_check_applicable_waivers ────────────────────────────────────────────
    async def iq_check_applicable_waivers(**kwargs: Any) -> ToolResult:
        """Prüft ob applicable Waivers für eine Violation existieren."""
        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert")

        violation_id: str = kwargs.get("violation_id", "").strip()
        if not violation_id:
            return ToolResult(success=False, error="violation_id ist erforderlich")

        result = await _iq_request(
            "GET",
            f"/api/v2/policyViolations/{violation_id}/applicableWaivers",
        )
        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        data = result["data"]
        active = data.get("activeWaivers", [])
        expired = data.get("expiredWaivers", [])

        # Empfehlung generieren
        if active:
            recommendation = (
                f"Es gibt {len(active)} aktive(n) Waiver. "
                "Keine Aktion nötig - die Violation ist bereits gewaived."
            )
        elif expired:
            recommendation = (
                f"Es gibt {len(expired)} abgelaufene(n) Waiver. "
                "Ein abgelaufener Waiver kann als Vorlage für einen neuen dienen. "
                "Soll ich einen neuen Waiver mit denselben Parametern anlegen?"
            )
        else:
            recommendation = (
                "Kein Waiver vorhanden. "
                "Soll ich einen neuen Waiver anlegen? Verwende dazu iq_get_waiver_reasons "
                "um die verfügbaren Begründungen abzurufen."
            )

        return ToolResult(
            success=True,
            data={
                "violation_id": violation_id,
                "active_waivers": active,
                "expired_waivers": expired,
                "has_active": len(active) > 0,
                "has_expired": len(expired) > 0,
                "recommendation": recommendation,
            },
        )

    registry.register(Tool(
        name="iq_check_applicable_waivers",
        description=(
            "Prüft ob für eine spezifische Policy-Violation bereits Waivers existieren (aktiv oder abgelaufen). "
            "Zeigt Details bestehender Waivers und empfiehlt ob ein abgelaufener Waiver erneuert "
            "oder ein neuer angelegt werden sollte. Verwende die violationId aus iq_get_violations."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="violation_id",
                type="string",
                description="ID der Policy-Violation (aus iq_get_violations)",
                required=True,
            ),
        ],
        handler=iq_check_applicable_waivers,
    ))
    count += 1

    # ── iq_get_waiver_reasons ──────────────────────────────────────────────────
    async def iq_get_waiver_reasons(**kwargs: Any) -> ToolResult:
        """Holt die vordefinierten Waiver-Reasons."""
        global _waiver_reasons_cache

        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert")

        # Cache nutzen
        if _waiver_reasons_cache is not None:
            return ToolResult(
                success=True,
                data={"reasons": _waiver_reasons_cache, "cached": True},
            )

        result = await _iq_request("GET", "/api/v2/policyWaiverReasons")
        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        reasons_data = result["data"]
        if isinstance(reasons_data, dict):
            reasons_data = reasons_data.get("reasons", reasons_data.get("policyWaiverReasons", []))
        if not isinstance(reasons_data, list):
            reasons_data = []

        reasons = []
        for r in reasons_data:
            reasons.append({
                "id": r.get("id", r.get("reasonId", "")),
                "reason": r.get("reason", r.get("reasonText", str(r))),
            })

        _waiver_reasons_cache = reasons

        return ToolResult(
            success=True,
            data={
                "reasons": reasons,
                "cached": False,
                "hinweis": "Zeige dem Benutzer die Reasons und frage welchen er verwenden möchte.",
            },
        )

    registry.register(Tool(
        name="iq_get_waiver_reasons",
        description=(
            "Holt die vordefinierten Waiver-Begründungen (Reasons) vom IQ Server. "
            "Diese müssen beim Anlegen eines Waivers angegeben werden. "
            "Rufe dies vor iq_create_waiver auf und zeige dem Benutzer die verfügbaren Optionen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[],
        handler=iq_get_waiver_reasons,
    ))
    count += 1

    # ── iq_create_waiver ───────────────────────────────────────────────────────
    async def iq_create_waiver(**kwargs: Any) -> ToolResult:
        """Legt einen Waiver für eine Policy-Violation an (Write-Operation)."""
        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert")

        app_public_id: str = kwargs.get("app_public_id", "").strip()
        if not app_public_id:
            app_public_id = settings.iq_server.default_app
        if not app_public_id:
            return ToolResult(success=False, error="app_public_id ist erforderlich")

        violation_id: str = kwargs.get("violation_id", "").strip()
        if not violation_id:
            return ToolResult(success=False, error="violation_id ist erforderlich")

        comment: str = kwargs.get("comment", "").strip()
        if not comment:
            return ToolResult(success=False, error="comment ist erforderlich - bitte eine Begründung angeben")

        reason_id: str = kwargs.get("reason_id", "").strip()
        if not reason_id:
            return ToolResult(success=False, error="reason_id ist erforderlich - rufe zuerst iq_get_waiver_reasons auf")

        expiry_days: int = int(kwargs.get("expiry_days", settings.iq_server.default_waiver_days))
        matcher_strategy: str = kwargs.get("matcher_strategy", settings.iq_server.default_matcher_strategy)

        # App internalId auflösen
        internal_id = await _resolve_app_id(app_public_id)
        if not internal_id:
            return ToolResult(success=False, error=f"Applikation '{app_public_id}' nicht gefunden")

        # Ablaufdatum berechnen
        expiry_time = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        waiver_body = {
            "comment": comment,
            "expiryTime": expiry_time,
            "matcherStrategy": matcher_strategy,
            "waiverReasonId": reason_id,
        }

        # Bestätigungspflicht?
        if settings.iq_server.require_waiver_confirmation:
            import uuid

            waiver_id = str(uuid.uuid4())[:8]
            _pending_waivers[waiver_id] = {
                "id": waiver_id,
                "app_public_id": app_public_id,
                "app_internal_id": internal_id,
                "violation_id": violation_id,
                "waiver_body": waiver_body,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            return ToolResult(
                success=True,
                requires_confirmation=True,
                confirmation_data={
                    "action": "Waiver anlegen",
                    "app": app_public_id,
                    "violation_id": violation_id,
                    "comment": comment,
                    "matcher_strategy": matcher_strategy,
                    "expiry_days": expiry_days,
                    "expiry_date": expiry_time[:10],
                },
                data={
                    "waiver_request_id": waiver_id,
                    "status": "pending_confirmation",
                    "message": (
                        f"Waiver für Applikation '{app_public_id}' wurde zur Bestätigung vorgemerkt.\n"
                        f"  Violation: {violation_id}\n"
                        f"  Kommentar: {comment}\n"
                        f"  Strategie: {matcher_strategy}\n"
                        f"  Ablauf: {expiry_time[:10]} ({expiry_days} Tage)\n"
                        f"Der Nutzer muss den Waiver bestätigen."
                    ),
                },
            )

        # Direkt ausführen
        result = await _iq_request(
            "POST",
            f"/api/v2/policyWaivers/application/{internal_id}/{violation_id}",
            json_body=waiver_body,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        return ToolResult(
            success=True,
            data={
                "message": f"Waiver wurde erfolgreich angelegt für '{app_public_id}'",
                "violation_id": violation_id,
                "comment": comment,
                "matcher_strategy": matcher_strategy,
                "expiry_date": expiry_time[:10],
            },
        )

    registry.register(Tool(
        name="iq_create_waiver",
        description=(
            "Legt einen Waiver (Ausnahme) für eine Policy-Violation an. ACHTUNG: Schreiboperation - "
            "erfordert Bestätigung durch den Nutzer. "
            "Rufe vorher iq_get_waiver_reasons auf um gültige Reasons zu ermitteln. "
            "Rufe vorher iq_check_applicable_waivers auf um zu prüfen ob bereits ein Waiver existiert."
        ),
        category=ToolCategory.DEVOPS,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="app_public_id",
                type="string",
                description="Public-ID der Applikation (leer = default_app aus Settings)",
                required=False,
            ),
            ToolParameter(
                name="violation_id",
                type="string",
                description="ID der Policy-Violation (aus iq_get_violations)",
                required=True,
            ),
            ToolParameter(
                name="comment",
                type="string",
                description="Begründung für den Waiver (wird im IQ Server gespeichert)",
                required=True,
            ),
            ToolParameter(
                name="reason_id",
                type="string",
                description="ID des Waiver-Reasons (aus iq_get_waiver_reasons)",
                required=True,
            ),
            ToolParameter(
                name="expiry_days",
                type="integer",
                description="Ablauf in Tagen ab jetzt (Standard: 90)",
                required=False,
                default=90,
            ),
            ToolParameter(
                name="matcher_strategy",
                type="string",
                description="Matching-Strategie: EXACT_COMPONENT (exakte Version), ALL_VERSIONS (alle Versionen), ALL_COMPONENTS (alle Komponenten)",
                required=False,
                default="EXACT_COMPONENT",
                enum=["EXACT_COMPONENT", "ALL_VERSIONS", "ALL_COMPONENTS"],
            ),
        ],
        handler=iq_create_waiver,
    ))
    count += 1

    # ── iq_component_details ───────────────────────────────────────────────────
    async def iq_component_details(**kwargs: Any) -> ToolResult:
        """Holt Details zu einer Komponente (Vulnerabilities, Versionen)."""
        if not settings.iq_server.enabled:
            return ToolResult(success=False, error="Sonatype IQ Server ist nicht aktiviert")

        comp_format: str = kwargs.get("format", "maven")
        group_id: str = kwargs.get("group_id", "").strip()
        artifact_id: str = kwargs.get("artifact_id", "").strip()
        version: str = kwargs.get("version", "").strip()

        if not artifact_id:
            return ToolResult(success=False, error="artifact_id ist erforderlich")

        coordinates = {"artifactId": artifact_id}
        if group_id:
            coordinates["groupId"] = group_id
        if version:
            coordinates["version"] = version

        body = {
            "componentIdentifier": {
                "format": comp_format,
                "coordinates": coordinates,
            }
        }

        result = await _iq_request("POST", "/api/v2/components/details", json_body=body)
        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        return ToolResult(
            success=True,
            data=result["data"],
        )

    registry.register(Tool(
        name="iq_component_details",
        description=(
            "Holt Details zu einer Komponente vom IQ Server: bekannte Vulnerabilities, "
            "verfügbare Versionen, Lizenzinformationen. "
            "Verwende dies um zu prüfen ob eine neuere, sichere Version verfügbar ist."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="format",
                type="string",
                description="Paket-Format (maven, npm, pypi, nuget, etc.)",
                required=False,
                default="maven",
            ),
            ToolParameter(
                name="group_id",
                type="string",
                description="Group-ID (Maven: z.B. org.apache.logging.log4j)",
                required=False,
            ),
            ToolParameter(
                name="artifact_id",
                type="string",
                description="Artifact-ID (z.B. log4j-core)",
                required=True,
            ),
            ToolParameter(
                name="version",
                type="string",
                description="Version (z.B. 2.14.1, leer = alle Versionen)",
                required=False,
            ),
        ],
        handler=iq_component_details,
    ))
    count += 1

    return count


# ══════════════════════════════════════════════════════════════════════════════
# Pending-Waiver-Verwaltung (für API-Endpunkte)
# ══════════════════════════════════════════════════════════════════════════════

def get_pending_waivers() -> Dict[str, dict]:
    """Gibt pending Waiver-Requests zurück (für API-Endpunkt)."""
    return _pending_waivers


async def confirm_waiver(waiver_request_id: str) -> Dict[str, Any]:
    """Bestätigt und führt einen pending Waiver aus."""
    if waiver_request_id not in _pending_waivers:
        return {"success": False, "error": "Waiver-Request-ID nicht gefunden"}

    entry = _pending_waivers[waiver_request_id]
    if entry["status"] != "pending":
        return {"success": False, "error": f"Waiver ist bereits {entry['status']}"}

    result = await _iq_request(
        "POST",
        f"/api/v2/policyWaivers/application/{entry['app_internal_id']}/{entry['violation_id']}",
        json_body=entry["waiver_body"],
    )

    if result["success"]:
        entry["status"] = "confirmed"
        return {
            "success": True,
            "message": f"Waiver für '{entry['app_public_id']}' wurde erfolgreich angelegt",
        }
    else:
        entry["status"] = "failed"
        entry["error"] = result["error"]
        return {"success": False, "error": result["error"]}


def reject_waiver(waiver_request_id: str) -> Dict[str, Any]:
    """Lehnt einen pending Waiver ab."""
    if waiver_request_id not in _pending_waivers:
        return {"success": False, "error": "Waiver-Request-ID nicht gefunden"}

    entry = _pending_waivers[waiver_request_id]
    entry["status"] = "rejected"
    return {"success": True, "message": "Waiver wurde abgelehnt"}
