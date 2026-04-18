"""
SentMessageCache — Echo-Schutz-Fallback (Sprint 1, A4).

Haelt die zuletzt vom Bot gesendeten ``message_id``s vor, damit
``_dispatch_incoming`` auch dann zuverlaessig self-Echos filtert,
wenn der ``person_id``-Vergleich einmal versagt (z.B. bei Forward,
Copy-Message oder wenn ein externer Consumer die Bot-Message neu
postet).

Implementation: LRU-Cache in-Memory + Pass-Through in SQLite
(so ueberlebt der Cache einen Restart innerhalb der TTL).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.webex.state.db import WebexDb

logger = logging.getLogger(__name__)


class SentMessageCache:
    """Kombinierter LRU+SQLite Cache fuer bot-gesendete Message-IDs."""

    DEFAULT_MAX_SIZE = 1000
    DEFAULT_RETENTION_HOURS = 24

    def __init__(
        self,
        *,
        max_size: int = DEFAULT_MAX_SIZE,
        retention_hours: int = DEFAULT_RETENTION_HOURS,
        db: Optional[WebexDb] = None,
    ) -> None:
        self._mem: "OrderedDict[str, None]" = OrderedDict()
        self._max = max(10, int(max_size))
        self._retention_hours = max(1, int(retention_hours))
        self._db = db

    async def add(self, message_id: str, room_id: str = "") -> None:
        """Registriert eine Bot-gesendete Message-ID."""
        if not message_id:
            return
        self._mem_add(message_id)
        if self._db:
            await asyncio.to_thread(self._db_add_sync, message_id, room_id)

    async def contains(self, message_id: str) -> bool:
        """Prueft ob die Message vom Bot stammt (In-Memory → DB-Fallback)."""
        if not message_id:
            return False
        if message_id in self._mem:
            self._mem.move_to_end(message_id)
            return True
        if self._db:
            hit = await asyncio.to_thread(self._db_has_sync, message_id)
            if hit:
                self._mem_add(message_id)
                return True
        return False

    async def purge_expired(self) -> int:
        """Loescht DB-Eintraege aelter als retention_hours. Gibt Anzahl zurueck."""
        if not self._db:
            return 0
        return await asyncio.to_thread(self._db_purge_sync)

    # ── In-Memory ─────────────────────────────────────────────────────────

    def _mem_add(self, message_id: str) -> None:
        if message_id in self._mem:
            self._mem.move_to_end(message_id)
            return
        self._mem[message_id] = None
        if len(self._mem) > self._max:
            self._mem.popitem(last=False)

    # ── DB-Sync-Implementierungen ─────────────────────────────────────────

    def _db_add_sync(self, message_id: str, room_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()  # type: ignore[union-attr]
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO sent_messages(message_id, room_id, created_at)
                VALUES (?, ?, ?)
                """,
                (message_id, room_id, now),
            )
        finally:
            if not self._db._is_memory:  # type: ignore[union-attr]
                conn.close()

    def _db_has_sync(self, message_id: str) -> bool:
        conn = self._db.connect()  # type: ignore[union-attr]
        try:
            row = conn.execute(
                "SELECT 1 FROM sent_messages WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
            return row is not None
        finally:
            if not self._db._is_memory:  # type: ignore[union-attr]
                conn.close()

    def _db_purge_sync(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
        ).isoformat()
        conn = self._db.connect()  # type: ignore[union-attr]
        try:
            cur = conn.execute(
                "DELETE FROM sent_messages WHERE created_at < ?",
                (cutoff,),
            )
            return cur.rowcount or 0
        finally:
            if not self._db._is_memory:  # type: ignore[union-attr]
                conn.close()
