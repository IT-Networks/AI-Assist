"""
Tool Parser - Parses tool calls from text content.

Handles text-based tool call parsing for models that don't support
native function calling (e.g., Mistral, Qwen, OpenHermes).

Supported formats:
1. Mistral: [TOOL_CALLS] [{"name": "func", "arguments": {...}}]
2. XML: <tool_call>{"name": "func", "arguments": {...}}</tool_call>
3. OpenHermes: <functioncall>{"name": "func", "arguments": {...}}</functioncall>
4. JSON-Block: ```json\n{"tool": "func", ...}\n```
5. Paren-Call: write_file("path": "...", "content": "...")  [Python-style; auto-normalized]
"""

import json
import logging
import re
import uuid
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MalformedToolCall(Exception):
    """Raised when content clearly attempted a tool call but could not be parsed.

    Orchestrator should catch this and surface a clear error to the user instead
    of silently leaking the raw text into chat output.
    """

    def __init__(self, snippet: str, hints: List[str]):
        self.snippet = snippet
        self.hints = hints
        super().__init__(
            f"Malformed tool call detected (hints={hints}): {snippet[:160]!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Pre-compiled Regex Patterns (Performance: avoid re-compilation on each call)
# ══════════════════════════════════════════════════════════════════════════════
_RE_MISTRAL_COMPACT = re.compile(r'\[TOOL_CALLS\](\w+)(\{.*?\}|\[.*?\])', re.DOTALL)
_RE_MISTRAL_STANDARD = re.compile(r'\[TOOL_CALLS\]\s*(\[.*?\])', re.DOTALL)
_RE_XML_TOOL_CALL = re.compile(r'<tool_call>(.*?)</tool_call>', re.DOTALL)
_RE_XML_FUNCTIONCALL = re.compile(r'<functioncall>(.*?)</functioncall>', re.DOTALL)
_RE_XML_FUNCTION_CALLS = re.compile(r'<function_calls>(.*?)</function_calls>', re.DOTALL)
_RE_XML_INVOKE = re.compile(r'<invoke>(.*?)</invoke>', re.DOTALL)
_RE_JSON_BLOCK = re.compile(r'```(?:json)?\s*\n(.*?)\n```', re.DOTALL)
_RE_INLINE_NAME = re.compile(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*\}')

# Paren-Call Format: funcname("key": value, ...)  OR  funcname({"key": value})
# Matches identifier followed by '(' that contains at least one quoted key
_RE_PAREN_CALL_HEAD = re.compile(r'\b([a-zA-Z_]\w{2,})\s*\(\s*\{?\s*"[\w\-]+"\s*:', re.DOTALL)

# Debug hint patterns
_RE_HINT_TOOL = re.compile(r'\[TOOL', re.IGNORECASE)
_RE_HINT_XML_TOOL = re.compile(r'<tool', re.IGNORECASE)
_RE_HINT_XML_FUNC = re.compile(r'<function', re.IGNORECASE)
_RE_HINT_NAME = re.compile(r'"name"\s*:')
_RE_HINT_TOOL_KEY = re.compile(r'"tool"\s*:')
_RE_HINT_PAREN_CALL = re.compile(r'\b\w{3,}\s*\(\s*\{?\s*"[\w\-]+"\s*:')


def parse_text_tool_calls(content: str, available_tools: List[Dict]) -> List[Dict]:
    """
    Parses tool calls from text content of models without native tool calling.

    Supported formats:
    1. Mistral: [TOOL_CALLS] [{"name": "func", "arguments": {...}}]
    2. XML:     <tool_call>{"name": "func", "arguments": {...}}</tool_call>
    3. OpenHermes: <functioncall>{"name": "func", "arguments": {...}}</functioncall>
    4. JSON-Block: ```json\n{"tool": "func", ...}\n```

    Args:
        content: The text content to parse
        available_tools: List of available tool schemas for validation

    Returns:
        List of parsed tool call dictionaries
    """
    if not content:
        return []

    # PERFORMANCE: Quick check for tool call markers
    # Avoids expensive regex operations when content has no tools
    _HAS_TOOL_MARKERS = (
        "[TOOL_CALLS]" in content or
        "<tool_call>" in content or
        "<functioncall>" in content or
        "<function_calls>" in content or
        "<invoke>" in content or
        ('"name"' in content and ("arguments" in content or "parameters" in content)) or
        ("```" in content and '"tool"' in content) or
        _RE_PAREN_CALL_HEAD.search(content) is not None
    )
    if not _HAS_TOOL_MARKERS:
        return []

    tool_names = {t["function"]["name"] for t in available_tools} if available_tools else set()
    parsed_calls = []

    # Format 1a: Mistral 678B Compact Format
    # [TOOL_CALLS]funcname{"arg": "val"} (no space, no JSON array)
    mistral_compact_matches = _RE_MISTRAL_COMPACT.findall(content)
    if mistral_compact_matches:
        for name, args_str in mistral_compact_matches:
            if not tool_names or name in tool_names:
                try:
                    args = json.loads(args_str)
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args_str
                        }
                    })
                except json.JSONDecodeError:
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": name, "arguments": args_str}
                    })
        if parsed_calls:
            logger.debug("Mistral 678B Compact Format detected: %d calls", len(parsed_calls))
            return parsed_calls

    # Format 1b: Mistral Standard Format
    # [TOOL_CALLS] [{"name": "...", "arguments": {...}}]
    mistral_match = _RE_MISTRAL_STANDARD.search(content)
    if mistral_match:
        try:
            calls = json.loads(mistral_match.group(1))
            if isinstance(calls, list):
                for call in calls:
                    name = call.get("name") or call.get("function")
                    args = call.get("arguments") or call.get("parameters") or {}
                    if name and (not tool_names or name in tool_names):
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
            if parsed_calls:
                logger.debug(f"[agent] Mistral Standard Format detected: {len(parsed_calls)} calls")
                return parsed_calls
        except (json.JSONDecodeError, KeyError):
            pass

    # Format 2: XML <tool_call> or <functioncall>
    xml_patterns = [
        _RE_XML_TOOL_CALL,
        _RE_XML_FUNCTIONCALL,
        _RE_XML_FUNCTION_CALLS,
        _RE_XML_INVOKE,
    ]
    for pattern in xml_patterns:
        matches = pattern.findall(content)
        for match in matches:
            try:
                call = json.loads(match.strip())
                name = call.get("name") or call.get("function") or call.get("tool_name")
                args = call.get("arguments") or call.get("parameters") or call.get("kwargs") or {}
                if name and (not tool_names or name in tool_names):
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args
                        }
                    })
            except (json.JSONDecodeError, KeyError):
                continue
    if parsed_calls:
        logger.debug(f"[agent] XML Tool-Call Format detected: {len(parsed_calls)} calls")
        return parsed_calls

    # Format 3: JSON code block with tool call structure
    json_blocks = _RE_JSON_BLOCK.findall(content)
    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            # Check if it's a tool call
            name = None
            args = {}
            if isinstance(data, dict):
                if "name" in data and ("arguments" in data or "parameters" in data):
                    name = data["name"]
                    args = data.get("arguments") or data.get("parameters") or {}
                elif "tool" in data:
                    name = data["tool"]
                    args = data.get("input") or data.get("arguments") or {}
            if name and (not tool_names or name in tool_names):
                parsed_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else args
                    }
                })
        except (json.JSONDecodeError, KeyError):
            continue
    if parsed_calls:
        logger.debug(f"[agent] JSON-Block Tool-Call Format detected: {len(parsed_calls)} calls")
        return parsed_calls

    # Format 4: Inline JSON with known tool name
    # Search for {"name": "known_tool", ...} directly in text
    if tool_names:
        inline_matches = _RE_INLINE_NAME.findall(content)
        for match_name in inline_matches:
            if match_name in tool_names:
                # Try to extract the full JSON block
                pattern = r'\{[^{}]*"name"\s*:\s*"' + re.escape(match_name) + r'"[^{}]*\}'
                full_matches = re.findall(pattern, content, re.DOTALL)
                for fm in full_matches:
                    try:
                        call = json.loads(fm)
                        args = call.get("arguments") or call.get("parameters") or {}
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": match_name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
                    except (json.JSONDecodeError, KeyError):
                        continue
        if parsed_calls:
            logger.debug(f"[agent] Inline JSON Tool-Call Format detected: {len(parsed_calls)} calls")
            return parsed_calls

    # Format 5: Paren-Call (Python-style)
    # funcname("key": value, ...) or funcname({"key": value})
    # Only attempt if tool_names is known, to avoid matching arbitrary function-like text.
    if tool_names:
        for name, payload in _iter_paren_calls(content):
            if name not in tool_names:
                continue
            args = _parse_paren_args(payload)
            if args is None:
                # Clearly intended as a tool call but unparseable - treat as malformed.
                # We do NOT raise here (parse_text_tool_calls must return a list);
                # instead, detect_malformed_tool_attempt() will surface this to the caller.
                logger.warning(
                    "[agent] paren-call for %r found but args unparseable: %r",
                    name, payload[:120]
                )
                continue
            parsed_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args)
                }
            })
        if parsed_calls:
            logger.debug(f"[agent] Paren-Call Format detected: {len(parsed_calls)} calls")
            return parsed_calls

    # Debug: If no tool call detected, log helpful info
    if content and len(content) > 20:
        # Check for potential tool call patterns that didn't match
        potential_patterns = [
            (_RE_HINT_TOOL, '[TOOL...'),
            (_RE_HINT_XML_TOOL, '<tool...'),
            (_RE_HINT_XML_FUNC, '<function...'),
            (_RE_HINT_NAME, '"name":'),
            (_RE_HINT_TOOL_KEY, '"tool":'),
        ]
        found_hints = []
        for pattern, hint in potential_patterns:
            if pattern.search(content):
                found_hints.append(hint)
        if found_hints:
            logger.debug(f"[agent] Text-Parser: No tool calls detected, but hints found: {found_hints}")
            logger.debug(f"[agent] Content start (100 chars): {content[:100]!r}")

    return []


