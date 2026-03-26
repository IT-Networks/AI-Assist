# Jira Mock Integration Options

## Current Problem

The `JiraMock` class in `tests/e2e/mocks/jira_mock.py` is a standalone Python class designed for pytest unit tests. It is NOT connected to AI-Assist's tool execution pipeline.

When E2E tests call Jira tools:
1. Test sends HTTP request to AI-Assist
2. AI-Assist calls LLM
3. LLM requests `search_jira` tool
4. `search_jira` checks `settings.jira.enabled` → returns error

## Solution 1: Environment-Based Mock Injection (Recommended)

Modify `app/services/jira_client.py` to use JiraMock when in test mode:

```python
# app/services/jira_client.py
import os

def get_jira_client():
    if os.getenv("E2E_TEST_MODE") == "true":
        from tests.e2e.mocks.jira_mock import JiraMock
        return get_mock_client()  # Return JiraMock wrapper
    return JiraClient(settings.jira)
```

**Pros:**
- No separate server needed
- JiraMock data can be customized per test
- Fast execution

**Cons:**
- Requires code change in production code
- Mock must implement same interface as JiraClient

## Solution 2: Mock Jira HTTP Server

Create a FastAPI server that implements Jira REST API endpoints:

```python
# tests/e2e/mocks/jira_server.py
from fastapi import FastAPI
from tests.e2e.mocks.jira_mock import JiraMock

app = FastAPI()
mock = JiraMock()

# Pre-populate scenarios
create_bug_fix_scenario(mock)
create_code_review_scenario(mock)

@app.get("/rest/api/2/search")
def search(jql: str = ""):
    return {"issues": mock.search(jql=jql)}

@app.get("/rest/api/2/issue/{issue_key}")
def get_issue(issue_key: str):
    return mock.get_issue(issue_key)
```

Then configure `config.yaml`:
```yaml
jira:
  enabled: true
  base_url: http://localhost:9000  # Mock server
```

**Pros:**
- No production code changes
- Full API compatibility
- Can test with real Jira config structure

**Cons:**
- Requires running additional server
- More complex setup

## Solution 3: Skip Jira Tests When Disabled

Mark Jira tests as expected failures:

```yaml
# scenarios/jira_workflows.yaml
scenarios:
  - name: search_jira_issues
    skip_if_disabled: ["jira"]  # New field
```

```python
# test_runner.py
if scenario.skip_if_disabled:
    for feature in scenario.skip_if_disabled:
        if not is_feature_enabled(feature):
            return TestResult(name=scenario.name, passed=True, skipped=True)
```

**Pros:**
- Simplest implementation
- No production code changes
- Tests don't fail when Jira unavailable

**Cons:**
- Doesn't actually test Jira workflows
- Reduces test coverage

## Recommended Implementation

1. **Short-term**: Solution 3 (skip tests when disabled)
2. **Medium-term**: Solution 1 (environment-based injection)
3. **Long-term**: Solution 2 (full mock server for integration testing)

## Quick Fix for Current Tests

Add to `config.yaml` a test Jira configuration:

```yaml
jira:
  enabled: true  # Enable for testing
  base_url: https://jira.example.com
  # ... credentials won't work, but tools will try
```

This will make tools attempt calls (instead of immediately returning error),
which is closer to real behavior even if they fail with "connection error".
