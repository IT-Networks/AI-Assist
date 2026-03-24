"""
Testing Services - Test-Ausführung und -Management.

Dieses Paket gruppiert Services für Testing:
- TestExecutor (JUnit/pytest)
- TestExecution
- TestSessionManager
- TestTemplateEngine

Verwendung:
    from app.services.testing import get_test_execution_service

    service = get_test_execution_service()
    result = await service.run_test("TestClass#testMethod")
"""

from app.services.test_executor import (
    TestExecutor,
    get_test_executor,
)

from app.services.test_execution import (
    TestExecutionService,
    get_test_execution_service,
)

from app.services.test_session_manager import (
    TestSessionManager,
    get_session_manager,
)

from app.services.test_template_engine import (
    TestTemplateEngine,
    get_template_engine,
)

__all__ = [
    # Executor
    "TestExecutor",
    "get_test_executor",
    # Execution
    "TestExecutionService",
    "get_test_execution_service",
    # Session
    "TestSessionManager",
    "get_session_manager",
    # Templates
    "TestTemplateEngine",
    "get_template_engine",
]
