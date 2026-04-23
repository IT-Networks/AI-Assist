"""Webex AI-Assist Chat-Bot Service — Facade/Coordinator.

Bindet einen dedizierten Webex-Space als Remote-Terminal an den Agent-Orchestrator:

  [Owner-Handy] ─▶ [Webex-Space "AI-Assist"] ─▶ AssistRoomHandler
                                                ├─ Dispatcher (Filter + Dedup + Auth)
                                                ├─ SlashCommandRouter (/new, /cancel, ...)
                                                ├─ AgentRunner (Orchestrator + Streaming)
                                                ├─ ApprovalFlow (Adaptive Cards)
                                                ├─ WebhookRegistrar
                                                └─ PollingLoop (Fallback/Safety)

Nach H3-Refactor (Sprint 4): Die frueheren ~1758 LOC der God-Class sind in
das Package ``app/services/webex/runtime/`` aufgeteilt. Diese Facade
koordiniert nur noch Lifecycle und reicht das gemeinsame ``HandlerContext``
an die Komponenten weiter. Public API unveraendert.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.webex.audit import AuditLogger
from app.services.webex.conversation import (
    ConversationBindingStore,
    ConversationKey,
    ConversationPolicy,
    ConversationRegistry,
    Scope,
)
from app.services.webex.interactive import ApprovalBus, ApprovalStatus
from app.services.webex.runtime import (
    AgentRunner,
    ApprovalFlow,
    ChannelContextBuilder,
    HandlerContext,
    MessageDispatcher,
    PollingLoop,
    SlashCommandRouter,
    WebhookRegistrar,
)
from app.services.webex.runtime.agent_runner import (
    _webex_bot_ctx,  # noqa: F401 — re-exported for webex_tools
    get_current_bot_context,  # noqa: F401 — public API
)
from app.services.webex.safety import ErrorPolicyGate
from app.services.webex.state import (
    DailyUsageStore,
    ProcessedMessagesStore,
    SentMessageCache,
    WebexDb,
    resolve_db_path,
)

logger = logging.getLogger(__name__)


# ── Hilfen (beibehalten fuer Backward-Compat) ───────────────────────────────

def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _session_id_for(room_id: str, parent_id: str = "") -> str:
    base = f"webex:{room_id}"
    return f"{base}:{parent_id}" if parent_id else base


# ── Hauptklasse ──────────────────────────────────────────────────────────────

class AssistRoomHandler:
    """Orchestriert den Nachrichten-Fluss zwischen AI-Assist-Room und Agent.

    Nach H3-Refactor: Koordinator-Klasse mit Lifecycle-Verantwortung.
    Die eigentliche Bot-Logik liegt in ``app/services/webex/runtime/*``.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._poll_task: Optional[asyncio.Task] = None
        self._ctx = HandlerContext()

        # Komponenten werden in start() bzw. _init_sprint1_components() aufgebaut.
        # WebhookRegistrar wird fruehzeitig erzeugt (haelt Webhook-IDs).
        self._registrar = WebhookRegistrar(self._ctx)

        self._agent_runner: Optional[AgentRunner] = None
        self._approval_flow: Optional[ApprovalFlow] = None
        self._context_builder: Optional[ChannelContextBuilder] = None
        self._slash_router: Optional[SlashCommandRouter] = None
        self._dispatcher: Optional[MessageDispatcher] = None
        self._poller: Optional[PollingLoop] = None

    # ── Backward-Compat Attribute (externe Call-Sites in main.py/routes) ────
    # main.py liest handler._room_title; routes/webex.py liest _room_id + _webhook_id.

    @property
    def _room_id(self) -> str:
        return self._ctx.room_id

    @_room_id.setter
    def _room_id(self, value: str) -> None:
        self._ctx.room_id = value

    @property
    def _room_title(self) -> str:
        return self._ctx.room_title

    @property
    def _webhook_id(self) -> str:
        return self._registrar.messages_webhook_id

    @_webhook_id.setter
    def _webhook_id(self, value: str) -> None:
        self._registrar.messages_webhook_id = value

    @property
    def _me(self) -> Dict[str, str]:
        return self._ctx.me

    @property
    def _active_runs(self) -> Dict[str, asyncio.Task]:
        """Backward-compat accessor; eigentliche Map wird im AgentRunner gehalten."""
        return self._agent_runner.active_runs if self._agent_runner is not None else {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True wenn der Fallback-Poller aktiv ist (Webhook-Only = False)."""
        return self._running and self._poll_task is not None and not self._poll_task.done()

    @property
    def is_active(self) -> bool:
        """True wenn Bot entweder polled ODER per Webhook empfangsbereit ist."""
        return self._running and (self.is_running or bool(self._registrar.messages_webhook_id))

    async def start(self) -> Dict[str, Any]:
        """Bot resolven, Room resolven, optional Greeting, Fallback-Poller starten."""
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        if self.is_running:
            logger.info("[webex-bot] bereits aktiv")
            return self.get_status()

        if not settings.webex.enabled:
            raise RuntimeError("Webex ist in den Settings deaktiviert (webex.enabled=false).")
        if not settings.webex.bot.enabled:
            raise RuntimeError("Webex-Bot ist deaktiviert (webex.bot.enabled=false).")

        client = get_webex_client()

        # Bot-Self-Info cachen
        try:
            self._ctx.me = await client.get_person_me()
            logger.info(
                "[webex-bot] Auth-Identity: %s (%s, type=%s)",
                self._ctx.me.get("display_name", "?"),
                self._ctx.me.get("email", "?"),
                self._ctx.me.get("type", "?"),
            )
        except Exception as e:
            logger.error("[webex-bot] Konnte Bot-Identity nicht laden: %s", e)
            raise

        # Room resolven
        if settings.webex.bot.multi_conversation and settings.webex.bot.rooms:
            try:
                self._ctx.room_id, self._ctx.room_title = await self._resolve_room(
                    client, settings.webex.bot,
                )
            except RuntimeError:
                first = settings.webex.bot.rooms[0]
                self._ctx.room_id = first.room_id
                self._ctx.room_title = f"<multi-conv:{first.room_id[:12]}>"
                logger.info(
                    "[webex-bot] Multi-Conv ohne expliziten Primary-Room — nutze %s als Default fuer Greeting",
                    self._ctx.room_id[:20],
                )
        else:
            self._ctx.room_id, self._ctx.room_title = await self._resolve_room(
                client, settings.webex.bot,
            )
        logger.info("[webex-bot] Bot-Room: %s (%s)", self._ctx.room_title, self._ctx.room_id[:20])

        # Sprint 1 Init (auch Component-Wiring) VOR Greeting, damit _track_sent greift
        if settings.webex.bot.edit_in_place:
            await self._init_sprint1_components(settings.webex.bot)

        # Komponenten finalisieren (auch wenn Sprint-1-Init nicht lief → Minimal-Setup)
        self._wire_runtime()

        # Greeting posten
        if settings.webex.bot.greet_on_startup:
            try:
                greeting = (
                    f"🟢 **AI-Assist online**  \n"
                    f"_Remote-Terminal-Modus aktiv. Ich reagiere nur auf Nachrichten autorisierter Absender._  \n"
                    f"\nKommandos: `/new`, `/cancel`, `/status`, `/model <name>`, `/help`"
                )
                greeting_result = await client.send_message(room_id=self._ctx.room_id, markdown=greeting)
                await self._track_sent(greeting_result)
            except Exception as e:
                logger.warning("[webex-bot] Greeting fehlgeschlagen: %s", e)

        assert self._poller is not None
        self._poller.reset_last_poll()
        self._running = True

        webhook_ok = False
        if settings.webex.bot.use_webhooks:
            try:
                await self._registrar.ensure()
                webhook_ok = True
                logger.info(
                    "[webex-bot] Webhook-Modus aktiv (webhook_id=%s)",
                    self._registrar.messages_webhook_id[:20],
                )
            except Exception as e:
                logger.error(
                    "[webex-bot] Webhook-Registrierung fehlgeschlagen (%s) — fallback zu Polling",
                    e,
                )

        # Poller starten wenn: (a) kein Webhook aktiv, ODER
        # (b) safety-poller aktiviert UND Webhook erfolgreich.
        need_poller = (not webhook_ok) or (
            settings.webex.bot.edit_in_place
            and settings.webex.bot.enable_safety_poller
            and webhook_ok
        )
        if need_poller:
            interval = (
                settings.webex.bot.safety_poll_seconds
                if webhook_ok
                else settings.webex.bot.fallback_poll_seconds
            )
            self._poll_task = asyncio.create_task(
                self._poller.run(interval_override=interval)
            )
            mode = "safety-poller" if webhook_ok else "fallback-poller"
            logger.info("[webex-bot] %s gestartet (Intervall: %ds)", mode, interval)

        return self.get_status()

    async def _init_sprint1_components(self, bot_cfg) -> None:
        """Initialisiert SQLite-Stores + ErrorPolicyGate (Sprint 1/2/3 opt-in)."""
        try:
            db_path = resolve_db_path()
            self._ctx.db = WebexDb(db_path)
            await asyncio.to_thread(self._ctx.db.migrate)

            self._ctx.usage_store = DailyUsageStore(self._ctx.db)
            self._ctx.processed_store = ProcessedMessagesStore(self._ctx.db)
            self._ctx.sent_cache = SentMessageCache(db=self._ctx.db)
            self._ctx.error_gate = ErrorPolicyGate(
                policy=bot_cfg.error_policy,
                cooldown_seconds=float(bot_cfg.error_cooldown_seconds),
            )

            # Sprint 2 — Approvals + Audit (individuell per Feature-Flag)
            approvals_cfg = getattr(bot_cfg, "approvals", None)
            if approvals_cfg is not None and approvals_cfg.enabled:
                self._ctx.approval_bus = ApprovalBus(
                    self._ctx.db,
                    default_timeout_seconds=float(approvals_cfg.timeout_seconds),
                )
                logger.info(
                    "[webex-bot] Approvals aktiv (timeout=%ds)",
                    approvals_cfg.timeout_seconds,
                )

            audit_cfg = getattr(bot_cfg, "audit", None)
            if audit_cfg is not None and audit_cfg.enabled:
                self._ctx.audit = AuditLogger(
                    self._ctx.db,
                    retention_days=int(audit_cfg.retention_days),
                    enabled=True,
                )

            # Sprint 3 — Registry + Bindings
            self._ctx.binding_store = ConversationBindingStore(self._ctx.db)
            self._ctx.room_overrides = {
                o.room_id: o for o in (getattr(bot_cfg, "rooms", None) or [])
            }
            self._ctx.registry = ConversationRegistry(
                binding_store=self._ctx.binding_store,
                policy_resolver=self._build_policy_resolver(bot_cfg),
            )
            if bot_cfg.multi_conversation:
                loaded = await self._ctx.registry.warm_load()
                logger.info(
                    "[webex-bot] Multi-Conversation aktiv (%d Overrides, %d Bindings geladen)",
                    len(self._ctx.room_overrides), loaded,
                )

            # Hauswirtschaft: abgelaufene Eintraege purgen
            try:
                purged_proc = await self._ctx.processed_store.purge_expired()
                purged_sent = await self._ctx.sent_cache.purge_expired()
                purged_aud = (
                    await self._ctx.audit.purge_expired() if self._ctx.audit else 0
                )
                if purged_proc or purged_sent or purged_aud:
                    logger.info(
                        "[webex-bot] DB-Purge: %d processed + %d sent + %d audit",
                        purged_proc, purged_sent, purged_aud,
                    )
            except Exception as e:
                logger.debug("[webex-bot] DB-Purge skipped: %s", e)

            logger.info(
                "[webex-bot] Sprint-1/2-Pipeline aktiv (db=%s, policy=%s, stream=%s, approvals=%s)",
                db_path,
                bot_cfg.error_policy,
                bool(getattr(bot_cfg.streaming, "enabled", False)),
                bool(getattr(bot_cfg.approvals, "enabled", False)),
            )
        except Exception as e:
            logger.error(
                "[webex-bot] Sprint-1-Init fehlgeschlagen (%s) — Legacy-Pipeline wird genutzt",
                e, exc_info=True,
            )
            self._ctx.reset_sprint_stores()

    def _wire_runtime(self) -> None:
        """Erzeugt die Runtime-Komponenten und verdrahtet sie miteinander.

        Muss nach Room-Resolve + Sprint-1-Init aufgerufen werden. Danach ist
        der Handler bereit, Webhook-Events und Poll-Messages zu dispatchen.
        """
        self._context_builder = ChannelContextBuilder(self._ctx)
        self._approval_flow = (
            ApprovalFlow(
                approval_bus=self._ctx.approval_bus,
                audit=self._ctx.audit,
                track_sent=self._track_sent,
                default_room_id_fn=lambda: self._ctx.room_id,
            )
            if self._ctx.approval_bus is not None
            else None
        )
        self._agent_runner = AgentRunner(
            self._ctx,
            approval_flow=self._approval_flow,
            context_builder=self._context_builder,
            track_sent=self._track_sent,
        )
        # Sprint 5: Meeting-Sub-Router (Lokal-Aufnahme + Webex-API-Summary).
        # Beide Komponenten sind optional — nur aktiv wenn Config sie anschaltet.
        meeting_router = self._build_meeting_router()
        self._slash_router = SlashCommandRouter(
            self._ctx,
            cancel_fn=self.cancel,
            get_status_fn=self.get_status,
            track_sent_fn=self._track_sent,
            meeting_router=meeting_router,
        )
        self._dispatcher = MessageDispatcher(
            self._ctx,
            slash_router=self._slash_router,
            agent_runner=self._agent_runner,
        )
        self._poller = PollingLoop(
            self._ctx,
            self._dispatcher,
            is_running_fn=lambda: self._running,
        )

    def _build_meeting_router(self):
        """Erstellt den MeetingSlashRouter wenn Config aktiv ist, sonst None."""
        from app.core.config import settings as _s
        mcfg = getattr(_s, "meetings", None)
        if mcfg is None or not mcfg.enabled:
            return None
        try:
            from app.services.meetings.router import MeetingSlashRouter
            from app.services.meetings.retention import MeetingRetention
            from app.services.meetings.summarizer import MeetingSummarizer
            from app.services.meetings.poster import MeetingPoster

            target_room = mcfg.post_to_room_id or self._ctx.room_id
            poster = MeetingPoster(default_room_id=target_room)
            summarizer = MeetingSummarizer()

            local_recorder = None
            if mcfg.local_audio.enabled:
                from pathlib import Path
                from app.services.local_audio import LocalCallRecorder
                retention = MeetingRetention(
                    base_dir=Path(mcfg.local_audio.output_dir).parent,
                    transcript_days=mcfg.retention_days,
                    audio_days=0 if mcfg.local_audio.purge_audio_after_summary else mcfg.retention_days,
                )
                local_recorder = LocalCallRecorder(
                    output_dir=Path(mcfg.local_audio.output_dir),
                    summarizer=summarizer,
                    poster=poster,
                    retention=retention,
                    purge_audio_after_summary=mcfg.local_audio.purge_audio_after_summary,
                )
                # Auto-Modus aus Config spiegeln
                if mcfg.local_audio.trigger_mode == "auto_on_detect":
                    # async, aber wir sind in sync init — schedule fuer spaeter
                    asyncio.create_task(local_recorder.set_auto(True))
            # Platzhalter fuer Pfad A (Webex-API-Summarizer) — kommt spaeter
            webex_summarizer = None
            # reply_fn wird beim Dispatch per-Call rebinded (siehe slash_commands.py)
            return MeetingSlashRouter(
                reply_fn=self._noop_reply,
                local_recorder=local_recorder,
                webex_summarizer=webex_summarizer,
            )
        except Exception as e:
            logger.warning("[webex-bot] MeetingSlashRouter init fehlgeschlagen: %s", e)
            return None

    @staticmethod
    async def _noop_reply(_md: str) -> None:
        """Platzhalter-reply_fn fuer MeetingSlashRouter — wird per-Call rebinded."""
        # Der SlashCommandRouter ersetzt diese Funktion vor jedem dispatch.
        pass

    async def stop(self) -> None:
        """Poller stoppen, laufende Agent-Runs bleiben bestehen (Graceful-Shutdown)."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        logger.info("[webex-bot] Service gestoppt")

    def get_status(self) -> Dict[str, Any]:
        from app.core.config import settings
        sprint1_info: Dict[str, Any] = {
            "edit_in_place": settings.webex.bot.edit_in_place,
            "db_active": self._ctx.db is not None,
        }
        if self._ctx.error_gate is not None:
            sprint1_info["error_policy"] = self._ctx.error_gate.policy
        sprint1_info["multi_conversation"] = settings.webex.bot.multi_conversation
        sprint1_info["lane_delivery"] = settings.webex.bot.lane_delivery
        if self._ctx.registry is not None:
            sprint1_info["conversations"] = len(self._ctx.registry.cached_conversations())
        sprint1_info["room_overrides"] = list(self._ctx.room_overrides.keys())

        last_poll = self._poller.last_poll_ts if self._poller is not None else None
        active_runs = list(self._active_runs.keys())

        return {
            "running": self._running,
            "poller_running": self.is_running,
            "bot_enabled": settings.webex.bot.enabled,
            "use_webhooks": settings.webex.bot.use_webhooks,
            "webhook_id": self._registrar.messages_webhook_id,
            "webhook_public_url": settings.webex.bot.webhook_public_url,
            "webhook_secret_set": bool(settings.webex.bot.webhook_secret),
            "room_id": self._ctx.room_id,
            "room_title": self._ctx.room_title,
            "bot_identity": self._ctx.me,
            "active_runs": active_runs,
            "last_poll": last_poll.isoformat() if last_poll else None,
            "allowed_senders": list(settings.webex.bot.allowed_senders or []),
            "daily_token_cap": int(settings.webex.bot.daily_token_cap or 0),
            "daily_token_usage": dict(self._ctx.daily_usage),
            "sprint1": sprint1_info,
        }

    async def cancel(self, room_id: str = "") -> bool:
        """Bricht laufenden Agent-Run fuer diesen Room ab. Ohne room_id → Bot-Room."""
        if self._agent_runner is None:
            return False
        target_session = _session_id_for(room_id or self._ctx.room_id)
        return await self._agent_runner.cancel(target_session)

    # ── Interne Helper ───────────────────────────────────────────────────────

    async def _track_sent(self, send_result: Dict[str, Any]) -> None:
        """Registriert eine vom Bot gesendete Nachricht im SentMessageCache."""
        if self._ctx.sent_cache is None:
            return
        msg_id = ""
        if isinstance(send_result, dict):
            msg_id = str(send_result.get("id") or "")
        if msg_id:
            await self._ctx.sent_cache.add(msg_id, room_id=self._ctx.room_id)

    async def _resolve_room(self, client, bot_cfg) -> tuple:
        """Liefert (room_id, title). Priorisiert bot.room_id, fallback auf bot.room_name."""
        if bot_cfg.room_id:
            try:
                data = await client._request("GET", f"/rooms/{bot_cfg.room_id}")
                return bot_cfg.room_id, data.get("title", bot_cfg.room_name)
            except Exception as e:
                logger.warning(
                    "[webex-bot] room_id '%s' nicht erreichbar (%s) — Fallback auf room_name",
                    bot_cfg.room_id[:20], e,
                )

        if not bot_cfg.room_name:
            raise RuntimeError("Weder room_id noch room_name gesetzt (webex.bot).")

        rooms = await client.list_all_rooms(
            room_type="",
            name_contains=bot_cfg.room_name,
            max_total=2000,
        )
        exact = [r for r in rooms if r.get("title", "").strip() == bot_cfg.room_name.strip()]
        pick = exact[0] if exact else (rooms[0] if rooms else None)
        if not pick:
            raise RuntimeError(
                f"Bot-Room '{bot_cfg.room_name}' nicht gefunden. "
                f"Bitte Room im Webex anlegen und den Bot/User hinzufuegen."
            )
        return pick["id"], pick.get("title", bot_cfg.room_name)

    def _build_policy_resolver(self, bot_cfg):
        """Baut den Policy-Resolver fuer die ConversationRegistry."""
        account_default = ConversationPolicy(
            scope=Scope.GROUP,
            allow_from=list(bot_cfg.allowed_senders or []),
            require_mention=False,
            default_model=bot_cfg.default_model or "",
            max_history=int(getattr(bot_cfg.response_style, "max_history", 10) or 10),
            daily_token_cap=int(bot_cfg.daily_token_cap or 0),
            error_policy=str(bot_cfg.error_policy or "once"),
        )
        overrides = self._ctx.room_overrides

        def resolver(key: ConversationKey, scope: Scope) -> ConversationPolicy:
            override = overrides.get(key.room_id)
            if override is None:
                return ConversationPolicy(
                    scope=scope,
                    allow_from=list(account_default.allow_from),
                    require_mention=account_default.require_mention,
                    default_model=account_default.default_model,
                    max_history=account_default.max_history,
                    daily_token_cap=account_default.daily_token_cap,
                    error_policy=account_default.error_policy,
                )
            room_policy = ConversationPolicy(
                scope=scope,
                allow_from=list(override.allow_from or []),
                require_mention=bool(override.require_mention),
                default_model=override.default_model or "",
                max_history=int(override.max_history or 0),
                daily_token_cap=int(override.daily_token_cap or 0),
                error_policy=str(override.error_policy or ""),
            )
            return room_policy.inherit_from(account_default)

        return resolver

    # ── Webhook-API (Proxies) ────────────────────────────────────────────────

    async def ensure_webhook(self) -> str:
        """Stellt den Webhook sicher. Delegiert an ``WebhookRegistrar``."""
        return await self._registrar.ensure()

    async def remove_webhook(self) -> bool:
        """Entfernt den Webhook. Delegiert an ``WebhookRegistrar``."""
        return await self._registrar.remove()

    @staticmethod
    def verify_signature(secret: str, body: bytes, signature: str) -> bool:
        """HMAC-SHA1 Signatur-Check. Thin wrapper um ``WebhookRegistrar``."""
        return WebhookRegistrar.verify_signature(secret, body, signature)

    # ── Webhook-Event-Handler ────────────────────────────────────────────────

    async def on_webhook_event(self, payload: Dict[str, Any]) -> None:
        """Entry-Point fuer eingehende Webex-Webhook-Events (messages.created)."""
        from app.core.config import settings as _settings
        from app.services.webex_client import get_webex_client

        if not self._running or self._dispatcher is None:
            logger.debug("[webex-bot] Webhook-Event ignoriert — Handler nicht aktiv")
            return

        if payload.get("resource") != "messages" or payload.get("event") != "created":
            return

        data = payload.get("data") or {}
        msg_id = data.get("id") or ""
        room_id = data.get("roomId") or ""
        actor_person_id = data.get("personId") or payload.get("actorId") or ""

        if not msg_id:
            return

        # Fruehe Filter ohne API-Call
        if self._ctx.me.get("id") and actor_person_id and actor_person_id == self._ctx.me.get("id"):
            return
        if _settings.webex.bot.multi_conversation:
            if room_id and self._ctx.room_overrides and room_id not in self._ctx.room_overrides:
                return
        else:
            if self._ctx.room_id and room_id and room_id != self._ctx.room_id:
                return

        # Volle Nachricht laden
        try:
            client = get_webex_client()
            full_msg = await client.get_message(msg_id)
        except Exception as e:
            logger.warning("[webex-bot] get_message(%s) failed: %s", msg_id[:20], e)
            return

        try:
            await self._dispatcher.dispatch(full_msg)
        except Exception as e:
            logger.error(
                "[webex-bot] webhook dispatch failed for %s: %s",
                msg_id[:20], e, exc_info=True,
            )

    async def on_attachment_action_event(self, payload: Dict[str, Any]) -> None:
        """Entry-Point fuer attachmentActions.created Events.

        C1-relevant: ``ApprovalBus.resolve()`` prueft intern, dass der Klicker
        der Original-Requester ist. Bei Fremd-Klick gibt resolve() False
        zurueck; wir loggen das im Audit.
        """
        from app.core.config import settings as _settings
        from app.services.webex_client import get_webex_client

        if not self._running or self._ctx.approval_bus is None:
            logger.debug("[webex-bot] attachment-action ignoriert (not running or approvals off)")
            return
        if payload.get("resource") != "attachmentActions" or payload.get("event") != "created":
            return

        data = payload.get("data") or {}
        action_id = data.get("id") or ""
        if not action_id:
            return

        try:
            action = await get_webex_client().get_attachment_action(action_id)
        except Exception as e:
            logger.warning(
                "[webex-bot] get_attachment_action(%s) failed: %s",
                action_id[:20], e,
            )
            return

        action_room = action.get("room_id") or ""
        if _settings.webex.bot.multi_conversation:
            if action_room and self._ctx.room_overrides and action_room not in self._ctx.room_overrides:
                return
        else:
            if self._ctx.room_id and action_room and action_room != self._ctx.room_id:
                return
        if action.get("person_id") and self._ctx.me.get("id") and action["person_id"] == self._ctx.me["id"]:
            return

        inputs = action.get("inputs") or {}
        rid = str(inputs.get("rid") or "")
        decision = str(inputs.get("action") or "").lower()
        actor_email = action.get("person_email") or ""

        if not rid or decision not in ("approve", "reject"):
            logger.info("[webex-bot] attachment-action ohne rid/action: inputs=%s", inputs)
            return

        approved = decision == "approve"
        ok = await self._ctx.approval_bus.resolve(
            rid, approved=approved, actor_email=actor_email,
        )
        # C1: Wenn resolve False zurueckgibt, kann das "unbekannt",
        # "nicht mehr pending" oder "nicht autorisiert" bedeuten.
        if self._ctx.audit is not None:
            unauthorized = False
            if not ok:
                try:
                    req = await self._ctx.approval_bus.get(rid)
                    if req is not None and req.status == ApprovalStatus.PENDING:
                        unauthorized = bool(req.requester_email)
                except Exception:  # pragma: no cover — Audit darf nie werfen
                    pass
            await self._ctx.audit.log(
                "approval_done",
                actor_email=actor_email,
                room_id=action_room or self._ctx.room_id,
                payload={
                    "rid": rid,
                    "decision": decision,
                    "accepted": ok,
                    "unauthorized_attempt": unauthorized,
                },
            )


# ── Singleton ────────────────────────────────────────────────────────────────

_handler: Optional[AssistRoomHandler] = None


def get_assist_room_handler() -> AssistRoomHandler:
    global _handler
    if _handler is None:
        _handler = AssistRoomHandler()
    return _handler
