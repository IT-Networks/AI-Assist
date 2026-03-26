"""
Tool Call Tracker for E2E Testing.

Extracts and tracks tool calls from AI-Assist events.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .models import TrackedToolCall, VerificationResult

logger = logging.getLogger(__name__)


class ToolCallTracker:
    """
    Extracts and tracks tool calls from AI-Assist events.

    Provides methods to:
    - Extract tool calls from /chat/sync events
    - Verify expected vs actual tool calls
    - Analyze tool call sequences
    """

    def __init__(self):
        """Initialize tracker."""
        self._calls: List[TrackedToolCall] = []

    def reset(self) -> None:
        """Reset tracked calls."""
        self._calls = []

    def extract_from_events(self, events: List[Dict[str, Any]]) -> List[TrackedToolCall]:
        """
        Extract tool calls from AI-Assist events.

        Args:
            events: List of events from /chat/sync response

        Returns:
            List of TrackedToolCall objects in order of execution
        """
        self._calls = []
        pending_calls: Dict[str, TrackedToolCall] = {}
        order = 0

        for event in events:
            event_type = event.get("type", "")
            data = event.get("data", {})

            if event_type == "tool_start":
                name = data.get("name", "unknown")
                args = data.get("arguments", {})

                # Parse arguments if string
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}

                call = TrackedToolCall(
                    name=name,
                    arguments=args,
                    status="pending",
                    order=order,
                )
                pending_calls[name] = call
                order += 1

            elif event_type == "tool_result":
                name = data.get("name", "")
                # AI-Assist uses "success" boolean, not "status" string
                success = data.get("success", data.get("status", "unknown"))
                result = data.get("result", "")

                # Find matching pending call
                if name in pending_calls:
                    call = pending_calls[name]
                    # Handle both boolean and string success indicators
                    if isinstance(success, bool):
                        call.status = "success" if success else "error"
                    else:
                        call.status = "success" if success in ("success", "ok", True) else "error"
                    call.result_preview = str(result)[:200] if result else ""

                    if call.status == "error":
                        call.error_message = data.get("error", str(result)[:100])

                    self._calls.append(call)
                    del pending_calls[name]
                else:
                    # Tool result without start (shouldn't happen)
                    tool_status = "success" if (success is True or success == "success") else "error"
                    self._calls.append(TrackedToolCall(
                        name=name,
                        arguments={},
                        status=tool_status,
                        result_preview=str(result)[:200] if result else "",
                        order=order,
                    ))
                    order += 1

            elif event_type == "tool_error":
                name = data.get("name", data.get("tool", "unknown"))
                error = data.get("error", "Unknown error")

                if name in pending_calls:
                    call = pending_calls[name]
                    call.status = "error"
                    call.error_message = error
                    self._calls.append(call)
                    del pending_calls[name]

        # Add any remaining pending calls
        for call in pending_calls.values():
            call.status = "incomplete"
            self._calls.append(call)

        # Sort by order
        self._calls.sort(key=lambda c: c.order)

        logger.debug(f"Extracted {len(self._calls)} tool calls: {[c.name for c in self._calls]}")
        return self._calls

    def get_tool_names(self) -> List[str]:
        """Get list of tool names in order."""
        return [c.name for c in self._calls]

    def get_tool_count(self) -> int:
        """Get total number of tool calls."""
        return len(self._calls)

    def get_successful_calls(self) -> List[TrackedToolCall]:
        """Get only successful tool calls."""
        return [c for c in self._calls if c.status == "success"]

    def get_failed_calls(self) -> List[TrackedToolCall]:
        """Get only failed tool calls."""
        return [c for c in self._calls if c.status == "error"]

    def find_call(self, tool_name: str) -> Optional[TrackedToolCall]:
        """Find first call with given tool name."""
        for call in self._calls:
            if call.name == tool_name:
                return call
        return None

    def find_all_calls(self, tool_name: str) -> List[TrackedToolCall]:
        """Find all calls with given tool name."""
        return [c for c in self._calls if c.name == tool_name]

    def verify_expected(
        self,
        expected_tools: List[str],
        strict_order: bool = False,
        allow_extra: bool = True,
    ) -> VerificationResult:
        """
        Verify actual tool calls against expected.

        Args:
            expected_tools: List of expected tool names
            strict_order: If True, order must match exactly
            allow_extra: If True, extra tools are allowed

        Returns:
            VerificationResult with pass/fail and details
        """
        actual_tools = self.get_tool_names()

        missing = [t for t in expected_tools if t not in actual_tools]
        unexpected = [t for t in actual_tools if t not in expected_tools] if not allow_extra else []

        order_correct = True
        if strict_order and not missing:
            # Check if expected tools appear in the correct order
            expected_indices = []
            for tool in expected_tools:
                try:
                    idx = actual_tools.index(tool)
                    expected_indices.append(idx)
                except ValueError:
                    pass

            order_correct = expected_indices == sorted(expected_indices)

        errors = []
        if missing:
            errors.append(f"Missing tools: {missing}")
        if unexpected:
            errors.append(f"Unexpected tools: {unexpected}")
        if not order_correct:
            errors.append(f"Order incorrect. Expected: {expected_tools}, Got: {actual_tools}")

        passed = len(missing) == 0 and len(unexpected) == 0 and order_correct

        return VerificationResult(
            passed=passed,
            expected_tools=expected_tools,
            actual_tools=actual_tools,
            missing_tools=missing,
            unexpected_tools=unexpected,
            order_correct=order_correct,
            errors=errors,
        )

    def to_sequence_string(self) -> str:
        """Get tool call sequence as readable string."""
        if not self._calls:
            return "(no tools called)"

        parts = []
        for call in self._calls:
            status_icon = "+" if call.status == "success" else "x"
            parts.append(f"{status_icon}{call.name}")

        return " -> ".join(parts)
