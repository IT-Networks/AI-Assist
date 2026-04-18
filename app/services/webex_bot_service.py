"""
Webex AI-Assist Chat-Bot Service.

Bindet einen dedizierten Webex-Space als Remote-Terminal an den Agent-Orchestrator:

  [Owner-Handy] ─▶ [Webex-Space "AI-Assist"] ─▶ AssistRoomHandler
                                                ├─ Echo-Schutz + Owner-Allowlist
                                                ├─ Slash-Cmds (/new, /cancel, /status, ...)
                                                ├─ Session-Mapping "webex:{room_id}"
                                                └─ Orchestrator.process() → send_message

Phase 1: Fallback-Polling (use_webhooks=False). Webhook-Endpoint folgt in Phase 2.
"""

import asyncio
import hashlib
import hmac
import logging
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Sprint 1 — Edit-in-place + Persistenz (opt-in via edit_in_place)
from app.services.webex.delivery import EditThrottle, StatusEditor
from app.services.webex.safety import ErrorPolicyGate, ErrorScope
from app.services.webex.state import (
    DailyUsageStore,
    ProcessedMessagesStore,
    SentMessageCache,
    WebexDb,
    resolve_db_path,
)

# Sprint 2 — Streaming + Approvals + Audit
from app.services.webex.audit import AuditLogger
from app.services.webex.interactive import (
    ApprovalBus,
    ApprovalTimeout,
    build_approval_card,
)

# Sprint 3 — Multi-Conversation + Lane-Delivery
from app.services.webex.conversation import (
    ConversationBindingStore,
    ConversationKey,
    ConversationPolicy,
    ConversationRegistry,
    Scope,
    WebexConversation,
)
from app.services.webex.delivery import LaneDeliverer


# ── Tool-Context: aktueller Bot-Run ──────────────────────────────────────────
# Wird vom AssistRoomHandler vor orchestrator.process() gesetzt und nach dem
# Run zurueckgesetzt. Agent-Tools koennen so den aktuellen Room / Thread
# erkennen ohne explizite Parameter. Ausserhalb eines Bot-Runs: None.
_webex_bot_ctx: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "webex_bot_ctx", default=None
)


def get_current_bot_context() -> Optional[Dict[str, str]]:
    """Gibt {'room_id','parent_id','session_id'} fuer den aktiven Bot-Run zurueck.

    Wird von Agent-Tools (webex_reply/share_diagram/share_file) genutzt, um den
    aktuellen Ziel-Room ohne expliziten Parameter zu finden. None ausserhalb
    eines laufenden Bot-Agent-Runs.
    """
    try:
        return _webex_bot_ctx.get()
    except LookupError:
        return None

logger = logging.getLogger(__name__)


# ── Hilfen ───────────────────────────────────────────────────────────────────

def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _session_id_for(room_id: str, parent_id: str = "") -> str:
    base = f"webex:{room_id}"
    return f"{base}:{parent_id}" if parent_id else base


# ── Hauptklasse ──────────────────────────────────────────────────────────────

