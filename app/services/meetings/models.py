"""Dataclasses fuer Meeting-Transkripte und Summaries.

Agnostisch zur Transkript-Quelle — sowohl Webex-API (VTT von Webex-Assistant)
als auch Lokal-Audio (Whisper-Timestamps) produzieren ``TranscriptBundle``
im gleichen Format. Der Summarizer kennt die Quelle nicht.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal


SourceKind = Literal["webex_api", "local_audio"]


@dataclass
class TranscriptTurn:
    """Ein einzelner Sprecher-Turn im Transkript.

    Attributes:
        speaker: Label — z.B. "User", "Remote", "alice@example.com", oder
            "<Sprecher 1>" fuer nicht-identifizierte Sprecher.
        start_seconds: Offset vom Meeting-Start.
        end_seconds: Offset vom Meeting-Start.
        text: Was gesagt wurde.
        confidence: Whisper-Score (0..1) oder 1.0 fuer API-Transkripte.
    """
    speaker: str
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float = 1.0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "speaker": self.speaker,
            "start_seconds": round(self.start_seconds, 2),
            "end_seconds": round(self.end_seconds, 2),
            "text": self.text,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class TranscriptBundle:
    """Normalisiertes Transkript aus Webex-API oder Lokal-Audio.

    Identifikation via ``meeting_id`` (Webex-Instance-ID) oder lokaler
    UUID. ``source_metadata`` haelt die roh-spezifischen Felder fuer
    Debugging/Audit (z.B. Webex-Recording-ID, Lokal-Audio-Pfade).
    """
    source: SourceKind
    meeting_id: str
    started_at_utc: datetime
    duration_seconds: float
    turns: List[TranscriptTurn]
    participants: List[str] = field(default_factory=list)
    source_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_chars(self) -> int:
        return sum(len(t.text) for t in self.turns)

    def plain_text(self, speaker_prefix: bool = True) -> str:
        """Linearisiertes Transkript fuer LLM-Input."""
        if not self.turns:
            return ""
        if speaker_prefix:
            return "\n".join(
                f"[{t.speaker}] {t.text}" for t in self.turns if t.text.strip()
            )
        return "\n".join(t.text for t in self.turns if t.text.strip())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "meeting_id": self.meeting_id,
            "started_at_utc": self.started_at_utc.isoformat(),
            "duration_seconds": round(self.duration_seconds, 2),
            "turns": [t.to_dict() for t in self.turns],
            "participants": list(self.participants),
            "source_metadata": dict(self.source_metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptBundle":
        started = data.get("started_at_utc") or ""
        if isinstance(started, str):
            started_dt = datetime.fromisoformat(started) if started else datetime.now(timezone.utc)
        else:
            started_dt = started
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        return cls(
            source=data.get("source", "webex_api"),
            meeting_id=str(data.get("meeting_id") or ""),
            started_at_utc=started_dt,
            duration_seconds=float(data.get("duration_seconds") or 0.0),
            turns=[
                TranscriptTurn(
                    speaker=str(t.get("speaker") or ""),
                    start_seconds=float(t.get("start_seconds") or 0.0),
                    end_seconds=float(t.get("end_seconds") or 0.0),
                    text=str(t.get("text") or ""),
                    confidence=float(t.get("confidence", 1.0)),
                )
                for t in (data.get("turns") or [])
            ],
            participants=list(data.get("participants") or []),
            source_metadata=dict(data.get("source_metadata") or {}),
        )


@dataclass
class MeetingSummary:
    """Ergebnis des ``MeetingSummarizer`` — vom LLM produzierte Kondensierung."""
    bundle: TranscriptBundle
    title: str
    summary_markdown: str
    action_items: List[str]
    key_decisions: List[str]
    generated_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle": self.bundle.to_dict(),
            "title": self.title,
            "summary_markdown": self.summary_markdown,
            "action_items": list(self.action_items),
            "key_decisions": list(self.key_decisions),
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "model_used": self.model_used,
        }

    def post_markdown(self) -> str:
        """Baut den finalen Markdown-Body fuer den Webex-Post."""
        started_local = self.bundle.started_at_utc.strftime("%Y-%m-%d %H:%M UTC")
        dur_min = int(self.bundle.duration_seconds // 60)
        header = f"📋 **{self.title}**\n_{started_local} · Dauer ~{dur_min} min · Quelle: {self.bundle.source}_"
        parts = [header, "", "**Zusammenfassung**", self.summary_markdown.strip()]
        if self.key_decisions:
            parts.append("")
            parts.append("**Entscheidungen**")
            parts.extend(f"- {d}" for d in self.key_decisions)
        if self.action_items:
            parts.append("")
            parts.append("**Action Items**")
            parts.extend(f"- [ ] {a}" for a in self.action_items)
        return "\n".join(parts)
