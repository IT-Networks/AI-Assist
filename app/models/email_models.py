"""
Datenmodelle für die Exchange E-Mail Integration.

Enthält Models für:
- E-Mail-Regeln (email_rules.json)
- Todo-Items (todos.json)
- Mail-Snapshots für Todo-Persistenz
"""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Attachment Info ────────────────────────────────────────────────────────────

class EmailAttachmentInfo(BaseModel):
    """Attachment-Metadaten (ohne Inhalt)."""
    name: str
    size: int = 0
    content_type: str = ""


# ── Mail Snapshot (für Todo-Persistenz) ────────────────────────────────────────

class MailSnapshot(BaseModel):
    """Snapshot einer E-Mail/Webex-Nachricht für die Todo-Persistenz."""
    subject: str
    sender: str
    sender_name: str = ""
    to: List[str] = []
    cc: List[str] = []
    date: str = ""
    body_text: str = ""
    body_html: str = ""
    attachments: List[EmailAttachmentInfo] = []
    file_urls: List[str] = []          # Webex-Datei-URLs (für Bild-Anzeige)


# ── E-Mail Regeln ──────────────────────────────────────────────────────────────

class EmailRule(BaseModel):
    """Eine Regel für die automatische Todo-Erkennung."""
    id: str = Field(default_factory=lambda: f"rule-{uuid4().hex[:6]}")
    name: str
    description: str                   # LLM-Prompt: Was soll geprüft werden?
    sender_filter: str = ""            # Leer = alle Absender
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class EmailRulesStore(BaseModel):
    """Persistenz-Format für email_rules.json."""
    rules: List[EmailRule] = []
    version: int = 1


# ── Todo Items ─────────────────────────────────────────────────────────────────

class TodoItem(BaseModel):
    """Ein erkanntes Todo aus einer E-Mail oder Webex-Nachricht."""
    id: str = Field(default_factory=lambda: f"todo-{uuid4().hex[:6]}")
    rule_id: str
    rule_name: str
    email_id: str                      # Bei Webex: Message-ID
    subject: str
    sender: str
    sender_name: str = ""
    received_at: str = ""
    todo_text: str                     # Extrahiertes Todo (Kurztext)
    ai_analysis: str = ""             # LLM-Analyse/Begründung
    priority: str = "medium"           # high, medium, low
    deadline: Optional[str] = None     # ISO-Datum oder None
    status: Literal["new", "read", "done"] = "new"
    source: Literal["email", "webex"] = "email"  # Quelle des Todos
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    mail_snapshot: MailSnapshot


class TodoStore(BaseModel):
    """Persistenz-Format für todos.json."""
    todos: List[TodoItem] = []
    last_poll: Optional[str] = None       # Letzter E-Mail-Poll
    last_webex_poll: Optional[str] = None # Letzter Webex-Poll
    processed_email_ids: List[str] = []   # Duplikat-Schutz (Email + Webex)
    version: int = 1
