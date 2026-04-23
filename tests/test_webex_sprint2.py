"""
Unit-Tests fuer Webex-Bot Sprint 2.

Gegenstand:
- Schema v2 Migration (neue Tabellen)
- EditThrottle (zeit + delta basiertes Throttling)
- Adaptive-Card-Builder (approval/result/error)
- ApprovalBus (create/resolve/timeout/cancel)
- AuditLogger (log/query/purge)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from app.services.webex.audit import AuditLogger
from app.services.webex.delivery import EditThrottle
from app.services.webex.interactive import (
    ApprovalBus,
    ApprovalStatus,
    ApprovalTimeout,
    build_approval_card,
    build_error_card,
    build_result_card,
)
from app.services.webex.interactive.cards import ADAPTIVE_CARD_CONTENT_TYPE
from app.services.webex.state import WebexDb


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def db() -> WebexDb:
    d = WebexDb(":memory:")
    d.migrate()
    yield d
    d.close()


# ═══════════════════════════════════════════════════════════════════════════
# Schema v2
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaV2:
    def test_approval_requests_table(self, db: WebexDb):
        conn = db.connect()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(approval_requests)"
        ).fetchall()}
        assert {"request_id", "session_id", "room_id", "tool_name",
                "tool_args_json", "confirmation_json", "card_message_id",
                "status", "created_at"}.issubset(cols)

    def test_webex_audit_table(self, db: WebexDb):
        conn = db.connect()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(webex_audit)"
        ).fetchall()}
        assert {"ts_utc", "event_type", "payload_json"}.issubset(cols)

    def test_schema_version_v2(self, db: WebexDb):
        """Schema muss mind. v2 sein (spaeteres Bump zu v3 ist OK)."""
        conn = db.connect()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] >= 2


# ═══════════════════════════════════════════════════════════════════════════
# EditThrottle
# ═══════════════════════════════════════════════════════════════════════════


class TestEditThrottle:
    def test_first_flush_requires_delta(self):
        t = EditThrottle(min_interval_seconds=0.0, min_delta_chars=50)
        # Erst bei ausreichend Zeichen flushen
        assert not t.should_flush(10)
        assert not t.should_flush(49)
        assert t.should_flush(50)

    def test_interval_blocks_flush(self):
        t = EditThrottle(min_interval_seconds=10.0, min_delta_chars=1)
        # Erster Flush OK (last=0, ausreichend Delta)
        assert t.should_flush(100)
        # Direkt danach geblockt durch Zeit
        assert not t.should_flush(200)

    def test_second_flush_needs_both_conditions(self):
        # Groessere Zeit-Marge fuer stabile Tests unter Jitter
        t = EditThrottle(min_interval_seconds=0.05, min_delta_chars=20)
        assert t.should_flush(100)
        time.sleep(0.15)  # klar ueber 0.05
        assert not t.should_flush(110)  # nicht genug Delta
        assert t.should_flush(130)       # beides erfuellt

    def test_force_flush_updates_state(self):
        t = EditThrottle(min_interval_seconds=0.0, min_delta_chars=50)
        t.force_flush(100)
        # Nach force_flush muss Delta wieder erfuellt sein
        assert not t.should_flush(140)
        assert t.should_flush(151)

    def test_reset_clears_state(self):
        t = EditThrottle(min_interval_seconds=10.0, min_delta_chars=1)
        t.should_flush(100)
        assert not t.should_flush(200)  # blocked
        t.reset()
        assert t.should_flush(1)  # nach reset frei


# ═══════════════════════════════════════════════════════════════════════════
# Card Builder
# ═══════════════════════════════════════════════════════════════════════════


class TestCardBuilder:
    def test_approval_card_structure(self):
        card = build_approval_card(
            request_id="rid-123",
            tool_name="write_file",
            risk_level="medium",
            description="Writing /foo/bar.txt",
        )
        assert card["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
        content = card["content"]
        assert content["type"] == "AdaptiveCard"
        assert content["version"] == "1.3"
        # Buttons
        actions = content["actions"]
        assert len(actions) == 2
        approve = [a for a in actions if a["data"]["action"] == "approve"][0]
        reject = [a for a in actions if a["data"]["action"] == "reject"][0]
        assert approve["data"]["rid"] == "rid-123"
        assert reject["data"]["rid"] == "rid-123"

    def test_approval_card_high_risk_style(self):
        card = build_approval_card(
            request_id="rid-1", tool_name="delete_file", risk_level="high",
        )
        approve = card["content"]["actions"][0]
        # High-risk: Approve wird als destructive gestyled (Warnung)
        assert approve["style"] == "destructive"

    def test_approval_card_with_requester(self):
        card = build_approval_card(
            request_id="r", tool_name="t", requester="alice@x.com",
        )
        body = card["content"]["body"]
        facts = next(b for b in body if b.get("type") == "FactSet")
        fact_titles = [f["value"] for f in facts["facts"]]
        assert "alice@x.com" in fact_titles

    def test_approval_card_truncates_long_args(self):
        long = "x" * 2000
        card = build_approval_card(
            request_id="r", tool_name="t", args_summary=long,
        )
        body = card["content"]["body"]
        # Args-TextBlock darf max 600 Zeichen haben
        mono_blocks = [b for b in body if b.get("fontType") == "Monospace"]
        assert mono_blocks
        assert len(mono_blocks[0]["text"]) <= 600

    def test_result_card_success(self):
        card = build_result_card("Done", summary="All good", success=True)
        body = card["content"]["body"]
        title = body[0]
        assert "✅" in title["text"]
        assert title["color"] == "Good"

    def test_result_card_failure(self):
        card = build_result_card("Failed", summary="Boom", success=False)
        title = card["content"]["body"][0]
        assert "❌" in title["text"]
        assert title["color"] == "Attention"

    def test_error_card(self):
        card = build_error_card("Something broke", details="Stack trace here")
        body = card["content"]["body"]
        assert body[0]["text"].startswith("⚠️")


# ═══════════════════════════════════════════════════════════════════════════
# ApprovalBus
# ═══════════════════════════════════════════════════════════════════════════


class TestApprovalBus:
    @pytest.mark.asyncio
    async def test_create_pending_returns_rid(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s1", room_id="r1", tool_name="write_file",
            tool_args={"path": "/a"}, confirmation_data={"operation": "write"},
        )
        assert len(rid) == 32  # uuid4 hex

    @pytest.mark.asyncio
    async def test_get_returns_pending(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s1", room_id="r1", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        req = await bus.get(rid)
        assert req is not None
        assert req.status == ApprovalStatus.PENDING
        assert req.session_id == "s1"

    @pytest.mark.asyncio
    async def test_resolve_approve_wakes_waiter(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )

        async def resolver():
            await asyncio.sleep(0.01)
            await bus.resolve(rid, approved=True, actor_email="alice@x.com")

        asyncio.create_task(resolver())
        decision = await bus.wait_for_decision(rid, timeout_seconds=2.0)
        assert decision.approved is True
        assert decision.actor_email == "alice@x.com"
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_resolve_reject(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )

        async def resolver():
            await asyncio.sleep(0.01)
            await bus.resolve(rid, approved=False, actor_email="bob@x.com")

        asyncio.create_task(resolver())
        decision = await bus.wait_for_decision(rid, timeout_seconds=2.0)
        assert decision.approved is False
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_timeout_raises(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        with pytest.raises(ApprovalTimeout):
            await bus.wait_for_decision(rid, timeout_seconds=0.05)
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_double_resolve_is_noop(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        assert await bus.resolve(rid, approved=True) is True
        # Zweites resolve bei non-pending → False
        assert await bus.resolve(rid, approved=False) is False

    @pytest.mark.asyncio
    async def test_cancel(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        assert await bus.cancel(rid) is True
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_list_pending(self, db: WebexDb):
        bus = ApprovalBus(db)
        r1 = await bus.create_pending(
            session_id="A", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        r2 = await bus.create_pending(
            session_id="A", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        await bus.create_pending(
            session_id="B", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        pendings_a = await bus.list_pending("A")
        assert len(pendings_a) == 2
        assert {r.request_id for r in pendings_a} == {r1, r2}

    @pytest.mark.asyncio
    async def test_set_card_message_id(self, db: WebexDb):
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
        )
        await bus.set_card_message_id(rid, "msg-xyz")
        req = await bus.get(rid)
        assert req.card_message_id == "msg-xyz"

    # ── C1: Approval Authorization ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_requester_email_persisted(self, db: WebexDb):
        """create_pending persistiert requester_email und get liefert es zurück."""
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
            requester_email="alice@example.com",
        )
        req = await bus.get(rid)
        assert req is not None
        assert req.requester_email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_resolve_rejects_foreign_approver(self, db: WebexDb):
        """Fremder Klicker darf die Card nicht auflösen; Status bleibt pending."""
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
            requester_email="alice@example.com",
        )
        # Bob (nicht Alice) klickt
        ok = await bus.resolve(rid, approved=True, actor_email="bob@example.com")
        assert ok is False
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.PENDING
        assert req.actor_email == ""  # keine Zuordnung

    @pytest.mark.asyncio
    async def test_resolve_accepts_self_approval(self, db: WebexDb):
        """Der Original-Requester darf seine eigene Card approven."""
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
            requester_email="alice@example.com",
        )
        ok = await bus.resolve(rid, approved=True, actor_email="alice@example.com")
        assert ok is True
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.APPROVED
        assert req.actor_email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_resolve_case_insensitive_email_match(self, db: WebexDb):
        """Email-Vergleich ignoriert Case & Whitespace."""
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
            requester_email="Alice@Example.COM",
        )
        ok = await bus.resolve(rid, approved=True, actor_email="  alice@example.com  ")
        assert ok is True
        req = await bus.get(rid)
        assert req.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_resolve_legacy_request_without_requester_email_still_works(
        self, db: WebexDb,
    ):
        """Legacy-Requests (vor v4) haben leeren requester_email → Auth skippt.

        Das ist die Backward-Compat-Regel: in-flight Approvals zum Zeitpunkt
        der Migration bleiben auflösbar (Timeout wäre die Alternative).
        """
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
            # requester_email nicht gesetzt → default ""
        )
        ok = await bus.resolve(rid, approved=True, actor_email="anyone@example.com")
        assert ok is True

    @pytest.mark.asyncio
    async def test_unauthorized_resolve_does_not_wake_waiter(self, db: WebexDb):
        """Fremd-Klick darf den wartenden Task nicht vorzeitig aufwecken."""
        bus = ApprovalBus(db)
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={}, confirmation_data={},
            requester_email="alice@example.com",
        )

        async def foreign_clicker():
            await asyncio.sleep(0.01)
            ok = await bus.resolve(
                rid, approved=True, actor_email="bob@example.com",
            )
            assert ok is False

        asyncio.create_task(foreign_clicker())
        # Waiter muss in den Timeout laufen — Bob's Klick zählt nicht
        with pytest.raises(ApprovalTimeout):
            await bus.wait_for_decision(rid, timeout_seconds=0.15)

    @pytest.mark.asyncio
    async def test_confirmation_data_roundtrip(self, db: WebexDb):
        """tool_args + confirmation_data werden als JSON persistiert + korrekt deserialisiert."""
        bus = ApprovalBus(db)
        cd = {"operation": "write", "path": "/x", "nested": {"a": 1}}
        rid = await bus.create_pending(
            session_id="s", room_id="r", tool_name="t",
            tool_args={"arg1": "val"}, confirmation_data=cd,
        )
        req = await bus.get(rid)
        assert req.tool_args == {"arg1": "val"}
        assert req.confirmation_data == cd


# ═══════════════════════════════════════════════════════════════════════════
# AuditLogger
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_and_query(self, db: WebexDb):
        audit = AuditLogger(db)
        await audit.log(
            "msg_in", actor_email="alice@x.com", room_id="r1",
            session_id="s1", payload={"text": "hello"},
        )
        events = await audit.query(session_id="s1")
        assert len(events) == 1
        assert events[0].event_type == "msg_in"
        assert events[0].payload == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_disabled_noop(self, db: WebexDb):
        audit = AuditLogger(db, enabled=False)
        await audit.log("msg_in", payload={"x": 1})
        events = await audit.query()
        assert events == []

    @pytest.mark.asyncio
    async def test_filter_by_type(self, db: WebexDb):
        audit = AuditLogger(db)
        await audit.log("msg_in", session_id="s", payload={})
        await audit.log("tool_call", session_id="s", payload={})
        await audit.log("error", session_id="s", payload={})
        errs = await audit.query(event_type="error")
        assert len(errs) == 1
        assert errs[0].event_type == "error"

    @pytest.mark.asyncio
    async def test_query_limit(self, db: WebexDb):
        audit = AuditLogger(db)
        for i in range(20):
            await audit.log("msg_in", session_id="s", payload={"i": i})
        events = await audit.query(session_id="s", limit=5)
        assert len(events) == 5

    @pytest.mark.asyncio
    async def test_filter_since(self, db: WebexDb):
        audit = AuditLogger(db)
        await audit.log("old", payload={})
        # Ausreichender Puffer: > 50ms zwischen old und cutoff
        await asyncio.sleep(0.1)
        cutoff = datetime.now(timezone.utc)
        await asyncio.sleep(0.05)
        await audit.log("new", payload={})
        events = await audit.query(since=cutoff)
        assert len(events) == 1, f"expected only 'new', got: {[e.event_type for e in events]}"
        assert events[0].event_type == "new"

    @pytest.mark.asyncio
    async def test_purge_expired(self, db: WebexDb):
        audit = AuditLogger(db, retention_days=1)
        # Manuell alten Eintrag einfuegen
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        conn = db.connect()
        conn.execute(
            """INSERT INTO webex_audit(ts_utc, event_type, payload_json)
               VALUES (?, ?, ?)""",
            (old_ts, "old", "{}"),
        )
        # Neuen Eintrag via API
        await audit.log("new", payload={})
        purged = await audit.purge_expired()
        assert purged == 1
        remaining = await audit.query()
        assert len(remaining) == 1
        assert remaining[0].event_type == "new"


# ═══════════════════════════════════════════════════════════════════════════
# WebexClient — neue Methoden (compile-test nur)
# ═══════════════════════════════════════════════════════════════════════════


class TestWebexClientExtensions:
    def test_imports_ok(self):
        """Smoke-Test: Imports + Methoden-Existenz."""
        from app.services.webex_client import WebexClient
        c = WebexClient()
        assert hasattr(c, "edit_message")
        assert hasattr(c, "delete_message")
        assert hasattr(c, "get_attachment_action")

    def test_send_message_accepts_attachments(self):
        """Signatur-Smoke-Test fuer neue attachments-Parameter."""
        import inspect
        from app.services.webex_client import WebexClient
        sig = inspect.signature(WebexClient.send_message)
        assert "attachments" in sig.parameters
