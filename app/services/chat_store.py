"""
Datei-basierte Persistenz für Chat-Sessions.
Jeder Chat wird als JSON-Datei in ./chats/{session_id}.json gespeichert.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional


def _chats_dir() -> Path:
    from app.core.config import settings
    return Path(getattr(settings.server, "chats_directory", "./chats"))


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
    created_at = now
    if path.exists():
        try:
            existing = json.loads(path.read_text("utf-8"))
            created_at = existing.get("created_at", now)
        except Exception:
            pass

    data = {
        "session_id": session_id,
        "title": title,
        "mode": mode,
        "messages_history": messages_history,
        "created_at": created_at,
        "updated_at": now,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_chat(session_id: str) -> Optional[Dict]:
    """Lädt eine Chat-Session von der Festplatte. None wenn nicht vorhanden."""
    path = _chat_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def list_chats() -> List[Dict]:
    """Gibt alle gespeicherten Chats zurück (ohne messages_history), sortiert nach updated_at."""
    chats_dir = _chats_dir()
    chats_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for path in chats_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
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
        data = json.loads(path.read_text("utf-8"))
        data["title"] = title
        data["updated_at"] = time.time()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
