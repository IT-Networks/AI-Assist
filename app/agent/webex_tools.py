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

    # ── webex_create_draft ────────────────────────────────────────────────────
    async def webex_create_draft(**kwargs: Any) -> ToolResult:
        """Erstellt einen lokalen Webex-Nachrichten-Entwurf - sendet NICHTS."""
        from app.services.webex_drafts import add_draft
        try:
            room_id = (kwargs.get("room_id", "") or "").strip()
            text = (kwargs.get("text", "") or "").strip()

            if not room_id:
                return ToolResult(success=False, error="room_id ist erforderlich.")
            if not text and not kwargs.get("markdown", "").strip():
                return ToolResult(
                    success=False,
                    error="Mindestens 'text' oder 'markdown' muss befüllt sein."
                )

            # Optional: Raum-Titel für UI mit-persistieren
            room_title = (kwargs.get("room_title", "") or "").strip()
            if not room_title:
                # Best-Effort: Titel aus Webex holen, scheitert lautlos
                try:
                    from app.services.webex_client import get_webex_client
                    client = get_webex_client()
                    room_data = await client._request("GET", f"/rooms/{room_id}")
                    room_title = room_data.get("title", "")
                except Exception:
                    room_title = ""

            draft = add_draft(
                room_id=room_id,
                text=text,
                room_title=room_title,
                markdown=(kwargs.get("markdown", "") or "").strip(),
                parent_id=(kwargs.get("parent_id", "") or "").strip(),
            )

            return ToolResult(
                success=True,
                data={
                    "draft": draft,
                    "info": (
                        "Entwurf lokal gespeichert. Wird NICHT automatisch an Webex "
                        "gesendet. Verwende webex_list_drafts zum Anzeigen oder "
                        "webex_delete_draft zum Löschen."
                    ),
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_create_draft",
        description=(
            "Erstellt einen LOKALEN Entwurf einer Webex-Nachricht. SENDET NICHT - der Draft "
            "wird ausschließlich in einer lokalen JSON-Datei (webex_drafts.json) gespeichert "
            "und kann später vom Nutzer manuell geprüft, bearbeitet oder gelöscht werden.\n\n"
            "WANN NUTZEN: Wenn du eine Antwort/Nachricht für Webex VORBEREITEN sollst aber "
            "der Nutzer sie selbst kontrollieren/abschicken will. Niemals zum direkten Senden!\n\n"
            "PARAMETER: room_id ist Pflicht (per webex_find_chat oder webex_list_rooms holen). "
            "Mindestens 'text' oder 'markdown' muss befüllt sein. parent_id für Thread-Antworten."
        ),
        category=ToolCategory.SEARCH,
        # Bewusst KEIN is_write_operation=True: schreibt nur lokale Datei, keine
        # externe Wirkung. Bestätigungs-Dialog wäre hier kontraproduktiv.
        parameters=[
            ToolParameter(
                name="room_id",
                type="string",
                description="Webex Room-ID (aus webex_find_chat / webex_list_rooms)",
                required=True,
            ),
            ToolParameter(
                name="text",
                type="string",
                description="Plaintext der Nachricht",
                required=True,
            ),
            ToolParameter(
                name="markdown",
                type="string",
                description=(
                    "Optionale Markdown-Variante (Webex rendert Markdown bevorzugt). "
                    "Wenn gesetzt, sollte 'text' zusätzlich als Fallback gefüllt sein."
                ),
                required=False,
            ),
            ToolParameter(
                name="parent_id",
                type="string",
                description="Optional: Message-ID auf die geantwortet wird (Thread-Reply)",
                required=False,
            ),
            ToolParameter(
                name="room_title",
                type="string",
                description="Optional: Raumname (wird sonst von Webex nachgeladen)",
                required=False,
            ),
        ],
        handler=webex_create_draft,
    ))
    count += 1

    # ── webex_list_drafts ─────────────────────────────────────────────────────
    async def webex_list_drafts(**kwargs: Any) -> ToolResult:
        """Listet alle lokal gespeicherten Webex-Drafts."""
        from app.services.webex_drafts import list_drafts
        try:
            room_id = (kwargs.get("room_id", "") or "").strip()
            drafts = list_drafts(room_id=room_id)
            return ToolResult(
                success=True,
                data={"drafts": drafts, "count": len(drafts), "room_filter": room_id}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_list_drafts",
        description=(
            "Listet alle lokal gespeicherten Webex-Nachrichten-Entwürfe (sortiert: neueste "
            "zuerst). Drafts wurden NIE an Webex gesendet - reiner lokaler State.\n\n"
            "Optional 'room_id' setzen um nur Drafts eines bestimmten Raums zu sehen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="room_id",
                type="string",
                description="Optional: nur Drafts dieses Raums anzeigen",
                required=False,
            ),
        ],
        handler=webex_list_drafts,
    ))
    count += 1

    # ── webex_delete_draft ────────────────────────────────────────────────────
    async def webex_delete_draft(**kwargs: Any) -> ToolResult:
        """Löscht einen lokalen Webex-Draft per ID."""
        from app.services.webex_drafts import delete_draft
        try:
            draft_id = (kwargs.get("draft_id", "") or "").strip()
            if not draft_id:
                return ToolResult(success=False, error="draft_id ist erforderlich.")
            ok = delete_draft(draft_id)
            if not ok:
                return ToolResult(
                    success=False,
                    error=f"Draft mit id={draft_id} nicht gefunden."
                )
            return ToolResult(
                success=True,
                data={"deleted": True, "draft_id": draft_id}
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="webex_delete_draft",
        description=(
            "Löscht einen lokalen Webex-Draft (per draft_id aus webex_list_drafts). "
            "Da Drafts NIE an Webex gesendet wurden, betrifft das Löschen nur den lokalen Store."
        ),
        category=ToolCategory.SEARCH,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="draft_id",
                type="string",
                description="UUID des Drafts (aus webex_list_drafts)",
                required=True,
            ),
        ],
        handler=webex_delete_draft,
    ))
    count += 1

    return count
