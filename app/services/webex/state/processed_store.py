"""
ProcessedMessagesStore — persistente Idempotenz fuer eingehende Messages.

Ersetzt den ``TodoStore``-Missbrauch fuer Webex-Process-Keys (Sprint 1,
A3): dedizierte Tabelle mit TTL-Cleanup beim Start statt unbounded Liste.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.services.webex.state.db import WebexDb

logger = logging.getLogger(__name__)


class ProcessedMessagesStore:
    """SQLite-Store fuer bereits verarbeitete Webex-Message-IDs.

    Key-Format: ``wx-bot:<version>:<message_id>`` — passt zum bestehenden
    Legacy-Key-Schema fuer potenziellen Rollback.
    """

    DEFAULT_RETENTION_DAYS = 14

    def __init__(self, db: WebexDb, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self._db = db
        self._retention = max(1, int(retention_days))

    async def is_processed(self, process_key: str) -> bool:
        """Liefert True wenn der Key bereits verarbeitet wurde."""
        return await asyncio.to_thread(self._is_processed_sync, process_key)

    async def mark_processed(self, process_key: str, room_id: str = "") -> None:
        """Markiert einen Key als verarbeitet (idempotent)."""
        await asyncio.to_thread(self._mark_sync, process_key, room_id)

    async def purge_expired(self) -> int:
        """Loescht Eintraege aelter als ``retention_days``. Gibt Anzahl zurueck."""
        return await asyncio.to_thread(self._purge_sync)

    async def count(self) -> int:
        """Anzahl aktueller Eintraege (fuer Debug)."""
        return await asyncio.to_thread(self._count_sync)

    # ── Sync-Implementierungen ────────────────────────────────────────────

    def _is_processed_sync(self, process_key: str) -> bool:
        conn = self._db.connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM processed_messages WHERE process_key = ? LIMIT 1",
                (process_key,),
            ).fetchone()
            return row is not None
        finally:
            if not self._db._is_memory:
                conn.close()

    def _mark_sync(self, process_key: str, room_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            # INSERT OR IGNORE → idempotent, kein Update wenn vorhanden
            conn.execute(
                """
                INSERT OR IGNORE INTO processed_messages(process_key, room_id, created_at)
                VALUES (?, ?, ?)
                """,
                (process_key, room_id, now),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _purge_sync(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention)).isoformat()
        conn = self._db.connect()
        try:
            cur = conn.execute(
                "DELETE FROM processed_messages WHERE created_at < ?",
                (cutoff,),
            )
            return cur.rowcount or 0
        finally:
            if not self._db._is_memory:
                conn.close()

    def _count_sync(self) -> int:
        conn = self._db.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM processed_messages"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            if not self._db._is_memory:
                conn.close()
