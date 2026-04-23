"""HandlerContext — gemeinsamer State-Container fuer Runtime-Komponenten.

Das Ziel ist, die ~20 Instanz-Attribute des frueheren God-Class-Handlers
an einer Stelle zu buendeln, damit Sub-Komponenten nicht jeweils 10+
Konstruktor-Parameter brauchen. Der Context ist mutabel — der
``AssistRoomHandler`` fuellt ihn in ``start()`` / ``_init_sprint1_components``,
die Komponenten lesen daraus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.services.webex.audit import AuditLogger
from app.services.webex.conversation import (
    ConversationBindingStore,
    ConversationRegistry,
)
from app.services.webex.interactive import ApprovalBus
from app.services.webex.safety import ErrorPolicyGate
from app.services.webex.state import (
    DailyUsageStore,
    ProcessedMessagesStore,
    SentMessageCache,
    WebexDb,
)


@dataclass
class HandlerContext:
    """Mutabler Container fuer Bot-Runtime-State.

    Beziehung zum Handler: Der ``AssistRoomHandler`` haelt genau EINE
    Instanz und reicht sie an alle Komponenten weiter. Felder werden von
    ``start()`` gesetzt, von Komponenten nur gelesen (Ausnahme:
    ``daily_usage`` als Legacy-Fallback-Counter; ``room_id`` /
    ``room_title`` werden beim Room-Resolve einmalig gesetzt).
    """

    # ── Identity / Room ──────────────────────────────────────────────────
    me: Dict[str, str] = field(default_factory=dict)
    """Bot-Self-Info aus ``GET /people/me`` (id, email, display_name)."""

    room_id: str = ""
    """Primaerer Bot-Room. In Multi-Conv kann leer sein bzw. als
    Default-Room fuer Greeting/Status dienen."""

    room_title: str = ""

    # ── Sprint-1 Stores (optional; None wenn ``edit_in_place=False``) ────
    db: Optional[WebexDb] = None
    usage_store: Optional[DailyUsageStore] = None
    processed_store: Optional[ProcessedMessagesStore] = None
    sent_cache: Optional[SentMessageCache] = None
    error_gate: Optional[ErrorPolicyGate] = None

    # ── Sprint-2 Stores (optional; None wenn Flag off) ───────────────────
    approval_bus: Optional[ApprovalBus] = None
    audit: Optional[AuditLogger] = None

    # ── Sprint-3 (Multi-Conversation) ────────────────────────────────────
    binding_store: Optional[ConversationBindingStore] = None
    registry: Optional[ConversationRegistry] = None
    room_overrides: Dict[str, Any] = field(default_factory=dict)
    """room_id → WebexRoomOverride config object."""

    # ── Legacy / Fallback ────────────────────────────────────────────────
    daily_usage: Dict[str, int] = field(default_factory=dict)
    """In-Memory Token-Counter wenn ``usage_store`` None ist. Key: "YYYY-MM-DD"."""

    # ── Per-Session Runtime ──────────────────────────────────────────────
    per_session_model: Dict[str, str] = field(default_factory=dict)
    """Vom ``/model``-Slash-Cmd gesetzt, gilt fuer den naechsten Agent-Run."""

    def reset_sprint_stores(self) -> None:
        """Setzt alle Sprint-1/2/3-Stores zurueck (Fehler-Fallback im Init)."""
        self.db = None
        self.usage_store = None
        self.processed_store = None
        self.sent_cache = None
        self.error_gate = None
        self.approval_bus = None
        self.audit = None
        self.binding_store = None
        self.registry = None
        self.room_overrides = {}
