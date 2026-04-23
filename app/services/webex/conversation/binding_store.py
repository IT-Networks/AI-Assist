"""
ConversationBindingStore — persistente Session-Bindings (Sprint 3, C2).

Pro Conversation-Key (room_id[:thread_id]) wird eine Session-ID und die
zugehoerige Policy persistiert, damit sie ueber Restarts hinweg stabil
bleiben. Das erlaubt die Agent-History-Fortsetzung pro Chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from app.services.webex.conversation.scope import ConversationPolicy, Scope
from app.services.webex.state.db import WebexDb

logger = logging.getLogger(__name__)


@dataclass
class ConversationBinding:
    """Persistenter Record in ``conversation_bindings``."""
    conv_key: str
    room_id: str
    thread_id: str
    session_id: str
    scope: Scope
    policy: ConversationPolicy
    created_at: str
    updated_at: str
    # v5 — Session-Generation (Context-Management)
    generation: int = 1
    last_activity_utc: str = ""
    reset_pending: bool = False

    @property
    def effective_session_id(self) -> str:
        """Orchestrator-Session-Key inkl. Generation-Suffix.

        Generation 1 (default) ist suffix-frei fuer Backward-Compat mit
        vor-v5 gespeicherten Chat-Store-Files. Ab Generation 2 wird
        ``:g{N}`` angehaengt.
        """
        if self.generation <= 1:
            return self.session_id
        return f"{self.session_id}:g{self.generation}"


class ConversationBindingStore:
    """SQLite-Store fuer Conversation-Bindings."""

    def __init__(self, db: WebexDb) -> None:
        self._db = db

    async def get(self, conv_key: str) -> Optional[ConversationBinding]:
        """Laedt ein Binding per conv_key. None wenn nicht vorhanden."""
        return await asyncio.to_thread(self._get_sync, conv_key)

    async def upsert(
        self,
        *,
        conv_key: str,
        room_id: str,
        thread_id: str,
        session_id: str,
        scope: Scope,
        policy: ConversationPolicy,
    ) -> None:
        """Erstellt oder aktualisiert ein Binding (idempotent)."""
        await asyncio.to_thread(
            self._upsert_sync, conv_key, room_id, thread_id,
            session_id, scope, policy,
        )

    async def list_all(self) -> List[ConversationBinding]:
        """Listet alle Bindings (fuer Warm-Load beim Start)."""
        return await asyncio.to_thread(self._list_all_sync)

    async def delete(self, conv_key: str) -> bool:
        """Loescht ein Binding."""
        return await asyncio.to_thread(self._delete_sync, conv_key)

    async def count(self) -> int:
        return await asyncio.to_thread(self._count_sync)

    # ── v5: Session-Generation ──────────────────────────────────────────────

    async def bump_generation(
        self,
        conv_key: str,
        *,
        mark_reset_pending: bool,
    ) -> int:
        """Inkrementiert ``generation`` um 1 und setzt ``last_activity_utc``.

        Args:
            mark_reset_pending: True = Bump kommt aus Idle-Detection
                (AgentRunner soll Inline-Footer zeigen).
                False = expliziter User-Reset (z.B. /new) — keine Ansage noetig.

        Returns:
            Die neue Generation-Nummer.
        """
        return await asyncio.to_thread(
            self._bump_generation_sync, conv_key, mark_reset_pending,
        )

    async def decrement_generation(self, conv_key: str) -> Optional[int]:
        """Decrementiert ``generation`` um 1, wenn > 1.

        Fuer ``/continue``. Gibt neue Generation zurueck, oder None wenn
        bereits bei 1 (dann ist /continue no-op).
        """
        return await asyncio.to_thread(self._decrement_generation_sync, conv_key)

    async def touch_activity(self, conv_key: str) -> None:
        """Setzt ``last_activity_utc`` auf jetzt (ohne Generation zu aendern)."""
        await asyncio.to_thread(self._touch_activity_sync, conv_key)

    async def clear_reset_pending(self, conv_key: str) -> None:
        """Setzt ``reset_pending`` auf False (nach Inline-Footer-Anzeige)."""
        await asyncio.to_thread(self._clear_reset_pending_sync, conv_key)

    # ── Sync-Implementierungen ────────────────────────────────────────────

    def _get_sync(self, conv_key: str) -> Optional[ConversationBinding]:
        conn = self._db.connect()
        try:
            row = conn.execute(
                """
                SELECT conv_key, room_id, thread_id, session_id, scope,
                       policy_json, created_at, updated_at,
                       generation, last_activity_utc, reset_pending
                  FROM conversation_bindings
                 WHERE conv_key = ?
                """,
                (conv_key,),
            ).fetchone()
            return _row_to_binding(row) if row else None
        finally:
            if not self._db._is_memory:
                conn.close()

    def _bump_generation_sync(self, conv_key: str, mark_reset_pending: bool) -> int:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            cur = conn.execute(
                """
                UPDATE conversation_bindings
                   SET generation = generation + 1,
                       last_activity_utc = ?,
                       reset_pending = ?,
                       updated_at = ?
                 WHERE conv_key = ?
                """,
                (now, 1 if mark_reset_pending else 0, now, conv_key),
            )
            if (cur.rowcount or 0) == 0:
                return 0
            row = conn.execute(
                "SELECT generation FROM conversation_bindings WHERE conv_key = ?",
                (conv_key,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            if not self._db._is_memory:
                conn.close()

    def _decrement_generation_sync(self, conv_key: str) -> Optional[int]:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            # Nur decrementieren wenn generation > 1 (CAS)
            cur = conn.execute(
                """
                UPDATE conversation_bindings
                   SET generation = generation - 1,
                       last_activity_utc = ?,
                       reset_pending = 0,
                       updated_at = ?
                 WHERE conv_key = ? AND generation > 1
                """,
                (now, now, conv_key),
            )
            if (cur.rowcount or 0) == 0:
                return None
            row = conn.execute(
                "SELECT generation FROM conversation_bindings WHERE conv_key = ?",
                (conv_key,),
            ).fetchone()
            return int(row[0]) if row else None
        finally:
            if not self._db._is_memory:
                conn.close()

    def _touch_activity_sync(self, conv_key: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            conn.execute(
                """
                UPDATE conversation_bindings
                   SET last_activity_utc = ?, updated_at = ?
                 WHERE conv_key = ?
                """,
                (now, now, conv_key),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _clear_reset_pending_sync(self, conv_key: str) -> None:
        conn = self._db.connect()
        try:
            conn.execute(
                "UPDATE conversation_bindings SET reset_pending = 0 WHERE conv_key = ?",
                (conv_key,),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _upsert_sync(
        self,
        conv_key: str,
        room_id: str,
        thread_id: str,
        session_id: str,
        scope: Scope,
        policy: ConversationPolicy,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        policy_json = json.dumps(policy.to_dict())
        conn = self._db.connect()
        try:
            # v5: last_activity_utc wird beim Insert auf now gesetzt, beim
            # Upsert bleibt der bestehende Wert erhalten (nur via touch_activity
            # oder bump_generation aktualisiert).
            conn.execute(
                """
                INSERT INTO conversation_bindings(
                    conv_key, room_id, thread_id, session_id, scope,
                    policy_json, created_at, updated_at,
                    generation, last_activity_utc, reset_pending
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0)
                ON CONFLICT(conv_key) DO UPDATE
                  SET session_id = excluded.session_id,
                      scope      = excluded.scope,
                      policy_json = excluded.policy_json,
                      updated_at = excluded.updated_at
                """,
                (
                    conv_key, room_id, thread_id, session_id, scope.value,
                    policy_json, now, now, now,
                ),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _list_all_sync(self) -> List[ConversationBinding]:
        conn = self._db.connect()
        try:
            rows = conn.execute(
                """
                SELECT conv_key, room_id, thread_id, session_id, scope,
                       policy_json, created_at, updated_at,
                       generation, last_activity_utc, reset_pending
                  FROM conversation_bindings
                 ORDER BY updated_at DESC
                """
            ).fetchall()
            return [_row_to_binding(r) for r in rows]
        finally:
            if not self._db._is_memory:
                conn.close()

    def _delete_sync(self, conv_key: str) -> bool:
        conn = self._db.connect()
        try:
            cur = conn.execute(
                "DELETE FROM conversation_bindings WHERE conv_key = ?",
                (conv_key,),
            )
            return (cur.rowcount or 0) > 0
        finally:
            if not self._db._is_memory:
                conn.close()

    def _count_sync(self) -> int:
        conn = self._db.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM conversation_bindings"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            if not self._db._is_memory:
                conn.close()


def _row_to_binding(row) -> ConversationBinding:
    # v5-Felder sind optional falls alte Row/Schema < 11 Spalten
    has_v5 = len(row) >= 11
    return ConversationBinding(
        conv_key=str(row[0]),
        room_id=str(row[1]),
        thread_id=str(row[2] or ""),
        session_id=str(row[3]),
        scope=Scope(row[4]),
        policy=ConversationPolicy.from_dict(json.loads(row[5] or "{}")),
        created_at=str(row[6]),
        updated_at=str(row[7]),
        generation=int(row[8]) if has_v5 else 1,
        last_activity_utc=str(row[9] or "") if has_v5 else "",
        reset_pending=bool(row[10]) if has_v5 else False,
    )
