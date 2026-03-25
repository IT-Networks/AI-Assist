"""
Datei-basierte Persistenz für Chat-Sessions.
Jeder Chat wird als JSON-Datei in ./chats/{session_id}.json gespeichert.
"""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from app.utils.json_utils import json_loads, json_dumps

logger = logging.getLogger(__name__)

# Gecachter absoluter Pfad für Chats-Directory
_cached_chats_dir: Optional[Path] = None


def _chats_dir() -> Path:
    """
    Gibt das Chats-Directory als absoluten Pfad zurück.
    Cached nach erstem Aufruf um Race-Conditions bei Startup zu vermeiden.
    """
    global _cached_chats_dir
    if _cached_chats_dir is not None:
        return _cached_chats_dir

    from app.core.config import settings
    chats_path = Path(getattr(settings.server, "chats_directory", "./chats"))

    # Zu absolutem Pfad konvertieren, relativ zum Config-File oder CWD
    if not chats_path.is_absolute():
        # Relativ zum Projekt-Root auflösen
        project_root = Path(__file__).parent.parent.parent  # app/services -> project root
        chats_path = (project_root / chats_path).resolve()

    # Directory erstellen falls nicht vorhanden
    chats_path.mkdir(parents=True, exist_ok=True)

    _cached_chats_dir = chats_path
    logger.debug(f"[chat_store] Chats directory: {_cached_chats_dir}")
    return _cached_chats_dir


def _chat_path(session_id: str) -> Path:
    return _chats_dir() / f"{session_id}.json"


def save_chat(
    session_id: str,
    title: str,
    messages_history: List[Dict],
    mode: str = "read_only",
) -> None:
    """Speichert (oder aktualisiert) eine Chat-Session auf der Festplatte."""
    chats_dir = _chats_dir()
    chats_dir.mkdir(parents=True, exist_ok=True)

    path = _chat_path(session_id)
    now = time.time()

    # created_at aus vorhandener Datei übernehmen
    # WICHTIG: Nicht überschreiben wenn bestehender Chat mehr Nachrichten hat!
    created_at = now
    existing_msg_count = 0
    if path.exists():
        try:
            existing = json_loads(path.read_text("utf-8"))
            created_at = existing.get("created_at", now)
            existing_msg_count = len(existing.get("messages_history", []))
        except Exception:
            pass

    new_msg_count = len(messages_history)

    # Schutz gegen Datenverlust: Nicht überschreiben wenn neuer State weniger Nachrichten hat
    # (Race-Condition bei Startup oder unvollständige State-Wiederherstellung)
    if existing_msg_count > 0 and new_msg_count == 0:
        logger.warning(
            f"[chat_store] Refusing to overwrite {session_id} with empty history "
            f"(existing has {existing_msg_count} messages)"
        )
        return

    data = {
        "session_id": session_id,
        "title": title,
        "mode": mode,
        "messages_history": messages_history,
        "created_at": created_at,
        "updated_at": now,
    }
    path.write_text(json_dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.debug(f"[chat_store] Saved chat {session_id}: {new_msg_count} messages")


def load_chat(session_id: str) -> Optional[Dict]:
    """Lädt eine Chat-Session von der Festplatte. None wenn nicht vorhanden."""
    path = _chat_path(session_id)
    if not path.exists():
        logger.debug(f"[chat_store] Chat file not found: {path}")
        return None
    try:
        data = json_loads(path.read_text("utf-8"))
        msg_count = len(data.get("messages_history", []))
        logger.debug(f"[chat_store] Loaded chat {session_id}: {msg_count} messages")
        return data
    except Exception as e:
        logger.warning(f"[chat_store] Failed to load chat {session_id}: {e}")
        return None


def list_chats() -> List[Dict]:
    """Gibt alle gespeicherten Chats zurück (ohne messages_history), sortiert nach updated_at."""
    chats_dir = _chats_dir()
    chats_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for path in chats_dir.glob("*.json"):
        try:
            data = json_loads(path.read_text("utf-8"))
            result.append({
                "session_id": data["session_id"],
                "title": data.get("title", "Chat"),
                "mode": data.get("mode", "read_only"),
                "message_count": len(data.get("messages_history", [])),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
            })
        except Exception:
            pass
    result.sort(key=lambda x: x["updated_at"])
    return result


def delete_chat(session_id: str) -> None:
    """Löscht die gespeicherte Chat-Session von der Festplatte."""
    path = _chat_path(session_id)
    if path.exists():
        path.unlink()


def update_title(session_id: str, title: str) -> bool:
    """Aktualisiert nur den Titel einer gespeicherten Chat-Session."""
    path = _chat_path(session_id)
    if not path.exists():
        return False
    try:
        data = json_loads(path.read_text("utf-8"))
        data["title"] = title
        data["updated_at"] = time.time()
        path.write_text(json_dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
