"""
Tests für den Knowledge Collector.

Testet:
- Models (PageNode, ResearchFinding, etc.)
- KnowledgeStore (save, search, list, reindex)
- KnowledgeSynthesizer (Frontmatter, Fallback-Synthese)
- SourceProvider Interface
- Event-Type-Mapping
- ResearchAgent.for_provider() Factory
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from app.agent.knowledge_collector.models import (
    KnowledgeEntry,
    PageNode,
    ResearchFinding,
    ResearchPlan,
    ResearchProgress,
)
from app.services.knowledge_store import KnowledgeStore


# ══════════════════════════════════════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════════════════════════════════════


class TestPageNode:
    """Tests für PageNode Datenmodell."""

    def test_flat_list_single_node(self):
        """Einzelner Knoten ohne Kinder."""
        node = PageNode(page_id="1", title="Root", url="", space_key="DEV", depth=0)
        flat = node.flat_list()
        assert len(flat) == 1
        assert flat[0].page_id == "1"

    def test_flat_list_with_children(self):
        """Baum mit Kindern wird korrekt abgeflacht."""
        child1 = PageNode(page_id="2", title="Child 1", url="", space_key="DEV", depth=1)
        child2 = PageNode(page_id="3", title="Child 2", url="", space_key="DEV", depth=1)
        grandchild = PageNode(page_id="4", title="Grandchild", url="", space_key="DEV", depth=2)
        child1.children = [grandchild]

        root = PageNode(page_id="1", title="Root", url="", space_key="DEV", depth=0, children=[child1, child2])
        flat = root.flat_list()

        assert len(flat) == 4
        assert [n.page_id for n in flat] == ["1", "2", "4", "3"]

    def test_source_provider_default(self):
        """Default source_provider ist 'confluence'."""
        node = PageNode(page_id="1", title="T", url="", space_key="S", depth=0)
        assert node.source_provider == "confluence"
        assert node.source_type == "page"

    def test_metadata_field(self):
        """Metadata-Dict kann Provider-spezifische Daten speichern."""
        node = PageNode(
            page_id="handbook:svc1", title="Service", url="", space_key="handbook",
            depth=0, source_provider="handbook", metadata={"service_id": "svc1"}
        )
        assert node.metadata["service_id"] == "svc1"


class TestResearchPlan:
    """Tests für ResearchPlan."""

    def test_estimated_pages_auto_calculated(self):
        """estimated_pages wird aus pages_to_analyze berechnet."""
        pages = [
            PageNode(page_id=str(i), title=f"P{i}", url="", space_key="DEV", depth=0)
            for i in range(5)
        ]
        plan = ResearchPlan(topic="Test", space_key="DEV", root_page_id="1", pages_to_analyze=pages)
        assert plan.estimated_pages == 5

    def test_estimated_pages_explicit(self):
        """Explizit gesetzter Wert wird nicht überschrieben."""
        plan = ResearchPlan(
            topic="Test", space_key="DEV", root_page_id="1",
            pages_to_analyze=[], estimated_pages=10
        )
        assert plan.estimated_pages == 10


class TestResearchProgress:
    """Tests für ResearchProgress."""

    def test_to_dict(self):
        """to_dict gibt alle Felder zurück."""
        progress = ResearchProgress(
            phase="analyzing",
            pages_total=10,
            pages_analyzed=3,
            findings_count=15,
            current_page="Seite A",
        )
        d = progress.to_dict()
        assert d["phase"] == "analyzing"
        assert d["pages_total"] == 10
        assert d["pages_analyzed"] == 3
        assert d["findings_count"] == 15
        assert d["current_page"] == "Seite A"


# ══════════════════════════════════════════════════════════════════════════════
# KnowledgeStore
# ══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeStore:
    """Tests für KnowledgeStore (SQLite FTS5 + MD-Dateien)."""

    @pytest.fixture
    def store(self, tmp_path):
        """Frischer KnowledgeStore in temporärem Verzeichnis."""
        return KnowledgeStore(str(tmp_path / "kb"))

    @pytest.fixture
    def sample_md(self):
        """Beispiel-MD mit Frontmatter."""
        return '''---
title: "Deployment-Prozess"
date: "2026-04-01"
space: DEV
tags:
  - deployment
  - jenkins
---

## Zusammenfassung

Deployment erfolgt über Jenkins Pipeline mit Blue/Green Strategie.

## Fakten

### Pipeline
- Jenkins wird für CI/CD verwendet
- Rollback in 5 Minuten möglich
'''

    def test_save_creates_file(self, store, sample_md):
        """save() erstellt MD-Datei im richtigen Ordner."""
        path = asyncio.get_event_loop().run_until_complete(
            store.save("Deployment-Prozess", "DEV", sample_md, {"title": "Deployment-Prozess", "space": "DEV"})
        )
        assert Path(path).exists()
        assert "dev" in path.lower()
        assert "deployment" in path.lower()

    def test_save_and_search_roundtrip(self, store, sample_md):
        """Gespeicherte MD ist über FTS5 suchbar."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Deployment-Prozess", "DEV", sample_md, {
                "title": "Deployment-Prozess", "space": "DEV",
                "tags": ["deployment", "jenkins"]
            })
        )

        results = loop.run_until_complete(store.search("deployment"))
        assert len(results) >= 1
        assert results[0].title == "Deployment-Prozess"

    def test_search_jenkins(self, store, sample_md):
        """FTS5 findet Terme im Content."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Deployment-Prozess", "DEV", sample_md, {
                "title": "Deployment-Prozess", "space": "DEV"
            })
        )

        results = loop.run_until_complete(store.search("jenkins"))
        assert len(results) >= 1

    def test_search_no_results(self, store):
        """Suche ohne Treffer gibt leere Liste zurück."""
        results = asyncio.get_event_loop().run_until_complete(store.search("nichtexistent"))
        assert results == []

    def test_list_all(self, store, sample_md):
        """list_all() gibt alle Einträge zurück."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Topic A", "DEV", sample_md, {"title": "Topic A", "space": "DEV"})
        )
        loop.run_until_complete(
            store.save("Topic B", "OPS", sample_md, {"title": "Topic B", "space": "OPS"})
        )

        all_entries = loop.run_until_complete(store.list_all())
        assert len(all_entries) == 2

    def test_list_all_filtered_by_space(self, store, sample_md):
        """list_all(space) filtert nach Space."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Topic A", "DEV", sample_md, {"title": "Topic A", "space": "DEV"})
        )
        loop.run_until_complete(
            store.save("Topic B", "OPS", sample_md, {"title": "Topic B", "space": "OPS"})
        )

        dev_entries = loop.run_until_complete(store.list_all(space="DEV"))
        assert len(dev_entries) == 1
        assert dev_entries[0].space == "DEV"

    def test_get_full_content(self, store, sample_md):
        """get_full_content() liest MD vollständig."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Test Topic", "DEV", sample_md, {"title": "Test Topic", "space": "DEV"})
        )

        results = loop.run_until_complete(store.search("deployment"))
        content = loop.run_until_complete(store.get_full_content(results[0].path))
        assert "Zusammenfassung" in content
        assert "Jenkins" in content

    def test_exists_found(self, store, sample_md):
        """exists() findet existierendes Thema."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Deployment", "DEV", sample_md, {"title": "Deployment", "space": "DEV"})
        )

        result = loop.run_until_complete(store.exists("Deployment", "DEV"))
        assert result is not None

    def test_exists_not_found(self, store):
        """exists() gibt None für nicht-existierendes Thema zurück."""
        result = asyncio.get_event_loop().run_until_complete(
            store.exists("Nichtexistent", "DEV")
        )
        assert result is None

    def test_upsert_semantics(self, store, sample_md):
        """Zweimaliges Speichern desselben Themas überschreibt (kein Duplikat)."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Deployment", "DEV", sample_md, {"title": "Deployment", "space": "DEV"})
        )
        loop.run_until_complete(
            store.save("Deployment", "DEV", sample_md + "\n- Neuer Fakt", {"title": "Deployment", "space": "DEV"})
        )

        all_entries = loop.run_until_complete(store.list_all())
        assert len(all_entries) == 1  # Kein Duplikat

    def test_reindex(self, store, sample_md):
        """reindex() baut Index aus existierenden MD-Dateien neu auf."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            store.save("Topic A", "DEV", sample_md, {"title": "Topic A", "space": "DEV"})
        )

        # Index löschen und neu aufbauen
        loop.run_until_complete(store.reindex())

        results = loop.run_until_complete(store.search("deployment"))
        assert len(results) >= 1


class TestKnowledgeStoreSlugify:
    """Tests für die Slugify-Funktion."""

    def test_basic_slug(self):
        assert KnowledgeStore._slugify("Deployment Prozess") == "deployment-prozess"

    def test_umlaute(self):
        assert KnowledgeStore._slugify("Übersicht") == "uebersicht"
        assert KnowledgeStore._slugify("Ärger") == "aerger"

    def test_special_chars(self):
        slug = KnowledgeStore._slugify("CI/CD Pipeline (v2)")
        assert "/" not in slug
        assert "(" not in slug

    def test_max_length(self):
        long_title = "A" * 200
        assert len(KnowledgeStore._slugify(long_title)) <= 80

    def test_empty_string(self):
        assert KnowledgeStore._slugify("") == "untitled"


class TestKnowledgeStoreFrontmatter:
    """Tests für Frontmatter-Parsing."""

    def test_parse_valid_frontmatter(self):
        content = '---\ntitle: "Test"\nspace: DEV\n---\n\nContent here'
        result = KnowledgeStore._parse_frontmatter(content)
        assert result["title"] == "Test"
        assert result["space"] == "DEV"

    def test_parse_no_frontmatter(self):
        content = "Just content without frontmatter"
        result = KnowledgeStore._parse_frontmatter(content)
        assert result == {}

    def test_parse_tags_as_list(self):
        content = '---\ntitle: "T"\ntags:\n  - a\n  - b\n---\n'
        result = KnowledgeStore._parse_frontmatter(content)
        assert result["tags"] == ["a", "b"]


# ══════════════════════════════════════════════════════════════════════════════
# Synthesizer (Fallback-Logik, kein LLM nötig)
# ══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeSynthesizer:
    """Tests für KnowledgeSynthesizer (Offline-Teile)."""

    def test_group_findings(self):
        """Findings werden nach Kategorie gruppiert."""
        from app.agent.knowledge_collector.synthesizer import KnowledgeSynthesizer

        findings = [
            ResearchFinding(fact="F1", source_page_id="1", source_title="S1", source_url="",
                          source_type="page", source_provider="confluence", category="fact"),
            ResearchFinding(fact="P1", source_page_id="2", source_title="S2", source_url="",
                          source_type="page", source_provider="confluence", category="process"),
            ResearchFinding(fact="F2", source_page_id="1", source_title="S1", source_url="",
                          source_type="page", source_provider="confluence", category="fact"),
        ]

        grouped = KnowledgeSynthesizer._group_findings(findings)
        assert len(grouped["fact"]) == 2
        assert len(grouped["process"]) == 1

    def test_collect_sources_deduplication(self):
        """Quellen werden dedupliziert."""
        from app.agent.knowledge_collector.synthesizer import KnowledgeSynthesizer

        findings = [
            ResearchFinding(fact="F1", source_page_id="1", source_title="Page A", source_url="u1",
                          source_type="page", source_provider="confluence"),
            ResearchFinding(fact="F2", source_page_id="1", source_title="Page A", source_url="u1",
                          source_type="page", source_provider="confluence"),
            ResearchFinding(fact="F3", source_page_id="2", source_title="Page B", source_url="u2",
                          source_type="page", source_provider="handbook"),
        ]

        sources = KnowledgeSynthesizer._collect_sources(findings)
        assert len(sources) == 2

    def test_fallback_synthesis_generates_markdown(self):
        """Fallback-Synthese erzeugt valides Markdown."""
        from app.agent.knowledge_collector.synthesizer import KnowledgeSynthesizer

        findings = [
            ResearchFinding(fact="Jenkins CI/CD", source_page_id="1", source_title="Deploy",
                          source_url="", source_type="page", source_provider="confluence",
                          category="fact"),
        ]
        grouped = KnowledgeSynthesizer._group_findings(findings)
        sources = KnowledgeSynthesizer._collect_sources(findings)

        md = KnowledgeSynthesizer._fallback_synthesis("Deployment", grouped, sources)
        assert "## Zusammenfassung" in md
        assert "## Fakten" in md
        assert "Jenkins CI/CD" in md
        assert "## Quellen" in md

    def test_build_empty_document(self):
        """Leeres Dokument bei 0 Findings."""
        from app.agent.knowledge_collector.synthesizer import KnowledgeSynthesizer

        synth = KnowledgeSynthesizer.__new__(KnowledgeSynthesizer)
        plan = ResearchPlan(topic="Leer", space_key="DEV", root_page_id="1", pages_to_analyze=[])
        md = synth._build_empty_document("Leer", plan)
        assert "---" in md
        assert "keine relevanten Fakten" in md.lower() or "findings_count: 0" in md


# ══════════════════════════════════════════════════════════════════════════════
# SourceProvider Interface
# ══════════════════════════════════════════════════════════════════════════════


class TestSourceProviderInterface:
    """Tests für das SourceProvider ABC."""

    def test_cannot_instantiate_abstract(self):
        """SourceProvider kann nicht direkt instanziiert werden."""
        from app.agent.knowledge_collector.source_provider import SourceProvider
        with pytest.raises(TypeError):
            SourceProvider()

    def test_confluence_provider_has_correct_tools(self):
        """ConfluenceProvider gibt die richtigen Tools zurück."""
        from app.agent.knowledge_collector.providers.confluence_provider import ConfluenceProvider
        provider = ConfluenceProvider()
        tools = provider.get_research_agent_tools()
        assert "read_confluence_page" in tools
        assert "list_confluence_pdfs" in tools
        assert "read_confluence_pdf" in tools

    def test_handbook_provider_has_correct_tools(self):
        """HandbookProvider gibt die richtigen Tools zurück."""
        from app.agent.knowledge_collector.providers.handbook_provider import HandbookProvider
        provider = HandbookProvider()
        tools = provider.get_research_agent_tools()
        assert "search_handbook" in tools
        assert "get_service_info" in tools

    def test_confluence_provider_name(self):
        from app.agent.knowledge_collector.providers.confluence_provider import ConfluenceProvider
        assert ConfluenceProvider().name == "confluence"

    def test_handbook_provider_name(self):
        from app.agent.knowledge_collector.providers.handbook_provider import HandbookProvider
        assert HandbookProvider().name == "handbook"


# ══════════════════════════════════════════════════════════════════════════════
# ResearchAgent Factory
# ══════════════════════════════════════════════════════════════════════════════


class TestResearchAgentFactory:
    """Tests für ResearchAgent.for_provider()."""

    def test_for_confluence_provider(self):
        """for_provider() setzt Confluence-Tools."""
        from app.agent.knowledge_collector.research_agent import ResearchAgent
        from app.agent.knowledge_collector.providers.confluence_provider import ConfluenceProvider

        agent = ResearchAgent.for_provider(ConfluenceProvider())
        assert "read_confluence_page" in agent.allowed_tools
        assert agent._provider_name == "confluence"
        assert "Confluence" in agent.display_name

    def test_for_handbook_provider(self):
        """for_provider() setzt Handbook-Tools."""
        from app.agent.knowledge_collector.research_agent import ResearchAgent
        from app.agent.knowledge_collector.providers.handbook_provider import HandbookProvider

        agent = ResearchAgent.for_provider(HandbookProvider())
        assert "search_handbook" in agent.allowed_tools
        assert "get_service_info" in agent.allowed_tools
        assert agent._provider_name == "handbook"


# ══════════════════════════════════════════════════════════════════════════════
# Event-Type-Mapping
# ══════════════════════════════════════════════════════════════════════════════


class TestEventTypeMapping:
    """Tests für Research Event-Type-Mapping."""

    def test_all_research_events_mapped(self):
        """Alle RESEARCH_* AgentEventTypes sind im Mapping."""
        from app.agent.orchestration.types import AgentEventType, MCP_EVENT_TYPE_MAPPING

        research_events = [e for e in AgentEventType if e.value.startswith("research_")]
        for event in research_events:
            assert event.value in MCP_EVENT_TYPE_MAPPING, f"{event.value} fehlt im Mapping"
            assert MCP_EVENT_TYPE_MAPPING[event.value] == event

    def test_research_started_mapping(self):
        from app.agent.orchestration.types import AgentEventType, MCP_EVENT_TYPE_MAPPING
        assert MCP_EVENT_TYPE_MAPPING["research_started"] == AgentEventType.RESEARCH_STARTED

    def test_research_complete_mapping(self):
        from app.agent.orchestration.types import AgentEventType, MCP_EVENT_TYPE_MAPPING
        assert MCP_EVENT_TYPE_MAPPING["research_complete"] == AgentEventType.RESEARCH_COMPLETE


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeBaseConfig:
    """Tests für die Config-Integration."""

    def test_config_loaded(self):
        """KnowledgeBaseConfig wird aus config.yaml geladen."""
        from app.core.config import settings
        assert hasattr(settings, "knowledge_base")
        assert settings.knowledge_base.enabled is True
        assert settings.knowledge_base.path == "knowledge-base"
        assert settings.knowledge_base.max_crawl_depth == 3

    def test_sources_config(self):
        """Sources-Konfiguration hat Defaults."""
        from app.core.config import settings
        assert settings.knowledge_base.sources.confluence is True
        assert settings.knowledge_base.sources.handbook is True
