"""
Context Compactor - Komprimiert Context-Items basierend auf Priorität.

Strategien:
1. Tool-Outputs auf Zusammenfassung reduzieren
2. Alte Items entfernen (niedrige Priorität zuerst)
3. Lange Inhalte kürzen
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import IntEnum
import re

from app.utils.token_counter import estimate_tokens, truncate_text_to_tokens


class ContextPriority(IntEnum):
    """Prioritätsstufen für Context-Items (niedriger = wichtiger)."""
    SYSTEM = 1          # System Prompts - nie entfernen
    MEMORY = 2          # Long-term Memory - selten entfernen
    CURRENT_FILE = 3    # Aktuell bearbeitete Datei
    RECENT_TOOL = 4     # Kürzliche Tool-Ergebnisse
    OLD_TOOL = 5        # Ältere Tool-Ergebnisse
    OLD_MESSAGE = 6     # Alte Nachrichten


@dataclass
class ContextItem:
    """Ein Item im Context."""
    content: str
    item_type: str              # "tool_output", "file", "message", "memory"
    priority: ContextPriority = ContextPriority.OLD_TOOL
    tokens: int = 0
    age: int = 0                # Wie viele Turns alt
    tool_name: Optional[str] = None
    is_summarized: bool = False

    def __post_init__(self):
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.content)


class ContextCompactor:
    """
    Komprimiert Context-Items um Token-Budget einzuhalten.

    Verwendet mehrere Strategien:
    1. Prioritäts-basiertes Entfernen (niedrige Prio zuerst)
    2. Tool-Output Summarization
    3. Längen-basiertes Kürzen
    """

    # Minimale Token-Länge für Summarization
    MIN_TOKENS_FOR_SUMMARY = 500

    # Maximale Token-Länge nach Summarization
    MAX_SUMMARY_TOKENS = 300

    # Keywords die in Summaries erhalten bleiben sollen
    PRESERVE_PATTERNS = [
        r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b',  # CamelCase (Klassennamen)
        r'\b[A-Z_]{2,}\b',                    # UPPER_CASE (Konstanten)
        r'\b\d+\b',                           # Zahlen
        r'"[^"]{1,50}"',                      # Kurze Strings
        r"'[^']{1,50}'",                      # Kurze Strings
    ]

    def _compute_relevance(self, item: ContextItem, recent_texts: List[str]) -> float:
        """
        Berechnet wie relevant ein Item für die letzten N Nachrichten ist.

        Zählt wie viele Wörter aus dem Item-Inhalt in den letzten Messages vorkommen.
        Items die gerade diskutiert werden, erhalten einen höheren Relevanz-Score
        und werden bei der Kompaktierung bevorzugt behalten.
        """
        if not recent_texts:
            return 0.0
        item_words = {w for w in item.content.lower().split() if len(w) > 4}
        if not item_words:
            return 0.0
        recent_text = " ".join(recent_texts).lower()
        hits = sum(1 for w in item_words if w in recent_text)
        return hits / len(item_words)

    def compact(
        self,
        items: List[ContextItem],
        target_tokens: int,
        preserve_recent: int = 3,
        recent_messages: Optional[List[str]] = None
    ) -> List[ContextItem]:
        """
        Komprimiert Items auf target_tokens.

        Args:
            items: Liste der Context-Items
            target_tokens: Ziel-Token-Budget
            preserve_recent: Anzahl der neuesten Items die nicht gekürzt werden
            recent_messages: Letzte User/Assistant-Nachrichten für Relevanz-Scoring

        Returns:
            Komprimierte Liste von Items
        """
        current_tokens = sum(item.tokens for item in items)

        if current_tokens <= target_tokens:
            return items

        # Relevanz-Scores berechnen (letzten 4 Nachrichten)
        recent_texts = (recent_messages or [])[-4:]
        relevance_scores = {
            id(item): self._compute_relevance(item, recent_texts)
            for item in items
        }

        # Sortiere nach Priorität (höhere Prio = niedrigere Zahl = wichtiger)
        # Bei gleicher Priorität: relevante Items zuletzt entfernen
        sorted_items = sorted(
            items,
            key=lambda x: (x.priority, -relevance_scores[id(x)], -x.age)
        )

        # Phase 1: Alte, niedrig-priorisierte Items komplett entfernen
        # Items mit hoher Relevanz zur aktuellen Konversation werden bevorzugt behalten
        while current_tokens > target_tokens and len(sorted_items) > preserve_recent:
            # Entferne Item mit niedrigster Priorität (höchste Zahl) und geringster Relevanz
            to_remove = max(
                sorted_items[:-preserve_recent],
                key=lambda x: (x.priority, x.age, -relevance_scores[id(x)])
            )
            current_tokens -= to_remove.tokens
            sorted_items.remove(to_remove)

        if current_tokens <= target_tokens:
            return sorted_items

        # Phase 2: Tool-Outputs summarisieren
        for item in sorted_items:
            if current_tokens <= target_tokens:
                break

            if (item.item_type == "tool_output"
                and item.tokens > self.MIN_TOKENS_FOR_SUMMARY
                and not item.is_summarized):

                old_tokens = item.tokens
                item.content = self._summarize_tool_output(item)
                item.tokens = estimate_tokens(item.content)
                item.is_summarized = True
                current_tokens -= (old_tokens - item.tokens)

        if current_tokens <= target_tokens:
            return sorted_items

        # Phase 3: Längenbegrenzung für alle Items
        for item in sorted_items:
            if current_tokens <= target_tokens:
                break

            if item.tokens > self.MAX_SUMMARY_TOKENS:
                old_tokens = item.tokens
                item.content = truncate_text_to_tokens(
                    item.content,
                    self.MAX_SUMMARY_TOKENS
                )
                item.tokens = estimate_tokens(item.content)
                current_tokens -= (old_tokens - item.tokens)

        return sorted_items

    def _summarize_tool_output(self, item: ContextItem) -> str:
        """
        Erstellt eine kompakte Zusammenfassung eines Tool-Outputs.

        Strategie:
        1. Wichtige Patterns extrahieren (Namen, IDs, Zahlen)
        2. Erste und letzte Zeilen behalten
        3. Rest kürzen
        """
        content = item.content
        lines = content.split('\n')

        # Extrahiere wichtige Informationen
        important_matches = []
        for pattern in self.PRESERVE_PATTERNS:
            matches = re.findall(pattern, content)
            important_matches.extend(matches[:10])  # Max 10 pro Pattern

        # Deduplizieren
        important_matches = list(dict.fromkeys(important_matches))[:20]

        # Zusammenfassung bauen
        summary_parts = []

        # Tool-Name Header
        if item.tool_name:
            summary_parts.append(f"[{item.tool_name} - Zusammenfassung]")

        # Erste paar Zeilen (oft wichtigste Info)
        first_lines = lines[:3]
        summary_parts.extend(first_lines)

        # Wichtige extrahierte Werte
        if important_matches:
            summary_parts.append(f"Gefundene Werte: {', '.join(important_matches[:15])}")

        # Letzte Zeile (oft Ergebnis/Status)
        if len(lines) > 5:
            summary_parts.append("...")
            summary_parts.append(lines[-1])

        # Info über Originalgrößea
        summary_parts.append(f"[Original: {item.tokens} Tokens, {len(lines)} Zeilen]")

        return '\n'.join(summary_parts)

    def compact_tool_outputs_only(
        self,
        items: List[ContextItem],
        max_tokens_per_item: int = 500
    ) -> List[ContextItem]:
        """
        Komprimiert nur Tool-Outputs, behält alles andere.

        Nützlich wenn man nur Tool-Outputs kürzen will ohne
        Items zu entfernen.
        """
        result = []
        for item in items:
            if item.item_type == "tool_output" and item.tokens > max_tokens_per_item:
                new_item = ContextItem(
                    content=self._summarize_tool_output(item),
                    item_type=item.item_type,
                    priority=item.priority,
                    age=item.age,
                    tool_name=item.tool_name,
                    is_summarized=True
                )
                result.append(new_item)
            else:
                result.append(item)
        return result


# Singleton
_compactor: Optional[ContextCompactor] = None


def get_compactor() -> ContextCompactor:
    """Gibt Singleton-Instanz zurück."""
    global _compactor
    if _compactor is None:
        _compactor = ContextCompactor()
    return _compactor
