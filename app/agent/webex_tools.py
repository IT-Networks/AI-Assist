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
            "Listet Webex-Räume auf (sortiert nach letzter Aktivität, neueste zuerst). "
            "Zeigt pro Raum: ID, Name, Typ, letzte Aktivität. "
            "Standard: 50 Räume, Hard-Cap 1000. Mit name_contains nach Namensbestandteil filtern.\n\n"
            "WICHTIG: Liefert nur EINE Seite (max 1000) — Räume die seit Monaten inaktiv sind, "
            "können bei vielen Räumen fehlen!\n\n"
            "WANN NUTZEN: Schneller Überblick über die aktivsten Räume.\n"
            "ALTERNATIVEN:\n"
            "- Bestimmten Chat suchen (auch alte/inaktive) → webex_find_chat (paginiert ALLE Räume "
            "+ kann nach Person suchen)\n"
            "- Liste + neueste Msgs pro Raum → webex_recent_activity"
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
            "ALTERNATIVEN:\n"
            "- room_id unbekannt, du kennst aber Person/Raumname → ZUERST webex_find_chat aufrufen, "
            "dann mit der zurückgegebenen room_id hierher\n"
            "- room_id unbekannt + Suche nach Begriff in Nachrichten → webex_search_all_rooms\n"
            "- Übersicht über mehrere Räume → webex_recent_activity"
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
            "ALTERNATIVEN:\n"
            "- Raum-ID unbekannt aber Person/Raumname bekannt → ZUERST webex_find_chat, "
            "dann hierher\n"
            "- Raum-ID unbekannt UND nur Stichwort bekannt → webex_search_all_rooms"
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

    # ── webex_find_chat ───────────────────────────────────────────────────────
    async def webex_find_chat(**kwargs: Any) -> ToolResult:
        """Findet einen Webex-Chat (Raum) über Person oder Raumname.

        Drei Suchstrategien je nach Input:
        1. person enthält '@' → Direkt-Chat via /messages/direct?personEmail=
        2. person ohne '@' → /people?displayName=, dann je Person Direkt-Chat
        3. room_name → paginiert ALLE Räume und filtert nach Title-Substring
        """
        from app.services.webex_client import get_webex_client
        try:
            person = (kwargs.get("person", "") or "").strip()
            room_name = (kwargs.get("room_name", "") or "").strip()

            if not person and not room_name:
                return ToolResult(
                    success=False,
                    error="Mindestens 'person' (Email/Name) ODER 'room_name' angeben."
                )

            client = get_webex_client()
            results: list = []
            people_searched: list = []

            # ── Strategie 1+2: Person → Direkt-Chat ─────────────────────────
            if person:
                if "@" in person:
                    # Direkter Email-Lookup
                    room = await client.find_direct_room_for_person(person)
                    if room:
                        results.append({**room, "match_type": "direct_chat_by_email"})
                else:
                    # Personensuche → Email → Direkt-Chat
                    people = await client.find_person(
                        person, limit=int(kwargs.get("max_people", 5) or 5)
                    )
                    for p in people:
                        people_searched.append({
                            "display_name": p.get("display_name", ""),
                            "email": p.get("primary_email", ""),
                            "department": p.get("department", ""),
                        })
                        email = p.get("primary_email", "")
                        if not email:
                            continue
                        room = await client.find_direct_room_for_person(email)
                        if room:
                            results.append({
                                **room,
                                "person_display_name": p.get("display_name", ""),
                                "person_id": p.get("id", ""),
                                "match_type": "direct_chat_by_name",
                            })

            # ── Strategie 3: Raum-Name (paginiert über alle Räume) ──────────
            if room_name:
                rooms = await client.list_all_rooms(
                    room_type=kwargs.get("type", ""),
                    name_contains=room_name,
                    max_total=int(kwargs.get("max_search", 2000) or 2000),
                    page_size=min(int(kwargs.get("page_size", 1000) or 1000), 1000),
                )
                for r in rooms:
                    results.append({**r, "match_type": "room_by_title"})

            return ToolResult(
                success=True,
                data={
                    "rooms": results,
                    "count": len(results),
                    "person_query": person,
                    "room_name_query": room_name,
                    "people_found": people_searched,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_find_chat",
        description=(
            "Findet einen Webex-Chat/Raum auf RAUM-EBENE (NICHT in Nachrichten suchen!). "
            "Genau das richtige Tool wenn du fragst 'In welchem Chat...' oder 'Mein Direktchat "
            "mit XY' — auch wenn der Chat seit Monaten inaktiv ist und nicht in webex_list_rooms "
            "auftaucht.\n\n"
            "DREI SUCHMODI (mindestens einen Parameter angeben):\n"
            "1) person='vorname.nachname@firma.de' → findet den 1:1-Direkt-Chat (auch sehr alte!)\n"
            "2) person='Max Mustermann' → sucht Person via /people, dann deren Direkt-Chat\n"
            "3) room_name='Projekt Foo' → paginiert ALLE Räume (bis 2000) und filtert nach "
            "Titel-Substring (auch inaktive Räume)\n\n"
            "WANN NUTZEN: Immer wenn du eine room_id BRAUCHST aber noch nicht hast — VOR "
            "webex_read_messages oder webex_search_messages.\n"
            "ABGRENZUNG zu webex_search_all_rooms: Letzteres durchsucht NACHRICHTENTEXT in "
            "vielen Räumen (teuer). webex_find_chat findet RÄUME via Person/Titel (effizient)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="person",
                type="string",
                description=(
                    "Email-Adresse ('a@b.de') ODER Anzeigename ('Max Mustermann') der Person. "
                    "Findet den 1:1-Direkt-Chat — funktioniert auch für seit Monaten inaktive Chats."
                ),
                required=False,
            ),
            ToolParameter(
                name="room_name",
                type="string",
                description=(
                    "Substring des Raum-Titels (case-insensitive). Paginiert über alle Räume "
                    "(nicht nur die zuletzt aktiven)."
                ),
                required=False,
            ),
            ToolParameter(
                name="type",
                type="string",
                description="Optional bei room_name: 'group' oder 'direct' filtern",
                required=False,
            ),
            ToolParameter(
                name="max_people",
                type="integer",
                description="Bei Namens-Suche: max. Treffer bei /people (Std 5, Max 100)",
                required=False,
                default=5,
            ),
            ToolParameter(
                name="max_search",
                type="integer",
                description="Bei room_name: max. zu durchsuchende Räume (Std 2000, Max 10000)",
                required=False,
                default=2000,
            ),
            ToolParameter(
                name="page_size",
                type="integer",
                description="Räume pro API-Call bei Pagination (Std und Max: 1000)",
                required=False,
                default=1000,
            ),
        ],
        handler=webex_find_chat,
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
            "Cross-Room-Suche im NACHRICHTENTEXT: durchsucht mehrere Webex-Räume gleichzeitig. "
            "Listet zuerst Räume (sortiert nach letzter Aktivität), paginiert in jedem Raum "
            "rückwärts und sammelt Treffer. Treffer enthalten 'room_title' für Kontext.\n\n"
            "ABGRENZUNG: Dieses Tool sucht NACHRICHTENINHALT. Wenn du nur einen Chat-Raum "
            "finden willst (z.B. 'Mein Chat mit Müller', 'Der Projekt-Foo Raum'), nutze "
            "stattdessen webex_find_chat — das ist deutlich effizienter und findet auch "
            "inaktive Räume.\n\n"
            "WANN NUTZEN: Wenn du nach einem konkreten BEGRIFF/INHALT in Nachrichten "
            "suchst und nicht weißt in welchem Raum. Default scannt 20 Räume × 2 Seiten = "
            "bis 4000 Msgs.\n"
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
            "- Bestimmten Chat finden (auch inaktiven) → webex_find_chat\n"
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

    # ══════════════════════════════════════════════════════════════════════════
    # Chat-Bot Tools (Phase 3) — nur sinnvoll wenn AI-Assist im Bot-Room laeuft
    # ══════════════════════════════════════════════════════════════════════════

    def _resolve_target(
        kwargs: Any,
    ) -> tuple:
        """Loest room_id + parent_id aus Tool-Kwargs ODER aus dem Bot-ContextVar.

        Prioritaet: explizit uebergebene kwargs > aktueller Bot-Run-Kontext.
        """
        from app.services.webex_bot_service import get_current_bot_context

        ctx = get_current_bot_context() or {}
        room_id = (kwargs.get("room_id") or "").strip() or ctx.get("room_id", "")
        parent_id = (kwargs.get("parent_id") or "").strip() or ctx.get("parent_id", "")
        return room_id, parent_id

    # ── webex_reply ───────────────────────────────────────────────────────────
    async def webex_reply(**kwargs: Any) -> ToolResult:
        from app.services.webex_client import get_webex_client
        try:
            room_id, parent_id = _resolve_target(kwargs)
            if not room_id:
                return ToolResult(
                    success=False,
                    error="Keine room_id verfuegbar (weder als Parameter noch aus Bot-Kontext).",
                )
            text = (kwargs.get("text") or "").strip()
            markdown = (kwargs.get("markdown") or "").strip()
            if not text and not markdown:
                return ToolResult(success=False, error="text oder markdown muss gesetzt sein.")

            # Thread-Reply nur wenn explizit gewollt (Default: in Thread-Kontext bleiben)
            keep_thread = str(kwargs.get("keep_thread", "true")).lower() in ("true", "1", "yes")
            effective_parent = parent_id if keep_thread else ""

            client = get_webex_client()
            result = await client.send_message(
                room_id=room_id,
                text=text,
                markdown=markdown,
                parent_id=effective_parent,
            )
            return ToolResult(
                success=True,
                data={
                    "room_id": room_id,
                    "message_id": result.get("id", ""),
                    "posted_in_thread": bool(effective_parent),
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_reply",
        description=(
            "Sendet eine Zwischen-Status-Nachricht in den aktuellen Webex-Chat (Bot-Room). "
            "Nuetzlich waehrend laufender Tool-Loops um dem User Fortschritt zu zeigen "
            "('Analysiere Logs...', 'Query laeuft...').\n\n"
            "WANN NUTZEN: Explizit bei laufenden Long-Tasks wenn der User ohne Update "
            ">10s warten muesste. NICHT fuer die finale Antwort — die wird automatisch "
            "gepostet.\n\n"
            "CONTEXT: room_id wird automatisch aus dem aktiven Bot-Run uebernommen — "
            "nur explizit setzen wenn du einen anderen Raum adressieren willst. "
            "parent_id wird per Default beibehalten (bleibt im Thread)."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="markdown",
                type="string",
                description="Nachrichtentext in Webex-Markdown (Links, Bullets, Code-Blocks).",
                required=False,
            ),
            ToolParameter(
                name="text",
                type="string",
                description="Alternative Plain-Text-Fassung (Fallback bei fehlendem Markdown-Support).",
                required=False,
            ),
            ToolParameter(
                name="room_id",
                type="string",
                description="Optional: Ziel-Room (Default: aktueller Bot-Room).",
                required=False,
            ),
            ToolParameter(
                name="parent_id",
                type="string",
                description="Optional: Parent-Message-ID fuer Thread-Reply.",
                required=False,
            ),
            ToolParameter(
                name="keep_thread",
                type="string",
                description="true/false — bei true im aktuellen Thread bleiben (Default: true).",
                required=False,
            ),
        ],
        handler=webex_reply,
    ))
    count += 1

    # ── webex_share_diagram ───────────────────────────────────────────────────
    async def webex_share_diagram(**kwargs: Any) -> ToolResult:
        from app.services.diagram_renderer import get_diagram_renderer, source_as_code_block
        from app.services.webex_client import get_webex_client
        try:
            room_id, parent_id = _resolve_target(kwargs)
            if not room_id:
                return ToolResult(
                    success=False,
                    error="Keine room_id verfuegbar (weder als Parameter noch aus Bot-Kontext).",
                )
            source = (kwargs.get("source") or "").strip()
            if not source:
                return ToolResult(success=False, error="source ist erforderlich.")
            fmt = (kwargs.get("format") or "mermaid").strip().lower()
            caption = (kwargs.get("caption") or "").strip()

            renderer = get_diagram_renderer()
            png_path = await renderer.render(source=source, fmt=fmt, caption=caption)

            client = get_webex_client()
            if png_path and png_path.exists():
                await client.upload_file(
                    file_path=str(png_path),
                    room_id=room_id,
                    parent_id=parent_id,
                    caption=caption or f"📊 {fmt.capitalize()} Diagramm",
                )
                return ToolResult(
                    success=True,
                    data={
                        "room_id": room_id,
                        "rendered": True,
                        "path": str(png_path.name),
                        "format": fmt,
                    },
                )

            # Fallback: Source als Code-Block posten
            fallback = source_as_code_block(source, fmt)
            header = caption or f"_(Diagramm-Render fehlgeschlagen — Quellcode als Fallback)_"
            await client.send_message(
                room_id=room_id,
                markdown=f"{header}\n\n{fallback}",
                parent_id=parent_id,
            )
            return ToolResult(
                success=True,
                data={"room_id": room_id, "rendered": False, "fallback": "source", "format": fmt},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_share_diagram",
        description=(
            "Rendert ein Diagramm (Mermaid/Graphviz/PlantUML) zu einem PNG und postet es "
            "als Datei-Anhang in den Webex-Chat. Bei Render-Fehler: Quellcode als "
            "Markdown-Code-Block als Fallback.\n\n"
            "WANN NUTZEN: Um Flows, Architektur, ER-Diagramme, State-Machines oder "
            "Dependency-Graphen visuell zu teilen.\n\n"
            "FORMATE:\n"
            "- mermaid: `graph TD; A-->B`\n"
            "- graphviz (dot): `digraph { A -> B }`\n"
            "- plantuml: `@startuml\\nA -> B\\n@enduml`\n\n"
            "RENDER-PIPELINE: lokale CLI (mmdc/dot) falls installiert → Kroki HTTP-API "
            "→ Source-Fallback. Praktisch immer erfolgreich sofern Internet/Proxy verfuegbar."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="source",
                type="string",
                description="Diagramm-Quelltext (Mermaid/Graphviz/PlantUML-Syntax).",
                required=True,
            ),
            ToolParameter(
                name="format",
                type="string",
                description="Diagramm-Format: mermaid | graphviz | plantuml (Default: mermaid).",
                required=False,
                enum=["mermaid", "graphviz", "plantuml", "dot"],
            ),
            ToolParameter(
                name="caption",
                type="string",
                description="Optionale Bildunterschrift im Chat.",
                required=False,
            ),
            ToolParameter(
                name="room_id",
                type="string",
                description="Optional: Ziel-Room (Default: aktueller Bot-Room).",
                required=False,
            ),
            ToolParameter(
                name="parent_id",
                type="string",
                description="Optional: Parent-Message fuer Thread-Reply.",
                required=False,
            ),
        ],
        handler=webex_share_diagram,
    ))
    count += 1

    # ── webex_share_file ──────────────────────────────────────────────────────
    async def webex_share_file(**kwargs: Any) -> ToolResult:
        from app.services.webex_client import get_webex_client
        from pathlib import Path as _Path
        try:
            room_id, parent_id = _resolve_target(kwargs)
            if not room_id:
                return ToolResult(
                    success=False,
                    error="Keine room_id verfuegbar (weder als Parameter noch aus Bot-Kontext).",
                )
            file_path_raw = (kwargs.get("path") or "").strip()
            if not file_path_raw:
                return ToolResult(success=False, error="'path' ist erforderlich.")
            caption = (kwargs.get("caption") or "").strip()

            project_root = _Path(__file__).parent.parent.parent
            allowed_roots = [
                (project_root / "sandbox_uploads").resolve(),
                (project_root / "reports").resolve(),
                (project_root / "claudedocs").resolve(),
            ]

            candidate = _Path(file_path_raw)
            if not candidate.is_absolute():
                candidate = (project_root / candidate).resolve()
            else:
                candidate = candidate.resolve()

            # Pfad-Whitelist pruefen (Path-Traversal-Schutz)
            if not any(
                str(candidate).startswith(str(root) + _Path("/").anchor if False else str(root))
                for root in allowed_roots
            ):
                # Strikter: candidate muss unter einem der erlaubten Roots liegen
                inside = False
                for root in allowed_roots:
                    try:
                        candidate.relative_to(root)
                        inside = True
                        break
                    except ValueError:
                        continue
                if not inside:
                    return ToolResult(
                        success=False,
                        error=(
                            "Pfad liegt ausserhalb der erlaubten Verzeichnisse "
                            "(sandbox_uploads/, reports/, claudedocs/)."
                        ),
                    )

            if not candidate.exists() or not candidate.is_file():
                return ToolResult(success=False, error=f"Datei nicht gefunden: {candidate}")

            # Groessenlimit: 90 MB (Webex-Hard-Cap ~100 MB)
            size = candidate.stat().st_size
            if size > 90 * 1024 * 1024:
                return ToolResult(
                    success=False,
                    error=f"Datei zu gross: {size} Bytes (max ~90 MB).",
                )

            client = get_webex_client()
            result = await client.upload_file(
                file_path=str(candidate),
                room_id=room_id,
                parent_id=parent_id,
                caption=caption,
            )
            return ToolResult(
                success=True,
                data={
                    "room_id": room_id,
                    "file": candidate.name,
                    "size": size,
                    "message_id": result.get("id", ""),
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_share_file",
        description=(
            "Postet eine Datei aus dem Projekt-Sandbox als Anhang in den aktuellen "
            "Webex-Bot-Chat.\n\n"
            "PFAD-WHITELIST (Security): Nur Dateien unter `sandbox_uploads/`, `reports/` "
            "oder `claudedocs/` koennen geteilt werden. Relative Pfade werden gegen "
            "das Projekt-Root aufgeloest. Pfade ausserhalb dieser Roots werden abgelehnt.\n\n"
            "LIMITS: Max ~90 MB (Webex-Hard-Cap 100 MB).\n\n"
            "WANN NUTZEN: Generierte Reports/CSVs/PDFs teilen; Screenshots oder Log-Ausschnitte "
            "die als Datei vorliegen."
        ),
        category=ToolCategory.FILE,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description=(
                    "Datei-Pfad (relativ zum Projekt-Root oder absolut). "
                    "Muss unter sandbox_uploads/, reports/ oder claudedocs/ liegen."
                ),
                required=True,
            ),
            ToolParameter(
                name="caption",
                type="string",
                description="Optionale Bildunterschrift/Kommentar.",
                required=False,
            ),
            ToolParameter(
                name="room_id",
                type="string",
                description="Optional: Ziel-Room (Default: aktueller Bot-Room).",
                required=False,
            ),
            ToolParameter(
                name="parent_id",
                type="string",
                description="Optional: Parent-Message fuer Thread-Reply.",
                required=False,
            ),
        ],
        handler=webex_share_file,
    ))
    count += 1

    return count
