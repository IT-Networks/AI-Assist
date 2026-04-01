"""
Datenmodelle für den Knowledge Collector.

Alle Dataclasses die von Orchestrator, Agents, Providers und Store verwendet werden.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PageNode:
    """Knoten im Seitenbaum einer Wissensquelle."""
    page_id: str
    title: str
    url: str
    space_key: str
    depth: int                                      # 0 = Root-Seite
    children: List["PageNode"] = field(default_factory=list)
    has_pdf_attachments: bool = False
    relevance_score: float = 0.0                    # 0.0-1.0, vom LLM bewertet
    source_type: str = "page"                       # "page" | "pdf" | "handbook" | "service"
    source_provider: str = "confluence"              # "confluence" | "handbook" | ...
    metadata: Dict[str, Any] = field(default_factory=dict)

    def flat_list(self) -> List["PageNode"]:
        """Flacht den Baum zu einer Liste ab (BFS)."""
        result = [self]
        for child in self.children:
            result.extend(child.flat_list())
        return result


@dataclass
class ResearchFinding:
    """Ein einzelnes Faktum/Erkenntnis aus einer Quelle."""
    fact: str
    source_page_id: str
    source_title: str
    source_url: str
    source_type: str                                # "page" | "pdf" | "handbook" | "service"
    source_provider: str                            # "confluence" | "handbook" | ...
    confidence: str = "medium"                      # "high" | "medium" | "low"
    category: str = "fact"                          # "fact" | "process" | "decision" | "definition"


@dataclass
class ResearchPlan:
    """Plan für die Research-Ausführung."""
    topic: str
    space_key: str
    root_page_id: str
    pages_to_analyze: List[PageNode]
    estimated_pages: int = 0
    estimated_pdfs: int = 0
    max_parallel: int = 5
    providers_used: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.estimated_pages:
            self.estimated_pages = len(self.pages_to_analyze)


@dataclass
class ResearchProgress:
    """Fortschritts-Status einer Research-Pipeline."""
    phase: str                                      # "discovering" | "planning" | "analyzing" | "synthesizing" | "complete" | "error"
    pages_total: int = 0
    pages_analyzed: int = 0
    pdfs_analyzed: int = 0
    findings_count: int = 0
    current_page: str = ""
    current_action: str = ""                        # z.B. "Unterseite gefunden (Tiefe 2)"
    latest_finding: str = ""                        # Letzte Erkenntnis für Live-Anzeige
    providers_active: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "pages_total": self.pages_total,
            "pages_analyzed": self.pages_analyzed,
            "pdfs_analyzed": self.pdfs_analyzed,
            "findings_count": self.findings_count,
            "current_page": self.current_page,
            "current_action": self.current_action,
            "latest_finding": self.latest_finding,
            "providers_active": self.providers_active,
            "error": self.error,
        }


@dataclass
class KnowledgeEntry:
    """Ein Eintrag im Knowledge-Index (Suchergebnis)."""
    path: str                                       # Relativer Pfad zur MD
    title: str
    space: str
    summary: str                                    # ≤200 Wörter
    tags: List[str] = field(default_factory=list)
    date: str = ""                                  # ISO-8601
    pages_analyzed: int = 0
    confidence: str = "medium"
    relevance_score: float = 0.0                    # Für Suchergebnisse (FTS5 rank)
