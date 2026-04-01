"""
Script Manager - Verwaltung von AI-generierten Python-Scripten.

Ermöglicht:
- Generierung und Validierung von Python-Scripten
- Sichere Speicherung mit Metadaten
- Ausführung in isolierter Umgebung
- Script-Historie und Wiederverwendung
"""

import ast
import asyncio
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Script:
    """Repräsentiert ein gespeichertes Python-Script."""
    id: str
    name: str
    description: str
    code: str
    created_at: datetime
    last_executed: Optional[datetime] = None
    execution_count: int = 0
    parameters: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    file_path: Optional[str] = None
    requirements: List[str] = field(default_factory=list)  # pip packages to install


@dataclass
class ValidationResult:
    """Ergebnis der Script-Validierung."""
    is_safe: bool
    errors: List[str]
    warnings: List[str]
    imports_used: List[str]
    functions_called: List[str]


@dataclass
class ExecutionResult:
    """Ergebnis einer Script-Ausführung."""
    success: bool
    stdout: str
    stderr: str
    return_value: Any = None
    execution_time_ms: int = 0
    error: Optional[str] = None


class ScriptSecurityError(Exception):
    """Fehler bei Script-Validierung."""
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Script-Sicherheitsprüfung fehlgeschlagen: {', '.join(errors)}")


