"""
Response Handler - Handles response streaming and finalization.

Handles:
- Final response streaming
- Token usage tracking
- History saving
- Analytics chain completion
- Plan extraction
"""

import asyncio
import json as json_module
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.agent.orchestration.types import (
    AgentEvent,
    AgentEventType,
    AgentState,
    TokenUsage,
)
from app.core.config import settings
from app.services.llm_client import (
    _get_http_client,
    _RETRY_DELAYS,
    _is_retryable,
    llm_client as central_llm_client,
)
from app.utils.token_counter import estimate_tokens

logger = logging.getLogger(__name__)

# Plan block pattern
_RE_PLAN_BLOCK = re.compile(r'\[PLAN\](.*?)\[/PLAN\]', re.DOTALL)


def build_usage_data(
    prompt_tokens: int,
    completion_tokens: int,
    finish_reason: str,
    model: str,
    state: AgentState,
    budget: Any = None,
) -> Dict[str, Any]:
    """
    Build standardized usage data dictionary.

    Args:
        prompt_tokens: Request prompt tokens
        completion_tokens: Request completion tokens
        finish_reason: LLM finish reason
        model: Model used
        state: Agent state for session totals
        budget: Optional token budget

    Returns:
        Usage data dictionary
    """
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "finish_reason": finish_reason,
        "model": model,
        "truncated": finish_reason == "length",
        "max_tokens": settings.llm.max_tokens,
        "session_total_prompt": state.total_prompt_tokens,
        "session_total_completion": state.total_completion_tokens,
        "budget": budget.get_status() if budget else None,
        "compaction_count": state.compaction_count,
    }


def track_token_usage(
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    request_type: str = "chat",
) -> None:
    """
    Track token usage to token tracker.

    Args:
        session_id: Session ID
        model: Model used
        input_tokens: Input tokens
        output_tokens: Output tokens
        request_type: Request type (chat, plan, max_iterations)
    """
    try:
        from app.services.token_tracker import get_token_tracker
        tracker = get_token_tracker()
        tracker.log_usage(
            session_id=session_id,
            model=model or settings.llm.default_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_type=request_type,
        )
    except Exception as e:
        logger.debug(f"[response_handler] Token tracking failed: {e}")


