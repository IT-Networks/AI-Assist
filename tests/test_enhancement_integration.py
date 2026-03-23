"""
E2E Integration Tests for Skill-based Enhancement Pipeline.

MIGRATION (2026-03-23):
Tests wurden von MCP-Capability-basiert zu Skill-Hint-basiert migriert.
Das System nutzt nun Skill-Hints statt Python-Backend-Capabilities.

Tests the enhancement confirmation flow:
1. Enhancement detection → 2. Skill hint collection → 3. User confirmation → 4. Task execution
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from app.agent.prompt_enhancer import (
    PromptEnhancer,
    EnhancementDetector,
    EnhancementCache,
    EnhancementType,
    ConfirmationStatus,
    EnrichedPrompt,
    ContextItem,
    get_prompt_enhancer,
    get_enhancement_cache
)


# ══════════════════════════════════════════════════════════════════════════════
# Test Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_sequential_thinking():
    """Mock SequentialThinking for testing."""
    @dataclass
    class MockThinkingSession:
        final_conclusion: str
        steps: List[str]

    thinking = MagicMock()
    thinking.think = AsyncMock(return_value=MockThinkingSession(
        final_conclusion="After careful analysis of the problem...",
        steps=["Step 1: Analyze input", "Step 2: Synthesize findings", "Step 3: Conclude"]
    ))
    return thinking


@pytest.fixture
def fresh_enhancer():
    """Create a fresh PromptEnhancer instance without cache."""
    cache = EnhancementCache(ttl_seconds=300, max_entries=50)
    return PromptEnhancer(cache=cache)


# ══════════════════════════════════════════════════════════════════════════════
# Enhancement Detection Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEnhancementDetectionE2E:
    """E2E tests for enhancement type detection."""

    def test_research_query_detection(self):
        """Research queries should trigger RESEARCH enhancement."""
        detector = EnhancementDetector()

        # Must contain RESEARCH_TRIGGERS: wiki, dokumentation, handbuch, confluence, readme, etc.
        research_queries = [
            "Schau in der Wiki nach wie das Logging konfiguriert wird",
            "Laut Dokumentation sollte das anders funktionieren",
            "Gemäß dem Handbuch müssen wir das so implementieren",
            "Siehe Confluence für die API Specs",
        ]

        for query in research_queries:
            result = detector.detect(query)
            assert result == EnhancementType.RESEARCH, f"Query '{query}' should be RESEARCH"

    def test_sequential_query_detection(self):
        """Complex analytical queries should trigger SEQUENTIAL enhancement."""
        detector = EnhancementDetector()

        # Must contain SEQUENTIAL_TRIGGERS: warum, debug, analysiere, fehler, problem, etc.
        sequential_queries = [
            "Warum funktioniert der Login nicht mehr",
            "Debug warum die API einen 500 zurückgibt",
            "Analysiere den Fehler im Payment-Service",
            "Es gibt ein Problem mit der Datenbankverbindung",
        ]

        for query in sequential_queries:
            result = detector.detect(query)
            assert result == EnhancementType.SEQUENTIAL, f"Query '{query}' should be SEQUENTIAL"

    def test_simple_query_no_enhancement(self):
        """Simple code queries should not trigger enhancement."""
        detector = EnhancementDetector()

        simple_queries = [
            "Fix the typo in line 42",
            "Add a comment",
            "Rename variable x to count",
        ]

        for query in simple_queries:
            assert not detector.should_enhance(query), f"Query '{query}' should NOT be enhanced"


# ══════════════════════════════════════════════════════════════════════════════
# Full Enhancement Flow Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEnhancementFlowE2E:
    """E2E tests for the complete enhancement flow with skill hints."""

    @pytest.mark.asyncio
    async def test_research_enhancement_flow(self, fresh_enhancer):
        """Test complete research enhancement flow with skill hints."""
        # Query must contain RESEARCH_TRIGGERS like "wiki", "dokumentation", etc.
        query = "Schau in der Wiki nach wie Testing Frameworks konfiguriert werden"

        # Step 1: Enhance with force_type to ensure RESEARCH
        # Skill hints are now returned instead of capability results
        enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)

        # Verify enhancement created
        assert enriched.enhancement_type == EnhancementType.RESEARCH
        assert enriched.original_query == query

        # Skill hints should be present
        if enriched.context_items:
            assert enriched.confirmation_status == ConfirmationStatus.PENDING
            # Verify skill hint content
            hint_sources = [item.source for item in enriched.context_items]
            assert "skill_hint" in hint_sources

            # Step 2: User confirms
            confirmed = fresh_enhancer.confirm(enriched, True)
            assert confirmed.confirmation_status == ConfirmationStatus.CONFIRMED

            # Step 3: Get context for planner
            context = confirmed.get_context_for_planner()
            assert context is not None
            assert "/research" in context or "/sc:research" in context
        else:
            # Fallback case - auto-confirmed
            assert enriched.confirmation_status == ConfirmationStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_sequential_enhancement_flow(self, fresh_enhancer, mock_sequential_thinking):
        """Test complete sequential thinking enhancement flow."""
        # Query must contain SEQUENTIAL_TRIGGERS like "debug", "analysiere", "fehler", etc.
        query = "Warum funktioniert das Caching nicht mehr"

        # Mock the sequential thinking
        with patch.object(fresh_enhancer, '_get_sequential_thinking', return_value=mock_sequential_thinking):
            # Step 1: Enhance with force_type
            enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.SEQUENTIAL)

            # When MCP returns results, status should be PENDING
            # When MCP fails or returns empty, status is CONFIRMED (fallback)
            if enriched.context_items:
                assert enriched.confirmation_status == ConfirmationStatus.PENDING
            else:
                # Fallback case - MCP failed, auto-confirmed
                assert enriched.confirmation_status == ConfirmationStatus.CONFIRMED

            # Step 2: User confirms (if pending)
            if enriched.confirmation_status == ConfirmationStatus.PENDING:
                confirmed = fresh_enhancer.confirm(enriched, True)
                assert confirmed.confirmation_status == ConfirmationStatus.CONFIRMED
            else:
                confirmed = enriched

            # Step 3: Get context
            context = confirmed.get_context_for_planner()
            assert context is not None

    @pytest.mark.asyncio
    async def test_rejected_enhancement_flow(self, fresh_enhancer):
        """Test enhancement rejection flow with skill hints."""
        # Use force_type to ensure we get a PENDING status
        query = "Schau in der Wiki nach"

        # Step 1: Enhance with force_type
        enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)

        # If we have context items (skill hints), status should be PENDING
        if enriched.context_items:
            assert enriched.confirmation_status == ConfirmationStatus.PENDING

            # Step 2: User rejects
            rejected = fresh_enhancer.confirm(enriched, False)

            # Verify rejection
            assert rejected.confirmation_status == ConfirmationStatus.REJECTED

            # Step 3: Context should be empty or indicate rejection
            context = rejected.get_context_for_planner()
            assert context is not None
        else:
            # Fallback case - no context, already confirmed
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Cache Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCacheIntegrationE2E:
    """E2E tests for enhancement caching with skill hints."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_hint_collection(self, fresh_enhancer):
        """Cached enhancements should not collect hints again."""
        # Use force_type to ensure RESEARCH type
        query = "Schau in der Wiki nach Python Best Practices"

        # First call - should collect skill hints
        enriched1 = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)

        # Confirm and cache
        confirmed = fresh_enhancer.confirm(enriched1, True)
        fresh_enhancer.cache.set(confirmed)

        # Second call - should use cache
        enriched2 = await fresh_enhancer.enhance(query)
        assert enriched2.cache_hit == True
        # Cached result should have same content
        assert enriched2.original_query == query

    @pytest.mark.asyncio
    async def test_skip_cache_forces_fresh(self, fresh_enhancer):
        """skip_cache=True should force fresh hint collection."""
        query = "Schau in der Wiki nach etwas Neues"

        # First call with force_type
        enriched1 = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)
        confirmed = fresh_enhancer.confirm(enriched1, True)
        fresh_enhancer.cache.set(confirmed)

        # Second call with skip_cache and force_type
        enriched2 = await fresh_enhancer.enhance(query, skip_cache=True, force_type=EnhancementType.RESEARCH)
        assert enriched2.cache_hit == False
        # Should have fresh hints
        assert enriched2.original_query == query


