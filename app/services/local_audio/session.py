"""LocalCallRecorder — State-Machine + Lifecycle fuer Lokal-Aufnahmen.

Pipeline:
  ``idle`` → /record on          → ``capturing``
  ``idle`` → /record auto + Webex detected → ``capturing``
  ``capturing`` → /record off OR Webex-Call ended → ``processing``
  ``processing`` → Whisper → Summarize → Post → ``idle``

**Kein automatischer Ansage-Post** im Webex-Space. Die aufnehmende Person
informiert Teilnehmer manuell vor Beginn (User-Decision 2026-04-23).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from app.services.local_audio.capture import (
    CaptureArtifacts,
    DualStreamRecorder,
)
from app.services.local_audio.detection import AudioSessionWatcher, CallEvent
from app.services.local_audio.transcribe import transcribe_and_merge
from app.services.meetings.models import MeetingSummary, TranscriptBundle
from app.services.meetings.poster import MeetingPoster
from app.services.meetings.retention import MeetingRetention
from app.services.meetings.summarizer import MeetingSummarizer

logger = logging.getLogger(__name__)


class RecorderMode(str, Enum):
    IDLE = "idle"
    ARMED_MANUAL = "armed-manual"
    ARMED_AUTO = "armed-auto"
    CAPTURING = "capturing"
    PROCESSING = "processing"


@dataclass
class RecordingSessionResult:
    """Resultat eines kompletten Capture-to-Post-Cycles."""
    meeting_id: str
    bundle: Optional[TranscriptBundle]
    summary: Optional[MeetingSummary]
    posted_message_id: Optional[str]
    error: Optional[str] = None
    finished_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LocalCallRecorder:
    """Hohe API fuer Bot-Slash-Cmds — kapselt Watcher + Recorder + Pipeline."""

    def __init__(
        self,
        *,
        output_dir: Path,
        summarizer: MeetingSummarizer,
        poster: MeetingPoster,
        retention: MeetingRetention,
        purge_audio_after_summary: bool = True,
        watcher: Optional[AudioSessionWatcher] = None,
        recorder: Optional[DualStreamRecorder] = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._summarizer = summarizer
        self._poster = poster
        self._retention = retention
        self._purge_audio = purge_audio_after_summary
        self._watcher = watcher or AudioSessionWatcher()
        self._recorder = recorder or DualStreamRecorder()

        self._mode: RecorderMode = RecorderMode.IDLE
        self._auto_enabled: bool = False
        self._watcher_task: Optional[asyncio.Task] = None
        self._current_meeting_id: Optional[str] = None
        self._current_artifacts: Optional[CaptureArtifacts] = None
        self._last_result: Optional[RecordingSessionResult] = None
        self._lock = asyncio.Lock()

    # ── Public API fuer SlashCommandRouter ──────────────────────────────────

    async def record_on(self) -> None:
        """Startet Aufnahme sofort (manueller Modus)."""
        async with self._lock:
            if self._mode == RecorderMode.CAPTURING:
                raise RuntimeError("Aufnahme laeuft bereits")
            if self._mode == RecorderMode.PROCESSING:
                raise RuntimeError("Vorherige Session wird noch verarbeitet")
            await self._start_capture_unlocked(reason="manual")

    async def record_off(self) -> str:
        """Stoppt Aufnahme + triggert Pipeline. Gibt menschlichen Status-Text zurueck."""
        async with self._lock:
            if self._mode != RecorderMode.CAPTURING:
                return "Keine aktive Aufnahme."
            artifacts = self._recorder.stop()
            meeting_id = self._current_meeting_id
            self._current_artifacts = artifacts
            self._mode = RecorderMode.PROCESSING

        # Pipeline ausserhalb des Locks — lange laufend
        result = await self._run_pipeline(artifacts, meeting_id)
        async with self._lock:
            self._mode = RecorderMode.ARMED_AUTO if self._auto_enabled else RecorderMode.IDLE
            self._last_result = result
        return _format_pipeline_summary(result)

    async def set_auto(self, enabled: bool) -> None:
        """Schaltet Auto-Detect-Modus ein/aus."""
        async with self._lock:
            self._auto_enabled = bool(enabled)
            if enabled and self._watcher.is_available:
                if self._watcher_task is None or self._watcher_task.done():
                    self._watcher_task = asyncio.create_task(
                        self._watcher.run_forever(self._on_call_event),
                        name="local-audio-watcher",
                    )
                if self._mode == RecorderMode.IDLE:
                    self._mode = RecorderMode.ARMED_AUTO
            else:
                if self._watcher_task and not self._watcher_task.done():
                    self._watcher.stop()
                    try:
                        await asyncio.wait_for(self._watcher_task, timeout=2.0)
                    except asyncio.TimeoutError:
                        self._watcher_task.cancel()
                self._watcher_task = None
                if self._mode == RecorderMode.ARMED_AUTO:
                    self._mode = RecorderMode.IDLE

    async def status(self) -> Dict[str, Any]:
        async with self._lock:
            info: Dict[str, Any] = {
                "mode": self._mode.value,
                "auto_enabled": self._auto_enabled,
                "capturing": self._mode == RecorderMode.CAPTURING,
                "capture_available": self._recorder.is_available,
                "watcher_available": self._watcher.is_available,
            }
            if self._current_artifacts and self._mode in (RecorderMode.CAPTURING, RecorderMode.PROCESSING):
                info["started_at"] = self._current_artifacts.started_at_utc.isoformat()
                info["meeting_id"] = self._current_meeting_id
            if self._last_result:
                info["last_session"] = {
                    "meeting_id": self._last_result.meeting_id,
                    "duration_seconds": (
                        self._last_result.bundle.duration_seconds
                        if self._last_result.bundle else 0.0
                    ),
                    "posted": bool(self._last_result.posted_message_id),
                    "error": self._last_result.error,
                }
            return info

    async def summarize_last(self) -> str:
        """On-Demand: letzte Capture erneut durch Pipeline jagen (falls Pfad faile)."""
        async with self._lock:
            if not self._last_result or not self._last_result.bundle:
                return "Keine vorherige Session zum Zusammenfassen verfuegbar."
            bundle = self._last_result.bundle
        try:
            summary = await self._summarizer.summarize(bundle)
            msg_id = await self._poster.post(summary)
            return f"📋 Summary gepostet: `{msg_id or '—'}`"
        except Exception as e:
            logger.error("[local-recorder] summarize_last failed: %s", e, exc_info=True)
            return f"⚠️ Re-Summary fehlgeschlagen: {e}"

    async def shutdown(self) -> None:
        """Fuer Bot-Stop: Watcher beenden, laufende Capture stoppen (ohne Pipeline)."""
        async with self._lock:
            if self._watcher_task and not self._watcher_task.done():
                self._watcher.stop()
                try:
                    await asyncio.wait_for(self._watcher_task, timeout=2.0)
                except asyncio.TimeoutError:
                    self._watcher_task.cancel()
                self._watcher_task = None
            if self._mode == RecorderMode.CAPTURING:
                self._recorder.stop()
                self._mode = RecorderMode.IDLE

    # ── Auto-Detection-Event-Handling ───────────────────────────────────────

    async def _on_call_event(self, event: CallEvent) -> None:
        """Wird vom Watcher fuer jedes started/ended-Event gerufen."""
        if not self._auto_enabled:
            return
        async with self._lock:
            if event.kind == "started" and self._mode == RecorderMode.ARMED_AUTO:
                logger.info(
                    "[local-recorder] Auto-Trigger: Webex-Call detected (pid=%d, %s)",
                    event.pid, event.process_name,
                )
                await self._start_capture_unlocked(reason="auto-detect")
            elif event.kind == "ended" and self._mode == RecorderMode.CAPTURING:
                logger.info(
                    "[local-recorder] Auto-Stop: Webex-Call beendet (pid=%d)",
                    event.pid,
                )
                artifacts = self._recorder.stop()
                meeting_id = self._current_meeting_id
                self._current_artifacts = artifacts
                self._mode = RecorderMode.PROCESSING
                # Pipeline im Hintergrund starten — on_event darf nicht blockieren
                asyncio.create_task(
                    self._auto_post_pipeline(artifacts, meeting_id),
                    name=f"auto-pipeline-{(meeting_id or '')[:8]}",
                )

    async def _auto_post_pipeline(
        self, artifacts: Optional[CaptureArtifacts], meeting_id: Optional[str],
    ) -> None:
        result = await self._run_pipeline(artifacts, meeting_id)
        async with self._lock:
            self._last_result = result
            self._mode = RecorderMode.ARMED_AUTO if self._auto_enabled else RecorderMode.IDLE

    # ── Interne Helpers ─────────────────────────────────────────────────────

    async def _start_capture_unlocked(self, *, reason: str) -> None:
        """Interne Start-Logik — Caller haelt das Lock."""
        meeting_id = _gen_meeting_id()
        try:
            self._current_artifacts = self._recorder.start(
                output_dir=self._output_dir, meeting_id=meeting_id,
            )
        except Exception as e:
            self._current_artifacts = None
            self._current_meeting_id = None
            logger.error("[local-recorder] capture start failed (%s): %s", reason, e)
            raise
        self._current_meeting_id = meeting_id
        self._mode = RecorderMode.CAPTURING
        logger.info("[local-recorder] Capturing: meeting=%s (%s)", meeting_id[:12], reason)

    async def _run_pipeline(
        self,
        artifacts: Optional[CaptureArtifacts],
        meeting_id: Optional[str],
    ) -> RecordingSessionResult:
        """Whisper → Summary → Post → Cleanup. Liefert Result-Objekt."""
        if not artifacts or not meeting_id:
            return RecordingSessionResult(
                meeting_id=meeting_id or "?",
                bundle=None, summary=None, posted_message_id=None,
                error="Keine Capture-Artefakte",
            )

        error: Optional[str] = None
        bundle: Optional[TranscriptBundle] = None
        summary: Optional[MeetingSummary] = None
        msg_id: Optional[str] = None

        # 1. Transcribe
        try:
            bundle = await transcribe_and_merge(artifacts)
        except Exception as e:
            error = f"Transkription fehlgeschlagen: {e}"
            logger.error("[local-recorder] transcribe failed: %s", e, exc_info=True)

        # 2. Summarize (nur wenn Bundle vorhanden)
        if bundle and bundle.turns:
            try:
                summary = await self._summarizer.summarize(bundle)
            except Exception as e:
                error = error or f"Summary fehlgeschlagen: {e}"
                logger.error("[local-recorder] summarize failed: %s", e, exc_info=True)

        # 3. Post
        if summary:
            try:
                msg_id = await self._poster.post(summary)
                if not msg_id:
                    error = error or "Webex-Post fehlgeschlagen (kein room konfiguriert?)"
            except Exception as e:
                error = error or f"Post fehlgeschlagen: {e}"
                logger.error("[local-recorder] post failed: %s", e, exc_info=True)

        # 4. Retention: Audio weg nach erfolgreicher Summary
        if summary and self._purge_audio:
            try:
                self._retention.purge_audio_for(meeting_id)
            except Exception as e:
                logger.warning("[local-recorder] audio purge failed: %s", e)

        return RecordingSessionResult(
            meeting_id=meeting_id,
            bundle=bundle,
            summary=summary,
            posted_message_id=msg_id,
            error=error,
        )


def _gen_meeting_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"local-{ts}-{uuid.uuid4().hex[:8]}"


def _format_pipeline_summary(result: RecordingSessionResult) -> str:
    """Menschliche Status-Antwort fuer ``/record off``."""
    if result.error and not result.posted_message_id:
        return f"⚠️ {result.error}"
    if result.posted_message_id:
        turn_count = len(result.bundle.turns) if result.bundle else 0
        return f"✅ Summary gepostet ({turn_count} Turns transkribiert)."
    if result.bundle and not result.summary:
        return "⚠️ Transkribiert, aber Summary fehlgeschlagen. `/record last` nochmal versuchen."
    return "⏳ Pipeline laeuft …"
