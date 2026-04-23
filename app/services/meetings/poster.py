"""MeetingPoster — postet eine ``MeetingSummary`` in einen Webex-Space.

Nutzt den bestehenden ``webex_client.send_message()``. Der Ziel-Room
kommt entweder aus der Config (``meetings.post_to_room_id``) oder
explizit vom Caller (Override z.B. fuer on-demand ``/summarize``).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.meetings.models import MeetingSummary

logger = logging.getLogger(__name__)


class MeetingPoster:
    """Postet Summaries in konfigurierten oder explizit uebergebenen Room."""

    def __init__(self, *, default_room_id: str = "", parent_id: str = "") -> None:
        self._default_room_id = default_room_id
        self._parent_id = parent_id

    async def post(
        self,
        summary: MeetingSummary,
        *,
        target_room_id: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> Optional[str]:
        """Postet die Summary. Gibt die Message-ID zurueck oder None bei Fehler."""
        from app.services.webex_client import get_webex_client

        room = (target_room_id or self._default_room_id or "").strip()
        if not room:
            logger.warning("[meeting-poster] kein target_room_id gesetzt — skip")
            return None

        body = summary.post_markdown()
        try:
            client = get_webex_client()
            result = await client.send_message(
                room_id=room,
                markdown=body,
                parent_id=parent_id if parent_id is not None else self._parent_id,
            )
            msg_id = str((result or {}).get("id") or "") or None
            logger.info(
                "[meeting-poster] Summary gepostet: room=%s msg=%s chars=%d",
                room[:20], (msg_id or "?")[:20], len(body),
            )
            return msg_id
        except Exception as e:
            logger.error(
                "[meeting-poster] send_message failed (room=%s): %s",
                room[:20], e,
            )
            return None
