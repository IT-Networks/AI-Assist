"""
Unit-Tests fuer Meeting-Summarization (Sprint 5).

Gegenstand:
- TranscriptTurn/Bundle/MeetingSummary Serialization + Rendering
- MeetingSummarizer: JSON-Parse, Fallback bei LLM-Fehler, Transkript-Truncation
- MeetingRetention: purge_expired, purge_audio_for
- MeetingSlashRouter: disabled-Fallbacks, /record on/off/status-Dispatch
- LocalCallRecorder State-Machine (ohne echte Audio-HW)
- Phase-B-Transcribe-Parser (Whisper verbose_json → TranscriptTurn)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.local_audio.transcribe import _parse_verbose_json
from app.services.meetings.models import (
    MeetingSummary,
    TranscriptBundle,
    TranscriptTurn,
)
from app.services.meetings.poster import MeetingPoster
from app.services.meetings.retention import MeetingRetention
from app.services.meetings.router import MeetingSlashRouter
from app.services.meetings.summarizer import (
    MeetingSummarizer,
    _parse_llm_json,
)


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════


def _bundle_of(turns: List[TranscriptTurn], **kwargs) -> TranscriptBundle:
    defaults = dict(
        source="local_audio",
        meeting_id="test-meeting",
        started_at_utc=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc),
        duration_seconds=sum(t.duration_seconds for t in turns),
        turns=turns,
        participants=list({t.speaker for t in turns}),
    )
    defaults.update(kwargs)
    return TranscriptBundle(**defaults)


class TestTranscriptTurn:
    def test_duration(self):
        t = TranscriptTurn("User", 1.0, 3.5, "hi")
        assert t.duration_seconds == 2.5

    def test_negative_duration_clamped_to_zero(self):
        t = TranscriptTurn("User", 3.0, 1.0, "???")
        assert t.duration_seconds == 0.0

    def test_roundtrip_dict(self):
        t = TranscriptTurn("Remote", 10.0, 12.0, "hello", confidence=0.85)
        d = t.to_dict()
        assert d["speaker"] == "Remote"
        assert d["confidence"] == 0.85


class TestTranscriptBundle:
    def test_plain_text_with_speaker(self):
        b = _bundle_of([
            TranscriptTurn("User", 0, 1, "hallo"),
            TranscriptTurn("Remote", 1, 2, "ja?"),
        ])
        txt = b.plain_text(speaker_prefix=True)
        assert "[User] hallo" in txt
        assert "[Remote] ja?" in txt

    def test_plain_text_skips_empty(self):
        b = _bundle_of([
            TranscriptTurn("User", 0, 1, "hallo"),
            TranscriptTurn("User", 1, 2, "   "),
            TranscriptTurn("Remote", 2, 3, "ja"),
        ])
        assert b.plain_text().count("\n") == 1  # 2 non-empty lines

    def test_total_chars(self):
        b = _bundle_of([
            TranscriptTurn("User", 0, 1, "abc"),
            TranscriptTurn("User", 1, 2, "defg"),
        ])
        assert b.total_chars == 7

    def test_roundtrip_dict(self):
        b = _bundle_of([TranscriptTurn("User", 0, 1, "hi")])
        d = b.to_dict()
        b2 = TranscriptBundle.from_dict(d)
        assert b2.meeting_id == b.meeting_id
        assert len(b2.turns) == 1
        assert b2.turns[0].text == "hi"


class TestMeetingSummaryRender:
    def test_post_markdown_with_all_sections(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 10, "hi")])
        s = MeetingSummary(
            bundle=bundle,
            title="Daily",
            summary_markdown="- Punkt 1\n- Punkt 2",
            action_items=["Alice: Slides fertig"],
            key_decisions=["Go-Live naechste Woche"],
        )
        md = s.post_markdown()
        assert "📋 **Daily**" in md
        assert "**Zusammenfassung**" in md
        assert "**Entscheidungen**" in md
        assert "**Action Items**" in md
        assert "- [ ] Alice: Slides fertig" in md

    def test_post_markdown_without_decisions_or_actions(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 10, "hi")])
        s = MeetingSummary(
            bundle=bundle, title="x", summary_markdown="- a",
            action_items=[], key_decisions=[],
        )
        md = s.post_markdown()
        assert "**Entscheidungen**" not in md
        assert "**Action Items**" not in md


# ═══════════════════════════════════════════════════════════════════════════
# Summarizer
# ═══════════════════════════════════════════════════════════════════════════


class TestSummarizerJSONParse:
    def test_parse_plain_json(self):
        raw = '{"title":"T","summary":"s","decisions":[],"action_items":[]}'
        assert _parse_llm_json(raw) == {"title":"T","summary":"s","decisions":[],"action_items":[]}

    def test_parse_with_code_fence(self):
        raw = '```json\n{"title":"T","summary":"s"}\n```'
        parsed = _parse_llm_json(raw)
        assert parsed is not None
        assert parsed["title"] == "T"

    def test_parse_with_preamble(self):
        raw = 'Hier das JSON: {"title":"T","summary":"s"}'
        parsed = _parse_llm_json(raw)
        assert parsed == {"title":"T","summary":"s"}

    def test_parse_empty_returns_none(self):
        assert _parse_llm_json("") is None
        assert _parse_llm_json("   ") is None

    def test_parse_broken_json_returns_none(self):
        assert _parse_llm_json("{bad json") is None


class TestMeetingSummarizer:
    @pytest.mark.asyncio
    async def test_summarize_happy_path(self):
        bundle = _bundle_of([
            TranscriptTurn("User", 0, 5, "Wir haben heute ueber die Migration gesprochen."),
            TranscriptTurn("Remote", 5, 10, "Genau, Go-Live ist Freitag."),
        ])
        summarizer = MeetingSummarizer()
        fake_response = (
            '{"title":"Migration-Meeting",'
            '"summary":"- Migration besprochen\\n- Go-Live Fr geplant",'
            '"decisions":["Go-Live am Freitag"],'
            '"action_items":["User: Migration vorbereiten"]}'
        )
        with patch("app.services.llm_client.llm_client") as mock_client:
            mock_client.chat_quick = AsyncMock(return_value=fake_response)
            result = await summarizer.summarize(bundle)
        assert result.title == "Migration-Meeting"
        assert len(result.key_decisions) == 1
        assert len(result.action_items) == 1
        assert result.model_used != "fallback"

    @pytest.mark.asyncio
    async def test_summarize_llm_error_fallback(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 1, "hi")])
        summarizer = MeetingSummarizer()
        with patch("app.services.llm_client.llm_client") as mock_client:
            mock_client.chat_quick = AsyncMock(side_effect=RuntimeError("LLM down"))
            result = await summarizer.summarize(bundle)
        assert result.model_used == "fallback"
        assert "fehlgeschlagen" in result.summary_markdown.lower()
        assert result.bundle is bundle  # Original-Transkript bleibt intakt

    @pytest.mark.asyncio
    async def test_summarize_bad_json_fallback(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 1, "hi")])
        summarizer = MeetingSummarizer()
        with patch("app.services.llm_client.llm_client") as mock_client:
            mock_client.chat_quick = AsyncMock(return_value="not json at all")
            result = await summarizer.summarize(bundle)
        assert result.model_used == "fallback"

    @pytest.mark.asyncio
    async def test_summarize_truncates_long_transcript(self):
        # Transkript > 80k chars → wird elidiert bevor es zum LLM geht
        long_text = "A" * 50_000
        bundle = _bundle_of([
            TranscriptTurn("User", 0, 100, long_text),
            TranscriptTurn("Remote", 100, 200, long_text),
        ])
        summarizer = MeetingSummarizer()
        fake_response = '{"title":"x","summary":"-","decisions":[],"action_items":[]}'
        captured_prompt: Dict[str, Any] = {}
        async def capture(messages, **kwargs):
            captured_prompt["user"] = messages[1]["content"]
            return fake_response
        with patch("app.services.llm_client.llm_client") as mock_client:
            mock_client.chat_quick = AsyncMock(side_effect=capture)
            await summarizer.summarize(bundle)
        assert "gekuerzt" in captured_prompt["user"]
        assert len(captured_prompt["user"]) < 100_000


# ═══════════════════════════════════════════════════════════════════════════
# Retention
# ═══════════════════════════════════════════════════════════════════════════


class TestMeetingRetention:
    def test_subdirs_created(self, tmp_path: Path):
        r = MeetingRetention(base_dir=tmp_path)
        assert r.audio_dir.exists()
        assert r.transcripts_dir.exists()
        assert r.summaries_dir.exists()

    def test_purge_audio_for_meeting(self, tmp_path: Path):
        r = MeetingRetention(base_dir=tmp_path)
        (r.audio_dir / "meeting1_mic.flac").write_bytes(b"1")
        (r.audio_dir / "meeting1_remote.flac").write_bytes(b"2")
        (r.audio_dir / "meeting2_mic.flac").write_bytes(b"3")
        count = r.purge_audio_for("meeting1")
        assert count == 2
        assert (r.audio_dir / "meeting2_mic.flac").exists()

    def test_purge_expired_audio(self, tmp_path: Path):
        r = MeetingRetention(base_dir=tmp_path, audio_days=1)
        old = r.audio_dir / "old.flac"
        old.write_bytes(b"x")
        # mtime auf 5 Tage zurueckdrehen
        import os
        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        os.utime(old, (old_ts, old_ts))
        new = r.audio_dir / "new.flac"
        new.write_bytes(b"y")
        result = r.purge_expired()
        assert result["audio"] == 1
        assert not old.exists()
        assert new.exists()

    def test_purge_expired_skips_when_zero_days(self, tmp_path: Path):
        """audio_days=0 bedeutet 'purge-per-meeting manuell', nicht time-based."""
        r = MeetingRetention(base_dir=tmp_path, audio_days=0)
        f = r.audio_dir / "x.flac"
        f.write_bytes(b"x")
        result = r.purge_expired()
        assert result["audio"] == 0
        assert f.exists()


# ═══════════════════════════════════════════════════════════════════════════
# MeetingPoster
# ═══════════════════════════════════════════════════════════════════════════


class TestMeetingPoster:
    @pytest.mark.asyncio
    async def test_post_uses_default_room(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 1, "hi")])
        summary = MeetingSummary(
            bundle=bundle, title="T", summary_markdown="- a",
            action_items=[], key_decisions=[],
        )
        poster = MeetingPoster(default_room_id="room-A")
        with patch("app.services.webex_client.get_webex_client") as mock_get:
            mock_client = MagicMock()
            mock_client.send_message = AsyncMock(return_value={"id": "msg-xyz"})
            mock_get.return_value = mock_client
            msg_id = await poster.post(summary)
        assert msg_id == "msg-xyz"
        mock_client.send_message.assert_called_once()
        assert mock_client.send_message.call_args.kwargs["room_id"] == "room-A"

    @pytest.mark.asyncio
    async def test_post_without_room_returns_none(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 1, "hi")])
        summary = MeetingSummary(
            bundle=bundle, title="T", summary_markdown="- a",
            action_items=[], key_decisions=[],
        )
        poster = MeetingPoster(default_room_id="")
        assert await poster.post(summary) is None

    @pytest.mark.asyncio
    async def test_post_override_room(self):
        bundle = _bundle_of([TranscriptTurn("User", 0, 1, "hi")])
        summary = MeetingSummary(
            bundle=bundle, title="T", summary_markdown="- a",
            action_items=[], key_decisions=[],
        )
        poster = MeetingPoster(default_room_id="default-room")
        with patch("app.services.webex_client.get_webex_client") as mock_get:
            mock_client = MagicMock()
            mock_client.send_message = AsyncMock(return_value={"id": "m1"})
            mock_get.return_value = mock_client
            await poster.post(summary, target_room_id="override-room")
        assert mock_client.send_message.call_args.kwargs["room_id"] == "override-room"


# ═══════════════════════════════════════════════════════════════════════════
# MeetingSlashRouter
# ═══════════════════════════════════════════════════════════════════════════


class TestMeetingSlashRouter:
    @pytest.mark.asyncio
    async def test_record_without_recorder_returns_disabled_message(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        router = MeetingSlashRouter(reply_fn=reply, local_recorder=None)
        handled = await router.handle("/record", "on")
        assert handled is True
        assert "nicht aktiviert" in replies[0].lower()

    @pytest.mark.asyncio
    async def test_record_on_calls_recorder(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        recorder = MagicMock()
        recorder.record_on = AsyncMock()
        router = MeetingSlashRouter(reply_fn=reply, local_recorder=recorder)
        await router.handle("/record", "on")
        recorder.record_on.assert_awaited_once()
        assert "gestartet" in replies[0].lower()

    @pytest.mark.asyncio
    async def test_record_off_returns_status(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        recorder = MagicMock()
        recorder.record_off = AsyncMock(return_value="✅ Summary gepostet (15 Turns transkribiert).")
        router = MeetingSlashRouter(reply_fn=reply, local_recorder=recorder)
        await router.handle("/record", "off")
        assert "beendet" in replies[0].lower()
        assert "15 Turns" in replies[0]

    @pytest.mark.asyncio
    async def test_record_auto_sets_flag(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        recorder = MagicMock()
        recorder.set_auto = AsyncMock()
        router = MeetingSlashRouter(reply_fn=reply, local_recorder=recorder)
        await router.handle("/record", "auto")
        recorder.set_auto.assert_awaited_once_with(True)
        await router.handle("/record", "manual")
        recorder.set_auto.assert_awaited_with(False)

    @pytest.mark.asyncio
    async def test_record_status(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        recorder = MagicMock()
        recorder.status = AsyncMock(return_value={
            "mode": "capturing", "capturing": True, "started_at": "2026-04-23T10:00",
        })
        router = MeetingSlashRouter(reply_fn=reply, local_recorder=recorder)
        await router.handle("/record", "status")
        assert "capturing" in replies[0]

    @pytest.mark.asyncio
    async def test_record_unknown_subcommand(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        recorder = MagicMock()
        router = MeetingSlashRouter(reply_fn=reply, local_recorder=recorder)
        await router.handle("/record", "foobar")
        assert "unbekanntes" in replies[0].lower()

    @pytest.mark.asyncio
    async def test_summarize_without_pathA_returns_placeholder(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        router = MeetingSlashRouter(reply_fn=reply, webex_summarizer=None)
        handled = await router.handle("/summarize", "latest")
        assert handled is True
        assert "noch nicht aktiviert" in replies[0].lower()

    @pytest.mark.asyncio
    async def test_unknown_command_not_handled(self):
        replies: List[str] = []
        async def reply(md): replies.append(md)
        router = MeetingSlashRouter(reply_fn=reply)
        handled = await router.handle("/foobar", "")
        assert handled is False


# ═══════════════════════════════════════════════════════════════════════════
# Whisper verbose_json Parser
# ═══════════════════════════════════════════════════════════════════════════


class TestWhisperParser:
    def test_parse_empty_payload(self):
        assert _parse_verbose_json({}, speaker="User") == []
        assert _parse_verbose_json({"segments": []}, speaker="User") == []

    def test_parse_normal_segments(self):
        payload = {
            "segments": [
                {"start": 0.0, "end": 2.5, "text": "Hallo Welt"},
                {"start": 2.5, "end": 5.0, "text": "Wie geht's?"},
            ]
        }
        turns = _parse_verbose_json(payload, speaker="User")
        assert len(turns) == 2
        assert turns[0].speaker == "User"
        assert turns[0].text == "Hallo Welt"
        assert turns[1].start_seconds == 2.5

    def test_parse_skips_empty_text(self):
        payload = {"segments": [
            {"start": 0, "end": 1, "text": "  "},
            {"start": 1, "end": 2, "text": "ok"},
        ]}
        turns = _parse_verbose_json(payload, speaker="User")
        assert len(turns) == 1
        assert turns[0].text == "ok"

    def test_parse_confidence_from_avg_logprob(self):
        import math
        payload = {"segments": [
            {"start": 0, "end": 1, "text": "x", "avg_logprob": -0.1},
        ]}
        turns = _parse_verbose_json(payload, speaker="User")
        assert 0.0 < turns[0].confidence <= 1.0
        assert abs(turns[0].confidence - math.exp(-0.1)) < 0.001


# ═══════════════════════════════════════════════════════════════════════════
# LocalCallRecorder State-Machine (ohne echte Audio-HW)
# ═══════════════════════════════════════════════════════════════════════════


class TestLocalCallRecorder:
    """Testet Lifecycle via injizierten Mock-Watcher + Mock-Recorder."""

    def _make_recorder(self, tmp_path: Path, recorder_mock=None, watcher_mock=None):
        from app.services.local_audio.session import LocalCallRecorder
        summarizer = MagicMock()
        summarizer.summarize = AsyncMock()
        poster = MagicMock()
        poster.post = AsyncMock(return_value="msg-1")
        retention = MeetingRetention(base_dir=tmp_path)
        recorder_mock = recorder_mock or MagicMock()
        recorder_mock.is_available = True
        recorder_mock.is_active = False
        watcher_mock = watcher_mock or MagicMock()
        watcher_mock.is_available = True
        return LocalCallRecorder(
            output_dir=tmp_path / "audio",
            summarizer=summarizer, poster=poster, retention=retention,
            watcher=watcher_mock, recorder=recorder_mock,
        ), recorder_mock, summarizer, poster

    @pytest.mark.asyncio
    async def test_initial_state_is_idle(self, tmp_path: Path):
        recorder, _, _, _ = self._make_recorder(tmp_path)
        status = await recorder.status()
        assert status["mode"] == "idle"

    @pytest.mark.asyncio
    async def test_record_on_transitions_to_capturing(self, tmp_path: Path):
        recorder_mock = MagicMock()
        recorder_mock.is_available = True
        from app.services.local_audio.capture import CaptureArtifacts
        started = datetime.now(timezone.utc)
        recorder_mock.start = MagicMock(return_value=CaptureArtifacts(
            meeting_id="m1", mic_path=tmp_path/"m1_mic.flac",
            remote_path=tmp_path/"m1_remote.flac", sample_rate=16000,
            started_at_utc=started, stopped_at_utc=started,
        ))
        rec, _, _, _ = self._make_recorder(tmp_path, recorder_mock=recorder_mock)
        await rec.record_on()
        status = await rec.status()
        assert status["mode"] == "capturing"
        recorder_mock.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_off_runs_pipeline_and_idles(self, tmp_path: Path):
        # Mock alles: capture → transcribe → summarize → post
        from app.services.local_audio.capture import CaptureArtifacts
        started = datetime.now(timezone.utc)
        stopped = started + timedelta(seconds=30)
        art = CaptureArtifacts(
            meeting_id="m1", mic_path=tmp_path/"m1_mic.flac",
            remote_path=tmp_path/"m1_remote.flac", sample_rate=16000,
            started_at_utc=started, stopped_at_utc=stopped,
        )
        recorder_mock = MagicMock()
        recorder_mock.is_available = True
        recorder_mock.start = MagicMock(return_value=art)
        recorder_mock.stop = MagicMock(return_value=art)
        rec, _, summarizer, poster = self._make_recorder(tmp_path, recorder_mock=recorder_mock)

        # Mock transcribe_and_merge
        mock_bundle = _bundle_of(
            [TranscriptTurn("User", 0, 5, "hi"), TranscriptTurn("Remote", 5, 10, "ja")],
            meeting_id="m1",
        )
        with patch("app.services.local_audio.session.transcribe_and_merge",
                   new=AsyncMock(return_value=mock_bundle)):
            summarizer.summarize = AsyncMock(return_value=MeetingSummary(
                bundle=mock_bundle, title="T", summary_markdown="-",
                action_items=[], key_decisions=[],
            ))
            await rec.record_on()
            result = await rec.record_off()

        assert "gepostet" in result.lower()
        status = await rec.status()
        assert status["mode"] == "idle"
        assert summarizer.summarize.await_count == 1
        assert poster.post.await_count == 1

    @pytest.mark.asyncio
    async def test_record_off_without_active_returns_noop(self, tmp_path: Path):
        rec, _, _, _ = self._make_recorder(tmp_path)
        result = await rec.record_off()
        assert "keine aktive" in result.lower()
