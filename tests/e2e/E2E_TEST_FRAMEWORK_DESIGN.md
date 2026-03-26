# E2E Test Framework Design Document

## Overview

This document describes the extended E2E test framework for AI-Assist, designed to:
1. Test tool behavior in realistic scenarios
2. Discover tool limitations and edge cases
3. Simulate complex workflows (Jira, GitHub, Code Review)
4. Provide workspace management with reset capability

## Architecture

```
tests/e2e/
├── framework/                 # Core framework
│   ├── client.py             # AI-Assist API client
│   ├── tracker.py            # Tool call tracking
│   ├── reporter.py           # HTML/JSON reports
│   ├── scenarios.py          # YAML scenario loader
│   └── models.py             # Data models
│
├── mocks/                     # Mock services
│   ├── jira_mock.py          # Jira API simulation
│   └── workspace_manager.py  # Workspace state management
│
├── scenarios/                 # Test scenarios (YAML)
│   ├── local_files.yaml      # Basic file operations
│   ├── github_operations.yaml # GitHub tool tests
│   ├── multi_tool_workflows.yaml
│   ├── jira_workflows.yaml   # Jira integration tests
│   └── tool_boundaries.yaml  # Edge cases and limits
│
├── test_*.py                  # pytest test files
├── run_tests.py              # CLI runner (test mode)
└── run_prod_tests.py         # CLI runner (production)
```

## Available Tools in AI-Assist

### File Operations
| Tool | Description | Write? |
|------|-------------|--------|
| `read_file` | Read file content | No |
| `write_file` | Create/overwrite file | Yes |
| `edit_file` | Patch existing file | Yes |
| `list_files` | List directory contents | No |
| `search_code` | Search in codebase | No |

### GitHub Integration
| Tool | Description |
|------|-------------|
| `github_list_repos` | List repositories |
| `github_list_prs` | List pull requests |
| `github_pr_details` | PR metadata |
| `github_pr_diff` | PR code changes |
| `github_get_file` | File from GitHub |
| `github_list_issues` | List issues |
| `github_issue_details` | Issue details |

### Jira Integration
| Tool | Description |
|------|-------------|
| `search_jira` | JQL search |
| `read_jira_issue` | Full issue details |

### DevOps Tools
| Tool | Description |
|------|-------------|
| `jenkins_list_jobs` | List Jenkins jobs |
| `jenkins_job_status` | Job status |
| `jenkins_trigger_build` | Start build |
| `compile_files` | Compile/lint code |
| `validate_file` | Validate file syntax |

### Analysis Tools
| Tool | Description |
|------|-------------|
| `graph_impact` | Impact analysis |
| `graph_dependents` | Find dependents |
| `graph_search` | Search code graph |

## Test Scenarios

### 1. Basic Operations (local_files.yaml)
- File reading and writing
- Directory listing
- Code search

### 2. GitHub Operations (github_operations.yaml)
- PR listing and details
- Issue tracking
- File retrieval from branches

### 3. Multi-Tool Workflows (multi_tool_workflows.yaml)
- Read-Modify-Verify cycles
- Code review workflows
- Feature implementation

### 4. Jira Workflows (jira_workflows.yaml)
- **Bug Analysis**: Read bug → Analyze code → Plan fix
- **Code Review**: Process review with subtasks
- **Feature Implementation**: Story → Requirements → Code

### 5. Tool Boundaries (tool_boundaries.yaml)
- Large file handling
- Binary file handling
- Permission errors
- Empty results
- Context accumulation
- Tool chain limits

## Mock Services

### JiraMock

```python
from tests.e2e.mocks import JiraMock, JiraIssue

mock = JiraMock()
mock.add_project("TEST", "Test Project")

# Create bug with affected files
bug = mock.create_issue(
    project_key="TEST",
    summary="Division by zero crashes app",
    issue_type="Bug",
    affected_files=["calculator.py"],
)

# Add subtask
mock.add_subtask(
    parent_key=bug.key,
    summary="Add zero check",
)

# Search
results = mock.search(issue_type="Bug", status="Open")
```

### WorkspaceManager

```python
from tests.e2e.mocks import WorkspaceManager

manager = WorkspaceManager(Path("./workspace"))

# Take snapshot before test
manager.snapshot("before_test")

# ... run test that modifies files ...

# Check what changed
diffs = manager.diff("before_test", "current")
for d in diffs:
    print(f"{d.status}: {d.path}")

# Restore original state
manager.restore("before_test")
```

## Test Scenarios for Tool Limits

### 1. Loop Prevention
AI-Assist limits repeated tool calls:
- `read_file`: Max 2x per file
- `edit_file`: Max 2x per file
- `write_file`: Max 1x per file

### 2. Context Accumulation
Test how tool results accumulate in context:
- Multiple reads
- Large search results
- Truncation behavior

### 3. Parallel Execution
Verify parallel tool execution:
- Read-only tools can run parallel
- Write tools run sequential

### 4. Error Handling
Test graceful error handling:
- Non-existent files
- Permission denied
- Invalid parameters
- Network timeouts (Jira/GitHub)

## Running Tests

### Test Mode (with LLM-Test-Proxy)
```bash
# Set environment
export AI_ASSIST_URL=http://localhost:8000
export PROXY_URL=http://localhost:8080
export TEST_MODEL=gptoss120b

# Run all tests
python run_tests.py

# Run specific scenario
python run_tests.py --scenario jira_workflows
```

### Production Mode (real LLM)
```bash
# Edit config
vim config_prod.env

# Run
python run_prod_tests.py --scenario tool_boundaries
```

## Expected Results

| Scenario Suite | Expected Pass Rate | Notes |
|---------------|-------------------|-------|
| Local Files | 70-80% | Basic operations reliable |
| GitHub Operations | 75-85% | Depends on API availability |
| Multi-Tool Workflows | 50-70% | Complex chains may fail |
| Jira Workflows | 60-80% | Depends on mock setup |
| Tool Boundaries | 40-60% | Edge cases often fail |

## Improvements Identified

### For Tests
1. Make tool requirements flexible (alternatives)
2. Add conditional expectations
3. Implement retry logic for flaky tests
4. Add timeout configuration per scenario

### For AI-Assist
1. Improve SYSTEM_PROMPT with explicit tool guidance
2. Add tool selection hints for common patterns
3. Improve error messages for tool failures
4. Add telemetry for tool usage patterns

## Future Extensions

1. **ServiceNow Mock**: Similar to JiraMock for ITSM testing
2. **Jenkins Mock**: For CI/CD workflow testing
3. **Database Mock**: For SQL tool testing
4. **Confluence Mock**: For documentation workflows
5. **Performance Testing**: Measure tool latency
6. **Regression Suite**: Automated nightly runs
