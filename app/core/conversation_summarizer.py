"""
Conversation Summarizer - Fasst ältere Konversationsteile zusammen.

Strategie (wie Claude Code):
1. Behalte letzte N Nachrichten unverändert
2. Fasse ältere Nachrichten zu einem Summary zusammen
3. Summary wird als System-Message eingefügt
"""

from typing import Dict, List, Optional
import httpx

from app.core.config import settings
from app.utils.token_counter import estimate_tokens, estimate_messages_tokens


class ConversationSummarizer:
    """
    Fasst ältere Konversationsteile zusammen um Token zu sparen.

    Behält die letzten N Messages und fasst den Rest zusammen.
    """

    # Anzahl der Messages die immer behalten werden
    KEEP_RECENT_MESSAGES = 6  # 3 User + 3 Assistant Turns

    # Minimale Anzahl Messages bevor Summary erstellt wird
    MIN_MESSAGES_FOR_SUMMARY = 8

    # Max Tokens für Summary
    MAX_SUMMARY_TOKENS = 800

    SUMMARY_PROMPT = """Fasse diese Konversation prägnant zusammen.

WICHTIG - Behalte:
- Getroffene Entscheidungen
- Genannte Dateien, Klassen, Tabellen
- Offene Aufgaben oder Probleme
- Wichtige technische Details

FORMAT:
- Stichpunkte
- Maximal 5-7 Punkte
- Keine Wiederholungen

KONVERSATION:
{conversation}

ZUSAMMENFASSUNG:"""

    async def summarize_if_needed(
        self,
        messages: List[Dict],
        target_tokens: int,
        force: bool = False
    ) -> List[Dict]:
        """
        Prüft ob Summary nötig und erstellt sie.

        Args:
            messages: Aktuelle Message-Liste
            target_tokens: Ziel-Token-Budget
            force: Erzwinge Summary auch wenn nicht nötig

        Returns:
            Neue Message-Liste (ggf. mit Summary)
        """
        current_tokens = estimate_messages_tokens(messages)

        # Prüfe ob Summary nötig
        if not force and current_tokens <= target_tokens:
            return messages

        # Mindestanzahl Messages für Summary
        if len(messages) < self.MIN_MESSAGES_FOR_SUMMARY:
            return messages

        # Finde System-Messages und trenne sie
        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Prüfe ob genug non-system Messages
        if len(non_system) <= self.KEEP_RECENT_MESSAGES:
            return messages

        # Teile in zu-summarisieren und zu-behalten
        to_summarize = non_system[:-self.KEEP_RECENT_MESSAGES]
        to_keep = non_system[-self.KEEP_RECENT_MESSAGES:]

        # Erstelle Summary
        summary = await self._create_summary(to_summarize)

        if not summary:
            return messages

        # Baue neue Message-Liste
        result = []

        # System-Messages zuerst
        result.extend(system_messages)

        # Summary als System-Message
        result.append({
            "role": "system",
            "content": f"=== ZUSAMMENFASSUNG BISHERIGER KONVERSATION ===\n{summary}\n=== ENDE ZUSAMMENFASSUNG ==="
        })

        # Neueste Messages behalten
        result.extend(to_keep)

        return result

    async def _create_summary(self, messages: List[Dict]) -> Optional[str]:
        """
        Erstellt Summary via LLM (kleines/schnelles Modell).

        Returns:
            Summary-Text oder None bei Fehler
        """
        # Konversation formatieren
        conversation_parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")[:1500]  # Max 1500 Zeichen pro Message
            conversation_parts.append(f"{role}: {content}")

        conversation_text = "\n\n".join(conversation_parts)

        # LLM aufrufen (kleines Modell für Geschwindigkeit)
        model = settings.llm.tool_model or settings.llm.default_model

        try:
            summary = await self._call_llm_for_summary(
                conversation_text,
                model
            )
            return summary
        except Exception as e:
            print(f"[summarizer] Fehler bei Summary-Erstellung: {e}")
            # Fallback: Einfache Kürzung
            return self._create_simple_summary(messages)

    async def _call_llm_for_summary(
        self,
        conversation: str,
        model: str
    ) -> str:
        """Ruft LLM für Summary auf."""
        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": self.SUMMARY_PROMPT.format(conversation=conversation)
            }],
            "temperature": 0.3,  # Niedrig für konsistente Summaries
            "max_tokens": self.MAX_SUMMARY_TOKENS,
            "stream": False
        }

        async with httpx.AsyncClient(
            timeout=30,
            verify=settings.llm.verify_ssl
        ) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")

        return ""

    def _create_simple_summary(self, messages: List[Dict]) -> str:
        """
        Einfache Fallback-Summary ohne LLM.

        Extrahiert nur die wichtigsten Teile.
        """
        parts = ["Bisherige Konversation (gekürzt):"]

        for i, msg in enumerate(messages[-5:]):  # Letzte 5
            role = msg.get("role", "?")
            content = msg.get("content", "")[:200]
            parts.append(f"- {role}: {content}...")

        return "\n".join(parts)

    def estimate_savings(self, messages: List[Dict]) -> Dict:
        """
        Schätzt Token-Einsparung durch Summary.

        Returns:
            Dict mit Statistiken
        """
        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self.KEEP_RECENT_MESSAGES:
            return {
                "would_summarize": False,
                "current_tokens": estimate_messages_tokens(messages),
                "estimated_savings": 0
            }

        to_summarize = non_system[:-self.KEEP_RECENT_MESSAGES]
        to_keep = non_system[-self.KEEP_RECENT_MESSAGES:]

        tokens_to_remove = estimate_messages_tokens(to_summarize)
        tokens_for_summary = self.MAX_SUMMARY_TOKENS + 50  # + Overhead

        return {
            "would_summarize": True,
            "messages_to_summarize": len(to_summarize),
            "messages_to_keep": len(to_keep),
            "current_tokens": estimate_messages_tokens(messages),
            "tokens_to_remove": tokens_to_remove,
            "tokens_for_summary": tokens_for_summary,
            "estimated_savings": max(0, tokens_to_remove - tokens_for_summary)
        }


# Singleton
_summarizer: Optional[ConversationSummarizer] = None


def get_summarizer() -> ConversationSummarizer:
    """Gibt Singleton-Instanz zurück."""
    global _summarizer
    if _summarizer is None:
        _summarizer = ConversationSummarizer()
    return _summarizer
