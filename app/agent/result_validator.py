"""
Result Validator - Bewertet Relevanz von Tool-Ergebnissen und extrahiert Quellen.

Features:
- TF-IDF basiertes Relevanz-Scoring (kein LLM-Call für Performance)
- Source-Metadata Extraktion für Quellenangaben
- Automatische Zusammenfassung langer Ergebnisse via LLM
- Threshold-basierte Filterung irrelevanter Ergebnisse
"""

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.tools import ToolResult
from app.utils.token_counter import estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class SourceMetadata:
    """Strukturierte Quelleninformation für Attribution."""
    source_type: str  # "confluence", "code", "handbook", "jira", "database", etc.
    source_id: str  # Page-ID, Dateipfad, Ticket-Key, etc.
    source_title: str  # Lesbare Bezeichnung
    source_url: Optional[str] = None
    excerpt_start: int = 0  # Zeile/Position des Excerpts
    excerpt_end: int = 0
    confidence: float = 1.0  # Wie sicher sind wir über diese Quelle?

    def format_citation(self) -> str:
        """Formatiert die Quelle als Zitation."""
        if self.source_url:
            return f"[{self.source_type.upper()}: {self.source_title}]({self.source_url})"
        return f"[{self.source_type.upper()}: {self.source_title} | {self.source_id}]"

    def format_header(self) -> str:
        """Formatiert einen Header für Tool-Results."""
        url_part = f"\n{self.source_url}" if self.source_url else ""
        return f"[QUELLE: {self.source_type} | {self.source_title}]{url_part}"


@dataclass
class ValidationResult:
    """Ergebnis der Validierung eines Tool-Results."""
    relevance_score: float  # 0.0 - 1.0
    should_use: bool  # score >= threshold
    source_metadata: Optional[SourceMetadata] = None
    summary: Optional[str] = None  # Gekürzt wenn > max_tokens
    original_tokens: int = 0
    summary_tokens: int = 0
    keywords_matched: List[str] = field(default_factory=list)
    reason: str = ""  # Warum wurde entschieden

    def get_content_with_source(self, original_content: str) -> str:
        """Gibt den Inhalt mit Quellenheader zurück."""
        if self.summary:
            content = self.summary
        else:
            content = original_content

        if self.source_metadata:
            header = self.source_metadata.format_header()
            return f"{header}\n\n{content}"

        return content


