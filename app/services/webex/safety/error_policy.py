"""
ErrorPolicyGate — verhindert Error-Spam in Webex-Rooms (Sprint 1, A2).

OpenClaw-aequivalent: ``error-policy.ts`` — entscheidet pro
(Room, Thread, Error-Class) ob eine Fehler-Notiz gepostet wird.

Policies:
- ``silent``  — nie posten (nur loggen)
- ``once``    — erste Meldung posten; weitere der gleichen Klasse
                waehrend des Cooldowns unterdruecken
- ``always``  — jede Fehler-Meldung posten (Legacy-Verhalten)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Dict, Literal, Optional

logger = logging.getLogger(__name__)


Policy = Literal["silent", "once", "always"]


@dataclass(frozen=True)
class ErrorScope:
    """Identifiziert einen Error-Cooldown-Bereich.

    ``error_class`` gruppiert Fehler semantisch — z.B. "rate-limit" und
    "tool-failure" haben eigene Cooldowns, damit ein einzelner
    Tool-Fehler nicht eine Rate-Limit-Warnung unterdrueckt.
    """
    room_id: str
    thread_id: str = ""
    error_class: str = "generic"

    @property
    def key(self) -> str:
        return f"{self.room_id}|{self.thread_id}|{self.error_class}"


class ErrorPolicyGate:
    """Policy-gated Decider: sollen wir den User ueber den Fehler informieren?

    In-Memory (Cooldown-Map). Das ist bewusst so: die Policy soll
    Crash-Resilient neu starten koennen — nach Neustart ist der
    Cooldown frisch, was gewollt ist.
    """

    def __init__(
        self,
        policy: Policy = "once",
        cooldown_seconds: float = 300.0,
    ) -> None:
        """Initialisiert den Gate mit einer Default-Policy.

        Args:
            policy: Globale Default-Policy.
            cooldown_seconds: Cooldown fuer "once"-Policy.
        """
        self._policy: Policy = policy
        self._cooldown = max(0.0, float(cooldown_seconds))
        self._last_post: Dict[str, float] = {}
        self._suppressed_count: Dict[str, int] = {}

    @property
    def policy(self) -> Policy:
        return self._policy

    def set_policy(self, policy: Policy) -> None:
        """Aendert die Policy zur Laufzeit (z.B. via Slash-Command)."""
        self._policy = policy

    def should_post(self, scope: ErrorScope) -> bool:
        """Entscheidet ob die Fehler-Nachricht gepostet werden soll.

        Side-effect: Bei True wird der Cooldown-Timer aktualisiert.
        Bei False wird ein Suppressed-Counter inkrementiert.
        """
        if self._policy == "silent":
            self._suppressed_count[scope.key] = self._suppressed_count.get(scope.key, 0) + 1
            return False
        if self._policy == "always":
            return True

        # "once" mit Cooldown
        now = monotonic()
        last = self._last_post.get(scope.key, 0.0)
        if now - last < self._cooldown:
            self._suppressed_count[scope.key] = self._suppressed_count.get(scope.key, 0) + 1
            return False
        self._last_post[scope.key] = now
        # Cooldown-Start: vorher evtl. gesammelte suppressed zaehlen wir nicht mehr.
        self._suppressed_count.pop(scope.key, None)
        return True

    def suppressed_count(self, scope: ErrorScope) -> int:
        """Anzahl unterdrueckter Fehler seit letztem Post (fuer Logging)."""
        return int(self._suppressed_count.get(scope.key, 0))

    def reset(self, scope: Optional[ErrorScope] = None) -> None:
        """Setzt Cooldown zurueck — global oder fuer einen Scope."""
        if scope is None:
            self._last_post.clear()
            self._suppressed_count.clear()
        else:
            self._last_post.pop(scope.key, None)
            self._suppressed_count.pop(scope.key, None)
