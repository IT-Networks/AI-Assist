"""
File Manager - Sichere Datei-Operationen mit Permission-System.

Features:
- Pfad-Whitelist für Sicherheit
- Diff-Generierung für Previews
- Backup vor Änderungen
- Read/Write/Edit mit Bestätigungs-Workflow
"""

import difflib
import shutil
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional
import os


@dataclass
class FileContent:
    """Inhalt einer gelesenen Datei."""
    path: str
    content: str
    size_bytes: int
    modified: datetime
    encoding: str = "utf-8"


@dataclass
class WritePreview:
    """Preview einer Schreib-Operation."""
    path: str
    is_new: bool
    old_content: Optional[str]
    new_content: str
    diff: Optional[str]


@dataclass
class EditPreview:
    """Preview einer Edit-Operation."""
    path: str
    old_string: str
    new_string: str
    diff: str
    new_content: str


class FileManager:
    """
    Verwaltet Datei-Operationen mit Sicherheits-Checks.

    Features:
    - Pfad-Whitelist: Nur erlaubte Pfade können gelesen/geschrieben werden
    - Extension-Filter: Nur erlaubte Dateitypen
    - Denied-Patterns: Blacklist für gefährliche Pfade
    - Backup: Automatisches Backup vor Änderungen
    """

    def __init__(
        self,
        allowed_paths: Optional[List[str]] = None,
        allowed_extensions: Optional[List[str]] = None,
        denied_patterns: Optional[List[str]] = None,
        backup_enabled: bool = True,
        backup_directory: str = "./backups"
    ):
        self.allowed_paths = [
            Path(p).resolve() for p in (allowed_paths or [])
        ]
        self.allowed_extensions = set(allowed_extensions or [])
        self.denied_patterns = denied_patterns or []
        self.backup_enabled = backup_enabled
        self.backup_dir = Path(backup_directory)

        if self.backup_enabled:
            self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _is_subpath(self, path: Path, parent: Path) -> bool:
        """Prüft ob path ein Unterverzeichnis von parent ist."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _validate_path(self, path: str, for_write: bool = False) -> Path:
        """
        Validiert ob der Pfad erlaubt ist.

        Args:
            path: Zu validierender Pfad
            for_write: True wenn Schreibzugriff benötigt wird

        Raises:
            PermissionError: Wenn der Pfad nicht erlaubt ist
            ValueError: Wenn der Pfad ungültig ist
        """
        # Pfad normalisieren
        try:
            resolved = Path(path).resolve()
        except Exception as e:
            raise ValueError(f"Ungültiger Pfad: {path}") from e

        # Prüfen ob in erlaubtem Pfad (wenn Whitelist definiert)
        if self.allowed_paths:
            allowed = any(
                self._is_subpath(resolved, allowed)
                for allowed in self.allowed_paths
            )
            if not allowed:
                raise PermissionError(
                    f"Pfad nicht in erlaubten Verzeichnissen: {path}\n"
                    f"Erlaubt: {[str(p) for p in self.allowed_paths]}"
                )

        # Extension prüfen (für Dateien beim Schreiben)
        if for_write and self.allowed_extensions and resolved.suffix:
            if resolved.suffix.lower() not in self.allowed_extensions:
                raise PermissionError(
                    f"Dateityp nicht erlaubt: {resolved.suffix}\n"
                    f"Erlaubt: {self.allowed_extensions}"
                )

        # Denied patterns prüfen
        path_str = str(resolved).replace("\\", "/")
        for pattern in self.denied_patterns:
            if fnmatch(path_str, pattern):
                raise PermissionError(f"Pfad durch Pattern blockiert: {pattern}")

        return resolved

    # ══════════════════════════════════════════════════════════════════════════
    # Read Operations
    # ══════════════════════════════════════════════════════════════════════════

    async def read_file(
        self,
        path: str,
        encoding: str = "utf-8"
    ) -> FileContent:
        """
        Liest eine Datei.

        Args:
            path: Pfad zur Datei
            encoding: Encoding (default: utf-8)

        Returns:
            FileContent mit Inhalt und Metadaten

        Raises:
            PermissionError: Pfad nicht erlaubt
            FileNotFoundError: Datei existiert nicht
        """
        resolved = self._validate_path(path, for_write=False)

        if not resolved.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {path}")

        if not resolved.is_file():
            raise ValueError(f"Kein reguläre Datei: {path}")

        content = resolved.read_text(encoding=encoding, errors="replace")
        stat = resolved.stat()

        return FileContent(
            path=str(resolved),
            content=content,
            size_bytes=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime),
            encoding=encoding
        )

    async def list_files(
        self,
        path: str,
        pattern: str = "*",
        recursive: bool = False
    ) -> List[str]:
        """
        Listet Dateien in einem Verzeichnis auf.

        Args:
            path: Verzeichnispfad
            pattern: Glob-Pattern (z.B. "*.java")
            recursive: Auch Unterverzeichnisse durchsuchen

        Returns:
            Liste von relativen Dateipfaden
        """
        resolved = self._validate_path(path, for_write=False)

        if not resolved.exists():
            raise FileNotFoundError(f"Verzeichnis nicht gefunden: {path}")

        if not resolved.is_dir():
            raise ValueError(f"Kein Verzeichnis: {path}")

        if recursive:
            files = list(resolved.rglob(pattern))
        else:
            files = list(resolved.glob(pattern))

        # Nur Dateien, keine Verzeichnisse
        files = [f for f in files if f.is_file()]

        # Denied patterns filtern
        result = []
        for f in files:
            f_str = str(f).replace("\\", "/")
            denied = any(fnmatch(f_str, p) for p in self.denied_patterns)
            if not denied:
                result.append(str(f.relative_to(resolved)))

        return sorted(result)

    # ══════════════════════════════════════════════════════════════════════════
    # Write Operations (mit Preview)
    # ══════════════════════════════════════════════════════════════════════════

    async def write_file(
        self,
        path: str,
        content: str
    ) -> WritePreview:
        """
        Bereitet eine Schreib-Operation vor (ohne auszuführen).

        Gibt einen Preview mit Diff zurück.
        Ausführung erst nach Bestätigung via execute_write().

        Args:
            path: Pfad zur Datei
            content: Neuer Dateiinhalt

        Returns:
            WritePreview mit Diff und Metadaten
        """
        resolved = self._validate_path(path, for_write=True)

        is_new = not resolved.exists()
        old_content = None
        diff = None

        if not is_new:
            old_content = resolved.read_text(encoding="utf-8", errors="replace")
            diff = self._generate_diff(old_content, content, str(resolved))

        return WritePreview(
            path=str(resolved),
            is_new=is_new,
            old_content=old_content,
            new_content=content,
            diff=diff
        )

    async def execute_write(
        self,
        path: str,
        content: str,
        create_backup: bool = True
    ) -> bool:
        """
        Führt eine Schreib-Operation aus (nach Bestätigung).

        Args:
            path: Pfad zur Datei
            content: Neuer Dateiinhalt
            create_backup: Backup erstellen wenn Datei existiert

        Returns:
            True wenn erfolgreich
        """
        resolved = self._validate_path(path, for_write=True)

        # Backup erstellen
        if create_backup and self.backup_enabled and resolved.exists():
            self._create_backup(resolved)

        # Verzeichnis erstellen falls nötig
        resolved.parent.mkdir(parents=True, exist_ok=True)

        # Datei schreiben
        resolved.write_text(content, encoding="utf-8")

        return True

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str
    ) -> EditPreview:
        """
        Bereitet eine Edit-Operation vor (ohne auszuführen).

        Ersetzt old_string durch new_string.

        Args:
            path: Pfad zur Datei
            old_string: Zu ersetzender Text
            new_string: Neuer Text

        Returns:
            EditPreview mit Diff

        Raises:
            FileNotFoundError: Datei existiert nicht
            ValueError: old_string nicht gefunden oder nicht eindeutig
        """
        resolved = self._validate_path(path, for_write=True)

        if not resolved.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {path}")

        content = resolved.read_text(encoding="utf-8", errors="replace")

        # Prüfen ob String existiert
        if old_string not in content:
            raise ValueError(f"String nicht gefunden in {path}:\n{old_string[:100]}...")

        # Prüfen ob eindeutig
        count = content.count(old_string)
        if count > 1:
            raise ValueError(
                f"String kommt {count}x vor in {path}. "
                "Bitte mehr Kontext angeben für eindeutige Ersetzung."
            )

        # Neuen Inhalt berechnen
        new_content = content.replace(old_string, new_string, 1)
        diff = self._generate_diff(content, new_content, str(resolved))

        return EditPreview(
            path=str(resolved),
            old_string=old_string,
            new_string=new_string,
            diff=diff,
            new_content=new_content
        )

    async def execute_edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        create_backup: bool = True
    ) -> bool:
        """
        Führt eine Edit-Operation aus (nach Bestätigung).

        Args:
            path: Pfad zur Datei
            old_string: Zu ersetzender Text
            new_string: Neuer Text
            create_backup: Backup erstellen

        Returns:
            True wenn erfolgreich
        """
        resolved = self._validate_path(path, for_write=True)

        if not resolved.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {path}")

        content = resolved.read_text(encoding="utf-8", errors="replace")

        if old_string not in content:
            raise ValueError(f"String nicht gefunden in {path}")

        # Backup erstellen
        if create_backup and self.backup_enabled:
            self._create_backup(resolved)

        # Ersetzen
        new_content = content.replace(old_string, new_string, 1)
        resolved.write_text(new_content, encoding="utf-8")

        return True

    # ══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _generate_diff(
        self,
        old: Optional[str],
        new: str,
        filename: str
    ) -> str:
        """Generiert einen unified diff."""
        if old is None:
            # Neue Datei
            lines = new.splitlines(keepends=True)
            return f"+++ {filename} (new file)\n" + "".join(f"+{line}" for line in lines[:50])

        diff_lines = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm=""
        )
        return "".join(diff_lines)

    def _create_backup(self, path: Path) -> Path:
        """Erstellt ein Backup einer Datei."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{path.stem}_{timestamp}{path.suffix}"
        backup_path = self.backup_dir / backup_name
        shutil.copy(path, backup_path)
        return backup_path


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_file_manager: Optional[FileManager] = None


def get_file_manager() -> FileManager:
    """Gibt die Singleton-Instanz des FileManagers zurück."""
    global _file_manager
    if _file_manager is None:
        from app.core.config import settings
        config = settings.file_operations

        _file_manager = FileManager(
            allowed_paths=config.allowed_paths if config.allowed_paths else None,
            allowed_extensions=config.allowed_extensions,
            denied_patterns=config.denied_patterns,
            backup_enabled=config.backup_enabled,
            backup_directory=config.backup_directory
        )
    return _file_manager
