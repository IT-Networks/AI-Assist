"""
Jira Mock Service for E2E Testing.

Simulates Jira API responses for testing tool behavior with:
- Issues (Bugs, Stories, Tasks, Subtasks)
- Projects
- Comments
- Transitions

Usage:
    mock = JiraMock()
    mock.add_project("TEST", "Test Project")
    mock.add_issue(JiraIssue(
        key="TEST-1",
        summary="Fix login bug",
        issue_type="Bug",
        status="Open",
        description="Login fails with special characters"
    ))
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum


class IssueType(str, Enum):
    BUG = "Bug"
    STORY = "Story"
    TASK = "Task"
    SUBTASK = "Sub-task"
    EPIC = "Epic"


class IssueStatus(str, Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    CODE_REVIEW = "Code Review"
    TESTING = "Testing"
    DONE = "Done"
    CLOSED = "Closed"


class Priority(str, Enum):
    BLOCKER = "Blocker"
    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"
    TRIVIAL = "Trivial"


@dataclass
class JiraComment:
    """A Jira comment."""
    id: str
    author: str
    body: str
    created: datetime = field(default_factory=datetime.now)


@dataclass
class JiraIssue:
    """A Jira issue."""
    key: str
    summary: str
    issue_type: str = "Task"
    status: str = "Open"
    priority: str = "Major"
    description: str = ""
    assignee: str = "Unassigned"
    reporter: str = "testuser"
    labels: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    parent_key: Optional[str] = None
    subtasks: List[str] = field(default_factory=list)
    comments: List[JiraComment] = field(default_factory=list)
    created: datetime = field(default_factory=datetime.now)
    updated: datetime = field(default_factory=datetime.now)

    # Acceptance criteria and code pointers for testing
    acceptance_criteria: str = ""
    affected_files: List[str] = field(default_factory=list)

    def to_api_response(self) -> Dict:
        """Convert to Jira API response format."""
        return {
            "key": self.key,
            "summary": self.summary,
            "status": self.status,
            "priority": self.priority,
            "type": self.issue_type,
            "assignee": self.assignee,
            "reporter": self.reporter,
            "description": self.description,
            "labels": self.labels,
            "components": self.components,
            "parent_key": self.parent_key,
            "subtask_count": len(self.subtasks),
            "subtasks": self.subtasks,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
            "url": f"https://jira.example.com/browse/{self.key}",
            "acceptance_criteria": self.acceptance_criteria,
            "affected_files": self.affected_files,
        }

    def to_full_response(self) -> Dict:
        """Convert to full Jira API response with comments."""
        response = self.to_api_response()
        response["comments"] = [
            {
                "id": c.id,
                "author": c.author,
                "body": c.body,
                "created": c.created.isoformat(),
            }
            for c in self.comments
        ]
        return response


@dataclass
class JiraProject:
    """A Jira project."""
    key: str
    name: str
    description: str = ""
    lead: str = "projectlead"


class JiraMock:
    """
    Mock Jira service for E2E testing.

    Provides realistic Jira-like responses for testing
    tool behavior in complex workflows.
    """

    def __init__(self):
        self.projects: Dict[str, JiraProject] = {}
        self.issues: Dict[str, JiraIssue] = {}
        self._next_issue_num: Dict[str, int] = {}

    def reset(self) -> None:
        """Reset all mock data."""
        self.projects.clear()
        self.issues.clear()
        self._next_issue_num.clear()

    def add_project(self, key: str, name: str, description: str = "") -> JiraProject:
        """Add a project."""
        project = JiraProject(key=key, name=name, description=description)
        self.projects[key] = project
        self._next_issue_num[key] = 1
        return project

    def add_issue(self, issue: JiraIssue) -> JiraIssue:
        """Add an issue."""
        self.issues[issue.key] = issue
        return issue

    def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        **kwargs
    ) -> JiraIssue:
        """Create a new issue with auto-generated key."""
        if project_key not in self.projects:
            raise ValueError(f"Project {project_key} not found")

        num = self._next_issue_num.get(project_key, 1)
        self._next_issue_num[project_key] = num + 1

        key = f"{project_key}-{num}"
        issue = JiraIssue(
            key=key,
            summary=summary,
            issue_type=issue_type,
            **kwargs
        )
        self.issues[key] = issue
        return issue

    def add_subtask(
        self,
        parent_key: str,
        summary: str,
        **kwargs
    ) -> JiraIssue:
        """Add a subtask to an existing issue."""
        if parent_key not in self.issues:
            raise ValueError(f"Parent issue {parent_key} not found")

        parent = self.issues[parent_key]
        project_key = parent_key.split("-")[0]

        subtask = self.create_issue(
            project_key=project_key,
            summary=summary,
            issue_type="Sub-task",
            parent_key=parent_key,
            **kwargs
        )

        parent.subtasks.append(subtask.key)
        return subtask

    def add_comment(
        self,
        issue_key: str,
        body: str,
        author: str = "testuser"
    ) -> JiraComment:
        """Add a comment to an issue."""
        if issue_key not in self.issues:
            raise ValueError(f"Issue {issue_key} not found")

        comment = JiraComment(
            id=f"comment-{len(self.issues[issue_key].comments) + 1}",
            author=author,
            body=body,
        )
        self.issues[issue_key].comments.append(comment)
        self.issues[issue_key].updated = datetime.now()
        return comment

    def transition_issue(self, issue_key: str, new_status: str) -> None:
        """Transition an issue to a new status."""
        if issue_key not in self.issues:
            raise ValueError(f"Issue {issue_key} not found")

        self.issues[issue_key].status = new_status
        self.issues[issue_key].updated = datetime.now()

    def search(
        self,
        jql: str = "",
        project: str = "",
        status: str = "",
        issue_type: str = "",
        max_results: int = 50
    ) -> List[Dict]:
        """
        Search issues with JQL-like filtering.

        Supports simple filters:
        - project = XXX
        - status = "Open"
        - type = Bug
        """
        results = []

        for issue in self.issues.values():
            # Apply filters
            if project and not issue.key.startswith(f"{project}-"):
                continue
            if status and issue.status.lower() != status.lower():
                continue
            if issue_type and issue.issue_type.lower() != issue_type.lower():
                continue

            results.append(issue.to_api_response())

            if len(results) >= max_results:
                break

        return results

    def get_issue(self, issue_key: str) -> Optional[Dict]:
        """Get full issue details."""
        if issue_key not in self.issues:
            return None
        return self.issues[issue_key].to_full_response()


# ============================================================================
# Pre-built Test Scenarios
# ============================================================================

def create_code_review_scenario(mock: JiraMock) -> JiraIssue:
    """
    Create a code review scenario with subtasks.

    Returns the parent issue with:
    - Main code review task
    - Subtasks for specific review items
    - Affected files to check
    """
    mock.add_project("CR", "Code Review")

    parent = mock.create_issue(
        project_key="CR",
        summary="Code Review: Calculator refactoring",
        issue_type="Story",
        status="Code Review",
        priority="Major",
        description="""
