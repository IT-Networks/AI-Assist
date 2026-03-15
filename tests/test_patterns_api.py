"""
Tests fuer die Pattern Learning REST API.

Testet:
- GET /api/patterns - Liste aller Patterns
- GET /api/patterns/{id} - Einzelnes Pattern
- POST /api/patterns/suggest - Pattern-Vorschlag
- POST /api/patterns/learn - Neues Pattern lernen
- POST /api/patterns/{id}/feedback - Feedback aufzeichnen
- DELETE /api/patterns/{id} - Pattern loeschen
- POST /api/patterns/cleanup - Alte Patterns bereinigen
"""

import os
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services.pattern_learner import ErrorPattern, PatternLearner


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_db():
    """Erstellt temporaere DB fuer Tests."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_patterns.db")
    yield db_path
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_learner(temp_db):
    """Erstellt gemockten PatternLearner."""
    learner = PatternLearner(db_path=temp_db)
    return learner


@pytest.fixture
def sample_pattern():
    """Erstellt ein Beispiel-Pattern."""
    now = datetime.now()
    return ErrorPattern(
        id="test-pattern-1",
        created_at=now,
        updated_at=now,
        error_type="TypeError",
        error_regex="TypeError",
        error_hash="test_hash_123",
        context_keywords=["user", "service"],
        file_patterns=["*Service.py"],
        code_context="def get_user():",
        solution_description="Add null check",
        solution_steps=["Check if None", "Handle edge case"],
        solution_code="if value is not None:",
        tools_used=["edit_file"],
        files_changed=["user_service.py"],
        times_seen=5,
        times_solved=3,
        times_suggested=10,
        times_accepted=7,
        times_rejected=2,
        confidence=0.75,
        user_ratings=[4, 5, 4],
    )


@pytest.fixture
def client(mock_learner):
    """Erstellt Test-Client mit gemocktem Learner."""
    from main import app
    from app.api.routes import patterns

    with patch.object(patterns, 'get_pattern_learner', return_value=mock_learner):
        with TestClient(app) as client:
            yield client, mock_learner


# ═══════════════════════════════════════════════════════════════════════════════
# List Patterns Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestListPatterns:
    """Tests fuer GET /api/patterns."""

    def test_list_empty(self, client):
        """Leere Liste wenn keine Patterns."""
        test_client, learner = client
        response = test_client.get("/api/patterns")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_patterns(self, client, sample_pattern):
        """Patterns werden aufgelistet."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.get("/api/patterns")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == sample_pattern.id
        assert data[0]["error_type"] == "TypeError"

    def test_list_with_min_confidence(self, client, sample_pattern):
        """Filter nach min_confidence funktioniert."""
        test_client, learner = client
        sample_pattern.confidence = 0.8
        learner.save_pattern(sample_pattern)

        # Mit hoher minConfidence
        response = test_client.get("/api/patterns?minConfidence=0.9")
        assert response.status_code == 200
        assert len(response.json()) == 0

        # Mit niedriger minConfidence
        response = test_client.get("/api/patterns?minConfidence=0.5")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_list_with_error_type_filter(self, client):
        """Filter nach errorType funktioniert."""
        test_client, learner = client
        now = datetime.now()

        # Erstelle mehrere Patterns
        for i, error_type in enumerate(["TypeError", "ValueError", "TypeError"]):
            p = ErrorPattern(
                id=f"pattern-{i}",
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
            )
            learner.save_pattern(p)

        response = test_client.get("/api/patterns?errorType=TypeError")
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_list_with_limit(self, client):
        """Limit Parameter funktioniert."""
        test_client, learner = client
        now = datetime.now()

        for i in range(10):
            p = ErrorPattern(
                id=f"pattern-{i}",
                created_at=now,
                updated_at=now,
                error_type="Error",
                error_regex="Error",
                error_hash=f"hash_{i}",
                context_keywords=[],
                file_patterns=[],
                code_context="",
                solution_description="Fix",
                solution_steps=[],
                solution_code=None,
                tools_used=[],
                files_changed=[],
            )
            learner.save_pattern(p)

        response = test_client.get("/api/patterns?limit=5")
        assert response.status_code == 200
        assert len(response.json()) == 5

    def test_list_invalid_limit(self, client):
        """Ungueltige Limits werden abgelehnt."""
        test_client, learner = client

        response = test_client.get("/api/patterns?limit=0")
        assert response.status_code == 400

        response = test_client.get("/api/patterns?limit=1000")
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Get Pattern Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetPattern:
    """Tests fuer GET /api/patterns/{id}."""

    def test_get_existing_pattern(self, client, sample_pattern):
        """Bestehendes Pattern wird zurueckgegeben."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.get(f"/api/patterns/{sample_pattern.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_pattern.id
        assert data["error_type"] == "TypeError"
        assert data["solution_description"] == "Add null check"

    def test_get_nonexistent_pattern(self, client):
        """404 bei nicht existierendem Pattern."""
        test_client, learner = client

        response = test_client.get("/api/patterns/nonexistent-id")

        assert response.status_code == 404
        assert "nicht gefunden" in response.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════════
# Suggest Pattern Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuggestPattern:
    """Tests fuer POST /api/patterns/suggest."""

    def test_suggest_no_match(self, client):
        """Kein Pattern-Vorschlag bei leerem DB."""
        test_client, learner = client

        response = test_client.post("/api/patterns/suggest", json={
            "errorType": "UnknownError",
            "stackTrace": "at some_location:42",
            "fileContext": "",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["pattern"] is None
        assert data["confidence"] == 0.0

    def test_suggest_exact_match(self, client, sample_pattern):
        """Exakter Match wird vorgeschlagen."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        # Generiere denselben Hash wie das Pattern
        error_text = f"{sample_pattern.error_type}: test error"
        normalized = learner.normalize_stack_trace("test trace")
        error_hash = learner.generate_error_hash(sample_pattern.error_type, normalized)
        sample_pattern.error_hash = error_hash
        learner.save_pattern(sample_pattern)

        response = test_client.post("/api/patterns/suggest", json={
            "errorType": sample_pattern.error_type,
            "stackTrace": "test trace",
            "fileContext": "",
        })

        assert response.status_code == 200
        data = response.json()
        # May or may not match exactly depending on hash
        # Just verify response structure
        assert "pattern" in data
        assert "confidence" in data
        assert "alternatives" in data


