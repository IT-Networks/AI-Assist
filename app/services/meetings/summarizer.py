"""MeetingSummarizer — GPT-OSS 120B kondensiert ein TranscriptBundle.

Ein einziger LLM-Call (kein Agent-Loop, keine Tools). Prompt produziert
JSON mit Feldern: title, summary, decisions, action_items. Das ist
robuster als Freiform-Markdown-Parsing und macht die Struktur testbar.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from app.services.meetings.models import MeetingSummary, TranscriptBundle

logger = logging.getLogger(__name__)


# Harter Token-Budget-Cap fuer den Prompt-Input. GPT-OSS 120B hat 128k,
# aber wir wollen Latenz klein halten und lassen Raum fuer die Antwort.
MAX_TRANSCRIPT_CHARS = 80_000  # ~ 20k tokens @ 4 chars/token

SYSTEM_PROMPT_DE = """\
Du bist ein praegnanter Meeting-Zusammenfasser. Lies das uebergebene
Transkript und antworte in reinem JSON (kein Markdown-Code-Fence):

{
  "title": "kurzer Titel (max 80 Zeichen)",
  "summary": "2-5 Bullet-Points als Markdown, jeweils mit '- ' beginnend",
  "decisions": ["Entscheidung 1", "..."],
  "action_items": ["Wer macht was bis wann (sofern erkennbar)", "..."]
}

Regeln:
- Kein Geschwafel. Keine Wiederholung.
- Nur Fakten aus dem Transkript — kein Halluzinieren von Namen/Daten.
- Wenn keine Entscheidungen/Action-Items: leere Arrays zurueckgeben.
- Deutsch, es sei denn das Transkript ist komplett englisch.
- JSON-Keys exakt wie oben, keine weiteren Felder.\
"""


class MeetingSummarizer:
    """Erzeugt ``MeetingSummary`` aus einem ``TranscriptBundle`` via LLM."""

    def __init__(self, *, model: Optional[str] = None) -> None:
        # None = default model des llm_client (config-gesteuert)
        self._model = model

    async def summarize(self, bundle: TranscriptBundle) -> MeetingSummary:
        """LLM-Call fuer Summary + Action-Items + Decisions.

        Fallback bei LLM-Fehler: Ein ``MeetingSummary`` mit Fehlerhinweis-
        Summary aber intakten Transkript-Daten (sodass User die VTT
        immer noch anschauen kann).
        """
        transcript_text = bundle.plain_text(speaker_prefix=True)
        if len(transcript_text) > MAX_TRANSCRIPT_CHARS:
            # Head + Tail behalten, Mitte elidieren — weil vorne oft
            # Kontext/Agenda, hinten oft Decisions/Action-Items steht.
            keep_each = MAX_TRANSCRIPT_CHARS // 2 - 100
            elided = len(transcript_text) - 2 * keep_each
            transcript_text = (
                transcript_text[:keep_each]
                + f"\n\n[... {elided} Zeichen gekuerzt ...]\n\n"
                + transcript_text[-keep_each:]
            )

        user_prompt = (
            f"Meeting-Start: {bundle.started_at_utc.isoformat()}\n"
            f"Dauer: {int(bundle.duration_seconds // 60)} Minuten\n"
            f"Sprecher: {', '.join(bundle.participants) if bundle.participants else 'unbekannt'}\n"
            f"Transkript-Quelle: {bundle.source}\n\n"
            f"--- TRANSKRIPT ---\n{transcript_text}"
        )

        try:
            from app.services.llm_client import llm_client
            raw = await llm_client.chat_quick(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_DE},
                    {"role": "user", "content": user_prompt},
                ],
                model=self._model,
                temperature=0.2,
                max_tokens=1500,
            )
        except Exception as e:
            logger.error("[meeting-summarizer] LLM-Call fehlgeschlagen: %s", e)
            return _fallback_summary(bundle, reason=str(e))

        parsed = _parse_llm_json(raw)
        if parsed is None:
            logger.warning(
                "[meeting-summarizer] JSON-Parse fehlgeschlagen, raw=%r",
                raw[:200],
            )
            return _fallback_summary(bundle, reason="JSON-Parse-Fehler")

        return MeetingSummary(
            bundle=bundle,
            title=str(parsed.get("title") or _default_title(bundle))[:100],
            summary_markdown=str(parsed.get("summary") or "_(Keine Zusammenfassung)_").strip(),
            action_items=[str(a).strip() for a in (parsed.get("action_items") or []) if a],
            key_decisions=[str(d).strip() for d in (parsed.get("decisions") or []) if d],
            generated_at_utc=datetime.now(timezone.utc),
            model_used=self._model or "default",
        )


def _default_title(bundle: TranscriptBundle) -> str:
    started = bundle.started_at_utc.strftime("%Y-%m-%d %H:%M")
    return f"Meeting {started}"


def _fallback_summary(bundle: TranscriptBundle, *, reason: str) -> MeetingSummary:
    return MeetingSummary(
        bundle=bundle,
        title=_default_title(bundle),
        summary_markdown=(
            "_(Automatische Zusammenfassung fehlgeschlagen — Transkript liegt "
            f"aber vor. Grund: {reason})_"
        ),
        action_items=[],
        key_decisions=[],
        model_used="fallback",
    )


_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Extrahiert JSON-Dict aus LLM-Antwort.

    Tolerant gegenueber umliegenden Code-Fences (```json ... ```) oder
    Einleitungstext ("Hier das JSON: { ... }"). Scheitert still bei
    kaputtem JSON — Caller fallbacked auf Fehler-Summary.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    # Erst Code-Fence versuchen
    m = _JSON_FENCE_PATTERN.search(raw)
    if m:
        raw = m.group(1)
    else:
        # Sonst: erster { bis letzter }
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            return None
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return None
    return None
