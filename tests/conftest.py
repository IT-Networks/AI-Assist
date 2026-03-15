"""
Pytest configuration and shared fixtures.
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances before each test."""
    # Reset task planner singleton
    import app.agent.task_planner as planner_module
    planner_module._task_planner = None

    # Reset task executor singleton
    import app.agent.task_executor as executor_module
    executor_module._task_executor = None

    # Reset agent configs singleton
    import app.agent.task_agents as agents_module
    agents_module._agent_configs = {}

    yield
