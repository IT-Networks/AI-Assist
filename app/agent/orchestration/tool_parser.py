"""
Tool Parser - Parses tool calls from text content.

Handles text-based tool call parsing for models that don't support
native function calling (e.g., Mistral, Qwen, OpenHermes).

Supported formats:
1. Mistral: [TOOL_CALLS] [{"name": "func", "arguments": {...}}]
2. XML: <tool_call>{"name": "func", "arguments": {...}}</tool_call>
3. OpenHermes: <functioncall>{"name": "func", "arguments": {...}}</functioncall>
4. JSON-Block: ```json\n{"tool": "func", ...}\n```
"""

import json
import logging
import re
import uuid
from typing import Dict, List

logger = logging.getLogger(__name__)


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

# Debug hint patterns
_RE_HINT_TOOL = re.compile(r'\[TOOL', re.IGNORECASE)
_RE_HINT_XML_TOOL = re.compile(r'<tool', re.IGNORECASE)
_RE_HINT_XML_FUNC = re.compile(r'<function', re.IGNORECASE)
_RE_HINT_NAME = re.compile(r'"name"\s*:')
_RE_HINT_TOOL_KEY = re.compile(r'"tool"\s*:')


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
        ("```" in content and '"tool"' in content)
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
}
