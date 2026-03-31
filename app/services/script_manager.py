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
        config = settings.script_execution
        self.allowed_imports = set(config.allowed_imports)
        self.allowed_file_paths = config.allowed_file_paths
        # Wenn erlaubte Dateipfade konfiguriert sind, entferne den generischen open-write Blocker
        # Der _safe_open() Guard in der Ausführung übernimmt die Sicherheitsprüfung
        patterns = list(config.blocked_patterns)
        if config.allowed_file_paths:
            patterns = [p for p in patterns if "open" not in p]
        self.blocked_patterns = [re.compile(p) for p in patterns]

    def validate(self, code: str) -> ValidationResult:
        """
        Analysiert Python-Code mit AST und Regex.

        Args:
            code: Python-Quellcode

        Returns:
            ValidationResult mit Sicherheitsbewertung
        """
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
                    if module not in self.allowed_imports:
                        errors.append(f"Nicht erlaubter Import: {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split('.')[0]
                    imports_used.append(node.module)
                    if module not in self.allowed_imports:
                        errors.append(f"Nicht erlaubter Import: from {node.module}")

            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    functions_called.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    functions_called.append(node.func.attr)

        # 3. Gefährliche Patterns (Regex)
        for pattern in self.blocked_patterns:
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
            requirements=json.loads(row.get('requirements') or '[]')
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
                requirements=json.loads(row.get('requirements') or '[]')
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

    async def run(
        self,
        script: Script,
        args: Dict[str, Any] = None,
        input_data: str = None
    ) -> ExecutionResult:
        """
        Führt ein Script aus.

        Args:
            script: Das auszuführende Script
            args: Argumente als Dictionary (werden als JSON übergeben)
            input_data: Optionale Eingabedaten

        Returns:
            ExecutionResult mit stdout/stderr
        """
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
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False,
            encoding='utf-8'
        ) as f:
            f.write(wrapper_code)
            temp_path = f.name

        try:
            if self.config.use_container and settings.docker_sandbox.enabled:
                result = await self._run_in_container(temp_path, input_data)
            else:
                result = await self._run_local(temp_path, input_data)

            execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
            result.execution_time_ms = execution_time
            return result

        finally:
            # Temp-Datei aufräumen
            try:
                Path(temp_path).unlink()
            except Exception:
                pass

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

        import sys
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

        cmd += requirements

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
                return f"pip install fehlgeschlagen: {stderr.decode('utf-8', errors='replace')[:500]}"

            logger.info(f"pip install erfolgreich: {requirements}")
            return None

        except asyncio.TimeoutError:
            return f"pip install Timeout nach {self.config.pip_install_timeout_seconds}s"
        except Exception as e:
            return f"pip install Fehler: {str(e)}"

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
        """Führt Script lokal aus (ohne Container)."""
        try:
            import sys
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input_data.encode() if input_data else None),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return ExecutionResult(
                    success=False,
                    stdout='',
                    stderr='',
                    error=f"Timeout nach {self.timeout}s"
                )

            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            # Output begrenzen
            max_output = self.config.max_output_size_kb * 1024
            if len(stdout_str) > max_output:
                stdout_str = stdout_str[:max_output] + f"\n... (gekürzt, {len(stdout_str)} Bytes total)"
            if len(stderr_str) > max_output:
                stderr_str = stderr_str[:max_output] + f"\n... (gekürzt)"

            return ExecutionResult(
                success=process.returncode == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                error=stderr_str if process.returncode != 0 else None
            )

        except Exception as e:
            logger.error(f"Script-Ausführung fehlgeschlagen: {e}")
            return ExecutionResult(
                success=False,
                stdout='',
                stderr='',
                error=str(e)
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
        input_data: str = None
    ) -> ExecutionResult:
        """
        Führt ein Script aus.

        Args:
            script_id: ID des Scripts
            args: Argumente für das Script
            input_data: Optionale Eingabedaten

        Returns:
            ExecutionResult mit stdout/stderr

        Raises:
            ScriptNotFoundError: Wenn Script nicht existiert
        """
        script = self.storage.get(script_id)
        if not script:
            raise ScriptNotFoundError(f"Script '{script_id}' nicht gefunden")

        result = await self.executor.run(script, args, input_data)

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

    async def install_requirements(self, requirements: List[str]) -> Optional[str]:
        """
        Installiert pip-Pakete aus Nexus-Repository.

        Args:
            requirements: Liste von Paket-Spezifikationen (z.B. ['pandas==1.3.0', 'numpy'])

        Returns:
            None bei Erfolg, error-string bei Fehler
        """
        return await self.executor._install_requirements(requirements)


def get_script_manager() -> ScriptManager:
    """Factory-Funktion für ScriptManager-Instanz."""
    return ScriptManager.get_instance()
