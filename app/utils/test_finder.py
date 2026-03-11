"""
Test Directory Finder - Findet und verwaltet Test-Verzeichnisse.

Unterstützt:
- Maven Standard (src/test/java)
- Gradle (src/test/java, src/test/groovy)
- Simple (test/, tests/)
- Custom Patterns

Spiegelt Package-Struktur von Source nach Test.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class TestDirectoryFinder:
    """
    Findet und verwaltet Test-Verzeichnisse in Java-Projekten.
    """

    # Bekannte Test-Verzeichnis-Patterns (Priorität: höchste zuerst)
    TEST_PATTERNS = [
        "src/test/java",           # Maven Standard
        "src/test/groovy",         # Gradle Groovy Tests
        "src/test/kotlin",         # Kotlin Tests
        "test/java",               # Alternative
        "tests/java",              # Alternative
        "test",                    # Simple
        "tests",                   # Simple
    ]

    # Source-zu-Test Mapping
    SOURCE_TO_TEST_MAPPING = [
        ("src/main/java", "src/test/java"),
        ("src/main/groovy", "src/test/groovy"),
        ("src/main/kotlin", "src/test/kotlin"),
        ("src", "test"),
        ("main", "test"),
    ]

    def __init__(self, repo_path: str):
        """
        Args:
            repo_path: Pfad zum Repository-Root
        """
        self.repo_path = Path(repo_path)

    def find_test_root(self) -> Optional[Path]:
        """
        Findet das Test-Root-Verzeichnis.

        Returns:
            Pfad zum Test-Verzeichnis oder None
        """
        for pattern in self.TEST_PATTERNS:
            test_dir = self.repo_path / pattern
            if test_dir.exists() and test_dir.is_dir():
                logger.debug(f"Test-Verzeichnis gefunden: {test_dir}")
                return test_dir

        # Kein existierendes gefunden - Standard zurückgeben
        return None

    def find_or_create_test_root(self) -> Path:
        """
        Findet oder erstellt das Test-Root-Verzeichnis.

        Returns:
            Pfad zum Test-Verzeichnis (erstellt wenn nötig)
        """
        existing = self.find_test_root()
        if existing:
            return existing

        # Maven Standard als Default
        default_test_dir = self.repo_path / "src" / "test" / "java"

        # Prüfen ob src/main/java existiert
        if (self.repo_path / "src" / "main" / "java").exists():
            default_test_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Test-Verzeichnis erstellt: {default_test_dir}")
            return default_test_dir

        # Fallback auf simple "test"
        simple_test = self.repo_path / "test"
        simple_test.mkdir(parents=True, exist_ok=True)
        logger.info(f"Test-Verzeichnis erstellt: {simple_test}")
        return simple_test

    def get_test_path_for_source(self, source_file: str) -> Tuple[Path, str]:
        """
        Ermittelt den Test-Pfad für eine Source-Datei.

        Spiegelt die Package-Struktur:
        src/main/java/com/example/Service.java
        → src/test/java/com/example/ServiceTest.java

        Args:
            source_file: Pfad zur Source-Datei

        Returns:
            Tuple (Test-Datei-Pfad, Package-String)
        """
        source_path = Path(source_file)

        # Relativ zum Repo machen
        try:
            rel_path = source_path.relative_to(self.repo_path)
        except ValueError:
            # Datei nicht im Repo - nur Dateiname verwenden
            rel_path = source_path

        # Source-zu-Test Mapping anwenden
        rel_str = str(rel_path).replace("\\", "/")
        test_rel_str = rel_str

        for src_pattern, test_pattern in self.SOURCE_TO_TEST_MAPPING:
            if rel_str.startswith(src_pattern + "/"):
                test_rel_str = rel_str.replace(src_pattern, test_pattern, 1)
                break

        # Dateiname zu Test-Name ändern
        test_path = Path(test_rel_str)
        stem = test_path.stem
        if not stem.endswith("Test"):
            test_name = f"{stem}Test{test_path.suffix}"
        else:
            test_name = test_path.name

        test_file_path = self.repo_path / test_path.parent / test_name

        # Package aus Pfad extrahieren
        package = self._extract_package(test_path.parent)

        return test_file_path, package

    def _extract_package(self, path: Path) -> str:
        """
        Extrahiert den Java-Package-Namen aus einem Pfad.

        src/test/java/com/example/service → com.example.service
        """
        parts = path.parts

        # Bekannte Präfixe entfernen
        skip_parts = {"src", "test", "main", "java", "groovy", "kotlin", "tests"}

        package_parts = []
        for part in parts:
            if part.lower() in skip_parts:
                continue
            package_parts.append(part)

        return ".".join(package_parts)

    def ensure_package_dirs(self, test_file_path: Path) -> None:
        """
        Stellt sicher dass alle Package-Verzeichnisse existieren.

        Args:
            test_file_path: Pfad zur Test-Datei
        """
        test_file_path.parent.mkdir(parents=True, exist_ok=True)

    def find_existing_test(self, source_file: str) -> Optional[Path]:
        """
        Sucht nach existierendem Test für eine Source-Datei.

        Args:
            source_file: Pfad zur Source-Datei

        Returns:
            Pfad zur Test-Datei oder None
        """
        source_path = Path(source_file)
        class_name = source_path.stem
        test_name = f"{class_name}Test.java"

        # 1. Standard-Pfad prüfen
        expected_path, _ = self.get_test_path_for_source(source_file)
        if expected_path.exists():
            return expected_path

        # 2. Im Test-Root suchen
        test_root = self.find_test_root()
        if test_root:
            matches = list(test_root.rglob(test_name))
            if matches:
                return matches[0]

        # 3. Im gesamten Repo suchen
        matches = list(self.repo_path.rglob(test_name))
        for match in matches:
            # Nur in Test-Verzeichnissen
            if "test" in str(match).lower():
                return match

        return None

    def list_test_files(self, pattern: str = "**/*Test.java") -> List[Path]:
        """
        Listet alle Test-Dateien im Repository.

        Args:
            pattern: Glob-Pattern für Test-Dateien

        Returns:
            Liste von Test-Datei-Pfaden
        """
        test_root = self.find_test_root()
        if test_root:
            return list(test_root.glob(pattern))

        # Fallback: Im gesamten Repo suchen
        return [
            p for p in self.repo_path.rglob(pattern)
            if "test" in str(p).lower() and "target" not in str(p).lower()
        ]

    def get_junit_version(self) -> Optional[str]:
        """
        Erkennt die verwendete JUnit-Version aus pom.xml oder build.gradle.

        Returns:
            "4", "5" oder None
        """
        # pom.xml prüfen
        pom_path = self.repo_path / "pom.xml"
        if pom_path.exists():
            try:
                content = pom_path.read_text(encoding="utf-8", errors="replace")
                if "junit-jupiter" in content or "junit-bom" in content:
                    return "5"
                if "junit</artifactId>" in content and "4." in content:
                    return "4"
                if "junit-jupiter" not in content and "junit" in content.lower():
                    return "4"  # Default für ältere Projekte
            except Exception:
                pass

        # build.gradle prüfen
        for gradle_file in ["build.gradle", "build.gradle.kts"]:
            gradle_path = self.repo_path / gradle_file
            if gradle_path.exists():
                try:
                    content = gradle_path.read_text(encoding="utf-8", errors="replace")
                    if "junit-jupiter" in content or "useJUnitPlatform" in content:
                        return "5"
                    if "junit:junit:" in content:
                        return "4"
                except Exception:
                    pass

        return None


def get_test_finder(repo_path: str) -> TestDirectoryFinder:
    """Factory-Funktion für TestDirectoryFinder."""
    return TestDirectoryFinder(repo_path)
