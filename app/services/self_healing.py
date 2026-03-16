"""
Self-Healing Code Service - Auto-Fix nach Tool-Fehlern.

Features:
- Automatische Fehlererkennung nach Tool-Ausfuehrungen
- Pattern-Matching gegen bekannte Fehler
- LLM-basierte Fix-Generierung bei unbekannten Fehlern
- Auto-Apply oder User-Confirmation basierend auf Konfiguration
- Retry-Mechanismus mit konfigurierbarem Limit
"""

import logging
import re
import sqlite3
import uuid

from app.utils.json_utils import json_loads, json_dumps
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Enums and Constants
# ═══════════════════════════════════════════════════════════════════════════════

class AutoApplyLevel(str, Enum):
    """Auto-apply level for fixes."""
    NONE = "none"      # Always ask user
    SAFE = "safe"      # Only apply safe fixes automatically
    ALL = "all"        # Apply all fixes automatically


class FixType(str, Enum):
    """Types of suggested fixes."""
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    INSTALL_DEPENDENCY = "install_dependency"
    CONFIG_CHANGE = "config_change"
    RETRY = "retry"


class HealingStatus(str, Enum):
    """Status of a healing attempt."""
    PENDING = "pending"
    APPLIED = "applied"
    SUCCESS = "success"
    FAILED = "failed"
    DISMISSED = "dismissed"


# Safe fix patterns that can be auto-applied
SAFE_FIX_PATTERNS = [
    r"missing\s+semicolon",
    r"missing\s+import",
    r"missing\s+closing\s+(bracket|brace|parenthesis)",
    r"trailing\s+whitespace",
    r"indentation\s+error",
    r"typo\s+in",
    r"undefined\s+variable.*did\s+you\s+mean",
]


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Column Constants (Performance: avoid SELECT *)
# ═══════════════════════════════════════════════════════════════════════════════

_HEALING_CONFIG_COLUMNS = """id, enabled, auto_apply_level, max_retries, retry_delay_ms,
    excluded_tools, min_confidence_for_auto, learn_from_success"""

_HEALING_ATTEMPTS_COLUMNS = """id, timestamp, session_id, chain_id, tool_name, error_type,
    error_message, pattern_id, pattern_name, fix_type, fix_description, fix_data,
    status, applied, success, retry_count, result_message"""


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SelfHealingConfig:
    """Configuration for Self-Healing behavior."""
    enabled: bool = True
    auto_apply_level: AutoApplyLevel = AutoApplyLevel.SAFE
    max_retries: int = 3
    retry_delay_ms: int = 1000
    excluded_tools: List[str] = field(default_factory=list)
    min_confidence_for_auto: float = 0.8
    learn_from_success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "autoApplyLevel": self.auto_apply_level.value,
            "maxRetries": self.max_retries,
            "retryDelayMs": self.retry_delay_ms,
            "excludedTools": self.excluded_tools,
            "minConfidenceForAuto": self.min_confidence_for_auto,
            "learnFromSuccess": self.learn_from_success,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfHealingConfig":
        return cls(
            enabled=data.get("enabled", True),
            auto_apply_level=AutoApplyLevel(data.get("autoApplyLevel", "safe")),
            max_retries=data.get("maxRetries", 3),
            retry_delay_ms=data.get("retryDelayMs", 1000),
            excluded_tools=data.get("excludedTools", []),
            min_confidence_for_auto=data.get("minConfidenceForAuto", 0.8),
            learn_from_success=data.get("learnFromSuccess", True),
        )


