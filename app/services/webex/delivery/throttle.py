"""
EditThrottle — drosselt Webex Message-Edits auf ein sicheres Intervall.

Webex erlaubt pro Room ca. 30 Message-Edits/Minute. Bei Token-Streaming
(jeder Agent-Token ist ein Event) wuerden wir ohne Throttle sofort ins
Rate-Limit laufen. Der Throttle prueft zwei Bedingungen:

- **Zeit-Mindestabstand** seit letztem Edit (Default: 1.5s → ~40/min)
- **Mindest-Deltas an Zeichen** seit letztem Edit (Default: 50 Zeichen)

Beides muss erfuellt sein, sonst kein Flush. Damit werden Micro-Tokens
gebuendelt und der User sieht sinnvoll inkrementell neue Inhalte.
"""

from __future__ import annotations

import logging
from time import monotonic

logger = logging.getLogger(__name__)


class EditThrottle:
    """Entscheidet per ``should_flush`` ob ein Edit ausgefuehrt werden darf."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.5,
        min_delta_chars: int = 50,
    ) -> None:
        """Initialisiert den Throttle.

        Args:
            min_interval_seconds: Mindest-Sekunden zwischen zwei Flushes.
            min_delta_chars: Mindest-Zuwachs an Zeichen fuer einen Flush.
        """
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._min_delta = max(1, int(min_delta_chars))
        self._last_flush_ts: float = 0.0
        self._last_len: int = 0

    def should_flush(self, current_len: int) -> bool:
        """Pruefe ob ein Edit ausgefuehrt werden soll.

        Side-effect: Bei True werden interne Zaehler auf den aktuellen
        Stand gesetzt. Bei False bleibt der Zustand unveraendert.
        """
        now = monotonic()
        if now - self._last_flush_ts < self._min_interval:
            return False
        if current_len - self._last_len < self._min_delta:
            return False
        self._last_flush_ts = now
        self._last_len = current_len
        return True

    def force_flush(self, current_len: int) -> None:
        """Markiert einen Flush extern (z.B. bei finalize())."""
        self._last_flush_ts = monotonic()
        self._last_len = current_len

    def reset(self) -> None:
        """Setzt den Throttle zurueck (fuer Wiederverwendung ueber Runs)."""
        self._last_flush_ts = 0.0
        self._last_len = 0
