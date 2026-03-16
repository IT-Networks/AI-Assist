"""
Tests for PR Review Service.
"""

import json
import os
import tempfile
import pytest

from app.services.pr_review import (
    PRReviewService,
    PRReviewConfig,
    ReviewType,
    Severity,
    ReviewStatus,
    Verdict,
    CustomRule,
    CodeAnalyzer,
    SuggestedFix,
    ReviewComment,
    get_pr_review_service,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_db():
    """Create temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Windows: SQLite may not release file locks immediately
    try:
        if os.path.exists(path):
            os.unlink(path)
    except PermissionError:
        pass  # Will be cleaned up by OS temp cleanup


@pytest.fixture
def service(temp_db):
    """Create test service with temporary database."""
    return PRReviewService(db_path=temp_db)


@pytest.fixture
def sample_files():
    """Sample files for testing."""
    return {
        "src/auth.py": '''
password = "admin123"
api_key = "sk-12345"

def login(user, pwd):
    if eval(user):
        return True
    except:
        pass
    print("Debug: login attempt")
''',
        "src/utils.js": '''
function getData() {
    console.log("fetching data");
    document.innerHTML = data;
}
''',
        "src/db.py": '''
def query(sql):
    return execute("SELECT * FROM users WHERE id = " + user_id)
''',
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPRReviewConfig:
    """Tests for PR review configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = PRReviewConfig()

        assert config.enabled == True
        assert ReviewType.SECURITY in config.review_types
        assert ReviewType.QUALITY in config.review_types
        assert config.min_severity == Severity.LOW
        assert config.auto_review_on_pr == False
        assert config.max_comments_per_file == 10
        assert config.max_total_comments == 50

    def test_config_to_dict(self):
        """Test configuration serialization."""
        config = PRReviewConfig(
            enabled=False,
            review_types=[ReviewType.SECURITY],
            min_severity=Severity.HIGH,
        )

        data = config.to_dict()

        assert data["enabled"] == False
        assert "security" in data["reviewTypes"]
        assert data["minSeverity"] == "high"

    def test_get_config(self, service):
        """Test getting configuration."""
        config = service.get_config()

        assert config is not None
        assert isinstance(config, PRReviewConfig)

    def test_set_config(self, service):
        """Test setting configuration."""
        new_config = PRReviewConfig(
            enabled=True,
            review_types=[ReviewType.SECURITY, ReviewType.PERFORMANCE],
            min_severity=Severity.MEDIUM,
            max_comments_per_file=5,
        )

        result = service.set_config(new_config)

        assert result.enabled == True
        assert ReviewType.PERFORMANCE in result.review_types
        assert result.min_severity == Severity.MEDIUM
        assert result.max_comments_per_file == 5

    def test_config_persists(self, temp_db):
        """Test configuration persists across service instances."""
        service1 = PRReviewService(db_path=temp_db)
        service1.set_config(PRReviewConfig(
            min_severity=Severity.CRITICAL,
            max_total_comments=25,
        ))

        service2 = PRReviewService(db_path=temp_db)
        config = service2.get_config()

        assert config.min_severity == Severity.CRITICAL
        assert config.max_total_comments == 25


# ═══════════════════════════════════════════════════════════════════════════════
# Code Analyzer Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCodeAnalyzer:
    """Tests for code analyzer."""

    def test_detect_hardcoded_password(self):
        """Test detection of hardcoded passwords."""
        analyzer = CodeAnalyzer()
        content = 'password = "secret123"'

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.SECURITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) == 1
        assert comments[0].severity == Severity.CRITICAL
        assert "password" in comments[0].title.lower()

    def test_detect_api_key(self):
        """Test detection of hardcoded API keys."""
        analyzer = CodeAnalyzer()
        content = 'api_key = "sk-12345"'

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.SECURITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) == 1
        assert "api" in comments[0].title.lower()

    def test_detect_eval(self):
        """Test detection of eval usage."""
        analyzer = CodeAnalyzer()
        content = 'result = eval(user_input)'

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.SECURITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) == 1
        assert "eval" in comments[0].title.lower()

    def test_detect_bare_except(self):
        """Test detection of bare except clauses."""
        analyzer = CodeAnalyzer()
        content = '''
try:
    risky()
except:
    pass
'''

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.QUALITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) >= 1
        except_comments = [c for c in comments if "except" in c.title.lower()]
        assert len(except_comments) >= 1

    def test_detect_print_statement(self):
        """Test detection of print statements."""
        analyzer = CodeAnalyzer()
        content = 'print("debug")'

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.QUALITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) >= 1
        print_comments = [c for c in comments if "print" in c.title.lower()]
        assert len(print_comments) >= 1

    def test_detect_console_log(self):
        """Test detection of console.log."""
        analyzer = CodeAnalyzer()
        content = 'console.log("debug")'

        comments = analyzer.analyze_file(
            file_path="test.js",
            content=content,
            review_types=[ReviewType.QUALITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) >= 1

    def test_detect_xss_risk(self):
        """Test detection of XSS risks."""
        analyzer = CodeAnalyzer()
        content = 'element.innerHTML = userInput'

        comments = analyzer.analyze_file(
            file_path="test.js",
            content=content,
            review_types=[ReviewType.SECURITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        assert len(comments) >= 1
        xss_comments = [c for c in comments if "xss" in c.title.lower() or "innerHTML" in c.title]
        assert len(xss_comments) >= 1

    def test_min_severity_filter(self):
        """Test minimum severity filtering."""
        analyzer = CodeAnalyzer()
        content = '''
password = "secret"
print("debug")
'''

        # With INFO min_severity, should get both
        comments_all = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.SECURITY, ReviewType.QUALITY],
            custom_rules=[],
            min_severity=Severity.INFO,
        )

        # With CRITICAL min_severity, should only get password
        comments_critical = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.SECURITY, ReviewType.QUALITY],
            custom_rules=[],
            min_severity=Severity.CRITICAL,
        )

        assert len(comments_all) >= 2
        assert len(comments_critical) == 1
        assert comments_critical[0].severity == Severity.CRITICAL

    def test_custom_rule_detection(self):
        """Test custom rule pattern matching."""
        analyzer = CodeAnalyzer()
        content = 'FORBIDDEN_KEYWORD = "test"'

        custom_rule = CustomRule(
            id="rule1",
            name="Forbidden keyword",
            description="This keyword is not allowed",
            severity=Severity.HIGH,
            enabled=True,
            pattern=r"FORBIDDEN_KEYWORD",
        )

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.CUSTOM],
            custom_rules=[custom_rule],
            min_severity=Severity.INFO,
        )

        assert len(comments) == 1
        assert comments[0].title == "Forbidden keyword"
        assert comments[0].severity == Severity.HIGH

    def test_disabled_custom_rule(self):
        """Test disabled custom rules are skipped."""
        analyzer = CodeAnalyzer()
        content = 'FORBIDDEN_KEYWORD = "test"'

        custom_rule = CustomRule(
            id="rule1",
            name="Forbidden keyword",
            description="This keyword is not allowed",
            severity=Severity.HIGH,
            enabled=False,  # Disabled
            pattern=r"FORBIDDEN_KEYWORD",
        )

        comments = analyzer.analyze_file(
            file_path="test.py",
            content=content,
            review_types=[ReviewType.CUSTOM],
            custom_rules=[custom_rule],
            min_severity=Severity.INFO,
        )

        assert len(comments) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Suggested Fix Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuggestedFix:
    """Tests for suggested fix formatting."""

    def test_suggested_fix_to_dict(self):
        """Test suggested fix serialization."""
        fix = SuggestedFix(
            description="Use environment variable",
            original_code='password = "secret"',
            fixed_code='password = os.environ.get("PASSWORD")',
            diff="--- a\n+++ b\n-old\n+new",
        )

        data = fix.to_dict()

        assert data["description"] == "Use environment variable"
        assert "secret" in data["original_code"]
        assert "environ" in data["fixed_code"]

    def test_to_copyable_format(self):
        """Test copyable format generation."""
        fix = SuggestedFix(
            description="Use environment variable",
            original_code='password = "secret"',
            fixed_code='password = os.environ.get("PASSWORD")',
            diff="",
        )

        copyable = fix.to_copyable_format()

        assert "// Use environment variable" in copyable
        assert "Replace this:" in copyable
        assert "With this:" in copyable
        assert 'password = "secret"' in copyable
        assert "environ" in copyable

    def test_to_diff_format(self):
        """Test diff format."""
        diff = "--- a/file\n+++ b/file\n-old\n+new"
        fix = SuggestedFix(
            description="Test",
            original_code="old",
            fixed_code="new",
            diff=diff,
        )

        assert fix.to_diff_format() == diff


