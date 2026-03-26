"""
E2E Test Runner.

Provides utilities to run test scenarios and collect results.
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .framework import (
    AIAssistClient,
    E2EReporter,
    ScenarioSuite,
    TestResult,
    TestScenario,
    TestSuiteResult,
    ToolAssertions,
    ToolCallTracker,
    load_scenario_file,
    load_scenarios,
)

logger = logging.getLogger(__name__)


class E2ETestRunner:
    """
    Runs E2E test scenarios against AI-Assist.

    Executes scenarios, tracks tool calls, and generates reports.
    """

    def __init__(
        self,
        ai_assist_url: str = "http://localhost:8000",
        proxy_url: str = "http://localhost:8080",
        model: str = "gptoss120b",
    ):
        """
        Initialize test runner.

        Args:
            ai_assist_url: AI-Assist server URL
            proxy_url: LLM-Test-Proxy URL
            model: Model to use for testing
        """
        self.ai_assist_url = ai_assist_url
        self.proxy_url = proxy_url
        self.model = model
        self.client: Optional[AIAssistClient] = None
        self.tracker = ToolCallTracker()
        self.reporter = E2EReporter()

    async def connect(self) -> None:
        """Connect to AI-Assist."""
        self.client = AIAssistClient(
            ai_assist_url=self.ai_assist_url,
            proxy_url=self.proxy_url,
        )
        await self.client.connect()

        # Health check
        health = await self.client.health_check()
        if not health["ai_assist"]:
            raise ConnectionError("AI-Assist server not available")

        logger.info("Connected to AI-Assist and Proxy")

    async def disconnect(self) -> None:
        """Disconnect from AI-Assist."""
        if self.client:
            await self.client.disconnect()
            self.client = None

    def _check_features_available(self, features: List[str]) -> tuple[bool, str]:
        """
        Check if required features are available.

        Args:
            features: List of feature names to check (e.g., ["jira", "github"])

        Returns:
            Tuple of (all_available, reason) - reason is set if not available
        """
        # Feature availability check - can be extended
        unavailable = []
        for feature in features:
            if feature.lower() == "jira":
                # Check if Jira mock server is reachable
                import socket
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(("localhost", 9000))
                    sock.close()
                    if result != 0:
                        unavailable.append(f"jira (mock server not running on port 9000)")
                except Exception:
                    unavailable.append(f"jira (mock server not reachable)")

        if unavailable:
            return False, f"Disabled features: {', '.join(unavailable)}"
        return True, ""

    async def run_scenario(
        self,
        scenario: TestScenario,
        workspace: Optional[Path] = None,
    ) -> TestResult:
        """
        Run a single test scenario.

        Args:
            scenario: Test scenario to run
            workspace: Optional workspace path for file operations

        Returns:
            TestResult with pass/fail and tool tracking
        """
        logger.info(f"Running scenario: {scenario.name}")
        start_time = time.time()
        errors = []

        # Check if scenario should be skipped
        if scenario.skip_if_disabled:
            available, reason = self._check_features_available(scenario.skip_if_disabled)
            if not available:
                logger.info(f"Skipping scenario {scenario.name}: {reason}")
                return TestResult(
                    name=scenario.name,
                    passed=True,  # Skipped counts as passed
                    prompt=scenario.prompt,
                    expected_tools=[t.name for t in scenario.expected_tools],
                    actual_tools=[],
                    response_preview="",
                    duration_ms=0,
                    errors=[],
                    skipped=True,
                    skip_reason=reason,
                )

        # Get metrics before
        metrics_before = await self.client.get_proxy_metrics()

        try:
            # Build prompt with workspace context if provided
            prompt = scenario.prompt
            if workspace:
                prompt = f"Working in directory: {workspace}\n\n{prompt}"

            # Send chat request
            response = await self.client.chat_sync(
                message=prompt,
                model=self.model,
            )

            # Extract tool calls
            self.tracker.reset()
            tool_calls = self.tracker.extract_from_events(response.events)

            # Verify expected tools
            expected_names = [t.name for t in scenario.expected_tools if t.required]

            try:
                ToolAssertions.assert_tools_called(
                    actual=tool_calls,
                    expected=expected_names,
                    strict_order=scenario.strict_order,
                )
            except AssertionError as e:
                errors.append(str(e))

            # Verify response content
            if scenario.response_expectations.contains:
                try:
                    ToolAssertions.assert_response_contains(
                        response=response.final_response,
                        expected=scenario.response_expectations.contains,
                    )
                except AssertionError as e:
                    errors.append(str(e))

            if scenario.response_expectations.not_contains:
                try:
                    ToolAssertions.assert_response_not_contains(
                        response=response.final_response,
                        forbidden=scenario.response_expectations.not_contains,
                    )
                except AssertionError as e:
                    errors.append(str(e))

            # Verify tool arguments if specified
            for expected_tool in scenario.expected_tools:
                if expected_tool.args:
                    try:
                        ToolAssertions.assert_tool_args(
                            actual=tool_calls,
                            tool_name=expected_tool.name,
                            expected_args=expected_tool.args,
                            match_type=expected_tool.match_type,
                        )
                    except AssertionError as e:
                        errors.append(str(e))

            passed = len(errors) == 0

        except Exception as e:
            logger.error(f"Scenario failed with exception: {e}")
            errors.append(f"Exception: {str(e)}")
            tool_calls = []
            response = None
            passed = False

        # Get metrics after
        metrics_after = await self.client.get_proxy_metrics()

        duration_ms = int((time.time() - start_time) * 1000)

        result = TestResult(
            name=scenario.name,
            passed=passed,
            prompt=scenario.prompt,
            expected_tools=[t.name for t in scenario.expected_tools],
            actual_tools=tool_calls,
            response_preview=response.final_response[:200] if response else "",
            duration_ms=duration_ms,
            errors=errors,
            proxy_metrics_before=metrics_before,
            proxy_metrics_after=metrics_after,
        )

        status = "PASSED" if passed else "FAILED"
        logger.info(f"Scenario {scenario.name}: {status} ({duration_ms}ms)")

        return result

    async def run_suite(
        self,
        suite: ScenarioSuite,
        workspace: Optional[Path] = None,
    ) -> TestSuiteResult:
        """
        Run all scenarios in a suite.

        Args:
            suite: Scenario suite to run
            workspace: Optional workspace path

        Returns:
            TestSuiteResult with all test results
        """
        logger.info(f"Running suite: {suite.name} ({len(suite.scenarios)} scenarios)")
        start_time = time.time()

        results = []
        for scenario in suite.scenarios:
            result = await self.run_scenario(scenario, workspace)
            results.append(result)

        duration_ms = int((time.time() - start_time) * 1000)

        suite_result = TestSuiteResult(
            name=suite.name,
            tests=results,
            total_duration_ms=duration_ms,
        )

        logger.info(
            f"Suite {suite.name} completed: "
            f"{suite_result.passed}/{suite_result.total} passed "
            f"({suite_result.success_rate:.1f}%)"
        )

        return suite_result

    async def run_scenarios_from_file(
        self,
        path: Path,
        workspace: Optional[Path] = None,
    ) -> TestSuiteResult:
        """
        Load and run scenarios from YAML file.

        Args:
            path: Path to YAML file
            workspace: Optional workspace path

        Returns:
            TestSuiteResult
        """
        suite = load_scenario_file(path)
        return await self.run_suite(suite, workspace)

    async def run_all_scenarios(
        self,
        scenarios_dir: Path,
        workspace: Optional[Path] = None,
        tags: Optional[List[str]] = None,
    ) -> List[TestSuiteResult]:
        """
        Run all scenario files in directory.

        Args:
            scenarios_dir: Directory with YAML files
            workspace: Optional workspace path
            tags: Filter by tags

        Returns:
            List of TestSuiteResult
        """
        suites = load_scenarios(scenarios_dir)
        results = []

        for suite in suites:
            # Filter by tags if specified
            if tags:
                suite.scenarios = [
                    s for s in suite.scenarios
                    if any(t in s.tags for t in tags)
                ]

            if suite.scenarios:
                result = await self.run_suite(suite, workspace)
                results.append(result)

        return results


async def run_e2e_tests(
    scenarios_dir: Optional[Path] = None,
    workspace: Optional[Path] = None,
    tags: Optional[List[str]] = None,
    generate_report: bool = True,
) -> List[TestSuiteResult]:
    """
    Convenience function to run E2E tests.

    Args:
        scenarios_dir: Directory with scenario YAML files
        workspace: Workspace for file operations
        tags: Filter scenarios by tags
        generate_report: Whether to generate HTML report

    Returns:
        List of TestSuiteResult
    """
    if scenarios_dir is None:
        scenarios_dir = Path(__file__).parent / "scenarios"

    runner = E2ETestRunner()

    try:
        await runner.connect()

        results = await runner.run_all_scenarios(
            scenarios_dir=scenarios_dir,
            workspace=workspace,
            tags=tags,
        )

        # Generate reports
        if generate_report:
            for suite_result in results:
                runner.reporter.generate_html_report(suite_result)
                runner.reporter.generate_json_report(suite_result)
                runner.reporter.print_summary(suite_result)

        return results

    finally:
        await runner.disconnect()


if __name__ == "__main__":
    # Run all tests when executed directly
    asyncio.run(run_e2e_tests())
