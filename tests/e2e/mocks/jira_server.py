"""
Mock Jira HTTP Server for E2E Testing.

Provides a FastAPI server that implements Jira REST API endpoints
with pre-populated test data for E2E scenarios.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .jira_mock import JiraMock, create_bug_fix_scenario, create_code_review_scenario, create_feature_request_scenario

logger = logging.getLogger(__name__)

app = FastAPI(title="Jira Mock Server", version="1.0.0")

# Initialize mock with test data
mock = JiraMock()
create_bug_fix_scenario(mock)
create_code_review_scenario(mock)
create_feature_request_scenario(mock)


# ============================================================================
# Pydantic Models for API responses
# ============================================================================

class JiraIssueResponse(BaseModel):
    """Jira issue API response."""
    key: str
    fields: Dict[str, Any]


class JiraSearchResponse(BaseModel):
    """Jira search API response."""
    issues: List[Dict[str, Any]]
    total: int
    maxResults: int
    startAt: int


# ============================================================================
# API Endpoints (Jira REST API v2 compatible)
# ============================================================================

@app.get("/")
def root():
    """Root endpoint for health check."""
    return {"status": "ok", "service": "jira-mock"}


@app.get("/rest/api/2/serverInfo")
def server_info():
    """Jira server info endpoint."""
    return {
        "version": "8.22.0",
        "versionNumbers": [8, 22, 0],
        "deploymentType": "Mock",
        "baseUrl": "http://localhost:9000",
        "serverTitle": "Jira Mock Server",
    }


@app.get("/rest/api/2/search")
def search_issues(
    jql: str = Query("", description="JQL query"),
    maxResults: int = Query(50, description="Max results"),
    startAt: int = Query(0, description="Start index"),
    fields: str = Query("*all", description="Fields to return"),
) -> JiraSearchResponse:
    """
    Search for issues using JQL.

    Supports basic JQL parsing for:
    - project = X
    - status = X
    - type = X
    - assignee = X
    """
    logger.info(f"Searching issues with JQL: {jql}")

    # Parse JQL into filter criteria
    filters = {}
    if jql:
        jql_lower = jql.lower()

        # Extract project
        if "project" in jql_lower:
            for part in jql.split(" "):
                if part.startswith("project"):
                    # Handle "project = X" or "project=X"
                    parts = jql.split("project")
                    if len(parts) > 1:
                        value = parts[1].strip().lstrip("=").strip().strip('"').strip("'")
                        # Take first word
                        filters["project"] = value.split()[0] if value else ""

        # Extract status
        if "status" in jql_lower:
            parts = jql.lower().split("status")
            if len(parts) > 1:
                value = parts[1].strip().lstrip("=").strip().strip('"').strip("'")
                filters["status"] = value.split()[0] if value else ""

        # Extract type
        if "type" in jql_lower or "issuetype" in jql_lower:
            keyword = "type" if "type" in jql_lower else "issuetype"
            parts = jql.lower().split(keyword)
            if len(parts) > 1:
                value = parts[1].strip().lstrip("=").strip().strip('"').strip("'")
                filters["issue_type"] = value.split()[0] if value else ""

    # Search using mock
    try:
        results = mock.search(**filters) if filters else mock.search()
    except Exception as e:
        logger.error(f"Search error: {e}")
        results = []

    # Convert to Jira API format
    issues = []
    for issue in results:
        issues.append({
            "key": issue.get("key", ""),
            "fields": {
                "summary": issue.get("summary", ""),
                "description": issue.get("description", ""),
                "status": {"name": issue.get("status", "")},
                "issuetype": {"name": issue.get("issue_type", "")},
                "priority": {"name": issue.get("priority", "Medium")},
                "assignee": {"displayName": issue.get("assignee", "")},
                "reporter": {"displayName": issue.get("reporter", "")},
                "project": {"key": issue.get("project", "")},
            }
        })

    return JiraSearchResponse(
        issues=issues,
        total=len(issues),
        maxResults=maxResults,
        startAt=startAt,
    )


@app.get("/rest/api/2/issue/{issue_key}")
def get_issue(issue_key: str) -> JiraIssueResponse:
    """
    Get a single issue by key.
    """
    logger.info(f"Getting issue: {issue_key}")

    issue = mock.get_issue(issue_key)
    if issue is None:
        raise HTTPException(
            status_code=404,
            detail=f"Issue {issue_key} not found"
        )

    return JiraIssueResponse(
        key=issue.get("key", issue_key),
        fields={
            "summary": issue.get("summary", ""),
            "description": issue.get("description", ""),
            "status": {"name": issue.get("status", "")},
            "issuetype": {"name": issue.get("issue_type", "")},
            "priority": {"name": issue.get("priority", "Medium")},
            "assignee": {"displayName": issue.get("assignee", "")},
            "reporter": {"displayName": issue.get("reporter", "")},
            "project": {"key": issue.get("project", "")},
            "customfield_10001": issue.get("acceptance_criteria", ""),  # Acceptance criteria
            "customfield_10002": issue.get("affected_files", []),  # Affected files
            "subtasks": [
                {"key": st.get("key"), "fields": {"summary": st.get("summary")}}
                for st in issue.get("subtasks", [])
            ],
            "parent": {"key": issue.get("parent", "")} if issue.get("parent") else None,
        }
    )


@app.get("/rest/api/2/project/{project_key}")
def get_project(project_key: str):
    """Get project info."""
    return {
        "key": project_key,
        "name": f"Project {project_key}",
        "projectTypeKey": "software",
    }


# ============================================================================
# Server Runner
# ============================================================================

def run_server(host: str = "127.0.0.1", port: int = 9000):
    """Run the mock Jira server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_server()
