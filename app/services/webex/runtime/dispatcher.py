"""MessageDispatcher — Ingress-Filter + Dedup + Routing.

Extrahiert aus ``AssistRoomHandler._dispatch_incoming``. Der Dispatcher
verantwortet:
1. Echo-Schutz (SentMessageCache + person_id-Match)
2. Room-Filter (Single-Room vs Multi-Conv)
3. Idempotenz via ``ProcessedMessagesStore.claim()`` (C2: atomar)
4. Auth-Check (ConversationPolicy oder allowed_senders)
5. Slash-Command-Routing oder Agent-Run-Start

Der Dispatcher kennt weder Streaming noch Orchestrator — er delegiert
an ``AgentRunner.start()`` fuer den eigentlichen Run.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.webex.conversation import WebexConversation
from app.services.webex.runtime.agent_runner import AgentRunner
from app.services.webex.runtime.context import HandlerContext
from app.services.webex.runtime.slash_commands import SlashCommandRouter

logger = logging.getLogger(__name__)


def _session_id_for(room_id: str, parent_id: str = "") -> str:
    base = f"webex:{room_id}"
    return f"{base}:{parent_id}" if parent_id else base


class MessageDispatcher:
    """Filtert, markiert und leitet eingehende Webex-Nachrichten."""

    PROCESS_KEY_PREFIX = "wx-bot:"
    VERSION_TAG = "v1"

    def __init__(
        self,
        context: HandlerContext,
        *,
        slash_router: SlashCommandRouter,
        agent_runner: AgentRunner,
    ) -> None:
        self._ctx = context
        self._slash_router = slash_router
        self._agent_runner = agent_runner

    async def dispatch(self, msg: Dict[str, Any]) -> None:
        """Filtert, idempotent markiert und leitet eine Nachricht in den Handler."""
        from app.core.config import settings
        from app.services.todo_store import get_todo_store

        msg_id = msg.get("id", "")
        if not msg_id:
            return

        # Sprint 1: SentMessageCache-Echo-Check VOR person_id (faengt Forwards/Copies ab)
        if self._ctx.sent_cache is not None and await self._ctx.sent_cache.contains(msg_id):
            return

        # Echo-Schutz: eigene Nachrichten ignorieren
        if msg.get("person_id") and self._ctx.me.get("id") and msg["person_id"] == self._ctx.me["id"]:
            return

        # Sprint 3: Multi-Conversation-Mode filtert via Registry statt single-room.
        multi_conv = settings.webex.bot.multi_conversation
        msg_room_id = msg.get("room_id") or ""
        conversation: Optional[WebexConversation] = None

        if multi_conv and self._ctx.registry is not None:
            if not msg_room_id or msg_room_id not in self._ctx.room_overrides:
                return
            conversation = await self._ctx.registry.resolve(msg)
            if conversation is None:
                return
            # v5: Idle-Reset-Check vor Dispatch. Registry bumpt Generation
            # wenn last_activity > 24h zurueckliegt; wir laden dann die
            # Conversation neu um die neue Generation + reset_pending zu sehen.
            bumped = await self._ctx.registry.maybe_bump_idle(conversation.conv_key)
            if bumped is not None:
                conversation = await self._ctx.registry.resolve(msg)
                if conversation is None:
                    return
        else:
            if msg_room_id != self._ctx.room_id:
                return

        # Fast-Path: schon verarbeitet? (C2: Claim kommt spaeter als authoritative Gate)
        process_key = f"{self.PROCESS_KEY_PREFIX}{self.VERSION_TAG}:{msg_id}"
        if self._ctx.processed_store is not None:
            if await self._ctx.processed_store.is_processed(process_key):
                return
        else:
            legacy_store = get_todo_store()
            if legacy_store.is_processed(process_key):
                return

        # Owner-Allowlist: Multi-Conv nutzt per-Conv-Policy, sonst Account-Default
        sender = (msg.get("person_email") or "").lower()
        if conversation is not None:
            if not conversation.policy.is_authorized(sender):
                logger.info(
                    "[webex-bot] conv-policy blockt Absender %s (room=%s)",
                    sender or "(empty)", msg_room_id[:20],
                )
                await self._mark_processed(process_key, msg_room_id or self._ctx.room_id)
                return
        else:
            allowed = [s.lower() for s in (settings.webex.bot.allowed_senders or [])]
            if allowed and sender not in allowed:
                logger.info(
                    "[webex-bot] Nachricht von nicht-autorisiertem Absender ignoriert: %s",
                    sender or "(empty)",
                )
                await self._mark_processed(process_key, msg_room_id or self._ctx.room_id)
                return

        # Ab hier: legitime User-Msg. C2: atomarer Claim gegen Race zwischen
        # Webhook und Safety-Poller — nur der Gewinner dispatcht den Agent,
        # sonst liefe derselbe Request zweimal.
        if self._ctx.processed_store is not None:
            claimed = await self._ctx.processed_store.claim(
                process_key, msg_room_id or self._ctx.room_id,
            )
            if not claimed:
                logger.debug(
                    "[webex-bot] msg %s bereits von parallelem Task beansprucht",
                    msg_id[:20],
                )
                return
        else:
            get_todo_store().mark_processed(process_key)

        text = (msg.get("text") or "").strip()
        parent_id = msg.get("parent_id") or ""
        if conversation is not None:
            # v5: effective_session_id inkludiert Generation-Suffix bei >1.
            session_id = conversation.effective_session_id
            target_room_id = conversation.room_id
            # Activity-Timestamp updaten (Basis fuer naechstes Idle-Fenster)
            await self._ctx.registry.touch(conversation.conv_key)
        else:
            session_id = _session_id_for(self._ctx.room_id, parent_id)
            target_room_id = self._ctx.room_id

        logger.info(
            "[webex-bot] IN [%s] %s: %s",
            sender or "?", session_id[-24:], text[:80].replace("\n", " "),
        )

        # Slash-Commands direkt behandeln — ohne Agent
        if text.startswith("/"):
            handled = await self._slash_router.handle(
                text, session_id, parent_id, target_room_id=target_room_id,
            )
            if handled:
                return

        # Sonst: Agent-Run starten (nicht-blockierend)
        await self._agent_runner.start(
            session_id, text, parent_id, msg,
            target_room_id=target_room_id,
            conversation=conversation,
        )

    async def _mark_processed(self, process_key: str, room_id: str) -> None:
        """Markiert einen Key als verarbeitet (fire-and-forget fuer Auth-Reject-Pfad)."""
        if self._ctx.processed_store is not None:
            await self._ctx.processed_store.mark_processed(process_key, room_id=room_id)
        else:
            from app.services.todo_store import get_todo_store
            get_todo_store().mark_processed(process_key)