class AssistRoomHandler:
    """Orchestriert den Nachrichten-Fluss zwischen AI-Assist-Room und Agent."""

    PROCESS_KEY_PREFIX = "wx-bot:"
    VERSION_TAG = "v1"

    def __init__(self) -> None:
        self._running: bool = False
        self._poll_task: Optional[asyncio.Task] = None
        self._active_runs: Dict[str, asyncio.Task] = {}  # session_id → Task
        self._me: Dict[str, str] = {}                    # Bot-Self-Info (id/email)
        self._room_id: str = ""
        self._room_title: str = ""
        self._last_poll_ts: Optional[datetime] = None
        self._webhook_id: str = ""                       # Phase 2: aktive Webhook-ID
        # Phase 4: Daily-Token-Usage-Counter — Key: "YYYY-MM-DD" (UTC), Value: Tokens
        # Legacy In-Memory-Fallback. Wenn edit_in_place=True wird _usage_store genutzt.
        self._daily_usage: Dict[str, int] = {}
        # ── Sprint 1 (optional; nur aktiv wenn edit_in_place=True) ───────
        self._db: Optional[WebexDb] = None
        self._usage_store: Optional[DailyUsageStore] = None
        self._processed_store: Optional[ProcessedMessagesStore] = None
        self._sent_cache: Optional[SentMessageCache] = None
        self._error_gate: Optional[ErrorPolicyGate] = None
        # ── Sprint 2 (optional; nur aktiv wenn jeweilige Flags True) ─────
        self._approval_bus: Optional[ApprovalBus] = None
        self._audit: Optional[AuditLogger] = None
        self._actions_webhook_id: str = ""
        # ── Sprint 3 (optional; nur aktiv wenn multi_conversation=True) ──
        self._binding_store: Optional[ConversationBindingStore] = None
        self._registry: Optional[ConversationRegistry] = None
        self._room_overrides: Dict[str, Any] = {}  # room_id → WebexRoomOverride

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True wenn der Fallback-Poller aktiv ist (Webhook-Only = False)."""
        return self._running and self._poll_task is not None and not self._poll_task.done()

    @property
    def is_active(self) -> bool:
        """True wenn Bot entweder polled ODER per Webhook empfangsbereit ist."""
        return self._running and (self.is_running or bool(self._webhook_id))

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

        # Bot-Self-Info cachen (id fuer Echo-Schutz, email fuer Logs)
        try:
            self._me = await client.get_person_me()
            logger.info(
                "[webex-bot] Auth-Identity: %s (%s, type=%s)",
                self._me.get("display_name", "?"),
                self._me.get("email", "?"),
                self._me.get("type", "?"),
            )
        except Exception as e:
            logger.error("[webex-bot] Konnte Bot-Identity nicht laden: %s", e)
            raise

        # Room resolven — in Multi-Conv-Mode optional (primaerer Room fuer Greeting/Status)
        if settings.webex.bot.multi_conversation and settings.webex.bot.rooms:
            try:
                self._room_id, self._room_title = await self._resolve_room(
                    client, settings.webex.bot,
                )
            except RuntimeError:
                # Kein primaerer Room → nimm ersten aus overrides als Default
                first = settings.webex.bot.rooms[0]
                self._room_id = first.room_id
                self._room_title = f"<multi-conv:{first.room_id[:12]}>"
                logger.info(
                    "[webex-bot] Multi-Conv ohne expliziten Primary-Room — nutze %s als Default fuer Greeting",
                    self._room_id[:20],
                )
        else:
            self._room_id, self._room_title = await self._resolve_room(
                client, settings.webex.bot,
            )
        logger.info("[webex-bot] Bot-Room: %s (%s)", self._room_title, self._room_id[:20])

        # Sprint 1: Persistenz + ErrorPolicy VOR Greeting initialisieren,
        # damit _track_sent() die Greeting-Msg direkt aufnimmt.
        if settings.webex.bot.edit_in_place:
            await self._init_sprint1_components(settings.webex.bot)

        # Greeting posten
        if settings.webex.bot.greet_on_startup:
            try:
                version = getattr(settings, "version", "") or "AI-Assist"
                greeting = (
                    f"🟢 **AI-Assist online**  \n"
                    f"_Remote-Terminal-Modus aktiv. Ich reagiere nur auf Nachrichten autorisierter Absender._  \n"
                    f"\nKommandos: `/new`, `/cancel`, `/status`, `/model <name>`, `/help`"
                )
                greeting_result = await client.send_message(room_id=self._room_id, markdown=greeting)
                await self._track_sent(greeting_result)
            except Exception as e:
                logger.warning("[webex-bot] Greeting fehlgeschlagen: %s", e)

        self._last_poll_ts = datetime.now(timezone.utc)
        self._running = True

        webhook_ok = False
        if settings.webex.bot.use_webhooks:
            try:
                await self.ensure_webhook()
                webhook_ok = True
                logger.info("[webex-bot] Webhook-Modus aktiv (webhook_id=%s)", self._webhook_id[:20])
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
                self._poll_loop(interval_override=interval)
            )
            mode = "safety-poller" if webhook_ok else "fallback-poller"
            logger.info("[webex-bot] %s gestartet (Intervall: %ds)", mode, interval)

        return self.get_status()

    async def _init_sprint1_components(self, bot_cfg) -> None:
        """Initialisiert SQLite-Stores + ErrorPolicyGate (Sprint 1 + 2 opt-in)."""
        try:
            db_path = resolve_db_path()
            self._db = WebexDb(db_path)
            await asyncio.to_thread(self._db.migrate)

            self._usage_store = DailyUsageStore(self._db)
            self._processed_store = ProcessedMessagesStore(self._db)
            self._sent_cache = SentMessageCache(db=self._db)
            self._error_gate = ErrorPolicyGate(
                policy=bot_cfg.error_policy,
                cooldown_seconds=float(bot_cfg.error_cooldown_seconds),
            )

            # Sprint 2 — Approvals + Audit (individuell per Feature-Flag)
            approvals_cfg = getattr(bot_cfg, "approvals", None)
            if approvals_cfg is not None and approvals_cfg.enabled:
                self._approval_bus = ApprovalBus(
                    self._db,
                    default_timeout_seconds=float(approvals_cfg.timeout_seconds),
                )
                logger.info(
                    "[webex-bot] Approvals aktiv (timeout=%ds)",
                    approvals_cfg.timeout_seconds,
                )

            audit_cfg = getattr(bot_cfg, "audit", None)
            if audit_cfg is not None and audit_cfg.enabled:
                self._audit = AuditLogger(
                    self._db,
                    retention_days=int(audit_cfg.retention_days),
                    enabled=True,
                )

            # Sprint 3 — Registry + Bindings (immer init wenn DB da, aber
            # voll aktiv nur bei multi_conversation=True)
            self._binding_store = ConversationBindingStore(self._db)
            self._room_overrides = {
                o.room_id: o for o in (getattr(bot_cfg, "rooms", None) or [])
            }
            self._registry = ConversationRegistry(
                binding_store=self._binding_store,
                policy_resolver=self._build_policy_resolver(bot_cfg),
            )
            if bot_cfg.multi_conversation:
                loaded = await self._registry.warm_load()
                logger.info(
                    "[webex-bot] Multi-Conversation aktiv (%d Overrides, %d Bindings geladen)",
                    len(self._room_overrides), loaded,
                )

            # Hauswirtschaft: abgelaufene Eintraege einmalig purgen
            try:
                purged_proc = await self._processed_store.purge_expired()
                purged_sent = await self._sent_cache.purge_expired()
                purged_aud = (
                    await self._audit.purge_expired() if self._audit else 0
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
            # Nie den Bot-Start crashen lassen — Fallback auf Legacy-Pfad
            logger.error(
                "[webex-bot] Sprint-1-Init fehlgeschlagen (%s) — Legacy-Pipeline wird genutzt",
                e, exc_info=True,
            )
            self._db = None
            self._usage_store = None
            self._processed_store = None
            self._sent_cache = None
            self._error_gate = None
            self._approval_bus = None
            self._audit = None
            self._binding_store = None
            self._registry = None
            self._room_overrides = {}

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
            "db_active": self._db is not None,
        }
        if self._error_gate is not None:
            sprint1_info["error_policy"] = self._error_gate.policy
        # Sprint 3
        sprint1_info["multi_conversation"] = settings.webex.bot.multi_conversation
        sprint1_info["lane_delivery"] = settings.webex.bot.lane_delivery
        if self._registry is not None:
            sprint1_info["conversations"] = len(self._registry.cached_conversations())
        sprint1_info["room_overrides"] = list(self._room_overrides.keys())
        return {
            "running": self._running,
            "poller_running": self.is_running,
            "bot_enabled": settings.webex.bot.enabled,
            "use_webhooks": settings.webex.bot.use_webhooks,
            "webhook_id": self._webhook_id,
            "webhook_public_url": settings.webex.bot.webhook_public_url,
            "webhook_secret_set": bool(settings.webex.bot.webhook_secret),
            "room_id": self._room_id,
            "room_title": self._room_title,
            "bot_identity": self._me,
            "active_runs": list(self._active_runs.keys()),
            "last_poll": self._last_poll_ts.isoformat() if self._last_poll_ts else None,
            "allowed_senders": list(settings.webex.bot.allowed_senders or []),
            "daily_token_cap": int(settings.webex.bot.daily_token_cap or 0),
            "daily_token_usage": dict(self._daily_usage),
            "sprint1": sprint1_info,
        }

    async def cancel(self, room_id: str = "") -> bool:
        """Bricht laufenden Agent-Run fuer diesen Room ab. Ohne room_id → Bot-Room."""
        target_session = _session_id_for(room_id or self._room_id)
        task = self._active_runs.get(target_session)
        if task and not task.done():
            task.cancel()
            # Warte kurz auf Task-Abbruch (CancelledError)
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            logger.info("[webex-bot] Agent-Run abgebrochen: %s", target_session)
            return True
        return False

    # ── Room-Resolution ──────────────────────────────────────────────────────

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

    # ── Polling ──────────────────────────────────────────────────────────────

    async def _poll_loop(self, interval_override: Optional[int] = None) -> None:
        from app.core.config import settings
        base = interval_override if interval_override is not None else settings.webex.bot.fallback_poll_seconds
        interval = max(3, int(base or 10))

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[webex-bot] Poll-Fehler: %s", e, exc_info=True)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _poll_once(self) -> None:
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        # Multi-Conversation: alle konfigurierten Rooms einbeziehen.
        # Single-Room: nur self._room_id.
        if settings.webex.bot.multi_conversation and self._room_overrides:
            room_ids = [rid for rid in self._room_overrides.keys() if rid]
            if self._room_id and self._room_id not in room_ids:
                room_ids.insert(0, self._room_id)
        else:
            if not self._room_id:
                return
            room_ids = [self._room_id]

        client = get_webex_client()
        since = self._last_poll_ts or (datetime.now(timezone.utc) - timedelta(minutes=5))
        # Zeitstempel VOR dem Poll updaten, um Race-Fenster klein zu halten
        poll_started_at = datetime.now(timezone.utc)

        try:
            messages = await client.get_new_messages_since(
                room_ids=room_ids,
                since=since,
                max_per_room=50,
            )
        except Exception as e:
            logger.warning("[webex-bot] get_new_messages_since failed: %s", e)
            return

        # Aeltester zuerst verarbeiten
        messages.sort(key=lambda m: m.get("created", ""))

        for msg in messages:
            try:
                await self._dispatch_incoming(msg)
            except Exception as e:
                logger.error("[webex-bot] dispatch failed for msg %s: %s",
                             (msg.get("id") or "?")[:20], e, exc_info=True)

        self._last_poll_ts = poll_started_at

    # ── Nachrichten-Dispatch ─────────────────────────────────────────────────

    async def _dispatch_incoming(self, msg: Dict[str, Any]) -> None:
        """Filtert, idempotent markiert und leitet eine Nachricht in den Handler."""
        from app.core.config import settings
        from app.services.todo_store import get_todo_store

        msg_id = msg.get("id", "")
        if not msg_id:
            return

        # Sprint 1: SentMessageCache-Echo-Check VOR person_id (faengt Forwards/Copies ab)
        if self._sent_cache is not None and await self._sent_cache.contains(msg_id):
            return

        # Echo-Schutz: eigene Nachrichten ignorieren
        if msg.get("person_id") and self._me.get("id") and msg["person_id"] == self._me["id"]:
            return

        # Sprint 3: Multi-Conversation-Mode filtert via Registry statt single-room.
        # Einzelnen Conversation-Kontext per Msg resolven.
        multi_conv = settings.webex.bot.multi_conversation
        msg_room_id = msg.get("room_id") or ""
        conversation: Optional[WebexConversation] = None

        if multi_conv and self._registry is not None:
            # Muss bekannter Room sein (Room-Override konfiguriert)
            if not msg_room_id or msg_room_id not in self._room_overrides:
                return
            conversation = await self._registry.resolve(msg)
            if conversation is None:
                return
        else:
            # Single-Room: nur der konfigurierte Room
            if msg_room_id != self._room_id:
                return

        # Idempotenz — neue Pipeline wenn verfuegbar, sonst Legacy-TodoStore
        process_key = f"{self.PROCESS_KEY_PREFIX}{self.VERSION_TAG}:{msg_id}"
        if self._processed_store is not None:
            if await self._processed_store.is_processed(process_key):
                return
        else:
            legacy_store = get_todo_store()
            if legacy_store.is_processed(process_key):
                return

        # Owner-Allowlist: Multi-Conv nutzt per-Conv-Policy, sonst Account-Default
        sender = (msg.get("person_email") or "").lower()
        if conversation is not None:
            if not conversation.policy.is_authorized(sender):
                logger.info(
                    "[webex-bot] conv-policy blockt Absender %s (room=%s)",
                    sender or "(empty)", msg_room_id[:20],
                )
                await self._mark_processed(process_key, msg_room_id or self._room_id)
                return
        else:
            allowed = [s.lower() for s in (settings.webex.bot.allowed_senders or [])]
            if allowed and sender not in allowed:
                logger.info(
                    "[webex-bot] Nachricht von nicht-autorisiertem Absender ignoriert: %s",
                    sender or "(empty)",
                )
                await self._mark_processed(process_key, msg_room_id or self._room_id)
                return

        # Ab hier: legitime User-Msg — markiere VOR Verarbeitung (Crash-Safety)
        await self._mark_processed(process_key, msg_room_id or self._room_id)

        text = (msg.get("text") or "").strip()
        parent_id = msg.get("parent_id") or ""
        # Session-ID: Multi-Conv nutzt conv.session_id, sonst bisheriges Schema.
        if conversation is not None:
            session_id = conversation.session_id
            target_room_id = conversation.room_id
        else:
            session_id = _session_id_for(self._room_id, parent_id)
            target_room_id = self._room_id

        logger.info(
            "[webex-bot] IN [%s] %s: %s",
            sender or "?", session_id[-24:], text[:80].replace("\n", " "),
        )

        # Slash-Commands direkt behandeln — ohne Agent
        if text.startswith("/"):
            handled = await self._handle_slash_command(
                text, session_id, parent_id, target_room_id=target_room_id,
            )
            if handled:
                return

        # Sonst: Agent-Run starten (nicht-blockierend)
        await self._start_agent_run(
            session_id, text, parent_id, msg,
            target_room_id=target_room_id,
            conversation=conversation,
        )

    def _build_policy_resolver(self, bot_cfg):
        """Baut den Policy-Resolver fuer die ConversationRegistry.

        Liefert fuer jeden ConversationKey eine effektive Policy:
        Account-Default merged mit Room-Override (wenn vorhanden).
        """
        # Account-Default Policy aus settings
        account_default = ConversationPolicy(
            scope=Scope.GROUP,
            allow_from=list(bot_cfg.allowed_senders or []),
            require_mention=False,
            default_model=bot_cfg.default_model or "",
            max_history=int(getattr(bot_cfg.response_style, "max_history", 10) or 10),
            daily_token_cap=int(bot_cfg.daily_token_cap or 0),
            error_policy=str(bot_cfg.error_policy or "once"),
        )
        overrides = self._room_overrides

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
            # Room-Override existiert → merge
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

    async def _mark_processed(self, process_key: str, room_id: str) -> None:
        """Markiert einen Process-Key als verarbeitet (neue Pipeline oder Legacy)."""
        if self._processed_store is not None:
            await self._processed_store.mark_processed(process_key, room_id=room_id)
        else:
            from app.services.todo_store import get_todo_store
            get_todo_store().mark_processed(process_key)

    async def _track_sent(self, send_result: Dict[str, Any]) -> None:
        """Registriert eine vom Bot gesendete Nachricht im SentMessageCache."""
        if self._sent_cache is None:
            return
        msg_id = ""
        if isinstance(send_result, dict):
            # webex_client.send_message gibt raw API-Response zurueck
            msg_id = str(send_result.get("id") or "")
        if msg_id:
            await self._sent_cache.add(msg_id, room_id=self._room_id)

    # ── Slash-Commands ───────────────────────────────────────────────────────

    async def _handle_slash_command(
        self,
        text: str,
        session_id: str,
        parent_id: str,
        *,
        target_room_id: str = "",
    ) -> bool:
        """Gibt True zurueck wenn der Text als Command behandelt wurde."""
        from app.services.chat_store import save_chat, load_chat
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        reply_room_id = target_room_id or self._room_id

        async def reply(md: str) -> None:
            try:
                result = await client.send_message(
                    room_id=reply_room_id, markdown=md, parent_id=parent_id,
                )
                await self._track_sent(result)
            except Exception as e:
                logger.warning("[webex-bot] reply (slash) fehlgeschlagen: %s", e)

        if cmd in ("/help", "/?"):
            await reply(
                "**AI-Assist Kommandos**\n"
                "- `/new` — neue Session (Historie verwerfen)\n"
                "- `/cancel` — laufende Anfrage abbrechen\n"
                "- `/status` — Session- und Bot-Status\n"
                "- `/model <name>` — LLM-Modell dieser Session setzen (z.B. `sonnet`)\n"
                "- `/help` — diese Hilfe"
            )
            return True

        if cmd == "/new":
            # Session zuruecksetzen (leere History-Datei)
            save_chat(
                session_id=session_id,
                title=f"Webex {session_id}",
                messages_history=[{"role": "system", "content": "Session reset via /new."}],
                mode="read_only",
            )
            # Auch Orchestrator-State leeren falls vorhanden
            try:
                from app.agent.orchestrator import get_agent_orchestrator
                orch = get_agent_orchestrator()
                if hasattr(orch, "_states") and session_id in getattr(orch, "_states", {}):
                    orch._states.pop(session_id, None)
            except Exception:
                pass
            await reply("🔄 Session zurueckgesetzt. Ich bin bereit.")
            return True

        if cmd == "/cancel":
            cancelled = await self.cancel(self._room_id)
            await reply("🛑 Laufender Agent-Run abgebrochen." if cancelled else "ℹ️ Kein laufender Agent-Run.")
            return True

        if cmd == "/status":
            chat = load_chat(session_id)
            history_count = len(chat.get("messages_history", [])) if chat else 0
            status = self.get_status()
            await reply(
                f"**Status**\n"
                f"- Room: `{self._room_title}`\n"
                f"- Session: `{session_id}` ({history_count} Msgs)\n"
                f"- Aktive Runs: `{len(status['active_runs'])}`\n"
                f"- Letzter Poll: `{status['last_poll'] or '—'}`"
            )
            return True

        if cmd == "/model":
            if not arg:
                await reply("Usage: `/model <name>` z.B. `/model sonnet`")
                return True
            # Model wird beim naechsten Agent-Run per-Request gesetzt
            self._per_session_model = getattr(self, "_per_session_model", {})
            self._per_session_model[session_id] = arg
            await reply(f"🤖 Modell fuer diese Session: `{arg}` (greift ab naechster Anfrage).")
            return True

        # Kein bekannter Slash-Cmd → als normale Anfrage behandeln
        return False

    # ── Agent-Run ────────────────────────────────────────────────────────────

    async def _start_agent_run(
        self,
        session_id: str,
        text: str,
        parent_id: str,
        original_msg: Dict[str, Any],
        *,
        target_room_id: str = "",
        conversation: Optional[WebexConversation] = None,
    ) -> None:
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        room_id = target_room_id or self._room_id

        # Gleiche Session schon busy?
        existing = self._active_runs.get(session_id)
        if existing and not existing.done():
            try:
                result = await client.send_message(
                    room_id=room_id,
                    markdown="⏳ Ich bearbeite bereits eine Anfrage in dieser Session. `/cancel` zum Abbrechen.",
                    parent_id=parent_id,
                )
                await self._track_sent(result)
            except Exception:
                pass
            return

        # Concurrency-Cap (Threads zaehlen mit)
        max_rooms = max(1, int(settings.webex.bot.max_concurrent_rooms or 3))
        live = sum(1 for t in self._active_runs.values() if not t.done())
        if live >= max_rooms:
            try:
                result = await client.send_message(
                    room_id=room_id,
                    markdown=f"⚠️ Maximale Parallelitaet erreicht ({max_rooms}). Bitte warten.",
                    parent_id=parent_id,
                )
                await self._track_sent(result)
            except Exception:
                pass
            return

        # Daily-Token-Cap: Conv-Policy-Override gewinnt vor Account-Default
        cap = int(settings.webex.bot.daily_token_cap or 0)
        if conversation is not None and conversation.policy.daily_token_cap > 0:
            cap = conversation.policy.daily_token_cap
        if cap > 0:
            key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._usage_store is not None:
                used = int(await self._usage_store.get_used(key))
            else:
                used = int(self._daily_usage.get(key, 0))
            if used >= cap:
                try:
                    result = await client.send_message(
                        room_id=room_id,
                        markdown=(
                            f"🚦 Tageslimit erreicht: {used}/{cap} Tokens. "
                            f"Limit setzt um 00:00 UTC zurueck."
                        ),
                        parent_id=parent_id,
                    )
                    await self._track_sent(result)
                except Exception:
                    pass
                return

        # Typing-Indicator — nur im Legacy-Pfad. Bei edit_in_place postet
        # _run_agent() direkt die Status-Message (die spaeter editiert wird).
        if not settings.webex.bot.edit_in_place:
            try:
                result = await client.send_message(
                    room_id=room_id,
                    markdown="⏳ _Agent arbeitet …_",
                    parent_id=parent_id,
                )
                await self._track_sent(result)
            except Exception as e:
                logger.debug("[webex-bot] typing-reply failed: %s", e)

        task = asyncio.create_task(
            self._run_agent(
                session_id, text, parent_id, original_msg,
                target_room_id=room_id,
                conversation=conversation,
            ),
            name=f"webex-bot:{session_id}",
        )
        self._active_runs[session_id] = task
        task.add_done_callback(lambda _t, sid=session_id: self._active_runs.pop(sid, None))

    async def _run_agent(
        self,
        session_id: str,
        text: str,
        parent_id: str,
        original_msg: Dict[str, Any],
        *,
        target_room_id: str = "",
        conversation: Optional[WebexConversation] = None,
    ) -> None:
        from app.agent.orchestrator import get_agent_orchestrator
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        orchestrator = get_agent_orchestrator()
        room_id = target_room_id or self._room_id

        # Modell bestimmen: /model > conv-policy > default_model > Orchestrator-Default
        model: Optional[str] = None
        per_session = getattr(self, "_per_session_model", {})
        if session_id in per_session:
            model = per_session[session_id]
        elif conversation is not None and conversation.policy.default_model:
            model = conversation.policy.default_model
        elif settings.webex.bot.default_model:
            model = settings.webex.bot.default_model

        # Final-Response aggregieren (Webex kein token-level Streaming)
        final_response_parts: List[str] = []
        error_msg: Optional[str] = None
        tool_count = 0
        last_tool_name: str = ""

        # ── Sprint 1/3: StatusEditor oder LaneDeliverer ──────────────────────
        editor: Any = None  # StatusEditor ODER LaneDeliverer (duck-typed)
        if settings.webex.bot.edit_in_place:
            if settings.webex.bot.lane_delivery:
                editor = LaneDeliverer(client, room_id, parent_id)
            else:
                editor = StatusEditor(client, room_id, parent_id)
            initial_id = await editor.start("⏳ _Agent arbeitet …_")
            if initial_id:
                await self._track_sent({"id": initial_id})

        # ── Sprint 2: Token-Streaming Throttle ───────────────────────────────
        streaming_cfg = settings.webex.bot.streaming
        streaming_on = bool(
            editor is not None
            and getattr(streaming_cfg, "enabled", False)
        )
        throttle: Optional[EditThrottle] = None
        if streaming_on:
            throttle = EditThrottle(
                min_interval_seconds=float(streaming_cfg.edit_interval_seconds),
                min_delta_chars=int(streaming_cfg.edit_min_delta_chars),
            )
        stream_max = int(getattr(streaming_cfg, "max_edit_chars", 6000))

        # Sprint 2: Audit-Log eingehende User-Msg
        if self._audit is not None:
            await self._audit.log(
                "msg_in",
                actor_email=original_msg.get("person_email", "") or "",
                room_id=room_id,
                session_id=session_id,
                payload={
                    "text": text[:500],
                    "msg_id": original_msg.get("id", ""),
                    "parent_id": parent_id,
                    "has_attachments": bool(original_msg.get("file_urls")),
                },
            )

        # Bot-Kontext setzen damit Agent-Tools den aktiven Room kennen
        ctx_token = _webex_bot_ctx.set({
            "room_id": room_id,
            "parent_id": parent_id,
            "session_id": session_id,
        })

        try:
            attachments = await self._build_attachments_async(original_msg)
            if attachments:
                logger.info("[webex-bot] %d Bild-Attachment(s) angehaengt", len(attachments))

            channel_context = await self._build_channel_context(original_msg)

            gen = orchestrator.process(
                session_id=session_id,
                user_message=text,
                model=model,
                context_selection=None,
                attachments=attachments,
                tts=False,
                channel_hint="webex",
                channel_context=channel_context,
            )

            async for event in gen:
                etype = getattr(event, "type", None)
                name = getattr(etype, "value", "") if etype is not None else ""

                if name == "token" and isinstance(event.data, str):
                    final_response_parts.append(event.data)
                    # Sprint 2: Streaming-Preview — throttled edit
                    if throttle is not None and editor is not None:
                        accumulated = "".join(final_response_parts)
                        if throttle.should_flush(len(accumulated)):
                            preview = accumulated[:stream_max] + " ▍"
                            await editor.update(preview, phase="streaming")
                elif name == "tool_start":
                    tool_count += 1
                    if editor is not None:
                        tool_name = ""
                        if isinstance(event.data, dict):
                            tool_name = str(event.data.get("name") or "")
                        last_tool_name = tool_name or last_tool_name or "?"
                        await editor.update(
                            f"🔧 _Tool: {last_tool_name}_",
                            phase=f"tool:{last_tool_name}",
                        )
                        if self._audit is not None:
                            await self._audit.log(
                                "tool_call",
                                room_id=room_id,
                                session_id=session_id,
                                payload={"tool": last_tool_name},
                            )
                elif name == "mcp_complete" and editor is not None:
                    await editor.update("🧠 _Analyse …_", phase="thinking")
                elif name == "usage":
                    # Token-Usage fuer Daily-Cap mitzaehlen
                    usage = event.data
                    total = 0
                    if hasattr(usage, "total_tokens"):
                        total = int(getattr(usage, "total_tokens", 0) or 0)
                    elif isinstance(usage, dict):
                        total = int(usage.get("total_tokens", 0) or 0)
                    if total:
                        key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        if self._usage_store is not None:
                            try:
                                await self._usage_store.add_tokens(key, total)
                            except Exception as e:
                                logger.debug("[webex-bot] usage_store.add failed: %s", e)
                                self._daily_usage[key] = self._daily_usage.get(key, 0) + total
                        else:
                            self._daily_usage[key] = self._daily_usage.get(key, 0) + total
                elif name == "done":
                    if isinstance(event.data, dict):
                        resp = event.data.get("response")
                        if isinstance(resp, str) and resp:
                            final_response_parts = [resp]
                elif name == "error":
                    data = event.data if isinstance(event.data, dict) else {}
                    error_msg = str(data.get("error") or event.data or "Unbekannter Fehler")
                    break
                elif name == "confirm_required":
                    # Sprint 2: Adaptive-Card-Approval-Flow (falls aktiviert)
                    if self._approval_bus is not None:
                        tool_name = ""
                        confirmation_data: Dict[str, Any] = {}
                        if isinstance(event.data, dict):
                            tool_name = str(event.data.get("name") or "")
                            confirmation_data = event.data.get("confirmation_data") or {}
                        approved, exec_output, exec_error = await self._handle_approval(
                            orchestrator=orchestrator,
                            session_id=session_id,
                            parent_id=parent_id,
                            tool_name=tool_name,
                            confirmation_data=confirmation_data,
                            editor=editor,
                            sender_email=(original_msg.get("person_email") or ""),
                            target_room_id=room_id,
                        )
                        if approved and exec_error is None:
                            if exec_output:
                                final_response_parts = [exec_output]
                        elif exec_error is not None:
                            error_msg = exec_error
                        else:
                            # User lehnt ab ODER timeout
                            error_msg = "Operation vom User abgelehnt oder Timeout."
                    else:
                        error_msg = (
                            "Diese Anfrage erfordert eine Schreibbestaetigung (path_approval). "
                            "Aktiviere `webex.bot.approvals.enabled` oder fuehre die Operation "
                            "in der Web-UI aus."
                        )
                    break

        except asyncio.CancelledError:
            logger.info("[webex-bot] Agent-Run cancelled: %s", session_id)
            try:
                if editor is not None:
                    await editor.finalize("🛑 _Abgebrochen._")
                else:
                    result = await client.send_message(
                        room_id=room_id,
                        markdown="🛑 _Abgebrochen._",
                        parent_id=parent_id,
                    )
                    await self._track_sent(result)
            except Exception:
                pass
            raise
        except Exception as e:
            logger.error("[webex-bot] Orchestrator error: %s", e, exc_info=True)
            error_msg = str(e)

        # Antwort zusammensetzen + posten
        answer = "".join(final_response_parts).strip()
        if error_msg:
            body = f"⚠️ **Fehler:** {error_msg}"
        elif not answer:
            body = "_(Keine Antwort vom Agent — bitte anders formulieren.)_"
        else:
            body = answer

        # Webex Message-Limit beachten
        limit = max(1000, int(settings.webex.bot.max_reply_chars or 7000))
        if len(body) > limit:
            head = body[:limit]
            body = head + f"\n\n_[... gekuerzt, {len(body) - limit} Zeichen ausgeblendet]_"

        # Optional Footer mit Tool-Nutzung
        if tool_count and not error_msg:
            body = f"{body}\n\n— _{tool_count} Tool-Call(s)_"

        # ── Error-Policy-Gate (Sprint 1): Spam-Supression ────────────────────
        should_post = True
        if error_msg and self._error_gate is not None:
            scope = ErrorScope(
                room_id=room_id,
                thread_id=parent_id or "",
                error_class="agent-error",
            )
            should_post = self._error_gate.should_post(scope)
            if not should_post:
                suppressed = self._error_gate.suppressed_count(scope)
                logger.info(
                    "[webex-bot] Fehler-Post unterdrueckt (policy=%s, suppressed=%d): %s",
                    self._error_gate.policy, suppressed, error_msg[:120],
                )

        try:
            if not should_post:
                if editor is not None:
                    await editor.delete()
            elif editor is not None:
                await editor.finalize(body)
            else:
                result = await client.send_message(
                    room_id=room_id,
                    markdown=body,
                    parent_id=parent_id,
                )
                await self._track_sent(result)
            # Audit: finalen Antwort-Post + ggf. Fehler protokollieren
            if self._audit is not None:
                if error_msg:
                    await self._audit.log(
                        "error",
                        room_id=room_id,
                        session_id=session_id,
                        payload={"error": error_msg[:500], "posted": should_post},
                    )
                elif should_post:
                    await self._audit.log(
                        "msg_out",
                        room_id=room_id,
                        session_id=session_id,
                        payload={
                            "length": len(body),
                            "tool_count": tool_count,
                        },
                    )
        except Exception as e:
            logger.error("[webex-bot] Antwort-Post fehlgeschlagen: %s", e)
        finally:
            # Bot-Kontext freigeben (ContextVar wurde zu Beginn von _run_agent gesetzt)
            try:
                _webex_bot_ctx.reset(ctx_token)
            except (LookupError, ValueError):
                pass

    # ── Sprint 2: Approval-Handler ───────────────────────────────────────────

    MAX_APPROVAL_PHASES = 3  # Sicherheits-Cap gegen Cascading-Approvals

    async def _handle_approval(
        self,
        *,
        orchestrator: Any,
        session_id: str,
        parent_id: str,
        tool_name: str,
        confirmation_data: Dict[str, Any],
        editor: Any,
        sender_email: str,
        target_room_id: str = "",
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Fuehrt den Approval-Workflow fuer eine Operation durch.

        Flow:
          1. Editor auf "Warte auf Freigabe" stellen
          2. Adaptive Card posten + mit ApprovalBus verknuepfen
          3. Auf Entscheidung warten (Timeout)
          4. Bei approved: Operation ausfuehren (Multi-Phase-Loop)
          5. Ergebnis / Fehler zurueckgeben

        Returns:
            (approved, output, error)
            - approved=True + output=str → erfolgreiche Ausfuehrung
            - approved=True + error=str → Ausfuehrung gescheitert
            - approved=False + error=None → rejected/timeout ohne Fehler
            - approved=False + error=str → Fehler im Approval-Setup
        """
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        if self._approval_bus is None:
            return (False, None, "Approval-Bus nicht aktiv.")

        client = get_webex_client()
        room_id = target_room_id or self._room_id
        risk_level = self._resolve_risk_level(tool_name, confirmation_data)
        description = str(
            confirmation_data.get("description")
            or confirmation_data.get("operation")
            or f"Operation: {tool_name}"
        )
        args_summary = self._build_args_summary(confirmation_data)

        # 1. Card posten
        rid: Optional[str] = None
        card_msg_id: str = ""
        try:
            rid = await self._approval_bus.create_pending(
                session_id=session_id,
                room_id=room_id,
                parent_id=parent_id,
                tool_name=tool_name or "unknown",
                tool_args=confirmation_data,
                confirmation_data=confirmation_data,
            )
            card = build_approval_card(
                request_id=rid,
                tool_name=tool_name or "unknown",
                risk_level=risk_level,
                description=description,
                args_summary=args_summary,
                requester=sender_email,
            )
            card_msg = await client.send_message(
                room_id=room_id,
                markdown=f"🔐 **Freigabe erforderlich**: `{tool_name}` — bitte im Card-Block entscheiden.",
                attachments=[card],
                parent_id=parent_id,
            )
            card_msg_id = str((card_msg or {}).get("id") or "")
            if card_msg_id:
                await self._track_sent({"id": card_msg_id})
                await self._approval_bus.set_card_message_id(rid, card_msg_id)

            if self._audit is not None:
                await self._audit.log(
                    "approval_new",
                    actor_email=sender_email,
                    room_id=room_id,
                    session_id=session_id,
                    risk_level=risk_level,
                    payload={
                        "rid": rid, "tool": tool_name,
                        "operation": confirmation_data.get("operation", ""),
                    },
                )

            if editor is not None:
                await editor.update("⏳ _Warte auf Freigabe …_", phase="awaiting-approval")

        except Exception as e:
            logger.error("[webex-bot] approval card post failed: %s", e, exc_info=True)
            if rid:
                try:
                    await self._approval_bus.cancel(rid)
                except Exception:
                    pass
            return (False, None, f"Approval-Setup fehlgeschlagen: {e}")

        # 2. Auf Entscheidung warten
        timeout = float(settings.webex.bot.approvals.timeout_seconds)
        try:
            decision = await self._approval_bus.wait_for_decision(
                rid, timeout_seconds=timeout,
            )
        except ApprovalTimeout:
            logger.info("[webex-bot] approval timeout rid=%s", rid)
            await self._cleanup_card(card_msg_id)
            return (False, None, None)
        except Exception as e:
            logger.warning("[webex-bot] approval wait failed: %s", e)
            await self._cleanup_card(card_msg_id)
            return (False, None, f"Approval-Wait-Error: {e}")

        # 3. Entscheidung abgebildet → Card-Buttons entfernen/archivieren
        await self._cleanup_card(card_msg_id)

        if not decision.approved:
            return (False, None, None)

        # 4. Ausfuehrung (Multi-Phase-Loop)
        current_conf = confirmation_data
        for phase in range(1, self.MAX_APPROVAL_PHASES + 1):
            if editor is not None:
                await editor.update(
                    f"⚙️ _Fuehre aus …_ (Phase {phase})", phase=f"executing-{phase}",
                )
            try:
                orchestrator.set_confirmation_result(session_id, True)
                result = await orchestrator._execute_confirmed_operation(current_conf)
            except Exception as e:
                logger.error("[webex-bot] execute_confirmed_operation failed: %s", e, exc_info=True)
                return (True, None, f"Ausfuehrung fehlgeschlagen: {e}")

            success = bool(getattr(result, "success", True))
            output = getattr(result, "output", None) or getattr(result, "result", None)
            error = getattr(result, "error", None)
            needs_next = bool(getattr(result, "requires_confirmation", False))

            if not success:
                return (True, None, str(error or "Operation fehlgeschlagen."))

            if not needs_next:
                final = ""
                if isinstance(output, str):
                    final = output
                elif output is not None:
                    final = str(output)
                return (True, final or "✓ Operation erfolgreich ausgefuehrt.", None)

            # Naechste Phase: neue Approval noetig
            next_conf = getattr(result, "confirmation_data", None) or {}
            if not next_conf:
                return (True, str(output or "") or "✓ Operation ausgefuehrt.", None)
            logger.info("[webex-bot] approval cascade phase %d → neue Card", phase + 1)

            # Rekursiver Approval-Call fuer naechste Phase
            approved2, out2, err2 = await self._handle_approval(
                orchestrator=orchestrator,
                session_id=session_id,
                parent_id=parent_id,
                tool_name=str(next_conf.get("operation") or tool_name),
                confirmation_data=next_conf,
                editor=editor,
                sender_email=sender_email,
                target_room_id=room_id,
            )
            return (approved2, out2, err2)

        return (True, None, "Max Approval-Phasen erreicht — Abbruch.")

    async def _cleanup_card(self, card_message_id: str) -> None:
        """Loescht die Approval-Card nach Entscheidung (Buttons sollen nicht klickbar bleiben)."""
        if not card_message_id:
            return
        try:
            from app.services.webex_client import get_webex_client
            await get_webex_client().delete_message(card_message_id)
        except Exception as e:
            logger.debug("[webex-bot] card cleanup failed (%s): %s", card_message_id[:20], e)

    @staticmethod
    def _resolve_risk_level(tool_name: str, confirmation_data: Dict[str, Any]) -> str:
        """Heuristik fuer Risiko-Einschaetzung (fuer Card-Styling)."""
        op = str(confirmation_data.get("operation") or "").lower()
        access = str(confirmation_data.get("access_type") or "").lower()
        tname = (tool_name or "").lower()
        if "delete" in op or access == "delete":
            return "high"
        if "script" in op or "exec" in tname or "command" in tname:
            return "high"
        if "write" in op or access == "write":
            return "medium"
        return "low"

    @staticmethod
    def _build_args_summary(confirmation_data: Dict[str, Any]) -> str:
        """Baut einen kompakten Args-Summary-String fuer die Card."""
        if not confirmation_data:
            return ""
        interesting = (
            "operation", "path", "requested_path", "access_type",
            "description", "action", "target",
        )
        lines: List[str] = []
        for k in interesting:
            v = confirmation_data.get(k)
            if v:
                lines.append(f"{k}: {str(v)[:120]}")
        return "\n".join(lines[:8])

    # ── Attachments fuer Vision-In (Phase 4) ─────────────────────────────────

    def _build_attachments(self, msg: Dict[str, Any]) -> Optional[List[dict]]:
        """Wandelt Bild-Attachments einer Webex-Msg in Orchestrator-Attachments.

        Synchron-Stub — der tatsaechliche Download passiert async via
        _build_attachments_async(). Diese Methode bleibt existent fuer Tests /
        einfache Flows, wird aber vom _run_agent ueberlagert.
        """
        return None

    async def _build_channel_context(self, msg: Dict[str, Any]) -> Optional[Any]:
        """Baut den ``ChannelContext`` fuer den Orchestrator (Webex-Chat-Verlauf).

        Holt die letzten N Messages aus Thread (wenn ``parent_id`` vorhanden)
        oder Room, sortiert aelteste-zuerst und filtert die Trigger-Message raus.
        Bei Fehler / deaktivierter History → None (Bot laeuft weiter ohne Verlauf).
        """
        from app.core.config import settings
        from app.services.webex_client import get_webex_client
        from app.services.webex_context import WebexContextBuilder

        style = getattr(settings.webex.bot, "response_style", None)
        if not style or not getattr(style, "include_history", True):
            return None

        msg_id = msg.get("id") or ""
        room_id = msg.get("room_id") or self._room_id
        if not room_id or not msg_id:
            return None

        builder = WebexContextBuilder(
            client=get_webex_client(),
            bot_person_id=self._me.get("id", ""),
        )
        try:
            return await builder.build(
                room_id=room_id,
                room_type=msg.get("room_type") or "",
                room_title=self._room_title,
                trigger_message_id=msg_id,
                trigger_author=msg.get("person_display_name") or msg.get("person_email") or "",
                thread_parent_id=msg.get("parent_id") or "",
                max_history=int(getattr(style, "max_history", 10) or 10),
                max_chars_per_message=int(getattr(style, "max_chars_per_message", 500) or 500),
            )
        except Exception as e:
            logger.warning("[webex-bot] channel-context build fehlgeschlagen: %s", e)
            return None

    async def _build_attachments_async(self, msg: Dict[str, Any]) -> Optional[List[dict]]:
        """Laedt Bild-Attachments der User-Msg herunter und baut multimodal-Attachments.

        Format (s. app/services/multimodal.py):
            {"type": "image", "mime": "image/png", "data": "<base64>", "name": "..."}

        Nicht-Bild-Attachments werden ignoriert (PDF/Docs koennte Phase 5 sein).
        """
        import base64
        from app.services.webex_client import get_webex_client

        file_urls = msg.get("file_urls") or []
        if not file_urls:
            return None

        client = get_webex_client()
        out: List[dict] = []

        for url in file_urls[:4]:  # max 4 Bilder pro Msg
            try:
                data, content_type, filename = await client.download_file(url)
            except Exception as e:
                logger.warning("[webex-bot] download_file failed for %s: %s", url[:60], e)
                continue

            mime = (content_type or "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                logger.debug("[webex-bot] Attachment skipped (non-image): %s", mime or "?")
                continue

            # Groesse limitieren (10 MB Bild-Hardcap fuer Vision)
            if len(data) > 10 * 1024 * 1024:
                logger.info("[webex-bot] Attachment skipped (too large): %d bytes", len(data))
                continue

            out.append({
                "type": "image",
                "mime": mime,
                "data": base64.b64encode(data).decode("ascii"),
                "name": filename or "attachment",
            })

        return out or None

    # ── Webhook-Modus ────────────────────────────────────────────────────────

    async def ensure_webhook(self) -> str:
        """Stellt sicher, dass genau EIN passender Webhook bei Webex registriert ist.

        - Sucht existierende Webhooks mit gleichem Namen/targetUrl
        - Loescht Duplikate oder Webhooks mit falscher URL
        - Erstellt neuen Webhook wenn keiner passt
        - Setzt Filter auf roomId={bot_room_id} damit nur Bot-Room-Events kommen
        """
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        cfg = settings.webex.bot
        if not cfg.webhook_public_url:
            raise RuntimeError(
                "webex.bot.webhook_public_url ist leer — bitte oeffentliche HTTPS-URL eintragen."
            )
        if not cfg.multi_conversation and not self._room_id:
            raise RuntimeError("Bot-Room nicht resolved — start() vorher aufrufen.")

        client = get_webex_client()
        target_url = cfg.webhook_public_url.strip()
        # Multi-Conv: leerer Filter = alle Rooms (Bot sieht nur Rooms wo er Mitglied ist).
        # Single-Room: roomId-Filter beschraenkt auf den Bot-Room.
        expected_filter = "" if cfg.multi_conversation else f"roomId={self._room_id}"
        expected_name = cfg.webhook_name or "ai-assist-bot"

        # Bestand einlesen und bereinigen
        existing = await client.list_webhooks(max_hooks=100)
        keep_id = ""
        for hook in existing:
            same_target = hook.get("targetUrl", "").strip() == target_url
            same_name = hook.get("name", "") == expected_name
            same_filter = hook.get("filter", "") == expected_filter
            resource_msg = hook.get("resource", "") == "messages"
            event_created = hook.get("event", "") == "created"
            if same_target and same_name and same_filter and resource_msg and event_created:
                if not keep_id:
                    keep_id = hook.get("id", "")
                    continue  # ersten passenden behalten
                # Duplikat loeschen
                try:
                    await client.delete_webhook(hook.get("id", ""))
                    logger.info("[webex-bot] Duplicate-Webhook entfernt: %s", hook.get("id", "")[:20])
                except Exception as e:
                    logger.warning("[webex-bot] Duplicate delete failed: %s", e)
            elif same_name or (same_target and not same_filter):
                # Altlast / falscher Filter: wegraeumen
                try:
                    await client.delete_webhook(hook.get("id", ""))
                    logger.info("[webex-bot] Alter Webhook entfernt: %s (name=%s, url=%s)",
                                hook.get("id", "")[:20], hook.get("name", ""), hook.get("targetUrl", ""))
                except Exception as e:
                    logger.warning("[webex-bot] Cleanup delete failed: %s", e)

        if keep_id:
            self._webhook_id = keep_id
            return keep_id

        # Neuen Webhook registrieren
        created = await client.register_webhook(
            name=expected_name,
            target_url=target_url,
            resource="messages",
            event="created",
            filter=expected_filter,
            secret=cfg.webhook_secret or "",
        )
        self._webhook_id = created.get("id", "")
        logger.info(
            "[webex-bot] Webhook registriert: id=%s url=%s filter=%s secret=%s",
            self._webhook_id[:20],
            target_url,
            expected_filter,
            "yes" if cfg.webhook_secret else "no",
        )

        # Sprint 2: Zweiten Webhook fuer attachmentActions registrieren
        # (sofern approvals aktiviert)
        if self._approval_bus is not None:
            try:
                await self._ensure_actions_webhook(client, cfg, target_url)
            except Exception as e:
                logger.warning(
                    "[webex-bot] actions-Webhook-Registrierung fehlgeschlagen: %s", e,
                )

        return self._webhook_id

    async def _ensure_actions_webhook(self, client, cfg, messages_target_url: str) -> str:
        """Registriert/deduped den Webhook fuer ``attachmentActions.created``.

        Target-URL wird aus ``actions_webhook_public_url`` (falls gesetzt) oder
        durch Ersetzen des Pfad-Suffix in ``webhook_public_url`` abgeleitet.
        """
        actions_url = (
            getattr(cfg, "actions_webhook_public_url", "") or ""
        ).strip()
        if not actions_url:
            # Auto-ableiten: letzte Path-Komponente ersetzen
            # z.B. https://.../webhooks/webex → https://.../webhooks/attachment-actions
            if "/webhooks/" in messages_target_url:
                base = messages_target_url.rsplit("/webhooks/", 1)[0]
                actions_url = f"{base}/webhooks/attachment-actions"
            else:
                logger.warning(
                    "[webex-bot] actions_webhook_public_url leer und webhook_public_url hat kein /webhooks/-Segment"
                )
                return ""

        expected_name = getattr(cfg, "actions_webhook_name", "") or "ai-assist-bot-actions"
        expected_filter = "" if cfg.multi_conversation else f"roomId={self._room_id}"

        existing = await client.list_webhooks(max_hooks=100)
        keep_id = ""
        for hook in existing:
            same_target = hook.get("targetUrl", "").strip() == actions_url
            same_name = hook.get("name", "") == expected_name
            same_filter = hook.get("filter", "") == expected_filter
            resource_ok = hook.get("resource", "") == "attachmentActions"
            event_ok = hook.get("event", "") == "created"
            if same_target and same_name and same_filter and resource_ok and event_ok:
                if not keep_id:
                    keep_id = hook.get("id", "")
                    continue
                try:
                    await client.delete_webhook(hook.get("id", ""))
                except Exception as e:
                    logger.debug("actions webhook duplicate delete failed: %s", e)
            elif same_name or (same_target and not same_filter):
                try:
                    await client.delete_webhook(hook.get("id", ""))
                except Exception as e:
                    logger.debug("actions webhook cleanup delete failed: %s", e)

        if keep_id:
            self._actions_webhook_id = keep_id
            return keep_id

        created = await client.register_webhook(
            name=expected_name,
            target_url=actions_url,
            resource="attachmentActions",
            event="created",
            filter=expected_filter,
            secret=cfg.webhook_secret or "",
        )
        self._actions_webhook_id = created.get("id", "")
        logger.info(
            "[webex-bot] attachmentActions-Webhook registriert: id=%s url=%s",
            self._actions_webhook_id[:20], actions_url,
        )
        return self._actions_webhook_id

    async def remove_webhook(self) -> bool:
        """Entfernt den aktuell registrierten Webhook (fuer Shutdown / Umkonfiguration)."""
        from app.services.webex_client import get_webex_client

        if not self._webhook_id:
            return False
        try:
            await get_webex_client().delete_webhook(self._webhook_id)
            logger.info("[webex-bot] Webhook entfernt: %s", self._webhook_id[:20])
            self._webhook_id = ""
            return True
        except Exception as e:
            logger.warning("[webex-bot] Webhook-Deletion fehlgeschlagen: %s", e)
            return False

    @staticmethod
    def verify_signature(secret: str, body: bytes, signature: str) -> bool:
        """Prueft X-Spark-Signature: HMAC-SHA1(secret, raw_body) == signature (hex).

        - Bei leerem secret: nur True wenn auch signature leer ist (konservativ).
        - constant-time Vergleich via hmac.compare_digest.
        """
        if not secret:
            return not signature
        if not signature:
            return False
        try:
            expected = hmac.new(
                secret.encode("utf-8"),
                body,
                hashlib.sha1,
            ).hexdigest()
            return hmac.compare_digest(expected, signature.strip())
        except Exception:
            return False

    async def on_webhook_event(self, payload: Dict[str, Any]) -> None:
        """Entry-Point fuer eingehende Webex-Webhook-Events (resource=messages, event=created).

        Webex-Webhook liefert nur Metadaten — der volle Nachrichtentext wird
        via GET /messages/{id} nachgeladen. Danach laeuft der normale Dispatch.
        """
        from app.services.webex_client import get_webex_client

        if not self._running:
            logger.debug("[webex-bot] Webhook-Event ignoriert — Handler nicht aktiv")
            return

        # Event-Filter: nur messages.created
        if payload.get("resource") != "messages" or payload.get("event") != "created":
            return

        data = payload.get("data") or {}
        msg_id = data.get("id") or ""
        room_id = data.get("roomId") or ""
        actor_person_id = data.get("personId") or payload.get("actorId") or ""

        if not msg_id:
            return

        # Fruehe Filter ohne API-Call: eigene Msg / fremder Room
        if self._me.get("id") and actor_person_id and actor_person_id == self._me.get("id"):
            return
        # Multi-Conv: akzeptiere Rooms aus overrides; Single-Room: nur self._room_id
        from app.core.config import settings as _settings
        if _settings.webex.bot.multi_conversation:
            if room_id and self._room_overrides and room_id not in self._room_overrides:
                return
        else:
            if self._room_id and room_id and room_id != self._room_id:
                return

        # Volle Nachricht laden (Webex liefert keinen Text im Webhook)
        try:
            client = get_webex_client()
            full_msg = await client.get_message(msg_id)
        except Exception as e:
            logger.warning("[webex-bot] get_message(%s) failed: %s", msg_id[:20], e)
            return

        try:
            await self._dispatch_incoming(full_msg)
        except Exception as e:
            logger.error("[webex-bot] webhook dispatch failed for %s: %s",
                         msg_id[:20], e, exc_info=True)

    async def on_attachment_action_event(self, payload: Dict[str, Any]) -> None:
        """Entry-Point fuer ``attachmentActions.created`` Webhook-Events (Sprint 2).

        Laedt die Action-Details nach und leitet die Entscheidung an die
        ApprovalBus weiter, die wartende Agent-Runs weckt.
        """
        from app.services.webex_client import get_webex_client

        if not self._running or self._approval_bus is None:
            logger.debug("[webex-bot] attachment-action ignoriert (not running or approvals off)")
            return
        if payload.get("resource") != "attachmentActions" or payload.get("event") != "created":
            return

        data = payload.get("data") or {}
        action_id = data.get("id") or ""
        if not action_id:
            return

        # Action-Details laden (inputs = {action, rid, ...})
        try:
            action = await get_webex_client().get_attachment_action(action_id)
        except Exception as e:
            logger.warning("[webex-bot] get_attachment_action(%s) failed: %s",
                           action_id[:20], e)
            return

        # Room-Filter: Multi-Conv akzeptiert konfigurierte Rooms, Single nur _room_id
        from app.core.config import settings as _settings
        action_room = action.get("room_id") or ""
        if _settings.webex.bot.multi_conversation:
            if action_room and self._room_overrides and action_room not in self._room_overrides:
                return
        else:
            if self._room_id and action_room and action_room != self._room_id:
                return
        # Eigene Actions ignorieren (Bot kann eigene Cards nicht klicken, aber sicher ist sicher)
        if action.get("person_id") and self._me.get("id") and action["person_id"] == self._me["id"]:
            return

        inputs = action.get("inputs") or {}
        rid = str(inputs.get("rid") or "")
        decision = str(inputs.get("action") or "").lower()
        actor_email = action.get("person_email") or ""

        if not rid or decision not in ("approve", "reject"):
            logger.info(
                "[webex-bot] attachment-action ohne rid/action: inputs=%s",
                inputs,
            )
            return

        approved = decision == "approve"
        ok = await self._approval_bus.resolve(
            rid, approved=approved, actor_email=actor_email,
        )
        if self._audit is not None:
            await self._audit.log(
                "approval_done",
                actor_email=actor_email,
                room_id=action_room or self._room_id,
                payload={"rid": rid, "decision": decision, "accepted": ok},
            )


# ── Singleton ────────────────────────────────────────────────────────────────

_handler: Optional[AssistRoomHandler] = None


def get_assist_room_handler() -> AssistRoomHandler:
    global _handler
    if _handler is None:
        _handler = AssistRoomHandler()
    return _handler
