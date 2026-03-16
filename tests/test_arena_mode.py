"""
Tests for Arena Mode Service.
"""

import os
import tempfile
import pytest

from app.services.arena_mode import (
    ArenaModeService,
    ArenaConfig,
    ArenaMatch,
    Vote,
    MatchStatus,
    ModelStats,
    EloCalculator,
    get_arena_mode_service,
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
    if os.path.exists(path):
        try:
            os.unlink(path)
        except PermissionError:
            pass  # Windows file lock


@pytest.fixture
def service(temp_db):
    """Create test service with temporary database."""
    return ArenaModeService(db_path=temp_db)


@pytest.fixture
def configured_service(service):
    """Service with arena mode enabled."""
    service.set_config(ArenaConfig(
        enabled=True,
        model_a="model-alpha",
        model_b="model-beta",
    ))
    return service


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestArenaConfig:
    """Tests for arena configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ArenaConfig()

        assert config.enabled == False
        assert config.model_a == ""
        assert config.model_b == ""
        assert config.auto_arena == False
        assert config.sample_rate == 1.0
        assert config.elo_k_factor == 32

    def test_config_to_dict(self):
        """Test configuration serialization."""
        config = ArenaConfig(
            enabled=True,
            model_a="gpt-4",
            model_b="claude-3",
        )

        data = config.to_dict()

        assert data["enabled"] == True
        assert data["modelA"] == "gpt-4"
        assert data["modelB"] == "claude-3"

    def test_config_from_dict(self):
        """Test configuration deserialization."""
        data = {
            "enabled": True,
            "modelA": "model-1",
            "modelB": "model-2",
            "autoArena": True,
            "sampleRate": 0.5,
        }

        config = ArenaConfig.from_dict(data)

        assert config.enabled == True
        assert config.model_a == "model-1"
        assert config.model_b == "model-2"
        assert config.auto_arena == True
        assert config.sample_rate == 0.5

    def test_get_config(self, service):
        """Test getting configuration."""
        config = service.get_config()

        assert config is not None
        assert isinstance(config, ArenaConfig)
        assert config.enabled == False

    def test_set_config(self, service):
        """Test setting configuration."""
        new_config = ArenaConfig(
            enabled=True,
            model_a="model-x",
            model_b="model-y",
            auto_arena=True,
        )

        result = service.set_config(new_config)

        assert result.enabled == True
        assert result.model_a == "model-x"
        assert result.model_b == "model-y"
        assert result.auto_arena == True

    def test_config_persists(self, temp_db):
        """Test configuration persists across service instances."""
        service1 = ArenaModeService(db_path=temp_db)
        service1.set_config(ArenaConfig(
            enabled=True,
            model_a="persistent-a",
            model_b="persistent-b",
        ))

        service2 = ArenaModeService(db_path=temp_db)
        config = service2.get_config()

        assert config.enabled == True
        assert config.model_a == "persistent-a"
        assert config.model_b == "persistent-b"

    def test_is_enabled(self, service):
        """Test is_enabled check."""
        assert service.is_enabled() is False

        service.set_config(ArenaConfig(enabled=True))
        assert service.is_enabled() is False  # No models configured

        service.set_config(ArenaConfig(enabled=True, model_a="a"))
        assert service.is_enabled() is False  # Only one model

        service.set_config(ArenaConfig(enabled=True, model_a="a", model_b="b"))
        assert service.is_enabled() is True


# ═══════════════════════════════════════════════════════════════════════════════
# ELO Calculator Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEloCalculator:
    """Tests for ELO rating calculation."""

    def test_expected_score_equal_ratings(self):
        """Test expected score with equal ratings."""
        expected = EloCalculator.expected_score(1000, 1000)
        assert expected == pytest.approx(0.5, rel=0.01)

    def test_expected_score_higher_rating(self):
        """Test expected score with higher rating."""
        expected = EloCalculator.expected_score(1200, 1000)
        assert expected > 0.5

    def test_expected_score_lower_rating(self):
        """Test expected score with lower rating."""
        expected = EloCalculator.expected_score(1000, 1200)
        assert expected < 0.5

    def test_update_ratings_a_wins(self):
        """Test rating update when A wins."""
        new_a, new_b = EloCalculator.update_ratings(1000, 1000, Vote.A, k_factor=32)

        assert new_a > 1000
        assert new_b < 1000
        assert new_a - 1000 == pytest.approx(1000 - new_b, rel=0.01)

    def test_update_ratings_b_wins(self):
        """Test rating update when B wins."""
        new_a, new_b = EloCalculator.update_ratings(1000, 1000, Vote.B, k_factor=32)

        assert new_a < 1000
        assert new_b > 1000

    def test_update_ratings_tie(self):
        """Test rating update on tie with equal ratings."""
        new_a, new_b = EloCalculator.update_ratings(1000, 1000, Vote.TIE, k_factor=32)

        # With equal ratings, a tie should keep ratings similar
        assert new_a == pytest.approx(1000, rel=0.01)
        assert new_b == pytest.approx(1000, rel=0.01)

    def test_update_ratings_upset(self):
        """Test rating update on upset (weaker wins)."""
        # Model A has much lower rating but wins
        new_a, new_b = EloCalculator.update_ratings(800, 1200, Vote.A, k_factor=32)

        # Winner should gain more in an upset
        assert new_a - 800 > 16  # More than normal gain
        assert new_b < 1200


# ═══════════════════════════════════════════════════════════════════════════════
# Match Management Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatchManagement:
    """Tests for match creation and management."""

    def test_create_match(self, configured_service):
        """Test creating a new match."""
        match = configured_service.create_match(
            prompt="Test prompt",
            session_id="session-1",
        )

        assert match.id is not None
        assert match.prompt == "Test prompt"
        assert match.session_id == "session-1"
        assert match.model_a == "model-alpha"
        assert match.model_b == "model-beta"
        assert match.status == MatchStatus.PENDING

    def test_create_match_with_custom_models(self, configured_service):
        """Test creating match with custom models."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
            model_a="custom-a",
            model_b="custom-b",
        )

        assert match.model_a == "custom-a"
        assert match.model_b == "custom-b"

    def test_create_match_with_context(self, configured_service):
        """Test creating match with context."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
            context="Previous conversation context",
        )

        assert match.context == "Previous conversation context"

    def test_set_response(self, configured_service):
        """Test setting model responses."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )

        # Set response from model_a
        updated = configured_service.set_response(
            match_id=match.id,
            model=match.model_a,
            response="Response from A",
            latency_ms=1000,
            tokens=100,
        )

        assert updated.response_a == "Response from A"
        assert updated.latency_a == 1000
        assert updated.tokens_a == 100
        assert updated.status == MatchStatus.PENDING  # Still waiting for B

    def test_set_both_responses(self, configured_service):
        """Test match becomes ready after both responses."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )

        configured_service.set_response(
            match_id=match.id,
            model=match.model_a,
            response="Response A",
            latency_ms=1000,
            tokens=100,
        )

        updated = configured_service.set_response(
            match_id=match.id,
            model=match.model_b,
            response="Response B",
            latency_ms=800,
            tokens=120,
        )

        assert updated.status == MatchStatus.READY
        assert updated.response_a == "Response A"
        assert updated.response_b == "Response B"

    def test_get_match(self, configured_service):
        """Test retrieving a match."""
        created = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )

        retrieved = configured_service.get_match(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.prompt == "Test"

    def test_get_nonexistent_match(self, configured_service):
        """Test getting nonexistent match."""
        result = configured_service.get_match("nonexistent")
        assert result is None

    def test_display_order_randomized(self, configured_service):
        """Test that display order is randomized for blind comparison."""
        # Create multiple matches and check that display order varies
        display_orders = []

        for _ in range(20):
            match = configured_service.create_match(
                prompt="Test",
                session_id="s1",
            )
            display_orders.append(match.display_a_is_model_a)

        # Should have both True and False in 20 samples
        assert True in display_orders
        assert False in display_orders


# ═══════════════════════════════════════════════════════════════════════════════
# Voting Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoting:
    """Tests for voting functionality."""

    def _create_ready_match(self, service):
        """Helper to create a match ready for voting."""
        match = service.create_match(
            prompt="Test",
            session_id="s1",
        )
        service.set_response(match.id, match.model_a, "Response A", 1000, 100)
        service.set_response(match.id, match.model_b, "Response B", 800, 120)
        return service.get_match(match.id)

    def test_vote_a(self, configured_service):
        """Test voting for A."""
        match = self._create_ready_match(configured_service)

        result = configured_service.vote(match.id, Vote.A)

        assert result.status == MatchStatus.VOTED
        assert result.vote == Vote.A
        assert result.voted_at is not None

    def test_vote_b(self, configured_service):
        """Test voting for B."""
        match = self._create_ready_match(configured_service)

        result = configured_service.vote(match.id, Vote.B)

        assert result.vote == Vote.B

    def test_vote_tie(self, configured_service):
        """Test voting tie."""
        match = self._create_ready_match(configured_service)

        result = configured_service.vote(match.id, Vote.TIE)

        assert result.vote == Vote.TIE

    def test_vote_with_feedback(self, configured_service):
        """Test voting with feedback."""
        match = self._create_ready_match(configured_service)

        result = configured_service.vote(
            match.id,
            Vote.A,
            feedback="Response A was more detailed"
        )

        assert result.feedback == "Response A was more detailed"

    def test_cannot_vote_pending(self, configured_service):
        """Test cannot vote on pending match."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )

        result = configured_service.vote(match.id, Vote.A)

        assert result is None

    def test_skip_match(self, configured_service):
        """Test skipping a match."""
        match = self._create_ready_match(configured_service)

        result = configured_service.skip_match(match.id)

        assert result.status == MatchStatus.SKIPPED

    def test_get_pending_match(self, configured_service):
        """Test getting pending match for voting."""
        match = self._create_ready_match(configured_service)

        pending = configured_service.get_pending_match("s1")

        assert pending is not None
        assert pending.id == match.id

    def test_actual_vote_accounts_for_display_swap(self, configured_service):
        """Test that actual vote accounts for display order swap."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )
        # Force display order
        match.display_a_is_model_a = False
        configured_service._save_match(match)

        configured_service.set_response(match.id, match.model_a, "A", 100, 10)
        configured_service.set_response(match.id, match.model_b, "B", 100, 10)

        # Vote for displayed A (which is actually model_b)
        configured_service.vote(match.id, Vote.A)

        # Actual vote should be B since display was swapped
        match = configured_service.get_match(match.id)
        assert match.get_actual_vote() == Vote.B


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatistics:
    """Tests for model statistics."""

    def _create_and_vote(self, service, vote: Vote):
        """Helper to create match and vote."""
        match = service.create_match(prompt="Test", session_id="s1")
        service.set_response(match.id, match.model_a, "A", 100, 10)
        service.set_response(match.id, match.model_b, "B", 100, 10)

        # Ensure consistent display order for predictable results
        match = service.get_match(match.id)
        match.display_a_is_model_a = True
        service._save_match(match)

        service.vote(match.id, vote)

    def test_stats_after_votes(self, configured_service):
        """Test statistics are updated after votes."""
        self._create_and_vote(configured_service, Vote.A)
        self._create_and_vote(configured_service, Vote.B)
        self._create_and_vote(configured_service, Vote.A)

        stats = configured_service.get_model_stats()

        assert len(stats) == 2

        alpha_stats = next(s for s in stats if s.model == "model-alpha")
        beta_stats = next(s for s in stats if s.model == "model-beta")

        assert alpha_stats.wins == 2
        assert alpha_stats.losses == 1
        assert alpha_stats.total_matches == 3

        assert beta_stats.wins == 1
        assert beta_stats.losses == 2
        assert beta_stats.total_matches == 3

    def test_elo_updated_after_vote(self, configured_service):
        """Test ELO ratings are updated after votes."""
        self._create_and_vote(configured_service, Vote.A)

        stats = configured_service.get_model_stats()

        alpha = next(s for s in stats if s.model == "model-alpha")
        beta = next(s for s in stats if s.model == "model-beta")

        # Winner should have higher ELO
        assert alpha.elo_rating > 1000
        assert beta.elo_rating < 1000

    def test_vs_stats_updated(self, configured_service):
        """Test vs_stats are updated after votes."""
        self._create_and_vote(configured_service, Vote.A)
        self._create_and_vote(configured_service, Vote.A)
        self._create_and_vote(configured_service, Vote.TIE)

        stats = configured_service.get_model_stats("model-alpha")

        assert len(stats) == 1
        vs = stats[0].vs_stats.get("model-beta", {})

        assert vs.get("wins") == 2
        assert vs.get("losses") == 0
        assert vs.get("ties") == 1

    def test_leaderboard_sorted_by_elo(self, configured_service):
        """Test leaderboard is sorted by ELO."""
        # Model-alpha wins more
        for _ in range(5):
            self._create_and_vote(configured_service, Vote.A)

        leaderboard = configured_service.get_leaderboard()

        assert leaderboard[0].model == "model-alpha"
        assert leaderboard[0].elo_rating > leaderboard[1].elo_rating

    def test_overall_stats(self, configured_service):
        """Test overall arena statistics."""
        self._create_and_vote(configured_service, Vote.A)
        self._create_and_vote(configured_service, Vote.B)
        self._create_and_vote(configured_service, Vote.TIE)

        stats = configured_service.get_overall_stats()

        assert stats["totalMatches"] == 3
        assert stats["modelCount"] == 2
        assert stats["votesA"] == 1
        assert stats["votesB"] == 1
        assert stats["votesTie"] == 1
        assert stats["configEnabled"] == True


# ═══════════════════════════════════════════════════════════════════════════════
# Match Display Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatchDisplay:
    """Tests for match display and serialization."""

    def test_to_dict_hides_models_before_vote(self, configured_service):
        """Test models are hidden before voting."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )

        data = match.to_dict()

        assert data["modelA"] == "???"
        assert data["modelB"] == "???"

    def test_to_dict_reveals_models_after_vote(self, configured_service):
        """Test models are revealed after voting."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )
        configured_service.set_response(match.id, match.model_a, "A", 100, 10)
        configured_service.set_response(match.id, match.model_b, "B", 100, 10)
        configured_service.vote(match.id, Vote.A)

        match = configured_service.get_match(match.id)
        data = match.to_dict()

        # Models should be revealed
        assert data["modelA"] != "???"
        assert data["modelB"] != "???"

    def test_to_dict_force_reveal(self, configured_service):
        """Test force reveal models."""
        match = configured_service.create_match(
            prompt="Test",
            session_id="s1",
        )

        data = match.to_dict(reveal_models=True)

        assert data["modelA"] != "???"
        assert data["modelB"] != "???"


# ═══════════════════════════════════════════════════════════════════════════════
# API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestArenaAPI:
    """Tests for Arena API endpoints."""

    @pytest.fixture
    def client(self, temp_db):
        """Create test client with temporary database."""
        import app.services.arena_mode as module
        original_service = module._arena_mode_service
        module._arena_mode_service = ArenaModeService(db_path=temp_db)

        # Enable arena mode
        module._arena_mode_service.set_config(ArenaConfig(
            enabled=True,
            model_a="test-model-a",
            model_b="test-model-b",
        ))

        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        yield client

        module._arena_mode_service = original_service

    def test_get_config(self, client):
        """Test GET /api/arena/config endpoint."""
        response = client.get("/api/arena/config")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "modelA" in data
        assert "modelB" in data

    def test_set_config(self, client):
        """Test PUT /api/arena/config endpoint."""
        response = client.put("/api/arena/config", json={
            "enabled": True,
            "modelA": "new-model-a",
            "modelB": "new-model-b",
            "autoArena": True,
            "sampleRate": 0.5,
            "eloKFactor": 24,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["modelA"] == "new-model-a"
        assert data["modelB"] == "new-model-b"
        assert data["sampleRate"] == 0.5

    def test_check_enabled(self, client):
        """Test GET /api/arena/enabled endpoint."""
        response = client.get("/api/arena/enabled")

        assert response.status_code == 200
        assert response.json()["enabled"] == True

    def test_start_match(self, client):
        """Test POST /api/arena/start endpoint."""
        response = client.post("/api/arena/start", json={
            "prompt": "Test prompt",
            "sessionId": "session-1",
        })

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["prompt"] == "Test prompt"
        assert data["modelA"] == "???"  # Hidden
        assert data["status"] == "pending"

    def test_set_response(self, client):
        """Test POST /api/arena/{id}/response endpoint."""
        # First create a match
        create_response = client.post("/api/arena/start", json={
            "prompt": "Test",
            "sessionId": "s1",
        })
        match_id = create_response.json()["id"]

        # Set response
        response = client.post(f"/api/arena/match/{match_id}/response", json={
            "model": "test-model-a",
            "response": "Test response",
            "latencyMs": 1000,
            "tokens": 100,
        })

        assert response.status_code == 200

    def test_vote(self, client):
        """Test POST /api/arena/{id}/vote endpoint."""
        # Create and complete match
        create_response = client.post("/api/arena/start", json={
            "prompt": "Test",
            "sessionId": "s1",
        })
        match_id = create_response.json()["id"]

        client.post(f"/api/arena/match/{match_id}/response", json={
            "model": "test-model-a",
            "response": "A",
            "latencyMs": 100,
            "tokens": 10,
        })
        client.post(f"/api/arena/match/{match_id}/response", json={
            "model": "test-model-b",
            "response": "B",
            "latencyMs": 100,
            "tokens": 10,
        })

        # Vote
        response = client.post(f"/api/arena/match/{match_id}/vote", json={
            "vote": "A",
            "feedback": "A was better",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "voted"
        assert data["modelA"] != "???"  # Revealed

    def test_get_stats(self, client):
        """Test GET /api/arena/stats endpoint."""
        response = client.get("/api/arena/stats")

        assert response.status_code == 200
        data = response.json()
        assert "totalMatches" in data
        assert "configEnabled" in data

    def test_get_leaderboard(self, client):
        """Test GET /api/arena/leaderboard endpoint."""
        response = client.get("/api/arena/leaderboard")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_history(self, client):
        """Test GET /api/arena/history endpoint."""
        # Create a match first
        client.post("/api/arena/start", json={
            "prompt": "Test",
            "sessionId": "s1",
        })

        response = client.get("/api/arena/history")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_arena_mode_service_singleton(self, temp_db):
        """Test singleton returns same instance."""
        import app.services.arena_mode as module
        module._arena_mode_service = None

        service1 = get_arena_mode_service(temp_db)
        service2 = get_arena_mode_service(temp_db)

        assert service1 is service2

        module._arena_mode_service = None