class ScriptNotFoundError(Exception):
    """Script nicht gefunden."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# Script Validator
# ══════════════════════════════════════════════════════════════════════════════

class ScriptValidator:
    """Statische Sicherheitsanalyse für Python-Scripte mittels AST."""

    def __init__(self):
        # Don't cache config - load dynamically in validate() to pick up UI changes
        pass

    def _get_current_config(self):
        """Load config dynamically to reflect UI changes (no caching)."""
        config = settings.script_execution
        allowed_imports = set(config.allowed_imports)
        allowed_file_paths = config.allowed_file_paths

        # Wenn erlaubte Dateipfade konfiguriert sind, entferne den generischen open-write Blocker
        # Der _safe_open() Guard in der Ausführung übernimmt die Sicherheitsprüfung
        patterns = list(config.blocked_patterns)
        if config.allowed_file_paths:
            patterns = [p for p in patterns if "open" not in p]

        blocked_patterns = [re.compile(p) for p in patterns]
        return allowed_imports, allowed_file_paths, blocked_patterns

    def validate(self, code: str) -> ValidationResult:
        """
        Analysiert Python-Code mit AST und Regex.

        Args:
            code: Python-Quellcode

        Returns:
            ValidationResult mit Sicherheitsbewertung
        """
        # Load current config dynamically (not cached) - reflects UI changes
        allowed_imports, allowed_file_paths, blocked_patterns = self._get_current_config()

        errors = []
        warnings = []
        imports_used = []
        functions_called = []

        # 1. Syntax-Check
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ValidationResult(
                is_safe=False,
                errors=[f"Syntax-Fehler Zeile {e.lineno}: {e.msg}"],
                warnings=[],
                imports_used=[],
                functions_called=[]
            )

        # 2. AST-Analyse (Single Traversal für Import-Check und Funktionsaufrufe)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split('.')[0]
                    imports_used.append(alias.name)
                    if module not in allowed_imports:
                        errors.append(f"Nicht erlaubter Import: {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split('.')[0]
                    imports_used.append(node.module)
                    if module not in allowed_imports:
                        errors.append(f"Nicht erlaubter Import: from {node.module}")

            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    functions_called.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    functions_called.append(node.func.attr)

        # 3. Gefährliche Patterns (Regex)
        for pattern in blocked_patterns:
            match = pattern.search(code)
            if match:
                errors.append(f"Gefährliches Pattern: {match.group()}")

        # 5. Warnungen für potenziell problematische Patterns
        if 'while True' in code or 'while 1' in code:
            warnings.append("Mögliche Endlosschleife (while True)")
        if 'sleep' in functions_called:
            warnings.append("Script verwendet sleep() - kann Timeout überschreiten")
        if 'input' in functions_called:
            warnings.append("Script verwendet input() - nicht interaktiv ausführbar")
        if len(code) > settings.script_execution.max_script_size_kb * 1024:
            errors.append(f"Script zu groß (max. {settings.script_execution.max_script_size_kb}KB)")

        return ValidationResult(
            is_safe=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            imports_used=list(set(imports_used)),
            functions_called=list(set(functions_called))
        )


# ══════════════════════════════════════════════════════════════════════════════
# Script Storage
# ══════════════════════════════════════════════════════════════════════════════

class ScriptStorage:
    """Speicherung von Scripten im Filesystem mit SQLite-Metadaten."""

    def __init__(self, scripts_dir: str):
        self.scripts_dir = Path(scripts_dir)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.scripts_dir / "scripts.db"
        self._connection: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Lazy connection with reuse."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self) -> None:
        """Schließt die Datenbankverbindung."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def _init_db(self) -> None:
        """Initialisiert die SQLite-Datenbank."""
        con = self._get_connection()
        with con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS scripts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    file_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_executed TEXT,
                    execution_count INTEGER DEFAULT 0,
                    parameters TEXT,
                    tags TEXT,
                    requirements TEXT DEFAULT '[]'
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_scripts_name ON scripts(name)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_scripts_created ON scripts(created_at)")

            # Migration: requirements column für existierende Datenbanken hinzufügen
            try:
                con.execute("ALTER TABLE scripts ADD COLUMN requirements TEXT DEFAULT '[]'")
            except Exception:
                pass  # Spalte existiert bereits

    def save(
        self,
        code: str,
        name: str,
        description: str,
        parameters: Dict[str, str] = None,
        requirements: List[str] = None
    ) -> Script:
        """Speichert ein Script."""
        script_id = str(uuid.uuid4())[:8]
        safe_name = re.sub(r'[^\w\-]', '_', name.lower())[:50]
        file_name = f"{script_id}_{safe_name}.py"
        file_path = self.scripts_dir / file_name

        # Code mit Header speichern
        header = f'''"""
Script: {name}
ID: {script_id}
Beschreibung: {description}
Erstellt: {datetime.now().isoformat()}
"""

'''
        file_path.write_text(header + code, encoding='utf-8')

        # Metadaten in DB
        con = self._get_connection()
        with con:
            con.execute("""
                INSERT INTO scripts (id, name, description, file_name, created_at, parameters, tags, requirements)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                script_id,
                name,
                description,
                file_name,
                datetime.now().isoformat(),
                json.dumps(parameters or {}),
                json.dumps([]),
                json.dumps(requirements or [])
            ))

        logger.info(f"Script gespeichert: {script_id} ({name})")

        return Script(
            id=script_id,
            name=name,
            description=description,
            code=code,
            created_at=datetime.now(),
            parameters=parameters or {},
            file_path=str(file_path),
            requirements=requirements or []
        )

    def get(self, script_id: str) -> Optional[Script]:
        """Lädt ein Script."""
        con = self._get_connection()
        row = con.execute(
            "SELECT * FROM scripts WHERE id = ?",
            (script_id,)
        ).fetchone()

        if not row:
            return None

        file_path = self.scripts_dir / row['file_name']
        if not file_path.exists():
            logger.warning(f"Script-Datei nicht gefunden: {file_path}")
            return None

        code = file_path.read_text(encoding='utf-8')
        # Header entfernen (alles nach dem ersten """\n\n)
        if code.startswith('"""'):
            end_header = code.find('"""\n\n', 3)
            if end_header > 0:
                code = code[end_header + 5:]

        return Script(
            id=row['id'],
            name=row['name'],
            description=row['description'] or '',
            code=code,
            created_at=datetime.fromisoformat(row['created_at']),
            last_executed=datetime.fromisoformat(row['last_executed']) if row['last_executed'] else None,
            execution_count=row['execution_count'],
            parameters=json.loads(row['parameters'] or '{}'),
            tags=json.loads(row['tags'] or '[]'),
            file_path=str(file_path),
            requirements=json.loads(row['requirements'] or '[]')
        )

    def list_all(self, filter_text: str = None, limit: int = 50) -> List[Script]:
        """Listet alle Scripte auf."""
        con = self._get_connection()
        if filter_text:
            rows = con.execute("""
                SELECT * FROM scripts
                WHERE name LIKE ? OR description LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (f'%{filter_text}%', f'%{filter_text}%', limit)).fetchall()
        else:
            rows = con.execute("""
                SELECT * FROM scripts
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

        scripts = []
        for row in rows:
            file_path = self.scripts_dir / row['file_name']
            scripts.append(Script(
                id=row['id'],
                name=row['name'],
                description=row['description'] or '',
                code='',  # Code nicht laden für Liste
                created_at=datetime.fromisoformat(row['created_at']),
                last_executed=datetime.fromisoformat(row['last_executed']) if row['last_executed'] else None,
                execution_count=row['execution_count'],
                parameters=json.loads(row['parameters'] or '{}'),
                tags=json.loads(row['tags'] or '[]'),
                file_path=str(file_path) if file_path.exists() else None,
                requirements=json.loads(row['requirements'] or '[]')
            ))
        return scripts

    def delete(self, script_id: str) -> bool:
        """Löscht ein Script."""
        con = self._get_connection()
        row = con.execute(
            "SELECT file_name FROM scripts WHERE id = ?",
            (script_id,)
        ).fetchone()

        if not row:
            return False

        file_path = self.scripts_dir / row['file_name']
        if file_path.exists():
            file_path.unlink()

        with con:
            con.execute("DELETE FROM scripts WHERE id = ?", (script_id,))

        logger.info(f"Script gelöscht: {script_id}")
        return True

    def update_execution(self, script_id: str):
        """Aktualisiert Ausführungs-Statistiken."""
        con = self._get_connection()
        with con:
            con.execute("""
                UPDATE scripts
                SET last_executed = ?, execution_count = execution_count + 1
                WHERE id = ?
            """, (datetime.now().isoformat(), script_id))

    def cleanup_old(self, days: int) -> int:
        """Löscht Scripte älter als X Tage."""
        if days <= 0:
            return 0

        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0

        con = self._get_connection()
        rows = con.execute("""
            SELECT id, file_name FROM scripts
            WHERE created_at < ? AND execution_count = 0
        """, (cutoff.isoformat(),)).fetchall()

        for row in rows:
            file_path = self.scripts_dir / row['file_name']
            if file_path.exists():
                file_path.unlink()
            deleted += 1

        if deleted:
            with con:
                con.execute("""
                    DELETE FROM scripts
                    WHERE created_at < ? AND execution_count = 0
                """, (cutoff.isoformat(),))

        if deleted:
            logger.info(f"Cleanup: {deleted} alte Scripte gelöscht")
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken zurück."""
        con = self._get_connection()
        row = con.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(execution_count), 0) as total_exec
            FROM scripts
        """).fetchone()
        count = row['count']
        total_executions = row['total_exec']

        # Dateigröße aus DB-Metadaten wäre effizienter, hier Generator für Memory-Effizienz
        total_size = sum(
            f.stat().st_size for f in self.scripts_dir.glob("*.py")
        )

        return {
            "script_count": count,
            "total_executions": total_executions,
            "total_size_kb": round(total_size / 1024, 2),
            "scripts_directory": str(self.scripts_dir)
        }


