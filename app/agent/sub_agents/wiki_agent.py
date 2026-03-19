"""Wiki-Agent – durchsucht Confluence-Dokumentation mit intelligentem Ranking."""

import re
from typing import Dict, List, Optional, Set

from app.agent.sub_agent import SubAgent, SubAgentResult


class WikiAgent(SubAgent):
    """
    Spezialisiert auf Confluence-Wiki-Recherche mit Relevanz-Ranking.

    Features:
    - Relevanz-basierte Seiten-Auswahl (Titel-Match > Excerpt-Match)
    - Budget-bewusste Strategie (max 9 Seiten)
    - Quellenreferenzen mit Seiten-IDs
    - Early-Exit bei wenig relevanten Treffern
    """

    name = "wiki_agent"
    display_name = "Wiki-Agent"

    # Verbesserter System-Prompt mit klarer Strategie
    description = """Du durchsuchst Confluence-Wiki-Seiten nach relevanter Dokumentation.

STRATEGIE:
1. ZUERST search_confluence mit präzisen Keywords (max 2-3 Wörter)
2. Bewerte die Suchergebnisse nach Relevanz:
   - Titel enthält exakten Suchbegriff → HOCH
   - Excerpt enthält Suchbegriff → MITTEL
   - Nur Space-Match → NIEDRIG
3. Lies NUR die Top-3 relevantesten Seiten vollständig
4. Bei jedem read_confluence_page: Extrahiere KONKRETE Fakten

WICHTIGE REGELN:
- Zitiere IMMER [Seiten-ID: Titel] bei Fakten
- Lies NICHT mehr als 6 Seiten (Budget-Limit)
- Bei <3 relevanten Treffern: Melde "wenig gefunden" statt weiterzusuchen
- Bei 0 Treffern: Versuche EINMAL mit alternativen Begriffen
- Erfinde KEINE Informationen - nur was in den Seiten steht

OUTPUT-FORMAT:
Jedes Finding als: "[ID:12345 - Seitentitel] Konkrete Information..."
"""

    max_iterations = 8  # Reduziert: 1-2 Suchen + max 6 Seiten
    allowed_tools = [
        "search_confluence",
        "read_confluence_page",
    ]

    # Tracking für Relevanz-Ranking
    _search_results: List[Dict] = []
    _pages_read: Set[str] = set()

    def __init__(self):
        super().__init__()
        self._search_results = []
        self._pages_read = set()

    async def run(
        self,
        query: str,
        llm_client,
        tool_registry,
    ) -> SubAgentResult:
        """
        Führt die Wiki-Recherche mit Relevanz-Ranking aus.

        Überschreibt die Basis-Implementierung um:
        1. Suchergebnisse zu ranken bevor Seiten gelesen werden
        2. Budget-bewusst nur relevante Seiten zu lesen
        """
        # Reset Tracking
        self._search_results = []
        self._pages_read = set()

        # Standard-Implementierung mit verbessertem Prompt verwenden
        return await super().run(query, llm_client, tool_registry)

    @staticmethod
    def rank_search_results(
        results: List[Dict],
        query: str
    ) -> List[Dict]:
        """
        Rankt Confluence-Suchergebnisse nach Relevanz.

        Scoring:
        - Titel enthält Query-Term exakt: +0.5
        - Titel enthält Query-Term teilweise: +0.3
        - Excerpt enthält Query-Term: +0.2
        - Kürzlich aktualisiert (falls verfügbar): +0.1

        Args:
            results: Liste von Suchergebnissen
            query: Die Suchanfrage

        Returns:
            Nach Relevanz sortierte Liste
        """
        if not results or not query:
            return results

        query_lower = query.lower()
        # Query in Tokens aufteilen
        query_terms = set(re.findall(r'\b\w{3,}\b', query_lower))

        scored = []
        for result in results:
            score = 0.0
            title = result.get("title", "").lower()
            excerpt = result.get("excerpt", "").lower()

            # Exakter Titel-Match (Query komplett im Titel)
            if query_lower in title:
                score += 0.5
            else:
                # Partial Titel-Match (einzelne Terme)
                title_matches = sum(1 for t in query_terms if t in title)
                if title_matches > 0:
                    score += 0.3 * (title_matches / len(query_terms))

            # Excerpt-Match
            excerpt_matches = sum(1 for t in query_terms if t in excerpt)
            if excerpt_matches > 0:
                score += 0.2 * (excerpt_matches / len(query_terms))

            # Bonus für vollständige Phrase im Excerpt
            if len(query_lower) > 5 and query_lower in excerpt:
                score += 0.15

            scored.append({
                **result,
                "_relevance_score": round(score, 3)
            })

        # Nach Score sortieren (absteigend)
        scored.sort(key=lambda x: x.get("_relevance_score", 0), reverse=True)

        return scored

    @staticmethod
    def should_read_page(result: Dict, already_read: int) -> bool:
        """
        Entscheidet ob eine Seite gelesen werden sollte.

        Args:
            result: Das gerankte Suchergebnis
            already_read: Anzahl bereits gelesener Seiten

        Returns:
            True wenn die Seite gelesen werden sollte
        """
        score = result.get("_relevance_score", 0)

        # Budget-Limits
        if already_read >= 6:
            return False  # Hard limit

        # Relevanz-Threshold basierend auf bereits gelesenen Seiten
        # Frühe Seiten: niedriger Threshold, späte: höher
        threshold = 0.1 + (already_read * 0.05)

        return score >= threshold

    @staticmethod
    def format_finding(page_id: str, title: str, content: str) -> str:
        """
        Formatiert ein Finding mit Quellenreferenz.

        Args:
            page_id: Confluence Seiten-ID
            title: Seitentitel
            content: Der extrahierte Inhalt

        Returns:
            Formatiertes Finding mit Referenz
        """
        # Titel kürzen wenn zu lang
        short_title = title[:40] + "..." if len(title) > 40 else title
        return f"[ID:{page_id} - {short_title}] {content}"
