"""
Agent-Tools für WLP (WebSphere Liberty Profile) Server-Verwaltung.

Tools:
  Server-Control:
    - wlp_list_servers      → Konfigurierte Server auflisten
    - wlp_server_start      → Server starten
    - wlp_server_stop       → Server stoppen
    - wlp_server_status     → Server-Status abfragen

  Config-Management:
    - wlp_config_read       → server.xml strukturiert lesen
    - wlp_config_features   → Aktivierte Features auflisten
    - wlp_config_validate   → Feature-Kompatibilität prüfen
    - wlp_config_edit       → Config ändern (mit Bestätigung)

  Log-Analyse:
    - wlp_get_logs          → Log-Datei lesen (tail)
    - wlp_log_errors        → Fehler extrahieren und erklären
    - wlp_log_suggest_fix   → Fix-Vorschlag für Fehlercode
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Known WLP Error Codes - Erklärungen und Fix-Vorschläge
# ══════════════════════════════════════════════════════════════════════════════

WLP_ERROR_CODES: Dict[str, Dict[str, str]] = {
    # Application Errors
    "CWWKZ0001I": {
        "severity": "INFO",
        "meaning": "Application started successfully",
        "fix": None,
    },
    "CWWKZ0002E": {
        "severity": "ERROR",
        "meaning": "App konnte nicht gestartet werden - Abhängigkeit fehlt oder Deployment-Fehler",
        "fix": "Prüfe ob alle benötigten JARs im lib-Ordner liegen und ob die web.xml korrekt ist",
    },
    "CWWKZ0003I": {
        "severity": "INFO",
        "meaning": "Application updated",
        "fix": None,
    },
    "CWWKZ0009I": {
        "severity": "INFO",
        "meaning": "Application stopped",
        "fix": None,
    },
    "CWWKZ0013E": {
        "severity": "ERROR",
        "meaning": "App konnte nicht gestartet werden - ClassNotFoundException oder NoClassDefFoundError",
        "fix": "Prüfe Classpath: fehlt eine Abhängigkeit in pom.xml oder im WAR/EAR?",
    },
    "CWWKZ0014W": {
        "severity": "WARNING",
        "meaning": "Application not found at configured location",
        "fix": "Prüfe server.xml: stimmt der location-Pfad? Liegt das WAR/EAR dort?",
    },

    # Feature Errors
    "CWWKF0001I": {
        "severity": "INFO",
        "meaning": "Feature successfully installed",
        "fix": None,
    },
    "CWWKF0011E": {
        "severity": "ERROR",
        "meaning": "Feature konnte nicht installiert werden - möglicherweise Konflikt",
        "fix": "Prüfe Feature-Kompatibilität: javax.* und jakarta.* Features sind nicht kompatibel",
    },
    "CWWKF0012I": {
        "severity": "INFO",
        "meaning": "Server installed features",
        "fix": None,
    },
    "CWWKF0032E": {
        "severity": "ERROR",
        "meaning": "Feature not found in repository",
        "fix": "Prüfe Feature-Name auf Tippfehler oder verwende featureManager install",
    },

    # Kernel Errors
    "CWWKE0701E": {
        "severity": "ERROR",
        "meaning": "Bundle konnte nicht aufgelöst werden - ein benötigtes Feature fehlt",
        "fix": "Aktiviere das fehlende Feature in server.xml unter <featureManager>",
    },
    "CWWKE0702E": {
        "severity": "ERROR",
        "meaning": "Bundle start failed",
        "fix": "Prüfe Logs auf vorherige Fehler - meist ist ein anderes Bundle die Ursache",
    },

    # DataSource Errors
    "CWWKE0819E": {
        "severity": "ERROR",
        "meaning": "DataSource-Verbindung fehlgeschlagen",
        "fix": "Prüfe: 1) DB erreichbar? 2) Credentials korrekt? 3) JDBC-Treiber installiert?",
    },
    "J2CA0045E": {
        "severity": "ERROR",
        "meaning": "Connection Pool erschöpft",
        "fix": "Erhöhe maxPoolSize in server.xml oder prüfe auf Connection Leaks",
    },

    # Server Lifecycle
    "CWWKF0011I": {
        "severity": "INFO",
        "meaning": "Server is ready to run a smarter planet",
        "fix": None,
    },
    "CWWKE0036I": {
        "severity": "INFO",
        "meaning": "Server stopped",
        "fix": None,
    },
}

# Feature-Kompatibilitätsmatrix (inkompatible Kombinationen)
INCOMPATIBLE_FEATURES: List[tuple] = [
    # javax vs jakarta (EE8 vs EE9+)
    (r"servlet-[345]\.", r"servlet-[6]\."),
    (r"jpa-2\.[012]", r"persistence-3\."),
    (r"ejb-3\.[12]", r"enterpriseBeans-4\."),
    (r"cdi-[12]\.", r"cdi-[34]\."),
    (r"jaxrs-2\.", r"restfulWS-3\."),
    (r"jaxb-2\.", r"xmlBinding-[34]\."),
    (r"jsonp-1\.", r"jsonp-2\."),
    (r"jsonb-1\.", r"jsonb-[23]\."),
    # MicroProfile Versionen
    (r"mpConfig-1\.", r"mpConfig-[23]\."),
    (r"mpHealth-[12]\.", r"mpHealth-[34]\."),
]


# ══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════════════════════════

def _read_lines_sync(path: str) -> list:
    """Synchrones Datei-Lesen für run_in_executor."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _parse_server_xml(xml_path: Path) -> Dict[str, Any]:
    """
    Parst server.xml und extrahiert strukturierte Informationen.

    Returns:
        Dict mit features, applications, datasources, etc.
    """
    if not xml_path.exists():
        return {"error": f"server.xml nicht gefunden: {xml_path}"}

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError as e:
        return {"error": f"XML-Parsefehler: {e}"}

    result = {
        "path": str(xml_path),
        "features": [],
        "applications": [],
        "datasources": [],
        "jndi_entries": [],
        "http_endpoint": None,
        "includes": [],
        "variables": {},
    }

    # Features extrahieren
    for fm in root.iter("featureManager"):
        for feature in fm.iter("feature"):
            if feature.text:
                result["features"].append(feature.text.strip())

    # Applications extrahieren
    for tag in ("application", "webApplication", "enterpriseApplication"):
        for el in root.iter(tag):
            app = {
                "type": tag,
                "id": el.get("id", ""),
                "name": el.get("name", ""),
                "location": el.get("location", ""),
                "context_root": el.get("context-root", el.get("contextRoot", "")),
            }
            result["applications"].append(app)

    # DataSources extrahieren
    for ds in root.iter("dataSource"):
        datasource = {
            "id": ds.get("id", ""),
            "jndiName": ds.get("jndiName", ""),
            "type": ds.get("type", ""),
        }
        # JDBC-Properties
        for props in ds.iter("properties"):
            datasource["properties"] = dict(props.attrib)
        # Connection Manager
        for cm in ds.iter("connectionManager"):
            datasource["connectionManager"] = dict(cm.attrib)
        result["datasources"].append(datasource)

    # JNDI Entries
    for jndi in root.iter("jndiEntry"):
        result["jndi_entries"].append({
            "jndiName": jndi.get("jndiName", ""),
            "value": jndi.get("value", ""),
        })

    # HTTP Endpoint
    for http in root.iter("httpEndpoint"):
        result["http_endpoint"] = {
            "id": http.get("id", ""),
            "host": http.get("host", "*"),
            "httpPort": http.get("httpPort", "9080"),
            "httpsPort": http.get("httpsPort", "9443"),
        }
        break

    # Includes
    for inc in root.iter("include"):
        result["includes"].append(inc.get("location", ""))

    # Variables
    for var in root.iter("variable"):
        name = var.get("name", "")
        value = var.get("value", var.get("defaultValue", ""))
        if name:
            result["variables"][name] = value

    return result


