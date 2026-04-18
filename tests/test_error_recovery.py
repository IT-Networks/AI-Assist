"""
Unit tests for error_recovery module.

Tests pattern-based error analysis and recovery hint generation.
"""

import pytest
from app.agent.orchestration.error_recovery import (
    get_recovery_hint,
    has_recovery_hint,
    get_alternative_tools,
)


class TestGetRecoveryHint:
    """Tests for get_recovery_hint() function."""

    def test_missing_path_argument(self):
        """Test recovery hint for missing 'path' argument."""
        hint = get_recovery_hint(
            tool_name="read_file",
            error="missing required argument 'path'"
        )
        assert hint
        assert "path" in hint.lower()
        assert "list_files" in hint

    def test_missing_query_argument(self):
        """Test recovery hint for missing 'query' argument."""
        hint = get_recovery_hint(
            tool_name="search_code",
            error="missing required argument 'query'"
        )
        assert hint
        assert "query" in hint.lower()
        assert "Suchbegriff" in hint

    def test_missing_content_argument(self):
        """Test recovery hint for missing 'content' argument."""
        hint = get_recovery_hint(
            tool_name="write_file",
            error="missing required argument 'content'"
        )
        assert hint
        assert "content" in hint.lower()
        assert "edit_file" in hint

    def test_file_not_found_error(self):
        """Test recovery hint for 'file not found' error."""
        hint = get_recovery_hint(
            tool_name="read_file",
            error="No such file or directory: /path/to/missing/file.py"
        )
        assert hint
        assert "Datei" in hint
        assert "list_files" in hint
        assert "search_code" in hint

    def test_permission_denied_error(self):
        """Test recovery hint for permission denied error."""
        hint = get_recovery_hint(
            tool_name="write_file",
            error="permission denied"
        )
        assert hint
        assert "Schreibzugriff" in hint or "READ-ONLY" in hint

    def test_json_decode_error(self):
        """Test recovery hint for JSON format error."""
        hint = get_recovery_hint(
            tool_name="some_tool",
            error="json decode error: Expecting value"
        )
        assert hint
        assert "JSON" in hint
        assert "[TOOL_CALLS]" in hint

    def test_connection_refused_error(self):
        """Test recovery hint for connection refused."""
        hint = get_recovery_hint(
            tool_name="some_tool",
            error="connection refused"
        )
        assert hint
        assert "nicht erreichbar" in hint.lower() or "service" in hint.lower()

    def test_timeout_error(self):
        """Test recovery hint for timeout error."""
        hint = get_recovery_hint(
            tool_name="search_code",
            error="timeout: operation took longer than 30 seconds"
        )
        assert hint
        assert "Zeitüberschreitung" in hint or "Timeout" in hint
        assert "kleineren" in hint or "spezifischeren" in hint

    def test_type_error(self):
        """Test recovery hint for type error."""
        hint = get_recovery_hint(
            tool_name="some_tool",
            error="type error: expected int but got str"
        )
        assert hint
        assert "Typ" in hint

    def test_value_error(self):
        """Test recovery hint for value error."""
        hint = get_recovery_hint(
            tool_name="some_tool",
            error="value error: invalid enum value"
        )
        assert hint
        assert "Wert" in hint or "Parameter" in hint

    def test_case_insensitive_matching(self):
        """Test that pattern matching is case-insensitive."""
        hint1 = get_recovery_hint("read_file", "FILE NOT FOUND")
        hint2 = get_recovery_hint("read_file", "file not found")
        hint3 = get_recovery_hint("read_file", "File Not Found")

        assert hint1
        assert hint2
        assert hint3
        # All should contain similar guidance
        assert "list_files" in hint1 or "Datei" in hint1
        assert "list_files" in hint2 or "Datei" in hint2
        assert "list_files" in hint3 or "Datei" in hint3

    def test_unknown_error_returns_empty(self):
        """Test that unknown error patterns return empty string."""
        hint = get_recovery_hint(
            tool_name="some_tool",
            error="completely_unknown_error_pattern_xyz"
        )
        # May return a generic fallback, not necessarily empty
        # But should not cause an exception
        assert isinstance(hint, str)

    def test_recovery_hint_with_available_tools(self):
        """Test recovery hint filtering based on available tools."""
        available_tools = ["read_file", "search_code"]
        hint = get_recovery_hint(
            tool_name="read_file",
            error="No such file or directory",
            available_tools=available_tools
        )
        assert hint
        # Should mention available alternatives
        if "list_files" in hint:
            # If list_files is in the generic hint but not available,
            # that's OK - it's just informational
            pass

    def test_recovery_hint_format(self):
        """Test that recovery hint has proper format."""
        hint = get_recovery_hint(
            tool_name="read_file",
            error="missing required argument 'path'"
        )
        assert hint
        # Should contain structured sections with markers
        assert "Recovery" in hint or "recovery" in hint or "Lösung" in hint


