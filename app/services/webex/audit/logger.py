"""
AuditLogger — strukturierter Audit-Trail in SQLite (Sprint 2, C5).

Zweck: Nachvollziehbarkeit aller sicherheitsrelevanten Webex-Bot-Events
(eingehende Msgs, Tool-Calls, Approvals, Fehler). Getrennte Tabelle
(``webex_audit``), damit Application-Logs (Datei) und Audit-Log
(DB) unabhaengig sind.

Event-Typen (nicht erschoepfend):
  msg_in         — eingehende User-Nachricht
  msg_out        — vom Bot gesendete Nachricht
  tool_call      — Tool-Call (name, args-summary)
  approval_new   — Approval-Request erstellt
  approval_done  — Approval entschieden (approved/rejected/timeout)
  error          — Fehler im Agent-Run / Dispatch
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.webex.state.db import WebexDb

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    """Ein Audit-Eintrag (nach Query rekonstruiert)."""
    id: int
    ts_utc: str
    event_type: str
    actor_email: str = ""
    room_id: str = ""
    session_id: str = ""
    risk_level: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


class AuditLogger:
    """SQLite-basierter Audit-Logger mit optionaler TTL-Bereinigung."""

    DEFAULT_RETENTION_DAYS = 90

    def __init__(
        self,
        db: WebexDb,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        enabled: bool = True,
    ) -> None:
        self._db = db
        self._retention_days = max(1, int(retention_days))
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def log(
        self,
        event_type: str,
        *,
        actor_email: str = "",
        room_id: str = "",
        session_id: str = "",
        risk_level: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Schreibt ein Audit-Event. No-op wenn disabled."""
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(
                self._log_sync,
                event_type, actor_email, room_id, session_id, risk_level,
                payload or {},
            )
        except Exception as e:
            logger.warning("[audit] log failed (%s): %s", event_type, e)

    async def query(
        self,
        *,
        session_id: str = "",
        event_type: str = "",
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        """Query fuer Debug/Inspection."""
        return await asyncio.to_thread(
            self._query_sync, session_id, event_type, since, max(1, min(limit, 1000)),
        )

    async def purge_expired(self) -> int:
        """Loescht Eintraege aelter als retention_days. Gibt Anzahl zurueck."""
        return await asyncio.to_thread(self._purge_sync)

    # ── Sync-Implementierungen ────────────────────────────────────────────

    def _log_sync(
        self,
        event_type: str,
        actor_email: str,
        room_id: str,
        session_id: str,
        risk_level: str,
        payload: Dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._db.connect()
        try:
            conn.execute(
                """
                INSERT INTO webex_audit(
                    ts_utc, event_type, actor_email, room_id, session_id,
                    risk_level, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, event_type,
                    actor_email or None,
                    room_id or None,
                    session_id or None,
                    risk_level or None,
                    json.dumps(payload, default=str),
                ),
            )
        finally:
            if not self._db._is_memory:
                conn.close()

    def _query_sync(
        self,
        session_id: str,
        event_type: str,
        since: Optional[datetime],
        limit: int,
    ) -> List[AuditEvent]:
        clauses: List[str] = []
        params: List[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if since:
            clauses.append("ts_utc >= ?")
            params.append(since.astimezone(timezone.utc).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        sql = f"""
            SELECT id, ts_utc, event_type, actor_email, room_id, session_id,
                   risk_level, payload_json
              FROM webex_audit
              {where}
             ORDER BY ts_utc DESC
             LIMIT ?
        """
        params.append(limit)
        conn = self._db.connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            if not self._db._is_memory:
                conn.close()

        events: List[AuditEvent] = []
        for r in rows:
            try:
                payload = json.loads(r[7] or "{}")
            except Exception:
                payload = {}
            events.append(AuditEvent(
                id=int(r[0]),
                ts_utc=str(r[1]),
                event_type=str(r[2]),
                actor_email=str(r[3] or ""),
                room_id=str(r[4] or ""),
                session_id=str(r[5] or ""),
                risk_level=str(r[6] or ""),
                payload=payload,
            ))
        return events

    def _purge_sync(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).isoformat()
        conn = self._db.connect()
        try:
            cur = conn.execute(
                "DELETE FROM webex_audit WHERE ts_utc < ?",
                (cutoff,),
            )
            return cur.rowcount or 0
        finally:
            if not self._db._is_memory:
                conn.close()
