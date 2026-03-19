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

    SUMMARY_PROMPT = """Fasse diese Konversation zusammen.

SCHRITT 1 - Strukturierter Block (IMMER zuerst, exakt dieses Format, leere Felder weglassen):
```json
{{
  "entities": {{
    "KlassenOderServiceName": {{"java": "Pfad/zur/Datei.java", "handbuch": "entry_id_oder_name", "pdf": "Seite N", "confluence": "Seitentitel"}}
  }},
  "decisions": ["Getroffene Entscheidung 1"],
  "open_issues": ["Offenes Problem 1"]
}}
```

SCHRITT 2 - Freitext-Zusammenfassung (max 5 Stichpunkte):
- Wichtige technische Details
- Analysierte Fehler oder Befunde
- Offene Aufgaben

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

        # Finde System-Messages und trenne sie (filter None entries)
        system_messages = [m for m in messages if m is not None and isinstance(m, dict) and m.get("role") == "system"]
        non_system = [m for m in messages if m is not None and isinstance(m, dict) and m.get("role") != "system"]

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
            if msg is None or not isinstance(msg, dict):
                continue
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "") or ""
            conversation_parts.append(f"{role}: {content[:1500]}")

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

        for msg in messages[-5:]:  # Letzte 5
            if msg is None or not isinstance(msg, dict):
                continue
            role = msg.get("role", "?")
            content = msg.get("content", "") or ""
            parts.append(f"- {role}: {content[:200]}...")

        return "\n".join(parts)

    # Prompt für Max-Iterations Zusammenfassung
    MAX_ITERATIONS_PROMPT = """Die Anfrage wurde nach {iterations} Iterationen abgebrochen (Limit erreicht).

URSPRÜNGLICHE ANFRAGE:
{user_query}

AUSGEFÜHRTE AKTIONEN:
{tool_summary}

LETZTE KONVERSATION:
{recent_messages}

Erstelle eine kurze Zusammenfassung (max 200 Wörter) die folgendes enthält:
1. **Erreicht**: Was wurde bereits erledigt/gefunden?
2. **Offen**: Was fehlt noch zur vollständigen Beantwortung?
3. **Empfehlung**: Konkreter Vorschlag für den Folge-Prompt

Antworte auf Deutsch und nutze Markdown-Formatierung."""

    async def create_max_iterations_summary(
        self,
        user_query: str,
        tool_calls_history: List,
        messages_history: List[Dict],
        iterations: int
    ) -> Optional[str]:
        """
        Erstellt eine LLM-basierte Zusammenfassung bei max iterations.

        Args:
            user_query: Die ursprüngliche Benutzeranfrage
            tool_calls_history: Liste der ausgeführten Tool-Calls
            messages_history: Die Konversations-Historie
            iterations: Anzahl der durchgeführten Iterationen

        Returns:
            Zusammenfassung als String oder None bei Fehler
        """
        try:
            # Tool-Aufrufe zusammenfassen
            tool_summary_parts = []
            tool_counts = {}
            for tc in tool_calls_history:
                tool_name = tc.name if hasattr(tc, 'name') else str(tc)
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

            for tool_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                tool_summary_parts.append(f"- {tool_name}: {count}x aufgerufen")

            tool_summary = "\n".join(tool_summary_parts) if tool_summary_parts else "Keine Tools aufgerufen"

            # Letzte Messages extrahieren (max 4)
            recent_parts = []
            for msg in messages_history[-4:]:
                if msg is None or not isinstance(msg, dict):
                    continue
                role = msg.get("role", "unknown")
                content = msg.get("content", "") or ""
                # Kürzen auf 300 Zeichen
                content_preview = content[:300]
                if len(content) > 300:
                    content_preview += "..."
                recent_parts.append(f"{role.upper()}: {content_preview}")

            recent_messages = "\n\n".join(recent_parts) if recent_parts else "Keine Messages"

            # LLM aufrufen
            prompt = self.MAX_ITERATIONS_PROMPT.format(
                iterations=iterations,
                user_query=user_query[:500],  # Begrenzen
                tool_summary=tool_summary,
                recent_messages=recent_messages
            )

            model = settings.llm.tool_model or settings.llm.default_model
            summary = await self._call_llm_simple(prompt, model, max_tokens=500)

            return summary

        except Exception as e:
            print(f"[summarizer] Max-Iterations-Summary Fehler: {e}")
            return None

    async def _call_llm_simple(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 500
    ) -> str:
        """Einfacher LLM-Aufruf für Summaries."""
        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
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

    def estimate_savings(self, messages: List[Dict]) -> Dict:
        """
        Schätzt Token-Einsparung durch Summary.

        Returns:
            Dict mit Statistiken
        """
        system_messages = [m for m in messages if m is not None and isinstance(m, dict) and m.get("role") == "system"]
        non_system = [m for m in messages if m is not None and isinstance(m, dict) and m.get("role") != "system"]

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