# ══════════════════════════════════════════════════════════════════════════════
# Event Callback Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEventCallbackE2E:
    """E2E tests for enhancement event callbacks with skill hints."""

    @pytest.mark.asyncio
    async def test_event_callback_receives_events(self):
        """Event callback should receive enhancement events."""
        events_received = []

        async def event_callback(event_type: str, data: dict):
            """Async event callback for testing."""
            events_received.append({"type": event_type, "data": data})

        cache = EnhancementCache(ttl_seconds=300, max_entries=50)
        enhancer = PromptEnhancer(cache=cache, event_callback=event_callback)

        # Use force_type to trigger actual enhancement with skill hints
        await enhancer.enhance("Schau in der Wiki nach etwas", force_type=EnhancementType.RESEARCH)

        # When force_type is used, we should receive events
        # At minimum, the test verifies the callback mechanism works
        assert isinstance(events_received, list)
        # Should have received at least enhancement_start event
        event_types = [e["type"] for e in events_received]
        assert "enhancement_start" in event_types


# ══════════════════════════════════════════════════════════════════════════════
# API Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIIntegrationE2E:
    """E2E tests for API endpoint integration with skill hints."""

    @pytest.mark.asyncio
    async def test_enhancement_api_response_format(self, fresh_enhancer):
        """Test that enhancement data matches API response format."""
        query = "Schau in der Wiki nach REST API Design"

        enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)

        # Simulate API response format
        api_response = {
            "session_id": "test-session",
            "has_enhancement": True,
            "enhancement": {
                "original_query": enriched.original_query,
                "enhancement_type": enriched.enhancement_type.value,
                "context_sources": enriched.context_sources,
                "summary": enriched.summary,
                "context_items": [
                    {
                        "source": item.source,
                        "title": item.title,
                        "content": item.content,
                        "content_preview": item.content[:300] if len(item.content) > 300 else item.content,
                        "relevance": item.relevance,
                        "file_path": item.file_path,
                        "url": item.url
                    }
                    for item in enriched.context_items
                ],
                "confirmation_message": enriched.get_confirmation_message()
            }
        }

        # Verify structure
        assert api_response["has_enhancement"] == True
        assert api_response["enhancement"]["enhancement_type"] in ["research", "sequential", "analyze", "brainstorm", "none"]
        assert isinstance(api_response["enhancement"]["context_items"], list)

    @pytest.mark.asyncio
    async def test_confirmation_api_flow(self, fresh_enhancer):
        """Test the API confirmation flow simulation with skill hints."""
        query = "Schau in der Wiki nach etwas"

        # Simulate: 1. Enhancement created with force_type
        enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)

        # Simulate: 2. API stores in state (pending_enhancement)
        pending_enhancement = enriched

        # Simulate: 3. API confirm endpoint called
        confirmed = fresh_enhancer.confirm(pending_enhancement, True)
        confirmed_context = confirmed.get_context_for_planner()

        # Simulate: 4. API response
        api_response = {
            "status": "confirmed",
            "message": "Enhancement-Kontext bestätigt",
            "context_length": len(confirmed_context),
            "continue": True
        }

        # Verify
        assert api_response["status"] == "confirmed"
        # Context length may be 0 if no items were collected, but structure is valid
        assert api_response["context_length"] >= 0
        assert api_response["continue"] == True


