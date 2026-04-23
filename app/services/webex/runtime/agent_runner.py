"""AgentRunner — Orchestrator-Run mit Streaming, Token-Accounting, Approvals.

Extrahiert aus ``AssistRoomHandler._start_agent_run`` + ``_run_agent``.
Haelt die ``_active_runs``-Map (session_id → Task) und die
Modul-Level-ContextVar ``_webex_bot_ctx``, die Agent-Tools lesen.

Run-Ablauf:
1. ``start()`` prueft Busy-Sessions, Concurrency-Cap, Daily-Token-Cap,
   postet optional Typing-Indicator und erzeugt den Task.
2. ``_run()`` laeuft als Task: waehlt Model, startet Editor (Status oder
   Lane), liest Attachments + Channel-Context, iteriert ueber den
   Orchestrator-Generator, behandelt ``confirm_required`` via
   ``ApprovalFlow``, aggregiert Tokens + Response, postet final via Editor.

``_webex_bot_ctx`` bleibt als Modul-Level-ContextVar, damit ``webex_tools``
den aktuellen Room/Session ohne expliziten Parameter findet.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.services.webex.conversation import WebexConversation
from app.services.webex.delivery import EditThrottle, LaneDeliverer, StatusEditor
from app.services.webex.runtime.approval_flow import ApprovalFlow
from app.services.webex.runtime.context import HandlerContext
from app.services.webex.runtime.context_builder import ChannelContextBuilder
from app.services.webex.safety import ErrorScope

logger = logging.getLogger(__name__)


# ── Tool-Context: aktueller Bot-Run ──────────────────────────────────────────
# Wird vom AgentRunner vor orchestrator.process() gesetzt und nach dem Run
# zurueckgesetzt. Agent-Tools koennen so den aktuellen Room / Thread erkennen
# ohne explizite Parameter. Ausserhalb eines Bot-Runs: None.
_webex_bot_ctx: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "webex_bot_ctx", default=None
)


def get_current_bot_context() -> Optional[Dict[str, str]]:
    """Gibt ``{'room_id','parent_id','session_id'}`` fuer den aktiven Bot-Run zurueck.

    Wird von Agent-Tools (``webex_reply``/``share_diagram``/``share_file``) genutzt,
    um den aktuellen Ziel-Room ohne expliziten Parameter zu finden. ``None``
    ausserhalb eines laufenden Bot-Agent-Runs.
    """
    try:
        return _webex_bot_ctx.get()
    except LookupError:
        return None


class AgentRunner:
    """Managed Agent-Runs fuer den Webex-Bot."""

    def __init__(
        self,
        context: HandlerContext,
        *,
        approval_flow: Optional[ApprovalFlow],
        context_builder: ChannelContextBuilder,
        track_sent: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._ctx = context
        self._approval_flow = approval_flow
        self._context_builder = context_builder
        self._track_sent = track_sent
        self._active_runs: Dict[str, asyncio.Task] = {}  # session_id → Task

    @property
    def active_runs(self) -> Dict[str, asyncio.Task]:
        """Map session_id → laufender Task. Fuer ``get_status`` und ``cancel``."""
        return self._active_runs

    async def cancel(self, session_id: str) -> bool:
        """Bricht den laufenden Agent-Run fuer diese Session ab."""
        task = self._active_runs.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            logger.info("[webex-bot] Agent-Run abgebrochen: %s", session_id)
            return True
        return False

    async def start(
        self,
        session_id: str,
        text: str,
        parent_id: str,
        original_msg: Dict[str, Any],
        *,
        target_room_id: str = "",
        conversation: Optional[WebexConversation] = None,
    ) -> None:
        """Prueft Caps, postet Typing-Indicator, erzeugt Task fuer ``_run()``."""
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        room_id = target_room_id or self._ctx.room_id

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
            if self._ctx.usage_store is not None:
                used = int(await self._ctx.usage_store.get_used(key))
            else:
                used = int(self._ctx.daily_usage.get(key, 0))
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
        # _run() direkt die Status-Message (die spaeter editiert wird).
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
            self._run(
                session_id, text, parent_id, original_msg,
                target_room_id=room_id,
                conversation=conversation,
            ),
            name=f"webex-bot:{session_id}",
        )
        self._active_runs[session_id] = task
        task.add_done_callback(lambda _t, sid=session_id: self._active_runs.pop(sid, None))

    async def _run(
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
        room_id = target_room_id or self._ctx.room_id

        # Modell bestimmen: /model > conv-policy > default_model > Orchestrator-Default
        model: Optional[str] = None
        if session_id in self._ctx.per_session_model:
            model = self._ctx.per_session_model[session_id]
        elif conversation is not None and conversation.policy.default_model:
            model = conversation.policy.default_model
        elif settings.webex.bot.default_model:
            model = settings.webex.bot.default_model

        # Final-Response aggregieren
        final_response_parts: List[str] = []
        error_msg: Optional[str] = None
        tool_count = 0
        last_tool_name: str = ""
        # Phase 4: gesammelte Tool-Namen fuer Collapse-Finalizer
        tool_history: List[str] = []

        # ── StatusEditor oder LaneDeliverer ──────────────────────────────
        editor: Any = None  # StatusEditor ODER LaneDeliverer (duck-typed)
        if settings.webex.bot.edit_in_place:
            async def _on_editor_new_msg(mid: str) -> None:
                await self._track_sent({"id": mid})

            if settings.webex.bot.lane_delivery:
                editor = LaneDeliverer(
                    client, room_id, parent_id,
                    on_new_message=_on_editor_new_msg,
                )
            else:
                editor = StatusEditor(
                    client, room_id, parent_id,
                    on_new_message=_on_editor_new_msg,
                )
            await editor.start("⏳ _Agent arbeitet …_")

        # ── Sprint 2: Token-Streaming Throttle ───────────────────────────
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
        if self._ctx.audit is not None:
            await self._ctx.audit.log(
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
            attachments = await self._context_builder.build_attachments(original_msg)
            if attachments:
                logger.info("[webex-bot] %d Bild-Attachment(s) angehaengt", len(attachments))

            channel_context = await self._context_builder.build_channel_context(original_msg)

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
                    if throttle is not None and editor is not None:
                        accumulated = "".join(final_response_parts)
                        if throttle.should_flush(len(accumulated)):
                            preview = accumulated[:stream_max] + " ▍"
                            await editor.update(preview, phase="streaming")
                elif name == "tool_start":
                    tool_count += 1
                    tool_name = ""
                    if isinstance(event.data, dict):
                        tool_name = str(event.data.get("name") or "")
                    last_tool_name = tool_name or last_tool_name or "?"
                    # Phase 4: in Tool-History aufnehmen (fuer Collapse-Finalizer)
                    if last_tool_name and last_tool_name != "?":
                        tool_history.append(last_tool_name)
                    if editor is not None:
                        await editor.update(
                            f"🔧 _Tool: {last_tool_name}_",
                            phase=f"tool:{last_tool_name}",
                        )
                        if self._ctx.audit is not None:
                            await self._ctx.audit.log(
                                "tool_call",
                                room_id=room_id,
                                session_id=session_id,
                                payload={"tool": last_tool_name},
                            )
                elif name == "mcp_complete" and editor is not None:
                    await editor.update("🧠 _Analyse …_", phase="thinking")
                elif name == "usage":
                    usage = event.data
                    total = 0
                    if hasattr(usage, "total_tokens"):
                        total = int(getattr(usage, "total_tokens", 0) or 0)
                    elif isinstance(usage, dict):
                        total = int(usage.get("total_tokens", 0) or 0)
                    if total:
                        key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        if self._ctx.usage_store is not None:
                            try:
                                await self._ctx.usage_store.add_tokens(key, total)
                            except Exception as e:
                                logger.debug("[webex-bot] usage_store.add failed: %s", e)
                                self._ctx.daily_usage[key] = self._ctx.daily_usage.get(key, 0) + total
                        else:
                            self._ctx.daily_usage[key] = self._ctx.daily_usage.get(key, 0) + total
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
                    if self._approval_flow is not None:
                        tool_name = ""
                        confirmation_data: Dict[str, Any] = {}
                        if isinstance(event.data, dict):
                            tool_name = str(event.data.get("name") or "")
                            confirmation_data = event.data.get("confirmation_data") or {}
                        approved, exec_output, exec_error = await self._approval_flow.run(
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

        # Optional Footer mit Tool-Nutzung — im Editor-Pfad uebernimmt
        # der Collapse-Finalizer die Anzeige (Phase 4). Fuer Legacy-Pfad
        # (ohne Editor, direktes client.send_message) bleibt der Inline-Footer.
        if tool_count and not error_msg and editor is None:
            body = f"{body}\n\n— _{tool_count} Tool-Call(s)_"

        # v5: Inline-Footer bei Idle-Reset (erste Antwort in neuer Generation).
        # Informiert User nicht-aufdringlich dass Kontext frisch ist.
        if conversation is not None and conversation.reset_pending and not error_msg:
            body = (
                f"{body}\n\n"
                "— _🆕 Neue Session (vorige >24h idle — `/continue` stellt sie wieder her)_"
            )
            if self._ctx.registry is not None:
                try:
                    await self._ctx.registry.acknowledge_reset(conversation.conv_key)
                except Exception as e:
                    logger.debug("[webex-bot] acknowledge_reset failed: %s", e)

        # ── Error-Policy-Gate (Sprint 1): Spam-Supression ────────────────
        should_post = True
        if error_msg and self._ctx.error_gate is not None:
            scope = ErrorScope(
                room_id=room_id,
                thread_id=parent_id or "",
                error_class="agent-error",
            )
            should_post = self._ctx.error_gate.should_post(scope)
            if not should_post:
                suppressed = self._ctx.error_gate.suppressed_count(scope)
                logger.info(
                    "[webex-bot] Fehler-Post unterdrueckt (policy=%s, suppressed=%d): %s",
                    self._ctx.error_gate.policy, suppressed, error_msg[:120],
                )

        try:
            if not should_post:
                if editor is not None:
                    await editor.delete()
            elif editor is not None:
                # Phase 4: Tool-History in Collapse-Summary reichen, damit der
                # Channel-Scroll "Frage → Antwort + (N Tools)" zeigt statt
                # der intermediaeren "🔧 tool: X"-Edits.
                await editor.finalize(body, tool_history=tool_history)
            else:
                result = await client.send_message(
                    room_id=room_id,
                    markdown=body,
                    parent_id=parent_id,
                )
                await self._track_sent(result)
            if self._ctx.audit is not None:
                if error_msg:
                    await self._ctx.audit.log(
                        "error",
                        room_id=room_id,
                        session_id=session_id,
                        payload={"error": error_msg[:500], "posted": should_post},
                    )
                elif should_post:
                    await self._ctx.audit.log(
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
            try:
                _webex_bot_ctx.reset(ctx_token)
            except (LookupError, ValueError):
                pass
