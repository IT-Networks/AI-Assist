"""MeetingRetention — File-Cleanup fuer Transkripte und Audio-Artefakte.

- Audio-Dateien (Pfad B Lokal-Capture): Default-Retention **0 Tage** —
  sofort nach Summary loeschen. Kein Roh-Audio-Archiv.
- Transkript-JSONs + Summary-Markdowns: Default **30 Tage**.
- Manuelle Purge via Slash-Cmd moeglich.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


class MeetingRetention:
    """Verwaltet ``app/state/meetings/`` — File-Lifecycle."""

    DEFAULT_TRANSCRIPT_DAYS = 30
    DEFAULT_AUDIO_DAYS = 0  # sofort nach Summary loeschen

    def __init__(
        self,
        *,
        base_dir: Path,
        transcript_days: int = DEFAULT_TRANSCRIPT_DAYS,
        audio_days: int = DEFAULT_AUDIO_DAYS,
    ) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._transcript_days = max(0, int(transcript_days))
        self._audio_days = max(0, int(audio_days))

    @property
    def base_dir(self) -> Path:
        return self._base

    @property
    def transcripts_dir(self) -> Path:
        p = self._base / "transcripts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def summaries_dir(self) -> Path:
        p = self._base / "summaries"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def audio_dir(self) -> Path:
        p = self._base / "audio"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def purge_audio_for(self, meeting_id: str) -> int:
        """Loescht alle Audio-Dateien fuer dieses Meeting. Return: count."""
        count = 0
        for f in self.audio_dir.glob(f"{meeting_id}*"):
            try:
                f.unlink()
                count += 1
            except OSError as e:
                logger.warning("[retention] unlink failed %s: %s", f.name, e)
        if count:
            logger.info("[retention] %d Audio-Dateien fuer %s geloescht", count, meeting_id[:12])
        return count

    def purge_expired(self) -> dict:
        """Loescht alle Dateien aelter als Retention.

        Returns dict ``{transcripts, summaries, audio}`` mit Counts.
        """
        result = {"transcripts": 0, "summaries": 0, "audio": 0}
        now = time.time()

        if self._transcript_days > 0 or self._transcript_days == 0:
            # 0 wuerde bedeuten "sofort loeschen" — das macht hier keinen Sinn,
            # daher nur wirklich > 0 pruefen. (Audio-Pfad hat eigene Semantik.)
            pass
        result["transcripts"] = self._purge_dir(
            self.transcripts_dir, now - self._transcript_days * 86400,
        ) if self._transcript_days > 0 else 0
        result["summaries"] = self._purge_dir(
            self.summaries_dir, now - self._transcript_days * 86400,
        ) if self._transcript_days > 0 else 0
        result["audio"] = self._purge_dir(
            self.audio_dir, now - self._audio_days * 86400,
        ) if self._audio_days > 0 else 0

        total = sum(result.values())
        if total:
            logger.info(
                "[retention] purge: transcripts=%d summaries=%d audio=%d",
                result["transcripts"], result["summaries"], result["audio"],
            )
        return result

    def list_transcripts(self) -> List[Path]:
        return sorted(self.transcripts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _purge_dir(directory: Path, cutoff_mtime: float) -> int:
        count = 0
        for f in directory.iterdir():
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff_mtime:
                    f.unlink()
                    count += 1
            except OSError as e:
                logger.debug("[retention] skip %s: %s", f.name, e)
        return count
