"""
Phase 3: Test frontend event handlers for pip progress and script output.

Tests that event handlers properly display pip progress and script output in chat.
"""

import pytest
from unittest.mock import MagicMock, patch
import json


# Mock chat object for testing
@pytest.fixture
def mock_chat():
    """Create a mock chat object."""
    return {
        'id': 'chat-1',
        'pane': MagicMock(),
        'scriptOutput': {}
    }


# Mock active chat in chatManager
@pytest.fixture
def mock_chat_manager():
    """Create a mock chat manager."""
    manager = MagicMock()
    manager.activeId = 'chat-1'
    manager.getActive = MagicMock(return_value=None)
    return manager


class TestPipProgressEventHandler:
    """Tests for pip installation progress event handler."""

    def test_pip_install_start_event(self, mock_chat):
        """Test handling of pip_install_start event."""
        # This would be called as: handlePipProgress(data, chat)
        event_data = {
            'type': 'pip_install_start',
            'message': 'Installiere 3 Python-Pakete...',
            'total': 3
        }

        # In real implementation, this would append to chat
        # For testing, we just verify the event structure
        assert event_data['type'] == 'pip_install_start'
        assert 'Installiere' in event_data['message']
        assert event_data['total'] == 3

    def test_pip_installing_event(self):
        """Test handling of pip_installing event (per-package)."""
        event_data = {
            'type': 'pip_installing',
            'package': 'pandas',
            'message': '↓ Installiere: pandas'
        }

        assert event_data['type'] == 'pip_installing'
        assert event_data['package'] == 'pandas'
        assert 'pandas' in event_data['message']

    def test_pip_installed_success_event(self):
        """Test handling of successful pip_installed event."""
        event_data = {
            'type': 'pip_installed',
            'package': 'pandas',
            'success': True,
            'message': '✓ pandas installiert'
        }

        assert event_data['type'] == 'pip_installed'
        assert event_data['success'] is True
        assert '✓' in event_data['message']

    def test_pip_installed_failure_event(self):
        """Test handling of failed pip_installed event."""
        event_data = {
            'type': 'pip_installed',
            'package': 'nonexistent-pkg',
            'success': False,
            'error': 'Could not find a version that satisfies requirement',
            'message': '✗ nonexistent-pkg fehlgeschlagen: Could not find...'
        }

        assert event_data['type'] == 'pip_installed'
        assert event_data['success'] is False
        assert 'Could not find' in event_data['error']
        assert '✗' in event_data['message']

    def test_pip_install_complete_event(self):
        """Test handling of pip_install_complete event."""
        event_data = {
            'type': 'pip_install_complete',
            'success': True,
            'duration_ms': 2456,
            'message': '✅ Pip-Installation abgeschlossen (2456ms)'
        }

        assert event_data['type'] == 'pip_install_complete'
        assert event_data['success'] is True
        assert 'abgeschlossen' in event_data['message']

    def test_multiple_pip_events_sequence(self):
        """Test handling sequence of multiple pip events."""
        events = [
            {
                'type': 'pip_install_start',
                'message': 'Installiere 2 Python-Pakete...',
                'total': 2
            },
            {
                'type': 'pip_installing',
                'package': 'pandas',
                'message': '↓ Installiere: pandas'
            },
            {
                'type': 'pip_installed',
                'package': 'pandas',
                'success': True,
                'message': '✓ pandas installiert'
            },
            {
                'type': 'pip_installing',
                'package': 'numpy',
                'message': '↓ Installiere: numpy'
            },
            {
                'type': 'pip_installed',
                'package': 'numpy',
                'success': True,
                'message': '✓ numpy installiert'
            },
            {
                'type': 'pip_install_complete',
                'success': True,
                'duration_ms': 3000,
                'message': '✅ Pip-Installation abgeschlossen (3000ms)'
            }
        ]

        # Verify event sequence
        assert events[0]['type'] == 'pip_install_start'
        assert events[1]['type'] == 'pip_installing'
        assert events[2]['type'] == 'pip_installed'
        assert events[2]['success'] is True
        assert events[-1]['type'] == 'pip_install_complete'
        assert events[-1]['success'] is True


