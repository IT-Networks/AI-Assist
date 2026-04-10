"""
Agent-Tools für Exchange E-Mail Integration.
Werden in der Tool-Registry des Orchestrators registriert.
"""

from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def register_email_tools(registry: ToolRegistry) -> int:
    """Registriert alle E-Mail-Tools. Gibt Anzahl registrierter Tools zurück."""
    from app.core.config import settings

    if not settings.email.enabled:
        return 0

    count = 0

    # ── email_list_folders ─────────────────────────────────────────────────────
    async def email_list_folders(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            client = get_email_client()
            folders = await client.list_folders()
            return ToolResult(success=True, data={"folders": folders, "count": len(folders)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_list_folders",
        description=(
            "Listet alle E-Mail-Ordner des Exchange-Postfachs auf. "
            "Zeigt pro Ordner: Name, Pfad, Anzahl Mails und ungelesene Mails. "
            "Verwende dies um einen Überblick über die Ordnerstruktur zu bekommen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=email_list_folders,
    ))
    count += 1

    # ── email_search ───────────────────────────────────────────────────────────
    async def email_search(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            client = get_email_client()
            results, total = await client.search_emails(
                query=kwargs.get("query", ""),
                sender=kwargs.get("sender", ""),
                subject=kwargs.get("subject", ""),
                folder=kwargs.get("folder", "inbox"),
                date_from=kwargs.get("date_from", ""),
                date_to=kwargs.get("date_to", ""),
                limit=int(kwargs.get("limit", 20)),
            )
            return ToolResult(
                success=True,
                data={"results": results, "total": total, "shown": len(results)}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_search",
        description=(
            "Durchsucht E-Mails im Exchange-Postfach nach Absender, Betreff, Datum oder Freitext. "
            "Gibt eine Liste mit Betreff, Absender, Datum und Vorschau zurück. "
            "Verwende email_id aus den Ergebnissen um eine Mail mit email_read zu öffnen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Freitext-Suche in Betreff und Body",
                required=False,
            ),
            ToolParameter(
                name="sender",
                type="string",
                description="Filter nach Absender (E-Mail-Adresse oder Name, Teilstring)",
                required=False,
            ),
            ToolParameter(
                name="subject",
                type="string",
                description="Filter nach Betreff (Teilstring)",
                required=False,
            ),
            ToolParameter(
                name="folder",
                type="string",
                description="Ordner (z.B. inbox, sent, drafts). Standard: inbox",
                required=False,
            ),
            ToolParameter(
                name="date_from",
                type="string",
                description="Ab Datum im ISO-Format (z.B. 2026-04-01)",
                required=False,
            ),
            ToolParameter(
                name="date_to",
                type="string",
                description="Bis Datum im ISO-Format (z.B. 2026-04-09)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Ergebnisse (Standard: 20, Max: 100)",
                required=False,
                default=20,
            ),
        ],
        handler=email_search,
    ))
    count += 1

    # ── email_read ─────────────────────────────────────────────────────────────
    async def email_read(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            email_id = kwargs.get("email_id", "")
            if not email_id:
                return ToolResult(success=False, error="email_id ist erforderlich.")

            client = get_email_client()
            email_data = await client.read_email(
                email_id=email_id,
                folder=kwargs.get("folder", "inbox"),
            )

            # Für LLM-Kontext: Text-Version verwenden (kürzen wenn nötig)
            body = email_data.get("body_text", "")
            if len(body) > 8000:
                body = body[:8000] + "\n\n[... gekürzt ...]"

            summary = {
                "subject": email_data["subject"],
                "from": email_data["sender"],
                "from_name": email_data.get("sender_name", ""),
                "to": email_data["to"],
                "cc": email_data.get("cc", []),
                "date": email_data["date"],
                "body": body,
                "attachments": email_data.get("attachments", []),
            }

            return ToolResult(success=True, data=summary)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_read",
        description=(
            "Liest eine einzelne E-Mail mit vollständigem Inhalt. "
            "Benötigt die email_id aus einer vorherigen email_search. "
            "Zeigt Absender, Empfänger, Datum, Body und Anhänge."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="email_id",
                type="string",
                description="Die ID der E-Mail (aus email_search Ergebnissen)",
                required=True,
            ),
            ToolParameter(
                name="folder",
                type="string",
                description="Ordner in dem die Mail liegt (Standard: inbox)",
                required=False,
            ),
        ],
        handler=email_read,
    ))
    count += 1

    # ── email_draft ────────────────────────────────────────────────────────────
    async def email_draft(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            to = kwargs.get("to", "").strip()
            subject = kwargs.get("subject", "").strip()
            body = kwargs.get("body", "").strip()

            if not to or not subject:
                return ToolResult(success=False, error="Empfänger (to) und Betreff (subject) sind erforderlich.")

            client = get_email_client()
            result = await client.create_draft(
                to=to,
                subject=subject,
                body=body,
                reply_to_id=kwargs.get("reply_to_id", ""),
            )
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    # ── email_list_rules ─────────────────────────────────────────────────────
    async def email_list_rules(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            client = get_email_client()
            rules = await client.list_rules()
            return ToolResult(success=True, data={"rules": rules, "count": len(rules)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_list_rules",
        description=(
            "Listet alle Inbox-Regeln (Server-Side Rules) des Exchange-Postfachs auf. "
            "Zeigt pro Regel: Name, aktiv/inaktiv, Bedingungen und Aktionen. "
            "Nuetzlich um zu verstehen welche automatischen Sortierungen und Weiterleitungen aktiv sind."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=email_list_rules,
    ))
    count += 1

    # ── email_resolve_name ────────────────────────────────────────────────────
    async def email_resolve_name(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            name = kwargs.get("name", "").strip()
            if not name:
                return ToolResult(success=False, error="name ist erforderlich.")
            client = get_email_client()
            results = await client.resolve_name(
                name=name,
                limit=int(kwargs.get("limit", 20)),
            )
            return ToolResult(
                success=True,
                data={"results": results, "count": len(results), "hint": "Mehr Ergebnisse moeglich" if len(results) >= 20 else ""}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_resolve_name",
        description=(
            "Durchsucht das globale Adressbuch (GAL / Active Directory) nach einer Person. "
            "Gibt E-Mail-Adresse, Name, Abteilung, Buero und Telefonnummer zurueck. "
            "Verwende dies um die E-Mail-Adresse oder Kontaktdaten eines Kollegen zu finden."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="name",
                type="string",
                description="Suchbegriff: Name, Vorname, Nachname oder Alias (mind. 2 Zeichen)",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Ergebnisse (Standard: 20)",
                required=False,
                default=20,
            ),
        ],
        handler=email_resolve_name,
    ))
    count += 1

    # ── email_search_contacts ─────────────────────────────────────────────────
    async def email_search_contacts(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            query = kwargs.get("query", "").strip()
            if not query:
                return ToolResult(success=False, error="query ist erforderlich.")
            client = get_email_client()
            results = await client.search_contacts(
                query=query,
                limit=int(kwargs.get("limit", 20)),
            )
            return ToolResult(
                success=True,
                data={"contacts": results, "count": len(results)}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_search_contacts",
        description=(
            "Durchsucht die persoenlichen Kontakte im Exchange-Postfach. "
            "Sucht nach Name, Firma, Abteilung. "
            "Fuer Firmen-Kontakte (GAL) verwende stattdessen email_resolve_name."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Suchbegriff: Name, Firma oder Abteilung",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Ergebnisse (Standard: 20)",
                required=False,
                default=20,
            ),
        ],
        handler=email_search_contacts,
    ))
    count += 1

    # ── email_get_oof ─────────────────────────────────────────────────────────
    async def email_get_oof(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            client = get_email_client()
            oof = await client.get_oof()
            return ToolResult(success=True, data=oof)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_get_oof",
        description=(
            "Liest den Out-of-Office / Abwesenheitsstatus des eigenen Postfachs. "
            "Zeigt Status (aktiv/inaktiv/geplant), Zeitraum und die Auto-Reply-Nachrichten "
            "(intern und extern)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=email_get_oof,
    ))
    count += 1

    # ── email_get_thread ──────────────────────────────────────────────────────
    async def email_get_thread(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            email_id = kwargs.get("email_id", "").strip()
            if not email_id:
                return ToolResult(success=False, error="email_id ist erforderlich.")
            client = get_email_client()
            thread = await client.get_thread(
                email_id=email_id,
                folder=kwargs.get("folder", "inbox"),
            )
            return ToolResult(success=True, data=thread)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_get_thread",
        description=(
            "Laedt die vollstaendige E-Mail-Konversation (Thread) zu einer bestimmten E-Mail. "
            "Zeigt alle Nachrichten im Thread chronologisch mit Absender, Datum und Body. "
            "Durchsucht Inbox, Gesendet und Entwuerfe. Benoetigt email_id aus email_search."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="email_id",
                type="string",
                description="Die ID der E-Mail (aus email_search Ergebnissen)",
                required=True,
            ),
            ToolParameter(
                name="folder",
                type="string",
                description="Ordner der Ausgangs-Mail (Standard: inbox)",
                required=False,
            ),
        ],
        handler=email_get_thread,
    ))
    count += 1

    # ── email_move ────────────────────────────────────────────────────────────
    async def email_move(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            email_id = kwargs.get("email_id", "").strip()
            target_folder = kwargs.get("target_folder", "").strip()
            if not email_id or not target_folder:
                return ToolResult(success=False, error="email_id und target_folder sind erforderlich.")
            client = get_email_client()
            result = await client.move_email(email_id=email_id, target_folder=target_folder)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_move",
        description=(
            "Verschiebt eine E-Mail in einen anderen Ordner. "
            "ACHTUNG: Schreib-Operation - erfordert Bestaetigung durch den Nutzer. "
            "Verwende email_list_folders um verfuegbare Ordner zu sehen."
        ),
        category=ToolCategory.SEARCH,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="email_id",
                type="string",
                description="Die ID der E-Mail (aus email_search Ergebnissen)",
                required=True,
            ),
            ToolParameter(
                name="target_folder",
                type="string",
                description="Name des Ziel-Ordners (z.B. 'Archiv', 'Projekte/2026')",
                required=True,
            ),
        ],
        handler=email_move,
    ))
    count += 1

    # ── email_flag ────────────────────────────────────────────────────────────
    async def email_flag(**kwargs: Any) -> ToolResult:
        from app.services.email_client import get_email_client
        try:
            email_id = kwargs.get("email_id", "").strip()
            if not email_id:
                return ToolResult(success=False, error="email_id ist erforderlich.")
            flag = kwargs.get("flag", "").strip()
            importance = kwargs.get("importance", "").strip()
            if not flag and not importance:
                return ToolResult(success=False, error="Mindestens flag oder importance muss angegeben werden.")
            client = get_email_client()
            result = await client.flag_email(email_id=email_id, flag=flag, importance=importance)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="email_flag",
        description=(
            "Setzt den Flag-Status und/oder die Wichtigkeit einer E-Mail. "
            "ACHTUNG: Schreib-Operation - erfordert Bestaetigung durch den Nutzer. "
            "Flag-Werte: flagged, complete, notflagged. Wichtigkeit: high, normal, low."
        ),
        category=ToolCategory.SEARCH,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="email_id",
                type="string",
                description="Die ID der E-Mail (aus email_search Ergebnissen)",
                required=True,
            ),
            ToolParameter(
                name="flag",
                type="string",
                description="Flag-Status: flagged, complete oder notflagged",
                required=False,
            ),
            ToolParameter(
                name="importance",
                type="string",
                description="Wichtigkeit: high, normal oder low",
                required=False,
            ),
        ],
        handler=email_flag,
    ))
    count += 1

    # ── email_draft ────────────────────────────────────────────────────────────

    registry.register(Tool(
        name="email_draft",
        description=(
            "Erstellt einen E-Mail-Entwurf im Exchange Drafts-Ordner. "
            "ACHTUNG: Schreib-Operation - erfordert Bestätigung durch den Nutzer. "
            "Der Entwurf kann danach im Outlook geöffnet und gesendet werden."
        ),
        category=ToolCategory.SEARCH,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="to",
                type="string",
                description="Empfänger E-Mail-Adresse(n), kommagetrennt bei mehreren",
                required=True,
            ),
            ToolParameter(
                name="subject",
                type="string",
                description="Betreff der E-Mail",
                required=True,
            ),
            ToolParameter(
                name="body",
                type="string",
                description="Inhalt der E-Mail (Text oder HTML)",
                required=True,
            ),
            ToolParameter(
                name="reply_to_id",
                type="string",
                description="Optional: email_id der Original-Mail für Reply-Verknüpfung",
                required=False,
            ),
        ],
        handler=email_draft,
    ))
    count += 1

    return count
