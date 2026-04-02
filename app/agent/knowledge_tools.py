"""
Knowledge Collector Tools – Tool-Registrierung für research_topic, search_knowledge, list_knowledge.

Registrierung: register_knowledge_collector_tools(registry) in lifespan.py aufrufen.
"""

import logging
from typing import Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tool Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_research_topic(
    topic: str,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    confluence_url: Optional[str] = None,
    max_depth: int = 3,
) -> ToolResult:
    """
    Handler für research_topic Tool.

    Emittiert RESEARCH_*-Events über die globale MCPEventBridge für Live-Streaming.
    Der Orchestrator (orchestrator.py) pollt diese Events und yieldet sie als SSE.
    """
    from app.core.config import settings

    if not settings.knowledge_base.enabled:
        return ToolResult(success=False, error="Knowledge Base ist nicht aktiviert (knowledge_base.enabled=false)")

    # Provider zusammenstellen (mit detailliertem Logging)
    providers, provider_info = _get_available_providers_with_info()
    if not providers:
        return ToolResult(
            success=False,
            error=f"Keine Wissensquellen verfuegbar.\n{provider_info}\nPruefe config.yaml: confluence.base_url und/oder handbook.enabled",
        )

    logger.info(f"[research_topic] Start: topic='{topic}', providers={[p.name for p in providers]}")
    logger.info(f"[research_topic] Provider-Info: {provider_info}")

    # KnowledgeStore
    from app.services.knowledge_store import get_knowledge_store
    store = get_knowledge_store()

    # ToolRegistry
    from app.agent import get_tool_registry
    tool_registry = get_tool_registry()

    # EventBridge für Live-Streaming
    from app.mcp.event_bridge import get_event_bridge
    event_bridge = get_event_bridge()

    async def on_research_progress(progress):
        """Callback: ResearchProgress → MCPEventBridge Events."""
        from app.agent.orchestration.types import AgentEventType
        phase_to_event = {
            "discovering": AgentEventType.RESEARCH_DISCOVERY.value,
            "planning": AgentEventType.RESEARCH_PLAN.value,
            "analyzing": AgentEventType.RESEARCH_PROGRESS.value,
            "synthesizing": AgentEventType.RESEARCH_PROGRESS.value,
            "complete": AgentEventType.RESEARCH_COMPLETE.value,
            "error": AgentEventType.RESEARCH_ERROR.value,
        }
        event_type = phase_to_event.get(progress.phase, AgentEventType.RESEARCH_PROGRESS.value)
        await event_bridge.emit(event_type, progress.to_dict())

    # Orchestrator erstellen mit Event-Callback
    from app.agent.knowledge_collector.orchestrator import ResearchOrchestrator

    orchestrator = ResearchOrchestrator(
        providers=providers,
        knowledge_store=store,
        tool_registry=tool_registry,
        on_progress=on_research_progress,
    )

    await event_bridge.emit("research_started", {
        "topic": topic,
        "space_key": space_key or "",
        "providers": [p.name for p in providers],
    })

    try:
        md_path = await orchestrator.research(
            topic=topic,
            root_page_id=root_page_id,
            space_key=space_key,
            confluence_url=confluence_url,
            max_depth=max_depth,
        )

        if md_path:
            return ToolResult(
                success=True,
                data=(
                    f"Research abgeschlossen. Wissen gespeichert unter:\n"
                    f"  {md_path}\n\n"
                    f"Du kannst das gesammelte Wissen jetzt mit search_knowledge durchsuchen."
                ),
            )
        else:
            # Detaillierte Fehlermeldung statt generische
            return ToolResult(
                success=True,  # success=True damit LLM nicht endlos retry
                data=(
                    f"Research zu '{topic}' abgeschlossen, aber keine Seiten gefunden.\n"
                    f"Aktive Quellen: {', '.join(p.name for p in providers)}\n"
                    f"{provider_info}\n"
                    f"Moegliche Ursachen:\n"
                    f"- Confluence: Kein Treffer fuer '{topic}' (pruefe Space/Suchbegriff)\n"
                    f"- Handbuch: Kein Service mit diesem Namen\n"
                    f"Tipp: Versuche mit space_key oder root_page_id fuer gezieltere Suche."
                ),
            )

    except Exception as e:
        logger.error(f"[research_topic] Fehler: {e}", exc_info=True)
        await event_bridge.emit("research_error", {"error": str(e), "topic": topic})
        # success=True mit Fehlerinfo — verhindert Endlos-Retry durch LLM
        return ToolResult(
            success=True,
            data=f"Research-Fehler: {e}\nDie Recherche konnte nicht abgeschlossen werden.",
        )


