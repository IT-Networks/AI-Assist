"""Wiki-Agent – durchsucht Confluence-Dokumentation mit intelligentem Ranking."""

import logging
import re
from typing import Any, Dict, List, Optional, Set

from app.agent.sub_agent import SubAgent, SubAgentResult

logger = logging.getLogger(__name__)


class WikiAgent(SubAgent):
    """
    Spezialisiert auf Confluence-Wiki-Recherche mit Relevanz-Ranking.

    Features:
    - Relevanz-basierte Seiten-Auswahl (Titel-Match > Excerpt-Match)
    - Budget-bewusste Strategie (max 6 Seiten)
    - Quellenreferenzen mit Seiten-IDs
    - Early-Exit bei wenig relevanten Treffern
    - Content-Extraktion für große Seiten (verhindert Context-Overflow)
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
5. PRÜFE mit list_confluence_pdfs ob relevante PDF-Attachments existieren
6. Bei relevanten PDFs: read_confluence_pdf mit query für Relevanz-Score

PDF-STRATEGIE:
- PDFs oft bei technischen Docs, Spezifikationen, Architektur-Diagrammen
- Nutze 'query' Parameter für automatische Relevanz-Bewertung
- Bei Score < 20%: PDF überspringen, nicht relevant

WICHTIGE REGELN:
- Zitiere IMMER [Seiten-ID: Titel] bei Fakten
- Lies NICHT mehr als 6 Seiten + 3 PDFs (Budget-Limit)
- Bei <3 relevanten Treffern: Melde "wenig gefunden" statt weiterzusuchen
- Bei 0 Treffern: Versuche EINMAL mit alternativen Begriffen
- Erfinde KEINE Informationen - nur was in den Seiten/PDFs steht

OUTPUT-FORMAT:
Jedes Finding als: "[ID:12345 - Seitentitel] Konkrete Information..."
Oder für PDFs: "[PDF: Dateiname.pdf] Konkrete Information..."
"""

    max_iterations = 10  # 1-2 Suchen + max 6 Seiten + optionale PDFs
    allowed_tools = [
        "search_confluence",
        "read_confluence_page",
        "list_confluence_pdfs",
        "read_confluence_pdf",
    ]

    # Content-Extraktion für große Confluence-Seiten aktivieren
    content_extraction_tools = ["read_confluence_page"]

    # Tracking für Relevanz-Ranking
    _search_results: List[Dict] = []
    _pages_read: Set[str] = set()
    _extractor = None  # Lazy init

    def __init__(self):
        super().__init__()
        self._search_results = []
        self._pages_read = set()
        self._extractor = None

    async def run(
        self,
        query: str,
        llm_client,
        tool_registry,
        conversation_context: Optional[str] = None,
    ) -> SubAgentResult:
        """
        Führt die Wiki-Recherche mit Relevanz-Ranking aus.

        Überschreibt die Basis-Implementierung um:
        1. Suchergebnisse zu ranken bevor Seiten gelesen werden
        2. Budget-bewusst nur relevante Seiten zu lesen
        3. Große Seiten intelligent zu komprimieren
        """
        # Reset Tracking
        self._search_results = []
        self._pages_read = set()
        self._extractor = None  # Reset für neuen Run

        # Standard-Implementierung mit verbessertem Prompt verwenden
        return await super().run(query, llm_client, tool_registry, conversation_context)

    async def _process_tool_result(
        self,
        tool_name: str,
        content: str,
        args: Dict[str, Any],
        llm_client,
    ) -> str:
        """
        Verarbeitet Confluence-Seiten mit intelligenter Content-Extraktion.

        Bei großen Seiten (>6000 Tokens):
        1. Chunking nach Dokumentstruktur
        2. Parallele Relevanz-Bewertung
        3. Nur relevante Teile behalten
        4. Zusammenfassung wenn nötig

        Args:
            tool_name: "read_confluence_page"
            content: Rohes Seiten-Ergebnis (kann 80.000+ Tokens sein)
            args: Tool-Argumente (enthält page_id)
            llm_client: LLMClient für Bewertungs-Calls

        Returns:
            Komprimierter, relevanter Content
        """
        # Lazy init des ContentExtractor
        if self._extractor is None:
            from app.agent.content_extractor import ContentExtractor
            self._extractor = ContentExtractor(llm_client)

        # Seiten-ID für Logging extrahieren
        page_id = args.get("page_id", "unknown")
        source_name = f"Confluence:{page_id}"

        # Extraktion durchführen
        try:
            result = await self._extractor.extract_relevant(
                content=content,
                query=self._current_query,
                source_name=source_name,
                model=self._model,
            )

            if result.is_relevant:
                # Relevanten Content mit Metadaten zurückgeben
                header = (
                    f"[{source_name}] "
                    f"Score: {result.relevance_score:.2f} | "
                    f"{result.original_tokens:,} → {result.extracted_tokens:,} Tokens | "
                    f"{result.chunks_kept}/{result.chunks_total} Chunks"
                )
                return f"{header}\n\n{result.extracted_content}"
            else:
                # Nicht relevant - nur kurze Markierung
                self._pages_read.add(page_id)
                return (
                    f"[{source_name}] NICHT RELEVANT\n"
                    f"Seite enthält keine Informationen zu: {self._current_query[:100]}\n"
                    f"({result.original_tokens:,} Tokens übersprungen)"
                )

        except Exception as e:
            logger.warning(f"[WikiAgent] Content-Extraktion fehlgeschlagen: {e}")
            # Fallback: Truncation auf sichere Größe
            max_chars = 20000  # ~5000 Tokens
            if len(content) > max_chars:
                return (
                    f"[{source_name}] (gekürzt wegen Fehler)\n\n"
                    f"{content[:max_chars]}\n\n[...{len(content) - max_chars} Zeichen gekürzt...]"
                )
            return content

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
