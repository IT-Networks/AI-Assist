"""
Webex-Kontext-Builder.

Holt die letzten Nachrichten aus dem aktuellen Webex-Raum oder -Thread und
baut daraus einen ``ChannelContext``, den der Orchestrator als
kanalspezifischen System-Prompt-Zusatz rendert.

Kontrakt:
- Thread-Scope (wenn ``thread_parent_id`` gesetzt) bevorzugt vor Room-Scope.
- Die Trigger-Message selbst wird aus der Liste entfernt — sie wird als
  regulaere ``user_message`` uebergeben und soll nicht doppelt erscheinen.
- Rueckgabe sortiert aelteste → neueste.
- Fehler beim Fetch werden geloggt und liefern None, damit der Bot ohne
  Kontext weiterlaeuft.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.agent.channel_profiles import ChannelContext, ChannelMessage
from app.services.webex_client import WebexClient

logger = logging.getLogger(__name__)


class WebexContextBuilder:
    """Baut ``ChannelContext`` aus Webex-Room- oder Thread-History."""

    def __init__(self, client: WebexClient, bot_person_id: str = "") -> None:
        self._client = client
        self._bot_person_id = bot_person_id

    async def build(
        self,
        room_id: str,
        room_type: str,
        room_title: str,
        trigger_message_id: str,
        trigger_author: str,
        thread_parent_id: str = "",
        max_history: int = 10,
        max_chars_per_message: int = 500,
    ) -> Optional[ChannelContext]:
        try:
            raw = await self._fetch(room_id, thread_parent_id, max_history)
        except Exception as e:
            logger.warning("[webex-context] Fetch fehlgeschlagen: %s", e)
            return None

        msgs: List[ChannelMessage] = []
        for item in raw:
            if item.get("id") == trigger_message_id:
                continue
            text = _clean_text(item.get("text", ""), max_chars_per_message)
            if not text:
                continue
            msgs.append(ChannelMessage(
                author=item.get("person_display_name") or item.get("person_email", "?"),
                text=text,
                timestamp=_parse_ts(item.get("created", "")),
                is_bot=bool(self._bot_person_id and item.get("person_id") == self._bot_person_id),
                is_reply=bool(item.get("parent_id")),
            ))

        # Webex liefert newest-first → oldest-first drehen
        msgs.reverse()

        # Nach Cut auf exakt max_history (Trigger kann ausgefiltert worden sein,
        # d.h. wir holen N+1 und trimmen auf N)
        if len(msgs) > max_history:
            msgs = msgs[-max_history:]

        return ChannelContext(
            channel="webex",
            room_type=room_type or "",
            room_title=room_title or "",
            thread_parent_id=thread_parent_id or "",
            messages=msgs,
            trigger_author=trigger_author or "",
        )

    async def _fetch(
        self,
        room_id: str,
        thread_parent_id: str,
        max_history: int,
    ) -> list:
        # +1 weil die Trigger-Message i.d.R. dabei ist und rausgefiltert wird
        limit = max(1, max_history + 1)
        if thread_parent_id:
            return await self._client.get_thread_replies(
                room_id=room_id,
                parent_id=thread_parent_id,
                max_replies=limit,
            )
        return await self._client.get_messages(
            room_id=room_id,
            max_messages=limit,
        )


# ── Hilfen ──────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        # Webex liefert ISO mit ``Z``-Suffix
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _clean_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) > max_chars:
        t = t[: max_chars - 1].rstrip() + "…"
    return t
