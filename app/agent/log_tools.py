"""
Agent-Tools für Remote-Log-Server-Zugriff (ospe_ope.log).
NICHT für lokale WLP-Server-Logs – dafür gibt es wlp_read_log / wlp_read_ffdc.
Wird beim Startup registriert wenn log_servers aktiviert ist.
"""

import asyncio
import re
from collections import Counter
from typing import Any, Dict, List

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

# Max Zeichen pro Server-Content im Tool-Result (verhindert Kontextfenster-Überflutung)
_MAX_CONTENT_CHARS = 30_000
_MAX_LINES_PER_SERVER = 2000

# Regex für Log-Level-Erkennung
_LOG_LEVEL_RE = re.compile(r"\b(FATAL|ERROR|WARN(?:ING)?|SEVERE|EXCEPTION)\b", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})"
)


def _extract_error_summary(content: str, server_name: str) -> Dict[str, Any]:
    """Extrahiert eine Fehler-Zusammenfassung aus Log-Content."""
    lines = content.splitlines()
    level_counts: Counter = Counter()
    errors: List[Dict[str, str]] = []

    for line in lines:
        m = _LOG_LEVEL_RE.search(line)
        if not m:
            continue
        level = m.group(1).upper()
        if level == "WARNING":
            level = "WARN"
        level_counts[level] += 1

        # Nur ERROR/FATAL/SEVERE/EXCEPTION als Fehler-Einträge sammeln (max 50)
        if level in ("ERROR", "FATAL", "SEVERE", "EXCEPTION") and len(errors) < 50:
            ts_match = _TIMESTAMP_RE.search(line)
            timestamp = ts_match.group(1) if ts_match else ""
            # Erste 200 Zeichen der Zeile als Message
            msg = line.strip()[:200]
            errors.append({"timestamp": timestamp, "server": server_name, "level": level, "message": msg})

    return {
        "level_counts": dict(level_counts),
        "total_errors": sum(v for k, v in level_counts.items() if k in ("ERROR", "FATAL", "SEVERE", "EXCEPTION")),
        "total_warnings": level_counts.get("WARN", 0),
        "error_entries": errors,
    }


def _read_file_sync(path: str) -> str:
    """Synchrones Datei-Lesen für run_in_executor."""
    with open(path, "r", errors="replace") as f:
        return f.read()


