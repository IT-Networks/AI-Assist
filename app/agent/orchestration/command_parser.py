"""
Command Parser - Parses slash commands and MCP force capabilities.

Handles:
- Slash command detection and activation (/command, /sc:command)
- MCP force capability detection ([MCP:capability])
- Flag parsing (--depth, --type, etc.)
- Continue marker handling ([CONTINUE], [CONTINUE_ENHANCED], etc.)
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from app.agent.constants import ControlMarkers

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns
_RE_MCP_FORCE = re.compile(r'^\[MCP:(\w+)\]\s*(.+)$', re.DOTALL)
_RE_SLASH_COMMAND = re.compile(r'^/(?:sc:)?([a-zA-Z][a-zA-Z0-9_-]*)\s*(.*)', re.DOTALL)

# Boolean flags (no value expected)
BOOLEAN_FLAGS: Set[str] = {
    'ultrathink', 'parallel', 'safe', 'interactive', 'preview',
    'validate', 'with-tests', 'force', 'verbose', 'quiet',
    'dry-run', 'watch', 'fix', 'strict', 'coverage'
}

# Value flags (expect a value)
VALUE_FLAGS: Set[str] = {
    'depth', 'type', 'format', 'strategy', 'scope', 'focus',
    'language', 'framework', 'constraints', 'model', 'target'
}


@dataclass
class ParsedCommand:
    """Result of parsing a slash command."""
    command_name: str
    query: str
    flags: Dict[str, any]
    original_message: str

    @property
    def has_flags(self) -> bool:
        return bool(self.flags)

    def get_transformed_message(self) -> str:
        """Get the message transformed with command context."""
        flags_context = ""
        if self.flags:
            flags_list = [
                f"--{k}={v}" if v is not True else f"--{k}"
                for k, v in self.flags.items()
            ]
            flags_context = f"\n[FLAGS: {' '.join(flags_list)}]"

        if self.query:
            return f"[COMMAND: /{self.command_name}]{flags_context}\n\n{self.query}"
        return self.original_message


@dataclass
class ContinueResult:
    """Result of checking for continue markers."""
    is_continue: bool = False
    is_continue_enhanced: bool = False
    is_retry_with_web: bool = False
    is_continue_no_web: bool = False
    transformed_message: Optional[str] = None


def parse_mcp_force_capability(message: str) -> Tuple[Optional[str], str]:
    """
    Parse MCP force capability from message.

    Format: [MCP:capability_name] actual query

    Args:
        message: User message to parse

    Returns:
        Tuple of (capability_name, remaining_message)
        capability_name is None if no force pattern found
    """
    match = _RE_MCP_FORCE.match(message)
    if match:
        capability = match.group(1)
        remaining = match.group(2).strip()
        logger.debug(f"[command_parser] Forced MCP capability: {capability}")
        return capability, remaining
    return None, message


def parse_slash_command(
    message: str,
    get_skills_for_command: callable = None
) -> Optional[ParsedCommand]:
    """
    Parse slash command from message.

    Supported formats:
    - /command <query>
    - /sc:command <query> (SuperClaude-Style)
    - /command --flag <query>

    Args:
        message: User message to parse
        get_skills_for_command: Function to get skills for a command name

    Returns:
        ParsedCommand if slash command found, None otherwise
    """
    match = _RE_SLASH_COMMAND.match(message)
    if not match:
        return None

    command_name = match.group(1).lower()
    command_rest = match.group(2).strip()

    # Parse flags and query
    flags = {}
    query_parts = []
    tokens = command_rest.split() if command_rest else []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith('--'):
            flag_name = token[2:]
            # Boolean flag or unknown flag without value
            if flag_name in BOOLEAN_FLAGS:
                flags[flag_name] = True
                i += 1
            # Value flag with expected value
            elif flag_name in VALUE_FLAGS and i + 1 < len(tokens) and not tokens[i + 1].startswith('--'):
                flags[flag_name] = tokens[i + 1]
                i += 2
            # Unknown flag - treat as boolean
            else:
                flags[flag_name] = True
                i += 1
        else:
            query_parts.append(token)
            i += 1

    query = ' '.join(query_parts)

    return ParsedCommand(
        command_name=command_name,
        query=query,
        flags=flags,
        original_message=message
    )


def check_continue_markers(message: str, state: any = None) -> ContinueResult:
    """
    Check for continue markers in message.

    Handles:
    - [CONTINUE] - after write confirmation
    - [CONTINUE_ENHANCED] - after enhancement confirmation
    - [RETRY_WITH_WEB] - after web fallback approval
    - [CONTINUE_WITHOUT_WEB] - after web fallback rejection

    Args:
        message: User message to check
        state: Agent state for context retrieval

    Returns:
        ContinueResult with detected markers and transformed message
    """
    stripped = message.strip()
    result = ContinueResult()

    # [CONTINUE] - after write confirmation
    if stripped == ControlMarkers.CONTINUE:
        result.is_continue = True
        result.transformed_message = (
            "Die letzte Datei-Operation wurde bestaetigt und ausgefuehrt. "
            "Setze die Arbeit fort und fuehre die verbleibenden Schritte aus."
        )
        logger.debug("[command_parser] Continue after confirmation detected")
        return result

    # [CONTINUE_ENHANCED] - after enhancement confirmation
    if stripped == ControlMarkers.CONTINUE_ENHANCED:
        result.is_continue_enhanced = True
        if state and state.enhancement_original_query:
            result.transformed_message = state.enhancement_original_query
            logger.info(f"[command_parser] Restored original query: {result.transformed_message[:50]}...")
        else:
            result.transformed_message = "Fahre mit der Anfrage fort."
        logger.debug("[command_parser] Continue after enhancement confirmation")
        return result

    # [RETRY_WITH_WEB] - after web fallback approval
    if stripped == ControlMarkers.RETRY_WITH_WEB:
        result.is_retry_with_web = True
        if state and state.enhancement_original_query:
            result.transformed_message = state.enhancement_original_query
            logger.info(f"[command_parser] Retrying with web: {result.transformed_message[:50]}...")
        else:
            result.transformed_message = "Fuehre die Recherche mit Web-Suche durch."
        logger.debug("[command_parser] Retry with web search approved")
        return result

    # [CONTINUE_WITHOUT_WEB] - after web fallback rejection
    if stripped == ControlMarkers.CONTINUE_WITHOUT_WEB:
        result.is_continue_no_web = True
        if state and state.enhancement_original_query:
            result.transformed_message = state.enhancement_original_query
        else:
            result.transformed_message = "Fahre ohne Web-Ergebnisse fort."
        logger.debug("[command_parser] Continue without web search")
        return result

    return result


def activate_skills_for_command(
    command: ParsedCommand,
    state: any,
    skill_manager: any
) -> List[any]:
    """
    Activate skills for a parsed command.

    Args:
        command: Parsed slash command
        state: Agent state to update
        skill_manager: Skill manager instance

    Returns:
        List of activated skills
    """
    command_skills = skill_manager.get_skills_for_command(
        command.command_name,
        include_inactive=True
    )

    if command_skills:
        for skill in command_skills:
            state.active_skill_ids.add(skill.id)
            logger.info(f"[command_parser] Skill '{skill.id}' activated by /{command.command_name}")

    return command_skills