# ═══════════════════════════════════════════════════════════════════════════════
# PR Review Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPRReview:
    """Tests for PR review creation and retrieval."""

    def test_create_review(self, service, sample_files):
        """Test creating a new review."""
        result = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=123,
            head_sha="abc1234",
            base_sha="def5678",
            files=sample_files,
        )

        assert result.id is not None
        assert result.pr_number == 123
        assert result.repo_owner == "test-org"
        assert result.status == ReviewStatus.COMPLETED
        assert result.summary is not None
        assert len(result.comments) > 0

    def test_review_detects_issues(self, service, sample_files):
        """Test that review detects security issues."""
        result = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=1,
            head_sha="abc",
            base_sha="def",
            files=sample_files,
        )

        # Should detect hardcoded password, api_key, eval, etc.
        critical_comments = [c for c in result.comments if c.severity == Severity.CRITICAL]
        assert len(critical_comments) >= 2  # password and api_key

    def test_review_generates_fixes(self, service, sample_files):
        """Test that review generates suggested fixes."""
        result = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=1,
            head_sha="abc",
            base_sha="def",
            files=sample_files,
        )

        comments_with_fixes = [c for c in result.comments if c.suggested_fix is not None]
        assert len(comments_with_fixes) > 0

    def test_review_summary_counts(self, service, sample_files):
        """Test review summary counts."""
        result = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=1,
            head_sha="abc",
            base_sha="def",
            files=sample_files,
        )

        assert result.summary.total_comments == len(result.comments)
        assert result.summary.files_reviewed == len(sample_files)
        assert result.summary.lines_analyzed > 0

    def test_review_verdict(self, service, sample_files):
        """Test review verdict determination."""
        result = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=1,
            head_sha="abc",
            base_sha="def",
            files=sample_files,
        )

        # Should request changes due to critical issues
        assert result.summary.verdict == Verdict.REQUEST_CHANGES

    def test_review_approve_clean_code(self, service):
        """Test review approves clean code."""
        clean_files = {
            "src/clean.py": '''
def add(a, b):
    """Add two numbers."""
    return a + b
'''
        }

        result = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=1,
            head_sha="abc",
            base_sha="def",
            files=clean_files,
        )

        assert result.summary.verdict == Verdict.APPROVE
        assert result.summary.total_comments == 0

    def test_get_review(self, service, sample_files):
        """Test retrieving a review."""
        created = service.create_review(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=1,
            head_sha="abc",
            base_sha="def",
            files=sample_files,
        )

        retrieved = service.get_review(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.pr_number == created.pr_number
        assert len(retrieved.comments) == len(created.comments)

    def test_get_nonexistent_review(self, service):
        """Test getting nonexistent review."""
        result = service.get_review("nonexistent-id")
        assert result is None

    def test_get_reviews_with_filters(self, service, sample_files):
        """Test getting reviews with filters."""
        # Create multiple reviews
        service.create_review(
            repo_owner="org1", repo_name="repo1", pr_number=1,
            head_sha="a", base_sha="b", files=sample_files,
        )
        service.create_review(
            repo_owner="org1", repo_name="repo2", pr_number=2,
            head_sha="c", base_sha="d", files=sample_files,
        )
        service.create_review(
            repo_owner="org2", repo_name="repo1", pr_number=3,
            head_sha="e", base_sha="f", files=sample_files,
        )

        # Filter by owner
        org1_reviews = service.get_reviews(repo_owner="org1")
        assert len(org1_reviews) == 2

        # Filter by repo
        repo1_reviews = service.get_reviews(repo_name="repo1")
        assert len(repo1_reviews) == 2

        # Filter by PR number
        pr1_reviews = service.get_reviews(pr_number=1)
        assert len(pr1_reviews) == 1

    def test_max_comments_per_file(self, service):
        """Test max comments per file limit."""
        # Create file with many issues
        problematic_file = "\n".join([
            f'password{i} = "secret{i}"'
            for i in range(20)
        ])

        service.set_config(PRReviewConfig(max_comments_per_file=5))

        result = service.create_review(
            repo_owner="test", repo_name="test", pr_number=1,
            head_sha="a", base_sha="b",
            files={"test.py": problematic_file},
        )

        # Should be limited to 5
        assert len(result.comments) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# Copyable Fixes Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCopyableFixes:
    """Tests for copy-friendly fix formatting."""

    def test_get_copyable_fixes(self, service, sample_files):
        """Test getting copyable fixes."""
        review = service.create_review(
            repo_owner="test", repo_name="test", pr_number=1,
            head_sha="a", base_sha="b", files=sample_files,
        )

        fixes = service.get_copyable_fixes(review.id)

        assert len(fixes) > 0
        for fix in fixes:
            assert "commentId" in fix
            assert "filePath" in fix
            assert "copyableText" in fix
            assert "diffText" in fix

    def test_get_fix_as_patch(self, service, sample_files):
        """Test getting fix as git patch."""
        review = service.create_review(
            repo_owner="test", repo_name="test", pr_number=1,
            head_sha="a", base_sha="b", files=sample_files,
        )

        comments_with_fixes = [c for c in review.comments if c.suggested_fix]
        if comments_with_fixes:
            patch = service.get_fix_as_patch(review.id, comments_with_fixes[0].id)

            assert patch is not None
            assert "---" in patch
            assert "+++" in patch

    def test_mark_copied(self, service, sample_files):
        """Test marking fix as copied."""
        review = service.create_review(
            repo_owner="test", repo_name="test", pr_number=1,
            head_sha="a", base_sha="b", files=sample_files,
        )

        comment = review.comments[0]
        success = service.mark_copied(review.id, comment.id)

        assert success == True

        # Verify it was saved
        updated_review = service.get_review(review.id)
        updated_comment = next(c for c in updated_review.comments if c.id == comment.id)
        assert updated_comment.copied == True

    def test_dismiss_comment(self, service, sample_files):
        """Test dismissing a comment."""
        review = service.create_review(
            repo_owner="test", repo_name="test", pr_number=1,
            head_sha="a", base_sha="b", files=sample_files,
        )

        comment = review.comments[0]
        success = service.dismiss_comment(review.id, comment.id)

        assert success == True

        # Verify dismissed comments are excluded from fixes
        fixes = service.get_copyable_fixes(review.id)
        dismissed_fix = [f for f in fixes if f["commentId"] == comment.id]
        assert len(dismissed_fix) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Rules Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCustomRules:
    """Tests for custom review rules."""

    def test_add_custom_rule(self, service):
        """Test adding a custom rule."""
        rule = CustomRule(
            id="test-rule",
            name="No TODO",
            description="TODO comments are not allowed",
            severity=Severity.LOW,
            enabled=True,
            pattern=r"TODO",
        )

        result = service.add_custom_rule(rule)

        assert result.id == "test-rule"
        assert result.name == "No TODO"

    def test_get_custom_rules(self, service):
        """Test getting custom rules."""
        service.add_custom_rule(CustomRule(
            id="rule1", name="Rule 1", description="Desc 1",
            severity=Severity.LOW, enabled=True, pattern=None,
        ))
        service.add_custom_rule(CustomRule(
            id="rule2", name="Rule 2", description="Desc 2",
            severity=Severity.HIGH, enabled=True, pattern=None,
        ))

        rules = service.get_custom_rules()

        assert len(rules) == 2

    def test_update_custom_rule(self, service):
        """Test updating a custom rule."""
        service.add_custom_rule(CustomRule(
            id="rule1", name="Original", description="Original desc",
            severity=Severity.LOW, enabled=True, pattern=None,
        ))

        result = service.update_custom_rule("rule1", {
            "name": "Updated",
            "severity": "high",
        })

        assert result is not None
        assert result.name == "Updated"
        assert result.severity == Severity.HIGH

    def test_delete_custom_rule(self, service):
        """Test deleting a custom rule."""
        service.add_custom_rule(CustomRule(
            id="rule1", name="Test", description="Test",
            severity=Severity.LOW, enabled=True, pattern=None,
        ))

        success = service.delete_custom_rule("rule1")
        assert success == True

        rules = service.get_custom_rules()
        assert len(rules) == 0

    def test_custom_rules_persist(self, temp_db):
        """Test custom rules persist across service instances."""
        service1 = PRReviewService(db_path=temp_db)
        service1.add_custom_rule(CustomRule(
            id="persistent", name="Persistent Rule", description="Persists",
            severity=Severity.MEDIUM, enabled=True, pattern=r"test",
        ))

        service2 = PRReviewService(db_path=temp_db)
        rules = service2.get_custom_rules()

        assert len(rules) == 1
        assert rules[0].name == "Persistent Rule"


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatistics:
    """Tests for review statistics."""

    def test_get_stats_empty(self, service):
        """Test stats with no reviews."""
        stats = service.get_stats()

        assert stats["totalReviews"] == 0
        assert stats["completedReviews"] == 0
        assert stats["totalComments"] == 0

    def test_get_stats_with_reviews(self, service, sample_files):
        """Test stats with reviews."""
        service.create_review(
            repo_owner="test", repo_name="test", pr_number=1,
            head_sha="a", base_sha="b", files=sample_files,
        )
        service.create_review(
            repo_owner="test", repo_name="test", pr_number=2,
            head_sha="c", base_sha="d", files=sample_files,
        )

        stats = service.get_stats()

        assert stats["totalReviews"] == 2
        assert stats["completedReviews"] == 2
        assert stats["totalComments"] > 0
        assert stats["avgCommentsPerReview"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestReviewsAPI:
    """Tests for Reviews API endpoints."""

    @pytest.fixture
    def client(self, temp_db):
        """Create test client with temporary database."""
        import app.services.pr_review as module
        original_service = module._pr_review_service
        module._pr_review_service = PRReviewService(db_path=temp_db)

        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        yield client

        module._pr_review_service = original_service

    def test_get_config(self, client):
        """Test GET /api/reviews/config endpoint."""
        response = client.get("/api/reviews/config")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "reviewTypes" in data

    def test_set_config(self, client):
        """Test PUT /api/reviews/config endpoint."""
        response = client.put("/api/reviews/config", json={
            "enabled": True,
            "reviewTypes": ["security", "quality"],
            "minSeverity": "medium",
            "autoReviewOnPr": False,
            "includeTestSuggestions": True,
            "maxCommentsPerFile": 15,
            "maxTotalComments": 75,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["minSeverity"] == "medium"
        assert data["maxCommentsPerFile"] == 15

    def test_trigger_review(self, client):
        """Test POST /api/reviews/trigger endpoint."""
        response = client.post("/api/reviews/trigger", json={
            "repoOwner": "test-org",
            "repoName": "test-repo",
            "prNumber": 123,
            "headSha": "abc1234567",
            "baseSha": "def7654321",
            "files": {
                "test.py": 'password = "secret"',
            },
        })

        assert response.status_code == 200
        data = response.json()
        assert data["prNumber"] == 123
        assert data["status"] == "completed"
        assert "comments" in data

    def test_get_review(self, client):
        """Test GET /api/reviews/{id} endpoint."""
        # First create a review
        create_response = client.post("/api/reviews/trigger", json={
            "repoOwner": "test",
            "repoName": "test",
            "prNumber": 1,
            "headSha": "abc1234",
            "baseSha": "def5678",
            "files": {"test.py": "x = 1"},
        })
        review_id = create_response.json()["id"]

        # Then get it
        response = client.get(f"/api/reviews/{review_id}")

        assert response.status_code == 200
        assert response.json()["id"] == review_id

    def test_get_reviews(self, client):
        """Test GET /api/reviews/list endpoint."""
        # Create some reviews
        client.post("/api/reviews/trigger", json={
            "repoOwner": "org1", "repoName": "repo1", "prNumber": 1,
            "headSha": "abc1234", "baseSha": "def5678", "files": {"test.py": "x = 1"},
        })

        response = client.get("/api/reviews/list")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_copyable_fixes(self, client):
        """Test GET /api/reviews/{id}/fixes endpoint."""
        create_response = client.post("/api/reviews/trigger", json={
            "repoOwner": "test",
            "repoName": "test",
            "prNumber": 1,
            "headSha": "abc1234",
            "baseSha": "def5678",
            "files": {"test.py": 'password = "secret"'},
        })
        review_id = create_response.json()["id"]

        response = client.get(f"/api/reviews/{review_id}/fixes")

        assert response.status_code == 200
        fixes = response.json()
        assert isinstance(fixes, list)

    def test_mark_fix_copied(self, client):
        """Test POST /api/reviews/{id}/fixes/{comment_id}/copied endpoint."""
        create_response = client.post("/api/reviews/trigger", json={
            "repoOwner": "test",
            "repoName": "test",
            "prNumber": 1,
            "headSha": "abc1234",
            "baseSha": "def5678",
            "files": {"test.py": 'password = "secret"'},
        })
        data = create_response.json()
        review_id = data["id"]
        comment_id = data["comments"][0]["id"]

        response = client.post(f"/api/reviews/{review_id}/fixes/{comment_id}/copied")

        assert response.status_code == 200
        assert response.json()["success"] == True

    def test_dismiss_comment(self, client):
        """Test POST /api/reviews/{id}/comments/{comment_id}/dismiss endpoint."""
        create_response = client.post("/api/reviews/trigger", json={
            "repoOwner": "test",
            "repoName": "test",
            "prNumber": 1,
            "headSha": "abc1234",
            "baseSha": "def5678",
            "files": {"test.py": 'password = "secret"'},
        })
        data = create_response.json()
        review_id = data["id"]
        comment_id = data["comments"][0]["id"]

        response = client.post(f"/api/reviews/{review_id}/comments/{comment_id}/dismiss")

        assert response.status_code == 200
        assert response.json()["success"] == True

    def test_get_stats(self, client):
        """Test GET /api/reviews/stats endpoint."""
        response = client.get("/api/reviews/stats")

        assert response.status_code == 200
        data = response.json()
        assert "totalReviews" in data
        assert "completedReviews" in data

    def test_create_custom_rule(self, client):
        """Test POST /api/reviews/rules endpoint."""
        response = client.post("/api/reviews/rules", json={
            "name": "No TODO",
            "description": "TODO comments not allowed",
            "severity": "low",
            "enabled": True,
            "pattern": r"TODO",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "No TODO"
        assert "id" in data

    def test_get_custom_rules(self, client):
        """Test GET /api/reviews/rules endpoint."""
        # Create a rule first
        client.post("/api/reviews/rules", json={
            "name": "Test Rule",
            "description": "Test",
            "severity": "medium",
            "enabled": True,
        })

        response = client.get("/api/reviews/rules")

        assert response.status_code == 200
        rules = response.json()
        assert len(rules) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_pr_review_service_singleton(self, temp_db):
        """Test singleton returns same instance."""
        import app.services.pr_review as module
        module._pr_review_service = None

        service1 = get_pr_review_service(temp_db)
        service2 = get_pr_review_service(temp_db)

        assert service1 is service2

        module._pr_review_service = None
