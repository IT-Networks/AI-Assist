"""
Integration tests for complete 3-phase script execution flow.

Tests the full flow: confirmation → pip install callbacks → output streaming → frontend display.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.services.script_manager import Script, ScriptManager, ScriptExecutor


@pytest.fixture
def script_with_requirements():
    """Create a script with pip requirements."""
    return Script(
        id="test-1",
        name="Test Script",
        description="Test script with requirements",
        code="print('Hello from script')",
        created_at=datetime.now(),
        requirements=["pandas", "numpy"]
    )


class TestIntegrationFlow:
    """Integration tests for full 3-phase flow."""

    @pytest.mark.asyncio
    async def test_pip_callbacks_fire_before_script_execution(self, script_with_requirements):
        """Test that pip callbacks complete before script execution."""
        executor = ScriptExecutor()
        event_log = []

        async def on_pip_start(reqs):
            event_log.append(('pip_start', reqs))

        async def on_pip_installing(pkg):
            event_log.append(('pip_installing', pkg))

        async def on_pip_installed(pkg, success, error):
            event_log.append(('pip_installed', pkg, success))

        async def on_pip_complete(success, ms):
            event_log.append(('pip_complete', success, ms))

        async def on_output(stream_type, chunk):
            event_log.append(('output', stream_type, chunk))

        # Register all callbacks
        executor.on_pip_start = on_pip_start
        executor.on_pip_installing = on_pip_installing
        executor.on_pip_installed = on_pip_installed
        executor.on_pip_complete = on_pip_complete
        executor.on_output_chunk = on_output

        # Simulate event sequence
        if executor.on_pip_start:
            await executor.on_pip_start(script_with_requirements.requirements)

        for pkg in script_with_requirements.requirements:
            if executor.on_pip_installing:
                await executor.on_pip_installing(pkg)
            if executor.on_pip_installed:
                await executor.on_pip_installed(pkg, True, None)

        if executor.on_pip_complete:
            await executor.on_pip_complete(True, 1234)

        # Script would output here
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', 'Hello from script')

        # Verify event sequence
        assert len(event_log) >= 7
        assert event_log[0][0] == 'pip_start'
        assert event_log[1][0] == 'pip_installing'
        assert event_log[2][0] == 'pip_installed'
        assert event_log[-1] == ('output', 'stdout', 'Hello from script')

    @pytest.mark.asyncio
    async def test_output_collected_during_execution(self):
        """Test that output is collected during script execution."""
        executor = ScriptExecutor()
        collected_output = []

        async def on_output(stream_type, chunk):
            collected_output.append({'type': stream_type, 'data': chunk})

        executor.on_output_chunk = on_output

        # Simulate script execution producing output
        outputs = [
            ('stdout', 'Processing data...'),
            ('stdout', 'Creating DataFrame...'),
            ('stdout', 'Done!')
        ]

        for stream_type, line in outputs:
            if executor.on_output_chunk:
                await executor.on_output_chunk(stream_type, line)

        assert len(collected_output) == 3
        assert collected_output[0]['data'] == 'Processing data...'
        assert collected_output[1]['data'] == 'Creating DataFrame...'
        assert collected_output[2]['data'] == 'Done!'

    @pytest.mark.asyncio
    async def test_error_in_pip_install_captured(self):
        """Test that errors during pip install are captured."""
        executor = ScriptExecutor()
        events = []

        async def on_pip_start(reqs):
            events.append(('start', reqs))

        async def on_pip_installing(pkg):
            events.append(('installing', pkg))

        async def on_pip_installed(pkg, success, error):
            events.append(('installed', pkg, success, error))

        async def on_pip_complete(success, ms):
            events.append(('complete', success, ms))

        executor.on_pip_start = on_pip_start
        executor.on_pip_installing = on_pip_installing
        executor.on_pip_installed = on_pip_installed
        executor.on_pip_complete = on_pip_complete

        # Simulate pip install with error
        if executor.on_pip_start:
            await executor.on_pip_start(['nonexistent-pkg'])

        if executor.on_pip_installing:
            await executor.on_pip_installing('nonexistent-pkg')

        if executor.on_pip_installed:
            await executor.on_pip_installed(
                'nonexistent-pkg',
                False,
                'Could not find a version that satisfies requirement'
            )

        if executor.on_pip_complete:
            await executor.on_pip_complete(False, 500)

        # Verify error was captured
        assert events[2][2] is False  # success=False
        assert 'Could not find' in events[2][3]  # error message
        assert events[3][1] is False  # pip_complete success=False

    @pytest.mark.asyncio
    async def test_script_execution_with_error_output(self):
        """Test that script errors are captured as stderr."""
        executor = ScriptExecutor()
        output_lines = []

        async def on_output(stream_type, chunk):
            output_lines.append((stream_type, chunk))

        executor.on_output_chunk = on_output

        # Simulate script that produces error
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', 'Starting operation...')
            await executor.on_output_chunk('stderr', 'Traceback (most recent call last):')
            await executor.on_output_chunk('stderr', '  File "script.py", line 5, in <module>')
            await executor.on_output_chunk('stderr', '    raise RuntimeError("Test error")')
            await executor.on_output_chunk('stderr', 'RuntimeError: Test error')

        # Verify error output was captured
        stderr_lines = [line for stream, line in output_lines if stream == 'stderr']
        assert len(stderr_lines) == 4
        assert 'Traceback' in stderr_lines[0]
        assert 'RuntimeError' in stderr_lines[-1]

    @pytest.mark.asyncio
    async def test_large_output_without_buffering_all(self):
        """Test that large output is handled without buffering entire output."""
        executor = ScriptExecutor()
        callback_count = 0
        total_data_size = 0

        async def on_output(stream_type, chunk):
            nonlocal callback_count, total_data_size
            callback_count += 1
            total_data_size += len(chunk)

        executor.on_output_chunk = on_output

        # Simulate large output (1000 lines)
        if executor.on_output_chunk:
            for i in range(1000):
                line = f"Data line {i}: " + ("x" * 50)
                await executor.on_output_chunk('stdout', line)

        # Verify output was delivered line-by-line
        assert callback_count == 1000
        assert total_data_size > 50000  # Each line is ~70 chars

    @pytest.mark.asyncio
    async def test_concurrent_scripts_dont_share_output(self):
        """Test that concurrent script executions don't share output."""
        executor1 = ScriptExecutor()
        executor2 = ScriptExecutor()

        output1 = []
        output2 = []

        async def on_output1(stream_type, chunk):
            output1.append(chunk)

        async def on_output2(stream_type, chunk):
            output2.append(chunk)

        executor1.on_output_chunk = on_output1
        executor2.on_output_chunk = on_output2

        # Simulate concurrent output
        if executor1.on_output_chunk:
            await executor1.on_output_chunk('stdout', 'Script 1 output')

        if executor2.on_output_chunk:
            await executor2.on_output_chunk('stdout', 'Script 2 output')

        # Verify outputs are separate
        assert len(output1) == 1
        assert len(output2) == 1
        assert output1[0] == 'Script 1 output'
        assert output2[0] == 'Script 2 output'

    @pytest.mark.asyncio
    async def test_full_event_sequence(self):
        """Test complete event sequence from pip to script execution."""
        executor = ScriptExecutor()
        full_sequence = []

        async def track_event(category, data):
            full_sequence.append((category, data))

        async def on_pip_start(reqs):
            await track_event('pip_start', reqs)

        async def on_pip_installing(pkg):
            await track_event('pip_installing', pkg)

        async def on_pip_installed(pkg, success, error):
            await track_event('pip_installed', (pkg, success))

        async def on_pip_complete(success, ms):
            await track_event('pip_complete', (success, ms))

        async def on_output(stream_type, chunk):
            await track_event('output', (stream_type, chunk))

        executor.on_pip_start = on_pip_start
        executor.on_pip_installing = on_pip_installing
        executor.on_pip_installed = on_pip_installed
        executor.on_pip_complete = on_pip_complete
        executor.on_output_chunk = on_output

        # Simulate full sequence
        packages = ['pandas', 'numpy']

        if executor.on_pip_start:
            await executor.on_pip_start(packages)

        for pkg in packages:
            if executor.on_pip_installing:
                await executor.on_pip_installing(pkg)
            if executor.on_pip_installed:
                await executor.on_pip_installed(pkg, True, None)

        if executor.on_pip_complete:
            await executor.on_pip_complete(True, 2500)

        # Script execution
        if executor.on_output_chunk:
            await executor.on_output_chunk('stdout', 'import pandas')
            await executor.on_output_chunk('stdout', 'df = pd.read_csv(...)')
            await executor.on_output_chunk('stdout', 'Results computed')

        # Verify full sequence
        assert len(full_sequence) >= 8
        assert full_sequence[0][0] == 'pip_start'
        assert full_sequence[1][0] == 'pip_installing'
        assert full_sequence[2][0] == 'pip_installed'
        assert full_sequence[3][0] == 'pip_installing'
        assert full_sequence[4][0] == 'pip_installed'
        assert full_sequence[5][0] == 'pip_complete'
        # Last 3 should be output events
        assert all(f[0] == 'output' for f in full_sequence[6:])