def _iter_paren_calls(content: str):
    """Yield (name, payload) tuples for each paren-style call found.

    payload is the raw string between the matching parens (without the surrounding
    parens themselves). Uses a balanced-paren scanner so nested () in JSON string
    values don't terminate the match early.
    """
    for m in _RE_PAREN_CALL_HEAD.finditer(content):
        name = m.group(1)
        open_idx = content.find('(', m.start())
        if open_idx < 0:
            continue
        end = _find_matching_paren(content, open_idx)
        if end < 0:
            continue
        payload = content[open_idx + 1:end]
        yield name, payload


def _find_matching_paren(s: str, open_idx: int) -> int:
    """Return index of the `)` matching `s[open_idx] == '('`, or -1.

    Skips parens inside JSON string literals. Not a full tokenizer — aims to
    survive typical LLM output where strings may contain '(' or ')'.
    """
    if open_idx >= len(s) or s[open_idx] != '(':
        return -1
    depth = 0
    in_str = False
    escape = False
    i = open_idx
    while i < len(s):
        c = s[i]
        if in_str:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _parse_paren_args(payload: str) -> Optional[Dict]:
    """Try hard to turn a paren-style arg payload into a dict.

    Accepts:
      "key": "val", "k2": "v2"     (bare kwargs)
      {"key": "val"}                (already JSON object)
    Returns None if it cannot extract *any* key/value safely — caller should
    treat that as malformed and surface a clear error rather than silently
    dropping content.
    """
    if not payload or not payload.strip():
        return None
    s = payload.strip()

    # If already wrapped in braces, try direct JSON parse first.
    if s.startswith('{') and s.endswith('}'):
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass  # fall through to bracket-wrap attempt

    # Wrap in braces and try JSON parse.
    try:
        data = json.loads('{' + s.rstrip(',').rstrip() + '}')
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Last resort: regex-extract each "key": "value" pair.
    # Only used when JSON parse fails (often due to unescaped quotes/newlines in values).
    pairs = re.findall(
        r'"([\w\-]+)"\s*:\s*"((?:\\.|[^"\\])*)"',
        s,
        flags=re.DOTALL,
    )
    if pairs:
        return {k: v for k, v in pairs}
    return None


