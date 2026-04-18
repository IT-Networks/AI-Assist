"""
DailyUsageStore — persistenter Token-Counter fuer Daily-Caps.

Ersetzt den fluechtigen ``_daily_usage: Dict[str, int]`` im Handler,
damit der Daily-Token-Cap einen Restart ueberlebt (Sprint 1, A3).

Key-Format: ``YYYY-MM-DD`` (UTC), analog zum bisherigen In-Memory-Code.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

from app.services.webex.state.db import WebexDb

logger = logging.getLogger(__name__)


class DailyUsageStore:
    """SQLite-Store fuer Daily-Token-Usage.

    Alle oeffentlichen Methoden sind Coroutines; die eigentlichen
    SQLite-Calls laufen im Thread-Executor (``asyncio.to_thread``),
    um den Event-Loop nicht zu blockieren.
    """

    def __init__(self, db: WebexDb) -> None:
        self._db = db

    @staticmethod
    def today_utc() -> str:
        """Helper: ``YYYY-MM-DD`` fuer jetzt (UTC)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def get_used(self, date_utc: str) -> int:
        """Gibt die bisher verbrauchten Tokens fuer das Datum zurueck."""
        return await asyncio.to_thread(self._get_used_sync, date_utc)

    async def add_tokens(self, date_utc: str, tokens: int) -> int:
        """Erhoeht den Counter um ``tokens`` und gibt den neuen Wert zurueck.

        Bei ``tokens <= 0`` wird nichts geaendert.
        """
        if tokens <= 0:
            return await self.get_used(date_utc)
        return await asyncio.to_thread(self._add_tokens_sync, date_utc, tokens)

    async def all(self) -> Dict[str, int]:
        """Gibt alle Tage mit Usage zurueck (fuer Status/Debug)."""
        return await asyncio.to_thread(self._all_sync)

    async def reset(self, date_utc: str = "") -> None:
        """Loescht Counter (komplett oder fuer einen Tag). Fuer Tests/Admin."""
        await asyncio.to_thread(self._reset_sync, date_utc)

    # ── Sync-Implementierungen ────────────────────────────────────────────

    def _get_used_sync(self, date_utc: str) -> int:
        conn = self._db.connect()
        try:
            row = conn.execute(
                "SELECT tokens_used FROM daily_usage WHERE date_utc = ?",
                (date_utc,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            if not self._db._is_memory:
                conn.close()

    def _add_tokens_sync(self, date_utc: str, tokens: int) -> int:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            conn.execute(
                """
                INSERT INTO daily_usage(date_utc, tokens_used, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(date_utc) DO UPDATE
                  SET tokens_used = tokens_used + excluded.tokens_used,
                      updated_at  = excluded.updated_at
                """,
                (date_utc, tokens, now),
            )
            row = conn.execute(
                "SELECT tokens_used FROM daily_usage WHERE date_utc = ?",
                (date_utc,),
            ).fetchone()
            return int(row[0]) if row else tokens
        finally:
            if not self._db._is_memory:
                conn.close()

    def _all_sync(self) -> Dict[str, int]:
        conn = self._db.connect()
        try:
            rows = conn.execute(
                "SELECT date_utc, tokens_used FROM daily_usage"
            ).fetchall()
            return {row[0]: int(row[1]) for row in rows}
        finally:
            if not self._db._is_memory:
                conn.close()

    def _reset_sync(self, date_utc: str) -> None:
        conn = self._db.connect()
        try:
            if date_utc:
                conn.execute("DELETE FROM daily_usage WHERE date_utc = ?", (date_utc,))
            else:
                conn.execute("DELETE FROM daily_usage")
        finally:
            if not self._db._is_memory:
                conn.close()
