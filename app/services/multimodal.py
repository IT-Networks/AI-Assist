"""
Multimodal Message Builder — baut OpenAI-kompatible Content-Arrays.

Unterstützt Bilder (als Base64 image_url) und Audio (nach Transkription als Text).
"""

import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

ContentPart = Dict[str, Any]


def build_user_content(
    text: str,
    attachments: Optional[List[dict]] = None,
) -> Union[str, List[ContentPart]]:
    """
    Baut user message content für OpenAI-kompatible API.

    Ohne Attachments: str (abwärtskompatibel, kein Token-Overhead).
    Mit Bild-Attachments: List[ContentPart] (multimodal).
    Audio-Attachments werden vorher transkribiert und als Text angehängt.

    Args:
        text: User-Nachricht als Text
        attachments: Liste von Attachment-Dicts mit type, mime, data, name

    Returns:
        str oder List[ContentPart] je nach Inhalt
    """
    if not attachments:
        return text

    parts: List[ContentPart] = []
    audio_transcriptions: List[str] = []

    # Audio-Transkriptionen sammeln
    for att in attachments:
        if att["type"] == "audio" and att.get("transcription"):
            label = att.get("name", "Audio")
            audio_transcriptions.append(f"[{label}]: {att['transcription']}")

    # Text-Part (inkl. Audio-Transkriptionen)
    full_text = text
    if audio_transcriptions:
        full_text += "\n\n--- Audio-Transkription ---\n" + "\n".join(audio_transcriptions)

    if full_text.strip():
        parts.append({"type": "text", "text": full_text})

    # Bild-Parts
    has_images = False
    for att in attachments:
        if att["type"] == "image":
            has_images = True
            data_url = f"data:{att['mime']};base64,{att['data']}"
            parts.append({
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                    "detail": "auto",
                }
            })
            logger.info(f"[multimodal] Bild angehängt: {att.get('name', 'unbenannt')} ({att['mime']})")

    # Wenn nur Text (Audio transkribiert, keine Bilder) → str zurückgeben
    if not has_images:
        return full_text

    return parts


def extract_text_from_content(content: Any) -> str:
    """
    Extrahiert Text aus str oder multimodal content array.

    Nützlich für Logging, Token-Counting, Mistral-Sanitizer etc.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content) if content else ""