# ══════════════════════════════════════════════════════════════════════════════
# Script Executor
# ══════════════════════════════════════════════════════════════════════════════

class ScriptExecutor:
    """Führt Python-Scripte sicher aus."""

    def __init__(self):
        self.config = settings.script_execution
        self.timeout = self.config.timeout_seconds
        # Callback-Mechanismus für Progress-Events (Phase 1)
        self.on_pip_start = None  # Callable[[List[str]], None]
        self.on_pip_installing = None  # Callable[[str], None]
        self.on_pip_installed = None  # Callable[[str, bool, Optional[str]], None]
        self.on_pip_complete = None  # Callable[[bool, int], None]
        # Callback-Mechanismus für Output-Streaming (Phase 2)
        self.on_output_chunk = None  # Callable[[str, str], None] - (stream_type, chunk)

        # OPTIMIZATION: Cache async status to avoid iscoroutinefunction() per call
        self._on_pip_start_is_async = False
        self._on_pip_installing_is_async = False
        self._on_pip_installed_is_async = False
        self._on_pip_complete_is_async = False
        self._on_output_chunk_is_async = False

    async def run(
        self,
        script: Script,
        args: Dict[str, Any] = None,
        input_data: str = None,
        on_pip_start=None,
        on_pip_installing=None,
        on_pip_installed=None,
        on_pip_complete=None,
        on_output_chunk=None
    ) -> ExecutionResult:
        """
        Führt ein Script aus.

        Args:
            script: Das auszuführende Script
            args: Argumente als Dictionary (werden als JSON übergeben)
            input_data: Optionale Eingabedaten
            on_pip_start: Callback(packages: List[str]) für pip start
            on_pip_installing: Callback(package: str) für Package-Installation
            on_pip_installed: Callback(package: str, success: bool, error: Optional[str])
            on_pip_complete: Callback(success: bool, total_ms: int) für pip complete
            on_output_chunk: Callback(stream_type: str, chunk: str) für stdout/stderr streaming

        Returns:
            ExecutionResult mit stdout/stderr
        """
        # PHASE 1: Register Callbacks
        self.on_pip_start = on_pip_start
        self.on_pip_installing = on_pip_installing
        self.on_pip_installed = on_pip_installed
        self.on_pip_complete = on_pip_complete
        # PHASE 2: Register Output Callback
        self.on_output_chunk = on_output_chunk

        # OPTIMIZATION: Pre-compute async status for callbacks (avoid per-call introspection)
        self._on_pip_start_is_async = asyncio.iscoroutinefunction(on_pip_start) if on_pip_start else False
        self._on_pip_installing_is_async = asyncio.iscoroutinefunction(on_pip_installing) if on_pip_installing else False
        self._on_pip_installed_is_async = asyncio.iscoroutinefunction(on_pip_installed) if on_pip_installed else False
        self._on_pip_complete_is_async = asyncio.iscoroutinefunction(on_pip_complete) if on_pip_complete else False
        self._on_output_chunk_is_async = asyncio.iscoroutinefunction(on_output_chunk) if on_output_chunk else False

        start_time = datetime.now()

        # 1. Requirements installieren (WICHTIG: muss vor Wrapper creation sein)
        if script.requirements:
            install_error = await self._install_requirements(script.requirements)
            if install_error:
                return ExecutionResult(
                    success=False,
                    stdout='',
                    stderr='',
                    error=f"Dependency-Installation fehlgeschlagen: {install_error}"
                )

        # 2. Wrapper-Code erstellen mit injizierten args und allowed file paths
        wrapper_code = self._create_wrapper(
            script.code,
            args or {},
            allowed_paths=self.config.allowed_file_paths
        )

        # 3. Temporäre Datei erstellen
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                encoding='utf-8'
            ) as f:
                f.write(wrapper_code)
                temp_path = f.name
        except IOError as e:
            logger.error(f"Temp-Datei-Erstellung fehlgeschlagen: {e}")
            return ExecutionResult(
                success=False,
                stdout='',
                stderr='',
                error=f"Temp-Datei-Fehler: {str(e)[:200]}"
            )

        try:
            if self.config.use_container and settings.docker_sandbox.enabled:
                result = await self._run_in_container(temp_path, input_data)
            else:
                result = await self._run_local(temp_path, input_data)

            execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
            result.execution_time_ms = execution_time
            return result

        finally:
            # Temp-Datei aufräumen mit Retry (für Windows Locks)
            if temp_path:
                self._cleanup_temp_file(temp_path)

    async def _install_requirements(self, requirements: List[str]) -> Optional[str]:
        """
        Installiert pip-Pakete aus konfiguriertem Nexus-Index.

        Args:
            requirements: Liste von Package-Spezifikationen (z.B. ['openpyxl==3.1.2'])

        Returns:
            None bei Erfolg, Error-String bei Fehler
        """
        if not requirements:
            return None

        if not self.config.pip_install_enabled:
            return "pip_install_enabled=False in ScriptExecutionConfig"

        if not self.config.pip_index_url:
            return "pip_index_url nicht konfiguriert in ScriptExecutionConfig"

        # PHASE 1: Send pip_start event
        if self.on_pip_start:
            if self._on_pip_start_is_async:
                await self.on_pip_start(requirements)
            else:
                self.on_pip_start(requirements)

        import sys

        # PHASE 1: Install pro Paket statt alle auf einmal (für Progress-Events)
        install_start = datetime.now()
        failed_packages = []

        for pkg in requirements:
            # PHASE 1: Send pip_installing event
            if self.on_pip_installing:
                if self._on_pip_installing_is_async:
                    await self.on_pip_installing(pkg)
                else:
                    self.on_pip_installing(pkg)

            cmd = [
                sys.executable, '-m', 'pip', 'install',
                '--index-url', self.config.pip_index_url,
                '--no-deps',  # Verhindert transitive Deps von public PyPI
                '--quiet',
            ]

            if self.config.pip_trusted_host:
                cmd += ['--trusted-host', self.config.pip_trusted_host]

            if self.config.pip_cache_requirements:
                cmd += ['--cache-dir', self.config.pip_cache_dir]
            else:
                cmd += ['--no-cache-dir']

            cmd += [pkg]

            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                _, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.pip_install_timeout_seconds
                )

                if process.returncode != 0:
                    stderr_text = stderr.decode('utf-8', errors='replace')[:200]
                    # PHASE 1: Send pip_installed error event
                    if self.on_pip_installed:
                        if self._on_pip_installed_is_async:
                            await self.on_pip_installed(pkg, False, stderr_text)
                        else:
                            self.on_pip_installed(pkg, False, stderr_text)
                    failed_packages.append((pkg, stderr_text))
                else:
                    logger.info(f"pip install erfolgreich: {pkg}")
                    # PHASE 1: Send pip_installed success event
                    if self.on_pip_installed:
                        if self._on_pip_installed_is_async:
                            await self.on_pip_installed(pkg, True, None)
                        else:
                            self.on_pip_installed(pkg, True, None)

            except asyncio.TimeoutError:
                if process:
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        try:
                            process.kill()
                            await asyncio.wait_for(process.wait(), timeout=1)
                        except Exception:
                            pass
                logger.warning(f"pip install timeout nach {self.config.pip_install_timeout_seconds}s für {pkg}")
                error_msg = f"Timeout nach {self.config.pip_install_timeout_seconds}s"
                # PHASE 1: Send pip_installed error event
                if self.on_pip_installed:
                    if self._on_pip_installed_is_async:
                        await self.on_pip_installed(pkg, False, error_msg)
                    else:
                        self.on_pip_installed(pkg, False, error_msg)
                failed_packages.append((pkg, error_msg))
            except Exception as e:
                logger.error(f"pip install Fehler für {pkg}: {type(e).__name__}: {e}")
                error_msg = f"{type(e).__name__}: {str(e)[:100]}"
                # PHASE 1: Send pip_installed error event
                if self.on_pip_installed:
                    if self._on_pip_installed_is_async:
                        await self.on_pip_installed(pkg, False, error_msg)
                    else:
                        self.on_pip_installed(pkg, False, error_msg)
                failed_packages.append((pkg, error_msg))

        # PHASE 1: Send pip_complete event
        install_time = int((datetime.now() - install_start).total_seconds() * 1000)
        success = len(failed_packages) == 0
        if self.on_pip_complete:
            if self._on_pip_complete_is_async:
                await self.on_pip_complete(success, install_time)
            else:
                self.on_pip_complete(success, install_time)

        # Return error if any packages failed
        if failed_packages:
            error_summary = "\n".join(f"  • {pkg}: {err}" for pkg, err in failed_packages)
            return f"pip install für einige Pakete fehlgeschlagen:\n{error_summary}"

        return None

    def _cleanup_temp_file(self, temp_path: str) -> None:
        """
        Löscht temporäre Datei mit Retry-Logik für Windows File Locks.

        Versucht die Datei 3x zu löschen (mit kurzer Verzögerung für Windows Locks).
        """
        import time
        path = Path(temp_path)

        for attempt in range(3):
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Temp-Datei gelöscht: {temp_path}")
                return
            except PermissionError:
                # Windows: Datei wird noch vom Prozess gesperrt
                if attempt < 2:
                    time.sleep(0.1)
                    continue
                logger.warning(f"Temp-Datei konnte nicht gelöscht werden (PermissionError): {temp_path}")
            except Exception as e:
                logger.warning(f"Temp-Datei-Cleanup fehlgeschlagen ({type(e).__name__}): {temp_path}")
                return

    def _create_wrapper(self, code: str, args: Dict[str, Any], allowed_paths: List[str] = None) -> str:
        """Erstellt Wrapper-Code mit injizierten Argumenten und Sicherheits-Guard."""
        args_json = json.dumps(args, ensure_ascii=False)
        paths_json = json.dumps(allowed_paths or [], ensure_ascii=False)

        # Wenn Dateipfade konfiguriert sind, injiziere _safe_open() Guard
        if allowed_paths:
            wrapper = f'''# === AUTO-GENERATED WRAPPER ===
import json
import builtins as _b
import pathlib as _pathlib

# Injizierte Argumente
SCRIPT_ARGS = json.loads({repr(args_json)})
ALLOWED_FILE_PATHS = json.loads({repr(paths_json)})

# Sicheres open() - prüft Pfade gegen Whitelist bei Schreib-Modi
_orig_open = _b.open
def _safe_open(file, mode="r", *args, **kwargs):
    write_modes = set("waxWAX")
    if any(c in str(mode) for c in write_modes):
        abs_file = str(_pathlib.Path(file).resolve())
        if not any(abs_file.startswith(str(_pathlib.Path(p).resolve())) for p in ALLOWED_FILE_PATHS):
            raise PermissionError(
                f"File write blocked: {{abs_file!r}} not in allowed_file_paths. Allowed: {{ALLOWED_FILE_PATHS}}"
            )
    return _orig_open(file, mode, *args, **kwargs)
_b.open = _safe_open

# === USER SCRIPT START ===
{code}
# === USER SCRIPT END ===
'''
        else:
            # Keine Dateipfade konfiguriert - einfacher Wrapper ohne Guard
            wrapper = f'''# === AUTO-GENERATED WRAPPER ===
import json
import sys

# Injizierte Argumente
SCRIPT_ARGS = json.loads({repr(args_json)})

# === USER SCRIPT START ===
{code}
# === USER SCRIPT END ===
'''
        return wrapper

    async def _run_local(self, script_path: str, input_data: str = None) -> ExecutionResult:
        """
        Führt Script lokal aus (ohne Container).

        Streams stdout/stderr in real-time via callbacks instead of buffering.
        Handles timeouts gracefully with proper process cleanup.
        """
        import sys
        process = None
        # OPTIMIZATION: Use lists for accumulation instead of string += (O(n) instead of O(n²))
        stdout_lines = []
        stderr_lines = []

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # PHASE 2: Stream output instead of buffering with communicate()
            if input_data:
                process.stdin.write(input_data.encode())
                await process.stdin.drain()
                process.stdin.close()

            start_time = datetime.now()
            try:
                # Stream stdout and stderr concurrently
                async def stream_output(stream, stream_type):
                    try:
                        while True:
                            line = await asyncio.wait_for(
                                stream.readline(),
                                timeout=0.5
                            )
                            if not line:
                                break
                            # OPTIMIZATION: Decode once, reuse for both storage and callback
                            decoded = line.decode('utf-8', errors='replace')
                            if stream_type == 'stdout':
                                stdout_lines.append(decoded)
                            else:
                                stderr_lines.append(decoded)

                            # PHASE 2: Emit output callback (with newline stripped for display)
                            if self.on_output_chunk:
                                display_text = decoded.rstrip('\r\n')
                                if self._on_output_chunk_is_async:
                                    await self.on_output_chunk(stream_type, display_text)
                                else:
                                    self.on_output_chunk(stream_type, display_text)
                    except asyncio.TimeoutError:
                        # Timeout on reading individual line, continue
                        pass
                    except Exception as e:
                        logger.warning(f"Error streaming {stream_type}: {e}")

                # Stream both stdout and stderr concurrently
                await asyncio.wait_for(
                    asyncio.gather(
                        stream_output(process.stdout, 'stdout'),
                        stream_output(process.stderr, 'stderr'),
                        process.wait()
                    ),
                    timeout=self.timeout
                )

            except asyncio.TimeoutError:
                # Graceful shutdown: terminate first, then kill if needed
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=1)
                    except Exception:
                        pass
                logger.warning(f"Script timeout after {self.timeout}s: {script_path}")
                # OPTIMIZATION: Join lists to strings (O(n) instead of repeated +=)
                stdout_str = ''.join(stdout_lines)
                stderr_str = ''.join(stderr_lines)
                return ExecutionResult(
                    success=False,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    error=f"Script Timeout nach {self.timeout}s (maximale Ausführungszeit überschritten)"
                )

            # OPTIMIZATION: Join lists to strings once at end
            stdout_str = ''.join(stdout_lines)
            stderr_str = ''.join(stderr_lines)

            # Output begrenzen mit Warnung
            max_output = self.config.max_output_size_kb * 1024
            truncated = False
            if len(stdout_str) > max_output:
                stdout_str = stdout_str[:max_output] + f"\n\n⚠️ OUTPUT GEKÜRZT: {len(stdout_str) - max_output} Bytes nicht angezeigt"
                truncated = True
            if len(stderr_str) > max_output:
                stderr_str = stderr_str[:max_output] + f"\n\n⚠️ STDERR GEKÜRZT: weitere Fehlerausgabe nicht angezeigt"
                truncated = True

            if truncated and process.returncode != 0:
                # If script failed AND output was truncated, user needs to know error was cut off
                error_msg = f"Script fehlgeschlagen. Fehlerausgabe gekürzt (max. {max_output} Bytes)"
            else:
                error_msg = stderr_str if process.returncode != 0 else None

            return ExecutionResult(
                success=process.returncode == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                error=error_msg
            )

        except FileNotFoundError as e:
            logger.error(f"Script-Datei nicht gefunden: {script_path}")
            return ExecutionResult(
                success=False,
                stdout='',
                stderr='',
                error=f"Script-Datei nicht gefunden: {script_path}"
            )
        except PermissionError as e:
            logger.error(f"Keine Berechtigung zum Ausführen von Script: {script_path}")
            return ExecutionResult(
                success=False,
                stdout='',
                stderr='',
                error=f"Keine Berechtigung zum Ausführen des Scripts (PermissionError)"
            )
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Script-Ausführung fehlgeschlagen ({error_type}): {e}")
            return ExecutionResult(
                success=False,
                stdout='',
                stderr='',
                error=f"Script-Ausführungsfehler ({error_type}): {str(e)[:200]}"
            )

    async def _run_in_container(self, script_path: str, input_data: str = None) -> ExecutionResult:
        """Führt Script in Docker/Podman-Container aus."""
        # Nutzt die bestehende Docker-Sandbox-Infrastruktur
        # Hier vereinfachte lokale Ausführung als Fallback
        logger.info("Container-Ausführung: Fallback auf lokale Ausführung")
        return await self._run_local(script_path, input_data)


