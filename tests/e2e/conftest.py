"""
Pytest fixtures for E2E testing.

Provides fixtures for:
- AI-Assist client
- Tool call tracker
- Test reporter
- Test workspace management
"""

import asyncio
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Generator, List

import pytest
import pytest_asyncio

from .framework import (
    AIAssistClient,
    E2EReporter,
    TestResult,
    TestSuiteResult,
    ToolAssertions,
    ToolCallTracker,
    TrackedToolCall,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

@pytest.fixture(scope="session")
def ai_assist_url() -> str:
    """AI-Assist server URL."""
    return os.getenv("AI_ASSIST_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def proxy_url() -> str:
    """LLM-Test-Proxy URL."""
    return os.getenv("PROXY_URL", "http://localhost:8080")


@pytest.fixture(scope="session")
def test_model() -> str:
    """Model to use for testing."""
    return os.getenv("TEST_MODEL", "gptoss120b")


# ============================================================================
# Client Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def ai_client(
    ai_assist_url: str,
    proxy_url: str,
) -> AsyncGenerator[AIAssistClient, None]:
    """
    Create connected AI-Assist client.

    Automatically connects and disconnects.
    """
    client = AIAssistClient(
        ai_assist_url=ai_assist_url,
        proxy_url=proxy_url,
        timeout=120.0,
    )
    await client.connect()

    # Health check
    health = await client.health_check()
    if not health["ai_assist"]:
        pytest.skip("AI-Assist server not available")

    yield client

    await client.disconnect()


@pytest.fixture
def tool_tracker() -> ToolCallTracker:
    """Create a fresh tool call tracker."""
    return ToolCallTracker()


@pytest.fixture
def assertions() -> ToolAssertions:
    """Provide ToolAssertions class."""
    return ToolAssertions


# ============================================================================
# Reporter Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def report_dir() -> Path:
    """Directory for test reports."""
    reports = Path(__file__).parent / "reports"
    reports.mkdir(exist_ok=True)
    return reports


@pytest.fixture(scope="session")
def reporter(report_dir: Path) -> E2EReporter:
    """Create test reporter."""
    return E2EReporter(output_dir=report_dir)


# ============================================================================
# Test Workspace Fixtures
# ============================================================================

@pytest.fixture
def workspace() -> Generator[Path, None, None]:
    """
    Create a temporary workspace directory.

    Provides an isolated directory for file operations.
    Automatically cleaned up after test.
    """
    workspace_path = Path(tempfile.mkdtemp(prefix="e2e_test_"))
    logger.info(f"Created test workspace: {workspace_path}")

    yield workspace_path

    # Cleanup
    shutil.rmtree(workspace_path, ignore_errors=True)
    logger.info(f"Cleaned up workspace: {workspace_path}")


@pytest.fixture
def sample_files(workspace: Path) -> dict:
    """
    Create sample files in workspace for testing.

    Returns dict mapping file names to paths.
    """
    files = {}

    # Python file
    py_file = workspace / "example.py"
    py_file.write_text("""
def greet(name: str) -> str:
    \"\"\"Greet someone by name.\"\"\"
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    \"\"\"Add two numbers.\"\"\"
    return a + b

class Calculator:
    def multiply(self, x: int, y: int) -> int:
        return x * y
""")
    files["example.py"] = py_file

    # JSON file
    json_file = workspace / "config.json"
    json_file.write_text("""{
    "name": "test-project",
    "version": "1.0.0",
    "settings": {
        "debug": true,
        "timeout": 30
    }
}""")
    files["config.json"] = json_file

    # Markdown file
    md_file = workspace / "README.md"
    md_file.write_text("""# Test Project

This is a test project for E2E testing.

## Features

- Feature 1
- Feature 2
- Feature 3
""")
    files["README.md"] = md_file

    # Subdirectory with files
    sub_dir = workspace / "src"
    sub_dir.mkdir()

    main_file = sub_dir / "main.py"
    main_file.write_text("""
from example import greet

if __name__ == "__main__":
    print(greet("World"))
""")
    files["src/main.py"] = main_file

    logger.info(f"Created {len(files)} sample files in workspace")
    return files


# ============================================================================
# Test Result Collection
# ============================================================================

@pytest.fixture(scope="module")
def test_results() -> List[TestResult]:
    """Collect test results for reporting."""
    return []


@pytest.fixture(scope="module")
def suite_start_time() -> datetime:
    """Track when test suite started."""
    return datetime.now()


# ============================================================================
# Helper Fixtures
# ============================================================================

@pytest.fixture
def extract_tools(tool_tracker: ToolCallTracker):
    """
    Helper to extract tool calls from response.

    Returns a function for use in tests.
    """
    async def _extract(client: AIAssistClient, response) -> List[TrackedToolCall]:
        """Extract tool calls from chat response."""
        tool_tracker.reset()
        return tool_tracker.extract_from_events(response.events)

    return _extract


@pytest.fixture
def assert_tools():
    """
    Helper to assert tool calls.

    Returns the ToolAssertions class for static method access.
    """
    return ToolAssertions


# ============================================================================
# Markers
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "requires_github: marks tests that require GitHub access"
    )
    config.addinivalue_line(
        "markers", "local_files: marks tests for local file operations"
    )
    config.addinivalue_line(
        "markers", "multi_tool: marks tests with multiple tool calls"
    )


# ============================================================================
# Event Loop
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
