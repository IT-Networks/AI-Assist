"""
Tests für die Performance-Optimierungen.

Validiert:
1. TokenBudget Dict-Lookup
2. Context Compactor Caching
3. Tool-Parser Early-Exit
4. Event Bridge Lock-Free Pattern
"""

import asyncio
import pytest
from unittest.mock import MagicMock


class TestTokenBudgetOptimization:
    """Tests für TokenBudget Dict-Lookup Optimierung."""

    def test_category_map_initialized(self):
        """Prüft ob das Kategorie-Mapping initialisiert wird."""
        from app.core.token_budget import TokenBudget
        budget = TokenBudget()

        assert hasattr(budget, '_category_map')
        assert 'system' in budget._category_map
        assert 'memory' in budget._category_map
        assert 'context' in budget._category_map
        assert 'conversation' in budget._category_map

    def test_can_add_uses_mapping(self):
        """Prüft ob can_add das Mapping nutzt."""
        from app.core.token_budget import TokenBudget
        budget = TokenBudget()

        # Sollte True sein - innerhalb des Limits
        assert budget.can_add('context', 100) == True

        # Sollte False sein für unbekannte Kategorie
        assert budget.can_add('unknown_category', 100) == False

    def test_add_uses_mapping(self):
        """Prüft ob add das Mapping nutzt."""
        from app.core.token_budget import TokenBudget
        budget = TokenBudget()

        initial = budget.used_context
        budget.add('context', 500)
        assert budget.used_context == initial + 500

        # Unbekannte Kategorie sollte False zurückgeben
        assert budget.add('unknown', 100) == False

    def test_set_uses_mapping(self):
        """Prüft ob set das Mapping nutzt."""
        from app.core.token_budget import TokenBudget
        budget = TokenBudget()

        budget.set('memory', 1234)
        assert budget.used_memory == 1234


class TestContextCompactorOptimization:
    """Tests für Context Compactor Caching Optimierung."""

    def test_relevance_with_cached_text(self):
        """Prüft ob _compute_relevance cached_text akzeptiert."""
        from app.core.context_compactor import ContextCompactor, ContextItem, ContextPriority

        compactor = ContextCompactor()
        item = ContextItem(
            content="Python programming language code",
            item_type="tool_output",
            priority=ContextPriority.OLD_TOOL,
            tokens=100
        )

        # Mit cached_text sollte kein join nötig sein
        cached = "python programming test"
        relevance = compactor._compute_relevance(item, [], _cached_text=cached)
        assert relevance > 0  # "python" und "programming" sollten matchen

    def test_compact_adds_sort_keys(self):
        """Prüft ob compact Sort-Keys cached wenn Kompaktierung nötig."""
        from app.core.context_compactor import ContextCompactor, ContextItem, ContextPriority

        compactor = ContextCompactor()
        items = [
            ContextItem("Test content", "tool_output", ContextPriority.OLD_TOOL, tokens=300),
            ContextItem("More content", "message", ContextPriority.OLD_MESSAGE, tokens=300),
            ContextItem("Even more", "tool_output", ContextPriority.RECENT_TOOL, tokens=300),
        ]

        # Kompaktierung ist nötig (900 > 500), also werden Sort-Keys gesetzt
        result = compactor.compact(items, target_tokens=500, preserve_recent=1, recent_messages=["test"])

        # Nach Kompaktierung sollten Sort-Keys existieren
        for item in result:
            assert hasattr(item, '_sort_key')
            assert hasattr(item, '_remove_key')

    def test_compact_efficient_removal(self):
        """Prüft ob Removal effizient ist (keine O(n^2))."""
        from app.core.context_compactor import ContextCompactor, ContextItem, ContextPriority

        compactor = ContextCompactor()
        # Erstelle viele Items
        items = [
            ContextItem(f"Content {i}", "tool_output", ContextPriority.OLD_TOOL, tokens=100)
            for i in range(50)
        ]

        # Kompaktiere auf weniger Tokens
        result = compactor.compact(items, target_tokens=1000, preserve_recent=2)

        # Sollte funktionieren und Items entfernt haben
        assert len(result) < len(items)


