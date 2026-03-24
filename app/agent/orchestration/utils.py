"""
Orchestration Utilities - Shared helper functions.

Contains:
- Regex patterns for tool call parsing
- PR context detection and filtering
- Context limit handling
- Message trimming
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

import httpx

from app.core.config import settings
from app.utils.token_counter import estimate_tokens, estimate_messages_tokens, truncate_text_to_tokens

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for tool call parsing
RE_MISTRAL_COMPACT = re.compile(r'\[TOOL_CALLS\](\w+)(\{.*?\}|\[.*?\])', re.DOTALL)
RE_MISTRAL_STANDARD = re.compile(r'\[TOOL_CALLS\]\s*(\[.*?\])', re.DOTALL)
RE_XML_TOOL_CALL = re.compile(r'<tool_call>(.*?)</tool_call>', re.DOTALL)
RE_XML_FUNCTIONCALL = re.compile(r'<functioncall>(.*?)</functioncall>', re.DOTALL)
RE_XML_FUNCTION_CALLS = re.compile(r'<function_calls>(.*?)</function_calls>', re.DOTALL)
RE_XML_INVOKE = re.compile(r'<invoke>(.*?)</invoke>', re.DOTALL)
RE_JSON_BLOCK = re.compile(r'```(?:json)?\s*\n(.*?)\n```', re.DOTALL)
RE_INLINE_NAME = re.compile(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*\}')

# PR URL pattern (works with any git server)
RE_PR_URL = re.compile(
    r'(https?://[^\s/]+/[^\s/]+/[^\s/]+/pull/\d+)',
    re.IGNORECASE
)

# Tools allowed in PR context
PR_CONTEXT_ALLOWED_TOOLS: Set[str] = {
    "github_pr_details",
    "github_pr_diff",
    "github_get_file",
    "github_search_code",
    "github_commit_diff",
    "github_recent_commits",
    "github_list_branches",
    "sequential_thinking",
    "seq_think",
}

# Tools forbidden in PR context (local files)
PR_CONTEXT_FORBIDDEN_TOOLS: Set[str] = {
    "search_code",
    "read_file",
    "batch_read_files",
    "grep_content",
    "glob_files",
    "find_files",
    "search_java_class",
    "trace_java_references",
    "search_python_class",
}


def get_model_context_limit(model_id: str) -> int:
    """
    Get context limit for a model.

    Supports:
    - Exact matches: "mistral-678b" -> llm_context_limits["mistral-678b"]
    - Path-based IDs: "mistral/mistral_large" -> search for "mistral"
    - Fallback to default_context_limit

    Args:
        model_id: Model identifier

    Returns:
        Context limit in tokens
    """
    limits = settings.llm.llm_context_limits or {}
    default = settings.llm.default_context_limit or 32000

    if not model_id:
        return default

    # Exact match
    if model_id in limits:
        return limits[model_id]

    # Normalized match (lowercase)
    model_lower = model_id.lower()
    for key, value in limits.items():
        if key.lower() == model_lower:
            return value

    # Partial match
    for key, value in limits.items():
        key_lower = key.lower()
        model_base = model_lower.replace("/", "-").replace("_", "-")
        key_base = key_lower.replace("/", "-").replace("_", "-")

        if key_base in model_base or model_base in key_base:
            logger.debug(f"[utils] Partial match: {model_id} -> {key} ({value} tokens)")
            return value

        model_parts = set(model_base.split("-"))
        key_parts = set(key_base.split("-"))
        if model_parts & key_parts:
            logger.debug(f"[utils] Partial match via parts: {model_id} -> {key} ({value} tokens)")
            return value

    logger.debug(f"[utils] No match for {model_id}, using default: {default}")
    return default


def trim_messages_to_limit(messages: List[Dict], max_tokens: int) -> List[Dict]:
    """
    Trim message contents to respect context limit.

    Priority:
    1. System prompt unchanged
    2. Last user message unchanged
    3. Tool results truncated (oldest first, largest first)
    4. Older messages truncated

    Args:
        messages: List of message dicts
        max_tokens: Maximum token limit

    Returns:
        Trimmed messages list
    """
    if not messages:
        return messages or []

    current_tokens = estimate_messages_tokens(messages)
    if current_tokens <= max_tokens:
        return messages

    logger.warning(f"[utils] Context too large ({current_tokens} > {max_tokens} tokens), trimming...")

    # Create copy
    trimmed = [dict(m) for m in messages]
    tokens_to_remove = current_tokens - max_tokens + 1000  # Buffer

    # Find large tool results
    tool_results = []
    for i, msg in enumerate(trimmed):
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            content_tokens = estimate_tokens(content)
            if content_tokens > 500:
                tool_results.append((i, content_tokens, content))

    # Sort by size (largest first) and trim
    tool_results.sort(key=lambda x: x[1], reverse=True)

    for idx, orig_tokens, content in tool_results:
        if tokens_to_remove <= 0:
            break

        target_tokens = max(200, orig_tokens - tokens_to_remove)
        truncated = truncate_text_to_tokens(content, target_tokens)
        trimmed[idx]["content"] = truncated

        removed = orig_tokens - estimate_tokens(truncated)
        tokens_to_remove -= removed
        logger.debug(f"[utils] Tool result {idx} trimmed: -{removed} tokens")

    # If still too large: trim older assistant messages
    if tokens_to_remove > 0:
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "assistant" and i < len(trimmed) - 2:
                content = msg.get("content", "")
                if content and len(content) > 500:
                    truncated = content[:300] + "\n[... gekuerzt ...]"
                    removed = estimate_tokens(content) - estimate_tokens(truncated)
                    trimmed[i]["content"] = truncated
                    tokens_to_remove -= removed
                    if tokens_to_remove <= 0:
                        break

    final_tokens = estimate_messages_tokens(trimmed)
    logger.info(f"[utils] Context trimmed: {current_tokens} -> {final_tokens} tokens")
    return trimmed


def detect_pr_context(user_message: str) -> Optional[str]:
    """
    Detect if user message contains a PR URL.

    Supports:
    - github.com/owner/repo/pull/123
    - github.intern/owner/repo/pull/456
    - IP-based URLs: 192.168.1.100/owner/repo/pull/789

    Args:
        user_message: User's message

    Returns:
        PR URL if found, None otherwise
    """
    match = RE_PR_URL.search(user_message)
    if match:
        return match.group(1)
    return None


def filter_tools_for_pr_context(
    tool_schemas: List[Dict[str, Any]],
    pr_url: str
) -> List[Dict[str, Any]]:
    """
    Filter tool schemas for PR context.

    Removes local file tools and keeps only GitHub tools.

    Args:
        tool_schemas: All available tool schemas
        pr_url: Detected PR URL

    Returns:
        Filtered tool schemas (only PR-relevant tools)
    """
    filtered = []
    removed = []

    for schema in tool_schemas:
        tool_name = schema.get("function", {}).get("name", "")

        # Remove explicitly forbidden tools
        if tool_name in PR_CONTEXT_FORBIDDEN_TOOLS:
            removed.append(tool_name)
            continue

        # Keep GitHub tools and allowed tools
        if (tool_name.startswith("github_") or
            tool_name in PR_CONTEXT_ALLOWED_TOOLS or
            tool_name.startswith("mcp_")):
            filtered.append(schema)
        else:
            removed.append(tool_name)

    if removed:
        logger.info(
            f"[utils] PR-Context detected: {pr_url[:60]}... | "
            f"Removed {len(removed)} local tools, {len(filtered)} tools available"
        )
        logger.debug(f"[utils] Removed tools: {removed[:10]}...")

    return filtered


async def analyze_pr_for_workspace(
    pr_number: int,
    title: str,
    diff: str,
    state: str
) -> Dict[str, Any]:
    """
    Analyze a PR for the workspace panel.

    Makes a separate LLM call with structured output format
    for display in the PR panel (severity badges, findings, verdict).

    Args:
        pr_number: PR number
        title: PR title
        diff: PR diff content
        state: PR state (open, closed, merged)

    Returns:
        Dict with bySeverity, verdict, findings, canApprove
    """
    prompt = f"""Analysiere diesen Pull Request und gib eine strukturierte Bewertung.
