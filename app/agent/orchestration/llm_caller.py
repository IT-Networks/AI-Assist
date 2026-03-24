"""
LLM Caller - Handles LLM API calls with tool support.

Provides:
- Model selection logic (per-tool, phase-specific, user-selected)
- Retry logic for connection issues
- Token usage tracking
- Context summarization
"""

import logging
from typing import Any, Dict, List, Optional

from app.agent.orchestration.types import TokenUsage
from app.agent.orchestration.utils import (
    get_model_context_limit,
    trim_messages_to_limit,
)
from app.core.config import settings
from app.services.llm_client import (
    llm_client as central_llm_client,
    LLMResponse,
    TIMEOUT_TOOL,
    TIMEOUT_ANALYSIS,
)
from app.core.conversation_summarizer import get_summarizer
from app.utils.token_counter import estimate_messages_tokens
from app.services.token_tracker import get_token_tracker

logger = logging.getLogger(__name__)


async def call_llm_with_tools(
    messages: List[Dict],
    tools: List[Dict],
    model: Optional[str] = None,
    is_tool_phase: bool = True
) -> Dict:
    """
    Call LLM with tool definitions.

    With retry logic for connection interruptions and 5xx errors.

    Args:
        messages: Chat messages
        tools: Tool definitions (empty for final response)
        model: Explicit model (overrides automatic selection)
        is_tool_phase: True = Tool phase (fast model), False = Analysis phase (large model)

    Returns:
        Dict with content, tool_calls, native_tools, usage, finish_reason, reasoning
    """
    # Model selection: Per-tool > Phase-specific > Explicit (header dropdown) > Default
    selected_model = None

    # 1. Check per-tool model
    if is_tool_phase and settings.llm.tool_models:
        for msg in reversed(messages):
            if msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                for prev_msg in messages:
                    for tc in prev_msg.get("tool_calls", []):
                        if tc.get("id") == tool_call_id:
                            tool_name = tc["function"]["name"]
                            if tool_name in settings.llm.tool_models:
                                selected_model = settings.llm.tool_models[tool_name]
                                break
                    if selected_model:
                        break
                if selected_model:
                    break

    if not selected_model:
        # Priority: User selection > Phase-specific > Default
        if model:
            selected_model = model
            logger.debug(f"[model] Using user-selected model: {selected_model}")
        elif is_tool_phase and tools and settings.llm.tool_model:
            selected_model = settings.llm.tool_model
            logger.debug(f"[model] Using tool_model: {selected_model}")
        elif not is_tool_phase and settings.llm.analysis_model:
            selected_model = settings.llm.analysis_model
            logger.debug(f"[model] Using analysis_model: {selected_model}")
        else:
            selected_model = settings.llm.default_model
            logger.debug(f"[model] Using default_model: {selected_model}")

    # Phase-specific temperature: Tool phase deterministic, Analysis phase configurable
    is_tool_phase = bool(tools)
    if is_tool_phase:
        effective_temperature = settings.llm.tool_temperature
    else:
        cfg_analysis_temp = settings.llm.analysis_temperature
        effective_temperature = cfg_analysis_temp if cfg_analysis_temp >= 0 else settings.llm.temperature

    # Apply model-specific context limit
    model_limit = get_model_context_limit(selected_model)
    # Safety check: minimum 1000 token limit
    if not model_limit or model_limit < 1000:
        model_limit = settings.llm.default_context_limit or 32000
        logger.warning(f"[llm_caller] Invalid context limit for {selected_model}, using {model_limit}")

    # For long chats: invoke summarizer (summarizes older messages)
    if messages:
        try:
            current_tokens = estimate_messages_tokens(messages)
            if current_tokens > model_limit * 0.8:  # At 80% of limit
                summarizer = get_summarizer()
                summarized = await summarizer.summarize_if_needed(
                    messages,
                    target_tokens=int(model_limit * 0.7)  # Target: 70% of limit
                )
                # Only use if summarizer returns something
                if summarized:
                    messages = summarized
                    logger.info(f"[llm_caller] Summarizer active: {current_tokens} -> {estimate_messages_tokens(messages)} tokens")
        except Exception as e:
            logger.warning(f"[llm_caller] Summarizer/Token estimation failed: {e}")

    # If still too large: apply trim
    trimmed_messages = trim_messages_to_limit(messages, model_limit)

    # Estimate context size for logging
    estimated_tokens = estimate_messages_tokens(trimmed_messages)
    logger.debug(f"[llm_caller] LLM Request: ~{estimated_tokens} tokens, model={selected_model}, limit={model_limit}")

    # Timeout based on phase
    timeout = TIMEOUT_TOOL if is_tool_phase else TIMEOUT_ANALYSIS

    # Reasoning based on phase (GPT-OSS, o1, o3-mini support)
    reasoning = settings.llm.tool_reasoning if is_tool_phase else settings.llm.analysis_reasoning

    # Tool prefill: Check if activated for this model
    # 1. Model-specific override has priority
    # 2. Fallback to global setting
    use_prefill = False
    if tools and is_tool_phase:
        model_prefill = settings.llm.tool_prefill_models.get(selected_model)
        if model_prefill is not None:
            use_prefill = model_prefill
        else:
            use_prefill = settings.llm.use_tool_prefill

    # Central LLM call with retry logic
    try:
        response: LLMResponse = await central_llm_client.chat_with_tools(
            messages=trimmed_messages,
            tools=tools if tools else None,
            model=selected_model,
            temperature=effective_temperature,
            max_tokens=settings.llm.max_tokens,
            timeout=timeout,
            reasoning=reasoning or None,
            use_tool_prefill=use_prefill,
        )
    except Exception as e:
        # Add context info for better error messages
        raise RuntimeError(
            f"LLM error (context: ~{estimated_tokens} tokens, model: {selected_model}): {e}"
        ) from e

    # Debug for models with text-based tool calls
    if response.finish_reason == "tool_calls" and not response.tool_calls:
        logger.warning(
            "[llm_caller] finish_reason='tool_calls' but no tool_calls in message object! "
            "Model: %s, Content (first 500 chars): %s",
            selected_model, (response.content or '')[:500]
        )

    # Create TokenUsage from LLMResponse
    usage = TokenUsage(
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        total_tokens=response.prompt_tokens + response.completion_tokens,
        finish_reason=response.finish_reason,
        model=selected_model,
        truncated=(response.finish_reason == "length")
    )

    return {
        "content": response.content or "",
        "tool_calls": response.tool_calls,
        "native_tools": bool(response.tool_calls),
        "usage": usage,
        "finish_reason": response.finish_reason,
        "reasoning": reasoning or None,
    }


