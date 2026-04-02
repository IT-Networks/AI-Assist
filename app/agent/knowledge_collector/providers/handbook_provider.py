"""HandbookProvider – Internes HTML-Handbuch als Wissensquelle für den Knowledge Collector."""

import logging
from typing import List, Optional, Set

from app.agent.knowledge_collector.models import PageNode
from app.agent.knowledge_collector.source_provider import SourceProvider
from app.core.config import settings

logger = logging.getLogger(__name__)


class HandbookProvider(SourceProvider):
    """
    Internes HTML-Handbuch als Wissensquelle.

    Discovery-Strategie:
    - Nutzt den bestehenden HandbookIndexer (FTS5) für die Suche
    - Gruppiert Ergebnisse nach Service-Name
    - Erstellt PageNode-Objekte mit source_type="handbook"
    """

    @property
    def name(self) -> str:
        return "handbook"

    @property
    def display_name(self) -> str:
        return "Internes Handbuch"

    @property
    def description(self) -> str:
        return (
            "HTML-Handbuch mit Service-Dokumentationen, Feldbeschreibungen, "
            "Aufrufvarianten und technischen Schnittstellenbeschreibungen. "
            "Enthält detaillierte Ein-/Ausgabefelder pro Service."
        )

    def is_available(self) -> bool:
        return settings.handbook.enabled and bool(settings.handbook.path)

    async def discover(
        self,
        topic: str,
        root_id: Optional[str] = None,
        max_depth: int = 3,
        space_key: Optional[str] = None,
    ) -> List[PageNode]:
        try:
            from app.services.handbook_indexer import get_handbook_indexer
            indexer = get_handbook_indexer()
        except Exception as e:
            logger.warning(f"[HandbookProvider] HandbookIndexer nicht verfügbar: {e}")
            return []

        try:
            results = indexer.search(topic, top_k=15)
        except Exception as e:
            logger.warning(f"[HandbookProvider] Suche fehlgeschlagen: {e}")
            return []

        nodes: List[PageNode] = []
        seen_services: Set[str] = set()

        for result in results:
            service_name = result.get("service_name", "")

            # Service-Seiten gruppieren (nicht jeder Tab einzeln)
            if service_name and service_name not in seen_services:
                seen_services.add(service_name)
                nodes.append(PageNode(
                    page_id=f"handbook:{service_name}",
                    title=f"Service: {service_name}",
                    url="",
                    space_key="handbook",
                    depth=0,
                    source_type="service",
                    source_provider="handbook",
                    metadata={"service_id": service_name},
                ))
            elif not service_name:
                file_path = result.get("file_path", "")
                title = result.get("title", "") or result.get("headings", "") or "Handbuch-Seite"
                # Duplikate vermeiden
                node_id = f"handbook:{file_path}" if file_path else f"handbook:{title}"
                if node_id not in seen_services:
                    seen_services.add(node_id)
                    nodes.append(PageNode(
                        page_id=node_id,
                        title=title[:100],
                        url="",
                        space_key="handbook",
                        depth=0,
                        source_type="handbook",
                        source_provider="handbook",
                        metadata={"file_path": file_path},
                    ))

        logger.debug(f"[HandbookProvider] {len(nodes)} Einträge gefunden für '{topic}'")
        return nodes

    def get_research_agent_tools(self) -> List[str]:
        return [
            "search_handbook",
            "get_service_info",
            "search_pdf",
            "read_pdf_pages",
        ]

    def get_agent_description(self) -> str:
        return (
            "Du analysierst das interne HTML-Handbuch. "
            "Nutze get_service_info(service_id=...) für detaillierte Service-Informationen "
            "(Tabs, Ein-/Ausgabefelder, Aufrufvarianten). "
            "Nutze search_handbook(query=...) für breitere Suche nach Feldern und Prozessen. "
            "Bei PDFs im Handbuch: get_pdf_info(filename=...) dann read_pdf_pages(filename=..., start_page=..., end_page=...). "
            "Extrahiere Service-Beschreibungen, Feld-Definitionen und Prozess-Dokumentation."
        )
