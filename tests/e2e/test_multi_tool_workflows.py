"""
E2E Tests: Multi-Tool Workflows.

Tests complex scenarios requiring multiple tools in sequence.
"""

import pytest
import pytest_asyncio
from pathlib import Path

from .framework import (
    AIAssistClient,
    ToolAssertions,
    ToolCallTracker,
)


@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestCodeUnderstandingWorkflows:
    """Tests for code understanding workflows."""

    async def test_codebase_overview(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test listing and analyzing codebase structure."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"Give me an overview of the project in {workspace} - list files and explain what the main modules do",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should list files first, then read
        assert "list_files" in tool_names, "Should list files"
        assert "read_file" in tool_names, "Should read files for understanding"

        # Verify order
        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["list_files", "read_file"],
            strict_order=True,
        )

    async def test_find_and_explain_function(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test finding and explaining a function."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"In {workspace}, find the 'greet' function and explain how it works",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should search then read
        assert "search_code" in tool_names or "read_file" in tool_names, \
            "Should search or read to find function"

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["greet", "name"],
        )


@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestCodeModificationWorkflows:
    """Tests for code modification workflows."""

    async def test_read_modify_verify(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test read-modify-verify workflow."""
        response = await ai_client.chat_sync(
            message=f"Add type hints to the 'add' function in {sample_files['example.py']} and show me the result",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Must read first
        assert "read_file" in tool_names, "Should read file first"

        # Then edit
        assert "edit_file" in tool_names or "write_file" in tool_names, \
            "Should modify file"

        # Verify read comes before edit
        read_idx = next(i for i, t in enumerate(tools) if t.name == "read_file")
        edit_idx = next(
            (i for i, t in enumerate(tools) if t.name in ["edit_file", "write_file"]),
            len(tools)
        )
        assert read_idx < edit_idx, "Should read before modifying"

    async def test_create_test_file(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
        workspace: Path,
    ):
        """Test creating test file for existing code."""
        response = await ai_client.chat_sync(
            message=f"Read {sample_files['example.py']} and create a test file at {workspace / 'test_example.py'}",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should read source, then write test
        assert "read_file" in tool_names, "Should read source file"
        assert "write_file" in tool_names, "Should write test file"

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["read_file", "write_file"],
            strict_order=True,
        )


@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestSearchAnalysisWorkflows:
    """Tests for search and analysis workflows."""

    async def test_find_all_function_usages(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test finding function definition and all usages."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"In {workspace}, find where 'greet' is defined and all places it's called",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["search_code"],
        )

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["greet"],
        )

    async def test_dependency_analysis(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test analyzing imports and dependencies."""
        response = await ai_client.chat_sync(
            message=f"Show me the imports in {sample_files['src/main.py']} and verify those modules exist",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        assert "read_file" in tool_names, "Should read main.py"

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["import"],
        )


@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestRefactoringWorkflows:
    """Tests for refactoring workflows."""

    async def test_rename_function(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test renaming a function across files."""
        response = await ai_client.chat_sync(
            message=f"Rename 'add' to 'sum_numbers' in {sample_files['example.py']}",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should search/read, then edit
        assert "read_file" in tool_names or "search_code" in tool_names, \
            "Should find function first"
        assert "edit_file" in tool_names or "write_file" in tool_names, \
            "Should modify file"

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["sum_numbers"],
        )


@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestComplexWorkflows:
    """Tests for complex multi-step workflows."""

    @pytest.mark.slow
    async def test_full_feature_implementation(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test implementing a new feature end-to-end."""
        response = await ai_client.chat_sync(
            message=f"Add a 'divide' function to {sample_files['example.py']} that handles division by zero gracefully",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Must read context first
        assert "read_file" in tool_names, "Should read existing code"

        # Then implement
        assert "edit_file" in tool_names or "write_file" in tool_names, \
            "Should implement the function"

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["divide"],
        )

    async def test_code_review_workflow(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test code review workflow."""
        response = await ai_client.chat_sync(
            message=f"Review the code in {sample_files['example.py']} and suggest improvements",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["read_file"],
        )

        # All tools should succeed
        ToolAssertions.assert_all_tools_successful(
            actual=tools,
            msg="Review should complete without errors",
        )


@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestToolSequenceVerification:
    """Tests that verify specific tool call sequences."""

    async def test_list_read_sequence(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Verify list_files before read_file sequence."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"What Python files are in {workspace} and what does each one do?",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Verify list_files comes before read_file
        if "list_files" in tool_names and "read_file" in tool_names:
            list_idx = tool_names.index("list_files")
            read_idx = tool_names.index("read_file")
            assert list_idx < read_idx, "Should list files before reading"

    async def test_search_read_sequence(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Verify search before detailed read sequence."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"Find any class definitions in {workspace} and explain their methods",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should search or list first, then read for details
        assert len(tools) >= 1, "Should use tools to answer"

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["Calculator"],
        )

    async def test_tool_count_bounds(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Verify tool call count is within reasonable bounds."""
        response = await ai_client.chat_sync(
            message=f"Count the number of functions in {sample_files['example.py']}",
        )

        tools = tool_tracker.extract_from_events(response.events)

        # Should use at least 1 tool, but not more than 5 for simple task
        ToolAssertions.assert_tool_count(
            actual=tools,
            expected_min=1,
            expected_max=5,
            msg="Simple task should use 1-5 tools",
        )
