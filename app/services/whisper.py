"""
Audio-Transkription via Whisper (OpenAI-kompatible API).

Unterstützt:
- Lokales Whisper (faster-whisper via OpenAI-kompatiblem Endpunkt)
- OpenAI Whisper API
- vLLM Whisper-Endpunkt

Browser-Aufnahmen kommen als WebM/Opus (.weba) — diese werden vor dem
Whisper-Call zu WAV konvertiert, da viele Whisper-Server WebM nicht
dekodieren können ("Audio decoder exception").

Konvertierungs-Priorität: ffmpeg > pydub > direkt senden (Fallback)
"""

import asyncio
import base64
import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx

# WICHTIG: Modul-Import statt Objekt-Import!
# `from app.core.config import settings` würde eine lokale Referenz binden,
# die nach Settings-Reload (via UI) stale wird.
import app.core.config as _config

logger = logging.getLogger(__name__)

# MIME → Dateiendung
_EXT_MAP = {
    "audio/webm": "webm",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}

# Formate die Konvertierung brauchen bevor Whisper sie verarbeiten kann
_NEEDS_CONVERSION = {"webm", "weba"}

# Cache: ffmpeg verfügbar?
_ffmpeg_path: Optional[str] = None
_ffmpeg_checked = False


def _find_ffmpeg() -> Optional[str]:
    """Sucht ffmpeg im PATH."""
    global _ffmpeg_path, _ffmpeg_checked
    if _ffmpeg_checked:
        return _ffmpeg_path
    _ffmpeg_checked = True
    _ffmpeg_path = shutil.which("ffmpeg")
    if _ffmpeg_path:
        logger.info(f"[whisper] ffmpeg gefunden: {_ffmpeg_path}")
    else:
        logger.warning("[whisper] ffmpeg nicht gefunden — WebM-Konvertierung eingeschränkt")
    return _ffmpeg_path


