"""ApprovalFlow — Adaptive-Card-Approval-Workflow (Sprint 2, extrahiert in H3).

Flow:
  1. Risk-Level / Description / Args-Summary aus ``confirmation_data`` ableiten
  2. ``ApprovalBus.create_pending(requester_email=...)`` (C1: Auth gebunden)
  3. Adaptive Card posten (Erlauben/Ablehnen-Buttons)
  4. Editor auf ``awaiting-approval`` stellen
  5. ``wait_for_decision()`` bis User klickt oder Timeout
  6. Card loeschen (Buttons duerfen nicht klickbar bleiben)
  7. Bei ``approved``: Multi-Phase-Loop ueber ``orchestrator._execute_confirmed_operation``

Cascade-Policy: Max ``MAX_APPROVAL_PHASES`` rekursive Phasen, dann Abbruch.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from app.services.webex.interactive import (
    ApprovalBus,
    ApprovalTimeout,
    build_approval_card,
)
from app.services.webex.audit import AuditLogger

logger = logging.getLogger(__name__)


class ApprovalFlow:
    """Fuehrt den Approval-Workflow fuer eine bestaetigungspflichtige Operation durch."""

    MAX_APPROVAL_PHASES = 3  # Sicherheits-Cap gegen Cascading-Approvals

    def __init__(
        self,
        *,
        approval_bus: ApprovalBus,
        audit: Optional[AuditLogger],
        track_sent: Callable[[Dict[str, Any]], Awaitable[None]],
        default_room_id_fn: Callable[[], str],
    ) -> None:
        self._bus = approval_bus
        self._audit = audit
        self._track_sent = track_sent
        self._default_room_id = default_room_id_fn

    async def run(
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
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Fuehrt den Approval-Workflow durch.

        Returns:
            Tupel ``(approved, output, error)``:
              - ``approved=True`` + ``output=str`` → erfolgreiche Ausfuehrung
              - ``approved=True`` + ``error=str`` → Ausfuehrung gescheitert
              - ``approved=False`` + ``error=None`` → rejected/timeout ohne Fehler
              - ``approved=False`` + ``error=str`` → Fehler im Approval-Setup
        """
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        client = get_webex_client()
        room_id = target_room_id or self._default_room_id()
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
            rid = await self._bus.create_pending(
                session_id=session_id,
                room_id=room_id,
                parent_id=parent_id,
                tool_name=tool_name or "unknown",
                tool_args=confirmation_data,
                confirmation_data=confirmation_data,
                requester_email=sender_email,
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
                await self._bus.set_card_message_id(rid, card_msg_id)

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
                    await self._bus.cancel(rid)
                except Exception:
                    pass
            return (False, None, f"Approval-Setup fehlgeschlagen: {e}")

        # 2. Auf Entscheidung warten
        timeout = float(settings.webex.bot.approvals.timeout_seconds)
        try:
            decision = await self._bus.wait_for_decision(rid, timeout_seconds=timeout)
        except ApprovalTimeout:
            logger.info("[webex-bot] approval timeout rid=%s", rid)
            await self._cleanup_card(card_msg_id)
            return (False, None, None)
        except Exception as e:
            logger.warning("[webex-bot] approval wait failed: %s", e)
            await self._cleanup_card(card_msg_id)
            return (False, None, f"Approval-Wait-Error: {e}")

        # 3. Card aufraeumen
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
            approved2, out2, err2 = await self.run(
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

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _cleanup_card(self, card_message_id: str) -> None:
        """Loescht die Approval-Card nach Entscheidung."""
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
        lines: list[str] = []
        for k in interesting:
            v = confirmation_data.get(k)
            if v:
                lines.append(f"{k}: {str(v)[:120]}")
        return "\n".join(lines[:8])
