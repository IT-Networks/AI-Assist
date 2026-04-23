"""
Unit-Tests fuer Webex-Bot Sprint 4 (Context-Management).

Gegenstand:
- Schema v5 (generation, last_activity_utc, reset_pending)
- ConversationBindingStore: bump/decrement/touch/clear_reset_pending
- ConversationRegistry: maybe_bump_idle, bump_manual, continue_previous
- WebexConversation.effective_session_id
- context_compactor.elide_tool_outputs
- build_collapsed_tool_summary (Phase 4)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from app.agent.context_compactor import (
    count_chars,
    elide_tool_outputs,
)
from app.services.webex.conversation import (
    ConversationBindingStore,
    ConversationKey,
    ConversationPolicy,
    ConversationRegistry,
    Scope,
    WebexConversation,
)
from app.services.webex.conversation.registry import (
    DEFAULT_IDLE_RESET_SECONDS,
)
from app.services.webex.delivery.status_editor import build_collapsed_tool_summary
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


@pytest.fixture
def binding_store(db: WebexDb) -> ConversationBindingStore:
    return ConversationBindingStore(db)


def _default_policy() -> ConversationPolicy:
    return ConversationPolicy(scope=Scope.GROUP)


def _dummy_resolver(key: ConversationKey, scope: Scope) -> ConversationPolicy:
    return ConversationPolicy(scope=scope)


# ═══════════════════════════════════════════════════════════════════════════
# Schema v5
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaV5:
    def test_conversation_bindings_has_v5_columns(self, db: WebexDb):
        conn = db.connect()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(conversation_bindings)"
        ).fetchall()}
        assert {"generation", "last_activity_utc", "reset_pending"}.issubset(cols)

    def test_schema_version_v5(self, db: WebexDb):
        """Schema muss mind. v5 sein."""
        conn = db.connect()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] >= 5


# ═══════════════════════════════════════════════════════════════════════════
# BindingStore Generation-Operationen
# ═══════════════════════════════════════════════════════════════════════════


class TestBindingGeneration:
    @pytest.mark.asyncio
    async def test_initial_generation_is_1(self, binding_store: ConversationBindingStore):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        binding = await binding_store.get("room1")
        assert binding is not None
        assert binding.generation == 1
        assert binding.reset_pending is False
        assert binding.effective_session_id == "webex:room1"  # kein Suffix bei gen=1

    @pytest.mark.asyncio
    async def test_bump_generation_increments(self, binding_store: ConversationBindingStore):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        new_gen = await binding_store.bump_generation("room1", mark_reset_pending=True)
        assert new_gen == 2
        binding = await binding_store.get("room1")
        assert binding.generation == 2
        assert binding.reset_pending is True
        assert binding.effective_session_id == "webex:room1:g2"

    @pytest.mark.asyncio
    async def test_bump_manual_does_not_mark_pending(
        self, binding_store: ConversationBindingStore,
    ):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        await binding_store.bump_generation("room1", mark_reset_pending=False)
        binding = await binding_store.get("room1")
        assert binding.reset_pending is False

    @pytest.mark.asyncio
    async def test_decrement_generation_at_gen_1_is_noop(
        self, binding_store: ConversationBindingStore,
    ):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        result = await binding_store.decrement_generation("room1")
        assert result is None  # bereits bei 1, kein Undo moeglich
        binding = await binding_store.get("room1")
        assert binding.generation == 1

    @pytest.mark.asyncio
    async def test_decrement_after_bump(self, binding_store: ConversationBindingStore):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        await binding_store.bump_generation("room1", mark_reset_pending=False)
        await binding_store.bump_generation("room1", mark_reset_pending=False)
        # Jetzt bei gen=3, /continue → gen=2
        result = await binding_store.decrement_generation("room1")
        assert result == 2

    @pytest.mark.asyncio
    async def test_touch_activity_updates_timestamp(
        self, binding_store: ConversationBindingStore,
    ):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        before = (await binding_store.get("room1")).last_activity_utc
        await asyncio.sleep(0.01)
        await binding_store.touch_activity("room1")
        after = (await binding_store.get("room1")).last_activity_utc
        assert after > before

    @pytest.mark.asyncio
    async def test_clear_reset_pending(self, binding_store: ConversationBindingStore):
        await binding_store.upsert(
            conv_key="room1", room_id="room1", thread_id="",
            session_id="webex:room1", scope=Scope.GROUP, policy=_default_policy(),
        )
        await binding_store.bump_generation("room1", mark_reset_pending=True)
        assert (await binding_store.get("room1")).reset_pending is True
        await binding_store.clear_reset_pending("room1")
        assert (await binding_store.get("room1")).reset_pending is False


# ═══════════════════════════════════════════════════════════════════════════
# Registry Idle-Detection
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistryIdle:
    @pytest.mark.asyncio
    async def test_maybe_bump_idle_skips_fresh_binding(
        self, binding_store: ConversationBindingStore,
    ):
        """Frisch angelegtes Binding soll NICHT sofort gebumpt werden."""
        registry = ConversationRegistry(
            binding_store=binding_store, policy_resolver=_dummy_resolver,
        )
        msg = {"room_id": "room1", "parent_id": "", "room_type": "group"}
        conv = await registry.resolve(msg)
        assert conv.generation == 1
        bumped = await registry.maybe_bump_idle(conv.conv_key)
        assert bumped is None

    @pytest.mark.asyncio
    async def test_maybe_bump_idle_triggers_after_threshold(
        self, binding_store: ConversationBindingStore,
    ):
        """Bei idle_reset_seconds=0.5 und sleep 1s → Bump."""
        registry = ConversationRegistry(
            binding_store=binding_store,
            policy_resolver=_dummy_resolver,
            idle_reset_seconds=1,  # 1 Sekunde Idle-Schwelle
        )
        msg = {"room_id": "room1", "parent_id": "", "room_type": "group"}
        conv = await registry.resolve(msg)
        await binding_store.touch_activity(conv.conv_key)
        await asyncio.sleep(1.2)  # > threshold
        new_gen = await registry.maybe_bump_idle(conv.conv_key)
        assert new_gen == 2
        # Conversation neu resolven muss die neue Generation + pending zeigen
        conv2 = await registry.resolve(msg)
        assert conv2.generation == 2
        assert conv2.reset_pending is True
        assert conv2.effective_session_id.endswith(":g2")

    @pytest.mark.asyncio
    async def test_bump_manual_no_reset_pending_flag(
        self, binding_store: ConversationBindingStore,
    ):
        registry = ConversationRegistry(
            binding_store=binding_store, policy_resolver=_dummy_resolver,
        )
        msg = {"room_id": "room1", "parent_id": "", "room_type": "group"}
        conv = await registry.resolve(msg)
        new_gen = await registry.bump_manual(conv.conv_key)
        assert new_gen == 2
        conv2 = await registry.resolve(msg)
        assert conv2.reset_pending is False  # Manual → keine Ansage

    @pytest.mark.asyncio
    async def test_continue_previous(
        self, binding_store: ConversationBindingStore,
    ):
        registry = ConversationRegistry(
            binding_store=binding_store, policy_resolver=_dummy_resolver,
        )
        msg = {"room_id": "room1", "parent_id": "", "room_type": "group"}
        conv = await registry.resolve(msg)
        await registry.bump_manual(conv.conv_key)
        prev = await registry.continue_previous(conv.conv_key)
        assert prev == 1
        # /continue am gen=1 ist no-op
        assert await registry.continue_previous(conv.conv_key) is None

    @pytest.mark.asyncio
    async def test_acknowledge_reset_clears_pending(
        self, binding_store: ConversationBindingStore,
    ):
        registry = ConversationRegistry(
            binding_store=binding_store,
            policy_resolver=_dummy_resolver,
            idle_reset_seconds=1,
        )
        msg = {"room_id": "room1", "parent_id": "", "room_type": "group"}
        conv = await registry.resolve(msg)
        await binding_store.touch_activity(conv.conv_key)
        await asyncio.sleep(1.2)
        await registry.maybe_bump_idle(conv.conv_key)
        conv2 = await registry.resolve(msg)
        assert conv2.reset_pending is True
        await registry.acknowledge_reset(conv2.conv_key)
        # Cache-Eintrag muss direkt aktualisiert sein
        assert conv2.reset_pending is False


# ═══════════════════════════════════════════════════════════════════════════
# Context-Compactor (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestContextCompactor:
    def test_empty_list_returns_empty(self):
        assert elide_tool_outputs([]) == []

    def test_short_tool_outputs_untouched(self):
        """Kurze Tool-Outputs (< head+tail+marker) werden nicht elidiert."""
        messages = [
            {"role": "tool", "content": "short"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        out = elide_tool_outputs(messages, keep_recent=1)
        assert out[0]["content"] == "short"

    def test_long_old_tool_output_elided(self):
        """Altes Tool-Output ausserhalb recent-window wird elidiert."""
        big = "A" * 3000
        messages = [
            {"role": "tool", "content": big},        # old → elide
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},       # innerhalb recent=3
            {"role": "assistant", "content": "a2"},
            {"role": "tool", "content": "B" * 3000},  # innerhalb recent → bleibt
        ]
        out = elide_tool_outputs(messages, keep_recent=3, head_chars=100, tail_chars=100)
        assert "elidiert" in out[0]["content"]
        assert out[0]["content"].startswith("A" * 100)
        assert out[0]["content"].endswith("A" * 100)
        # letzte Tool-Message unveraendert
        assert out[-1]["content"] == "B" * 3000

    def test_non_tool_messages_preserved(self):
        """System/user/assistant werden nicht angetastet, auch wenn sehr lang."""
        big = "X" * 5000
        messages = [
            {"role": "system", "content": big},
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": "fresh"},
        ]
        out = elide_tool_outputs(messages, keep_recent=1)
        for i in range(3):
            assert out[i]["content"] == big

    def test_idempotent(self):
        """Zweiter Durchlauf aendert nichts Relevantes (Original-Marker wird nicht doppelt elidiert)."""
        big = "Z" * 3000
        messages = [
            {"role": "tool", "content": big},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        first = elide_tool_outputs(messages, keep_recent=1)
        second = elide_tool_outputs(first, keep_recent=1)
        # Nach zweitem Durchlauf ist die elidierte Message unter
        # head+tail+marker → wird in Ruhe gelassen.
        assert second[0]["content"] == first[0]["content"]

    def test_original_messages_not_mutated(self):
        big = "Y" * 2000
        messages = [{"role": "tool", "content": big}, {"role": "user", "content": "q"}]
        elide_tool_outputs(messages, keep_recent=1, head_chars=50, tail_chars=50)
        assert messages[0]["content"] == big  # Original unveraendert

    def test_count_chars(self):
        assert count_chars([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]) == 10


# ═══════════════════════════════════════════════════════════════════════════
# Collapse-Finalizer (Phase 4)
# ═══════════════════════════════════════════════════════════════════════════


class TestCollapsedToolSummary:
    def test_empty_list_returns_empty_string(self):
        assert build_collapsed_tool_summary([]) == ""

    def test_single_tool(self):
        result = build_collapsed_tool_summary(["read_file"])
        assert "<details>" in result
        assert "<summary>" in result
        assert "read_file" in result
        assert "1 Tool(s)" in result

    def test_multiple_tools_preview_first_3(self):
        tools = ["a", "b", "c", "d", "e"]
        result = build_collapsed_tool_summary(tools)
        assert "a, b, c, +2" in result  # +2 = d, e
        # Volle Liste in body
        for t in tools:
            assert f"`{t}`" in result

    def test_deduplicated_preview_order_preserved(self):
        """Preview-Summary dedupliziert. Full list behaelt Dupes (Reihenfolge-Info)."""
        tools = ["read", "write", "read", "read"]
        result = build_collapsed_tool_summary(tools)
        # Preview zeigt nur 2 distincts → "read, write"
        assert "2 Tool(s)" not in result  # Total-Count nutzt Full-List
        assert "4 Tool(s)" in result       # len(tool_history) = 4
        assert "read, write" in result
