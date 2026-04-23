"""Meeting-Summarization Foundation (Sprint 5).

Shared components für beide Pfade:
- Pfad A: Webex-API (Transkripte werden automatisch vom Webex-Assistant erstellt)
- Pfad B: Lokal-Audio (Windows WASAPI-Capture → Whisper → unified VTT)

Beide feeden denselben ``MeetingSummarizer`` (GPT-OSS 120B) und denselben
``MeetingPoster`` (Webex-Space). Pfad-spezifischer Code liegt unter
``app/services/webex/meetings/`` (A) bzw. ``app/services/local_audio/`` (B).
"""

from app.services.meetings.models import (
    MeetingSummary,
    TranscriptBundle,
    TranscriptTurn,
)
from app.services.meetings.poster import MeetingPoster
from app.services.meetings.retention import MeetingRetention
from app.services.meetings.summarizer import MeetingSummarizer

__all__ = [
    "MeetingPoster",
    "MeetingRetention",
    "MeetingSummarizer",
    "MeetingSummary",
    "TranscriptBundle",
    "TranscriptTurn",
]
