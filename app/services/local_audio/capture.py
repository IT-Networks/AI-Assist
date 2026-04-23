"""DualStreamRecorder — zeichnet Mikrofon + System-Output-Loopback parallel auf.

Design:
- **Mic-Spur**: Standard-Input-Device via ``sounddevice`` (PortAudio).
- **Loopback-Spur**: Default-Render-Endpoint via ``pyaudiowpatch``
  (Windows-WASAPI-Loopback).
- Beide Streams laufen in eigenen Threads; jeder Callback schreibt
  direkt in eine FLAC-Datei via ``soundfile``. Keine RAM-Buffering —
  stundenlanger Mitschnitt geht.
- Beide Streams: 16 kHz Mono, das ist Whisper-Standard.

Graceful Degradation: Wenn eine Library fehlt (non-Windows oder Install
unvollstaendig), wird der Recorder als ``unavailable`` markiert. Aufrufer
bekommen ``RuntimeError`` beim ``start()``; die Detection-Ebene sollte das
vorher mit ``is_available`` abfangen und den Slash-Cmd sauber beantworten.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


TARGET_SAMPLERATE = 16_000  # Whisper-native
TARGET_CHANNELS = 1         # Mono, spart Platz + ausreichend fuer Speech


@dataclass
class CaptureArtifacts:
    """Ergebnis eines Capture-Cycles — Pfade zu den FLAC-Dateien + Meta."""
    meeting_id: str
    mic_path: Path
    remote_path: Path
    sample_rate: int
    started_at_utc: datetime
    stopped_at_utc: datetime

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.stopped_at_utc - self.started_at_utc).total_seconds())


class DualStreamRecorder:
    """Zeichnet Mic + Loopback parallel in getrennte FLAC-Dateien auf."""

    def __init__(self) -> None:
        self._mic_thread: Optional[threading.Thread] = None
        self._loopback_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._active = False
        self._current: Optional[CaptureArtifacts] = None
        self._available = _probe_audio_libs()

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self, *, output_dir: Path, meeting_id: str) -> CaptureArtifacts:
        """Startet beide Capture-Threads. Raise ``RuntimeError`` bei Doppel-Start oder fehlenden Libs."""
        if self._active:
            raise RuntimeError("Capture laeuft bereits — zuerst stop() aufrufen")
        if not self._available:
            raise RuntimeError(
                "Audio-Capture nicht verfuegbar — sounddevice/pyaudiowpatch/soundfile "
                "fehlen oder Plattform nicht Windows"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc)
        mic_path = output_dir / f"{meeting_id}_mic.flac"
        remote_path = output_dir / f"{meeting_id}_remote.flac"

        self._stop_evt.clear()
        self._mic_thread = threading.Thread(
            target=_run_mic_capture,
            args=(mic_path, self._stop_evt),
            name=f"mic-capture-{meeting_id[:8]}",
            daemon=True,
        )
        self._loopback_thread = threading.Thread(
            target=_run_loopback_capture,
            args=(remote_path, self._stop_evt),
            name=f"loopback-capture-{meeting_id[:8]}",
            daemon=True,
        )
        self._mic_thread.start()
        self._loopback_thread.start()
        self._active = True

        # Start-Artefakte; stopped_at wird beim stop() ueberschrieben.
        self._current = CaptureArtifacts(
            meeting_id=meeting_id,
            mic_path=mic_path,
            remote_path=remote_path,
            sample_rate=TARGET_SAMPLERATE,
            started_at_utc=started,
            stopped_at_utc=started,
        )
        logger.info(
            "[capture] gestartet: meeting=%s mic=%s remote=%s",
            meeting_id[:12], mic_path.name, remote_path.name,
        )
        return self._current

    def stop(self, *, join_timeout: float = 5.0) -> Optional[CaptureArtifacts]:
        """Stoppt beide Threads, flusht FLAC-Writer, gibt Artefakte zurueck."""
        if not self._active:
            return None
        self._stop_evt.set()
        if self._mic_thread:
            self._mic_thread.join(timeout=join_timeout)
        if self._loopback_thread:
            self._loopback_thread.join(timeout=join_timeout)

        if self._current:
            self._current.stopped_at_utc = datetime.now(timezone.utc)
        self._active = False
        result = self._current
        self._current = None
        if result:
            logger.info(
                "[capture] gestoppt: meeting=%s dauer=%.1fs",
                result.meeting_id[:12], result.duration_seconds,
            )
        return result


# ── Library-Probe ────────────────────────────────────────────────────────────

def _probe_audio_libs() -> bool:
    """Prueft ob sounddevice + pyaudiowpatch + soundfile importierbar sind."""
    try:
        import sounddevice  # noqa: F401
        import soundfile    # noqa: F401
        import pyaudiowpatch  # noqa: F401
        return True
    except Exception as e:
        logger.info("[capture] audio libs nicht geladen: %s", e)
        return False


# ── Capture-Worker-Threads ──────────────────────────────────────────────────

def _run_mic_capture(output_path: Path, stop_evt: threading.Event) -> None:
    """Liest vom Default-Input und schreibt direkt in FLAC."""
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
        import soundfile as sf     # type: ignore[import-not-found]
    except Exception as e:
        logger.error("[capture] mic: import fail %s", e)
        return

    try:
        with sf.SoundFile(
            str(output_path),
            mode="w",
            samplerate=TARGET_SAMPLERATE,
            channels=TARGET_CHANNELS,
            format="FLAC",
            subtype="PCM_16",
        ) as sf_out:
            def _callback(indata, frames, time_info, status):  # type: ignore[no-untyped-def]
                if status:
                    logger.debug("[capture] mic status: %s", status)
                # sounddevice liefert float32; soundfile schreibt passend um.
                sf_out.write(indata)

            with sd.InputStream(
                samplerate=TARGET_SAMPLERATE,
                channels=TARGET_CHANNELS,
                dtype="float32",
                blocksize=TARGET_SAMPLERATE // 10,  # 100ms-Blocks
                callback=_callback,
            ):
                while not stop_evt.is_set():
                    time.sleep(0.1)
    except Exception as e:
        logger.error("[capture] mic thread crashed: %s", e, exc_info=True)


def _run_loopback_capture(output_path: Path, stop_evt: threading.Event) -> None:
    """Liest vom Default-Render-Endpoint (System-Output) via WASAPI-Loopback."""
    try:
        import pyaudiowpatch as pyaudio  # type: ignore[import-not-found]
        import soundfile as sf           # type: ignore[import-not-found]
        import numpy as np               # type: ignore[import-not-found]
    except Exception as e:
        logger.error("[capture] loopback: import fail %s", e)
        return

    p = None
    stream = None
    try:
        p = pyaudio.PyAudio()
        # Default-Loopback-Device des Default-Wiedergabegeraets finden
        try:
            default_loopback = p.get_default_wasapi_loopback()
        except Exception as e:
            logger.error("[capture] loopback: kein default device: %s", e)
            return

        source_sr = int(default_loopback.get("defaultSampleRate") or TARGET_SAMPLERATE)
        source_ch = int(default_loopback.get("maxInputChannels") or 2)

        with sf.SoundFile(
            str(output_path),
            mode="w",
            samplerate=TARGET_SAMPLERATE,
            channels=TARGET_CHANNELS,
            format="FLAC",
            subtype="PCM_16",
        ) as sf_out:
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=source_ch,
                rate=source_sr,
                input=True,
                frames_per_buffer=source_sr // 10,
                input_device_index=default_loopback["index"],
            )

            # Resampling-Schritt: Source-SR (meist 48000) → 16000, zu Mono.
            # Verhaeltnis-basiertes Down-Sampling per NumPy-Slice: billig und
            # "gut genug" fuer Speech (keine Anti-Alias-Filterung, aber das
            # Rauschen liegt > Nyquist — Whisper ist toleriert).
            ratio = source_sr / TARGET_SAMPLERATE
            while not stop_evt.is_set():
                try:
                    raw = stream.read(source_sr // 10, exception_on_overflow=False)
                except Exception as e:
                    logger.debug("[capture] loopback read skip: %s", e)
                    continue
                buf = np.frombuffer(raw, dtype=np.float32)
                if source_ch > 1:
                    # Interleaved L,R,L,R → Mono via Mittelwert
                    buf = buf.reshape(-1, source_ch).mean(axis=1)
                # Downsample via Index-Sampling
                if ratio > 1.0:
                    indices = (np.arange(0, len(buf) / ratio) * ratio).astype(int)
                    indices = indices[indices < len(buf)]
                    buf = buf[indices]
                sf_out.write(buf.astype(np.float32))
    except Exception as e:
        logger.error("[capture] loopback thread crashed: %s", e, exc_info=True)
    finally:
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if p is not None:
                p.terminate()
        except Exception:
            pass
