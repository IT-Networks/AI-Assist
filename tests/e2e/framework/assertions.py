"""
Custom Assertions for E2E Testing.

Provides pytest-style assertions for tool calls and responses.
"""

import re
from typing import Any, Dict, List, Optional, Union

from .models import TrackedToolCall


class ToolAssertionError(AssertionError):
    """Custom assertion error for tool-related failures."""
    pass


class ToolAssertions:
    """
    Custom assertions for tool call verification.

    All methods raise ToolAssertionError on failure.
    """

    @staticmethod
    def assert_tools_called(
        actual: List[TrackedToolCall],
        expected: List[str],
        strict_order: bool = False,
        msg: str = "",
    ) -> None:
        """
        Assert that specified tools were called.

        Args:
            actual: List of actual tool calls
            expected: List of expected tool names
            strict_order: If True, order must match
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If assertion fails
        """
        actual_names = [c.name for c in actual]

        # Check all expected tools are present
        missing = [t for t in expected if t not in actual_names]
        if missing:
            raise ToolAssertionError(
                f"Expected tools not called: {missing}. "
                f"Actual tools: {actual_names}. {msg}"
            )

        # Check order if strict
        if strict_order:
            expected_indices = [actual_names.index(t) for t in expected if t in actual_names]
            if expected_indices != sorted(expected_indices):
                raise ToolAssertionError(
                    f"Tool order incorrect. Expected: {expected}, "
                    f"Actual order: {actual_names}. {msg}"
                )

    @staticmethod
    def assert_tool_not_called(
        actual: List[TrackedToolCall],
        tool_name: str,
        msg: str = "",
    ) -> None:
        """
        Assert that a specific tool was NOT called.

        Args:
            actual: List of actual tool calls
            tool_name: Tool name that should not appear
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If tool was called
        """
        actual_names = [c.name for c in actual]

        if tool_name in actual_names:
            raise ToolAssertionError(
                f"Tool '{tool_name}' should NOT have been called. "
                f"Actual tools: {actual_names}. {msg}"
            )

    @staticmethod
    def assert_tools_not_called(
        actual: List[TrackedToolCall],
        tools: List[str],
        msg: str = "",
    ) -> None:
        """
        Assert that specified tools were NOT called.

        Args:
            actual: List of actual tool calls
            tools: List of tool names that should not appear
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If any tool was called
        """
        actual_names = [c.name for c in actual]
        called = [t for t in tools if t in actual_names]

        if called:
            raise ToolAssertionError(
                f"Tools should NOT have been called: {called}. "
                f"Actual tools: {actual_names}. {msg}"
            )

    @staticmethod
    def assert_tool_count(
        actual: List[TrackedToolCall],
        expected_min: int = 0,
        expected_max: Optional[int] = None,
        msg: str = "",
    ) -> None:
        """
        Assert tool call count is within range.

        Args:
            actual: List of actual tool calls
            expected_min: Minimum expected calls
            expected_max: Maximum expected calls (None = no limit)
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If count out of range
        """
        count = len(actual)

        if count < expected_min:
            raise ToolAssertionError(
                f"Too few tool calls. Expected at least {expected_min}, "
                f"got {count}. {msg}"
            )

        if expected_max is not None and count > expected_max:
            raise ToolAssertionError(
                f"Too many tool calls. Expected at most {expected_max}, "
                f"got {count}. Tools: {[c.name for c in actual]}. {msg}"
            )

    @staticmethod
    def assert_tool_args(
        actual: List[TrackedToolCall],
        tool_name: str,
        expected_args: Dict[str, Any],
        match_type: str = "contains",
        msg: str = "",
    ) -> None:
        """
        Assert tool was called with specific arguments.

        Args:
            actual: List of actual tool calls
            tool_name: Tool name to check
            expected_args: Expected arguments
            match_type: "exact", "contains", or "regex"
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If assertion fails
        """
        # Find the tool call
        tool_call = None
        for call in actual:
            if call.name == tool_name:
                tool_call = call
                break

        if tool_call is None:
            raise ToolAssertionError(
                f"Tool '{tool_name}' was not called. "
                f"Actual tools: {[c.name for c in actual]}. {msg}"
            )

        actual_args = tool_call.arguments

        for key, expected_value in expected_args.items():
            if key not in actual_args:
                raise ToolAssertionError(
                    f"Tool '{tool_name}' missing argument '{key}'. "
                    f"Actual args: {actual_args}. {msg}"
                )

            actual_value = actual_args[key]

            if match_type == "exact":
                if actual_value != expected_value:
                    raise ToolAssertionError(
                        f"Tool '{tool_name}' argument '{key}' mismatch. "
                        f"Expected: {expected_value}, Got: {actual_value}. {msg}"
                    )

            elif match_type == "contains":
                if isinstance(expected_value, str) and isinstance(actual_value, str):
                    # Handle special "contains()" syntax
                    if expected_value.startswith("contains(") and expected_value.endswith(")"):
                        search_term = expected_value[9:-1]
                        if search_term not in actual_value:
                            raise ToolAssertionError(
                                f"Tool '{tool_name}' argument '{key}' should contain '{search_term}'. "
                                f"Actual: {actual_value}. {msg}"
                            )
                    elif expected_value not in actual_value:
                        raise ToolAssertionError(
                            f"Tool '{tool_name}' argument '{key}' should contain '{expected_value}'. "
                            f"Actual: {actual_value}. {msg}"
                        )
                elif expected_value != actual_value:
                    raise ToolAssertionError(
                        f"Tool '{tool_name}' argument '{key}' mismatch. "
                        f"Expected: {expected_value}, Got: {actual_value}. {msg}"
                    )

            elif match_type == "regex":
                if not re.search(str(expected_value), str(actual_value)):
                    raise ToolAssertionError(
                        f"Tool '{tool_name}' argument '{key}' does not match regex '{expected_value}'. "
                        f"Actual: {actual_value}. {msg}"
                    )

    @staticmethod
    def assert_all_tools_successful(
        actual: List[TrackedToolCall],
        msg: str = "",
    ) -> None:
        """
        Assert all tool calls were successful.

        Args:
            actual: List of actual tool calls
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If any tool failed
        """
        failed = [c for c in actual if c.status != "success"]

        if failed:
            errors = [f"{c.name}: {c.error_message or 'unknown error'}" for c in failed]
            raise ToolAssertionError(
                f"Some tools failed: {errors}. {msg}"
            )

    @staticmethod
    def assert_response_contains(
        response: str,
        expected: Union[str, List[str]],
        msg: str = "",
    ) -> None:
        """
        Assert response contains expected text(s).

        Args:
            response: Response text
            expected: Expected text or list of texts
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If text not found
        """
        if isinstance(expected, str):
            expected = [expected]

        missing = [t for t in expected if t not in response]

        if missing:
            preview = response[:200] + "..." if len(response) > 200 else response
            raise ToolAssertionError(
                f"Response missing expected text: {missing}. "
                f"Response preview: {preview}. {msg}"
            )

    @staticmethod
    def assert_response_not_contains(
        response: str,
        forbidden: Union[str, List[str]],
        msg: str = "",
    ) -> None:
        """
        Assert response does NOT contain specified text(s).

        Args:
            response: Response text
            forbidden: Text or list of texts that should not appear
            msg: Optional message on failure

        Raises:
            ToolAssertionError: If forbidden text found
        """
        if isinstance(forbidden, str):
            forbidden = [forbidden]

        found = [t for t in forbidden if t in response]

        if found:
            raise ToolAssertionError(
                f"Response contains forbidden text: {found}. {msg}"
            )
