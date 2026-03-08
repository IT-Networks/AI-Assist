"""
Agent-Tools für Internet-Recherche mit Nutzer-Bestätigungspflicht.

Der Agent kann Suchanfragen stellen – jede Anfrage muss zuerst
vom Nutzer im Frontend bestätigt werden, bevor sie ausgeführt wird.
Die Query darf keine internen Projektdaten enthalten.
"""

import asyncio
from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def register_search_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    count = 0

    # ── web_search ────────────────────────────────────────────────────────────
    async def web_search(**kwargs: Any) -> ToolResult:
        from app.api.routes.search import (
            _pending,
            check_internal_data,
            _ddg_search,
        )
        import uuid
        from datetime import datetime

        query: str = kwargs.get("query", "").strip()
        reason: str = kwargs.get("reason", "")
        max_results: int = min(int(kwargs.get("max_results", 5)), 10)

        if not query:
            return ToolResult(success=False, error="query darf nicht leer sein")

        if not settings.search.enabled:
            return ToolResult(
                success=False,
                error=(
                    "Web-Suche ist deaktiviert. "
                    "Der Nutzer kann sie über das Frontend oder den Befehl "
                    "'Websuche einschalten' aktivieren."
                ),
            )

        # Interne Daten prüfen
        warnings = check_internal_data(query)
        if warnings:
            return ToolResult(
                success=False,
                error=(
                    f"Die Suchanfrage enthält möglicherweise interne Projektdaten "
                    f"({', '.join(warnings)}). "
                    f"Bitte nur generische Begriffe verwenden, "
                    f"z.B. Fehlercodes ohne interne Hostnamen oder IPs."
                ),
            )

        # Pending-Eintrag anlegen
        search_id = str(uuid.uuid4())[:8]
        _pending[search_id] = {
            "id": search_id,
            "query": query,
            "reason": reason,
            "max_results": max_results,
            "status": "pending",
            "results": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }

        # Auf Bestätigung warten (max. 90 Sekunden, alle 2s prüfen)
        timeout_s = 90
        elapsed = 0
        while elapsed < timeout_s:
            await asyncio.sleep(2)
            elapsed += 2
            item = _pending.get(search_id, {})
            status = item.get("status", "missing")

            if status == "done":
                results = item.get("results") or []
                if item.get("error"):
                    return ToolResult(success=False, error=item["error"])
                return ToolResult(
                    success=True,
                    data={
                        "query": query,
                        "result_count": len(results),
                        "results": results,
                        "source": "DuckDuckGo",
                    },
                )
            elif status == "rejected":
                _pending.pop(search_id, None)
                return ToolResult(
                    success=False,
                    error="Die Suchanfrage wurde vom Nutzer abgelehnt.",
                )

        # Timeout
        if search_id in _pending:
            _pending[search_id]["status"] = "timeout"
        return ToolResult(
            success=False,
            error=(
                f"Timeout: Die Suchanfrage '{query}' wurde innerhalb von "
                f"{timeout_s}s nicht bestätigt."
            ),
        )

    registry.register(Tool(
        name="web_search",
        description=(
            "Führt eine Internet-Recherche via DuckDuckGo durch. "
            "Jede Anfrage MUSS vom Nutzer im Frontend bestätigt werden. "
            "Verwende dies für: Fehlercodes (z.B. 'CWWKZ0013E Liberty'), "
            "Maven-Fehler ('NoSuchMethodError spring-core 5.3'), "
            "Bibliotheksversionen ('jackson-databind 2.15 security fix'), "
            "allgemeine Technologiedokumentation. "
            "VERBOTEN: Interne IPs, Hostnamen, Datenpfade, Projektnamen in der Query."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description=(
                    "Suchbegriff – NUR generische Informationen, "
                    "keine internen IPs, Hostnamen oder Projektdaten. "
                    "Beispiel: 'CWWKZ0013E websphere liberty' oder "
                    "'NoClassDefFoundError com.ibm.mq.MQException maven'"
                ),
                required=True,
            ),
            ToolParameter(
                name="reason",
                type="string",
                description="Kurze Begründung für die Suche (wird dem Nutzer angezeigt)",
                required=False,
            ),
            ToolParameter(
                name="max_results",
                type="integer",
                description="Anzahl Ergebnisse (1-10, Standard: 5)",
                required=False,
            ),
        ],
        handler=web_search,
    ))
    count += 1

    # ── web_search_toggle ─────────────────────────────────────────────────────
    async def web_search_toggle(**kwargs: Any) -> ToolResult:
        enabled: bool = bool(kwargs.get("enabled", True))
        settings.search.enabled = enabled
        state = "aktiviert" if enabled else "deaktiviert"
        return ToolResult(
            success=True,
            data={
                "enabled": enabled,
                "message": f"Web-Suche wurde {state}.",
            },
        )

    registry.register(Tool(
        name="web_search_toggle",
        description=(
            "Schaltet die Web-Suche-Funktion ein oder aus. "
            "Nutze dies wenn der Nutzer 'Websuche einschalten', "
            "'Suche aus' oder ähnliches schreibt."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="enabled",
                type="boolean",
                description="true = einschalten, false = ausschalten",
                required=True,
            ),
        ],
        handler=web_search_toggle,
    ))
    count += 1

    return count
