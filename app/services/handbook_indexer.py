"""
Handbook Indexer - Indexiert HTML-Handbücher von Netzlaufwerken.

Optimiert für große Handbücher (100.000+ Dateien):
- Progress-Streaming während Indexierung
- Batching für DB-Operationen
- Abbruch-Möglichkeit
- Inkrementelle Updates

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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import AsyncGenerator, Callable, Dict, Generator, List, Optional, Tuple
import os


@dataclass
class ServiceInfo:
    """Informationen über einen Service aus dem Handbuch."""
    service_id: str
    service_name: str
    description: str = ""
    tabs: List[Dict] = field(default_factory=list)
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


@dataclass
class IndexProgress:
    """Progress-Status während der Indexierung."""
    phase: str  # scanning, analyzing, indexing, saving, done, error
    total_files: int = 0
    processed_files: int = 0
    current_file: str = ""
    services_found: int = 0
    fields_found: int = 0
    errors: int = 0
    skipped: int = 0
    elapsed_seconds: float = 0
    estimated_remaining_seconds: float = 0
    message: str = ""

    def to_dict(self) -> Dict:
        return {
            "phase": self.phase,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "current_file": self.current_file,
            "services_found": self.services_found,
            "fields_found": self.fields_found,
            "errors": self.errors,
            "skipped": self.skipped,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "estimated_remaining_seconds": round(self.estimated_remaining_seconds, 1),
            "message": self.message,
            "percent": round(self.processed_files / max(self.total_files, 1) * 100, 1)
        }


class HandbookIndexer:
    """
    SQLite FTS5-basierter Index für HTML-Handbücher.
    Ermöglicht schnelle Volltextsuche über Service-Dokumentation.
    Optimiert für große Handbücher mit Progress-Streaming.
    """

    def __init__(self, db_path: str = "./index/handbook_index.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._cancel_flag = threading.Event()
        self._current_progress: Optional[IndexProgress] = None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        # Performance-Optimierungen für Batch-Inserts
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA cache_size=10000")
        con.execute("PRAGMA busy_timeout=5000")
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
    # Abort / Cancel
    # ══════════════════════════════════════════════════════════════════════════

    def cancel_build(self) -> None:
        """Bricht die laufende Indexierung ab."""
        self._cancel_flag.set()

    def is_cancelled(self) -> bool:
        """Prüft ob Abbruch angefordert wurde."""
        return self._cancel_flag.is_set()

    def get_current_progress(self) -> Optional[Dict]:
        """Gibt den aktuellen Progress zurück (für Polling)."""
        if self._current_progress:
            return self._current_progress.to_dict()
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # Build Index (mit Progress Generator)
    # ══════════════════════════════════════════════════════════════════════════

    def build_with_progress(
        self,
        handbook_path: str,
        functions_subdir: str = "funktionen",
        fields_subdir: str = "felder",
        exclude_patterns: Optional[List[str]] = None,
        force: bool = False,
        batch_size: int = 500,
        structure_mode: str = "auto",
        known_tab_suffixes: Optional[List[str]] = None,
        parallel_workers: int = 8
    ) -> Generator[IndexProgress, None, Dict]:
        """
        Indexiert alle HTML-Dateien mit Progress-Streaming.

        Args:
            handbook_path: Pfad zum Handbuch-Verzeichnis
            functions_subdir: Unterordner für Funktionen (bei directory-Modus)
            fields_subdir: Unterordner für Felder
            exclude_patterns: Glob-Patterns zum Ausschließen
            force: Alle Dateien neu indexieren
            batch_size: Batch-Größe für DB-Operationen
            structure_mode: "auto", "directory" oder "flat"
            known_tab_suffixes: Tab-Suffixe für flache Struktur
            parallel_workers: Anzahl paralleler Threads für Netzwerk-I/O

        Yields:
            IndexProgress Objekte mit aktuellem Status

        Returns:
            Dict mit Statistiken am Ende
        """
        # Speichere Struktur-Einstellungen für spätere Verwendung
        self._structure_mode = structure_mode
        self._known_tab_suffixes = set(known_tab_suffixes or [
            'statistik', 'use_cases', 'aenderungen', 'dqm',
            'fachlich', 'intern', 'parameter', 'uebersicht',
            'eingabe', 'ausgabe', 'allgemein', 'technik',
            'historie', 'beispiele', 'varianten', 'fehler'
        ])
        self._cancel_flag.clear()
        start = time.time()
        handbook = Path(handbook_path)

        progress = IndexProgress(phase="scanning", message="Suche HTML-Dateien...")
        self._current_progress = progress
        yield progress

        if not handbook.exists():
            progress.phase = "error"
            progress.message = f"Pfad existiert nicht: {handbook_path}"
            yield progress
            return {"error": progress.message}

        exclude_patterns = exclude_patterns or []

        # 1. Dateien zählen (schnell, ohne vollständige Liste)
        progress.message = "Zähle Dateien..."
        yield progress

        html_files = []
        file_count = 0

        for pattern in ["**/*.htm", "**/*.html"]:
            for f in handbook.glob(pattern):
                if self._cancel_flag.is_set():
                    progress.phase = "cancelled"
                    progress.message = "Abgebrochen durch Benutzer"
                    yield progress
                    return {"cancelled": True}

                rel_path = str(f.relative_to(handbook))
                excluded = any(
                    fnmatch(rel_path, excl) or fnmatch(rel_path.replace("\\", "/"), excl)
                    for excl in exclude_patterns
                )
                if not excluded:
                    html_files.append(f)
                    file_count += 1

                    # Progress alle 1000 Dateien
                    if file_count % 1000 == 0:
                        progress.total_files = file_count
                        progress.message = f"Gefunden: {file_count} Dateien..."
                        yield progress

        progress.total_files = len(html_files)
        progress.phase = "analyzing"
        progress.message = f"{len(html_files)} HTML-Dateien gefunden. Analysiere Struktur..."
        yield progress

        # Speichere Build-Status: in_progress + erwartete Dateien
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('build_status', 'in_progress')")
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('total_files_expected', ?)", (str(len(html_files)),))
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', '0')")
            con.commit()

        # 2. Service-Struktur analysieren
        services = self._analyze_service_structure(handbook, functions_subdir)
        progress.services_found = len(services)

        # 3. Feld-Struktur analysieren
        field_infos = self._analyze_field_structure(handbook, fields_subdir)
        progress.fields_found = len(field_infos)

        progress.message = f"{len(services)} Services, {len(field_infos)} Felder gefunden"
        yield progress

        # 4. Indexierung mit paralleler Dateiverarbeitung
        progress.phase = "indexing"
        indexed = 0
        skipped = 0
        errors = 0

        def parse_file_worker(html_file: Path) -> Optional[Tuple[Path, str, float, Tuple]]:
            """Worker für paralleles Parsen (Thread-safe)."""
            try:
                rel_path = str(html_file.relative_to(handbook))
                mtime = html_file.stat().st_mtime
                data = self._parse_html_file(html_file, handbook, functions_subdir)
                return (html_file, rel_path, mtime, data)
            except Exception:
                return None

        def save_batch_to_db(con, batch_results):
            """Speichert geparste Dateien in DB (sequentiell)."""
            nonlocal indexed, errors
            for result in batch_results:
                if result is None:
                    errors += 1
                    continue
                html_file, rel_path, mtime, data = result
                try:
                    con.execute("DELETE FROM handbook_fts WHERE file_path=?", (rel_path,))
                    con.execute(
                        """INSERT INTO handbook_fts
                           (file_path, service_name, tab_name, title, headings, content, tables_text)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        data
                    )
                    con.execute(
                        "INSERT OR REPLACE INTO handbook_meta(key, value) VALUES (?, ?)",
                        (f"mtime:{rel_path}", str(mtime))
                    )
                    indexed += 1
                except Exception:
                    errors += 1

        # Optimierte parallele Verarbeitung
        chunk_size = parallel_workers * 25  # Größere Chunks: 200 Dateien bei 8 Workern

        def get_file_mtime(html_file: Path) -> Optional[Tuple[Path, str, float]]:
            """Worker für parallelen mtime-Check (schneller als sequentiell über Netzwerk)."""
            try:
                rel_path = str(html_file.relative_to(handbook))
                mtime = html_file.stat().st_mtime
                return (html_file, rel_path, mtime)
            except Exception:
                return None

        with self._connect() as con:
            # Lade bereits indexierte mtimes für Skip-Check
            existing_mtimes = {}
            if not force:
                rows = con.execute("SELECT key, value FROM handbook_meta WHERE key LIKE 'mtime:%'").fetchall()
                for row in rows:
                    existing_mtimes[row[0][6:]] = float(row[1])  # Entferne "mtime:" Prefix

            # Ein Executor für die gesamte Indexierung (nicht pro Chunk neu erstellen)
            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                i = 0
                while i < len(html_files):
                    if self._cancel_flag.is_set():
                        # Status speichern: cancelled
                        con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('build_status', 'cancelled')")
                        con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', ?)", (str(i),))
                        con.commit()
                        progress.phase = "cancelled"
                        progress.message = "Abgebrochen durch Benutzer"
                        yield progress
                        return {"cancelled": True, "indexed": indexed}

                    # Chunk von Dateien holen
                    chunk = html_files[i:i + chunk_size]

                    # PARALLELER mtime-Check (statt sequentiell)
                    mtime_results = list(executor.map(get_file_mtime, chunk))

                    files_to_process = []
                    for result in mtime_results:
                        if result is None:
                            errors += 1
                            continue
                        html_file, rel_path, mtime = result
                        if rel_path in existing_mtimes and abs(existing_mtimes[rel_path] - mtime) < 0.001:
                            skipped += 1
                        else:
                            files_to_process.append(html_file)

                    # Paralleles Parsen der nicht-übersprungenen Dateien
                    if files_to_process:
                        results = list(executor.map(parse_file_worker, files_to_process))
                        # Sequentielles Speichern in DB
                        save_batch_to_db(con, results)
                        con.commit()

                    i += chunk_size

                # Progress Update
                progress.processed_files = min(i, len(html_files))
                progress.errors = errors
                progress.skipped = skipped
                progress.elapsed_seconds = time.time() - start

                # Geschätzte Restzeit
                processed = indexed + skipped + errors
                if processed > 0:
                    avg_time = progress.elapsed_seconds / processed
                    remaining = len(html_files) - progress.processed_files
                    progress.estimated_remaining_seconds = avg_time * remaining

                progress.message = f"Indexiere... {indexed} neu, {skipped} übersprungen ({parallel_workers} Threads)"

                # Fortschritt in DB speichern (für Fortsetzung nach Abbruch)
                con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', ?)", (str(min(i, len(html_files))),))
                con.commit()
                yield progress

        # 5. Services und Felder speichern
        progress.phase = "saving"
        progress.message = "Speichere Services und Felder..."
        yield progress

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
            # Build-Status: complete
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('build_status', 'complete')")
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', ?)", (str(len(html_files)),))
            con.commit()

        # Fertig
        progress.phase = "done"
        progress.elapsed_seconds = time.time() - start
        progress.message = f"Fertig! {indexed} indexiert, {skipped} übersprungen, {errors} Fehler"
        self._current_progress = None
        yield progress

        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "services": len(services),
            "fields": len(field_infos),
            "total_files": len(html_files),
            "duration_s": round(time.time() - start, 2)
        }

    def _parse_html_file(
        self,
        file_path: Path,
        root: Path,
        functions_subdir: str
    ) -> Tuple[str, str, str, str, str, str, str]:
        """Parsed eine HTML-Datei und gibt Tuple für DB-Insert zurück."""
        try:
            from bs4 import BeautifulSoup
            return self._parse_with_bs4(file_path, root, functions_subdir)
        except ImportError:
            return self._parse_simple(file_path, root, functions_subdir)

    def _parse_with_bs4(
        self,
        file_path: Path,
        root: Path,
        functions_subdir: str
    ) -> Tuple[str, str, str, str, str, str, str]:
        """Parsed mit BeautifulSoup (lxml bevorzugt für Performance)."""
        from bs4 import BeautifulSoup

        content = file_path.read_text(encoding="utf-8", errors="replace")
        # lxml ist ~5x schneller als html.parser
        try:
            soup = BeautifulSoup(content, "lxml")
        except Exception:
            soup = BeautifulSoup(content, "html.parser")

        title = soup.title.string if soup.title else file_path.stem
        title = (title.strip() if title else file_path.stem)[:500]

        headings = " ".join(
            h.get_text(strip=True)
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
        )[:2000]

        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text_content = soup.get_text(separator=" ", strip=True)
        text_content = re.sub(r'\s+', ' ', text_content)[:100000]

        tables_text = self._extract_tables(soup)[:50000]

        rel_path = file_path.relative_to(root)
        service_name, tab_name = self._detect_service_tab(rel_path, functions_subdir)

        return (
            str(rel_path),
            service_name or "",
            tab_name or "",
            title,
            headings,
            text_content,
            tables_text
        )

    def _parse_simple(
        self,
        file_path: Path,
        root: Path,
        functions_subdir: str
    ) -> Tuple[str, str, str, str, str, str, str]:
        """Einfaches Parsing ohne BeautifulSoup."""
        content = file_path.read_text(encoding="utf-8", errors="replace")

        text_content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        text_content = re.sub(r'<style[^>]*>.*?</style>', '', text_content, flags=re.DOTALL | re.IGNORECASE)
        text_content = re.sub(r'<[^>]+>', ' ', text_content)
        text_content = re.sub(r'\s+', ' ', text_content).strip()[:100000]

        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        title = (title_match.group(1).strip() if title_match else file_path.stem)[:500]

        headings = " ".join(re.findall(r'<h[1-4][^>]*>(.*?)</h[1-4]>', content, re.IGNORECASE | re.DOTALL))
        headings = re.sub(r'<[^>]+>', '', headings)[:2000]

        rel_path = file_path.relative_to(root)
        service_name, tab_name = self._detect_service_tab(rel_path, functions_subdir)

        return (
            str(rel_path),
            service_name or "",
            tab_name or "",
            title,
            headings,
            text_content,
            ""
        )

    # Legacy build method (ohne Progress)
    def build(
        self,
        handbook_path: str,
        functions_subdir: str = "funktionen",
        fields_subdir: str = "felder",
        exclude_patterns: Optional[List[str]] = None,
        force: bool = False
    ) -> Dict:
        """Legacy build - sammelt alle Progress-Events und gibt nur Endergebnis."""
        result = {}
        for progress in self.build_with_progress(
            handbook_path, functions_subdir, fields_subdir, exclude_patterns, force
        ):
            if progress.phase == "done":
                result = {
                    "indexed": progress.processed_files - progress.skipped - progress.errors,
                    "skipped": progress.skipped,
                    "errors": progress.errors,
                    "services": progress.services_found,
                    "fields": progress.fields_found,
                    "duration_s": progress.elapsed_seconds
                }
        return result

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
                excluded = any(
                    fnmatch(rel_path, excl) or fnmatch(rel_path.replace("\\", "/"), excl)
                    for excl in exclude_patterns
                )
                if not excluded:
                    html_files.append(f)

        return html_files

    def _analyze_service_structure(
        self,
        root: Path,
        functions_subdir: str
    ) -> Dict[str, ServiceInfo]:
        """
        Analysiert die Struktur um Services und Tabs zu erkennen.

        Unterstützt drei Modi (via self._structure_mode):
        - "auto": Erkennt automatisch ob Unterordner existieren
        - "directory": Erwartet funktionen/SERVICE_NAME/tab.htm
        - "flat": Erwartet FUNKTIONSNAME_tabname.htm (alle in einem Ordner)
        """
        services = {}

        # Tab-Suffixe aus Config oder Default
        known_tabs = getattr(self, '_known_tab_suffixes', {
            'statistik', 'use_cases', 'aenderungen', 'dqm',
            'fachlich', 'intern', 'parameter', 'uebersicht',
            'eingabe', 'ausgabe', 'allgemein', 'technik',
            'historie', 'beispiele', 'varianten', 'fehler'
        })

        structure_mode = getattr(self, '_structure_mode', 'auto')

        funktionen_dir = root / functions_subdir
        if not funktionen_dir.exists():
            # Fallback: root selbst als Funktions-Verzeichnis (flache Struktur)
            funktionen_dir = root

        # Bestimme Modus
        use_flat_mode = False
        if structure_mode == "flat":
            use_flat_mode = True
        elif structure_mode == "directory":
            use_flat_mode = False
        else:  # auto
            # Prüfe ob Unterordner existieren
            try:
                has_subdirs = any(
                    d.is_dir() for d in funktionen_dir.iterdir()
                    if not d.name.startswith('.')
                )
                use_flat_mode = not has_subdirs or funktionen_dir == root
            except (PermissionError, OSError):
                use_flat_mode = True

        if not use_flat_mode:
            # Modus 1: Ordner-basierte Struktur
            for service_dir in funktionen_dir.iterdir():
                if service_dir.is_dir() and not service_dir.name.startswith('.'):
                    service_id = service_dir.name
                    tabs = []
                    for htm_file in list(service_dir.glob("*.htm")) + list(service_dir.glob("*.html")):
                        tabs.append({
                            "name": htm_file.stem,
                            "file_path": str(htm_file.relative_to(root))
                        })

                    service_name = service_id.replace("-", " ").replace("_", " ").title()

                    services[service_id] = ServiceInfo(
                        service_id=service_id,
                        service_name=service_name,
                        tabs=tabs
                    )
        else:
            # Modus 2: Flache Struktur mit Namenskonvention
            # Sammle alle HTM-Dateien und gruppiere nach Funktionsname
            file_groups: Dict[str, List[Dict]] = {}

            for htm_file in list(funktionen_dir.glob("*.htm")) + list(funktionen_dir.glob("*.html")):
                filename = htm_file.stem  # z.B. "ALIAS_LESEN_statistik"

                # Versuche Tab-Suffix zu erkennen
                service_id = None
                tab_name = None

                # Suche nach bekanntem Tab-Suffix am Ende
                for tab in known_tabs:
                    if filename.lower().endswith(f"_{tab}"):
                        # Gefunden: extrahiere Service-Name
                        service_id = filename[:-(len(tab) + 1)]  # +1 für Unterstrich
                        tab_name = tab
                        break

                if not service_id:
                    # Fallback: Letzter Unterstrich trennt Service und Tab
                    if "_" in filename:
                        parts = filename.rsplit("_", 1)
                        service_id = parts[0]
                        tab_name = parts[1]
                    else:
                        # Kein Unterstrich: gesamter Name ist Service
                        service_id = filename
                        tab_name = "hauptseite"

                if service_id not in file_groups:
                    file_groups[service_id] = []

                file_groups[service_id].append({
                    "name": tab_name,
                    "file_path": str(htm_file.relative_to(root))
                })

            # Erstelle ServiceInfo für jede Gruppe
            for service_id, tabs in file_groups.items():
                service_name = service_id.replace("-", " ").replace("_", " ").title()
                services[service_id] = ServiceInfo(
                    service_id=service_id,
                    service_name=service_name,
                    tabs=sorted(tabs, key=lambda t: t["name"])
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
        """
        Ermittelt Service und Tab aus dem Dateipfad.

        Unterstützt:
        1. Ordner-Struktur: funktionen/SERVICE/tab.htm
        2. Flache Struktur: FUNKTIONSNAME_tabname.htm
        """
        parts = rel_path.parts

        # Modus 1: Ordner-basierte Struktur
        if len(parts) >= 2 and parts[0] == functions_subdir:
            service_name = parts[1].replace("-", " ").replace("_", " ").title()
            tab_name = rel_path.stem
            return service_name, tab_name

        # Modus 2: Flache Struktur mit Namenskonvention
        filename = rel_path.stem  # z.B. "ALIAS_LESEN_statistik"

        # Bekannte Tab-Suffixe aus Config oder Default
        known_tabs = getattr(self, '_known_tab_suffixes', {
            'statistik', 'use_cases', 'aenderungen', 'dqm',
            'fachlich', 'intern', 'parameter', 'uebersicht',
            'eingabe', 'ausgabe', 'allgemein', 'technik',
            'historie', 'beispiele', 'varianten', 'fehler'
        })

        # Suche nach bekanntem Tab-Suffix
        for tab in known_tabs:
            if filename.lower().endswith(f"_{tab}"):
                service_id = filename[:-(len(tab) + 1)]
                service_name = service_id.replace("-", " ").replace("_", " ").title()
                return service_name, tab

        # Fallback: Letzter Unterstrich trennt Service und Tab
        if "_" in filename:
            file_parts = filename.rsplit("_", 1)
            service_name = file_parts[0].replace("-", " ").replace("_", " ").title()
            tab_name = file_parts[1]
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
        """Volltext-Suche über das gesamte Handbuch."""
        if not query.strip():
            return []

        safe_query = query.replace('"', '""')

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

    def list_services(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Listet Services mit Pagination auf."""
        with self._connect() as con:
            rows = con.execute(
                """SELECT service_id, service_name, description
                   FROM handbook_services
                   ORDER BY service_name
                   LIMIT ? OFFSET ?""",
                (limit, offset)
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
            count = con.execute("SELECT COUNT(*) FROM handbook_fts").fetchone()[0]
        return count > 0

    def get_stats(self) -> Dict:
        """Gibt Index-Statistiken zurück, inklusive Build-Status."""
        if not self.db_path.exists():
            return {
                "indexed": False,
                "indexed_pages": 0,
                "services_count": 0,
                "fields_count": 0,
                "last_build": None,
                "handbook_path": None,
                "db_size_kb": 0,
                "build_status": "none",  # none, complete, incomplete, cancelled
                "total_files_expected": 0,
                "files_processed": 0
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

            # Build-Status Metadaten
            build_status_row = con.execute(
                "SELECT value FROM handbook_meta WHERE key='build_status'"
            ).fetchone()
            total_files_row = con.execute(
                "SELECT value FROM handbook_meta WHERE key='total_files_expected'"
            ).fetchone()
            processed_files_row = con.execute(
                "SELECT value FROM handbook_meta WHERE key='files_processed'"
            ).fetchone()

        last_build = None
        if last_build_row:
            ts = int(last_build_row[0])
            last_build = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

        handbook_path = handbook_path_row[0] if handbook_path_row else None
        db_size_kb = round(self.db_path.stat().st_size / 1024, 1)

        # Build-Status auswerten
        build_status = build_status_row[0] if build_status_row else "none"
        total_files_expected = int(total_files_row[0]) if total_files_row else 0
        files_processed = int(processed_files_row[0]) if processed_files_row else 0

        # Automatische Erkennung: Wenn Dateien erwartet aber nicht alle verarbeitet
        if build_status == "none" and total_files_expected > 0 and files_processed < total_files_expected:
            build_status = "incomplete"

        return {
            "indexed": page_count > 0,
            "indexed_pages": page_count,
            "services_count": service_count,
            "fields_count": field_count,
            "last_build": last_build,
            "handbook_path": handbook_path,
            "db_size_kb": db_size_kb,
            "build_status": build_status,
            "total_files_expected": total_files_expected,
            "files_processed": files_processed
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
