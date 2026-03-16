"""
Arena Mode Service - Model Comparison with Blind Voting.

Side-by-side comparison of model outputs for quality evaluation.
Can be enabled/disabled via settings with configurable model pairs.

Features:
- Blind A/B comparison (model names hidden until vote)
- ELO rating system for model ranking
- Performance metrics (latency, tokens)
- Match history and statistics
- Integration with agent calls
"""

import logging
import math
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

class Vote(str, Enum):
    """Vote options."""
    A = "A"
    B = "B"
    TIE = "tie"


class MatchStatus(str, Enum):
    """Match status."""
    PENDING = "pending"      # Waiting for responses
    READY = "ready"          # Both responses received, waiting for vote
    VOTED = "voted"          # User has voted
    SKIPPED = "skipped"      # User skipped voting


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Column Constants (Performance: avoid SELECT *)
# ═══════════════════════════════════════════════════════════════════════════════

_MATCH_COLUMNS = """id, timestamp, session_id, prompt, context, model_a, model_b,
    response_a, response_b, latency_a, latency_b, tokens_a, tokens_b,
    status, vote, voted_at, feedback, display_a_is_model_a"""

_MODEL_STATS_COLUMNS = "model, wins, losses, ties, total_matches, elo_rating, total_latency, total_tokens, vs_stats_json"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ArenaConfig:
    """Arena mode configuration."""
    enabled: bool = False
    model_a: str = ""         # First model for comparison
    model_b: str = ""         # Second model for comparison
    auto_arena: bool = False  # Auto-trigger arena for agent calls
    sample_rate: float = 1.0  # Sampling rate for auto-arena (0.0-1.0)
    elo_k_factor: int = 32    # ELO K-factor for rating updates

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "modelA": self.model_a,
            "modelB": self.model_b,
            "autoArena": self.auto_arena,
            "sampleRate": self.sample_rate,
            "eloKFactor": self.elo_k_factor,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ArenaConfig":
        return ArenaConfig(
            enabled=data.get("enabled", False),
            model_a=data.get("modelA", ""),
            model_b=data.get("modelB", ""),
            auto_arena=data.get("autoArena", False),
            sample_rate=data.get("sampleRate", 1.0),
            elo_k_factor=data.get("eloKFactor", 32),
        )


@dataclass
class ArenaMatch:
    """A single arena match comparing two models."""
    id: str
    timestamp: int
    session_id: str

    # Prompt
    prompt: str
    context: Optional[str] = None

    # Models (hidden until vote)
    model_a: str = ""
    model_b: str = ""

    # Responses
    response_a: str = ""
    response_b: str = ""

    # Metrics
    latency_a: int = 0  # ms
    latency_b: int = 0
    tokens_a: int = 0
    tokens_b: int = 0

    # Status and vote
    status: MatchStatus = MatchStatus.PENDING
    vote: Optional[Vote] = None
    voted_at: Optional[int] = None

    # Optional feedback
    feedback: Optional[str] = None

    # For display (randomized assignment)
    display_a_is_model_a: bool = True  # If False, A/B are swapped in display

    def to_dict(self, reveal_models: bool = False) -> Dict[str, Any]:
        """Convert to dict. Only reveal model names if voted or reveal_models=True."""
        result = {
            "id": self.id,
            "timestamp": self.timestamp,
            "sessionId": self.session_id,
            "prompt": self.prompt,
            "context": self.context,
            "responseA": self.response_a if self.display_a_is_model_a else self.response_b,
            "responseB": self.response_b if self.display_a_is_model_a else self.response_a,
            "latencyA": self.latency_a if self.display_a_is_model_a else self.latency_b,
            "latencyB": self.latency_b if self.display_a_is_model_a else self.latency_a,
            "tokensA": self.tokens_a if self.display_a_is_model_a else self.tokens_b,
            "tokensB": self.tokens_b if self.display_a_is_model_a else self.tokens_a,
            "status": self.status.value,
            "vote": self.vote.value if self.vote else None,
            "votedAt": self.voted_at,
            "feedback": self.feedback,
        }

        # Only reveal model names if voted or explicitly requested
        if reveal_models or self.status == MatchStatus.VOTED:
            result["modelA"] = self.model_a if self.display_a_is_model_a else self.model_b
            result["modelB"] = self.model_b if self.display_a_is_model_a else self.model_a
        else:
            result["modelA"] = "???"
            result["modelB"] = "???"

        return result

    def get_actual_vote(self) -> Optional[Vote]:
        """Get the actual vote accounting for display swap."""
        if not self.vote:
            return None

        if self.display_a_is_model_a:
            return self.vote
        else:
            # Swap A/B if display was swapped
            if self.vote == Vote.A:
                return Vote.B
            elif self.vote == Vote.B:
                return Vote.A
            return self.vote  # TIE stays the same


