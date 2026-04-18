"""
LaneDeliverer — OpenClaw-style Reasoning- + Answer-Lane fuer Webex (Sprint 3, B3).

Teilt die Agent-Antwort in zwei getrennte Messages auf:

- **Reasoning-Lane**: editierbare Preview mit Status/Tool/Thinking-Text.
  Wird waehrend des Agent-Runs aktualisiert (mit Edit-Rotation!).
- **Answer-Lane**: die eigentliche finale Antwort. Eine neue Message
  (kein Edit der Reasoning-Lane), damit der Chat-Verlauf sauber bleibt.

Am Ende wird die Reasoning-Lane entweder geloescht (Default) oder
archiviert (Future: retain_preview_on_cleanup aus OpenClaw).

**Edit-Limit-Rotation (Sprint 2.1):**
Die Reasoning-Lane nutzt ``EditCounterBucket`` mit Threshold 9 — bei
Erreichen wird rotiert (alte Reasoning-Msg loeschen, neue posten).
Die Answer-Lane wird nur einmal gepostet, benoetigt keinen Counter.

Interface ist gezielt kompatibel mit ``StatusEditor``, damit der
Handler je nach ``lane_delivery``-Flag das eine oder andere wahlen kann.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from app.services.webex.delivery.edit_counter import EditCounterBucket

logger = logging.getLogger(__name__)


MAX_EDIT_CHARS = 6500

OnNewMessageCallback = Callable[[str], Awaitable[None]]


class LaneDeliverer:
    """Trennt Reasoning und Answer in zwei Messages.

    Nutzung:
        lane = LaneDeliverer(client, room_id, parent_id)
        await lane.start("⏳ Queued …")         # → reasoning-Lane
        await lane.update("🧠 Analyse …")       # → edit reasoning
        await lane.update("🔧 tool: search")    # → edit reasoning
        await lane.finalize("Hier die Antwort") # → answer-Lane (neue Msg), cleanup reasoning
    """

    def __init__(
        self,
        client: Any,
        room_id: str,
        parent_id: str = "",
        *,
        delete_reasoning_on_finalize: bool = True,
        on_new_message: Optional[OnNewMessageCallback] = None,
        edit_threshold: int = EditCounterBucket.DEFAULT_THRESHOLD,
    ) -> None:
        """Initialisiert den Deliverer.

        Args:
            client: WebexClient-Instanz (Duck-typed).
            room_id: Ziel-Room-ID.
            parent_id: Thread-Parent (Reply-Kontext) oder leer.
            delete_reasoning_on_finalize: True → Reasoning-Lane wird bei
                finalize() geloescht (Default). False → bleibt bestehen.
            on_new_message: Async-Callback fuer neue message_ids (initial,
                nach Rotation, + answer-message). SentMessageCache-Tracking.
            edit_threshold: Edits ab der rotiert wird (Default 9).
        """
        self._client = client
        self._room_id = room_id
        self._parent_id = parent_id
        self._delete_reasoning = delete_reasoning_on_finalize
        self._on_new_message = on_new_message
        self._reasoning_id: Optional[str] = None
        self._answer_id: Optional[str] = None
        self._last_reasoning: str = ""
        self._last_phase: str = ""
        self._counter = EditCounterBucket(threshold=edit_threshold)

    # ── Interface (kompatibel mit StatusEditor) ───────────────────────────

    @property
    def message_id(self) -> Optional[str]:
        """Die aktuelle Reasoning-Message-ID (fuer SentCache-Tracking)."""
        return self._reasoning_id

    @property
    def answer_message_id(self) -> Optional[str]:
        """Die finale Answer-Message-ID (None bevor finalize)."""
        return self._answer_id

    @property
    def edit_count(self) -> int:
        """Anzahl Edits auf der Reasoning-Lane (fuer Tests/Debug)."""
        return self._counter.count

    async def start(self, initial_text: str = "⏳ _Queued …_") -> Optional[str]:
        """Postet die initiale Reasoning-Lane-Message."""
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=initial_text,
                parent_id=self._parent_id,
            )
            self._reasoning_id = str(msg.get("id") or "") or None
            self._last_reasoning = initial_text
            self._last_phase = "queued"
            self._counter.reset()
            await self._notify_reasoning()
            return self._reasoning_id
        except Exception as e:
            logger.warning("[lane-deliverer] start failed: %s", e)
            return None

    async def update(self, text: str, *, phase: str = "") -> bool:
        """Aktualisiert die Reasoning-Lane (Edit-in-place mit Auto-Rotation).

        Phase-Dedup verhindert redundante Edits.
        """
        if phase and phase == self._last_phase:
            return False
        normalized = text[:MAX_EDIT_CHARS] if text else ""
        if normalized == self._last_reasoning:
            return False

        if not self._reasoning_id:
            # Noch keine reasoning-Lane → jetzt posten
            return await self._post_new_reasoning(normalized, phase=phase)

        # Proaktive Rotation bevor Webex-10-Edit-Limit
        if self._counter.needs_rotation():
            logger.info(
                "[lane-deliverer] reasoning edit threshold (%d) reached → rotation",
                self._counter.threshold,
            )
            return await self._rotate_reasoning(normalized, phase=phase)

        try:
            await self._client.edit_message(
                self._reasoning_id,
                room_id=self._room_id,
                markdown=normalized,
            )
            self._counter.increment()
            self._last_reasoning = normalized
            if phase:
                self._last_phase = phase
            return True
        except Exception as e:
            logger.info(
                "[lane-deliverer] reasoning edit failed (%s) → rotation fallback", e,
            )
            return await self._rotate_reasoning(normalized, phase=phase)

    async def finalize(self, answer_text: str) -> bool:
        """Finalisiert den Run: Answer als neue Message posten + Reasoning-Cleanup."""
        normalized = answer_text[:MAX_EDIT_CHARS] if answer_text else ""
        posted = await self._post_answer(normalized)

        # Reasoning-Lane aufraeumen (Default: loeschen)
        if self._reasoning_id and self._delete_reasoning:
            try:
                await self._client.delete_message(self._reasoning_id)
                self._reasoning_id = None
            except Exception as e:
                logger.debug("[lane-deliverer] reasoning delete failed: %s", e)

        return posted

    async def delete(self) -> bool:
        """Loescht beide Lanes (z.B. bei silent-error-Policy)."""
        any_deleted = False
        for mid_attr in ("_reasoning_id", "_answer_id"):
            mid = getattr(self, mid_attr, None)
            if not mid:
                continue
            try:
                await self._client.delete_message(mid)
                setattr(self, mid_attr, None)
                any_deleted = True
            except Exception as e:
                logger.debug("[lane-deliverer] delete %s failed: %s", mid_attr, e)
        return any_deleted

    # ── Internals ─────────────────────────────────────────────────────────

    async def _rotate_reasoning(self, text: str, *, phase: str = "") -> bool:
        """Rotation der Reasoning-Lane: neue Msg + Counter-Reset + alte loeschen."""
        old_id = self._reasoning_id
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=text,
                parent_id=self._parent_id,
            )
            self._reasoning_id = str(msg.get("id") or "") or None
        except Exception as e:
            logger.warning("[lane-deliverer] rotation: send new failed: %s", e)
            return False

        self._last_reasoning = text
        if phase:
            self._last_phase = phase
        self._counter.reset()
        await self._notify_reasoning()

        if old_id:
            try:
                await self._client.delete_message(old_id)
            except Exception as e:
                logger.debug(
                    "[lane-deliverer] rotation: delete old %s failed: %s",
                    old_id[:20], e,
                )
        return True

    async def _post_new_reasoning(self, text: str, *, phase: str = "") -> bool:
        """Postet die erste Reasoning-Lane (wenn noch keine existiert)."""
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=text,
                parent_id=self._parent_id,
            )
            self._reasoning_id = str(msg.get("id") or "") or None
            self._last_reasoning = text
            if phase:
                self._last_phase = phase
            self._counter.reset()
            await self._notify_reasoning()
            return True
        except Exception as e:
            logger.warning("[lane-deliverer] first-reasoning post failed: %s", e)
            return False

    async def _post_answer(self, text: str) -> bool:
        """Postet die finale Answer als neue Message (kein Edit)."""
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=text,
                parent_id=self._parent_id,
            )
            self._answer_id = str(msg.get("id") or "") or None
            if self._answer_id and self._on_new_message:
                try:
                    await self._on_new_message(self._answer_id)
                except Exception as e:
                    logger.warning("[lane-deliverer] answer callback failed: %s", e)
            return True
        except Exception as e:
            logger.error("[lane-deliverer] answer post failed: %s", e)
            return False

    async def _notify_reasoning(self) -> None:
        """Ruft den on_new_message-Callback fuer die aktuelle Reasoning-Msg."""
        if not self._on_new_message or not self._reasoning_id:
            return
        try:
            await self._on_new_message(self._reasoning_id)
        except Exception as e:
            logger.warning("[lane-deliverer] reasoning callback failed: %s", e)
