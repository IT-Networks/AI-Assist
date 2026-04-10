"""
Audio-Transkription via Whisper (OpenAI-kompatible API).

Unterstützt:
- Lokales Whisper (faster-whisper via OpenAI-kompatiblem Endpunkt)
- OpenAI Whisper API
- vLLM Whisper-Endpunkt
"""

import base64
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# MIME → Dateiendung
_EXT_MAP = {
    "audio/webm": "webm",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
}


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

        async with httpx.AsyncClient(timeout=60, verify=False) as client:
            headers = {}
            if settings.whisper.api_key and settings.whisper.api_key != "none":
                headers["Authorization"] = f"Bearer {settings.whisper.api_key}"

            response = await client.post(
                f"{settings.whisper.base_url}/audio/transcriptions",
                headers=headers,
                files={"file": (f"audio.{ext}", audio_bytes, mime)},
                data={
                    "model": settings.whisper.model,
                    "language": language,
                },
            )
            response.raise_for_status()
            result = response.json()
            text = result.get("text", "").strip()
            logger.info(f"[whisper] Transkription: {len(text)} Zeichen")
            return text if text else None

    except Exception as e:
        logger.error(f"[whisper] Transkription fehlgeschlagen: {e}")
        return None