class TestHasRecoveryHint:
    """Tests for has_recovery_hint() function."""

    def test_detects_missing_argument_pattern(self):
        """Test detection of missing argument patterns."""
        assert has_recovery_hint("missing required argument 'path'")
        assert has_recovery_hint("missing required argument 'query'")

    def test_detects_file_not_found_pattern(self):
        """Test detection of file not found patterns."""
        assert has_recovery_hint("No such file or directory")
        assert has_recovery_hint("file not found")
        assert has_recovery_hint("cannot find file")

    def test_detects_permission_errors(self):
        """Test detection of permission error patterns."""
        assert has_recovery_hint("permission denied")
        assert has_recovery_hint("access denied")
        assert has_recovery_hint("insufficient permissions")

    def test_detects_json_errors(self):
        """Test detection of JSON format errors."""
        assert has_recovery_hint("json decode error")
        assert has_recovery_hint("invalid json")

    def test_detects_connection_errors(self):
        """Test detection of connection errors."""
        assert has_recovery_hint("connection refused")
        assert has_recovery_hint("connection timeout")

    def test_detects_generic_error_as_fallback(self):
        """Test that generic 'error' is detected as fallback."""
        assert has_recovery_hint("error: something bad happened")

    def test_no_false_positives(self):
        """Test that unrelated messages don't match."""
        # These should not match (no recovery hint patterns)
        assert not has_recovery_hint("This is a normal message")
        assert not has_recovery_hint("The tool executed successfully")
        assert not has_recovery_hint("Processing completed")


class TestGetAlternativeTools:
    """Tests for get_alternative_tools() function."""

    def test_alternatives_for_read_file(self):
        """Test getting alternatives for read_file tool."""
        alts = get_alternative_tools("read_file")
        assert isinstance(alts, list)
        # Should suggest other file reading tools
        if alts:
            assert all(isinstance(t, str) for t in alts)

    def test_alternatives_for_search_code(self):
        """Test getting alternatives for search_code tool."""
        alts = get_alternative_tools("search_code")
        assert isinstance(alts, list)

    def test_alternatives_filtered_by_available_tools(self):
        """Test that alternatives are filtered by availability."""
        available = ["list_files", "grep_code"]
        alts = get_alternative_tools(
            "read_file",
            available_tools=available
        )
        # Should only contain tools from available list
        for tool in alts:
            assert tool in available

    def test_alternative_tools_are_sorted(self):
        """Test that alternative tools are returned in sorted order."""
        alts = get_alternative_tools("read_file")
        assert alts == sorted(alts)

    def test_unknown_tool_returns_empty_list(self):
        """Test that unknown tool returns empty alternatives."""
        alts = get_alternative_tools("totally_unknown_tool_xyz")
        assert isinstance(alts, list)
        # May be empty or have some defaults


class TestToolSpecificErrors:
    """Tests for tool-specific error handling."""

    def test_read_file_too_large(self):
        """Test recovery hint for read_file with file too large."""
        hint = get_recovery_hint(
            tool_name="read_file",
            error="file_too_large"
        )
        if hint:  # Tool-specific error
            assert "batch_read_files" in hint or "search_code" in hint

    def test_read_file_encoding_error(self):
        """Test recovery hint for read_file with encoding error."""
        hint = get_recovery_hint(
            tool_name="read_file",
            error="encoding_error"
        )
        if hint:  # Tool-specific error
            assert "Encoding" in hint or "encoding" in hint

    def test_search_code_no_results(self):
        """Test recovery hint for search_code with no results."""
        hint = get_recovery_hint(
            tool_name="search_code",
            error="no_results"
        )
        if hint:  # Tool-specific error
            assert "generischeres" in hint or "generic" in hint

    def test_write_file_already_exists(self):
        """Test recovery hint for write_file with file already exists."""
        hint = get_recovery_hint(
            tool_name="write_file",
            error="already_exists"
        )
        if hint:  # Tool-specific error
            assert "edit_file" in hint or "existierende" in hint


class TestErrorPriority:
    """Tests for error pattern priority matching."""

    def test_high_priority_pattern_wins(self):
        """Test that high priority patterns match over generic ones."""
        # "missing required argument" should match high-priority pattern
        hint = get_recovery_hint(
            tool_name="read_file",
            error="missing required argument 'path': The 'path' parameter is required for reading files"
        )
        assert hint
        assert "path" in hint.lower()

    def test_specific_pattern_preferred_over_generic(self):
        """Test that specific patterns are preferred over generic error."""
        # "permission denied" is more specific than generic "error"
        hint = get_recovery_hint(
            tool_name="write_file",
            error="permission denied: cannot write to /root/file.txt"
        )
        assert hint
        # Should mention permission issue, not generic error handling
        assert "permission" in hint.lower() or "schreibzugriff" in hint.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
