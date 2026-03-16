"""
Error Pattern Learning Service - Phase 5.

Ermoeglicht:
- Automatische Pattern-Extraktion bei Fehlern
- Keyword-basierte Similarity
- Confidence Score Berechnung
- User Feedback Loop
- SQLite Persistenz
"""

import hashlib
import json  # For file I/O (json.load/dump)
import logging
import re
import sqlite3

from app.utils.json_utils import json_loads, json_dumps
from dataclasses import dataclass, field, asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Pre-compiled Regex Patterns (Performance Optimization)
# ═══════════════════════════════════════════════════════════════════════════════

# Error type patterns - compiled once at module load
_JAVA_ERROR_PATTERNS = [
    re.compile(r"(NullPointerException)"),
    re.compile(r"(IllegalArgumentException)"),
    re.compile(r"(IllegalStateException)"),
    re.compile(r"(IndexOutOfBoundsException)"),
    re.compile(r"(SQLException)"),
    re.compile(r"(IOException)"),
    re.compile(r"(ClassNotFoundException)"),
    re.compile(r"(NoSuchMethodException)"),
    re.compile(r"(RuntimeException)"),
    re.compile(r"(Exception)"),
]

_PYTHON_ERROR_PATTERNS = [
    re.compile(r"(TypeError)"),
    re.compile(r"(ValueError)"),
    re.compile(r"(KeyError)"),
    re.compile(r"(IndexError)"),
    re.compile(r"(AttributeError)"),
    re.compile(r"(ImportError)"),
    re.compile(r"(FileNotFoundError)"),
    re.compile(r"(RuntimeError)"),
    re.compile(r"(Exception)"),
]

_ALL_ERROR_PATTERNS = _JAVA_ERROR_PATTERNS + _PYTHON_ERROR_PATTERNS

# Stack trace normalization patterns
_RE_LINE_NUMBERS = re.compile(r":(\d+)")
_RE_TIMESTAMPS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_RE_HEX_ADDRESSES = re.compile(r"@[0-9a-fA-F]+")
_RE_UUIDS = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_RE_WHITESPACE = re.compile(r"\s+")

# Keyword extraction patterns
_KEYWORD_PATTERNS = [
    re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", re.IGNORECASE),  # CamelCase
    re.compile(r"\b([a-z]+_[a-z]+(?:_[a-z]+)*)\b", re.IGNORECASE),     # snake_case
    re.compile(r"\b(null|undefined|error|exception|failed|invalid)\b", re.IGNORECASE),
    re.compile(r"\b([a-z]+(?:service|controller|manager|handler|factory|repository))\b", re.IGNORECASE),
]
_RE_WORDS = re.compile(r"\b[a-zA-Z]{3,}\b")


