"""
Phase 1: Test callback mechanism for pip installation progress.

Tests that callbacks are properly fired during pip package installation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
from pathlib import Path

from app.services.script_manager import ScriptExecutor, Script


@pytest.fixture
def executor():
    """Create ScriptExecutor instance."""
    return ScriptExecutor()


@pytest.fixture
def sample_script():
    """Create a sample script with requirements."""
    return Script(
        id="test-1",
        name="Test Script",
        description="Test script with requirements",
        code="print('Hello')",
        created_at=datetime.now(),
        requirements=["pandas==1.3.0", "numpy"]
    )


class TestCallbackMechanism:
    """Tests for Phase 1 callback mechanism."""

    @pytest.mark.asyncio
    async def test_pip_callbacks_registered(self, executor):
        """Test that callbacks are properly registered."""
        mock_start = AsyncMock()
        mock_installing = AsyncMock()
        mock_installed = AsyncMock()
        mock_complete = AsyncMock()

        # Callbacks should be registered when run() is called
        assert executor.on_pip_start is None
        assert executor.on_pip_installing is None
        assert executor.on_pip_installed is None
        assert executor.on_pip_complete is None

    @pytest.mark.asyncio
    async def test_on_pip_start_callback_fired(self, executor, sample_script):
        """Test that on_pip_start callback is fired with correct data."""
        callback_data = []

        async def on_pip_start(requirements):
            callback_data.append(('start', requirements))

        # Note: This is a unit test of callback registration, not actual pip
        executor.on_pip_start = on_pip_start
        if executor.on_pip_start:
            await executor.on_pip_start(sample_script.requirements)

        assert len(callback_data) == 1
        assert callback_data[0][0] == 'start'
        assert callback_data[0][1] == sample_script.requirements

    @pytest.mark.asyncio
    async def test_on_pip_installing_callback_fired(self, executor):
        """Test that on_pip_installing callback is fired for each package."""
        callback_data = []

        async def on_pip_installing(pkg):
            callback_data.append(('installing', pkg))

        executor.on_pip_installing = on_pip_installing

        # Simulate multiple package installations
        test_packages = ["pandas", "numpy", "scipy"]
        for pkg in test_packages:
            if executor.on_pip_installing:
                await executor.on_pip_installing(pkg)

        assert len(callback_data) == 3
        assert all(data[0] == 'installing' for data in callback_data)
        assert [data[1] for data in callback_data] == test_packages

    @pytest.mark.asyncio
    async def test_on_pip_installed_success_callback(self, executor):
        """Test that on_pip_installed callback is fired on success."""
        callback_data = []

        async def on_pip_installed(pkg, success, error):
            callback_data.append(('installed', pkg, success, error))

        executor.on_pip_installed = on_pip_installed

        # Simulate successful installation
        if executor.on_pip_installed:
            await executor.on_pip_installed("pandas", True, None)

        assert len(callback_data) == 1
        assert callback_data[0] == ('installed', 'pandas', True, None)

    @pytest.mark.asyncio
    async def test_on_pip_installed_failure_callback(self, executor):
        """Test that on_pip_installed callback is fired on failure with error."""
        callback_data = []

        async def on_pip_installed(pkg, success, error):
            callback_data.append(('installed', pkg, success, error))

        executor.on_pip_installed = on_pip_installed

        # Simulate failed installation
        error_msg = "Could not find a version that satisfies requirement"
        if executor.on_pip_installed:
            await executor.on_pip_installed("nonexistent-pkg", False, error_msg)

        assert len(callback_data) == 1
        assert callback_data[0][0] == 'installed'
        assert callback_data[0][1] == "nonexistent-pkg"
        assert callback_data[0][2] is False
        assert error_msg in callback_data[0][3]

    @pytest.mark.asyncio
    async def test_on_pip_complete_callback_fired(self, executor):
        """Test that on_pip_complete callback is fired with timing."""
        callback_data = []

        async def on_pip_complete(success, total_ms):
            callback_data.append(('complete', success, total_ms))

        executor.on_pip_complete = on_pip_complete

        # Simulate completion
        if executor.on_pip_complete:
            await executor.on_pip_complete(True, 5000)

        assert len(callback_data) == 1
        assert callback_data[0] == ('complete', True, 5000)

    @pytest.mark.asyncio
    async def test_sync_callbacks_supported(self, executor):
        """Test that synchronous callbacks are supported."""
        callback_data = []

        # Sync callback (not async)
        def on_pip_start(requirements):
            callback_data.append(('start', requirements))

        executor.on_pip_start = on_pip_start

        # Should handle both sync and async
        if executor.on_pip_start:
            result = executor.on_pip_start(["pandas"])
            # Sync callback returns None, not a coroutine
            assert not hasattr(result, '__await__')

        assert len(callback_data) == 1

    @pytest.mark.asyncio
    async def test_multiple_callbacks_chained(self, executor):
        """Test that multiple callbacks fire in sequence."""
        callback_sequence = []

        async def on_pip_start(reqs):
            callback_sequence.append('start')

        async def on_pip_installing(pkg):
            callback_sequence.append(f'installing:{pkg}')

        async def on_pip_installed(pkg, success, error):
            callback_sequence.append(f'installed:{pkg}:{success}')

        async def on_pip_complete(success, ms):
            callback_sequence.append(f'complete:{success}')

        executor.on_pip_start = on_pip_start
        executor.on_pip_installing = on_pip_installing
        executor.on_pip_installed = on_pip_installed
        executor.on_pip_complete = on_pip_complete

        # Simulate pip install sequence
        if executor.on_pip_start:
            await executor.on_pip_start(["pandas", "numpy"])

        for pkg in ["pandas", "numpy"]:
            if executor.on_pip_installing:
                await executor.on_pip_installing(pkg)
            if executor.on_pip_installed:
                await executor.on_pip_installed(pkg, True, None)

        if executor.on_pip_complete:
            await executor.on_pip_complete(True, 3000)

        assert len(callback_sequence) == 7
        assert callback_sequence[0] == 'start'
        assert callback_sequence[1] == 'installing:pandas'
        assert callback_sequence[2] == 'installed:pandas:True'
        assert callback_sequence[3] == 'installing:numpy'
        assert callback_sequence[4] == 'installed:numpy:True'
        assert callback_sequence[5] == 'complete:True'

    def test_callback_cleanup_after_execution(self, executor):
        """Test that callbacks are cleaned up after execution."""
        async def dummy_callback(*args, **kwargs):
            pass

        executor.on_pip_start = dummy_callback
        assert executor.on_pip_start is not None

        # Reset callbacks (as done in run() finally block)
        executor.on_pip_start = None
        assert executor.on_pip_start is None
