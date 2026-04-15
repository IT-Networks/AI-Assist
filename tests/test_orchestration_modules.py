"""
Tests for orchestration submodules.

Tests the modularized components extracted from orchestrator.py.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from dataclasses import dataclass

from app.agent.orchestration import (
    # Types
    AgentMode,
    AgentEventType,
    AgentEvent,
    AgentState,
    ToolCall,
    TokenUsage,
    MCP_EVENT_TYPE_MAPPING,
    # Command Parser
    parse_mcp_force_capability,
    parse_slash_command,
    check_continue_markers,
    ParsedCommand,
    ContinueResult,
    BOOLEAN_FLAGS,
    VALUE_FLAGS,
    # Tool Executor
    is_parallelizable_tool,
    parse_tool_calls,
    check_loop_prevention,
    truncate_result,
    PARALLELIZABLE_TOOL_PREFIXES,
    SEQUENTIAL_ONLY_TOOLS,
    # Tool Parser
    parse_text_tool_calls,
    # Response Handler
    strip_tool_markers,
    extract_plan_block,
    build_usage_data,
    track_token_usage,
    # Context Builder
    extract_conversation_context,
    build_agent_instructions,
    # Utils
    get_model_context_limit,
    detect_pr_context,
    filter_tools_for_pr_context,
)


class TestTypes:
    """Tests for orchestration types."""

    def test_agent_mode_values(self):
        """Test AgentMode enum values."""
        assert AgentMode.READ_ONLY.value == "read_only"
        assert AgentMode.WRITE_WITH_CONFIRM.value == "write_with_confirm"
        assert AgentMode.AUTONOMOUS.value == "autonomous"
        assert AgentMode.PLAN_THEN_EXECUTE.value == "plan_then_execute"
        assert AgentMode.DEBUG.value == "debug"

    def test_agent_event_type_values(self):
        """Test AgentEventType enum values."""
        assert AgentEventType.TOKEN.value == "token"
        assert AgentEventType.TOOL_START.value == "tool_start"
        assert AgentEventType.DONE.value == "done"

    def test_agent_event_to_dict(self):
        """Test AgentEvent.to_dict()."""
        event = AgentEvent(AgentEventType.TOKEN, "hello")
        d = event.to_dict()
        assert d["type"] == "token"
        assert d["data"] == "hello"

    def test_token_usage_defaults(self):
        """Test TokenUsage default values."""
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.finish_reason == ""
        assert usage.model == ""
        assert usage.truncated is False

    def test_agent_state_defaults(self):
        """Test AgentState default values."""
        state = AgentState(session_id="test-123")
        assert state.session_id == "test-123"
        assert state.mode == AgentMode.READ_ONLY
        assert state.project_id is None
        assert len(state.active_skill_ids) == 0
        assert len(state.messages_history) == 0

    def test_mcp_event_type_mapping(self):
        """Test MCP_EVENT_TYPE_MAPPING constant."""
        # Check uppercase events
        assert MCP_EVENT_TYPE_MAPPING["MCP_START"] == AgentEventType.MCP_START
        assert MCP_EVENT_TYPE_MAPPING["MCP_COMPLETE"] == AgentEventType.MCP_COMPLETE
        # Check lowercase events
        assert MCP_EVENT_TYPE_MAPPING["mcp_start"] == AgentEventType.MCP_START
        assert MCP_EVENT_TYPE_MAPPING["mcp_error"] == AgentEventType.MCP_ERROR
        # Check workspace events
        assert MCP_EVENT_TYPE_MAPPING["workspace_code_change"] == AgentEventType.WORKSPACE_CODE_CHANGE
        assert MCP_EVENT_TYPE_MAPPING["workspace_pr"] == AgentEventType.WORKSPACE_PR


class TestCommandParser:
    """Tests for command parser module."""

    def test_parse_mcp_force_capability_match(self):
        """Test MCP force capability detection."""
        cap, msg = parse_mcp_force_capability("[MCP:analyze] explain this code")
        assert cap == "analyze"
        assert msg == "explain this code"

    def test_parse_mcp_force_capability_no_match(self):
        """Test no MCP force when pattern not present."""
        cap, msg = parse_mcp_force_capability("normal message")
        assert cap is None
        assert msg == "normal message"

    def test_parse_slash_command_simple(self):
        """Test simple slash command."""
        result = parse_slash_command("/analyze src/")
        assert result is not None
        assert result.command_name == "analyze"
        assert result.query == "src/"
        assert result.flags == {}

    def test_parse_slash_command_with_sc_prefix(self):
        """Test SuperClaude-style command."""
        result = parse_slash_command("/sc:brainstorm login feature")
        assert result is not None
        assert result.command_name == "brainstorm"
        assert result.query == "login feature"

    def test_parse_slash_command_with_boolean_flags(self):
        """Test boolean flag parsing."""
        result = parse_slash_command("/test --coverage --verbose src/")
        assert result is not None
        assert result.command_name == "test"
        assert result.flags.get("coverage") is True
        assert result.flags.get("verbose") is True
        assert result.query == "src/"

    def test_parse_slash_command_with_value_flags(self):
        """Test value flag parsing."""
        result = parse_slash_command("/analyze --depth deep --type quality src/")
        assert result is not None
        assert result.flags.get("depth") == "deep"
        assert result.flags.get("type") == "quality"
        assert result.query == "src/"

    def test_parse_slash_command_no_match(self):
        """Test non-slash command returns None."""
        result = parse_slash_command("normal message")
        assert result is None

    def test_parsed_command_transformed_message(self):
        """Test transformed message generation."""
        cmd = ParsedCommand(
            command_name="test",
            query="src/",
            flags={"coverage": True},
            original_message="/test --coverage src/"
        )
        transformed = cmd.get_transformed_message()
        assert "[COMMAND: /test]" in transformed
        assert "[FLAGS: --coverage]" in transformed
        assert "src/" in transformed

    def test_check_continue_markers_continue(self):
        """Test [CONTINUE] marker detection."""
        from app.agent.constants import ControlMarkers
        result = check_continue_markers(ControlMarkers.CONTINUE)
        assert result.is_continue is True
        assert result.transformed_message is not None


class TestToolExecutor:
    """Tests for tool executor module."""

    def test_is_parallelizable_tool_read(self):
        """Test read tools are parallelizable."""
        assert is_parallelizable_tool("read_file") is True
        assert is_parallelizable_tool("search_code") is True
        assert is_parallelizable_tool("github_pr_diff") is True

    def test_is_parallelizable_tool_write(self):
        """Test write tools are not parallelizable."""
        assert is_parallelizable_tool("write_file") is False
        assert is_parallelizable_tool("edit_file") is False
        assert is_parallelizable_tool("execute_command") is False

    def test_is_parallelizable_tool_mcp(self):
        """Test MCP tools are not parallelizable."""
        assert is_parallelizable_tool("mcp_analyze") is False
        assert is_parallelizable_tool("sequential_thinking") is False

    def test_parse_tool_calls(self):
        """Test tool call parsing."""
        state = AgentState(session_id="test")
        raw_calls = [
            {
                "id": "call_1",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "test.py"}'
                }
            }
        ]
        parsed = parse_tool_calls(raw_calls, state)
        assert len(parsed) == 1
        assert parsed[0].name == "read_file"
        assert parsed[0].arguments == {"path": "test.py"}

    def test_check_loop_prevention_read_file(self):
        """Test read_file loop prevention."""
        state = AgentState(session_id="test")
        tc = ToolCall(id="1", name="read_file", arguments={"path": "test.py"})

        # First and second read allowed
        assert check_loop_prevention(tc, state) is None
        assert check_loop_prevention(tc, state) is None

        # Third read blocked
        warning = check_loop_prevention(tc, state)
        assert warning is not None
        assert "bereits" in warning

    def test_check_loop_prevention_write_file(self):
        """Test write_file loop prevention (stricter)."""
        state = AgentState(session_id="test")
        tc = ToolCall(id="1", name="write_file", arguments={"path": "test.py"})

        # First write allowed
        assert check_loop_prevention(tc, state) is None

        # Second write blocked
        warning = check_loop_prevention(tc, state)
        assert warning is not None
        assert "STOP" in warning

    def test_truncate_result_short(self):
        """Test short results not truncated."""
        result = truncate_result("short text", max_chars=100)
        assert result == "short text"

    def test_truncate_result_long(self):
        """Test long results truncated."""
        long_text = "x" * 30000
        result = truncate_result(long_text, max_chars=1000)
        assert len(result) < len(long_text)
        assert "gekuerzt" in result

    def test_truncate_result_pr_tool(self):
        """Test PR tools get special truncation."""
        pr_content = "\n".join([f"Line {i}" for i in range(50)])
        result = truncate_result(pr_content, tool_name="github_pr_diff")
        # Should only have first 15 lines + info message
        assert "Workspace-Panel" in result
        assert "Line 0" in result
        assert "Line 20" not in result  # Should be truncated


class TestResponseHandler:
    """Tests for response handler module."""

    def test_strip_tool_markers_tool_calls(self):
        """Test [TOOL_CALLS] marker removal."""
        content = 'Hello [TOOL_CALLS] [{"name": "test"}] world'
        clean = strip_tool_markers(content)
        assert "[TOOL_CALLS]" not in clean
        assert "Hello" in clean
        assert "world" in clean

    def test_strip_tool_markers_xml(self):
        """Test XML tool marker removal."""
        content = 'Hello <tool_call>{"name": "test"}</tool_call> world'
        clean = strip_tool_markers(content)
        assert "<tool_call>" not in clean
        assert "Hello" in clean

    def test_strip_tool_markers_empty(self):
        """Test empty content handled."""
        assert strip_tool_markers("") == ""
        assert strip_tool_markers(None) is None

    def test_extract_plan_block_found(self):
        """Test plan block extraction."""
        response = "Some text [PLAN]\n1. Step one\n2. Step two\n[/PLAN] more text"
        plan = extract_plan_block(response)
        assert plan is not None
        assert "Step one" in plan
        assert "Step two" in plan

    def test_extract_plan_block_not_found(self):
        """Test no plan block returns None."""
        response = "Normal response without plan"
        plan = extract_plan_block(response)
        assert plan is None

    def test_build_usage_data_basic(self):
        """Test build_usage_data creates correct structure."""
        state = AgentState(session_id="test")
        state.total_prompt_tokens = 100
        state.total_completion_tokens = 50
        state.compaction_count = 1

        usage = build_usage_data(
            prompt_tokens=100,
            completion_tokens=50,
            finish_reason="stop",
            model="test-model",
            state=state,
            budget=None,
        )

        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50
        assert usage["total_tokens"] == 150
        assert usage["finish_reason"] == "stop"
        assert usage["model"] == "test-model"
        assert usage["truncated"] is False
        assert usage["session_total_prompt"] == 100
        assert usage["session_total_completion"] == 50
        assert usage["compaction_count"] == 1
        assert usage["budget"] is None

    def test_build_usage_data_truncated(self):
        """Test truncated flag set when finish_reason is length."""
        state = AgentState(session_id="test")
        usage = build_usage_data(
            prompt_tokens=100,
            completion_tokens=50,
            finish_reason="length",
            model="test-model",
            state=state,
            budget=None,
        )
        assert usage["truncated"] is True

    def test_track_token_usage_no_error(self):
        """Test track_token_usage doesn't raise on missing tracker."""
        # Should not raise even if tracker is not available
        track_token_usage(
            session_id="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            request_type="chat",
        )
        # No assertion - just verify no exception


