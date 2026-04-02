"""
SourceProvider – Abstraktes Interface für Wissensquellen.

Jeder Provider repräsentiert eine durchsuchbare Wissensquelle
(Confluence, Handbuch, zukünftig: Jira, SharePoint, ...).

Neue Quelle hinzufügen:
1. SourceProvider-Subklasse implementieren (6 Methoden)
2. In config.yaml unter knowledge_base.sources aktivieren
3. In ResearchOrchestrator.__init__() registrieren
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from app.agent.knowledge_collector.models import PageNode


class SourceProvider(ABC):
    """
    Abstrakte Basis für Wissensquellen im Knowledge Collector.

    Jeder Provider kapselt:
    - Discovery: Welche Seiten/Dokumente gibt es zu einem Thema?
    - Tool-Config: Welche Tools braucht der ResearchAgent für diese Quelle?
    - Agent-Prompt: Wie soll der ResearchAgent mit dieser Quelle umgehen?
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Eindeutiger Identifier: 'confluence', 'handbook', 'jira', ..."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Anzeigename für UI/Logs: 'Confluence Wiki', 'Internes Handbuch', ..."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Beschreibung für LLM-Routing.

        Wird dem LLM präsentiert um zu entscheiden ob diese Quelle
        für ein gegebenes Thema relevant ist.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """
        Prüft ob der Provider konfiguriert und nutzbar ist.

        z.B. Confluence: base_url gesetzt?
        z.B. Handbuch: enabled && path gesetzt?
        """
        ...

    @abstractmethod
    async def discover(
        self,
        topic: str,
        root_id: Optional[str] = None,
        max_depth: int = 3,
        space_key: Optional[str] = None,
    ) -> List[PageNode]:
        """
        Entdeckt durchsuchbare Einheiten zu einem Thema.

        Args:
            topic: Das zu recherchierende Thema
            root_id: Optional: Start-ID (z.B. Confluence page_id)
            max_depth: Max. Rekursionstiefe für Baumstrukturen
            space_key: Optional: Space/Bereich für die Suche

        Returns:
            Liste von PageNode-Objekten (flach oder als Baum via .children)
        """
        ...

    @abstractmethod
    def get_research_agent_tools(self) -> List[str]:
        """
        Tool-Whitelist für den ResearchAgent.

        Confluence: ["read_confluence_page", "list_confluence_pdfs", "read_confluence_pdf"]
        Handbook:   ["search_handbook", "get_service_info", "search_pdf", "read_pdf_pages"]
        """
        ...

    @abstractmethod
    def get_agent_description(self) -> str:
        """
        Spezialisierter Prompt-Teil für den ResearchAgent.

        Erklärt dem Agent WIE er die Tools dieser Quelle nutzen soll.
        Wird an den Basis-System-Prompt angehängt.
        """
        ...
