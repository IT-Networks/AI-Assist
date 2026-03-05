"""
Handbook Indexer - Indexiert HTML-Handbücher von Netzlaufwerken.

Unterstützt die Struktur:
/handbuch/
├── index.html
├── funktionen/           # Service-Funktionen (konfigurierbar)
│   ├── service-a/
│   │   ├── uebersicht.htm
│   │   ├── eingabe.htm
│   │   └── ausgabe.htm
│   └── service-b/
│       └── ...
└── felder/               # Feld-Definitionen (konfigurierbar)
    ├── feld-xyz.htm
    └── ...
"""

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import os


@dataclass
class ServiceInfo:
    """Informationen über einen Service aus dem Handbuch."""
    service_id: str
    service_name: str
    description: str = ""
    tabs: List[Dict] = field(default_factory=list)  # [{name, file_path}]
    input_fields: List[Dict] = field(default_factory=list)
    output_fields: List[Dict] = field(default_factory=list)
    call_variants: List[Dict] = field(default_factory=list)


@dataclass
class FieldInfo:
    """Informationen über ein Feld aus dem Handbuch."""
    field_id: str
    field_name: str
    field_type: str = ""
    description: str = ""
    used_in_services: List[str] = field(default_factory=list)
    source_file: str = ""


class HandbookIndexer:
    """
    SQLite FTS5-basierter Index für HTML-Handbücher.
    Ermöglicht schnelle Volltextsuche über Service-Dokumentation.
    """

    def __init__(self, db_path: str = "./index/handbook_index.db"):
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
                -- Volltextsuche über alle Handbuch-Seiten
                CREATE VIRTUAL TABLE IF NOT EXISTS handbook_fts USING fts5(
                    file_path UNINDEXED,
                    service_name,
                    tab_name,
                    title,
                    headings,
                    content,
                    tables_text,
                    tokenize='porter unicode61'
                );

                -- Service-Übersicht mit strukturierten Daten
                CREATE TABLE IF NOT EXISTS handbook_services (
                    service_id TEXT PRIMARY KEY,
                    service_name TEXT NOT NULL,
                    description TEXT,
                    tabs_json TEXT,
                    input_fields_json TEXT,
                    output_fields_json TEXT,
                    call_variants_json TEXT
                );

                -- Feld-Definitionen
                CREATE TABLE IF NOT EXISTS handbook_fields (
                    field_id TEXT PRIMARY KEY,
                    field_name TEXT NOT NULL,
                    field_type TEXT,
                    description TEXT,
                    used_in_services_json TEXT,
                    source_file TEXT
                );

                -- Metadaten
                CREATE TABLE IF NOT EXISTS handbook_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- Index für schnelle Datei-Lookup
                CREATE INDEX IF NOT EXISTS idx_handbook_files
                ON handbook_services(service_id);
            """)

    # ══════════════════════════════════════════════════════════════════════════
    # Build Index
    # ══════════════════════════════════════════════════════════════════════════

    def build(
        self,
        handbook_path: str,
        functions_subdir: str = "funktionen",
        fields_subdir: str = "felder",
        exclude_patterns: Optional[List[str]] = None,
        force: bool = False
    ) -> Dict:
        """
        Indexiert alle HTML-Dateien im Handbuch-Verzeichnis.

        Args:
            handbook_path: Pfad zum Handbuch-Verzeichnis
            functions_subdir: Name des Unterordners für Funktionen/Services
            fields_subdir: Name des Unterordners für Feld-Definitionen
            exclude_patterns: Glob-Patterns für auszuschließende Pfade
            force: True = alle Dateien neu indexieren

        Returns:
            Dict mit Statistiken (indexed, services, fields, errors, duration_s)
        """
        start = time.time()
        handbook = Path(handbook_path)

        if not handbook.exists():
            raise ValueError(f"Handbuch-Pfad existiert nicht: {handbook_path}")

        exclude_patterns = exclude_patterns or []
        stats = {"indexed": 0, "services": 0, "fields": 0, "errors": 0, "skipped": 0}

        # 1. Alle HTML/HTM Dateien finden
        html_files = self._find_html_files(handbook, exclude_patterns)

        # 2. Service-Struktur analysieren
        services = self._analyze_service_structure(handbook, functions_subdir)
        stats["services"] = len(services)

        # 3. Feld-Struktur analysieren
        field_infos = self._analyze_field_structure(handbook, fields_subdir)
        stats["fields"] = len(field_infos)

        # 4. Jede Datei indexieren (parallel)
        def index_file(html_file: Path) -> str:
            try:
                rel_path = str(html_file.relative_to(handbook))

                # Prüfen ob Datei unverändert (wenn nicht force)
                if not force:
                    mtime = html_file.stat().st_mtime
                    with self._connect() as con:
                        row = con.execute(
                            "SELECT value FROM handbook_meta WHERE key=?",
                            (f"mtime:{rel_path}",)
                        ).fetchone()
                        if row and abs(float(row[0]) - mtime) < 0.001:
                            return "skipped"

                self._index_html_file(html_file, handbook, services, functions_subdir)

                # mtime speichern
                with self._connect() as con:
                    con.execute(
                        "INSERT OR REPLACE INTO handbook_meta(key, value) VALUES (?, ?)",
                        (f"mtime:{rel_path}", str(html_file.stat().st_mtime))
                    )
                return "indexed"
            except Exception as e:
                print(f"Fehler bei {html_file}: {e}")
                return "error"

        with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
            futures = {pool.submit(index_file, f): f for f in html_files}
            for fut in as_completed(futures):
                result = fut.result()
                if result == "indexed":
                    stats["indexed"] += 1
                elif result == "skipped":
                    stats["skipped"] += 1
                else:
                    stats["errors"] += 1

        # 5. Services und Felder speichern
        for service in services.values():
            self._save_service(service)

        for field_info in field_infos.values():
            self._save_field(field_info)

        # 6. Metadaten aktualisieren
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('last_build', ?)",
                (str(int(time.time())),)
            )
            con.execute(
                "INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('handbook_path', ?)",
                (handbook_path,)
            )
            con.execute(
                "INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('total_files', ?)",
                (str(len(html_files)),)
            )

        stats["duration_s"] = round(time.time() - start, 2)
        return stats

    def _find_html_files(
        self,
        handbook: Path,
        exclude_patterns: List[str]
    ) -> List[Path]:
        """Findet alle HTML/HTM Dateien, exklusive der Patterns."""
        html_files = []

        for pattern in ["**/*.htm", "**/*.html"]:
            for f in handbook.glob(pattern):
                rel_path = str(f.relative_to(handbook))
                # Prüfen ob durch Pattern ausgeschlossen
                excluded = False
                for excl in exclude_patterns:
                    if fnmatch(rel_path, excl) or fnmatch(rel_path.replace("\\", "/"), excl):
                        excluded = True
                        break
                if not excluded:
                    html_files.append(f)

        return html_files

    def _analyze_service_structure(
        self,
        root: Path,
        functions_subdir: str
    ) -> Dict[str, ServiceInfo]:
        """Analysiert die Ordnerstruktur um Services und Tabs zu erkennen."""
        services = {}

        funktionen_dir = root / functions_subdir
        if funktionen_dir.exists():
            for service_dir in funktionen_dir.iterdir():
                if service_dir.is_dir():
                    service_id = service_dir.name
                    tabs = []
                    for htm_file in list(service_dir.glob("*.htm")) + list(service_dir.glob("*.html")):
                        tabs.append({
                            "name": htm_file.stem,
                            "file_path": str(htm_file.relative_to(root))
                        })

                    # Service-Name aus Ordnername ableiten
                    service_name = service_id.replace("-", " ").replace("_", " ").title()

                    services[service_id] = ServiceInfo(
                        service_id=service_id,
                        service_name=service_name,
                        tabs=tabs
                    )

        return services

    def _analyze_field_structure(
        self,
        root: Path,
        fields_subdir: str
    ) -> Dict[str, FieldInfo]:
        """Analysiert Feld-Definitionen aus dem Felder-Verzeichnis."""
        fields = {}

        felder_dir = root / fields_subdir
        if felder_dir.exists():
            for htm_file in list(felder_dir.glob("*.htm")) + list(felder_dir.glob("*.html")):
                field_id = htm_file.stem
                fields[field_id] = FieldInfo(
                    field_id=field_id,
                    field_name=field_id.replace("-", " ").replace("_", " ").title(),
                    source_file=str(htm_file.relative_to(root))
                )

        return fields

    def _index_html_file(
        self,
        file_path: Path,
        root: Path,
        services: Dict[str, ServiceInfo],
        functions_subdir: str
    ) -> None:
        """Parsed eine HTML-Datei und fügt sie zum Index hinzu."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            # Fallback ohne BeautifulSoup
            return self._index_html_file_simple(file_path, root, services, functions_subdir)

        content = file_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(content, "html.parser")

        # Metadaten extrahieren
        title = soup.title.string if soup.title else file_path.stem
        title = title.strip() if title else file_path.stem

        # Headings extrahieren
        headings = " ".join(
            h.get_text(strip=True)
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
        )

        # Text extrahieren (ohne Scripts/Styles)
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text_content = soup.get_text(separator=" ", strip=True)
        # Whitespace normalisieren
        text_content = re.sub(r'\s+', ' ', text_content)

        # Tabellen extrahieren
        tables_text = self._extract_tables(soup)

        # Service/Tab ermitteln
        rel_path = file_path.relative_to(root)
        service_name, tab_name = self._detect_service_tab(rel_path, functions_subdir)

        # In FTS5 Index einfügen
        with self._connect() as con:
            con.execute(
                "DELETE FROM handbook_fts WHERE file_path=?",
                (str(rel_path),)
            )
            con.execute(
                """INSERT INTO handbook_fts
                   (file_path, service_name, tab_name, title, headings, content, tables_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(rel_path),
                    service_name or "",
                    tab_name or "",
                    title[:500],
                    headings[:2000],
                    text_content[:100000],
                    tables_text[:50000]
                )
            )

    def _index_html_file_simple(
        self,
        file_path: Path,
        root: Path,
        services: Dict[str, ServiceInfo],
        functions_subdir: str
    ) -> None:
        """Einfache HTML-Indexierung ohne BeautifulSoup."""
        content = file_path.read_text(encoding="utf-8", errors="replace")

        # Einfache Tag-Entfernung
        text_content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        text_content = re.sub(r'<style[^>]*>.*?</style>', '', text_content, flags=re.DOTALL | re.IGNORECASE)
        text_content = re.sub(r'<[^>]+>', ' ', text_content)
        text_content = re.sub(r'\s+', ' ', text_content).strip()

        # Title extrahieren
        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else file_path.stem

        # Headings extrahieren
        headings = " ".join(re.findall(r'<h[1-4][^>]*>(.*?)</h[1-4]>', content, re.IGNORECASE | re.DOTALL))
        headings = re.sub(r'<[^>]+>', '', headings)

        rel_path = file_path.relative_to(root)
        service_name, tab_name = self._detect_service_tab(rel_path, functions_subdir)

        with self._connect() as con:
            con.execute("DELETE FROM handbook_fts WHERE file_path=?", (str(rel_path),))
            con.execute(
                """INSERT INTO handbook_fts
                   (file_path, service_name, tab_name, title, headings, content, tables_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(rel_path), service_name or "", tab_name or "", title[:500],
                 headings[:2000], text_content[:100000], "")
            )

    def _extract_tables(self, soup) -> str:
        """Extrahiert Tabelleninhalte als Text."""
        tables_text = []
        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                cells = [
                    td.get_text(strip=True)
                    for td in tr.find_all(["td", "th"])
                ]
                rows.append(" | ".join(cells))
            tables_text.append("\n".join(rows))
        return "\n\n".join(tables_text)

    def _detect_service_tab(
        self,
        rel_path: Path,
        functions_subdir: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Ermittelt Service und Tab aus dem Dateipfad."""
        parts = rel_path.parts

        # Prüfen ob im Funktionen-Verzeichnis
        if len(parts) >= 3 and parts[0] == functions_subdir:
            service_name = parts[1].replace("-", " ").replace("_", " ").title()
            tab_name = rel_path.stem
            return service_name, tab_name

        return None, None

    def _save_service(self, service: ServiceInfo) -> None:
        """Speichert Service-Informationen in der DB."""
        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO handbook_services
                   (service_id, service_name, description, tabs_json,
                    input_fields_json, output_fields_json, call_variants_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    service.service_id,
                    service.service_name,
                    service.description,
                    json.dumps(service.tabs, ensure_ascii=False),
                    json.dumps(service.input_fields, ensure_ascii=False),
                    json.dumps(service.output_fields, ensure_ascii=False),
                    json.dumps(service.call_variants, ensure_ascii=False),
                )
            )

    def _save_field(self, field_info: FieldInfo) -> None:
        """Speichert Feld-Informationen in der DB."""
        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO handbook_fields
                   (field_id, field_name, field_type, description,
                    used_in_services_json, source_file)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    field_info.field_id,
                    field_info.field_name,
                    field_info.field_type,
                    field_info.description,
                    json.dumps(field_info.used_in_services, ensure_ascii=False),
                    field_info.source_file,
                )
            )

    # ══════════════════════════════════════════════════════════════════════════
    # Search
    # ══════════════════════════════════════════════════════════════════════════

    def search(
        self,
        query: str,
        service_filter: Optional[str] = None,
        tab_filter: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict]:
        """
        Volltext-Suche über das gesamte Handbuch.

        Args:
            query: Suchbegriff(e)
            service_filter: Optional - nur in diesem Service suchen
            tab_filter: Optional - nur in diesem Tab suchen
            top_k: Maximale Anzahl Ergebnisse

        Returns:
            Liste von Dicts mit file_path, service_name, tab_name, title, snippet, rank
        """
        if not query.strip():
            return []

        safe_query = query.replace('"', '""')

        # SQL zusammenbauen mit optionalen Filtern
        sql = """
            SELECT file_path, service_name, tab_name, title,
                   snippet(handbook_fts, 5, '>>>', '<<<', '...', 30) AS snippet,
                   rank
            FROM handbook_fts
            WHERE handbook_fts MATCH ?
        """
        params = [safe_query]

        if service_filter:
            sql += " AND service_name = ?"
            params.append(service_filter)

        if tab_filter:
            sql += " AND tab_name = ?"
            params.append(tab_filter)

        sql += " ORDER BY rank LIMIT ?"
        params.append(top_k)

        with self._connect() as con:
            try:
                rows = con.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # Fallback: LIKE-Suche wenn FTS-Query ungültig
                like = f"%{query}%"
                rows = con.execute(
                    """SELECT file_path, service_name, tab_name, title,
                              substr(content, 1, 200) AS snippet, 0 AS rank
                       FROM handbook_fts
                       WHERE content LIKE ? OR title LIKE ? OR headings LIKE ?
                       LIMIT ?""",
                    (like, like, like, top_k)
                ).fetchall()

        return [
            {
                "file_path": row["file_path"],
                "service_name": row["service_name"],
                "tab_name": row["tab_name"],
                "title": row["title"],
                "snippet": row["snippet"],
                "rank": row["rank"],
            }
            for row in rows
        ]

    def get_page_content(self, file_path: str) -> Optional[str]:
        """Lädt den Inhalt einer Handbuch-Seite."""
        with self._connect() as con:
            row = con.execute(
                "SELECT content FROM handbook_fts WHERE file_path = ?",
                (file_path,)
            ).fetchone()

        return row["content"] if row else None

    def get_service_info(self, service_id: str) -> Optional[Dict]:
        """Gibt strukturierte Service-Informationen zurück."""
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM handbook_services WHERE service_id = ?",
                (service_id,)
            ).fetchone()

        if not row:
            return None

        return {
            "service_id": row["service_id"],
            "service_name": row["service_name"],
            "description": row["description"],
            "tabs": json.loads(row["tabs_json"] or "[]"),
            "input_fields": json.loads(row["input_fields_json"] or "[]"),
            "output_fields": json.loads(row["output_fields_json"] or "[]"),
            "call_variants": json.loads(row["call_variants_json"] or "[]"),
        }

    def list_services(self) -> List[Dict]:
        """Listet alle indexierten Services auf."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT service_id, service_name, description FROM handbook_services ORDER BY service_name"
            ).fetchall()

        return [
            {
                "service_id": row["service_id"],
                "service_name": row["service_name"],
                "description": row["description"],
            }
            for row in rows
        ]

    def get_field_info(self, field_id: str) -> Optional[Dict]:
        """Gibt Feld-Informationen zurück."""
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM handbook_fields WHERE field_id = ?",
                (field_id,)
            ).fetchone()

        if not row:
            return None

        return {
            "field_id": row["field_id"],
            "field_name": row["field_name"],
            "field_type": row["field_type"],
            "description": row["description"],
            "used_in_services": json.loads(row["used_in_services_json"] or "[]"),
            "source_file": row["source_file"],
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Status & Maintenance
    # ══════════════════════════════════════════════════════════════════════════

    def is_built(self) -> bool:
        """Prüft ob ein Index existiert und Daten enthält."""
        if not self.db_path.exists():
            return False
        with self._connect() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM handbook_fts"
            ).fetchone()[0]
        return count > 0

    def get_stats(self) -> Dict:
        """Gibt Index-Statistiken zurück."""
        if not self.db_path.exists():
            return {
                "is_built": False,
                "indexed_pages": 0,
                "services": 0,
                "fields": 0,
                "last_build": None,
                "handbook_path": None,
                "db_size_kb": 0
            }

        with self._connect() as con:
            page_count = con.execute("SELECT COUNT(*) FROM handbook_fts").fetchone()[0]
            service_count = con.execute("SELECT COUNT(*) FROM handbook_services").fetchone()[0]
            field_count = con.execute("SELECT COUNT(*) FROM handbook_fields").fetchone()[0]

            last_build_row = con.execute(
                "SELECT value FROM handbook_meta WHERE key='last_build'"
            ).fetchone()
            handbook_path_row = con.execute(
                "SELECT value FROM handbook_meta WHERE key='handbook_path'"
            ).fetchone()

        last_build = None
        if last_build_row:
            ts = int(last_build_row[0])
            last_build = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

        handbook_path = handbook_path_row[0] if handbook_path_row else None
        db_size_kb = round(self.db_path.stat().st_size / 1024, 1)

        return {
            "is_built": page_count > 0,
            "indexed_pages": page_count,
            "services": service_count,
            "fields": field_count,
            "last_build": last_build,
            "handbook_path": handbook_path,
            "db_size_kb": db_size_kb
        }

    def clear(self) -> None:
        """Löscht den gesamten Index."""
        with self._connect() as con:
            con.executescript("""
                DELETE FROM handbook_fts;
                DELETE FROM handbook_services;
                DELETE FROM handbook_fields;
                DELETE FROM handbook_meta;
            """)


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_handbook_indexer: Optional[HandbookIndexer] = None


def get_handbook_indexer() -> HandbookIndexer:
    """Gibt die Singleton-Instanz des Handbook-Indexers zurück."""
    global _handbook_indexer
    if _handbook_indexer is None:
        from app.core.config import settings
        db_path = Path(settings.index.directory) / "handbook_index.db"
        _handbook_indexer = HandbookIndexer(str(db_path))
    return _handbook_indexer
