"""
Research API Routes - Multi-Source Research für MCP-Commands.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.research_router import (
    get_research_router,
    QueryClassifier,
    QuerySanitizer,
    QueryClassification,
    SourceType,
)
from app.models.skill import ResearchConfig, ResearchScope


router = APIRouter(prefix="/api/research", tags=["research"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class ClassifyRequest(BaseModel):
    """Request für Query-Klassifikation."""
    query: str = Field(..., min_length=1, description="Die zu klassifizierende Query")


class ClassifyResponse(BaseModel):
    """Response für Query-Klassifikation."""
    query: str
    classification: str
    keywords: List[str]
    web_allowed: bool
    explanation: str


class SanitizeRequest(BaseModel):
    """Request für Query-Sanitization."""
    query: str = Field(..., min_length=1, description="Die zu bereinigende Query")


class SanitizeResponse(BaseModel):
    """Response für Query-Sanitization."""
    original: str
    sanitized: str
    removed_terms: List[str]
    is_safe_for_web: bool


class ResearchRequest(BaseModel):
    """Request für Multi-Source Research."""
    query: str = Field(..., min_length=1, description="Die Research-Query")
    command: Optional[str] = Field(None, description="MCP-Command für Skill-Kontext")
    session_id: Optional[str] = Field(None, description="Session-ID")
    sources: Optional[List[str]] = Field(
        None,
        description="Erlaubte Quellen: skill, handbook, confluence, web"
    )
    scope: Optional[str] = Field(
        "internal-only",
        description="Research-Scope: internal-only, external-safe, all"
    )
    max_results: int = Field(10, ge=1, le=50, description="Maximale Ergebnisse")


class ResearchResultItem(BaseModel):
    """Ein einzelnes Research-Ergebnis."""
    source: str
    source_name: str
    content: str
    relevance_score: float
    url: Optional[str] = None


class ResearchResponse(BaseModel):
    """Response für Multi-Source Research."""
    query: str
    classification: str
    results: List[ResearchResultItem]
    total_results: int
    sources_used: List[str]
    context_string: str


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/classify", response_model=ClassifyResponse)
async def classify_query(request: ClassifyRequest):
    """
    Klassifiziert eine Query für Routing-Entscheidungen.

    Klassifikationen:
    - TECHNICAL: Frameworks, Libraries, Best Practices → Web erlaubt
    - BUSINESS: Fachliche Prozesse, Domäne → Web mit Vorsicht
    - INTERNAL: Interne Services, Projekte → Kein Web
    - MIXED: Kombination → Selektive Web-Nutzung
    """
    classifier = QueryClassifier()
    classification, keywords = classifier.classify(request.query)

    # Erklärung generieren
    explanations = {
        QueryClassification.TECHNICAL: "Technische Query - Web-Recherche ist sicher",
        QueryClassification.BUSINESS: "Fachliche Query - Web-Recherche nur anonymisiert",
        QueryClassification.INTERNAL: "Interne Query - Keine Web-Recherche erlaubt",
        QueryClassification.MIXED: "Gemischte Query - Web-Recherche mit Sanitization",
    }

    web_allowed = classification in (
        QueryClassification.TECHNICAL,
        QueryClassification.MIXED
    )

    return ClassifyResponse(
        query=request.query,
        classification=classification.value,
        keywords=keywords,
        web_allowed=web_allowed,
        explanation=explanations[classification]
    )


@router.post("/sanitize", response_model=SanitizeResponse)
async def sanitize_query(request: SanitizeRequest):
    """
    Entfernt sensible Informationen aus einer Query für Web-Recherche.

    Entfernt:
    - Interne Service-Namen (OrderService → Service)
    - Projekt-Codes (PROJ-123 → entfernt)
    - IP-Adressen
    - Interne URLs
    - Datenbank-/Schema-Namen
    """
    sanitizer = QuerySanitizer()
    sanitized = sanitizer.sanitize(request.query)
    removed = sanitizer.get_removed_terms(request.query, sanitized)

    # Prüfen ob die Query sicher ist
    classifier = QueryClassifier()
    classification, _ = classifier.classify(sanitized)
    is_safe = classification != QueryClassification.INTERNAL

    return SanitizeResponse(
        original=request.query,
        sanitized=sanitized,
        removed_terms=removed,
        is_safe_for_web=is_safe
    )


@router.post("/execute", response_model=ResearchResponse)
async def execute_research(request: ResearchRequest):
    """
    Führt Multi-Source Research durch.

    Sucht parallel in:
    - Skills (Wissensbasen)
    - Handbuch (FTS)
    - Confluence
    - Web (nur wenn erlaubt und sanitized)

    Die Ergebnisse werden nach Relevanz sortiert und dedupliziert.
    """
    router = get_research_router()

    # Config aus Request erstellen
    scope_map = {
        "internal-only": ResearchScope.INTERNAL_ONLY,
        "external-safe": ResearchScope.EXTERNAL_SAFE,
        "all": ResearchScope.ALL,
    }

    config = ResearchConfig(
        scope=scope_map.get(request.scope, ResearchScope.INTERNAL_ONLY),
        allowed_sources=request.sources or ["skills", "handbook", "confluence"],
        max_internal_results=request.max_results,
        max_web_results=min(5, request.max_results)
    )

    # Research ausführen
    context = await router.research(
        query=request.query,
        config=config,
        session_id=request.session_id
    )

    return ResearchResponse(
        query=context.query,
        classification=context.classification.value,
        results=[
            ResearchResultItem(
                source=r.source.value,
                source_name=r.source_name,
                content=r.content,
                relevance_score=r.relevance_score,
                url=r.url
            )
            for r in context.results
        ],
        total_results=len(context.results),
        sources_used=[s.value for s in context.sources_used],
        context_string=context.to_context_string()
    )


@router.get("/sources")
async def list_available_sources():
    """
    Listet verfügbare Research-Quellen auf.
    """
    return {
        "sources": [
            {
                "id": "skills",
                "name": "Skill-Wissensbasen",
                "description": "Durchsuchbare Wissensbasen aus aktivierten Skills",
                "internal": True
            },
            {
                "id": "handbook",
                "name": "Handbuch",
                "description": "HTML-Handbuch mit Service-Dokumentation",
                "internal": True
            },
            {
                "id": "confluence",
                "name": "Confluence",
                "description": "Confluence Wiki-Seiten und Spaces",
                "internal": True
            },
            {
                "id": "web",
                "name": "Web-Suche",
                "description": "Externe Web-Recherche (nur für technische Queries)",
                "internal": False
            },
            {
                "id": "code",
                "name": "Code-Repository",
                "description": "Java/Python Code-Suche",
                "internal": True
            }
        ],
        "scopes": [
            {
                "id": "internal-only",
                "name": "Nur Intern",
                "description": "Nur interne Quellen (Skills, Handbuch, Confluence)",
                "web_allowed": False
            },
            {
                "id": "external-safe",
                "name": "Extern Sicher",
                "description": "Web erlaubt für technische Queries (sanitized)",
                "web_allowed": True
            },
            {
                "id": "all",
                "name": "Alle Quellen",
                "description": "Alle Quellen ohne Einschränkung",
                "web_allowed": True
            }
        ]
    }
