"""
Text-to-Speech via OpenAI-kompatible API (/v1/audio/speech).

Unterstützte Server:
- OpenedAI-Speech (Piper + XTTS)
- Coqui TTS
- Piper TTS
- OpenAI TTS API
- Edge-TTS Wrapper

Die base_url kann direkt auf den Endpunkt zeigen oder auf die Basis-URL.
Bei 404 wird automatisch /v1/audio/speech angehängt.
"""

import logging
from typing import Optional

import httpx

import app.core.config as _config

logger = logging.getLogger(__name__)

# Maximale Textlänge pro TTS-Call (OpenAI Limit: 4096)
MAX_TEXT_LENGTH = 4096


async def synthesize_speech(text: str, voice: str = None) -> Optional[bytes]:
    """
    Konvertiert Text zu Audio via TTS-Server.

    Args:
        text: Zu sprechender Text (wird bei > 4096 Zeichen gekürzt)
        voice: Stimme (None = Config-Default)

    Returns:
        Audio-Bytes im konfigurierten Format (FLAC/MP3/WAV) oder None
    """
    settings = _config.settings
    if not getattr(settings, "tts", None) or not settings.tts.enabled:
        logger.debug("[tts] TTS nicht konfiguriert")
        return None

    if not settings.tts.base_url:
        logger.warning("[tts] TTS aktiviert aber keine base_url konfiguriert")
        return None

    if not text or not text.strip():
        return None

    # Text kürzen wenn zu lang
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH - 3] + "..."
        logger.info(f"[tts] Text gekürzt auf {MAX_TEXT_LENGTH} Zeichen")

    voice = voice or settings.tts.voice or "alloy"
    response_format = settings.tts.response_format or "flac"
    speed = settings.tts.speed if 0.5 <= settings.tts.speed <= 2.0 else 1.0

    payload = {
        "input": text,
        "voice": voice,
        "response_format": response_format,
        "speed": speed,
    }

    headers = {"Content-Type": "application/json"}
    if settings.tts.api_key and settings.tts.api_key != "none":
        headers["Authorization"] = f"Bearer {settings.tts.api_key}"

    # URL-Kandidaten: base_url kann verschieden formatiert sein
    base = settings.tts.base_url.rstrip("/")
    urls = []
    if "/audio/speech" in base:
        urls.append(base)
    else:
        urls.append(f"{base}/audio/speech")
        if "/v1" not in base:
            urls.append(f"{base}/v1/audio/speech")

    try:
        async with httpx.AsyncClient(timeout=60, verify=False) as client:
            for url in urls:
                logger.info(f"[tts] Sende an {url}: {len(text)} Zeichen, voice={voice}, format={response_format}")
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code == 404 and url != urls[-1]:
                        logger.warning(f"[tts] 404 bei {url} — versuche Alternative")
                        continue
                    response.raise_for_status()
                    audio_bytes = response.content
                    logger.info(f"[tts] Audio generiert: {len(audio_bytes)} bytes ({response_format})")
                    return audio_bytes
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404 and url != urls[-1]:
                        continue
                    logger.error(f"[tts] API-Fehler {e.response.status_code} bei {url}: {e.response.text[:300]}")
                    return None

        logger.error(f"[tts] Alle URLs fehlgeschlagen: {urls}")
        return None

    except Exception as e:
        logger.error(f"[tts] Fehler: {e}")
        return None


def get_tts_mime() -> str:
    """Gibt den MIME-Type für das konfigurierte TTS-Format zurück."""
    settings = _config.settings
    fmt = getattr(settings.tts, "response_format", "flac") if hasattr(settings, "tts") else "flac"
    mime_map = {
        "flac": "audio/flac",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/opus",
        "aac": "audio/aac",
    }
    return mime_map.get(fmt, "audio/flac")
