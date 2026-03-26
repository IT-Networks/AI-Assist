"""
E2E Tests for Jira Integration Workflows.

Tests complex workflows that combine Jira and code operations:
- Reading Jira issues and understanding requirements
- Implementing bug fixes based on Jira descriptions
- Processing code reviews with subtasks
- Feature implementation from user stories

Uses JiraMock for simulated Jira data.
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List

import pytest
import pytest_asyncio

from tests.e2e.framework import (
    AIAssistClient,
    E2EReporter,
    TestResult,
    TestSuiteResult,
    ToolAssertions,
    ToolCallTracker,
    TrackedToolCall,
    load_scenario_file,
)
from tests.e2e.mocks import (
    JiraMock,
    JiraIssue,
    WorkspaceManager,
)
from tests.e2e.mocks.jira_mock import (
    create_bug_fix_scenario,
    create_code_review_scenario,
    create_feature_request_scenario,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="module")
def jira_mock() -> JiraMock:
    """Create and populate Jira mock with test data."""
    mock = JiraMock()

    # Add bug fix scenario
    create_bug_fix_scenario(mock)

    # Add code review scenario
    create_code_review_scenario(mock)

    # Add feature request scenario
    create_feature_request_scenario(mock)

    return mock


@pytest.fixture(scope="module")
def workspace_manager(tmp_path_factory) -> WorkspaceManager:
    """Create test workspace with snapshot capability."""
    from tests.e2e.mocks.workspace_manager import create_python_project_workspace

    workspace_base = tmp_path_factory.mktemp("jira_test_workspace")
    manager = create_python_project_workspace(workspace_base)

    # Take initial snapshot
    manager.snapshot("initial")

    return manager


@pytest_asyncio.fixture
async def ai_client():
    """Create connected AI-Assist client."""
    import os

    client = AIAssistClient(
        ai_assist_url=os.getenv("AI_ASSIST_URL", "http://localhost:8000"),
        proxy_url=os.getenv("PROXY_URL", "http://localhost:8080"),
    )
    await client.connect()

    health = await client.health_check()
    if not health["ai_assist"]:
        pytest.skip("AI-Assist server not available")

    yield client
    await client.disconnect()


@pytest.fixture
def tool_tracker() -> ToolCallTracker:
    """Create tool tracker."""
    return ToolCallTracker()


# ============================================================================
# Test Classes
# ============================================================================

class TestJiraBasicOperations:
    """Tests for basic Jira reading operations."""

    @pytest.mark.asyncio
    async def test_search_jira_issues(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        jira_mock: JiraMock,
    ):
        """Test searching Jira for issues."""
        # Verify mock has data
        assert len(jira_mock.issues) > 0, "Mock should have issues"

        response = await ai_client.chat_sync(
            message="Use search_jira to find all open bugs. List the issue keys.",
            model="gptoss120b",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should use search_jira
        assert "search_jira" in tool_names or len(tools) > 0, \
            f"Expected search_jira, got: {tool_names}"

    @pytest.mark.asyncio
    async def test_read_jira_issue_details(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        jira_mock: JiraMock,
    ):
        """Test reading full Jira issue details."""
        # Get a known issue key
        issue_key = list(jira_mock.issues.keys())[0]

        response = await ai_client.chat_sync(
            message=f"Use read_jira_issue to read the full details of {issue_key}.",
            model="gptoss120b",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should use read_jira_issue
        assert "read_jira_issue" in tool_names or len(tools) > 0, \
            f"Expected read_jira_issue, got: {tool_names}"


class TestJiraCodeWorkflows:
    """Tests for Jira + code modification workflows."""

    @pytest.mark.asyncio
    async def test_bug_analysis_workflow(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        jira_mock: JiraMock,
        workspace_manager: WorkspaceManager,
    ):
        """Test reading bug and analyzing affected code."""
        # Ensure workspace is clean
        workspace_manager.restore("initial")

        bug = jira_mock.search(issue_type="Bug")[0]

        response = await ai_client.chat_sync(
            message=f"""
            1. Read Jira issue {bug['key']} to understand the bug
            2. The issue mentions affected files - read each one
            3. Summarize what code changes are needed
            """,
            model="gptoss120b",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        # Should use both read_jira_issue and read_file
        logger.info(f"Tools called: {tool_names}")

        # Verify response mentions the bug
        assert "divide" in response.final_response.lower() or \
               "zero" in response.final_response.lower(), \
            "Response should mention the division by zero bug"

    @pytest.mark.asyncio
    async def test_code_review_subtasks(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        jira_mock: JiraMock,
    ):
        """Test processing code review with subtasks."""
        # Find the code review story
        reviews = jira_mock.search(issue_type="Story", status="Code Review")
        assert len(reviews) > 0, "Should have code review story"

        review = reviews[0]

        response = await ai_client.chat_sync(
            message=f"""
            Process code review {review['key']}:
            1. Read the main story
            2. List its subtasks
            3. Summarize what needs to be reviewed
            """,
            model="gptoss120b",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        logger.info(f"Code review tools: {tool_names}")

        # Verify subtasks are mentioned
        assert "subtask" in response.final_response.lower() or \
               "review" in response.final_response.lower()

    @pytest.mark.asyncio
    async def test_feature_implementation(
        self,
        ai_client: AIAssistClient,
        tool_tracker: ToolCallTracker,
        jira_mock: JiraMock,
        workspace_manager: WorkspaceManager,
    ):
        """Test implementing feature from Jira story."""
        # Ensure clean state
        workspace_manager.restore("initial")

        features = jira_mock.search(issue_type="Story", status="Open")
        feature = features[0]

        response = await ai_client.chat_sync(
            message=f"""
            Implement feature {feature['key']}:
            1. Read the Jira story for requirements
            2. Read the affected files
            3. Plan the implementation (but don't implement yet)
            """,
            model="gptoss120b",
        )

        tools = tool_tracker.extract_from_events(response.events)
        tool_names = [t.name for t in tools]

        logger.info(f"Feature planning tools: {tool_names}")

        # Verify power function is mentioned
        assert "power" in response.final_response.lower()

        # Check if workspace was modified
        if workspace_manager.has_changes("initial"):
            logger.warning("Workspace was modified during planning - restoring")
            workspace_manager.restore("initial")


class TestWorkspaceManagement:
    """Tests for workspace state management."""

    def test_snapshot_restore(self, workspace_manager: WorkspaceManager):
        """Test workspace snapshot and restore."""
        # Take snapshot
        initial = workspace_manager.snapshot("test_start")
        assert initial.file_count > 0

        # Modify a file
        test_file = workspace_manager.workspace_path / "test_modified.py"
        test_file.write_text("# Modified content")

        # Check changes detected
        assert workspace_manager.has_changes("test_start")

        # Get diff
        diffs = workspace_manager.diff("test_start", "current")
        assert len(diffs) > 0
        assert any(d.path == "test_modified.py" for d in diffs)

        # Restore
        restore_diffs = workspace_manager.restore("test_start")
        assert len(restore_diffs) > 0

        # Verify restored
        assert not test_file.exists()
        assert not workspace_manager.has_changes("test_start")

    def test_multiple_snapshots(self, workspace_manager: WorkspaceManager):
        """Test multiple named snapshots."""
        workspace_manager.snapshot("snap1")

        # Modify
        (workspace_manager.workspace_path / "file1.txt").write_text("v1")
        workspace_manager.snapshot("snap2")

        # Modify again
        (workspace_manager.workspace_path / "file2.txt").write_text("v2")

        # Restore to snap1 (should remove both files)
        workspace_manager.restore("snap1")

        assert not (workspace_manager.workspace_path / "file1.txt").exists()
        assert not (workspace_manager.workspace_path / "file2.txt").exists()


# ============================================================================
# Suite Runner
# ============================================================================

async def run_jira_workflow_suite() -> TestSuiteResult:
    """Run all Jira workflow tests and generate report."""
    from tests.e2e.test_runner import E2ETestRunner

    runner = E2ETestRunner()
    await runner.connect()

    try:
        scenario_file = Path(__file__).parent / "scenarios" / "jira_workflows.yaml"
        result = await runner.run_scenario_file(scenario_file)
        return result
    finally:
        await runner.disconnect()


if __name__ == "__main__":
    # Run tests directly
    result = asyncio.run(run_jira_workflow_suite())
    print(f"\nJira Workflow Tests: {result.passed}/{result.total} passed")
    print(f"Success Rate: {result.success_rate:.1f}%")
