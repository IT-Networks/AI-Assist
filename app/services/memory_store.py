"""
Memory Store - Persistenter Speicher für Session-übergreifende Fakten.

Features:
- SQLite-basiert mit FTS5 für schnelle Suche
- Automatische Relevanz-Sortierung
- Access-Tracking für häufig genutzte Fakten
"""

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.utils.token_counter import estimate_tokens


@dataclass
class MemoryEntry:
    """Ein Eintrag im Memory Store."""
    id: str
    session_id: str
    category: str           # "fact", "entity", "preference", "decision"
    key: str                # Eindeutiger Schlüssel/Titel
    value: str              # Der gespeicherte Inhalt
    importance: float       # 0.0 - 1.0
    created_at: str
    accessed_at: str
    access_count: int

    @property
    def tokens(self) -> int:
        return estimate_tokens(f"{self.key}: {self.value}")


class MemoryStore:
    """
    Persistenter Memory Store für Session-übergreifende Fakten.

    Verwendet SQLite mit FTS5 für schnelle Volltextsuche.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(settings.index.directory) / "memory.db")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        """Erstellt Tabellen wenn nicht vorhanden."""
        with self._connect() as con:
            con.executescript("""
                -- Haupttabelle für Memories
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    importance REAL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    UNIQUE(session_id, category, key)
                );

                -- FTS5 für Volltextsuche
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    id UNINDEXED,
                    key,
                    value,
                    tokenize='unicode61 remove_diacritics 0'
                );

                -- Indizes
                CREATE INDEX IF NOT EXISTS idx_memory_session
                    ON memories(session_id);
                CREATE INDEX IF NOT EXISTS idx_memory_category
                    ON memories(category);
                CREATE INDEX IF NOT EXISTS idx_memory_importance
                    ON memories(importance DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_access
                    ON memories(access_count DESC);
            """)

    # ── CRUD Operations ────────────────────────────────────────────────────

    async def remember(
        self,
        session_id: str,
        category: str,
        key: str,
        value: str,
        importance: float = 0.5
    ) -> str:
        """
        Speichert einen Fakt im Memory Store.

        Bei gleichem (session_id, category, key) wird aktualisiert.

        Returns:
            ID des Eintrags
        """
        now = datetime.utcnow().isoformat()
        memory_id = str(uuid.uuid4())[:12]

        with self._connect() as con:
            # Upsert
            con.execute("""
                INSERT INTO memories (id, session_id, category, key, value,
                                      importance, created_at, accessed_at, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(session_id, category, key) DO UPDATE SET
                    value = excluded.value,
                    importance = excluded.importance,
                    accessed_at = excluded.accessed_at
            """, (memory_id, session_id, category, key, value, importance, now, now))

            # FTS aktualisieren
            # Erst löschen falls vorhanden
            con.execute("""
                DELETE FROM memory_fts WHERE id IN (
                    SELECT id FROM memories
                    WHERE session_id = ? AND category = ? AND key = ?
                )
            """, (session_id, category, key))

            # Dann neu einfügen
            con.execute("""
                INSERT INTO memory_fts (id, key, value)
                SELECT id, key, value FROM memories
                WHERE session_id = ? AND category = ? AND key = ?
            """, (session_id, category, key))

        return memory_id

    async def recall(
        self,
        session_id: str,
        query: str,
        limit: int = 10,
        category: Optional[str] = None
    ) -> List[MemoryEntry]:
        """
        Sucht relevante Memories basierend auf Query.

        Kombiniert FTS-Suche mit Importance und Access-Count.
        """
        with self._connect() as con:
            # FTS-Suche
            safe_query = query.replace('"', '""')

            if category:
                rows = con.execute("""
                    SELECT m.*, fts.rank
                    FROM memories m
                    JOIN memory_fts fts ON m.id = fts.id
                    WHERE m.session_id = ?
                      AND m.category = ?
                      AND memory_fts MATCH ?
                    ORDER BY fts.rank, m.importance DESC, m.access_count DESC
                    LIMIT ?
                """, (session_id, category, safe_query, limit)).fetchall()
            else:
                rows = con.execute("""
                    SELECT m.*, fts.rank
                    FROM memories m
                    JOIN memory_fts fts ON m.id = fts.id
                    WHERE m.session_id = ?
                      AND memory_fts MATCH ?
                    ORDER BY fts.rank, m.importance DESC, m.access_count DESC
                    LIMIT ?
                """, (session_id, safe_query, limit)).fetchall()

            return [self._row_to_entry(row) for row in rows]

    async def get_by_category(
        self,
        session_id: str,
        category: str,
        limit: int = 20
    ) -> List[MemoryEntry]:
        """Holt alle Memories einer Kategorie."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT * FROM memories
                WHERE session_id = ? AND category = ?
                ORDER BY importance DESC, access_count DESC
                LIMIT ?
            """, (session_id, category, limit)).fetchall()

            return [self._row_to_entry(row) for row in rows]

    async def get_all(
        self,
        session_id: str,
        limit: int = 50
    ) -> List[MemoryEntry]:
        """Holt alle Memories einer Session."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT * FROM memories
                WHERE session_id = ?
                ORDER BY importance DESC, access_count DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [self._row_to_entry(row) for row in rows]

    async def forget(self, memory_id: str) -> bool:
        """Löscht einen Memory-Eintrag."""
        with self._connect() as con:
            con.execute("DELETE FROM memory_fts WHERE id = ?", (memory_id,))
            result = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return result.rowcount > 0

    async def forget_session(self, session_id: str) -> int:
        """Löscht alle Memories einer Session."""
        with self._connect() as con:
            con.execute("""
                DELETE FROM memory_fts WHERE id IN (
                    SELECT id FROM memories WHERE session_id = ?
                )
            """, (session_id,))
            result = con.execute(
                "DELETE FROM memories WHERE session_id = ?",
                (session_id,)
            )
            return result.rowcount

    # ── Context Injection ──────────────────────────────────────────────────

    async def get_context_injection(
        self,
        session_id: str,
        current_message: str,
        max_tokens: int = 2000
    ) -> str:
        """
        Holt relevante Memories für den aktuellen Kontext.

        Strategie:
        1. Semantische Suche basierend auf aktueller Nachricht
        2. Wichtige Facts (high importance)
        3. Häufig genutzte Facts (high access_count)
        """
        # Relevante Memories suchen
        relevant = await self.recall(session_id, current_message, limit=15)

        # Falls keine Treffer, hole wichtigste allgemein
        if not relevant:
            relevant = await self.get_all(session_id, limit=10)

        if not relevant:
            return ""

        # Zusammenbauen mit Token-Limit
        parts = []
        tokens_used = 0

        for memory in relevant:
            entry_text = f"• [{memory.category}] {memory.key}: {memory.value}"
            entry_tokens = estimate_tokens(entry_text)

            if tokens_used + entry_tokens > max_tokens:
                break

            parts.append(entry_text)
            tokens_used += entry_tokens

            # Access-Count erhöhen
            await self._increment_access(memory.id)

        if not parts:
            return ""

        return "=== BEKANNTE FAKTEN (aus vorherigen Sessions) ===\n" + "\n".join(parts)

    async def _increment_access(self, memory_id: str) -> None:
        """Erhöht Access-Count eines Eintrags."""
        now = datetime.utcnow().isoformat()
        with self._connect() as con:
            con.execute("""
                UPDATE memories
                SET access_count = access_count + 1,
                    accessed_at = ?
                WHERE id = ?
            """, (now, memory_id))

    # ── Helpers ────────────────────────────────────────────────────────────

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        """Konvertiert DB-Row zu MemoryEntry."""
        return MemoryEntry(
            id=row["id"],
            session_id=row["session_id"],
            category=row["category"],
            key=row["key"],
            value=row["value"],
            importance=row["importance"],
            created_at=row["created_at"],
            accessed_at=row["accessed_at"],
            access_count=row["access_count"]
        )

    async def get_stats(self, session_id: str) -> dict:
        """Statistiken für eine Session."""
        with self._connect() as con:
            row = con.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(access_count) as total_accesses,
                    AVG(importance) as avg_importance
                FROM memories
                WHERE session_id = ?
            """, (session_id,)).fetchone()

            categories = con.execute("""
                SELECT category, COUNT(*) as count
                FROM memories
                WHERE session_id = ?
                GROUP BY category
            """, (session_id,)).fetchall()

            return {
                "total_memories": row["total"] or 0,
                "total_accesses": row["total_accesses"] or 0,
                "avg_importance": round(row["avg_importance"] or 0, 2),
                "by_category": {r["category"]: r["count"] for r in categories}
            }


# Singleton
_memory_store: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    """Gibt Singleton-Instanz zurück."""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store