def _convert_to_wav_ffmpeg(audio_bytes: bytes, input_ext: str) -> Optional[bytes]:
    """
    Konvertiert Audio zu WAV via ffmpeg (subprocess).

    Dies ist die zuverlässigste Methode — ffmpeg unterstützt alle
    gängigen Formate inkl. WebM/Opus.
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / f"input.{input_ext}"
            output_path = Path(tmpdir) / "output.wav"

            input_path.write_bytes(audio_bytes)

            result = subprocess.run(
                [
                    ffmpeg, "-y",
                    "-i", str(input_path),
                    "-ar", "16000",       # 16kHz — optimal für Whisper
                    "-ac", "1",           # Mono
                    "-c:a", "pcm_s16le",  # 16-bit PCM WAV
                    str(output_path),
                ],
                capture_output=True,
                timeout=30,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")[:500]
                logger.warning(f"[whisper] ffmpeg Fehler: {stderr}")
                return None

            wav_bytes = output_path.read_bytes()
            logger.info(
                f"[whisper] ffmpeg Konvertierung: {input_ext} "
                f"({len(audio_bytes)} bytes) → WAV ({len(wav_bytes)} bytes)"
            )
            return wav_bytes

    except subprocess.TimeoutExpired:
        logger.warning("[whisper] ffmpeg Timeout (30s)")
        return None
    except Exception as e:
        logger.warning(f"[whisper] ffmpeg Konvertierung fehlgeschlagen: {e}")
        return None


def _convert_to_wav_pydub(audio_bytes: bytes, input_ext: str) -> Optional[bytes]:
    """Fallback: Konvertierung via pydub (benötigt pydub + ffmpeg)."""
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=input_ext)
        # Whisper-optimiert: 16kHz Mono
        audio = audio.set_frame_rate(16000).set_channels(1)
        wav_buffer = io.BytesIO()
        audio.export(wav_buffer, format="wav")
        wav_buffer.seek(0)
        wav_bytes = wav_buffer.read()
        logger.info(
            f"[whisper] pydub Konvertierung: {input_ext} "
            f"({len(audio_bytes)} bytes) → WAV ({len(wav_bytes)} bytes)"
        )
        return wav_bytes
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"[whisper] pydub Konvertierung fehlgeschlagen: {e}")
        return None


def _convert_audio_to_wav(audio_bytes: bytes, input_ext: str) -> Optional[bytes]:
    """
    Konvertiert Audio zu WAV. Probiert ffmpeg, dann pydub.

    Returns:
        WAV-Bytes oder None wenn Konvertierung nicht möglich.
    """
    # 1. ffmpeg (zuverlässigste Methode)
    wav = _convert_to_wav_ffmpeg(audio_bytes, input_ext)
    if wav:
        return wav

    # 2. pydub (braucht auch ffmpeg, aber manchmal anders verfügbar)
    wav = _convert_to_wav_pydub(audio_bytes, input_ext)
    if wav:
        return wav

    logger.error(
        f"[whisper] Konvertierung von {input_ext} zu WAV fehlgeschlagen. "
        "Bitte ffmpeg installieren: apt install ffmpeg / brew install ffmpeg"
    )
    return None


async def transcribe_audio(audio_base64: str, mime: str, language: str = "de") -> Optional[str]:
    """
    Transkribiert Base64-kodiertes Audio via Whisper API.

    Flow:
    1. Base64 dekodieren
    2. WebM/WEBA → WAV konvertieren (ffmpeg/pydub)
    3. WAV an Whisper-Server senden
    4. Transkription zurückgeben

    Args:
        audio_base64: Base64-kodierte Audio-Daten
        mime: MIME-Type des Audios
        language: Sprache für Transkription (default: de)

    Returns:
        Transkribierter Text oder None bei Fehler
    """
    settings = _config.settings
    if not getattr(settings, "whisper", None) or not settings.whisper.enabled:
        logger.warning("[whisper] Whisper nicht konfiguriert — Audio wird übersprungen")
        return None

    try:
        audio_bytes = base64.b64decode(audio_base64)
        ext = _EXT_MAP.get(mime, "webm")
        upload_mime = mime
        upload_filename = f"audio.{ext}"

        logger.info(f"[whisper] Audio empfangen: {len(audio_bytes)} bytes, MIME={mime}, ext={ext}")

        # WebM/Opus/WEBA: Whisper-Server können das oft nicht dekodieren
        # → zu WAV konvertieren (16kHz, Mono, PCM — optimal für Whisper)
        if ext in _NEEDS_CONVERSION:
            wav_bytes = _convert_audio_to_wav(audio_bytes, ext)
            if wav_bytes:
                audio_bytes = wav_bytes
                ext = "wav"
                upload_mime = "audio/wav"
                upload_filename = "audio.wav"
            else:
                logger.warning(f"[whisper] Konvertierung fehlgeschlagen — sende {ext} direkt (kann fehlschlagen)")

        async with httpx.AsyncClient(timeout=120, verify=False) as client:
            headers = {}
            if settings.whisper.api_key and settings.whisper.api_key != "none":
                headers["Authorization"] = f"Bearer {settings.whisper.api_key}"

            logger.info(f"[whisper] Sende an {settings.whisper.base_url}: {upload_filename} ({len(audio_bytes)} bytes)")

            response = await client.post(
                f"{settings.whisper.base_url}/audio/transcriptions",
                headers=headers,
                files={"file": (upload_filename, audio_bytes, upload_mime)},
                data={
                    "model": settings.whisper.model,
                    "language": language,
                },
            )
            response.raise_for_status()
            result = response.json()
            text = result.get("text", "").strip()
            logger.info(f"[whisper] Transkription erfolgreich: {len(text)} Zeichen")
            return text if text else None

    except httpx.HTTPStatusError as e:
        logger.error(f"[whisper] API-Fehler {e.response.status_code}: {e.response.text[:500]}")
        return None
    except Exception as e:
        logger.error(f"[whisper] Transkription fehlgeschlagen: {e}")
        return None
