"""
ServiceNow Tools - Tool-Definitionen fuer ServiceNow Service Portal.

Tools:
- search_servicenow_applications: Anwendungen suchen
- get_servicenow_app_details: App-Details abrufen
- query_servicenow_changes: Changes abfragen
- search_servicenow_knowledge: Knowledge Base durchsuchen
- query_servicenow_cmdb: Generische CMDB-Abfrage
- query_servicenow_incidents: Incidents abfragen
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
import re

from app.agent.tools import Tool, ToolParameter, ToolResult, ToolCategory
from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    """Lazy import des ServiceNow Clients."""
    from app.services.servicenow_client import get_servicenow_client
    return get_servicenow_client()


def _format_display_value(value) -> str:
    """Extrahiert display_value aus ServiceNow Feld."""
    if isinstance(value, dict):
        return value.get("display_value", value.get("value", str(value)))
    return str(value) if value else ""


# ============================================================================
# Tool 1: Anwendungen suchen
# ============================================================================

async def search_servicenow_applications(
    query: str,
    status: str = "active",
    category: str = "",
    max_results: int = 15
) -> ToolResult:
    """
    Durchsucht ServiceNow nach Business Applications.
    Sucht in: Name, Kurzbeschreibung, Owner, Support-Gruppe.
    """
    client = _get_client()
    config = settings.servicenow

    # Query aufbauen
    conditions = []
    if query:
        search_fields = ["nameLIKE", "short_descriptionLIKE", "owned_byLIKE"]
        search_conditions = "^OR".join([f"{f}{query}" for f in search_fields])
        conditions.append(f"({search_conditions})")

    if status and status != "all":
        status_map = {"active": "1", "retired": "3", "planned": "0"}
        conditions.append(f"install_status={status_map.get(status, '1')}")

    if category:
        conditions.append(f"categoryLIKE{category}")

    snow_query = "^".join(conditions) if conditions else ""

    try:
        result = await client.query_table(
            table=config.business_app_table,
            query=snow_query,
            fields=[
                "sys_id", "name", "short_description", "owned_by",
                "support_group", "install_status", "business_criticality",
                "u_application_type", "sys_updated_on"
            ],
            limit=max_results,
            order_by="name"
        )

        # Formatieren
        output = f"=== ServiceNow Anwendungen ({len(result.records)} Treffer) ===\n\n"

        for record in result.records:
            name = _format_display_value(record.get("name", ""))
            sys_id = record.get("sys_id", "")
            desc = _format_display_value(record.get("short_description", ""))
            owner = _format_display_value(record.get("owned_by", ""))
            support = _format_display_value(record.get("support_group", ""))
            status_val = _format_display_value(record.get("install_status", ""))
            criticality = _format_display_value(record.get("business_criticality", ""))
            app_type = _format_display_value(record.get("u_application_type", ""))

            output += f"[APP] {name}\n"
            output += f"   ID: {sys_id}\n"
            if desc:
                output += f"   Beschreibung: {desc}\n"
            output += f"   Owner: {owner or 'N/A'} | Support: {support or 'N/A'}\n"
            output += f"   Status: {status_val} | Kritikalitaet: {criticality or 'N/A'}\n"
            if app_type:
                output += f"   Typ: {app_type}\n"
            output += "\n"

        if result.from_cache:
            output += "(Ergebnis aus Cache)\n"

        if result.total_count > len(result.records):
            output += f"\n(Zeige {len(result.records)} von {result.total_count} Ergebnissen)\n"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"[ServiceNow] search_applications failed: {e}")
        return ToolResult(success=False, error=f"ServiceNow-Fehler: {str(e)}")


TOOL_SEARCH_APPLICATIONS = Tool(
    name="search_servicenow_applications",
    description=(
        "Durchsucht ServiceNow Service Portal nach Business Applications. "
        "Findet Anwendungen nach Name, Beschreibung oder Owner. "
        "Zeigt Status, Kritikalitaet und Support-Informationen."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Suchbegriff (Name, Beschreibung, Owner)",
            required=True
        ),
        ToolParameter(
            name="status",
            type="string",
            description="Status-Filter: 'active', 'retired', 'planned', 'all'",
            required=False,
            default="active",
            enum=["active", "retired", "planned", "all"]
        ),
        ToolParameter(
            name="category",
            type="string",
            description="Kategorie-Filter (optional)",
            required=False
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Maximale Anzahl Ergebnisse (default: 15)",
            required=False,
            default=15
        ),
    ],
    handler=search_servicenow_applications
)


# ============================================================================
# Tool 2: Anwendungs-Details
# ============================================================================

async def get_servicenow_app_details(
    app_name: str,
    include_incidents: bool = True,
    include_changes: bool = True,
    include_documentation: bool = True
) -> ToolResult:
    """Holt vollstaendige Details einer ServiceNow Anwendung."""
    client = _get_client()
    config = settings.servicenow

    try:
        # Anwendung finden
        app_result = await client.query_table(
            table=config.business_app_table,
            query=f"nameLIKE{app_name}",
            limit=1
        )

        if not app_result.records:
            return ToolResult(success=False, error=f"Anwendung '{app_name}' nicht gefunden")

        app = app_result.records[0]
        app_sys_id = app.get("sys_id")
        app_display_name = _format_display_value(app.get("name", ""))

        output = f"=== {app_display_name} ===\n\n"
        output += f"[STAMMDATEN]\n"
        output += f"   sys_id: {app_sys_id}\n"
        output += f"   Beschreibung: {_format_display_value(app.get('short_description', ''))}\n"
        output += f"   Owner: {_format_display_value(app.get('owned_by', 'N/A'))}\n"
        output += f"   Support-Gruppe: {_format_display_value(app.get('support_group', 'N/A'))}\n"
        output += f"   Kritikalitaet: {_format_display_value(app.get('business_criticality', 'N/A'))}\n"
        output += f"   Status: {_format_display_value(app.get('install_status', 'N/A'))}\n"
        output += f"   Version: {_format_display_value(app.get('version', 'N/A'))}\n"
        output += "\n"

        # Incidents laden
        if include_incidents:
            incidents = await client.query_table(
                table=config.incident_table,
                query=f"cmdb_ci={app_sys_id}^stateNOT IN6,7,8",
                fields=["number", "short_description", "priority", "state", "assigned_to", "opened_at"],
                limit=10,
                order_by="-priority,opened_at"
            )

            output += f"[OFFENE INCIDENTS] ({incidents.total_count})\n"
            if incidents.records:
                for inc in incidents.records:
                    prio = _format_display_value(inc.get("priority", "?"))
                    number = inc.get("number", "")
                    desc = _format_display_value(inc.get("short_description", ""))[:60]
                    output += f"   [P{prio}] {number}: {desc}\n"
            else:
                output += "   Keine offenen Incidents\n"
            output += "\n"

        # Changes laden
        if include_changes:
            changes = await client.query_table(
                table=config.change_table,
                query=f"cmdb_ci={app_sys_id}^stateNOT IN3,4,7",
                fields=["number", "short_description", "type", "state", "start_date", "end_date"],
                limit=10,
                order_by="start_date"
            )

            output += f"[AKTIVE CHANGES] ({changes.total_count})\n"
            if changes.records:
                for chg in changes.records:
                    chg_type = _format_display_value(chg.get("type", "Normal"))
                    number = chg.get("number", "")
                    desc = _format_display_value(chg.get("short_description", ""))[:50]
                    start = chg.get("start_date", "")
                    output += f"   [{chg_type}] {number}: {desc}\n"
                    output += f"       Geplant: {start}\n"
            else:
                output += "   Keine aktiven Changes\n"
            output += "\n"

        # Knowledge Articles laden
        if include_documentation:
            kb_articles = await client.query_table(
                table=config.knowledge_table,
                query=f"cmdb_ci={app_sys_id}^workflow_state=published",
                fields=["number", "short_description", "sys_view_count", "sys_updated_on"],
                limit=10,
                order_by="-sys_view_count"
            )

            output += f"[DOKUMENTATION] ({kb_articles.total_count} Artikel)\n"
            if kb_articles.records:
                for kb in kb_articles.records:
                    number = kb.get("number", "")
                    desc = _format_display_value(kb.get("short_description", ""))[:60]
                    output += f"   {number}: {desc}\n"
            else:
                output += "   Keine Knowledge-Artikel verknuepft\n"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"[ServiceNow] get_app_details failed: {e}")
        return ToolResult(success=False, error=f"ServiceNow-Fehler: {str(e)}")


TOOL_APP_DETAILS = Tool(
    name="get_servicenow_app_details",
    description=(
        "Holt vollstaendige Details einer ServiceNow Anwendung inkl. "
        "offener Incidents, aktiver Changes und verknuepfter Dokumentation."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter(
            name="app_name",
            type="string",
            description="Name der Anwendung (oder Teil davon)",
            required=True
        ),
        ToolParameter(
            name="include_incidents",
            type="boolean",
            description="Offene Incidents laden (default: true)",
            required=False,
            default=True
        ),
        ToolParameter(
            name="include_changes",
            type="boolean",
            description="Aktive Changes laden (default: true)",
            required=False,
            default=True
        ),
        ToolParameter(
            name="include_documentation",
            type="boolean",
            description="Knowledge-Artikel laden (default: true)",
            required=False,
            default=True
        ),
    ],
    handler=get_servicenow_app_details
)


# ============================================================================
# Tool 3: Changes abfragen
# ============================================================================

async def query_servicenow_changes(
    timeframe: str = "this_week",
    app_filter: str = "",
    change_type: str = "all",
    state: str = "scheduled",
    max_results: int = 20
) -> ToolResult:
    """Fragt anstehende oder aktuelle Changes ab."""
    client = _get_client()
    config = settings.servicenow

    # Zeitraum berechnen
    now = datetime.now()
    if timeframe == "today":
        start = now.replace(hour=0, minute=0, second=0)
        end = now.replace(hour=23, minute=59, second=59)
    elif timeframe == "this_week":
        start = now - timedelta(days=now.weekday())
        end = start + timedelta(days=6)
    elif timeframe == "next_week":
        start = now + timedelta(days=(7 - now.weekday()))
        end = start + timedelta(days=6)
    elif timeframe == "this_month":
        start = now.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    else:
        start = now - timedelta(days=7)
        end = now + timedelta(days=30)

    # Query aufbauen
    conditions = [
        f"start_date>={start.strftime('%Y-%m-%d')}",
        f"start_date<={end.strftime('%Y-%m-%d')}"
    ]

    if state == "scheduled":
        conditions.append("state=-5^ORstate=-4")  # Scheduled, Assess
    elif state == "in_progress":
        conditions.append("state=-2^ORstate=-1")  # Implement, Review
    elif state == "closed":
        conditions.append("state=3")

    if change_type != "all":
        type_map = {"normal": "Normal", "standard": "Standard", "emergency": "Emergency"}
        conditions.append(f"type={type_map.get(change_type, 'Normal')}")

    if app_filter:
        conditions.append(f"cmdb_ci.nameLIKE{app_filter}")

    snow_query = "^".join(conditions)

    try:
        result = await client.query_table(
            table=config.change_table,
            query=snow_query,
            fields=[
                "number", "short_description", "type", "state", "risk",
                "start_date", "end_date", "cmdb_ci", "assigned_to", "approval"
            ],
            limit=max_results,
            order_by="start_date"
        )

        output = f"=== ServiceNow Changes ({timeframe}) ===\n"
        output += f"Gefunden: {result.total_count} | Angezeigt: {len(result.records)}\n\n"

        type_icons = {"Normal": "[N]", "Standard": "[S]", "Emergency": "[E]"}

        for chg in result.records:
            chg_type = _format_display_value(chg.get("type", "Normal"))
            risk = _format_display_value(chg.get("risk", "?"))
            state_val = _format_display_value(chg.get("state", "?"))
            approval = _format_display_value(chg.get("approval", ""))
            number = chg.get("number", "")
            desc = _format_display_value(chg.get("short_description", ""))[:70]
            start_date = chg.get("start_date", "")
            end_date = chg.get("end_date", "")
            ci = _format_display_value(chg.get("cmdb_ci", ""))

            type_icon = type_icons.get(chg_type, "[?]")

            output += f"{type_icon} {number} [{chg_type}]\n"
            output += f"   {desc}\n"
            output += f"   Zeitraum: {start_date} - {end_date}\n"
            output += f"   Status: {state_val} | Risiko: {risk}"
            if approval:
                output += f" | Genehmigung: {approval}"
            output += "\n"

            if ci:
                output += f"   Betrifft: {ci}\n"
            output += "\n"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"[ServiceNow] query_changes failed: {e}")
        return ToolResult(success=False, error=f"ServiceNow-Fehler: {str(e)}")


TOOL_QUERY_CHANGES = Tool(
    name="query_servicenow_changes",
    description=(
        "Fragt Change Requests aus ServiceNow ab. "
        "Zeigt geplante, laufende oder abgeschlossene Changes fuer einen Zeitraum."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter(
            name="timeframe",
            type="string",
            description="Zeitraum: 'today', 'this_week', 'next_week', 'this_month'",
            required=False,
            default="this_week",
            enum=["today", "this_week", "next_week", "this_month"]
        ),
        ToolParameter(
            name="app_filter",
            type="string",
            description="Filter auf Anwendungsname (optional)",
            required=False
        ),
        ToolParameter(
            name="change_type",
            type="string",
            description="Change-Typ: 'normal', 'standard', 'emergency', 'all'",
            required=False,
            default="all",
            enum=["normal", "standard", "emergency", "all"]
        ),
        ToolParameter(
            name="state",
            type="string",
            description="Status: 'scheduled', 'in_progress', 'closed', 'all'",
            required=False,
            default="scheduled",
            enum=["scheduled", "in_progress", "closed", "all"]
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Maximale Anzahl Ergebnisse (default: 20)",
            required=False,
            default=20
        ),
    ],
    handler=query_servicenow_changes
)


# ============================================================================
# Tool 4: Knowledge Base durchsuchen
# ============================================================================

async def search_servicenow_knowledge(
    query: str,
    category: str = "",
    app_filter: str = "",
    max_results: int = 10
) -> ToolResult:
    """Durchsucht die ServiceNow Knowledge Base."""
    client = _get_client()
    config = settings.servicenow

    conditions = ["workflow_state=published"]

    if query:
        conditions.append(f"short_descriptionLIKE{query}^ORtextLIKE{query}")

    if category:
        conditions.append(f"kb_category.labelLIKE{category}")

    if app_filter:
        conditions.append(f"cmdb_ci.nameLIKE{app_filter}")

    snow_query = "^".join(conditions)

    try:
        result = await client.query_table(
            table=config.knowledge_table,
            query=snow_query,
            fields=[
                "number", "short_description", "text", "kb_category",
                "sys_view_count", "author", "sys_updated_on", "cmdb_ci"
            ],
            limit=max_results,
            order_by="-sys_view_count"
        )

        output = f"=== ServiceNow Knowledge Base ===\n"
        output += f"Suchergebnisse fuer: '{query}'\n"
        output += f"Gefunden: {result.total_count}\n\n"

        for kb in result.records:
            number = kb.get("number", "")
            desc = _format_display_value(kb.get("short_description", ""))
            views = kb.get("sys_view_count", "0")
            category_name = _format_display_value(kb.get("kb_category", ""))
            updated = kb.get("sys_updated_on", "")[:10]

            output += f"[KB] {number}: {desc}\n"
            if category_name:
                output += f"   Kategorie: {category_name}\n"
            output += f"   Aufrufe: {views} | Aktualisiert: {updated}\n"

            # Textvorschau (erste 200 Zeichen, HTML entfernt)
            text = _format_display_value(kb.get("text", ""))
            text_clean = re.sub(r'<[^>]+>', '', text)[:200]
            if text_clean:
                output += f"   Vorschau: {text_clean}...\n"
            output += "\n"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"[ServiceNow] search_knowledge failed: {e}")
        return ToolResult(success=False, error=f"ServiceNow-Fehler: {str(e)}")


TOOL_SEARCH_KNOWLEDGE = Tool(
    name="search_servicenow_knowledge",
    description=(
        "Durchsucht die ServiceNow Knowledge Base nach Dokumentation, "
        "Anleitungen und Troubleshooting-Artikeln."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Suchbegriff fuer Knowledge-Artikel",
            required=True
        ),
        ToolParameter(
            name="category",
            type="string",
            description="Kategorie-Filter (optional)",
            required=False
        ),
        ToolParameter(
            name="app_filter",
            type="string",
            description="Filter auf verknuepfte Anwendung (optional)",
            required=False
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Maximale Anzahl Ergebnisse (default: 10)",
            required=False,
            default=10
        ),
    ],
    handler=search_servicenow_knowledge
)


# ============================================================================
# Tool 5: Generische CMDB-Abfrage
# ============================================================================

async def query_servicenow_cmdb(
    table: str,
    query: str = "",
    fields: str = "",
    max_results: int = 20
) -> ToolResult:
    """
    Fuehrt eine generische CMDB-Abfrage aus.
    Fuer fortgeschrittene Benutzer und Custom Tables.
    """
    client = _get_client()

    # Felder parsen
    field_list = [f.strip() for f in fields.split(",")] if fields else None

    try:
        result = await client.query_table(
            table=table,
            query=query,
            fields=field_list,
            limit=max_results
        )

        output = f"=== CMDB Query: {table} ===\n"
        output += f"Query: {query or '(alle)'}\n"
        output += f"Ergebnisse: {result.total_count} | Angezeigt: {len(result.records)}\n\n"

        for i, record in enumerate(result.records, 1):
            output += f"--- Record {i} ---\n"
            for key, value in record.items():
                if key.startswith("sys_") and key not in ["sys_id", "sys_updated_on"]:
                    continue  # System-Felder ueberspringen

                display = _format_display_value(value)
                if display and display != "None":
                    output += f"  {key}: {display[:100]}\n"
            output += "\n"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"[ServiceNow] query_cmdb failed: {e}")
        return ToolResult(success=False, error=f"ServiceNow-Fehler: {str(e)}")


TOOL_QUERY_CMDB = Tool(
    name="query_servicenow_cmdb",
    description=(
        "Fuehrt eine generische CMDB-Abfrage auf beliebigen ServiceNow-Tabellen aus. "
        "Fuer Custom Tables und fortgeschrittene Abfragen. "
        "Query-Syntax: 'field=value^field2LIKE%term%' (ServiceNow Encoded Query)"
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter(
            name="table",
            type="string",
            description="Tabellenname (z.B. 'cmdb_ci_server', 'u_custom_apps')",
            required=True
        ),
        ToolParameter(
            name="query",
            type="string",
            description="ServiceNow Encoded Query (optional)",
            required=False
        ),
        ToolParameter(
            name="fields",
            type="string",
            description="Komma-separierte Liste der Felder (optional, sonst alle)",
            required=False
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Maximale Anzahl Ergebnisse (default: 20)",
            required=False,
            default=20
        ),
    ],
    handler=query_servicenow_cmdb
)


# ============================================================================
# Tool 6: Incidents abfragen
# ============================================================================

async def query_servicenow_incidents(
    app_filter: str = "",
    priority: str = "all",
    state: str = "open",
    max_results: int = 20
) -> ToolResult:
    """Fragt Incidents aus ServiceNow ab."""
    client = _get_client()
    config = settings.servicenow

    conditions = []

    if state == "open":
        conditions.append("stateNOT IN6,7,8")  # Nicht Resolved/Closed/Cancelled
    elif state == "resolved":
        conditions.append("state=6")
    elif state == "closed":
        conditions.append("state=7")

    if priority != "all":
        conditions.append(f"priority={priority}")

    if app_filter:
        conditions.append(f"cmdb_ci.nameLIKE{app_filter}")

    snow_query = "^".join(conditions) if conditions else ""

    try:
        result = await client.query_table(
            table=config.incident_table,
            query=snow_query,
            fields=[
                "number", "short_description", "priority", "state", "impact",
                "urgency", "assigned_to", "cmdb_ci", "opened_at", "sys_updated_on"
            ],
            limit=max_results,
            order_by="-priority,opened_at"
        )

        output = f"=== ServiceNow Incidents ===\n"
        output += f"Filter: {app_filter or 'alle'} | Status: {state} | Prioritaet: {priority}\n"
        output += f"Gefunden: {result.total_count}\n\n"

        prio_icons = {"1": "[P1]", "2": "[P2]", "3": "[P3]", "4": "[P4]", "5": "[P5]"}

        for inc in result.records:
            prio = _format_display_value(inc.get("priority", "?"))
            prio_val = inc.get("priority", {})
            if isinstance(prio_val, dict):
                prio_num = prio_val.get("value", "?")
            else:
                prio_num = str(prio_val)
            state_display = _format_display_value(inc.get("state", "?"))
            number = inc.get("number", "")
            desc = _format_display_value(inc.get("short_description", ""))[:70]
            ci = _format_display_value(inc.get("cmdb_ci", ""))
            assigned = _format_display_value(inc.get("assigned_to", ""))
            opened = inc.get("opened_at", "")[:16]

            output += f"{prio_icons.get(prio_num, '[P?]')} {number}\n"
            output += f"   {desc}\n"
            output += f"   Status: {state_display}\n"

            if ci:
                output += f"   Anwendung: {ci}\n"
            if assigned:
                output += f"   Zugewiesen: {assigned}\n"

            output += f"   Erstellt: {opened}\n"
            output += "\n"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"[ServiceNow] query_incidents failed: {e}")
        return ToolResult(success=False, error=f"ServiceNow-Fehler: {str(e)}")


TOOL_QUERY_INCIDENTS = Tool(
    name="query_servicenow_incidents",
    description=(
        "Fragt Incidents aus ServiceNow ab. "
        "Kann nach Anwendung, Prioritaet und Status gefiltert werden."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter(
            name="app_filter",
            type="string",
            description="Filter auf Anwendungsname (optional)",
            required=False
        ),
        ToolParameter(
            name="priority",
            type="string",
            description="Prioritaet: '1', '2', '3', '4', '5', 'all'",
            required=False,
            default="all",
            enum=["1", "2", "3", "4", "5", "all"]
        ),
        ToolParameter(
            name="state",
            type="string",
            description="Status: 'open', 'resolved', 'closed', 'all'",
            required=False,
            default="open",
            enum=["open", "resolved", "closed", "all"]
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Maximale Anzahl Ergebnisse (default: 20)",
            required=False,
            default=20
        ),
    ],
    handler=query_servicenow_incidents
)


# ============================================================================
# Tool-Registrierung
# ============================================================================

ALL_SERVICENOW_TOOLS = [
    TOOL_SEARCH_APPLICATIONS,
    TOOL_APP_DETAILS,
    TOOL_QUERY_CHANGES,
    TOOL_SEARCH_KNOWLEDGE,
    TOOL_QUERY_CMDB,
    TOOL_QUERY_INCIDENTS,
]


def register_servicenow_tools(registry) -> None:
    """Registriert alle ServiceNow-Tools."""
    if not settings.servicenow.enabled:
        logger.debug("[ServiceNow] Tools disabled (servicenow.enabled=false)")
        return

    logger.info(f"[ServiceNow] Registering {len(ALL_SERVICENOW_TOOLS)} tools")
    for tool in ALL_SERVICENOW_TOOLS:
        registry.register(tool)
