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
    _tokens_cached: bool = False  # PHASE 1.3: Track if tokens computed

    def __post_init__(self):
        # PHASE 1.3: Cache token estimates to avoid recomputation
        if self.tokens == 0 and self.content:
            self.tokens = estimate_tokens(self.content)
            self._tokens_cached = True

    def update_content(self, new_content: str) -> None:
        """Update content and recompute tokens (PHASE 1.3: caching)."""
        self.content = new_content
        self.tokens = estimate_tokens(new_content)
        self._tokens_cached = True


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

    # PHASE 2.2: Pre-compiled regex patterns (compiled once, reused for all instances)
    PRESERVE_PATTERNS = [
        re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b'),  # CamelCase (Klassennamen)
        re.compile(r'\b[A-Z_]{2,}\b'),                    # UPPER_CASE (Konstanten)
        re.compile(r'\b\d+\b'),                           # Zahlen
        re.compile(r'"[^"]{1,50}"'),                      # Kurze Strings
        re.compile(r"'[^']{1,50}'"),                      # Kurze Strings
    ]

    def _compute_relevance(self, item: ContextItem, recent_texts: List[str], _cached_text: str = None) -> float:
        """
        Berechnet wie relevant ein Item für die letzten N Nachrichten ist.

        Zählt wie viele Wörter aus dem Item-Inhalt in den letzten Messages vorkommen.
        Items die gerade diskutiert werden, erhalten einen höheren Relevanz-Score
        und werden bei der Kompaktierung bevorzugt behalten.

        Args:
            _cached_text: PERFORMANCE - vorberechneter recent_text (vermeidet wiederholtes join)
        """
        if not recent_texts and not _cached_text:
            return 0.0
        item_words = {w for w in item.content.lower().split() if len(w) > 4}
        if not item_words:
            return 0.0
        # PERFORMANCE: Nutze gecachten Text wenn verfügbar
        recent_text = _cached_text if _cached_text else " ".join(recent_texts).lower()
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

        # PERFORMANCE: recent_text einmal berechnen und cachen
        recent_texts = (recent_messages or [])[-4:]
        cached_recent_text = " ".join(recent_texts).lower() if recent_texts else ""

        # Relevanz-Scores berechnen mit gecachtem Text
        relevance_scores = {
            id(item): self._compute_relevance(item, recent_texts, cached_recent_text)
            for item in items
        }

        # PERFORMANCE: Sort-Keys einmal berechnen und cachen
        for item in items:
            item._sort_key = (item.priority, -relevance_scores[id(item)], -item.age)
            item._remove_key = (item.priority, item.age, -relevance_scores[id(item)])

        # Sortiere nach Priorität (höhere Prio = niedrigere Zahl = wichtiger)
        sorted_items = sorted(items, key=lambda x: x._sort_key)

        # Phase 1: Alte, niedrig-priorisierte Items komplett entfernen
        # PERFORMANCE: Sortiere einmal nach remove_key, dann iterativ entfernen (O(n log n) statt O(n²))
        if current_tokens > target_tokens and len(sorted_items) > preserve_recent:
            # Kandidaten für Entfernung (alle außer preserve_recent)
            candidates = sorted_items[:-preserve_recent] if preserve_recent else sorted_items[:]
            # Sortiere Kandidaten nach Entfernungs-Priorität (höchste zuerst)
            candidates_sorted = sorted(candidates, key=lambda x: x._remove_key, reverse=True)

            removed_set = set()
            for item in candidates_sorted:
                if current_tokens <= target_tokens:
                    break
                current_tokens -= item.tokens
                removed_set.add(id(item))

            # Rebuild sorted_items ohne entfernte
            sorted_items = [item for item in sorted_items if id(item) not in removed_set]

        if current_tokens <= target_tokens:
            return sorted_items

        # PHASE 1.3 + 2.2: Phase 2+3 merged into single pass with token caching
        for item in sorted_items:
            if current_tokens <= target_tokens:
                break

            # Try summarization first (if applicable and not already summarized)
            if (item.item_type == "tool_output"
                and item.tokens > self.MIN_TOKENS_FOR_SUMMARY
                and not item.is_summarized):

                old_tokens = item.tokens
                summarized_content = self._summarize_tool_output(item)

                # Use cached token estimation
                item.update_content(summarized_content)
                item.is_summarized = True
                current_tokens -= (old_tokens - item.tokens)

            # Then truncate if still over limit
            elif item.tokens > self.MAX_SUMMARY_TOKENS:
                old_tokens = item.tokens
                truncated_content = truncate_text_to_tokens(
                    item.content,
                    self.MAX_SUMMARY_TOKENS
                )

                # Use cached token estimation
                item.update_content(truncated_content)
                current_tokens -= (old_tokens - item.tokens)

        return sorted_items

    def _summarize_tool_output(self, item: ContextItem) -> str:
        """
        Erstellt eine kompakte Zusammenfassung eines Tool-Outputs.

        Strategie:
        1. Wichtige Patterns extrahieren (Namen, IDs, Zahlen)
        2. Erste und letzte Zeilen behalten
        3. Rest kürzen

        PHASE 2.2: Uses pre-compiled patterns for efficiency
        """
        content = item.content
        lines = content.split('\n')

        # PHASE 2.2: Use pre-compiled patterns (already compiled in __init__)
        important_matches = []
        search_content = content[:1000]  # Limit search to first 1000 chars for speed
        for pattern in self.PRESERVE_PATTERNS:
            matches = pattern.findall(search_content)
            important_matches.extend(matches[:5])  # Limit to 5 per pattern

        # Deduplizieren
        important_matches = list(dict.fromkeys(important_matches))[:10]

        # Zusammenfassung bauen (memory-efficient)
        summary_parts = []

        # Tool-Name Header
        if item.tool_name:
            summary_parts.append(f"[{item.tool_name} - Zusammenfassung]")

        # Erste paar Zeilen (oft wichtigste Info)
        summary_parts.extend(lines[:3])

        # Wichtige extrahierte Werte
        if important_matches:
            summary_parts.append(f"Gefundene Werte: {', '.join(important_matches)}")

        # Letzte Zeile (oft Ergebnis/Status)
        if len(lines) > 5:
            summary_parts.append("...")
            summary_parts.append(lines[-1])

        # Info über Originalgrößea
        summary_parts.append(f"[Original: {len(lines)} Zeilen]")

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
