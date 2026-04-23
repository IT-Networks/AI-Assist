"""ChannelContextBuilder — baut Chat-Verlauf + Bild-Attachments fuer den Agent.

Extrahiert aus ``AssistRoomHandler``:
- ``_build_channel_context`` — Thread/Room-History fuer Orchestrator-Context
- ``_build_attachments_async`` — Bild-Downloads + Base64-Encoding

Non-Image-Attachments werden ignoriert (PDF/Docs koennte spaeter Phase X
werden). Bild-Limit: 10MB/Stueck, max 4 pro Message.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from app.services.webex.runtime.context import HandlerContext

logger = logging.getLogger(__name__)


class ChannelContextBuilder:
    """Erstellt ``ChannelContext`` und multimodale Attachments fuer Orchestrator-Runs."""

    # Hard-Caps fuer Bild-Attachments (Vision-Budget)
    MAX_IMAGES_PER_MSG = 4
    MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

    def __init__(self, context: HandlerContext) -> None:
        self._ctx = context

    async def build_channel_context(self, msg: Dict[str, Any]) -> Optional[Any]:
        """Baut den ``ChannelContext`` fuer den Orchestrator (Webex-Chat-Verlauf).

        Holt die letzten N Messages aus Thread (wenn ``parent_id`` vorhanden)
        oder Room, sortiert aelteste-zuerst und filtert die Trigger-Message raus.
        Bei Fehler / deaktivierter History → None.
        """
        from app.core.config import settings
        from app.services.webex_client import get_webex_client
        from app.services.webex_context import WebexContextBuilder

        style = getattr(settings.webex.bot, "response_style", None)
        if not style or not getattr(style, "include_history", True):
            return None

        msg_id = msg.get("id") or ""
        room_id = msg.get("room_id") or self._ctx.room_id
        if not room_id or not msg_id:
            return None

        builder = WebexContextBuilder(
            client=get_webex_client(),
            bot_person_id=self._ctx.me.get("id", ""),
        )
        try:
            return await builder.build(
                room_id=room_id,
                room_type=msg.get("room_type") or "",
                room_title=self._ctx.room_title,
                trigger_message_id=msg_id,
                trigger_author=msg.get("person_display_name") or msg.get("person_email") or "",
                thread_parent_id=msg.get("parent_id") or "",
                max_history=int(getattr(style, "max_history", 10) or 10),
                max_chars_per_message=int(getattr(style, "max_chars_per_message", 500) or 500),
            )
        except Exception as e:
            logger.warning("[webex-bot] channel-context build fehlgeschlagen: %s", e)
            return None

    async def build_attachments(self, msg: Dict[str, Any]) -> Optional[List[dict]]:
        """Laedt Bild-Attachments der User-Msg herunter und baut multimodal-Attachments.

        Format (s. ``app/services/multimodal.py``):
            ``{"type": "image", "mime": "image/png", "data": "<base64>", "name": "..."}``

        Nicht-Bild-Attachments werden ignoriert.
        """
        from app.services.webex_client import get_webex_client

        file_urls = msg.get("file_urls") or []
        if not file_urls:
            return None

        client = get_webex_client()
        out: List[dict] = []

        for url in file_urls[: self.MAX_IMAGES_PER_MSG]:
            try:
                data, content_type, filename = await client.download_file(url)
            except Exception as e:
                logger.warning("[webex-bot] download_file failed for %s: %s", url[:60], e)
                continue

            mime = (content_type or "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                logger.debug("[webex-bot] Attachment skipped (non-image): %s", mime or "?")
                continue

            if len(data) > self.MAX_IMAGE_BYTES:
                logger.info("[webex-bot] Attachment skipped (too large): %d bytes", len(data))
                continue

            out.append({
                "type": "image",
                "mime": mime,
                "data": base64.b64encode(data).decode("ascii"),
                "name": filename or "attachment",
            })

        return out or None
