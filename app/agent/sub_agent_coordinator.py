"""
Sub-Agent Coordinator - Deduplizierung und Ranking von Sub-Agent-Ergebnissen.

Features:
- Deduplizierung ähnlicher Findings via Jaccard-Similarity
- Relevanz-Ranking basierend auf Query-Match
- Cross-Agent-Synthese mit Quellen-Attribution
- Early-Exit bei hoher Konfidenz
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.sub_agent import SubAgentResult

logger = logging.getLogger(__name__)


@dataclass
class RankedFinding:
    """Ein geranktes Finding mit Quelleninformation."""
    content: str
    source_agent: str
    source_id: str  # Dateipfad, Page-ID, Ticket-Key, etc.
    relevance_score: float
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None  # ID des Original-Findings
    finding_id: str = ""  # Eindeutige ID

    def __post_init__(self):
        if not self.finding_id:
            # Generiere ID aus Content-Hash
            import hashlib
            self.finding_id = hashlib.md5(
                f"{self.source_agent}:{self.content[:100]}".encode()
            ).hexdigest()[:8]


@dataclass
class CoordinatedResult:
    """Koordiniertes Ergebnis aller Sub-Agents."""
    total_findings: int
    unique_findings: int
    duplicates_removed: int
    ranked_findings: List[RankedFinding]
    synthesis: str  # Kombinierte Zusammenfassung
    top_source: str  # Agent mit besten Ergebnissen
    agents_used: List[str]
    high_confidence: bool = False  # True wenn ein Agent sehr sicher ist

    def to_context_block(self) -> str:
        """Formatiert das Ergebnis als System-Context-Block."""
        if not self.ranked_findings:
            return ""

        lines = [
            "## Sub-Agent Recherche-Ergebnisse",
            "",
            f"*{self.unique_findings} relevante Findings aus {len(self.agents_used)} Quellen*",
            ""
        ]

        # Nach Agent gruppieren
        by_agent: Dict[str, List[RankedFinding]] = defaultdict(list)
        for f in self.ranked_findings:
            if not f.is_duplicate:
                by_agent[f.source_agent].append(f)

        for agent, findings in by_agent.items():
            agent_display = agent.replace("_", " ").title()
            lines.append(f"### {agent_display} ({len(findings)} Treffer)")
            for f in findings[:5]:  # Max 5 pro Agent
                source_hint = f"[{f.source_id}]" if f.source_id else ""
                # Kürze lange Findings
                content_short = f.content[:150]
                if len(f.content) > 150:
                    content_short += "..."
                lines.append(f"- {source_hint} {content_short}")
            lines.append("")

        # Synthese am Ende
        if self.synthesis:
            lines.append("### Zusammenfassung")
            lines.append(self.synthesis)
            lines.append("")

        return "\n".join(lines)


class SubAgentCoordinator:
    """
    Koordiniert Sub-Agent-Ergebnisse.

    Features:
    1. Deduplizierung ähnlicher Findings
    2. Relevanz-Ranking
    3. Cross-Agent-Synthese
    4. Early-Exit bei hoher Konfidenz
    """

    SIMILARITY_THRESHOLD = 0.65  # Für Duplikat-Erkennung (etwas niedriger für mehr Dedupe)
    HIGH_CONFIDENCE_THRESHOLD = 0.85  # Für Early-Exit
    MAX_FINDINGS_PER_AGENT = 10  # Limit pro Agent

    def __init__(self):
        self._findings: List[RankedFinding] = []
        self._query: str = ""

    async def process_results(
        self,
        results: List[SubAgentResult],
        query: str
    ) -> CoordinatedResult:
        """
        Verarbeitet Sub-Agent-Ergebnisse.

        1. Extrahiert alle Findings
        2. Berechnet Relevanz-Scores
        3. Erkennt Duplikate via Similarity
        4. Rankt nach Relevanz
        5. Erstellt synthetisierte Zusammenfassung

        Args:
            results: Liste von SubAgentResult
            query: Die ursprüngliche User-Query

        Returns:
            CoordinatedResult mit gerankten, deduplizierten Findings
        """
        self._query = query
        self._findings = []

        if not results:
            return CoordinatedResult(
                total_findings=0,
                unique_findings=0,
                duplicates_removed=0,
                ranked_findings=[],
                synthesis="",
                top_source="",
                agents_used=[]
            )

        # 1. Findings aus allen Agents extrahieren
        for result in results:
            if not result.success:
                continue

            agent_findings = self._extract_findings(result)
            self._findings.extend(agent_findings)

        total_findings = len(self._findings)

        if not self._findings:
            return CoordinatedResult(
                total_findings=0,
                unique_findings=0,
                duplicates_removed=0,
                ranked_findings=[],
                synthesis="Keine relevanten Ergebnisse gefunden.",
                top_source="",
                agents_used=[r.agent_name for r in results if r.success]
            )

        # 2. Relevanz-Scores berechnen
        self._calculate_relevance_scores()

        # 3. Duplikate erkennen
        self._detect_duplicates()

        # 4. Nach Relevanz sortieren (ohne Duplikate)
        unique_findings = [f for f in self._findings if not f.is_duplicate]
        unique_findings.sort(key=lambda f: f.relevance_score, reverse=True)

        duplicates_removed = total_findings - len(unique_findings)

        # 5. Top-Source ermitteln
        agent_scores: Dict[str, float] = defaultdict(float)
        for f in unique_findings:
            agent_scores[f.source_agent] += f.relevance_score

        top_source = max(agent_scores.items(), key=lambda x: x[1])[0] if agent_scores else ""

        # 6. High-Confidence Check
        high_confidence = any(f.relevance_score >= self.HIGH_CONFIDENCE_THRESHOLD for f in unique_findings)

        # 7. Synthese erstellen
        synthesis = self._create_synthesis(unique_findings, query)

        logger.debug(
            f"[SubAgentCoordinator] {total_findings} total → {len(unique_findings)} unique "
            f"(-{duplicates_removed} dupes), top: {top_source}"
        )

        return CoordinatedResult(
            total_findings=total_findings,
            unique_findings=len(unique_findings),
            duplicates_removed=duplicates_removed,
            ranked_findings=unique_findings,
            synthesis=synthesis,
            top_source=top_source,
            agents_used=list(set(r.agent_name for r in results if r.success)),
            high_confidence=high_confidence
        )

    def _extract_findings(self, result: SubAgentResult) -> List[RankedFinding]:
        """Extrahiert Findings aus einem SubAgentResult."""
        findings = []

        # Key-Findings als separate Findings
        for i, finding in enumerate(result.key_findings[:self.MAX_FINDINGS_PER_AGENT]):
            source_id = result.sources[i] if i < len(result.sources) else ""
            findings.append(RankedFinding(
                content=finding,
                source_agent=result.agent_name,
                source_id=source_id,
                relevance_score=0.0  # Wird später berechnet
            ))

        # Summary als zusätzliches Finding wenn vorhanden
        if result.summary and len(result.summary) > 50:
            findings.append(RankedFinding(
                content=result.summary[:300],
                source_agent=result.agent_name,
                source_id="summary",
                relevance_score=0.0
            ))

        return findings

    def _calculate_relevance_scores(self) -> None:
        """Berechnet Relevanz-Scores für alle Findings."""
        query_tokens = self._tokenize(self._query)
        query_set = set(query_tokens)

        for finding in self._findings:
            content_tokens = self._tokenize(finding.content)
            content_set = set(content_tokens)

            if not query_set or not content_set:
                finding.relevance_score = 0.1
                continue

            # Jaccard-Ähnlichkeit als Basis
            intersection = len(query_set & content_set)
            union = len(query_set | content_set)
            jaccard = intersection / union if union > 0 else 0

            # Coverage-Bonus: Wie viele Query-Tokens wurden gefunden?
            coverage = intersection / len(query_set) if query_set else 0

            # Kombinierter Score
            score = (jaccard * 0.4) + (coverage * 0.6)

            # Bonus für Quellen-Attribution
            if finding.source_id and finding.source_id != "summary":
                score += 0.1

            finding.relevance_score = min(1.0, score)

    def _detect_duplicates(self) -> None:
        """Erkennt semantisch ähnliche Findings via Jaccard-Similarity."""
        n = len(self._findings)

        for i in range(n):
            if self._findings[i].is_duplicate:
                continue

            for j in range(i + 1, n):
                if self._findings[j].is_duplicate:
                    continue

                # Similarity berechnen
                similarity = self._calculate_similarity(
                    self._findings[i].content,
                    self._findings[j].content
                )

                if similarity >= self.SIMILARITY_THRESHOLD:
                    # Das Finding mit niedrigerem Score als Duplikat markieren
                    if self._findings[i].relevance_score >= self._findings[j].relevance_score:
                        self._findings[j].is_duplicate = True
                        self._findings[j].duplicate_of = self._findings[i].finding_id
                    else:
                        self._findings[i].is_duplicate = True
                        self._findings[i].duplicate_of = self._findings[j].finding_id

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        Berechnet Jaccard-Similarity zwischen zwei Texten.

        Verwendet Token-basierte Similarity für Geschwindigkeit.
        """
        tokens1 = set(self._tokenize(text1))
        tokens2 = set(self._tokenize(text2))

        if not tokens1 or not tokens2:
            return 0.0

        intersection = len(tokens1 & tokens2)
        union = len(tokens1 | tokens2)

        return intersection / union if union > 0 else 0.0

    def _tokenize(self, text: str) -> List[str]:
        """Tokenisiert Text (lowercase, alphanumerisch)."""
        if not text:
            return []
        return re.findall(r'\b[a-zA-Z0-9äöüß]{2,}\b', text.lower())

    def _create_synthesis(
        self,
        ranked_findings: List[RankedFinding],
        query: str
    ) -> str:
        """
        Erstellt eine synthetisierte Zusammenfassung.

        Gruppiert Findings nach Agent und fasst zusammen.
        """
        if not ranked_findings:
            return "Keine relevanten Informationen gefunden."

        # Top-Findings nach Agent gruppieren
        by_agent: Dict[str, List[str]] = defaultdict(list)
        for f in ranked_findings[:15]:  # Top 15
            by_agent[f.source_agent].append(f.content[:100])

        # Kurze Synthese erstellen
        parts = []
        for agent, contents in by_agent.items():
            agent_display = agent.replace("_", " ").title()
            count = len(contents)
            # Erstes Finding als Beispiel
            example = contents[0] if contents else ""
            parts.append(f"**{agent_display}** ({count}): {example}...")

        if not parts:
            return "Recherche abgeschlossen, aber keine eindeutigen Ergebnisse."

        return " | ".join(parts[:3])  # Max 3 Agents in Synthese


def format_sub_agent_results(results: List[SubAgentResult]) -> str:
    """
    Legacy-Funktion für Kompatibilität.

    Verwendet jetzt den Coordinator intern.
    """
    import asyncio

    coordinator = SubAgentCoordinator()

    # Synchroner Wrapper für async Funktion
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Im async Context - direkt await verwenden geht nicht
            # Fallback auf einfache Formatierung
            return _simple_format(results)
        else:
            coordinated = loop.run_until_complete(
                coordinator.process_results(results, "")
            )
            return coordinated.to_context_block()
    except RuntimeError:
        # Kein Event Loop - einfache Formatierung
        return _simple_format(results)


def _simple_format(results: List[SubAgentResult]) -> str:
    """Einfache Formatierung ohne Koordination (Fallback)."""
    if not results:
        return ""

    parts = ["## Sub-Agent Ergebnisse\n"]
    for r in results:
        if not r.success:
            continue
        parts.append(f"### {r.agent_name}")
        for finding in r.key_findings[:5]:
            parts.append(f"- {finding}")
        if r.summary:
            parts.append(f"\n{r.summary[:200]}...")
        parts.append("")

    return "\n".join(parts)
