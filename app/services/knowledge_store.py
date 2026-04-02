"""
KnowledgeStore – Persistierung und Suche über gesammeltes Wissen.

Verwaltet:
- MD-Dateien im knowledge-base/ Ordner (nach Space gruppiert)
- SQLite FTS5 Index für schnelle Volltextsuche
- Frontmatter-Parsing für Metadaten

Pattern: Analog zu HandbookIndexer (SQLite FTS5, unicode61 tokenizer).
"""

import re
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.agent.knowledge_collector.models import KnowledgeEntry

logger = logging.getLogger(__name__)

# Singleton
_knowledge_store: Optional["KnowledgeStore"] = None


def get_knowledge_store() -> "KnowledgeStore":
    """Gibt die Singleton-Instanz zurück. Muss vorher via init_knowledge_store() initialisiert werden."""
    global _knowledge_store
    if _knowledge_store is None:
        from app.core.config import settings
        _knowledge_store = KnowledgeStore(settings.knowledge_base.path)
    return _knowledge_store


def init_knowledge_store(base_path: str) -> "KnowledgeStore":
    """Initialisiert den KnowledgeStore (beim Startup)."""
    global _knowledge_store
    _knowledge_store = KnowledgeStore(base_path)
    return _knowledge_store


# Regex für Frontmatter-Extraktion
_RE_FRONTMATTER = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
# Regex für Summary-Sektion
_RE_SUMMARY = re.compile(r'##\s*Zusammenfassung\s*\n(.*?)(?=\n##\s|\Z)', re.DOTALL)