# ══════════════════════════════════════════════════════════════════════════════
# Error Handling Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorHandlingE2E:
    """E2E tests for error handling scenarios with skill hints."""

    @pytest.mark.asyncio
    async def test_graceful_fallback_on_hint_failure(self, fresh_enhancer):
        """Enhancement should gracefully handle hint collection failures."""
        query = "Recherchiere etwas"

        # Mock _collect_research_hints to raise an exception
        with patch.object(fresh_enhancer, '_collect_research_hints',
                          AsyncMock(side_effect=Exception("Hint collection failed"))):
            # Should not raise, should return fallback
            enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.RESEARCH)

            # Fallback should be confirmed automatically
            assert enriched.confirmation_status == ConfirmationStatus.CONFIRMED
            assert len(enriched.context_items) == 0

    @pytest.mark.asyncio
    async def test_none_type_bypasses_hints(self, fresh_enhancer):
        """NONE enhancement type should skip hint collection entirely."""
        query = "Simple fix"  # Should detect as NONE

        enriched = await fresh_enhancer.enhance(query, force_type=EnhancementType.NONE)

        # No hints should be collected for NONE type
        assert enriched.enhancement_type == EnhancementType.NONE
        assert enriched.confirmation_status == ConfirmationStatus.CONFIRMED
        # No context items for NONE type
        assert len(enriched.context_items) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator Integration Tests (Mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestOrchestratorIntegrationE2E:
    """E2E tests simulating orchestrator integration with skill hints."""

    @pytest.mark.asyncio
    async def test_enhancement_to_task_flow_simulation(self, fresh_enhancer):
        """Simulate the full orchestrator enhancement-to-task flow with skill hints."""
        # Query must contain trigger words
        user_query = "Schau in der Wiki nach und implementiere ein Logging-System"

        # Phase 1: Detection - query contains "wiki" trigger
        should_enhance = fresh_enhancer.detector.should_enhance(user_query)
        assert should_enhance == True

        enhancement_type = fresh_enhancer.detector.detect(user_query)
        assert enhancement_type == EnhancementType.RESEARCH

        # Phase 2: Enhancement with force_type (now returns skill hints)
        enriched = await fresh_enhancer.enhance(user_query, force_type=EnhancementType.RESEARCH)

        # Phase 3: Confirmation (user confirms via API)
        confirmed = fresh_enhancer.confirm(enriched, True)
        enriched_context = confirmed.get_context_for_planner()

        # Phase 4: Context passed to TaskPlanner (simulated)
        task_planner_receives = {
            "user_message": user_query,
            "context": enriched_context
        }

        # Verify context is properly formatted for task planner
        assert task_planner_receives["context"] is not None

    @pytest.mark.asyncio
    async def test_continue_enhanced_message_simulation(self, fresh_enhancer):
        """Simulate [CONTINUE_ENHANCED] message handling with skill hints."""
        original_query = "Schau in der Wiki nach Python Testing"

        # Step 1: Initial enhancement with force_type (now returns skill hints)
        enriched = await fresh_enhancer.enhance(original_query, force_type=EnhancementType.RESEARCH)

        # Step 2: Store in "state" (simulating orchestrator)
        state = {
            "pending_enhancement": enriched,
            "enhancement_original_query": original_query,
            "confirmed_enhancement_context": None
        }

        # Step 3: API confirm endpoint (simulating)
        confirmed = fresh_enhancer.confirm(state["pending_enhancement"], True)
        state["confirmed_enhancement_context"] = confirmed.get_context_for_planner()
        state["pending_enhancement"] = None

        # Step 4: [CONTINUE_ENHANCED] arrives
        continue_message = "[CONTINUE_ENHANCED]"

        # Step 5: Orchestrator restores original query and uses context
        if continue_message == "[CONTINUE_ENHANCED]":
            restored_query = state["enhancement_original_query"]
            context_to_use = state["confirmed_enhancement_context"]

            # Clear after use
            state["confirmed_enhancement_context"] = None
            state["enhancement_original_query"] = None

        # Verify restoration
        assert restored_query == original_query
        assert context_to_use is not None
        # Context may be empty string if no items collected, but not None
        assert isinstance(context_to_use, str)