@dataclass
class CodeChange:
    """A single code change."""
    file_path: str
    line_number: Optional[int] = None
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolError:
    """Captured tool error."""
    tool: str
    error_type: str
    error_message: str
    stack_trace: str = ""
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
            "stackTrace": self.stack_trace,
            "context": {
                "filePath": self.file_path,
                "lineNumber": self.line_number,
                "codeSnippet": self.code_snippet,
            },
            "toolArgs": self.tool_args,
        }

    @classmethod
    def from_tool_result(cls, tool_name: str, error: str, args: Dict = None) -> "ToolError":
        """Create ToolError from a tool execution result."""
        # Extract error type from message
        error_type = "UnknownError"
        if ":" in error:
            error_type = error.split(":")[0].strip()

        # Extract file path and line number if present
        file_path = None
        line_number = None
        code_snippet = None

        # Common patterns for file/line extraction
        file_match = re.search(r'(?:in\s+|file\s+|at\s+)?["\']?([^"\'\s]+\.(py|java|js|ts|go|rs))["\']?', error, re.I)
        if file_match:
            file_path = file_match.group(1)

        line_match = re.search(r'line\s*(\d+)|:(\d+):', error)
        if line_match:
            line_number = int(line_match.group(1) or line_match.group(2))

        return cls(
            tool=tool_name,
            error_type=error_type,
            error_message=error,
            stack_trace="",
            file_path=file_path,
            line_number=line_number,
            code_snippet=code_snippet,
            tool_args=args or {},
        )


@dataclass
class SuggestedFix:
    """A suggested fix for an error."""
    id: str
    fix_type: FixType
    description: str
    changes: List[CodeChange] = field(default_factory=list)
    command: Optional[str] = None
    confidence: float = 0.5
    safe_to_auto_apply: bool = False
    pattern_id: Optional[str] = None
    pattern_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.fix_type.value,
            "description": self.description,
            "changes": [c.to_dict() for c in self.changes],
            "command": self.command,
            "confidence": round(self.confidence, 2),
            "safeToAutoApply": self.safe_to_auto_apply,
            "patternId": self.pattern_id,
            "patternName": self.pattern_name,
        }


@dataclass
class HealingAttempt:
    """Record of a healing attempt."""
    id: str
    timestamp: int  # Unix timestamp ms
    session_id: str
    chain_id: Optional[str] = None

    # Error info
    original_error: Optional[ToolError] = None

    # Pattern match
    pattern_id: Optional[str] = None
    pattern_name: Optional[str] = None

    # Fix
    suggested_fix: Optional[SuggestedFix] = None

    # Status
    status: HealingStatus = HealingStatus.PENDING
    applied: bool = False
    success: bool = False
    retry_count: int = 0

    # Result
    result_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "sessionId": self.session_id,
            "chainId": self.chain_id,
            "originalError": self.original_error.to_dict() if self.original_error else None,
            "patternId": self.pattern_id,
            "patternName": self.pattern_name,
            "suggestedFix": self.suggested_fix.to_dict() if self.suggested_fix else None,
            "status": self.status.value,
            "applied": self.applied,
            "success": self.success,
            "retryCount": self.retry_count,
            "resultMessage": self.result_message,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Fix Generators - Pattern-based fix generation
# ═══════════════════════════════════════════════════════════════════════════════

