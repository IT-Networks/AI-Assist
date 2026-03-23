"""
Prompt Enhancer - Kontext-Anreicherung vor Task-Decomposition.

Pipeline:
1. Enhancement-Detector prüft ob Kontext-Sammlung nötig
2. Cache-Check für bereits angereicherte ähnliche Prompts
3. Context Collector sammelt relevanten Kontext
4. User-Bestätigung des gesammelten Kontexts
5. Weiterleitung an TaskPlanner mit angereichertem Prompt

MIGRATION (2026-03-23):
Capabilities (Research, Analyze) wurden zu Skills migriert.
Für Research/Analyze verwende die entsprechenden Skill-Commands:
- /research oder /sc:research
- /analyze oder /sc:analyze

Der PromptEnhancer liefert nun Hinweise auf Skills statt eigener Kontext-Sammlung.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.agent.constants import should_skip_enhancement

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════

class EnhancementType(str, Enum):
    """Art der Kontext-Anreicherung."""
    NONE = "none"                    # Keine Anreicherung nötig
    RESEARCH = "research"            # Wiki/Docs/Code durchsuchen -> Skill-Hint
    SEQUENTIAL = "sequential"        # Strukturierte Analyse (Debug, etc.)
    ANALYZE = "analyze"              # Bestehendes System verstehen -> Skill-Hint


class ConfirmationStatus(str, Enum):
    """Status der User-Bestätigung."""
    PENDING = "pending"              # Wartet auf Bestätigung
    CONFIRMED = "confirmed"          # User hat bestätigt
    REJECTED = "rejected"            # User hat abgelehnt
    MODIFIED = "modified"            # User hat Änderungen vorgenommen


# ══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ContextItem:
    """Ein einzelnes Kontext-Element."""
    source: str                      # z.B. "wiki", "code", "confluence"
    title: str                       # Kurzer Titel
    content: str                     # Der eigentliche Inhalt
    relevance: float = 1.0           # 0.0-1.0, wie relevant
    file_path: Optional[str] = None  # Falls aus Datei
    url: Optional[str] = None        # Falls aus Web/Wiki

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "content": self.content[:500] + "..." if len(self.content) > 500 else self.content,
            "relevance": self.relevance,
            "file_path": self.file_path,
            "url": self.url
        }

    def to_context_string(self) -> str:
        """Formatiert als Kontext-String für LLM."""
        header = f"## {self.title}"
        if self.file_path:
            header += f" ({self.file_path})"
        elif self.url:
            header += f" ({self.url})"
        return f"{header}\n{self.content}"


@dataclass
class EnrichedPrompt:
    """
    Angereicherter Prompt mit gesammeltem Kontext.

    WICHTIG: Sammelt NUR Kontext, strukturiert NICHT.
    Die Strukturierung übernimmt der TaskPlanner.
    """
    # Original
    original_query: str

    # Anreicherungs-Metadaten
    enhancement_type: EnhancementType
    enhanced_at: datetime = field(default_factory=datetime.utcnow)

    # Gesammelter Kontext (das Kernstück)
    context_items: List[ContextItem] = field(default_factory=list)

    # Zusammenfassung für User-Bestätigung
    summary: str = ""

    # Bestätigungs-Status
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING
    user_feedback: Optional[str] = None

    # Cache-Metadaten
    cache_key: Optional[str] = None
    cache_hit: bool = False

    @property
    def total_context_length(self) -> int:
        """Gesamtlänge des Kontexts in Zeichen."""
        return sum(len(item.content) for item in self.context_items)

    @property
    def context_sources(self) -> List[str]:
        """Liste der Kontext-Quellen."""
        return list(set(item.source for item in self.context_items))

    def get_context_for_planner(self) -> str:
        """
        Formatiert den Kontext für den TaskPlanner.

        Returns:
            Formatierter Kontext-String
        """
        if not self.context_items:
            return ""

        parts = [
            "# Gesammelter Kontext",
            f"Quellen: {', '.join(self.context_sources)}",
            ""
        ]

        for item in sorted(self.context_items, key=lambda x: -x.relevance):
            parts.append(item.to_context_string())
            parts.append("")

        return "\n".join(parts)

    def get_confirmation_message(self) -> str:
        """
        Erstellt die Nachricht für User-Bestätigung.

        Returns:
            Formatierte Bestätigungs-Nachricht
        """
        lines = [
            "## Kontext-Sammlung abgeschlossen",
            "",
            f"**Ursprüngliche Anfrage:** {self.original_query[:100]}...",
            "",
            f"**Gefundener Kontext:** ({len(self.context_items)} Elemente)",
        ]

        for item in self.context_items[:5]:  # Max 5 für Übersicht
            lines.append(f"  - [{item.source}] {item.title}")

        if len(self.context_items) > 5:
            lines.append(f"  - ... und {len(self.context_items) - 5} weitere")

        lines.extend([
            "",
            "**Zusammenfassung:**",
            self.summary or "(Keine Zusammenfassung verfügbar)",
            "",
            "Soll ich mit diesem Kontext fortfahren?",
            "- **Ja**: Weiter zur Task-Planung",
            "- **Nein**: Ohne Kontext fortfahren",
            "- **Mehr Details**: Vollständigen Kontext anzeigen",
        ])

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "enhancement_type": self.enhancement_type.value,
            "enhanced_at": self.enhanced_at.isoformat(),
            "context_items": [item.to_dict() for item in self.context_items],
            "summary": self.summary,
            "confirmation_status": self.confirmation_status.value,
            "total_context_length": self.total_context_length,
            "context_sources": self.context_sources
        }


# ══════════════════════════════════════════════════════════════════════════════
# Enhancement Cache
# ══════════════════════════════════════════════════════════════════════════════

class EnhancementCache:
    """
    Cache für angereicherte Prompts.

    Vermeidet redundante Aufrufe für ähnliche Anfragen.
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 50):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._cache: Dict[str, tuple[EnrichedPrompt, float]] = {}

    def _compute_key(self, query: str, enhancement_type: EnhancementType) -> str:
        """Berechnet Cache-Key aus Query und Typ."""
        # Normalisieren: lowercase, whitespace trimmen
        normalized = " ".join(query.lower().split())
        raw = f"{enhancement_type.value}:{normalized}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(
        self,
        query: str,
        enhancement_type: EnhancementType
    ) -> Optional[EnrichedPrompt]:
        """
        Holt gecachten EnrichedPrompt falls vorhanden und nicht expired.

        Returns:
            EnrichedPrompt oder None
        """
        key = self._compute_key(query, enhancement_type)

        if key not in self._cache:
            return None

        enriched, timestamp = self._cache[key]

        # TTL prüfen
        if time.time() - timestamp > self.ttl:
            del self._cache[key]
            return None

        # Cache Hit markieren
        enriched.cache_hit = True
        enriched.cache_key = key

        logger.debug(f"[EnhancementCache] Cache hit for key {key}")
        return enriched

    def set(self, enriched: EnrichedPrompt) -> str:
        """
        Speichert EnrichedPrompt im Cache.

        Returns:
            Cache-Key
        """
        key = self._compute_key(enriched.original_query, enriched.enhancement_type)

        # LRU: Älteste entfernen wenn voll
        if len(self._cache) >= self.max_entries:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]

        enriched.cache_key = key
        self._cache[key] = (enriched, time.time())

        logger.debug(f"[EnhancementCache] Cached with key {key}")
        return key

    def invalidate(self, key: Optional[str] = None) -> int:
        """
        Invalidiert Cache-Einträge.

        Args:
            key: Spezifischer Key oder None für alle

        Returns:
            Anzahl invalidierter Einträge
        """
        if key:
            if key in self._cache:
                del self._cache[key]
                return 1
            return 0

        count = len(self._cache)
        self._cache.clear()
        return count


