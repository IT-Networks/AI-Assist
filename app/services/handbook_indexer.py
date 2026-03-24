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

import hashlib
import json
import re
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from fnmatch import fnmatch
from pathlib import Path
from typing import AsyncGenerator, Callable, Dict, Generator, List, Optional, Set, Tuple
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


@dataclass
class IndexCheckpoint:
    """Checkpoint-Zustand für Resume nach Unterbrechung."""
    handbook_path: str
    config_hash: str
    phase: str  # scanning | analyzing | indexing
    batch_index: int = 0
    scanned_files: List[str] = field(default_factory=list)
    services_json: str = "{}"
    fields_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""


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

                -- Checkpoint für unterbrochene Indexierungen (Singleton)
                CREATE TABLE IF NOT EXISTS handbook_checkpoint (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    handbook_path TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    batch_index INTEGER DEFAULT 0,
                    scanned_files_json TEXT,
                    services_json TEXT,
                    fields_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Bereits verarbeitete Dateien (für Resume)
                CREATE TABLE IF NOT EXISTS handbook_processed_files (
                    file_path TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
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
    # Checkpoint Management
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_config_hash(config: Dict) -> str:
        """Berechnet Hash für Build-Konfig-Validierung."""
        relevant = {
            'handbook_path': config.get('handbook_path', ''),
            'functions_subdir': config.get('functions_subdir', 'funktionen'),
            'fields_subdir': config.get('fields_subdir', 'felder'),
            'exclude_patterns': sorted(config.get('exclude_patterns', [])),
            'structure_mode': config.get('structure_mode', 'auto'),
        }
        return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]

    def _save_checkpoint_full(
        self,
        handbook_path: str,
        config: Dict,
        phase: str,
        batch_index: int,
        scanned_files: List[str],
        services: Dict[str, ServiceInfo],
        fields: Dict[str, FieldInfo]
    ) -> None:
        """
        Speichert vollständigen Checkpoint (nur nach Analyse-Phase).

        Serialisiert alle Daten - sollte nur einmal pro Build aufgerufen werden,
        nicht bei jedem Batch.
        """
        config_hash = self._compute_config_hash(config)
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # Services/Fields zu JSON serialisieren (nur hier, nicht bei jedem Batch!)
        services_json = json.dumps({
            sid: asdict(s) for sid, s in services.items()
        }, ensure_ascii=False)
        fields_json = json.dumps({
            fid: asdict(f) for fid, f in fields.items()
        }, ensure_ascii=False)
        scanned_json = json.dumps(scanned_files, ensure_ascii=False)

        with self._connect() as con:
            con.execute("""
                INSERT OR REPLACE INTO handbook_checkpoint
                (id, handbook_path, config_hash, phase, batch_index,
                 scanned_files_json, services_json, fields_json, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (handbook_path, config_hash, phase, batch_index,
                  scanned_json, services_json, fields_json, now, now))
            con.commit()

    def _update_checkpoint_progress(self, con: sqlite3.Connection, batch_index: int) -> None:
        """
        Aktualisiert nur den Batch-Index im Checkpoint (leichtgewichtig).

        Wird bei jedem Batch aufgerufen - keine JSON-Serialisierung!
        """
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.execute("""
            UPDATE handbook_checkpoint
            SET batch_index = ?, updated_at = ?
            WHERE id = 1
        """, (batch_index, now))

    def _mark_files_processed_batch(self, con: sqlite3.Connection, file_paths: List[str]) -> None:
        """
        Markiert Dateien als verarbeitet (ohne eigenen Commit).

        Nutzt bestehende Connection für Transaktions-Konsolidierung.
        """
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.executemany(
            "INSERT OR REPLACE INTO handbook_processed_files (file_path, processed_at) VALUES (?, ?)",
            [(fp, now) for fp in file_paths]
        )

    def _mark_files_processed(self, file_paths: List[str]) -> None:
        """Markiert Dateien als verarbeitet (standalone mit eigenem Commit)."""
        with self._connect() as con:
            self._mark_files_processed_batch(con, file_paths)
            con.commit()

    def _get_processed_files(self) -> Set[str]:
        """Lädt alle bereits verarbeiteten Dateipfade."""
        with self._connect() as con:
            rows = con.execute("SELECT file_path FROM handbook_processed_files").fetchall()
        return {row[0] for row in rows}

    def _load_checkpoint(self) -> Optional[IndexCheckpoint]:
        """Lädt vorhandenen Checkpoint."""
        with self._connect() as con:
            row = con.execute("""
                SELECT handbook_path, config_hash, phase, batch_index,
                       scanned_files_json, services_json, fields_json,
                       created_at, updated_at
                FROM handbook_checkpoint WHERE id=1
            """).fetchone()

        if not row:
            return None

        scanned_files = json.loads(row[4]) if row[4] else []

        return IndexCheckpoint(
            handbook_path=row[0],
            config_hash=row[1],
            phase=row[2],
            batch_index=row[3],
            scanned_files=scanned_files,
            services_json=row[5] or "{}",
            fields_json=row[6] or "{}",
            created_at=row[7],
            updated_at=row[8]
        )

    def _is_checkpoint_compatible(self, handbook_path: str, config: Dict) -> bool:
        """Prüft ob Checkpoint mit aktueller Config kompatibel ist."""
        checkpoint = self._load_checkpoint()
        if not checkpoint:
            return False

        current_hash = self._compute_config_hash(config)
        return (checkpoint.handbook_path == handbook_path and
                checkpoint.config_hash == current_hash)

    def _clear_checkpoint(self) -> None:
        """Löscht Checkpoint nach erfolgreichem Build."""
        with self._connect() as con:
            con.execute("DELETE FROM handbook_checkpoint WHERE id=1")
            con.execute("DELETE FROM handbook_processed_files")
            con.commit()

    def _clear_index_data(self) -> None:
        """Löscht alle Index-Daten (für force=true Neuindexierung)."""
        with self._connect() as con:
            con.executescript("""
                DELETE FROM handbook_fts;
                DELETE FROM handbook_services;
                DELETE FROM handbook_fields;
                DELETE FROM handbook_meta;
            """)

    def has_checkpoint(self) -> bool:
        """Prüft ob ein Checkpoint existiert."""
        checkpoint = self._load_checkpoint()
        return checkpoint is not None

    def get_checkpoint_info(self) -> Optional[Dict]:
        """Gibt Checkpoint-Informationen für Status-Anzeige zurück."""
        checkpoint = self._load_checkpoint()
        if not checkpoint:
            return None

        processed_count = len(self._get_processed_files())
        total_count = len(checkpoint.scanned_files)

        return {
            "phase": checkpoint.phase,
            "batch_index": checkpoint.batch_index,
            "files_scanned": total_count,
            "files_processed": processed_count,
            "created_at": checkpoint.created_at,
            "updated_at": checkpoint.updated_at,
            "handbook_path": checkpoint.handbook_path
        }

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

        # Build-Config für Checkpoint-Validierung
        build_config = {
            'handbook_path': handbook_path,
            'functions_subdir': functions_subdir,
            'fields_subdir': fields_subdir,
            'exclude_patterns': exclude_patterns or [],
            'structure_mode': structure_mode,
        }

        # Bei force=true: Kompletten Index und Checkpoint löschen
        if force:
            self._clear_checkpoint()
            self._clear_index_data()

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

        # 2. Service-Struktur analysieren (nutzt bereits gescannte Dateien)
        services = self._analyze_service_structure_fast(handbook, functions_subdir, html_files)
        progress.services_found = len(services)

        # 3. Feld-Struktur analysieren (nutzt bereits gescannte Dateien)
        field_infos = self._analyze_field_structure_fast(handbook, fields_subdir, html_files)
        progress.fields_found = len(field_infos)

        progress.message = f"{len(services)} Services, {len(field_infos)} Felder gefunden"
        yield progress

        # Checkpoint speichern: Scanning + Analyzing abgeschlossen (EINMALIG vollständig)
        scanned_file_paths = [str(f.relative_to(handbook)) for f in html_files]
        self._save_checkpoint_full(
            handbook_path=handbook_path,
            config=build_config,
            phase="indexing",
            batch_index=0,
            scanned_files=scanned_file_paths,
            services=services,
            fields=field_infos
        )

        # 4. Indexierung mit paralleler Dateiverarbeitung
        progress.phase = "indexing"
        progress.message = "Starte Indexierung..."
        yield progress  # Sofort Phase-Wechsel anzeigen

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

                    # Markiere verarbeitete Dateien im Chunk (in gleicher Transaktion)
                    chunk_rel_paths = [str(f.relative_to(handbook)) for f in chunk if f is not None]
                    self._mark_files_processed_batch(con, chunk_rel_paths)

                    i += chunk_size
                    batch_idx = i // chunk_size

                    # Checkpoint-Progress nur alle 10 Batches aktualisieren (Performance!)
                    # Bei 82k Dateien: ~41 Updates statt ~410
                    if batch_idx % 10 == 0 or i >= len(html_files):
                        self._update_checkpoint_progress(con, batch_idx)

                    # Fortschritt in DB speichern
                    con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', ?)",
                                (str(min(i, len(html_files))),))

                    # EIN Commit pro Batch (statt 4)
                    con.commit()

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

        # Checkpoint löschen nach erfolgreichem Build
        self._clear_checkpoint()

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

    def resume_build(
        self,
        functions_subdir: str = "funktionen",
        fields_subdir: str = "felder",
        exclude_patterns: Optional[List[str]] = None,
        batch_size: int = 500,
        structure_mode: str = "auto",
        known_tab_suffixes: Optional[List[str]] = None,
        parallel_workers: int = 8
    ) -> Generator[IndexProgress, None, Dict]:
        """
        Setzt eine unterbrochene Indexierung fort.

        Nutzt den gespeicherten Checkpoint um:
        - Bereits gescannte Dateien wiederzuverwenden
        - Bereits analysierte Services/Fields zu laden
        - Bei der zuletzt verarbeiteten Datei fortzusetzen

        Yields:
            IndexProgress Objekte mit aktuellem Status

        Returns:
            Dict mit Statistiken oder Fehler
        """
        checkpoint = self._load_checkpoint()
        if not checkpoint:
            progress = IndexProgress(phase="error", message="Kein Checkpoint vorhanden")
            yield progress
            return {"error": "Kein Checkpoint vorhanden"}

        handbook_path = checkpoint.handbook_path
        handbook = Path(handbook_path)

        # Config für Validierung
        build_config = {
            'handbook_path': handbook_path,
            'functions_subdir': functions_subdir,
            'fields_subdir': fields_subdir,
            'exclude_patterns': exclude_patterns or [],
            'structure_mode': structure_mode,
        }

        # Prüfe Kompatibilität
        if not self._is_checkpoint_compatible(handbook_path, build_config):
            progress = IndexProgress(
                phase="error",
                message="Checkpoint nicht kompatibel (Config geändert). Bitte neu indexieren."
            )
            yield progress
            return {"error": "Checkpoint nicht kompatibel"}

        # Prüfe ob Pfad noch existiert
        if not handbook.exists():
            progress = IndexProgress(
                phase="error",
                message=f"Handbuch-Pfad existiert nicht mehr: {handbook_path}"
            )
            yield progress
            return {"error": f"Pfad existiert nicht: {handbook_path}"}

        self._structure_mode = structure_mode
        self._known_tab_suffixes = set(known_tab_suffixes or [
            'statistik', 'use_cases', 'aenderungen', 'dqm',
            'fachlich', 'intern', 'parameter', 'uebersicht',
            'eingabe', 'ausgabe', 'allgemein', 'technik',
            'historie', 'beispiele', 'varianten', 'fehler'
        ])
        self._cancel_flag.clear()
        start = time.time()

        progress = IndexProgress(
            phase="resuming",
            message=f"Setze Indexierung fort von Checkpoint ({checkpoint.phase})..."
        )
        self._current_progress = progress
        yield progress

        # Lade gescannte Dateien aus Checkpoint
        scanned_file_paths = checkpoint.scanned_files
        html_files = [handbook / p for p in scanned_file_paths]
        progress.total_files = len(html_files)

        # Lade Services und Fields aus Checkpoint
        services_data = json.loads(checkpoint.services_json)
        services = {
            sid: ServiceInfo(**data) for sid, data in services_data.items()
        }
        fields_data = json.loads(checkpoint.fields_json)
        field_infos = {
            fid: FieldInfo(**data) for fid, data in fields_data.items()
        }

        progress.services_found = len(services)
        progress.fields_found = len(field_infos)

        # Lade bereits verarbeitete Dateien
        processed_files = self._get_processed_files()
        progress.message = f"Fortsetzung: {len(processed_files)} bereits verarbeitet, {len(html_files) - len(processed_files)} verbleibend"
        yield progress

        # Filtere bereits verarbeitete Dateien heraus
        remaining_files = [f for f in html_files if str(f.relative_to(handbook)) not in processed_files]

        # Update Build-Status
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('build_status', 'in_progress')")
            con.commit()

        progress.phase = "indexing"
        progress.message = f"Indexiere verbleibende {len(remaining_files)} Dateien..."
        yield progress

        indexed = 0
        skipped = 0
        errors = 0

        def parse_file_worker(html_file: Path) -> Optional[Tuple[Path, str, float, Tuple]]:
            try:
                rel_path = str(html_file.relative_to(handbook))
                mtime = html_file.stat().st_mtime
                data = self._parse_html_file(html_file, handbook, functions_subdir)
                return (html_file, rel_path, mtime, data)
            except Exception:
                return None

        def save_batch_to_db(con, batch_results):
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

        chunk_size = parallel_workers * 25

        with self._connect() as con:
            existing_mtimes = {}
            rows = con.execute("SELECT key, value FROM handbook_meta WHERE key LIKE 'mtime:%'").fetchall()
            for row in rows:
                existing_mtimes[row[0][6:]] = float(row[1])

            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                i = 0
                while i < len(remaining_files):
                    if self._cancel_flag.is_set():
                        con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('build_status', 'cancelled')")
                        con.commit()
                        progress.phase = "cancelled"
                        progress.message = "Abgebrochen durch Benutzer"
                        yield progress
                        return {"cancelled": True, "indexed": indexed}

                    chunk = remaining_files[i:i + chunk_size]

                    # Paralleles Parsen
                    results = list(executor.map(parse_file_worker, chunk))
                    save_batch_to_db(con, results)

                    # Markiere als verarbeitet (in gleicher Transaktion)
                    chunk_rel_paths = [str(f.relative_to(handbook)) for f in chunk]
                    self._mark_files_processed_batch(con, chunk_rel_paths)

                    i += chunk_size
                    batch_idx = checkpoint.batch_index + (i // chunk_size)

                    # Checkpoint-Progress nur alle 10 Batches aktualisieren (Performance!)
                    if batch_idx % 10 == 0 or i >= len(remaining_files):
                        self._update_checkpoint_progress(con, batch_idx)

                    # Progress Update
                    total_processed = len(processed_files) + i
                    progress.processed_files = min(total_processed, len(html_files))
                    progress.errors = errors
                    progress.elapsed_seconds = time.time() - start

                    if indexed + errors > 0:
                        avg_time = progress.elapsed_seconds / (indexed + errors)
                        remaining_count = len(remaining_files) - i
                        progress.estimated_remaining_seconds = avg_time * remaining_count

                    progress.message = f"Indexiere... {indexed} neu ({parallel_workers} Threads)"
                    con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', ?)",
                                (str(progress.processed_files),))

                    # EIN Commit pro Batch
                    con.commit()
                    yield progress

        # Services und Felder speichern
        progress.phase = "saving"
        progress.message = "Speichere Services und Felder..."
        yield progress

        for service in services.values():
            self._save_service(service)
        for field_info in field_infos.values():
            self._save_field(field_info)

        # Metadaten aktualisieren
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('last_build', ?)",
                        (str(int(time.time())),))
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('handbook_path', ?)",
                        (handbook_path,))
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('total_files', ?)",
                        (str(len(html_files)),))
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('build_status', 'complete')")
            con.execute("INSERT OR REPLACE INTO handbook_meta(key, value) VALUES ('files_processed', ?)",
                        (str(len(html_files)),))
            con.commit()

        # Checkpoint löschen nach erfolgreichem Build
        self._clear_checkpoint()

        progress.phase = "done"
        progress.elapsed_seconds = time.time() - start
        progress.message = f"Fertig! {indexed} indexiert, {errors} Fehler (Fortsetzung)"
        self._current_progress = None
        yield progress

        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "services": len(services),
            "fields": len(field_infos),
            "total_files": len(html_files),
            "duration_s": round(time.time() - start, 2),
            "resumed": True
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

    def _analyze_service_structure_fast(
        self,
        root: Path,
        functions_subdir: str,
        html_files: List[Path]
    ) -> Dict[str, ServiceInfo]:
        """
        Schnelle Service-Analyse: Nutzt bereits gescannte Dateien statt erneut glob.
        Vermeidet doppelte Netzwerk-Zugriffe.
        """
        services = {}

        known_tabs = getattr(self, '_known_tab_suffixes', {
            'statistik', 'use_cases', 'aenderungen', 'dqm',
            'fachlich', 'intern', 'parameter', 'uebersicht',
            'eingabe', 'ausgabe', 'allgemein', 'technik',
            'historie', 'beispiele', 'varianten', 'fehler'
        })

        structure_mode = getattr(self, '_structure_mode', 'auto')
        funktionen_dir = root / functions_subdir

        # Filtere relevante Dateien aus bereits gescannter Liste
        # (statt erneut glob über Netzwerk)
        if funktionen_dir.exists() and funktionen_dir != root:
            funktionen_prefix = str(funktionen_dir.relative_to(root))
            relevant_files = [
                f for f in html_files
                if str(f.relative_to(root)).startswith(funktionen_prefix)
            ]
        else:
            # Flat mode: alle Dateien im Root
            relevant_files = [f for f in html_files if f.parent == root]

        # Bestimme Modus
        use_flat_mode = structure_mode == "flat"
        if structure_mode == "auto":
            # Prüfe ob Unterordner in den relevanten Dateien
            has_subdirs = any(
                len(f.relative_to(root).parts) > 2
                for f in relevant_files[:100]  # Sample für Performance
            )
            use_flat_mode = not has_subdirs

        if not use_flat_mode:
            # Modus 1: Ordner-basierte Struktur
            service_files: Dict[str, List[Path]] = {}
            for htm_file in relevant_files:
                rel_parts = htm_file.relative_to(root).parts
                if len(rel_parts) >= 2:
                    service_id = rel_parts[1] if rel_parts[0] == functions_subdir else rel_parts[0]
                    if service_id not in service_files:
                        service_files[service_id] = []
                    service_files[service_id].append(htm_file)

            for service_id, files in service_files.items():
                tabs = [{"name": f.stem, "file_path": str(f.relative_to(root))} for f in files]
                services[service_id] = ServiceInfo(
                    service_id=service_id,
                    service_name=service_id.replace("-", " ").replace("_", " ").title(),
                    tabs=tabs
                )
        else:
            # Modus 2: Flache Struktur
            file_groups: Dict[str, List[Dict]] = {}

            for htm_file in relevant_files:
                filename = htm_file.stem
                service_id = None
                tab_name = None

                for tab in known_tabs:
                    if filename.lower().endswith(f"_{tab}"):
                        service_id = filename[:-(len(tab) + 1)]
                        tab_name = tab
                        break

                if not service_id:
                    if "_" in filename:
                        parts = filename.rsplit("_", 1)
                        service_id = parts[0]
                        tab_name = parts[1]
                    else:
                        service_id = filename
                        tab_name = "hauptseite"

                if service_id not in file_groups:
                    file_groups[service_id] = []
                file_groups[service_id].append({
                    "name": tab_name,
                    "file_path": str(htm_file.relative_to(root))
                })

            for service_id, tabs in file_groups.items():
                services[service_id] = ServiceInfo(
                    service_id=service_id,
                    service_name=service_id.replace("-", " ").replace("_", " ").title(),
                    tabs=sorted(tabs, key=lambda t: t["name"])
                )

        return services

    def _analyze_field_structure_fast(
        self,
        root: Path,
        fields_subdir: str,
        html_files: List[Path]
    ) -> Dict[str, FieldInfo]:
        """
        Schnelle Feld-Analyse: Nutzt bereits gescannte Dateien.
        """
        fields = {}

        felder_dir = root / fields_subdir
        if not felder_dir.exists():
            return fields

        # Filtere nur Dateien aus dem Felder-Verzeichnis
        felder_prefix = str(felder_dir.relative_to(root))
        relevant_files = [
            f for f in html_files
            if str(f.relative_to(root)).startswith(felder_prefix)
            and f.parent == felder_dir  # Nur direkte Kinder, keine Unterordner
        ]

        for htm_file in relevant_files:
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

    def search_grouped(
        self,
        query: str,
        top_k: int = 10,
        max_snippets: int = 2
    ) -> List[Dict]:
        """
        Volltext-Suche mit Gruppierung nach Service.

        Args:
            query: Suchbegriff (min. 3 Zeichen empfohlen)
            top_k: Maximale Anzahl Services
            max_snippets: Maximale Snippets pro Service

        Returns:
            Liste von gruppierten Suchergebnissen
        """
        if not query.strip() or len(query.strip()) < 2:
            return []

        safe_query = query.replace('"', '""')

        # Suche mit mehr Ergebnissen für Gruppierung
        sql = """
            SELECT file_path, service_name, tab_name, title,
                   snippet(handbook_fts, 5, '>>>', '<<<', '...', 30) AS snippet,
                   rank
            FROM handbook_fts
            WHERE handbook_fts MATCH ?
            ORDER BY rank
            LIMIT 100
        """

        with self._connect() as con:
            try:
                rows = con.execute(sql, [safe_query]).fetchall()
            except sqlite3.OperationalError:
                # Fallback für ungültige FTS-Queries
                like = f"%{query}%"
                rows = con.execute(
                    """SELECT file_path, service_name, tab_name, title,
                              substr(content, 1, 200) AS snippet, 0 AS rank
                       FROM handbook_fts
                       WHERE content LIKE ? OR title LIKE ? OR headings LIKE ?
                       LIMIT 100""",
                    (like, like, like)
                ).fetchall()

            # Gruppierung nach Service (innerhalb der Connection)
            services: Dict[str, Dict] = {}

            # Bekannte Tab-Suffixe für Normalisierung
            known_tabs = {
                'statistik', 'use_cases', 'use cases', 'aenderungen', 'änderungen', 'dqm',
                'fachlich', 'intern', 'parameter', 'uebersicht', 'übersicht',
                'eingabe', 'ausgabe', 'allgemein', 'technik',
                'historie', 'beispiele', 'varianten', 'fehler'
            }

            def normalize_service_name(name: str) -> str:
                """Normalisiert Service-Namen: entfernt .htm, Tab-Suffixe, etc."""
                if not name:
                    return "Unbekannt"

                # .htm/.html entfernen
                normalized = re.sub(r'\.html?$', '', name, flags=re.IGNORECASE)

                # Tab-Suffixe entfernen (mit Leerzeichen oder Unterstrich)
                for tab in known_tabs:
                    # Am Ende mit Leerzeichen oder Unterstrich
                    patterns = [
                        rf'\s+{re.escape(tab)}$',
                        rf'_{re.escape(tab)}$',
                        rf'-{re.escape(tab)}$',
                    ]
                    for pattern in patterns:
                        normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)

                # Bereinigen und Title-Case
                normalized = normalized.strip()
                if normalized:
                    # Unterstriche/Bindestriche durch Leerzeichen ersetzen und Title-Case
                    normalized = re.sub(r'[-_]+', ' ', normalized)
                    normalized = ' '.join(word.capitalize() for word in normalized.split())

                return normalized or "Unbekannt"

            for row in rows:
                raw_service_name = row["service_name"] or "Unbekannt"
                service_name = normalize_service_name(raw_service_name)

                if service_name not in services:
                    # Service-ID aus handbook_services holen (mit LIKE für Fuzzy-Match)
                    # Services haben Unterstriche zwischen Wörtern, z.B. "DATEN_LESEN"
                    service_id_pattern = service_name.upper().replace(" ", "_")
                    service_row = con.execute(
                        """SELECT service_id, service_name, description
                           FROM handbook_services
                           WHERE service_name = ?
                              OR UPPER(service_name) = ?
                              OR service_id = ?
                              OR UPPER(service_id) = ?
                              OR service_name LIKE ?
                              OR service_id LIKE ?
                           LIMIT 1""",
                        (service_name, service_name.upper(), service_id_pattern, service_id_pattern,
                         f"%{service_name}%", f"%{service_id_pattern}%")
                    ).fetchone()

                    services[service_name] = {
                        "service_id": service_row["service_id"] if service_row else service_name.lower().replace(" ", "_"),
                        "service_name": service_row["service_name"] if service_row else service_name,
                        "description": service_row["description"] if service_row else "",
                        "match_count": 0,
                        "matched_tabs": set(),
                        "top_snippets": [],
                    }

                svc = services[service_name]
                svc["match_count"] += 1

                # Tab aus raw_service_name extrahieren falls nicht vorhanden
                tab_name = row["tab_name"]
                if not tab_name:
                    # Versuche Tab aus dem raw service name zu extrahieren
                    for tab in known_tabs:
                        if tab.lower() in raw_service_name.lower():
                            tab_name = tab.replace("_", " ").title()
                            break

                if tab_name:
                    svc["matched_tabs"].add(tab_name)

                if len(svc["top_snippets"]) < max_snippets:
                    svc["top_snippets"].append({
                        "tab_name": tab_name or "",
                        "text": row["snippet"] or ""
                    })

        # In Liste umwandeln, nach Match-Count sortieren
        result = []
        for svc in services.values():
            svc["matched_tabs"] = list(svc["matched_tabs"])
            result.append(svc)

        result.sort(key=lambda x: x["match_count"], reverse=True)

        return result[:top_k]

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
        from app.core.config import settings

        with self._connect() as con:
            # Fuzzy-Match für service_id (verschiedene Formate möglich)
            service_id_upper = service_id.upper().replace("-", "_").replace(" ", "_")
            service_id_lower = service_id.lower().replace("-", "_").replace(" ", "_")

            row = con.execute(
                """SELECT * FROM handbook_services
                   WHERE service_id = ?
                      OR UPPER(REPLACE(REPLACE(service_id, '-', '_'), ' ', '_')) = ?
                      OR LOWER(REPLACE(REPLACE(service_id, '-', '_'), ' ', '_')) = ?
                      OR service_name LIKE ?
                   LIMIT 1""",
                (service_id, service_id_upper, service_id_lower, f"%{service_id}%")
            ).fetchone()

            if not row:
                return None

            actual_service_id = row["service_id"]
            service_name = row["service_name"]

            # Tab-Dateien aus FTS-Index holen (mit file_path für HTML-Laden)
            # Robustes Matching: service_name, service_id (mit verschiedenen Formaten)
            service_name_upper = service_name.upper().replace(" ", "_").replace("-", "_")
            service_name_lower = service_name.lower().replace(" ", "_").replace("-", "_")
            service_id_normalized = actual_service_id.upper().replace(" ", "_").replace("-", "_")

            fts_rows = con.execute(
                """SELECT file_path, tab_name, title, content
                   FROM handbook_fts
                   WHERE service_name = ?
                      OR service_name LIKE ?
                      OR UPPER(REPLACE(REPLACE(service_name, ' ', '_'), '-', '_')) = ?
                      OR LOWER(REPLACE(REPLACE(service_name, ' ', '_'), '-', '_')) = ?
                      OR UPPER(REPLACE(REPLACE(service_name, ' ', '_'), '-', '_')) = ?
                   ORDER BY tab_name""",
                (service_name, f"%{service_name}%", service_name_upper, service_name_lower, service_id_normalized)
            ).fetchall()

            # Fallback: Suche nach file_path Pattern wenn keine Treffer
            if not fts_rows:
                # Versuche über Pfad zu finden: funktionen/SERVICE_ID/
                path_pattern = f"%{actual_service_id}%"
                fts_rows = con.execute(
                    """SELECT file_path, tab_name, title, content
                       FROM handbook_fts
                       WHERE file_path LIKE ?
                       ORDER BY tab_name""",
                    (path_pattern,)
                ).fetchall()

            # Alle bekannten Service-IDs für Auto-Linking holen
            all_services = con.execute(
                "SELECT service_id FROM handbook_services"
            ).fetchall()
            known_functions = [r["service_id"] for r in all_services]

            # Tabs mit HTML-Content aus Original-Dateien laden
            tabs_with_content = []
            seen_tabs = set()

            # Handbook-Pfad ermitteln: Settings haben Priorität, dann Index-DB
            handbook_path = None
            path_source = None

            # 1. Aus Settings (aktuelle Konfiguration)
            if settings.handbook.enabled and settings.handbook.path:
                handbook_path = Path(settings.handbook.path)
                path_source = "settings"

            # 2. Fallback: Aus Index-DB (wurde beim Indexieren gespeichert)
            if not handbook_path:
                path_row = con.execute(
                    "SELECT value FROM handbook_meta WHERE key = 'handbook_path'"
                ).fetchone()
                if path_row and path_row["value"]:
                    handbook_path = Path(path_row["value"])
                    path_source = "index_db"

            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[get_service_info] handbook_path={handbook_path} (source={path_source})")

            import logging
            logger = logging.getLogger(__name__)

            for fts_row in fts_rows:
                tab_name = fts_row["tab_name"] or fts_row["title"] or "Inhalt"
                if tab_name in seen_tabs:
                    continue
                seen_tabs.add(tab_name)

                # Versuche Original-HTML zu laden
                html_content = None
                rel_file_path = fts_row["file_path"]

                if handbook_path and rel_file_path:
                    # Pfad zusammenbauen (relative Pfade aus DB + handbook root)
                    file_path = handbook_path / rel_file_path
                    logger.debug(f"[get_service_info] Trying to load: {file_path}")

                    if file_path.exists():
                        try:
                            raw_html = file_path.read_text(encoding="utf-8", errors="replace")
                            # Body-Inhalt extrahieren
                            html_content = self._extract_body_html(raw_html)
                            logger.debug(f"[get_service_info] Loaded HTML from: {file_path}")
                        except Exception as e:
                            logger.warning(f"[get_service_info] Failed to read {file_path}: {e}")
                    else:
                        logger.debug(f"[get_service_info] File not found: {file_path}")
                else:
                    logger.debug(f"[get_service_info] No handbook_path ({handbook_path}) or file_path ({rel_file_path})")

                # Fallback auf Plain-Text wenn HTML nicht verfügbar
                if not html_content:
                    html_content = f"<p>{fts_row['content'] or ''}</p>"

                tabs_with_content.append({
                    "name": tab_name,
                    "title": tab_name.replace("_", " ").title(),
                    "content": html_content,
                    "file_path": fts_row["file_path"]
                })

            # Falls keine FTS-Tabs, Metadaten-Tabs verwenden
            tabs_meta = json.loads(row["tabs_json"] or "[]")
            if not tabs_with_content and tabs_meta:
                tabs_with_content = tabs_meta

        return {
            "service_id": actual_service_id,
            "service_name": service_name,
            "description": row["description"] or "",
            "tabs": tabs_with_content,
            "input_fields": json.loads(row["input_fields_json"] or "[]"),
            "output_fields": json.loads(row["output_fields_json"] or "[]"),
            "call_variants": json.loads(row["call_variants_json"] or "[]"),
            "known_functions": known_functions,  # Für Auto-Linking im Frontend
        }

    def _extract_body_html(self, html: str) -> str:
        """Extrahiert den Body-Inhalt aus HTML und bereinigt ihn."""
        import re

        # Body-Inhalt extrahieren
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
        if body_match:
            content = body_match.group(1)
        else:
            content = html

        # Script und Style Tags entfernen
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)

        # Navigation, Header, Footer entfernen
        content = re.sub(r'<nav[^>]*>.*?</nav>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<header[^>]*>.*?</header>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<footer[^>]*>.*?</footer>', '', content, flags=re.DOTALL | re.IGNORECASE)

        # Inline-Styles und Event-Handler entfernen (Sicherheit)
        content = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', content, flags=re.IGNORECASE)

        return content.strip()

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
        """Gibt Index-Statistiken zurück, inklusive Build-Status und Checkpoint-Info."""
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
                "files_processed": 0,
                "has_checkpoint": False,
                "checkpoint_info": None
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

        # Checkpoint-Info hinzufügen
        checkpoint_info = self.get_checkpoint_info()

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
            "files_processed": files_processed,
            "has_checkpoint": checkpoint_info is not None,
            "checkpoint_info": checkpoint_info
        }

    def clear(self) -> None:
        """Löscht den gesamten Index inklusive Checkpoints."""
        self._clear_index_data()
        self._clear_checkpoint()


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
