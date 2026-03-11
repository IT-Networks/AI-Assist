"""
Tests für app/utils/validators/change_detector.py

Testet die Git- und Timestamp-basierte Änderungserkennung.
"""

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

from app.utils.validators.change_detector import (
    ChangedFile,
    GitChangeDetector,
    TimestampChangeDetector,
    get_changed_files,
)


class TestChangedFile:
    """Tests für ChangedFile Dataclass."""

    def test_create_modified_file(self):
        """Geänderte Datei erstellen."""
        cf = ChangedFile(
            path="/test/file.py",
            status="modified",
            staged=False,
        )
        assert cf.path == "/test/file.py"
        assert cf.status == "modified"
        assert cf.staged is False

    def test_create_staged_file(self):
        """Gestaged Datei erstellen."""
        cf = ChangedFile(
            path="/test/new.py",
            status="added",
            staged=True,
        )
        assert cf.status == "added"
        assert cf.staged is True

    def test_create_untracked_file(self):
        """Untracked Datei erstellen."""
        cf = ChangedFile(
            path="/test/untracked.py",
            status="untracked",
        )
        assert cf.status == "untracked"


class TestGitChangeDetector:
    """Tests für GitChangeDetector."""

    def test_detector_initialization(self):
        """Detector initialisieren."""
        detector = GitChangeDetector("/tmp/test")
        assert detector.repo_path == Path("/tmp/test")

    @pytest.mark.asyncio
    async def test_non_git_repo(self):
        """Erkennung eines Nicht-Git-Verzeichnisses."""
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = GitChangeDetector(tmpdir)
            is_git = await detector.is_git_repo()
            assert is_git is False

    @pytest.mark.asyncio
    async def test_get_changed_files_non_git(self):
        """Geänderte Dateien in Nicht-Git-Repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = GitChangeDetector(tmpdir)
            changed = await detector.get_changed_files()
            assert changed == []

    def test_parse_status_modified(self):
        """Status 'M' parsen."""
        detector = GitChangeDetector("/tmp")
        assert detector._parse_status("M") == "modified"

    def test_parse_status_added(self):
        """Status 'A' parsen."""
        detector = GitChangeDetector("/tmp")
        assert detector._parse_status("A") == "added"

    def test_parse_status_deleted(self):
        """Status 'D' parsen."""
        detector = GitChangeDetector("/tmp")
        assert detector._parse_status("D") == "deleted"

    def test_parse_status_renamed(self):
        """Status 'R' parsen."""
        detector = GitChangeDetector("/tmp")
        assert detector._parse_status("R") == "renamed"

    def test_parse_status_unknown(self):
        """Unbekannter Status."""
        detector = GitChangeDetector("/tmp")
        assert detector._parse_status("X") == "modified"

    def test_matches_extensions_no_filter(self):
        """Extension-Match ohne Filter."""
        detector = GitChangeDetector("/tmp")
        assert detector._matches_extensions("test.py", None) is True
        assert detector._matches_extensions("test.java", None) is True

    def test_matches_extensions_with_filter(self):
        """Extension-Match mit Filter."""
        detector = GitChangeDetector("/tmp")
        extensions = [".py", ".java"]
        assert detector._matches_extensions("test.py", extensions) is True
        assert detector._matches_extensions("test.java", extensions) is True
        assert detector._matches_extensions("test.txt", extensions) is False

    def test_matches_extensions_case_insensitive(self):
        """Extension-Match case-insensitive."""
        detector = GitChangeDetector("/tmp")
        extensions = [".PY", ".JAVA"]
        assert detector._matches_extensions("test.py", extensions) is True
        assert detector._matches_extensions("test.Java", extensions) is True


class TestTimestampChangeDetector:
    """Tests für TimestampChangeDetector."""

    def test_detector_initialization(self):
        """Detector initialisieren."""
        detector = TimestampChangeDetector("/tmp/test")
        assert detector.repo_path == Path("/tmp/test")

    @pytest.mark.asyncio
    async def test_nonexistent_path(self):
        """Nicht existierender Pfad."""
        detector = TimestampChangeDetector("/nonexistent/path/that/does/not/exist")
        changed = await detector.get_changed_files()
        assert changed == []

    @pytest.mark.asyncio
    async def test_find_recent_files(self):
        """Kürzlich geänderte Dateien finden."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Datei erstellen (gerade eben geändert)
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("print('hello')")

            detector = TimestampChangeDetector(tmpdir)
            changed = await detector.get_changed_files(
                since_minutes=60,
                extensions=[".py"],
            )

            assert len(changed) == 1
            assert changed[0].status == "modified"
            assert "test.py" in changed[0].path

    @pytest.mark.asyncio
    async def test_exclude_dirs(self):
        """Verzeichnisse ausschließen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Normale Datei
            normal = Path(tmpdir) / "test.py"
            normal.write_text("normal")

            # Datei in auszuschließendem Verzeichnis
            excluded_dir = Path(tmpdir) / "__pycache__"
            excluded_dir.mkdir()
            excluded = excluded_dir / "cached.py"
            excluded.write_text("cached")

            detector = TimestampChangeDetector(tmpdir)
            changed = await detector.get_changed_files(
                since_minutes=60,
                exclude_dirs=["__pycache__"],
            )

            paths = [c.path for c in changed]
            assert any("test.py" in p for p in paths)
            assert not any("__pycache__" in p for p in paths)

    @pytest.mark.asyncio
    async def test_extension_filter(self):
        """Nur bestimmte Extensions finden."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Python-Datei
            py_file = Path(tmpdir) / "test.py"
            py_file.write_text("python")

            # Text-Datei
            txt_file = Path(tmpdir) / "readme.txt"
            txt_file.write_text("text")

            detector = TimestampChangeDetector(tmpdir)
            changed = await detector.get_changed_files(
                since_minutes=60,
                extensions=[".py"],
            )

            assert len(changed) == 1
            assert "test.py" in changed[0].path