class TestContextBuilder:
    """Tests for context builder module."""

    def test_extract_conversation_context_basic(self):
        """Test basic conversation context extraction."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        context = extract_conversation_context(messages)
        assert context is not None
        assert "User: Hello" in context
        assert "Assistant: Hi there!" in context

    def test_extract_conversation_context_no_history(self):
        """Test with no conversation history."""
        messages = [{"role": "user", "content": "First message"}]
        context = extract_conversation_context(messages)
        assert context is None

    def test_extract_conversation_context_truncates_long_content(self):
        """Test that long content is truncated."""
        long_content = "x" * 500
        messages = [
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": "Short response"},
            {"role": "user", "content": "Follow up"},
        ]
        context = extract_conversation_context(messages)
        assert context is not None
        assert "..." in context  # Should be truncated
        assert len(context) < 500  # Should be shorter than original

    def test_extract_conversation_context_ignores_system(self):
        """Test that system messages are ignored."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "Follow up"},
        ]
        context = extract_conversation_context(messages)
        assert context is not None
        assert "System prompt" not in context
        assert "User message" in context

    def test_build_agent_instructions_read_only(self):
        """Test agent instructions for READ_ONLY mode."""
        instructions = build_agent_instructions(AgentMode.READ_ONLY)
        assert "Agent-Anweisungen" in instructions
        assert "MODUS: Nur Lesen" in instructions
        assert "search_code" in instructions
        assert "read_file" in instructions

    def test_build_agent_instructions_write_mode(self):
        """Test agent instructions for WRITE_WITH_CONFIRM mode."""
        instructions = build_agent_instructions(AgentMode.WRITE_WITH_CONFIRM)
        assert "MODUS: Schreiben mit Bestätigung" in instructions
        assert "write_file" in instructions
        assert "batch_write_files" in instructions

    def test_build_agent_instructions_plan_mode(self):
        """Test agent instructions for PLAN_THEN_EXECUTE mode."""
        # Planning phase (not approved)
        instructions = build_agent_instructions(AgentMode.PLAN_THEN_EXECUTE, plan_approved=False)
        assert "MODUS: Planungsphase" in instructions
        assert "[PLAN]" in instructions

        # Execution phase (approved)
        instructions = build_agent_instructions(AgentMode.PLAN_THEN_EXECUTE, plan_approved=True)
        assert "MODUS: Ausführungsphase" in instructions

    def test_build_agent_instructions_autonomous(self):
        """Test agent instructions for AUTONOMOUS mode."""
        instructions = build_agent_instructions(AgentMode.AUTONOMOUS)
        assert "MODUS: Autonom" in instructions
        assert "ohne Bestätigung" in instructions


