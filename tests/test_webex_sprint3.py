"""
Unit-Tests fuer Webex-Bot Sprint 3.

Gegenstand:
- Schema v3 (conversation_bindings Tabelle)
- Scope / ConversationKey / ConversationPolicy (inkl. inheritance)
- ConversationBindingStore (CRUD)
- ConversationRegistry (resolve/warm_load/forget)
- LaneDeliverer (reasoning + answer Lanes)
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.webex.conversation import (
    ConversationBinding,
    ConversationBindingStore,
    ConversationKey,
    ConversationPolicy,
    ConversationRegistry,
    Scope,
    WebexConversation,
)
from app.services.webex.delivery import LaneDeliverer
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
# Schema v3
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaV3:
    def test_conversation_bindings_table_exists(self, db: WebexDb):
        conn = db.connect()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(conversation_bindings)"
        ).fetchall()}
        expected = {"conv_key", "room_id", "thread_id", "session_id",
                    "scope", "policy_json", "created_at", "updated_at"}
        assert expected.issubset(cols)

    def test_schema_version_v3(self, db: WebexDb):
        conn = db.connect()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 3


# ═══════════════════════════════════════════════════════════════════════════
# Scope + ConversationKey
# ═══════════════════════════════════════════════════════════════════════════


class TestScope:
    def test_from_room_type_direct(self):
        assert Scope.from_room_type("direct", False) == Scope.DIRECT
        assert Scope.from_room_type("direct", True) == Scope.DIRECT  # direct bleibt direct

    def test_from_room_type_thread(self):
        assert Scope.from_room_type("group", True) == Scope.THREAD

    def test_from_room_type_group(self):
        assert Scope.from_room_type("group", False) == Scope.GROUP
        assert Scope.from_room_type("", False) == Scope.GROUP


class TestConversationKey:
    def test_key_without_thread(self):
        k = ConversationKey(room_id="R1")
        assert k.key == "R1"

    def test_key_with_thread(self):
        k = ConversationKey(room_id="R1", thread_id="T1")
        assert k.key == "R1:T1"

    def test_from_message(self):
        msg = {"room_id": "R1", "parent_id": "T1"}
        k = ConversationKey.from_message(msg)
        assert k.room_id == "R1"
        assert k.thread_id == "T1"

    def test_from_message_no_thread(self):
        msg = {"room_id": "R1", "parent_id": None}
        k = ConversationKey.from_message(msg)
        assert k.thread_id == ""


# ═══════════════════════════════════════════════════════════════════════════
# ConversationPolicy
# ═══════════════════════════════════════════════════════════════════════════


class TestConversationPolicy:
    def test_to_from_dict_roundtrip(self):
        p = ConversationPolicy(
            scope=Scope.DIRECT,
            allow_from=["a@x.com"],
            require_mention=True,
            default_model="sonnet",
            max_history=15,
            daily_token_cap=500000,
            error_policy="silent",
        )
        p2 = ConversationPolicy.from_dict(p.to_dict())
        assert p2 == p

    def test_inherit_empty_child_takes_parent(self):
        parent = ConversationPolicy(
            allow_from=["admin@x.com"], default_model="sonnet",
            max_history=20, daily_token_cap=1000, error_policy="always",
        )
        child = ConversationPolicy()  # leer
        merged = child.inherit_from(parent)
        assert merged.allow_from == ["admin@x.com"]
        assert merged.default_model == "sonnet"
        assert merged.daily_token_cap == 1000
        assert merged.error_policy == "always"

    def test_inherit_child_overrides_set_fields(self):
        parent = ConversationPolicy(
            allow_from=["admin@x.com"], default_model="sonnet",
            daily_token_cap=1000,
        )
        child = ConversationPolicy(
            allow_from=["user@x.com"], default_model="haiku",
            daily_token_cap=500,
        )
        merged = child.inherit_from(parent)
        assert merged.allow_from == ["user@x.com"]
        assert merged.default_model == "haiku"
        assert merged.daily_token_cap == 500

    def test_inherit_empty_list_inherits(self):
        parent = ConversationPolicy(allow_from=["admin@x.com"])
        child = ConversationPolicy(allow_from=[])
        merged = child.inherit_from(parent)
        # Leere allow_from = vom Parent uebernehmen
        assert merged.allow_from == ["admin@x.com"]

    def test_is_authorized_empty_allows_all(self):
        p = ConversationPolicy(allow_from=[])
        assert p.is_authorized("anyone@x.com")
        assert p.is_authorized("")

    def test_is_authorized_case_insensitive(self):
        p = ConversationPolicy(allow_from=["Alice@X.com"])
        assert p.is_authorized("ALICE@x.COM")
        assert not p.is_authorized("bob@x.com")


# ═══════════════════════════════════════════════════════════════════════════
# ConversationBindingStore
# ═══════════════════════════════════════════════════════════════════════════


class TestConversationBindingStore:
    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, db: WebexDb):
        store = ConversationBindingStore(db)
        assert await store.get("R1") is None

    @pytest.mark.asyncio
    async def test_upsert_and_get(self, db: WebexDb):
        store = ConversationBindingStore(db)
        policy = ConversationPolicy(default_model="sonnet")
        await store.upsert(
            conv_key="R1", room_id="R1", thread_id="",
            session_id="webex:R1", scope=Scope.GROUP, policy=policy,
        )
        b = await store.get("R1")
        assert b is not None
        assert b.session_id == "webex:R1"
        assert b.scope == Scope.GROUP
        assert b.policy.default_model == "sonnet"

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent(self, db: WebexDb):
        store = ConversationBindingStore(db)
        for _ in range(3):
            await store.upsert(
                conv_key="R1", room_id="R1", thread_id="",
                session_id="webex:R1", scope=Scope.GROUP,
                policy=ConversationPolicy(),
            )
        assert await store.count() == 1

    @pytest.mark.asyncio
    async def test_upsert_updates_policy(self, db: WebexDb):
        store = ConversationBindingStore(db)
        await store.upsert(
            conv_key="R1", room_id="R1", thread_id="",
            session_id="s", scope=Scope.GROUP,
            policy=ConversationPolicy(default_model="haiku"),
        )
        await store.upsert(
            conv_key="R1", room_id="R1", thread_id="",
            session_id="s", scope=Scope.GROUP,
            policy=ConversationPolicy(default_model="sonnet"),
        )
        b = await store.get("R1")
        assert b.policy.default_model == "sonnet"

    @pytest.mark.asyncio
    async def test_list_all(self, db: WebexDb):
        store = ConversationBindingStore(db)
        await store.upsert(
            conv_key="R1", room_id="R1", thread_id="",
            session_id="s1", scope=Scope.GROUP, policy=ConversationPolicy(),
        )
        await store.upsert(
            conv_key="R2:T1", room_id="R2", thread_id="T1",
            session_id="s2", scope=Scope.THREAD, policy=ConversationPolicy(),
        )
        all_b = await store.list_all()
        assert len(all_b) == 2
        keys = {b.conv_key for b in all_b}
        assert keys == {"R1", "R2:T1"}

    @pytest.mark.asyncio
    async def test_delete(self, db: WebexDb):
        store = ConversationBindingStore(db)
        await store.upsert(
            conv_key="R1", room_id="R1", thread_id="",
            session_id="s", scope=Scope.GROUP, policy=ConversationPolicy(),
        )
        assert await store.delete("R1") is True
        assert await store.get("R1") is None
        assert await store.delete("R1") is False


# ═══════════════════════════════════════════════════════════════════════════
# ConversationRegistry
# ═══════════════════════════════════════════════════════════════════════════


def _default_resolver(account_policy: ConversationPolicy):
    def resolver(key: ConversationKey, scope: Scope) -> ConversationPolicy:
        p = ConversationPolicy(
            scope=scope,
            allow_from=list(account_policy.allow_from),
            default_model=account_policy.default_model,
        )
        return p
    return resolver


class TestConversationRegistry:
    @pytest.mark.asyncio
    async def test_resolve_new_creates_binding(self, db: WebexDb):
        store = ConversationBindingStore(db)
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy(default_model="sonnet")),
        )
        msg = {"room_id": "R1", "room_type": "group", "parent_id": ""}
        conv = await reg.resolve(msg)
        assert conv is not None
        assert conv.room_id == "R1"
        assert conv.scope == Scope.GROUP
        assert conv.session_id == "webex:R1"
        assert conv.policy.default_model == "sonnet"
        # Persistiert
        b = await store.get("R1")
        assert b is not None

    @pytest.mark.asyncio
    async def test_resolve_existing_uses_cache(self, db: WebexDb):
        store = ConversationBindingStore(db)
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy()),
        )
        msg = {"room_id": "R1", "room_type": "group", "parent_id": ""}
        conv1 = await reg.resolve(msg)
        conv2 = await reg.resolve(msg)
        # Identische Instanz aus Cache
        assert conv1 is conv2

    @pytest.mark.asyncio
    async def test_resolve_without_room_id_returns_none(self, db: WebexDb):
        store = ConversationBindingStore(db)
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy()),
        )
        msg = {"room_id": "", "room_type": "direct"}
        assert await reg.resolve(msg) is None

    @pytest.mark.asyncio
    async def test_resolve_direct_scope(self, db: WebexDb):
        store = ConversationBindingStore(db)
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy()),
        )
        msg = {"room_id": "R1", "room_type": "direct"}
        conv = await reg.resolve(msg)
        assert conv.scope == Scope.DIRECT

    @pytest.mark.asyncio
    async def test_resolve_thread_scope(self, db: WebexDb):
        store = ConversationBindingStore(db)
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy()),
        )
        msg = {"room_id": "R1", "room_type": "group", "parent_id": "T1"}
        conv = await reg.resolve(msg)
        assert conv.scope == Scope.THREAD
        assert conv.conv_key == "R1:T1"

    @pytest.mark.asyncio
    async def test_warm_load_from_db(self, db: WebexDb):
        store = ConversationBindingStore(db)
        await store.upsert(
            conv_key="R1", room_id="R1", thread_id="",
            session_id="sid", scope=Scope.GROUP, policy=ConversationPolicy(),
        )
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy()),
        )
        loaded = await reg.warm_load()
        assert loaded == 1
        assert reg.is_known_room("R1")

    @pytest.mark.asyncio
    async def test_forget_removes_binding(self, db: WebexDb):
        store = ConversationBindingStore(db)
        reg = ConversationRegistry(
            binding_store=store,
            policy_resolver=_default_resolver(ConversationPolicy()),
        )
        msg = {"room_id": "R1", "room_type": "group"}
        conv = await reg.resolve(msg)
        assert await reg.forget(conv.conv_key) is True
        # Neue Anfrage erzeugt neue Conversation (alt geloescht)
        conv2 = await reg.resolve(msg)
        assert conv2.conv_key == conv.conv_key  # gleicher Key
        # Aber new binding (DB)
        assert await store.count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# LaneDeliverer
# ═══════════════════════════════════════════════════════════════════════════


class _MockClient:
    def __init__(self):
        self.sent: List[Dict[str, Any]] = []
        self.edits: List[Dict[str, Any]] = []
        self.deletes: List[str] = []
        self._next = 0

    def _new_id(self) -> str:
        self._next += 1
        return f"msg-{self._next}"

    async def send_message(self, *, room_id: str, markdown: str = "",
                           parent_id: str = "", **_: Any) -> Dict[str, Any]:
        record = {"id": self._new_id(), "room_id": room_id,
                  "markdown": markdown, "parent_id": parent_id}
        self.sent.append(record)
        return record

    async def edit_message(self, message_id: str, *, room_id: str,
                           markdown: str = "", **_: Any) -> Dict[str, Any]:
        record = {"id": message_id, "room_id": room_id, "markdown": markdown}
        self.edits.append(record)
        return record

    async def delete_message(self, message_id: str) -> None:
        self.deletes.append(message_id)


class TestLaneDeliverer:
    @pytest.mark.asyncio
    async def test_start_posts_reasoning(self):
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1", parent_id="T1")
        mid = await d.start("⏳ queued")
        assert mid == "msg-1"
        assert len(c.sent) == 1
        assert c.sent[0]["markdown"] == "⏳ queued"

    @pytest.mark.asyncio
    async def test_update_edits_reasoning(self):
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1")
        await d.start()
        await d.update("🔧 tool", phase="p1")
        assert len(c.edits) == 1
        assert c.edits[0]["markdown"] == "🔧 tool"

    @pytest.mark.asyncio
    async def test_finalize_posts_answer_and_deletes_reasoning(self):
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1")
        await d.start()
        await d.update("🧠 analyze", phase="p1")
        await d.finalize("Final answer here")
        # send_message calls: 1 reasoning start + 1 answer
        assert len(c.sent) == 2
        assert c.sent[1]["markdown"] == "Final answer here"
        # reasoning deleted
        assert c.deletes == ["msg-1"]
        assert d.answer_message_id == "msg-2"

    @pytest.mark.asyncio
    async def test_finalize_keeps_reasoning_if_configured(self):
        c = _MockClient()
        d = LaneDeliverer(
            c, room_id="R1", delete_reasoning_on_finalize=False,
        )
        await d.start()
        await d.finalize("Answer")
        assert c.deletes == []  # nicht geloescht

    @pytest.mark.asyncio
    async def test_update_phase_dedup(self):
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1")
        await d.start()
        await d.update("text", phase="p1")
        await d.update("same-phase", phase="p1")
        # 2. Update mit gleicher Phase → skipped
        assert len(c.edits) == 1

    @pytest.mark.asyncio
    async def test_delete_clears_both_lanes(self):
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1")
        await d.start()
        await d.finalize("answer")
        # reasoning wurde bei finalize geloescht; answer bleibt
        assert len(c.deletes) == 1
        # Jetzt delete() loescht answer
        await d.delete()
        assert len(c.deletes) == 2

    @pytest.mark.asyncio
    async def test_interface_compatible_with_status_editor(self):
        """Duck-type Check: LaneDeliverer hat gleiches Interface wie StatusEditor."""
        from app.services.webex.delivery import StatusEditor
        editor_methods = {"start", "update", "finalize", "delete", "message_id"}
        lane_methods = {m for m in dir(LaneDeliverer) if not m.startswith("_")}
        status_methods = {m for m in dir(StatusEditor) if not m.startswith("_")}
        # Alle StatusEditor-public-API muss auch in LaneDeliverer sein
        assert editor_methods.issubset(lane_methods)
        assert editor_methods.issubset(status_methods)
