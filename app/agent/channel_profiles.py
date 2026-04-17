"""
Channel-Profile fuer kanalspezifische Antwort-Stile.

Wird vom Orchestrator genutzt, wenn eine Anfrage nicht aus der Web-UI kommt,
sondern aus einem Chat-Kanal (Webex, spaeter Teams/Slack). Jedes Profil liefert:

- einen Style-Block (wie soll die LLM antworten)
- einen Context-Renderer (wie wird Chat-Verlauf in den System-Prompt eingebettet)

Beim Aufruf von ``orchestrator.process(channel_hint="webex", channel_context=...)``
wird das passende Profil aus der Registry geladen und an den System-Prompt
angehaengt. Ist ``channel_hint`` None oder unbekannt, ist das Modul ein No-Op —
die Web-UI bleibt unbeeinflusst.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Value Objects ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChannelMessage:
    """Eine einzelne Nachricht im Chat-Verlauf (kanalagnostisch)."""
    author: str
    text: str
    timestamp: datetime
    is_bot: bool = False
    is_reply: bool = False


@dataclass(frozen=True)
class ChannelContext:
    """Chat-Verlauf + Meta zum aktuellen Bot-Trigger.

    ``messages`` ist AELTESTE zuerst, NEUESTE am Ende. Die Trigger-Nachricht
    selbst ist NICHT enthalten — die wird als regulaere ``user_message`` an
    den Orchestrator uebergeben.
    """
    channel: str
    room_type: str = ""          # "direct" | "group"
    room_title: str = ""
    thread_parent_id: str = ""
    messages: List[ChannelMessage] = field(default_factory=list)
    trigger_author: str = ""


# ── Profile ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChannelProfile:
    key: str
    style_template: str
    context_intro: str
    default_params: Dict[str, Any] = field(default_factory=dict)


_WEBEX_STYLE = """## Antwort-Stil: Webex-Chat

Du beantwortest eine Nachricht in einem Webex-Chat-Space — oft gelesen auf
dem Handy. Dein Antwortstil unterscheidet sich von der Web-UI:

- **Kurz halten**: Zielwert ~{target_chars} Zeichen, moeglichst in
  1-3 Absaetzen. Lange Ausfuehrungen nur, wenn explizit verlangt.
- **Keine H1/H2-Ueberschriften** (``# Titel``, ``## Titel``). Fetter Text
  und Listen sind ok. {tables_rule}
- **Code sparsam**: Einzeiler inline (``code``). Fences nur bei echten
  Mehrzeilern und maximal ~10 Zeilen.
- **Kein Preamble**: Keine Einleitungs-Floskeln („Gerne helfe ich ..."),
  direkt zur Antwort.
- **Sichtbarkeits-Luecke**: In Gruppen-Spaces siehst du nur Nachrichten,
  die dich erwaehnen (@mention) oder Thread-Replies auf deine Posts.
  Fehlt dir Kontext, frage lieber kurz zurueck, statt zu raten.
"""

_WEBEX_CONTEXT_INTRO = """## Bisheriger Chatverlauf

Dies sind die letzten Nachrichten aus dem aktuellen Webex-{scope}.
Sie dienen als KONTEXT — sortiert von alt nach neu. Je weiter unten,
desto relevanter. Die **aktuelle User-Frage** bekommst du als separate
User-Message und hat HOECHSTE Prioritaet.
"""


WEBEX_PROFILE = ChannelProfile(
    key="webex",
    style_template=_WEBEX_STYLE,
    context_intro=_WEBEX_CONTEXT_INTRO,
    default_params={
        "target_chars": 600,
        "allow_tables": False,
        "max_history": 10,
        "max_chars_per_message": 500,
    },
)


CHANNEL_PROFILES: Dict[str, ChannelProfile] = {
    "webex": WEBEX_PROFILE,
}


# ── Rendering ────────────────────────────────────────────────────────────────

def _render_style(profile: ChannelProfile, params: Dict[str, Any]) -> str:
    merged = {**profile.default_params, **(params or {})}
    tables_rule = (
        "Tabellen nur wenn der User explizit eine Tabelle verlangt."
        if not merged.get("allow_tables")
        else "Tabellen sind erlaubt, aber sparsam einsetzen."
    )
    return profile.style_template.format(
        target_chars=int(merged.get("target_chars", 600)),
        tables_rule=tables_rule,
    )


def _render_context(profile: ChannelProfile, ctx: ChannelContext) -> str:
    if not ctx.messages:
        return ""
    scope = "Thread" if ctx.thread_parent_id else "Space"
    intro = profile.context_intro.format(scope=scope)
    lines: List[str] = [intro]
    for i, msg in enumerate(ctx.messages):
        is_last = i == len(ctx.messages) - 1
        ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
        tag = "Bot" if msg.is_bot else msg.author
        marker = "  <- direkt vor der aktuellen Frage" if is_last else ""
        lines.append(f"[{ts} | {tag}] {msg.text}{marker}")
    lines.append("— ENDE VERLAUF —")
    return "\n".join(lines)


def build_channel_prompt(
    hint: Optional[str],
    ctx: Optional[ChannelContext] = None,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Baut den kanalspezifischen Zusatz-System-Prompt.

    Gibt '' zurueck wenn ``hint`` None oder ein unbekannter Key ist, sodass
    der Orchestrator einfach ``system_prompt += build_channel_prompt(...)``
    aufrufen kann ohne If-Check.
    """
    if not hint:
        return ""
    profile = CHANNEL_PROFILES.get(hint)
    if profile is None:
        logger.warning("[channel] Unbekannter channel_hint '%s' — ignoriert", hint)
        return ""

    parts: List[str] = [_render_style(profile, params or {})]
    if ctx is not None:
        ctx_block = _render_context(profile, ctx)
        if ctx_block:
            parts.append(ctx_block)
    return "\n\n".join(parts)