@dataclass
class ModelStats:
    """Statistics for a single model."""
    model: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    total_matches: int = 0
    win_rate: float = 0.0
    elo_rating: float = 1000.0
    avg_latency: float = 0.0
    avg_tokens: float = 0.0
    vs_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "totalMatches": self.total_matches,
            "winRate": self.win_rate,
            "eloRating": self.elo_rating,
            "avgLatency": self.avg_latency,
            "avgTokens": self.avg_tokens,
            "vsStats": self.vs_stats,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ELO Rating Calculator
# ═══════════════════════════════════════════════════════════════════════════════

class EloCalculator:
    """ELO rating system for model ranking."""

    @staticmethod
    def expected_score(rating_a: float, rating_b: float) -> float:
        """Calculate expected score for player A."""
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))

    @staticmethod
    def update_ratings(
        rating_a: float,
        rating_b: float,
        result: Vote,
        k_factor: int = 32,
    ) -> Tuple[float, float]:
        """
        Update ELO ratings based on match result.

        Args:
            rating_a: Current rating of model A
            rating_b: Current rating of model B
            result: Match result (A wins, B wins, or Tie)
            k_factor: K-factor for rating adjustment

        Returns:
            Tuple of (new_rating_a, new_rating_b)
        """
        expected_a = EloCalculator.expected_score(rating_a, rating_b)
        expected_b = 1.0 - expected_a

        if result == Vote.A:
            actual_a, actual_b = 1.0, 0.0
        elif result == Vote.B:
            actual_a, actual_b = 0.0, 1.0
        else:  # TIE
            actual_a, actual_b = 0.5, 0.5

        new_rating_a = rating_a + k_factor * (actual_a - expected_a)
        new_rating_b = rating_b + k_factor * (actual_b - expected_b)

        return new_rating_a, new_rating_b


# ═══════════════════════════════════════════════════════════════════════════════
# Arena Mode Service
# ═══════════════════════════════════════════════════════════════════════════════

