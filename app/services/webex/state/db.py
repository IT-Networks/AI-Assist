"""
Webex-Bot SQLite Persistenz — Connection + Schema-Migration.

Die Datenbank liegt per Default unter ``<repo-root>/app/state/webex_bot.db``.
Fuer Tests kann ``WebexDb(":memory:")`` genutzt werden.

Thread-/Async-Safety:
- `sqlite3` Verbindungen sind nicht thread-safe; daher pro Call eine
  neue Connection (billig dank WAL + shared cache) und der Aufruf
  wrapped in ``asyncio.to_thread`` durch die Stores.
- WAL-Mode + ``synchronous=NORMAL`` bieten Durability ohne fsync-Overhead.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


# Schema-Version fuer zukuenftige Migrationen. Erhoehen wenn breaking.
_SCHEMA_VERSION = 3


_MIGRATIONS = [
    # v1 — Sprint 1 Initial-Schema
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_usage (
        date_utc    TEXT PRIMARY KEY,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        updated_at  TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_messages (
        process_key TEXT PRIMARY KEY,
        room_id     TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_processed_created
        ON processed_messages(created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS sent_messages (
        message_id TEXT PRIMARY KEY,
        room_id    TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sent_created
        ON sent_messages(created_at);
    """,
    # v2 — Sprint 2: Approvals + Audit
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        request_id       TEXT PRIMARY KEY,
        session_id       TEXT NOT NULL,
        room_id          TEXT NOT NULL,
        parent_id        TEXT,
        tool_name        TEXT NOT NULL,
        tool_args_json   TEXT NOT NULL,
        confirmation_json TEXT NOT NULL,
        card_message_id  TEXT,
        status           TEXT NOT NULL,
        actor_email      TEXT,
        created_at       TEXT NOT NULL,
        resolved_at      TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_approval_session
        ON approval_requests(session_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_approval_status
        ON approval_requests(status);
    """,
    """
    CREATE TABLE IF NOT EXISTS webex_audit (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc        TEXT NOT NULL,
        event_type    TEXT NOT NULL,
        actor_email   TEXT,
        room_id       TEXT,
        session_id    TEXT,
        risk_level    TEXT,
        payload_json  TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audit_ts
        ON webex_audit(ts_utc);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audit_session
        ON webex_audit(session_id);
    """,
    # v3 — Sprint 3: Conversation-Bindings (Multi-Conversation)
    """
    CREATE TABLE IF NOT EXISTS conversation_bindings (
        conv_key     TEXT PRIMARY KEY,        -- "{room_id}:{thread_id}"
        room_id      TEXT NOT NULL,
        thread_id    TEXT NOT NULL DEFAULT '',
        session_id   TEXT NOT NULL,
        scope        TEXT NOT NULL,            -- direct|group|thread
        policy_json  TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bindings_room
        ON conversation_bindings(room_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bindings_session
        ON conversation_bindings(session_id);
    """,
]


def resolve_db_path() -> Path:
    """Gibt den Default-Pfad fuer die Webex-Bot-SQLite-Datei zurueck.

    Ermittelt das Repository-Root relativ zu diesem Modul. Erstellt das
    ``app/state/``-Verzeichnis bei Bedarf.
    """
    # app/services/webex/state/db.py → parents[4] = AI-Assist/
    repo_root = Path(__file__).resolve().parents[4]
    state_dir = repo_root / "app" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "webex_bot.db"


class WebexDb:
    """Leichter SQLite-Wrapper mit automatischer Migration.

    Die Klasse haelt KEINE persistente Connection — jeder Call oeffnet
    eine neue Connection (WAL macht das billig). Das ist fuer Cross-
    Thread-Nutzung erforderlich (asyncio.to_thread wechselt Worker).
    """

    def __init__(self, path: Union[str, Path]) -> None:
        """Initialisiert die DB und fuehrt pending Migrationen aus.

        Args:
            path: Dateipfad oder ``":memory:"`` fuer in-memory (Tests).
        """
        self._path = str(path)
        self._is_memory = self._path == ":memory:"
        # Fuer in-memory muss die Connection gehalten werden, sonst
        # verliert jeder neue Connect den Inhalt.
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._memory_conn = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                isolation_level=None,  # autocommit
            )

    @property
    def path(self) -> str:
        """DB-Pfad (fuer Logging/Debugging)."""
        return self._path

    def connect(self) -> sqlite3.Connection:
        """Oeffnet eine neue Connection mit WAL + sane Defaults.

        Rueckgabe ist immer eine Connection im autocommit-Modus
        (``isolation_level=None``). Transaktionen werden explizit
        via ``BEGIN``/``COMMIT`` gesteuert — das passt besser zu
        unserem Nutzungsmuster (viele kleine Writes).
        """
        if self._is_memory:
            # Bei ":memory:" immer dieselbe Connection zurueckgeben.
            assert self._memory_conn is not None
            return self._memory_conn

        conn = sqlite3.connect(
            self._path,
            timeout=5.0,                 # busy-timeout 5s
            isolation_level=None,        # autocommit
            check_same_thread=False,     # asyncio.to_thread wechselt Worker
        )
        # WAL + NORMAL sync — guter Trade-off (Durability ohne fsync-Kosten)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
        except sqlite3.Error as e:
            logger.warning("[webex-db] PRAGMA setup failed: %s", e)
        return conn

    def migrate(self) -> int:
        """Fuehrt pending Migrationen aus. Gibt aktuelle Version zurueck.

        Idempotent: Kann bei jedem Start aufgerufen werden.
        """
        conn = self.connect()
        try:
            # Schema-Version lesen (falls Tabelle schon existiert)
            current = 0
            try:
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                if row:
                    current = int(row[0])
            except sqlite3.OperationalError:
                current = 0  # Tabelle existiert noch nicht

            if current >= _SCHEMA_VERSION:
                return current

            # Alle DDL-Statements ausfuehren (idempotent dank IF NOT EXISTS)
            for ddl in _MIGRATIONS:
                conn.execute(ddl)

            # Version setzen (upsert)
            conn.execute("DELETE FROM schema_version;")
            conn.execute(
                "INSERT INTO schema_version(version) VALUES (?);",
                (_SCHEMA_VERSION,),
            )
            logger.info(
                "[webex-db] Migration abgeschlossen: v%d → v%d (path=%s)",
                current, _SCHEMA_VERSION, self._path,
            )
            return _SCHEMA_VERSION
        finally:
            if not self._is_memory:
                conn.close()

    def close(self) -> None:
        """Schliesst die persistente in-memory-Connection (nur relevant fuer Tests)."""
        if self._memory_conn is not None:
            try:
                self._memory_conn.close()
            except sqlite3.Error:
                pass
            self._memory_conn = None
