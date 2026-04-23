"""
ConversationRegistry — resolve Conversation per Message (Sprint 3, B4).

Haelt pro Message die passende ``WebexConversation`` vor (mit Cache).
Nicht gebundene Conversations werden bei ``resolve()`` neu erstellt,
persistiert und in den Cache geschoben.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.services.webex.conversation.binding_store import (
    ConversationBinding,
    ConversationBindingStore,
)
from app.services.webex.conversation.scope import (
    ConversationKey,
    ConversationPolicy,
    Scope,
)

logger = logging.getLogger(__name__)


# Default Idle-Timeout fuer Auto-Generation-Bump (24h, siehe /sc:brainstorm).
DEFAULT_IDLE_RESET_SECONDS = 24 * 60 * 60


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parst ISO8601 → datetime (UTC). None wenn leer/ungueltig."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@dataclass
class WebexConversation:
    """Resolved Conversation — in-memory repraesentation."""
    conv_key: str
    room_id: str
    thread_id: str
    session_id: str
    scope: Scope
    policy: ConversationPolicy
    # v5 — Session-Generation (Context-Management)
    generation: int = 1
    reset_pending: bool = False

    @classmethod
    def from_binding(cls, binding: ConversationBinding) -> "WebexConversation":
        return cls(
            conv_key=binding.conv_key,
            room_id=binding.room_id,
            thread_id=binding.thread_id,
            session_id=binding.session_id,
            scope=binding.scope,
            policy=binding.policy,
            generation=binding.generation,
            reset_pending=binding.reset_pending,
        )

    @property
    def token_cap_key(self) -> str:
        """Scope-Key fuer den Daily-Token-Cap (heute nur global)."""
        return self.room_id

    @property
    def effective_session_id(self) -> str:
        """Orchestrator-Session-Key inkl. Generation-Suffix.

        Generation 1 (default) ist suffix-frei fuer Backward-Compat.
        Ab Generation 2 wird ``:g{N}`` angehaengt.
        """
        if self.generation <= 1:
            return self.session_id
        return f"{self.session_id}:g{self.generation}"


# Auto-Strategy: default ist "webex:{room_id}[:{thread_id}]"
def default_session_id_factory(key: ConversationKey) -> str:
    return f"webex:{key.key}"


# Policy-Resolver: wird beim ConversationRegistry injiziert, um
# Account-Default + Room-Overrides zu einer effektiven Policy zu merge-n.
PolicyResolver = Callable[[ConversationKey, Scope], ConversationPolicy]


class ConversationRegistry:
    """Zentraler Resolver — pro Message gibt es genau eine Conversation.

    Der Registry kennt:
    - Account-Default-Policy
    - Room-Overrides (als Liste/Map)
    - Persistenz via BindingStore

    ``resolve(msg)`` ist die Hauptmethode:
    - Cache-Hit: liefert direkt
    - DB-Hit: Binding laden, in Cache legen
    - Sonst: neue Conversation bauen, persistieren, cachen
    """

    def __init__(
        self,
        *,
        binding_store: ConversationBindingStore,
        policy_resolver: PolicyResolver,
        session_id_factory: Callable[[ConversationKey], str] = default_session_id_factory,
        idle_reset_seconds: int = DEFAULT_IDLE_RESET_SECONDS,
    ) -> None:
        self._bindings = binding_store
        self._policy_resolver = policy_resolver
        self._session_id_factory = session_id_factory
        self._idle_reset_seconds = max(0, int(idle_reset_seconds))
        self._cache: Dict[str, WebexConversation] = {}
        self._lock = asyncio.Lock()

    async def warm_load(self) -> int:
        """Laedt alle persistierten Bindings in den Cache.

        Kann beim Bot-Start aufgerufen werden. Gibt Anzahl geladener
        Bindings zurueck.
        """
        bindings = await self._bindings.list_all()
        async with self._lock:
            for b in bindings:
                self._cache[b.conv_key] = WebexConversation.from_binding(b)
        logger.info("[conv-registry] %d Bindings aus DB geladen", len(bindings))
        return len(bindings)

    async def resolve(self, msg: Dict[str, Any]) -> Optional[WebexConversation]:
        """Resolved die ``WebexConversation`` fuer eine eingehende Message.

        Returns:
            ``WebexConversation`` oder None wenn das Msg keine room_id hat.
        """
        key = ConversationKey.from_message(msg)
        if not key.room_id:
            return None

        async with self._lock:
            cached = self._cache.get(key.key)
            if cached:
                return cached

        # DB-Lookup ausserhalb der Lock (I/O)
        binding = await self._bindings.get(key.key)
        if binding:
            conv = WebexConversation.from_binding(binding)
            async with self._lock:
                self._cache[key.key] = conv
            return conv

        # Neue Conversation anlegen
        scope = Scope.from_room_type(
            str(msg.get("room_type") or ""),
            has_thread=bool(key.thread_id),
        )
        policy = self._policy_resolver(key, scope)
        session_id = self._session_id_factory(key)
        conv = WebexConversation(
            conv_key=key.key,
            room_id=key.room_id,
            thread_id=key.thread_id,
            session_id=session_id,
            scope=scope,
            policy=policy,
        )

        # Persistieren
        await self._bindings.upsert(
            conv_key=conv.conv_key,
            room_id=conv.room_id,
            thread_id=conv.thread_id,
            session_id=conv.session_id,
            scope=conv.scope,
            policy=conv.policy,
        )
        async with self._lock:
            self._cache[conv.conv_key] = conv

        logger.info(
            "[conv-registry] Neue Conversation: key=%s scope=%s session=%s",
            conv.conv_key, conv.scope.value, conv.session_id,
        )
        return conv

    def is_known_room(self, room_id: str) -> bool:
        """Prueft ob ein Room in irgendeinem Cache-Eintrag vorkommt.

        Wird in Multi-Conv-Mode als schneller Pre-Filter genutzt, bevor
        man den DB-Lookup macht. Cache-Basierte Antwort — kann False
        zurueckgeben bei kalt-Cache (dann faellt der eigentliche Resolve
        auf DB zurueck).
        """
        if not room_id:
            return False
        return any(c.room_id == room_id for c in self._cache.values())

    def cached_conversations(self) -> List[WebexConversation]:
        """Debug/Status: listet in-Memory gehaltene Conversations."""
        return list(self._cache.values())

    async def forget(self, conv_key: str) -> bool:
        """Entfernt ein Binding aus Cache+DB (fuer /reset-Cmd)."""
        async with self._lock:
            self._cache.pop(conv_key, None)
        return await self._bindings.delete(conv_key)

    # ── v5: Session-Generation ──────────────────────────────────────────────

    async def maybe_bump_idle(self, conv_key: str) -> Optional[int]:
        """Bumpt die Generation wenn ``last_activity`` > ``idle_reset_seconds`` zurueckliegt.

        Wird vom Dispatcher VOR jeder eingehenden Message aufgerufen.
        Returns neue Generation-Nummer falls gebumped, sonst None.
        """
        if self._idle_reset_seconds <= 0:
            return None
        binding = await self._bindings.get(conv_key)
        if binding is None:
            return None
        last = _parse_iso(binding.last_activity_utc)
        if last is None:
            # Frisches Binding ohne Activity-Timestamp — kein Idle-Bump.
            await self._bindings.touch_activity(conv_key)
            return None
        idle = (datetime.now(timezone.utc) - last).total_seconds()
        if idle < self._idle_reset_seconds:
            return None
        new_gen = await self._bindings.bump_generation(
            conv_key, mark_reset_pending=True,
        )
        if new_gen > 0:
            # Cache invalidieren: WebexConversation muss neu geladen werden
            async with self._lock:
                self._cache.pop(conv_key, None)
            logger.info(
                "[conv-registry] Idle-Reset: %s → generation=%d (idle=%.0fs)",
                conv_key[:32], new_gen, idle,
            )
        return new_gen if new_gen > 0 else None

    async def bump_manual(self, conv_key: str) -> Optional[int]:
        """Manueller Reset (``/new``): Generation +1, kein Reset-Pending-Flag.

        Returns neue Generation oder None wenn Binding nicht existiert.
        """
        new_gen = await self._bindings.bump_generation(
            conv_key, mark_reset_pending=False,
        )
        if new_gen > 0:
            async with self._lock:
                self._cache.pop(conv_key, None)
            logger.info(
                "[conv-registry] Manual-Reset: %s → generation=%d",
                conv_key[:32], new_gen,
            )
            return new_gen
        return None

    async def continue_previous(self, conv_key: str) -> Optional[int]:
        """Reaktiviert die vorherige Generation (``/continue``).

        Returns neue (dekrementierte) Generation oder None wenn bereits
        bei 1 (kein Undo moeglich).
        """
        prev_gen = await self._bindings.decrement_generation(conv_key)
        if prev_gen is not None:
            async with self._lock:
                self._cache.pop(conv_key, None)
            logger.info(
                "[conv-registry] Continue: %s → generation=%d",
                conv_key[:32], prev_gen,
            )
        return prev_gen

    async def touch(self, conv_key: str) -> None:
        """Markiert Binding als aktiv (last_activity_utc = now)."""
        await self._bindings.touch_activity(conv_key)

    async def acknowledge_reset(self, conv_key: str) -> None:
        """Wird nach Inline-Footer-Anzeige aufgerufen: clear reset_pending."""
        await self._bindings.clear_reset_pending(conv_key)
        # Cache-Eintrag aktualisieren, damit nachfolgende resolves nicht
        # fälschlicherweise reset_pending=True sehen.
        async with self._lock:
            cached = self._cache.get(conv_key)
            if cached is not None:
                cached.reset_pending = False
