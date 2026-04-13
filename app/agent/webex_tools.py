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
            max_rooms = min(int(kwargs.get("max_rooms", 50) or 50), 1000)
            name_filter = (kwargs.get("name_contains", "") or "").strip().lower()

            rooms = await client.list_rooms(room_type=room_type, max_rooms=max_rooms)

            if name_filter:
                rooms = [r for r in rooms if name_filter in (r.get("title", "") or "").lower()]

            return ToolResult(success=True, data={"rooms": rooms, "count": len(rooms)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_list_rooms",
        description=(
            "Listet Webex-Räume auf (Gruppenräume und Direktnachrichten), sortiert nach letzter "
            "Aktivität (neueste zuerst). Zeigt pro Raum: ID, Name, Typ, letzte Aktivität. "
            "Standard: 50 Räume. Für vollständige Übersicht: max_rooms=500+ (Hard-Cap 1000). "
            "Mit name_contains kannst du Räume nach Namensbestandteil filtern.\n\n"
            "WANN NUTZEN: Wenn du nur die Raumliste/IDs brauchst (z.B. um danach gezielt "
            "webex_read_messages oder webex_search_messages aufzurufen).\n"
            "ALTERNATIVE: Wenn du Liste + neueste Msgs pro Raum willst → webex_recent_activity "
            "(spart einen Tool-Roundtrip)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="type",
                type="string",
                description="Raumtyp filtern: 'group' oder 'direct' (leer = alle)",
                required=False,
            ),
            ToolParameter(
                name="max_rooms",
                type="integer",
                description="Max. Räume (Standard: 50, Max: 1000)",
                required=False,
                default=50,
            ),
            ToolParameter(
                name="name_contains",
                type="string",
                description="Optionaler Filter: nur Räume deren Name diesen Teilstring enthält",
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
            since = (kwargs.get("since", "") or "").strip()
            max_pages = int(kwargs.get("max_pages", 1) or 1)

            if only_mentions:
                messages = await client.get_messages_mentioning_me(
                    room_id=room_id,
                    max_messages=int(kwargs.get("limit", 20)),
                )
                # Optional client-seitig nach since filtern
                if since:
                    messages = [m for m in messages if m.get("created", "") >= since]
            elif since or max_pages > 1:
                # Paginierter Modus: lade so viele Seiten wie nötig bis since erreicht
                messages = await client.get_messages_paginated(
                    room_id=room_id,
                    max_pages=max_pages,
                    page_size=min(int(kwargs.get("limit", 100)), 100),
                    since=since,
                    before=kwargs.get("before", ""),
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
                data={
                    "messages": messages,
                    "count": len(messages),
                    "since": since,
                    "pages_loaded": max_pages if (since or max_pages > 1) else 1,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_read_messages",
        description=(
            "Liest Nachrichten aus EINEM Webex-Raum. Benötigt room_id (aus webex_list_rooms). "
            "Zeigt Absender, Zeitstempel, Text, Thread-Status, Mentions.\n\n"
            "MODI:\n"
            "1) Standard (limit=20, max_pages=1): nur die letzten 'limit' Msgs (max 100).\n"
            "2) Paginiert (since gesetzt ODER max_pages > 1): rückwärts in die Vergangenheit, "
            "je Seite 100 Msgs, stoppt bei 'since' oder nach max_pages Seiten.\n"
            "3) mentions_only=true: nur Nachrichten die mich @erwähnen.\n\n"
            "WANN NUTZEN: Wenn die room_id BEKANNT ist und du Msgs eines bestimmten Raums brauchst.\n"
            "FÜR ÄLTERE NACHRICHTEN (>1 Tag): IMMER 'since' (ISO-Datum, z.B. '2026-02-13') setzen, "
            "sonst bekommst du nur die neuesten Msgs!\n"
            "ALTERNATIVEN: Wenn Raum unbekannt → webex_search_all_rooms; "
            "wenn nur Übersicht über mehrere Räume → webex_recent_activity."
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
                description=(
                    "Im Standard-Modus: Anzahl Msgs total (Std 20, Max 100). "
                    "Im Paginated-Modus (since/max_pages): Seitengröße (Std 100, Max 100)."
                ),
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
                description="Startpunkt: nur Nachrichten VOR diesem ISO-Zeitpunkt (Pagination)",
                required=False,
            ),
            ToolParameter(
                name="since",
                type="string",
                description=(
                    "Stop-Datum (ISO, z.B. '2026-02-01T00:00:00Z'). Lädt rückwärts via "
                    "Pagination bis Nachrichten älter als dieser Zeitpunkt erreicht werden. "
                    "WICHTIG für ältere Chats: ohne 'since' bekommst du nur die neuesten Msgs!"
                ),
                required=False,
            ),
            ToolParameter(
                name="max_pages",
                type="integer",
                description=(
                    "Max. Anzahl Seiten (à 100 Msgs) bei Pagination. Standard: 1. "
                    "Empfehlung für 2 Monate Rückblick: 5-15. Hard-Cap: 50."
                ),
                required=False,
                default=1,
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
            since = (kwargs.get("since", "") or "").strip()
            max_pages = int(kwargs.get("max_pages", 5) or 5)
            page_size = min(int(kwargs.get("page_size", 100) or 100), 100)

            # Paginierte Suche: Webex API hat keinen serverseitigen Textfilter,
            # daher rückwärts durch die Historie bis 'since' oder max_pages erreicht.
            messages = await client.get_messages_paginated(
                room_id=room_id,
                max_pages=max_pages,
                page_size=page_size,
                since=since,
            )

            # Client-seitige Textsuche
            query_lower = query.lower()
            matches = [
                msg for msg in messages
                if query_lower in msg.get("text", "").lower()
            ]

            # Optional Treffer-Limit nach Filterung
            result_limit = int(kwargs.get("limit", 50) or 50)
            truncated = False
            if len(matches) > result_limit:
                matches = matches[:result_limit]
                truncated = True

            # Kürzen für LLM
            for msg in matches:
                if len(msg.get("text", "")) > 2000:
                    msg["text"] = msg["text"][:2000] + "\n[... gekürzt ...]"

            # Älteste tatsächlich durchsuchte Nachricht für Transparenz
            oldest_scanned = messages[-1].get("created", "") if messages else ""

            return ToolResult(
                success=True,
                data={
                    "matches": matches,
                    "count": len(matches),
                    "query": query,
                    "scanned_total": len(messages),
                    "oldest_scanned": oldest_scanned,
                    "since": since,
                    "pages_requested": max_pages,
                    "result_truncated": truncated,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_search_messages",
        description=(
            "Durchsucht Nachrichten in EINEM Webex-Raum nach einem Suchbegriff "
            "(case-insensitive Substring-Match, client-seitig — Webex-API hat keinen "
            "serverseitigen Volltext-Filter). Lädt bis zu max_pages × 100 Msgs paginiert "
            "rückwärts und filtert lokal.\n\n"
            "WANN NUTZEN: Raum bekannt + Suchbegriff. Default scannt 5 Seiten (~500 Msgs).\n"
            "FÜR ÄLTERE CHATS: 'since' (ISO-Datum) setzen oder max_pages erhöhen, sonst "
            "werden nur die neuesten ~500 Msgs durchsucht. Beispiel für 2 Monate Rückblick "
            "(heute 2026-04-13): since='2026-02-13'.\n\n"
            "ANTWORT enthält 'oldest_scanned' (ISO-Datum der ältesten gescannten Msg) und "
            "'scanned_total'. Wenn 'oldest_scanned' nicht weit genug zurückreicht: erneut "
            "mit höherem max_pages aufrufen.\n\n"
            "ALTERNATIVE: Wenn der Raum UNBEKANNT ist → webex_search_all_rooms (sucht über "
            "alle Räume gleichzeitig)."
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
                description="Suchbegriff im Nachrichtentext (case-insensitive)",
                required=True,
            ),
            ToolParameter(
                name="since",
                type="string",
                description=(
                    "Stop-Datum (ISO, z.B. '2026-02-13' oder '2026-02-13T00:00:00Z'). "
                    "Pagination stoppt sobald Nachrichten älter als dieser Zeitpunkt sind. "
                    "ENTSCHEIDEND für die Suche in alten Chats."
                ),
                required=False,
            ),
            ToolParameter(
                name="max_pages",
                type="integer",
                description=(
                    "Max. Anzahl Seiten à 100 Msgs (Standard: 5 = 500 Msgs). "
                    "Für 2 Monate Rückblick in aktiven Räumen: 10-20. Hard-Cap: 50."
                ),
                required=False,
                default=5,
            ),
            ToolParameter(
                name="page_size",
                type="integer",
                description="Nachrichten pro Seite (Standard und Max: 100)",
                required=False,
                default=100,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. zurückgegebene Treffer nach Filterung (Standard: 50)",
                required=False,
                default=50,
            ),
        ],
        handler=webex_search_messages,
    ))
    count += 1

    # ── webex_search_all_rooms ────────────────────────────────────────────────
    async def webex_search_all_rooms(**kwargs: Any) -> ToolResult:
        """Sucht einen Begriff über mehrere Räume hinweg (Cross-Room-Suche).

        Listet Räume (sortiert nach letzter Aktivität), paginiert in jedem Raum
        rückwärts bis 'since' oder 'max_pages_per_room' erreicht ist und sammelt Treffer.
        """
        from app.services.webex_client import get_webex_client
        try:
            query = (kwargs.get("query", "") or "").strip()
            if not query:
                return ToolResult(success=False, error="query ist erforderlich.")

            client = get_webex_client()
            room_type = kwargs.get("type", "")
            max_rooms = min(int(kwargs.get("max_rooms", 20) or 20), 200)
            max_pages_per_room = min(int(kwargs.get("max_pages_per_room", 2) or 2), 20)
            page_size = min(int(kwargs.get("page_size", 100) or 100), 100)
            since = (kwargs.get("since", "") or "").strip()
            result_limit = int(kwargs.get("limit", 50) or 50)
            name_filter = (kwargs.get("room_name_contains", "") or "").strip().lower()

            # Räume holen
            rooms = await client.list_rooms(room_type=room_type, max_rooms=max_rooms)
            if name_filter:
                rooms = [r for r in rooms if name_filter in (r.get("title", "") or "").lower()]

            query_lower = query.lower()
            all_matches: list = []
            rooms_scanned = 0
            rooms_with_errors: list = []

            for room in rooms:
                room_id = room.get("id", "")
                if not room_id:
                    continue

                try:
                    messages = await client.get_messages_paginated(
                        room_id=room_id,
                        max_pages=max_pages_per_room,
                        page_size=page_size,
                        since=since,
                    )
                    rooms_scanned += 1

                    for msg in messages:
                        if query_lower in (msg.get("text", "") or "").lower():
                            # Raum-Kontext anreichern
                            msg["room_title"] = room.get("title", "")
                            if len(msg.get("text", "")) > 1500:
                                msg["text"] = msg["text"][:1500] + "\n[... gekürzt ...]"
                            all_matches.append(msg)

                            if len(all_matches) >= result_limit * 2:
                                # Genug Kandidaten gesammelt
                                break

                    if len(all_matches) >= result_limit * 2:
                        break

                except Exception as e:
                    rooms_with_errors.append({
                        "room_id": room_id[:20],
                        "title": room.get("title", "")[:50],
                        "error": str(e)[:200],
                    })

            # Nach Datum absteigend sortieren und limitieren
            all_matches.sort(key=lambda m: m.get("created", ""), reverse=True)
            truncated = len(all_matches) > result_limit
            matches = all_matches[:result_limit]

            return ToolResult(
                success=True,
                data={
                    "matches": matches,
                    "count": len(matches),
                    "query": query,
                    "rooms_total": len(rooms),
                    "rooms_scanned": rooms_scanned,
                    "rooms_with_errors": rooms_with_errors,
                    "since": since,
                    "result_truncated": truncated,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_search_all_rooms",
        description=(
            "Cross-Room-Suche: durchsucht MEHRERE Webex-Räume gleichzeitig nach einem Begriff. "
            "Listet zuerst Räume (sortiert nach letzter Aktivität), paginiert in jedem Raum "
            "rückwärts und sammelt Treffer. Treffer enthalten 'room_title' für Kontext.\n\n"
            "WANN NUTZEN: Wenn der Raum UNBEKANNT ist und du wissen willst 'in welchem Chat "
            "wurde XY erwähnt'. Default scannt 20 Räume × 2 Seiten = bis 4000 Msgs.\n"
            "FÜR ÄLTERE CHATS: 'since' (ISO-Datum) setzen + max_pages_per_room auf 5-10 erhöhen. "
            "Beispiel 2 Monate (heute 2026-04-13): since='2026-02-13', max_pages_per_room=5.\n"
            "PERFORMANCE-TIPP: room_name_contains drastisch reduziert die Anzahl gescannter "
            "Räume — verwenden wenn du den Raumnamen-Teil kennst.\n\n"
            "ACHTUNG: Kann viele API-Calls erzeugen (max_rooms × max_pages_per_room).\n"
            "ALTERNATIVE: Wenn die room_id BEKANNT ist → webex_search_messages (effizienter, "
            "1 Raum statt N)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Suchbegriff (case-insensitive, Substring-Match im Nachrichtentext)",
                required=True,
            ),
            ToolParameter(
                name="since",
                type="string",
                description=(
                    "Stop-Datum (ISO, z.B. '2026-02-13'). Pagination jedes Raums stoppt sobald "
                    "ältere Msgs erreicht werden. Wichtig für Suche in alten Chats."
                ),
                required=False,
            ),
            ToolParameter(
                name="type",
                type="string",
                description="Raumtyp: 'group', 'direct' oder leer (alle)",
                required=False,
            ),
            ToolParameter(
                name="max_rooms",
                type="integer",
                description="Max. Anzahl Räume zu durchsuchen (Standard: 20, Max: 200)",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="max_pages_per_room",
                type="integer",
                description=(
                    "Max. Seiten à 100 Msgs pro Raum (Standard: 2 = ~200 Msgs/Raum, Max: 20). "
                    "Für 2 Monate Rückblick: 5-10."
                ),
                required=False,
                default=2,
            ),
            ToolParameter(
                name="room_name_contains",
                type="string",
                description="Optionaler Räume-Filter: nur Räume mit diesem Namens-Teilstring",
                required=False,
            ),
            ToolParameter(
                name="page_size",
                type="integer",
                description="Nachrichten pro Seite (Standard und Max: 100)",
                required=False,
                default=100,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max. zurückgegebene Treffer insgesamt (Standard: 50)",
                required=False,
                default=50,
            ),
        ],
        handler=webex_search_all_rooms,
    ))
    count += 1

    # ── webex_recent_activity ─────────────────────────────────────────────────
    async def webex_recent_activity(**kwargs: Any) -> ToolResult:
        """Übersicht: Räume + jeweils letzte N Nachrichten (für schnellen Überblick)."""
        from app.services.webex_client import get_webex_client
        try:
            client = get_webex_client()
            max_rooms = min(int(kwargs.get("max_rooms", 10) or 10), 50)
            messages_per_room = min(int(kwargs.get("messages_per_room", 5) or 5), 20)
            room_type = kwargs.get("type", "")
            since = (kwargs.get("since", "") or "").strip()

            rooms = await client.list_rooms(room_type=room_type, max_rooms=max_rooms)

            overview: list = []
            for room in rooms:
                room_id = room.get("id", "")
                if not room_id:
                    continue
                try:
                    msgs = await client.get_messages(
                        room_id=room_id, max_messages=messages_per_room
                    )
                    if since:
                        msgs = [m for m in msgs if m.get("created", "") >= since]
                    if not msgs and since:
                        continue  # leere Räume bei since-Filter überspringen

                    # Kürzen für LLM
                    for m in msgs:
                        if len(m.get("text", "")) > 500:
                            m["text"] = m["text"][:500] + "..."

                    overview.append({
                        "room_id": room_id,
                        "title": room.get("title", ""),
                        "type": room.get("type", ""),
                        "last_activity": room.get("last_activity", ""),
                        "messages": msgs,
                        "message_count": len(msgs),
                    })
                except Exception as e:
                    overview.append({
                        "room_id": room_id,
                        "title": room.get("title", ""),
                        "error": str(e)[:200],
                    })

            return ToolResult(
                success=True,
                data={"rooms": overview, "count": len(overview)}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_recent_activity",
        description=(
            "Schnellübersicht: listet die N aktivsten Räume + jeweils die letzten K Nachrichten. "
            "Ein Tool-Call statt webex_list_rooms + N×webex_read_messages.\n\n"
            "WANN NUTZEN: Allgemeine Fragen wie 'Was ist gerade los in Webex?', 'Was ist neu seit "
            "gestern?', 'Worüber wurde zuletzt diskutiert?'. Mit 'since' (ISO-Datum) zeigt nur "
            "Räume mit neuer Aktivität (leere werden ausgeblendet).\n\n"
            "ALTERNATIVEN:\n"
            "- Nur @-Mentions an mich → webex_read_messages mit mentions_only=true (pro Raum)\n"
            "- Nach konkretem Begriff suchen → webex_search_all_rooms\n"
            "- Tiefere Historie eines bestimmten Raums → webex_read_messages mit since/max_pages\n\n"
            "LIMITS: Max 50 Räume, max 20 Msgs/Raum. Kein Pagination-Modus (für ältere Msgs "
            "die anderen Tools nutzen)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="max_rooms",
                type="integer",
                description="Max. Räume (Standard: 10, Max: 50)",
                required=False,
                default=10,
            ),
            ToolParameter(
                name="messages_per_room",
                type="integer",
                description="Letzte N Msgs pro Raum (Standard: 5, Max: 20)",
                required=False,
                default=5,
            ),
            ToolParameter(
                name="type",
                type="string",
                description="Raumtyp: 'group', 'direct' oder leer (alle)",
                required=False,
            ),
            ToolParameter(
                name="since",
                type="string",
                description="Optional: nur Msgs seit diesem ISO-Datum, leere Räume werden ausgeblendet",
                required=False,
            ),
        ],
        handler=webex_recent_activity,
    ))
    count += 1

    return count
