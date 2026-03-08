import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional


class JavaIndexer:
    """
    SQLite FTS5-basierter Index für Java-Repositories.
    Ermöglicht schnelle Volltextsuche über alle .java Dateien,
    sodass beim Chat nur relevante Dateien in den Kontext geladen werden.
    """

    def __init__(self, db_path: str = "./index/java_index.db"):
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
            # Migration: Alten ASCII-Index auf Unicode umstellen
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='java_fts'")
            if cur.fetchone():
                # Prüfen ob alter Tokenizer verwendet wird (Index neu bauen nötig)
                cur = con.execute("SELECT sql FROM sqlite_master WHERE name='java_fts'")
                row = cur.fetchone()
                if row and "porter ascii" in (row[0] or "").lower():
                    print("[java_indexer] Migriere FTS5 Index von ASCII auf Unicode...")
                    con.execute("DROP TABLE IF EXISTS java_fts")
                    con.execute("DELETE FROM java_files")  # Force rebuild

            con.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS java_fts USING fts5(
                    file_path UNINDEXED,
                    package,
                    class_name,
                    method_names,
                    imports,
                    content,
                    tokenize='unicode61 remove_diacritics 0'
                );
                CREATE TABLE IF NOT EXISTS java_files (
                    file_path TEXT PRIMARY KEY,
                    mtime     REAL,
                    size_kb   REAL
                );
                CREATE TABLE IF NOT EXISTS java_index_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    # ── Build ────────────────────────────────────────────────────────────────

    def build(self, repo_path: str, reader, force: bool = False) -> Dict:
        """
        Scannt alle .java Dateien im Repository und indexiert sie.
        reader: JavaReader-Instanz
        force:  True = alle Dateien neu indexieren (auch unveränderte)
        """
        from app.services.java_reader import JavaReader  # avoid circular import at module level
        start = time.time()
        indexed = 0
        skipped = 0
        errors = 0

        repo = Path(repo_path).resolve()
        exclude = set(reader.exclude_dirs)
        max_bytes = reader.max_file_size

        java_files = [
            p for p in repo.rglob("*.java")
            if not any(exc in p.parts for exc in exclude)
            and p.stat().st_size <= max_bytes
        ]

        def _index_file(java_file: Path):
            rel = str(java_file.relative_to(repo))
            mtime = java_file.stat().st_mtime
            size_kb = round(java_file.stat().st_size / 1024, 1)

            with self._connect() as con:
                if not force:
                    row = con.execute(
                        "SELECT mtime FROM java_files WHERE file_path=?", (rel,)
                    ).fetchone()
                    if row and abs(row["mtime"] - mtime) < 0.001:
                        return "skipped"

                try:
                    content = java_file.read_text(encoding="utf-8", errors="replace")
                    summary = reader.summarize_file(rel)
                    package = summary.get("package", "")
                    class_name = summary.get("class_name", "")
                    method_names = summary.get("signatures", "")[:2000]
                    imports = " ".join(summary.get("imports", []))
                except Exception:
                    return "error"

                # Remove old entry, insert fresh
                con.execute("DELETE FROM java_fts WHERE file_path=?", (rel,))
                con.execute(
                    "INSERT INTO java_fts(file_path, package, class_name, method_names, imports, content) "
                    "VALUES (?,?,?,?,?,?)",
                    (rel, package, class_name, method_names, imports, content[:50000]),
                )
                con.execute(
                    "INSERT OR REPLACE INTO java_files(file_path, mtime, size_kb) VALUES (?,?,?)",
                    (rel, mtime, size_kb),
                )
                return "indexed"

        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
            futures = {pool.submit(_index_file, f): f for f in java_files}
            for fut in as_completed(futures):
                result = fut.result()
                if result == "indexed":
                    indexed += 1
                elif result == "skipped":
                    skipped += 1
                else:
                    errors += 1

        # Remove stale entries (files deleted from repo)
        with self._connect() as con:
            all_indexed = {row[0] for row in con.execute("SELECT file_path FROM java_files")}
            current = {str(f.relative_to(repo)) for f in java_files}
            stale = all_indexed - current
            for path in stale:
                con.execute("DELETE FROM java_fts WHERE file_path=?", (path,))
                con.execute("DELETE FROM java_files WHERE file_path=?", (path,))

            con.execute(
                "INSERT OR REPLACE INTO java_index_meta(key,value) VALUES ('last_build',?)",
                (str(int(time.time())),),
            )
            con.execute(
                "INSERT OR REPLACE INTO java_index_meta(key,value) VALUES ('repo_path',?)",
                (repo_path,),
            )

        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "stale_removed": len(stale),
            "total_files": len(java_files),
            "duration_s": round(time.time() - start, 2),
        }

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Volltext-FTS5-Suche. Gibt die relevantesten Dateipfade + Snippet zurück.
        Nutzt Porter-Stemming, d.h. 'ordering' findet auch 'OrderService'.
        """
        if not query.strip():
            return []

        # FTS5 MATCH-Syntax: escape quotes
        safe_query = query.replace('"', '""')

        with self._connect() as con:
            try:
                rows = con.execute(
                    """
                    SELECT file_path,
                           snippet(java_fts, 5, '>>>', '<<<', '...', 20) AS snippet,
                           rank
                    FROM java_fts
                    WHERE java_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback: LIKE-Suche wenn FTS-Query syntaktisch ungültig
                like = f"%{query}%"
                rows = con.execute(
                    """
                    SELECT file_path,
                           substr(content, 1, 150) AS snippet,
                           0 AS rank
                    FROM java_fts
                    WHERE content LIKE ? OR class_name LIKE ? OR method_names LIKE ?
                    LIMIT ?
                    """,
                    (like, like, like, top_k),
                ).fetchall()

        return [
            {
                "file_path": row["file_path"],
                "snippet": row["snippet"],
                "rank": row["rank"],
            }
            for row in rows
        ]

    # ── Status ───────────────────────────────────────────────────────────────

    def is_built(self) -> bool:
        if not self.db_path.exists():
            return False
        with self._connect() as con:
            count = con.execute("SELECT COUNT(*) FROM java_files").fetchone()[0]
        return count > 0

    def get_stats(self) -> Dict:
        if not self.db_path.exists():
            return {"is_built": False, "indexed_files": 0, "last_build": None, "db_size_kb": 0}

        with self._connect() as con:
            count = con.execute("SELECT COUNT(*) FROM java_files").fetchone()[0]
            last_build_row = con.execute(
                "SELECT value FROM java_index_meta WHERE key='last_build'"
            ).fetchone()

        last_build = None
        if last_build_row:
            ts = int(last_build_row[0])
            last_build = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

        db_size_kb = round(self.db_path.stat().st_size / 1024, 1)

        return {
            "is_built": count > 0,
            "indexed_files": count,
            "last_build": last_build,
            "db_size_kb": db_size_kb,
        }

    def clear(self) -> None:
        with self._connect() as con:
            con.executescript("""
                DELETE FROM java_fts;
                DELETE FROM java_files;
                DELETE FROM java_index_meta;
            """)


# Singleton-Instanz (wird von main.py mit korrektem Pfad initialisiert)
_java_indexer: Optional[JavaIndexer] = None


def get_java_indexer() -> JavaIndexer:
    global _java_indexer
    if _java_indexer is None:
        from app.core.config import settings
        db_path = Path(settings.index.directory) / "java_index.db"
        _java_indexer = JavaIndexer(str(db_path))
    return _java_indexer
