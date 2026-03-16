"""
Automated PR Review Service.

AI-powered code review with copy-friendly suggested edits.
Reviews are generated but NOT auto-applied - user copies edits manually.

Features:
- Multi-type analysis (security, quality, style, tests, performance)
- Line-by-line review comments with suggested fixes
- Copyable diff format for manual application
- Custom review rules (natural language)
- Review history and statistics
"""

import logging
import re
import sqlite3
import time
import uuid

from app.utils.json_utils import json_loads, json_dumps
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Enums and Types
# ═══════════════════════════════════════════════════════════════════════════════

class ReviewType(str, Enum):
    """Types of review checks."""
    SECURITY = "security"
    QUALITY = "quality"
    STYLE = "style"
    TESTS = "tests"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"
    CUSTOM = "custom"


class Severity(str, Enum):
    """Issue severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewStatus(str, Enum):
    """Review status."""
    PENDING = "pending"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class Verdict(str, Enum):
    """Overall review verdict."""
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    COMMENT = "comment"


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Column Constants (Performance: avoid SELECT *)
# ═══════════════════════════════════════════════════════════════════════════════

_REVIEW_COLUMNS = """id, pr_number, repo_owner, repo_name, head_sha, base_sha,
    timestamp, status, summary_json, comments_json, tests_json, error_message"""

_CUSTOM_RULE_COLUMNS = "id, name, description, severity, enabled, pattern"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SuggestedFix:
    """A suggested code fix in copyable format."""
    description: str
    original_code: str
    fixed_code: str
    diff: str  # Unified diff format for easy copying

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_copyable_format(self) -> str:
        """Format the fix for easy clipboard copying."""
        lines = [
            f"// {self.description}",
            "",
            "// Replace this:",
            "```",
            self.original_code,
            "```",
            "",
            "// With this:",
            "```",
            self.fixed_code,
            "```",
        ]
        return "\n".join(lines)

    def to_diff_format(self) -> str:
        """Return the diff in unified format."""
        return self.diff


@dataclass
class ReviewComment:
    """A single review comment with suggested fix."""
    id: str
    file_path: str
    line: int
    end_line: Optional[int]
    side: str  # LEFT or RIGHT

    review_type: ReviewType
    severity: Severity
    title: str
    body: str

    suggested_fix: Optional[SuggestedFix] = None
    dismissed: bool = False
    copied: bool = False  # Track if user copied this fix

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "filePath": self.file_path,
            "line": self.line,
            "endLine": self.end_line,
            "side": self.side,
            "type": self.review_type.value,
            "severity": self.severity.value,
            "title": self.title,
            "body": self.body,
            "dismissed": self.dismissed,
            "copied": self.copied,
        }
        if self.suggested_fix:
            result["suggestedFix"] = self.suggested_fix.to_dict()
        return result


@dataclass
class TestSuggestion:
    """Suggested test to add."""
    target_file: str
    test_file: str
    test_code: str
    coverage: List[str]  # Methods/branches covered

    def to_dict(self) -> Dict[str, Any]:
        return {
            "targetFile": self.target_file,
            "testFile": self.test_file,
            "testCode": self.test_code,
            "coverage": self.coverage,
        }


@dataclass
class ReviewSummary:
    """Summary of a PR review."""
    total_comments: int
    by_severity: Dict[str, int]
    by_type: Dict[str, int]
    verdict: Verdict
    files_reviewed: int
    lines_analyzed: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totalComments": self.total_comments,
            "bySeverity": self.by_severity,
            "byType": self.by_type,
            "verdict": self.verdict.value,
            "filesReviewed": self.files_reviewed,
            "linesAnalyzed": self.lines_analyzed,
        }


@dataclass
class PRReviewResult:
    """Complete PR review result."""
    id: str
    pr_number: int
    repo_owner: str
    repo_name: str
    head_sha: str
    base_sha: str
    timestamp: int
    status: ReviewStatus
    summary: Optional[ReviewSummary]
    comments: List[ReviewComment]
    suggested_tests: List[TestSuggestion]
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prNumber": self.pr_number,
            "repoOwner": self.repo_owner,
            "repoName": self.repo_name,
            "headSha": self.head_sha,
            "baseSha": self.base_sha,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "summary": self.summary.to_dict() if self.summary else None,
            "comments": [c.to_dict() for c in self.comments],
            "suggestedTests": [t.to_dict() for t in self.suggested_tests],
            "errorMessage": self.error_message,
        }


@dataclass
class CustomRule:
    """Custom review rule in natural language."""
    id: str
    name: str
    description: str
    severity: Severity
    enabled: bool = True
    pattern: Optional[str] = None  # Optional regex pattern

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "severity": self.severity.value,
            "enabled": self.enabled,
            "pattern": self.pattern,
        }


@dataclass
class PRReviewConfig:
    """Configuration for PR review service."""
    enabled: bool = True
    review_types: List[ReviewType] = field(default_factory=lambda: [
        ReviewType.SECURITY,
        ReviewType.QUALITY,
        ReviewType.STYLE,
    ])
    min_severity: Severity = Severity.LOW
    auto_review_on_pr: bool = False  # Disabled - manual trigger only
    include_test_suggestions: bool = True
    max_comments_per_file: int = 10
    max_total_comments: int = 50

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reviewTypes": [t.value for t in self.review_types],
            "minSeverity": self.min_severity.value,
            "autoReviewOnPr": self.auto_review_on_pr,
            "includeTestSuggestions": self.include_test_suggestions,
            "maxCommentsPerFile": self.max_comments_per_file,
            "maxTotalComments": self.max_total_comments,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Review Patterns - Common code issues
# ═══════════════════════════════════════════════════════════════════════════════

# Security patterns
SECURITY_PATTERNS = [
    (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password detected", Severity.CRITICAL),
    (r"api_key\s*=\s*['\"][^'\"]+['\"]", "Hardcoded API key detected", Severity.CRITICAL),
    (r"secret\s*=\s*['\"][^'\"]+['\"]", "Hardcoded secret detected", Severity.CRITICAL),
    (r"eval\s*\(", "Use of eval() is dangerous", Severity.HIGH),
    (r"exec\s*\(", "Use of exec() is dangerous", Severity.HIGH),
    (r"subprocess\.call\([^)]*shell\s*=\s*True", "Shell injection risk", Severity.HIGH),
    (r"os\.system\s*\(", "Command injection risk with os.system", Severity.MEDIUM),
    (r"pickle\.loads?\s*\(", "Insecure deserialization with pickle", Severity.HIGH),
    (r"yaml\.load\([^)]*Loader\s*=\s*None", "Unsafe YAML loading", Severity.MEDIUM),
    (r"SELECT.*\+.*\+", "Potential SQL injection (string concatenation)", Severity.HIGH),
    (r"innerHTML\s*=", "XSS risk with innerHTML", Severity.MEDIUM),
]

# Quality patterns
QUALITY_PATTERNS = [
    (r"except:\s*$", "Bare except clause catches all exceptions", Severity.MEDIUM),
    (r"except\s+Exception\s*:", "Overly broad exception handling", Severity.LOW),
    (r"TODO|FIXME|HACK|XXX", "TODO/FIXME comment found", Severity.INFO),
    (r"print\s*\(", "Debug print statement found", Severity.LOW),
    (r"console\.log\s*\(", "Debug console.log found", Severity.LOW),
    (r"debugger;?", "Debugger statement found", Severity.MEDIUM),
    (r"^\s{50,}", "Excessive indentation (>50 spaces)", Severity.LOW),
    (r"def \w+\([^)]{100,}\)", "Function has too many parameters", Severity.MEDIUM),
    (r"class \w+\([^)]{200,}\)", "Class has too many base classes", Severity.LOW),
]

# Style patterns
STYLE_PATTERNS = [
    (r"[a-z][A-Z]", "Consider using snake_case (Python convention)", Severity.INFO),
    (r"\t", "Tab character found (prefer spaces)", Severity.INFO),
    (r".{120,}", "Line exceeds 120 characters", Severity.INFO),
    (r"^\s*#\s*[a-z]", "Comment should start with capital letter", Severity.INFO),
    (r"import \*", "Wildcard import found", Severity.LOW),
    (r"from \w+ import \*", "Wildcard import found", Severity.LOW),
]

# Performance patterns
PERFORMANCE_PATTERNS = [
    (r"for .+ in .+\.keys\(\):", "Iterating over .keys() is redundant", Severity.LOW),
    (r"\+= ['\"]", "String concatenation in loop (use join)", Severity.MEDIUM),
    (r"time\.sleep\(\d{2,}\)", "Long sleep detected (>10s)", Severity.LOW),
    (r"SELECT \*", "SELECT * may fetch unnecessary columns", Severity.LOW),
    (r"N\+1|n\+1", "Potential N+1 query pattern", Severity.MEDIUM),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Code Analyzer
# ═══════════════════════════════════════════════════════════════════════════════

class CodeAnalyzer:
    """Analyzes code for issues using pattern matching."""

    def __init__(self):
        self.patterns = {
            ReviewType.SECURITY: SECURITY_PATTERNS,
            ReviewType.QUALITY: QUALITY_PATTERNS,
            ReviewType.STYLE: STYLE_PATTERNS,
            ReviewType.PERFORMANCE: PERFORMANCE_PATTERNS,
        }

    def analyze_file(
        self,
        file_path: str,
        content: str,
        review_types: List[ReviewType],
        custom_rules: List[CustomRule],
        min_severity: Severity,
    ) -> List[ReviewComment]:
        """Analyze a file and return review comments."""
        comments = []
        lines = content.split("\n")
        severity_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
        min_severity_idx = severity_order.index(min_severity)

        # Check built-in patterns
        for review_type in review_types:
            if review_type == ReviewType.CUSTOM:
                continue  # Handled separately

            patterns = self.patterns.get(review_type, [])
            for pattern, title, severity in patterns:
                if severity_order.index(severity) > min_severity_idx:
                    continue

                for line_num, line in enumerate(lines, 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        comment = self._create_comment(
                            file_path=file_path,
                            line=line_num,
                            line_content=line,
                            review_type=review_type,
                            severity=severity,
                            title=title,
                            pattern=pattern,
                        )
                        comments.append(comment)

        # Check custom rules
        for rule in custom_rules:
            if not rule.enabled:
                continue
            if severity_order.index(rule.severity) > min_severity_idx:
                continue

            if rule.pattern:
                for line_num, line in enumerate(lines, 1):
                    if re.search(rule.pattern, line, re.IGNORECASE):
                        comment = self._create_comment(
                            file_path=file_path,
                            line=line_num,
                            line_content=line,
                            review_type=ReviewType.CUSTOM,
                            severity=rule.severity,
                            title=rule.name,
                            pattern=rule.pattern,
                            body=rule.description,
                        )
                        comments.append(comment)

        return comments

    def _create_comment(
        self,
        file_path: str,
        line: int,
        line_content: str,
        review_type: ReviewType,
        severity: Severity,
        title: str,
        pattern: str,
        body: Optional[str] = None,
    ) -> ReviewComment:
        """Create a review comment with suggested fix."""
        suggested_fix = self._generate_fix(line_content, pattern, title)

        return ReviewComment(
            id=str(uuid.uuid4())[:8],
            file_path=file_path,
            line=line,
            end_line=None,
            side="RIGHT",
            review_type=review_type,
            severity=severity,
            title=title,
            body=body or f"Pattern matched: `{pattern}`",
            suggested_fix=suggested_fix,
        )

    def _generate_fix(self, line_content: str, pattern: str, title: str) -> Optional[SuggestedFix]:
        """Generate a suggested fix for the issue."""
        original = line_content.strip()
        fixed = None
        description = ""

        # Security fixes
        if "password" in title.lower() and "=" in original:
            fixed = re.sub(r"['\"][^'\"]+['\"]", 'os.environ.get("PASSWORD")', original)
            description = "Use environment variable instead of hardcoded password"

        elif "api_key" in title.lower() and "=" in original:
            fixed = re.sub(r"['\"][^'\"]+['\"]", 'os.environ.get("API_KEY")', original)
            description = "Use environment variable instead of hardcoded API key"

        elif "eval(" in title.lower():
            fixed = original.replace("eval(", "ast.literal_eval(")
            description = "Use ast.literal_eval for safe evaluation"

        # Quality fixes
        elif "bare except" in title.lower():
            fixed = original.replace("except:", "except Exception as e:")
            description = "Catch specific exceptions"

        elif "print" in title.lower() and "print(" in original:
            fixed = original.replace("print(", "logger.debug(")
            description = "Use logging instead of print"

        elif "console.log" in title.lower():
            fixed = "// " + original  # Comment out
            description = "Remove or comment out debug statement"

        # Style fixes
        elif "wildcard import" in title.lower():
            match = re.search(r"from (\w+) import \*", original)
            if match:
                module = match.group(1)
                fixed = f"from {module} import specific_function  # TODO: specify imports"
                description = "Import specific items instead of wildcard"

        if fixed and fixed != original:
            diff = self._create_diff(original, fixed)
            return SuggestedFix(
                description=description,
                original_code=original,
                fixed_code=fixed,
                diff=diff,
            )

        return None

    def _create_diff(self, original: str, fixed: str) -> str:
        """Create a unified diff format string."""
        return f"""--- a/file
