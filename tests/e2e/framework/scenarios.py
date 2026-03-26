"""
YAML Test Scenario Loader for E2E Testing.

Loads and validates test scenarios from YAML files.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ToolExpectation:
    """Expected tool call in a test scenario."""
    name: str
    required: bool = True
    args: Dict[str, Any] = field(default_factory=dict)
    match_type: str = "contains"  # exact, contains, regex


@dataclass
class ResponseExpectation:
    """Expected response content."""
    contains: List[str] = field(default_factory=list)
    not_contains: List[str] = field(default_factory=list)
    regex: Optional[str] = None


@dataclass
class TestScenario:
    """A single test scenario."""
    name: str
    description: str
    prompt: str
    expected_tools: List[ToolExpectation]
    response_expectations: ResponseExpectation
    strict_order: bool = False
    allow_extra_tools: bool = True
    timeout: float = 120.0
    tags: List[str] = field(default_factory=list)
    setup: Optional[Dict[str, Any]] = None
    teardown: Optional[Dict[str, Any]] = None
    skip_if_disabled: List[str] = field(default_factory=list)  # Features required for test

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestScenario":
        """Create TestScenario from dictionary."""
        # Parse tool expectations
        tools_data = data.get("expected_tools", [])
        expected_tools = []
        for tool in tools_data:
            if isinstance(tool, str):
                expected_tools.append(ToolExpectation(name=tool))
            elif isinstance(tool, dict):
                expected_tools.append(ToolExpectation(
                    name=tool.get("name", ""),
                    required=tool.get("required", True),
                    args=tool.get("args", {}),
                    match_type=tool.get("match_type", "contains"),
                ))

        # Parse response expectations
        resp_data = data.get("response", {})
        response_expectations = ResponseExpectation(
            contains=resp_data.get("contains", []),
            not_contains=resp_data.get("not_contains", []),
            regex=resp_data.get("regex"),
        )

        return cls(
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            expected_tools=expected_tools,
            response_expectations=response_expectations,
            strict_order=data.get("strict_order", False),
            allow_extra_tools=data.get("allow_extra_tools", True),
            timeout=data.get("timeout", 120.0),
            tags=data.get("tags", []),
            setup=data.get("setup"),
            teardown=data.get("teardown"),
            skip_if_disabled=data.get("skip_if_disabled", []),
        )


@dataclass
class ScenarioSuite:
    """A collection of test scenarios."""
    name: str
    description: str
    scenarios: List[TestScenario]
    model: str = "gptoss120b"
    base_url: str = "http://localhost:8000"
    proxy_url: str = "http://localhost:8080"
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioSuite":
        """Create ScenarioSuite from dictionary."""
        scenarios = [
            TestScenario.from_dict(s)
            for s in data.get("scenarios", [])
        ]

        return cls(
            name=data.get("name", "unnamed_suite"),
            description=data.get("description", ""),
            scenarios=scenarios,
            model=data.get("model", "gptoss120b"),
            base_url=data.get("base_url", "http://localhost:8000"),
            proxy_url=data.get("proxy_url", "http://localhost:8080"),
            tags=data.get("tags", []),
        )


def load_scenario_file(path: Union[str, Path]) -> ScenarioSuite:
    """
    Load a scenario suite from a YAML file.

    Args:
        path: Path to YAML file

    Returns:
        ScenarioSuite object

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is invalid
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    logger.info(f"Loaded scenario suite from {path}")
    return ScenarioSuite.from_dict(data)


def load_scenarios(directory: Union[str, Path]) -> List[ScenarioSuite]:
    """
    Load all scenario suites from a directory.

    Args:
        directory: Directory containing YAML files

    Returns:
        List of ScenarioSuite objects
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    suites = []
    for yaml_file in directory.glob("*.yaml"):
        try:
            suite = load_scenario_file(yaml_file)
            suites.append(suite)
            logger.info(f"Loaded {len(suite.scenarios)} scenarios from {yaml_file.name}")
        except Exception as e:
            logger.error(f"Failed to load {yaml_file}: {e}")

    return suites


def filter_scenarios_by_tag(
    suites: List[ScenarioSuite],
    include_tags: Optional[List[str]] = None,
    exclude_tags: Optional[List[str]] = None,
) -> List[TestScenario]:
    """
    Filter scenarios by tags.

    Args:
        suites: List of scenario suites
        include_tags: Only include scenarios with these tags
        exclude_tags: Exclude scenarios with these tags

    Returns:
        Filtered list of TestScenario objects
    """
    scenarios = []

    for suite in suites:
        for scenario in suite.scenarios:
            # Check exclude tags
            if exclude_tags:
                if any(tag in scenario.tags for tag in exclude_tags):
                    continue

            # Check include tags
            if include_tags:
                if not any(tag in scenario.tags for tag in include_tags):
                    continue

            scenarios.append(scenario)

    return scenarios
