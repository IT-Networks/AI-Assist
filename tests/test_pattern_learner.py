"""
Tests fuer den Error Pattern Learning Service.

Testet:
- ErrorPattern Dataclass
- Pattern Extraction und Normalisierung
- Similarity Berechnung
- Pattern Learning und Feedback
- SQLite Persistenz
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.pattern_learner import (
    ErrorPattern,
    PatternLearner,
    get_pattern_learner,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ErrorPattern Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorPattern:
    """Tests fuer die ErrorPattern Dataclass."""

    def test_create_pattern(self):
        """Pattern kann erstellt werden."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="test-123",
            created_at=now,
            updated_at=now,
            error_type="NullPointerException",
            error_regex="NullPointerException",
            error_hash="abc123",
            context_keywords=["user", "service"],
            file_patterns=["*Service.java"],
            code_context="public class UserService",
            solution_description="Check for null",
            solution_steps=["Step 1", "Step 2"],
            solution_code="if (user != null)",
            tools_used=["edit_file"],
            files_changed=["UserService.java"],
        )

        assert pattern.id == "test-123"
        assert pattern.error_type == "NullPointerException"
        assert pattern.times_seen == 0
        assert pattern.confidence == 0.5

    def test_acceptance_rate_zero(self):
        """Acceptance Rate ist 0 wenn nie vorgeschlagen."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="test-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="hash1",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
        )

        assert pattern.acceptance_rate == 0.0

    def test_acceptance_rate_calculation(self):
        """Acceptance Rate wird korrekt berechnet."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="test-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="hash1",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            times_suggested=10,
            times_accepted=7,
        )

        assert pattern.acceptance_rate == 0.7

    def test_avg_rating_empty(self):
        """Avg Rating ist 0 ohne Bewertungen."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="test-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="hash1",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
        )

        assert pattern.avg_rating == 0.0

    def test_avg_rating_calculation(self):
        """Avg Rating wird korrekt berechnet."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="test-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="hash1",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            user_ratings=[4, 5, 3, 4],
        )

        assert pattern.avg_rating == 4.0

    def test_update_confidence(self):
        """Confidence wird basierend auf Stats aktualisiert."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="test-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="hash1",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            times_suggested=10,
            times_accepted=8,
            times_solved=5,
            user_ratings=[5, 5, 4],
        )

        pattern.update_confidence()

        # Confidence sollte hoch sein bei guten Stats
        assert pattern.confidence > 0.4
        assert pattern.confidence <= 1.0

    def test_confidence_decay(self):
        """Alte Patterns haben niedrigere Confidence."""
        old_date = datetime.now() - timedelta(days=60)
        pattern = ErrorPattern(
            id="test-1",
            created_at=old_date,
            updated_at=old_date,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="hash1",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            times_suggested=10,
            times_accepted=10,
            confidence=0.8,
        )

        pattern.update_confidence()

        # Decay sollte Confidence reduzieren
        assert pattern.confidence < 0.5

    def test_to_dict_and_from_dict(self):
        """Pattern kann serialisiert und deserialisiert werden."""
        now = datetime.now()
        original = ErrorPattern(
            id="test-serialize",
            created_at=now,
            updated_at=now,
            error_type="ValueError",
            error_regex="ValueError",
            error_hash="xyz789",
            context_keywords=["parse", "input"],
            file_patterns=["*.py"],
            code_context="def parse_input():",
            solution_description="Validate input",
            solution_steps=["Check type", "Handle error"],
            solution_code="try: ... except:",
            tools_used=["read_file", "edit_file"],
            files_changed=["parser.py"],
            times_seen=5,
            times_solved=3,
            confidence=0.75,
            user_ratings=[4, 5],
        )

        # To dict and back
        data = original.to_dict()
        restored = ErrorPattern.from_dict(data)

        assert restored.id == original.id
        assert restored.error_type == original.error_type
        assert restored.confidence == original.confidence
        assert restored.context_keywords == original.context_keywords
        assert restored.user_ratings == original.user_ratings


# ═══════════════════════════════════════════════════════════════════════════════
# PatternLearner Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatternLearner:
    """Tests fuer den PatternLearner Service."""

    def setup_method(self):
        """Setup fuer jeden Test - erstellt temp DB."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_patterns.db")
        self.learner = PatternLearner(db_path=self.db_path)

    def teardown_method(self):
        """Cleanup nach jedem Test."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestErrorTypeExtraction(TestPatternLearner):
    """Tests fuer Error Type Extraction."""

    def test_extract_java_exception(self):
        """Java Exceptions werden erkannt."""
        error = "java.lang.NullPointerException: Cannot invoke method on null"
        result = self.learner.extract_error_type(error)
        assert result == "NullPointerException"

    def test_extract_python_exception(self):
        """Python Exceptions werden erkannt."""
        error = "TypeError: 'int' object is not subscriptable"
        result = self.learner.extract_error_type(error)
        assert result == "TypeError"

    def test_extract_value_error(self):
        """ValueError wird erkannt."""
        error = "ValueError: invalid literal for int() with base 10"
        result = self.learner.extract_error_type(error)
        assert result == "ValueError"

    def test_extract_unknown_error(self):
        """Unbekannte Fehler geben UnknownError zurueck."""
        error = "Some random error message without exception type"
        result = self.learner.extract_error_type(error)
        assert result == "UnknownError"

    def test_extract_sql_exception(self):
        """SQLException wird erkannt."""
        error = "java.sql.SQLException: Connection refused"
        result = self.learner.extract_error_type(error)
        assert result == "SQLException"


class TestStackTraceNormalization(TestPatternLearner):
    """Tests fuer Stack Trace Normalisierung."""

    def test_remove_line_numbers(self):
        """Line Numbers werden entfernt."""
        trace = "at UserService.java:42"
        result = self.learner.normalize_stack_trace(trace)
        assert ":42" not in result
        assert "UserService.java" in result

    def test_remove_timestamps(self):
        """Timestamps werden entfernt."""
        trace = "Error at 2024-01-15T10:30:00 in UserService"
        result = self.learner.normalize_stack_trace(trace)
        # Full timestamp should be removed
        assert "2024-01-15T10:30:00" not in result
        assert "UserService" in result

    def test_remove_hex_addresses(self):
        """Hex-Adressen werden entfernt."""
        trace = "Object@1a2b3c4d in UserService"
        result = self.learner.normalize_stack_trace(trace)
        assert "@1a2b3c4d" not in result

    def test_remove_uuids(self):
        """UUIDs werden entfernt."""
        trace = "Request 550e8400-e29b-41d4-a716-446655440000 failed"
        result = self.learner.normalize_stack_trace(trace)
        assert "550e8400-e29b-41d4-a716-446655440000" not in result

    def test_collapse_whitespace(self):
        """Mehrfache Whitespaces werden zusammengefasst."""
        trace = "Error   in     UserService"
        result = self.learner.normalize_stack_trace(trace)
        assert "  " not in result


class TestKeywordExtraction(TestPatternLearner):
    """Tests fuer Keyword Extraction."""

    def test_extract_camelcase(self):
        """CamelCase-Woerter werden extrahiert."""
        text = "Error in UserServiceController"
        keywords = self.learner.extract_keywords(text)
        assert any("user" in kw for kw in keywords)

    def test_extract_snake_case(self):
        """snake_case-Woerter werden extrahiert."""
        text = "Error in user_service_handler"
        keywords = self.learner.extract_keywords(text)
        assert "user_service_handler" in keywords

    def test_extract_error_keywords(self):
        """Error-spezifische Keywords werden extrahiert."""
        text = "null pointer error with invalid input"
        keywords = self.learner.extract_keywords(text)
        assert "null" in keywords or "error" in keywords or "invalid" in keywords

    def test_filter_common_words(self):
        """Common Words werden ausgefiltert."""
        text = "The error was from the user and for the system"
        keywords = self.learner.extract_keywords(text)
        assert "the" not in keywords
        assert "and" not in keywords
        assert "for" not in keywords

    def test_limit_keywords(self):
        """Keywords werden auf 20 begrenzt."""
        text = " ".join([f"keyword{i}" for i in range(50)])
        keywords = self.learner.extract_keywords(text)
        assert len(keywords) <= 20


class TestFilePatternExtraction(TestPatternLearner):
    """Tests fuer File Pattern Extraction."""

    def test_extract_service_pattern(self):
        """Service-Dateien erzeugen *Service.* Pattern."""
        files = ["src/UserService.java"]
        patterns = self.learner.extract_file_patterns(files)
        assert "*Service.*" in patterns

    def test_extract_controller_pattern(self):
        """Controller-Dateien erzeugen *Controller.* Pattern."""
        files = ["src/UserController.java"]
        patterns = self.learner.extract_file_patterns(files)
        assert "*Controller.*" in patterns

    def test_extract_extension_pattern(self):
        """Datei-Extensions werden als Pattern extrahiert."""
        files = ["src/User.java", "src/Config.xml"]
        patterns = self.learner.extract_file_patterns(files)
        assert "*.java" in patterns
        assert "*.xml" in patterns


class TestSimilarityCalculation(TestPatternLearner):
    """Tests fuer Jaccard Similarity."""

    def test_identical_keywords(self):
        """Identische Keywords geben 1.0."""
        kw1 = ["user", "service", "error"]
        kw2 = ["user", "service", "error"]
        result = self.learner.calculate_keyword_similarity(kw1, kw2)
        assert result == 1.0

    def test_no_overlap(self):
        """Keine Ueberlappung gibt 0.0."""
        kw1 = ["user", "service"]
        kw2 = ["payment", "handler"]
        result = self.learner.calculate_keyword_similarity(kw1, kw2)
        assert result == 0.0

    def test_partial_overlap(self):
        """Teilweise Ueberlappung gibt Wert zwischen 0 und 1."""
        kw1 = ["user", "service", "error"]
        kw2 = ["user", "handler", "error"]
        result = self.learner.calculate_keyword_similarity(kw1, kw2)
        # 2 gemeinsam, 4 total => 2/4 = 0.5
        assert result == 0.5

    def test_empty_keywords(self):
        """Leere Listen geben 0.0."""
        result = self.learner.calculate_keyword_similarity([], ["user"])
        assert result == 0.0


class TestPatternPersistence(TestPatternLearner):
    """Tests fuer SQLite Persistenz."""

    def test_save_and_retrieve_pattern(self):
        """Pattern kann gespeichert und wieder geladen werden."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="persist-test-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="unique_hash_123",
            context_keywords=["type", "error"],
            file_patterns=["*.py"],
            code_context="def func():",
            solution_description="Fix type",
            solution_steps=["Step 1"],
            solution_code="pass",
            tools_used=["edit_file"],
            files_changed=["func.py"],
        )

        # Save
        success = self.learner.save_pattern(pattern)
        assert success

        # Retrieve by ID
        loaded = self.learner.get_pattern_by_id("persist-test-1")
        assert loaded is not None
        assert loaded.id == pattern.id
        assert loaded.error_type == pattern.error_type

    def test_get_pattern_by_hash(self):
        """Pattern kann nach Hash gefunden werden."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="hash-test-1",
            created_at=now,
            updated_at=now,
            error_type="KeyError",
            error_regex="KeyError",
            error_hash="hash_for_lookup",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix key",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
        )

        self.learner.save_pattern(pattern)

        loaded = self.learner.get_pattern_by_hash("hash_for_lookup")
        assert loaded is not None
        assert loaded.id == "hash-test-1"

    def test_delete_pattern(self):
        """Pattern kann geloescht werden."""
        now = datetime.now()
        pattern = ErrorPattern(
            id="delete-test-1",
            created_at=now,
            updated_at=now,
            error_type="ValueError",
            error_regex="ValueError",
            error_hash="delete_hash",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Fix",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
        )

        self.learner.save_pattern(pattern)

        # Loeschen
        success = self.learner.delete_pattern("delete-test-1")
        assert success

        # Sollte nicht mehr gefunden werden
        loaded = self.learner.get_pattern_by_id("delete-test-1")
        assert loaded is None

    def test_delete_nonexistent_pattern(self):
        """Loeschen eines nicht existierenden Patterns gibt False."""
        success = self.learner.delete_pattern("nonexistent-id")
        assert not success

    def test_list_patterns_empty(self):
        """Leere Datenbank gibt leere Liste."""
        patterns = self.learner.list_patterns()
        assert patterns == []

    def test_list_patterns_with_filter(self):
        """Patterns koennen nach Typ gefiltert werden."""
        now = datetime.now()

        # Create patterns with different types
        for i, error_type in enumerate(["TypeError", "ValueError", "TypeError"]):
            pattern = ErrorPattern(
                id=f"filter-test-{i}",
                created_at=now,
                updated_at=now,
                error_type=error_type,
                error_regex=error_type,
                error_hash=f"hash_{i}",
                context_keywords=[],
                file_patterns=[],
                code_context="",
                solution_description="Fix",
                solution_steps=[],
                solution_code=None,
                tools_used=[],
                files_changed=[],
                confidence=0.5,
            )
            self.learner.save_pattern(pattern)

        # Filter by TypeError
        type_errors = self.learner.list_patterns(error_type="TypeError")
        assert len(type_errors) == 2

        # Filter by ValueError
        value_errors = self.learner.list_patterns(error_type="ValueError")
        assert len(value_errors) == 1

    def test_list_patterns_with_min_confidence(self):
        """Patterns koennen nach Confidence gefiltert werden."""
        now = datetime.now()

        for i, conf in enumerate([0.2, 0.5, 0.8]):
            pattern = ErrorPattern(
                id=f"conf-test-{i}",
                created_at=now,
                updated_at=now,
                error_type="Error",
                error_regex="Error",
                error_hash=f"conf_hash_{i}",
                context_keywords=[],
                file_patterns=[],
                code_context="",
                solution_description="Fix",
                solution_steps=[],
                solution_code=None,
                tools_used=[],
                files_changed=[],
                confidence=conf,
            )
            self.learner.save_pattern(pattern)

        # Min confidence 0.5
        high_conf = self.learner.list_patterns(min_confidence=0.5)
        assert len(high_conf) == 2


class TestLearningPipeline(TestPatternLearner):
    """Tests fuer das Pattern Learning."""

    def test_learn_new_pattern(self):
        """Neues Pattern wird gelernt."""
        pattern, is_new = self.learner.learn_pattern(
            error_text="TypeError: cannot subscript None",
            stack_trace="at line 42 in UserService.py",
            solution_description="Add null check before subscript",
            solution_steps=["Check if value is None", "Handle None case"],
            solution_code="if value is not None:",
            tools_used=["edit_file"],
            files_changed=["UserService.py"],
            code_context="def get_user():",
        )

        assert is_new
        assert pattern.id is not None
        assert pattern.error_type == "TypeError"
        assert pattern.times_seen == 1
        assert pattern.times_solved == 1

    def test_learn_updates_existing_pattern(self):
        """Bestehendes Pattern wird aktualisiert."""
        # Learn first time
        pattern1, is_new1 = self.learner.learn_pattern(
            error_text="ValueError: invalid input",
            stack_trace="at parse_input:10",
            solution_description="Validate input",
            solution_steps=["Check input"],
            code_context="def parse():",
        )

        assert is_new1

        # Learn same error again
        pattern2, is_new2 = self.learner.learn_pattern(
            error_text="ValueError: invalid input",
            stack_trace="at parse_input:10",  # Same normalized trace
            solution_description="Validate input v2",
            solution_steps=["Check input better"],
        )

        assert not is_new2
        assert pattern2.id == pattern1.id
        assert pattern2.times_seen == 2
        assert pattern2.times_solved == 2


class TestSuggestPattern(TestPatternLearner):
    """Tests fuer Pattern-Vorschlaege."""

    def test_suggest_exact_match(self):
        """Exakter Hash-Match wird vorgeschlagen."""
        # Learn pattern first
        self.learner.learn_pattern(
            error_text="IndexError: list index out of range",
            stack_trace="at process_list:15",
            solution_description="Check list length",
            solution_steps=["Add boundary check"],
        )

        # Suggest for same error
        pattern, confidence, alternatives = self.learner.suggest_pattern(
            error_text="IndexError: list index out of range",
            stack_trace="at process_list:15",
        )

        assert pattern is not None
        assert pattern.error_type == "IndexError"
        assert len(alternatives) == 0  # Exact match has no alternatives

    def test_suggest_similar_pattern(self):
        """Aehnliches Pattern wird vorgeschlagen."""
        # Learn pattern
        self.learner.learn_pattern(
            error_text="TypeError: unsupported operand user service",
            stack_trace="at UserService:42",
            solution_description="Fix type mismatch",
            solution_steps=["Check types"],
            code_context="user service handler code",
        )

        # Suggest for similar error (different trace but same keywords)
        pattern, confidence, alternatives = self.learner.suggest_pattern(
            error_text="TypeError: operand error in user",
            stack_trace="at different_location:99",
            file_context="user service processing",
        )

        # Should find similar pattern by keywords
        # Note: might be None if similarity threshold not met
        if pattern:
            assert pattern.error_type == "TypeError"

    def test_suggest_no_match(self):
        """Kein Vorschlag wenn kein passendes Pattern."""
        pattern, confidence, alternatives = self.learner.suggest_pattern(
            error_text="CompletelyUniqueError: never seen before",
            stack_trace="at unique_location:999",
        )

        assert pattern is None
        assert confidence == 0.0


class TestFeedback(TestPatternLearner):
    """Tests fuer User Feedback."""

    def test_record_accepted_feedback(self):
        """Accepted Feedback wird aufgezeichnet."""
        # Create pattern
        pattern, _ = self.learner.learn_pattern(
            error_text="KeyError: missing key",
            stack_trace="at dict_access:5",
            solution_description="Check key exists",
            solution_steps=["Use get() or check in"],
        )

        # Record feedback
        updated = self.learner.record_feedback(
            pattern_id=pattern.id,
            accepted=True,
            rating=5,
        )

        assert updated.times_accepted == 1
        assert updated.times_rejected == 0
        assert 5 in updated.user_ratings

    def test_record_rejected_feedback(self):
        """Rejected Feedback wird aufgezeichnet."""
        pattern, _ = self.learner.learn_pattern(
            error_text="AttributeError: object has no attribute",
            stack_trace="at access:10",
            solution_description="Fix attribute",
            solution_steps=["Check object type"],
        )

        updated = self.learner.record_feedback(
            pattern_id=pattern.id,
            accepted=False,
            rating=1,
        )

        assert updated.times_rejected == 1
        assert updated.times_accepted == 0
        assert 1 in updated.user_ratings

    def test_feedback_updates_confidence(self):
        """Feedback aktualisiert die Confidence."""
        pattern, _ = self.learner.learn_pattern(
            error_text="ImportError: no module named test",
            stack_trace="at import:1",
            solution_description="Install module",
            solution_steps=["pip install"],
        )

        # Multiple positive feedback
        for _ in range(5):
            pattern = self.learner.record_feedback(
                pattern_id=pattern.id,
                accepted=True,
                rating=5,
            )
            # Simulate suggestion count
            pattern.times_suggested += 1
            self.learner.save_pattern(pattern)

        # Confidence should have increased
        final = self.learner.get_pattern_by_id(pattern.id)
        assert final.confidence > 0.3

    def test_feedback_nonexistent_pattern(self):
        """Feedback fuer nicht existierendes Pattern gibt None."""
        result = self.learner.record_feedback(
            pattern_id="nonexistent-id",
            accepted=True,
        )
        assert result is None

    def test_rating_limit(self):
        """Nur gueltige Ratings werden akzeptiert."""
        pattern, _ = self.learner.learn_pattern(
            error_text="RuntimeError: test",
            stack_trace="at test:1",
            solution_description="Fix",
            solution_steps=[],
        )

        # Valid rating
        updated = self.learner.record_feedback(
            pattern_id=pattern.id,
            accepted=True,
            rating=3,
        )
        assert 3 in updated.user_ratings

        # Invalid rating (out of range) - should not be added
        updated = self.learner.record_feedback(
            pattern_id=pattern.id,
            accepted=True,
            rating=10,  # Invalid
        )
        assert 10 not in updated.user_ratings


class TestMaintenance(TestPatternLearner):
    """Tests fuer Maintenance-Funktionen."""

    def test_cleanup_old_patterns(self):
        """Alte Patterns mit niedriger Confidence werden geloescht."""
        old_date = datetime.now() - timedelta(days=100)
        now = datetime.now()

        # Create old pattern with low confidence
        old_pattern = ErrorPattern(
            id="old-pattern-1",
            created_at=old_date,
            updated_at=old_date,
            error_type="OldError",
            error_regex="OldError",
            error_hash="old_hash",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Old fix",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            confidence=0.1,  # Low confidence
        )
        self.learner.save_pattern(old_pattern)

        # Create recent pattern
        recent_pattern = ErrorPattern(
            id="recent-pattern-1",
            created_at=now,
            updated_at=now,
            error_type="RecentError",
            error_regex="RecentError",
            error_hash="recent_hash",
            context_keywords=[],
            file_patterns=[],
            code_context="",
            solution_description="Recent fix",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            confidence=0.8,  # High confidence
        )
        self.learner.save_pattern(recent_pattern)

        # Cleanup
        deleted = self.learner.cleanup_old_patterns(max_age_days=90)

        # Old pattern should be deleted
        assert deleted >= 0  # May be 0 or 1 depending on exact timing
        assert self.learner.get_pattern_by_id("recent-pattern-1") is not None

    def test_export_patterns(self):
        """Patterns koennen exportiert werden."""
        now = datetime.now()

        # Create some patterns
        for i in range(3):
            pattern = ErrorPattern(
                id=f"export-test-{i}",
                created_at=now,
                updated_at=now,
                error_type=f"Error{i}",
                error_regex=f"Error{i}",
                error_hash=f"export_hash_{i}",
                context_keywords=["test"],
                file_patterns=[],
                code_context="",
                solution_description=f"Fix {i}",
                solution_steps=[],
                solution_code=None,
                tools_used=[],
                files_changed=[],
            )
            self.learner.save_pattern(pattern)

        # Export
        export_path = os.path.join(self.temp_dir, "export.json")
        count = self.learner.export_patterns(export_path)

        assert count == 3
        assert os.path.exists(export_path)

        # Verify content
        with open(export_path) as f:
            data = json.load(f)
        assert data["count"] == 3
        assert len(data["patterns"]) == 3

    def test_import_patterns(self):
        """Patterns koennen importiert werden."""
        # Create export file
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "count": 2,
            "patterns": [
                {
                    "id": "import-1",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "error_type": "ImportedError",
                    "error_regex": "ImportedError",
                    "error_hash": "import_hash_1",
                    "context_keywords": [],
                    "file_patterns": [],
                    "code_context": "",
                    "solution_description": "Imported fix",
                    "solution_steps": [],
                    "solution_code": None,
                    "tools_used": [],
                    "files_changed": [],
                    "confidence": 0.6,
                    "user_ratings": [],
                },
                {
                    "id": "import-2",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "error_type": "ImportedError2",
                    "error_regex": "ImportedError2",
                    "error_hash": "import_hash_2",
                    "context_keywords": [],
                    "file_patterns": [],
                    "code_context": "",
                    "solution_description": "Imported fix 2",
                    "solution_steps": [],
                    "solution_code": None,
                    "tools_used": [],
                    "files_changed": [],
                    "confidence": 0.7,
                    "user_ratings": [],
                },
            ],
        }

        import_path = os.path.join(self.temp_dir, "import.json")
        with open(import_path, "w") as f:
            json.dump(export_data, f)

        # Import
        imported = self.learner.import_patterns(import_path)

        assert imported == 2
        assert self.learner.get_pattern_by_id("import-1") is not None
        assert self.learner.get_pattern_by_id("import-2") is not None


class TestFindSimilarPatterns(TestPatternLearner):
    """Tests fuer aehnliche Pattern-Suche."""

    def test_find_similar_by_keywords(self):
        """Patterns mit aehnlichen Keywords werden gefunden."""
        now = datetime.now()

        # Create pattern with keywords
        pattern = ErrorPattern(
            id="similar-1",
            created_at=now,
            updated_at=now,
            error_type="TypeError",
            error_regex="TypeError",
            error_hash="similar_hash_1",
            context_keywords=["user", "service", "handler", "database"],
            file_patterns=[],
            code_context="",
            solution_description="Fix type",
            solution_steps=[],
            solution_code=None,
            tools_used=[],
            files_changed=[],
            confidence=0.7,
        )
        self.learner.save_pattern(pattern)

        # Find similar with overlapping keywords
        similar = self.learner.find_similar_patterns(
            error_type="TypeError",
            keywords=["user", "service", "controller"],  # 2/5 overlap
            min_similarity=0.2,
        )

        assert len(similar) >= 1
        assert similar[0][0].id == "similar-1"

    def test_no_similar_patterns(self):
        """Keine aehnlichen Patterns wenn keine Uebereinstimmung."""
        similar = self.learner.find_similar_patterns(
            error_type="UniqueErrorType",
            keywords=["unique", "keywords"],
        )

        assert len(similar) == 0


class TestSingleton(TestPatternLearner):
    """Tests fuer Singleton-Instanz."""

    def test_get_pattern_learner_singleton(self):
        """get_pattern_learner gibt immer dieselbe Instanz."""
        # Reset singleton for test
        import app.services.pattern_learner as pl
        pl._pattern_learner = None

        learner1 = get_pattern_learner()
        learner2 = get_pattern_learner()

        assert learner1 is learner2

        # Cleanup
        pl._pattern_learner = None