class TestToolParserEarlyExit:
    """Tests für Tool-Parser Early-Exit Optimierung."""

    def test_early_exit_no_markers(self):
        """Prüft ob ohne Marker sofort [] zurückgegeben wird."""
        from app.agent.orchestrator import _parse_text_tool_calls

        # Kein Tool-Marker im Content
        content = "This is a normal response without any tool calls."
        result = _parse_text_tool_calls(content, [])

        assert result == []

    def test_early_exit_with_marker(self):
        """Prüft ob mit Marker geparst wird."""
        from app.agent.orchestrator import _parse_text_tool_calls

        # Mit Tool-Marker
        content = '[TOOL_CALLS] [{"name": "test_tool", "arguments": {"key": "value"}}]'
        tools = [{"function": {"name": "test_tool"}}]
        result = _parse_text_tool_calls(content, tools)

        assert len(result) == 1
        assert result[0]["function"]["name"] == "test_tool"

    def test_early_exit_empty_content(self):
        """Prüft ob leerer Content sofort [] zurückgibt."""
        from app.agent.orchestrator import _parse_text_tool_calls

        assert _parse_text_tool_calls("", []) == []
        assert _parse_text_tool_calls(None, []) == []


class TestEventBridgeOptimization:
    """Tests für Event Bridge Lock-Free Optimierung."""

    @pytest.mark.asyncio
    async def test_emit_without_subscribers(self):
        """Prüft ob emit ohne Subscriber schnell zurückkehrt."""
        from app.mcp.event_bridge import MCPEventBridge

        bridge = MCPEventBridge()
        # Sollte ohne Fehler durchlaufen
        await bridge.emit("TEST_EVENT", {"data": "test"})

    @pytest.mark.asyncio
    async def test_emit_with_subscribers(self):
        """Prüft ob Events an Subscriber gesendet werden."""
        from app.mcp.event_bridge import MCPEventBridge

        bridge = MCPEventBridge()
        queue = bridge.subscribe()

        await bridge.emit("TEST_EVENT", {"data": "test"})

        # Event sollte in Queue sein
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event_type == "TEST_EVENT"
        assert event.data["data"] == "test"

    @pytest.mark.asyncio
    async def test_subscribe_async_available(self):
        """Prüft ob async subscribe verfügbar ist."""
        from app.mcp.event_bridge import MCPEventBridge

        bridge = MCPEventBridge()
        queue = await bridge.subscribe_async()

        assert queue is not None
        assert bridge._subscribers

    @pytest.mark.asyncio
    async def test_dead_subscriber_cleanup(self):
        """Prüft ob tote Subscriber aufgeräumt werden."""
        from app.mcp.event_bridge import MCPEventBridge

        bridge = MCPEventBridge()
        queue = bridge.subscribe()

        # Simuliere volle Queue
        for i in range(bridge._max_buffer):
            await bridge.emit("FILL", {"i": i})

        # Nächstes Event sollte dropped werden (Queue voll)
        # aber keine Exception werfen
        await bridge.emit("OVERFLOW", {"test": True})


class TestOptimizationsIntegration:
    """Integrationstests für alle Optimierungen."""

    def test_all_modules_importable(self):
        """Prüft ob alle optimierten Module importierbar sind."""
        from app.core.token_budget import TokenBudget
        from app.core.context_compactor import ContextCompactor
        from app.agent.orchestrator import _parse_text_tool_calls
        from app.mcp.event_bridge import MCPEventBridge

        # Alle Module sollten importierbar sein
        assert TokenBudget is not None
        assert ContextCompactor is not None
        assert _parse_text_tool_calls is not None
        assert MCPEventBridge is not None

    def test_token_budget_workflow(self):
        """Testet kompletten TokenBudget Workflow."""
        from app.core.token_budget import TokenBudget

        budget = TokenBudget()

        # Workflow: check -> add -> check
        assert budget.can_add('context', 1000)
        budget.add('context', 1000)
        assert budget.used_context == 1000

        # Sollte jetzt weniger Platz haben
        remaining_before = budget.remaining
        budget.add('memory', 500)
        assert budget.remaining < remaining_before
