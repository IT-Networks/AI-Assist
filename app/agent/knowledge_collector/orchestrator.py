"""
ResearchOrchestrator – Koordiniert die Research-Pipeline.

Pipeline:
1. Source Selection: LLM wählt relevante Provider
2. Discovery: Provider entdecken Seiten/Dokumente parallel
3. Planning: LLM bewertet Relevanz, filtert, priorisiert
4. Execution: Parallele ResearchAgents extrahieren Fakten
5. Synthesis: LLM synthetisiert Findings zu strukturierter MD
6. Persistierung: KnowledgeStore speichert MD + FTS5-Index

Fortschritt wird über on_progress Callback an den Aufrufer gestreamt.
"""

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable, Dict, List, Optional

from app.agent.knowledge_collector.models import (
    PageNode,
    ResearchFinding,
    ResearchPlan,
    ResearchProgress,
)
from app.agent.knowledge_collector.research_agent import ResearchAgent
from app.agent.knowledge_collector.source_provider import SourceProvider
from app.agent.knowledge_collector.synthesizer import KnowledgeSynthesizer
from app.core.config import settings
from app.services.llm_client import llm_client as default_llm_client

logger = logging.getLogger(__name__)


class ResearchOrchestrator:
    """
    Koordiniert die Research-Pipeline: Discovery → Planning → Execution → Synthesis.

    Nicht zu verwechseln mit dem Main-Orchestrator (agent/orchestrator.py).
    """

    def __init__(
        self,
        providers: List[SourceProvider],
        knowledge_store,
        tool_registry,
        on_progress: Optional[Callable[[ResearchProgress], Awaitable[None]]] = None,
    ):
        self._providers = {p.name: p for p in providers if p.is_available()}
        self._knowledge_store = knowledge_store
        self._tool_registry = tool_registry
        self._on_progress = on_progress
        self._config = settings.knowledge_base
        self._synthesizer = KnowledgeSynthesizer()
        self._model = settings.llm.tool_model or settings.llm.default_model

    async def research(
        self,
        topic: str,
        root_page_id: Optional[str] = None,
        space_key: Optional[str] = None,
        confluence_url: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> str:
        """
        Führt die komplette Research-Pipeline aus.

        Args:
            topic: Das zu recherchierende Thema
            root_page_id: Optional: Confluence Seiten-ID als Startpunkt
            space_key: Optional: Confluence Space Key
            confluence_url: Optional: Confluence-URL als Startpunkt
            max_depth: Optional: Max. Crawl-Tiefe (Default aus Config)

        Returns:
            Pfad zur generierten MD-Datei
        """
        max_depth = max_depth or self._config.max_crawl_depth
        start_time = time.time()

        # URL → page_id Auflösung
        if confluence_url and not root_page_id:
            root_page_id = await self._resolve_url(confluence_url)

        # Duplikat-Check
        existing = await self._knowledge_store.exists(topic, space_key or "")
        if existing:
            logger.info(f"[Research] Thema existiert bereits: {existing}")
            # Trotzdem fortfahren (Update-Semantik)

        # ── Phase 0: Source Selection ──
        await self._emit(ResearchProgress(
            phase="discovering",
            current_action="Wissensquellen werden ausgewählt...",
        ))
        selected_providers = await self._select_sources(topic)
        if not selected_providers:
            logger.warning(f"[Research] Keine Provider ausgewaehlt fuer '{topic}'")
            await self._emit(ResearchProgress(phase="error", error="Keine Wissensquellen verfuegbar"))
            return ""

        provider_names = [p.name for p in selected_providers]
        logger.info(f"[Research] Provider ausgewaehlt: {provider_names}")

        # ── Phase 1: Discovery ──
        await self._emit(ResearchProgress(
            phase="discovering",
            current_action=f"Suche in {', '.join(p.display_name for p in selected_providers)}...",
            providers_active=provider_names,
        ))

        all_pages, discovery_errors = await self._discover_all_with_errors(
            selected_providers, topic, root_page_id, max_depth, space_key
        )

        if not all_pages:
            error_detail = f"Keine Seiten zu '{topic}' gefunden."
            if discovery_errors:
                error_detail += " Fehler: " + "; ".join(discovery_errors)
            logger.warning(f"[Research] Discovery leer: {error_detail}")
            await self._emit(ResearchProgress(phase="error", error=error_detail))
            return ""

        logger.info(f"[Research] Discovery: {len(all_pages)} Seiten gefunden")

        # ── Phase 2: Planning ──
        await self._emit(ResearchProgress(
            phase="planning",
            pages_total=len(all_pages),
            current_action="Relevanz wird bewertet...",
        ))
        plan = await self._plan_research(all_pages, topic, space_key or "", root_page_id or "")

        await self._emit(ResearchProgress(
            phase="planning",
            pages_total=plan.estimated_pages,
            current_action=f"Plan: {plan.estimated_pages} Seiten, {len(plan.providers_used)} Quellen",
        ))

        # ── Phase 3: Execution ──
        findings = await self._execute_research(plan)
        logger.info(f"[Research] Execution: {len(findings)} Findings extrahiert")

        # ── Phase 4: Synthesis ──
        await self._emit(ResearchProgress(
            phase="synthesizing",
            findings_count=len(findings),
            pages_analyzed=plan.estimated_pages,
            current_action="Wissen wird zusammengefasst...",
        ))
        md_path = await self._synthesize_and_save(topic, plan, findings, space_key or "")

        # ── Done ──
        duration = time.time() - start_time
        await self._emit(ResearchProgress(
            phase="complete",
            pages_total=plan.estimated_pages,
            pages_analyzed=plan.estimated_pages,
            findings_count=len(findings),
            current_action=f"Abgeschlossen in {duration:.0f}s",
            providers_active=plan.providers_used,
        ))

        return md_path

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 0: Source Selection
    # ══════════════════════════════════════════════════════════════════════════

    async def _select_sources(self, topic: str) -> List[SourceProvider]:
        """LLM-basierte Quellen-Auswahl oder alle verfügbaren bei nur einem Provider."""
        available = list(self._providers.values())

        if len(available) <= 1:
            return available

        # LLM entscheidet welche Quellen relevant sind
        provider_descriptions = "\n".join([
            f"- {p.name}: {p.description}" for p in available
        ])

        prompt = (
            f'Welche Wissensquellen sind für das Thema "{topic}" relevant?\n\n'
            f"Verfügbare Quellen:\n{provider_descriptions}\n\n"
            'Antworte NUR mit JSON: {"sources": ["source1", "source2"]}'
        )

        try:
            text = await default_llm_client.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.0,
                max_tokens=100,
            )

            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                selected_names = data.get("sources", [])
                selected = [p for p in available if p.name in selected_names]
                if selected:
                    return selected
        except Exception as e:
            logger.debug(f"[Research] Source-Selection LLM fehlgeschlagen: {e}")

        # Fallback: alle Provider
        return available

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 1: Discovery
    # ══════════════════════════════════════════════════════════════════════════

    async def _discover_all(
        self,
        providers: List[SourceProvider],
        topic: str,
        root_id: Optional[str],
        max_depth: int,
        space_key: Optional[str] = None,
    ) -> List[PageNode]:
        """Parallel Discovery ueber alle Provider (ohne Fehler-Details)."""
        pages, _ = await self._discover_all_with_errors(providers, topic, root_id, max_depth, space_key)
        return pages

    async def _discover_all_with_errors(
        self,
        providers: List[SourceProvider],
        topic: str,
        root_id: Optional[str],
        max_depth: int,
        space_key: Optional[str] = None,
    ) -> tuple:
        """Parallel Discovery ueber alle Provider mit Fehler-Details."""
        tasks = []
        for provider in providers:
            rid = root_id if provider.name == "confluence" else None
            tasks.append(self._discover_with_events(provider, topic, rid, max_depth, space_key))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_pages: List[PageNode] = []
        errors: List[str] = []
        for provider, result in zip(providers, results):
            if isinstance(result, Exception):
                err_msg = f"{provider.name}: {result}"
                logger.error(f"[Research] Discovery fehlgeschlagen fuer {provider.name}: {result}", exc_info=result)
                errors.append(err_msg)
                continue
            flat_count = 0
            for node in result:
                flat = node.flat_list()
                all_pages.extend(flat)
                flat_count += len(flat)
            logger.info(f"[Research] {provider.name}: {flat_count} Seiten entdeckt")

        return all_pages, errors

    async def _discover_with_events(
        self,
        provider: SourceProvider,
        topic: str,
        root_id: Optional[str],
        max_depth: int,
        space_key: Optional[str] = None,
    ) -> List[PageNode]:
        """Discovery mit Fortschritts-Events."""
        nodes = await provider.discover(topic, root_id=root_id, max_depth=max_depth, space_key=space_key)

        # Events für jede entdeckte Seite
        flat = []
        for node in nodes:
            flat.extend(node.flat_list())

        for node in flat:
            await self._emit(ResearchProgress(
                phase="discovering",
                current_page=node.title,
                current_action=f"Gefunden in {provider.display_name} (Tiefe {node.depth})",
                pages_total=len(flat),
            ))

        return nodes

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 2: Planning
    # ══════════════════════════════════════════════════════════════════════════

    async def _plan_research(
        self,
        pages: List[PageNode],
        topic: str,
        space_key: str,
        root_page_id: str,
    ) -> ResearchPlan:
        """
        LLM bewertet Relevanz jeder Seite.
        Filtert irrelevante, priorisiert relevante.
        """
        max_pages = self._config.max_pages_per_research

        if len(pages) <= max_pages:
            # Alle Seiten analysieren — kein Filtering nötig
            return ResearchPlan(
                topic=topic,
                space_key=space_key,
                root_page_id=root_page_id,
                pages_to_analyze=pages,
                max_parallel=self._config.max_parallel_agents,
                providers_used=list(set(p.source_provider for p in pages)),
            )

        # LLM-basierte Relevanz-Bewertung (Batch: alle Titel in einem Call)
        titles = "\n".join([
            f"{i+1}. [{p.source_provider}] {p.title}"
            for i, p in enumerate(pages)
        ])

        prompt = (
            f'Bewerte die Relevanz dieser Seiten zum Thema "{topic}".\n\n'
            f"Seiten:\n{titles}\n\n"
            f"Gib die Nummern der {max_pages} relevantesten Seiten zurück.\n"
            'Antworte NUR mit JSON: {"relevant": [1, 3, 5, ...]}'
        )

        try:
            text = await default_llm_client.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.0,
                max_tokens=200,
            )

            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                indices = data.get("relevant", [])
                # 1-basierte Indizes → 0-basiert
                selected = [pages[i - 1] for i in indices if 0 < i <= len(pages)]
                if selected:
                    return ResearchPlan(
                        topic=topic,
                        space_key=space_key,
                        root_page_id=root_page_id,
                        pages_to_analyze=selected[:max_pages],
                        max_parallel=self._config.max_parallel_agents,
                        providers_used=list(set(p.source_provider for p in selected)),
                    )
        except Exception as e:
            logger.debug(f"[Research] Relevanz-Bewertung fehlgeschlagen: {e}")

        # Fallback: erste max_pages Seiten
        return ResearchPlan(
            topic=topic,
            space_key=space_key,
            root_page_id=root_page_id,
            pages_to_analyze=pages[:max_pages],
            max_parallel=self._config.max_parallel_agents,
            providers_used=list(set(p.source_provider for p in pages[:max_pages])),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 3: Execution
    # ══════════════════════════════════════════════════════════════════════════

    async def _execute_research(self, plan: ResearchPlan) -> List[ResearchFinding]:
        """
        Verteilt Seiten auf ResearchAgents, führt parallel aus.

        Strategie: Seiten nach Provider gruppieren, pro Provider einen Agent mit passender
        Tool-Whitelist erstellen. Dann Batches mit max_parallel parallel ausführen.
        """
        all_findings: List[ResearchFinding] = []

        # Seiten nach Provider gruppieren
        by_provider: Dict[str, List[PageNode]] = {}
        for page in plan.pages_to_analyze:
            by_provider.setdefault(page.source_provider, []).append(page)

        # Arbeits-Einheiten erstellen (Provider + Seiten-Chunk)
        work_units: List[tuple] = []  # (provider, pages_chunk)
        for provider_name, pages in by_provider.items():
            provider = self._providers.get(provider_name)
            if not provider:
                continue
            # Seiten in Chunks aufteilen (2-3 Seiten pro Agent)
            chunk_size = max(1, min(3, len(pages)))
            for i in range(0, len(pages), chunk_size):
                chunk = pages[i:i + chunk_size]
                work_units.append((provider, chunk))

        # In Batches ausführen
        batch_size = plan.max_parallel
        analyzed_count = 0

        for batch_start in range(0, len(work_units), batch_size):
            batch = work_units[batch_start:batch_start + batch_size]
            tasks = []

            for provider, pages_chunk in batch:
                agent = ResearchAgent.for_provider(provider)

                # Progress-Event: Seite wird analysiert
                for page in pages_chunk:
                    await self._emit(ResearchProgress(
                        phase="analyzing",
                        pages_total=plan.estimated_pages,
                        pages_analyzed=analyzed_count,
                        findings_count=len(all_findings),
                        current_page=page.title,
                        current_action=f"Wird analysiert ({provider.display_name})",
                    ))

                tasks.append(
                    agent.run_research(
                        pages=pages_chunk,
                        topic=plan.topic,
                        llm_client=default_llm_client,
                        tool_registry=self._tool_registry,
                        on_finding=self._on_finding_callback(plan, analyzed_count, all_findings),
                    )
                )

            # Parallel ausführen
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for (provider, pages_chunk), result in zip(batch, results):
                analyzed_count += len(pages_chunk)
                if isinstance(result, Exception):
                    logger.warning(f"[Research] Agent-Fehler ({provider.name}): {result}")
                    await self._emit(ResearchProgress(
                        phase="analyzing",
                        error=f"Fehler bei {provider.display_name}: {result}",
                    ))
                    continue
                if isinstance(result, list):
                    all_findings.extend(result)

                # Fortschritts-Event
                await self._emit(ResearchProgress(
                    phase="analyzing",
                    pages_total=plan.estimated_pages,
                    pages_analyzed=analyzed_count,
                    findings_count=len(all_findings),
                    current_action=f"{analyzed_count}/{plan.estimated_pages} Seiten analysiert",
                ))

        return all_findings

    def _on_finding_callback(
        self,
        plan: ResearchPlan,
        analyzed_count: int,
        all_findings: List[ResearchFinding],
    ) -> Callable:
        """Erstellt einen Callback für Live-Finding-Events."""
        async def callback(finding: ResearchFinding):
            await self._emit(ResearchProgress(
                phase="analyzing",
                pages_total=plan.estimated_pages,
                pages_analyzed=analyzed_count,
                findings_count=len(all_findings) + 1,
                latest_finding=finding.fact[:200],
                current_action=f"Erkenntnis: {finding.fact[:100]}...",
            ))
        return callback

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 4: Synthesis
    # ══════════════════════════════════════════════════════════════════════════

    async def _synthesize_and_save(
        self,
        topic: str,
        plan: ResearchPlan,
        findings: List[ResearchFinding],
        space_key: str,
    ) -> str:
        """Synthetisiert Findings und speichert als MD."""
        md_content = await self._synthesizer.synthesize(topic, plan, findings)

        # Metadaten für den Index
        metadata = {
            "title": topic,
            "space": space_key or plan.space_key,
            "pages_analyzed": plan.estimated_pages,
            "pdfs_analyzed": sum(1 for f in findings if f.source_type == "pdf"),
            "confidence": self._calc_confidence(findings),
            "providers": plan.providers_used,
            "source_pages": list(set(f.source_page_id for f in findings)),
            "tags": list(set(f.category for f in findings)),
        }

        path = await self._knowledge_store.save(
            topic=topic,
            space=space_key or plan.space_key,
            content=md_content,
            metadata=metadata,
        )

        # Knowledge Graph Linking (optional — nur wenn Graph aktiv)
        try:
            from app.services.knowledge_graph_linker import KnowledgeGraphLinker
            from app.services.knowledge_graph import get_graph_registry
            registry = get_graph_registry()
            active_graph = registry.get_active()
            if active_graph:
                linker = KnowledgeGraphLinker(active_graph)
                edges = linker.link_knowledge_document(path, metadata, findings)
                logger.info(f"[Research] {edges} Graph-Kanten erstellt")
        except Exception as e:
            logger.debug(f"[Research] Graph-Linking uebersprungen: {e}")

        return path

    # ══════════════════════════════════════════════════════════════════════════
    # Hilfsfunktionen
    # ══════════════════════════════════════════════════════════════════════════

    async def _resolve_url(self, url: str) -> Optional[str]:
        """Extrahiert page_id aus einer Confluence-URL."""
        match = re.search(r"/pages/(\d+)", url)
        if match:
            return match.group(1)
        # pageId Query-Param
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "pageId" in params:
            return params["pageId"][0]
        return None

    @staticmethod
    def _calc_confidence(findings: List[ResearchFinding]) -> str:
        if not findings:
            return "low"
        high = sum(1 for f in findings if f.confidence == "high")
        ratio = high / len(findings)
        if ratio > 0.5:
            return "high"
        elif ratio > 0.2:
            return "medium"
        return "low"

    async def _emit(self, progress: ResearchProgress):
        """Emittiert ein Progress-Event an den Aufrufer."""
        if self._on_progress:
            try:
                await self._on_progress(progress)
            except Exception as e:
                logger.debug(f"[Research] Progress-Callback Fehler: {e}")