# ══════════════════════════════════════════════════════════════════════════════
# Enhancement Detector
# ══════════════════════════════════════════════════════════════════════════════

class EnhancementDetector:
    """
    Erkennt ob und welche Art von Kontext-Anreicherung sinnvoll ist.
    """

    # Keywords für verschiedene Enhancement-Typen
    RESEARCH_TRIGGERS = [
        # Deutsch
        "wiki", "dokumentation", "handbuch", "confluence", "readme",
        "wie beschrieben", "laut", "gemäß", "nach vorlage", "siehe",
        "basierend auf", "entsprechend", "wie in",
        # Englisch
        "documentation", "as described", "according to", "based on",
        "as specified", "per the"
    ]

    SEQUENTIAL_TRIGGERS = [
        # Deutsch
        "warum", "wieso", "weshalb", "debug", "debugge", "analysiere",
        "verstehe nicht", "fehler", "problem", "bug", "exception",
        "funktioniert nicht", "geht nicht", "kaputt", "root cause",
        "ursache", "stacktrace", "traceback",
        # Englisch
        "why", "debug", "analyze", "doesn't work", "broken", "error",
        "issue", "investigate", "root cause"
    ]

    ANALYZE_TRIGGERS = [
        # Deutsch
        "refactor", "erweitere", "ändere", "impact", "auswirkung",
        "was passiert wenn", "abhängigkeiten", "dependencies",
        "bestehend", "existierend", "aktuell",
        # Englisch
        "refactor", "extend", "modify", "impact", "existing",
        "current", "dependencies"
    ]

    def detect(self, query: str) -> EnhancementType:
        """
        Erkennt den passenden Enhancement-Typ für eine Query.

        Args:
            query: Die User-Anfrage

        Returns:
            EnhancementType
        """
        query_lower = query.lower()

        # Prioritäts-Reihenfolge: Research > Sequential > Analyze
        # (spezifischer zu allgemeiner)

        # Research: Externe Quellen explizit referenziert
        if any(trigger in query_lower for trigger in self.RESEARCH_TRIGGERS):
            logger.debug("[EnhancementDetector] Detected: RESEARCH")
            return EnhancementType.RESEARCH

        # Sequential: Debug/Fehleranalyse
        if any(trigger in query_lower for trigger in self.SEQUENTIAL_TRIGGERS):
            logger.debug("[EnhancementDetector] Detected: SEQUENTIAL")
            return EnhancementType.SEQUENTIAL

        # Analyze: Bestehendes verstehen/erweitern
        if any(trigger in query_lower for trigger in self.ANALYZE_TRIGGERS):
            logger.debug("[EnhancementDetector] Detected: ANALYZE")
            return EnhancementType.ANALYZE

        # Keine Anreicherung nötig
        logger.debug("[EnhancementDetector] Detected: NONE")
        return EnhancementType.NONE

    def should_enhance(self, query: str) -> bool:
        """
        Prüft ob Enhancement sinnvoll ist.

        Args:
            query: Die User-Anfrage

        Returns:
            True wenn Enhancement empfohlen
        """
        # Zu kurze Queries nicht anreichern
        if len(query.strip()) < 20:
            return False

        # Skip-Marker prüfen (zentralisierte Konstanten)
        if should_skip_enhancement(query):
            return False

        return self.detect(query) != EnhancementType.NONE


