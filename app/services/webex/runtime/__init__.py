"""Webex Chat-Bot Runtime-Komponenten (Sprint 4, H3-Refactor).

Dieses Package zerlegt die ehemalige God-Class ``AssistRoomHandler`` in
kleinere, klar verantwortliche Komponenten. Der ``AssistRoomHandler``
bleibt als Facade und koordiniert die Komponenten ueber einen gemeinsamen
``HandlerContext``.

Komponenten:
- ``HandlerContext``: Shared State (Stores, Identity, Room-Config)
- ``WebhookRegistrar``: Webex-Webhook-Registrierung (messages + attachmentActions)
- ``SlashCommandRouter``: Behandlung aller ``/``-Kommandos
- ``ChannelContextBuilder``: Chat-Verlauf + Bild-Attachments fuer Orchestrator
- ``ApprovalFlow``: Adaptive-Card-Approval-Workflow (Multi-Phase)
- ``AgentRunner``: Orchestrator-Run + Streaming + Token-Accounting
- ``MessageDispatcher``: Ingress-Filter + Dedup + Routing
- ``PollingLoop``: Fallback-/Safety-Polling
"""

from app.services.webex.runtime.context import HandlerContext
from app.services.webex.runtime.webhooks import WebhookRegistrar
from app.services.webex.runtime.slash_commands import SlashCommandRouter
from app.services.webex.runtime.context_builder import ChannelContextBuilder
from app.services.webex.runtime.approval_flow import ApprovalFlow
from app.services.webex.runtime.agent_runner import AgentRunner
from app.services.webex.runtime.dispatcher import MessageDispatcher
from app.services.webex.runtime.polling import PollingLoop

__all__ = [
    "HandlerContext",
    "WebhookRegistrar",
    "SlashCommandRouter",
    "ChannelContextBuilder",
    "ApprovalFlow",
    "AgentRunner",
    "MessageDispatcher",
    "PollingLoop",
]