def detect_malformed_tool_attempt(content: str) -> Optional[Tuple[str, List[str]]]:
    """Check whether content looks like an attempted-but-broken tool call.

    Returns (snippet, hints) if a tool call was clearly intended but the
    `parse_text_tool_calls` function returned no results. Callers can use this
    to raise `MalformedToolCall` or surface a user-visible error, preventing
    the malformed text from silently leaking into chat output.

    Returns None if content has no tool-call hints.
    """
    if not content or len(content) < 20:
        return None
    hints = []
    if _RE_HINT_TOOL.search(content):
        hints.append('[TOOL...')
    if _RE_HINT_XML_TOOL.search(content):
        hints.append('<tool...')
    if _RE_HINT_XML_FUNC.search(content):
        hints.append('<function...')
    if _RE_HINT_NAME.search(content):
        hints.append('"name":')
    if _RE_HINT_TOOL_KEY.search(content):
        hints.append('"tool":')
    if _RE_HINT_PAREN_CALL.search(content):
        hints.append('funcname(...)')
    if not hints:
        return None
    snippet = content[:200]
    return snippet, hints


# Export regex patterns for use by orchestrator (if still needed for other purposes)
REGEX_PATTERNS = {
    "mistral_compact": _RE_MISTRAL_COMPACT,
    "mistral_standard": _RE_MISTRAL_STANDARD,
    "xml_tool_call": _RE_XML_TOOL_CALL,
    "xml_functioncall": _RE_XML_FUNCTIONCALL,
    "xml_function_calls": _RE_XML_FUNCTION_CALLS,
    "xml_invoke": _RE_XML_INVOKE,
    "json_block": _RE_JSON_BLOCK,
    "inline_name": _RE_INLINE_NAME,
    "paren_call_head": _RE_PAREN_CALL_HEAD,
}


__all__ = [
    "parse_text_tool_calls",
    "detect_malformed_tool_attempt",
    "MalformedToolCall",
    "REGEX_PATTERNS",
]