def strip_tool_markers(content: str) -> str:
    """
    Remove tool-call markers from content after parsing.

    Prevents [TOOL_CALLS]..., <tool_call>...</tool_call> etc.
    from appearing in the final output to the user.

    Args:
        content: Raw content with possible tool markers

    Returns:
        Cleaned content without tool markers
    """
    if not content:
        return content

    clean = content

    # [TOOL_CALLS] with JSON array
    clean = re.sub(r'\[TOOL_CALLS\]\s*\[.*?\]', '', clean, flags=re.DOTALL)

    # [TOOL_CALLS] with direct JSON
    clean = re.sub(r'\[TOOL_CALLS\]\w*\{[^}]*\}', '', clean, flags=re.DOTALL)

    # Generic [TOOL_CALLS] cleanup
    clean = re.sub(r'\[TOOL_CALLS\][^\[]*', '', clean, flags=re.DOTALL)

    # XML-style tool calls
    clean = re.sub(r'<tool_call>.*?</tool_call>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'<functioncall>.*?</functioncall>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'<function_calls>.*?</function_calls>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'<invoke>.*?</invoke>', '', clean, flags=re.DOTALL)

    # JSON blocks with tool structure
    clean = re.sub(
        r'```(?:json)?\s*\n\s*\{\s*"(?:name|tool|function)"\s*:.*?\}\s*\n```',
        '',
        clean,
        flags=re.DOTALL
    )

    # Reduce multiple newlines
    clean = re.sub(r'\n{3,}', '\n\n', clean)

    return clean.strip()


def extract_plan_block(response: str) -> Optional[str]:
    """
    Extract [PLAN]...[/PLAN] block from response.

    Args:
        response: LLM response text

    Returns:
        Plan text if found, None otherwise
    """
    match = _RE_PLAN_BLOCK.search(response)
    if match:
        return match.group(1).strip()
    return None


async def handle_planning_response(
    plan_response: str,
    state: AgentState,
    session_id: str,
    request_prompt_tokens: int,
    request_completion_tokens: int,
    last_finish_reason: str,
    last_model: str,
    budget: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Handle response in PLAN_THEN_EXECUTE mode.

    Args:
        plan_response: LLM response text
        state: Agent state
        session_id: Session ID
        request_prompt_tokens: Prompt tokens used
        request_completion_tokens: Completion tokens used
        last_finish_reason: Last finish reason
        last_model: Last model used
        budget: Token budget

    Yields:
        AgentEvent objects
    """
    # Update totals
    state.total_prompt_tokens += request_prompt_tokens
    state.total_completion_tokens += request_completion_tokens

    # Build usage data using helper
    usage_data = build_usage_data(
        prompt_tokens=request_prompt_tokens,
        completion_tokens=request_completion_tokens,
        finish_reason=last_finish_reason,
        model=last_model,
        state=state,
        budget=budget,
    )

    # Track tokens using helper
    track_token_usage(
        session_id=session_id,
        model=last_model,
        input_tokens=request_prompt_tokens,
        output_tokens=request_completion_tokens,
        request_type="plan",
    )

    # Extract plan block
    plan_text = extract_plan_block(plan_response)

    if plan_text:
        state.pending_plan = plan_text

        # Save to history
        state.messages_history.append({
            "role": "assistant",
            "content": plan_response
        })

        # Persist to disk
        try:
            from app.services.chat_store import save_chat
            save_chat(
                session_id=session_id,
                title=state.title or "Chat",
                messages_history=state.messages_history,
                mode=state.mode.value,
            )
        except Exception:
            pass

        yield AgentEvent(AgentEventType.PLAN_READY, {
            "plan": plan_text,
            "full_response": plan_response,
        })
        yield AgentEvent(AgentEventType.USAGE, usage_data)
        yield AgentEvent(AgentEventType.DONE, {
            "response": plan_response,
            "is_plan": True,
            "tool_calls_count": len(state.tool_calls_history),
            "usage": usage_data,
        })
    else:
        # No [PLAN] block - output as normal response (fallback)
        if plan_response:
            yield AgentEvent(AgentEventType.TOKEN, plan_response)
        state.messages_history.append({
            "role": "assistant",
            "content": plan_response
        })
        yield AgentEvent(AgentEventType.USAGE, usage_data)
        yield AgentEvent(AgentEventType.DONE, {
            "response": plan_response,
            "tool_calls_count": len(state.tool_calls_history),
            "usage": usage_data,
        })


async def finalize_response(
    assistant_response: str,
    state: AgentState,
    session_id: str,
    request_prompt_tokens: int,
    request_completion_tokens: int,
    last_finish_reason: str,
    last_model: str,
    budget: Any,
    analytics: Any = None,
    task_tracker: Any = None,
    processing_task_id: str = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Finalize response and emit events.

    Args:
        assistant_response: Final assistant response
        state: Agent state
        session_id: Session ID
        request_prompt_tokens: Prompt tokens used
        request_completion_tokens: Completion tokens used
        last_finish_reason: Last finish reason
        last_model: Last model used
        budget: Token budget
        analytics: Analytics logger
        task_tracker: Task tracker
        processing_task_id: Processing task ID

    Yields:
        AgentEvent objects
    """
    # Update totals
    state.total_prompt_tokens += request_prompt_tokens
    state.total_completion_tokens += request_completion_tokens

    # Save to history
    if assistant_response:
        state.messages_history.append({
            "role": "assistant",
            "content": assistant_response
        })

        # Persist to disk
        try:
            from app.services.chat_store import save_chat
            save_chat(
                session_id=session_id,
                title=state.title or "Chat",
                messages_history=state.messages_history,
                mode=state.mode.value,
            )
        except Exception:
            pass

    # Build usage data using helper
    usage_data = build_usage_data(
        prompt_tokens=request_prompt_tokens,
        completion_tokens=request_completion_tokens,
        finish_reason=last_finish_reason,
        model=last_model,
        state=state,
        budget=budget,
    )

    # Track tokens using helper
    track_token_usage(
        session_id=session_id,
        model=last_model,
        input_tokens=request_prompt_tokens,
        output_tokens=request_completion_tokens,
        request_type="chat",
    )

    yield AgentEvent(AgentEventType.USAGE, usage_data)

    # End analytics chain
    if analytics and analytics.enabled:
        try:
            await analytics.end_chain(
                status="resolved",
                response=assistant_response[:500] if assistant_response else ""
            )
        except Exception:
            pass

    # Complete task tracker
    if task_tracker and processing_task_id:
        try:
            await task_tracker.complete_step(processing_task_id, 0)
            await task_tracker.complete_task(processing_task_id)
        except Exception:
            pass

    # Handle pending PR analysis
    if state.pending_pr_analysis is not None:
        try:
            import asyncio
            pr_result = await asyncio.wait_for(
                state.pending_pr_analysis, timeout=30.0
            )
            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                "prNumber": state.pending_pr_number,
                **pr_result
            })
        except asyncio.TimeoutError:
            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                "prNumber": state.pending_pr_number,
                "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                "verdict": "comment",
                "findings": [],
                "summary": "Analyse-Timeout",
                "canApprove": state.pending_pr_state == "open"
            })
        except Exception as e:
            logger.warning(f"[response_handler] PR analysis error: {e}")
            yield AgentEvent(AgentEventType.WORKSPACE_PR_ANALYSIS, {
                "prNumber": state.pending_pr_number,
                "error": str(e),
                "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                "verdict": "comment",
                "findings": [],
                "canApprove": state.pending_pr_state == "open"
            })
        finally:
            state.pending_pr_analysis = None

    yield AgentEvent(AgentEventType.DONE, {
        "response": assistant_response,
        "tool_calls_count": len(state.tool_calls_history),
        "usage": usage_data
    })


