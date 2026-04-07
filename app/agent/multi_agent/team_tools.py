"""
Multi-Agent Team Tools – Tool-Registrierung fuer run_team.

Registrierung: register_team_tools(registry) in main.py aufrufen.
Nur aktiv wenn multi_agent.enabled=true in config.yaml.
"""

import logging
from typing import Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


async def _handle_run_team(
    goal: str,
    team: Optional[str] = None,
) -> ToolResult:
    """Handler fuer run_team Tool."""
    from app.core.config import settings

    if not settings.multi_agent.enabled:
        return ToolResult(success=False, error="Multi-Agent System ist nicht aktiviert (multi_agent.enabled=false)")

    # Team laden
    team_config = None
    if team:
        team_config = next(
            (t for t in settings.multi_agent.teams if t.name == team),
            None
        )
    elif settings.multi_agent.teams:
        team_config = settings.multi_agent.teams[0]  # Default: erstes Team

    if not team_config:
        available = [t.name for t in settings.multi_agent.teams] if settings.multi_agent.teams else []
        return ToolResult(
            success=True,
            data=f"Kein Team '{team or 'default'}' gefunden. Verfuegbare Teams: {', '.join(available) or 'keine konfiguriert'}",
        )

    # Zu internem TeamConfig-Objekt konvertieren
    from app.agent.multi_agent.models import TeamConfig, TeamAgentConfig
    internal_config = TeamConfig(
        name=team_config.name,
        description=team_config.description,
        agents=[
            TeamAgentConfig(
                name=a.name,
                model=a.model,
                system_prompt=a.system_prompt,
                tools=list(a.tools),
                max_turns=a.max_turns,
            )
            for a in team_config.agents
        ],
        strategy=team_config.strategy,
        max_parallel=team_config.max_parallel,
    )

    # EventBridge fuer Live-Streaming
    from app.mcp.event_bridge import get_event_bridge
    event_bridge = get_event_bridge()

    async def on_progress(data):
        phase = data.get("phase", "progress")
        event_type = f"team_{phase}" if not phase.startswith("team_") else phase
        await event_bridge.emit(event_type, data)

    # Orchestrator erstellen und ausfuehren
    from app.agent.multi_agent.orchestrator import MultiAgentOrchestrator

    orchestrator = MultiAgentOrchestrator(
        team_config=internal_config,
        on_progress=on_progress,
    )

    await event_bridge.emit("team_started", {
        "team": internal_config.name,
        "goal": goal[:200],
        "agents": internal_config.agent_names(),
    })

    try:
        result = await orchestrator.run(goal)

        # Ergebnis inkl. Diagramme weiterreichen (erhoehtes Limit fuer Mermaid-Bloecke)
        summary = result.final_summary or "(Keine Zusammenfassung)"
        if len(summary) > 6000:
            summary = summary[:6000] + "\n\n[...Zusammenfassung gekuerzt...]"

        token_info = f", {result.total_tokens} Tokens in {result.total_llm_calls} LLM-Calls" if result.total_tokens else ""

        return ToolResult(
            success=True,
            data=(
                f"Team-Run '{result.team_name}' abgeschlossen.\n"
                f"Tasks: {result.completed_tasks}/{result.total_tasks} erfolgreich"
                f"{f', {result.failed_tasks} fehlgeschlagen' if result.failed_tasks else ''}\n"
                f"Dauer: {result.duration_seconds:.1f}s{token_info}\n\n"
                f"Gib dem User das folgende Ergebnis VOLLSTAENDIG weiter. "
                f"WICHTIG: Alle Markdown-Tabellen und ```mermaid Code-Bloecke MUESSEN "
                f"unveraendert uebernommen werden, damit sie im Frontend korrekt gerendert werden.\n\n"
                f"{summary}"
            ),
        )
    except Exception as e:
        logger.error(f"[run_team] Fehler: {e}", exc_info=True)
        return ToolResult(
            success=True,
            data=f"Team-Run fehlgeschlagen: {e}",
        )


def register_team_tools(registry: ToolRegistry) -> int:
    """Registriert Multi-Agent Team Tools."""
    from app.core.config import settings

    if not settings.multi_agent.enabled:
        logger.info("[MultiAgent] Deaktiviert (multi_agent.enabled=false)")
        return 0

    team_names = [t.name for t in settings.multi_agent.teams] if settings.multi_agent.teams else []
    teams_hint = f" Verfuegbare Teams: {', '.join(team_names)}." if team_names else ""

    registry.register(Tool(
        name="run_team",
        description=(
            "WICHTIG: Nutze dieses Tool wenn der User ein TEAM erwaehnt oder eine komplexe "
            "Aufgabe stellt die mehrere Perspektiven braucht (Code-Review, Analyse, Recherche). "
            "Startet ein Multi-Agent-Team das die Aufgabe automatisch in parallele Tasks zerlegt. "
            "NICHT manuell search_code oder read_file aufrufen — das Team macht das automatisch! "
            f"{teams_hint} "
            "Nach Aufruf: Ergebnis dem User mitteilen und KEINE weiteren Tools aufrufen."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter("goal", "string", "Das Ziel/die Aufgabe fuer das Team", required=True),
            ToolParameter("team", "string", "Name des Teams (aus config.yaml). Ohne Angabe wird das erste Team verwendet.", required=False),
        ],
        is_write_operation=False,
        handler=_handle_run_team,
    ))

    team_names = [t.name for t in settings.multi_agent.teams] if settings.multi_agent.teams else []
    logger.info(f"[MultiAgent] run_team Tool registriert. Teams: {team_names}")
    return 1
