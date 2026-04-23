"""WebhookRegistrar — Webex-Webhook-Registrierung + Signatur-Verifikation.

Extrahiert aus ``AssistRoomHandler`` (H3): ``ensure_webhook``,
``_ensure_actions_webhook``, ``remove_webhook``, ``verify_signature``.

Der Registrar kennt nur ``HandlerContext`` (fuer me/room_id/room_overrides
und ``approval_bus`` um zu entscheiden ob der zweite Webhook fuer
``attachmentActions`` registriert werden muss). Webhook-IDs werden im
Registrar selbst gehalten — der Handler fragt ueber die ``messages_webhook_id``
/ ``actions_webhook_id`` Properties ab.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from app.services.webex.runtime.context import HandlerContext

logger = logging.getLogger(__name__)


class WebhookRegistrar:
    """Stellt passende Webex-Webhooks sicher und raeumt Duplikate auf."""

    def __init__(self, context: HandlerContext) -> None:
        self._ctx = context
        self._messages_webhook_id: str = ""
        self._actions_webhook_id: str = ""

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def messages_webhook_id(self) -> str:
        return self._messages_webhook_id

    @messages_webhook_id.setter
    def messages_webhook_id(self, value: str) -> None:
        self._messages_webhook_id = value

    @property
    def actions_webhook_id(self) -> str:
        return self._actions_webhook_id

    # ── Public API ──────────────────────────────────────────────────────

    async def ensure(self) -> str:
        """Stellt sicher, dass genau EIN passender Webhook bei Webex registriert ist.

        - Sucht existierende Webhooks mit gleichem Namen/targetUrl
        - Loescht Duplikate oder Webhooks mit falscher URL
        - Erstellt neuen Webhook wenn keiner passt
        - Setzt Filter auf ``roomId={bot_room_id}`` im Single-Room-Modus.
          In Multi-Conv-Mode leerer Filter (Bot sieht nur Rooms wo Mitglied).
        """
        from app.core.config import settings
        from app.services.webex_client import get_webex_client

        cfg = settings.webex.bot
        if not cfg.webhook_public_url:
            raise RuntimeError(
                "webex.bot.webhook_public_url ist leer — bitte oeffentliche HTTPS-URL eintragen."
            )
        if not cfg.multi_conversation and not self._ctx.room_id:
            raise RuntimeError("Bot-Room nicht resolved — start() vorher aufrufen.")

        client = get_webex_client()
        target_url = cfg.webhook_public_url.strip()
        expected_filter = "" if cfg.multi_conversation else f"roomId={self._ctx.room_id}"
        expected_name = cfg.webhook_name or "ai-assist-bot"

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
                    continue
                try:
                    await client.delete_webhook(hook.get("id", ""))
                    logger.info("[webex-bot] Duplicate-Webhook entfernt: %s", hook.get("id", "")[:20])
                except Exception as e:
                    logger.warning("[webex-bot] Duplicate delete failed: %s", e)
            elif same_name or (same_target and not same_filter):
                try:
                    await client.delete_webhook(hook.get("id", ""))
                    logger.info(
                        "[webex-bot] Alter Webhook entfernt: %s (name=%s, url=%s)",
                        hook.get("id", "")[:20], hook.get("name", ""), hook.get("targetUrl", ""),
                    )
                except Exception as e:
                    logger.warning("[webex-bot] Cleanup delete failed: %s", e)

        if keep_id:
            self._messages_webhook_id = keep_id
            return keep_id

        created = await client.register_webhook(
            name=expected_name,
            target_url=target_url,
            resource="messages",
            event="created",
            filter=expected_filter,
            secret=cfg.webhook_secret or "",
        )
        self._messages_webhook_id = created.get("id", "")
        logger.info(
            "[webex-bot] Webhook registriert: id=%s url=%s filter=%s secret=%s",
            self._messages_webhook_id[:20],
            target_url,
            expected_filter,
            "yes" if cfg.webhook_secret else "no",
        )

        # Sprint 2: Zweiten Webhook fuer attachmentActions registrieren
        # (nur wenn approvals aktiviert)
        if self._ctx.approval_bus is not None:
            try:
                await self._ensure_actions_webhook(client, cfg, target_url)
            except Exception as e:
                logger.warning(
                    "[webex-bot] actions-Webhook-Registrierung fehlgeschlagen: %s", e,
                )

        return self._messages_webhook_id

    async def remove(self) -> bool:
        """Entfernt den aktuell registrierten Webhook."""
        from app.services.webex_client import get_webex_client

        if not self._messages_webhook_id:
            return False
        try:
            await get_webex_client().delete_webhook(self._messages_webhook_id)
            logger.info("[webex-bot] Webhook entfernt: %s", self._messages_webhook_id[:20])
            self._messages_webhook_id = ""
            return True
        except Exception as e:
            logger.warning("[webex-bot] Webhook-Deletion fehlgeschlagen: %s", e)
            return False

    # ── Interne Helpers ─────────────────────────────────────────────────

    async def _ensure_actions_webhook(self, client, cfg, messages_target_url: str) -> str:
        """Registriert/deduped den Webhook fuer ``attachmentActions.created``.

        Target-URL wird aus ``actions_webhook_public_url`` (falls gesetzt) oder
        durch Ersetzen des Pfad-Suffix in ``webhook_public_url`` abgeleitet.
        """
        actions_url = (getattr(cfg, "actions_webhook_public_url", "") or "").strip()
        if not actions_url:
            if "/webhooks/" in messages_target_url:
                base = messages_target_url.rsplit("/webhooks/", 1)[0]
                actions_url = f"{base}/webhooks/attachment-actions"
            else:
                logger.warning(
                    "[webex-bot] actions_webhook_public_url leer und webhook_public_url hat kein /webhooks/-Segment"
                )
                return ""

        expected_name = getattr(cfg, "actions_webhook_name", "") or "ai-assist-bot-actions"
        expected_filter = "" if cfg.multi_conversation else f"roomId={self._ctx.room_id}"

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

    # ── Signatur-Verifikation ───────────────────────────────────────────

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
