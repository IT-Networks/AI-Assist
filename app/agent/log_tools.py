"""
Agent-Tools für Log-Server-Zugriff.
Wird beim Startup registriert wenn log_servers aktiviert ist.
"""

import asyncio
from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


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
            "Listet alle konfigurierten Log-Stages und ihre Server auf. "
            "Zeigt Stage-IDs für nachfolgende Log-Abfragen."
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
        search_term: str = kwargs.get("search_term", "")

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")
        if not stage.servers:
            return ToolResult(success=False, error="Stage hat keine Server konfiguriert")

        tail = settings.log_servers.default_tail
        results = []

        for server in stage.servers:
            content = await _fetch_server_logs(server, tail)
            if content is None:
                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": False,
                    "error": "Login oder Download fehlgeschlagen",
                })
                continue

            lines = content.splitlines()

            if search_term:
                term_lower = search_term.lower()
                matching = [l for l in lines if term_lower in l.lower()]
            else:
                matching = lines

            results.append({
                "server_id": server.id,
                "server": server.name,
                "success": True,
                "lines_count": len(lines),
                "matching_lines_count": len(matching),
                "content": "\n".join(matching) if search_term else content,
            })

        successful = [r for r in results if r["success"]]
        return ToolResult(
            success=bool(successful),
            data={
                "stage": stage.name,
                "servers_total": len(stage.servers),
                "servers_successful": len(successful),
                "results": results,
            },
            error=None if successful else "Kein Server erreichbar",
        )

    registry.register(Tool(
        name="log_download_stage",
        description=(
            "Lädt die aktuellen Logs von ALLEN Servern einer Stage herunter. "
            "Jeder Server wird per Login authentifiziert und die ospe_ope.log geladen. "
            "Optional kann ein Suchbegriff angegeben werden um nur relevante Zeilen zu erhalten. "
            "Nutze log_list_stages um die stage_id zu ermitteln."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage", required=True),
            ToolParameter(name="search_term", type="string", description="Optionaler Suchbegriff – filtert Log-Zeilen", required=False),
        ],
        handler=log_download_stage,
    ))
    count += 1

    # ── log_search_stage ──────────────────────────────────────────────────────
    async def log_search_stage(**kwargs: Any) -> ToolResult:
        from datetime import datetime
        from app.api.routes.log_servers import _fetch_server_logs, _filter_lines_to_window

        stage_id: str = kwargs.get("stage_id", "")
        search_term: str = kwargs.get("search_term", "")
        time_start: str = kwargs.get("time_start", "")
        time_end: str = kwargs.get("time_end", "")

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")
        if not stage.servers:
            return ToolResult(success=False, error="Stage hat keine Server konfiguriert")

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

        for server in stage.servers:
            content = await _fetch_server_logs(server, tail)
            if content is None:
                results.append({"server": server.name, "success": False, "error": "Login/Download fehlgeschlagen"})
                continue

            lines = content.splitlines()

            # Zeitfenster-Filter (wenn angegeben)
            if t_start and t_end:
                lines = _filter_lines_to_window(lines, t_start, t_end)

            # Suchbegriff-Filter
            if search_term:
                term_lower = search_term.lower()
                lines = [l for l in lines if term_lower in l.lower()]

            results.append({
                "server_id": server.id,
                "server": server.name,
                "success": True,
                "matching_lines": len(lines),
                "content": "\n".join(lines) if lines else "",
            })

        found = [r for r in results if r["success"] and r.get("matching_lines", 0) > 0]
        return ToolResult(
            success=bool(found),
            data={
                "stage": stage.name,
                "servers_total": len(stage.servers),
                "servers_with_matches": len(found),
                "results": results,
            },
            error=None if found else "Keine Treffer in den Logs gefunden",
        )

    registry.register(Tool(
        name="log_search_stage",
        description=(
            "Durchsucht die Logs ALLER Server einer Stage nach einem Suchbegriff "
            "und/oder in einem Zeitfenster. Sammelt Ergebnisse von allen erreichbaren Servern. "
            "Mindestens search_term oder time_start+time_end muss angegeben werden. "
            "Nutze log_list_stages um die stage_id zu ermitteln."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage", required=True),
            ToolParameter(name="search_term", type="string", description="Suchbegriff in Log-Zeilen", required=False),
            ToolParameter(name="time_start", type="string", description="Beginn des Zeitfensters ISO-8601 (optional)", required=False),
            ToolParameter(name="time_end", type="string", description="Ende des Zeitfensters ISO-8601 (optional)", required=False),
        ],
        handler=log_search_stage,
    ))
    count += 1

    # ── log_read_ffdc ─────────────────────────────────────────────────────────
    async def log_read_ffdc(**kwargs: Any) -> ToolResult:
        """Liest FFDC-Logs eines WLP-Servers (First Failure Data Capture)."""
        from pathlib import Path
        import logging
        logger = logging.getLogger(__name__)

        server_id: str = kwargs.get("server_id", "")

        # max_files mit Range-Validierung (1-20)
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
            "Liest FFDC-Logs (First Failure Data Capture) eines WLP-Servers. "
            "FFDC-Logs enthalten detaillierte Stack-Traces bei unerwarteten Fehlern. "
            "Nutze dies wenn messages.log nur 'FFDC' erwähnt aber keine Details zeigt. "
            "Gibt die neuesten FFDC-Dateien zurück."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des WLP-Servers (aus wlp_list_servers)", required=True),
            ToolParameter(name="max_files", type="integer", description="Maximale Anzahl FFDC-Dateien (Standard: 3)", required=False),
        ],
        handler=log_read_ffdc,
    ))
    count += 1

    return count
