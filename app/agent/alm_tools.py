"""
Agent-Tools fuer HP ALM/Quality Center Testfall-Management.

WICHTIG: Diese Tools sind NUR fuer HP ALM/Quality Center (QC).
Fuer andere Test-Integrationen (SOAP, JUnit, etc.) gibt es separate Tools.

Test Plan Module (Testfall-Definitionen):
- alm_test_connection: Verbindung pruefen und Login testen
- alm_search_tests: Testfaelle suchen (Name, Autor, Datum, Status, Typ)
- alm_read_test: Testfall mit Details und Steps laden
- alm_get_test_steps: Design-Steps eines Testfalls separat laden
- alm_create_test: Neuen Testfall erstellen (mit Bestaetigung)
- alm_update_test: Testfall aktualisieren (mit Bestaetigung)
- alm_list_folders: Test-Plan Folder auflisten

Test Lab Module (Testausfuehrung):
- alm_list_test_lab_folders: Test Lab Ordnerstruktur auflisten
- alm_list_test_sets: Test-Sets im Test Lab auflisten
- alm_search_test_instances: Test-Instances suchen (Tester, Datum, Status)
- alm_get_run_history: Run-Historie einer Test-Instance anzeigen
- alm_create_run: Test-Run erstellen (mit Bestaetigung)

Project Management:
- alm_list_projects: Verfuegbare Projekte in Domain auflisten
- alm_switch_project: Projekt zur Laufzeit wechseln
- alm_get_context: Aktuellen Kontext (Domain/Projekt) anzeigen

Authentifizierung erfolgt automatisch bei Verwendung der Tools.
Die Zugangsdaten werden aus den Settings geladen (alm.username, alm.password).
"""

import logging
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry
from app.core.exceptions import ALMError

logger = logging.getLogger(__name__)

# Pending Operations fuer Bestaetigung
_pending_operations: Dict[str, Dict[str, Any]] = {}


