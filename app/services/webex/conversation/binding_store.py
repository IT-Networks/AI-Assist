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

    # ── Sync-Implementierungen ────────────────────────────────────────────

    def _get_sync(self, conv_key: str) -> Optional[ConversationBinding]:
        conn = self._db.connect()
        try:
            row = conn.execute(
                """
                SELECT conv_key, room_id, thread_id, session_id, scope,
                       policy_json, created_at, updated_at
                  FROM conversation_bindings
                 WHERE conv_key = ?
                """,
                (conv_key,),
            ).fetchone()
            return _row_to_binding(row) if row else None
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
            conn.execute(
                """
                INSERT INTO conversation_bindings(
                    conv_key, room_id, thread_id, session_id, scope,
                    policy_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conv_key) DO UPDATE
                  SET session_id = excluded.session_id,
                      scope      = excluded.scope,
                      policy_json = excluded.policy_json,
                      updated_at = excluded.updated_at
                """,
                (
                    conv_key, room_id, thread_id, session_id, scope.value,
                    policy_json, now, now,
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
                       policy_json, created_at, updated_at
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
    return ConversationBinding(
        conv_key=str(row[0]),
        room_id=str(row[1]),
        thread_id=str(row[2] or ""),
        session_id=str(row[3]),
        scope=Scope(row[4]),
        policy=ConversationPolicy.from_dict(json.loads(row[5] or "{}")),
        created_at=str(row[6]),
        updated_at=str(row[7]),
    )
