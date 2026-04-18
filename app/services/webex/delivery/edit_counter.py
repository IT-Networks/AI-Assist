"""
EditCounterBucket — zaehlt Edits pro aktueller Webex-Message-ID.

Webex-API erlaubt maximal **10 Edits pro Message**. Bei Streaming-
Preview mit 1.5s-Throttle erreicht das Limit nach ca. 15-30 Sekunden
Generation. Der Bucket signalisiert dem Deliverer, wann rotiert werden
soll (alte Msg loeschen, neue posten, Counter reset).

Default-Threshold: 9 (1 Puffer unter Webex-Limit 10).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EditCounterBucket:
    """Zaehlt Edits pro Message; reset bei Rotation.

    Nicht thread-safe — jeder Deliverer nutzt einen eigenen Bucket
    im Single-Task-Kontext eines Agent-Runs.
    """

    DEFAULT_THRESHOLD = 9

    def __init__(self, *, threshold: int = DEFAULT_THRESHOLD) -> None:
        """Initialisiert den Bucket.

        Args:
            threshold: Anzahl Edits ab der ``needs_rotation()`` True liefert.
                Default 9 = 1 Puffer unter Webex-Limit 10.
        """
        self._count = 0
        self._threshold = max(1, int(threshold))

    @property
    def count(self) -> int:
        """Aktuelle Edit-Anzahl seit letztem Reset."""
        return self._count

    @property
    def threshold(self) -> int:
        """Konfigurierte Rotations-Schwelle."""
        return self._threshold

    def increment(self) -> None:
        """Nach erfolgreichem Edit aufrufen."""
        self._count += 1

    def needs_rotation(self) -> bool:
        """True wenn next-edit das Webex-Limit reissen wuerde."""
        return self._count >= self._threshold

    def reset(self) -> None:
        """Nach Rotation aufrufen — Counter auf 0 zuruecksetzen."""
        self._count = 0