class FixGenerator:
    """Generates fixes for common error patterns."""

    # Pattern -> Fix generator mapping
    FIX_PATTERNS = [
        # Python errors
        (r"SyntaxError.*missing.*:$", "add_colon"),
        (r"IndentationError", "indentation"),
        (r"NameError.*name '(\w+)' is not defined", "undefined_name"),
        (r"ImportError.*No module named '(\w+)'", "missing_import"),
        (r"ModuleNotFoundError.*No module named '(\w+)'", "missing_module"),

        # Java errors
        (r"error:.*';' expected", "add_semicolon"),
        (r"error:.*cannot find symbol.*variable (\w+)", "undefined_variable"),
        (r"error:.*package (\S+) does not exist", "missing_import_java"),
        (r"error:.*class.*is public.*should be declared in.*\.java", "wrong_filename"),

        # JavaScript/TypeScript errors
        (r"SyntaxError.*Unexpected token", "unexpected_token"),
        (r"ReferenceError.*(\w+) is not defined", "undefined_reference"),
        (r"Cannot find module '([^']+)'", "missing_npm_module"),

        # General file errors
        (r"FileNotFoundError|ENOENT|No such file", "file_not_found"),
        (r"PermissionError|EACCES", "permission_error"),
        (r"ConnectionError|ECONNREFUSED", "connection_error"),
    ]

    @classmethod
    def generate_fix(cls, error: ToolError) -> Optional[SuggestedFix]:
        """Generate a fix suggestion based on error pattern matching."""
        error_msg = error.error_message.lower()

        for pattern, fix_method in cls.FIX_PATTERNS:
            match = re.search(pattern, error.error_message, re.IGNORECASE)
            if match:
                method = getattr(cls, f"_fix_{fix_method}", None)
                if method:
                    return method(error, match)

        return None

    @classmethod
    def _fix_add_semicolon(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix missing semicolon."""
        changes = []
        if error.file_path and error.line_number:
            changes.append(CodeChange(
                file_path=error.file_path,
                line_number=error.line_number,
                description="Add missing semicolon at end of line"
            ))

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description="Add missing semicolon",
            changes=changes,
            confidence=0.95,
            safe_to_auto_apply=True,
        )

    @classmethod
    def _fix_add_colon(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix missing colon in Python."""
        changes = []
        if error.file_path and error.line_number:
            changes.append(CodeChange(
                file_path=error.file_path,
                line_number=error.line_number,
                description="Add missing colon at end of statement"
            ))

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description="Add missing colon",
            changes=changes,
            confidence=0.92,
            safe_to_auto_apply=True,
        )

    @classmethod
    def _fix_indentation(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix indentation error."""
        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description="Fix indentation - align with surrounding code block",
            changes=[CodeChange(
                file_path=error.file_path or "",
                line_number=error.line_number,
                description="Correct indentation level"
            )] if error.file_path else [],
            confidence=0.85,
            safe_to_auto_apply=True,
        )

    @classmethod
    def _fix_undefined_name(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix undefined name - suggest import or definition."""
        name = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description=f"Add import or define '{name}'",
            changes=[CodeChange(
                file_path=error.file_path or "",
                description=f"Import or define '{name}'"
            )] if error.file_path else [],
            confidence=0.7,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_missing_import(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix missing Python import."""
        module = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description=f"Add import for '{module}'",
            changes=[CodeChange(
                file_path=error.file_path or "",
                line_number=1,
                new_content=f"import {module}",
                description=f"Add 'import {module}' at top of file"
            )] if error.file_path else [],
            confidence=0.88,
            safe_to_auto_apply=True,
        )

    @classmethod
    def _fix_missing_module(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix missing Python module - suggest pip install."""
        module = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.INSTALL_DEPENDENCY,
            description=f"Install missing module '{module}'",
            command=f"pip install {module}",
            confidence=0.75,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_missing_npm_module(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix missing npm module."""
        module = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.INSTALL_DEPENDENCY,
            description=f"Install missing npm package '{module}'",
            command=f"npm install {module}",
            confidence=0.75,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_undefined_variable(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix undefined Java variable."""
        variable = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description=f"Declare variable '{variable}' or fix typo",
            changes=[],
            confidence=0.65,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_missing_import_java(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix missing Java import."""
        package = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description=f"Add Java import for '{package}'",
            changes=[CodeChange(
                file_path=error.file_path or "",
                description=f"Add 'import {package}.*;' after package declaration"
            )] if error.file_path else [],
            confidence=0.85,
            safe_to_auto_apply=True,
        )

    @classmethod
    def _fix_unexpected_token(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix unexpected token - usually syntax error."""
        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description="Fix syntax error - check for missing brackets, quotes, or operators",
            changes=[],
            confidence=0.6,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_undefined_reference(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix undefined reference in JavaScript."""
        name = match.group(1) if match.groups() else "unknown"

        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description=f"Define or import '{name}'",
            changes=[],
            confidence=0.7,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_file_not_found(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix file not found error."""
        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.RUN_COMMAND,
            description="Create missing file or check path",
            command=f"touch {error.file_path}" if error.file_path else None,
            confidence=0.6,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_permission_error(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix permission error."""
        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.RUN_COMMAND,
            description="Check file permissions or run with elevated privileges",
            confidence=0.5,
            safe_to_auto_apply=False,
        )

    @classmethod
    def _fix_connection_error(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix connection error."""
        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.RETRY,
            description="Retry connection - service may be temporarily unavailable",
            confidence=0.6,
            safe_to_auto_apply=True,
        )

    @classmethod
    def _fix_wrong_filename(cls, error: ToolError, match: re.Match) -> SuggestedFix:
        """Fix wrong Java filename."""
        return SuggestedFix(
            id=str(uuid.uuid4()),
            fix_type=FixType.EDIT_FILE,
            description="Rename file to match public class name",
            confidence=0.9,
            safe_to_auto_apply=False,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Self-Healing Engine
# ═══════════════════════════════════════════════════════════════════════════════

class SelfHealingEngine:
    """
    Engine for detecting errors, generating fixes, and applying them.

    Integrates with:
    - Pattern Learner for known error patterns
    - LLM for unknown errors
    - Tool execution pipeline for applying fixes
    """

    def __init__(self, db_path: str = "./data/healing.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._config: Optional[SelfHealingConfig] = None
        self._pending_attempts: Dict[str, HealingAttempt] = {}

    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Config table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS healing_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 1,
                auto_apply_level TEXT DEFAULT 'safe',
                max_retries INTEGER DEFAULT 3,
                retry_delay_ms INTEGER DEFAULT 1000,
                excluded_tools TEXT DEFAULT '[]',
                min_confidence_for_auto REAL DEFAULT 0.8,
                learn_from_success INTEGER DEFAULT 1
            )
        """)

        # Attempts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS healing_attempts (
                id TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                chain_id TEXT,
                tool_name TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT,
                pattern_id TEXT,
                pattern_name TEXT,
                fix_type TEXT,
                fix_description TEXT,
                fix_data TEXT,
                status TEXT DEFAULT 'pending',
                applied INTEGER DEFAULT 0,
                success INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                result_message TEXT
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_healing_timestamp ON healing_attempts(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_healing_session ON healing_attempts(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_healing_status ON healing_attempts(status)")

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    # ═══════════════════════════════════════════════════════════════════════════
    # Configuration
    # ═══════════════════════════════════════════════════════════════════════════

    def get_config(self) -> SelfHealingConfig:
        """Get current configuration."""
        if self._config:
            return self._config

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"SELECT {_HEALING_CONFIG_COLUMNS} FROM healing_config WHERE id = 1")
        row = cursor.fetchone()
        conn.close()

        if row:
            self._config = SelfHealingConfig(
                enabled=bool(row[1]),
                auto_apply_level=AutoApplyLevel(row[2]),
                max_retries=row[3],
                retry_delay_ms=row[4],
                excluded_tools=json_loads(row[5]) if row[5] else [],
                min_confidence_for_auto=row[6],
                learn_from_success=bool(row[7]),
            )
        else:
            # Create default config
            self._config = SelfHealingConfig()
            self.set_config(self._config)

        return self._config

    def set_config(self, config: SelfHealingConfig) -> SelfHealingConfig:
        """Set configuration."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO healing_config
            (id, enabled, auto_apply_level, max_retries, retry_delay_ms,
             excluded_tools, min_confidence_for_auto, learn_from_success)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        """, (
            1 if config.enabled else 0,
            config.auto_apply_level.value,
            config.max_retries,
            config.retry_delay_ms,
            json_dumps(config.excluded_tools),
            config.min_confidence_for_auto,
            1 if config.learn_from_success else 0,
        ))

        conn.commit()
        conn.close()

        self._config = config
        return config

    # ═══════════════════════════════════════════════════════════════════════════
    # Error Analysis
    # ═══════════════════════════════════════════════════════════════════════════

    def analyze_error(
        self,
        tool_name: str,
        error: str,
        tool_args: Dict = None,
        session_id: str = "default",
        chain_id: str = None
    ) -> Optional[HealingAttempt]:
        """
        Analyze a tool error and generate a healing attempt.

        Returns:
            HealingAttempt if a fix was found, None otherwise
        """
        config = self.get_config()

        if not config.enabled:
            logger.debug("Self-healing disabled")
            return None

        if tool_name in config.excluded_tools:
            logger.debug(f"Tool {tool_name} is excluded from self-healing")
            return None

        # Create tool error
        tool_error = ToolError.from_tool_result(tool_name, error, tool_args)

        # Try to find a fix
        suggested_fix = self._find_fix(tool_error)

        if not suggested_fix:
            logger.debug(f"No fix found for error: {error[:100]}")
            return None

        # Create healing attempt
        attempt = HealingAttempt(
            id=str(uuid.uuid4()),
            timestamp=int(datetime.now().timestamp() * 1000),
            session_id=session_id,
            chain_id=chain_id,
            original_error=tool_error,
            pattern_id=suggested_fix.pattern_id,
            pattern_name=suggested_fix.pattern_name,
            suggested_fix=suggested_fix,
            status=HealingStatus.PENDING,
        )

        # Store attempt
        self._save_attempt(attempt)
        self._pending_attempts[attempt.id] = attempt

        logger.info(f"Generated healing attempt {attempt.id} for {tool_name}: {suggested_fix.description}")

        return attempt

    def _find_fix(self, error: ToolError) -> Optional[SuggestedFix]:
        """Find a fix for the error using patterns or LLM."""
        # 1. Try pattern-based fix generation
        fix = FixGenerator.generate_fix(error)
        if fix:
            return fix

        # 2. Try to match against learned patterns
        fix = self._match_learned_patterns(error)
        if fix:
            return fix

        # 3. For complex errors, we could call LLM here
        # (For now, return None - LLM integration can be added later)

        return None

    def _match_learned_patterns(self, error: ToolError) -> Optional[SuggestedFix]:
        """Match error against learned patterns from pattern_learner."""
        try:
            from app.services.pattern_learner import get_pattern_learner

            learner = get_pattern_learner()
            suggestion = learner.suggest_solution(
                error_type=error.error_type,
                error_message=error.error_message,
                context={"tool": error.tool, "file": error.file_path}
            )

            if suggestion and suggestion.get("pattern"):
                pattern = suggestion["pattern"]
                return SuggestedFix(
                    id=str(uuid.uuid4()),
                    fix_type=FixType.EDIT_FILE,
                    description=pattern.solution_description,
                    changes=[CodeChange(
                        file_path=error.file_path or "",
                        description=step
                    ) for step in pattern.solution_steps[:3]],
                    confidence=pattern.confidence,
                    safe_to_auto_apply=pattern.confidence >= 0.9,
                    pattern_id=pattern.id,
                    pattern_name=pattern.error_type,
                )
        except Exception as e:
            logger.warning(f"Pattern matching failed: {e}")

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Fix Application
    # ═══════════════════════════════════════════════════════════════════════════

    def should_auto_apply(self, attempt: HealingAttempt) -> bool:
        """Determine if a fix should be auto-applied."""
        config = self.get_config()

        if not attempt.suggested_fix:
            return False

        if config.auto_apply_level == AutoApplyLevel.NONE:
            return False

        if config.auto_apply_level == AutoApplyLevel.ALL:
            return True

        # SAFE level - check confidence and safe flag
        fix = attempt.suggested_fix
        if fix.safe_to_auto_apply and fix.confidence >= config.min_confidence_for_auto:
            return True

        return False

    def apply_fix(
        self,
        attempt_id: str,
        executor: Callable[[str, Dict], Any] = None
    ) -> Tuple[bool, str]:
        """
        Apply a fix for a healing attempt.

        Args:
            attempt_id: ID of the healing attempt
            executor: Optional function to execute tools (tool_name, args) -> result

        Returns:
            Tuple of (success, message)
        """
        attempt = self._pending_attempts.get(attempt_id)
        if not attempt:
            attempt = self._load_attempt(attempt_id)

        if not attempt:
            return False, f"Attempt {attempt_id} not found"

        if not attempt.suggested_fix:
            return False, "No fix suggested"

        fix = attempt.suggested_fix
        attempt.applied = True
        attempt.status = HealingStatus.APPLIED

        try:
            # Apply based on fix type
            if fix.fix_type == FixType.EDIT_FILE:
                success, message = self._apply_edit_fix(fix, executor)
            elif fix.fix_type == FixType.RUN_COMMAND:
                success, message = self._apply_command_fix(fix, executor)
            elif fix.fix_type == FixType.INSTALL_DEPENDENCY:
                success, message = self._apply_install_fix(fix, executor)
            elif fix.fix_type == FixType.RETRY:
                success, message = True, "Retry suggested"
            else:
                success, message = False, f"Unknown fix type: {fix.fix_type}"

            attempt.success = success
            attempt.status = HealingStatus.SUCCESS if success else HealingStatus.FAILED
            attempt.result_message = message

        except Exception as e:
            logger.error(f"Error applying fix: {e}")
            attempt.success = False
            attempt.status = HealingStatus.FAILED
            attempt.result_message = str(e)
            success, message = False, str(e)

        # Update attempt
        self._save_attempt(attempt)

        # Learn from success if configured
        if success and self.get_config().learn_from_success:
            self._learn_from_success(attempt)

        return success, message

    def _apply_edit_fix(self, fix: SuggestedFix, executor: Callable) -> Tuple[bool, str]:
        """Apply an edit file fix."""
        if not fix.changes:
            return False, "No changes specified"

        if not executor:
            return False, "No executor provided for edit operation"

        for change in fix.changes:
            if change.new_content and change.file_path:
                result = executor("edit_file", {
                    "path": change.file_path,
                    "content": change.new_content,
                    "line": change.line_number,
                })
                if not result or not result.get("success"):
                    return False, f"Failed to apply change to {change.file_path}"

        return True, f"Applied {len(fix.changes)} changes"

    def _apply_command_fix(self, fix: SuggestedFix, executor: Callable) -> Tuple[bool, str]:
        """Apply a command fix."""
        if not fix.command:
            return False, "No command specified"

        if executor:
            result = executor("shell_execute", {"command": fix.command})
            if result and result.get("success"):
                return True, f"Executed: {fix.command}"
            return False, f"Command failed: {fix.command}"

        return False, "No executor for command"

    def _apply_install_fix(self, fix: SuggestedFix, executor: Callable) -> Tuple[bool, str]:
        """Apply an install dependency fix."""
        if not fix.command:
            return False, "No install command specified"

        if executor:
            result = executor("shell_execute", {"command": fix.command})
            if result and result.get("success"):
                return True, f"Installed: {fix.command}"
            return False, f"Install failed: {fix.command}"

        return False, "No executor for install"

    def dismiss_fix(self, attempt_id: str) -> bool:
        """Dismiss a suggested fix."""
        attempt = self._pending_attempts.get(attempt_id)
        if not attempt:
            attempt = self._load_attempt(attempt_id)

        if not attempt:
            return False

        attempt.status = HealingStatus.DISMISSED
        attempt.result_message = "Dismissed by user"
        self._save_attempt(attempt)

        if attempt_id in self._pending_attempts:
            del self._pending_attempts[attempt_id]

        return True

    def _learn_from_success(self, attempt: HealingAttempt):
        """Learn from a successful fix to improve future suggestions."""
        if not attempt.original_error or not attempt.suggested_fix:
            return

        try:
            from app.services.pattern_learner import get_pattern_learner

            learner = get_pattern_learner()

            # Record successful fix as a pattern
            learner.learn_pattern(
                error_type=attempt.original_error.error_type,
                error_message=attempt.original_error.error_message,
                solution_description=attempt.suggested_fix.description,
                solution_steps=[c.description for c in attempt.suggested_fix.changes],
                tools_used=[attempt.original_error.tool],
                context={"auto_learned": True, "source": "self_healing"}
            )
        except Exception as e:
            logger.warning(f"Failed to learn from success: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Persistence
    # ═══════════════════════════════════════════════════════════════════════════

    def _save_attempt(self, attempt: HealingAttempt):
        """Save healing attempt to database."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO healing_attempts
            (id, timestamp, session_id, chain_id, tool_name, error_type, error_message,
             pattern_id, pattern_name, fix_type, fix_description, fix_data,
             status, applied, success, retry_count, result_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            attempt.id,
            attempt.timestamp,
            attempt.session_id,
            attempt.chain_id,
            attempt.original_error.tool if attempt.original_error else None,
            attempt.original_error.error_type if attempt.original_error else None,
            attempt.original_error.error_message if attempt.original_error else None,
            attempt.pattern_id,
            attempt.pattern_name,
            attempt.suggested_fix.fix_type.value if attempt.suggested_fix else None,
            attempt.suggested_fix.description if attempt.suggested_fix else None,
            json_dumps(attempt.suggested_fix.to_dict()) if attempt.suggested_fix else None,
            attempt.status.value,
            1 if attempt.applied else 0,
            1 if attempt.success else 0,
            attempt.retry_count,
            attempt.result_message,
        ))

        conn.commit()
        conn.close()

    def _load_attempt(self, attempt_id: str) -> Optional[HealingAttempt]:
        """Load healing attempt from database."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"SELECT {_HEALING_ATTEMPTS_COLUMNS} FROM healing_attempts WHERE id = ?", (attempt_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        fix_data = json_loads(row[11]) if row[11] else None

        return HealingAttempt(
            id=row[0],
            timestamp=row[1],
            session_id=row[2],
            chain_id=row[3],
            original_error=ToolError(
                tool=row[4] or "",
                error_type=row[5] or "",
                error_message=row[6] or "",
            ) if row[4] else None,
            pattern_id=row[7],
            pattern_name=row[8],
            suggested_fix=SuggestedFix(
                id=fix_data.get("id", ""),
                fix_type=FixType(fix_data.get("type", "edit_file")),
                description=fix_data.get("description", ""),
                confidence=fix_data.get("confidence", 0.5),
                safe_to_auto_apply=fix_data.get("safeToAutoApply", False),
            ) if fix_data else None,
            status=HealingStatus(row[12]),
            applied=bool(row[13]),
            success=bool(row[14]),
            retry_count=row[15],
            result_message=row[16],
        )

    def get_attempts(
        self,
        session_id: str = None,
        status: HealingStatus = None,
        limit: int = 50
    ) -> List[HealingAttempt]:
        """Get healing attempts with optional filters."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = f"SELECT {_HEALING_ATTEMPTS_COLUMNS} FROM healing_attempts WHERE 1=1"
        params = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        attempts = []
        for row in rows:
            attempt = self._load_attempt(row[0])
            if attempt:
                attempts.append(attempt)

        return attempts

    def get_pending_attempts(self, session_id: str = None) -> List[HealingAttempt]:
        """Get pending healing attempts."""
        return self.get_attempts(session_id=session_id, status=HealingStatus.PENDING)

    def get_stats(self) -> Dict[str, Any]:
        """Get healing statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Total attempts
        cursor.execute("SELECT COUNT(*) FROM healing_attempts")
        total = cursor.fetchone()[0]

        # By status
        cursor.execute("""
            SELECT status, COUNT(*) FROM healing_attempts GROUP BY status
        """)
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

        # Success rate
        cursor.execute("""
            SELECT COUNT(*) FROM healing_attempts WHERE applied = 1
        """)
        applied = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM healing_attempts WHERE success = 1
        """)
        successful = cursor.fetchone()[0]

        # Recent
        cursor.execute("""
            SELECT tool_name, COUNT(*) FROM healing_attempts
            WHERE timestamp > ?
            GROUP BY tool_name
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """, (int((datetime.now().timestamp() - 86400 * 7) * 1000),))
        top_tools = {row[0]: row[1] for row in cursor.fetchall()}

        conn.close()

        return {
            "totalAttempts": total,
            "byStatus": by_status,
            "appliedCount": applied,
            "successCount": successful,
            "successRate": round(successful / max(applied, 1) * 100, 1),
            "topToolsWithErrors": top_tools,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_self_healing_engine: Optional[SelfHealingEngine] = None


def get_self_healing_engine() -> SelfHealingEngine:
    """Get the singleton SelfHealingEngine instance."""
    global _self_healing_engine
    if _self_healing_engine is None:
        _self_healing_engine = SelfHealingEngine()
    return _self_healing_engine
