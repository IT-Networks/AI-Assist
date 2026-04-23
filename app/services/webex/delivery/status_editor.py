"""
StatusEditor — Edit-in-place Status-Message fuer Webex (Sprint 1, A1).

Ersetzt das Pattern "erst 'Agent arbeitet …' als neue Message posten,
dann am Ende eine zweite mit der Antwort" durch **eine** Message, die
waehrend des Agent-Runs per ``PUT /messages/{id}`` aktualisiert wird.

Phasen:
    queued   → thinking → tool:<name> → streaming → done | error

**Edit-Limit-Rotation (Sprint 2.1):**
Webex erlaubt max 10 Edits pro Message. Bei langer Streaming-Generation
erreichen wir das Limit. Der Editor zaehlt Edits via ``EditCounterBucket``
und rotiert proaktiv ab Threshold 9: alte Msg loeschen, neue posten.
User sieht weiterhin "eine Message" — die message_id aendert sich aber
intern. Externe Tracker werden via ``on_new_message``-Callback informiert.

Fallback: Wenn Edit fehlschlaegt (400/404/429), wird ebenfalls rotiert.

**Collapse-Finalizer (Sprint 4 / Phase 4):**
``finalize(body, tool_history=...)`` klappt die Zwischen-Status-Zeilen
hinter einem markdown ``<details>``-Block zusammen. User sieht "Frage →
Antwort + (N Tools verwendet)"; Details bleiben auf Klick expandierbar.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional

from app.services.webex.delivery.edit_counter import EditCounterBucket

logger = logging.getLogger(__name__)


# Webex Message-Limit ~7440 Zeichen — wir bleiben darunter.
MAX_EDIT_CHARS = 6500


# Typ-Alias fuer den new-message-Callback.
OnNewMessageCallback = Callable[[str], Awaitable[None]]


def build_collapsed_tool_summary(tool_history: List[str]) -> str:
    """Baut den Markdown-Suffix mit zusammenklappbarer Tool-Liste.

    Format (Webex-App rendert ``<details>``/``<summary>``):
        <details>
        <summary>🔧 N Tool(s): tool_a, tool_b</summary>

        - tool_a
        - tool_b
        - tool_c

        </details>

    Wenn das Webex-Client die Tags nicht rendert, faellt die Darstellung
    auf den rohen Text zurueck — unschoen aber funktional.
    """
    if not tool_history:
        return ""
    # Dedupe bei behalt der Reihenfolge (sieht der User eher wie "Verlauf")
    seen: set = set()
    ordered: List[str] = []
    for tool in tool_history:
        if tool not in seen:
            seen.add(tool)
            ordered.append(tool)
    preview_list = ", ".join(ordered[:3])
    if len(ordered) > 3:
        preview_list += f", +{len(ordered) - 3}"
    items = "\n".join(f"- `{t}`" for t in tool_history)  # volle Liste mit Dupes
    return (
        "\n\n<details>\n"
        f"<summary>🔧 {len(tool_history)} Tool(s): {preview_list}</summary>\n\n"
        f"{items}\n\n"
        "</details>"
    )


class StatusEditor:
    """Hilfsklasse fuer Edit-in-place Status-Messages mit Auto-Rotation.

    Nicht thread-safe — pro Agent-Run eine Instanz.
    """

    def __init__(
        self,
        client: Any,
        room_id: str,
        parent_id: str = "",
        *,
        on_new_message: Optional[OnNewMessageCallback] = None,
        edit_threshold: int = EditCounterBucket.DEFAULT_THRESHOLD,
    ) -> None:
        """Initialisiert den Editor.

        Args:
            client: WebexClient-Instanz (Duck-typed; muss ``send_message``,
                ``edit_message`` und ``delete_message`` anbieten).
            room_id: Ziel-Room fuer die Status-Message.
            parent_id: Thread-Parent-ID (Reply-Kontext) oder leer.
            on_new_message: Optional async-Callback der bei jedem neuen
                ``message_id`` (initial + nach Rotation) aufgerufen wird.
                Nuetzlich fuer SentMessageCache-Tracking.
            edit_threshold: Ab dieser Edit-Anzahl wird rotiert (Default 9,
                1 Puffer unter Webex-Limit 10).
        """
        self._client = client
        self._room_id = room_id
        self._parent_id = parent_id
        self._on_new_message = on_new_message
        self._message_id: Optional[str] = None
        self._last_phase: str = ""
        self._last_text: str = ""
        self._counter = EditCounterBucket(threshold=edit_threshold)

    @property
    def message_id(self) -> Optional[str]:
        """Die aktuelle Status-Message-ID (None vor ``start()``)."""
        return self._message_id

    @property
    def edit_count(self) -> int:
        """Anzahl Edits auf der aktuellen Message (fuer Tests/Debug)."""
        return self._counter.count

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
            self._counter.reset()
            await self._notify_new_message()
            return self._message_id
        except Exception as e:
            logger.warning("[status-editor] initial send failed: %s", e)
            self._message_id = None
            return None

    async def update(self, text: str, *, phase: str = "") -> bool:
        """Aktualisiert die Status-Message (Edit-in-place oder Rotation).

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
            # Noch keine Message → erstmalig posten
            return await self._post_new(normalized, phase=phase)

        # Proaktive Rotation bevor wir das Webex-10-Edit-Limit reissen.
        if self._counter.needs_rotation():
            logger.info(
                "[status-editor] edit threshold (%d) reached → rotation",
                self._counter.threshold,
            )
            return await self._rotate(normalized, phase=phase)

        try:
            await self._client.edit_message(
                self._message_id,
                room_id=self._room_id,
                markdown=normalized,
            )
            self._counter.increment()
            self._last_text = normalized
            if phase:
                self._last_phase = phase
            return True
        except Exception as e:
            # Edit fehlgeschlagen (400/404/429) → reaktive Rotation
            logger.info(
                "[status-editor] edit failed (%s) → rotation fallback", e,
            )
            return await self._rotate(normalized, phase=phase)

    async def finalize(
        self,
        text: str,
        *,
        tool_history: Optional[List[str]] = None,
    ) -> bool:
        """Setzt den finalen Antwort-Text (ersetzt Status-Message).

        Args:
            text: Finaler Markdown-Body.
            tool_history: Optional Liste der wahrend des Runs verwendeten
                Tool-Namen. Wird als zusammenklappbare ``<details>``-Liste
                angehaengt (Phase 4 Collapse-Finalizer). Ohne oder leer:
                kein Suffix.

        Unterschied zu ``update()``: keine Dedup-Pruefung, garantierter Write.
        Falls Counter bereits Threshold erreicht hat, wird rotiert damit
        Finalize nicht den 10. (limit-reissenden) Edit verursacht.
        """
        # Collapse-Suffix VOR Truncation bauen, damit der Body Prio hat.
        summary = build_collapsed_tool_summary(tool_history or [])
        combined = f"{text}{summary}" if text else summary
        normalized = combined[:MAX_EDIT_CHARS] if combined else ""
        if not self._message_id:
            return await self._post_new(normalized, phase="done")

        # Bei Threshold-Erreichung: rotieren statt editieren
        if self._counter.needs_rotation():
            logger.info(
                "[status-editor] finalize with threshold reached → rotation",
            )
            return await self._rotate(normalized, phase="done")

        try:
            await self._client.edit_message(
                self._message_id,
                room_id=self._room_id,
                markdown=normalized,
            )
            self._counter.increment()
            self._last_text = normalized
            self._last_phase = "done"
            return True
        except Exception as e:
            logger.info(
                "[status-editor] finalize-edit failed (%s) → rotation", e,
            )
            return await self._rotate(normalized, phase="done")

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

    async def _rotate(self, text: str, *, phase: str = "") -> bool:
        """Rotation: neue Msg posten, Counter resetten, alte Msg loeschen.

        Reihenfolge bewusst: erst neuen Post (damit User keine Luecke
        sieht wenn Delete schneller ist), dann alte loeschen.
        """
        old_id = self._message_id
        try:
            msg = await self._client.send_message(
                room_id=self._room_id,
                markdown=text,
                parent_id=self._parent_id,
            )
            self._message_id = str(msg.get("id") or "") or None
        except Exception as e:
            logger.warning("[status-editor] rotation: send new failed: %s", e)
            return False

        self._last_text = text
        if phase:
            self._last_phase = phase
        self._counter.reset()
        await self._notify_new_message()

        # Alte Msg best-effort loeschen
        if old_id:
            try:
                await self._client.delete_message(old_id)
            except Exception as e:
                logger.debug("[status-editor] rotation: delete old %s failed: %s",
                             old_id[:20], e)
        return True

    async def _post_new(self, text: str, *, phase: str = "") -> bool:
        """Sendet die erste Status-Message (wenn noch keine existiert).

        Fuer Rotation wird ``_rotate()`` genutzt — das include Delete der alten.
        """
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
            self._counter.reset()
            await self._notify_new_message()
            return True
        except Exception as e:
            logger.warning("[status-editor] first-post failed: %s", e)
            return False

    async def _notify_new_message(self) -> None:
        """Ruft den on_new_message-Callback bei neuer message_id.

        Fehler im Callback werden geloggt, aber nicht propagiert
        (Rotation soll robust bleiben).
        """
        if not self._on_new_message or not self._message_id:
            return
        try:
            await self._on_new_message(self._message_id)
        except Exception as e:
            logger.warning("[status-editor] on_new_message callback failed: %s", e)
