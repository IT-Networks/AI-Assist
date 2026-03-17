import re
import sqlite3
from pathlib import Path
from typing import List, Optional


class PDFIndexer:
    """
    SQLite FTS5-basierter Index für hochgeladene PDFs.
    Indexiert jede Seite als eigenen Chunk, sodass beim Chat
    nur die zum Thema passenden Seiten in den Kontext geladen werden.
    """

    def __init__(self, db_path: str = "./index/pdf_index.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        # Performance: WAL mode + Timeout bei Lock-Konflikten
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5(
                    pdf_id   UNINDEXED,
                    page_num UNINDEXED,
                    content,
                    tokenize='unicode61 remove_diacritics 0'
                );
            """)

    # ── Index ─────────────────────────────────────────────────────────────────

    def index_pdf(self, pdf_id: str, text: str) -> int:
        """
        Indexiert den Volltext einer hochgeladenen PDF.
        text: Gesamttext wie von PDFReader.extract_text() geliefert,
              mit Seitenmarkierungen "--- Seite N ---".
        Gibt die Anzahl indexierter Seiten zurück.
        """
        pages = self._split_into_pages(text)
        if not pages:
            return 0

        with self._connect() as con:
            # Alte Einträge entfernen (Re-upload desselben PDFs)
            con.execute("DELETE FROM pdf_fts WHERE pdf_id=?", (pdf_id,))
            con.executemany(
                "INSERT INTO pdf_fts(pdf_id, page_num, content) VALUES (?,?,?)",
                [(pdf_id, page_num, content) for page_num, content in pages],
            )

        return len(pages)

    def _split_into_pages(self, text: str) -> List[tuple]:
        """
        Teilt den Gesamttext anhand der Seitenmarkierungen auf.
        Format: "--- Seite N ---\nContent"
        Gibt Liste von (page_num, content) zurück.
        """
        pattern = re.compile(r"---\s*Seite\s*(\d+)\s*---", re.IGNORECASE)
        parts = pattern.split(text)

        # parts = [before_first, page_num, content, page_num, content, ...]
        pages = []
        i = 1
        while i + 1 < len(parts):
            page_num = int(parts[i])
            content = parts[i + 1].strip()
            if content and "[Kein extrahierbarer Text]" not in content:
                pages.append((page_num, content))
            i += 2

        # Falls keine Seitenmarkierungen → gesamten Text als Seite 1
        if not pages and text.strip():
            pages = [(1, text.strip())]

        return pages

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, pdf_id: str, query: str, top_k: int = 5) -> str:
        """
        Sucht in den indizierten Seiten eines PDFs nach der Abfrage.
        Gibt die relevantesten Seiten als formatierten String zurück
        (direkt als LLM-Kontext verwendbar).
        """
        if not query.strip() or not self.has_pdf(pdf_id):
            return ""

        safe_query = query.replace('"', '""')

        with self._connect() as con:
            try:
                rows = con.execute(
                    """
                    SELECT page_num, content, rank
                    FROM pdf_fts
                    WHERE pdf_id=? AND pdf_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (pdf_id, safe_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback LIKE-Suche
                like = f"%{query}%"
                rows = con.execute(
                    """
                    SELECT page_num, content, 0 AS rank
                    FROM pdf_fts
                    WHERE pdf_id=? AND content LIKE ?
                    ORDER BY page_num
                    LIMIT ?
                    """,
                    (pdf_id, like, top_k),
                ).fetchall()

        if not rows:
            return ""

        # Seiten nach Seitenzahl sortiert ausgeben (lesbarer als nach Relevanz)
        sorted_rows = sorted(rows, key=lambda r: r["page_num"])
        parts = [
            f"--- Seite {row['page_num']} ---\n{row['content']}"
            for row in sorted_rows
        ]
        return "\n\n".join(parts)

    def get_page_count(self, pdf_id: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM pdf_fts WHERE pdf_id=?", (pdf_id,)
            ).fetchone()
        return row[0] if row else 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def has_pdf(self, pdf_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM pdf_fts WHERE pdf_id=? LIMIT 1", (pdf_id,)
            ).fetchone()
        return row is not None

    def remove_pdf(self, pdf_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM pdf_fts WHERE pdf_id=?", (pdf_id,))

    def search_all(self, query: str, top_k: int = 10) -> List[dict]:
        """
        Sucht über ALLE indizierten PDFs nach der Abfrage.

        Args:
            query: Suchbegriff
            top_k: Maximale Anzahl Ergebnisse

        Returns:
            Liste von Dicts mit: pdf_id, page, chunk, score
        """
        if not query.strip():
            return []

        safe_query = query.replace('"', '""')
        results = []

        with self._connect() as con:
            try:
                rows = con.execute(
                    """
                    SELECT pdf_id, page_num, content, rank
                    FROM pdf_fts
                    WHERE pdf_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback: LIKE-Suche (langsamer aber robuster)
                like = f"%{query}%"
                rows = con.execute(
                    """
                    SELECT pdf_id, page_num, content, 0 AS rank
                    FROM pdf_fts
                    WHERE content LIKE ?
                    ORDER BY page_num
                    LIMIT ?
                    """,
                    (like, top_k),
                ).fetchall()

            for row in rows:
                # Rank ist negativ bei FTS5 (niedrigerer Wert = besser)
                # Normalisieren zu 0.0-1.0 Score
                rank = row["rank"]
                score = 1.0 / (1.0 + abs(rank)) if rank else 0.5

                results.append({
                    "pdf_id": row["pdf_id"],
                    "page": row["page_num"],
                    "chunk": row["content"],
                    "score": score,
                    "filename": row["pdf_id"]  # PDF-ID ist oft der Filename
                })

        return results

    def get_pdf_count(self) -> int:
        """Gibt die Anzahl indexierter PDFs zurück."""
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(DISTINCT pdf_id) FROM pdf_fts"
            ).fetchone()
        return row[0] if row else 0


# Singleton
_pdf_indexer: Optional[PDFIndexer] = None


def get_pdf_indexer() -> PDFIndexer:
    global _pdf_indexer
    if _pdf_indexer is None:
        from app.core.config import settings
        db_path = Path(settings.index.directory) / "pdf_index.db"
        _pdf_indexer = PDFIndexer(str(db_path))
    return _pdf_indexer
