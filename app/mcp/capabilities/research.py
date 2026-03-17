"""
Research Capability - Deep research with parallel sources.

Combines multiple research sources in parallel:
- Web Search (DuckDuckGo)
- Code Search (Java/Python Index)
- Documentation (Handbook/Confluence)
- Memory (Previous knowledge)

Implements Phase 5 & 6 of the MCP Architecture.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilityPhase,
    CapabilitySession,
    CapabilityArtifact
)

logger = logging.getLogger(__name__)


class ResearchSource(str, Enum):
    """Available research sources."""
    WEB = "web"              # DuckDuckGo Web Search
    CODE_JAVA = "code_java"  # Java Index Search
    CODE_PYTHON = "code_python"  # Python Index Search
    HANDBOOK = "handbook"    # Handbook Search
    CONFLUENCE = "confluence"  # Confluence Search
    MEMORY = "memory"        # Memory Store Search
    PDF = "pdf"              # PDF Documents


@dataclass
class ResearchResult:
    """A single research result from any source."""
    source: ResearchSource
    title: str
    content: str
    url: Optional[str] = None
    relevance: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "relevance": self.relevance,
            "metadata": self.metadata
        }


@dataclass
class SourceStatus:
    """Status of a research source execution."""
    source: ResearchSource
    status: str  # "pending", "running", "completed", "failed", "skipped"
    results_count: int = 0
    error: Optional[str] = None
    duration_ms: int = 0


class ResearchCapability(BaseCapability):
    """
    Deep research capability with parallel source execution.

    Flow:
    1. INIT: Parse query, determine relevant sources
    2. EXPLORE: Execute sources in parallel, collect results
    3. ANALYZE: Deduplicate, rank, and synthesize results
    4. SYNTHESIZE: Create comprehensive research report
    5. VALIDATE: Cross-reference findings
    6. OUTPUT: Generate final artifacts
    """

    def __init__(
        self,
        llm_callback: Optional[Callable] = None,
        event_emitter: Optional[Callable] = None
    ):
        super().__init__(llm_callback)
        self.event_emitter = event_emitter
        self._source_results: Dict[str, List[ResearchResult]] = {}
        self._source_status: Dict[str, SourceStatus] = {}
        self._wiki_primary: bool = False  # True when wiki/confluence was explicitly requested

    @property
    def name(self) -> str:
        return "research"

    @property
    def description(self) -> str:
        return (
            "Tiefgehende Recherche mit parallelen Quellen. "
            "Kombiniert Web-Suche, Code-Index, Dokumentation und Memory. "
            "Verwende für: Technische Recherche, API-Dokumentation, Best Practices."
        )

    @property
    def handoff_targets(self) -> List[str]:
        return ["analyze", "design", "implement"]

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Was soll recherchiert werden?"
                },
                "context": {
                    "type": "string",
                    "description": "Optional: Zusätzlicher Kontext"
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["web", "code_java", "code_python", "handbook", "confluence", "memory", "pdf", "all"]
                    },
                    "description": "Welche Quellen durchsuchen? (default: all)"
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "normal", "deep"],
                    "description": "Recherche-Tiefe (default: normal)"
                },
                "max_results_per_source": {
                    "type": "integer",
                    "description": "Max. Ergebnisse pro Quelle (default: 5)"
                }
            },
            "required": ["query"]
        }

    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit an event for UI updates."""
        if self.event_emitter:
            try:
                if asyncio.iscoroutinefunction(self.event_emitter):
                    await self.event_emitter(event_type, data)
                else:
                    self.event_emitter(event_type, data)
            except Exception as e:
                logger.warning(f"[research] Error emitting event: {e}")

    def _determine_sources(
        self,
        query: str,
        requested_sources: Optional[List[str]],
        internal_only: bool = False
    ) -> List[ResearchSource]:
        """
        Determine which sources to use based on query and request.

        Strategy: Internal sources first (Wiki/Confluence/Handbook), Web only as fallback.

        Args:
            query: The search query
            requested_sources: Explicitly requested sources
            internal_only: If True, never include web search
        """
        if requested_sources and "all" not in requested_sources:
            return [ResearchSource(s) for s in requested_sources if s in [e.value for e in ResearchSource]]

        query_lower = query.lower()
        sources = []

        # Always include memory for context
        sources.append(ResearchSource.MEMORY)

        # ════════════════════════════════════════════════════════════════════
        # INTERNAL SOURCES FIRST (Wiki/Confluence/Handbook/Code)
        # ════════════════════════════════════════════════════════════════════

        # Confluence/wiki queries - HIGH PRIORITY for internal docs
        wiki_keywords = ["wiki", "confluence", "seite", "page", "artikel",
                        "durchsuche", "suche in", "finde in", "schau in"]
        if any(kw in query_lower for kw in wiki_keywords):
            sources.append(ResearchSource.CONFLUENCE)
            # Mark that we explicitly want wiki - web only as fallback
            self._wiki_primary = True
        else:
            # Default: always check Confluence for internal knowledge
            sources.append(ResearchSource.CONFLUENCE)
            self._wiki_primary = False

        # Documentation queries
        if any(kw in query_lower for kw in ["handbuch", "service", "documentation", "docs"]):
            sources.append(ResearchSource.HANDBOOK)

        # Code-related queries
        if any(kw in query_lower for kw in ["code", "class", "method", "function", "api", "implementation"]):
            sources.append(ResearchSource.CODE_JAVA)
            sources.append(ResearchSource.CODE_PYTHON)

        # PDF for document-related
        if any(kw in query_lower for kw in ["pdf", "document", "specification", "spec"]):
            sources.append(ResearchSource.PDF)

        # ════════════════════════════════════════════════════════════════════
        # WEB ONLY AS FALLBACK OR FOR EXTERNAL TOPICS
        # ════════════════════════════════════════════════════════════════════

        # Check if web search is enabled globally (search.enabled in config.yaml)
        from app.core.config import settings
        web_enabled = settings.search.enabled

        # Skip web if:
        # - web_search_enabled is False (global disable)
        # - internal_only is True
        # - wiki was explicitly requested
        if web_enabled and not internal_only and not self._wiki_primary:
            # Web search only for clearly external/general queries
            external_keywords = ["best practice", "how to", "tutorial", "example",
                                "library", "framework", "stackoverflow", "github"]
            if any(kw in query_lower for kw in external_keywords):
                sources.append(ResearchSource.WEB)

        return list(set(sources))

    async def _phase_init(self, session: CapabilitySession) -> None:
        """Initialize research session."""
        requested_sources = session.metadata.get("sources", [])
        depth = session.metadata.get("depth", "normal")

        sources = self._determine_sources(session.query, requested_sources)

        session.metadata["active_sources"] = [s.value for s in sources]
        session.metadata["depth"] = depth

        # Initialize source status
        for source in sources:
            self._source_status[source.value] = SourceStatus(
                source=source,
                status="pending"
            )

        session.add_step(
            phase=CapabilityPhase.INIT,
            title="Research Initialization",
            content=f"Starting research for: {session.query}\n\n"
                   f"**Active Sources:** {', '.join(s.value for s in sources)}\n"
                   f"**Depth:** {depth}",
            insights=[f"{len(sources)} sources selected", f"Depth: {depth}"]
        )

        await self._emit_event("MCP_START", {
            "tool_name": "research",
            "query": session.query[:200],
            "sources": [s.value for s in sources],
            "depth": depth
        })

    async def _phase_explore(self, session: CapabilitySession) -> None:
        """Execute research sources in parallel."""
        sources = [ResearchSource(s) for s in session.metadata.get("active_sources", [])]
        max_results = session.metadata.get("max_results_per_source", 5)
        depth = session.metadata.get("depth", "normal")

        # Create tasks for parallel execution
        tasks = []
        for source in sources:
            task = asyncio.create_task(
                self._search_source(source, session.query, max_results, depth),
                name=f"research_{source.value}"
            )
            tasks.append((source, task))

        # Execute all sources in parallel
        await self._emit_event("MCP_PROGRESS", {
            "current_step": 1,
            "total_steps": len(sources) + 2,
            "message": f"Searching {len(sources)} sources in parallel..."
        })

        # Gather results with timeout
        timeout = 30 if depth == "deep" else 15
        all_results = []

        for source, task in tasks:
            try:
                self._source_status[source.value].status = "running"
                start_time = asyncio.get_event_loop().time()

                results = await asyncio.wait_for(task, timeout=timeout)

                duration = int((asyncio.get_event_loop().time() - start_time) * 1000)
                self._source_status[source.value].status = "completed"
                self._source_status[source.value].results_count = len(results)
                self._source_status[source.value].duration_ms = duration

                self._source_results[source.value] = results
                all_results.extend(results)

                await self._emit_event("MCP_STEP", {
                    "step_number": sources.index(source) + 1,
                    "step_type": "exploration",
                    "title": f"Source: {source.value}",
                    "content": f"Found {len(results)} results in {duration}ms",
                    "confidence": 0.8 if results else 0.3
                })

            except asyncio.TimeoutError:
                self._source_status[source.value].status = "failed"
                self._source_status[source.value].error = "Timeout"
                logger.warning(f"[research] Source {source.value} timed out")

            except Exception as e:
                self._source_status[source.value].status = "failed"
                self._source_status[source.value].error = str(e)
                logger.error(f"[research] Source {source.value} failed: {e}")

        # ════════════════════════════════════════════════════════════════════
        # WEB FALLBACK: If internal sources found nothing, request confirmation
        # ════════════════════════════════════════════════════════════════════
        internal_results = [
            r for r in all_results
            if r.source not in (ResearchSource.WEB,)
        ]

        web_fallback_approved = session.metadata.get("web_fallback_approved", False)

        # Check if web search is globally enabled
        from app.core.config import settings
        web_globally_enabled = settings.search.enabled

        if not internal_results and ResearchSource.WEB not in sources and web_globally_enabled:
            logger.info("[research] No internal results found")

            # Sanitize query for web search (remove internal info)
            from app.services.research_router import QuerySanitizer
            sanitizer = QuerySanitizer()
            sanitized_query = sanitizer.sanitize(session.query)
            removed_terms = sanitizer.get_removed_terms(session.query, sanitized_query)

            if removed_terms:
                logger.info(f"[research] Sanitized query, removed: {removed_terms}")

            # Store sanitized query for potential web search
            session.metadata["sanitized_query"] = sanitized_query
            session.metadata["removed_terms"] = removed_terms

            if not web_fallback_approved:
                # Request user confirmation for web search
                await self._emit_event("WEB_FALLBACK_REQUIRED", {
                    "original_query": session.query[:100],
                    "sanitized_query": sanitized_query,
                    "removed_terms": removed_terms,
                    "message": f"Keine internen Ergebnisse gefunden. Web-Suche mit bereinigter Query?",
                    "internal_sources_checked": [s.value for s in sources]
                })

                logger.info("[research] Web fallback requires user confirmation")
                # Mark that we need confirmation - the orchestrator will handle this
                session.metadata["needs_web_fallback_confirmation"] = True

            else:
                # Web fallback was approved - execute with sanitized query
                logger.info("[research] Web fallback approved, searching with sanitized query")
                await self._emit_event("MCP_PROGRESS", {
                    "current_step": len(sources) + 1,
                    "total_steps": len(sources) + 2,
                    "message": f"Web-Suche: {sanitized_query[:50]}..."
                })

                try:
                    web_results = await asyncio.wait_for(
                        self._search_web(sanitized_query, max_results),
                        timeout=timeout
                    )
                    if web_results:
                        self._source_results[ResearchSource.WEB.value] = web_results
                        self._source_status[ResearchSource.WEB.value] = SourceStatus(
                            source=ResearchSource.WEB,
                            status="completed",
                            results_count=len(web_results)
                        )
                        all_results.extend(web_results)
                        logger.info(f"[research] Web fallback found {len(web_results)} results")
                except Exception as e:
                    logger.warning(f"[research] Web fallback failed: {e}")

        # Summarize exploration
        successful_sources = [s for s, st in self._source_status.items() if st.status == "completed"]
        failed_sources = [s for s, st in self._source_status.items() if st.status == "failed"]

        session.add_step(
            phase=CapabilityPhase.EXPLORE,
            title="Parallel Source Exploration",
            content=self._format_exploration_results(),
            insights=[
                f"Total results: {len(all_results)}",
                f"Successful sources: {len(successful_sources)}",
                f"Failed sources: {len(failed_sources)}",
                "Web fallback used" if ResearchSource.WEB.value in self._source_results and ResearchSource.WEB not in sources else ""
            ]
        )

    async def _search_source(
        self,
        source: ResearchSource,
        query: str,
        max_results: int,
        depth: str
    ) -> List[ResearchResult]:
        """Search a single source."""
        results = []

        try:
            if source == ResearchSource.WEB:
                results = await self._search_web(query, max_results)

            elif source == ResearchSource.CODE_JAVA:
                results = await self._search_java(query, max_results)

            elif source == ResearchSource.CODE_PYTHON:
                results = await self._search_python(query, max_results)

            elif source == ResearchSource.HANDBOOK:
                results = await self._search_handbook(query, max_results)

            elif source == ResearchSource.CONFLUENCE:
                results = await self._search_confluence(query, max_results)

            elif source == ResearchSource.MEMORY:
                results = await self._search_memory(query, max_results)

            elif source == ResearchSource.PDF:
                results = await self._search_pdf(query, max_results)

        except Exception as e:
            logger.error(f"[research] Error searching {source.value}: {e}")

        return results

    async def _search_web(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search the web using DuckDuckGo."""
        try:
            # Import the search function
            from app.api.routes.search import _ddg_search

            ddg_results = await _ddg_search(query, max_results)

            results = []
            for r in ddg_results:
                if r.get("title") != "Fehler" and r.get("title") != "Timeout":
                    results.append(ResearchResult(
                        source=ResearchSource.WEB,
                        title=r.get("title", ""),
                        content=r.get("snippet", ""),
                        url=r.get("url", ""),
                        relevance=0.7,
                        metadata={"search_engine": "duckduckgo"}
                    ))

            return results

        except Exception as e:
            logger.error(f"[research] Web search failed: {e}")
            return []

    async def _search_java(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search the Java code index."""
        try:
            from app.services.java_indexer import get_java_indexer

            indexer = get_java_indexer()
            java_results = indexer.search(query, top_k=max_results)

            results = []
            for r in java_results:
                results.append(ResearchResult(
                    source=ResearchSource.CODE_JAVA,
                    title=f"{r.get('class_name', '')}::{r.get('method_name', '')}",
                    content=r.get("signature", "") + "\n" + r.get("docstring", "")[:300],
                    url=r.get("file_path", ""),
                    relevance=r.get("score", 0.5),
                    metadata={
                        "package": r.get("package", ""),
                        "type": r.get("type", "method")
                    }
                ))

            return results

        except Exception as e:
            logger.debug(f"[research] Java search failed: {e}")
            return []

    async def _search_python(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search the Python code index."""
        try:
            from app.services.python_indexer import get_python_indexer

            indexer = get_python_indexer()
            py_results = indexer.search(query, top_k=max_results)

            results = []
            for r in py_results:
                results.append(ResearchResult(
                    source=ResearchSource.CODE_PYTHON,
                    title=f"{r.get('module', '')}::{r.get('name', '')}",
                    content=r.get("signature", "") + "\n" + r.get("docstring", "")[:300],
                    url=r.get("file_path", ""),
                    relevance=r.get("score", 0.5),
                    metadata={
                        "module": r.get("module", ""),
                        "type": r.get("type", "function")
                    }
                ))

            return results

        except Exception as e:
            logger.debug(f"[research] Python search failed: {e}")
            return []

    async def _search_handbook(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search the handbook."""
        try:
            from app.services.handbook_indexer import get_handbook_indexer

            indexer = get_handbook_indexer()
            hb_results = indexer.search(query, top_k=max_results)

            results = []
            for r in hb_results:
                results.append(ResearchResult(
                    source=ResearchSource.HANDBOOK,
                    title=r.get("service_id", ""),
                    content=r.get("description", "")[:500],
                    relevance=r.get("score", 0.5),
                    metadata={
                        "fields": r.get("fields", []),
                        "category": r.get("category", "")
                    }
                ))

            return results

        except Exception as e:
            logger.debug(f"[research] Handbook search failed: {e}")
            return []

    async def _search_confluence(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search Confluence."""
        try:
            from app.services.confluence_client import get_confluence_client

            client = get_confluence_client()
            if not client.is_configured:
                return []

            conf_results = await client.search(query, limit=max_results)

            results = []
            for r in conf_results:
                results.append(ResearchResult(
                    source=ResearchSource.CONFLUENCE,
                    title=r.get("title", ""),
                    content=r.get("excerpt", "")[:500],
                    url=r.get("url", ""),
                    relevance=0.6,
                    metadata={
                        "space": r.get("space", ""),
                        "type": r.get("type", "page")
                    }
                ))

            return results

        except Exception as e:
            logger.debug(f"[research] Confluence search failed: {e}")
            return []

    async def _search_memory(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search the memory store."""
        try:
            from app.services.memory_store import get_memory_store

            store = get_memory_store()
            memories = await store.recall(
                query=query,
                scopes=["global", "project", "session"],
                limit=max_results
            )

            results = []
            for m in memories:
                results.append(ResearchResult(
                    source=ResearchSource.MEMORY,
                    title=m.key,
                    content=m.value,
                    relevance=m.importance,
                    metadata={
                        "category": m.category,
                        "scope": m.scope,
                        "access_count": m.access_count
                    }
                ))

            return results

        except Exception as e:
            logger.debug(f"[research] Memory search failed: {e}")
            return []

    async def _search_pdf(self, query: str, max_results: int) -> List[ResearchResult]:
        """Search PDF documents."""
        try:
            from app.services.pdf_indexer import get_pdf_indexer

            indexer = get_pdf_indexer()
            pdf_results = indexer.search(query, top_k=max_results)

            results = []
            for r in pdf_results:
                results.append(ResearchResult(
                    source=ResearchSource.PDF,
                    title=r.get("filename", ""),
                    content=r.get("chunk", "")[:500],
                    relevance=r.get("score", 0.5),
                    metadata={
                        "page": r.get("page", 0),
                        "pdf_id": r.get("pdf_id", "")
                    }
                ))

            return results

        except Exception as e:
            logger.debug(f"[research] PDF search failed: {e}")
            return []

    def _format_exploration_results(self) -> str:
        """Format exploration results for the session step."""
        lines = ["## Research Source Results\n"]

        for source_name, status in self._source_status.items():
            icon = "✓" if status.status == "completed" else "✗" if status.status == "failed" else "○"
            lines.append(f"### {icon} {source_name.upper()}")
            lines.append(f"- Status: {status.status}")
            lines.append(f"- Results: {status.results_count}")
            if status.duration_ms:
                lines.append(f"- Duration: {status.duration_ms}ms")
            if status.error:
                lines.append(f"- Error: {status.error}")
            lines.append("")

            # Show top results
            results = self._source_results.get(source_name, [])
            for r in results[:3]:
                lines.append(f"  - **{r.title[:60]}**")
                lines.append(f"    {r.content[:100]}...")
            lines.append("")

        return "\n".join(lines)

    async def _phase_analyze(self, session: CapabilitySession) -> None:
        """Analyze and rank results."""
        all_results = []
        for results in self._source_results.values():
            all_results.extend(results)

        # Sort by relevance
        all_results.sort(key=lambda r: r.relevance, reverse=True)

        # Deduplicate by title similarity
        seen_titles = set()
        unique_results = []
        for r in all_results:
            title_key = r.title.lower()[:30]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_results.append(r)

        # Group by source for analysis
        by_source = {}
        for r in unique_results:
            source = r.source.value
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(r)

        # Create analysis
        analysis_content = self._create_analysis(unique_results, by_source)

        session.add_step(
            phase=CapabilityPhase.ANALYZE,
            title="Result Analysis",
            content=analysis_content,
            insights=[
                f"Total unique results: {len(unique_results)}",
                f"Sources with results: {len(by_source)}"
            ]
        )

        await self._emit_event("MCP_PROGRESS", {
            "current_step": len(self._source_status) + 1,
            "total_steps": len(self._source_status) + 2,
            "message": f"Analyzing {len(unique_results)} results..."
        })

    def _create_analysis(
        self,
        results: List[ResearchResult],
        by_source: Dict[str, List[ResearchResult]]
    ) -> str:
        """Create structured analysis of results."""
        lines = ["## Research Analysis\n"]

        # Summary
        lines.append("### Summary")
        lines.append(f"- **Total Results:** {len(results)}")
        lines.append(f"- **Sources:** {', '.join(by_source.keys())}")
        lines.append("")

        # Top results
        lines.append("### Top Findings")
        for i, r in enumerate(results[:10], 1):
            lines.append(f"\n**{i}. [{r.source.value}] {r.title}**")
            lines.append(f"   Relevance: {r.relevance:.0%}")
            lines.append(f"   {r.content[:200]}...")
            if r.url:
                lines.append(f"   URL: {r.url}")

        # Source breakdown
        lines.append("\n### By Source")
        for source, source_results in by_source.items():
            lines.append(f"\n#### {source.upper()} ({len(source_results)} results)")
            for r in source_results[:3]:
                lines.append(f"- {r.title}: {r.content[:100]}...")

        return "\n".join(lines)

    async def _phase_synthesize(self, session: CapabilitySession) -> None:
        """Synthesize comprehensive research report."""
        all_results = []
        for results in self._source_results.values():
            all_results.extend(results)

        # Get analyze step
        analyze_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.ANALYZE),
            None
        )

        synthesis_prompt = f"""
Erstelle einen Research Report basierend auf den gesammelten Ergebnissen.

QUERY: {session.query}

ANALYSE:
{analyze_step.content if analyze_step else ""}

Erstelle einen strukturierten Report:

# Research Report: {session.query[:50]}

## Executive Summary
[3-5 key findings]

## Detailed Findings

### From Code Analysis
[Relevante Code-Erkenntnisse]

### From Documentation
[Erkenntnisse aus Dokumentation]

### From Web Research
[Web-Recherche-Ergebnisse]

## Key Insights
1. [Wichtigste Erkenntnis]
2. [Zweite Erkenntnis]
...

## Recommendations
- [Empfehlung basierend auf Research]

## Sources
[Liste der verwendeten Quellen]
"""

        # PERFORMANCE: Bei "quick" Tiefe LLM-Synthese überspringen (spart 20-60s)
        depth = session.metadata.get("depth", "normal")
        if depth == "quick":
            # Schneller Default-Report ohne LLM-Call
            report = self._generate_default_report(session.query, all_results)
            logger.debug("[research] Quick mode: skipping LLM synthesis")
        elif self.llm_callback:
            report = await self._call_llm(synthesis_prompt)
        else:
            report = self._generate_default_report(session.query, all_results)

        session.add_step(
            phase=CapabilityPhase.SYNTHESIZE,
            title="Research Report",
            content=report
        )

        # Create report artifact
        session.add_artifact(
            artifact_type="research_report",
            title=f"Research: {session.query[:40]}",
            content=report,
            metadata={
                "total_results": len(all_results),
                "sources": list(self._source_results.keys())
            }
        )

        # Create structured results artifact
        session.add_artifact(
            artifact_type="research_results",
            title="Structured Results",
            content=str([r.to_dict() for r in all_results[:20]]),
            metadata={"count": len(all_results)}
        )

        await self._emit_event("MCP_COMPLETE", {
            "tool_name": "research",
            "results_count": len(all_results),
            "sources": list(self._source_results.keys()),
            "conclusion": f"Found {len(all_results)} results across {len(self._source_results)} sources."
        })

    def _generate_default_report(
        self,
        query: str,
        results: List[ResearchResult]
    ) -> str:
        """Generate default report when LLM is not available."""
        lines = [
            f"# Research Report: {query}",
            "",
            "## Executive Summary",
            f"- Found {len(results)} results across multiple sources",
            "- Results sorted by relevance",
            "",
            "## Detailed Findings",
            ""
        ]

        # Group by source
        by_source: Dict[str, List[ResearchResult]] = {}
        for r in results:
            source = r.source.value
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(r)

        for source, source_results in by_source.items():
            lines.append(f"### From {source.upper()}")
            for r in source_results[:5]:
                lines.append(f"\n**{r.title}**")
                lines.append(f"{r.content[:200]}...")
                if r.url:
                    lines.append(f"- URL: {r.url}")
            lines.append("")

        lines.extend([
            "## Key Insights",
            "1. [Based on collected results]",
            "2. [Further analysis needed]",
            "",
            "## Recommendations",
            "- Review top-ranked results for detailed information",
            "- Consider additional specific searches if needed",
            "",
            "## Sources",
            f"- {len(by_source)} source types searched",
            f"- Total results: {len(results)}"
        ])

        return "\n".join(lines)


# Singleton
_research_capability: Optional[ResearchCapability] = None


def get_research_capability(
    llm_callback: Optional[Callable] = None,
    event_emitter: Optional[Callable] = None
) -> ResearchCapability:
    """Get the singleton ResearchCapability instance."""
    global _research_capability
    if _research_capability is None:
        _research_capability = ResearchCapability(llm_callback, event_emitter)
    else:
        # Always update callbacks when provided (fixes stale singleton issue)
        if llm_callback:
            _research_capability.llm_callback = llm_callback
        if event_emitter:
            _research_capability.event_emitter = event_emitter
    return _research_capability
