"""
Tests fuer KnowledgeGraphLinker und Graph-Erweiterungen.

Testet:
- Neue NodeTypes und EdgeTypes
- KnowledgeGraphLinker: Knoten/Kanten-Erstellung
- Tag-Matching fuer Code-Verlinkung
- RELATED_TO Kanten bei gemeinsamen Tags
- Graceful Skip wenn kein Graph aktiv
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.agent.knowledge_collector.models import ResearchFinding
from app.services.knowledge_graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    KnowledgeGraphStore,
    NodeType,
)
from app.services.knowledge_graph_linker import KnowledgeGraphLinker


# ══════════════════════════════════════════════════════════════════════════════
# NodeType / EdgeType Enums
# ══════════════════════════════════════════════════════════════════════════════

class TestNewEnums:
    """Tests fuer die neuen Knowledge-Enums."""

    def test_knowledge_node_types_exist(self):
        assert NodeType.KNOWLEDGE_DOC == "knowledge_doc"
        assert NodeType.CONFLUENCE_PAGE == "confluence_page"
        assert NodeType.HANDBOOK_SERVICE == "handbook_service"
        assert NodeType.PROCESS == "process"

    def test_knowledge_edge_types_exist(self):
        assert EdgeType.DOCUMENTS == "documents"
        assert EdgeType.DESCRIBES == "describes"
        assert EdgeType.RELATED_TO == "related_to"

    def test_original_types_unchanged(self):
        """Bestehende Typen sind nicht veraendert."""
        assert NodeType.CLASS == "class"
        assert NodeType.INTERFACE == "interface"
        assert EdgeType.EXTENDS == "extends"
        assert EdgeType.CALLS == "calls"


# ══════════════════════════════════════════════════════════════════════════════
# KnowledgeGraphStore neue Methoden
# ══════════════════════════════════════════════════════════════════════════════

class TestGraphStoreNewMethods:
    """Tests fuer search_nodes und get_nodes_by_type."""

    @pytest.fixture
    def store(self, tmp_path):
        return KnowledgeGraphStore(str(tmp_path / "test_graph.db"))

    def test_search_nodes_by_name(self, store):
        store.add_node(GraphNode(id="c1", type=NodeType.CLASS, name="UserService"))
        store.add_node(GraphNode(id="c2", type=NodeType.CLASS, name="PaymentService"))
        store.add_node(GraphNode(id="c3", type=NodeType.CLASS, name="UserRepository"))

        results = store.search_nodes("User")
        assert len(results) == 2
        names = {n.name for n in results}
        assert "UserService" in names
        assert "UserRepository" in names

    def test_search_nodes_case_insensitive(self, store):
        store.add_node(GraphNode(id="c1", type=NodeType.CLASS, name="DeploymentConfig"))

        results = store.search_nodes("deployment")
        assert len(results) == 1
        assert results[0].name == "DeploymentConfig"

    def test_search_nodes_no_match(self, store):
        store.add_node(GraphNode(id="c1", type=NodeType.CLASS, name="Foo"))
        results = store.search_nodes("zzz_nonexistent")
        assert len(results) == 0

    def test_get_nodes_by_type(self, store):
        store.add_node(GraphNode(id="c1", type=NodeType.CLASS, name="A"))
        store.add_node(GraphNode(id="i1", type=NodeType.INTERFACE, name="B"))
        store.add_node(GraphNode(id="kb1", type=NodeType.KNOWLEDGE_DOC, name="Doc1",
                                 metadata={"tags": ["test"]}))

        kb_nodes = store.get_nodes_by_type(NodeType.KNOWLEDGE_DOC)
        assert len(kb_nodes) == 1
        assert kb_nodes[0].id == "kb1"

    def test_get_nodes_by_type_empty(self, store):
        results = store.get_nodes_by_type(NodeType.PROCESS)
        assert len(results) == 0


# ══════════════════════════════════════════════════════════════════════════════
# KnowledgeGraphLinker
# ══════════════════════════════════════════════════════════════════════════════

class TestKnowledgeGraphLinker:
    """Tests fuer den KnowledgeGraphLinker."""

    @pytest.fixture
    def store(self, tmp_path):
        return KnowledgeGraphStore(str(tmp_path / "test_graph.db"))

    @pytest.fixture
    def linker(self, store):
        return KnowledgeGraphLinker(store)

    @pytest.fixture
    def sample_findings(self):
        return [
            ResearchFinding(
                fact="Jenkins CI/CD Pipeline",
                source_page_id="12345",
                source_title="Deployment-Uebersicht",
                source_url="https://confluence.example.com/pages/12345",
                source_type="page",
                source_provider="confluence",
                category="fact",
            ),
            ResearchFinding(
                fact="Service XY hat 3 Aufrufvarianten",
                source_page_id="handbook:svc-xy",
                source_title="Service XY",
                source_url="",
                source_type="service",
                source_provider="handbook",
                category="definition",
            ),
        ]

    @pytest.fixture
    def sample_metadata(self):
        return {
            "title": "Deployment-Prozess",
            "space": "DEV",
            "date": "2026-04-01",
            "confidence": "high",
            "tags": ["deployment", "jenkins", "ci-cd"],
            "findings_count": 18,
        }

    def test_creates_knowledge_node(self, linker, store, sample_metadata, sample_findings):
        """Erstellt KNOWLEDGE_DOC Knoten."""
        edges = linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)

        kb_nodes = store.get_nodes_by_type(NodeType.KNOWLEDGE_DOC)
        assert len(kb_nodes) == 1
        assert kb_nodes[0].name == "Deployment-Prozess"
        assert kb_nodes[0].file_path == "dev/deployment.md"

    def test_links_confluence_sources(self, linker, store, sample_metadata, sample_findings):
        """Erstellt CONFLUENCE_PAGE Knoten + REFERENCES Kante."""
        linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)

        conf_nodes = store.get_nodes_by_type(NodeType.CONFLUENCE_PAGE)
        assert len(conf_nodes) == 1
        assert conf_nodes[0].name == "Deployment-Uebersicht"

    def test_links_handbook_sources(self, linker, store, sample_metadata, sample_findings):
        """Erstellt HANDBOOK_SERVICE Knoten + DESCRIBES Kante."""
        linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)

        hb_nodes = store.get_nodes_by_type(NodeType.HANDBOOK_SERVICE)
        assert len(hb_nodes) == 1
        assert hb_nodes[0].name == "Service XY"

    def test_links_code_elements_by_tag(self, linker, store, sample_metadata, sample_findings):
        """Tag-Matching: 'deployment' im Tag findet 'DeploymentService' Code-Knoten."""
        # Code-Knoten anlegen die via Tag gematcht werden
        store.add_node(GraphNode(id="cls:DeploymentService", type=NodeType.CLASS, name="DeploymentService"))
        store.add_node(GraphNode(id="cls:UserService", type=NodeType.CLASS, name="UserService"))

        linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)

        # DeploymentService sollte DOCUMENTS-Kante haben, UserService nicht
        edges = store.get_edges_from("kb:deployment-prozess")
        documents_edges = [e for e in edges if e.type == EdgeType.DOCUMENTS]
        assert len(documents_edges) >= 1
        target_ids = {e.to_id for e in documents_edges}
        assert "cls:DeploymentService" in target_ids

    def test_links_related_knowledge(self, linker, store, sample_metadata, sample_findings):
        """RELATED_TO Kante bei >= 2 gemeinsamen Tags."""
        # Erstes KB-Doc anlegen
        store.add_node(GraphNode(
            id="kb:ci-cd-pipeline", type=NodeType.KNOWLEDGE_DOC, name="CI/CD Pipeline",
            metadata={"tags": ["ci-cd", "jenkins", "build"]},
        ))

        # Zweites KB-Doc via Linker (hat tags: deployment, jenkins, ci-cd)
        linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)

        # Sollte RELATED_TO Kante haben (jenkins + ci-cd als gemeinsame Tags)
        edges = store.get_edges_from("kb:deployment-prozess")
        related_edges = [e for e in edges if e.type == EdgeType.RELATED_TO]
        assert len(related_edges) == 1
        assert related_edges[0].to_id == "kb:ci-cd-pipeline"

    def test_no_related_with_single_tag_overlap(self, linker, store, sample_metadata, sample_findings):
        """Kein RELATED_TO bei nur 1 gemeinsamen Tag."""
        store.add_node(GraphNode(
            id="kb:other", type=NodeType.KNOWLEDGE_DOC, name="Other",
            metadata={"tags": ["jenkins", "monitoring"]},  # Nur 'jenkins' gemeinsam
        ))

        linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)

        edges = store.get_edges_from("kb:deployment-prozess")
        related_edges = [e for e in edges if e.type == EdgeType.RELATED_TO]
        assert len(related_edges) == 0

    def test_returns_edge_count(self, linker, store, sample_metadata, sample_findings):
        """Gibt korrekte Anzahl erstellter Kanten zurueck."""
        edges = linker.link_knowledge_document("dev/deployment.md", sample_metadata, sample_findings)
        assert edges >= 2  # Mindestens Confluence + Handbook Source

    def test_empty_title_skips(self, linker, store, sample_findings):
        """Leerer Titel wird uebersprungen."""
        edges = linker.link_knowledge_document("x.md", {"title": ""}, sample_findings)
        assert edges == 0

    def test_deduplicates_sources(self, linker, store, sample_metadata):
        """Doppelte Quellen werden dedupliziert."""
        findings = [
            ResearchFinding(fact="F1", source_page_id="123", source_title="Page A",
                          source_url="u1", source_type="page", source_provider="confluence"),
            ResearchFinding(fact="F2", source_page_id="123", source_title="Page A",
                          source_url="u1", source_type="page", source_provider="confluence"),
        ]

        linker.link_knowledge_document("dev/test.md", sample_metadata, findings)

        conf_nodes = store.get_nodes_by_type(NodeType.CONFLUENCE_PAGE)
        assert len(conf_nodes) == 1  # Nur 1 trotz 2 Findings
