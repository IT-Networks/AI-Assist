"""
Research Router - Multi-Source Research für MCP-Commands.

Features:
- Query-Klassifikation (TECHNICAL, BUSINESS, INTERNAL)
- Query-Sanitization für sichere Web-Recherche
- Parallele Recherche in mehreren Quellen
- Ergebnis-Aggregation mit Relevanz-Ranking
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.skill import ResearchConfig, ResearchScope


# ══════════════════════════════════════════════════════════════════════════════
# Enums und Datenklassen
# ══════════════════════════════════════════════════════════════════════════════

class QueryClassification(str, Enum):
    """Klassifikation einer Query für Routing-Entscheidungen."""
    TECHNICAL = "technical"    # Frameworks, Libraries, Patterns → Web erlaubt
    BUSINESS = "business"      # Prozesse, Domäne → Web mit Vorsicht
    INTERNAL = "internal"      # Projekte, Services, Personen → Kein Web
    MIXED = "mixed"            # Kombination → Selektive Web-Nutzung


class SourceType(str, Enum):
    """Verfügbare Datenquellen."""
    SKILL = "skill"
    HANDBOOK = "handbook"
    CONFLUENCE = "confluence"
    WEB = "web"
    CODE = "code"


@dataclass
class ResearchQuery:
    """Eine einzelne Research-Query mit Metadaten."""
    original: str
    sanitized: str
    classification: QueryClassification
    keywords: List[str]
    target_source: SourceType
    priority: int = 1


@dataclass
class ResearchResult:
    """Ein einzelnes Research-Ergebnis."""
    source: SourceType
    source_name: str
    content: str
    relevance_score: float
    url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "source": self.source.value,
            "source_name": self.source_name,
            "content": self.content,
            "relevance_score": self.relevance_score,
            "url": self.url,
            "metadata": self.metadata
        }


@dataclass
class AggregatedContext:
    """Aggregierter Kontext aus allen Quellen."""
    query: str
    classification: QueryClassification
    results: List[ResearchResult]
    total_tokens: int
    sources_used: List[SourceType]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "query": self.query,
            "classification": self.classification.value,
            "results": [r.to_dict() for r in self.results],
            "total_tokens": self.total_tokens,
            "sources_used": [s.value for s in self.sources_used],
            "timestamp": self.timestamp.isoformat()
        }

    def to_context_string(self) -> str:
        """Formatiert die Ergebnisse als Kontext-String für LLM."""
        if not self.results:
            return ""

        parts = ["=== RECHERCHE-ERGEBNISSE ===\n"]
        parts.append(f"Query: {self.query}")
        parts.append(f"Klassifikation: {self.classification.value}\n")

        for result in self.results:
            parts.append(f"\n[{result.source.value.upper()}: {result.source_name}]")
            parts.append(result.content)
            if result.url:
                parts.append(f"Quelle: {result.url}")

        parts.append("\n=== ENDE RECHERCHE ===")
        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Query Classifier
# ══════════════════════════════════════════════════════════════════════════════

class QueryClassifier:
    """
    Klassifiziert Queries für sichere Routing-Entscheidungen.

    TECHNICAL: Frameworks, Libraries, Patterns, Best Practices
    BUSINESS: Fachliche Prozesse, Domänenkonzepte
    INTERNAL: Interne Services, Projekte, Personen, IPs
    """

    # Technische Begriffe → Web-Recherche sicher
    TECHNICAL_INDICATORS = {
        # Programmiersprachen
        "java", "python", "javascript", "typescript", "kotlin", "scala", "go", "rust",
        # Frameworks
        "spring", "spring boot", "fastapi", "flask", "django", "react", "vue", "angular",
        "express", "nest", "quarkus", "micronaut",
        # Datenbanken
        "sql", "nosql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        # Konzepte
        "rest", "graphql", "grpc", "microservices", "api", "oauth", "jwt",
        "design pattern", "best practice", "architecture", "clean code",
        # Tools
        "docker", "kubernetes", "jenkins", "git", "maven", "gradle", "npm",
        # Allgemein
        "tutorial", "example", "how to", "implementation", "library", "framework",
        "version", "upgrade", "migration", "performance", "optimization",
    }

    # Business/Fachliche Begriffe → Web nur anonymisiert
    BUSINESS_INDICATORS = {
        "prozess", "workflow", "anforderung", "requirement", "use case",
        "stakeholder", "domain", "fachlich", "business", "geschäft",
        "kunde", "customer", "bestellung", "order", "rechnung", "invoice",
        "vertrag", "contract", "produkt", "product",
    }

    # Interne Begriffe → NIEMALS im Web suchen
    INTERNAL_PATTERNS = [
        # Service-Namen (CamelCase mit Service/Controller/etc.)
        r'\b[A-Z][a-zA-Z]+(?:Service|Controller|Manager|Client|Handler|Repository|Bean)\b',
        # Projekt-Codes (z.B. PROJ-123, ABC-456)
        r'\b[A-Z]{2,5}-\d{2,5}\b',
        # IP-Adressen
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
        # Interne URLs
        r'(?:intranet|internal|corp|company)\.[a-z]+\.[a-z]+',
        # Datenbankschemas (mit Präfix)
        r'\b(?:schema|db)\s*[:\.]?\s*[a-zA-Z_]{3,}\b',
    ]

    # Bekannte technische Begriffe die NICHT als intern gelten
    # (auch wenn sie CamelCase haben)
    KNOWN_TECHNICAL_TERMS = {
        "spring boot", "springboot", "spring", "boot",
        "react native", "vue", "angular", "typescript",
        "node", "nodejs", "express", "fastapi",
        "docker", "kubernetes", "jenkins", "github",
        "elasticsearch", "postgresql", "mongodb", "redis",
        "graphql", "restful", "oauth", "jwt",
    }

    def __init__(self):
        self._internal_patterns = [re.compile(p, re.IGNORECASE) for p in self.INTERNAL_PATTERNS]

    def classify(self, query: str) -> Tuple[QueryClassification, List[str]]:
        """
        Klassifiziert eine Query und extrahiert Keywords.

        Returns:
            Tuple[QueryClassification, List[str]]: Klassifikation und Keywords
        """
        query_lower = query.lower()
        keywords = self._extract_keywords(query)

        # Prüfe auf interne Begriffe zuerst (höchste Priorität)
        if self._has_internal_content(query):
            return QueryClassification.INTERNAL, keywords

        # Zähle Indikatoren
        technical_count = sum(1 for t in self.TECHNICAL_INDICATORS if t in query_lower)
        business_count = sum(1 for b in self.BUSINESS_INDICATORS if b in query_lower)

        # Klassifikation basierend auf Indikatoren
        if technical_count > 0 and business_count == 0:
            return QueryClassification.TECHNICAL, keywords
        elif business_count > 0 and technical_count == 0:
            return QueryClassification.BUSINESS, keywords
        elif technical_count > 0 and business_count > 0:
            return QueryClassification.MIXED, keywords
        else:
            # Default: Business (sicherer, weniger Web-Exposure)
            return QueryClassification.BUSINESS, keywords

    def _has_internal_content(self, query: str) -> bool:
        """Prüft ob die Query interne/sensible Inhalte enthält."""
        query_lower = query.lower()

        # Erst prüfen ob bekannte technische Begriffe vorhanden sind
        # In dem Fall strenger prüfen
        has_known_tech = any(term in query_lower for term in self.KNOWN_TECHNICAL_TERMS)

        for pattern in self._internal_patterns:
            match = pattern.search(query)
            if match:
                matched_text = match.group().lower()
                # Wenn der Match ein bekannter technischer Begriff ist, ignorieren
                if has_known_tech and any(term in matched_text for term in self.KNOWN_TECHNICAL_TERMS):
                    continue
                return True
        return False

    def _extract_keywords(self, query: str) -> List[str]:
        """Extrahiert relevante Keywords aus der Query."""
        # Entferne Stoppwörter
        stopwords = {
            "der", "die", "das", "ein", "eine", "und", "oder", "aber",
            "wie", "was", "wann", "wo", "warum", "welche", "welcher",
            "ich", "du", "er", "sie", "es", "wir", "ihr",
            "ist", "sind", "war", "waren", "wird", "werden",
            "kann", "können", "soll", "sollen", "muss", "müssen",
            "the", "a", "an", "and", "or", "but", "how", "what", "when",
            "where", "why", "which", "is", "are", "was", "were", "will",
            "can", "could", "should", "must", "have", "has", "had",
            "für", "mit", "von", "zu", "bei", "nach", "über", "unter",
            "for", "with", "from", "to", "at", "by", "about", "into",
        }

        # Tokenize
        words = re.findall(r'\b[a-zA-ZäöüßÄÖÜ]{3,}\b', query.lower())

        # Filter und dedupliziere
        keywords = []
        seen = set()
        for word in words:
            if word not in stopwords and word not in seen:
                keywords.append(word)
                seen.add(word)

        return keywords[:10]  # Max 10 Keywords


# ══════════════════════════════════════════════════════════════════════════════
# Query Sanitizer
# ══════════════════════════════════════════════════════════════════════════════

class QuerySanitizer:
    """
    Entfernt sensible Informationen aus Queries für Web-Recherche.

    Ersetzt interne Service-Namen, Projekt-IDs, IPs etc. durch
    generische Begriffe, sodass keine internen Daten nach außen gelangen.
    """

    # Ersetzungsregeln: (Pattern, Ersetzung)
    SANITIZATION_RULES = [
        # Service-Namen → "Service"
        (r'\b[A-Z][a-zA-Z]+Service\b', 'Service'),
        (r'\b[A-Z][a-zA-Z]+Controller\b', 'Controller'),
        (r'\b[A-Z][a-zA-Z]+Manager\b', 'Manager'),
        (r'\b[A-Z][a-zA-Z]+Client\b', 'Client'),
        (r'\b[A-Z][a-zA-Z]+Handler\b', 'Handler'),
        (r'\b[A-Z][a-zA-Z]+Repository\b', 'Repository'),

        # Projekt-Codes entfernen
        (r'\b[A-Z]{2,5}-\d{2,5}\b', ''),

        # IP-Adressen entfernen
        (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', ''),

        # Interne URLs entfernen
        (r'https?://[^\s]+(?:intranet|internal|corp)[^\s]*', ''),

        # Port-Nummern entfernen
        (r':\d{2,5}\b', ''),

        # Datenbank-Schemas → "database"
        (r'\b(?:schema|db)\s*[:\.]?\s*[a-zA-Z_]+\b', 'database'),

        # Tabellen-Namen → "table"
        (r'\btable\s+[a-zA-Z_]+\b', 'table'),

        # Spezifische Umgebungen entfernen
        (r'\b(?:prod|dev|test|staging|qa|uat)\b', '', re.IGNORECASE),

        # Mehrfache Leerzeichen normalisieren
        (r'\s+', ' '),
    ]

    def __init__(self, custom_patterns: Optional[List[str]] = None):
        """
        Args:
            custom_patterns: Zusätzliche Patterns die entfernt werden sollen
        """
        self._rules = []
        for rule in self.SANITIZATION_RULES:
            pattern = rule[0]
            replacement = rule[1]
            flags = rule[2] if len(rule) > 2 else 0
            self._rules.append((re.compile(pattern, flags), replacement))

        # Custom Patterns hinzufügen
        if custom_patterns:
            for pattern in custom_patterns:
                self._rules.append((re.compile(pattern), ''))

    def sanitize(self, query: str) -> str:
        """
        Entfernt sensible Informationen aus der Query.

        Args:
            query: Original-Query

        Returns:
            Bereinigte Query für Web-Recherche
        """
        sanitized = query

        for pattern, replacement in self._rules:
            sanitized = pattern.sub(replacement, sanitized)

        # Cleanup
        sanitized = sanitized.strip()
        sanitized = re.sub(r'\s+', ' ', sanitized)

        return sanitized

    def get_removed_terms(self, original: str, sanitized: str) -> List[str]:
        """Gibt die entfernten Begriffe zurück (für Logging/Debugging)."""
        original_words = set(original.lower().split())
        sanitized_words = set(sanitized.lower().split())
        return list(original_words - sanitized_words)


# ══════════════════════════════════════════════════════════════════════════════
# Research Router
# ══════════════════════════════════════════════════════════════════════════════

class ResearchRouter:
    """
    Koordiniert Multi-Source Research basierend auf Query-Klassifikation.

    Routing-Matrix:
    - TECHNICAL: Skills + Handbuch + Confluence + Web (sanitized)
    - BUSINESS: Skills + Handbuch + Confluence (kein Web)
    - INTERNAL: Skills + Handbuch + Confluence (kein Web)
    - MIXED: Skills + Handbuch + Confluence + Web (stark sanitized)
    """

    def __init__(
        self,
        skill_manager=None,
        handbook_service=None,
        confluence_service=None,
        web_search_service=None,
    ):
        self.classifier = QueryClassifier()
        self.sanitizer = QuerySanitizer()
        self.skill_manager = skill_manager
        self.handbook_service = handbook_service
        self.confluence_service = confluence_service
        self.web_search_service = web_search_service

    async def research(
        self,
        query: str,
        config: Optional[ResearchConfig] = None,
        session_id: Optional[str] = None,
    ) -> AggregatedContext:
        """
        Führt Multi-Source Research durch.

        Args:
            query: Die Recherche-Query
            config: Optionale Research-Konfiguration aus Skill
            session_id: Session-ID für Skill-Kontext

        Returns:
            AggregatedContext mit allen Ergebnissen
        """
        # Default-Config
        if config is None:
            config = ResearchConfig()

        # Query klassifizieren
        classification, keywords = self.classifier.classify(query)

        # Quellen basierend auf Klassifikation und Config auswählen
        sources = self._select_sources(classification, config)

        # Parallele Recherche
        results = await self._execute_parallel_research(
            query=query,
            keywords=keywords,
            classification=classification,
            sources=sources,
            config=config,
            session_id=session_id
        )

        # Ergebnisse aggregieren und ranken
        aggregated = self._aggregate_results(
            query=query,
            classification=classification,
            results=results,
            max_results=config.max_internal_results + config.max_web_results
        )

        return aggregated

    def _select_sources(
        self,
        classification: QueryClassification,
        config: ResearchConfig
    ) -> List[SourceType]:
        """Wählt Quellen basierend auf Klassifikation und Config."""
        allowed = set(config.allowed_sources)
        sources = []

        # Interne Quellen immer erlauben wenn konfiguriert
        if "skills" in allowed:
            sources.append(SourceType.SKILL)
        if "handbook" in allowed:
            sources.append(SourceType.HANDBOOK)
        if "confluence" in allowed:
            sources.append(SourceType.CONFLUENCE)
        if "code" in allowed:
            sources.append(SourceType.CODE)

        # Web nur bei bestimmten Klassifikationen und wenn erlaubt
        if "web" in allowed:
            if config.scope == ResearchScope.ALL:
                sources.append(SourceType.WEB)
            elif config.scope == ResearchScope.EXTERNAL_SAFE:
                if classification in (QueryClassification.TECHNICAL, QueryClassification.MIXED):
                    sources.append(SourceType.WEB)
            # INTERNAL_ONLY: Kein Web

        return sources

    async def _execute_parallel_research(
        self,
        query: str,
        keywords: List[str],
        classification: QueryClassification,
        sources: List[SourceType],
        config: ResearchConfig,
        session_id: Optional[str] = None,
    ) -> List[ResearchResult]:
        """Führt parallele Recherche in allen Quellen durch."""
        tasks = []

        for source in sources:
            if source == SourceType.SKILL:
                tasks.append(self._search_skills(query, keywords, session_id, config.max_internal_results))
            elif source == SourceType.HANDBOOK:
                tasks.append(self._search_handbook(query, keywords, config.max_internal_results))
            elif source == SourceType.CONFLUENCE:
                tasks.append(self._search_confluence(query, keywords, config.max_internal_results))
            elif source == SourceType.WEB:
                # Query sanitizen für Web
                sanitized_query = query
                if config.sanitize_queries:
                    sanitized_query = self.sanitizer.sanitize(query)
                tasks.append(self._search_web(sanitized_query, keywords, config.max_web_results))
            elif source == SourceType.CODE:
                tasks.append(self._search_code(query, keywords, config.max_internal_results))

        # Alle Tasks parallel ausführen
        if tasks:
            results_lists = await asyncio.gather(*tasks, return_exceptions=True)

            # Ergebnisse zusammenführen, Exceptions ignorieren
            all_results = []
            for result in results_lists:
                if isinstance(result, list):
                    all_results.extend(result)
                elif isinstance(result, Exception):
                    # Loggen aber nicht abbrechen
                    print(f"[ResearchRouter] Source error: {result}")

            return all_results

        return []

    async def _search_skills(
        self,
        query: str,
        keywords: List[str],
        session_id: Optional[str],
        max_results: int
    ) -> List[ResearchResult]:
        """Sucht in Skill-Wissensbasen."""
        if not self.skill_manager:
            return []

        try:
            # Aktive Skill-IDs holen (falls Session)
            skill_ids = None
            if session_id:
                skill_ids = list(self.skill_manager.get_active_skill_ids(session_id))

            results = self.skill_manager.search_knowledge(
                query=query,
                skill_ids=skill_ids,
                top_k=max_results
            )

            return [
                ResearchResult(
                    source=SourceType.SKILL,
                    source_name=r.skill_name,
                    content=r.snippet,
                    relevance_score=abs(r.rank) if r.rank else 0.5,
                    metadata={"skill_id": r.skill_id, "source_path": r.source_path}
                )
                for r in results
            ]
        except Exception as e:
            print(f"[ResearchRouter] Skill search error: {e}")
            return []

    async def _search_handbook(
        self,
        query: str,
        keywords: List[str],
        max_results: int
    ) -> List[ResearchResult]:
        """Sucht im Handbuch."""
        if not self.handbook_service:
            return []

        try:
            # Handbook-Service aufrufen (falls implementiert)
            results = await self.handbook_service.search(query, limit=max_results)

            return [
                ResearchResult(
                    source=SourceType.HANDBOOK,
                    source_name=r.get("title", "Handbuch"),
                    content=r.get("snippet", r.get("content", "")),
                    relevance_score=r.get("score", 0.5),
                    metadata={"path": r.get("path")}
                )
                for r in results
            ]
        except Exception as e:
            print(f"[ResearchRouter] Handbook search error: {e}")
            return []

    async def _search_confluence(
        self,
        query: str,
        keywords: List[str],
        max_results: int
    ) -> List[ResearchResult]:
        """Sucht in Confluence."""
        if not self.confluence_service:
            return []

        try:
            # Confluence-Service aufrufen (falls implementiert)
            results = await self.confluence_service.search(query, limit=max_results)

            return [
                ResearchResult(
                    source=SourceType.CONFLUENCE,
                    source_name=r.get("title", "Confluence"),
                    content=r.get("excerpt", r.get("content", "")),
                    relevance_score=r.get("score", 0.5),
                    url=r.get("url"),
                    metadata={"space": r.get("space"), "id": r.get("id")}
                )
                for r in results
            ]
        except Exception as e:
            print(f"[ResearchRouter] Confluence search error: {e}")
            return []

    async def _search_web(
        self,
        sanitized_query: str,
        keywords: List[str],
        max_results: int
    ) -> List[ResearchResult]:
        """Sucht im Web (mit sanitized Query)."""
        if not self.web_search_service:
            return []

        try:
            # Web-Search-Service aufrufen
            results = await self.web_search_service.search(
                query=sanitized_query,
                max_results=max_results
            )

            return [
                ResearchResult(
                    source=SourceType.WEB,
                    source_name=r.get("title", "Web"),
                    content=r.get("snippet", ""),
                    relevance_score=r.get("score", 0.5),
                    url=r.get("url"),
                    metadata={"domain": r.get("domain")}
                )
                for r in results
            ]
        except Exception as e:
            print(f"[ResearchRouter] Web search error: {e}")
            return []

    async def _search_code(
        self,
        query: str,
        keywords: List[str],
        max_results: int
    ) -> List[ResearchResult]:
        """Sucht im Code-Repository."""
        # Placeholder für Code-Suche
        return []

    def _aggregate_results(
        self,
        query: str,
        classification: QueryClassification,
        results: List[ResearchResult],
        max_results: int = 20
    ) -> AggregatedContext:
        """Aggregiert und rankt Ergebnisse."""
        # Deduplizierung nach Content-Hash
        seen_content = set()
        unique_results = []

        for result in results:
            content_hash = hash(result.content[:200] if len(result.content) > 200 else result.content)
            if content_hash not in seen_content:
                seen_content.add(content_hash)
                unique_results.append(result)

        # Nach Relevanz sortieren
        unique_results.sort(key=lambda r: r.relevance_score, reverse=True)

        # Auf max_results beschränken
        final_results = unique_results[:max_results]

        # Token-Schätzung (ca. 4 Zeichen pro Token)
        total_content = "".join(r.content for r in final_results)
        estimated_tokens = len(total_content) // 4

        # Verwendete Quellen
        sources_used = list(set(r.source for r in final_results))

        return AggregatedContext(
            query=query,
            classification=classification,
            results=final_results,
            total_tokens=estimated_tokens,
            sources_used=sources_used
        )


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_research_router: Optional[ResearchRouter] = None


def get_research_router() -> ResearchRouter:
    """Gibt die Singleton-Instanz des ResearchRouters zurück."""
    global _research_router
    if _research_router is None:
        # Services lazy importieren um Circular Imports zu vermeiden
        from app.services.skill_manager import get_skill_manager

        _research_router = ResearchRouter(
            skill_manager=get_skill_manager(),
            # Andere Services werden später hinzugefügt
        )
    return _research_router
