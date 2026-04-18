"""
Adaptive Card Builder (Sprint 2, B2).

Erzeugt Webex-kompatible Adaptive Cards (Schema 1.3) fuer:
- Approval-Requests (Tool-Freigabe mit Erlauben/Ablehnen-Buttons)
- Result-Karten (Erfolg/Fehler nach Ausfuehrung)
- Error-Karten (Fehlermeldung mit Details)

Webex akzeptiert die Card im ``attachments``-Feld von POST /messages:
```
{
  "roomId": "...",
  "markdown": "fallback text",
  "attachments": [{
    "contentType": "application/vnd.microsoft.card.adaptive",
    "content": { ... AdaptiveCard JSON ... }
  }]
}
```

Button-Clicks generieren ein ``attachmentActions.created`` Webhook-Event;
die ``inputs``-Werte der Submit-Action kommen im Event an.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Adaptive Card Version (Webex unterstuetzt 1.3)
_ADAPTIVE_CARD_VERSION = "1.3"

# Webex Content-Type fuer Adaptive Cards
ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"


def _wrap(card: Dict[str, Any]) -> Dict[str, Any]:
    """Webex-attachment-Wrapper um einen AdaptiveCard-Body."""
    return {
        "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
        "content": card,
    }


def build_approval_card(
    request_id: str,
    tool_name: str,
    *,
    risk_level: str = "medium",
    description: str = "",
    args_summary: str = "",
    requester: str = "",
) -> Dict[str, Any]:
    """Baut eine Approval-Card mit "Erlauben"/"Ablehnen"-Buttons.

    Args:
        request_id: Eindeutige Request-ID (wird in submit-Data eingebettet).
        tool_name: Name des Tools das Approval benoetigt.
        risk_level: "low" | "medium" | "high" — faerbt die Buttons ein.
        description: Kurze Beschreibung der Operation.
        args_summary: Optional vorformatierter Args-Dump (max ~600 Zeichen).
        requester: Optional: Email/Name des urspruenglichen Requesters.

    Returns:
        Webex-attachment-Dict, direkt als attachments[0] verwendbar.
    """
    body: list[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "🔐 Tool-Freigabe erforderlich",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Attention" if risk_level == "high" else "Default",
        },
    ]

    facts: list[Dict[str, Any]] = [
        {"title": "Tool:", "value": tool_name},
        {"title": "Risiko:", "value": (risk_level or "medium").upper()},
    ]
    if requester:
        facts.append({"title": "Angefordert von:", "value": requester})

    body.append({"type": "FactSet", "facts": facts})

    if description:
        body.append({
            "type": "TextBlock",
            "text": description[:500],
            "wrap": True,
            "spacing": "Medium",
        })

    if args_summary:
        body.append({
            "type": "TextBlock",
            "text": args_summary[:600],
            "wrap": True,
            "fontType": "Monospace",
            "size": "Small",
            "spacing": "Small",
        })

    approve_style = "positive" if risk_level != "high" else "destructive"
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
        "actions": [
            {
                "type": "Action.Submit",
                "title": "✓ Erlauben",
                "style": approve_style,
                "data": {"action": "approve", "rid": request_id},
            },
            {
                "type": "Action.Submit",
                "title": "✗ Ablehnen",
                "style": "destructive",
                "data": {"action": "reject", "rid": request_id},
            },
        ],
    }
    return _wrap(card)


def build_result_card(
    title: str,
    *,
    summary: str = "",
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Baut eine Result-Card fuer Tool-Ausfuehrungsergebnisse."""
    icon = "✅" if success else "❌"
    color = "Good" if success else "Attention"

    body: list[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"{icon} {title}",
            "weight": "Bolder",
            "size": "Medium",
            "color": color,
        },
    ]
    if summary:
        body.append({
            "type": "TextBlock",
            "text": summary[:800],
            "wrap": True,
            "spacing": "Medium",
        })
    if details:
        facts = [
            {"title": f"{k}:", "value": str(v)[:200]}
            for k, v in list(details.items())[:10]
        ]
        body.append({"type": "FactSet", "facts": facts})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    return _wrap(card)


def build_error_card(message: str, *, details: str = "") -> Dict[str, Any]:
    """Baut eine Fehler-Card (keine Buttons)."""
    body: list[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "⚠️ Fehler",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Attention",
        },
        {
            "type": "TextBlock",
            "text": message[:500],
            "wrap": True,
            "spacing": "Medium",
        },
    ]
    if details:
        body.append({
            "type": "TextBlock",
            "text": details[:800],
            "wrap": True,
            "fontType": "Monospace",
            "size": "Small",
            "spacing": "Small",
        })
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    return _wrap(card)
