"""Context-Compactor — Stufe 1 (Elision) fuer Multi-Turn-History.

Hintergrund (/sc:brainstorm 2026-04-22):
- GPT-OSS 120B (128k Kontext) ist self-hosted → jede Token-Reduktion ist
  direkte Latenz-Einsparung, nicht nur Kostensenkung.
- Stufe-1-Compaction ist **kein LLM-Call** — reine String-Manipulation,
  darum immer aktiv.
- Stufe-2 (LLM-Summary) ist optional und wuerde bei 70% Window-Fuellung
  greifen — bei 128k Window quasi nie relevant. Nicht implementiert.

Was Stufe 1 tut:
1. Tool-Outputs (``role="tool"``) in allen Messages **ausser den letzten N**
   werden auf erste 500 + letzte 500 Zeichen kondensiert.
2. Assistant-Zwischenzustaende (leere/kurze ``content`` aelterer Turns) bleiben.
3. System-Prompts und User-Messages werden NICHT angetastet.

Aufruf-Kontrakt: idempotent + side-effect-frei. Nimmt Liste von Dicts,
gibt neue Liste von Dicts zurueck (flache Kopie + modifizierte Tool-Nodes).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# Default: erste + letzte 500 Zeichen behalten. Grenze so, dass das
# Gesamt-Output noch nach "Tool-Output" aussieht (fuer LLM-Lesbarkeit).
DEFAULT_ELISION_HEAD = 500
DEFAULT_ELISION_TAIL = 500

# Wieviele Turns am Ende werden NICHT elidiert. Ein "Turn" = user+assistant-Paar;
# wir zaehlen Messages statt Turns, um role="tool" ebenfalls zu beruecksichtigen.
DEFAULT_KEEP_RECENT_MESSAGES = 8

# Marker der im elidierten Content eingefuegt wird — dient als
# Idempotenz-Sentinel: zweiter Durchlauf erkennt bereits-kompaktierte
# Messages und laesst sie unveraendert.
_ELISION_MARKER = "Zeichen elidiert (Compaction Stufe 1)"


def elide_tool_outputs(
    messages: List[Dict[str, Any]],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_MESSAGES,
    head_chars: int = DEFAULT_ELISION_HEAD,
    tail_chars: int = DEFAULT_ELISION_TAIL,
) -> List[Dict[str, Any]]:
    """Kondensiert lange Tool-Output-Contents in alten Messages.

    Args:
        messages: Conversation-History als Liste von ``{"role": ..., "content": ...}``.
        keep_recent: Wieviele Messages am Ende unangetastet bleiben.
        head_chars: Zeichen vom Anfang des Tool-Outputs behalten.
        tail_chars: Zeichen vom Ende des Tool-Outputs behalten.

    Returns:
        Neue Liste, wobei Tool-Messages ausserhalb des ``keep_recent``-
        Fensters auf ``head + "[…{N} Zeichen elidiert…]" + tail``
        komprimiert werden. Messages die bereits kuerzer als
        ``head+tail+marker`` sind, bleiben unveraendert (kein Verlust).
    """
    if not messages or keep_recent <= 0:
        return list(messages)

    total = len(messages)
    split_idx = max(0, total - keep_recent)
    if split_idx == 0:
        # Alles "recent" → nichts zu elidieren
        return list(messages)

    out: List[Dict[str, Any]] = []
    min_length = head_chars + tail_chars + 32  # Marker-Overhead einrechnen
    elided_count = 0

    for idx, msg in enumerate(messages):
        if idx >= split_idx:
            # Recent window — beibehalten
            out.append(msg)
            continue
        if msg.get("role") != "tool":
            # Non-tool old messages bleiben wie sie sind (system/user/assistant
            # kann wichtigen Kontext halten, den wir nicht erraten koennen).
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= min_length:
            out.append(msg)
            continue
        # Idempotenz: bereits elidierte Messages nicht nochmal kondensieren
        # (sonst schrumpft die Message bei jedem Durchlauf weiter).
        if _ELISION_MARKER in content:
            out.append(msg)
            continue
        # Elision: head … [N elidiert] … tail
        elided_bytes = len(content) - head_chars - tail_chars
        new_content = (
            content[:head_chars]
            + f"\n\n[… {elided_bytes} {_ELISION_MARKER} …]\n\n"
            + content[-tail_chars:]
        )
        # Flache Kopie — Original nicht mutieren
        new_msg = dict(msg)
        new_msg["content"] = new_content
        out.append(new_msg)
        elided_count += 1

    if elided_count > 0:
        logger.debug(
            "[context-compactor] %d tool-outputs elidiert (%d/%d recent, %d behalten)",
            elided_count, keep_recent, total, total - elided_count,
        )
    return out


def count_chars(messages: List[Dict[str, Any]]) -> int:
    """Summe der ``content``-Zeichen ueber alle Messages (fuer Debug/Metrics)."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
    return total
