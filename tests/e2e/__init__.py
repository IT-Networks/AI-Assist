"""
E2E Test Package for AI-Assist.

Provides end-to-end testing with tool call tracking and verification.
"""

from .framework import (
    # Client
    AIAssistClient,
    # Tracker
    ToolCallTracker,
    # Models
    TrackedToolCall,
    TestResult,
    TestSuiteResult,
    VerificationResult,
    # Assertions
    ToolAssertions,
    ToolAssertionError,
    # Scenarios
    load_scenarios,
    load_scenario_file,
    TestScenario,
    ToolExpectation,
    ScenarioSuite,
    # Reporter
    E2EReporter,
)

from .test_runner import E2ETestRunner, run_e2e_tests

__all__ = [
    # Client
    "AIAssistClient",
    # Tracker
    "ToolCallTracker",
    # Models
    "TrackedToolCall",
    "TestResult",
    "TestSuiteResult",
    "VerificationResult",
    # Assertions
    "ToolAssertions",
    "ToolAssertionError",
    # Scenarios
    "load_scenarios",
    "load_scenario_file",
    "TestScenario",
    "ToolExpectation",
    "ScenarioSuite",
    # Reporter
    "E2EReporter",
    # Runner
    "E2ETestRunner",
    "run_e2e_tests",
]
