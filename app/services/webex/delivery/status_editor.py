"""
StatusEditor — Edit-in-place Status-Message fuer Webex (Sprint 1, A1).

Ersetzt das Pattern "erst 'Agent arbeitet …' als neue Message posten,
dann am Ende eine zweite mit der Antwort" durch **eine** Message, die
waehrend des Agent-Runs per ``PUT /messages/{id}`` aktualisiert wird.

Phasen:
    queued   → thinking → tool:<name> → streaming → done | error

Fallback: Wenn Edit fehlschlaegt (404/429), wird eine neue Message
gepostet und die message_id aktualisiert. Der Aufrufer sieht nichts
davon — der Editor bleibt funktional.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Webex Message-Limit ~7440 Zeichen — wir bleiben darunter.
MAX_EDIT_CHARS = 6500


class StatusEditor:
    """Hilfsklasse fuer Edit-in-place Status-Messages.

    Nicht thread-safe — pro Agent-Run eine Instanz.
    """

    def __init__(
        self,
        client: Any,
        room_id: str,
        parent_id: str = "",
    ) -> None:
        """Initialisiert den Editor.

        Args:
            client: WebexClient-Instanz (Duck-typed; muss ``send_message``,
                ``edit_message`` und ``delete_message`` anbieten).
            room_id: Ziel-Room fuer die Status-Message.
            parent_id: Thread-Parent-ID (Reply-Kontext) oder leer.
        """
        self._client = client
        self._room_id = room_id
        self._parent_id = parent_id
        self._message_id: Optional[str] = None
        self._last_phase: str = ""
        self._last_text: str = ""

    @property
    def message_id(self) -> Optional[str]:
        """Die aktuelle Status-Message-ID (None vor ``start()``)."""
        return self._message_id

    async def start(self, initial_text: str = "⏳ _Queued …_") -> Optional[str]:
        """Postet die initiale Status-Message.

        Returns:
            Die neue ``message_id`` oder None bei Fehler.
        """
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=initial_text,
                parent_id=self._parent_id,
            )
            self._message_id = str(msg.get("id") or "") or None
            self._last_text = initial_text
            self._last_phase = "queued"
            return self._message_id
        except Exception as e:
            logger.warning("[status-editor] initial send failed: %s", e)
            self._message_id = None
            return None

    async def update(self, text: str, *, phase: str = "") -> bool:
        """Aktualisiert die Status-Message (Edit-in-place).

        Args:
            text: Neuer Markdown-Text (max ~6500 Zeichen; wird getruncatet).
            phase: Optionaler Phase-Marker zur Dedup-Erkennung. Wenn
                gleich der letzten Phase, wird KEIN Edit ausgefuehrt
                (spart API-Calls).

        Returns:
            True wenn ein Edit/Send ausgefuehrt wurde, sonst False.
        """
        if phase and phase == self._last_phase:
            return False

        normalized = text[:MAX_EDIT_CHARS] if text else ""
        if normalized == self._last_text:
            return False

        if not self._message_id:
            # Noch keine Message → sendemal posten
            return await self._post_new(normalized, phase=phase)

        try:
            await self._client.edit_message(
                self._message_id,
                room_id=self._room_id,
                markdown=normalized,
            )
            self._last_text = normalized
            if phase:
                self._last_phase = phase
            return True
        except Exception as e:
            # Edit fehlgeschlagen → Fallback: neue Msg
            logger.info(
                "[status-editor] edit failed (%s) → fallback to send", e
            )
            return await self._post_new(normalized, phase=phase)

    async def finalize(self, text: str) -> bool:
        """Setzt den finalen Antwort-Text (ersetzt Status-Message).

        Unterschied zu ``update()``: keine Dedup-Pruefung, garantierter Write.
        """
        normalized = text[:MAX_EDIT_CHARS] if text else ""
        if not self._message_id:
            return await self._post_new(normalized, phase="done")

        try:
            await self._client.edit_message(
                self._message_id,
                room_id=self._room_id,
                markdown=normalized,
            )
            self._last_text = normalized
            self._last_phase = "done"
            return True
        except Exception as e:
            logger.info("[status-editor] finalize-edit failed (%s) → new msg", e)
            return await self._post_new(normalized, phase="done")

    async def delete(self) -> bool:
        """Loescht die Status-Message (z.B. bei silent-error-Policy)."""
        if not self._message_id:
            return False
        try:
            await self._client.delete_message(self._message_id)
            self._message_id = None
            return True
        except Exception as e:
            logger.debug("[status-editor] delete failed: %s", e)
            return False

    # ── Internals ─────────────────────────────────────────────────────────

    async def _post_new(self, text: str, *, phase: str = "") -> bool:
        """Sendet eine neue Status-Message (Fallback bei Edit-Fehler)."""
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=text,
                parent_id=self._parent_id,
            )
            self._message_id = str(msg.get("id") or "") or None
            self._last_text = text
            if phase:
                self._last_phase = phase
            return True
        except Exception as e:
            logger.warning("[status-editor] fallback send failed: %s", e)
            return False
