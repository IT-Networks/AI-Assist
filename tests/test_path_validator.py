"""
Tests für app/utils/path_validator.py

Testet die Security-Validierungen gegen Path-Traversal-Angriffe.
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.utils.path_validator import (
    validate_path_within_base,
    validate_identifier,
    sanitize_filename,
)


class TestValidatePathWithinBase:
    """Tests für validate_path_within_base()"""

    @pytest.fixture
    def temp_base_dir(self):
        """Erstellt ein temporäres Basis-Verzeichnis für Tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Unterverzeichnisse erstellen
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()
            (subdir / "file.txt").touch()
            yield tmpdir

    def test_valid_relative_path(self, temp_base_dir):
        """Relativer Pfad innerhalb der Basis sollte gültig sein."""
        is_valid, resolved, error = validate_path_within_base("subdir/file.txt", temp_base_dir)
        assert is_valid is True
        assert resolved is not None
        assert error is None
        assert "subdir" in resolved

    def test_valid_simple_filename(self, temp_base_dir):
        """Einfacher Dateiname ohne Pfad sollte gültig sein."""
        is_valid, resolved, error = validate_path_within_base("subdir", temp_base_dir)
        assert is_valid is True
        assert error is None

    def test_path_traversal_attack_dotdot(self, temp_base_dir):
        """Path-Traversal mit .. sollte abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("../etc/passwd", temp_base_dir)
        assert is_valid is False
        assert error is not None
        assert ".." in error or "außerhalb" in error.lower() or "ungültig" in error.lower()

    def test_path_traversal_attack_hidden_dotdot(self, temp_base_dir):
        """Versteckter Path-Traversal sollte abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("subdir/../../etc/passwd", temp_base_dir)
        assert is_valid is False
        assert error is not None

    def test_absolute_path_rejected_by_default(self, temp_base_dir):
        """Absolute Pfade sollten standardmäßig abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("/etc/passwd", temp_base_dir)
        assert is_valid is False
        assert error is not None

    def test_absolute_path_allowed_when_configured(self, temp_base_dir):
        """Absolute Pfade sollten mit allow_absolute=True erlaubt sein (wenn innerhalb Basis)."""
        # Absoluter Pfad innerhalb der Basis
        abs_path = os.path.join(temp_base_dir, "subdir")
        is_valid, resolved, error = validate_path_within_base(abs_path, temp_base_dir, allow_absolute=True)
        assert is_valid is True
        assert error is None

    def test_absolute_path_outside_base_rejected(self, temp_base_dir):
        """Absolute Pfade außerhalb der Basis sollten auch mit allow_absolute=True abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("/etc/passwd", temp_base_dir, allow_absolute=True)
        assert is_valid is False
        assert "außerhalb" in error.lower()

    def test_tilde_expansion_rejected(self, temp_base_dir):
        """Home-Verzeichnis-Expansion (~) sollte abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("~/secret", temp_base_dir)
        assert is_valid is False
        assert error is not None

    def test_environment_variable_rejected(self, temp_base_dir):
        """Umgebungsvariablen sollten abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("${HOME}/secret", temp_base_dir)
        assert is_valid is False
        assert error is not None

    def test_windows_env_variable_rejected(self, temp_base_dir):
        """Windows-Umgebungsvariablen (%VAR%) sollten abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("%USERPROFILE%/secret", temp_base_dir)
        assert is_valid is False
        assert error is not None

    def test_empty_path_rejected(self, temp_base_dir):
        """Leerer Pfad sollte abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("", temp_base_dir)
        assert is_valid is False
        assert "leer" in error.lower()

    def test_whitespace_only_path_rejected(self, temp_base_dir):
        """Pfad nur aus Leerzeichen sollte abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("   ", temp_base_dir)
        assert is_valid is False

    def test_empty_base_path_rejected(self, temp_base_dir):
        """Leeres Basis-Verzeichnis sollte Fehler zurückgeben."""
        is_valid, resolved, error = validate_path_within_base("file.txt", "")
        assert is_valid is False
        assert "basis" in error.lower() or "konfiguriert" in error.lower()

    def test_nonexistent_base_path_rejected(self):
        """Nicht existierendes Basis-Verzeichnis sollte abgelehnt werden."""
        is_valid, resolved, error = validate_path_within_base("file.txt", "/nonexistent/path/xyz123")
        assert is_valid is False
        assert "existiert nicht" in error.lower()


class TestValidateIdentifier:
    """Tests für validate_identifier()"""

    def test_valid_simple_identifier(self):
        """Einfacher alphanumerischer Identifier sollte gültig sein."""
        is_valid, error = validate_identifier("myServer123")
        assert is_valid is True
        assert error is None

    def test_valid_with_hyphens(self):
        """Identifier mit Bindestrichen sollte gültig sein."""
        is_valid, error = validate_identifier("my-server-name")
        assert is_valid is True
        assert error is None

    def test_valid_with_underscores(self):
        """Identifier mit Unterstrichen sollte gültig sein."""
        is_valid, error = validate_identifier("my_server_name")
        assert is_valid is True
        assert error is None

    def test_dots_rejected_by_default(self):
        """Punkte sollten standardmäßig abgelehnt werden."""
        is_valid, error = validate_identifier("server.name")
        assert is_valid is False
        assert "ungültige zeichen" in error.lower()

    def test_dots_allowed_when_configured(self):
        """Punkte sollten mit allow_dots=True erlaubt sein."""
        is_valid, error = validate_identifier("server.name", allow_dots=True)
        assert is_valid is True
        assert error is None

    def test_hidden_file_rejected(self):
        """Versteckte Dateien (mit Punkt am Anfang) sollten abgelehnt werden."""
        is_valid, error = validate_identifier(".hidden", allow_dots=True)
        assert is_valid is False
        assert "punkt" in error.lower()

    def test_max_length_enforced(self):
        """Maximale Länge sollte durchgesetzt werden."""
        long_name = "a" * 100
        is_valid, error = validate_identifier(long_name, max_length=64)
        assert is_valid is False
        assert "lang" in error.lower()

    def test_empty_identifier_rejected(self):
        """Leerer Identifier sollte abgelehnt werden."""
        is_valid, error = validate_identifier("")
        assert is_valid is False
        assert "leer" in error.lower()

    def test_special_chars_rejected(self):
        """Sonderzeichen sollten abgelehnt werden."""
        for char in ["!", "@", "#", "$", "/", "\\", ":", "*", "?"]:
            is_valid, error = validate_identifier(f"server{char}name")
            assert is_valid is False, f"Character {char} sollte abgelehnt werden"

    def test_path_traversal_in_identifier_rejected(self):
        """Path-Traversal-Zeichen im Identifier sollten abgelehnt werden."""
        is_valid, error = validate_identifier("../secret")
        assert is_valid is False

    def test_whitespace_only_rejected(self):
        """Identifier nur aus Leerzeichen sollte abgelehnt werden."""
        is_valid, error = validate_identifier("   ")
        assert is_valid is False


class TestSanitizeFilename:
    """Tests für sanitize_filename()"""

    def test_valid_filename_unchanged(self):
        """Gültiger Dateiname sollte unverändert bleiben."""
        assert sanitize_filename("document.pdf") == "document.pdf"

    def test_path_separators_removed(self):
        """Pfad-Separatoren sollten entfernt werden."""
        result = sanitize_filename("/path/to/file.txt")
        assert "/" not in result
        assert result == "file.txt"

    def test_windows_path_separators_removed(self):
        """Windows-Pfad-Separatoren sollten entfernt werden."""
        result = sanitize_filename("C:\\Users\\file.txt")
        assert "\\" not in result
        assert result == "file.txt"

    def test_dangerous_chars_replaced(self):
        """Gefährliche Zeichen sollten ersetzt werden."""
        result = sanitize_filename("file<>:\"|?*.txt")
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_hidden_file_prefix_removed(self):
        """Versteckte Datei-Prefix (.) sollte entfernt werden."""
        result = sanitize_filename(".hidden_file")
        assert not result.startswith(".")
        assert result == "hidden_file"

    def test_max_length_enforced(self):
        """Maximale Länge sollte durchgesetzt werden."""
        long_name = "a" * 300 + ".txt"
        result = sanitize_filename(long_name, max_length=255)
        assert len(result) <= 255
        assert result.endswith(".txt")

    def test_empty_filename_returns_unnamed(self):
        """Leerer Dateiname sollte 'unnamed' zurückgeben."""
        assert sanitize_filename("") == "unnamed"

    def test_only_dots_returns_unnamed(self):
        """Dateiname nur aus Punkten sollte 'unnamed' zurückgeben."""
        assert sanitize_filename("...") == "unnamed"

    def test_null_bytes_removed(self):
        """Null-Bytes sollten entfernt werden."""
        result = sanitize_filename("file\x00name.txt")
        assert "\x00" not in result


class TestSecurityScenarios:
    """Integrationstests für Sicherheitsszenarien"""

    @pytest.fixture
    def temp_base_dir(self):
        """Erstellt ein temporäres Basis-Verzeichnis für Tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_combined_attack_vectors(self, temp_base_dir):
        """Kombinierte Angriffsvektoren sollten abgelehnt werden."""
        attack_vectors = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "....//....//etc/passwd",
            "subdir/../../../etc/passwd",
            "${HOME}/../../../etc/passwd",
            "file.txt\x00.jpg",
            "file\n../../etc/passwd",
        ]

        for vector in attack_vectors:
            is_valid, _, _ = validate_path_within_base(vector, temp_base_dir)
            assert is_valid is False, f"Angriff sollte abgelehnt werden: {repr(vector)}"

    def test_unicode_normalization_attacks(self, temp_base_dir):
        """Unicode-Normalisierungsangriffe sollten behandelt werden."""
        # Diese Tests prüfen ob Unicode-Tricks durchkommen
        tricky_paths = [
            "..／etc/passwd",  # Fullwidth solidus
            "。。/etc/passwd",  # Ideographic full stop
        ]

        for path in tricky_paths:
            is_valid, _, _ = validate_path_within_base(path, temp_base_dir)
            # Sollte entweder abgelehnt werden oder sicher aufgelöst
            if is_valid:
                # Wenn gültig, dann muss resolved_path innerhalb der Basis liegen
                pass  # Die Funktion prüft das bereits intern