class TestUtils:
    """Tests for orchestration utilities."""

    def test_detect_pr_context_github(self):
        """Test GitHub PR URL detection."""
        msg = "Please review https://github.com/owner/repo/pull/123"
        url = detect_pr_context(msg)
        assert url == "https://github.com/owner/repo/pull/123"

    def test_detect_pr_context_internal(self):
        """Test internal GitHub PR URL detection."""
        msg = "Check https://github.internal/team/project/pull/456"
        url = detect_pr_context(msg)
        assert url == "https://github.internal/team/project/pull/456"

    def test_detect_pr_context_no_match(self):
        """Test no PR URL returns None."""
        msg = "Normal message without PR"
        url = detect_pr_context(msg)
        assert url is None

    def test_filter_tools_for_pr_context(self):
        """Test tool filtering for PR context."""
        tools = [
            {"function": {"name": "github_pr_diff"}},
            {"function": {"name": "read_file"}},
            {"function": {"name": "github_get_file"}},
            {"function": {"name": "search_code"}},
        ]
        filtered = filter_tools_for_pr_context(tools, "https://github.com/o/r/pull/1")

        # GitHub tools kept
        names = [t["function"]["name"] for t in filtered]
        assert "github_pr_diff" in names
        assert "github_get_file" in names

        # Local tools removed
        assert "read_file" not in names
        assert "search_code" not in names


