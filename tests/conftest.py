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

    # Reset token tracker singleton
    import app.services.token_tracker as tracker_module
    tracker_module._token_tracker = None

    # Reset self-healing engine singleton
    import app.services.self_healing as healing_module
    healing_module._self_healing_engine = None

    # Reset parallel agents orchestrator singleton
    import app.services.parallel_agents as agents_module
    agents_module._parallel_orchestrator = None

    # Reset PR review service singleton
    import app.services.pr_review as review_module
    review_module._pr_review_service = None

    # Reset arena mode service singleton
    import app.services.arena_mode as arena_module
    arena_module._arena_mode_service = None

    # Reset script manager singleton (Phase 1/2/3 testing)
    import app.services.script_manager as script_mgr_module
    script_mgr_module.ScriptManager._instance = None

    # Reset email singletons
    import app.services.todo_store as todo_store_module
    todo_store_module._todo_store = None

    import app.services.email_automation as email_auto_module
    email_auto_module._automation = None

    import app.services.email_client as email_client_module
    email_client_module._email_client = None

    # Reset webex singletons
    import app.services.webex_client as webex_client_module
    webex_client_module._webex_client = None

    import app.services.webex_automation as webex_auto_module
    webex_auto_module._automation = None

    yield
