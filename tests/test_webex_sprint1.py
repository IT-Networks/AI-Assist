"""
Unit-Tests fuer Webex-Bot Sprint 1.

Gegenstand:
- WebexDb + Migration
- DailyUsageStore (persistenter Token-Counter)
- ProcessedMessagesStore (Idempotenz)
- SentMessageCache (Echo-Guard)
- ErrorPolicyGate (Cooldown)
- StatusEditor (Edit-in-place via Mock-Client)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from app.services.webex.delivery import StatusEditor
from app.services.webex.safety import ErrorPolicyGate, ErrorScope
from app.services.webex.state import (
    DailyUsageStore,
    ProcessedMessagesStore,
    SentMessageCache,
    WebexDb,
)


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
# WebexDb
# ═══════════════════════════════════════════════════════════════════════════


class TestWebexDb:
    def test_migrate_creates_all_tables(self, db: WebexDb):
        conn = db.connect()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "schema_version" in tables
        assert "daily_usage" in tables
        assert "processed_messages" in tables
        assert "sent_messages" in tables

    def test_migrate_is_idempotent(self, db: WebexDb):
        v1 = db.migrate()
        v2 = db.migrate()
        # Current schema version — migration ist idempotent, Version bleibt stabil.
        assert v1 == v2
        assert v1 >= 1

    def test_schema_version_persisted(self, db: WebexDb):
        conn = db.connect()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None
        assert row[0] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# DailyUsageStore
# ═══════════════════════════════════════════════════════════════════════════


class TestDailyUsageStore:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self, db: WebexDb):
        store = DailyUsageStore(db)
        assert await store.get_used("2026-04-18") == 0

    @pytest.mark.asyncio
    async def test_add_tokens_increments(self, db: WebexDb):
        store = DailyUsageStore(db)
        assert await store.add_tokens("2026-04-18", 100) == 100
        assert await store.add_tokens("2026-04-18", 50) == 150
        assert await store.get_used("2026-04-18") == 150

    @pytest.mark.asyncio
    async def test_separate_days(self, db: WebexDb):
        store = DailyUsageStore(db)
        await store.add_tokens("2026-04-18", 100)
        await store.add_tokens("2026-04-19", 200)
        assert await store.get_used("2026-04-18") == 100
        assert await store.get_used("2026-04-19") == 200

    @pytest.mark.asyncio
    async def test_zero_or_negative_noop(self, db: WebexDb):
        store = DailyUsageStore(db)
        await store.add_tokens("2026-04-18", 100)
        await store.add_tokens("2026-04-18", 0)
        await store.add_tokens("2026-04-18", -50)
        assert await store.get_used("2026-04-18") == 100

    @pytest.mark.asyncio
    async def test_all_returns_map(self, db: WebexDb):
        store = DailyUsageStore(db)
        await store.add_tokens("2026-04-18", 100)
        await store.add_tokens("2026-04-19", 200)
        all_usage = await store.all()
        assert all_usage == {"2026-04-18": 100, "2026-04-19": 200}

    @pytest.mark.asyncio
    async def test_reset_single_day(self, db: WebexDb):
        store = DailyUsageStore(db)
        await store.add_tokens("2026-04-18", 100)
        await store.add_tokens("2026-04-19", 200)
        await store.reset("2026-04-18")
        assert await store.get_used("2026-04-18") == 0
        assert await store.get_used("2026-04-19") == 200

    @pytest.mark.asyncio
    async def test_reset_all(self, db: WebexDb):
        store = DailyUsageStore(db)
        await store.add_tokens("2026-04-18", 100)
        await store.reset()
        assert await store.all() == {}

    def test_today_utc_format(self):
        today = DailyUsageStore.today_utc()
        assert len(today) == 10  # YYYY-MM-DD
        assert today.count("-") == 2


# ═══════════════════════════════════════════════════════════════════════════
# ProcessedMessagesStore
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessedMessagesStore:
    @pytest.mark.asyncio
    async def test_empty_not_processed(self, db: WebexDb):
        store = ProcessedMessagesStore(db)
        assert not await store.is_processed("wx-bot:v1:abc")

    @pytest.mark.asyncio
    async def test_mark_and_check(self, db: WebexDb):
        store = ProcessedMessagesStore(db)
        await store.mark_processed("wx-bot:v1:abc", room_id="room1")
        assert await store.is_processed("wx-bot:v1:abc")

    @pytest.mark.asyncio
    async def test_mark_is_idempotent(self, db: WebexDb):
        store = ProcessedMessagesStore(db)
        await store.mark_processed("wx-bot:v1:abc", room_id="room1")
        # Zweites mark darf nicht crashen und nicht duplizieren
        await store.mark_processed("wx-bot:v1:abc", room_id="room1")
        assert await store.count() == 1

    @pytest.mark.asyncio
    async def test_count_increments(self, db: WebexDb):
        store = ProcessedMessagesStore(db)
        await store.mark_processed("wx-bot:v1:a", "r")
        await store.mark_processed("wx-bot:v1:b", "r")
        await store.mark_processed("wx-bot:v1:c", "r")
        assert await store.count() == 3

    @pytest.mark.asyncio
    async def test_purge_expired(self, db: WebexDb):
        """Manuell alte Eintraege einfuegen und prueft ob Purge sie entfernt."""
        store = ProcessedMessagesStore(db, retention_days=14)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()

        conn = db.connect()
        conn.execute(
            "INSERT INTO processed_messages(process_key, room_id, created_at) VALUES(?,?,?)",
            ("old-key", "r", old_ts),
        )
        conn.execute(
            "INSERT INTO processed_messages(process_key, room_id, created_at) VALUES(?,?,?)",
            ("new-key", "r", new_ts),
        )

        purged = await store.purge_expired()
        assert purged == 1
        assert not await store.is_processed("old-key")
        assert await store.is_processed("new-key")

    # ── C2: Atomic Claim ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_claim_first_caller_wins(self, db: WebexDb):
        """Erster claim() bekommt True, nachfolgende claim() des gleichen Keys False."""
        store = ProcessedMessagesStore(db)
        first = await store.claim("wx-bot:v1:msg1", room_id="room1")
        second = await store.claim("wx-bot:v1:msg1", room_id="room1")
        assert first is True
        assert second is False

    @pytest.mark.asyncio
    async def test_claim_is_atomic_under_concurrency(self, db: WebexDb):
        """Bei N parallelen claim()s auf denselben Key gewinnt genau einer.

        Das ist die Kern-Invariante gegen Webhook/Poller-Race:
        Auch wenn beide Tasks die is_processed-Prüfung unsichtbar
        bestanden haben, darf nur ein Claim True zurückgeben.
        """
        store = ProcessedMessagesStore(db)
        key = "wx-bot:v1:race-key"
        results = await asyncio.gather(
            *[store.claim(key, room_id="r") for _ in range(20)]
        )
        winners = [r for r in results if r]
        assert len(winners) == 1, f"Expected exactly 1 winner, got {len(winners)}"
        assert await store.count() == 1

    @pytest.mark.asyncio
    async def test_mark_processed_legacy_wrapper_still_works(self, db: WebexDb):
        """mark_processed() delegiert auf claim(), Return-Wert bleibt None."""
        store = ProcessedMessagesStore(db)
        ret = await store.mark_processed("wx-bot:v1:legacy", room_id="r")
        assert ret is None  # Fire-and-forget Kontrakt
        assert await store.is_processed("wx-bot:v1:legacy")

    @pytest.mark.asyncio
    async def test_claim_different_keys_all_win(self, db: WebexDb):
        """Unabhängige Keys kollidieren nicht — jeder Claim gewinnt."""
        store = ProcessedMessagesStore(db)
        results = await asyncio.gather(
            *[store.claim(f"wx-bot:v1:k{i}", "r") for i in range(10)]
        )
        assert all(results)
        assert await store.count() == 10


# ═══════════════════════════════════════════════════════════════════════════
# SentMessageCache
# ═══════════════════════════════════════════════════════════════════════════


class TestSentMessageCache:
    @pytest.mark.asyncio
    async def test_empty_not_contains(self, db: WebexDb):
        cache = SentMessageCache(db=db)
        assert not await cache.contains("msg-1")

    @pytest.mark.asyncio
    async def test_add_and_check(self, db: WebexDb):
        cache = SentMessageCache(db=db)
        await cache.add("msg-1", room_id="room1")
        assert await cache.contains("msg-1")

    @pytest.mark.asyncio
    async def test_db_fallback_after_mem_eviction(self, db: WebexDb):
        """Kleines Max-Size, damit der In-Memory-Cache evictet — DB faengt auf."""
        cache = SentMessageCache(max_size=10, db=db)  # min ist 10
        await cache.add("msg-old", room_id="r")
        # 11 weitere Eintraege → msg-old wird aus In-Memory evicted (LRU)
        for i in range(11):
            await cache.add(f"msg-{i}", room_id="r")
        # In-Memory: nicht mehr drin — aber DB hat es
        assert await cache.contains("msg-old")

    @pytest.mark.asyncio
    async def test_without_db_pure_memory(self):
        cache = SentMessageCache(max_size=100)  # no db
        await cache.add("msg-1")
        assert await cache.contains("msg-1")

    @pytest.mark.asyncio
    async def test_empty_id_ignored(self, db: WebexDb):
        cache = SentMessageCache(db=db)
        await cache.add("", room_id="r")
        assert not await cache.contains("")

    @pytest.mark.asyncio
    async def test_purge_expired_removes_old(self, db: WebexDb):
        cache = SentMessageCache(db=db, retention_hours=24)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        conn = db.connect()
        conn.execute(
            "INSERT INTO sent_messages(message_id, room_id, created_at) VALUES(?,?,?)",
            ("old", "r", old_ts),
        )
        purged = await cache.purge_expired()
        assert purged == 1


# ═══════════════════════════════════════════════════════════════════════════
# ErrorPolicyGate
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorPolicyGate:
    def _scope(self) -> ErrorScope:
        return ErrorScope(room_id="R", thread_id="", error_class="agent-error")

    def test_silent_never_posts(self):
        gate = ErrorPolicyGate(policy="silent")
        scope = self._scope()
        for _ in range(5):
            assert not gate.should_post(scope)
        assert gate.suppressed_count(scope) == 5

    def test_always_always_posts(self):
        gate = ErrorPolicyGate(policy="always")
        scope = self._scope()
        for _ in range(5):
            assert gate.should_post(scope)

    def test_once_posts_first_then_cooldown(self):
        gate = ErrorPolicyGate(policy="once", cooldown_seconds=60.0)
        scope = self._scope()
        assert gate.should_post(scope)  # first
        assert not gate.should_post(scope)  # within cooldown
        assert not gate.should_post(scope)
        assert gate.suppressed_count(scope) == 2

    def test_once_zero_cooldown_always_posts(self):
        gate = ErrorPolicyGate(policy="once", cooldown_seconds=0.0)
        scope = self._scope()
        # cooldown 0 → jeder Call ist "nach" dem Cooldown
        assert gate.should_post(scope)
        assert gate.should_post(scope)
        assert gate.should_post(scope)

    def test_different_scopes_independent(self):
        gate = ErrorPolicyGate(policy="once", cooldown_seconds=60.0)
        s1 = ErrorScope(room_id="R1", error_class="x")
        s2 = ErrorScope(room_id="R2", error_class="x")
        s3 = ErrorScope(room_id="R1", error_class="y")
        assert gate.should_post(s1)
        assert gate.should_post(s2)  # anderer Room → frischer Cooldown
        assert gate.should_post(s3)  # andere Error-Class → frischer Cooldown
        # Gleicher Scope wieder → unterdrueckt
        assert not gate.should_post(s1)

    def test_set_policy_runtime(self):
        gate = ErrorPolicyGate(policy="silent")
        scope = self._scope()
        assert not gate.should_post(scope)
        gate.set_policy("always")
        assert gate.should_post(scope)

    def test_reset_scope(self):
        gate = ErrorPolicyGate(policy="once", cooldown_seconds=60.0)
        scope = self._scope()
        gate.should_post(scope)  # blockiert danach
        assert not gate.should_post(scope)
        gate.reset(scope)
        assert gate.should_post(scope)  # nach reset wieder frei

    def test_reset_all(self):
        gate = ErrorPolicyGate(policy="once", cooldown_seconds=60.0)
        s1 = ErrorScope(room_id="R1")
        s2 = ErrorScope(room_id="R2")
        gate.should_post(s1)
        gate.should_post(s2)
        gate.reset()
        assert gate.should_post(s1)
        assert gate.should_post(s2)

    def test_scope_key_format(self):
        scope = ErrorScope(room_id="R1", thread_id="T1", error_class="cls")
        assert scope.key == "R1|T1|cls"


# ═══════════════════════════════════════════════════════════════════════════
# StatusEditor
# ═══════════════════════════════════════════════════════════════════════════


class _MockClient:
    """Minimaler Duck-typed Mock fuer StatusEditor-Tests."""

    def __init__(self, *, fail_edit: bool = False, fail_send: bool = False):
        self.sent: List[Dict[str, Any]] = []
        self.edits: List[Dict[str, Any]] = []
        self.deletes: List[str] = []
        self._next_id = 0
        self._fail_edit = fail_edit
        self._fail_send = fail_send

    def _new_id(self) -> str:
        self._next_id += 1
        return f"msg-{self._next_id}"

    async def send_message(self, *, room_id: str, markdown: str = "",
                           parent_id: str = "", **_: Any) -> Dict[str, Any]:
        if self._fail_send:
            raise RuntimeError("send failed (simulated)")
        record = {
            "id": self._new_id(),
            "room_id": room_id,
            "markdown": markdown,
            "parent_id": parent_id,
        }
        self.sent.append(record)
        return record

    async def edit_message(self, message_id: str, *, room_id: str,
                           markdown: str = "", **_: Any) -> Dict[str, Any]:
        if self._fail_edit:
            raise RuntimeError("edit failed (simulated)")
        record = {
            "id": message_id,
            "room_id": room_id,
            "markdown": markdown,
        }
        self.edits.append(record)
        return record

    async def delete_message(self, message_id: str) -> None:
        self.deletes.append(message_id)


class TestStatusEditor:
    @pytest.mark.asyncio
    async def test_start_sends_initial(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", parent_id="T1")
        mid = await ed.start("⏳ queued")
        assert mid == "msg-1"
        assert ed.message_id == "msg-1"
        assert len(c.sent) == 1
        assert c.sent[0]["markdown"] == "⏳ queued"
        assert c.sent[0]["parent_id"] == "T1"

    @pytest.mark.asyncio
    async def test_update_edits_same_message(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start("⏳")
        result = await ed.update("🔧 tool-x", phase="tool:x")
        assert result is True
        assert len(c.edits) == 1
        assert c.edits[0]["id"] == "msg-1"
        assert c.edits[0]["markdown"] == "🔧 tool-x"

    @pytest.mark.asyncio
    async def test_update_deduplicates_by_phase(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start()
        await ed.update("first", phase="p1")
        await ed.update("second", phase="p1")  # selbe Phase → no-op
        assert len(c.edits) == 1

    @pytest.mark.asyncio
    async def test_update_allows_change_after_new_phase(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start()
        await ed.update("A", phase="p1")
        await ed.update("B", phase="p2")
        assert len(c.edits) == 2

    @pytest.mark.asyncio
    async def test_update_deduplicates_identical_text(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start()
        await ed.update("same", phase="p1")
        await ed.update("same", phase="p2")  # andere Phase, aber gleicher Text
        assert len(c.edits) == 1

    @pytest.mark.asyncio
    async def test_finalize_edits_when_message_exists(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start()
        await ed.finalize("final answer")
        assert len(c.edits) == 1
        assert c.edits[0]["markdown"] == "final answer"

    @pytest.mark.asyncio
    async def test_finalize_posts_new_when_no_message(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        # Kein start() → kein _message_id
        await ed.finalize("direct answer")
        assert len(c.sent) == 1
        assert len(c.edits) == 0

    @pytest.mark.asyncio
    async def test_edit_fallback_to_send(self):
        """Wenn Edit 429/404 wirft, postet StatusEditor transparent neu."""
        c = _MockClient(fail_edit=True)
        ed = StatusEditor(c, room_id="R1")
        await ed.start("⏳")
        result = await ed.update("new-text")
        # Edit hat gefailt → neue send_message
        assert result is True
        assert len(c.sent) == 2  # initial + fallback
        assert ed.message_id == "msg-2"  # ID rotiert

    @pytest.mark.asyncio
    async def test_delete_removes_message(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start()
        deleted = await ed.delete()
        assert deleted is True
        assert c.deletes == ["msg-1"]
        assert ed.message_id is None

    @pytest.mark.asyncio
    async def test_delete_noop_when_no_message(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        assert await ed.delete() is False

    @pytest.mark.asyncio
    async def test_start_failure_returns_none(self):
        c = _MockClient(fail_send=True)
        ed = StatusEditor(c, room_id="R1")
        mid = await ed.start()
        assert mid is None
        assert ed.message_id is None

    @pytest.mark.asyncio
    async def test_long_text_truncated(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1")
        await ed.start()
        long_text = "x" * 10000
        await ed.finalize(long_text)
        # Check the edit payload was truncated
        assert len(c.edits[0]["markdown"]) <= 6500