class TestFlagConstants:
    """Tests for flag constants."""

    def test_boolean_flags(self):
        """Test boolean flags are defined."""
        assert "ultrathink" in BOOLEAN_FLAGS
        assert "coverage" in BOOLEAN_FLAGS
        assert "verbose" in BOOLEAN_FLAGS

    def test_value_flags(self):
        """Test value flags are defined."""
        assert "depth" in VALUE_FLAGS
        assert "type" in VALUE_FLAGS
        assert "format" in VALUE_FLAGS

    def test_no_overlap(self):
        """Test no overlap between boolean and value flags."""
        overlap = BOOLEAN_FLAGS & VALUE_FLAGS
        assert len(overlap) == 0


class TestToolPrefixes:
    """Tests for tool prefix constants."""

    def test_parallelizable_prefixes(self):
        """Test parallelizable tool prefixes."""
        assert "search_" in PARALLELIZABLE_TOOL_PREFIXES
        assert "read_" in PARALLELIZABLE_TOOL_PREFIXES
        assert "github_" in PARALLELIZABLE_TOOL_PREFIXES

    def test_sequential_only_tools(self):
        """Test sequential-only tools."""
        assert "write_file" in SEQUENTIAL_ONLY_TOOLS
        assert "edit_file" in SEQUENTIAL_ONLY_TOOLS
        assert "execute_command" in SEQUENTIAL_ONLY_TOOLS


