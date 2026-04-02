"""ConfluenceProvider – Confluence Wiki als Wissensquelle für den Knowledge Collector."""

import logging
from typing import List, Optional

from app.agent.knowledge_collector.models import PageNode
from app.agent.knowledge_collector.source_provider import SourceProvider
from app.core.config import settings

logger = logging.getLogger(__name__)


class ConfluenceProvider(SourceProvider):
    """
    Confluence Wiki als Wissensquelle.

    Discovery-Strategie:
    1. Wenn root_id → get_child_pages() rekursiv bis max_depth
    2. Wenn nur topic → search() in default_space, dann Baum ab Top-Treffer
    """

    @property
    def name(self) -> str:
        return "confluence"

    @property
    def display_name(self) -> str:
        return "Confluence Wiki"

    @property
    def description(self) -> str:
        return (
            "Confluence-Wiki mit Projektdokumentation, Architektur-Beschreibungen, "
            "Betriebshandbücher, Prozessbeschreibungen und technische Spezifikationen. "
            "Enthält Seiten mit Unterseiten-Hierarchie und PDF-Attachments."
        )

    def is_available(self) -> bool:
        return bool(settings.confluence.base_url)

    async def discover(
        self,
        topic: str,
        root_id: Optional[str] = None,
        max_depth: int = 3,
        space_key: Optional[str] = None,
    ) -> List[PageNode]:
        from app.services.confluence_client import ConfluenceClient

        client = ConfluenceClient()
        nodes: List[PageNode] = []

        logger.info(f"[ConfluenceProvider] discover: topic='{topic}', root_id={root_id}, max_depth={max_depth}")
        logger.info(f"[ConfluenceProvider] base_url={settings.confluence.base_url}, default_space={settings.confluence.default_space}")

        if root_id:
            # Strategie 1: Root-Seite + Unterseiten rekursiv
            try:
                root_page = await client.get_page_by_id(root_id)
                logger.info(f"[ConfluenceProvider] Root-Seite geladen: '{root_page.get('title', '?')}'")
                root_node = PageNode(
                    page_id=root_page["id"],
                    title=root_page["title"],
                    url=root_page.get("url", ""),
                    space_key=root_page.get("space", ""),
                    depth=0,
                    source_provider="confluence",
                    source_type="page",
                )
                root_node.children = await self._get_children_recursive(
                    client, root_id, root_page.get("space", ""), 1, max_depth
                )
                nodes.append(root_node)
            except Exception as e:
                logger.error(f"[ConfluenceProvider] Root-Seite {root_id} nicht abrufbar: {e}", exc_info=True)
                raise  # Nicht schlucken — Fehler nach oben propagieren
        else:
            # Strategie 2: Suche nach Thema, dann Unterseiten der Top-Treffer
            search_space = space_key or settings.confluence.default_space or None  # User-space_key hat Vorrang
            logger.info(f"[ConfluenceProvider] Suche nach '{topic}' in space={search_space}")
            try:
                search_results = await client.search(topic, space_key=search_space, limit=10)
                logger.info(f"[ConfluenceProvider] Suche ergab {len(search_results)} Treffer")
                for i, result in enumerate(search_results[:5]):
                    logger.info(f"[ConfluenceProvider] Treffer {i+1}: '{result.get('title', '?')}' (ID: {result.get('id', '?')})")
                    node = PageNode(
                        page_id=result["id"],
                        title=result["title"],
                        url=result.get("url", ""),
                        space_key=result.get("space", ""),
                        depth=0,
                        source_provider="confluence",
                        source_type="page",
                    )
                    # Unterseiten nur fuer Top-3 (Budget)
                    if len(nodes) < 3 and max_depth > 0:
                        node.children = await self._get_children_recursive(
                            client, result["id"], result.get("space", ""), 1, max_depth
                        )
                    nodes.append(node)
            except Exception as e:
                logger.error(f"[ConfluenceProvider] Suche fehlgeschlagen: {e}", exc_info=True)
                raise  # Nicht schlucken — Fehler nach oben propagieren

        logger.info(f"[ConfluenceProvider] Ergebnis: {len(nodes)} Seiten entdeckt")
        return nodes

    async def _get_children_recursive(
        self,
        client,
        page_id: str,
        space_key: str,
        current_depth: int,
        max_depth: int,
    ) -> List[PageNode]:
        """Rekursive Traversierung der Kind-Seiten."""
        if current_depth >= max_depth:
            return []

        try:
            children = await client.get_child_pages(page_id)
        except Exception as e:
            logger.debug(f"[ConfluenceProvider] Keine Kind-Seiten für {page_id}: {e}")
            return []

        nodes = []
        for child in children:
            node = PageNode(
                page_id=child["id"],
                title=child["title"],
                url=child.get("url", ""),
                space_key=child.get("space_key", space_key),
                depth=current_depth,
                source_provider="confluence",
                source_type="page",
            )
            node.children = await self._get_children_recursive(
                client, child["id"], space_key, current_depth + 1, max_depth
            )
            nodes.append(node)

        return nodes

    def get_research_agent_tools(self) -> List[str]:
        return [
            "read_confluence_page",
            "list_confluence_pdfs",
            "read_confluence_pdf",
        ]

    def get_agent_description(self) -> str:
        return (
            "Du analysierst Confluence-Wiki-Seiten. "
            "Lies jede zugewiesene Seite mit read_confluence_page(page_id=...). "
            "Prüfe mit list_confluence_pdfs(page_id=...) ob relevante PDF-Attachments existieren. "
            "Bei relevanten PDFs: read_confluence_pdf(page_id=..., filename=...) mit query für Relevanz-Score. "
            "Extrahiere ALLE Fakten, Definitionen und Prozesse aus dem Seiteninhalt."
        )
