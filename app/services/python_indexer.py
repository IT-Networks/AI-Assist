import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional


class PythonIndexer:
    """
    SQLite FTS5-basierter Index für Python-Repositories.
    Identisches Muster wie JavaIndexer – nur für .py Dateien.
    """

    def __init__(self, db_path: str = "./index/python_index.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS python_fts USING fts5(
                    file_path UNINDEXED,
                    module_name,
                    class_names,
                    function_names,
                    imports,
                    content,
                    tokenize='porter ascii'
                );
                CREATE TABLE IF NOT EXISTS python_files (
                    file_path TEXT PRIMARY KEY,
                    mtime     REAL,
                    size_kb   REAL
                );
                CREATE TABLE IF NOT EXISTS python_index_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    # ── Build ────────────────────────────────────────────────────────────────

    def build(self, repo_path: str, reader, force: bool = False) -> Dict:
        """
        Scannt alle .py Dateien und indexiert sie.
        reader: PythonReader-Instanz
        force:  True = alle Dateien neu indexieren (auch unveränderte)
        """
        start = time.time()
        indexed = 0
        skipped = 0
        errors = 0

        repo = Path(repo_path).resolve()
        py_files = [
            repo / rel for rel in reader.get_all_files()
        ]

        def _index_file(py_file: Path):
            rel = str(py_file.relative_to(repo))
            try:
                stat = py_file.stat()
            except OSError:
                return "error"
            mtime = stat.st_mtime
            size_kb = round(stat.st_size / 1024, 1)

            with self._connect() as con:
                if not force:
                    row = con.execute(
                        "SELECT mtime FROM python_files WHERE file_path=?", (rel,)
                    ).fetchone()
                    if row and abs(row["mtime"] - mtime) < 0.001:
                        return "skipped"

                try:
                    content = py_file.read_text(encoding="utf-8", errors="replace")
                    summary = reader.summarize_file(rel)
                    module_name = Path(rel).stem
                    class_names = " ".join(c["name"] for c in summary.get("classes", []))
                    function_names = " ".join(f["name"] for f in summary.get("functions", []))
                    imports = " ".join(summary.get("imports", []))
                except Exception:
                    return "error"

                con.execute("DELETE FROM python_fts WHERE file_path=?", (rel,))
                con.execute(
                    "INSERT INTO python_fts(file_path, module_name, class_names, function_names, imports, content) "
                    "VALUES (?,?,?,?,?,?)",
                    (rel, module_name, class_names, function_names, imports, content[:50000]),
                )
                con.execute(
                    "INSERT OR REPLACE INTO python_files(file_path, mtime, size_kb) VALUES (?,?,?)",
                    (rel, mtime, size_kb),
                )
                return "indexed"

        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
            futures = {pool.submit(_index_file, f): f for f in py_files}
            for fut in as_completed(futures):
                result = fut.result()
                if result == "indexed":
                    indexed += 1
                elif result == "skipped":
                    skipped += 1
                else:
                    errors += 1

        # Remove stale entries
        with self._connect() as con:
            all_indexed = {row[0] for row in con.execute("SELECT file_path FROM python_files")}
            current = {str(f.relative_to(repo)) for f in py_files}
            stale = all_indexed - current
            for path in stale:
                con.execute("DELETE FROM python_fts WHERE file_path=?", (path,))
                con.execute("DELETE FROM python_files WHERE file_path=?", (path,))

            con.execute(
                "INSERT OR REPLACE INTO python_index_meta(key,value) VALUES ('last_build',?)",
                (str(int(time.time())),),
            )
            con.execute(
                "INSERT OR REPLACE INTO python_index_meta(key,value) VALUES ('repo_path',?)",
                (repo_path,),
            )

        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "stale_removed": len(stale),
            "total_files": len(py_files),
            "duration_s": round(time.time() - start, 2),
        }

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """FTS5-Suche. Gibt [{file_path, snippet, rank}] zurück."""
        if not query.strip():
            return []

        safe_query = query.replace('"', '""')

        with self._connect() as con:
            try:
                rows = con.execute(
                    """
                    SELECT file_path,
                           snippet(python_fts, 5, '>>>', '<<<', '...', 20) AS snippet,
                           rank
                    FROM python_fts
                    WHERE python_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{query}%"
                rows = con.execute(
                    """
                    SELECT file_path,
                           substr(content, 1, 150) AS snippet,
                           0 AS rank
                    FROM python_fts
                    WHERE content LIKE ? OR class_names LIKE ? OR function_names LIKE ?
                    LIMIT ?
                    """,
                    (like, like, like, top_k),
                ).fetchall()

        return [
            {"file_path": row["file_path"], "snippet": row["snippet"], "rank": row["rank"]}
            for row in rows
        ]

    # ── Status ───────────────────────────────────────────────────────────────

    def is_built(self) -> bool:
        if not self.db_path.exists():
            return False
        with self._connect() as con:
            count = con.execute("SELECT COUNT(*) FROM python_files").fetchone()[0]
        return count > 0

    def get_stats(self) -> Dict:
        if not self.db_path.exists():
            return {"is_built": False, "indexed_files": 0, "last_build": None, "db_size_kb": 0}

        with self._connect() as con:
            count = con.execute("SELECT COUNT(*) FROM python_files").fetchone()[0]
            row = con.execute(
                "SELECT value FROM python_index_meta WHERE key='last_build'"
            ).fetchone()

        last_build = None
        if row:
            last_build = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(row[0])))

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
                DELETE FROM python_fts;
                DELETE FROM python_files;
                DELETE FROM python_index_meta;
            """)


# Singleton
_python_indexer: Optional[PythonIndexer] = None


def get_python_indexer() -> PythonIndexer:
    global _python_indexer
    if _python_indexer is None:
        from app.core.config import settings
        db_path = Path(settings.index.directory) / "python_index.db"
        _python_indexer = PythonIndexer(str(db_path))
    return _python_indexer