async def stream_final_response_with_usage(
    messages: List[Dict],
    model: Optional[str] = None
) -> Dict:
    """
    Stream final response and track token usage.

    With retry logic for connection interruptions.

    Args:
        messages: Chat messages
        model: Optional model override

    Returns:
        Dict with "tokens" (AsyncGenerator) and "usage" (TokenUsage after completion)
    """
    # Model selection: User selection > Phase-specific > Default
    if model:
        selected_model = model
        logger.debug(f"[stream] Using user-selected model: {selected_model}")
    elif settings.llm.analysis_model:
        selected_model = settings.llm.analysis_model
        logger.debug(f"[stream] Using analysis_model: {selected_model}")
    else:
        selected_model = settings.llm.default_model
        logger.debug(f"[stream] Using default_model: {selected_model}")

    base_url = settings.llm.base_url.rstrip("/")

    headers = {"Content-Type": "application/json"}
    if settings.llm.api_key and settings.llm.api_key != "none":
        headers["Authorization"] = f"Bearer {settings.llm.api_key}"

    # Reasoning for analysis phase (streaming = final response)
    reasoning = settings.llm.analysis_reasoning
    stream_messages = messages
    if reasoning and reasoning in ("low", "medium", "high"):
        stream_messages = central_llm_client._inject_reasoning(messages, reasoning)
        logger.debug(f"[stream] Reasoning activated: {reasoning}")

    payload = {
        "model": selected_model,
        "messages": stream_messages,
        "temperature": settings.llm.temperature,
        "max_tokens": settings.llm.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True}
    }

    # Container for usage (filled during streaming)
    usage_container = {"usage": None, "finish_reason": ""}

    # Estimate prompt tokens (~4 chars per token)
    prompt_text = "".join(m.get("content", "") or "" for m in messages)
    estimated_prompt_tokens = len(prompt_text) // 4

    async def token_generator():
        completion_tokens = 0
        completion_chars = 0
        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                logger.debug(f"[stream] Retry {attempt} after {delay}s")
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json_module.loads(raw)

                            # Check for usage in final chunk (OpenAI stream_options)
                            if "usage" in chunk and chunk["usage"]:
                                usage_data = chunk["usage"]
                                usage_container["usage"] = TokenUsage(
                                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                                    completion_tokens=usage_data.get("completion_tokens", 0),
                                    total_tokens=usage_data.get("total_tokens", 0),
                                    finish_reason=usage_container["finish_reason"],
                                    model=selected_model,
                                    truncated=(usage_container["finish_reason"] == "length")
                                )

                            choices = chunk.get("choices", [])
                            if choices:
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                token = delta.get("content", "")

                                # Extract finish_reason
                                if choice.get("finish_reason"):
                                    usage_container["finish_reason"] = choice["finish_reason"]

                                if token:
                                    completion_chars += len(token)
                                    completion_tokens = completion_chars // 4  # ~4 chars per token
                                    yield token

                        except (ValueError, KeyError, IndexError):
                            continue
                return  # Successfully completed
            except Exception as e:
                last_exc = e
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    logger.debug(f"[stream] Interrupted: {e}, retry {attempt + 1}")
                    continue
                logger.debug(f"[stream] Error (no retry): {e}")
                break

        # Fallback: If no usage from server, estimate based on char count
        if not usage_container["usage"]:
            usage_container["usage"] = TokenUsage(
                prompt_tokens=estimated_prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=estimated_prompt_tokens + completion_tokens,
                finish_reason=usage_container["finish_reason"],
                model=selected_model,
                truncated=(usage_container["finish_reason"] == "length")
            )

    return {
        "tokens": token_generator(),
        "usage": usage_container  # Filled after streaming
    }
