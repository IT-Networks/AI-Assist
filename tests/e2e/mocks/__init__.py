"""
E2E Test Mocks.

Provides mock services for external integrations:
- JiraMock: Simulates Jira API
- GitHubMock: Simulates GitHub API
- WorkspaceMock: Manages test workspace with reset capability
"""

from .jira_mock import JiraMock, JiraIssue, JiraProject
from .workspace_manager import WorkspaceManager, WorkspaceState

__all__ = [
    "JiraMock",
    "JiraIssue",
    "JiraProject",
    "WorkspaceManager",
    "WorkspaceState",
]