# Code Review Required

## Changes
- Refactored Calculator class for better testability
- Added new MathOperations utility class
- Updated unit tests

## Files Changed
- src/calculator.py
- src/math_operations.py
- tests/test_calculator.py

## Review Checklist
1. Check for code style violations
2. Verify test coverage
3. Review error handling
4. Check documentation
        """,
        acceptance_criteria="""
- All tests pass
- Code coverage >= 80%
- No linting errors
- Documentation updated
        """,
        affected_files=[
            "src/calculator.py",
            "src/math_operations.py",
            "tests/test_calculator.py",
        ]
    )

    # Add subtasks
    mock.add_subtask(
        parent_key=parent.key,
        summary="Review: Check code style",
        description="Run linter and fix violations",
        status="Open",
    )

    mock.add_subtask(
        parent_key=parent.key,
        summary="Review: Verify test coverage",
        description="Ensure all new code has tests",
        status="Open",
    )

    mock.add_subtask(
        parent_key=parent.key,
        summary="Review: Check error handling",
        description="Verify proper exception handling",
        status="Open",
    )

    # Add review comments
    mock.add_comment(
        parent.key,
        "Please review the Calculator changes. Focus on the divide method.",
        author="developer"
    )

    return parent


def create_bug_fix_scenario(mock: JiraMock) -> JiraIssue:
    """
    Create a bug fix scenario.

    Returns a bug issue with:
    - Steps to reproduce
    - Expected vs actual behavior
    - Affected code references
    """
    mock.add_project("BUG", "Bug Tracking")

    bug = mock.create_issue(
        project_key="BUG",
        summary="Division by zero crashes application",
        issue_type="Bug",
        status="Open",
        priority="Critical",
        description="""
# Bug Report

## Steps to Reproduce
1. Open calculator
2. Enter 10 / 0
3. Application crashes

## Expected Behavior
Show error message "Cannot divide by zero"

## Actual Behavior
Unhandled ZeroDivisionError crashes the app

## Root Cause Analysis
The divide() method in Calculator class doesn't handle zero divisor.

## Fix Location
File: example.py
Method: Calculator.divide()
        """,
        acceptance_criteria="""
- Division by zero returns None or raises custom error
- Error is logged
- User sees friendly message
- Unit test added for this case
        """,
        affected_files=["example.py", "tests/test_example.py"],
        labels=["bug", "critical", "calculator"]
    )

    mock.add_comment(
        bug.key,
        "Confirmed. This affects production. Please fix ASAP.",
        author="qa_engineer"
    )

    return bug


def create_feature_request_scenario(mock: JiraMock) -> JiraIssue:
    """
    Create a feature request scenario with implementation tasks.
    """
    mock.add_project("FEAT", "Feature Requests")

    feature = mock.create_issue(
        project_key="FEAT",
        summary="Add power() function to Calculator",
        issue_type="Story",
        status="Open",
        priority="Major",
        description="""
# Feature Request: Power Function

## User Story
As a user, I want to calculate powers (x^y) so that I can perform
exponential calculations.

## Requirements
1. Add power(base, exponent) method to Calculator
2. Handle negative exponents
3. Handle fractional exponents
4. Add comprehensive tests

## Technical Notes
- Use math.pow() for implementation
- Consider edge cases: 0^0, negative bases with fractional exponents
        """,
        acceptance_criteria="""
- power(2, 3) returns 8
- power(2, -1) returns 0.5
- power(0, 0) returns 1 (mathematical convention)
- All edge cases documented
- 100% test coverage for new method
        """,
        affected_files=["example.py", "tests/test_example.py"]
    )

    # Implementation subtasks
    mock.add_subtask(
        parent_key=feature.key,
        summary="Implement power() method",
        status="Open",
    )

    mock.add_subtask(
        parent_key=feature.key,
        summary="Add unit tests for power()",
        status="Open",
    )

    mock.add_subtask(
        parent_key=feature.key,
        summary="Update documentation",
        status="Open",
    )

    return feature
