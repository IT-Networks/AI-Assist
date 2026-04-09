"""
Pydantic-Modelle für die Webex Messaging Integration.

Regeln, Message-Snapshots und Stores.
"""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Webex Regeln ───────────────────────────────────────────────────────────────

class WebexRule(BaseModel):
    """Eine Regel für die automatische Todo-Erkennung aus Webex-Nachrichten."""
    id: str = Field(default_factory=lambda: f"wxr-{uuid4().hex[:6]}")
    name: str
    description: str                   # LLM-Prompt: Was soll geprüft werden?
    room_filter: str = ""              # Room-ID oder Room-Name (leer = alle)
    sender_filter: str = ""            # Person-Email (leer = alle)
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class WebexRulesStore(BaseModel):
    """Persistenz-Format für webex_rules.json."""
    rules: List[WebexRule] = []
    version: int = 1


# ── Message Snapshot (für Todo-Persistenz) ─────────────────────────────────────

class WebexMessageSnapshot(BaseModel):
    """Snapshot einer Webex-Nachricht für die Todo-Persistenz."""
    id: str = ""
    room_id: str = ""
    room_title: str = ""
    person_email: str = ""
    person_display_name: str = ""
    text: str = ""
    html: str = ""
    created: str = ""
    parent_id: str = ""                # Thread-Parent
    has_files: bool = False