# ══════════════════════════════════════════════════════════════════════════════
# Script Manager (Facade)
# ══════════════════════════════════════════════════════════════════════════════

class ScriptManager:
    """
    Zentrale Verwaltung für Python-Scripte.

    Koordiniert Validierung, Speicherung und Ausführung.
    """

    _instance: Optional["ScriptManager"] = None

    def __init__(self):
        config = settings.script_execution
        self.config = config
        self.validator = ScriptValidator()
        self.storage = ScriptStorage(config.scripts_directory)
        self.executor = ScriptExecutor()

    @classmethod
    def get_instance(cls) -> "ScriptManager":
        """Singleton-Zugriff."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def invalidate_cache(cls):
        """Invalidates the singleton cache when settings are updated via UI."""
        cls._instance = None

    async def generate_and_save(
        self,
        code: str,
        name: str,
        description: str,
        parameters: Dict[str, str] = None,
        requirements: List[str] = None
    ) -> Tuple[Script, ValidationResult]:
        """
        Validiert und speichert ein Script.

        Args:
            code: Python-Quellcode
            name: Kurzer Name für das Script
            description: Beschreibung was das Script macht
            parameters: Parameter-Definitionen {name: description}
            requirements: pip-Packages zum Installieren vor Ausführung

        Returns:
            Tuple aus (Script, ValidationResult)

        Raises:
            ScriptSecurityError: Wenn Validierung fehlschlägt
        """
        # 1. Validierung
        validation = self.validator.validate(code)
        if not validation.is_safe:
            raise ScriptSecurityError(validation.errors)

        # 2. Speichern
        script = self.storage.save(code, name, description, parameters, requirements)

        logger.info(f"Script generiert und gespeichert: {script.id} ({name}), requirements={requirements}")
        return script, validation

    async def execute(
        self,
        script_id: str,
        args: Dict[str, Any] = None,
        input_data: str = None,
        on_pip_start=None,
        on_pip_installing=None,
        on_pip_installed=None,
        on_pip_complete=None,
        on_output_chunk=None
    ) -> ExecutionResult:
        """
        Führt ein Script aus.

        Args:
            script_id: ID des Scripts
            args: Argumente für das Script
            input_data: Optionale Eingabedaten
            on_pip_start: Callback(packages: List[str]) für pip start
            on_pip_installing: Callback(package: str) für Package-Installation
            on_pip_installed: Callback(package: str, success: bool, error: Optional[str])
            on_pip_complete: Callback(success: bool, total_ms: int) für pip complete
            on_output_chunk: Callback(stream_type: str, chunk: str) für stdout/stderr streaming

        Returns:
            ExecutionResult mit stdout/stderr

        Raises:
            ScriptNotFoundError: Wenn Script nicht existiert
        """
        script = self.storage.get(script_id)
        if not script:
            raise ScriptNotFoundError(f"Script '{script_id}' nicht gefunden")

        # PHASE 1: Pass callbacks to executor
        # PHASE 2: Add output streaming callback
        result = await self.executor.run(
            script, args, input_data,
            on_pip_start=on_pip_start,
            on_pip_installing=on_pip_installing,
            on_pip_installed=on_pip_installed,
            on_pip_complete=on_pip_complete,
            on_output_chunk=on_output_chunk
        )

        # Statistik aktualisieren
        self.storage.update_execution(script_id)

        return result

    def list_scripts(self, filter_text: str = None) -> List[Script]:
        """Listet alle verfügbaren Scripte."""
        return self.storage.list_all(filter_text)

    def get_script(self, script_id: str) -> Optional[Script]:
        """Holt ein spezifisches Script."""
        return self.storage.get(script_id)

    def delete_script(self, script_id: str) -> bool:
        """Löscht ein Script."""
        return self.storage.delete(script_id)

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken zurück."""
        stats = self.storage.get_stats()
        stats["enabled"] = self.config.enabled
        stats["require_confirmation"] = self.config.require_confirmation
        return stats

    def validate_code(self, code: str) -> ValidationResult:
        """Validiert Code ohne zu speichern."""
        return self.validator.validate(code)

    def cleanup(self) -> int:
        """Führt Cleanup alter Scripte durch."""
        return self.storage.cleanup_old(self.config.cleanup_days)

    async def install_requirements(
        self,
        requirements: List[str],
        on_pip_start=None,
        on_pip_installing=None,
        on_pip_installed=None,
        on_pip_complete=None
    ) -> Optional[str]:
        """
        Installiert pip-Pakete aus Nexus-Repository.

        Args:
            requirements: Liste von Paket-Spezifikationen (z.B. ['pandas==1.3.0', 'numpy'])
            on_pip_start: Callback(requirements: List[str]) - wird am Anfang aufgerufen
            on_pip_installing: Callback(pkg: str) - wird vor Installation jedes Pakets aufgerufen
            on_pip_installed: Callback(pkg: str, success: bool, error: Optional[str]) - nach Installation
            on_pip_complete: Callback(success: bool, total_ms: int) - am Ende

        Returns:
            None bei Erfolg, error-string bei Fehler
        """
        # Setze Callbacks auf Executor
        self.executor.on_pip_start = on_pip_start
        self.executor.on_pip_installing = on_pip_installing
        self.executor.on_pip_installed = on_pip_installed
        self.executor.on_pip_complete = on_pip_complete

        try:
            return await self.executor._install_requirements(requirements)
        finally:
            # Cleanup Callbacks
            self.executor.on_pip_start = None
            self.executor.on_pip_installing = None
            self.executor.on_pip_installed = None
            self.executor.on_pip_complete = None


def get_script_manager() -> ScriptManager:
    """Factory-Funktion für ScriptManager-Instanz."""
    return ScriptManager.get_instance()