+++ b/file
@@ -1 +1 @@
-{original}
+{fixed}"""


# ═══════════════════════════════════════════════════════════════════════════════
# PR Review Service
# ═══════════════════════════════════════════════════════════════════════════════

class PRReviewService:
    """Service for automated PR reviews with copy-friendly edits."""

    def __init__(self, db_path: str = "data/pr_reviews.db"):
        self.db_path = db_path
        self.config = PRReviewConfig()
        self.analyzer = CodeAnalyzer()
        self.custom_rules: List[CustomRule] = []
        self._init_db()
        self._load_config()

    def _init_db(self):
        """Initialize SQLite database."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    pr_number INTEGER NOT NULL,
                    repo_owner TEXT NOT NULL,
                    repo_name TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    summary_json TEXT,
                    comments_json TEXT,
                    tests_json TEXT,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS custom_rules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    pattern TEXT
                );

                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_reviews_pr ON reviews(pr_number);
                CREATE INDEX IF NOT EXISTS idx_reviews_repo ON reviews(repo_owner, repo_name);
                CREATE INDEX IF NOT EXISTS idx_reviews_timestamp ON reviews(timestamp DESC);
            """)

    def _load_config(self):
        """Load configuration from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT value FROM config WHERE key = 'settings'")
            row = cursor.fetchone()
            if row:
                data = json_loads(row[0])
                self.config = PRReviewConfig(
                    enabled=data.get("enabled", True),
                    review_types=[ReviewType(t) for t in data.get("reviewTypes", [])],
                    min_severity=Severity(data.get("minSeverity", "low")),
                    auto_review_on_pr=data.get("autoReviewOnPr", False),
                    include_test_suggestions=data.get("includeTestSuggestions", True),
                    max_comments_per_file=data.get("maxCommentsPerFile", 10),
                    max_total_comments=data.get("maxTotalComments", 50),
                )

            # Load custom rules
            cursor = conn.execute(f"SELECT {_CUSTOM_RULE_COLUMNS} FROM custom_rules")
            for row in cursor.fetchall():
                self.custom_rules.append(CustomRule(
                    id=row[0],
                    name=row[1],
                    description=row[2],
                    severity=Severity(row[3]),
                    enabled=bool(row[4]),
                    pattern=row[5],
                ))

    def get_config(self) -> PRReviewConfig:
        """Get current configuration."""
        return self.config

    def set_config(self, config: PRReviewConfig) -> PRReviewConfig:
        """Update configuration."""
        self.config = config

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                ("settings", json_dumps(config.to_dict()))
            )

        return self.config

    # ───────────────────────────────────────────────────────────────────────────
    # Custom Rules
    # ───────────────────────────────────────────────────────────────────────────

    def get_custom_rules(self) -> List[CustomRule]:
        """Get all custom rules."""
        return self.custom_rules

    def add_custom_rule(self, rule: CustomRule) -> CustomRule:
        """Add a custom review rule."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO custom_rules (id, name, description, severity, enabled, pattern) VALUES (?, ?, ?, ?, ?, ?)",
                (rule.id, rule.name, rule.description, rule.severity.value, rule.enabled, rule.pattern)
            )

        self.custom_rules.append(rule)
        return rule

    def update_custom_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[CustomRule]:
        """Update a custom rule."""
        for i, rule in enumerate(self.custom_rules):
            if rule.id == rule_id:
                if "name" in updates:
                    rule.name = updates["name"]
                if "description" in updates:
                    rule.description = updates["description"]
                if "severity" in updates:
                    rule.severity = Severity(updates["severity"])
                if "enabled" in updates:
                    rule.enabled = updates["enabled"]
                if "pattern" in updates:
                    rule.pattern = updates["pattern"]

                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE custom_rules SET name=?, description=?, severity=?, enabled=?, pattern=? WHERE id=?",
                        (rule.name, rule.description, rule.severity.value, rule.enabled, rule.pattern, rule_id)
                    )

                return rule
        return None

    def delete_custom_rule(self, rule_id: str) -> bool:
        """Delete a custom rule."""
        for i, rule in enumerate(self.custom_rules):
            if rule.id == rule_id:
                self.custom_rules.pop(i)

                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM custom_rules WHERE id = ?", (rule_id,))

                return True
        return False

    # ───────────────────────────────────────────────────────────────────────────
    # PR Review
    # ───────────────────────────────────────────────────────────────────────────

    def create_review(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        head_sha: str,
        base_sha: str,
        files: Dict[str, str],  # file_path -> content
        review_types: Optional[List[ReviewType]] = None,
    ) -> PRReviewResult:
        """
        Create a new PR review.

        Args:
            repo_owner: Repository owner
            repo_name: Repository name
            pr_number: PR number
            head_sha: Head commit SHA
            base_sha: Base commit SHA
            files: Dictionary of file paths to content
            review_types: Types of review to perform (default: from config)

        Returns:
            PRReviewResult with comments and suggested fixes
        """
        review_id = str(uuid.uuid4())[:12]
        timestamp = int(time.time())
        types = review_types or self.config.review_types

        # Create initial review record
        result = PRReviewResult(
            id=review_id,
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            head_sha=head_sha,
            base_sha=base_sha,
            timestamp=timestamp,
            status=ReviewStatus.ANALYZING,
            summary=None,
            comments=[],
            suggested_tests=[],
        )

        try:
            # Analyze each file
            all_comments = []
            lines_analyzed = 0

            for file_path, content in files.items():
                lines_analyzed += len(content.split("\n"))

                file_comments = self.analyzer.analyze_file(
                    file_path=file_path,
                    content=content,
                    review_types=types,
                    custom_rules=self.custom_rules,
                    min_severity=self.config.min_severity,
                )

                # Limit comments per file
                all_comments.extend(file_comments[:self.config.max_comments_per_file])

            # Limit total comments
            result.comments = all_comments[:self.config.max_total_comments]

            # Generate summary
            by_severity = {s.value: 0 for s in Severity}
            by_type = {t.value: 0 for t in ReviewType}

            for comment in result.comments:
                by_severity[comment.severity.value] += 1
                by_type[comment.review_type.value] += 1

            # Determine verdict
            if by_severity[Severity.CRITICAL.value] > 0:
                verdict = Verdict.REQUEST_CHANGES
            elif by_severity[Severity.HIGH.value] >= 3:
                verdict = Verdict.REQUEST_CHANGES
            elif len(result.comments) == 0:
                verdict = Verdict.APPROVE
            else:
                verdict = Verdict.COMMENT

            result.summary = ReviewSummary(
                total_comments=len(result.comments),
                by_severity=by_severity,
                by_type=by_type,
                verdict=verdict,
                files_reviewed=len(files),
                lines_analyzed=lines_analyzed,
            )

            result.status = ReviewStatus.COMPLETED

        except Exception as e:
            logger.error(f"Review failed: {e}")
            result.status = ReviewStatus.FAILED
            result.error_message = str(e)

        # Save to database
        self._save_review(result)

        return result

    def _save_review(self, result: PRReviewResult):
        """Save review to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO reviews
                   (id, pr_number, repo_owner, repo_name, head_sha, base_sha,
                    timestamp, status, summary_json, comments_json, tests_json, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.id,
                    result.pr_number,
                    result.repo_owner,
                    result.repo_name,
                    result.head_sha,
                    result.base_sha,
                    result.timestamp,
                    result.status.value,
                    json_dumps(result.summary.to_dict()) if result.summary else None,
                    json_dumps([c.to_dict() for c in result.comments]),
                    json_dumps([t.to_dict() for t in result.suggested_tests]),
                    result.error_message,
                )
            )

    def get_review(self, review_id: str) -> Optional[PRReviewResult]:
        """Get a review by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"SELECT {_REVIEW_COLUMNS} FROM reviews WHERE id = ?", (review_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return self._row_to_result(row)

    def get_reviews(
        self,
        repo_owner: Optional[str] = None,
        repo_name: Optional[str] = None,
        pr_number: Optional[int] = None,
        status: Optional[ReviewStatus] = None,
        limit: int = 50,
    ) -> List[PRReviewResult]:
        """Get reviews with optional filters."""
        query = f"SELECT {_REVIEW_COLUMNS} FROM reviews WHERE 1=1"
        params = []

        if repo_owner:
            query += " AND repo_owner = ?"
            params.append(repo_owner)
        if repo_name:
            query += " AND repo_name = ?"
            params.append(repo_name)
        if pr_number:
            query += " AND pr_number = ?"
            params.append(pr_number)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_result(row) for row in cursor.fetchall()]

    def _row_to_result(self, row) -> PRReviewResult:
        """Convert database row to PRReviewResult."""
        summary_data = json_loads(row[8]) if row[8] else None
        comments_data = json_loads(row[9]) if row[9] else []
        tests_data = json_loads(row[10]) if row[10] else []

        summary = None
        if summary_data:
            summary = ReviewSummary(
                total_comments=summary_data["totalComments"],
                by_severity=summary_data["bySeverity"],
                by_type=summary_data["byType"],
                verdict=Verdict(summary_data["verdict"]),
                files_reviewed=summary_data["filesReviewed"],
                lines_analyzed=summary_data["linesAnalyzed"],
            )

        comments = []
        for c in comments_data:
            suggested_fix = None
            if c.get("suggestedFix"):
                sf = c["suggestedFix"]
                suggested_fix = SuggestedFix(
                    description=sf["description"],
                    original_code=sf["original_code"],
                    fixed_code=sf["fixed_code"],
                    diff=sf["diff"],
                )

            comments.append(ReviewComment(
                id=c["id"],
                file_path=c["filePath"],
                line=c["line"],
                end_line=c.get("endLine"),
                side=c["side"],
                review_type=ReviewType(c["type"]),
                severity=Severity(c["severity"]),
                title=c["title"],
                body=c["body"],
                suggested_fix=suggested_fix,
                dismissed=c.get("dismissed", False),
                copied=c.get("copied", False),
            ))

        tests = [
            TestSuggestion(
                target_file=t["targetFile"],
                test_file=t["testFile"],
                test_code=t["testCode"],
                coverage=t["coverage"],
            )
            for t in tests_data
        ]

        return PRReviewResult(
            id=row[0],
            pr_number=row[1],
            repo_owner=row[2],
            repo_name=row[3],
            head_sha=row[4],
            base_sha=row[5],
            timestamp=row[6],
            status=ReviewStatus(row[7]),
            summary=summary,
            comments=comments,
            suggested_tests=tests,
            error_message=row[11],
        )

    # ───────────────────────────────────────────────────────────────────────────
    # Copy-Friendly Edit Formatting
    # ───────────────────────────────────────────────────────────────────────────

    def get_copyable_fixes(self, review_id: str) -> List[Dict[str, Any]]:
        """
        Get all fixes from a review in copy-friendly format.

        Returns list of fixes with:
        - file_path
        - line
        - copyable_text: Ready to paste
        - diff_text: Unified diff format
        """
        review = self.get_review(review_id)
        if not review:
            return []

        fixes = []
        for comment in review.comments:
            if comment.suggested_fix and not comment.dismissed:
                fixes.append({
                    "commentId": comment.id,
                    "filePath": comment.file_path,
                    "line": comment.line,
                    "severity": comment.severity.value,
                    "title": comment.title,
                    "copyableText": comment.suggested_fix.to_copyable_format(),
                    "diffText": comment.suggested_fix.to_diff_format(),
                    "originalCode": comment.suggested_fix.original_code,
                    "fixedCode": comment.suggested_fix.fixed_code,
                    "description": comment.suggested_fix.description,
                })

        return fixes

    def get_fix_as_patch(self, review_id: str, comment_id: str) -> Optional[str]:
        """Get a single fix as a git-style patch."""
        review = self.get_review(review_id)
        if not review:
            return None

        for comment in review.comments:
            if comment.id == comment_id and comment.suggested_fix:
                return f"""--- a/{comment.file_path}