# ══════════════════════════════════════════════════════════════════════════════
# Prompt Enhancer (Hauptklasse)
# ══════════════════════════════════════════════════════════════════════════════

class PromptEnhancer:
    """
    Orchestriert die Prompt-Anreicherung.

    Sammelt Kontext-Hinweise und bereitet
    den angereicherten Prompt für den TaskPlanner vor.

    MIGRATION (2026-03-23):
    Research/Analyze-Capabilities wurden zu Skills migriert.
    Der Enhancer liefert nun Skill-Empfehlungen statt eigener Kontext-Sammlung.
    """

    def __init__(
        self,
        cache: Optional[EnhancementCache] = None,
        mcp_callback: Optional[Callable] = None,
        event_callback: Optional[Callable] = None
    ):
        """
        Args:
            cache: Enhancement-Cache (optional)
            mcp_callback: Callback für MCP-Tool-Aufrufe
            event_callback: Callback für Events
        """
        self.cache = cache or EnhancementCache()
        self.detector = EnhancementDetector()
        self.mcp_callback = mcp_callback
        self.event_callback = event_callback

        # Lazy-loaded components
        self._sequential_thinking = None

    def _get_sequential_thinking(self):
        """Lazy-load SequentialThinking."""
        if self._sequential_thinking is None:
            try:
                from app.mcp.sequential_thinking import get_sequential_thinking
                self._sequential_thinking = get_sequential_thinking(
                    event_callback=self.event_callback
                )
            except ImportError as e:
                logger.debug(f"[PromptEnhancer] SequentialThinking not available: {e}")
        return self._sequential_thinking

    async def enhance(
        self,
        query: str,
        force_type: Optional[EnhancementType] = None,
        skip_cache: bool = False
    ) -> EnrichedPrompt:
        """
        Reichert einen Prompt mit Kontext an.

        Args:
            query: Die User-Anfrage
            force_type: Erzwingt bestimmten Enhancement-Typ
            skip_cache: Cache überspringen

        Returns:
            EnrichedPrompt mit gesammeltem Kontext
        """
        # Typ bestimmen
        enhancement_type = force_type or self.detector.detect(query)

        # Bei NONE: Leeren EnrichedPrompt zurückgeben
        if enhancement_type == EnhancementType.NONE:
            return EnrichedPrompt(
                original_query=query,
                enhancement_type=EnhancementType.NONE,
                confirmation_status=ConfirmationStatus.CONFIRMED  # Direkt bestätigt
            )

        # Cache prüfen
        if not skip_cache:
            cached = self.cache.get(query, enhancement_type)
            if cached:
                logger.info(f"[PromptEnhancer] Using cached enhancement")
                return cached

        # Event: Enhancement startet
        if self.event_callback:
            await self.event_callback("enhancement_start", {
                "type": enhancement_type.value,
                "query_length": len(query)
            })

        # Kontext sammeln basierend auf Typ
        try:
            # Progress-Event VOR der eigentlichen Arbeit
            if self.event_callback:
                await self.event_callback("MCP_PROGRESS", {
                    "mode": "enhancement",
                    "message": f"Sammle {enhancement_type.value}-Kontext...",
                    "progress": 20
                })

            context_items = await self._collect_context(query, enhancement_type)

            # Progress nach Kontext-Sammlung
            if self.event_callback:
                await self.event_callback("MCP_PROGRESS", {
                    "mode": "enhancement",
                    "message": f"Erstelle Zusammenfassung ({len(context_items)} Items)...",
                    "progress": 80
                })

            summary = await self._create_summary(query, context_items)

            # Bei leerem Kontext direkt bestätigen (nichts zu confirmen)
            confirmation = (
                ConfirmationStatus.CONFIRMED if not context_items
                else ConfirmationStatus.PENDING
            )

            enriched = EnrichedPrompt(
                original_query=query,
                enhancement_type=enhancement_type,
                context_items=context_items,
                summary=summary,
                confirmation_status=confirmation
            )

            # Nur cachen wenn Kontext gesammelt wurde
            if context_items:
                self.cache.set(enriched)

            # Event: Enhancement abgeschlossen
            if self.event_callback:
                await self.event_callback("enhancement_complete", {
                    "type": enhancement_type.value,
                    "context_count": len(context_items),
                    "total_length": enriched.total_context_length
                })

            return enriched

        except Exception as e:
            logger.warning(f"[PromptEnhancer] Enhancement failed: {e}")

            # Fallback: Leerer EnrichedPrompt (Task-System übernimmt direkt)
            return EnrichedPrompt(
                original_query=query,
                enhancement_type=enhancement_type,
                confirmation_status=ConfirmationStatus.CONFIRMED,  # Direkt weiter
                summary=f"Enhancement fehlgeschlagen: {e}"
            )

    async def _collect_context(
        self,
        query: str,
        enhancement_type: EnhancementType
    ) -> List[ContextItem]:
        """
        Sammelt Kontext basierend auf Enhancement-Typ.

        Args:
            query: Die User-Anfrage
            enhancement_type: Art der Anreicherung

        Returns:
            Liste von ContextItems
        """
        context_items = []

        if enhancement_type == EnhancementType.RESEARCH:
            context_items = await self._collect_research_hints(query)

        elif enhancement_type == EnhancementType.SEQUENTIAL:
            context_items = await self._collect_sequential_context(query)

        elif enhancement_type == EnhancementType.ANALYZE:
            context_items = await self._collect_analyze_hints(query)

        return context_items

    async def _collect_research_hints(self, query: str) -> List[ContextItem]:
        """
        Liefert Skill-Hinweise für Research statt eigener Kontext-Sammlung.

        MIGRATION: ResearchCapability wurde zu Skills migriert.
        """
        items = []

        # Hinweis auf verfügbare Research-Skills
        items.append(ContextItem(
            source="skill_hint",
            title="Research-Skill empfohlen",
            content=(
                "Für umfassende Recherche verwende /research oder /sc:research. "
                "Der Enterprise Research Skill durchsucht: "
                "Confluence, Handbook, Code-Index und Web (mit Query-Sanitization). "
                "Ergebnis: Strukturierter Report mit Quellen-Angaben."
            ),
            relevance=0.9
        ))

        logger.debug("[PromptEnhancer] Research hints provided (skill-based)")
        return items

    async def _collect_sequential_context(self, query: str) -> List[ContextItem]:
        """
        Sammelt Kontext für strukturierte Analyse.

        PERFORMANCE: Überspringt LLM-basiertes Sequential Thinking während Enhancement,
        da dies zu 30s+ Timeouts führt. Stattdessen wird eine schnelle heuristische
        Analyse verwendet. Das vollständige Sequential Thinking wird später im
        Tool-Phase (via /seq Kommando) oder bei Task-Decomposition ausgeführt.
        """
        items = []

        # PERFORMANCE: Kein LLM-Call während Enhancement - zu langsam (30s+ Timeout)
        # Stattdessen schnelle heuristische Analyse
        query_lower = query.lower()

        # Erkennung: Debug/Fehleranalyse
        if any(kw in query_lower for kw in ["fehler", "error", "bug", "exception", "stacktrace", "traceback"]):
            items.append(ContextItem(
                source="sequential_hint",
                title="Fehleranalyse empfohlen",
                content="Query enthält Fehler-Keywords. Empfehle strukturierte Debug-Analyse mit /seq Kommando.",
                relevance=0.8
            ))

        # Erkennung: Warum/Wieso-Fragen
        if any(kw in query_lower for kw in ["warum", "wieso", "weshalb", "why"]):
            items.append(ContextItem(
                source="sequential_hint",
                title="Ursachenanalyse empfohlen",
                content="Query enthält Warum-Frage. Empfehle Root-Cause-Analyse.",
                relevance=0.75
            ))

        # Erkennung: Debug-Anforderung
        if any(kw in query_lower for kw in ["debug", "debugge", "trace", "analysiere"]):
            items.append(ContextItem(
                source="sequential_hint",
                title="Debug-Modus empfohlen",
                content="Query enthält Debug-Keywords. Für tiefe Analyse /seq verwenden.",
                relevance=0.7
            ))

        if items:
            logger.debug(f"[PromptEnhancer] Sequential hints: {len(items)} (skipped LLM for performance)")
        else:
            logger.debug("[PromptEnhancer] No sequential context needed")

        return items

    async def _collect_analyze_hints(self, query: str) -> List[ContextItem]:
        """
        Liefert Skill-Hinweise für Analyze statt eigener Kontext-Sammlung.

        MIGRATION: AnalyzeCapability wurde zu Skills migriert.
        """
        items = []

        # Hinweis auf verfügbare Analyze-Skills
        items.append(ContextItem(
            source="skill_hint",
            title="Analyze-Skill empfohlen",
            content=(
                "Für Code-Analyse verwende /analyze oder /sc:analyze. "
                "Der Enterprise Analyze Skill prüft: "
                "OWASP Top 10, Performance (Big O), Architektur (12-Factor). "
                "Ergebnis: Severity-Rating mit actionable Recommendations."
            ),
            relevance=0.9
        ))

        logger.debug("[PromptEnhancer] Analyze hints provided (skill-based)")
        return items

    async def _create_summary(
        self,
        query: str,
        context_items: List[ContextItem]
    ) -> str:
        """
        Erstellt eine Zusammenfassung des gesammelten Kontexts.

        Args:
            query: Original-Query
            context_items: Gesammelte Kontext-Items

        Returns:
            Zusammenfassungs-String
        """
        if not context_items:
            return "Kein relevanter Kontext gefunden."

        sources = list(set(item.source for item in context_items))
        total_items = len(context_items)

        summary_parts = [
            f"Gefunden: {total_items} relevante Kontext-Elemente",
            f"Quellen: {', '.join(sources)}"
        ]

        # Top-3 nach Relevanz
        top_items = sorted(context_items, key=lambda x: -x.relevance)[:3]
        if top_items:
            summary_parts.append("Wichtigste Findings:")
            for item in top_items:
                summary_parts.append(f"  - {item.title}")

        return "\n".join(summary_parts)

    def confirm(
        self,
        enriched: EnrichedPrompt,
        confirmed: bool,
        feedback: Optional[str] = None
    ) -> EnrichedPrompt:
        """
        Verarbeitet User-Bestätigung.

        Args:
            enriched: Der angereicherte Prompt
            confirmed: True wenn bestätigt
            feedback: Optionales User-Feedback

        Returns:
            Aktualisierter EnrichedPrompt
        """
        if confirmed:
            enriched.confirmation_status = ConfirmationStatus.CONFIRMED
        else:
            enriched.confirmation_status = ConfirmationStatus.REJECTED

        enriched.user_feedback = feedback

        logger.info(
            f"[PromptEnhancer] Confirmation: {enriched.confirmation_status.value}"
        )

        return enriched