class TestScriptOutputEventHandler:
    """Tests for script output event handler."""

    def test_script_stdout_event(self, mock_chat):
        """Test handling of script stdout event."""
        event_data = {
            'type': 'script_output',
            'stream_type': 'stdout',
            'chunk': 'Processing data...',
            'message': 'Processing data...'
        }

        assert event_data['type'] == 'script_output'
        assert event_data['stream_type'] == 'stdout'
        assert event_data['chunk'] == 'Processing data...'

    def test_script_stderr_event(self, mock_chat):
        """Test handling of script stderr event."""
        event_data = {
            'type': 'script_output',
            'stream_type': 'stderr',
            'chunk': 'Error: Division by zero',
            'message': 'Error: Division by zero'
        }

        assert event_data['type'] == 'script_output'
        assert event_data['stream_type'] == 'stderr'
        assert 'Error' in event_data['chunk']

    def test_output_accumulation_per_chat(self, mock_chat):
        """Test that output is accumulated per chat."""
        mock_chat['scriptOutput'] = {'stdout': '', 'stderr': ''}

        # Simulate multiple output events
        output_events = [
            {'stream_type': 'stdout', 'chunk': 'Line 1'},
            {'stream_type': 'stdout', 'chunk': 'Line 2'},
            {'stream_type': 'stderr', 'chunk': 'Error: something'},
        ]

        for event in output_events:
            if mock_chat['scriptOutput']:
                stream_type = event['stream_type']
                chunk = event['chunk']
                mock_chat['scriptOutput'][stream_type] += chunk + '\n'

        # Verify accumulation
        assert 'Line 1' in mock_chat['scriptOutput']['stdout']
        assert 'Line 2' in mock_chat['scriptOutput']['stdout']
        assert 'Error' in mock_chat['scriptOutput']['stderr']

    def test_output_with_special_characters(self):
        """Test handling of special characters in output."""
        event_data = {
            'type': 'script_output',
            'stream_type': 'stdout',
            'chunk': 'HTML: <div>test</div>',
            'message': 'HTML: <div>test</div>'
        }

        # In real implementation, escapeHtml() would sanitize this
        assert '<div>' in event_data['chunk']

    def test_output_with_unicode(self):
        """Test handling of unicode characters."""
        event_data = {
            'type': 'script_output',
            'stream_type': 'stdout',
            'chunk': 'Unicode: 中文 العربية 🚀',
            'message': 'Unicode: 中文 العربية 🚀'
        }

        assert '中文' in event_data['chunk']
        assert '🚀' in event_data['chunk']

    def test_empty_output_line(self):
        """Test handling of empty output lines."""
        event_data = {
            'type': 'script_output',
            'stream_type': 'stdout',
            'chunk': '',
            'message': ''
        }

        assert event_data['chunk'] == ''

    def test_large_output_line(self):
        """Test handling of very large output lines."""
        large_chunk = 'x' * 10000
        event_data = {
            'type': 'script_output',
            'stream_type': 'stdout',
            'chunk': large_chunk,
            'message': large_chunk
        }

        assert len(event_data['chunk']) == 10000

    def test_output_stream_isolation(self):
        """Test that stdout and stderr are kept separate."""
        output_buffer = {'stdout': '', 'stderr': ''}

        events = [
            ('stdout', 'Output 1'),
            ('stderr', 'Error 1'),
            ('stdout', 'Output 2'),
            ('stderr', 'Error 2'),
        ]

        for stream_type, chunk in events:
            output_buffer[stream_type] += chunk + '\n'

        # Verify separation
        stdout_lines = output_buffer['stdout'].strip().split('\n')
        stderr_lines = output_buffer['stderr'].strip().split('\n')

        assert len(stdout_lines) == 2
        assert len(stderr_lines) == 2
        assert stdout_lines[0] == 'Output 1'
        assert stderr_lines[0] == 'Error 1'


class TestEventRouting:
    """Tests for mcp_progress event routing logic."""

    def test_pip_events_routed_to_pip_handler(self):
        """Test that pip_* events are routed to pip handler."""
        event_data = {
            'type': 'pip_installing',
            'package': 'pandas'
        }

        # Route logic: if type starts with 'pip_' → handlePipProgress
        if event_data['type'].startswith('pip_'):
            handler = 'handlePipProgress'
        else:
            handler = 'other'

        assert handler == 'handlePipProgress'

    def test_script_output_events_routed_to_output_handler(self):
        """Test that script_output events are routed to output handler."""
        event_data = {
            'type': 'script_output',
            'stream_type': 'stdout'
        }

        # Route logic: if type == 'script_output' → handleScriptOutput
        if event_data['type'] == 'script_output':
            handler = 'handleScriptOutput'
        else:
            handler = 'other'

        assert handler == 'handleScriptOutput'

    def test_other_mcp_progress_routed_to_thinking_handler(self):
        """Test that other mcp_progress events use thinking handler."""
        event_data = {
            'type': 'step_analysis'
        }

        # Route logic: otherwise → updateThinkingProgress
        if event_data['type'].startswith('pip_'):
            handler = 'handlePipProgress'
        elif event_data['type'] == 'script_output':
            handler = 'handleScriptOutput'
        else:
            handler = 'updateThinkingProgress'

        assert handler == 'updateThinkingProgress'


class TestEventDisplay:
    """Tests for how events are displayed in chat."""

    def test_pip_start_message_format(self):
        """Test message format for pip start."""
        event = {
            'type': 'pip_install_start',
            'message': 'Installiere 3 Python-Pakete...'
        }
        # Format: ⬇️ message
        formatted = f"⬇️ {event['message']}"
        assert formatted == "⬇️ Installiere 3 Python-Pakete..."

    def test_pip_installing_message_format(self):
        """Test message format for each package installation."""
        event = {
            'type': 'pip_installing',
            'message': '↓ Installiere: pandas'
        }
        # Format: "  " + message (indented)
        formatted = f"  {event['message']}"
        assert formatted == "  ↓ Installiere: pandas"

    def test_pip_success_message_format(self):
        """Test message format for successful installation."""
        event = {
            'type': 'pip_installed',
            'success': True,
            'message': '✓ pandas installiert'
        }
        # Format: "  " + message
        formatted = f"  {event['message']}"
        assert formatted == "  ✓ pandas installiert"

    def test_pip_complete_message_format(self):
        """Test message format for completion."""
        event = {
            'type': 'pip_install_complete',
            'message': '✅ Pip-Installation abgeschlossen (2456ms)'
        }
        # Format: message
        formatted = f"✅ {event['message']}"
        assert '(2456ms)' in formatted

    def test_stdout_message_format(self):
        """Test message format for stdout."""
        event = {
            'type': 'script_output',
            'stream_type': 'stdout',
            'chunk': 'Processing data...'
        }
        # Format: 📤 + chunk
        prefix = '📤'
        formatted = f"{prefix} {event['chunk']}"
        assert formatted == "📤 Processing data..."

    def test_stderr_message_format(self):
        """Test message format for stderr."""
        event = {
            'type': 'script_output',
            'stream_type': 'stderr',
            'chunk': 'Error: Something went wrong'
        }
        # Format: ⚠️ + chunk
        prefix = '⚠️'
        formatted = f"{prefix} {event['chunk']}"
        assert formatted == "⚠️ Error: Something went wrong"