class ResultValidator:
    """
    Validiert und bewertet Tool-Ergebnisse.

    Verwendet TF-IDF für Relevanz-Scoring (kein LLM-Call für Performance).
    Extrahiert Source-Metadata für Quellenangaben.
    Kann lange Ergebnisse via LLM zusammenfassen.
    """

    # Relevanz-Threshold (aus Design-Entscheidung)
    RELEVANCE_THRESHOLD = 0.3

    # Max Tokens bevor Summary erstellt wird
    MAX_TOKENS_BEFORE_SUMMARY = 2000

    # Stopwords für TF-IDF (Deutsch + Englisch)
    STOPWORDS: Set[str] = {
        # Deutsch
        "der", "die", "das", "ein", "eine", "und", "oder", "aber", "ist", "sind",
        "war", "waren", "wird", "werden", "hat", "haben", "kann", "können", "muss",
        "müssen", "soll", "sollen", "für", "mit", "von", "zu", "in", "auf", "aus",
        "bei", "nach", "über", "unter", "vor", "hinter", "zwischen", "durch",
        "nicht", "auch", "nur", "noch", "schon", "sehr", "mehr", "wie", "als",
        "wenn", "weil", "dass", "ob", "dieser", "diese", "dieses", "jeder", "jede",
        "jedes", "alle", "alles", "andere", "anderen", "einige", "welche", "welcher",
        # Englisch
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "will",
        "be", "been", "being", "have", "has", "had", "do", "does", "did", "can",
        "could", "should", "would", "may", "might", "must", "shall", "for", "with",
        "from", "to", "in", "on", "at", "by", "about", "into", "through", "during",
        "before", "after", "above", "below", "between", "under", "again", "further",
        "then", "once", "here", "there", "when", "where", "why", "how", "all",
        "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
        "only", "own", "same", "so", "than", "too", "very", "just", "also",
        # Code-spezifische Stopwords
        "public", "private", "protected", "static", "final", "void", "class",
        "interface", "extends", "implements", "import", "package", "return",
        "new", "this", "super", "null", "true", "false", "if", "else", "for",
        "while", "do", "switch", "case", "break", "continue", "try", "catch",
        "finally", "throw", "throws", "def", "self", "none", "lambda", "async",
        "await", "function", "const", "let", "var",
    }

    # Tool-spezifische Source-Type Mappings
    TOOL_SOURCE_TYPES: Dict[str, str] = {
        "search_code": "code",
        "read_file": "code",
        "batch_read_files": "code",
        "grep_content": "code",
        "search_confluence": "confluence",
        "read_confluence_page": "confluence",
        "search_handbook": "handbook",
        "search_skills": "knowledge",
        "search_jira": "jira",
        "get_jira_issue": "jira",
        "query_database": "database",
        "search_pdfs": "pdf",
        "web_search": "web",
        "fetch_webpage": "web",
    }

    def __init__(
        self,
        llm_client=None,
        analysis_model: Optional[str] = None,
        relevance_threshold: float = 0.3,
        max_tokens_before_summary: int = 2000
    ):
        """
        Initialisiert den ResultValidator.

        Args:
            llm_client: LLM-Client für Zusammenfassungen (optional)
            analysis_model: Modell für Summaries (default: aus config)
            relevance_threshold: Min Score für Ergebnis-Nutzung
            max_tokens_before_summary: Ab wann LLM-Summary erstellt wird
        """
        self._llm = llm_client
        self._model = analysis_model
        self.relevance_threshold = relevance_threshold
        self.max_tokens_before_summary = max_tokens_before_summary

    async def validate(
        self,
        tool_name: str,
        query: str,
        result: ToolResult,
        create_summary: bool = True
    ) -> ValidationResult:
        """
        Validiert ein Tool-Ergebnis.

        1. Berechnet Relevanz-Score via TF-IDF
        2. Extrahiert Source-Metadata
        3. Kürzt wenn nötig via LLM-Summary

        Args:
            tool_name: Name des ausgeführten Tools
            query: Ursprüngliche User-Query
            result: Tool-Ergebnis
            create_summary: Ob bei langen Ergebnissen Summary erstellt werden soll

        Returns:
            ValidationResult mit Score, Metadata und ggf. Summary
        """
        if not result.success:
            return ValidationResult(
                relevance_score=0.0,
                should_use=False,
                reason=f"Tool-Fehler: {result.error}"
            )

        content = result.to_context()
        if not content or len(content.strip()) < 10:
            return ValidationResult(
                relevance_score=0.0,
                should_use=False,
                reason="Leeres oder zu kurzes Ergebnis"
            )

        # 1. Relevanz-Score berechnen
        score, matched_keywords = self._calculate_relevance(query, content)

        # 2. Source-Metadata extrahieren
        source_metadata = self._extract_source_metadata(tool_name, result, content)

        # 3. Token-Zählung
        original_tokens = estimate_tokens(content)

        # 4. Entscheidung ob verwenden
        should_use = score >= self.relevance_threshold

        # 5. Summary erstellen wenn nötig und gewünscht
        summary = None
        summary_tokens = 0

        if should_use and create_summary and original_tokens > self.max_tokens_before_summary:
            summary = await self._create_summary(content, query, tool_name)
            if summary:
                summary_tokens = estimate_tokens(summary)
                logger.debug(
                    f"[ResultValidator] Summary erstellt: {original_tokens} → {summary_tokens} tokens"
                )

        # Reason erstellen
        if should_use:
            reason = f"Relevanz {score:.2f} >= {self.relevance_threshold}"
            if matched_keywords:
                reason += f", Keywords: {', '.join(matched_keywords[:5])}"
        else:
            reason = f"Relevanz {score:.2f} < {self.relevance_threshold} (zu niedrig)"

        return ValidationResult(
            relevance_score=score,
            should_use=should_use,
            source_metadata=source_metadata,
            summary=summary,
            original_tokens=original_tokens,
            summary_tokens=summary_tokens,
            keywords_matched=matched_keywords,
            reason=reason
        )

    def _calculate_relevance(
        self,
        query: str,
        content: str
    ) -> Tuple[float, List[str]]:
        """
        TF-IDF basiertes Relevanz-Scoring.

        Kein LLM-Call - rein algorithmus-basiert für Performance.

        Args:
            query: Die User-Query
            content: Der zu bewertende Inhalt

        Returns:
            Tuple von (Score 0.0-1.0, Liste gematchter Keywords)
        """
        # Tokenize
        query_tokens = self._tokenize(query)
        content_tokens = self._tokenize(content)

        if not query_tokens or not content_tokens:
            return 0.0, []

        query_set = set(query_tokens)
        content_counter = Counter(content_tokens)
        content_set = set(content_tokens)

        # Matched Keywords
        matched = list(query_set & content_set)

        if not matched:
            return 0.0, []

        # TF: Wie oft erscheinen Query-Tokens im Content?
        tf_scores = []
        for token in matched:
            tf = content_counter[token] / len(content_tokens)
            tf_scores.append(tf)

        avg_tf = sum(tf_scores) / len(tf_scores) if tf_scores else 0

        # Coverage: Wie viele Query-Tokens wurden gefunden?
        coverage = len(matched) / len(query_set)

        # Position Bonus: Tokens am Anfang des Contents sind wichtiger
        position_bonus = 0.0
        first_500_tokens = content_tokens[:500]
        first_500_set = set(first_500_tokens)
        early_matches = query_set & first_500_set
        if early_matches:
            position_bonus = 0.1 * (len(early_matches) / len(query_set))

        # Kombinierter Score
        # Gewichtung: Coverage (50%), TF (30%), Position (20%)
        score = (coverage * 0.5) + (min(avg_tf * 10, 0.3)) + (position_bonus * 0.2)

        # Normalisieren auf 0-1
        score = min(1.0, max(0.0, score))

        # Bonus für exakte Phrasen-Matches
        query_lower = query.lower()
        content_lower = content.lower()
        if len(query_lower) > 5 and query_lower in content_lower:
            score = min(1.0, score + 0.2)

        return score, matched

    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenisiert Text und entfernt Stopwords.

        Args:
            text: Zu tokenisierender Text

        Returns:
            Liste von Tokens (lowercase, ohne Stopwords)
        """
        if not text:
            return []

        # Lowercase und nur alphanumerische Zeichen behalten
        text_lower = text.lower()
        # Wörter extrahieren (min 2 Zeichen)
        tokens = re.findall(r'\b[a-zA-Z0-9äöüß]{2,}\b', text_lower)

        # Stopwords entfernen
        filtered = [t for t in tokens if t not in self.STOPWORDS]

        return filtered

    def _extract_source_metadata(
        self,
        tool_name: str,
        result: ToolResult,
        content: str
    ) -> Optional[SourceMetadata]:
        """
        Extrahiert strukturierte Quelleninformationen.

        Args:
            tool_name: Name des Tools
            result: Tool-Ergebnis
            content: Ergebnis-Content

        Returns:
            SourceMetadata oder None
        """
        source_type = self.TOOL_SOURCE_TYPES.get(tool_name, "unknown")

        # Tool-spezifische Extraktion
        if tool_name in ("search_code", "read_file", "grep_content"):
            return self._extract_code_source(content, source_type)

        elif tool_name in ("search_confluence", "read_confluence_page"):
            return self._extract_confluence_source(content, source_type)

        elif tool_name in ("search_jira", "get_jira_issue"):
            return self._extract_jira_source(content, source_type)

        elif tool_name == "search_handbook":
            return self._extract_handbook_source(content, source_type)

        elif tool_name == "query_database":
            return self._extract_database_source(content, source_type)

        # Fallback
        return SourceMetadata(
            source_type=source_type,
            source_id=tool_name,
            source_title=f"Tool: {tool_name}",
            confidence=0.5
        )

    def _extract_code_source(
        self,
        content: str,
        source_type: str
    ) -> Optional[SourceMetadata]:
        """Extrahiert Source-Info aus Code-Suche."""
        # Pattern: "── path/to/file.java ──" oder "Gefunden in: path/to/file.py"
        file_patterns = [
            r'──\s*([^\s─]+\.(java|py|sql|xml|json|yaml|yml|ts|js))\s*──',
            r'(?:Gefunden in|Found in|File|Datei)[:\s]+([^\s]+\.(java|py|sql|xml|json))',
            r'\[([^\]]+\.(java|py|sql|xml|json))\]',
        ]

        for pattern in file_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                file_path = match.group(1)
                # Zeilennummern extrahieren
                line_match = re.search(r':(\d+)', content)
                line_num = int(line_match.group(1)) if line_match else 0

                return SourceMetadata(
                    source_type=source_type,
                    source_id=file_path,
                    source_title=file_path.split("/")[-1],
                    excerpt_start=line_num,
                    confidence=0.9
                )

        return None

    def _extract_confluence_source(
        self,
        content: str,
        source_type: str
    ) -> Optional[SourceMetadata]:
        """Extrahiert Source-Info aus Confluence-Ergebnissen."""
        # Pattern für ID und Titel
        id_match = re.search(r'[Ii][Dd][:\s"]+(\d{5,})', content)
        title_match = re.search(r'[Tt]itle[:\s"]+([^\n"]+)', content)
        url_match = re.search(r'(https?://[^\s]+/pages/\d+[^\s]*)', content)

        if id_match:
            page_id = id_match.group(1)
            title = title_match.group(1).strip() if title_match else f"Page {page_id}"
            url = url_match.group(1) if url_match else None

            return SourceMetadata(
                source_type=source_type,
                source_id=page_id,
                source_title=title[:50],
                source_url=url,
                confidence=0.95
            )

        return None

    def _extract_jira_source(
        self,
        content: str,
        source_type: str
    ) -> Optional[SourceMetadata]:
        """Extrahiert Source-Info aus Jira-Ergebnissen."""
        # Pattern: PROJECT-123
        ticket_match = re.search(r'([A-Z]{2,}-\d+)', content)
        summary_match = re.search(r'[Ss]ummary[:\s"]+([^\n"]+)', content)

        if ticket_match:
            ticket_key = ticket_match.group(1)
            summary = summary_match.group(1).strip()[:50] if summary_match else ticket_key

            return SourceMetadata(
                source_type=source_type,
                source_id=ticket_key,
                source_title=summary,
                confidence=0.95
            )

        return None

    def _extract_handbook_source(
        self,
        content: str,
        source_type: str
    ) -> Optional[SourceMetadata]:
        """Extrahiert Source-Info aus Handbuch-Ergebnissen."""
        # Pattern: [ServiceName] Title oder Service: Name
        service_match = re.search(r'\[([^\]]+)\]\s*([^\n]+)', content)
        if service_match:
            service = service_match.group(1)
            title = service_match.group(2).strip()[:50]

            return SourceMetadata(
                source_type=source_type,
                source_id=service,
                source_title=title,
                confidence=0.85
            )

        return None

    def _extract_database_source(
        self,
        content: str,
        source_type: str
    ) -> Optional[SourceMetadata]:
        """Extrahiert Source-Info aus Datenbank-Ergebnissen."""
        # Pattern: Tabelle oder Schema
        table_match = re.search(r'(?:FROM|INTO|TABLE)\s+([A-Z_][A-Z0-9_\.]+)', content, re.IGNORECASE)
        rows_match = re.search(r'(\d+)\s*(?:Zeilen|rows|Ergebnisse)', content, re.IGNORECASE)

        if table_match:
            table = table_match.group(1)
            row_count = rows_match.group(1) if rows_match else "?"

            return SourceMetadata(
                source_type=source_type,
                source_id=table,
                source_title=f"Query: {table} ({row_count} rows)",
                confidence=0.9
            )

        return SourceMetadata(
            source_type=source_type,
            source_id="query",
            source_title="Database Query",
            confidence=0.7
        )

    async def _create_summary(
        self,
        content: str,
        query: str,
        tool_name: str
    ) -> Optional[str]:
        """
        LLM-basierte Zusammenfassung für große Ergebnisse.

        Verwendet analysis_model für Konsistenz.

        Args:
            content: Zu kürzender Inhalt
            query: User-Query für Kontext
            tool_name: Tool-Name für Kontext

        Returns:
            Zusammenfassung oder None bei Fehler
        """
        if not self._llm:
            # Fallback: Einfache Kürzung
            return self._simple_truncate(content, 1500)

        # Model aus Config holen wenn nicht gesetzt
        if not self._model:
            from app.core.config import settings
            self._model = settings.llm.analysis_model or settings.llm.default_model

        prompt = f"""Fasse folgendes Tool-Ergebnis zusammen.