def register_log_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    if not settings.log_servers.enabled:
        return 0

    count = 0

    # ── log_list_stages ───────────────────────────────────────────────────────
    async def log_list_stages(**kwargs: Any) -> ToolResult:
        stages = [
            {
                "id": s.id,
                "name": s.name,
                "server_count": len(s.servers),
                "servers": [{"id": srv.id, "name": srv.name, "url": srv.url} for srv in s.servers],
            }
            for s in settings.log_servers.stages
        ]
        return ToolResult(success=True, data={"stages": stages})

    registry.register(Tool(
        name="log_list_stages",
        description=(
            "Listet alle konfigurierten Remote-Log-Server-Stages auf (OSPE-Server, NICHT lokale WLP-Server). "
            "Gibt Stage-IDs und Server-IDs zurück. Diese IDs werden für log_download_stage und "
            "log_search_stage benötigt. Jede Stage enthält mehrere Remote-Server die per HTTP "
            "Login (JSESSIONID) abgefragt werden."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=log_list_stages,
    ))
    count += 1

    # ── log_download_stage ────────────────────────────────────────────────────
    async def log_download_stage(**kwargs: Any) -> ToolResult:
        from app.api.routes.log_servers import _fetch_server_logs

        stage_id: str = kwargs.get("stage_id", "")
        server_id: str = kwargs.get("server_id", "")
        search_term: str = kwargs.get("search_term", "")

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")
        if not stage.servers:
            return ToolResult(success=False, error="Stage hat keine Server konfiguriert")

        # Server-Auswahl: einzelner Server oder alle
        if server_id:
            servers_to_try = [s for s in stage.servers if s.id == server_id]
            if not servers_to_try:
                return ToolResult(success=False, error=f"Server '{server_id}' nicht in Stage '{stage.name}' gefunden")
        else:
            servers_to_try = stage.servers

        tail = settings.log_servers.default_tail
        results = []

        for server in servers_to_try:
            try:
                result = await _fetch_server_logs(server, tail)
                if not result.success:
                    results.append({
                        "server_id": server.id,
                        "server": server.name,
                        "success": False,
                        "error": result.error,
                    })
                    continue

                lines = result.content.splitlines()
                total_lines = len(lines)

                if search_term:
                    term_lower = search_term.lower()
                    lines = [l for l in lines if term_lower in l.lower()]

                truncated = len(lines) > _MAX_LINES_PER_SERVER
                if truncated:
                    lines = lines[-_MAX_LINES_PER_SERVER:]

                content = "\n".join(lines)
                if len(content) > _MAX_CONTENT_CHARS:
                    content = content[-_MAX_CONTENT_CHARS:]
                    truncated = True

                # Fehler-Zusammenfassung extrahieren
                err_summary = _extract_error_summary(result.content, server.name)

                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": True,
                    "total_lines": total_lines,
                    "returned_lines": len(lines),
                    "truncated": truncated,
                    "error_summary": err_summary,
                    "content": content,
                })
            except Exception as e:
                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": False,
                    "error": f"Unerwarteter Fehler: {type(e).__name__}: {e}",
                })
                continue

        successful = [r for r in results if r["success"]]
        all_errors = [r["error"] for r in results if not r["success"]]

        # Gesamt-Fehler-Übersicht über alle Server
        total_level_counts: Counter = Counter()
        all_error_entries: list = []
        for r in successful:
            summary = r.get("error_summary", {})
            for level, cnt in summary.get("level_counts", {}).items():
                total_level_counts[level] += cnt
            all_error_entries.extend(summary.get("error_entries", []))

        return ToolResult(
            success=bool(successful),
            data={
                "stage": stage.name,
                "servers_total": len(servers_to_try),
                "servers_successful": len(successful),
                "error_overview": {
                    "level_counts": dict(total_level_counts),
                    "total_errors": sum(v for k, v in total_level_counts.items() if k in ("ERROR", "FATAL", "SEVERE", "EXCEPTION")),
                    "total_warnings": total_level_counts.get("WARN", 0),
                    "error_entries": all_error_entries[:100],
                },
                "results": results,
            },
            error=None if successful else f"Kein Server erreichbar: {'; '.join(all_errors)}",
        )

    registry.register(Tool(
        name="log_download_stage",
        description=(
            "Lädt die ospe_ope.log von Remote-OSPE-Servern herunter (NICHT lokale WLP-Server). "
            "Standardmäßig werden ALLE Server einer Stage abgefragt – offline Server werden "
            "übersprungen und die restlichen trotzdem abgefragt. "
            "Optional: server_id angeben um gezielt einen einzelnen Server abzufragen. "
            "Optional: search_term zum Filtern der Log-Zeilen. "
            "Voraussetzung: log_list_stages aufrufen um stage_id (und ggf. server_id) zu erhalten."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage (aus log_list_stages)", required=True),
            ToolParameter(name="server_id", type="string", description="Einzelnen Server abfragen statt alle (ID aus log_list_stages)", required=False),
            ToolParameter(name="search_term", type="string", description="Filtert Log-Zeilen auf diesen Suchbegriff", required=False),
        ],
        handler=log_download_stage,
    ))
    count += 1

    # ── log_search_stage ──────────────────────────────────────────────────────
    async def log_search_stage(**kwargs: Any) -> ToolResult:
        from datetime import datetime
        from app.api.routes.log_servers import _fetch_server_logs, _filter_lines_to_window

        stage_id: str = kwargs.get("stage_id", "")
        server_id: str = kwargs.get("server_id", "")
        search_term: str = kwargs.get("search_term", "")
        time_start: str = kwargs.get("time_start", "")
        time_end: str = kwargs.get("time_end", "")

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")
        if not stage.servers:
            return ToolResult(success=False, error="Stage hat keine Server konfiguriert")

        # Server-Auswahl: einzelner Server oder alle
        if server_id:
            servers_to_try = [s for s in stage.servers if s.id == server_id]
            if not servers_to_try:
                return ToolResult(success=False, error=f"Server '{server_id}' nicht in Stage '{stage.name}' gefunden")
        else:
            servers_to_try = stage.servers

        # Zeitfenster parsen (optional)
        t_start = t_end = None
        if time_start and time_end:
            try:
                t_start = datetime.fromisoformat(time_start.replace("Z", "+00:00")).replace(tzinfo=None)
                t_end = datetime.fromisoformat(time_end.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                return ToolResult(success=False, error="Ungültiges Zeitformat – ISO-8601 erwartet")

        tail = settings.log_servers.default_tail
        results = []

        for server in servers_to_try:
            try:
                result = await _fetch_server_logs(server, tail)
                if not result.success:
                    results.append({"server_id": server.id, "server": server.name, "success": False, "error": result.error})
                    continue

                all_lines = result.content.splitlines()
                total_lines = len(all_lines)
                lines = all_lines

                # Zeitfenster-Filter (wenn angegeben)
                if t_start and t_end:
                    lines = _filter_lines_to_window(lines, t_start, t_end)

                # Suchbegriff-Filter
                if search_term:
                    term_lower = search_term.lower()
                    lines = [l for l in lines if term_lower in l.lower()]

                truncated = len(lines) > _MAX_LINES_PER_SERVER
                if truncated:
                    lines = lines[-_MAX_LINES_PER_SERVER:]

                content = "\n".join(lines) if lines else ""
                if len(content) > _MAX_CONTENT_CHARS:
                    content = content[-_MAX_CONTENT_CHARS:]
                    truncated = True

                # Error-Summary auf Original-Content (nicht gefiltert)
                err_summary = _extract_error_summary(result.content, server.name)

                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": True,
                    "total_lines": total_lines,
                    "matching_lines": len(lines),
                    "truncated": truncated,
                    "error_summary": err_summary,
                    "content": content,
                })
            except Exception as e:
                results.append({"server_id": server.id, "server": server.name, "success": False, "error": f"{type(e).__name__}: {e}"})
                continue

        found = [r for r in results if r["success"] and r.get("matching_lines", 0) > 0]
        all_errors = [r["error"] for r in results if not r["success"]]

        # Gesamt-Fehler-Übersicht
        total_level_counts: Counter = Counter()
        all_error_entries: list = []
        for r in results:
            if not r.get("success"):
                continue
            summary = r.get("error_summary", {})
            for level, cnt in summary.get("level_counts", {}).items():
                total_level_counts[level] += cnt
            all_error_entries.extend(summary.get("error_entries", []))

        return ToolResult(
            success=bool(found),
            data={
                "stage": stage.name,
                "servers_total": len(servers_to_try),
                "servers_with_matches": len(found),
                "error_overview": {
                    "level_counts": dict(total_level_counts),
                    "total_errors": sum(v for k, v in total_level_counts.items() if k in ("ERROR", "FATAL", "SEVERE", "EXCEPTION")),
                    "total_warnings": total_level_counts.get("WARN", 0),
                    "error_entries": all_error_entries[:100],
                },
                "results": results,
            },
            error=None if found else f"Keine Treffer: {'; '.join(all_errors)}" if all_errors else "Keine Treffer in den Logs gefunden",
        )

    registry.register(Tool(
        name="log_search_stage",
        description=(
            "Durchsucht die ospe_ope.log auf Remote-OSPE-Servern (NICHT lokale WLP-Server). "
            "Fragt ALLE Server einer Stage ab – offline Server werden übersprungen. "
            "Optional: server_id um gezielt einen Server zu durchsuchen. "
            "Filtert nach Suchbegriff und/oder Zeitfenster. Mindestens search_term oder "
            "time_start+time_end muss angegeben werden. "
            "Voraussetzung: log_list_stages aufrufen um stage_id zu erhalten."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage (aus log_list_stages)", required=True),
            ToolParameter(name="server_id", type="string", description="Einzelnen Server durchsuchen statt alle (ID aus log_list_stages)", required=False),
            ToolParameter(name="search_term", type="string", description="Suchbegriff in Log-Zeilen", required=False),
            ToolParameter(name="time_start", type="string", description="Beginn des Zeitfensters ISO-8601 (optional)", required=False),
            ToolParameter(name="time_end", type="string", description="Ende des Zeitfensters ISO-8601 (optional)", required=False),
        ],
        handler=log_search_stage,
    ))
    count += 1

    # ── log_read_ffdc ─────────────────────────────────────────────────────────
    async def log_read_ffdc(**kwargs: Any) -> ToolResult:
        """Liest FFDC-Logs eines lokalen WLP-Servers (First Failure Data Capture)."""
        from pathlib import Path
        import logging
        logger = logging.getLogger(__name__)

        server_id: str = kwargs.get("server_id", "")

        try:
            max_files_raw = int(kwargs.get("max_files", 3))
            max_files: int = max(1, min(max_files_raw, 20))
        except (ValueError, TypeError):
            max_files = 3

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"WLP-Server '{server_id}' nicht gefunden")

        ffdc_dir = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "logs" / "ffdc"
        if not ffdc_dir.exists():
            return ToolResult(success=False, error=f"FFDC-Verzeichnis nicht gefunden: {ffdc_dir}")

        ffdc_files = sorted(ffdc_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not ffdc_files:
            ffdc_files = sorted(ffdc_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not ffdc_files:
            return ToolResult(success=True, data={"found": False, "message": "Keine FFDC-Dateien gefunden"})

        results = []
        loop = asyncio.get_event_loop()
        for fpath in ffdc_files[:max_files]:
            try:
                content = await loop.run_in_executor(None, _read_file_sync, str(fpath))
                results.append({
                    "file": fpath.name,
                    "path": str(fpath),
                    "size_kb": round(fpath.stat().st_size / 1024, 1),
                    "content": content[:8000] if len(content) > 8000 else content,
                    "truncated": len(content) > 8000,
                })
            except Exception as e:
                logger.warning(f"FFDC-Datei lesen fehlgeschlagen ({fpath.name}): {e}")
                results.append({"file": fpath.name, "error": str(e)})

        return ToolResult(success=True, data={
            "ffdc_dir": str(ffdc_dir),
            "total_files": len(ffdc_files),
            "files_read": results,
        })

    registry.register(Tool(
        name="log_read_ffdc",
        description=(
            "Liest FFDC-Logs (First Failure Data Capture) eines LOKALEN WLP-Servers. "
            "NICHT für Remote-OSPE-Server – dafür log_download_stage / log_search_stage nutzen. "
            "FFDC-Logs enthalten detaillierte Stack-Traces bei unerwarteten Fehlern. "
            "Nutze dies wenn messages.log nur 'FFDC' erwähnt aber keine Details zeigt."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des lokalen WLP-Servers (aus wlp_list_servers, NICHT aus log_list_stages)", required=True),
            ToolParameter(name="max_files", type="integer", description="Maximale Anzahl FFDC-Dateien (Standard: 3)", required=False),
        ],
        handler=log_read_ffdc,
    ))
    count += 1

    return count
