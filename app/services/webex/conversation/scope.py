"""
Scope, ConversationKey, ConversationPolicy (Sprint 3).

OpenClaw-Pendant: `DM | Group | Topic` mit Policy-Inheritance
(Account → Room → Thread). Wir nutzen ``DIRECT | GROUP | THREAD``
fuer Webex (Webex kennt Threads als ``parentId``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Scope(str, Enum):
    """Conversation-Scope (analog OpenClaw DM/Group/Topic)."""
    DIRECT = "direct"   # 1:1 Space (Webex room_type="direct")
    GROUP = "group"     # Mehr-Personen-Space, keine Thread-Verschachtelung
    THREAD = "thread"   # Thread (parentId gesetzt) innerhalb eines group-Space

    @classmethod
    def from_room_type(cls, room_type: str, has_thread: bool) -> "Scope":
        """Heuristik: Webex ``roomType`` + parentId → Scope.

        - roomType="direct" → DIRECT
        - roomType="group" + parentId → THREAD
        - sonst → GROUP
        """
        rt = (room_type or "").lower()
        if rt == "direct":
            return cls.DIRECT
        if has_thread:
            return cls.THREAD
        return cls.GROUP


@dataclass(frozen=True)
class ConversationKey:
    """Eindeutiger Schluessel fuer eine Conversation.

    Thread-Chats bekommen einen eigenen Key, damit Policies per-Thread
    differenzierbar sind (z.B. anderes Modell in einem Dev-Thread).
    """
    room_id: str
    thread_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.room_id}:{self.thread_id}" if self.thread_id else self.room_id

    @classmethod
    def from_message(cls, msg: Dict[str, Any]) -> "ConversationKey":
        return cls(
            room_id=str(msg.get("room_id") or ""),
            thread_id=str(msg.get("parent_id") or ""),
        )


@dataclass
class ConversationPolicy:
    """Policy fuer eine Conversation (oder Account/Room-Default).

    ``inherit_from()`` merged ein Child-Override in den Parent. Als
    "unset"-Marker gelten leere Listen/Strings und ``0`` fuer Ints:

    - ``allow_from=[]`` → Parent wird uebernommen
    - ``default_model=""`` → Parent wird uebernommen
    - ``max_history=0`` → Parent wird uebernommen
    - ``daily_token_cap=0`` → Parent wird uebernommen
    - ``error_policy=""`` → Parent wird uebernommen
    - ``require_mention``/``scope``: Child-Wert gewinnt (keine unset-Semantik)
    """
    scope: Scope = Scope.GROUP
    allow_from: List[str] = field(default_factory=list)
    require_mention: bool = False
    default_model: str = ""
    max_history: int = 0                  # 0 = unset
    daily_token_cap: int = 0
    error_policy: str = ""                # "" = unset

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope.value,
            "allow_from": list(self.allow_from),
            "require_mention": self.require_mention,
            "default_model": self.default_model,
            "max_history": self.max_history,
            "daily_token_cap": self.daily_token_cap,
            "error_policy": self.error_policy,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationPolicy":
        return cls(
            scope=Scope(data.get("scope", Scope.GROUP.value)),
            allow_from=list(data.get("allow_from") or []),
            require_mention=bool(data.get("require_mention", False)),
            default_model=str(data.get("default_model") or ""),
            max_history=int(data.get("max_history", 0)),
            daily_token_cap=int(data.get("daily_token_cap", 0)),
            error_policy=str(data.get("error_policy") or ""),
        )

    def inherit_from(self, parent: "ConversationPolicy") -> "ConversationPolicy":
        """Erzeugt eine neue Policy: ``self`` ueberschreibt gesetzte Felder von ``parent``."""
        return ConversationPolicy(
            scope=self.scope,
            allow_from=self.allow_from or list(parent.allow_from),
            require_mention=self.require_mention or parent.require_mention,
            default_model=self.default_model or parent.default_model,
            max_history=self.max_history or parent.max_history,
            daily_token_cap=self.daily_token_cap or parent.daily_token_cap,
            error_policy=self.error_policy or parent.error_policy,
        )

    def is_authorized(self, sender_email: str) -> bool:
        """Prueft ob der Sender laut Allowlist erlaubt ist.

        Leere allow_from = jeder erlaubt. Case-insensitive.
        """
        if not self.allow_from:
            return True
        return (sender_email or "").lower() in {
            s.lower() for s in self.allow_from
        }
