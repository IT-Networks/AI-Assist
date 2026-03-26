"""
E2E Tests: GitHub Operations.

Tests tool usage patterns for GitHub PR and repository interactions.
"""

import pytest
import pytest_asyncio

from .framework import (
    AIAssistClient,
    ToolAssertions,
    ToolCallTracker,
)


@pytest.mark.requires_github
@pytest.mark.asyncio
class TestPROperations:
    """Tests for GitHub Pull Request operations."""

    async def test_get_pr_details(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test fetching PR details."""
        response = await ai_client.chat_sync(
            message="Get the details of the most recent PR in the AI-Assist repository",
        )

        tools = tool_tracker.extract_from_events(response.events)

        # Should use get_pr or list_prs
        tool_names = [t.name for t in tools]
        assert "get_pr" in tool_names or "list_prs" in tool_names, \
            f"Expected get_pr or list_prs, got: {tool_names}"

    async def test_list_open_prs(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test listing open pull requests."""
        response = await ai_client.chat_sync(
            message="Show me all open pull requests in this repository",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["list_prs"],
        )

    async def test_get_pr_diff(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test getting PR diff/changes."""
        response = await ai_client.chat_sync(
            message="Show me the code changes in the most recent PR",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should use get_pr_diff or get_pr
        assert "get_pr_diff" in tool_names or "get_pr" in tool_names, \
            f"Expected PR diff tool, got: {tool_names}"


@pytest.mark.requires_github
@pytest.mark.asyncio
class TestRepositoryOperations:
    """Tests for repository information operations."""

    async def test_get_repo_info(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test getting repository information."""
        response = await ai_client.chat_sync(
            message="What is the description of this repository?",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["get_repo_info"],
        )

    async def test_list_branches(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test listing repository branches."""
        response = await ai_client.chat_sync(
            message="List all branches in this repository",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["list_branches"],
        )

        ToolAssertions.assert_response_contains(
            response=response.final_response,
            expected=["main"],
        )


@pytest.mark.requires_github
@pytest.mark.asyncio
class TestIssueOperations:
    """Tests for GitHub issue operations."""

    async def test_list_issues(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test listing repository issues."""
        response = await ai_client.chat_sync(
            message="Show me the open issues in this repository",
        )

        tools = tool_tracker.extract_from_events(response.events)

        ToolAssertions.assert_tools_called(
            actual=tools,
            expected=["list_issues"],
        )


@pytest.mark.requires_github
@pytest.mark.multi_tool
@pytest.mark.asyncio
class TestCombinedGitHubOperations:
    """Tests for combined GitHub and code operations."""

    async def test_pr_with_code_context(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test getting PR with related code context."""
        response = await ai_client.chat_sync(
            message="Get the most recent PR and show me the main file that was changed",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should use PR tool and potentially read_file
        assert "get_pr" in tool_names or "list_prs" in tool_names, \
            "Should fetch PR information"

    async def test_pr_code_search(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test searching code in PR context."""
        response = await ai_client.chat_sync(
            message="In the most recent PR, find any new functions that were added",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should use multiple tools
        assert len(tools) >= 1, "Should use at least one tool"

    async def test_analyze_pr_impact(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
    ):
        """Test analyzing PR impact on codebase."""
        response = await ai_client.chat_sync(
            message="Analyze the most recent PR - what files were changed and how?",
        )

        tools = tool_tracker.extract_from_events(response.events)

        # Should use PR-related tools
        assert len(tools) >= 1, "Should use tools to analyze PR"

        # Verify all tools succeeded
        ToolAssertions.assert_all_tools_successful(
            actual=tools,
            msg="All PR analysis tools should succeed",
        )
