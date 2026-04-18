"""
Unit-Tests fuer Webex-Edit-Rotation (Sprint 2.1).

Deckt:
- EditCounterBucket (Counter, Threshold, Reset)
- StatusEditor Rotation (proactive + reactive)
- LaneDeliverer Rotation (nur Reasoning-Lane)
- on_new_message Callback
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.webex.delivery import (
    EditCounterBucket,
    LaneDeliverer,
    StatusEditor,
)


# ═══════════════════════════════════════════════════════════════════════════
# Mock-Client
# ═══════════════════════════════════════════════════════════════════════════


class _MockClient:
    """Tracked Mock fuer send/edit/delete mit optionalen Failure-Modi."""

    def __init__(
        self,
        *,
        fail_edit_after: Optional[int] = None,
        fail_send: bool = False,
    ):
        self.sent: List[Dict[str, Any]] = []
        self.edits: List[Dict[str, Any]] = []
        self.deletes: List[str] = []
        self._next_id = 0
        self._fail_edit_after = fail_edit_after
        self._fail_send = fail_send

    def _new_id(self) -> str:
        self._next_id += 1
        return f"msg-{self._next_id}"

    async def send_message(self, *, room_id: str, markdown: str = "",
                           parent_id: str = "", **_: Any) -> Dict[str, Any]:
        if self._fail_send:
            raise RuntimeError("send failed")
        record = {"id": self._new_id(), "room_id": room_id,
                  "markdown": markdown, "parent_id": parent_id}
        self.sent.append(record)
        return record

    async def edit_message(self, message_id: str, *, room_id: str,
                           markdown: str = "", **_: Any) -> Dict[str, Any]:
        if (
            self._fail_edit_after is not None
            and len(self.edits) >= self._fail_edit_after
        ):
            raise RuntimeError("edit failed (simulated)")
        record = {"id": message_id, "room_id": room_id, "markdown": markdown}
        self.edits.append(record)
        return record

    async def delete_message(self, message_id: str) -> None:
        self.deletes.append(message_id)


# ═══════════════════════════════════════════════════════════════════════════
# EditCounterBucket
# ═══════════════════════════════════════════════════════════════════════════


class TestEditCounterBucket:
    def test_initial_count_zero(self):
        b = EditCounterBucket()
        assert b.count == 0
        assert not b.needs_rotation()

    def test_increment_increases_count(self):
        b = EditCounterBucket(threshold=3)
        b.increment()
        b.increment()
        assert b.count == 2
        assert not b.needs_rotation()

    def test_needs_rotation_at_threshold(self):
        b = EditCounterBucket(threshold=3)
        for _ in range(3):
            b.increment()
        assert b.count == 3
        assert b.needs_rotation()

    def test_reset_clears_count(self):
        b = EditCounterBucket(threshold=3)
        for _ in range(5):
            b.increment()
        b.reset()
        assert b.count == 0
        assert not b.needs_rotation()

    def test_default_threshold_is_9(self):
        """Sprint-2.1-Design: Default = 9 (1 Puffer unter Webex-10)."""
        b = EditCounterBucket()
        assert b.threshold == 9

    def test_threshold_min_1(self):
        """Defensive: auch 0 ergibt mindestens 1."""
        b = EditCounterBucket(threshold=0)
        assert b.threshold == 1


# ═══════════════════════════════════════════════════════════════════════════
# StatusEditor — Rotation
# ═══════════════════════════════════════════════════════════════════════════


class TestStatusEditorRotation:
    @pytest.mark.asyncio
    async def test_rotation_triggered_at_threshold(self):
        """Nach `threshold` Edits wird die naechste update() zur Rotation."""
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=3)
        await ed.start("start")
        # 3 Edits → Counter = 3 (needs_rotation True)
        for i in range(3):
            await ed.update(f"text{i}", phase=f"p{i}")
        assert ed.edit_count == 3
        # 4. update triggered Rotation
        await ed.update("trigger-rotation", phase="p-rot")
        # Nach Rotation: Counter=0, neue msg_id, alte msg geloescht
        assert ed.edit_count == 0
        assert ed.message_id == "msg-2"  # neue Msg
        assert c.deletes == ["msg-1"]     # alte geloescht

    @pytest.mark.asyncio
    async def test_rotation_deletes_old_message(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=2)
        await ed.start()          # post msg-1
        await ed.update("a", phase="pa")
        await ed.update("b", phase="pb")  # count=2
        await ed.update("c", phase="pc")  # trigger rotation
        # 2 sends (initial + rotation), 1 delete (old)
        assert len(c.sent) == 2
        assert c.deletes == ["msg-1"]

    @pytest.mark.asyncio
    async def test_rotation_resets_counter(self):
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=2)
        await ed.start()
        await ed.update("a", phase="pa")
        await ed.update("b", phase="pb")
        assert ed.edit_count == 2
        await ed.update("c", phase="pc")  # rotation
        assert ed.edit_count == 0

    @pytest.mark.asyncio
    async def test_rotation_callback_fires(self):
        c = _MockClient()
        seen: List[str] = []

        async def on_new(mid: str) -> None:
            seen.append(mid)

        ed = StatusEditor(
            c, room_id="R1", edit_threshold=2, on_new_message=on_new,
        )
        await ed.start()                    # msg-1 → callback
        await ed.update("a", phase="pa")    # edit (no new msg)
        await ed.update("b", phase="pb")    # edit (no new msg)
        await ed.update("c", phase="pc")    # ROTATION → msg-2 → callback
        assert seen == ["msg-1", "msg-2"]

    @pytest.mark.asyncio
    async def test_edit_exception_triggers_reactive_rotation(self):
        """Bei Edit-Fehler (z.B. 400 'edit limit') wird rotiert."""
        c = _MockClient(fail_edit_after=0)  # jeder Edit failt
        ed = StatusEditor(c, room_id="R1", edit_threshold=99)
        await ed.start()  # msg-1
        # update() versucht Edit, failed, rotiert zu msg-2
        result = await ed.update("new", phase="p1")
        assert result is True
        assert ed.message_id == "msg-2"
        assert c.deletes == ["msg-1"]

    @pytest.mark.asyncio
    async def test_normal_edit_below_threshold(self):
        """Unter Threshold laeuft alles wie bisher — keine Rotation."""
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=10)
        await ed.start()
        for i in range(5):
            await ed.update(f"v{i}", phase=f"p{i}")
        assert len(c.sent) == 1       # nur initial-send
        assert len(c.edits) == 5       # 5 Edits
        assert len(c.deletes) == 0     # keine Deletes
        assert ed.edit_count == 5

    @pytest.mark.asyncio
    async def test_finalize_rotates_if_threshold_reached(self):
        """Finalize bei threshold-reached → rotation (damit finalize nicht 11. Edit wird)."""
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=2)
        await ed.start()
        await ed.update("a", phase="pa")
        await ed.update("b", phase="pb")
        assert ed.edit_count == 2       # threshold reached
        await ed.finalize("final-answer")
        # finalize rotierte statt zu editieren
        assert ed.message_id == "msg-2"
        assert c.deletes == ["msg-1"]

    @pytest.mark.asyncio
    async def test_finalize_edits_below_threshold(self):
        """Finalize unterhalb Threshold editiert normal — keine Rotation."""
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=10)
        await ed.start()
        await ed.update("a", phase="pa")
        await ed.finalize("final")
        # Ein Edit (update) + ein Edit (finalize) = 2 edits, 1 send (initial)
        assert len(c.sent) == 1
        assert len(c.edits) == 2
        assert len(c.deletes) == 0


# ═══════════════════════════════════════════════════════════════════════════
# LaneDeliverer — Rotation (nur Reasoning-Lane)
# ═══════════════════════════════════════════════════════════════════════════


class TestLaneDelivererRotation:
    @pytest.mark.asyncio
    async def test_reasoning_rotates_at_threshold(self):
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1", edit_threshold=2)
        await d.start()
        await d.update("a", phase="pa")
        await d.update("b", phase="pb")
        await d.update("c", phase="pc")  # rotation
        assert d.message_id == "msg-2"
        assert c.deletes == ["msg-1"]

    @pytest.mark.asyncio
    async def test_answer_not_affected_by_rotation(self):
        """Answer wird nur 1x gepostet — kein Counter, keine Rotation."""
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1", edit_threshold=2)
        await d.start()
        await d.update("a", phase="pa")
        await d.update("b", phase="pb")
        await d.update("c", phase="pc")  # rotation → msg-2
        await d.finalize("answer")        # answer = msg-3
        # answer msg-id ist unabhaengig von rotation
        assert d.answer_message_id == "msg-3"
        # reasoning (msg-2) wurde bei finalize geloescht
        # msg-1 wurde bei rotation geloescht
        assert set(c.deletes) == {"msg-1", "msg-2"}

    @pytest.mark.asyncio
    async def test_rotation_callback_fires(self):
        c = _MockClient()
        seen: List[str] = []

        async def on_new(mid: str) -> None:
            seen.append(mid)

        d = LaneDeliverer(
            c, room_id="R1", edit_threshold=2, on_new_message=on_new,
        )
        await d.start()                    # msg-1 → callback
        await d.update("a", phase="pa")
        await d.update("b", phase="pb")
        await d.update("c", phase="pc")    # rotation → msg-2 → callback
        await d.finalize("answer")         # answer-msg → callback
        assert seen == ["msg-1", "msg-2", "msg-3"]

    @pytest.mark.asyncio
    async def test_update_phase_dedup_still_works_after_rotation(self):
        """Phase-Dedup muss auch nach Rotation greifen."""
        c = _MockClient()
        d = LaneDeliverer(c, room_id="R1", edit_threshold=2)
        await d.start()
        await d.update("a", phase="pa")
        await d.update("b", phase="pb")
        await d.update("c", phase="pc")   # rotation (phase=pc)
        # Zweites update mit gleicher phase → dedup
        result = await d.update("c2", phase="pc")
        assert result is False

    @pytest.mark.asyncio
    async def test_edit_exception_triggers_rotation(self):
        c = _MockClient(fail_edit_after=0)
        d = LaneDeliverer(c, room_id="R1", edit_threshold=99)
        await d.start()
        await d.update("x", phase="p1")  # edit fails → rotation
        assert d.message_id == "msg-2"
        assert c.deletes == ["msg-1"]


# ═══════════════════════════════════════════════════════════════════════════
# StatusEditor — Long-Stream Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestLongStreamIntegration:
    @pytest.mark.asyncio
    async def test_25_updates_cause_2_rotations_with_threshold_9(self):
        """Simulation einer langen Streaming-Generation.

        Mit threshold=9 erwarten wir bei 25 Updates 2 Rotationen:
        - Updates 1-9:  msg-1 (edits)
        - Update 10:    ROTATION → msg-2 (count=0)
        - Updates 11-19: msg-2 (edits)
        - Update 20:    ROTATION → msg-3 (count=0)
        - Updates 21-25: msg-3 (edits, count=5)
        """
        c = _MockClient()
        ed = StatusEditor(c, room_id="R1", edit_threshold=9)
        await ed.start()
        for i in range(25):
            await ed.update(f"chunk-{i}", phase=f"p{i}")

        # 3 Messages gesendet (initial + 2 Rotationen)
        assert len(c.sent) == 3
        # 2 Messages geloescht (Rotation 1 + Rotation 2)
        assert len(c.deletes) == 2
        assert c.deletes == ["msg-1", "msg-2"]
        # Aktueller State: msg-3 mit count=5 (Updates 20-24 nach Rotation bei 19)
        assert ed.message_id == "msg-3"
        assert ed.edit_count == 5

    @pytest.mark.asyncio
    async def test_notification_callback_captures_all_message_ids(self):
        """Callback muss jede neue msg_id fangen — keine darf verloren gehen."""
        c = _MockClient()
        seen: List[str] = []

        async def on_new(mid: str) -> None:
            seen.append(mid)

        ed = StatusEditor(
            c, room_id="R1", edit_threshold=3, on_new_message=on_new,
        )
        await ed.start()
        for i in range(12):
            await ed.update(f"t{i}", phase=f"p{i}")
        # Bei threshold=3: Rotationen bei Update 4, 8, 12 → 4 messages total
        # (msg-1 initial + msg-2 + msg-3 + msg-4)
        assert seen == ["msg-1", "msg-2", "msg-3", "msg-4"]