class ArenaModeService:
    """Service for arena mode model comparison."""

    def __init__(self, db_path: str = "data/arena_mode.db"):
        self.db_path = db_path
        self.config = ArenaConfig()
        self._init_db()
        self._load_config()

    def _init_db(self):
        """Initialize SQLite database."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    context TEXT,
                    model_a TEXT NOT NULL,
                    model_b TEXT NOT NULL,
                    response_a TEXT,
                    response_b TEXT,
                    latency_a INTEGER DEFAULT 0,
                    latency_b INTEGER DEFAULT 0,
                    tokens_a INTEGER DEFAULT 0,
                    tokens_b INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    vote TEXT,
                    voted_at INTEGER,
                    feedback TEXT,
                    display_a_is_model_a INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS model_stats (
                    model TEXT PRIMARY KEY,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    ties INTEGER DEFAULT 0,
                    total_matches INTEGER DEFAULT 0,
                    elo_rating REAL DEFAULT 1000.0,
                    total_latency INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    vs_stats_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_matches_session ON matches(session_id);
                CREATE INDEX IF NOT EXISTS idx_matches_timestamp ON matches(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
            """)

    def _load_config(self):
        """Load configuration from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT value FROM config WHERE key = 'settings'")
            row = cursor.fetchone()
            if row:
                data = json_loads(row[0])
                self.config = ArenaConfig.from_dict(data)

    # ───────────────────────────────────────────────────────────────────────────
    # Configuration
    # ───────────────────────────────────────────────────────────────────────────

    def get_config(self) -> ArenaConfig:
        """Get current configuration."""
        return self.config

    def set_config(self, config: ArenaConfig) -> ArenaConfig:
        """Update configuration."""
        self.config = config

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                ("settings", json_dumps(config.to_dict()))
            )

        return self.config

    def is_enabled(self) -> bool:
        """Check if arena mode is enabled."""
        return bool(self.config.enabled and self.config.model_a and self.config.model_b)

    # ───────────────────────────────────────────────────────────────────────────
    # Match Management
    # ───────────────────────────────────────────────────────────────────────────

    def create_match(
        self,
        prompt: str,
        session_id: str,
        context: Optional[str] = None,
        model_a: Optional[str] = None,
        model_b: Optional[str] = None,
    ) -> ArenaMatch:
        """
        Create a new arena match.

        Args:
            prompt: The prompt to send to both models
            session_id: Chat session ID
            context: Optional context
            model_a: Model A (defaults to config)
            model_b: Model B (defaults to config)

        Returns:
            ArenaMatch ready for responses
        """
        import random

        match_id = str(uuid.uuid4())[:12]
        timestamp = int(time.time())

        # Use config models if not specified
        actual_model_a = model_a or self.config.model_a
        actual_model_b = model_b or self.config.model_b

        # Randomly swap display order for blind comparison
        display_a_is_model_a = random.choice([True, False])

        match = ArenaMatch(
            id=match_id,
            timestamp=timestamp,
            session_id=session_id,
            prompt=prompt,
            context=context,
            model_a=actual_model_a,
            model_b=actual_model_b,
            status=MatchStatus.PENDING,
            display_a_is_model_a=display_a_is_model_a,
        )

        self._save_match(match)
        return match

    def set_response(
        self,
        match_id: str,
        model: str,
        response: str,
        latency_ms: int,
        tokens: int,
    ) -> Optional[ArenaMatch]:
        """
        Set the response for a model in a match.

        Args:
            match_id: Match ID
            model: Model name
            response: Model response
            latency_ms: Response latency in milliseconds
            tokens: Token count

        Returns:
            Updated match or None if not found
        """
        match = self.get_match(match_id)
        if not match:
            return None

        if model == match.model_a:
            match.response_a = response
            match.latency_a = latency_ms
            match.tokens_a = tokens
        elif model == match.model_b:
            match.response_b = response
            match.latency_b = latency_ms
            match.tokens_b = tokens
        else:
            logger.warning(f"Unknown model {model} for match {match_id}")
            return None

        # Check if both responses are ready
        if match.response_a and match.response_b:
            match.status = MatchStatus.READY

        self._save_match(match)
        return match

    def vote(
        self,
        match_id: str,
        vote: Vote,
        feedback: Optional[str] = None,
    ) -> Optional[ArenaMatch]:
        """
        Submit a vote for a match.

        Args:
            match_id: Match ID
            vote: Vote (A, B, or tie)
            feedback: Optional feedback

        Returns:
            Updated match with revealed models
        """
        match = self.get_match(match_id)
        if not match:
            return None

        if match.status != MatchStatus.READY:
            logger.warning(f"Cannot vote on match {match_id} with status {match.status}")
            return None

        match.vote = vote
        match.voted_at = int(time.time())
        match.status = MatchStatus.VOTED
        match.feedback = feedback

        self._save_match(match)

        # Update model statistics
        actual_vote = match.get_actual_vote()
        self._update_stats(match.model_a, match.model_b, actual_vote)

        return match

    def skip_match(self, match_id: str) -> Optional[ArenaMatch]:
        """Skip voting on a match."""
        match = self.get_match(match_id)
        if not match:
            return None

        match.status = MatchStatus.SKIPPED
        self._save_match(match)
        return match

    def get_match(self, match_id: str) -> Optional[ArenaMatch]:
        """Get a match by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"SELECT {_MATCH_COLUMNS} FROM matches WHERE id = ?", (match_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_match(row)

    def get_pending_match(self, session_id: str) -> Optional[ArenaMatch]:
        """Get any pending match for a session that needs voting."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"SELECT {_MATCH_COLUMNS} FROM matches WHERE session_id = ? AND status = 'ready' ORDER BY timestamp DESC LIMIT 1",
                (session_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_match(row)

    def get_matches(
        self,
        session_id: Optional[str] = None,
        status: Optional[MatchStatus] = None,
        limit: int = 50,
    ) -> List[ArenaMatch]:
        """Get matches with optional filters."""
        query = f"SELECT {_MATCH_COLUMNS} FROM matches WHERE 1=1"
        params = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_match(row) for row in cursor.fetchall()]

    def _save_match(self, match: ArenaMatch):
        """Save a match to the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO matches
                (id, timestamp, session_id, prompt, context, model_a, model_b,
                 response_a, response_b, latency_a, latency_b, tokens_a, tokens_b,
                 status, vote, voted_at, feedback, display_a_is_model_a)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                match.id,
                match.timestamp,
                match.session_id,
                match.prompt,
                match.context,
                match.model_a,
                match.model_b,
                match.response_a,
                match.response_b,
                match.latency_a,
                match.latency_b,
                match.tokens_a,
                match.tokens_b,
                match.status.value,
                match.vote.value if match.vote else None,
                match.voted_at,
                match.feedback,
                1 if match.display_a_is_model_a else 0,
            ))

    def _row_to_match(self, row) -> ArenaMatch:
        """Convert database row to ArenaMatch."""
        return ArenaMatch(
            id=row[0],
            timestamp=row[1],
            session_id=row[2],
            prompt=row[3],
            context=row[4],
            model_a=row[5],
            model_b=row[6],
            response_a=row[7] or "",
            response_b=row[8] or "",
            latency_a=row[9] or 0,
            latency_b=row[10] or 0,
            tokens_a=row[11] or 0,
            tokens_b=row[12] or 0,
            status=MatchStatus(row[13]),
            vote=Vote(row[14]) if row[14] else None,
            voted_at=row[15],
            feedback=row[16],
            display_a_is_model_a=bool(row[17]),
        )

    # ───────────────────────────────────────────────────────────────────────────
    # Statistics
    # ───────────────────────────────────────────────────────────────────────────

    def _update_stats(self, model_a: str, model_b: str, vote: Optional[Vote]):
        """Update model statistics after a vote."""
        if not vote:
            return

        with sqlite3.connect(self.db_path) as conn:
            # Ensure both models have stats entries
            for model in [model_a, model_b]:
                conn.execute(
                    "INSERT OR IGNORE INTO model_stats (model) VALUES (?)",
                    (model,)
                )

            # Get current stats
            cursor = conn.execute(
                "SELECT model, elo_rating, vs_stats_json FROM model_stats WHERE model IN (?, ?)",
                (model_a, model_b)
            )
            stats = {row[0]: {"elo": row[1], "vs": json_loads(row[2])} for row in cursor.fetchall()}

            # Calculate new ELO ratings
            elo_a = stats.get(model_a, {}).get("elo", 1000.0)
            elo_b = stats.get(model_b, {}).get("elo", 1000.0)
            new_elo_a, new_elo_b = EloCalculator.update_ratings(
                elo_a, elo_b, vote, self.config.elo_k_factor
            )

            # Update vs_stats
            vs_a = stats.get(model_a, {}).get("vs", {})
            vs_b = stats.get(model_b, {}).get("vs", {})

            if model_b not in vs_a:
                vs_a[model_b] = {"wins": 0, "losses": 0, "ties": 0}
            if model_a not in vs_b:
                vs_b[model_a] = {"wins": 0, "losses": 0, "ties": 0}

            # Update win/loss/tie counts
            if vote == Vote.A:
                conn.execute(
                    "UPDATE model_stats SET wins = wins + 1, total_matches = total_matches + 1, elo_rating = ? WHERE model = ?",
                    (new_elo_a, model_a)
                )
                conn.execute(
                    "UPDATE model_stats SET losses = losses + 1, total_matches = total_matches + 1, elo_rating = ? WHERE model = ?",
                    (new_elo_b, model_b)
                )
                vs_a[model_b]["wins"] += 1
                vs_b[model_a]["losses"] += 1
            elif vote == Vote.B:
                conn.execute(
                    "UPDATE model_stats SET losses = losses + 1, total_matches = total_matches + 1, elo_rating = ? WHERE model = ?",
                    (new_elo_a, model_a)
                )
                conn.execute(
                    "UPDATE model_stats SET wins = wins + 1, total_matches = total_matches + 1, elo_rating = ? WHERE model = ?",
                    (new_elo_b, model_b)
                )
                vs_a[model_b]["losses"] += 1
                vs_b[model_a]["wins"] += 1
            else:  # TIE
                conn.execute(
                    "UPDATE model_stats SET ties = ties + 1, total_matches = total_matches + 1, elo_rating = ? WHERE model = ?",
                    (new_elo_a, model_a)
                )
                conn.execute(
                    "UPDATE model_stats SET ties = ties + 1, total_matches = total_matches + 1, elo_rating = ? WHERE model = ?",
                    (new_elo_b, model_b)
                )
                vs_a[model_b]["ties"] += 1
                vs_b[model_a]["ties"] += 1

            # Save updated vs_stats
            conn.execute(
                "UPDATE model_stats SET vs_stats_json = ? WHERE model = ?",
                (json_dumps(vs_a), model_a)
            )
            conn.execute(
                "UPDATE model_stats SET vs_stats_json = ? WHERE model = ?",
                (json_dumps(vs_b), model_b)
            )

    def get_model_stats(self, model: Optional[str] = None) -> List[ModelStats]:
        """Get statistics for one or all models."""
        with sqlite3.connect(self.db_path) as conn:
            if model:
                cursor = conn.execute(
                    f"SELECT {_MODEL_STATS_COLUMNS} FROM model_stats WHERE model = ?", (model,)
                )
            else:
                cursor = conn.execute(
                    f"SELECT {_MODEL_STATS_COLUMNS} FROM model_stats ORDER BY elo_rating DESC"
                )

            stats = []
            for row in cursor.fetchall():
                total = row[4] or 1  # Avoid division by zero
                win_rate = row[1] / total if total > 0 else 0.0
                avg_latency = row[6] / total if row[6] else 0.0
                avg_tokens = row[7] / total if row[7] else 0.0

                stats.append(ModelStats(
                    model=row[0],
                    wins=row[1],
                    losses=row[2],
                    ties=row[3],
                    total_matches=row[4],
                    win_rate=win_rate,
                    elo_rating=row[5],
                    avg_latency=avg_latency,
                    avg_tokens=avg_tokens,
                    vs_stats=json_loads(row[8]) if row[8] else {},
                ))

            return stats

    def get_leaderboard(self, limit: int = 10) -> List[ModelStats]:
        """Get model leaderboard sorted by ELO rating."""
        return self.get_model_stats()[:limit]

    def get_overall_stats(self) -> Dict[str, Any]:
        """Get overall arena statistics."""
        with sqlite3.connect(self.db_path) as conn:
            total_matches = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE status = 'voted'"
            ).fetchone()[0]

            model_count = conn.execute(
                "SELECT COUNT(*) FROM model_stats"
            ).fetchone()[0]

            votes_a = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE vote = 'A'"
            ).fetchone()[0]

            votes_b = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE vote = 'B'"
            ).fetchone()[0]

            votes_tie = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE vote = 'tie'"
            ).fetchone()[0]

            pending = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE status = 'ready'"
            ).fetchone()[0]

            return {
                "totalMatches": total_matches,
                "modelCount": model_count,
                "votesA": votes_a,
                "votesB": votes_b,
                "votesTie": votes_tie,
                "pendingVotes": pending,
                "configEnabled": self.config.enabled,
                "configModelA": self.config.model_a,
                "configModelB": self.config.model_b,
            }


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton Access
# ═══════════════════════════════════════════════════════════════════════════════

_arena_mode_service: Optional[ArenaModeService] = None


def get_arena_mode_service(db_path: str = "data/arena_mode.db") -> ArenaModeService:
    """Get or create the arena mode service singleton."""
    global _arena_mode_service
    if _arena_mode_service is None:
        _arena_mode_service = ArenaModeService(db_path)
    return _arena_mode_service
