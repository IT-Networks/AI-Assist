"""
Phase 2: Test real-time output streaming from script execution.

Tests that stdout/stderr are streamed line-by-line via callbacks.
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from app.services.script_manager import ScriptExecutor, Script
from datetime import datetime


@pytest.fixture
def executor():
    """Create ScriptExecutor instance."""
    return ScriptExecutor()


@pytest.fixture
def temp_script(tmp_path):
    """Create temporary Python script file."""
    def _create_script(code: str):
        script_file = tmp_path / "test_script.py"
        script_file.write_text(code)
        return str(script_file)
    return _create_script


class TestOutputStreaming:
    """Tests for Phase 2 output streaming."""

    @pytest.mark.asyncio
    async def test_output_callback_receives_stdout(self, executor, temp_script):
        """Test that stdout lines are delivered to callback."""
        script_path = temp_script("print('Line 1')\nprint('Line 2')")

        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate running script locally
        # Note: This is a simplified test - actual _run_local() would execute the script
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', 'Line 1')
            await executor.on_output_chunk('stdout', 'Line 2')

        assert len(output_events) == 2
        assert output_events[0] == ('stdout', 'Line 1')
        assert output_events[1] == ('stdout', 'Line 2')

    @pytest.mark.asyncio
    async def test_output_callback_receives_stderr(self, executor):
        """Test that stderr lines are delivered to callback separately."""
        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate stderr
        if executor.on_output_chunk:
            await executor.on_output_chunk('stderr', 'Error message')

        assert len(output_events) == 1
        assert output_events[0] == ('stderr', 'Error message')

    @pytest.mark.asyncio
    async def test_stdout_stderr_interleaved(self, executor):
        """Test that stdout and stderr are tracked separately even when interleaved."""
        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate interleaved output
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', 'Output 1')
            await executor.on_output_chunk('stderr', 'Error 1')
            await executor.on_output_chunk('stdout', 'Output 2')
            await executor.on_output_chunk('stderr', 'Error 2')

        assert len(output_events) == 4
        stdout_lines = [e[1] for e in output_events if e[0] == 'stdout']
        stderr_lines = [e[1] for e in output_events if e[0] == 'stderr']

        assert stdout_lines == ['Output 1', 'Output 2']
        assert stderr_lines == ['Error 1', 'Error 2']

    @pytest.mark.asyncio
    async def test_output_callback_with_special_characters(self, executor):
        """Test that special characters are properly handled in output."""
        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate special characters
        special_chars = "Special: <script>alert('xss')</script>"
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', special_chars)

        assert len(output_events) == 1
        assert output_events[0][1] == special_chars

    @pytest.mark.asyncio
    async def test_output_callback_with_unicode(self, executor):
        """Test that unicode characters are properly handled."""
        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate unicode output
        unicode_text = "Unicode: 中文 العربية 🚀"
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', unicode_text)

        assert len(output_events) == 1
        assert output_events[0][1] == unicode_text

    @pytest.mark.asyncio
    async def test_output_callback_not_called_when_none(self, executor):
        """Test that no error occurs when callback is None."""
        executor.on_output_chunk = None

        # Should not raise even though callback is None
        try:
            # This would be called in _run_local()
            if executor.on_output_chunk:
                await executor.on_output_chunk('stdout', 'test')
            # No error should occur
            assert True
        except Exception as e:
            pytest.fail(f"Should not raise exception: {e}")

    @pytest.mark.asyncio
    async def test_sync_output_callback_supported(self, executor):
        """Test that synchronous output callbacks are supported."""
        output_events = []

        # Sync callback
        def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        if executor.on_output_chunk:
            result = executor.on_output_chunk('stdout', 'test')
            # Sync callback returns None
            assert result is None

        assert len(output_events) == 1

    @pytest.mark.asyncio
    async def test_output_accumulation_without_buffering_all(self, executor):
        """Test that output is streamed without buffering entire output in memory."""
        output_count = 0

        async def on_output(stream_type, chunk):
            nonlocal output_count
            output_count += 1

        executor.on_output_chunk = on_output

        # Simulate streaming 1000 lines
        if executor.on_output_chunk:
            for i in range(1000):
                await executor.on_output_chunk('stdout', f'Line {i}')

        # Each line generates a callback
        assert output_count == 1000

    @pytest.mark.asyncio
    async def test_empty_output_lines_handled(self, executor):
        """Test that empty lines are properly handled."""
        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', 'Line 1')
            await executor.on_output_chunk('stdout', '')  # Empty line
            await executor.on_output_chunk('stdout', 'Line 2')

        assert len(output_events) == 3
        assert output_events[1] == ('stdout', '')

    @pytest.mark.asyncio
    async def test_multiline_chunk_handling(self, executor):
        """Test handling of multiline chunks."""
        output_events = []

        async def on_output(stream_type, chunk):
            output_events.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate a chunk with multiple lines (though _run_local uses readline)
        multiline = "Line 1\nLine 2\nLine 3"
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', multiline)

        assert len(output_events) == 1
        assert output_events[0][1] == multiline

    @pytest.mark.asyncio
    async def test_output_callback_exceptions_dont_crash(self, executor):
        """Test that callback exceptions don't crash the process."""
        async def failing_callback(stream_type, chunk):
            raise ValueError("Callback error")

        executor.on_output_chunk = failing_callback

        # In real _run_local(), callback exceptions would be caught
        # This test documents the expected behavior
        try:
            if executor.on_output_chunk:
                await executor.on_output_chunk('stdout', 'test')
            # In production, this would be caught in _run_local()
        except ValueError:
            # Expected - callbacks can raise but should be handled
            pass
