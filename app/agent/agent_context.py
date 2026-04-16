"""
Agent-Context: ContextVar fuer die aktuelle Session-ID.

Tool-Handler haben keinen direkten Zugriff auf die Session-ID. Statt jeden
Handler-Signature zu erweitern, nutzen wir contextvars (asyncio-safe).

Der Orchestrator und der confirm-Endpoint setzen die Session-ID, bevor sie
ein Tool aufrufen. Der Handler liest sie via current_session_id().
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_current_session_id: ContextVar[Optional[str]] = ContextVar(
    "current_session_id", default=None
)


def current_session_id() -> Optional[str]:
    """Liefert die aktuell aktive Session-ID, falls gesetzt."""
    return _current_session_id.get()


def set_current_session_id(session_id: Optional[str]):
    """Setzt die Session-ID fuer den aktuellen Async-Context.

    Returns ein Token zum spaeteren Reset. Praktisch nutzen wir es im
    contextmanager-Stil, aber Token-Reset ist optional - der Context
    endet eh am Coroutine-Ende.
    """
    return _current_session_id.set(session_id)


def reset_current_session_id(token) -> None:
    """Reset zur vorherigen Session-ID (fuer try/finally)."""
    _current_session_id.reset(token)