async def llm_callback_for_mcp(
    prompt: str,
    context: Optional[str] = None,
    session_id: str = "mcp-default"
) -> str:
    """
    LLM-Callback für MCP Sequential Thinking.

    Ermöglicht echtes LLM-Denken statt Template-Fallback.
    WICHTIG: Verwendet längeren Timeout (60s) da Analyse-Schritte
    mehr Zeit benötigen als einfache Klassifikation.

    Args:
        prompt: The prompt for the LLM
        context: Optional additional context
        session_id: Session ID for token tracking

    Returns:
        LLM response content or empty string on error
    """
    system_prompt = """Du bist ein strukturierter analytischer Denker.
Antworte IMMER im exakten Format das im Prompt angegeben ist.
Sei präzise und gib detaillierte Analyse-Schritte."""

    messages = [
        {"role": "system", "content": system_prompt}
    ]
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": prompt})

    try:
        # WICHTIG: Nicht chat_quick() verwenden (15s Timeout, 256 Tokens)!
        # Sequential Thinking braucht mehr Zeit und Tokens.
        response = await central_llm_client.chat_with_tools(
            messages=messages,
            temperature=0.2,  # Niedrig für konsistentes Format
            max_tokens=2048,  # Genug Tokens für detaillierte Schritte
            timeout=TIMEOUT_TOOL,  # 60s statt 15s
        )
        result = response.content or ""
        logger.debug(f"[MCP] LLM callback response length: {len(result)}")

        # Token-Tracking für MCP-Calls
        if hasattr(response, 'usage') and response.usage:
            try:
                tracker = get_token_tracker()
                model = getattr(response, 'model', None) or settings.llm.default_model
                tracker.log_usage(
                    session_id=session_id,
                    model=model,
                    input_tokens=response.usage.prompt_tokens or 0,
                    output_tokens=response.usage.completion_tokens or 0,
                    request_type="mcp",
                )
            except Exception as track_err:
                logger.debug(f"[MCP] Token tracking failed: {track_err}")

        return result
    except Exception as e:
        logger.warning(f"[MCP] LLM callback failed: {e}")
        return ""
