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
        self._daily_usage: Dict[str, int] = {}

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

        # Room resolven
        self._room_id, self._room_title = await self._resolve_room(client, settings.webex.bot)
        logger.info("[webex-bot] Bot-Room: %s (%s)", self._room_title, self._room_id[:20])

        # Greeting posten
        if settings.webex.bot.greet_on_startup:
            try:
                version = getattr(settings, "version", "") or "AI-Assist"
                greeting = (
                    f"🟢 **AI-Assist online**  \n"
                    f"_Remote-Terminal-Modus aktiv. Ich reagiere nur auf Nachrichten autorisierter Absender._  \n"
                    f"\nKommandos: `/new`, `/cancel`, `/status`, `/model <name>`, `/help`"
                )
                await client.send_message(room_id=self._room_id, markdown=greeting)
            except Exception as e:
                logger.warning("[webex-bot] Greeting fehlgeschlagen: %s", e)

        self._last_poll_ts = datetime.now(timezone.utc)
        self._running = True

        if settings.webex.bot.use_webhooks:
            # Webhook-Modus: Webhook bei Webex registrieren / aktualisieren
            try:
                await self.ensure_webhook()
                logger.info("[webex-bot] Webhook-Modus aktiv (webhook_id=%s)", self._webhook_id[:20])
            except Exception as e:
                logger.error(
                    "[webex-bot] Webhook-Registrierung fehlgeschlagen (%s) — fallback zu Polling",
                    e,
                )
                self._poll_task = asyncio.create_task(self._poll_loop())
        else:
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info(
                "[webex-bot] Fallback-Poller gestartet (Intervall: %ds)",
                settings.webex.bot.fallback_poll_seconds,
            )

        return self.get_status()

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

    async def _poll_loop(self) -> None:
        from app.core.config import settings
        interval = max(3, int(settings.webex.bot.fallback_poll_seconds or 10))

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
        from app.services.webex_client import get_webex_client

        if not self._room_id:
            return

        client = get_webex_client()
        since = self._last_poll_ts or (datetime.now(timezone.utc) - timedelta(minutes=5))
        # Zeitstempel VOR dem Poll updaten, um Race-Fenster klein zu halten
        poll_started_at = datetime.now(timezone.utc)

        try:
            messages = await client.get_new_messages_since(
                room_ids=[self._room_id],
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

        # Echo-Schutz: eigene Nachrichten ignorieren
        if msg.get("person_id") and self._me.get("id") and msg["person_id"] == self._me["id"]:
            return

        # Nur der konfigurierte Room
        if msg.get("room_id") != self._room_id:
            return

        # Idempotenz
        store = get_todo_store()
        process_key = f"{self.PROCESS_KEY_PREFIX}{self.VERSION_TAG}:{msg_id}"
        if store.is_processed(process_key):
            return

        # Owner-Allowlist
        allowed = [s.lower() for s in (settings.webex.bot.allowed_senders or [])]
        sender = (msg.get("person_email") or "").lower()
        if allowed and sender not in allowed:
            logger.info(
                "[webex-bot] Nachricht von nicht-autorisiertem Absender ignoriert: %s",
                sender or "(empty)",
            )
            store.mark_processed(process_key)
            return

        # Ab hier: legitime User-Msg — markiere VOR Verarbeitung (Crash-Safety)
        store.mark_processed(process_key)

        text = (msg.get("text") or "").strip()
        parent_id = msg.get("parent_id") or ""
        session_id = _session_id_for(self._room_id, parent_id)

        logger.info(
            "[webex-bot] IN [%s] %s: %s",
            sender or "?", session_id[-24:], text[:80].replace("\n", " "),
        )

        # Slash-Commands direkt behandeln — ohne Agent
        if text.startswith("/"):
            handled = await self._handle_slash_command(text, session_id, parent_id)
            if handled:
                return

        # Sonst: Agent-Run starten (nicht-blockierend)
        await self._start_agent_run(session_id, text, parent_id, msg)

    # ── Slash-Commands ───────────────────────────────────────────────────────

    async def _handle_slash_command(
        self, text: str, session_id: str, parent_id: str
    ) -> bool:
        """Gibt True zurueck wenn der Text als Command behandelt wurde."""
        from app.services.chat_store import save_chat, load_chat
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        async def reply(md: str) -> None:
            try:
                await client.send_message(
                    room_id=self._room_id, markdown=md, parent_id=parent_id,
                )
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
        self, session_id: str, text: str, parent_id: str, original_msg: Dict[str, Any],
    ) -> None:
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        client = get_webex_client()

        # Gleiche Session schon busy?
        existing = self._active_runs.get(session_id)
        if existing and not existing.done():
            try:
                await client.send_message(
                    room_id=self._room_id,
                    markdown="⏳ Ich bearbeite bereits eine Anfrage in dieser Session. `/cancel` zum Abbrechen.",
                    parent_id=parent_id,
                )
            except Exception:
                pass
            return

        # Concurrency-Cap (Threads zaehlen mit)
        max_rooms = max(1, int(settings.webex.bot.max_concurrent_rooms or 3))
        live = sum(1 for t in self._active_runs.values() if not t.done())
        if live >= max_rooms:
            try:
                await client.send_message(
                    room_id=self._room_id,
                    markdown=f"⚠️ Maximale Parallelitaet erreicht ({max_rooms}). Bitte warten.",
                    parent_id=parent_id,
                )
            except Exception:
                pass
            return

        # Daily-Token-Cap (Phase 4) — 0 bedeutet deaktiviert
        cap = int(settings.webex.bot.daily_token_cap or 0)
        if cap > 0:
            key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            used = int(self._daily_usage.get(key, 0))
            if used >= cap:
                try:
                    await client.send_message(
                        room_id=self._room_id,
                        markdown=(
                            f"🚦 Tageslimit erreicht: {used}/{cap} Tokens. "
                            f"Limit setzt um 00:00 UTC zurueck. "
                            f"(`webex.bot.daily_token_cap` in den Settings anpassbar.)"
                        ),
                        parent_id=parent_id,
                    )
                except Exception:
                    pass
                return

        # Typing-Indicator
        try:
            await client.send_message(
                room_id=self._room_id,
                markdown="⏳ _Agent arbeitet …_",
                parent_id=parent_id,
            )
        except Exception as e:
            logger.debug("[webex-bot] typing-reply failed: %s", e)

        task = asyncio.create_task(
            self._run_agent(session_id, text, parent_id, original_msg),
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
    ) -> None:
        from app.agent.orchestrator import get_agent_orchestrator
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        orchestrator = get_agent_orchestrator()

        # Modell bestimmen: /model > default_model > Orchestrator-Default
        model: Optional[str] = None
        per_session = getattr(self, "_per_session_model", {})
        if session_id in per_session:
            model = per_session[session_id]
        elif settings.webex.bot.default_model:
            model = settings.webex.bot.default_model

        # Final-Response aggregieren (Webex kein token-level Streaming)
        final_response_parts: List[str] = []
        error_msg: Optional[str] = None
        tool_count = 0

        # Bot-Kontext setzen damit Agent-Tools den aktiven Room kennen
        ctx_token = _webex_bot_ctx.set({
            "room_id": self._room_id,
            "parent_id": parent_id,
            "session_id": session_id,
        })

        try:
            attachments = await self._build_attachments_async(original_msg)
            if attachments:
                logger.info("[webex-bot] %d Bild-Attachment(s) angehaengt", len(attachments))

            gen = orchestrator.process(
                session_id=session_id,
                user_message=text,
                model=model,
                context_selection=None,
                attachments=attachments,
                tts=False,
            )

            async for event in gen:
                etype = getattr(event, "type", None)
                name = getattr(etype, "value", "") if etype is not None else ""

                if name == "token" and isinstance(event.data, str):
                    final_response_parts.append(event.data)
                elif name == "tool_start":
                    tool_count += 1
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
                    # Phase 1: Bestaetigungs-Flow nicht implementiert → freundlich ablehnen
                    error_msg = (
                        "Diese Anfrage erfordert eine Schreibbestaetigung (path_approval). "
                        "Phase 1 unterstuetzt das im Chat-Bot noch nicht — "
                        "bitte direkt in der Web-UI ausfuehren."
                    )
                    break

        except asyncio.CancelledError:
            logger.info("[webex-bot] Agent-Run cancelled: %s", session_id)
            try:
                await client.send_message(
                    room_id=self._room_id,
                    markdown="🛑 _Abgebrochen._",
                    parent_id=parent_id,
                )
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

        try:
            await client.send_message(
                room_id=self._room_id,
                markdown=body,
                parent_id=parent_id,
            )
        except Exception as e:
            logger.error("[webex-bot] Antwort-Post fehlgeschlagen: %s", e)
        finally:
            # Bot-Kontext freigeben (ContextVar wurde zu Beginn von _run_agent gesetzt)
            try:
                _webex_bot_ctx.reset(ctx_token)
            except (LookupError, ValueError):
                pass

    # ── Attachments fuer Vision-In (Phase 4) ─────────────────────────────────

    def _build_attachments(self, msg: Dict[str, Any]) -> Optional[List[dict]]:
        """Wandelt Bild-Attachments einer Webex-Msg in Orchestrator-Attachments.

        Synchron-Stub — der tatsaechliche Download passiert async via
        _build_attachments_async(). Diese Methode bleibt existent fuer Tests /
        einfache Flows, wird aber vom _run_agent ueberlagert.
        """
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
        if not self._room_id:
            raise RuntimeError("Bot-Room nicht resolved — start() vorher aufrufen.")

        client = get_webex_client()
        target_url = cfg.webhook_public_url.strip()
        expected_filter = f"roomId={self._room_id}"
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
        return self._webhook_id

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

        # Fruehe Filter ohne API-Call: fremder Room / eigene Msg
        if self._room_id and room_id and room_id != self._room_id:
            return
        if self._me.get("id") and actor_person_id and actor_person_id == self._me.get("id"):
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


# ── Singleton ────────────────────────────────────────────────────────────────

_handler: Optional[AssistRoomHandler] = None


def get_assist_room_handler() -> AssistRoomHandler:
    global _handler
    if _handler is None:
        _handler = AssistRoomHandler()
    return _handler
