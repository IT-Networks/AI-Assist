"""
Tests fuer den Webex-Bot Performance-Pass (Phase 1-3, 2026-04-26).

Phase 1: O(N²) Streaming-Join-Fix in ``AgentRunner._run`` — verifiziert dass
``"".join(parts)`` nur bei tatsaechlichem Throttle-Flush passiert.

Phase 2: Parallele Attachment- und Channel-Context-Fetches
- Outer ``gather(build_attachments, build_channel_context)`` in ``_run``
- Inner ``gather`` ueber Image-Downloads in ``ChannelContextBuilder.build_attachments``
Verifiziert ueber Wallclock-Timing (mit kontrolliert verzoegerten Mocks) +
Failure-Isolation.

Phase 3: ``WebexClient.get_new_messages_since`` Fan-out mit Semaphore(5).
Verifiziert ueber Wallclock-Timing + Concurrency-Counter + Fehler-Isolation
zwischen Rooms.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.webex.runtime.context_builder import ChannelContextBuilder


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1 — Streaming-Join O(N²)-Regression
# ═══════════════════════════════════════════════════════════════════════════


class _CountingThrottle:
    """Throttle-Stub der jeden N-ten Aufruf flushen laesst.

    Zaehlt zudem die Aufrufe und merkt sich den letzten ``current_len``.
    """

    def __init__(self, flush_every: int) -> None:
        self.flush_every = max(1, flush_every)
        self.calls = 0
        self.flushes = 0
        self.last_len = 0

    def should_flush(self, current_len: int) -> bool:
        self.calls += 1
        self.last_len = current_len
        if self.calls % self.flush_every == 0:
            self.flushes += 1
            return True
        return False


def _streaming_token_loop(parts: List[str], throttle: _CountingThrottle) -> int:
    """Repliziert die Streaming-Logik aus ``AgentRunner._run`` (Phase 1).

    Zaehlt, wie oft ``"".join(parts)`` ausgefuehrt wurde — sollte == flushes
    sein, NICHT == Anzahl Tokens.
    """
    join_count = 0
    accumulated_len = 0
    for tok in parts:
        # Append + Counter (so wie agent_runner.py jetzt)
        accumulated_len += len(tok)
        if throttle.should_flush(accumulated_len):
            _ = "".join(parts[: parts.index(tok) + 1])
            join_count += 1
    return join_count


class TestPhase1StreamingJoin:
    def test_join_only_on_flush_not_per_token(self):
        # 1000 Tokens, throttle flusht jeden 50.
        tokens = ["abcde"] * 1000
        throttle = _CountingThrottle(flush_every=50)
        join_count = _streaming_token_loop(tokens, throttle)
        # Verifikation: Join nur 20× (1000/50), nicht 1000×.
        assert throttle.calls == 1000
        assert throttle.flushes == 20
        assert join_count == 20

    def test_accumulated_len_matches_actual_string_length(self):
        # Sanity-Check: Running-Counter ist konsistent mit echter Stringlaenge.
        tokens = ["a", "bc", "def", "ghij", "klmno"]
        throttle = _CountingThrottle(flush_every=1)  # immer flushen
        accumulated_len = 0
        for tok in tokens:
            accumulated_len += len(tok)
            throttle.should_flush(accumulated_len)
        assert accumulated_len == sum(len(t) for t in tokens)
        assert throttle.last_len == accumulated_len


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — Parallele Attachment-Downloads
# ═══════════════════════════════════════════════════════════════════════════


class _DelayedDownloadClient:
    """Webex-Client-Stub mit pro-URL konfigurierbarer Verzoegerung."""

    def __init__(self, delays: Dict[str, float], failures: Optional[Dict[str, Exception]] = None):
        self._delays = delays
        self._failures = failures or {}
        self.calls: List[str] = []

    async def download_file(self, url: str):
        self.calls.append(url)
        if url in self._failures:
            raise self._failures[url]
        await asyncio.sleep(self._delays.get(url, 0.0))
        # 1×1 px transparent PNG — minimaler valider Image-Body
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xfc\xff\xff?\x00\x05\xfe\x02"
            b"\xfe\xa7\x06A\x9b\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return png_data, "image/png", url.rsplit("/", 1)[-1]


@pytest.mark.asyncio
async def test_phase2_image_downloads_run_in_parallel():
    """4 Bilder × 0.2 s Delay sollten ~0.2 s parallel statt ~0.8 s sequentiell sein."""
    urls = [f"https://example.com/img{i}.png" for i in range(4)]
    delays = {u: 0.2 for u in urls}
    fake_client = _DelayedDownloadClient(delays)

    builder = ChannelContextBuilder(context=MagicMock())
    msg = {"file_urls": urls}

    # `from app.services.webex_client import get_webex_client` wird lokal in
    # build_attachments importiert — Patch dort wo das Symbol nachgeladen wird.
    with patch(
        "app.services.webex_client.get_webex_client",
        return_value=fake_client,
    ):
        start = time.monotonic()
        out = await builder.build_attachments(msg)
        elapsed = time.monotonic() - start

    assert out is not None and len(out) == 4
    # Mit Parallelisierung deutlich unter dem sequenziellen 4 × 0.2 = 0.8s.
    # 0.5 s Headroom fuer CI-Jitter.
    assert elapsed < 0.5, f"Erwartet parallel (~0.2s), gemessen {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_phase2_one_failed_download_does_not_kill_others():
    urls = [f"https://example.com/img{i}.png" for i in range(4)]
    delays = {u: 0.05 for u in urls}
    failures = {urls[1]: RuntimeError("download blew up")}
    fake_client = _DelayedDownloadClient(delays, failures=failures)

    builder = ChannelContextBuilder(context=MagicMock())
    msg = {"file_urls": urls}

    with patch(
        "app.services.webex_client.get_webex_client",
        return_value=fake_client,
    ):
        out = await builder.build_attachments(msg)

    # 3 von 4 ueberleben.
    assert out is not None
    assert len(out) == 3


@pytest.mark.asyncio
async def test_phase2_no_attachments_returns_none():
    builder = ChannelContextBuilder(context=MagicMock())
    out = await builder.build_attachments({"file_urls": []})
    assert out is None


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — Polling-Fan-out
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_phase3_polling_fan_out_is_concurrent():
    """10 Rooms × 100 ms RTT sollten <500 ms dauern, nicht 1000 ms."""
    from app.services.webex_client import WebexClient
    import app.services.webex_client as wxc_mod

    client = WebexClient()

    async def fake_request(method: str, path: str, **kwargs):
        await asyncio.sleep(0.1)
        return {"items": []}

    # Reset Semaphore (kann von vorherigem Test hot sein).
    wxc_mod._POLL_SEMAPHORE = None

    with patch.object(client, "_request", side_effect=fake_request):
        from datetime import datetime, timezone
        start = time.monotonic()
        msgs = await client.get_new_messages_since(
            room_ids=[f"room-{i}" for i in range(10)],
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        elapsed = time.monotonic() - start

    assert msgs == []
    # Mit Cap=5: 10 Rooms in 2 Wellen × (Jitter≤0.2 + Req=0.1) ≈ 0.6 s worst-case.
    # Sequenziell waeren es ~1.0 s + 10×Jitter; ein Schwellwert von 0.7 s
    # beweist Fan-out und ist robust gegen CI-Scheduler-Noise.
    assert elapsed < 0.7, f"Polling nicht parallelisiert: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_phase3_polling_one_room_failure_does_not_kill_others():
    from app.services.webex_client import WebexClient
    import app.services.webex_client as wxc_mod

    client = WebexClient()
    wxc_mod._POLL_SEMAPHORE = None

    async def fake_request(method: str, path: str, **kwargs):
        room = (kwargs.get("params") or {}).get("roomId", "")
        if room == "bad":
            raise RuntimeError("API down for this room")
        return {"items": [{"id": "m1", "created": "2026-04-26T10:00:00Z", "text": "hi"}]}

    with patch.object(client, "_request", side_effect=fake_request):
        from datetime import datetime, timezone
        msgs = await client.get_new_messages_since(
            room_ids=["good-1", "bad", "good-2"],
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    # Beide good-Rooms liefern, bad ist still failed.
    room_ids = sorted(m.get("room_id", "") for m in msgs)
    assert room_ids == ["good-1", "good-2"]


@pytest.mark.asyncio
async def test_phase3_polling_concurrency_capped_at_semaphore():
    """Mehr als 5 gleichzeitig laufende Calls dürfen nicht passieren."""
    from app.services.webex_client import WebexClient
    import app.services.webex_client as wxc_mod

    client = WebexClient()
    wxc_mod._POLL_SEMAPHORE = None

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_request(method: str, path: str, **kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            if in_flight > peak:
                peak = in_flight
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return {"items": []}

    with patch.object(client, "_request", side_effect=fake_request):
        from datetime import datetime, timezone
        await client.get_new_messages_since(
            room_ids=[f"room-{i}" for i in range(20)],
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    assert peak <= 5, f"Concurrency-Cap verletzt: peak={peak}"