async def _handle_search_knowledge(
    query: str,
    top_k: int = 5,
    include_full_content: bool = False,
) -> ToolResult:
    """Handler für search_knowledge Tool."""
    from app.core.config import settings

    if not settings.knowledge_base.enabled:
        return ToolResult(success=False, error="Knowledge Base ist nicht aktiviert")

    from app.services.knowledge_store import get_knowledge_store
    store = get_knowledge_store()

    results = await store.search(query, top_k=top_k)

    if not results:
        return ToolResult(
            success=True,
            data=f"Keine Ergebnisse in der Knowledge-Base für: '{query}'",
        )

    output_lines = [f"Knowledge-Base Suche: '{query}' ({len(results)} Treffer)\n"]

    for i, entry in enumerate(results, 1):
        # Freshness berechnen
        freshness_hint = ""
        if entry.date:
            try:
                from datetime import datetime, date
                doc_date = datetime.strptime(entry.date, "%Y-%m-%d").date()
                age_days = (date.today() - doc_date).days
                if age_days > 30:
                    freshness_hint = f" (Alter: {age_days} Tage - moeglicherweise veraltet)"
                else:
                    freshness_hint = f" (Alter: {age_days} Tage)"
            except (ValueError, TypeError):
                pass

        output_lines.append(f"--- [{i}] {entry.title} ---")
        output_lines.append(f"Space: {entry.space} | Datum: {entry.date}{freshness_hint} | Confidence: {entry.confidence}")
        if entry.tags:
            output_lines.append(f"Tags: {', '.join(entry.tags)}")
        output_lines.append(f"Pfad: {entry.path}")
        output_lines.append("")

        if include_full_content:
            content = await store.get_full_content(entry.path)
            output_lines.append(content)
        else:
            output_lines.append(f"Zusammenfassung:\n{entry.summary}")
        output_lines.append("")

    if not results:
        output_lines.append("Kein Wissen zu diesem Thema vorhanden. Vorschlag: research_topic(topic='...') um Wissen zu sammeln.")

    return ToolResult(success=True, data="\n".join(output_lines))


async def _handle_list_knowledge(
    space: Optional[str] = None,
) -> ToolResult:
    """Handler für list_knowledge Tool."""
    from app.core.config import settings

    if not settings.knowledge_base.enabled:
        return ToolResult(success=False, error="Knowledge Base ist nicht aktiviert")

    from app.services.knowledge_store import get_knowledge_store
    store = get_knowledge_store()

    entries = await store.list_all(space=space)

    if not entries:
        filter_hint = f" im Space '{space}'" if space else ""
        return ToolResult(
            success=True,
            data=f"Keine Wissens-Dokumente{filter_hint} vorhanden. Nutze research_topic um Wissen zu sammeln.",
        )

    output_lines = [f"Knowledge-Base: {len(entries)} Dokumente\n"]
    for entry in entries:
        tags = ", ".join(entry.tags[:5]) if entry.tags else "-"
        output_lines.append(
            f"• [{entry.space}] {entry.title} ({entry.date}, {entry.pages_analyzed} Seiten, {entry.confidence})"
        )
        output_lines.append(f"  Tags: {tags} | Pfad: {entry.path}")

    return ToolResult(success=True, data="\n".join(output_lines))


# ══════════════════════════════════════════════════════════════════════════════
# Provider-Helfer
# ══════════════════════════════════════════════════════════════════════════════

def _get_available_providers():
    """Erstellt die Liste verfügbarer SourceProvider basierend auf Config."""
    providers, _ = _get_available_providers_with_info()
    return providers


