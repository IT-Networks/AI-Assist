"""
Memory Store - Persistenter Speicher für hierarchisches Wissen.

Features:
- SQLite-basiert mit FTS5 für schnelle Suche
- 3-Schichten-Modell: Global → Project → Session
- Automatische Relevanz-Sortierung
- Access-Tracking für häufig genutzte Fakten
- Auto-Learning Unterstützung

Inspiriert von Claude Code's Auto-Memory System.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.utils.token_counter import estimate_tokens


class MemoryScope(str, Enum):
    """Scope eines Memory-Eintrags."""
    GLOBAL = "global"      # User-weit, über alle Projekte
    PROJECT = "project"    # Projekt-spezifisch
    SESSION = "session"    # Nur aktuelle Session


class MemoryCategory(str, Enum):
    """Kategorien für Memories."""
    FACT = "fact"              # Allgemeine Fakten
    ENTITY = "entity"          # Entitäten (Klassen, Services, etc.)
    PREFERENCE = "preference"  # User-Präferenzen
    DECISION = "decision"      # Architektur-Entscheidungen
    PATTERN = "pattern"        # Wiederverwendbare Muster
    SOLUTION = "solution"      # Gelöste Probleme
    WARNING = "warning"        # Bekannte Fallen/Bugs


class MemorySource(str, Enum):
    """Quelle eines Memory-Eintrags."""
    USER = "user"              # Explizit vom User
    AI_LEARNED = "ai_learned"  # Vom AI gelernt
    TOOL_RESULT = "tool_result"  # Aus Tool-Ergebnis extrahiert


@dataclass
class MemoryEntry:
    """Ein Eintrag im Memory Store."""
    id: str
    scope: str                  # 'global' | 'project' | 'session'
    project_id: Optional[str]   # NULL für global
    session_id: Optional[str]   # NULL für global/project
    category: str               # MemoryCategory
    key: str                    # Eindeutiger Schlüssel/Titel
    value: str                  # Der gespeicherte Inhalt
    importance: float           # 0.0 - 1.0
    confidence: float           # Wie sicher ist die Info?
    source: str                 # MemorySource
    created_at: str
    accessed_at: str
    access_count: int
    related_files: List[str] = field(default_factory=list)
    related_entities: List[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return estimate_tokens(f"{self.key}: {self.value}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "project_id": self.project_id,
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "importance": self.importance,
            "source": self.source
        }


class MemoryStore:
    """
    Persistenter Memory Store mit 3-Schichten-Modell.

    Scopes:
    - GLOBAL: User-weite Erkenntnisse (project_id = NULL, session_id = NULL)
    - PROJECT: Projekt-spezifisch (project_id = X, session_id = NULL)
    - SESSION: Nur aktuelle Session (project_id = X, session_id = Y)

    Verwendet SQLite mit FTS5 für schnelle Volltextsuche.
    """

    # Schema Version für Migrationen
    SCHEMA_VERSION = 2

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(settings.index.directory) / "memory.db")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        # Performance: WAL mode für bessere Concurrent-Reads
        con.execute("PRAGMA journal_mode=WAL")
        # Timeout bei Lock-Konflikten (5 Sekunden statt 0)
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_db(self) -> None:
        """Erstellt/migriert Tabellen."""
        with self._connect() as con:
            # Prüfe ob Migration nötig
            try:
                version = con.execute(
                    "SELECT value FROM memory_meta WHERE key = 'schema_version'"
                ).fetchone()
                current_version = int(version["value"]) if version else 1
            except sqlite3.OperationalError:
                current_version = 0

            if current_version < self.SCHEMA_VERSION:
                self._migrate_schema(con, current_version)

    def _migrate_schema(self, con: sqlite3.Connection, from_version: int) -> None:
        """Migriert Schema auf aktuelle Version."""
        if from_version == 0:
            # Initiale Erstellung
            con.executescript("""
                -- Meta-Tabelle für Schema-Version
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- Haupttabelle für Memories (erweitert)
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL DEFAULT 'session',
                    project_id TEXT,
                    session_id TEXT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    importance REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.8,
                    source TEXT DEFAULT 'ai_learned',
                    created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    related_files TEXT,
                    related_entities TEXT,
                    UNIQUE(scope, project_id, session_id, category, key)
                );

                -- FTS5 für Volltextsuche
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    id UNINDEXED,
                    key,
                    value,
                    tokenize='unicode61 remove_diacritics 0'
                );

                -- Indizes
                CREATE INDEX IF NOT EXISTS idx_memory_scope
                    ON memories(scope);
                CREATE INDEX IF NOT EXISTS idx_memory_project
                    ON memories(project_id);
                CREATE INDEX IF NOT EXISTS idx_memory_session
                    ON memories(session_id);
                CREATE INDEX IF NOT EXISTS idx_memory_category
                    ON memories(category);
                CREATE INDEX IF NOT EXISTS idx_memory_importance
                    ON memories(importance DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_access
                    ON memories(access_count DESC);
            """)

        elif from_version == 1:
            # Migration von Version 1 (alte session_id-basierte) zu Version 2 (multi-scope)
            # Prüfe ob alte Tabelle existiert
            old_table = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
            ).fetchone()

            if old_table:
                # Alte Daten migrieren
                con.executescript("""
                    -- Backup alte Tabelle
                    ALTER TABLE memories RENAME TO memories_old;

                    -- Neue Tabelle erstellen
                    CREATE TABLE memories (
                        id TEXT PRIMARY KEY,
                        scope TEXT NOT NULL DEFAULT 'session',
                        project_id TEXT,
                        session_id TEXT,
                        category TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        importance REAL DEFAULT 0.5,
                        confidence REAL DEFAULT 0.8,
                        source TEXT DEFAULT 'ai_learned',
                        created_at TEXT NOT NULL,
                        accessed_at TEXT NOT NULL,
                        access_count INTEGER DEFAULT 0,
                        related_files TEXT,
                        related_entities TEXT,
                        UNIQUE(scope, project_id, session_id, category, key)
                    );

                    -- Daten migrieren (alte session_id wird zu session scope)
                    INSERT INTO memories (
                        id, scope, project_id, session_id, category, key, value,
                        importance, confidence, source, created_at, accessed_at, access_count
                    )
                    SELECT
                        id, 'session', NULL, session_id, category, key, value,
                        importance, 0.8, 'ai_learned', created_at, accessed_at, access_count
                    FROM memories_old;

                    -- Neue Indizes
                    CREATE INDEX IF NOT EXISTS idx_memory_scope ON memories(scope);
                    CREATE INDEX IF NOT EXISTS idx_memory_project ON memories(project_id);
                    CREATE INDEX IF NOT EXISTS idx_memory_session ON memories(session_id);
                    CREATE INDEX IF NOT EXISTS idx_memory_category ON memories(category);
                    CREATE INDEX IF NOT EXISTS idx_memory_importance ON memories(importance DESC);
                    CREATE INDEX IF NOT EXISTS idx_memory_access ON memories(access_count DESC);

                    -- Alte Tabelle löschen
                    DROP TABLE memories_old;
                """)

        # Version aktualisieren
        con.execute(
            "INSERT OR REPLACE INTO memory_meta (key, value) VALUES ('schema_version', ?)",
            (str(self.SCHEMA_VERSION),)
        )

    # ══════════════════════════════════════════════════════════════════════════
    # CRUD Operations (Erweitert für Multi-Scope)
    # ══════════════════════════════════════════════════════════════════════════

    async def remember(
        self,
        key: str,
        value: str,
        category: str = "fact",
        scope: str = "session",
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        importance: float = 0.5,
        confidence: float = 0.8,
        source: str = "ai_learned",
        related_files: Optional[List[str]] = None,
        related_entities: Optional[List[str]] = None
    ) -> str:
        """
        Speichert einen Fakt im Memory Store.

        Args:
            key: Eindeutiger Schlüssel/Titel
            value: Der zu speichernde Inhalt
            category: Kategorie (fact, pattern, decision, etc.)
            scope: 'global', 'project', oder 'session'
            project_id: Projekt-ID (für project/session scope)
            session_id: Session-ID (für session scope)
            importance: Wichtigkeit 0.0-1.0
            confidence: Konfidenz 0.0-1.0
            source: 'user', 'ai_learned', oder 'tool_result'
            related_files: Zugehörige Dateien
            related_entities: Zugehörige Entitäten

        Returns:
            ID des Eintrags
        """
        now = datetime.utcnow().isoformat()
        memory_id = str(uuid.uuid4())[:12]

        # JSON-Serialisierung für Listen
        files_json = json.dumps(related_files) if related_files else None
        entities_json = json.dumps(related_entities) if related_entities else None

        with self._connect() as con:
            # Upsert
            con.execute("""
                INSERT INTO memories (
                    id, scope, project_id, session_id, category, key, value,
                    importance, confidence, source, created_at, accessed_at,
                    access_count, related_files, related_entities
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(scope, project_id, session_id, category, key) DO UPDATE SET
                    value = excluded.value,
                    importance = excluded.importance,
                    confidence = excluded.confidence,
                    accessed_at = excluded.accessed_at,
                    related_files = excluded.related_files,
                    related_entities = excluded.related_entities
            """, (
                memory_id, scope, project_id, session_id, category, key, value,
                importance, confidence, source, now, now,
                files_json, entities_json
            ))

            # FTS aktualisieren
            con.execute("""
                DELETE FROM memory_fts WHERE id IN (
                    SELECT id FROM memories
                    WHERE scope = ? AND
                          (project_id = ? OR (project_id IS NULL AND ? IS NULL)) AND
                          (session_id = ? OR (session_id IS NULL AND ? IS NULL)) AND
                          category = ? AND key = ?
                )
            """, (scope, project_id, project_id, session_id, session_id, category, key))

            con.execute("""
                INSERT INTO memory_fts (id, key, value)
                SELECT id, key, value FROM memories
                WHERE scope = ? AND
                      (project_id = ? OR (project_id IS NULL AND ? IS NULL)) AND
                      (session_id = ? OR (session_id IS NULL AND ? IS NULL)) AND
                      category = ? AND key = ?
            """, (scope, project_id, project_id, session_id, session_id, category, key))

        return memory_id

    async def recall(
        self,
        query: str,
        scopes: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
        min_importance: float = 0.0
    ) -> List[MemoryEntry]:
        """
        Sucht relevante Memories basierend auf Query.

        Args:
            query: Suchbegriff
            scopes: Liste der zu durchsuchenden Scopes ['global', 'project', 'session']
            project_id: Projekt-Filter
            session_id: Session-Filter
            category: Kategorie-Filter
            limit: Max. Ergebnisse
            min_importance: Mindest-Wichtigkeit

        Returns:
            Liste von MemoryEntry
        """
        if scopes is None:
            scopes = ["global", "project", "session"]

        with self._connect() as con:
            # FTS5-sichere Query
            safe_query = '"' + query.replace('"', '""') + '"'

            # Dynamische WHERE-Klausel
            conditions = ["memory_fts MATCH ?", "m.importance >= ?"]
            params: List[Any] = [safe_query, min_importance]

            # Scope-Filter
            scope_conditions = []
            for scope in scopes:
                if scope == "global":
                    scope_conditions.append("(m.scope = 'global')")
                elif scope == "project" and project_id:
                    scope_conditions.append("(m.scope = 'project' AND m.project_id = ?)")
                    params.append(project_id)
                elif scope == "session" and session_id:
                    scope_conditions.append("(m.scope = 'session' AND m.session_id = ?)")
                    params.append(session_id)

            if scope_conditions:
                conditions.append(f"({' OR '.join(scope_conditions)})")

            if category:
                conditions.append("m.category = ?")
                params.append(category)

            params.append(limit)

            sql = f"""
                SELECT m.*, fts.rank
                FROM memories m
                JOIN memory_fts fts ON m.id = fts.id
                WHERE {' AND '.join(conditions)}
                ORDER BY fts.rank, m.importance DESC, m.access_count DESC
                LIMIT ?
            """

            rows = con.execute(sql, params).fetchall()
            return [self._row_to_entry(row) for row in rows]

    async def get_by_scope(
        self,
        scope: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50
    ) -> List[MemoryEntry]:
        """Holt alle Memories eines Scopes."""
        with self._connect() as con:
            conditions = ["scope = ?"]
            params: List[Any] = [scope]

            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)
            if session_id:
                conditions.append("session_id = ?")
                params.append(session_id)
            if category:
                conditions.append("category = ?")
                params.append(category)

            params.append(limit)

            rows = con.execute(f"""
                SELECT * FROM memories
                WHERE {' AND '.join(conditions)}
                ORDER BY importance DESC, access_count DESC
                LIMIT ?
            """, params).fetchall()

            return [self._row_to_entry(row) for row in rows]

    async def get_all(
        self,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        include_global: bool = True,
        limit: int = 50
    ) -> List[MemoryEntry]:
        """Holt alle relevanten Memories für einen Kontext."""
        with self._connect() as con:
            conditions = []
            params: List[Any] = []

            scope_parts = []
            if include_global:
                scope_parts.append("scope = 'global'")
            if project_id:
                scope_parts.append("(scope = 'project' AND project_id = ?)")
                params.append(project_id)
            if session_id:
                scope_parts.append("(scope = 'session' AND session_id = ?)")
                params.append(session_id)

            if scope_parts:
                conditions.append(f"({' OR '.join(scope_parts)})")

            params.append(limit)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            rows = con.execute(f"""
                SELECT * FROM memories
                {where_clause}
                ORDER BY importance DESC, access_count DESC
                LIMIT ?
            """, params).fetchall()

            return [self._row_to_entry(row) for row in rows]

    async def forget(self, memory_id: str) -> bool:
        """Löscht einen Memory-Eintrag."""
        with self._connect() as con:
            con.execute("DELETE FROM memory_fts WHERE id = ?", (memory_id,))
            result = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return result.rowcount > 0

    async def forget_scope(
        self,
        scope: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> int:
        """Löscht alle Memories eines Scopes."""
        with self._connect() as con:
            conditions = ["scope = ?"]
            params: List[Any] = [scope]

            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)
            if session_id:
                conditions.append("session_id = ?")
                params.append(session_id)

            where_clause = " AND ".join(conditions)

            # FTS löschen
            con.execute(f"""
                DELETE FROM memory_fts WHERE id IN (
                    SELECT id FROM memories WHERE {where_clause}
                )
            """, params)

            # Memories löschen
            result = con.execute(f"""
                DELETE FROM memories WHERE {where_clause}
            """, params)

            return result.rowcount

    # Backward compatibility: alte session_id-basierte Methode
    async def forget_session(self, session_id: str) -> int:
        """Löscht alle Memories einer Session (Backward Compatibility)."""
        return await self.forget_scope("session", session_id=session_id)

    # ══════════════════════════════════════════════════════════════════════════
    # Context Injection (Erweitert für Multi-Scope)
    # ══════════════════════════════════════════════════════════════════════════

    async def get_context_injection(
        self,
        current_message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        max_tokens: int = 2000
    ) -> str:
        """
        Holt relevante Memories für den aktuellen Kontext.

        Strategie:
        1. Semantische Suche basierend auf aktueller Nachricht
        2. Wichtige Facts (high importance)
        3. Häufig genutzte Facts (high access_count)

        Args:
            current_message: Aktuelle User-Nachricht für semantische Suche
            project_id: Projekt-ID für Scope-Filter
            session_id: Session-ID für Scope-Filter
            scopes: Welche Scopes durchsuchen (default: alle)
            max_tokens: Token-Budget für Injection
        """
        if scopes is None:
            scopes = ["global", "project", "session"]

        # Relevante Memories suchen
        relevant = await self.recall(
            query=current_message,
            scopes=scopes,
            project_id=project_id,
            session_id=session_id,
            limit=15
        )

        # Falls keine Treffer, hole wichtigste allgemein
        if not relevant:
            relevant = await self.get_all(
                project_id=project_id,
                session_id=session_id,
                include_global=("global" in scopes),
                limit=10
            )

        if not relevant:
            return ""

        # Zusammenbauen mit Token-Limit
        parts = []
        tokens_used = 0
        accessed_ids = []

        # Gruppiere nach Scope für bessere Übersicht
        by_scope: Dict[str, List[str]] = {"global": [], "project": [], "session": []}

        for memory in relevant:
            entry_text = f"• [{memory.category}] {memory.key}: {memory.value}"
            entry_tokens = estimate_tokens(entry_text)

            if tokens_used + entry_tokens > max_tokens:
                break

            by_scope.get(memory.scope, []).append(entry_text)
            tokens_used += entry_tokens
            accessed_ids.append(memory.id)

        # Formatieren
        for scope_name, scope_label in [
            ("global", "GLOBALES WISSEN"),
            ("project", "PROJEKT-WISSEN"),
            ("session", "SESSION-KONTEXT")
        ]:
            if by_scope[scope_name]:
                parts.append(f"### {scope_label}")
                parts.extend(by_scope[scope_name])
                parts.append("")

        # Access-Count aktualisieren
        if accessed_ids:
            await self._increment_access_batch(accessed_ids)

        if not parts:
            return ""

        return "=== BEKANNTE FAKTEN ===\n" + "\n".join(parts)

    async def _increment_access_batch(self, memory_ids: List[str]) -> None:
        """Batch-Update: Erhöht Access-Count für mehrere Einträge."""
        if not memory_ids:
            return
        now = datetime.utcnow().isoformat()
        placeholders = ",".join("?" * len(memory_ids))
        with self._connect() as con:
            con.execute(f"""
                UPDATE memories
                SET access_count = access_count + 1,
                    accessed_at = ?
                WHERE id IN ({placeholders})
            """, [now] + memory_ids)

    # ══════════════════════════════════════════════════════════════════════════
    # Auto-Learning Support
    # ══════════════════════════════════════════════════════════════════════════

    async def learn_pattern(
        self,
        key: str,
        value: str,
        project_id: str,
        related_files: Optional[List[str]] = None
    ) -> str:
        """Speichert ein erkanntes Pattern (projekt-weit)."""
        return await self.remember(
            key=key,
            value=value,
            category=MemoryCategory.PATTERN.value,
            scope=MemoryScope.PROJECT.value,
            project_id=project_id,
            importance=0.7,
            confidence=0.7,
            source=MemorySource.AI_LEARNED.value,
            related_files=related_files
        )

    async def learn_decision(
        self,
        key: str,
        value: str,
        project_id: str,
        importance: float = 0.9
    ) -> str:
        """Speichert eine Architektur-Entscheidung (projekt-weit)."""
        return await self.remember(
            key=key,
            value=value,
            category=MemoryCategory.DECISION.value,
            scope=MemoryScope.PROJECT.value,
            project_id=project_id,
            importance=importance,
            confidence=0.9,
            source=MemorySource.AI_LEARNED.value
        )

    async def learn_solution(
        self,
        key: str,
        value: str,
        project_id: str,
        related_files: Optional[List[str]] = None
    ) -> str:
        """Speichert eine Problemlösung (projekt-weit)."""
        return await self.remember(
            key=key,
            value=value,
            category=MemoryCategory.SOLUTION.value,
            scope=MemoryScope.PROJECT.value,
            project_id=project_id,
            importance=0.75,
            confidence=0.85,
            source=MemorySource.AI_LEARNED.value,
            related_files=related_files
        )

    async def save_user_preference(
        self,
        key: str,
        value: str,
        scope: str = "global",
        project_id: Optional[str] = None
    ) -> str:
        """Speichert eine explizite User-Präferenz."""
        return await self.remember(
            key=key,
            value=value,
            category=MemoryCategory.PREFERENCE.value,
            scope=scope,
            project_id=project_id,
            importance=0.9,
            confidence=1.0,
            source=MemorySource.USER.value
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        """Konvertiert DB-Row zu MemoryEntry."""
        related_files = []
        related_entities = []

        if row["related_files"]:
            try:
                related_files = json.loads(row["related_files"])
            except json.JSONDecodeError:
                pass

        if row["related_entities"]:
            try:
                related_entities = json.loads(row["related_entities"])
            except json.JSONDecodeError:
                pass

        return MemoryEntry(
            id=row["id"],
            scope=row["scope"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            category=row["category"],
            key=row["key"],
            value=row["value"],
            importance=row["importance"],
            confidence=row["confidence"],
            source=row["source"],
            created_at=row["created_at"],
            accessed_at=row["accessed_at"],
            access_count=row["access_count"],
            related_files=related_files,
            related_entities=related_entities
        )

    async def get_stats(
        self,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> dict:
        """Statistiken für einen Kontext."""
        with self._connect() as con:
            conditions = []
            params: List[Any] = []

            if project_id:
                conditions.append("(scope = 'project' AND project_id = ?)")
                params.append(project_id)
            if session_id:
                conditions.append("(scope = 'session' AND session_id = ?)")
                params.append(session_id)

            # Immer global inkludieren
            conditions.append("scope = 'global'")

            where_clause = f"WHERE {' OR '.join(conditions)}" if conditions else ""

            row = con.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(access_count) as total_accesses,
                    AVG(importance) as avg_importance
                FROM memories
                {where_clause}
            """, params).fetchone()

            # Erweiterte Params für Gruppierung
            all_params = params + params if params else []

            scopes = con.execute(f"""
                SELECT scope, COUNT(*) as count
                FROM memories
                {where_clause}
                GROUP BY scope
            """, params).fetchall()

            categories = con.execute(f"""
                SELECT category, COUNT(*) as count
                FROM memories
                {where_clause}
                GROUP BY category
            """, params).fetchall()

            return {
                "total_memories": row["total"] or 0,
                "total_accesses": row["total_accesses"] or 0,
                "avg_importance": round(row["avg_importance"] or 0, 2),
                "by_scope": {r["scope"]: r["count"] for r in scopes},
                "by_category": {r["category"]: r["count"] for r in categories}
            }


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_memory_store: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    """Gibt Singleton-Instanz zurück."""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store
