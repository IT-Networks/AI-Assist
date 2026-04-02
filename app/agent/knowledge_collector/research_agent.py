"""
ResearchAgent – Spezialisierter Sub-Agent für systematische Fakten-Extraktion.

Unterschied zum WikiAgent:
- WikiAgent: Beantwortet ad-hoc Fragen durch Confluence-Suche
- ResearchAgent: Extrahiert ALLE Fakten aus gegebenen Seiten systematisch

Unterschied zum KnowledgeAgent:
- KnowledgeAgent: Sucht breit über Handbuch + PDFs + Skills
- ResearchAgent: Fokussiert auf tiefe Extraktion aus einer spezifischen Quellenliste
"""

import json
import logging
from typing import Callable, List, Optional, Awaitable

from app.agent.sub_agent import SubAgent, SubAgentResult
from app.agent.knowledge_collector.models import PageNode, ResearchFinding
from app.agent.knowledge_collector.source_provider import SourceProvider

logger = logging.getLogger(__name__)

# Basis-System-Prompt (wird mit Provider-spezifischem Teil ergänzt)
_BASE_DESCRIPTION = """Du bist ein Fakten-Extraktions-Agent. Deine Aufgabe ist es,
ALLE relevanten Fakten, Definitionen, Prozesse und Entscheidungen aus
den zugewiesenen Quellen zu extrahieren.

EXTRAKTION - Erfasse JEDEN relevanten Fakt als eigenständiges Finding:
- Fakten: Konkrete Aussagen, Zahlen, Konfigurationen, Namen
- Prozesse: Abläufe, Workflows, Reihenfolgen, Abhängigkeiten
- Definitionen: Begriffserklärungen, Zuständigkeiten, Rollen
- Entscheidungen: Warum wurde etwas so gemacht? Begründungen

REGELN:
- Erfinde KEINE Informationen - nur was in den Quellen steht
- Jeder Fakt muss einer konkreten Quelle zugeordnet sein
- Auch scheinbar unwichtige Details können relevant sein
- Bei Widersprüchen: Beide Versionen mit Quelle erfassen

OUTPUT: Antworte abschließend NUR mit diesem JSON-Format:
{
  "findings": [
    {"fact": "Konkreter Fakt...", "category": "fact|process|decision|definition", "confidence": "high|medium|low"},
    ...
  ],
  "sources": ["page_id:Seitentitel", ...]
}"""