def _get_available_providers_with_info():
    """Erstellt Provider-Liste mit detaillierten Diagnose-Infos."""
    from app.core.config import settings

    providers = []
    info_lines = []

    # Confluence
    if settings.knowledge_base.sources.confluence:
        from app.agent.knowledge_collector.providers.confluence_provider import ConfluenceProvider
        provider = ConfluenceProvider()
        if provider.is_available():
            providers.append(provider)
            info_lines.append(f"Confluence: AKTIV (base_url={settings.confluence.base_url})")
        else:
            info_lines.append(f"Confluence: INAKTIV (base_url ist leer in config.yaml)")
            logger.warning("[research] Confluence-Provider nicht verfuegbar: base_url ist leer")
    else:
        info_lines.append("Confluence: DEAKTIVIERT (knowledge_base.sources.confluence=false)")

    # Handbuch
    if settings.knowledge_base.sources.handbook:
        from app.agent.knowledge_collector.providers.handbook_provider import HandbookProvider
        provider = HandbookProvider()
        if provider.is_available():
            providers.append(provider)
            info_lines.append(f"Handbuch: AKTIV (path={settings.handbook.path})")
        else:
            reason = "nicht enabled" if not settings.handbook.enabled else "path ist leer"
            info_lines.append(f"Handbuch: INAKTIV ({reason})")
            logger.warning(f"[research] Handbook-Provider nicht verfuegbar: {reason}")
    else:
        info_lines.append("Handbuch: DEAKTIVIERT (knowledge_base.sources.handbook=false)")

    info = "\n".join(info_lines)
    logger.info(f"[research] Provider-Status:\n{info}")

    return providers, info


# ══════════════════════════════════════════════════════════════════════════════
# Tool-Registrierung
# ══════════════════════════════════════════════════════════════════════════════

def register_knowledge_collector_tools(registry: ToolRegistry) -> int:
    """
    Registriert Knowledge Collector Tools im ToolRegistry.

    Aufgerufen in lifespan.py beim Startup.

    Returns:
        Anzahl registrierter Tools
    """
    registry.register(Tool(
        name="research_topic",
        description=(
            "WICHTIG: Nutze dieses Tool wenn der User eine Recherche, Wissenssammlung oder Knowledge-Base-Aufbau anfordert. "
            "Startet eine automatische, systematische Recherche zu einem Thema. "
            "Durchsucht Confluence-Seiten inkl. ALLER Unterseiten und PDFs sowie das Handbuch AUTOMATISCH. "
            "NICHT manuell einzelne Confluence-Seiten lesen — dieses Tool macht das automatisch und parallel. "
            "WICHTIG: 'topic' muss ein KURZER Suchbegriff sein (2-4 Woerter), KEIN ganzer Satz! "
            "Beispiel: topic='Dyns Prozesse PGV' statt topic='Pruefung von Dyns Prozessen fuer PGVs...' "
            "Nach Aufruf: Ergebnis dem User mitteilen und KEINE weiteren Tools aufrufen."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter("topic", "string", "Das zu recherchierende Thema", required=True),
            ToolParameter("space_key", "string", "Confluence Space Key (z.B. 'DEV', 'OPS')", required=False),
            ToolParameter("root_page_id", "string", "ID der Confluence-Startseite für Unterseiten-Traversierung", required=False),
            ToolParameter("confluence_url", "string", "URL einer Confluence-Seite als Startpunkt", required=False),
            ToolParameter("max_depth", "integer", "Max. Tiefe für Unterseiten (Standard: 3)", required=False, default=3),
        ],
        is_write_operation=False,  # Keine Bestätigung nötig — erstellt nur MD in knowledge-base/
        handler=_handle_research_topic,
    ))

    registry.register(Tool(
        name="search_knowledge",
        description=(
            "Durchsucht die firmeninterne Wissensbasis (Knowledge-Base). "
            "Findet gesammelte Fakten und Erkenntnisse aus früheren Recherchen. "
            "Nutze dieses Tool wenn nach firmeninternem Wissen gefragt wird, "
            "z.B. zu Services, Deployments, Prozessen oder Architekturen."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter("query", "string", "Suchanfrage", required=True),
            ToolParameter("top_k", "integer", "Max. Anzahl Ergebnisse (Standard: 5)", required=False, default=5),
            ToolParameter("include_full_content", "boolean",
                         "Wenn true: Gibt vollständigen MD-Inhalt statt nur Summary zurück",
                         required=False, default=False),
        ],
        handler=_handle_search_knowledge,
    ))

    registry.register(Tool(
        name="list_knowledge",
        description=(
            "Listet alle gesammelten Wissens-Dokumente der Knowledge-Base auf. "
            "Zeigt Titel, Datum, Space, Tags und Confidence pro Dokument."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter("space", "string", "Optional: Nur Dokumente aus diesem Space", required=False),
        ],
        handler=_handle_list_knowledge,
    ))

    return 3
