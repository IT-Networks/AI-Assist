"""
Agent-Tools für Webex Messaging Integration.
Werden in der Tool-Registry des Orchestrators registriert.
"""

from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def register_webex_tools(registry: ToolRegistry) -> int:
    """Registriert alle Webex-Tools. Gibt Anzahl registrierter Tools zurück."""
    from app.core.config import settings

    if not settings.webex.enabled:
        return 0

    count = 0

    # ── webex_list_rooms ──────────────────────────────────────────────────────
    async def webex_list_rooms(**kwargs: Any) -> ToolResult:
        from app.services.webex_client import get_webex_client
        try:
            client = get_webex_client()
            room_type = kwargs.get("type", "")
            rooms = await client.list_rooms(room_type=room_type)
            return ToolResult(success=True, data={"rooms": rooms, "count": len(rooms)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_list_rooms",
        description=(
            "Listet alle Webex-Räume auf (Gruppenräume und Direktnachrichten). "
            "Zeigt pro Raum: Name, Typ, letzte Aktivität. "
            "Verwende die Room-ID um Nachrichten mit webex_read_messages zu lesen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="type",
                type="string",
                description="Raumtyp filtern: 'group' oder 'direct' (leer = alle)",
                required=False,
            ),
        ],
        handler=webex_list_rooms,
    ))
    count += 1

    # ── webex_read_messages ───────────────────────────────────────────────────
    async def webex_read_messages(**kwargs: Any) -> ToolResult:
        from app.services.webex_client import get_webex_client
        try:
            room_id = kwargs.get("room_id", "")
            if not room_id:
                return ToolResult(success=False, error="room_id ist erforderlich.")

            client = get_webex_client()
            only_mentions = kwargs.get("mentions_only", "").lower() in ("true", "1", "yes")

            if only_mentions:
                messages = await client.get_messages_mentioning_me(
                    room_id=room_id,
                    max_messages=int(kwargs.get("limit", 20)),
                )
            else:
                messages = await client.get_messages(
                    room_id=room_id,
                    max_messages=int(kwargs.get("limit", 20)),
                    before=kwargs.get("before", ""),
                )

            # Kontext anreichern und für LLM kürzen
            for msg in messages:
                if len(msg.get("text", "")) > 2000:
                    msg["text"] = msg["text"][:2000] + "\n[... gekürzt ...]"
                # Kompakte Kontext-Infos
                msg["is_reply"] = bool(msg.get("parent_id"))
                msg["has_mentions"] = bool(msg.get("mentioned_people")) or bool(msg.get("mentioned_groups"))

            return ToolResult(
                success=True,
                data={"messages": messages, "count": len(messages)}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_read_messages",
        description=(
            "Liest Nachrichten aus einem Webex-Raum. "
            "Benötigt die room_id aus webex_list_rooms. "
            "Zeigt Absender, Zeitstempel, Inhalt, Thread-Status und Mentions. "
            "Mit mentions_only=true werden nur Nachrichten geladen die dich @erwähnen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="room_id",
                type="string",
                description="Die ID des Webex-Raums (aus webex_list_rooms)",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl Nachrichten (Standard: 20, Max: 100)",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="mentions_only",
                type="string",
                description="Nur Nachrichten die mich @erwähnen (true/false, Standard: false)",
                required=False,
            ),
            ToolParameter(
                name="before",
                type="string",
                description="Nachrichten vor diesem Zeitpunkt (ISO-Format) für Paginierung",
                required=False,
            ),
        ],
        handler=webex_read_messages,
    ))
    count += 1

    # ── webex_search_messages ─────────────────────────────────────────────────
    async def webex_search_messages(**kwargs: Any) -> ToolResult:
        from app.services.webex_client import get_webex_client
        try:
            room_id = kwargs.get("room_id", "")
            query = kwargs.get("query", "")
            if not room_id:
                return ToolResult(success=False, error="room_id ist erforderlich.")
            if not query:
                return ToolResult(success=False, error="query ist erforderlich.")

            client = get_webex_client()
            messages = await client.get_messages(
                room_id=room_id,
                max_messages=int(kwargs.get("limit", 50)),
            )

            # Client-seitige Textsuche (Webex API hat keinen serverseitigen Textfilter)
            query_lower = query.lower()
            matches = [
                msg for msg in messages
                if query_lower in msg.get("text", "").lower()
            ]

            # Kürzen für LLM
            for msg in matches:
                if len(msg.get("text", "")) > 2000:
                    msg["text"] = msg["text"][:2000] + "\n[... gekürzt ...]"

            return ToolResult(
                success=True,
                data={"matches": matches, "count": len(matches), "query": query}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_search_messages",
        description=(
            "Durchsucht Nachrichten in einem Webex-Raum nach einem Suchbegriff. "
            "Benötigt room_id und query. Sucht im Nachrichtentext."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="room_id",
                type="string",
                description="Die ID des Webex-Raums",
                required=True,
            ),
            ToolParameter(
                name="query",
                type="string",
                description="Suchbegriff im Nachrichtentext",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. Anzahl zu durchsuchender Nachrichten (Standard: 50)",
                required=False,
                default=50,
            ),
        ],
        handler=webex_search_messages,
    ))
    count += 1

    return count
