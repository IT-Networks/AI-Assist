"""
Graph Tools - Lokale Code-Analyse über den Knowledge Graph.

Diese Tools arbeiten AUSSCHLIESSLICH LOKAL mit dem indexierten Code.
Sie haben KEINE Verbindung zu externen Diensten wie GitHub, GitLab, etc.

Verfügbare Tools:
- graph_impact: Analysiert Auswirkungen von Code-Änderungen
- graph_context: Holt Kontext-Informationen zu einem Code-Element
- graph_find_path: Findet Verbindungen zwischen Code-Elementen
- graph_search: Intelligente Suche im indexierten Code
- graph_dependents: Zeigt wer ein Element verwendet
"""

import logging
from typing import Any, Dict, List, Optional

from app.agent.tools import (
    Tool,
    ToolParameter,
    ToolResult,
    ToolCategory,
    ToolRegistry,
)
from app.services.graph_query_service import (
    get_graph_query_service,
    GraphQueryService,
)
from app.services.knowledge_graph import (
    get_graph_registry,
    NodeType,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tool Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def handle_graph_impact(
    target: str,
    depth: int = 2,
    **kwargs
) -> ToolResult:
    """
    Handler für graph_impact Tool.

    Analysiert welche Code-Elemente betroffen wären,
    wenn das angegebene Element geändert wird.
    """
    try:
        # Prüfen ob Graph verfügbar
        registry = get_graph_registry()
        if not registry.get_active():
            return ToolResult(
                success=False,
                error="Kein Knowledge Graph aktiv. Bitte zuerst einen Graph indexieren."
            )

        service = get_graph_query_service()
        result = await service.analyze_impact(target, max_depth=min(depth, 5))

        if not result.direct_impacts and not result.transitive_impacts:
            return ToolResult(
                success=True,
                data=f"Keine Abhängigkeiten für '{result.target_name}' gefunden.\n"
                     f"Das Element hat keine bekannten Verwender im indexierten Code."
            )

        # Formatierte Ausgabe
        output_lines = [
            f"=== Impact-Analyse für {result.target_name} ({result.target_type}) ===",
            f"Risiko-Score: {result.risk_score:.1%}",
            "",
        ]

        if result.direct_impacts:
            output_lines.append(f"Direkte Abhängigkeiten ({len(result.direct_impacts)}):")
            for dep in result.direct_impacts[:15]:
                output_lines.append(f"  - {dep['type']} {dep['name']} ({dep['relation']})")

        if result.transitive_impacts:
            output_lines.append(f"\nIndirekte Abhängigkeiten ({len(result.transitive_impacts)}):")
            for dep in result.transitive_impacts[:10]:
                output_lines.append(f"  - {dep['type']} {dep['name']} (Tiefe: {dep['depth']})")

        if result.affected_files:
            output_lines.append(f"\nBetroffene Dateien ({len(result.affected_files)}):")
            for f in result.affected_files[:10]:
                output_lines.append(f"  - {f}")

        output_lines.append(f"\n{result.summary}")

        return ToolResult(success=True, data="\n".join(output_lines))

    except Exception as e:
        logger.exception("[graph_impact] Fehler")
        return ToolResult(success=False, error=f"Impact-Analyse fehlgeschlagen: {e}")


async def handle_graph_context(
    element_id: str,
    **kwargs
) -> ToolResult:
    """
    Handler für graph_context Tool.

    Holt umfassende Kontext-Informationen zu einem Code-Element:
    Vererbung, Interfaces, Abhängigkeiten, Verwender, etc.
    """
    try:
        registry = get_graph_registry()
        if not registry.get_active():
            return ToolResult(
                success=False,
                error="Kein Knowledge Graph aktiv. Bitte zuerst einen Graph indexieren."
            )

        service = get_graph_query_service()
        result = await service.get_context(element_id)

        if not result:
            return ToolResult(
                success=False,
                error=f"Element '{element_id}' nicht im Knowledge Graph gefunden."
            )

        # Formatierte Ausgabe
        output_lines = [
            f"=== Kontext für {result.name} ({result.node_type}) ===",
        ]

        if result.file_path:
            output_lines.append(f"Datei: {result.file_path}:{result.line_number or ''}")

        if result.extends:
            output_lines.append(f"Erweitert: {result.extends}")

        if result.implements:
            output_lines.append(f"Implementiert: {', '.join(result.implements)}")

        if result.parent_class:
            output_lines.append(f"Gehört zu: {result.parent_class}")

        if result.uses:
            output_lines.append(f"\nVerwendet ({len(result.uses)}):")
            for u in result.uses:
                output_lines.append(f"  - {u}")

        if result.used_by:
            output_lines.append(f"\nVerwendet von ({len(result.used_by)}):")
            for u in result.used_by:
                output_lines.append(f"  - {u}")

        if result.calls:
            output_lines.append(f"\nRuft auf ({len(result.calls)}):")
            for c in result.calls:
                output_lines.append(f"  - {c}")

        if result.called_by:
            output_lines.append(f"\nAufgerufen von ({len(result.called_by)}):")
            for c in result.called_by:
                output_lines.append(f"  - {c}")

        if result.related_tables:
            output_lines.append(f"\nDatenbank-Tabellen: {', '.join(result.related_tables)}")

        if result.metadata:
            output_lines.append(f"\nMetadaten: {result.metadata}")

        return ToolResult(success=True, data="\n".join(output_lines))

    except Exception as e:
        logger.exception("[graph_context] Fehler")
        return ToolResult(success=False, error=f"Kontext-Abfrage fehlgeschlagen: {e}")


async def handle_graph_find_path(
    from_element: str,
    to_element: str,
    **kwargs
) -> ToolResult:
    """
    Handler für graph_find_path Tool.

    Findet die Verbindung (Pfad) zwischen zwei Code-Elementen.
    Zeigt wie sie über Vererbung, Aufrufe oder Abhängigkeiten zusammenhängen.
    """
    try:
        registry = get_graph_registry()
        if not registry.get_active():
            return ToolResult(
                success=False,
                error="Kein Knowledge Graph aktiv. Bitte zuerst einen Graph indexieren."
            )

        service = get_graph_query_service()
        result = await service.find_connection(from_element, to_element, max_hops=5)

        if not result.found:
            return ToolResult(
                success=True,
                data=f"Keine Verbindung zwischen '{from_element}' und '{to_element}' gefunden.\n"
                     f"Die Elemente sind im indexierten Code nicht verbunden (max. 5 Hops)."
            )

        # Formatierte Ausgabe
        output_lines = [
            f"=== Pfad von {from_element} nach {to_element} ===",
            f"Länge: {result.length} Schritte",
            "",
        ]

        for i, step in enumerate(result.path):
            arrow = "→" if i < len(result.path) - 1 else ""
            output_lines.append(f"  {step['from']} --[{step['relation']}]--> {step['to']} {arrow}")

        return ToolResult(success=True, data="\n".join(output_lines))

    except Exception as e:
        logger.exception("[graph_find_path] Fehler")
        return ToolResult(success=False, error=f"Pfad-Suche fehlgeschlagen: {e}")


async def handle_graph_search(
    query: str,
    type_filter: Optional[str] = None,
    limit: int = 10,
    **kwargs
) -> ToolResult:
    """
    Handler für graph_search Tool.

    Intelligente Suche im Knowledge Graph mit natürlicher Sprache.
    Unterstützt spezielle Patterns wie "verwendet X", "implementiert X".
    """
    try:
        registry = get_graph_registry()
        if not registry.get_active():
            return ToolResult(
                success=False,
                error="Kein Knowledge Graph aktiv. Bitte zuerst einen Graph indexieren."
            )

        service = get_graph_query_service()
        filters = {"type": type_filter} if type_filter else None
        results = await service.smart_search(query, filters, min(limit, 50))

        if not results:
            return ToolResult(
                success=True,
                data=f"Keine Ergebnisse für '{query}' gefunden.\n"
                     f"Versuche eine andere Suche oder prüfe ob der Code indexiert ist."
            )

        # Formatierte Ausgabe
        output_lines = [
            f"=== Suchergebnisse für '{query}' ({len(results)} gefunden) ===",
            "",
        ]

        for node in results:
            line = f"  [{node.type.value}] {node.name}"
            if node.file_path:
                # Kurzer Pfad
                short_path = node.file_path.replace("\\", "/").split("/")[-2:]
                line += f" ({'/'.join(short_path)}:{node.line_number or ''})"
            output_lines.append(line)

        return ToolResult(success=True, data="\n".join(output_lines))

    except Exception as e:
        logger.exception("[graph_search] Fehler")
        return ToolResult(success=False, error=f"Suche fehlgeschlagen: {e}")


async def handle_graph_dependents(
    element_id: str,
    include_tests: bool = True,
    **kwargs
) -> ToolResult:
    """
    Handler für graph_dependents Tool.

    Zeigt alle Code-Elemente die das angegebene Element verwenden.
    """
    try:
        registry = get_graph_registry()
        if not registry.get_active():
            return ToolResult(
                success=False,
                error="Kein Knowledge Graph aktiv. Bitte zuerst einen Graph indexieren."
            )

        service = get_graph_query_service()
        dependents = await service.get_dependents(element_id, include_tests=include_tests)

        if not dependents:
            return ToolResult(
                success=True,
                data=f"Keine Verwender für '{element_id}' gefunden.\n"
                     f"Das Element wird im indexierten Code nicht verwendet."
            )

        # Formatierte Ausgabe
        test_note = "" if include_tests else " (ohne Tests)"
        output_lines = [
            f"=== Verwender von {element_id}{test_note} ({len(dependents)} gefunden) ===",
            "",
        ]

        # Nach Typ gruppieren
        by_type: Dict[str, List] = {}
        for node in dependents:
            type_name = node.type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(node)

        for type_name, nodes in sorted(by_type.items()):
            output_lines.append(f"{type_name.upper()} ({len(nodes)}):")
            for node in nodes[:10]:
                line = f"  - {node.name}"
                if node.file_path:
                    short_path = node.file_path.replace("\\", "/").split("/")[-1]
                    line += f" ({short_path})"
                output_lines.append(line)
            if len(nodes) > 10:
                output_lines.append(f"  ... und {len(nodes) - 10} weitere")

        return ToolResult(success=True, data="\n".join(output_lines))

    except Exception as e:
        logger.exception("[graph_dependents] Fehler")
        return ToolResult(success=False, error=f"Abhängigkeitssuche fehlgeschlagen: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Tool Registration
# ══════════════════════════════════════════════════════════════════════════════

def register_graph_tools(registry: ToolRegistry) -> int:
    """
    Registriert alle Graph-Tools.

    Diese Tools arbeiten LOKAL mit dem indexierten Knowledge Graph.
    Sie benötigen KEINE Internet-Verbindung und greifen NICHT auf
    externe Dienste wie GitHub, GitLab oder ähnliche zu.

    Unterschied zu anderen Tools:
    - github_* Tools: Benötigen GitHub API, arbeiten remote
    - git_* Tools: Arbeiten mit lokalem Git Repository
    - graph_* Tools: Arbeiten mit dem lokalen Knowledge Graph (Code-Analyse)
    """

    # ─────────────────────────────────────────────────────────────────
    # graph_impact - Impact-Analyse für Code-Änderungen
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_impact",
        description="""Analysiert die Auswirkungen einer Code-Änderung im LOKALEN Projekt.

WANN VERWENDEN:
- Bevor du eine Klasse/Methode änderst: "Was bricht wenn ich das ändere?"
- Um Risiko einer Änderung einzuschätzen
- Um alle betroffenen Dateien zu finden

BEISPIELE:
- graph_impact("UserService") → Zeigt wer UserService verwendet
- graph_impact("UserService.save", depth=3) → Tiefere Analyse

AUSGABE:
- Direkte Abhängigkeiten (wer verwendet das Element direkt)
- Indirekte Abhängigkeiten (wer verwendet die Verwender)
- Betroffene Dateien
- Risiko-Score (0-100%)

HINWEIS: Arbeitet NUR mit lokal indexiertem Code, keine GitHub-Verbindung.""",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="target",
                type="string",
                description="ID oder Name des zu analysierenden Elements (z.B. 'UserService' oder 'UserService.save')",
                required=True
            ),
            ToolParameter(
                name="depth",
                type="integer",
                description="Analysetiefe für indirekte Abhängigkeiten (1-5, Standard: 2)",
                required=False,
                default=2
            ),
        ],
        handler=handle_graph_impact
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_context - Kontext-Informationen holen
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_context",
        description="""Holt detaillierte Kontext-Informationen zu einem Code-Element aus dem LOKALEN Index.

WANN VERWENDEN:
- Um zu verstehen was eine Klasse/Methode macht
- Um Vererbung und Interfaces zu sehen
- Um Abhängigkeiten zu verstehen bevor du Code änderst

BEISPIELE:
- graph_context("PaymentService") → Zeigt Interfaces, Dependencies, Verwender
- graph_context("UserRepository.findById") → Zeigt wer die Methode aufruft

AUSGABE:
- Vererbung (extends)
- Implementierte Interfaces
- Verwendete Dependencies
- Wer das Element verwendet
- Datei und Zeilennummer

HINWEIS: Arbeitet NUR mit lokal indexiertem Code, keine GitHub-Verbindung.""",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="element_id",
                type="string",
                description="ID oder Name des Elements (z.B. 'UserService' oder vollqualifiziert 'com.example.UserService')",
                required=True
            ),
        ],
        handler=handle_graph_context
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_find_path - Verbindung zwischen Elementen finden
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_find_path",
        description="""Findet wie zwei Code-Elemente miteinander verbunden sind im LOKALEN Projekt.

WANN VERWENDEN:
- "Wie hängen Controller und Database zusammen?"
- Um den Aufrufpfad zwischen zwei Klassen zu verstehen
- Um die Architektur nachzuvollziehen

BEISPIELE:
- graph_find_path("UserController", "DatabaseService")
  → UserController → UserService → UserRepository → DatabaseService

AUSGABE:
- Pfad als Kette von Elementen
- Art der Beziehung (calls, uses, extends, implements)
- Pfadlänge

HINWEIS: Arbeitet NUR mit lokal indexiertem Code, keine GitHub-Verbindung.""",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="from_element",
                type="string",
                description="Start-Element (z.B. 'UserController')",
                required=True
            ),
            ToolParameter(
                name="to_element",
                type="string",
                description="Ziel-Element (z.B. 'DatabaseService')",
                required=True
            ),
        ],
        handler=handle_graph_find_path
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_search - Intelligente Suche im Graph
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_search",
        description="""Sucht im LOKALEN Knowledge Graph nach Code-Elementen.

WANN VERWENDEN:
- "Finde alle REST Controller"
- "Welche Klassen verwenden UserService?"
- "Zeige alle Implementierungen von PaymentGateway"

SPEZIELLE SUCHMUSTER:
- "verwendet UserService" → findet alle Klassen die UserService nutzen
- "implementiert Serializable" → findet alle Implementierungen
- Einfach "UserService" → findet alle Matches

PARAMETER:
- type_filter: Optional "class", "method", "interface" zum Filtern

HINWEIS: Arbeitet NUR mit lokal indexiertem Code, keine GitHub-Verbindung.
Für GitHub-Suche verwende stattdessen github_search_code.""",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Suchanfrage (z.B. 'UserService', 'verwendet PaymentGateway', 'REST Controller')",
                required=True
            ),
            ToolParameter(
                name="type_filter",
                type="string",
                description="Optional: Nur bestimmten Typ suchen (class, method, interface, field)",
                required=False,
                enum=["class", "method", "interface", "field", "table", "enum"]
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Maximale Anzahl Ergebnisse (Standard: 10, Max: 50)",
                required=False,
                default=10
            ),
        ],
        handler=handle_graph_search
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_dependents - Verwender eines Elements finden
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_dependents",
        description="""Zeigt alle Code-Elemente die das angegebene Element verwenden im LOKALEN Projekt.

WANN VERWENDEN:
- "Wer verwendet diese Klasse?"
- "Welche Tests testen diese Methode?"
- Vor dem Refactoring: Was muss ich alles anpassen?

BEISPIELE:
- graph_dependents("UserRepository") → UserService, UserServiceTest, AdminService, ...
- graph_dependents("UserRepository", include_tests=false) → Ohne Test-Klassen

AUSGABE:
- Gruppiert nach Typ (class, method, etc.)
- Mit Dateiangabe
- Optional ohne Tests

HINWEIS: Arbeitet NUR mit lokal indexiertem Code, keine GitHub-Verbindung.""",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="element_id",
                type="string",
                description="ID oder Name des Elements (z.B. 'UserRepository')",
                required=True
            ),
            ToolParameter(
                name="include_tests",
                type="boolean",
                description="Auch Test-Klassen einbeziehen (Standard: true)",
                required=False,
                default=True
            ),
        ],
        handler=handle_graph_dependents
    ))

    logger.info("[GraphTools] 5 Graph-Tools registriert (lokale Code-Analyse)")
    return 5