class KnowledgeStore:
    """
    Verwaltet die Knowledge-Base: MD-Dateien + FTS5-Index.

    Ordnerstruktur:
        knowledge-base/
        ├── {space-key}/
        │   ├── {topic-slug}.md
        │   └── {topic-2}.md
        ├── _allgemein/
        │   └── {topic}.md
        └── _index.db           ← SQLite FTS5
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_path / "_index.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_db(self):
        """Erstellt SQLite FTS5 + Meta-Tabellen."""
        with self._connect() as con:
            con.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    path,
                    title,
                    summary,
                    content,
                    tags,
                    space,
                    tokenize='unicode61'
                );

                CREATE TABLE IF NOT EXISTS knowledge_meta (
                    path TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    space TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    date TEXT NOT NULL DEFAULT '',
                    pages_analyzed INTEGER DEFAULT 0,
                    pdfs_analyzed INTEGER DEFAULT 0,
                    confidence TEXT DEFAULT 'medium',
                    source_pages TEXT DEFAULT '',
                    providers TEXT DEFAULT ''
                );
            """)

    async def save(
        self,
        topic: str,
        space: str,
        content: str,
        metadata: Dict,
    ) -> str:
        """
        Speichert MD-Datei und aktualisiert FTS5-Index.

        Args:
            topic: Thema (wird zum Dateinamen slugifiziert)
            space: Space-Key (wird zum Ordnernamen)
            content: Vollständiger MD-Inhalt (inkl. Frontmatter)
            metadata: Zusätzliche Metadaten für den Index

        Returns:
            Absoluter Pfad zur geschriebenen MD-Datei
        """
        slug = self._slugify(topic)
        space_dir = self.base_path / (space.lower() if space else "_allgemein")
        space_dir.mkdir(parents=True, exist_ok=True)

        md_path = space_dir / f"{slug}.md"
        md_path.write_text(content, encoding="utf-8")

        # Metadaten aus Frontmatter + übergebenen Metadaten zusammenführen
        frontmatter = self._parse_frontmatter(content)
        merged = {**frontmatter, **metadata}

        rel_path = str(md_path.relative_to(self.base_path))
        self._index_md(rel_path, content, merged)

        logger.info(f"[KnowledgeStore] Gespeichert: {rel_path}")
        return str(md_path)

    def _index_md(self, rel_path: str, content: str, metadata: Dict):
        """Fügt eine MD-Datei in den FTS5-Index ein (oder aktualisiert sie)."""
        title = metadata.get("title", "")
        space = metadata.get("space", "")
        summary = metadata.get("summary", "")
        tags = metadata.get("tags", [])
        if isinstance(tags, list):
            tags = ",".join(tags)
        date = metadata.get("date", datetime.now().strftime("%Y-%m-%d"))
        pages_analyzed = metadata.get("pages_analyzed", 0)
        pdfs_analyzed = metadata.get("pdfs_analyzed", 0)
        confidence = metadata.get("confidence", "medium")
        source_pages = metadata.get("source_pages", "")
        if isinstance(source_pages, list):
            source_pages = ",".join(source_pages)
        providers = metadata.get("providers", "")
        if isinstance(providers, list):
            providers = ",".join(providers)

        # Summary aus MD extrahieren falls nicht in Metadata
        if not summary:
            match = _RE_SUMMARY.search(content)
            if match:
                summary = match.group(1).strip()[:800]

        # Inhalt ohne Frontmatter für FTS
        content_body = _RE_FRONTMATTER.sub("", content)

        with self._connect() as con:
            # Alte Einträge entfernen (Upsert-Semantik)
            con.execute("DELETE FROM knowledge_fts WHERE path = ?", (rel_path,))
            con.execute("DELETE FROM knowledge_meta WHERE path = ?", (rel_path,))

            # FTS5 Index
            con.execute(
                "INSERT INTO knowledge_fts (path, title, summary, content, tags, space) VALUES (?, ?, ?, ?, ?, ?)",
                (rel_path, title, summary, content_body, tags, space),
            )

            # Meta-Tabelle
            con.execute("""
                INSERT INTO knowledge_meta (path, title, space, summary, tags, date, pages_analyzed, pdfs_analyzed, confidence, source_pages, providers)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rel_path, title, space, summary, tags, date, pages_analyzed, pdfs_analyzed, confidence, source_pages, providers))

    async def search(self, query: str, top_k: int = 5) -> List[KnowledgeEntry]:
        """
        FTS5-Suche über alle Knowledge-MDs.

        Args:
            query: Suchanfrage (wird für FTS5 aufbereitet)
            top_k: Max. Ergebnisse

        Returns:
            Nach Relevanz sortierte KnowledgeEntry-Liste
        """
        # Query für FTS5 aufbereiten (Terme mit OR verbinden für breitere Suche)
        fts_query = self._build_fts_query(query)

        results = []
        try:
            with self._connect() as con:
                cursor = con.execute("""
                    SELECT m.path, m.title, m.space, m.summary, m.tags, m.date,
                           m.pages_analyzed, m.confidence, f.rank
                    FROM knowledge_fts f
                    JOIN knowledge_meta m ON f.path = m.path
                    WHERE knowledge_fts MATCH ?
                    ORDER BY f.rank
                    LIMIT ?
                """, (fts_query, top_k))

                for row in cursor:
                    tags_str = row["tags"] or ""
                    results.append(KnowledgeEntry(
                        path=row["path"],
                        title=row["title"],
                        space=row["space"],
                        summary=row["summary"],
                        tags=[t.strip() for t in tags_str.split(",") if t.strip()],
                        date=row["date"],
                        pages_analyzed=row["pages_analyzed"],
                        confidence=row["confidence"],
                        relevance_score=abs(row["rank"]),
                    ))
        except Exception as e:
            logger.warning(f"[KnowledgeStore] Suchfehler: {e}")

        return results

    async def list_all(self, space: Optional[str] = None) -> List[KnowledgeEntry]:
        """Listet alle Knowledge-Einträge, optional gefiltert nach Space."""
        with self._connect() as con:
            if space:
                cursor = con.execute(
                    "SELECT * FROM knowledge_meta WHERE space = ? ORDER BY date DESC",
                    (space,),
                )
            else:
                cursor = con.execute("SELECT * FROM knowledge_meta ORDER BY date DESC")

            results = []
            for row in cursor:
                tags_str = row["tags"] or ""
                results.append(KnowledgeEntry(
                    path=row["path"],
                    title=row["title"],
                    space=row["space"],
                    summary=row["summary"],
                    tags=[t.strip() for t in tags_str.split(",") if t.strip()],
                    date=row["date"],
                    pages_analyzed=row["pages_analyzed"],
                    confidence=row["confidence"],
                ))
            return results

    async def get_full_content(self, path: str) -> str:
        """
        Liest eine MD-Datei vollständig.

        Args:
            path: Relativer Pfad (z.B. "dev/deployment.md")
        """
        full_path = self.base_path / path
        if not full_path.exists():
            return f"[Fehler] Datei nicht gefunden: {path}"
        return full_path.read_text(encoding="utf-8")

    async def exists(self, topic: str, space: str) -> Optional[str]:
        """
        Prüft ob ein Thema bereits existiert (Duplikat-Check).

        Returns:
            Pfad zur existierenden MD oder None
        """
        slug = self._slugify(topic)
        space_dir = self.base_path / (space.lower() if space else "_allgemein")
        md_path = space_dir / f"{slug}.md"
        if md_path.exists():
            return str(md_path.relative_to(self.base_path))
        return None

    async def reindex(self):
        """Rebuilt den FTS5-Index aus allen vorhandenen MD-Dateien."""
        with self._connect() as con:
            con.execute("DELETE FROM knowledge_fts")
            con.execute("DELETE FROM knowledge_meta")

        count = 0
        for md_file in self.base_path.rglob("*.md"):
            if md_file.name.startswith("_"):
                continue
            content = md_file.read_text(encoding="utf-8")
            metadata = self._parse_frontmatter(content)
            rel_path = str(md_file.relative_to(self.base_path))
            self._index_md(rel_path, content, metadata)
            count += 1

        logger.info(f"[KnowledgeStore] Reindex abgeschlossen: {count} Dateien")

    # ══════════════════════════════════════════════════════════════════════════
    # Hilfsfunktionen
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _slugify(text: str) -> str:
        """Konvertiert Text zu einem Dateinamen-kompatiblen Slug."""
        slug = text.lower().strip()
        # Umlaute ersetzen
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
        for old, new in replacements.items():
            slug = slug.replace(old, new)
        # Nur alphanumerische Zeichen und Bindestriche
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        return slug[:80] if slug else "untitled"

    @staticmethod
    def _parse_frontmatter(content: str) -> Dict:
        """Extrahiert YAML-Frontmatter aus MD-Inhalt."""
        match = _RE_FRONTMATTER.match(content)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """
        Baut eine FTS5-kompatible Query.

        Sicherheit: Entfernt alle FTS5-Operatoren (AND, OR, NOT, NEAR, Quotes)
        und Sonderzeichen. Nur alphanumerische Terme werden als Prefix-Suche verwendet.
        """
        if not query or not query.strip():
            return "___empty___"  # FTS5 braucht mindestens einen Term

        # Alles entfernen was kein Wort-Zeichen ist (inkl. FTS5-Operatoren)
        cleaned = re.sub(r'[^\w\säöüßÄÖÜ]', ' ', query, flags=re.UNICODE)
        # FTS5 reservierte Woerter entfernen
        fts5_reserved = {"AND", "OR", "NOT", "NEAR"}
        terms = [
            t.strip() for t in cleaned.split()
            if len(t.strip()) >= 2 and t.strip().upper() not in fts5_reserved
        ]
        if not terms:
            # Fallback: Ersten Term des Originals verwenden
            fallback = re.sub(r'[^\w]', '', query)[:20]
            return f"{fallback}*" if fallback else "___empty___"
        # Terme als Prefix-Suche (term*) fuer breitere Ergebnisse
        return " ".join(f"{t}*" for t in terms[:10])
