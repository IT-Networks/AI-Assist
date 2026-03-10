"""
Agent-Tools für WLP-Server-Verwaltung.
"""

import asyncio
from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def _read_lines_sync(path: str) -> list:
    """Synchrones Datei-Lesen für run_in_executor."""
    with open(path, "r", errors="replace") as f:
        return f.readlines()


def register_wlp_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    if not settings.wlp.enabled:
        return 0

    count = 0

    # ── wlp_list_servers ──────────────────────────────────────────────────────
    async def wlp_list_servers(**kwargs: Any) -> ToolResult:
        from app.api.routes.wlp import _running_processes
        servers = [
            {
                "id": s.id,
                "name": s.name,
                "server_name": s.server_name,
                "wlp_path": s.wlp_path,
                "is_running": s.id in _running_processes,
            }
            for s in settings.wlp.servers
        ]
        return ToolResult(success=True, data={"servers": servers})

    registry.register(Tool(
        name="wlp_list_servers",
        description="Listet alle konfigurierten WLP-Server auf inkl. Status (läuft/gestoppt).",
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=wlp_list_servers,
    ))
    count += 1

    # ── wlp_validate_server ───────────────────────────────────────────────────
    async def wlp_validate_server(**kwargs: Any) -> ToolResult:
        from app.api.routes.wlp import _validate_server_xml
        from pathlib import Path

        server_id: str = kwargs.get("server_id", "")
        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"Server '{server_id}' nicht gefunden")

        result = _validate_server_xml(srv.wlp_path, srv.server_name)

        # Auch gebautes Artefakt prüfen
        repo_path = settings.wlp.repo_path or settings.java.get_active_path()
        if repo_path:
            for pattern in ["**/target/*.war", "**/target/*.ear"]:
                matches = list(Path(repo_path).glob(pattern))
                if matches:
                    newest = max(matches, key=lambda p: p.stat().st_mtime)
                    result["built_artifact"] = str(newest)
                    break

        return ToolResult(success=result.get("valid", False), data=result,
                          error=result.get("error"))

    registry.register(Tool(
        name="wlp_validate_server",
        description=(
            "Prüft die server.xml eines WLP-Servers: ob WAR/EAR eingestellt ist, "
            "ob das Artefakt unter dem konfigurierten Pfad existiert "
            "und ob ein gebautes Artefakt im aktiven Repo gefunden wird."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des WLP-Servers", required=True),
        ],
        handler=wlp_validate_server,
    ))
    count += 1

    # ── wlp_get_logs ──────────────────────────────────────────────────────────
    async def wlp_get_logs(**kwargs: Any) -> ToolResult:
        from pathlib import Path
        import logging
        logger = logging.getLogger(__name__)

        server_id: str = kwargs.get("server_id", "")

        # lines mit Range-Validierung (1-10000)
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
            # Async File I/O: Blocking read in Thread-Pool auslagern
            loop = asyncio.get_event_loop()
            all_lines = await loop.run_in_executor(None, _read_lines_sync, str(log_path))
        except Exception as e:
            logger.warning(f"WLP Log-Datei lesen fehlgeschlagen: {e}")
            return ToolResult(success=False, error=f"Log-Datei lesen fehlgeschlagen: {e}")

        tail = all_lines[-lines:]
        return ToolResult(success=True, data={
            "log_path": str(log_path),
            "total_lines": len(all_lines),
            "lines": [l.rstrip() for l in tail],
            "content": "".join(tail),
        })

    registry.register(Tool(
        name="wlp_get_logs",
        description=(
            "Liest die letzten Zeilen aus messages.log eines WLP-Servers. "
            "Nützlich zum Analysieren von Startfehlern oder Laufzeitproblemen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des WLP-Servers", required=True),
            ToolParameter(name="lines", type="integer", description="Anzahl der letzten Zeilen (Standard: 100)", required=False),
        ],
        handler=wlp_get_logs,
    ))
    count += 1

    return count