+++ b/{comment.file_path}
@@ -{comment.line} +{comment.line} @@
-{comment.suggested_fix.original_code}
+{comment.suggested_fix.fixed_code}
"""

        return None

    def mark_copied(self, review_id: str, comment_id: str) -> bool:
        """Mark a fix as copied by user."""
        review = self.get_review(review_id)
        if not review:
            return False

        for comment in review.comments:
            if comment.id == comment_id:
                comment.copied = True
                self._save_review(review)
                return True

        return False

    def dismiss_comment(self, review_id: str, comment_id: str) -> bool:
        """Dismiss a review comment."""
        review = self.get_review(review_id)
        if not review:
            return False

        for comment in review.comments:
            if comment.id == comment_id:
                comment.dismissed = True
                self._save_review(review)
                return True

        return False

    # ───────────────────────────────────────────────────────────────────────────
    # Statistics
    # ───────────────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get review statistics."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM reviews WHERE status = 'completed'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM reviews WHERE status = 'failed'"
            ).fetchone()[0]

            # Count total comments
            total_comments = 0
            cursor = conn.execute("SELECT comments_json FROM reviews WHERE status = 'completed'")
            for row in cursor.fetchall():
                if row[0]:
                    comments = json_loads(row[0])
                    total_comments += len(comments)

            return {
                "totalReviews": total,
                "completedReviews": completed,
                "failedReviews": failed,
                "totalComments": total_comments,
                "avgCommentsPerReview": total_comments / completed if completed > 0 else 0,
                "customRulesCount": len(self.custom_rules),
            }


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton Access
# ═══════════════════════════════════════════════════════════════════════════════

_pr_review_service: Optional[PRReviewService] = None


def get_pr_review_service(db_path: str = "data/pr_reviews.db") -> PRReviewService:
    """Get or create the PR review service singleton."""
    global _pr_review_service
    if _pr_review_service is None:
        _pr_review_service = PRReviewService(db_path)
    return _pr_review_service
