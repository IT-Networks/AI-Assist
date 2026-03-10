"""
Java Validator - Syntax-Check mit javac.

Prüfungen:
1. Syntax (javac -Xlint:all) - Quick Mode
2. Maven Compile - Full Mode
3. Gradle Compile - Full Mode (wenn build.gradle vorhanden)

Modi:
- quick: Nur javac Syntax-Check (schnell, keine Dependencies)
- maven: Maven compile (vollständig, Dependencies werden aufgelöst)
- gradle: Gradle compile
"""

import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.validators.base import (
    BaseValidator,
    Severity,
    ValidationIssue,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class JavaValidator(BaseValidator):
    """Validator für Java-Dateien."""

    file_extensions = [".java"]
    name = "java"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        # Konfiguration mit Defaults
        self.mode = self.config.get("mode", "quick")  # quick | maven | gradle
        self.java_home = self.config.get("java_home", "")
        self.javac_options = self.config.get("javac_options", "-Xlint:all")

        # Tool-Pfade ermitteln
        self._javac_path = self._find_javac()
        self._mvn_path = shutil.which("mvn") or shutil.which("mvn.cmd")

    def _find_javac(self) -> Optional[str]:
        """Findet javac im JAVA_HOME oder PATH."""
        # Explizites JAVA_HOME
        if self.java_home:
            javac = Path(self.java_home) / "bin" / "javac"
            if javac.exists():
                return str(javac)
            # Windows
            javac_exe = Path(self.java_home) / "bin" / "javac.exe"
            if javac_exe.exists():
                return str(javac_exe)

        # System JAVA_HOME
        java_home_env = os.environ.get("JAVA_HOME")
        if java_home_env:
            javac = Path(java_home_env) / "bin" / "javac"
            if javac.exists():
                return str(javac)
            javac_exe = Path(java_home_env) / "bin" / "javac.exe"
            if javac_exe.exists():
                return str(javac_exe)

        # PATH
        return shutil.which("javac")

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """Validiert eine Java-Datei."""
        start = time.time()
        issues: List[ValidationIssue] = []

        # Datei existiert?
        if not Path(file_path).exists():
            return ValidationResult(
                file_path=file_path,
                file_type=self.name,
                success=False,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    message=f"File not found: {file_path}",
                )],
            )

        # javac verfügbar?
        if not self._javac_path:
            return self._create_skipped_result(
                file_path,
                "javac not found. Set JAVA_HOME or install JDK."
            )

        # Modus bestimmen
        if self.mode == "maven":
            issues = await self._run_maven_compile(file_path)
        elif self.mode == "gradle":
            issues = await self._run_gradle_compile(file_path)
        else:
            # Quick Mode: Nur javac
            issues = await self._run_javac(file_path)

        return self._create_result(file_path, issues, start, strict)

    async def _run_javac(self, file_path: str) -> List[ValidationIssue]:
        """Führt javac Syntax-Check aus (ohne Kompilierung)."""
        # -d /dev/null um keine .class zu erzeugen
        # Windows: -d NUL
        null_dev = "NUL" if os.name == "nt" else "/dev/null"

        cmd = [self._javac_path]

        # Optionen parsen
        if self.javac_options:
            cmd.extend(self.javac_options.split())

        # Encoding
        cmd.extend(["-encoding", "UTF-8"])

        # Nur Syntax prüfen, nicht kompilieren
        cmd.extend(["-d", null_dev])

        cmd.append(file_path)

        returncode, stdout, stderr = await self._run_command(cmd, timeout=30)

        if returncode == 0 and not stderr:
            return []

        # Fehler parsen
        return self._parse_javac_output(stderr or stdout, file_path)

    def _parse_javac_output(self, output: str, file_path: str) -> List[ValidationIssue]:
        """Parst javac Fehlerausgabe."""
        issues = []

        # javac Fehler-Format:
        # File.java:10: error: ';' expected
        # File.java:10: warning: [deprecation] xyz is deprecated
        pattern = re.compile(
            r"^(.+?):(\d+): (error|warning): (.+)$",
            re.MULTILINE
        )

        for match in pattern.finditer(output):
            severity_str = match.group(3)
            message = match.group(4)

            severity = Severity.ERROR if severity_str == "error" else Severity.WARNING

            # Rule aus Warning extrahieren (z.B. [deprecation])
            rule = None
            rule_match = re.search(r"\[(\w+)\]", message)
            if rule_match:
                rule = rule_match.group(1)

            issues.append(ValidationIssue(
                severity=severity,
                message=message,
                line=int(match.group(2)),
                rule=rule,
                source="javac",
            ))

        # Zusätzliche Informationen (Note:, ^-Zeilen) ignorieren

        return issues

    async def _run_maven_compile(self, file_path: str) -> List[ValidationIssue]:
        """Führt Maven compile aus."""
        if not self._mvn_path:
            return [ValidationIssue(
                severity=Severity.WARNING,
                message="Maven not found. Falling back to javac.",
                source="maven",
            )] + await self._run_javac(file_path)

        # pom.xml finden
        file_dir = Path(file_path).parent
        pom_path = self._find_pom(file_dir)

        if not pom_path:
            # Fallback auf javac
            return await self._run_javac(file_path)

        cmd = [self._mvn_path, "compile", "-q", "-f", str(pom_path)]

        returncode, stdout, stderr = await self._run_command(
            cmd,
            cwd=str(pom_path.parent),
            timeout=120
        )

        if returncode == 0:
            return []

        # Maven-Fehler parsen
        return self._parse_maven_output(stderr or stdout, file_path)

    def _find_pom(self, start_dir: Path) -> Optional[Path]:
        """Sucht pom.xml aufwärts im Verzeichnisbaum."""
        current = start_dir
        for _ in range(10):  # Max 10 Ebenen nach oben
            pom = current / "pom.xml"
            if pom.exists():
                return pom
            if current.parent == current:
                break
            current = current.parent
        return None

    def _parse_maven_output(self, output: str, file_path: str) -> List[ValidationIssue]:
        """Parst Maven Fehlerausgabe."""
        issues = []

        # Maven Compiler Fehler
        # [ERROR] /path/File.java:[10,5] error message
        pattern = re.compile(
            r"\[ERROR\]\s+(.+?):?\[(\d+),(\d+)\]\s*(.+)",
            re.MULTILINE
        )

        for match in pattern.finditer(output):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=match.group(4),
                line=int(match.group(2)),
                column=int(match.group(3)),
                source="maven",
            ))

        # Wenn keine spezifischen Fehler gefunden, generischen Fehler
        if not issues and "BUILD FAILURE" in output:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message="Maven build failed. Check output for details.",
                source="maven",
            ))

        return issues

    async def _run_gradle_compile(self, file_path: str) -> List[ValidationIssue]:
        """Führt Gradle compile aus."""
        gradle_path = shutil.which("gradle") or shutil.which("gradlew")

        if not gradle_path:
            return [ValidationIssue(
                severity=Severity.WARNING,
                message="Gradle not found. Falling back to javac.",
                source="gradle",
            )] + await self._run_javac(file_path)

        # build.gradle finden
        file_dir = Path(file_path).parent
        gradle_file = self._find_gradle_build(file_dir)

        if not gradle_file:
            return await self._run_javac(file_path)

        cmd = [gradle_path, "compileJava", "-q"]

        returncode, stdout, stderr = await self._run_command(
            cmd,
            cwd=str(gradle_file.parent),
            timeout=120
        )

        if returncode == 0:
            return []

        # Gradle-Fehler sind ähnlich wie javac
        return self._parse_javac_output(stderr or stdout, file_path)

    def _find_gradle_build(self, start_dir: Path) -> Optional[Path]:
        """Sucht build.gradle aufwärts."""
        current = start_dir
        for _ in range(10):
            for name in ("build.gradle", "build.gradle.kts"):
                gradle = current / name
                if gradle.exists():
                    return gradle
            if current.parent == current:
                break
            current = current.parent
        return None