def register_alm_tools(registry: ToolRegistry) -> int:
    """
    Registriert alle ALM-Tools.

    Args:
        registry: Tool-Registry

    Returns:
        Anzahl registrierter Tools
    """
    from app.core.config import settings
    from app.services.alm_client import get_alm_client

    count = 0

    # ══════════════════════════════════════════════════════════════════════════
    # alm_test_connection - Verbindung pruefen und Login testen
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_test_connection(**kwargs: Any) -> ToolResult:
        """Testet die Verbindung zu HP ALM/Quality Center."""
        if not settings.alm.enabled:
            return ToolResult(
                success=False,
                error="HP ALM/Quality Center ist nicht aktiviert. Bitte in den Einstellungen aktivieren."
            )

        try:
            client = get_alm_client()
            result = await client.test_connection()

            if result.get("success"):
                return ToolResult(
                    success=True,
                    data=(
                        f"ALM Verbindung erfolgreich!\n"
                        f"- Server: {settings.alm.base_url}\n"
                        f"- Domain: {result.get('domain', settings.alm.domain)}\n"
                        f"- Project: {result.get('project', settings.alm.project)}\n"
                        f"- User: {result.get('user', settings.alm.username)}"
                    )
                )
            else:
                return ToolResult(
                    success=False,
                    error=f"ALM Verbindung fehlgeschlagen: {result.get('error', 'Unbekannter Fehler')}"
                )

        except ALMError as e:
            return ToolResult(success=False, error=f"ALM Fehler: {e}")
        except Exception as e:
            logger.exception("ALM Connection Test Error")
            return ToolResult(success=False, error=f"Verbindungsfehler: {e}")

    registry.register(Tool(
        name="alm_test_connection",
        description=(
            "Testet die Verbindung zu HP ALM/Quality Center und fuehrt einen Login durch. "
            "Verwende dieses Tool um zu pruefen ob ALM erreichbar ist und die Zugangsdaten korrekt sind. "
            "WICHTIG: Dies ist fuer HP ALM/Quality Center, NICHT fuer andere Test-Tools wie SOAP-Tests."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[],
        is_write_operation=False,
        handler=alm_test_connection,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_search_tests - Testfaelle suchen (erweitert)
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_search_tests(**kwargs: Any) -> ToolResult:
        """Sucht Testfaelle in HP ALM mit erweiterten Filtern."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert (alm.enabled=false)")

        query: str = kwargs.get("query", "")
        folder_id: Optional[int] = kwargs.get("folder_id")
        owner: Optional[str] = kwargs.get("owner")
        created_after: Optional[str] = kwargs.get("created_after")
        created_before: Optional[str] = kwargs.get("created_before")
        status: Optional[str] = kwargs.get("status")
        test_type: Optional[str] = kwargs.get("test_type")
        limit: int = kwargs.get("limit", 20)

        try:
            client = get_alm_client()
            # Aktuelles Projekt merken fuer Ausgabe
            context = client.get_current_context()
            current_project = f"{context['domain']}/{context['project']}"

            tests = await client.search_tests(
                query=query,
                folder_id=folder_id,
                owner=owner,
                created_after=created_after,
                created_before=created_before,
                status=status,
                test_type=test_type,
                limit=limit,
            )

            if not tests:
                filters = []
                if query:
                    filters.append(f"Name: '{query}'")
                if owner:
                    filters.append(f"Autor: '{owner}'")
                if created_after:
                    filters.append(f"Nach: {created_after}")
                filter_str = ", ".join(filters) if filters else "keine"
                return ToolResult(
                    success=True,
                    data=f"Keine Testfaelle in **{current_project}** gefunden (Filter: {filter_str})"
                )

            # Formatierte Ausgabe MIT Projekt-Info
            lines = [f"## {len(tests)} Testfaelle in **{current_project}**\n"]
            lines.append("| ID | Name | Typ | Status | Owner | Erstellt |")
            lines.append("|---|---|---|---|---|---|")

            for test in tests:
                lines.append(
                    f"| {test.id} | {test.name} | {test.test_type} | "
                    f"{test.status or '-'} | {test.owner or '-'} | {test.creation_date or '-'} |"
                )

            lines.append(f"\n*Projekt: {current_project}*")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Search Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_search_tests",
        description=(
            "Sucht Testfaelle in HP ALM/Quality Center mit erweiterten Filtern. "
            "Kann nach Name, Autor, Erstelldatum, Status und Typ filtern. "
            "Beispiel: 'Zeige alle Tests von user123 aus dem letzten Monat'"
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Suchbegriff (wird im Testfall-Namen gesucht)",
                required=False,
            ),
            ToolParameter(
                name="folder_id",
                type="integer",
                description="Nur in diesem Test-Plan-Folder suchen",
                required=False,
            ),
            ToolParameter(
                name="owner",
                type="string",
                description="Testfall-Autor (Benutzername, Teilsuche moeglich)",
                required=False,
            ),
            ToolParameter(
                name="created_after",
                type="string",
                description="Erstellt nach Datum (Format: YYYY-MM-DD)",
                required=False,
            ),
            ToolParameter(
                name="created_before",
                type="string",
                description="Erstellt vor Datum (Format: YYYY-MM-DD)",
                required=False,
            ),
            ToolParameter(
                name="status",
                type="string",
                description="Testfall-Status (z.B. Ready, Design, Imported)",
                required=False,
            ),
            ToolParameter(
                name="test_type",
                type="string",
                description="Testtyp: MANUAL oder AUTOMATED",
                required=False,
                enum=["MANUAL", "AUTOMATED"],
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Ergebnisse (default: 20)",
                required=False,
                default=20,
            ),
        ],
        handler=alm_search_tests,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_read_test - Testfall mit Details laden
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_read_test(**kwargs: Any) -> ToolResult:
        """Liest einen Testfall mit allen Details."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        # Akzeptiere test_id (int) ODER test_identifier (string)
        test_id: Optional[int] = kwargs.get("test_id")
        test_identifier: Optional[str] = kwargs.get("test_identifier")
        # Optional: Projekt-Kontext (wenn Test aus anderem Projekt stammt)
        project: Optional[str] = kwargs.get("project")
        domain: Optional[str] = kwargs.get("domain")

        # Wenn test_id als String uebergeben wurde (z.B. "123"), konvertieren
        if test_id is None and test_identifier is None:
            return ToolResult(
                success=False,
                error="test_id (numerisch) oder test_identifier (Name/Key) ist erforderlich"
            )

        try:
            client = get_alm_client()

            # Wenn Projekt angegeben, erst dorthin wechseln
            if project:
                current = client.get_current_context()
                target_domain = domain or current['domain']
                if project != current['project'] or target_domain != current['domain']:
                    logger.info(f"ALM: Wechsle zu Projekt {target_domain}/{project} fuer Test-Zugriff")
                    switch_result = await client.switch_project(project, target_domain)
                    if not switch_result.get("already_active"):
                        # Validiere neues Projekt
                        test_conn = await client.test_connection(verify_project=True)
                        if not test_conn.get("success"):
                            return ToolResult(
                                success=False,
                                error=f"Projektwechsel fehlgeschlagen: {test_conn.get('error')}"
                            )

            # Wenn nur test_identifier gegeben, suche den Test
            if test_id is None and test_identifier:
                # Versuche erst als Zahl zu parsen
                try:
                    test_id = int(test_identifier)
                except ValueError:
                    # Kein Integer - suche nach Name
                    logger.info(f"ALM: Suche Test mit Identifier '{test_identifier}'")
                    tests = await client.search_tests(query=test_identifier, limit=5)

                    if not tests:
                        context = client.get_current_context()
                        return ToolResult(
                            success=False,
                            error=f"Kein Test mit Name/Key '{test_identifier}' in {context['domain']}/{context['project']} gefunden"
                        )

                    if len(tests) == 1:
                        test_id = tests[0].id
                    else:
                        # Mehrere Treffer - zeige Liste
                        context = client.get_current_context()
                        lines = [f"Mehrere Tests gefunden fuer '{test_identifier}' in {context['domain']}/{context['project']}:\n"]
                        for t in tests:
                            lines.append(f"- **ID {t.id}**: {t.name}")
                        lines.append("\nBitte gib die eindeutige test_id an.")
                        return ToolResult(success=True, data="\n".join(lines))

            test = await client.get_test(test_id, include_steps=True)
            # Projekt-Info im Ergebnis
            context = client.get_current_context()
            result = test.to_markdown()
            result += f"\n\n*Projekt: {context['domain']}/{context['project']}*"
            return ToolResult(success=True, data=result)

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Read Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_read_test",
        description=(
            "Liest einen Testfall aus HP ALM mit allen Details: "
            "Name, Beschreibung, Folder-Pfad, Status und alle Test-Schritte. "
            "Akzeptiert entweder die numerische Test-ID oder einen Test-Namen/Key zur Suche. "
            "WICHTIG: Wenn der Test aus einem anderen Projekt stammt, gib project (und optional domain) an!"
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="test_id",
                type="integer",
                description="Die numerische Test-ID (aus alm_search_tests)",
                required=False,
            ),
            ToolParameter(
                name="test_identifier",
                type="string",
                description="Alternativ: Test-Name oder Key zur Suche (z.B. 'TC001' oder 'Login Test')",
                required=False,
            ),
            ToolParameter(
                name="project",
                type="string",
                description="Optional: Projekt-Name wenn Test aus anderem Projekt stammt (wechselt automatisch)",
                required=False,
            ),
            ToolParameter(
                name="domain",
                type="string",
                description="Optional: Domain wenn Test aus anderer Domain stammt",
                required=False,
            ),
        ],
        handler=alm_read_test,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_get_test_steps - Test-Schritte separat laden
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_get_test_steps(**kwargs: Any) -> ToolResult:
        """Laedt die Design-Steps eines Testfalls."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        test_id: int = kwargs.get("test_id", 0)
        if not test_id:
            return ToolResult(success=False, error="test_id ist erforderlich")

        try:
            client = get_alm_client()
            steps = await client.get_test_steps(test_id)

            if not steps:
                return ToolResult(
                    success=True,
                    data=f"Keine Design-Steps fuer Testfall {test_id} gefunden"
                )

            lines = [f"## Design-Steps fuer Test {test_id}\n"]
            lines.append("| # | Name | Beschreibung | Erwartetes Ergebnis |")
            lines.append("|---|------|--------------|---------------------|")

            for step in steps:
                desc = (step.description[:50] + "...") if len(step.description) > 50 else step.description
                desc = desc.replace("\n", " ").replace("|", "\\|")
                expected = (step.expected_result[:50] + "...") if len(step.expected_result) > 50 else step.expected_result
                expected = expected.replace("\n", " ").replace("|", "\\|")
                lines.append(f"| {step.step_order} | {step.name} | {desc} | {expected} |")

            lines.append(f"\n*{len(steps)} Steps gefunden*")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Get Steps Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_get_test_steps",
        description=(
            "Laedt die Design-Steps (Testschritte) eines Testfalls aus HP ALM. "
            "Zeigt alle Schritte mit Beschreibung und erwartetem Ergebnis. "
            "Verwende dies wenn alm_read_test keine Steps anzeigt."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="test_id",
                type="integer",
                description="Die Test-ID",
                required=True,
            ),
        ],
        handler=alm_get_test_steps,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_create_test - Testfall erstellen (mit Bestaetigung)
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_create_test(**kwargs: Any) -> ToolResult:
        """Erstellt einen neuen Testfall in HP ALM."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        name: str = kwargs.get("name", "")
        folder_id: int = kwargs.get("folder_id", 0)
        description: str = kwargs.get("description", "")
        test_type: str = kwargs.get("test_type", settings.alm.default_test_type)
        steps: List[Dict] = kwargs.get("steps", [])
        confirmed: bool = kwargs.get("_confirmed", False)

        if not name:
            return ToolResult(success=False, error="name ist erforderlich")
        if not folder_id:
            return ToolResult(success=False, error="folder_id ist erforderlich")

        # Bestaetigung erforderlich?
        if settings.alm.require_confirmation and not confirmed:
            preview = f"## Neuer Testfall\n\n"
            preview += f"**Name:** {name}\n"
            preview += f"**Folder-ID:** {folder_id}\n"
            preview += f"**Typ:** {test_type}\n"
            if description:
                preview += f"**Beschreibung:** {description}\n"
            if steps:
                preview += f"\n**{len(steps)} Test-Schritte:**\n"
                for i, step in enumerate(steps, 1):
                    preview += f"  {i}. {step.get('description', 'Schritt')}\n"

            return ToolResult(
                success=True,
                requires_confirmation=True,
                confirmation_data={
                    "action": "alm_create_test",
                    "description": f"Testfall '{name}' in ALM erstellen",
                    "preview": preview,
                    "params": kwargs,
                },
            )

        try:
            client = get_alm_client()
            test = await client.create_test(
                name=name,
                folder_id=folder_id,
                description=description,
                test_type=test_type,
                steps=steps,
            )

            result = f"Testfall erfolgreich erstellt!\n\n"
            result += test.to_markdown()
            return ToolResult(success=True, data=result)

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Create Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_create_test",
        description=(
            "Erstellt einen neuen Testfall in HP ALM/Quality Center. "
            "Erfordert Bestaetigung durch den User. "
            "Verwende alm_list_folders um die folder_id zu ermitteln."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="name",
                type="string",
                description="Name des Testfalls",
                required=True,
            ),
            ToolParameter(
                name="folder_id",
                type="integer",
                description="Ziel-Folder-ID im Test Plan (verwende alm_list_folders)",
                required=True,
            ),
            ToolParameter(
                name="description",
                type="string",
                description="Beschreibung des Testfalls",
                required=False,
            ),
            ToolParameter(
                name="test_type",
                type="string",
                description="Testtyp: MANUAL oder AUTOMATED",
                required=False,
                enum=["MANUAL", "AUTOMATED"],
                default="MANUAL",
            ),
            ToolParameter(
                name="steps",
                type="array",
                description="Test-Schritte als Array von {description, expected_result}",
                required=False,
            ),
        ],
        handler=alm_create_test,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_update_test - Testfall aktualisieren (mit Bestaetigung)
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_update_test(**kwargs: Any) -> ToolResult:
        """Aktualisiert einen bestehenden Testfall."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        test_id: int = kwargs.get("test_id", 0)
        fields: Dict[str, Any] = kwargs.get("fields", {})
        confirmed: bool = kwargs.get("_confirmed", False)

        if not test_id:
            return ToolResult(success=False, error="test_id ist erforderlich")
        if not fields:
            return ToolResult(success=False, error="fields ist erforderlich")

        # Bestaetigung erforderlich?
        if settings.alm.require_confirmation and not confirmed:
            preview = f"## Testfall {test_id} aktualisieren\n\n"
            preview += "**Aenderungen:**\n"
            for key, value in fields.items():
                preview += f"- **{key}:** {value}\n"

            return ToolResult(
                success=True,
                requires_confirmation=True,
                confirmation_data={
                    "action": "alm_update_test",
                    "description": f"Testfall {test_id} in ALM aktualisieren",
                    "preview": preview,
                    "params": kwargs,
                },
            )

        try:
            client = get_alm_client()
            test = await client.update_test(test_id, fields)

            result = f"Testfall erfolgreich aktualisiert!\n\n"
            result += test.to_markdown()
            return ToolResult(success=True, data=result)

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Update Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_update_test",
        description=(
            "Aktualisiert einen bestehenden Testfall in HP ALM. "
            "Erfordert Bestaetigung durch den User."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="test_id",
                type="integer",
                description="Test-ID des zu aktualisierenden Testfalls",
                required=True,
            ),
            ToolParameter(
                name="fields",
                type="object",
                description="Zu aendernde Felder als Object {name: ..., description: ..., status: ...}",
                required=True,
            ),
        ],
        handler=alm_update_test,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_list_folders - Test-Plan Folder auflisten
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_list_folders(**kwargs: Any) -> ToolResult:
        """Listet Test-Plan Folder auf."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        parent_id: int = kwargs.get("parent_id", 0)

        try:
            client = get_alm_client()
            folders = await client.list_folders(parent_id)

            if not folders:
                return ToolResult(
                    success=True,
                    data="Keine Folder gefunden" + (f" unter Parent-ID {parent_id}" if parent_id else "")
                )

            lines = ["## Test-Plan Folder\n"]
            lines.append("| ID | Name | Parent-ID |")
            lines.append("|---|---|---|")

            for folder in folders:
                lines.append(f"| {folder.id} | {folder.name} | {folder.parent_id} |")

            lines.append(f"\n*{len(folders)} Folder gefunden*")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Folders Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_list_folders",
        description=(
            "Listet Test-Plan Folder in HP ALM auf. "
            "Verwende dies um die folder_id fuer alm_create_test zu ermitteln. "
            "Ohne parent_id werden Root-Folder angezeigt."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="parent_id",
                type="integer",
                description="Parent-Folder-ID (0 oder leer = Root-Folder)",
                required=False,
                default=0,
            ),
        ],
        handler=alm_list_folders,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_list_test_sets - Test-Sets auflisten
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_list_test_sets(**kwargs: Any) -> ToolResult:
        """Listet Test-Sets aus dem Test Lab auf."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        folder_id: Optional[int] = kwargs.get("folder_id")

        try:
            client = get_alm_client()
            test_sets = await client.list_test_sets(folder_id)

            if not test_sets:
                return ToolResult(success=True, data="Keine Test-Sets gefunden")

            lines = ["## Test-Sets (Test Lab)\n"]
            lines.append("| ID | Name | Status |")
            lines.append("|---|---|---|")

            for ts in test_sets:
                lines.append(f"| {ts.id} | {ts.name} | {ts.status or '-'} |")

            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Test-Sets Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_list_test_sets",
        description=(
            "Listet Test-Sets aus dem Test Lab in HP ALM auf. "
            "Test-Sets enthalten Test-Instances fuer die Ausfuehrung."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="folder_id",
                type="integer",
                description="Optional: Nur Test-Sets in diesem Test Lab Folder",
                required=False,
            ),
        ],
        handler=alm_list_test_sets,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_create_run - Test-Run erstellen (mit Bestaetigung)
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_create_run(**kwargs: Any) -> ToolResult:
        """Erstellt einen Test-Run in HP ALM."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        test_instance_id: int = kwargs.get("test_instance_id", 0)
        status: str = kwargs.get("status", "")
        comment: str = kwargs.get("comment", "")
        confirmed: bool = kwargs.get("_confirmed", False)

        if not test_instance_id:
            return ToolResult(success=False, error="test_instance_id ist erforderlich")
        if not status:
            return ToolResult(success=False, error="status ist erforderlich (Passed/Failed/Not Completed/Blocked)")

        # Bestaetigung erforderlich?
        if settings.alm.require_confirmation and not confirmed:
            preview = f"## Test-Run erstellen\n\n"
            preview += f"**Test-Instance-ID:** {test_instance_id}\n"
            preview += f"**Status:** {status}\n"
            if comment:
                preview += f"**Kommentar:** {comment}\n"

            return ToolResult(
                success=True,
                requires_confirmation=True,
                confirmation_data={
                    "action": "alm_create_run",
                    "description": f"Test-Run mit Status '{status}' in ALM erstellen",
                    "preview": preview,
                    "params": kwargs,
                },
            )

        try:
            client = get_alm_client()
            run = await client.create_run(
                test_instance_id=test_instance_id,
                status=status,
                comment=comment,
            )

            result = f"Test-Run erfolgreich erstellt!\n\n"
            result += f"- **Run-ID:** {run.id}\n"
            result += f"- **Status:** {run.status}\n"
            result += f"- **Ausgefuehrt von:** {run.executor}\n"
            if run.execution_date:
                result += f"- **Datum:** {run.execution_date}\n"

            return ToolResult(success=True, data=result)

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Create Run Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_create_run",
        description=(
            "Erstellt einen Test-Run in HP ALM um ein Testergebnis zu dokumentieren. "
            "Aktualisiert automatisch den Status der Test-Instance. "
            "Erfordert Bestaetigung durch den User."
        ),
        category=ToolCategory.DEVOPS,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="test_instance_id",
                type="integer",
                description="Test-Instance-ID aus dem Test Lab",
                required=True,
            ),
            ToolParameter(
                name="status",
                type="string",
                description="Testergebnis-Status",
                required=True,
                enum=["Passed", "Failed", "Not Completed", "Blocked"],
            ),
            ToolParameter(
                name="comment",
                type="string",
                description="Kommentar zum Testergebnis",
                required=False,
            ),
        ],
        handler=alm_create_run,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_list_test_lab_folders - Test Lab Ordnerstruktur
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_list_test_lab_folders(**kwargs: Any) -> ToolResult:
        """Listet Test Lab Folder (Ordnerstruktur fuer Test-Sets)."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        parent_id: int = kwargs.get("parent_id", 0)

        try:
            client = get_alm_client()
            folders = await client.list_test_set_folders(parent_id)

            if not folders:
                return ToolResult(
                    success=True,
                    data="Keine Test Lab Folder gefunden" + (f" unter Parent-ID {parent_id}" if parent_id else "")
                )

            lines = ["## Test Lab Folder\n"]
            lines.append("| ID | Name | Parent-ID |")
            lines.append("|---|---|---|")

            for folder in folders:
                lines.append(f"| {folder.id} | {folder.name} | {folder.parent_id} |")

            lines.append(f"\n*{len(folders)} Folder gefunden*")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Test Lab Folders Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_list_test_lab_folders",
        description=(
            "Listet Test Lab Folder in HP ALM auf. "
            "Dies ist die Ordnerstruktur im Test Lab (nicht Test Plan!). "
            "Verwende dies um Test-Set-Folder-IDs zu ermitteln."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="parent_id",
                type="integer",
                description="Parent-Folder-ID (0 oder leer = Root-Folder)",
                required=False,
                default=0,
            ),
        ],
        handler=alm_list_test_lab_folders,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_get_run_history - Run-Historie einer Test-Instance
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_get_run_history(**kwargs: Any) -> ToolResult:
        """Laedt die Run-Historie einer Test-Instance."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        test_instance_id: int = kwargs.get("test_instance_id", 0)
        limit: int = kwargs.get("limit", 20)

        if not test_instance_id:
            return ToolResult(success=False, error="test_instance_id ist erforderlich")

        try:
            client = get_alm_client()
            runs = await client.get_run_history(test_instance_id, limit)

            if not runs:
                return ToolResult(
                    success=True,
                    data=f"Keine Runs fuer Test-Instance {test_instance_id} gefunden"
                )

            lines = [f"## Run-Historie (Test-Instance {test_instance_id})\n"]
            lines.append("| Run-ID | Status | Datum | Tester | Kommentar |")
            lines.append("|---|---|---|---|---|")

            for run in runs:
                comment_short = (run.comment[:30] + "...") if len(run.comment) > 30 else run.comment
                lines.append(
                    f"| {run.id} | {run.status} | {run.execution_date or '-'} | "
                    f"{run.executor or '-'} | {comment_short or '-'} |"
                )

            lines.append(f"\n*{len(runs)} Runs gefunden*")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Run History Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_get_run_history",
        description=(
            "Zeigt die Run-Historie einer Test-Instance in HP ALM. "
            "Zeigt alle vergangenen Testausfuehrungen mit Status, Datum und Tester."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="test_instance_id",
                type="integer",
                description="Test-Instance-ID aus dem Test Lab",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Ergebnisse (default: 20)",
                required=False,
                default=20,
            ),
        ],
        handler=alm_get_run_history,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_search_test_instances - Test-Instances suchen (erweitert)
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_search_test_instances(**kwargs: Any) -> ToolResult:
        """Sucht Test-Instances im Test Lab mit erweiterten Filtern."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        query: str = kwargs.get("query", "")
        test_set_id: Optional[int] = kwargs.get("test_set_id")
        status: Optional[str] = kwargs.get("status")
        tester: Optional[str] = kwargs.get("tester")
        executed_after: Optional[str] = kwargs.get("executed_after")
        executed_before: Optional[str] = kwargs.get("executed_before")
        limit: int = kwargs.get("limit", 50)

        try:
            client = get_alm_client()
            instances = await client.search_test_instances(
                query=query,
                test_set_id=test_set_id,
                status=status,
                tester=tester,
                executed_after=executed_after,
                executed_before=executed_before,
                limit=limit,
            )

            if not instances:
                filters = []
                if tester:
                    filters.append(f"Tester: '{tester}'")
                if executed_after:
                    filters.append(f"Nach: {executed_after}")
                if status:
                    filters.append(f"Status: {status}")
                filter_str = ", ".join(filters) if filters else "keine"
                return ToolResult(
                    success=True,
                    data=f"Keine Test-Instances gefunden (Filter: {filter_str})"
                )

            lines = ["## Test-Instances (Test Lab)\n"]
            lines.append("| Instance-ID | Test-Name | Test-Set-ID | Status | Tester | Datum |")
            lines.append("|---|---|---|---|---|---|")

            for inst in instances:
                lines.append(
                    f"| {inst.id} | {inst.test_name} | {inst.test_set_id} | "
                    f"{inst.status} | {inst.tester or '-'} | {inst.exec_date or '-'} |"
                )

            lines.append(f"\n*{len(instances)} Test-Instances gefunden*")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Search Instances Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_search_test_instances",
        description=(
            "Sucht Test-Instances im Test Lab von HP ALM mit erweiterten Filtern. "
            "Kann nach Name, Tester, Ausfuehrungsdatum und Status filtern. "
            "Beispiel: 'Zeige alle fehlgeschlagenen Tests von user123 diese Woche'"
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Suchbegriff (im Test-Namen)",
                required=False,
            ),
            ToolParameter(
                name="test_set_id",
                type="integer",
                description="Nur in diesem Test-Set suchen",
                required=False,
            ),
            ToolParameter(
                name="status",
                type="string",
                description="Nur mit diesem Status",
                required=False,
                enum=["Passed", "Failed", "No Run", "Not Completed", "Blocked"],
            ),
            ToolParameter(
                name="tester",
                type="string",
                description="Ausgefuehrt von diesem Tester (Benutzername)",
                required=False,
            ),
            ToolParameter(
                name="executed_after",
                type="string",
                description="Ausgefuehrt nach Datum (Format: YYYY-MM-DD)",
                required=False,
            ),
            ToolParameter(
                name="executed_before",
                type="string",
                description="Ausgefuehrt vor Datum (Format: YYYY-MM-DD)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Ergebnisse (default: 50)",
                required=False,
                default=50,
            ),
        ],
        handler=alm_search_test_instances,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_list_projects - Verfuegbare Projekte auflisten
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_list_projects(**kwargs: Any) -> ToolResult:
        """Listet verfuegbare Projekte in der aktuellen oder angegebenen Domain."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        domain: Optional[str] = kwargs.get("domain")

        try:
            client = get_alm_client()

            # Aktuellen Kontext anzeigen
            context = client.get_current_context()
            projects = await client.list_projects(domain)

            if not projects:
                return ToolResult(
                    success=True,
                    data=f"Keine Projekte in Domain '{domain or context['domain']}' gefunden"
                )

            lines = [f"## Projekte in Domain: {domain or context['domain']}\n"]
            lines.append(f"**Aktuelles Projekt:** {context['project']}\n")
            lines.append("| Projekt | Domain |")
            lines.append("|---------|--------|")

            for proj in projects:
                marker = " **(aktiv)**" if proj["name"] == context["project"] else ""
                lines.append(f"| {proj['name']}{marker} | {proj['domain']} |")

            lines.append(f"\n*{len(projects)} Projekte gefunden*")
            lines.append("\n**Tipp:** Verwende `alm_switch_project` um das Projekt zu wechseln.")
            return ToolResult(success=True, data="\n".join(lines))

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM List Projects Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_list_projects",
        description=(
            "Listet alle verfuegbaren Projekte in HP ALM auf. "
            "Zeigt auch das aktuell aktive Projekt an. "
            "Optional kann eine andere Domain angegeben werden."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[
            ToolParameter(
                name="domain",
                type="string",
                description="Optional: Domain (default: aktuelle Domain aus Settings)",
                required=False,
            ),
        ],
        handler=alm_list_projects,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_switch_project - Projekt wechseln
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_switch_project(**kwargs: Any) -> ToolResult:
        """Wechselt das aktive ALM-Projekt zur Laufzeit."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        project: str = kwargs.get("project", "").strip()
        domain: Optional[str] = kwargs.get("domain")
        if domain:
            domain = domain.strip()

        if not project:
            return ToolResult(success=False, error="project ist erforderlich")

        logger.info(f"ALM Switch: Wechsle zu Projekt '{project}' (Domain: {domain or 'unveraendert'})")

        try:
            client = get_alm_client()
            result = await client.switch_project(project, domain)

            if result["success"]:
                # Bereits im Zielprojekt? Dann nur bestaetigen, keine Validierung noetig
                if result.get("already_active"):
                    return ToolResult(
                        success=True,
                        data=(
                            f"## Bereits im Projekt!\n\n"
                            f"**Aktuelles Projekt:** {result['domain']}/{result['project']}\n\n"
                            f"Kein Wechsel erforderlich."
                        )
                    )

                # Verbindung testen mit Projekt-Validierung
                logger.info(f"ALM Switch: Teste Verbindung zu {result['domain']}/{result['project']}")
                test_result = await client.test_connection(verify_project=True)

                if test_result.get("success"):
                    return ToolResult(
                        success=True,
                        data=(
                            f"## Projekt gewechselt!\n\n"
                            f"**Von:** {result['previous_domain']}/{result['previous_project']}\n"
                            f"**Nach:** {result['domain']}/{result['project']}\n\n"
                            f"Verbindung erfolgreich getestet."
                        )
                    )
                else:
                    # Zurueck wechseln bei Fehler
                    logger.warning(f"ALM Switch: Projekt nicht gefunden, wechsle zurueck")
                    await client.switch_project(result['previous_project'], result['previous_domain'])

                    # Verfuegbare Projekte laden fuer bessere Fehlermeldung
                    available_hint = ""
                    try:
                        projects = await client.list_projects(result['domain'])
                        if projects:
                            project_names = [p['name'] for p in projects[:5]]
                            available_hint = f"\n\nVerfuegbare Projekte in '{result['domain']}': {', '.join(project_names)}"
                            if len(projects) > 5:
                                available_hint += f" (und {len(projects) - 5} weitere)"
                    except Exception:
                        pass

                    return ToolResult(
                        success=False,
                        error=f"Projekt existiert nicht oder keine Berechtigung: {test_result.get('error')}{available_hint}"
                    )

            return ToolResult(success=False, error="Projektwechsel fehlgeschlagen")

        except ALMError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("ALM Switch Project Error")
            return ToolResult(success=False, error=f"Unerwarteter Fehler: {e}")

    registry.register(Tool(
        name="alm_switch_project",
        description=(
            "Wechselt das aktive HP ALM Projekt zur Laufzeit. "
            "Verwende alm_list_projects um verfuegbare Projekte zu sehen. "
            "Der Wechsel ist temporaer und aendert nicht die Settings."
        ),
        category=ToolCategory.DEVOPS,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="project",
                type="string",
                description="Name des Zielprojekts",
                required=True,
            ),
            ToolParameter(
                name="domain",
                type="string",
                description="Optional: Domain wechseln (default: aktuelle Domain)",
                required=False,
            ),
        ],
        handler=alm_switch_project,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # alm_get_context - Aktuellen Kontext anzeigen
    # ══════════════════════════════════════════════════════════════════════════

    async def alm_get_context(**kwargs: Any) -> ToolResult:
        """Zeigt den aktuellen ALM-Kontext (Domain/Projekt)."""
        if not settings.alm.enabled:
            return ToolResult(success=False, error="HP ALM ist nicht aktiviert")

        try:
            client = get_alm_client()
            context = client.get_current_context()

            status = "Aktiv" if context["has_session"] else "Nicht verbunden"

            return ToolResult(
                success=True,
                data=(
                    f"## Aktueller ALM-Kontext\n\n"
                    f"**Server:** {context['base_url']}\n"
                    f"**Domain:** {context['domain']}\n"
                    f"**Projekt:** {context['project']}\n"
                    f"**Benutzer:** {context['user']}\n"
                    f"**Session:** {status}"
                )
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Fehler: {e}")

    registry.register(Tool(
        name="alm_get_context",
        description=(
            "Zeigt den aktuellen HP ALM Kontext: Server, Domain, Projekt, Benutzer und Session-Status."
        ),
        category=ToolCategory.KNOWLEDGE,
        parameters=[],
        handler=alm_get_context,
    ))
    count += 1

    logger.info(f"ALM Tools registriert: {count} Tools")
    return count
