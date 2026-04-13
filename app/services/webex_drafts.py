"""
Lokaler Draft-Store für Webex-Nachrichten.

Webex hat im Gegensatz zu Exchange keinen serverseitigen Draft-Folder. Dieser
Store hält Entwürfe in einer JSON-Datei (webex_drafts.json im Projekt-Root).
Drafts werden NIE automatisch an Webex gesendet - sie sind reiner lokaler State.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_DRAFTS_FILE = Path(__file__).parent.parent.parent / "webex_drafts.json"


def _load_all() -> List[dict]:
    """Lädt alle Drafts aus der JSON-Datei."""
    if not _DRAFTS_FILE.exists():
        return []
    try:
        data = json.loads(_DRAFTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning("webex_drafts.json laden fehlgeschlagen: %s", e)
        return []


def _save_all(drafts: List[dict]) -> None:
    """Persistiert alle Drafts in die JSON-Datei."""
    try:
        _DRAFTS_FILE.write_text(
            json.dumps(drafts, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error("webex_drafts.json speichern fehlgeschlagen: %s", e)
        raise


def add_draft(
    room_id: str,
    text: str,
    room_title: str = "",
    markdown: str = "",
    parent_id: str = "",
) -> dict:
    """Legt einen neuen Draft an und persistiert ihn.

    Args:
        room_id: Webex Room-ID
        text: Plaintext der Nachricht
        room_title: Optionaler Raumname für die UI
        markdown: Optionale Markdown-Variante (überschreibt text bei Webex-Render)
        parent_id: Optional - Message-ID falls der Draft eine Thread-Antwort werden soll

    Returns:
        Der erstellte Draft als dict.
    """
    draft = {
        "id": str(uuid.uuid4()),
        "room_id": room_id,
        "room_title": room_title,
        "text": text,
        "markdown": markdown,
        "parent_id": parent_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "draft",  # explizit: NIE versendet
    }

    drafts = _load_all()
    drafts.append(draft)
    _save_all(drafts)
    logger.info("Webex-Draft angelegt: id=%s room=%s", draft["id"][:8], room_id[:20])
    return draft


def list_drafts(room_id: str = "") -> List[dict]:
    """Listet alle Drafts (optional gefiltert nach room_id)."""
    drafts = _load_all()
    if room_id:
        drafts = [d for d in drafts if d.get("room_id") == room_id]
    # Neueste zuerst
    drafts.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return drafts


def get_draft(draft_id: str) -> Optional[dict]:
    """Holt einen Draft per ID."""
    for d in _load_all():
        if d.get("id") == draft_id:
            return d
    return None


def delete_draft(draft_id: str) -> bool:
    """Löscht einen Draft per ID. Gibt True zurück wenn gelöscht."""
    drafts = _load_all()
    new_drafts = [d for d in drafts if d.get("id") != draft_id]
    if len(new_drafts) == len(drafts):
        return False
    _save_all(new_drafts)
    logger.info("Webex-Draft gelöscht: id=%s", draft_id[:8])
    return True
