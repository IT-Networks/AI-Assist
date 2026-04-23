"""SlashCommandRouter — behandelt ``/``-Kommandos im Webex-Chat.

Unterstuetzte Kommandos:
- ``/help``, ``/?`` — Command-Uebersicht
- ``/new`` — Session zuruecksetzen (Generation +1, neue leere History)
- ``/continue`` — vorige Generation reaktivieren (Generation -1, alte History zurueck)
- ``/cancel`` — laufenden Agent-Run abbrechen
- ``/status`` — Session/Bot-Status
- ``/model <name>`` — LLM-Modell per Session setzen

Der Router kriegt Callbacks (``cancel``, ``get_status``, ``track_sent``) statt
einer Handler-Referenz, um zirkulaere Importe zu vermeiden.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from app.services.webex.runtime.context import HandlerContext

logger = logging.getLogger(__name__)


class SlashCommandRouter:
    """Dispatch-Layer fuer Slash-Commands im Webex-Bot-Chat."""

    def __init__(
        self,
        context: HandlerContext,
        *,
        cancel_fn: Callable[[str], Awaitable[bool]],
        get_status_fn: Callable[[], Dict[str, Any]],
        track_sent_fn: Callable[[Dict[str, Any]], Awaitable[None]],
        meeting_router: Optional[Any] = None,  # MeetingSlashRouter
    ) -> None:
        self._ctx = context
        self._cancel = cancel_fn
        self._get_status = get_status_fn
        self._track_sent = track_sent_fn
        self._meeting_router = meeting_router

    async def handle(
        self,
        text: str,
        session_id: str,
        parent_id: str,
        *,
        target_room_id: str = "",
    ) -> bool:
        """Gibt ``True`` zurueck wenn der Text als Command behandelt wurde."""
        from app.services.chat_store import save_chat, load_chat
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        reply_room_id = target_room_id or self._ctx.room_id

        async def reply(md: str) -> None:
            try:
                result = await client.send_message(
                    room_id=reply_room_id, markdown=md, parent_id=parent_id,
                )
                await self._track_sent(result)
            except Exception as e:
                logger.warning("[webex-bot] reply (slash) fehlgeschlagen: %s", e)

        if cmd in ("/help", "/?"):
            await reply(
                "**AI-Assist Kommandos**\n"
                "- `/new` — neue Session (Historie verwerfen, frischer Kontext)\n"
                "- `/continue` — vorige Session reaktivieren (Undo `/new` / Idle-Reset)\n"
                "- `/cancel` — laufende Anfrage abbrechen\n"
                "- `/status` — Session- und Bot-Status\n"
                "- `/model <name>` — LLM-Modell dieser Session setzen (z.B. `sonnet`)\n"
                "- `/help` — diese Hilfe"
            )
            return True

        if cmd == "/new":
            # v5: Generation bumpen (nicht die Chat-Store-File ueberschreiben,
            # damit /continue die alte History rekonstruieren kann). Der
            # naechste Turn nutzt effective_session_id mit neuer Generation,
            # Orchestrator legt dafuer einen frischen AgentState an.
            new_session_id = await self._bump_generation(manual=True)
            try:
                from app.agent.orchestrator import get_agent_orchestrator
                orch = get_agent_orchestrator()
                # Beide Keys aufraeumen: den alten (der gerade benutzt wurde)
                # UND den neuen (falls vorherige Turns aus Cache kommen).
                if hasattr(orch, "_states"):
                    orch._states.pop(session_id, None)
                    if new_session_id:
                        orch._states.pop(new_session_id, None)
            except Exception:
                pass
            await reply("🔄 Session zurueckgesetzt. Ich bin bereit. _(`/continue` stellt die vorige wieder her.)_")
            return True

        if cmd == "/continue":
            # v5: Generation dekrementieren → vorige Chat-History reaktivieren.
            prev_gen = await self._continue_previous()
            if prev_gen is None:
                await reply("ℹ️ Keine vorige Session zum Wiederherstellen.")
            else:
                # In-memory-State der aktuellen Generation aufraeumen,
                # damit der naechste Turn die vorige History aus chat_store laedt.
                try:
                    from app.agent.orchestrator import get_agent_orchestrator
                    orch = get_agent_orchestrator()
                    if hasattr(orch, "_states"):
                        orch._states.pop(session_id, None)
                except Exception:
                    pass
                suffix = "" if prev_gen <= 1 else f" (Generation {prev_gen})"
                await reply(f"↩️ Vorige Session wiederhergestellt{suffix}.")
            return True

        if cmd == "/cancel":
            cancelled = await self._cancel(self._ctx.room_id)
            await reply(
                "🛑 Laufender Agent-Run abgebrochen."
                if cancelled else
                "ℹ️ Kein laufender Agent-Run."
            )
            return True

        if cmd == "/status":
            chat = load_chat(session_id)
            history_count = len(chat.get("messages_history", [])) if chat else 0
            status = self._get_status()
            await reply(
                f"**Status**\n"
                f"- Room: `{self._ctx.room_title}`\n"
                f"- Session: `{session_id}` ({history_count} Msgs)\n"
                f"- Aktive Runs: `{len(status['active_runs'])}`\n"
                f"- Letzter Poll: `{status['last_poll'] or '—'}`"
            )
            return True

        if cmd == "/model":
            if not arg:
                await reply("Usage: `/model <name>` z.B. `/model sonnet`")
                return True
            self._ctx.per_session_model[session_id] = arg
            await reply(f"🤖 Modell fuer diese Session: `{arg}` (greift ab naechster Anfrage).")
            return True

        # Sprint 5: Meeting-Commands (/record, /summarize) an Sub-Router delegieren
        if self._meeting_router is not None and cmd in ("/record", "/summarize"):
            # Der MeetingSlashRouter kriegt eine passende reply_fn injiziert
            # und behandelt die Kommandos eigenstaendig. Wir nutzen hier die
            # bereits vorhandene reply() Closure, um den gleichen Thread-Kontext
            # (parent_id + target_room_id) beizubehalten.
            self._meeting_router._reply = reply  # rebind per-call
            handled = await self._meeting_router.handle(cmd, arg)
            if handled:
                return True

        return False

    # ── v5: Session-Generation Helpers ──────────────────────────────────────

    async def _resolve_conv_key(self) -> Optional[str]:
        """Ermittelt den conv_key des aktuellen Slash-Kontexts.

        Nur in Multi-Conversation-Mode vorhanden — Single-Room-Bot hat
        keine Bindings. Bei Bedarf wird das Binding implizit gepostet.
        """
        if self._ctx.registry is None or not self._ctx.room_id:
            return None
        # Wir wissen vom Dispatcher, welche Conv aktiv war — der
        # Slash-Command laeuft im gleichen Room, also nimm den primaeren.
        # Thread-spezifische Bindings werden via msg.parent_id erkannt;
        # fuer Slash-Cmds nutzen wir dasselbe Schema.
        # Simplest: iteriere gecachte Conversations, finde den Eintrag
        # dessen session_id zum aktuellen session_id passt.
        for cached in self._ctx.registry.cached_conversations():
            if cached.effective_session_id == self._ctx.room_id:
                return cached.conv_key
        # Fallback: erste gecachte Conversation fuer diesen Room
        for cached in self._ctx.registry.cached_conversations():
            if cached.room_id == self._ctx.room_id:
                return cached.conv_key
        return None

    async def _bump_generation(self, *, manual: bool) -> str:
        """Bumpt Generation (falls Registry vorhanden). Gibt neue effective_session_id zurueck.

        Single-Room-Mode ohne Registry: kein Bump moeglich, Legacy-Verhalten
        (Chat-Store-Reset ueber Legacy-Pfad).
        """
        if self._ctx.registry is None:
            # Legacy-Fallback ohne Registry: leere Chat-History schreiben.
            # Generation-Feature ist nur in Multi-Conv-Mode aktiv.
            return ""
        conv_key = await self._resolve_conv_key()
        if conv_key is None:
            return ""
        await self._ctx.registry.bump_manual(conv_key)
        # Conversation neu resolven, um effective_session_id zu holen
        # (Cache wurde in bump_manual invalidiert).
        for cached in self._ctx.registry.cached_conversations():
            if cached.conv_key == conv_key:
                return cached.effective_session_id
        return ""

    async def _continue_previous(self) -> Optional[int]:
        """Decrementiert Generation. Gibt neue Generation-Nummer zurueck, oder None."""
        if self._ctx.registry is None:
            return None
        conv_key = await self._resolve_conv_key()
        if conv_key is None:
            return None
        return await self._ctx.registry.continue_previous(conv_key)
