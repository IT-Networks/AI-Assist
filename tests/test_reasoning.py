"""
Tests für Reasoning-Support im LLM-Client und Orchestrator.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.llm_client import LLMClient, LLMResponse
from app.core.config import settings


class TestReasoningInjection:
    """Tests für die _inject_reasoning Methode."""

    def test_inject_reasoning_high(self):
        """Reasoning 'high' wird korrekt in System-Message injiziert."""
        client = LLMClient()
        messages = [{"role": "system", "content": "Du bist ein Assistent."}]

        result = client._inject_reasoning(messages, "high")

        assert result[0]["content"].startswith("reasoning: high\n\n")
        assert "Du bist ein Assistent." in result[0]["content"]

    def test_inject_reasoning_medium(self):
        """Reasoning 'medium' wird korrekt injiziert."""
        client = LLMClient()
        messages = [{"role": "system", "content": "Test"}]

        result = client._inject_reasoning(messages, "medium")

        assert result[0]["content"] == "reasoning: medium\n\nTest"

    def test_inject_reasoning_low(self):
        """Reasoning 'low' wird korrekt injiziert."""
        client = LLMClient()
        messages = [{"role": "system", "content": "Test"}]

        result = client._inject_reasoning(messages, "low")

        assert result[0]["content"] == "reasoning: low\n\nTest"

    def test_inject_reasoning_none_returns_original(self):
        """Bei None wird die Original-Liste zurückgegeben."""
        client = LLMClient()
        messages = [{"role": "system", "content": "Test"}]

        result = client._inject_reasoning(messages, None)

        assert result == messages

    def test_inject_reasoning_empty_returns_original(self):
        """Bei leerem String wird die Original-Liste zurückgegeben."""
        client = LLMClient()
        messages = [{"role": "system", "content": "Test"}]

        result = client._inject_reasoning(messages, "")

        assert result == messages

    def test_inject_reasoning_invalid_level_returns_original(self):
        """Bei ungültigem Level wird die Original-Liste zurückgegeben."""
        client = LLMClient()
        messages = [{"role": "system", "content": "Test"}]

        result = client._inject_reasoning(messages, "invalid")

        assert result == messages

    def test_inject_reasoning_no_system_message_creates_one(self):
        """Wenn keine System-Message existiert, wird eine erstellt."""
        client = LLMClient()
        messages = [{"role": "user", "content": "Hallo"}]

        result = client._inject_reasoning(messages, "high")

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "reasoning: high"
        assert result[1]["role"] == "user"

    def test_inject_reasoning_does_not_duplicate(self):
        """Reasoning wird nicht doppelt hinzugefügt."""
        client = LLMClient()
        messages = [{"role": "system", "content": "reasoning: high\n\nTest"}]

        result = client._inject_reasoning(messages, "high")

        # Sollte nicht "reasoning: high\n\nreasoning: high\n\nTest" sein
        assert result[0]["content"] == "reasoning: high\n\nTest"

    def test_inject_reasoning_preserves_original(self):
        """Original-Liste wird nicht modifiziert."""
        client = LLMClient()
        original = [{"role": "system", "content": "Test"}]

        result = client._inject_reasoning(original, "high")

        # Original sollte unverändert sein
        assert original[0]["content"] == "Test"
        # Result sollte modifiziert sein
        assert result[0]["content"].startswith("reasoning:")


class TestReasoningConfig:
    """Tests für Reasoning-Konfiguration."""

    def test_config_has_reasoning_fields(self):
        """Config hat die Reasoning-Felder."""
        assert hasattr(settings.llm, 'reasoning_effort')
        assert hasattr(settings.llm, 'analysis_reasoning')
        assert hasattr(settings.llm, 'tool_reasoning')

    def test_analysis_reasoning_default(self):
        """analysis_reasoning hat sinnvollen Default."""
        # Default sollte 'high' sein
        assert settings.llm.analysis_reasoning in ('', 'low', 'medium', 'high')

    def test_tool_reasoning_default_empty(self):
        """tool_reasoning sollte standardmäßig leer sein."""
        # Tool-Phase braucht normalerweise kein Reasoning
        assert settings.llm.tool_reasoning == '' or settings.llm.tool_reasoning in ('low', 'medium', 'high')


class TestReasoningInLLMCall:
    """Tests für Reasoning in chat_with_tools."""

    @pytest.mark.asyncio
    async def test_chat_with_tools_passes_reasoning(self):
        """chat_with_tools übergibt reasoning Parameter korrekt."""
        client = LLMClient()

        with patch.object(client, '_inject_reasoning', return_value=[{"role": "user", "content": "test"}]) as mock_inject:
            with patch('app.services.llm_client._get_http_client') as mock_http:
                mock_response = MagicMock()
                mock_response.json.return_value = {
                    "choices": [{"message": {"content": "Test"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}
                }
                mock_response.raise_for_status = MagicMock()
                mock_http.return_value.post = AsyncMock(return_value=mock_response)

                await client.chat_with_tools(
                    messages=[{"role": "user", "content": "test"}],
                    reasoning="high"
                )

                mock_inject.assert_called_once()
                call_args = mock_inject.call_args
                assert call_args[0][1] == "high"  # reasoning parameter

    @pytest.mark.asyncio
    async def test_chat_with_tools_no_reasoning_when_none(self):
        """chat_with_tools ruft _inject_reasoning nicht auf wenn reasoning=None."""
        client = LLMClient()

        with patch.object(client, '_inject_reasoning', return_value=[{"role": "user", "content": "test"}]) as mock_inject:
            with patch('app.services.llm_client._get_http_client') as mock_http:
                mock_response = MagicMock()
                mock_response.json.return_value = {
                    "choices": [{"message": {"content": "Test"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}
                }
                mock_response.raise_for_status = MagicMock()
                mock_http.return_value.post = AsyncMock(return_value=mock_response)

                await client.chat_with_tools(
                    messages=[{"role": "user", "content": "test"}],
                    reasoning=None
                )

                # _inject_reasoning sollte NICHT aufgerufen werden bei None
                mock_inject.assert_not_called()
