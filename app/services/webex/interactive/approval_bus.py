"""
ApprovalBus — koordiniert Tool-Approvals via Adaptive Cards (Sprint 2).

Flow:
  1. Agent emittiert ``confirm_required`` → Handler ruft ``create_pending()``
  2. ``ApprovalBus`` persistiert Request, postet Card, erzeugt ``asyncio.Event``
  3. Handler ``await``et die Entscheidung via ``wait_for_decision(rid, timeout)``
  4. User klickt Button → Webhook ``attachmentActions.created``
  5. ``action_handler.dispatch`` ruft ``ApprovalBus.resolve(rid, approved)``
  6. ``Event.set()`` weckt den wartenden Handler; er fuehrt die Operation aus

Zustandsdiagramm (Status):
  pending → approved | rejected | timeout | cancelled
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from app.services.webex.state.db import WebexDb

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ApprovalTimeout(Exception):
    """Wird ausgeloest wenn die Approval-Timeout-Zeit abgelaufen ist."""


@dataclass
class ApprovalDecision:
    """Antwort auf ``wait_for_decision``."""
    request_id: str
    approved: bool
    actor_email: str = ""


@dataclass
class ApprovalRequest:
    """Ein persistenter Approval-Request (SQLite-Record)."""
    request_id: str
    session_id: str
    room_id: str
    parent_id: str
    tool_name: str
    tool_args: Dict[str, Any]
    confirmation_data: Dict[str, Any]
    card_message_id: str
    status: ApprovalStatus
    actor_email: str
    created_at: str
    resolved_at: str = ""


class ApprovalBus:
    """Zentrale Koordination aller Tool-Approvals in der Webex-Integration.

    Stellt SQLite-Persistenz + asyncio.Event-basierte Waiter bereit.
    Thread/Task-safe innerhalb eines asyncio-Event-Loops.
    """

    def __init__(self, db: WebexDb, default_timeout_seconds: float = 300.0) -> None:
        self._db = db
        self._default_timeout = max(1.0, float(default_timeout_seconds))
        # Waiter: request_id → (Event, [optional] decision)
        self._waiters: Dict[str, asyncio.Event] = {}
        self._decisions: Dict[str, ApprovalDecision] = {}
        self._lock = asyncio.Lock()

    # ── Request-Lifecycle ─────────────────────────────────────────────────

    async def create_pending(
        self,
        *,
        session_id: str,
        room_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        confirmation_data: Dict[str, Any],
        parent_id: str = "",
        card_message_id: str = "",
    ) -> str:
        """Erstellt einen neuen pending Approval-Request.

        Returns:
            request_id (UUID4-Hex).
        """
        rid = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        # Event VOR persistieren anlegen — verhindert Race, wenn der
        # Webhook sofort antwortet.
        async with self._lock:
            self._waiters[rid] = asyncio.Event()

        await asyncio.to_thread(
            self._insert_sync,
            rid,
            session_id,
            room_id,
            parent_id,
            tool_name,
            tool_args,
            confirmation_data,
            card_message_id,
            now,
        )
        logger.info(
            "[approval-bus] pending erstellt: rid=%s tool=%s session=%s",
            rid, tool_name, session_id,
        )
        return rid

    async def set_card_message_id(self, request_id: str, message_id: str) -> None:
        """Aktualisiert die ``card_message_id`` nachdem die Card gepostet wurde."""
        await asyncio.to_thread(self._update_card_id_sync, request_id, message_id)

    async def wait_for_decision(
        self,
        request_id: str,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> ApprovalDecision:
        """Wartet auf die Benutzer-Entscheidung (blockierend fuer diesen Task).

        Raises:
            ApprovalTimeout: Wenn die Zeit abgelaeuft.
            KeyError: Wenn ``request_id`` nicht bekannt.
        """
        async with self._lock:
            event = self._waiters.get(request_id)
            if event is None:
                raise KeyError(f"Unknown request_id: {request_id}")

        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Status auf TIMEOUT setzen
            await self._set_status(request_id, ApprovalStatus.TIMEOUT)
            async with self._lock:
                self._waiters.pop(request_id, None)
                self._decisions.pop(request_id, None)
            raise ApprovalTimeout(f"Approval {request_id} timed out after {timeout}s")

        async with self._lock:
            decision = self._decisions.pop(request_id, None)
            self._waiters.pop(request_id, None)
        if decision is None:
            # Kann passieren wenn resolve() vor wait_for_decision() lief.
            # Fallback: aus DB laden.
            req = await self.get(request_id)
            if req is None:
                raise KeyError(f"Request {request_id} nicht gefunden nach Decision")
            decision = ApprovalDecision(
                request_id=request_id,
                approved=(req.status == ApprovalStatus.APPROVED),
                actor_email=req.actor_email,
            )
        return decision

    async def resolve(
        self,
        request_id: str,
        *,
        approved: bool,
        actor_email: str = "",
    ) -> bool:
        """Setzt die Entscheidung und weckt alle Waiter.

        Returns:
            True wenn erfolgreich aufgeloest, False wenn Request unbekannt
            oder nicht mehr pending.
        """
        # Atomar: DB-Status CAS pending → approved/rejected
        target = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        updated = await asyncio.to_thread(
            self._resolve_sync, request_id, target, actor_email,
        )
        if not updated:
            logger.info(
                "[approval-bus] resolve ignoriert (nicht pending oder unbekannt): rid=%s",
                request_id,
            )
            return False

        decision = ApprovalDecision(
            request_id=request_id, approved=approved, actor_email=actor_email,
        )
        async with self._lock:
            self._decisions[request_id] = decision
            event = self._waiters.get(request_id)
        if event is not None:
            event.set()
        logger.info(
            "[approval-bus] resolved rid=%s approved=%s actor=%s",
            request_id, approved, actor_email or "?",
        )
        return True

    async def cancel(self, request_id: str) -> bool:
        """Bricht einen pending Request ab (z.B. bei Session-Reset)."""
        updated = await asyncio.to_thread(
            self._resolve_sync, request_id, ApprovalStatus.CANCELLED, "",
        )
        async with self._lock:
            event = self._waiters.pop(request_id, None)
            self._decisions.pop(request_id, None)
        if event is not None:
            event.set()
        return updated

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        """Laedt einen Request aus der DB."""
        row = await asyncio.to_thread(self._get_sync, request_id)
        if row is None:
            return None
        return _row_to_request(row)

    async def list_pending(self, session_id: str = "") -> list[ApprovalRequest]:
        """Listet pending Requests (optional gefiltert nach session_id)."""
        rows = await asyncio.to_thread(self._list_pending_sync, session_id)
        return [_row_to_request(r) for r in rows]

    # ── Interne sync SQLite-Operationen ───────────────────────────────────

    def _insert_sync(
        self,
        rid: str,
        session_id: str,
        room_id: str,
        parent_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        confirmation_data: Dict[str, Any],
        card_message_id: str,
        created_at: str,
    ) -> None:
        conn = self._db.connect()
        try:
            conn.execute(
                """
                INSERT INTO approval_requests(
                    request_id, session_id, room_id, parent_id,
                    tool_name, tool_args_json, confirmation_json,
                    card_message_id, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid, session_id, room_id, parent_id,
                    tool_name, json.dumps(tool_args, default=str),
                    json.dumps(confirmation_data, default=str),
                    card_message_id or None, ApprovalStatus.PENDING.value, created_at,
                ),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _update_card_id_sync(self, rid: str, message_id: str) -> None:
        conn = self._db.connect()
        try:
            conn.execute(
                "UPDATE approval_requests SET card_message_id = ? WHERE request_id = ?",
                (message_id, rid),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    async def _set_status(self, rid: str, status: ApprovalStatus) -> None:
        await asyncio.to_thread(self._set_status_sync, rid, status)

    def _set_status_sync(self, rid: str, status: ApprovalStatus) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            conn.execute(
                """
                UPDATE approval_requests
                   SET status = ?, resolved_at = ?
                 WHERE request_id = ?
                """,
                (status.value, now, rid),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _resolve_sync(
        self, rid: str, status: ApprovalStatus, actor_email: str,
    ) -> bool:
        """Atomar: setzt status+actor, aber nur wenn aktuell pending."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            cur = conn.execute(
                """
                UPDATE approval_requests
                   SET status = ?, resolved_at = ?, actor_email = ?
                 WHERE request_id = ? AND status = ?
                """,
                (status.value, now, actor_email or None, rid, ApprovalStatus.PENDING.value),
            )
            return (cur.rowcount or 0) > 0
        finally:
            if not self._db._is_memory:
                conn.close()

    def _get_sync(self, rid: str):
        conn = self._db.connect()
        try:
            return conn.execute(
                """
                SELECT request_id, session_id, room_id, parent_id, tool_name,
                       tool_args_json, confirmation_json, card_message_id,
                       status, actor_email, created_at, resolved_at
                  FROM approval_requests
                 WHERE request_id = ?
                """,
                (rid,),
            ).fetchone()
        finally:
            if not self._db._is_memory:
                conn.close()

    def _list_pending_sync(self, session_id: str):
        conn = self._db.connect()
        try:
            if session_id:
                return conn.execute(
                    """
                    SELECT request_id, session_id, room_id, parent_id, tool_name,
                           tool_args_json, confirmation_json, card_message_id,
                           status, actor_email, created_at, resolved_at
                      FROM approval_requests
                     WHERE session_id = ? AND status = ?
                     ORDER BY created_at
                    """,
                    (session_id, ApprovalStatus.PENDING.value),
                ).fetchall()
            return conn.execute(
                """
                SELECT request_id, session_id, room_id, parent_id, tool_name,
                       tool_args_json, confirmation_json, card_message_id,
                       status, actor_email, created_at, resolved_at
                  FROM approval_requests
                 WHERE status = ?
                 ORDER BY created_at
                """,
                (ApprovalStatus.PENDING.value,),
            ).fetchall()
        finally:
            if not self._db._is_memory:
                conn.close()


def _row_to_request(row) -> ApprovalRequest:
    """Konvertiert eine SQLite-Zeile in einen ApprovalRequest."""
    return ApprovalRequest(
        request_id=row[0],
        session_id=row[1],
        room_id=row[2],
        parent_id=row[3] or "",
        tool_name=row[4],
        tool_args=json.loads(row[5] or "{}"),
        confirmation_data=json.loads(row[6] or "{}"),
        card_message_id=row[7] or "",
        status=ApprovalStatus(row[8]),
        actor_email=row[9] or "",
        created_at=row[10],
        resolved_at=row[11] or "",
    )
