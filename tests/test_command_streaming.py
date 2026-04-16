"""Tests fuer den Streaming-Pfad in command_runner (v2.37.33)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.services.command_runner import (
    DEFAULT_BINARY_WHITELIST,
    DEFAULT_RINGBUFFER_BYTES,
    _Ringbuffer,
    run_workspace_command,
)
from app.services.process_registry import (
    ProcessRegistry,
    reset_process_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_process_registry()
    yield
    reset_process_registry()


# ════════════════════════════════════════════════════════════════════════════
# _Ringbuffer
# ════════════════════════════════════════════════════════════════════════════

def test_ringbuffer_empty():
    rb = _Ringbuffer(100)
    assert rb.to_bytes() == b""
    assert rb.to_text() == ""
    assert rb.truncated is False
    assert rb.total_bytes == 0


def test_ringbuffer_under_limit():
    rb = _Ringbuffer(100)
    rb.append(b"hello ")
    rb.append(b"world")
    assert rb.to_bytes() == b"hello world"
    assert rb.truncated is False
    assert rb.total_bytes == 11


def test_ringbuffer_overflow_drops_oldest():
    rb = _Ringbuffer(10)
    rb.append(b"AAAAA")  # 5
    rb.append(b"BBBBB")  # 10 (limit)
    rb.append(b"CCCCC")  # 15 -> drop AAAAA -> 10
    assert rb.to_bytes() == b"BBBBBCCCCC"
    assert rb.truncated is True
    assert rb.total_bytes == 15


def test_ringbuffer_single_chunk_larger_than_limit():
    """Single huge line wird hart auf max_bytes gekuerzt (tail behalten)."""
    rb = _Ringbuffer(5)
    rb.append(b"abcdefghij")  # 10 bytes, limit 5
    assert rb.to_bytes() == b"fghij"
    assert rb.truncated is True
    assert rb.total_bytes == 10


def test_ringbuffer_ignores_empty_chunks():
    rb = _Ringbuffer(100)
    rb.append(b"")
    rb.append(b"x")
    rb.append(b"")
    assert rb.to_bytes() == b"x"
    assert rb.total_bytes == 1


def test_ringbuffer_keeps_at_least_one_chunk():
    """Auch bei riesigem letzten Chunk bleibt mindestens einer drin."""
    rb = _Ringbuffer(10)
    rb.append(b"AAA")
    rb.append(b"X" * 50)  # 50 bytes, viel groesser als limit
    # Nach overflow-trim: AAA raus, X*50 bleibt -> dann hart auf 10 gekuerzt
    assert len(rb.to_bytes()) == 10
    assert rb.truncated is True


def test_ringbuffer_to_text_replaces_bad_utf8():
    rb = _Ringbuffer(100)
    rb.append(b"\xff\xfe")  # ungueltige utf-8 sequenz
    text = rb.to_text()
    assert text  # nicht leer, durch errors=replace


# ════════════════════════════════════════════════════════════════════════════
# Streaming-Mode: chunk_cb wird pro Zeile aufgerufen
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_streaming_chunk_cb_called_per_line(tmp_path):
    """3 Zeilen stdout -> mindestens 3 Callbacks."""
    chunks = []

    async def cb(stream, line, seq):
        chunks.append((stream, line.rstrip(), seq))

    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c",
                 "import sys; print('A'); print('B'); print('C'); sys.stdout.flush()"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        chunk_cb=cb,
    )
    assert result.success is True
    assert result.exit_code == 0
    stdout_chunks = [c for c in chunks if c[0] == "stdout"]
    assert len(stdout_chunks) >= 3
    # Reihenfolge stimmt + seq monoton
    seqs = [c[2] for c in chunks]
    assert seqs == sorted(seqs)
    assert seqs[0] >= 1
    lines_text = " ".join(c[1] for c in stdout_chunks)
    assert "A" in lines_text and "B" in lines_text and "C" in lines_text


@pytest.mark.asyncio
async def test_streaming_separates_stdout_stderr(tmp_path):
    chunks = []

    async def cb(stream, line, seq):
        chunks.append((stream, line.rstrip()))

    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c",
                 "import sys; print('out-line'); sys.stderr.write('err-line\\n')"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        chunk_cb=cb,
    )
    assert result.success is True
    streams = {s for s, _ in chunks}
    assert "stdout" in streams
    assert "stderr" in streams


@pytest.mark.asyncio
async def test_streaming_callback_exception_does_not_break(tmp_path, caplog):
    """Wenn chunk_cb wirft, soll der Run trotzdem zu Ende laufen."""
    async def bad_cb(stream, line, seq):
        raise RuntimeError("boom")

    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('hi')"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        chunk_cb=bad_cb,
    )
    assert result.success is True
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_streaming_ringbuffer_truncates_huge_output(tmp_path):
    """100 Zeilen je ~100 bytes, ringbuffer 1 KB -> truncated=True."""
    chunks = []

    async def cb(stream, line, seq):
        chunks.append(seq)

    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c",
                 "import sys\nfor i in range(100):\n    print('X' * 100)"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        chunk_cb=cb,
        ringbuffer_bytes=1024,
    )
    assert result.success is True
    assert result.truncated is True
    # total_stdout_bytes deutlich groesser als ringbuffer
    assert result.total_stdout_bytes > 5000
    assert len(result.stdout_preview) <= 5020  # stdout_preview hat eigenes truncate (5000)


@pytest.mark.asyncio
async def test_streaming_returns_command_id(tmp_path):
    """Mit registry+session_id soll result.command_id gesetzt sein."""
    reg = ProcessRegistry()
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('x')"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        session_id="sid",
        registry=reg,
        chunk_cb=None,  # auch im non-streaming Pfad muss command_id gesetzt sein
    )
    assert result.success is True
    assert result.command_id is not None
    assert len(result.command_id) > 8  # uuid


@pytest.mark.asyncio
async def test_streaming_cancel_partial_output(tmp_path):
    """Sleep mit Output zwischendurch -> cancel -> partielle Ausgabe verfuegbar."""
    reg = ProcessRegistry()
    chunks = []

    async def cb(stream, line, seq):
        chunks.append(line.rstrip())

    async def cancel_after(delay):
        await asyncio.sleep(delay)
        await reg.cancel_session("cancel-sess")

    canceller = asyncio.create_task(cancel_after(0.4))
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-u", "-c",
                 "import time, sys\nfor i in range(20):\n"
                 "    print(f'line-{i}'); sys.stdout.flush(); time.sleep(0.1)"],
        timeout_seconds=10,
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        session_id="cancel-sess",
        registry=reg,
        chunk_cb=cb,
    )
    await canceller
    assert result.success is False
    assert "abgebrochen" in (result.error or "").lower()
    # Es sollten ein paar Zeilen geflossen sein
    assert len(chunks) >= 1
    assert result.duration_ms < 5000  # nicht alle 20 Zeilen abgewartet


@pytest.mark.asyncio
async def test_streaming_to_dict_includes_streaming_fields(tmp_path):
    reg = ProcessRegistry()
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('x' * 100)"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        session_id="d-sess",
        registry=reg,
        chunk_cb=lambda *a, **k: asyncio.sleep(0),  # noop async
    )
    d = result.to_dict()
    assert "command_id" in d
    assert "total_bytes" in d
    assert d["total_bytes"]["stdout"] >= 100