WICHTIG: Alle Texte (title, description, summary) MÜSSEN auf DEUTSCH sein!

PR #{pr_number}: {title}
Status: {state}

DIFF:
```
{diff[:12000]}
```

Antworte NUR mit einem JSON-Objekt in diesem Format (keine Erklärungen):
{{
  "bySeverity": {{
    "critical": <Anzahl kritischer Issues>,
    "high": <Anzahl hoher Issues>,
    "medium": <Anzahl mittlerer Issues>,
    "low": <Anzahl niedriger Issues>,
    "info": <Anzahl Info-Hinweise>
  }},
  "verdict": "<approve|request_changes|comment>",
  "findings": [
    {{
      "severity": "<critical|high|medium|low|info>",
      "title": "<Kurzer deutscher Titel>",
      "file": "<Dateipfad>",
      "line": <Zeilennummer oder null>,
      "description": "<Kurze deutsche Beschreibung>",
      "codeSnippet": "<Betroffene Code-Zeilen wenn relevant, sonst null>"
    }}
  ],
  "summary": "<1-2 Sätze deutsche Zusammenfassung>"
}}

Bewertungskriterien:
- critical: Sicherheitslücken, Datenverlust-Risiko
- high: Bugs, Breaking Changes, Performance-Probleme
- medium: Code-Qualität, fehlende Tests, schlechte Patterns
- low: Style-Issues, Minor Improvements
- info: Dokumentation, Kommentare

Maximal 10 Findings. Bei closed/merged PRs: verdict="comment".
ALLE AUSGABEN AUF DEUTSCH!"""

    try:
        # Fast model for analysis
        model = settings.llm.tool_model or settings.llm.default_model
        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2500,
            "stream": False
        }

        async with httpx.AsyncClient(
            timeout=30,
            verify=settings.llm.verify_ssl
        ) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())

                # Validation and defaults
                by_severity = result.get("bySeverity", {})
                for sev in ["critical", "high", "medium", "low", "info"]:
                    by_severity[sev] = int(by_severity.get(sev, 0))

                verdict = result.get("verdict", "comment")
                if verdict not in ("approve", "request_changes", "comment"):
                    verdict = "comment"

                # For closed/merged always use comment
                if state in ("closed", "merged"):
                    verdict = "comment"

                return {
                    "bySeverity": by_severity,
                    "verdict": verdict,
                    "findings": result.get("findings", [])[:10],
                    "summary": result.get("summary", ""),
                    "canApprove": state == "open"
                }

            else:
                logger.warning(f"[utils] PR analysis: No JSON found in response: {content[:200]}")

    except Exception as e:
        logger.warning(f"[utils] PR workspace analysis failed: {e}")

    # Fallback on error
    return {
        "bySeverity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
        "verdict": "comment",
        "findings": [],
        "summary": "Analyse fehlgeschlagen",
        "canApprove": state == "open"
    }