def _check_feature_compatibility(features: List[str]) -> List[Dict[str, str]]:
    """
    Prüft Feature-Kompatibilität und findet Konflikte.

    Returns:
        Liste von Konflikten mit betroffenen Features
    """
    conflicts = []

    for pattern_a, pattern_b in INCOMPATIBLE_FEATURES:
        re_a = re.compile(pattern_a)
        re_b = re.compile(pattern_b)

        matches_a = [f for f in features if re_a.search(f)]
        matches_b = [f for f in features if re_b.search(f)]

        if matches_a and matches_b:
            conflicts.append({
                "type": "incompatible_features",
                "features_a": matches_a,
                "features_b": matches_b,
                "message": f"Features {matches_a} und {matches_b} sind nicht kompatibel (javax vs jakarta)",
            })

    return conflicts


def _extract_errors_from_log(lines: List[str]) -> List[Dict[str, Any]]:
    """
    Extrahiert WLP-Fehler aus Log-Zeilen.

    Returns:
        Liste von Fehlern mit Code, Severity, Message und Erklärung
    """
    errors = []
    error_pattern = re.compile(r"\[(ERROR|WARNING|AUDIT|INFO)\s*\].*?(CWW[A-Z]{2}\d{4}[EIWA])")
    exception_pattern = re.compile(r"([\w.]+Exception|[\w.]+Error):\s*(.+)")

    for i, line in enumerate(lines):
        # WLP Error Codes
        match = error_pattern.search(line)
        if match:
            severity = match.group(1)
            code = match.group(2)

            error_info = WLP_ERROR_CODES.get(code, {})
            errors.append({
                "line_number": i + 1,
                "code": code,
                "severity": severity,
                "raw_line": line.strip(),
                "meaning": error_info.get("meaning", "Unbekannter Fehlercode"),
                "fix": error_info.get("fix"),
            })

        # Java Exceptions
        exc_match = exception_pattern.search(line)
        if exc_match and "Exception" in line or "Error" in line:
            # Nur wenn nicht schon als WLP-Code erfasst
            if not match:
                errors.append({
                    "line_number": i + 1,
                    "code": "JAVA_EXCEPTION",
                    "severity": "ERROR",
                    "raw_line": line.strip(),
                    "exception_type": exc_match.group(1),
                    "exception_message": exc_match.group(2)[:200],
                    "meaning": f"Java Exception: {exc_match.group(1)}",
                    "fix": "Prüfe Stack-Trace für Details",
                })

    return errors


