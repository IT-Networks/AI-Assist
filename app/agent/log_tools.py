"""
Agent-Tools für Log-Server-Zugriff (sequentiell + Zeitfenster).
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
            "Zeigt Stage-IDs und Server-IDs für nachfolgende Log-Abfragen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=log_list_stages,
    ))
    count += 1

    # ── log_find_server ───────────────────────────────────────────────────────
    async def log_find_server(**kwargs: Any) -> ToolResult:
        from datetime import datetime

        stage_id: str = kwargs.get("stage_id", "")
        reference_time: str = kwargs.get("reference_time", "")
        search_term: str = kwargs.get("search_term", "")

        # min_score mit Range-Validierung (0-150)
        try:
            min_score_raw = float(kwargs.get("min_score", 60.0))
            min_score: float = max(0.0, min(min_score_raw, 150.0))
        except (ValueError, TypeError):
            min_score = 60.0

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")

        try:
            ref_time = datetime.fromisoformat(reference_time.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return ToolResult(success=False, error=f"Ungültiges Zeitformat: {reference_time}")

        tail = settings.log_servers.default_tail
        tried = []

        for server in stage.servers:
            from app.api.routes.log_servers import _fetch_server_logs, _extract_timestamps
            content = await _fetch_server_logs(server, tail)
            if content is None:
                tried.append({"server": server.name, "score": -1, "error": "Download fehlgeschlagen"})
                continue

            timestamps = _extract_timestamps(content)
            if not timestamps:
                tried.append({"server": server.name, "score": 0, "note": "Keine Zeitstempel gefunden"})
                continue

            closest = min(timestamps, key=lambda t: abs((t - ref_time).total_seconds()))
            delta_s = abs((closest - ref_time).total_seconds())
            content_match = search_term.lower() in content.lower() if search_term else True
            score = round(max(0, 100 - delta_s / 60) * (1.5 if content_match else 1.0), 1)

            entry = {
                "server_id": server.id,
                "server": server.name,
                "score": score,
                "closest_timestamp": closest.isoformat(),
                "delta_seconds": round(delta_s, 1),
                "content_match": content_match,
            }
            tried.append(entry)

            if score >= min_score:
                return ToolResult(success=True, data={
                    "found": True,
                    "early_exit": True,
                    "best_match": entry,
                    "servers_tried": tried,
                    "servers_skipped": len(stage.servers) - len(tried),
                })

        tried_valid = [r for r in tried if r.get("score", -1) >= 0]
        best = max(tried_valid, key=lambda r: r["score"]) if tried_valid else None
        return ToolResult(
            success=bool(best),
            data={"found": bool(best), "early_exit": False, "best_match": best, "servers_tried": tried},
            error=None if best else "Kein passender Server gefunden",
        )

    registry.register(Tool(
        name="log_find_server",
        description=(
            "Sucht SEQUENTIELL den passenden Log-Server für eine Stage anhand eines Referenz-Zeitpunkts. "
            "Bricht sofort ab (Early-Exit) wenn ein Server mit Score ≥ min_score gefunden wird. "
            "Nutze log_list_stages um stage_id zu ermitteln. "
            "reference_time im ISO-8601-Format (z.B. '2024-01-15T14:30:00')."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage", required=True),
            ToolParameter(name="reference_time", type="string", description="Referenz-Zeitstempel ISO-8601", required=True),
            ToolParameter(name="search_term", type="string", description="Optionaler Suchbegriff zur Verifikation", required=False),
            ToolParameter(name="min_score", type="number", description="Mindest-Score für Early-Exit (0-150, Standard: 60)", required=False),
        ],
        handler=log_find_server,
    ))
    count += 1

    # ── log_read_window ───────────────────────────────────────────────────────
    async def log_read_window(**kwargs: Any) -> ToolResult:
        from datetime import datetime
        from app.api.routes.log_servers import _fetch_server_logs, _filter_lines_to_window

        stage_id: str = kwargs.get("stage_id", "")
        time_start: str = kwargs.get("time_start", "")
        time_end: str = kwargs.get("time_end", "")
        search_term: str = kwargs.get("search_term", "")
        server_id: str = kwargs.get("server_id", "")  # Optional: direkt auf Server zugreifen

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")

        try:
            t_start = datetime.fromisoformat(time_start.replace("Z", "+00:00")).replace(tzinfo=None)
            t_end   = datetime.fromisoformat(time_end.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return ToolResult(success=False, error="Ungültiges Zeitformat – ISO-8601 erwartet")

        tail = settings.log_servers.default_tail
        servers_to_try = stage.servers

        # Wenn server_id angegeben: nur diesen Server versuchen
        if server_id:
            srv = next((s for s in stage.servers if s.id == server_id), None)
            if not srv:
                return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")
            servers_to_try = [srv]

        tried = []
        for server in servers_to_try:
            content = await _fetch_server_logs(server, tail)
            if content is None:
                tried.append({"server": server.name, "error": "Download fehlgeschlagen"})
                continue

            all_lines = content.splitlines()
            window_lines = _filter_lines_to_window(all_lines, t_start, t_end)

            if search_term:
                term_lower = search_term.lower()
                matching = [l for l in window_lines if term_lower in l.lower()]
            else:
                matching = window_lines

            tried.append({
                "server_id": server.id,
                "server": server.name,
                "total_lines": len(all_lines),
                "window_lines": len(window_lines),
                "matching_lines": len(matching),
            })

            # Early-Exit bei Treffer
            if matching:
                return ToolResult(success=True, data={
                    "stage": stage.name,
                    "server_id": server.id,
                    "server": server.name,
                    "time_start": time_start,
                    "time_end": time_end,
                    "found": True,
                    "early_exit": True,
                    "window_lines": window_lines,
                    "matching_lines": matching,
                    "content": "\n".join(window_lines),
                    "servers_tried": tried,
                    "servers_skipped": len(servers_to_try) - len(tried),
                })

        # Kein Treffer
        best_try = max(tried, key=lambda t: t.get("window_lines", 0)) if tried else None
        return ToolResult(
            success=False,
            data={
                "found": False,
                "servers_tried": tried,
                "best_partial": best_try,
                "time_start": time_start,
                "time_end": time_end,
            },
            error="Keine passenden Log-Einträge im Zeitfenster gefunden",
        )

    registry.register(Tool(
        name="log_read_window",
        description=(
            "Lädt Logs von Servern einer Stage und filtert auf ein Zeitfenster. "
            "Probiert Server SEQUENTIELL und bricht sofort ab sobald ein Server "
            "passende Zeilen im Zeitfenster liefert. "
            "Optional: server_id direkt angeben um einen bestimmten Server zu nutzen. "
            "Ideal nach log_find_server: erst Server finden, dann Fenster lesen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage", required=True),
            ToolParameter(name="time_start", type="string", description="Beginn des Zeitfensters ISO-8601", required=True),
            ToolParameter(name="time_end", type="string", description="Ende des Zeitfensters ISO-8601", required=True),
            ToolParameter(name="search_term", type="string", description="Optionaler Suchbegriff im Zeitfenster", required=False),
            ToolParameter(name="server_id", type="string", description="Optionale direkte Server-ID (überspringt Suche)", required=False),
        ],
        handler=log_read_window,
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
                # Async File I/O: Blocking read in Thread-Pool auslagern
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