KONTEXT: Der User fragt nach "{query}"
TOOL: {tool_name}

ERGEBNIS (gekürzt auf erste 6000 Zeichen):
{content[:6000]}

ZUSAMMENFASSUNG (max 400 Wörter):
- Behalte ALLE relevanten Fakten, Zahlen, Pfade und Namen
- Strukturiere als Bullet-Points wenn sinnvoll
- Entferne nur redundante oder irrelevante Teile
- Wenn Code-Snippets relevant sind, behalte die wichtigsten
"""

        try:
            response = await self._llm.chat_simple(
                prompt,
                model=self._model,
                max_tokens=600,
                temperature=0.2
            )
            return response.strip() if response else None
        except Exception as e:
            logger.warning(f"[ResultValidator] Summary-Erstellung fehlgeschlagen: {e}")
            return self._simple_truncate(content, 1500)

    def _simple_truncate(self, content: str, max_chars: int) -> str:
        """Einfache Kürzung als Fallback."""
        if len(content) <= max_chars:
            return content

        # Bei Code: Versuche bei Zeilenende zu kürzen
        truncated = content[:max_chars]
        last_newline = truncated.rfind('\n')
        if last_newline > max_chars * 0.7:
            truncated = truncated[:last_newline]

        return truncated + "\n\n[... gekürzt ...]"


# Singleton-Instanz
_validator: Optional[ResultValidator] = None


def get_result_validator(
    llm_client=None,
    analysis_model: Optional[str] = None
) -> ResultValidator:
    """Gibt die ResultValidator-Instanz zurück."""
    global _validator
    if _validator is None:
        _validator = ResultValidator(
            llm_client=llm_client,
            analysis_model=analysis_model
        )
    return _validator


def reset_result_validator() -> None:
    """Setzt den ResultValidator zurück (für Tests)."""
    global _validator
    _validator = None
