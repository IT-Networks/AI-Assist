"""PollingLoop — Fallback- bzw. Safety-Poller fuer Webex-Messages.

Wird aktiv wenn (a) ``use_webhooks=False`` oder (b) ``enable_safety_poller=True``
bei aktivem Webhook. Ruft ``get_new_messages_since()`` und delegiert jedes
neue Event an den ``MessageDispatcher``.

Multi-Conv-Mode: pollt alle konfigurierten Rooms.
Single-Room: nur den primaeren Room.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from app.services.webex.runtime.context import HandlerContext
from app.services.webex.runtime.dispatcher import MessageDispatcher

logger = logging.getLogger(__name__)


class PollingLoop:
    """Poller fuer neue Webex-Messages im Bot-Room bzw. konfigurierten Rooms."""

    def __init__(
        self,
        context: HandlerContext,
        dispatcher: MessageDispatcher,
        *,
        is_running_fn: Callable[[], bool],
    ) -> None:
        self._ctx = context
        self._dispatcher = dispatcher
        self._is_running = is_running_fn
        self._last_poll_ts: Optional[datetime] = None

    @property
    def last_poll_ts(self) -> Optional[datetime]:
        return self._last_poll_ts

    def reset_last_poll(self) -> None:
        """Setzt den Zeitstempel auf jetzt (beim Start)."""
        self._last_poll_ts = datetime.now(timezone.utc)

    async def run(self, interval_override: Optional[int] = None) -> None:
        """Haupt-Loop: ruft ``_poll_once()`` in Intervallen."""
        from app.core.config import settings

        base = interval_override if interval_override is not None else settings.webex.bot.fallback_poll_seconds
        interval = max(3, int(base or 10))

        while self._is_running():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[webex-bot] Poll-Fehler: %s", e, exc_info=True)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _poll_once(self) -> None:
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        # Multi-Conversation: alle konfigurierten Rooms einbeziehen.
        if settings.webex.bot.multi_conversation and self._ctx.room_overrides:
            room_ids = [rid for rid in self._ctx.room_overrides.keys() if rid]
            if self._ctx.room_id and self._ctx.room_id not in room_ids:
                room_ids.insert(0, self._ctx.room_id)
        else:
            if not self._ctx.room_id:
                return
            room_ids = [self._ctx.room_id]

        client = get_webex_client()
        since = self._last_poll_ts or (datetime.now(timezone.utc) - timedelta(minutes=5))
        # Zeitstempel VOR dem Poll updaten, um Race-Fenster klein zu halten
        poll_started_at = datetime.now(timezone.utc)

        try:
            messages = await client.get_new_messages_since(
                room_ids=room_ids,
                since=since,
                max_per_room=50,
            )
        except Exception as e:
            logger.warning("[webex-bot] get_new_messages_since failed: %s", e)
            return

        # Aeltester zuerst verarbeiten
        messages.sort(key=lambda m: m.get("created", ""))

        for msg in messages:
            try:
                await self._dispatcher.dispatch(msg)
            except Exception as e:
                logger.error(
                    "[webex-bot] dispatch failed for msg %s: %s",
                    (msg.get("id") or "?")[:20], e, exc_info=True,
                )

        self._last_poll_ts = poll_started_at