@dataclass
class ErrorPattern:
    """Ein gelerntes Fehler-Muster mit Loesung."""

    id: str
    created_at: datetime
    updated_at: datetime

    # Error Identification
    error_type: str              # "NullPointerException", "TypeError", etc.
    error_regex: str             # Regex fuer Stack-Trace Matching
    error_hash: str              # Hash fuer Quick-Lookup

    # Context Keywords (fuer Similarity)
    context_keywords: List[str]  # Extracted keywords

    # File Context
    file_patterns: List[str]     # Glob patterns: ["*Service.java", "*Controller.java"]
    code_context: str            # Surrounding code snippet

    # Solution
    solution_description: str
    solution_steps: List[str]
    solution_code: Optional[str]
    tools_used: List[str]        # ["edit_file", "search_code"]
    files_changed: List[str]

    # Statistics
    times_seen: int = 0
    times_solved: int = 0
    times_suggested: int = 0
    times_accepted: int = 0
    times_rejected: int = 0

    # Confidence
    confidence: float = 0.5      # 0.0 - 1.0

    # User Feedback
    user_ratings: List[int] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        if self.times_suggested == 0:
            return 0.0
        return self.times_accepted / self.times_suggested

    @property
    def avg_rating(self) -> float:
        if not self.user_ratings:
            return 0.0
        return sum(self.user_ratings) / len(self.user_ratings)

    def update_confidence(self):
        """Berechnet Confidence basierend auf Statistiken."""
        # Basis: Acceptance Rate
        base = self.acceptance_rate * 0.4

        # Bonus: Anzahl erfolgreicher Loesungen
        solve_bonus = min(self.times_solved / 10, 0.3)

        # Bonus: User Ratings
        rating_bonus = (self.avg_rating / 5.0) * 0.2 if self.user_ratings else 0

        # Penalty: Alter (decay)
        days_old = (datetime.now() - self.updated_at).days
        decay = max(0, 1 - (days_old / 90))  # 90 Tage bis 0

        self.confidence = (base + solve_bonus + rating_bonus) * decay
        self.confidence = max(0.1, min(1.0, self.confidence))

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary fuer JSON/DB."""
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error_type": self.error_type,
            "error_regex": self.error_regex,
            "error_hash": self.error_hash,
            "context_keywords": self.context_keywords,
            "file_patterns": self.file_patterns,
            "code_context": self.code_context,
            "solution_description": self.solution_description,
            "solution_steps": self.solution_steps,
            "solution_code": self.solution_code,
            "tools_used": self.tools_used,
            "files_changed": self.files_changed,
            "times_seen": self.times_seen,
            "times_solved": self.times_solved,
            "times_suggested": self.times_suggested,
            "times_accepted": self.times_accepted,
            "times_rejected": self.times_rejected,
            "confidence": self.confidence,
            "user_ratings": self.user_ratings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ErrorPattern":
        """Erstellt Pattern aus Dictionary."""
        return cls(
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            error_type=data["error_type"],
            error_regex=data["error_regex"],
            error_hash=data["error_hash"],
            context_keywords=data.get("context_keywords", []),
            file_patterns=data.get("file_patterns", []),
            code_context=data.get("code_context", ""),
            solution_description=data["solution_description"],
            solution_steps=data.get("solution_steps", []),
            solution_code=data.get("solution_code"),
            tools_used=data.get("tools_used", []),
            files_changed=data.get("files_changed", []),
            times_seen=data.get("times_seen", 0),
            times_solved=data.get("times_solved", 0),
            times_suggested=data.get("times_suggested", 0),
            times_accepted=data.get("times_accepted", 0),
            times_rejected=data.get("times_rejected", 0),
            confidence=data.get("confidence", 0.5),
            user_ratings=data.get("user_ratings", []),
        )


class PatternLearner:
    """
    Service fuer Error Pattern Learning.

    Features:
    - Pattern-Extraktion aus Fehlern
    - Similarity-basierte Suche
    - Confidence-Berechnung
    - SQLite-Persistenz
    """

    def __init__(self, db_path: str = "./data/patterns.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialisiert die SQLite-Datenbank."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS error_patterns (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                error_hash TEXT NOT NULL,
                error_type TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_error_hash ON error_patterns(error_hash)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_error_type ON error_patterns(error_type)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_confidence ON error_patterns(confidence DESC)
        """)

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Erstellt eine DB-Verbindung."""
        return sqlite3.connect(self.db_path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Pattern Extraction
    # ═══════════════════════════════════════════════════════════════════════════════

    def extract_error_type(self, error_text: str) -> str:
        """Extrahiert den Error-Typ aus einem Fehlertext."""
        # Use pre-compiled patterns for performance
        for pattern in _ALL_ERROR_PATTERNS:
            match = pattern.search(error_text)
            if match:
                return match.group(1)

        return "UnknownError"

    def normalize_stack_trace(self, stack_trace: str) -> str:
        """
        Normalisiert einen Stack-Trace fuer Hashing.
        Entfernt Line Numbers, Timestamps, Instance IDs.
        Uses pre-compiled patterns for performance.
        """
        normalized = stack_trace

        # Use pre-compiled patterns (10-50x faster)
        normalized = _RE_LINE_NUMBERS.sub("", normalized)
        normalized = _RE_TIMESTAMPS.sub("", normalized)
        normalized = _RE_HEX_ADDRESSES.sub("", normalized)
        normalized = _RE_UUIDS.sub("", normalized)
        normalized = _RE_WHITESPACE.sub(" ", normalized).strip()

        return normalized

    def generate_error_hash(self, error_type: str, normalized_trace: str) -> str:
        """Generiert einen Hash fuer schnelles Lookup."""
        content = f"{error_type}:{normalized_trace}"
        return hashlib.md5(content.encode()).hexdigest()

    def extract_keywords(self, text: str) -> List[str]:
        """Extrahiert relevante Keywords aus Text. Uses pre-compiled patterns."""
        keywords = set()

        # Use pre-compiled keyword patterns (much faster)
        for pattern in _KEYWORD_PATTERNS:
            for match in pattern.finditer(text):
                kw = match.group(1).lower()
                if len(kw) > 2:
                    keywords.add(kw)

        # Also extract words from error messages using pre-compiled pattern
        _STOPWORDS = {"the", "and", "for", "from", "with", "was", "are", "this", "that"}
        for match in _RE_WORDS.finditer(text):
            word = match.group(0).lower()
            if word not in _STOPWORDS:
                keywords.add(word)

        return list(keywords)[:20]  # Limit to 20 keywords

    def extract_file_patterns(self, files_changed: List[str]) -> List[str]:
        """Extrahiert Glob-Patterns aus geaenderten Dateien."""
        patterns = set()
        for file_path in files_changed:
            # Extract filename pattern
            filename = Path(file_path).name
            stem = Path(file_path).stem

            # Common patterns
            if "Service" in stem:
                patterns.add("*Service.*")
            if "Controller" in stem:
                patterns.add("*Controller.*")
            if "Repository" in stem:
                patterns.add("*Repository.*")
            if "Handler" in stem:
                patterns.add("*Handler.*")
            if "Manager" in stem:
                patterns.add("*Manager.*")

            # Add actual filename as pattern
            patterns.add(f"*{Path(file_path).suffix}")

        return list(patterns)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Similarity Calculation
    # ═══════════════════════════════════════════════════════════════════════════════

    def calculate_keyword_similarity(
        self,
        keywords1: List[str],
        keywords2: List[str]
    ) -> float:
        """Berechnet Jaccard-Similarity zwischen Keyword-Sets."""
        if not keywords1 or not keywords2:
            return 0.0

        set1 = set(keywords1)
        set2 = set(keywords2)

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union > 0 else 0.0

    def find_similar_patterns(
        self,
        error_type: str,
        keywords: List[str],
        min_similarity: float = 0.3,
        limit: int = 5
    ) -> List[Tuple[ErrorPattern, float]]:
        """Findet aehnliche Patterns basierend auf Keywords."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Get patterns of same error type
        cursor.execute(
            "SELECT data FROM error_patterns WHERE error_type = ? ORDER BY confidence DESC",
            (error_type,)
        )

        results = []
        for row in cursor.fetchall():
            pattern = ErrorPattern.from_dict(json_loads(row[0]))
            similarity = self.calculate_keyword_similarity(keywords, pattern.context_keywords)

            if similarity >= min_similarity:
                results.append((pattern, similarity))

        conn.close()

        # Sort by similarity and return top N
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    # ═══════════════════════════════════════════════════════════════════════════════
    # Pattern Management
    # ═══════════════════════════════════════════════════════════════════════════════

    def get_pattern_by_hash(self, error_hash: str) -> Optional[ErrorPattern]:
        """Sucht Pattern nach Hash."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT data FROM error_patterns WHERE error_hash = ?",
            (error_hash,)
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            return ErrorPattern.from_dict(json_loads(row[0]))
        return None

    def get_pattern_by_id(self, pattern_id: str) -> Optional[ErrorPattern]:
        """Sucht Pattern nach ID."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT data FROM error_patterns WHERE id = ?",
            (pattern_id,)
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            return ErrorPattern.from_dict(json_loads(row[0]))
        return None

    def list_patterns(
        self,
        min_confidence: float = 0.0,
        error_type: Optional[str] = None,
        limit: int = 50
    ) -> List[ErrorPattern]:
        """Listet Patterns mit optionalen Filtern."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if error_type:
            cursor.execute(
                """SELECT data FROM error_patterns
                   WHERE confidence >= ? AND error_type = ?
                   ORDER BY confidence DESC LIMIT ?""",
                (min_confidence, error_type, limit)
            )
        else:
            cursor.execute(
                """SELECT data FROM error_patterns
                   WHERE confidence >= ?
                   ORDER BY confidence DESC LIMIT ?""",
                (min_confidence, limit)
            )

        patterns = [
            ErrorPattern.from_dict(json_loads(row[0]))
            for row in cursor.fetchall()
        ]

        conn.close()
        return patterns

    def save_pattern(self, pattern: ErrorPattern) -> bool:
        """Speichert oder aktualisiert ein Pattern."""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """INSERT OR REPLACE INTO error_patterns
                   (id, data, error_hash, error_type, confidence, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    pattern.id,
                    json_dumps(pattern.to_dict()),
                    pattern.error_hash,
                    pattern.error_type,
                    pattern.confidence,
                    pattern.created_at.isoformat(),
                    pattern.updated_at.isoformat(),
                )
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save pattern: {e}")
            return False
        finally:
            conn.close()

    def delete_pattern(self, pattern_id: str) -> bool:
        """Loescht ein Pattern."""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM error_patterns WHERE id = ?", (pattern_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete pattern: {e}")
            return False
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════════════════════
    # Learning Pipeline
    # ═══════════════════════════════════════════════════════════════════════════════

    def learn_pattern(
        self,
        error_text: str,
        stack_trace: str,
        solution_description: str,
        solution_steps: List[str],
        solution_code: Optional[str] = None,
        tools_used: List[str] = None,
        files_changed: List[str] = None,
        code_context: str = ""
    ) -> Tuple[ErrorPattern, bool]:
        """
        Lernt ein neues Pattern oder aktualisiert ein bestehendes.

        Returns:
            Tuple[ErrorPattern, bool]: (pattern, is_new)
        """
        import uuid

        # Extract error info
        error_type = self.extract_error_type(error_text)
        normalized = self.normalize_stack_trace(stack_trace)
        error_hash = self.generate_error_hash(error_type, normalized)

        # Check if pattern exists
        existing = self.get_pattern_by_hash(error_hash)

        if existing:
            # Update existing pattern
            existing.times_seen += 1
            existing.times_solved += 1
            existing.updated_at = datetime.now()

            # Add new keywords
            new_keywords = self.extract_keywords(error_text + " " + code_context)
            existing.context_keywords = list(
                set(existing.context_keywords) | set(new_keywords)
            )[:30]

            # Update confidence
            existing.update_confidence()
            self.save_pattern(existing)

            return existing, False
        else:
            # Create new pattern
            keywords = self.extract_keywords(error_text + " " + code_context)
            file_patterns = self.extract_file_patterns(files_changed or [])

            # Generate regex from error text
            error_regex = re.escape(error_type)

            pattern = ErrorPattern(
                id=str(uuid.uuid4()),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                error_type=error_type,
                error_regex=error_regex,
                error_hash=error_hash,
                context_keywords=keywords,
                file_patterns=file_patterns,
                code_context=code_context[:1000],  # Limit context size
                solution_description=solution_description,
                solution_steps=solution_steps,
                solution_code=solution_code,
                tools_used=tools_used or [],
                files_changed=files_changed or [],
                times_seen=1,
                times_solved=1,
                confidence=0.5,
            )

            self.save_pattern(pattern)
            return pattern, True

    def suggest_pattern(
        self,
        error_text: str,
        stack_trace: str = "",
        file_context: str = ""
    ) -> Tuple[Optional[ErrorPattern], float, List[Tuple[ErrorPattern, float]]]:
        """
        Schlaegt ein Pattern fuer einen Fehler vor.

        Returns:
            Tuple[pattern, confidence, alternatives]
        """
        # Extract error info
        error_type = self.extract_error_type(error_text)
        normalized = self.normalize_stack_trace(stack_trace)
        error_hash = self.generate_error_hash(error_type, normalized)

        # Check for exact match
        exact_match = self.get_pattern_by_hash(error_hash)
        if exact_match:
            exact_match.times_suggested += 1
            self.save_pattern(exact_match)
            return exact_match, exact_match.confidence, []

        # Find similar patterns
        keywords = self.extract_keywords(error_text + " " + file_context)
        similar = self.find_similar_patterns(error_type, keywords, min_similarity=0.3)

        if similar:
            best_pattern, best_similarity = similar[0]
            best_pattern.times_suggested += 1
            self.save_pattern(best_pattern)

            # Adjust confidence based on similarity
            adjusted_confidence = best_pattern.confidence * best_similarity

            return best_pattern, adjusted_confidence, similar[1:]

        return None, 0.0, []

    def record_feedback(
        self,
        pattern_id: str,
        accepted: bool,
        rating: Optional[int] = None,
        comment: Optional[str] = None
    ) -> Optional[ErrorPattern]:
        """Zeichnet User-Feedback fuer ein Pattern auf."""
        pattern = self.get_pattern_by_id(pattern_id)
        if not pattern:
            return None

        if accepted:
            pattern.times_accepted += 1
        else:
            pattern.times_rejected += 1

        if rating is not None and 1 <= rating <= 5:
            pattern.user_ratings.append(rating)
            # Keep only last 50 ratings
            pattern.user_ratings = pattern.user_ratings[-50:]

        pattern.updated_at = datetime.now()
        pattern.update_confidence()

        self.save_pattern(pattern)
        return pattern

    # ═══════════════════════════════════════════════════════════════════════════════
    # Maintenance
    # ═══════════════════════════════════════════════════════════════════════════════

    def cleanup_old_patterns(self, max_age_days: int = 90) -> int:
        """Loescht alte Patterns mit niedriger Confidence."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cutoff_date = datetime.now().isoformat()

        # Delete patterns older than max_age_days with low confidence
        cursor.execute(
            """DELETE FROM error_patterns
               WHERE confidence < 0.3
               AND julianday(?) - julianday(updated_at) > ?""",
            (cutoff_date, max_age_days)
        )

        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"Cleaned up {deleted} old patterns")
        return deleted

    def export_patterns(self, output_path: str) -> int:
        """Exportiert alle Patterns als JSON."""
        patterns = self.list_patterns(limit=10000)

        data = {
            "exported_at": datetime.now().isoformat(),
            "count": len(patterns),
            "patterns": [p.to_dict() for p in patterns]
        }

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        return len(patterns)

    def import_patterns(self, input_path: str) -> int:
        """Importiert Patterns aus JSON."""
        with open(input_path) as f:
            data = json.load(f)

        imported = 0
        for pattern_data in data.get("patterns", []):
            pattern = ErrorPattern.from_dict(pattern_data)
            if self.save_pattern(pattern):
                imported += 1

        logger.info(f"Imported {imported} patterns")
        return imported


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_pattern_learner: Optional[PatternLearner] = None


def get_pattern_learner() -> PatternLearner:
    """Gibt die Singleton-Instanz des PatternLearners zurueck."""
    global _pattern_learner
    if _pattern_learner is None:
        _pattern_learner = PatternLearner()
    return _pattern_learner
