"""
KnowledgeGraphLinker – Verbindet Knowledge-Base-Dokumente mit dem Knowledge Graph.

Wird nach jedem Research-Run aufgerufen und erstellt:
- KNOWLEDGE_DOC Knoten für jede MD-Datei
- CONFLUENCE_PAGE / HANDBOOK_SERVICE Knoten für Quellen
- REFERENCES Kanten zu Quellseiten
- DOCUMENTS Kanten zu Code-Elementen (via Tag-Matching)
- RELATED_TO Kanten zwischen thematisch verwandten MDs
"""

import logging
from typing import Dict, List, Set

from app.agent.knowledge_collector.models import ResearchFinding
from app.services.knowledge_graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    KnowledgeGraphStore,
    NodeType,
)

logger = logging.getLogger(__name__)


class KnowledgeGraphLinker:
    """Verbindet Knowledge-Base-Dokumente mit dem Knowledge Graph."""

    def __init__(self, graph_store: KnowledgeGraphStore):
        self._store = graph_store

    def link_knowledge_document(
        self,
        md_path: str,
        metadata: Dict,
        findings: List[ResearchFinding],
    ) -> int:
        """
        Erstellt Graph-Knoten und Kanten fuer ein Knowledge-Dokument.

        Args:
            md_path: Pfad zur MD-Datei
            metadata: Frontmatter-Daten (title, space, tags, etc.)
            findings: Extrahierte ResearchFindings (fuer Quellen-Verlinkung)

        Returns:
            Anzahl erstellter Kanten
        """
        title = metadata.get("title", "")
        if not title:
            return 0

        slug = self._slugify(title)
        kb_node_id = f"kb:{slug}"

        # 1. Knowledge-Knoten erstellen
        kb_node = GraphNode(
            id=kb_node_id,
            type=NodeType.KNOWLEDGE_DOC,
            name=title,
            file_path=md_path,
            metadata={
                "space": metadata.get("space", ""),
                "date": metadata.get("date", ""),
                "confidence": metadata.get("confidence", ""),
                "tags": metadata.get("tags", []),
                "findings_count": metadata.get("findings_count", len(findings)),
            },
        )
        self._store.add_node(kb_node)

        edges_created = 0

        # 2. Quell-Seiten verlinken
        edges_created += self._link_sources(kb_node_id, metadata, findings)

        # 3. Code-Elemente verlinken (Tag-Matching)
        edges_created += self._link_code_elements(kb_node_id, metadata)

        # 4. Verwandte Knowledge-Docs verlinken
        edges_created += self._link_related_knowledge(kb_node_id, metadata)

        logger.info(f"[GraphLinker] {title}: {edges_created} Kanten erstellt")
        return edges_created

    def _link_sources(
        self,
        kb_node_id: str,
        metadata: Dict,
        findings: List[ResearchFinding],
    ) -> int:
        """Erstellt Knoten + REFERENCES/DESCRIBES-Kanten zu Quellseiten."""
        count = 0
        seen: Set[str] = set()

        for finding in findings:
            source_key = f"{finding.source_provider}:{finding.source_page_id}"
            if source_key in seen or not finding.source_page_id:
                continue
            seen.add(source_key)

            if finding.source_provider == "confluence":
                source_node = GraphNode(
                    id=f"conf:{finding.source_page_id}",
                    type=NodeType.CONFLUENCE_PAGE,
                    name=finding.source_title,
                    metadata={
                        "url": finding.source_url,
                        "space": metadata.get("space", ""),
                    },
                )
                self._store.add_node(source_node)
                self._store.add_edge(GraphEdge(
                    from_id=kb_node_id,
                    to_id=source_node.id,
                    type=EdgeType.REFERENCES,
                    weight=1.0,
                ))
                count += 1

            elif finding.source_provider == "handbook":
                source_node = GraphNode(
                    id=f"hb:{finding.source_page_id}",
                    type=NodeType.HANDBOOK_SERVICE,
                    name=finding.source_title,
                )
                self._store.add_node(source_node)
                self._store.add_edge(GraphEdge(
                    from_id=kb_node_id,
                    to_id=source_node.id,
                    type=EdgeType.DESCRIBES,
                    weight=1.0,
                ))
                count += 1

        return count

    def _link_code_elements(self, kb_node_id: str, metadata: Dict) -> int:
        """Sucht Code-Knoten die zu den Tags passen und erstellt DOCUMENTS-Kanten."""
        count = 0
        tags = metadata.get("tags", [])
        if not tags:
            return 0

        code_types = {NodeType.CLASS, NodeType.INTERFACE, NodeType.TABLE, NodeType.ENUM}

        for tag in tags:
            if len(tag) < 3:
                continue
            matching_nodes = self._store.search_nodes(tag, limit=5)
            for code_node in matching_nodes:
                if code_node.type in code_types and code_node.id != kb_node_id:
                    self._store.add_edge(GraphEdge(
                        from_id=kb_node_id,
                        to_id=code_node.id,
                        type=EdgeType.DOCUMENTS,
                        weight=0.7,
                        metadata={"matched_tag": tag},
                    ))
                    count += 1

        return count

    def _link_related_knowledge(self, kb_node_id: str, metadata: Dict) -> int:
        """Erstellt RELATED_TO-Kanten zu anderen KB-Docs mit ueberlappenden Tags."""
        count = 0
        tags = set(metadata.get("tags", []))
        if len(tags) < 2:
            return 0

        kb_nodes = self._store.get_nodes_by_type(NodeType.KNOWLEDGE_DOC)
        for other in kb_nodes:
            if other.id == kb_node_id:
                continue
            other_tags = set(other.metadata.get("tags", []))
            overlap = tags & other_tags
            if len(overlap) >= 2:
                weight = len(overlap) / max(len(tags), len(other_tags))
                self._store.add_edge(GraphEdge(
                    from_id=kb_node_id,
                    to_id=other.id,
                    type=EdgeType.RELATED_TO,
                    weight=round(weight, 2),
                    metadata={"shared_tags": list(overlap)},
                ))
                count += 1

        return count

    @staticmethod
    def _slugify(text: str) -> str:
        """Einfacher Slug fuer Node-IDs."""
        import re
        slug = text.lower().strip()
        replacements = {"ae": "ae", "oe": "oe", "ue": "ue", "ss": "ss"}
        for old, new in replacements.items():
            slug = slug.replace(old, new)
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
        return slug[:60] if slug else "untitled"