# ══════════════════════════════════════════════════════════════════════════════
# Singleton Access
# ══════════════════════════════════════════════════════════════════════════════

_prompt_enhancer: Optional[PromptEnhancer] = None
_enhancement_cache: Optional[EnhancementCache] = None


def get_enhancement_cache() -> EnhancementCache:
    """Gibt die Cache-Instanz zurück (Singleton)."""
    global _enhancement_cache
    if _enhancement_cache is None:
        _enhancement_cache = EnhancementCache(
            ttl_seconds=300,  # 5 Minuten
            max_entries=50
        )
    return _enhancement_cache


def get_prompt_enhancer(
    mcp_callback: Optional[Callable] = None,
    event_callback: Optional[Callable] = None
) -> PromptEnhancer:
    """
    Gibt die PromptEnhancer-Instanz zurück (Singleton).

    Args:
        mcp_callback: Callback für MCP-Aufrufe
        event_callback: Callback für Events

    Returns:
        PromptEnhancer-Instanz
    """
    global _prompt_enhancer
    if _prompt_enhancer is None:
        _prompt_enhancer = PromptEnhancer(
            cache=get_enhancement_cache(),
            mcp_callback=mcp_callback,
            event_callback=event_callback
        )
    else:
        # Always update callbacks if provided (fixes stale singleton issue)
        if event_callback:
            _prompt_enhancer.event_callback = event_callback
            # Reset lazy-loaded components to use new callback
            _prompt_enhancer._sequential_thinking = None
        if mcp_callback:
            _prompt_enhancer.mcp_callback = mcp_callback
    return _prompt_enhancer
