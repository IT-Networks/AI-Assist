"""
Orchestration Submodule - Modularized Agent Orchestrator Components.

This package contains the decomposed components of the Agent Orchestrator:
- types: Shared types (AgentEvent, AgentState, ToolCall, etc.)
- command_parser: Slash command and MCP force capability parsing
- context_builder: MCP enhancement and context building
- phase_runner: Research phase, sub-agent execution, task decomposition
- tool_executor: Tool execution logic (parallel & sequential)
- response_handler: Response streaming and finalization
- utils: Shared utility functions

Refactoring Status:
- Original orchestrator.py: ~4,700 LOC
- New modular structure: ~1,800 LOC across 7 modules
- Main orchestrator.py can import from this package
"""

# Types
from app.agent.orchestration.types import (
    AgentMode,
    AgentEventType,
    AgentEvent,
    AgentState,
    ToolCall,
    TokenUsage,
    MCP_EVENT_TYPE_MAPPING,
)

# Command Parser
from app.agent.orchestration.command_parser import (
    parse_mcp_force_capability,
    parse_slash_command,
    check_continue_markers,
    activate_skills_for_command,
    ParsedCommand,
    ContinueResult,
    BOOLEAN_FLAGS,
    VALUE_FLAGS,
)

# Context Builder
from app.agent.orchestration.context_builder import (
    run_prompt_enhancement,
    build_messages_context,
    extract_conversation_context,
    build_agent_instructions,
)

# Phase Runner
from app.agent.orchestration.phase_runner import (
    should_auto_research,
    run_research_phase,
    # run_sub_agents_phase removed in v2.31.5
    run_task_decomposition,
    run_forced_capability,
)

# Tool Executor
from app.agent.orchestration.tool_executor import (
    is_parallelizable_tool,
    parse_tool_calls,
    check_loop_prevention,
    execute_tools_parallel,
    execute_tool_sequential,
    truncate_result,
    PARALLELIZABLE_TOOL_PREFIXES,
    SEQUENTIAL_ONLY_TOOLS,
    MCP_CAPABILITY_TOOLS,
)

# Tool Parser (text-based tool call parsing for non-native models)
from app.agent.orchestration.tool_parser import (
    parse_text_tool_calls,
    REGEX_PATTERNS as TOOL_PARSER_REGEX,
)

# Response Handler
from app.agent.orchestration.response_handler import (
    strip_tool_markers,
    extract_plan_block,
    handle_planning_response,
    finalize_response,
    build_usage_data,
    track_token_usage,
    stream_final_response_with_usage,
)

# Utils
from app.agent.orchestration.utils import (
    get_model_context_limit,
    trim_messages_to_limit,
    detect_pr_context,
    filter_tools_for_pr_context,
    analyze_pr_for_workspace,
)

# LLM Caller
from app.agent.orchestration.llm_caller import (
    call_llm_with_tools,
    llm_callback_for_mcp,
)

# Workspace Events
from app.agent.orchestration.workspace_events import (
    build_code_change_event,
    build_sql_result_event,
    format_sql_result_for_agent,
    detect_language,
    generate_diff,
    EXT_TO_LANGUAGE,
)

__all__ = [
    # Types
    "AgentMode",
    "AgentEventType",
    "AgentEvent",
    "AgentState",
    "ToolCall",
    "TokenUsage",
    "MCP_EVENT_TYPE_MAPPING",
    # Command Parser
    "parse_mcp_force_capability",
    "parse_slash_command",
    "check_continue_markers",
    "activate_skills_for_command",
    "ParsedCommand",
    "ContinueResult",
    "BOOLEAN_FLAGS",
    "VALUE_FLAGS",
    # Context Builder
    "run_prompt_enhancement",
    "build_messages_context",
    "extract_conversation_context",
    "build_agent_instructions",
    # Phase Runner
    "should_auto_research",
    "run_research_phase",
    # "run_sub_agents_phase",  # removed in v2.31.5
    "run_task_decomposition",
    "run_forced_capability",
    # Tool Executor
    "is_parallelizable_tool",
    "parse_tool_calls",
    "check_loop_prevention",
    "execute_tools_parallel",
    "execute_tool_sequential",
    "truncate_result",
    "PARALLELIZABLE_TOOL_PREFIXES",
    "SEQUENTIAL_ONLY_TOOLS",
    "MCP_CAPABILITY_TOOLS",
    # Tool Parser
    "parse_text_tool_calls",
    "TOOL_PARSER_REGEX",
    # Response Handler
    "strip_tool_markers",
    "extract_plan_block",
    "handle_planning_response",
    "finalize_response",
    "build_usage_data",
    "track_token_usage",
    "stream_final_response_with_usage",
    # Utils
    "get_model_context_limit",
    "trim_messages_to_limit",
    "detect_pr_context",
    "filter_tools_for_pr_context",
    "analyze_pr_for_workspace",
    # LLM Caller
    "call_llm_with_tools",
    "llm_callback_for_mcp",
    # Workspace Events
    "build_code_change_event",
    "build_sql_result_event",
    "format_sql_result_for_agent",
    "detect_language",
    "generate_diff",
    "EXT_TO_LANGUAGE",
]