class TestGetChangedFilesConvenience:
    """Tests für get_changed_files() Convenience-Funktion."""

    @pytest.mark.asyncio
    async def test_fallback_to_timestamp(self):
        """Fallback auf Timestamp-Detector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Datei erstellen
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("test")

            # Sollte auf TimestampChangeDetector fallen
            changed = await get_changed_files(
                tmpdir,
                use_git=True,  # Versucht Git, fällt auf Timestamp zurück
                fallback_minutes=60,
                extensions=[".py"],
            )

            assert len(changed) == 1

    @pytest.mark.asyncio
    async def test_explicit_timestamp_mode(self):
        """Expliziter Timestamp-Modus."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "file.java"
            test_file.write_text("class Test {}")

            changed = await get_changed_files(
                tmpdir,
                use_git=False,  # Direkt Timestamp
                fallback_minutes=60,
                extensions=[".java"],
            )

            assert len(changed) == 1
            assert "file.java" in changed[0].path

    @pytest.mark.asyncio
    async def test_empty_directory(self):
        """Leeres Verzeichnis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            changed = await get_changed_files(tmpdir, use_git=False)
            assert changed == []


class TestEdgeCases:
    """Tests für Grenzfälle."""

    @pytest.mark.asyncio
    async def test_nested_directories(self):
        """Verschachtelte Verzeichnisse."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Tief verschachtelte Datei
            deep_dir = Path(tmpdir) / "a" / "b" / "c"
            deep_dir.mkdir(parents=True)
            deep_file = deep_dir / "deep.py"
            deep_file.write_text("deep")

            detector = TimestampChangeDetector(tmpdir)
            changed = await detector.get_changed_files(
                since_minutes=60,
                extensions=[".py"],
            )

            assert len(changed) == 1
            assert "deep.py" in changed[0].path

    @pytest.mark.asyncio
    async def test_multiple_extensions(self):
        """Mehrere Extensions gleichzeitig."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.py").write_text("py")
            (Path(tmpdir) / "b.java").write_text("java")
            (Path(tmpdir) / "c.sql").write_text("sql")
            (Path(tmpdir) / "d.txt").write_text("txt")

            detector = TimestampChangeDetector(tmpdir)
            changed = await detector.get_changed_files(
                since_minutes=60,
                extensions=[".py", ".java", ".sql"],
            )

            assert len(changed) == 3
            extensions = [Path(c.path).suffix for c in changed]
            assert ".py" in extensions
            assert ".java" in extensions
            assert ".sql" in extensions
            assert ".txt" not in extensions

    @pytest.mark.asyncio
    async def test_hidden_files_included(self):
        """Versteckte Dateien werden gefunden."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hidden = Path(tmpdir) / ".hidden.py"
            hidden.write_text("hidden")

            detector = TimestampChangeDetector(tmpdir)
            changed = await detector.get_changed_files(
                since_minutes=60,
                extensions=[".py"],
            )

            # Hidden files should be included (not in exclude_dirs)
            paths = [c.path for c in changed]
            assert any(".hidden.py" in p for p in paths)
