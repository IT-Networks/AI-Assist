"""MeetingSlashRouter — Sub-Handler fuer ``/record`` und ``/summarize``.

Wird vom bestehenden ``SlashCommandRouter`` delegiert, wenn ein Command
mit ``/record`` oder ``/summarize`` beginnt. Hier liegt die Logik
(Pfad-B Session-Control + Pfad-A On-Demand-Summary), damit die
Commands nicht den Haupt-Router aufblaehen.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

logger = logging.getLogger(__name__)


class MeetingSlashRouter:
    """Routet ``/record``- und ``/summarize``-Sub-Commands.

    ``local_recorder`` und ``webex_summarizer`` sind optional —
    wenn None, antworten die entsprechenden Commands mit "feature
    nicht aktiviert".
    """

    def __init__(
        self,
        *,
        reply_fn: Callable[[str], Awaitable[None]],
        local_recorder: Any = None,   # LocalCallRecorder | None
        webex_summarizer: Any = None, # WebexMeetingSummarizer | None (Pfad A, spaeter)
    ) -> None:
        self._reply = reply_fn
        self._local_recorder = local_recorder
        self._webex_summarizer = webex_summarizer

    async def handle(self, cmd: str, arg: str) -> bool:
        """Gibt ``True`` zurueck wenn der Command erkannt wurde."""
        if cmd == "/record":
            await self._handle_record(arg)
            return True
        if cmd == "/summarize":
            await self._handle_summarize(arg)
            return True
        return False

    async def _handle_record(self, arg: str) -> None:
        if self._local_recorder is None:
            await self._reply(
                "ℹ️ Lokale Audio-Aufnahme ist nicht aktiviert. "
                "In `config.yaml` unter `meetings.local_audio.enabled: true` aktivieren."
            )
            return
        sub = (arg.split(maxsplit=1)[0] if arg else "").lower()
        try:
            if sub == "on":
                await self._local_recorder.record_on()
                await self._reply("🔴 Aufnahme gestartet.")
            elif sub == "off":
                summary_msg = await self._local_recorder.record_off()
                if summary_msg:
                    await self._reply(f"⏹️ Aufnahme beendet. {summary_msg}")
                else:
                    await self._reply("⏹️ Aufnahme beendet.")
            elif sub == "auto":
                await self._local_recorder.set_auto(True)
                await self._reply(
                    "🟢 Auto-Aufnahme aktiv — startet automatisch bei Webex-Call-Detection."
                )
            elif sub == "manual":
                await self._local_recorder.set_auto(False)
                await self._reply("🟡 Auto-Modus aus. Aufnahme nur noch per `/record on`.")
            elif sub in ("status", ""):
                status = await self._local_recorder.status()
                await self._reply(_format_record_status(status))
            elif sub == "last":
                msg = await self._local_recorder.summarize_last()
                await self._reply(msg)
            else:
                await self._reply(
                    "Unbekanntes `/record`-Kommando. Gueltig: `on`, `off`, `auto`, `manual`, `status`, `last`."
                )
        except Exception as e:
            logger.error("[meeting-router] /record %s failed: %s", sub, e, exc_info=True)
            await self._reply(f"⚠️ `/record {sub}` fehlgeschlagen: {e}")

    async def _handle_summarize(self, arg: str) -> None:
        if self._webex_summarizer is None:
            await self._reply(
                "ℹ️ Webex-Meeting-Summaries via API sind noch nicht aktiviert. "
                "Pfad A wird in der naechsten Iteration gebaut."
            )
            return
        # Platzhalter — wird implementiert in Pfad A (nicht in diesem Sprint)
        await self._reply("Pfad A folgt in der naechsten Iteration.")


def _format_record_status(status: Dict[str, Any]) -> str:
    """Formatiert den Status-Dict fuer Chat-Output."""
    mode = status.get("mode", "unknown")
    lines = [f"**Aufnahme-Status**", f"- Modus: `{mode}`"]
    if status.get("capturing"):
        started = status.get("started_at", "?")
        lines.append(f"- Aufnahme läuft seit: `{started}`")
    last = status.get("last_session")
    if last:
        lines.append(f"- Letzte Session: `{last.get('meeting_id', '?')}` ({last.get('duration_seconds', 0):.0f}s)")
    return "\n".join(lines)
