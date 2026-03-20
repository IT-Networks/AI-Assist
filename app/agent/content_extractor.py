"""
Content Extractor - Intelligente Extraktion relevanter Informationen aus großen Dokumenten.

Statt blindem Truncation:
1. Chunking des Dokuments (nach Struktur: Überschriften, Absätze)
2. Parallele Relevanz-Bewertung pro Chunk (schnelle LLM-Calls)
3. Extraktion nur relevanter Teile
4. Zusammenfassung für den Context

Anwendungsfall:
- Confluence-Seiten mit 80.000+ Tokens auf relevante 2.000 Tokens reduzieren
- Keine wichtigen Informationen verlieren durch intelligente Bewertung
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Konfiguration
# ══════════════════════════════════════════════════════════════════════════════

# Chunk-Größe: 5000 Tokens (~20KB) - groß genug für zusammenhängende Abschnitte
CHUNK_SIZE_TOKENS = 5000

# Minimum-Relevanz für Beibehaltung (0.0 - 1.0)
MIN_RELEVANCE_THRESHOLD = 0.35

# Maximale Ausgabe nach Extraktion
MAX_OUTPUT_TOKENS = 5000

# Dokumente unter diesem Limit werden nicht verarbeitet
SMALL_DOC_THRESHOLD = 6000

# Maximale parallele Scoring-Calls (verhindert Rate-Limiting)
MAX_PARALLEL_SCORES = 8


# ══════════════════════════════════════════════════════════════════════════════
# Datenstrukturen
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ChunkScore:
    """Bewertung eines Dokument-Chunks."""
    index: int
    content: str
    relevance_score: float  # 0.0 - 1.0
    reason: str  # Warum relevant/irrelevant
    token_estimate: int


@dataclass
class ExtractionResult:
    """Ergebnis der Content-Extraktion."""
    is_relevant: bool
    relevance_score: float  # Durchschnitt der relevanten Chunks
    extracted_content: str  # Komprimierte, relevante Infos
    original_tokens: int
    extracted_tokens: int
    chunks_total: int
    chunks_kept: int
    chunks_skipped: int
    processing_time_ms: int = 0


# ══════════════════════════════════════════════════════════════════════════════
# Pre-compiled Regex Patterns (Performance)
# ══════════════════════════════════════════════════════════════════════════════

# Markdown-Überschriften: # ## ### etc.
_RE_MD_HEADING = re.compile(r'\n(?=#{1,4}\s)')

# HTML-Überschriften: <h1> <h2> <h3>
_RE_HTML_HEADING = re.compile(r'(?=<h[1-4][^>]*>)', re.IGNORECASE)

# Confluence-Makros: {code} {panel} {info} etc.
_RE_CONFLUENCE_MACRO = re.compile(r'\n(?=\{[a-z]+[:\}])', re.IGNORECASE)

# Doppelte Newlines (Absätze)
_RE_PARAGRAPH = re.compile(r'\n\n+')

# JSON-Extraktion aus LLM-Response
_RE_JSON_EXTRACT = re.compile(r'\{[^{}]*"score"[^{}]*\}', re.DOTALL)


# ══════════════════════════════════════════════════════════════════════════════
# ContentExtractor
# ══════════════════════════════════════════════════════════════════════════════

class ContentExtractor:
    """
    Extrahiert relevante Informationen aus großen Dokumenten.

    Workflow:
    1. Dokument in Chunks splitten (nach Überschriften/Absätzen)
    2. Jeden Chunk parallel auf Relevanz prüfen (schneller LLM-Call)
    3. Nur relevante Chunks behalten
    4. Relevante Chunks zu kompakter Zusammenfassung verdichten

    Performance-Optimierungen:
    - Pre-compiled Regex Patterns
    - Parallele Chunk-Bewertung mit Semaphore
    - Frühzeitiger Exit bei kleinen Dokumenten
    - Token-Schätzung statt exakter Berechnung
    """

    def __init__(self, llm_client: "LLMClient"):
        """
        Args:
            llm_client: LLMClient-Instanz für Bewertungs-Calls
        """
        self._llm = llm_client
        self._semaphore = asyncio.Semaphore(MAX_PARALLEL_SCORES)

    async def extract_relevant(
        self,
        content: str,
        query: str,
        source_name: str = "Dokument",
        model: Optional[str] = None,
    ) -> ExtractionResult:
        """
        Extrahiert relevante Teile aus einem großen Dokument.

        Args:
            content: Das vollständige Dokument
            query: Die ursprüngliche Nutzer-Anfrage
            source_name: Name der Quelle für Logging
            model: LLM-Modell für Bewertung (default: tool_model aus Settings)

        Returns:
            ExtractionResult mit komprimiertem, relevantem Content
        """
        import time
        start_time = time.time()

        original_tokens = self._estimate_tokens(content)

        # Kleine Dokumente: Keine Extraktion nötig
        if original_tokens <= SMALL_DOC_THRESHOLD:
            return ExtractionResult(
                is_relevant=True,
                relevance_score=1.0,
                extracted_content=content,
                original_tokens=original_tokens,
                extracted_tokens=original_tokens,
                chunks_total=1,
                chunks_kept=1,
                chunks_skipped=0,
                processing_time_ms=int((time.time() - start_time) * 1000),
            )

        logger.info(
            f"[ContentExtractor] {source_name}: {original_tokens} Tokens → Chunk-Analyse"
        )

        # Phase 1: Intelligentes Chunking
        chunks = self._split_into_chunks(content)
        logger.debug(f"[ContentExtractor] {len(chunks)} Chunks erstellt")

        # Phase 2: Parallele Relevanz-Bewertung
        chunk_scores = await self._score_chunks_parallel(chunks, query, model)

        # Phase 3: Relevante Chunks filtern und sortieren
        relevant_chunks = sorted(
            [cs for cs in chunk_scores if cs.relevance_score >= MIN_RELEVANCE_THRESHOLD],
            key=lambda x: x.index  # Original-Reihenfolge beibehalten
        )

        processing_time = int((time.time() - start_time) * 1000)

        if not relevant_chunks:
            # Keine relevanten Chunks gefunden
            logger.info(
                f"[ContentExtractor] {source_name}: Keine relevanten Chunks "
                f"(Threshold: {MIN_RELEVANCE_THRESHOLD})"
            )
            return ExtractionResult(
                is_relevant=False,
                relevance_score=0.0,
                extracted_content=(
                    f"[{source_name}] Keine relevanten Informationen gefunden.\n"
                    f"Anfrage: {query[:100]}..."
                ),
                original_tokens=original_tokens,
                extracted_tokens=30,
                chunks_total=len(chunks),
                chunks_kept=0,
                chunks_skipped=len(chunks),
                processing_time_ms=processing_time,
            )

        # Phase 4: Relevante Chunks zusammenführen
        avg_score = sum(cs.relevance_score for cs in relevant_chunks) / len(relevant_chunks)
        combined_relevant = "\n\n---\n\n".join(cs.content for cs in relevant_chunks)
        combined_tokens = self._estimate_tokens(combined_relevant)

        # Wenn relevanter Content noch zu groß: Zusammenfassung generieren
        if combined_tokens > MAX_OUTPUT_TOKENS:
            logger.debug(
                f"[ContentExtractor] Zusammenfassung: {combined_tokens} → ~{MAX_OUTPUT_TOKENS} Tokens"
            )
            extracted = await self._summarize_relevant(combined_relevant, query, model)
        else:
            extracted = combined_relevant

        extracted_tokens = self._estimate_tokens(extracted)
        processing_time = int((time.time() - start_time) * 1000)

        logger.info(
            f"[ContentExtractor] {source_name}: "
            f"{original_tokens:,} → {extracted_tokens:,} Tokens "
            f"({len(relevant_chunks)}/{len(chunks)} Chunks, Score: {avg_score:.2f}, "
            f"{processing_time}ms)"
        )

        return ExtractionResult(
            is_relevant=True,
            relevance_score=avg_score,
            extracted_content=extracted,
            original_tokens=original_tokens,
            extracted_tokens=extracted_tokens,
            chunks_total=len(chunks),
            chunks_kept=len(relevant_chunks),
            chunks_skipped=len(chunks) - len(relevant_chunks),
            processing_time_ms=processing_time,
        )

    def _estimate_tokens(self, text: str) -> int:
        """Schnelle Token-Schätzung (~4 Zeichen pro Token)."""
        return len(text) // 4 if text else 0

    def _split_into_chunks(self, content: str) -> List[str]:
        """
        Splittet Dokument in Chunks, respektiert Dokumentstruktur.

        Priorität:
        1. Markdown-Überschriften (## ...)
        2. HTML-Überschriften (<h2>...)
        3. Confluence-Makros ({code}, {panel})
        4. Doppelte Newlines (Absätze)
        5. Feste Größe als Fallback
        """
        # Versuche verschiedene Splitting-Strategien
        sections = self._try_split_by_structure(content)

        # Chunks zusammenfügen bis CHUNK_SIZE erreicht
        chunks = self._merge_sections_to_chunks(sections)

        # Fallback: Zu große Chunks hart splitten
        final_chunks = self._split_oversized_chunks(chunks)

        return final_chunks

    def _try_split_by_structure(self, content: str) -> List[str]:
        """Versucht Dokument nach Struktur zu splitten."""
        # Strategie 1: Markdown-Überschriften
        sections = _RE_MD_HEADING.split(content)
        if len(sections) > 3:
            return [s.strip() for s in sections if s.strip()]

        # Strategie 2: HTML-Überschriften (Confluence)
        sections = _RE_HTML_HEADING.split(content)
        if len(sections) > 3:
            return [s.strip() for s in sections if s.strip()]

        # Strategie 3: Confluence-Makros
        sections = _RE_CONFLUENCE_MACRO.split(content)
        if len(sections) > 3:
            return [s.strip() for s in sections if s.strip()]

        # Strategie 4: Absätze
        sections = _RE_PARAGRAPH.split(content)
        return [s.strip() for s in sections if s.strip()]

    def _merge_sections_to_chunks(self, sections: List[str]) -> List[str]:
        """Fügt kleine Sections zu Chunks zusammen."""
        chunks = []
        current_chunk_parts = []
        current_size = 0
        max_chunk_chars = CHUNK_SIZE_TOKENS * 4  # ~4 chars per token

        for section in sections:
            section_size = len(section)

            # Wenn Section allein schon zu groß: Als eigenen Chunk
            if section_size > max_chunk_chars * 1.2:
                # Aktuellen Chunk abschließen
                if current_chunk_parts:
                    chunks.append("\n\n".join(current_chunk_parts))
                    current_chunk_parts = []
                    current_size = 0
                # Große Section als eigenen Chunk
                chunks.append(section)
                continue

            # Prüfen ob Section in aktuellen Chunk passt
            if current_size + section_size > max_chunk_chars and current_chunk_parts:
                # Chunk abschließen und neuen starten
                chunks.append("\n\n".join(current_chunk_parts))
                current_chunk_parts = [section]
                current_size = section_size
            else:
                # Section zum aktuellen Chunk hinzufügen
                current_chunk_parts.append(section)
                current_size += section_size

        # Letzten Chunk abschließen
        if current_chunk_parts:
            chunks.append("\n\n".join(current_chunk_parts))

        return chunks

    def _split_oversized_chunks(self, chunks: List[str]) -> List[str]:
        """Splittet zu große Chunks hart auf."""
        max_chunk_chars = CHUNK_SIZE_TOKENS * 4 * 1.5  # 50% Toleranz
        final_chunks = []

        for chunk in chunks:
            if len(chunk) > max_chunk_chars:
                # Hart in Teile splitten (mit Overlap für Kontext)
                chunk_size = CHUNK_SIZE_TOKENS * 4
                overlap = 200  # Zeichen Overlap

                for i in range(0, len(chunk), chunk_size - overlap):
                    end = min(i + chunk_size, len(chunk))
                    final_chunks.append(chunk[i:end])

                    if end >= len(chunk):
                        break
            else:
                final_chunks.append(chunk)

        return final_chunks

    async def _score_chunks_parallel(
        self,
        chunks: List[str],
        query: str,
        model: Optional[str],
    ) -> List[ChunkScore]:
        """
        Bewertet alle Chunks parallel auf Relevanz.

        Nutzt Semaphore um Rate-Limiting zu vermeiden.
        """

        async def score_with_semaphore(index: int, chunk: str) -> ChunkScore:
            async with self._semaphore:
                return await self._score_single_chunk(index, chunk, query, model)

        # Alle Chunks parallel bewerten
        scores = await asyncio.gather(*[
            score_with_semaphore(i, chunk)
            for i, chunk in enumerate(chunks)
        ])

        return list(scores)

    async def _score_single_chunk(
        self,
        index: int,
        chunk: str,
        query: str,
        model: Optional[str],
    ) -> ChunkScore:
        """Bewertet einen einzelnen Chunk auf Relevanz."""
        token_estimate = self._estimate_tokens(chunk)

        # Chunk-Preview für Bewertung (max 2000 Zeichen für schnelle Bewertung)
        preview = chunk[:2000] if len(chunk) > 2000 else chunk

        prompt = f"""Bewerte diesen Text-Abschnitt für die Anfrage.

ANFRAGE: {query}

TEXT-ABSCHNITT (Ausschnitt):
{preview}

Antworte NUR mit JSON (keine Erklärung):
{{"score": 0.0, "reason": "kurz"}}

Score-Bedeutung:
- 0.0-0.2: Irrelevant (Inhaltsverzeichnis, Boilerplate, andere Themen)
- 0.3-0.5: Teilweise relevant (erwähnt Thema, aber nicht zentral)
- 0.6-0.8: Relevant (enthält nützliche Informationen)
- 0.9-1.0: Hoch relevant (direkte Antworten, Kerninfos)"""

        try:
            response = await self._llm.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=80,
                temperature=0.0,
            )

            # JSON aus Response extrahieren
            json_match = _RE_JSON_EXTRACT.search(response)
            if json_match:
                data = json.loads(json_match.group(0))
                score = float(data.get("score", 0.5))
                # Score auf 0-1 begrenzen
                score = max(0.0, min(1.0, score))
                return ChunkScore(
                    index=index,
                    content=chunk,
                    relevance_score=score,
                    reason=data.get("reason", ""),
                    token_estimate=token_estimate,
                )
        except json.JSONDecodeError:
            logger.debug(f"[ContentExtractor] Chunk {index}: JSON-Parse fehlgeschlagen")
        except Exception as e:
            logger.warning(f"[ContentExtractor] Chunk {index} Scoring fehlgeschlagen: {e}")

        # Fallback: Mittlere Relevanz (sicher behalten)
        return ChunkScore(
            index=index,
            content=chunk,
            relevance_score=0.5,
            reason="Scoring fehlgeschlagen - behalten",
            token_estimate=token_estimate,
        )

    async def _summarize_relevant(
        self,
        combined_content: str,
        query: str,
        model: Optional[str],
    ) -> str:
        """Fasst relevante Chunks zu kompakter Zusammenfassung zusammen."""
        # Limitiere Input für Zusammenfassung
        max_input_chars = MAX_OUTPUT_TOKENS * 4 * 3  # 3x Output-Größe als Input
        if len(combined_content) > max_input_chars:
            combined_content = combined_content[:max_input_chars] + "\n\n[...weiterer Inhalt gekürzt...]"

        prompt = f"""Extrahiere die wichtigsten Informationen aus diesem Text für die Anfrage.

ANFRAGE: {query}

TEXT:
{combined_content}

AUSGABE-REGELN:
1. Nur Fakten die direkt zur Anfrage passen
2. Strukturiert mit Bullet-Points oder kurzen Absätzen
3. Maximal 1000 Wörter
4. Keine Wiederholungen
5. Quellenhinweise beibehalten (Dateinamen, IDs, Konfigurationswerte etc.)
6. Technische Details präzise übernehmen (Klassennamen, Pfade, Werte)

EXTRAHIERTE INFORMATIONEN:"""

        try:
            response = await self._llm.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.1,
            )
            return response.strip()
        except Exception as e:
            logger.error(f"[ContentExtractor] Zusammenfassung fehlgeschlagen: {e}")
            # Fallback: Erste MAX_OUTPUT_TOKENS Tokens des kombinierten Contents
            fallback_chars = MAX_OUTPUT_TOKENS * 4
            return combined_content[:fallback_chars] + "\n\n[...gekürzt wegen Fehler...]"


# ══════════════════════════════════════════════════════════════════════════════
# Convenience-Funktion
# ══════════════════════════════════════════════════════════════════════════════

async def extract_relevant_content(
    content: str,
    query: str,
    llm_client: "LLMClient",
    source_name: str = "Dokument",
    model: Optional[str] = None,
) -> ExtractionResult:
    """
    Convenience-Funktion für einmalige Content-Extraktion.

    Args:
        content: Das vollständige Dokument
        query: Die ursprüngliche Nutzer-Anfrage
        llm_client: LLMClient-Instanz
        source_name: Name der Quelle für Logging
        model: LLM-Modell für Bewertung

    Returns:
        ExtractionResult mit komprimiertem, relevantem Content
    """
    extractor = ContentExtractor(llm_client)
    return await extractor.extract_relevant(content, query, source_name, model)
