"""Lokale Windows-Audio-Aufnahme (Meetings Phase B, Sprint 5).

Pipeline:
- ``AudioSessionWatcher``: pycaw-basierte Erkennung von Webex-Audio-Sessions
- ``DualStreamRecorder``: parallel Mic (sounddevice) + System-Output-Loopback
  (pyaudiowpatch), schreibt beide Streams separat als FLAC
- ``AudioTranscriber``: Whisper-Pipeline mit Timestamps, merged die beiden
  FLAC-Spuren zu einem zeitlich sortierten ``TranscriptBundle``
- ``LocalCallRecorder``: Lifecycle/State-Machine (idle/armed/capturing/processing)

**Windows-only**: Alle deps nutzen WASAPI. Detection fällt auf Linux/Mac
still auf "disabled" zurück — der Bot crasht nicht, die Slash-Cmds
antworten dann mit "feature disabled".
"""

from app.services.local_audio.session import (
    LocalCallRecorder,
    RecordingSessionResult,
)

__all__ = [
    "LocalCallRecorder",
    "RecordingSessionResult",
]
