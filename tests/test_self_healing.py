"""
Tests for Self-Healing Code Service and API.

Tests cover:
- Configuration management
- Error analysis and pattern matching
- Fix generation
- Fix application and dismissal
- Statistics
- API endpoints
"""

import json
import os
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.self_healing import (
    SelfHealingEngine,
    SelfHealingConfig,
    AutoApplyLevel,
    FixType,
    HealingStatus,
    ToolError,
    SuggestedFix,
    HealingAttempt,
    CodeChange,
    FixGenerator,
    get_self_healing_engine,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_healing.db")
        yield db_path


@pytest.fixture
def engine(temp_db):
    """Create a SelfHealingEngine with temporary database."""
    return SelfHealingEngine(db_path=temp_db)


@pytest.fixture
def configured_engine(engine):
    """Engine with custom configuration."""
    config = SelfHealingConfig(
        enabled=True,
        auto_apply_level=AutoApplyLevel.SAFE,
        max_retries=3,
        min_confidence_for_auto=0.8,
    )
    engine.set_config(config)
    return engine


# ═══════════════════════════════════════════════════════════════════════════════
# Data Class Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelfHealingConfig:
    """Tests for SelfHealingConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SelfHealingConfig()

        assert config.enabled == True
        assert config.auto_apply_level == AutoApplyLevel.SAFE
        assert config.max_retries == 3
        assert config.retry_delay_ms == 1000
        assert config.min_confidence_for_auto == 0.8

    def test_to_dict(self):
        """Test config serialization."""
        config = SelfHealingConfig(
            enabled=False,
            auto_apply_level=AutoApplyLevel.NONE,
            max_retries=5,
        )

        data = config.to_dict()

        assert data["enabled"] == False
        assert data["autoApplyLevel"] == "none"
        assert data["maxRetries"] == 5

    def test_from_dict(self):
        """Test config deserialization."""
        data = {
            "enabled": True,
            "autoApplyLevel": "all",
            "maxRetries": 2,
            "excludedTools": ["tool1", "tool2"],
        }

        config = SelfHealingConfig.from_dict(data)

        assert config.enabled == True
        assert config.auto_apply_level == AutoApplyLevel.ALL
        assert config.max_retries == 2
        assert config.excluded_tools == ["tool1", "tool2"]


class TestToolError:
    """Tests for ToolError dataclass."""

    def test_from_tool_result_simple(self):
        """Test creating ToolError from simple error."""
        error = ToolError.from_tool_result(
            "compile_validate",
            "SyntaxError: Missing semicolon"
        )

        assert error.tool == "compile_validate"
        assert error.error_type == "SyntaxError"
        assert "semicolon" in error.error_message

    def test_from_tool_result_with_file(self):
        """Test creating ToolError with file path."""
        error = ToolError.from_tool_result(
            "compile_validate",
            "Error in file 'src/Main.java': SyntaxError at line 42"
        )

        assert error.tool == "compile_validate"
        assert error.file_path == "src/Main.java"
        assert error.line_number == 42

    def test_to_dict(self):
        """Test ToolError serialization."""
        error = ToolError(
            tool="test_tool",
            error_type="TestError",
            error_message="Test message",
            file_path="test.py",
            line_number=10,
        )

        data = error.to_dict()

        assert data["tool"] == "test_tool"
        assert data["errorType"] == "TestError"
        assert data["context"]["filePath"] == "test.py"


class TestSuggestedFix:
    """Tests for SuggestedFix dataclass."""

    def test_create_fix(self):
        """Test creating a suggested fix."""
        fix = SuggestedFix(
            id="fix-1",
            fix_type=FixType.EDIT_FILE,
            description="Add semicolon",
            confidence=0.95,
            safe_to_auto_apply=True,
        )

        assert fix.id == "fix-1"
        assert fix.fix_type == FixType.EDIT_FILE
        assert fix.safe_to_auto_apply == True

    def test_fix_with_changes(self):
        """Test fix with code changes."""
        fix = SuggestedFix(
            id="fix-2",
            fix_type=FixType.EDIT_FILE,
            description="Fix import",
            changes=[
                CodeChange(
                    file_path="test.py",
                    line_number=1,
                    new_content="import os",
                )
            ],
            confidence=0.9,
        )

        assert len(fix.changes) == 1
        assert fix.changes[0].file_path == "test.py"

    def test_to_dict(self):
        """Test fix serialization."""
        fix = SuggestedFix(
            id="fix-3",
            fix_type=FixType.RUN_COMMAND,
            description="Install package",
            command="pip install pytest",
            confidence=0.8,
        )

        data = fix.to_dict()

        assert data["type"] == "run_command"
        assert data["command"] == "pip install pytest"


# ═══════════════════════════════════════════════════════════════════════════════
# Fix Generator Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixGenerator:
    """Tests for FixGenerator pattern matching."""

    def test_semicolon_fix(self):
        """Test semicolon fix generation."""
        error = ToolError(
            tool="compile",
            error_type="SyntaxError",
            error_message="error: ';' expected",
            file_path="Test.java",
            line_number=10,
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is not None
        assert fix.fix_type == FixType.EDIT_FILE
        assert "semicolon" in fix.description.lower()
        assert fix.confidence >= 0.9

    def test_indentation_fix(self):
        """Test indentation fix generation."""
        error = ToolError(
            tool="python",
            error_type="IndentationError",
            error_message="IndentationError: unexpected indent",
            file_path="test.py",
            line_number=5,
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is not None
        assert fix.fix_type == FixType.EDIT_FILE
        assert "indentation" in fix.description.lower()

    def test_missing_import_fix(self):
        """Test missing import fix generation."""
        error = ToolError(
            tool="python",
            error_type="ImportError",
            error_message="ImportError: No module named 'requests'",
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is not None
        assert fix.fix_type == FixType.EDIT_FILE
        assert "requests" in fix.description

    def test_missing_module_fix(self):
        """Test missing module fix generation."""
        error = ToolError(
            tool="python",
            error_type="ModuleNotFoundError",
            error_message="ModuleNotFoundError: No module named 'flask'",
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is not None
        assert fix.fix_type == FixType.INSTALL_DEPENDENCY
        assert "pip install flask" in fix.command

    def test_npm_module_fix(self):
        """Test npm module fix generation."""
        error = ToolError(
            tool="node",
            error_type="Error",
            error_message="Cannot find module 'express'",
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is not None
        assert fix.fix_type == FixType.INSTALL_DEPENDENCY
        assert "npm install express" in fix.command

    def test_connection_error_fix(self):
        """Test connection error fix generation."""
        error = ToolError(
            tool="http",
            error_type="ConnectionError",
            error_message="ConnectionError: ECONNREFUSED",
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is not None
        assert fix.fix_type == FixType.RETRY

    def test_unknown_error_no_fix(self):
        """Test unknown error returns no fix."""
        error = ToolError(
            tool="unknown",
            error_type="UnknownError",
            error_message="Some random error message",
        )

        fix = FixGenerator.generate_fix(error)

        assert fix is None


# ═══════════════════════════════════════════════════════════════════════════════
# Engine Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelfHealingEngineInit:
    """Tests for SelfHealingEngine initialization."""

    def test_creates_database(self, temp_db):
        """Test database is created on init."""
        engine = SelfHealingEngine(db_path=temp_db)
        assert Path(temp_db).exists()

    def test_creates_tables(self, engine):
        """Test required tables are created."""
        conn = engine._get_conn()
        cursor = conn.cursor()

        # Check healing_config table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='healing_config'"
        )
        assert cursor.fetchone() is not None

        # Check healing_attempts table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='healing_attempts'"
        )
        assert cursor.fetchone() is not None

        conn.close()


class TestConfiguration:
    """Tests for configuration management."""

    def test_get_default_config(self, engine):
        """Test getting default configuration."""
        config = engine.get_config()

        assert config.enabled == True
        assert config.auto_apply_level == AutoApplyLevel.SAFE
        assert config.max_retries == 3

    def test_set_config(self, engine):
        """Test setting configuration."""
        config = SelfHealingConfig(
            enabled=False,
            auto_apply_level=AutoApplyLevel.ALL,
            max_retries=5,
            excluded_tools=["tool1"],
        )

        result = engine.set_config(config)

        assert result.enabled == False
        assert result.auto_apply_level == AutoApplyLevel.ALL
        assert result.max_retries == 5

    def test_config_persists(self, temp_db):
        """Test configuration persists across instances."""
        engine1 = SelfHealingEngine(db_path=temp_db)
        config = SelfHealingConfig(
            enabled=False,
            max_retries=10,
        )
        engine1.set_config(config)

        # Create new instance
        engine2 = SelfHealingEngine(db_path=temp_db)
        retrieved = engine2.get_config()

        assert retrieved.enabled == False
        assert retrieved.max_retries == 10


class TestErrorAnalysis:
    """Tests for error analysis."""

    def test_analyze_simple_error(self, configured_engine):
        """Test analyzing a simple error."""
        attempt = configured_engine.analyze_error(
            tool_name="compile",
            error="error: ';' expected",
            session_id="test-session",
        )

        assert attempt is not None
        assert attempt.session_id == "test-session"
        assert attempt.suggested_fix is not None

    def test_analyze_excluded_tool(self, engine):
        """Test excluded tools are skipped."""
        config = SelfHealingConfig(
            enabled=True,
            excluded_tools=["excluded_tool"],
        )
        engine.set_config(config)

        attempt = engine.analyze_error(
            tool_name="excluded_tool",
            error="Some error",
        )

        assert attempt is None

    def test_analyze_when_disabled(self, engine):
        """Test analysis when disabled."""
        config = SelfHealingConfig(enabled=False)
        engine.set_config(config)

        attempt = engine.analyze_error(
            tool_name="test",
            error="Some error",
        )

        assert attempt is None

    def test_analyze_unknown_error(self, configured_engine):
        """Test analyzing unknown error returns None."""
        attempt = configured_engine.analyze_error(
            tool_name="unknown",
            error="Completely unknown error type xyz",
        )

        assert attempt is None


class TestAutoApply:
    """Tests for auto-apply logic."""

    def test_should_auto_apply_safe_fix(self, engine):
        """Test safe fix should auto-apply in safe mode."""
        config = SelfHealingConfig(
            auto_apply_level=AutoApplyLevel.SAFE,
            min_confidence_for_auto=0.8,
        )
        engine.set_config(config)

        attempt = HealingAttempt(
            id="test",
            timestamp=0,
            session_id="test",
            suggested_fix=SuggestedFix(
                id="fix",
                fix_type=FixType.EDIT_FILE,
                description="Safe fix",
                confidence=0.9,
                safe_to_auto_apply=True,
            ),
        )

        assert engine.should_auto_apply(attempt) == True

    def test_should_not_auto_apply_unsafe_fix(self, engine):
        """Test unsafe fix should not auto-apply in safe mode."""
        config = SelfHealingConfig(
            auto_apply_level=AutoApplyLevel.SAFE,
        )
        engine.set_config(config)

        attempt = HealingAttempt(
            id="test",
            timestamp=0,
            session_id="test",
            suggested_fix=SuggestedFix(
                id="fix",
                fix_type=FixType.RUN_COMMAND,
                description="Unsafe fix",
                confidence=0.9,
                safe_to_auto_apply=False,
            ),
        )

        assert engine.should_auto_apply(attempt) == False

    def test_should_auto_apply_all_mode(self, engine):
        """Test all fixes auto-apply in all mode."""
        config = SelfHealingConfig(
            auto_apply_level=AutoApplyLevel.ALL,
        )
        engine.set_config(config)

        attempt = HealingAttempt(
            id="test",
            timestamp=0,
            session_id="test",
            suggested_fix=SuggestedFix(
                id="fix",
                fix_type=FixType.RUN_COMMAND,
                description="Any fix",
                confidence=0.5,
                safe_to_auto_apply=False,
            ),
        )

        assert engine.should_auto_apply(attempt) == True

    def test_should_not_auto_apply_none_mode(self, engine):
        """Test no fixes auto-apply in none mode."""
        config = SelfHealingConfig(
            auto_apply_level=AutoApplyLevel.NONE,
        )
        engine.set_config(config)

        attempt = HealingAttempt(
            id="test",
            timestamp=0,
            session_id="test",
            suggested_fix=SuggestedFix(
                id="fix",
                fix_type=FixType.EDIT_FILE,
                description="Safe fix",
                confidence=1.0,
                safe_to_auto_apply=True,
            ),
        )

        assert engine.should_auto_apply(attempt) == False


class TestFixApplication:
    """Tests for fix application."""

    def test_dismiss_fix(self, configured_engine):
        """Test dismissing a fix."""
        attempt = configured_engine.analyze_error(
            tool_name="compile",
            error="error: ';' expected",
        )

        assert attempt is not None

        success = configured_engine.dismiss_fix(attempt.id)
        assert success == True

        # Check status
        loaded = configured_engine._load_attempt(attempt.id)
        assert loaded.status == HealingStatus.DISMISSED

    def test_dismiss_nonexistent(self, configured_engine):
        """Test dismissing nonexistent attempt."""
        success = configured_engine.dismiss_fix("nonexistent-id")
        assert success == False


class TestPersistence:
    """Tests for data persistence."""

    def test_save_and_load_attempt(self, configured_engine):
        """Test saving and loading attempts."""
        attempt = configured_engine.analyze_error(
            tool_name="compile",
            error="error: ';' expected",
            session_id="test-session",
        )

        assert attempt is not None

        # Load and verify
        loaded = configured_engine._load_attempt(attempt.id)

        assert loaded is not None
        assert loaded.id == attempt.id
        assert loaded.session_id == "test-session"

    def test_get_attempts(self, configured_engine):
        """Test getting attempts with filters."""
        # Create some attempts with errors that will generate fixes
        attempt1 = configured_engine.analyze_error(
            tool_name="compile",
            error="error: ';' expected",
            session_id="session-1",
        )
        attempt2 = configured_engine.analyze_error(
            tool_name="node",
            error="Cannot find module 'express'",
            session_id="session-2",
        )

        assert attempt1 is not None
        assert attempt2 is not None

        # Get all
        all_attempts = configured_engine.get_attempts()
        assert len(all_attempts) >= 2

        # Filter by session
        session_attempts = configured_engine.get_attempts(session_id="session-1")
        assert len(session_attempts) == 1
        assert session_attempts[0].session_id == "session-1"


class TestStatistics:
    """Tests for statistics."""

    def test_get_stats_empty(self, engine):
        """Test stats with no attempts."""
        stats = engine.get_stats()

        assert stats["totalAttempts"] == 0
        assert stats["appliedCount"] == 0
        assert stats["successCount"] == 0

    def test_get_stats_with_data(self, configured_engine):
        """Test stats with attempts."""
        # Create some attempts with errors that will generate fixes
        attempt1 = configured_engine.analyze_error(
            tool_name="compile",
            error="error: ';' expected",
        )
        attempt2 = configured_engine.analyze_error(
            tool_name="node",
            error="Cannot find module 'lodash'",
        )

        assert attempt1 is not None
        assert attempt2 is not None

        stats = configured_engine.get_stats()

        assert stats["totalAttempts"] >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealingAPI:
    """Tests for Healing API endpoints."""

    @pytest.fixture
    def client(self, temp_db):
        """Create test client with temporary database."""
        import app.services.self_healing as module
        original_engine = module._self_healing_engine
        module._self_healing_engine = SelfHealingEngine(db_path=temp_db)

        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        yield client

        module._self_healing_engine = original_engine

    def test_get_config(self, client):
        """Test GET /api/healing/config endpoint."""
        response = client.get("/api/healing/config")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "autoApplyLevel" in data

    def test_set_config(self, client):
        """Test PUT /api/healing/config endpoint."""
        response = client.put("/api/healing/config", json={
            "enabled": False,
            "autoApplyLevel": "none",
            "maxRetries": 5,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] == False
        assert data["autoApplyLevel"] == "none"
        assert data["maxRetries"] == 5

    def test_analyze_error(self, client):
        """Test POST /api/healing/analyze endpoint."""
        response = client.post("/api/healing/analyze", json={
            "toolName": "compile",
            "error": "error: ';' expected",
            "sessionId": "test-session",
        })

        assert response.status_code == 200
        data = response.json()
        assert "found" in data

    def test_get_attempts(self, client):
        """Test GET /api/healing/attempts endpoint."""
        response = client.get("/api/healing/attempts")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_pending_attempts(self, client):
        """Test GET /api/healing/attempts/pending endpoint."""
        response = client.get("/api/healing/attempts/pending")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_stats(self, client):
        """Test GET /api/healing/stats endpoint."""
        response = client.get("/api/healing/stats")

        assert response.status_code == 200
        data = response.json()
        assert "totalAttempts" in data
        assert "successRate" in data

    def test_dismiss_nonexistent(self, client):
        """Test dismissing nonexistent attempt."""
        response = client.post("/api/healing/dismiss/nonexistent-id")

        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_self_healing_engine_singleton(self):
        """Test singleton returns same instance."""
        import app.services.self_healing as module
        module._self_healing_engine = None

        engine1 = get_self_healing_engine()
        engine2 = get_self_healing_engine()

        assert engine1 is engine2


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_error_message(self, configured_engine):
        """Test handling empty error message."""
        attempt = configured_engine.analyze_error(
            tool_name="test",
            error="",
        )

        # Should not crash
        assert attempt is None

    def test_very_long_error(self, configured_engine):
        """Test handling very long error message."""
        long_error = "Error: " + "x" * 10000

        attempt = configured_engine.analyze_error(
            tool_name="test",
            error=long_error,
        )

        # Should not crash
        assert attempt is None or attempt is not None

    def test_special_characters_in_error(self, configured_engine):
        """Test handling special characters."""
        attempt = configured_engine.analyze_error(
            tool_name="test",
            error="Error: <script>alert('xss')</script>",
        )

        # Should not crash
        assert attempt is None or attempt is not None

    def test_unicode_in_error(self, configured_engine):
        """Test handling unicode characters."""
        attempt = configured_engine.analyze_error(
            tool_name="test",
            error="Error: Ungültiger Bezeichner 日本語",
        )

        # Should not crash
        assert attempt is None or attempt is not None