async def _run_server_command(
    wlp_path: str,
    server_name: str,
    command: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Führt einen WLP server-Befehl aus (start/stop/status).

    Args:
        wlp_path: Pfad zum WLP-Installationsverzeichnis
        server_name: Name des Servers
        command: start|stop|status
        timeout: Timeout in Sekunden

    Returns:
        Dict mit success, output, error
    """
    import os

    is_windows = os.name == 'nt'

    if is_windows:
        server_script = Path(wlp_path) / "bin" / "server.bat"
        cmd = ["cmd.exe", "/c", str(server_script), command, server_name]
    else:
        server_script = Path(wlp_path) / "bin" / "server"
        cmd = [str(server_script), command, server_name]

    if not server_script.exists():
        return {
            "success": False,
            "error": f"WLP server-Skript nicht gefunden: {server_script}",
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=wlp_path,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": output,
            "command": " ".join(cmd),
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": f"Timeout nach {timeout} Sekunden"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# Tool Registration
# ══════════════════════════════════════════════════════════════════════════════

def register_wlp_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    if not settings.wlp.enabled:
        return 0

    count = 0

    # ══════════════════════════════════════════════════════════════════════════
    # SERVER CONTROL TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    # ── wlp_list_servers ──────────────────────────────────────────────────────
    async def wlp_list_servers(**kwargs: Any) -> ToolResult:
        """Listet alle konfigurierten WLP-Server auf."""
        from app.api.routes.wlp import _running_processes

        servers = []
        for s in settings.wlp.servers:
            server_info = {
                "id": s.id,
                "name": s.name,
                "server_name": s.server_name,
                "wlp_path": s.wlp_path,
                "is_running": s.id in _running_processes,
            }

            # Prüfe ob server.xml existiert
            server_xml = Path(s.wlp_path) / "usr" / "servers" / s.server_name / "server.xml"
            server_info["config_exists"] = server_xml.exists()

            servers.append(server_info)

        if not servers:
            return ToolResult(
                success=True,
                data={
                    "servers": [],
                    "message": "Keine WLP-Server konfiguriert. Füge Server über das Frontend hinzu.",
                }
            )

        return ToolResult(success=True, data={"servers": servers, "count": len(servers)})

    registry.register(Tool(
        name="wlp_list_servers",
        description=(
            "Listet alle konfigurierten WLP-Server auf mit Status (läuft/gestoppt). "
            "Nutze dies um die server_id für andere WLP-Tools zu ermitteln."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=wlp_list_servers,
    ))
    count += 1

    # ── wlp_server_start ──────────────────────────────────────────────────────
    async def wlp_server_start(**kwargs: Any) -> ToolResult:
        """Startet einen WLP-Server."""
        from app.api.routes.wlp import _running_processes

        server_id: str = kwargs.get("server_id", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        # Prüfe ob bereits läuft
        if server_id in _running_processes:
            proc = _running_processes[server_id]
            if proc.returncode is None:
                return ToolResult(
                    success=True,
                    data={"message": f"Server '{srv.name}' läuft bereits", "already_running": True}
                )

        result = await _run_server_command(
            srv.wlp_path, srv.server_name, "start", timeout=srv.start_timeout_seconds
        )

        if result["success"]:
            return ToolResult(
                success=True,
                data={
                    "message": f"Server '{srv.name}' wird gestartet",
                    "output": result.get("output", ""),
                    "hint": "Nutze wlp_server_status um den Startstatus zu prüfen",
                }
            )
        else:
            return ToolResult(success=False, error=result.get("error", "Start fehlgeschlagen"))

    registry.register(Tool(
        name="wlp_server_start",
        description=(
            "Startet einen WLP-Server. Der Start erfolgt im Hintergrund. "
            "Nutze wlp_server_status um zu prüfen ob der Server bereit ist."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers (aus wlp_list_servers)",
                required=True,
            ),
        ],
        handler=wlp_server_start,
    ))
    count += 1

    # ── wlp_server_stop ───────────────────────────────────────────────────────
    async def wlp_server_stop(**kwargs: Any) -> ToolResult:
        """Stoppt einen WLP-Server."""
        server_id: str = kwargs.get("server_id", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        result = await _run_server_command(srv.wlp_path, srv.server_name, "stop", timeout=60)

        if result["success"]:
            return ToolResult(
                success=True,
                data={
                    "message": f"Server '{srv.name}' gestoppt",
                    "output": result.get("output", ""),
                }
            )
        else:
            # Stop kann auch erfolgreich sein wenn Server nicht lief
            if "not running" in result.get("output", "").lower():
                return ToolResult(
                    success=True,
                    data={"message": f"Server '{srv.name}' war bereits gestoppt"}
                )
            return ToolResult(success=False, error=result.get("error", "Stop fehlgeschlagen"))

    registry.register(Tool(
        name="wlp_server_stop",
        description="Stoppt einen laufenden WLP-Server.",
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers",
                required=True,
            ),
        ],
        handler=wlp_server_stop,
    ))
    count += 1

    # ── wlp_server_status ─────────────────────────────────────────────────────
    async def wlp_server_status(**kwargs: Any) -> ToolResult:
        """Prüft den Status eines WLP-Servers."""
        from app.api.routes.wlp import _running_processes

        server_id: str = kwargs.get("server_id", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        result = await _run_server_command(srv.wlp_path, srv.server_name, "status", timeout=30)

        # Status-Output parsen
        output = result.get("output", "")
        is_running = "is running" in output.lower() or server_id in _running_processes

        # PID extrahieren wenn vorhanden
        pid = None
        pid_match = re.search(r"process id[:\s]+(\d+)", output, re.IGNORECASE)
        if pid_match:
            pid = int(pid_match.group(1))

        return ToolResult(
            success=True,
            data={
                "server_id": server_id,
                "server_name": srv.name,
                "is_running": is_running,
                "pid": pid,
                "output": output,
            }
        )

    registry.register(Tool(
        name="wlp_server_status",
        description="Prüft ob ein WLP-Server läuft und gibt Status-Informationen zurück.",
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers",
                required=True,
            ),
        ],
        handler=wlp_server_status,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # CONFIG MANAGEMENT TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    # ── wlp_config_read ───────────────────────────────────────────────────────
    async def wlp_config_read(**kwargs: Any) -> ToolResult:
        """Liest server.xml und gibt strukturierte Informationen zurück."""
        server_id: str = kwargs.get("server_id", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        xml_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "server.xml"
        parsed = _parse_server_xml(xml_path)

        if "error" in parsed:
            return ToolResult(success=False, error=parsed["error"])

        return ToolResult(success=True, data=parsed)

    registry.register(Tool(
        name="wlp_config_read",
        description=(
            "Liest die server.xml eines WLP-Servers und gibt strukturierte Informationen zurück: "
            "Features, Applications, DataSources, HTTP-Endpoint, Variables. "
            "Nutze dies um die aktuelle Konfiguration zu verstehen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers",
                required=True,
            ),
        ],
        handler=wlp_config_read,
    ))
    count += 1

    # ── wlp_config_features ───────────────────────────────────────────────────
    async def wlp_config_features(**kwargs: Any) -> ToolResult:
        """Listet alle aktivierten Features eines WLP-Servers auf."""
        server_id: str = kwargs.get("server_id", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        xml_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "server.xml"
        parsed = _parse_server_xml(xml_path)

        if "error" in parsed:
            return ToolResult(success=False, error=parsed["error"])

        features = parsed.get("features", [])

        # Kategorisiere Features
        categories = {
            "web": [],
            "persistence": [],
            "security": [],
            "messaging": [],
            "microprofile": [],
            "other": [],
        }

        for f in features:
            f_lower = f.lower()
            if any(x in f_lower for x in ["servlet", "jsp", "jstl", "websocket", "faces"]):
                categories["web"].append(f)
            elif any(x in f_lower for x in ["jpa", "jdbc", "persistence", "datasource"]):
                categories["persistence"].append(f)
            elif any(x in f_lower for x in ["security", "ssl", "appSecurity", "ldap"]):
                categories["security"].append(f)
            elif any(x in f_lower for x in ["jms", "mq", "messaging"]):
                categories["messaging"].append(f)
            elif f_lower.startswith("mp"):
                categories["microprofile"].append(f)
            else:
                categories["other"].append(f)

        return ToolResult(
            success=True,
            data={
                "total_count": len(features),
                "features": features,
                "by_category": {k: v for k, v in categories.items() if v},
            }
        )

    registry.register(Tool(
        name="wlp_config_features",
        description=(
            "Listet alle aktivierten Features eines WLP-Servers auf, kategorisiert nach "
            "Web, Persistence, Security, Messaging, MicroProfile."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers",
                required=True,
            ),
        ],
        handler=wlp_config_features,
    ))
    count += 1

    # ── wlp_config_validate ───────────────────────────────────────────────────
    async def wlp_config_validate(**kwargs: Any) -> ToolResult:
        """
        Validiert die server.xml auf Feature-Kompatibilität und prüft Artefakte.
        """
        server_id: str = kwargs.get("server_id", "")
        add_feature: str = kwargs.get("add_feature", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        xml_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "server.xml"
        parsed = _parse_server_xml(xml_path)

        if "error" in parsed:
            return ToolResult(success=False, error=parsed["error"])

        features = parsed.get("features", [])

        # Wenn neues Feature geprüft werden soll
        if add_feature:
            features = features + [add_feature]

        # Feature-Kompatibilität prüfen
        conflicts = _check_feature_compatibility(features)

        # Artefakte prüfen
        artifact_issues = []
        server_dir = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name

        for app in parsed.get("applications", []):
            loc = app.get("location", "")
            if loc:
                # Absoluter oder relativer Pfad
                if Path(loc).is_absolute():
                    artifact_path = Path(loc)
                else:
                    artifact_path = server_dir / "apps" / loc
                    if not artifact_path.exists():
                        artifact_path = server_dir / "dropins" / loc

                if not artifact_path.exists():
                    artifact_issues.append({
                        "app": app.get("name") or app.get("id") or loc,
                        "expected_path": str(artifact_path),
                        "issue": "Artefakt nicht gefunden",
                    })

        is_valid = len(conflicts) == 0 and len(artifact_issues) == 0

        result = {
            "valid": is_valid,
            "feature_count": len(features),
            "conflicts": conflicts,
            "artifact_issues": artifact_issues,
        }

        if add_feature:
            result["checked_feature"] = add_feature
            if conflicts:
                result["recommendation"] = f"Feature '{add_feature}' würde Konflikte verursachen"
            else:
                result["recommendation"] = f"Feature '{add_feature}' kann hinzugefügt werden"

        return ToolResult(success=True, data=result)

    registry.register(Tool(
        name="wlp_config_validate",
        description=(
            "Validiert die server.xml eines WLP-Servers: "
            "Prüft Feature-Kompatibilität (javax vs jakarta) und ob Artefakte vorhanden sind. "
            "Optional kann mit add_feature geprüft werden ob ein neues Feature kompatibel wäre."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers",
                required=True,
            ),
            ToolParameter(
                name="add_feature",
                type="string",
                description="Optional: Neues Feature dessen Kompatibilität geprüft werden soll",
                required=False,
            ),
        ],
        handler=wlp_config_validate,
    ))
    count += 1

    # ── wlp_config_edit ───────────────────────────────────────────────────────
    async def wlp_config_edit(**kwargs: Any) -> ToolResult:
        """
        Bearbeitet die server.xml (mit Bestätigung).

        Unterstützt:
        - add_feature: Feature hinzufügen
        - remove_feature: Feature entfernen
        - set_variable: Variable setzen
        """
        server_id: str = kwargs.get("server_id", "")
        add_feature: str = kwargs.get("add_feature", "")
        remove_feature: str = kwargs.get("remove_feature", "")
        set_variable_name: str = kwargs.get("set_variable_name", "")
        set_variable_value: str = kwargs.get("set_variable_value", "")

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        xml_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "server.xml"

        if not xml_path.exists():
            return ToolResult(success=False, error=f"server.xml nicht gefunden: {xml_path}")

        # Original lesen
        original_content = xml_path.read_text(encoding="utf-8")

        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
        except ET.ParseError as e:
            return ToolResult(success=False, error=f"XML-Parsefehler: {e}")

        changes = []

        # Feature hinzufügen
        if add_feature:
            fm = root.find("featureManager")
            if fm is None:
                fm = ET.SubElement(root, "featureManager")

            # Prüfen ob Feature schon existiert
            existing = [f.text for f in fm.findall("feature") if f.text]
            if add_feature not in existing:
                new_feature = ET.SubElement(fm, "feature")
                new_feature.text = add_feature
                changes.append(f"Feature '{add_feature}' hinzugefügt")
            else:
                changes.append(f"Feature '{add_feature}' war bereits vorhanden")

        # Feature entfernen
        if remove_feature:
            fm = root.find("featureManager")
            if fm is not None:
                for f in fm.findall("feature"):
                    if f.text and f.text.strip() == remove_feature:
                        fm.remove(f)
                        changes.append(f"Feature '{remove_feature}' entfernt")
                        break

        # Variable setzen
        if set_variable_name:
            # Existierende Variable finden oder neue erstellen
            found = False
            for var in root.findall("variable"):
                if var.get("name") == set_variable_name:
                    old_value = var.get("value", var.get("defaultValue", ""))
                    var.set("value", set_variable_value)
                    changes.append(f"Variable '{set_variable_name}' geändert: {old_value} → {set_variable_value}")
                    found = True
                    break

            if not found:
                new_var = ET.SubElement(root, "variable")
                new_var.set("name", set_variable_name)
                new_var.set("value", set_variable_value)
                changes.append(f"Variable '{set_variable_name}' = '{set_variable_value}' hinzugefügt")

        if not changes:
            return ToolResult(success=False, error="Keine Änderung angegeben (add_feature, remove_feature oder set_variable_name)")

        # Neuen Content generieren
        ET.indent(root, space="    ")
        new_content = ET.tostring(root, encoding="unicode", xml_declaration=True)

        # Diff erstellen
        import difflib
        diff = list(difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="server.xml (original)",
            tofile="server.xml (neu)",
        ))
        diff_str = "".join(diff)

        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Änderungen an server.xml:\n{chr(10).join(changes)}",
            confirmation_data={
                "operation": "wlp_config_edit",
                "path": str(xml_path),
                "changes": changes,
                "diff": diff_str,
                "new_content": new_content,
            }
        )

    registry.register(Tool(
        name="wlp_config_edit",
        description=(
            "Bearbeitet die server.xml eines WLP-Servers. "
            "WICHTIG: Änderungen werden erst nach User-Bestätigung angewendet. "
            "Unterstützt: add_feature, remove_feature, set_variable_name/set_variable_value."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers",
                required=True,
            ),
            ToolParameter(
                name="add_feature",
                type="string",
                description="Feature das hinzugefügt werden soll (z.B. 'servlet-4.0')",
                required=False,
            ),
            ToolParameter(
                name="remove_feature",
                type="string",
                description="Feature das entfernt werden soll",
                required=False,
            ),
            ToolParameter(
                name="set_variable_name",
                type="string",
                description="Name der Variable die gesetzt werden soll",
                required=False,
            ),
            ToolParameter(
                name="set_variable_value",
                type="string",
                description="Wert der Variable",
                required=False,
            ),
        ],
        handler=wlp_config_edit,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # LOG ANALYSIS TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    # ── wlp_get_logs ──────────────────────────────────────────────────────────
    async def wlp_get_logs(**kwargs: Any) -> ToolResult:
        """Liest die letzten Zeilen aus messages.log."""
        server_id: str = kwargs.get("server_id", "")

        try:
            lines_raw = int(kwargs.get("lines", 100))
            lines: int = max(1, min(lines_raw, 10000))
        except (ValueError, TypeError):
            lines = 100

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        log_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "logs" / "messages.log"
        if not log_path.exists():
            return ToolResult(success=False, error=f"messages.log nicht gefunden: {log_path}")

        try:
            loop = asyncio.get_event_loop()
            all_lines = await loop.run_in_executor(None, _read_lines_sync, str(log_path))
        except Exception as e:
            logger.warning(f"WLP Log-Datei lesen fehlgeschlagen: {e}")
            return ToolResult(success=False, error=f"Log-Datei lesen fehlgeschlagen: {e}")

        tail = all_lines[-lines:]
        return ToolResult(success=True, data={
            "log_path": str(log_path),
            "total_lines": len(all_lines),
            "returned_lines": len(tail),
            "content": "".join(tail),
        })

    registry.register(Tool(
        name="wlp_get_logs",
        description=(
            "Liest die letzten Zeilen aus messages.log eines WLP-Servers. "
            "Für Fehleranalyse nutze besser wlp_log_errors."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des WLP-Servers", required=True),
            ToolParameter(name="lines", type="integer", description="Anzahl der letzten Zeilen (Standard: 100, Max: 10000)", required=False),
        ],
        handler=wlp_get_logs,
    ))
    count += 1

    # ── wlp_log_errors ────────────────────────────────────────────────────────
    async def wlp_log_errors(**kwargs: Any) -> ToolResult:
        """Extrahiert Fehler aus dem WLP-Log und erklärt sie."""
        server_id: str = kwargs.get("server_id", "")
        lines: int = min(int(kwargs.get("lines", 500)), 10000)
        severity_filter: str = kwargs.get("severity", "").upper()  # ERROR, WARNING, ALL

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        log_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "logs" / "messages.log"
        if not log_path.exists():
            return ToolResult(success=False, error=f"messages.log nicht gefunden: {log_path}")

        try:
            loop = asyncio.get_event_loop()
            all_lines = await loop.run_in_executor(None, _read_lines_sync, str(log_path))
        except Exception as e:
            return ToolResult(success=False, error=f"Log-Datei lesen fehlgeschlagen: {e}")

        # Letzte N Zeilen analysieren
        tail = all_lines[-lines:]
        errors = _extract_errors_from_log(tail)

        # Filter nach Severity
        if severity_filter and severity_filter != "ALL":
            errors = [e for e in errors if e.get("severity") == severity_filter]

        # Gruppiere nach Error-Code
        by_code: Dict[str, List[Dict]] = {}
        for err in errors:
            code = err.get("code", "UNKNOWN")
            if code not in by_code:
                by_code[code] = []
            by_code[code].append(err)

        summary = {
            "total_errors": len(errors),
            "unique_codes": len(by_code),
            "by_severity": {
                "ERROR": len([e for e in errors if e.get("severity") == "ERROR"]),
                "WARNING": len([e for e in errors if e.get("severity") == "WARNING"]),
            },
            "errors": errors[:50],  # Max 50 Fehler zurückgeben
            "by_code": {code: len(items) for code, items in by_code.items()},
        }

        if errors:
            # Füge Empfehlung für häufigsten Fehler hinzu
            most_common = max(by_code.items(), key=lambda x: len(x[1]))
            first_error = most_common[1][0]
            summary["recommendation"] = {
                "most_common_code": most_common[0],
                "count": len(most_common[1]),
                "meaning": first_error.get("meaning"),
                "fix": first_error.get("fix"),
            }

        return ToolResult(success=True, data=summary)

    registry.register(Tool(
        name="wlp_log_errors",
        description=(
            "Analysiert das WLP-Log und extrahiert Fehler mit Erklärungen. "
            "Erkennt CWWK-Codes, Java-Exceptions und gibt Fix-Vorschläge. "
            "Nutze dies wenn der Server nicht startet oder Probleme auftreten."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des WLP-Servers", required=True),
            ToolParameter(name="lines", type="integer", description="Anzahl der letzten Zeilen zum Analysieren (Standard: 500)", required=False),
            ToolParameter(name="severity", type="string", description="Filter: ERROR, WARNING oder ALL", required=False, enum=["ERROR", "WARNING", "ALL"]),
        ],
        handler=wlp_log_errors,
    ))
    count += 1

    # ── wlp_log_suggest_fix ───────────────────────────────────────────────────
    async def wlp_log_suggest_fix(**kwargs: Any) -> ToolResult:
        """Gibt einen Fix-Vorschlag für einen WLP-Fehlercode."""
        error_code: str = kwargs.get("error_code", "").upper()

        if not error_code:
            return ToolResult(success=False, error="error_code ist erforderlich")

        # Normalisiere Code (entferne Leerzeichen)
        error_code = error_code.strip()

        if error_code in WLP_ERROR_CODES:
            info = WLP_ERROR_CODES[error_code]
            return ToolResult(
                success=True,
                data={
                    "code": error_code,
                    "severity": info.get("severity"),
                    "meaning": info.get("meaning"),
                    "fix": info.get("fix"),
                    "known": True,
                }
            )
        else:
            # Versuche teilweise Übereinstimmung
            partial_matches = [
                (code, info) for code, info in WLP_ERROR_CODES.items()
                if error_code[:6] in code  # Gleiche Prefix (z.B. CWWKZ0)
            ]

            if partial_matches:
                return ToolResult(
                    success=True,
                    data={
                        "code": error_code,
                        "known": False,
                        "message": f"Code '{error_code}' nicht in Datenbank, aber ähnliche Codes gefunden",
                        "similar_codes": [
                            {"code": code, "meaning": info.get("meaning")}
                            for code, info in partial_matches[:5]
                        ],
                        "hint": "Nutze Web-Suche für Details zu diesem Fehlercode",
                    }
                )

            return ToolResult(
                success=True,
                data={
                    "code": error_code,
                    "known": False,
                    "message": f"Code '{error_code}' nicht bekannt",
                    "hint": "Suche nach dem Code in der IBM Knowledge Center Dokumentation",
                }
            )

    registry.register(Tool(
        name="wlp_log_suggest_fix",
        description=(
            "Gibt Erklärung und Fix-Vorschlag für einen WLP-Fehlercode (z.B. CWWKZ0013E). "
            "Nutze dies um schnell zu verstehen was ein Fehler bedeutet."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="error_code",
                type="string",
                description="WLP-Fehlercode (z.B. CWWKZ0013E, CWWKE0701E)",
                required=True,
            ),
        ],
        handler=wlp_log_suggest_fix,
    ))
    count += 1

    logger.info(f"[wlp_tools] {count} WLP-Tools registriert")
    return count