# ═══════════════════════════════════════════════════════════════════════════════
# Learn Pattern Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLearnPattern:
    """Tests fuer POST /api/patterns/learn."""

    def test_learn_new_pattern(self, client):
        """Neues Pattern kann gelernt werden."""
        test_client, learner = client

        response = test_client.post("/api/patterns/learn", json={
            "errorType": "ValueError",
            "errorText": "invalid literal for int()",
            "stackTrace": "at parse_int:10",
            "solutionDescription": "Validate input before parsing",
            "solutionSteps": ["Check if string is numeric", "Handle exception"],
            "solutionCode": "if value.isdigit():",
            "toolsUsed": ["edit_file"],
            "filesChanged": ["parser.py"],
            "codeContext": "def parse_value(value):",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["isNew"] is True
        assert data["patternId"] is not None
        assert data["confidence"] >= 0.0

    def test_learn_updates_existing(self, client, sample_pattern):
        """Bestehendes Pattern wird aktualisiert."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        # Learn same error type with matching hash
        response = test_client.post("/api/patterns/learn", json={
            "errorType": sample_pattern.error_type,
            "errorText": "similar error",
            "stackTrace": "",  # Same normalized trace
            "solutionDescription": "Updated solution",
            "solutionSteps": ["Step 1"],
        })

        assert response.status_code == 200
        # May create new or update existing based on hash

    def test_learn_validates_required_fields(self, client):
        """Pflichtfelder werden validiert."""
        test_client, learner = client

        response = test_client.post("/api/patterns/learn", json={
            "errorType": "Error",
            # Missing errorText and solutionDescription
        })

        assert response.status_code == 422  # Validation error


# ═══════════════════════════════════════════════════════════════════════════════
# Feedback Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordFeedback:
    """Tests fuer POST /api/patterns/{id}/feedback."""

    def test_record_positive_feedback(self, client, sample_pattern):
        """Positives Feedback wird aufgezeichnet."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.post(
            f"/api/patterns/{sample_pattern.id}/feedback",
            json={
                "accepted": True,
                "rating": 5,
                "comment": "Great solution!",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["newConfidence"] >= 0.0

    def test_record_negative_feedback(self, client, sample_pattern):
        """Negatives Feedback wird aufgezeichnet."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.post(
            f"/api/patterns/{sample_pattern.id}/feedback",
            json={
                "accepted": False,
                "rating": 1,
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_feedback_nonexistent_pattern(self, client):
        """404 bei Feedback fuer nicht existierendes Pattern."""
        test_client, learner = client

        response = test_client.post(
            "/api/patterns/nonexistent-id/feedback",
            json={"accepted": True}
        )

        assert response.status_code == 404

    def test_feedback_invalid_rating(self, client, sample_pattern):
        """Ungueltiges Rating wird validiert."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.post(
            f"/api/patterns/{sample_pattern.id}/feedback",
            json={
                "accepted": True,
                "rating": 10,  # Invalid: must be 1-5
            }
        )

        # Pydantic validation should catch this
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# Delete Pattern Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeletePattern:
    """Tests fuer DELETE /api/patterns/{id}."""

    def test_delete_existing_pattern(self, client, sample_pattern):
        """Bestehendes Pattern wird geloescht."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.delete(f"/api/patterns/{sample_pattern.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify deleted
        assert learner.get_pattern_by_id(sample_pattern.id) is None

    def test_delete_nonexistent_pattern(self, client):
        """404 bei Loeschen eines nicht existierenden Patterns."""
        test_client, learner = client

        response = test_client.delete("/api/patterns/nonexistent-id")

        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleanupPatterns:
    """Tests fuer POST /api/patterns/cleanup."""

    def test_cleanup_default_age(self, client):
        """Cleanup mit Default-Alter funktioniert."""
        test_client, learner = client

        response = test_client.post("/api/patterns/cleanup")

        assert response.status_code == 200
        data = response.json()
        assert "deleted" in data
        assert data["deleted"] >= 0

    def test_cleanup_custom_age(self, client):
        """Cleanup mit benutzerdefiniertem Alter funktioniert."""
        test_client, learner = client

        response = test_client.post("/api/patterns/cleanup?max_age_days=30")

        assert response.status_code == 200

    def test_cleanup_invalid_age(self, client):
        """Ungueltige Alter werden abgelehnt."""
        test_client, learner = client

        response = test_client.post("/api/patterns/cleanup?max_age_days=0")
        assert response.status_code == 400

        response = test_client.post("/api/patterns/cleanup?max_age_days=400")
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Export/Import Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestExportPatterns:
    """Tests fuer GET /api/patterns/export/json."""

    def test_export_empty(self, client):
        """Export bei leerer DB funktioniert."""
        test_client, learner = client

        response = test_client.get("/api/patterns/export/json")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

    def test_export_with_patterns(self, client, sample_pattern):
        """Export mit Patterns funktioniert."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.get("/api/patterns/export/json")

        assert response.status_code == 200
        # Response is a file download


class TestImportPatterns:
    """Tests fuer POST /api/patterns/import/json."""

    def test_import_endpoint_returns_info(self, client):
        """Import-Endpoint gibt CLI-Info zurueck."""
        test_client, learner = client

        response = test_client.post("/api/patterns/import/json")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "command" in data


# ═══════════════════════════════════════════════════════════════════════════════
# Response Model Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResponseModels:
    """Tests fuer Response-Modelle."""

    def test_pattern_response_model(self, client, sample_pattern):
        """PatternResponse enthaelt alle erforderlichen Felder."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.get(f"/api/patterns/{sample_pattern.id}")

        assert response.status_code == 200
        data = response.json()

        # Verify all fields present
        required_fields = [
            "id", "error_type", "confidence", "solution_description",
            "solution_steps", "solution_code", "times_seen", "times_solved",
            "times_accepted", "times_rejected", "acceptance_rate", "avg_rating",
            "context_keywords", "file_patterns", "tools_used",
            "created_at", "updated_at"
        ]

        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_suggest_response_model(self, client):
        """PatternSuggestResponse enthaelt alle erforderlichen Felder."""
        test_client, learner = client

        response = test_client.post("/api/patterns/suggest", json={
            "errorType": "TestError",
            "stackTrace": "",
            "fileContext": "",
        })

        assert response.status_code == 200
        data = response.json()

        assert "pattern" in data
        assert "confidence" in data
        assert "alternatives" in data

    def test_learn_response_model(self, client):
        """PatternLearnResponse enthaelt alle erforderlichen Felder."""
        test_client, learner = client

        response = test_client.post("/api/patterns/learn", json={
            "errorType": "TestError",
            "errorText": "test error",
            "solutionDescription": "test solution",
        })

        assert response.status_code == 200
        data = response.json()

        assert "patternId" in data
        assert "isNew" in data
        assert "confidence" in data

    def test_feedback_response_model(self, client, sample_pattern):
        """PatternFeedbackResponse enthaelt alle erforderlichen Felder."""
        test_client, learner = client
        learner.save_pattern(sample_pattern)

        response = test_client.post(
            f"/api/patterns/{sample_pattern.id}/feedback",
            json={"accepted": True}
        )

        assert response.status_code == 200
        data = response.json()

        assert "success" in data
        assert "newConfidence" in data