class ResearchAgent(SubAgent):
    """
    Spezialisierter Sub-Agent für die Fakten-Extraktion.

    Wird vom ResearchOrchestrator mit einer Liste von Seiten beauftragt
    und extrahiert systematisch alle Fakten daraus.

    Nutzung:
        agent = ResearchAgent.for_provider(confluence_provider)
        findings = await agent.run_research(pages, topic, llm_client, tool_registry)
    """

    name = "research_agent"
    display_name = "Research-Agent"
    description = _BASE_DESCRIPTION
    allowed_tools: List[str] = []  # Wird dynamisch via for_provider() gesetzt
    content_extraction_tools = ["read_confluence_page"]
    max_iterations = 8

    def __init__(self):
        super().__init__()
        self._provider_name: str = "unknown"

    @classmethod
    def for_provider(cls, provider: SourceProvider) -> "ResearchAgent":
        """
        Factory: Erstellt einen ResearchAgent mit provider-spezifischer Tool-Whitelist.

        Args:
            provider: SourceProvider der die Tools und den Prompt definiert

        Returns:
            Konfigurierter ResearchAgent
        """
        agent = cls()
        agent.allowed_tools = provider.get_research_agent_tools()
        agent.description = _BASE_DESCRIPTION + "\n\n" + provider.get_agent_description()
        agent._provider_name = provider.name
        agent.display_name = f"Research-Agent ({provider.display_name})"

        # Content-Extraktion für große Dokumente
        if "read_confluence_page" in agent.allowed_tools:
            agent.content_extraction_tools = ["read_confluence_page"]
        else:
            agent.content_extraction_tools = []

        return agent

    async def run_research(
        self,
        pages: List[PageNode],
        topic: str,
        llm_client,
        tool_registry,
        on_finding: Optional[Callable[[ResearchFinding], Awaitable[None]]] = None,
    ) -> List[ResearchFinding]:
        """
        Führt Research über eine Liste von Seiten aus.

        Args:
            pages: Zu analysierende Seiten/Dokumente
            topic: Das übergeordnete Thema
            llm_client: LLMClient-Singleton
            tool_registry: ToolRegistry-Singleton
            on_finding: Optional Callback pro gefundenem Fakt (für Live-Events)

        Returns:
            Liste von ResearchFinding-Objekten
        """
        # Kontext für den Agent: welche Seiten er analysieren soll
        pages_context = "\n".join([
            f"- '{p.title}' (ID: {p.page_id}, Typ: {p.source_type})"
            for p in pages
        ])

        context = (
            f"THEMA: {topic}\n\n"
            f"ZU ANALYSIERENDE QUELLEN:\n{pages_context}\n\n"
            f"Lies JEDE dieser Quellen und extrahiere alle Fakten zum Thema."
        )

        result = await self.run(
            query=f"Extrahiere alle Fakten zum Thema '{topic}' aus den zugewiesenen Quellen.",
            llm_client=llm_client,
            tool_registry=tool_registry,
            conversation_context=context,
        )

        findings = self._parse_findings(result, pages)

        # Callbacks für Live-Events
        if on_finding:
            for finding in findings:
                try:
                    await on_finding(finding)
                except Exception as e:
                    logger.debug(f"[ResearchAgent] Finding-Callback Fehler (non-critical): {e}")

        return findings

    def _parse_findings(
        self,
        result: SubAgentResult,
        pages: List[PageNode],
    ) -> List[ResearchFinding]:
        """
        Parst ResearchFindings aus dem SubAgentResult.

        Versucht JSON-Parsing, fällt auf Freitext-Extraktion zurück.
        """
        findings: List[ResearchFinding] = []

        # Page-Lookup für Quellen-Zuordnung
        page_lookup = {p.page_id: p for p in pages}
        first_page = pages[0] if pages else None

        # Versuche JSON-Parsing aus key_findings oder summary
        raw_findings = self._try_parse_json_findings(result)

        if raw_findings:
            for rf in raw_findings:
                fact = rf.get("fact", "")
                if not fact:
                    continue
                findings.append(ResearchFinding(
                    fact=fact,
                    source_page_id=first_page.page_id if first_page else "",
                    source_title=first_page.title if first_page else "",
                    source_url=first_page.url if first_page else "",
                    source_type=first_page.source_type if first_page else "page",
                    source_provider=self._provider_name,
                    confidence=rf.get("confidence", "medium"),
                    category=rf.get("category", "fact"),
                ))
        elif result.key_findings:
            # Fallback: key_findings als einzelne Fakten
            for kf in result.key_findings:
                findings.append(ResearchFinding(
                    fact=kf,
                    source_page_id=first_page.page_id if first_page else "",
                    source_title=first_page.title if first_page else "",
                    source_url=first_page.url if first_page else "",
                    source_type=first_page.source_type if first_page else "page",
                    source_provider=self._provider_name,
                    confidence="medium",
                    category="fact",
                ))
        elif result.summary:
            # Letzter Fallback: Summary als einzelnes Finding
            findings.append(ResearchFinding(
                fact=result.summary[:500],
                source_page_id=first_page.page_id if first_page else "",
                source_title=first_page.title if first_page else "",
                source_url=first_page.url if first_page else "",
                source_type=first_page.source_type if first_page else "page",
                source_provider=self._provider_name,
                confidence="low",
                category="fact",
            ))

        return findings

    @staticmethod
    def _try_parse_json_findings(result: SubAgentResult) -> List[dict]:
        """Versucht findings-Array aus dem SubAgentResult JSON zu parsen."""
        # Versuche summary als JSON
        for text in [result.summary, str(result.key_findings)]:
            if not text:
                continue
            try:
                # Suche nach JSON-Objekt mit findings-Key
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(text[start:end])
                    if "findings" in data and isinstance(data["findings"], list):
                        return data["findings"]
            except (json.JSONDecodeError, KeyError):
                continue
        return []
