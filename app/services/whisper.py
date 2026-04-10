"""
Audio-Transkription via Whisper (OpenAI-kompatible API).

Unterstützt:
- Lokales Whisper (faster-whisper via OpenAI-kompatiblem Endpunkt)
- OpenAI Whisper API
- vLLM Whisper-Endpunkt

Whisper akzeptiert: flac, mp3, mp4, mpeg, mpega, m4a, ogg, wav, webm
Browser-Aufnahmen kommen als WebM/Opus — werden ggf. zu WAV konvertiert.
"""

import base64
import io
import logging
import struct
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# MIME → Dateiendung für Whisper-Upload
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

# Formate die Whisper direkt akzeptiert (OpenAI-Spezifikation)
_WHISPER_NATIVE_FORMATS = {"flac", "mp3", "mp4", "mpeg", "m4a", "ogg", "wav", "webm"}


def _convert_webm_to_wav(webm_bytes: bytes) -> Optional[bytes]:
    """
    Konvertiert WebM/Opus zu WAV via pydub (falls verfügbar).

    Fallback: Gibt None zurück wenn pydub nicht installiert.
    """
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(webm_bytes), format="webm")
        wav_buffer = io.BytesIO()
        audio.export(wav_buffer, format="wav")
        wav_buffer.seek(0)
        logger.info(f"[whisper] WebM→WAV Konvertierung: {len(webm_bytes)} → {wav_buffer.getbuffer().nbytes} bytes")
        return wav_buffer.read()
    except ImportError:
        logger.debug("[whisper] pydub nicht installiert — WebM wird direkt gesendet")
        return None
    except Exception as e:
        logger.warning(f"[whisper] WebM→WAV Konvertierung fehlgeschlagen: {e}")
        return None


async def transcribe_audio(audio_base64: str, mime: str, language: str = "de") -> Optional[str]:
    """
    Transkribiert Base64-kodiertes Audio via Whisper API.

    Args:
        audio_base64: Base64-kodierte Audio-Daten
        mime: MIME-Type des Audios
        language: Sprache für Transkription (default: de)

    Returns:
        Transkribierter Text oder None bei Fehler
    """
    if not getattr(settings, "whisper", None) or not settings.whisper.enabled:
        logger.warning("[whisper] Whisper nicht konfiguriert — Audio wird übersprungen")
        return None

    try:
        audio_bytes = base64.b64decode(audio_base64)
        ext = _EXT_MAP.get(mime, "webm")
        upload_mime = mime
        upload_filename = f"audio.{ext}"

        logger.info(f"[whisper] Audio empfangen: {len(audio_bytes)} bytes, MIME={mime}, ext={ext}")

        # WebM/Opus: Manche Whisper-Server (faster-whisper, lokale Instanzen)
        # haben Probleme mit WebM — optional zu WAV konvertieren
        if ext == "webm":
            wav_bytes = _convert_webm_to_wav(audio_bytes)
            if wav_bytes:
                audio_bytes = wav_bytes
                ext = "wav"
                upload_mime = "audio/wav"
                upload_filename = "audio.wav"
                logger.info("[whisper] Verwende konvertiertes WAV statt WebM")

        async with httpx.AsyncClient(timeout=120, verify=False) as client:
            headers = {}
            if settings.whisper.api_key and settings.whisper.api_key != "none":
                headers["Authorization"] = f"Bearer {settings.whisper.api_key}"

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
