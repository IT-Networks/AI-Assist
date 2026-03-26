"""
E2E Tests: Local File Operations.

Tests tool usage patterns for file reading, writing, and searching.
"""

import pytest
import pytest_asyncio
from pathlib import Path

from .framework import (
    AIAssistClient,
    ToolAssertions,
    ToolCallTracker,
)


@pytest.mark.local_files
@pytest.mark.asyncio
class TestFileReadOperations:
    """Tests for file reading operations."""

    async def test_read_single_python_file(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test reading a single Python file and extracting information."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"Read the file {sample_files['example.py']} and tell me what functions are defined",
        )

        tools = tool_tracker.extract_from_events(response.events)

        # Verify read_file was called
        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["read_file"],
        )

        # Verify response contains function names
        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["greet", "add"],
        )

    async def test_read_json_config(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test reading and parsing JSON configuration."""
        response = await ai_client.chat_sync(
            message=f"What is the project name and version in {sample_files['config.json']}?",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["read_file"],
        )

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["test-project", "1.0.0"],
        )

    async def test_read_nonexistent_file(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        workspace: Path,
    ):
        """Test handling of non-existent file request."""
        nonexistent = workspace / "does_not_exist.py"

        response = await ai_client.chat_sync(
            message=f"Read the file {nonexistent} and summarize it",
        )

        tools = tool_tracker.extract_from_events(response.events)

        # Should attempt to read
        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["read_file"],
        )

        # Response should indicate file not found
        assert "not found" in response.final_response.lower() or "error" in response.final_response.lower()


@pytest.mark.local_files
@pytest.mark.asyncio
class TestFileSearchOperations:
    """Tests for code search operations."""

    async def test_search_function_definition(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test searching for function definition."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"In directory {workspace}, find where the function 'multiply' is defined",
        )

        tools = tool_tracker.extract_from_events(response.events)

        # Should use search_code
        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["search_code"],
        )

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["Calculator", "multiply"],
        )

    async def test_list_python_files(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test listing Python files in directory."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"List all Python files in {workspace}",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["list_files"],
        )

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=[".py"],
        )


@pytest.mark.local_files
@pytest.mark.asyncio
class TestFileWriteOperations:
    """Tests for file writing operations."""

    async def test_create_new_file(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        workspace: Path,
    ):
        """Test creating a new file with content."""
        response = await ai_client.chat_sync(
            message=f"Create a new file at {workspace / 'output.txt'} with content 'Hello E2E'",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["write_file"],
        )

        # Verify file was created
        output_file = workspace / "output.txt"
        assert output_file.exists(), "File should have been created"

    async def test_edit_existing_file(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test modifying an existing file."""
        response = await ai_client.chat_sync(
            message=f"Add a function called 'subtract' to {sample_files['example.py']} that subtracts two numbers",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should read first, then edit
        assert "read_file" in tool_names, "Should read file first"
        assert "edit_file" in tool_names or "write_file" in tool_names, "Should modify file"

        # Verify order if both present
        if "read_file" in tool_names and "edit_file" in tool_names:
            ToolAssertions.assert_tools_called(
                actual=tools,
                expected=["read_file", "edit_file"],
                strict_order=True,
            )


@pytest.mark.local_files
@pytest.mark.asyncio
class TestMultiFileOperations:
    """Tests for operations spanning multiple files."""

    async def test_compare_multiple_files(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test reading and comparing multiple files."""
        response = await ai_client.chat_sync(
            message=f"Compare the imports in {sample_files['src/main.py']} with the functions in {sample_files['example.py']}",
        )

        tools = tool_tracker.extract_from_events(response.events)
        read_calls = [t for t in tools if t.name == "read_file"]

        # Should read multiple files
        assert len(read_calls) >= 2, "Should read at least 2 files"

    async def test_search_across_files(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        sample_files: dict,
    ):
        """Test searching pattern across multiple files."""
        workspace = sample_files["example.py"].parent

        response = await ai_client.chat_sync(
            message=f"Find all files in {workspace} that contain the word 'def'",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["search_code"],
        )