class TestToolParser:
    """Tests for tool parser module."""

    def test_parse_text_tool_calls_mistral_format(self):
        """Test Mistral [TOOL_CALLS] format parsing."""
        content = '[TOOL_CALLS] [{"name": "read_file", "arguments": {"path": "test.py"}}]'
        tools = [{"function": {"name": "read_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "read_file"

    def test_parse_text_tool_calls_xml_format(self):
        """Test XML <tool_call> format parsing."""
        content = '<tool_call>{"name": "search_code", "arguments": {"query": "test"}}</tool_call>'
        tools = [{"function": {"name": "search_code"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "search_code"

    def test_parse_text_tool_calls_empty_content(self):
        """Test empty content returns empty list."""
        assert parse_text_tool_calls("", []) == []
        assert parse_text_tool_calls(None, []) == []

    def test_parse_text_tool_calls_no_markers(self):
        """Test content without tool markers returns empty list."""
        content = "This is just regular text without any tool calls."
        result = parse_text_tool_calls(content, [])
        assert result == []

    def test_parse_text_tool_calls_validates_tool_names(self):
        """Test that only known tool names are parsed."""
        content = '[TOOL_CALLS] [{"name": "unknown_tool", "arguments": {}}]'
        tools = [{"function": {"name": "read_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 0  # unknown_tool not in available tools

    def test_parse_text_tool_calls_functioncall_format(self):
        """Test OpenHermes <functioncall> format parsing."""
        content = '<functioncall>{"name": "list_files", "arguments": {"dir": "/src"}}</functioncall>'
        tools = [{"function": {"name": "list_files"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "list_files"

    def test_parse_paren_call_simple(self):
        """Paren-style Python call is normalized to a tool call."""
        content = 'write_file("path": "/tmp/a.py", "content": "print(1)")'
        tools = [{"function": {"name": "write_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "write_file"
        import json as _json
        args = _json.loads(result[0]["function"]["arguments"])
        assert args["path"] == "/tmp/a.py"
        assert args["content"] == "print(1)"

    def test_parse_paren_call_with_braces(self):
        """Paren-style with braces around args also parses."""
        content = 'read_file({"path": "/tmp/b.py"})'
        tools = [{"function": {"name": "read_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "read_file"

    def test_parse_paren_call_ignores_unknown_tools(self):
        """Paren-style only matches when the function name is a known tool."""
        content = 'print("path": "x")'
        tools = [{"function": {"name": "write_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert result == []

    def test_detect_malformed_tool_attempt_paren(self):
        """Clearly-attempted paren call that fails to parse is flagged."""
        from app.agent.orchestration.tool_parser import detect_malformed_tool_attempt
        # Malformed: period instead of comma, unmatched brace
        content = 'write_file("path":"C:/a.py". "content": "broken\nstuff"}'
        detected = detect_malformed_tool_attempt(content)
        assert detected is not None
        snippet, hints = detected
        assert 'funcname(...)' in hints

    def test_detect_malformed_returns_none_for_plain_text(self):
        """Plain prose is NOT flagged as malformed."""
        from app.agent.orchestration.tool_parser import detect_malformed_tool_attempt
        content = "Die letzte Datei-Operation wurde bestaetigt und ausgefuehrt."
        assert detect_malformed_tool_attempt(content) is None

    def test_malformed_paren_returns_empty_list(self):
        """Parser returns [] (not partial garbage) for malformed paren call."""
        content = 'write_file("path":"C:/a.py". broken'
        tools = [{"function": {"name": "write_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert result == []

    def test_mistral_compact_with_nested_json(self):
        """Nested {...} inside args is captured in full (balanced-brace scanner)."""
        import json as _json
        content = '[TOOL_CALLS]edit_file{"path": "a.py", "changes": {"line": 5, "text": "x"}}'
        tools = [{"function": {"name": "edit_file"}}]
        result = parse_text_tool_calls(content, tools)
        assert len(result) == 1
        args = _json.loads(result[0]["function"]["arguments"])
        assert args["changes"]["line"] == 5
        assert args["changes"]["text"] == "x"
