"""Whisper-Timestamp-Transkription + Merge Mic/Loopback → TranscriptBundle.

Erweitert den bestehenden Whisper-Wrapper um einen Timestamp-Modus
(``response_format=verbose_json``), der pro Segment ``start``/``end``/``text``
liefert. Jede der beiden Spuren (Mic, Loopback) wird einzeln transkribiert,
dann zeitlich gemerged.

Sprecher-Diarisierung: **einfach** — Mic-Output → ``"User"``, Loopback →
``"Remote"``. Fuer Meetings mit 3+ Personen unterscheiden wir nicht
weiter; der Summarizer arbeitet trotzdem mit der groben Trennung gut.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import app.core.config as _config
from app.services.local_audio.capture import CaptureArtifacts
from app.services.meetings.models import TranscriptBundle, TranscriptTurn

logger = logging.getLogger(__name__)


async def transcribe_and_merge(artifacts: CaptureArtifacts) -> TranscriptBundle:
    """Transkribiert beide Spuren parallel, merged zu unified ``TranscriptBundle``.

    Bei Fehler auf einer Spur wird diese einfach leer gelassen — die
    andere bleibt erhalten. Wenn beide failen, gibt's ein leeres Bundle
    mit 0 Turns (Caller kann fallback-en).
    """
    mic_turns_task = _transcribe_with_timestamps(artifacts.mic_path, speaker="User")
    remote_turns_task = _transcribe_with_timestamps(artifacts.remote_path, speaker="Remote")
    mic_turns, remote_turns = await asyncio.gather(
        mic_turns_task, remote_turns_task, return_exceptions=True,
    )

    # Exceptions separat behandeln — nicht die ganze Pipeline killen
    if isinstance(mic_turns, BaseException):
        logger.warning("[transcribe] mic transcribe failed: %s", mic_turns)
        mic_turns = []
    if isinstance(remote_turns, BaseException):
        logger.warning("[transcribe] remote transcribe failed: %s", remote_turns)
        remote_turns = []

    all_turns = sorted(
        list(mic_turns) + list(remote_turns),
        key=lambda t: t.start_seconds,
    )

    return TranscriptBundle(
        source="local_audio",
        meeting_id=artifacts.meeting_id,
        started_at_utc=artifacts.started_at_utc,
        duration_seconds=artifacts.duration_seconds,
        turns=all_turns,
        participants=["User", "Remote"] if (mic_turns or remote_turns) else [],
        source_metadata={
            "mic_path": str(artifacts.mic_path),
            "remote_path": str(artifacts.remote_path),
            "sample_rate": artifacts.sample_rate,
        },
    )


async def _transcribe_with_timestamps(
    audio_path: Path,
    *,
    speaker: str,
    language: str = "de",
) -> List[TranscriptTurn]:
    """Sendet FLAC-Datei an Whisper, holt Segment-Timestamps.

    Nutzt ``response_format=verbose_json`` — gibt pro Segment start/end/text.
    Scheitert still auf leere Liste wenn Whisper nicht erreichbar.
    """
    settings = _config.settings
    if not getattr(settings, "whisper", None) or not settings.whisper.enabled:
        logger.warning("[transcribe] Whisper nicht aktiviert — skip %s", audio_path.name)
        return []

    if not audio_path.exists() or audio_path.stat().st_size < 1024:
        # Zu kleine/leere Datei → nichts zu transkribieren
        logger.debug("[transcribe] skip (empty/missing): %s", audio_path.name)
        return []

    try:
        async with httpx.AsyncClient(timeout=300, verify=False) as client:
            headers: Dict[str, str] = {}
            if settings.whisper.api_key and settings.whisper.api_key != "none":
                headers["Authorization"] = f"Bearer {settings.whisper.api_key}"

            with open(audio_path, "rb") as fh:
                audio_bytes = fh.read()

            data = {
                "language": language,
                "response_format": "verbose_json",
            }
            if settings.whisper.model and settings.whisper.model != "none":
                data["model"] = settings.whisper.model
            files = [("file", (audio_path.name, audio_bytes, "audio/flac"))]

            base = settings.whisper.base_url.rstrip("/")
            url = base if base.endswith("/audio/transcriptions") else f"{base}/audio/transcriptions"

            response = await client.post(url, headers=headers, data=data, files=files)
            if response.status_code >= 400:
                logger.warning(
                    "[transcribe] %s → HTTP %d: %s",
                    audio_path.name, response.status_code, response.text[:200],
                )
                return []
            payload = response.json()
    except Exception as e:
        logger.error("[transcribe] %s failed: %s", audio_path.name, e)
        return []

    return _parse_verbose_json(payload, speaker=speaker)


def _parse_verbose_json(payload: Dict[str, Any], *, speaker: str) -> List[TranscriptTurn]:
    """Konvertiert Whisper verbose_json in ``TranscriptTurn``s.

    Erwartet das OpenAI-kompatible Format:
        {"segments": [{"start": 0.0, "end": 3.2, "text": "...", ...}, ...]}
    Fallback auf leere Liste bei unerwarteter Struktur.
    """
    segments = payload.get("segments") or []
    if not isinstance(segments, list):
        return []
    turns: List[TranscriptTurn] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(seg.get("start") or 0.0)
            end = float(seg.get("end") or start)
        except (TypeError, ValueError):
            continue
        # Whisper liefert avg_logprob; grobe Confidence = exp(avg_logprob) clamped [0,1]
        confidence = 1.0
        try:
            import math
            if "avg_logprob" in seg:
                confidence = max(0.0, min(1.0, math.exp(float(seg["avg_logprob"]))))
        except Exception:
            pass
        turns.append(TranscriptTurn(
            speaker=speaker,
            start_seconds=start,
            end_seconds=end,
            text=text,
            confidence=confidence,
        ))
    return turns
