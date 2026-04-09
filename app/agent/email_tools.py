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
