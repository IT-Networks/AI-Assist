"""
JUnit Test Execution Service.

Führt JUnit-Tests aus, parst Ergebnisse und generiert Fix-Vorschläge.
"""

import asyncio
import json
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)


class TestStatus(str, Enum):
    """Status eines Tests oder Test-Laufs."""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class TestCase:
    """Ein einzelner Test-Case."""
    name: str
    class_name: str
    status: TestStatus
    duration_seconds: float = 0.0

    # Failure/Error info
    failure_message: Optional[str] = None
    failure_type: Optional[str] = None  # AssertionError, NullPointerException, etc.
    stack_trace: Optional[str] = None

    # Source location
    file_path: Optional[str] = None
    line_number: Optional[int] = None

    # Fix suggestion
    suggested_fix: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "class_name": self.class_name,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "failure_message": self.failure_message,
            "failure_type": self.failure_type,
            "stack_trace": self.stack_trace,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class TestSuite:
    """Eine Test-Suite (Test-Klasse)."""
    name: str
    file_path: str
    tests: List[TestCase] = field(default_factory=list)

    # Timing
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.FAILED)

    @property
    def errors(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.ERROR)

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.SKIPPED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "tests": [t.to_dict() for t in self.tests],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
        }


@dataclass
class TestRun:
    """Ein kompletter Test-Lauf."""
    id: str
    session_id: str
    target: str  # Class/Package being tested
    status: TestStatus

    suites: List[TestSuite] = field(default_factory=list)

    # Timing
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    # Coverage
    coverage_percent: Optional[float] = None
    coverage_report_path: Optional[str] = None

    # Build info
    build_tool: str = "maven"  # maven, gradle
    build_command: str = ""
    build_output: str = ""
    exit_code: int = 0

    @property
    def total_tests(self) -> int:
        return sum(s.total for s in self.suites)

    @property
    def passed_tests(self) -> int:
        return sum(s.passed for s in self.suites)

    @property
    def failed_tests(self) -> int:
        return sum(s.failed for s in self.suites)

    @property
    def error_tests(self) -> int:
        return sum(s.errors for s in self.suites)

    @property
    def skipped_tests(self) -> int:
        return sum(s.skipped for s in self.suites)

    @property
    def duration_seconds(self) -> float:
        return sum(s.duration_seconds for s in self.suites)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "target": self.target,
            "status": self.status.value,
            "suites": [s.to_dict() for s in self.suites],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "coverage_percent": self.coverage_percent,
            "coverage_report_path": self.coverage_report_path,
            "build_tool": self.build_tool,
            "build_command": self.build_command,
            "exit_code": self.exit_code,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "error_tests": self.error_tests,
            "skipped_tests": self.skipped_tests,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class TestFix:
    """Ein generierter Fix für einen fehlgeschlagenen Test."""
    id: str
    test_class: str
    test_method: str

    # Fix details
    fix_type: str  # "assertion", "null_check", "mock_setup", "implementation"
    description: str
    confidence: float  # 0.0 - 1.0

    # Code changes
    file_path: str
    original_code: str
    fixed_code: str
    diff: str

    # Validation
    validated: bool = False
    validation_passed: bool = False
    validation_output: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "test_class": self.test_class,
            "test_method": self.test_method,
            "fix_type": self.fix_type,
            "description": self.description,
            "confidence": self.confidence,
            "file_path": self.file_path,
            "original_code": self.original_code,
            "fixed_code": self.fixed_code,
            "diff": self.diff,
            "validated": self.validated,
            "validation_passed": self.validation_passed,
            "validation_output": self.validation_output,
        }


class TestExecutionService:
    """Führt Tests aus und sammelt Ergebnisse."""

    def __init__(self, java_path: str = None):
        from app.core.config import settings
        self.java_path = java_path or settings.java.get_active_path()
        self.build_tool = self._detect_build_tool()
        self._test_runs: Dict[str, TestRun] = {}

    def _detect_build_tool(self) -> str:
        """Erkennt Maven oder Gradle."""
        if self.java_path:
            path = Path(self.java_path)
            if (path / "pom.xml").exists():
                return "maven"
            if (path / "build.gradle").exists() or (path / "build.gradle.kts").exists():
                return "gradle"
        return "maven"

    async def run_tests(
        self,
        target: str,
        session_id: str,
        with_coverage: bool = True,
        test_method: str = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Führt Tests aus und streamt Ergebnisse.

        Args:
            target: Klassen- oder Package-Name (z.B. "UserService" oder "com.example.service")
            session_id: Session-ID für Tracking
            with_coverage: Coverage-Report generieren
            test_method: Optional: Einzelne Test-Methode

        Yields:
            - {"type": "started", "data": TestRun}
            - {"type": "output", "data": {"line": str}}
            - {"type": "test_started", "data": {"class": str, "method": str}}
            - {"type": "test_finished", "data": TestCase}
            - {"type": "suite_finished", "data": TestSuite}
            - {"type": "finished", "data": TestRun}
            - {"type": "error", "data": {"message": str}}
        """
        run_id = str(uuid.uuid4())
        test_run = TestRun(
            id=run_id,
            session_id=session_id,
            target=target,
            status=TestStatus.RUNNING,
            started_at=datetime.utcnow().isoformat(),
            build_tool=self.build_tool
        )

        self._test_runs[run_id] = test_run
        yield {"type": "started", "data": test_run.to_dict()}

        if not self.java_path:
            yield {"type": "error", "data": {"message": "Kein Java-Projekt konfiguriert"}}
            return

        try:
            # Build command
            if self.build_tool == "maven":
                cmd = self._build_maven_command(target, test_method, with_coverage)
            else:
                cmd = self._build_gradle_command(target, test_method, with_coverage)

            test_run.build_command = cmd
            logger.info(f"[TestExecution] Running: {cmd}")

            # Execute
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=self.java_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**dict(__import__('os').environ), "JAVA_TOOL_OPTIONS": ""}
            )

            output_lines = []
            current_test_class = None
            current_test_method = None

            async for line in process.stdout:
                line_str = line.decode('utf-8', errors='replace').rstrip()
                output_lines.append(line_str)

                # Streaming output
                yield {"type": "output", "data": {"line": line_str}}

                # Parse test events from Maven/Gradle output
                event = self._parse_test_output_line(line_str)
                if event:
                    if event.get("type") == "test_started":
                        current_test_class = event["data"].get("class")
                        current_test_method = event["data"].get("method")
                    yield event

            exit_code = await process.wait()
            test_run.build_output = "\n".join(output_lines[-500:])  # Last 500 lines
            test_run.exit_code = exit_code

            # Parse JUnit XML reports
            suites = await self._parse_junit_reports()
            test_run.suites = suites

            # Yield suite finished events
            for suite in suites:
                yield {"type": "suite_finished", "data": suite.to_dict()}

            # Parse coverage
            if with_coverage:
                coverage = await self._parse_coverage_report()
                if coverage is not None:
                    test_run.coverage_percent = coverage
                    logger.info(f"[TestExecution] Coverage: {coverage:.1f}%")

            # Final status
            if test_run.total_tests == 0:
                test_run.status = TestStatus.ERROR
            elif test_run.failed_tests == 0 and test_run.error_tests == 0:
                test_run.status = TestStatus.PASSED
            else:
                test_run.status = TestStatus.FAILED

            test_run.finished_at = datetime.utcnow().isoformat()

            yield {"type": "finished", "data": test_run.to_dict()}

        except Exception as e:
            logger.error(f"[TestExecution] Error: {e}")
            test_run.status = TestStatus.ERROR
            test_run.finished_at = datetime.utcnow().isoformat()
            yield {"type": "error", "data": {"message": str(e)}}

    def _build_maven_command(self, target: str, test_method: str = None,
                             with_coverage: bool = True) -> str:
        """Baut Maven Test-Command."""
        cmd = "mvn test -B"  # -B for batch mode

        # Target filter
        if target:
            if test_method:
                # Specific method
                cmd += f" -Dtest={target}#{test_method}"
            elif "." in target and not target.endswith("Test"):
                # Package
                cmd += f" -Dtest={target}.**"
            else:
                # Class
                cmd += f" -Dtest=*{target}*"

        # Coverage with JaCoCo
        if with_coverage:
            cmd += " org.jacoco:jacoco-maven-plugin:prepare-agent"
            cmd += " -Djacoco.destFile=target/jacoco.exec"

        # Ensure surefire reports are generated
        cmd += " -Dsurefire.useFile=true"
        cmd += " -Dmaven.test.failure.ignore=true"  # Don't fail build on test failures

        return cmd

    def _build_gradle_command(self, target: str, test_method: str = None,
                              with_coverage: bool = True) -> str:
        """Baut Gradle Test-Command."""
        cmd = "gradle test --info"

        # Target filter
        if target:
            if test_method:
                cmd += f" --tests {target}.{test_method}"
            elif "." in target:
                cmd += f" --tests {target}.*"
            else:
                cmd += f" --tests *{target}*"

        # Coverage with JaCoCo
        if with_coverage:
            cmd += " jacocoTestReport"

        return cmd

    def _parse_test_output_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parst eine Zeile des Test-Outputs für Live-Events."""
        # Maven Surefire patterns
        # [INFO] Running com.example.UserServiceTest
        running_match = re.search(r'\[INFO\] Running (\S+)', line)
        if running_match:
            class_name = running_match.group(1)
            return {
                "type": "test_started",
                "data": {"class": class_name, "method": None}
            }

        # [INFO] Tests run: 5, Failures: 1, Errors: 0, Skipped: 0
        summary_match = re.search(
            r'Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)',
            line
        )
        if summary_match:
            return {
                "type": "test_summary",
                "data": {
                    "total": int(summary_match.group(1)),
                    "failures": int(summary_match.group(2)),
                    "errors": int(summary_match.group(3)),
                    "skipped": int(summary_match.group(4))
                }
            }

        # Gradle patterns
        # > Task :test
        # UserServiceTest > testCreateUser PASSED
        gradle_test_match = re.search(r'(\w+) > (\w+) (PASSED|FAILED|SKIPPED)', line)
        if gradle_test_match:
            status_map = {"PASSED": TestStatus.PASSED, "FAILED": TestStatus.FAILED, "SKIPPED": TestStatus.SKIPPED}
            return {
                "type": "test_finished",
                "data": {
                    "class": gradle_test_match.group(1),
                    "method": gradle_test_match.group(2),
                    "status": status_map.get(gradle_test_match.group(3), TestStatus.PASSED).value
                }
            }

        return None

    async def _parse_junit_reports(self) -> List[TestSuite]:
        """Parst JUnit XML Reports aus target/surefire-reports."""
        suites = []

        # Maven Surefire reports
        surefire_dir = Path(self.java_path) / "target" / "surefire-reports"
        if surefire_dir.exists():
            for xml_file in surefire_dir.glob("TEST-*.xml"):
                suite = await self._parse_junit_xml(xml_file)
                if suite:
                    suites.append(suite)

        # Gradle reports
        gradle_dir = Path(self.java_path) / "build" / "test-results" / "test"
        if gradle_dir.exists():
            for xml_file in gradle_dir.glob("TEST-*.xml"):
                suite = await self._parse_junit_xml(xml_file)
                if suite:
                    suites.append(suite)

        return suites

    async def _parse_junit_xml(self, xml_path: Path) -> Optional[TestSuite]:
        """Parst eine JUnit XML Datei."""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            suite = TestSuite(
                name=root.get("name", "Unknown"),
                file_path=str(xml_path),
                duration_seconds=float(root.get("time", 0))
            )

            for testcase in root.findall("testcase"):
                tc = TestCase(
                    name=testcase.get("name", "unknown"),
                    class_name=testcase.get("classname", ""),
                    duration_seconds=float(testcase.get("time", 0)),
                    status=TestStatus.PASSED
                )

                # Check for failure
                failure = testcase.find("failure")
                if failure is not None:
                    tc.status = TestStatus.FAILED
                    tc.failure_type = failure.get("type", "")
                    tc.failure_message = failure.get("message", "")
                    tc.stack_trace = failure.text or ""
                    # Extract line number from stack trace
                    tc.line_number = self._extract_line_from_stacktrace(tc.stack_trace)

                # Check for error
                error = testcase.find("error")
                if error is not None:
                    tc.status = TestStatus.ERROR
                    tc.failure_type = error.get("type", "")
                    tc.failure_message = error.get("message", "")
                    tc.stack_trace = error.text or ""
                    tc.line_number = self._extract_line_from_stacktrace(tc.stack_trace)

                # Check for skipped
                skipped = testcase.find("skipped")
                if skipped is not None:
                    tc.status = TestStatus.SKIPPED
                    tc.failure_message = skipped.get("message")

                suite.tests.append(tc)

            return suite

        except Exception as e:
            logger.error(f"[TestExecution] Failed to parse JUnit XML {xml_path}: {e}")
            return None

    def _extract_line_from_stacktrace(self, stack_trace: str) -> Optional[int]:
        """Extrahiert Zeilennummer aus Stack Trace."""
        if not stack_trace:
            return None

        # Pattern: at com.example.Class.method(File.java:42)
        match = re.search(r'\([\w.]+\.java:(\d+)\)', stack_trace)
        if match:
            return int(match.group(1))
        return None

    async def _parse_coverage_report(self) -> Optional[float]:
        """Parst JaCoCo Coverage Report."""
        # Maven JaCoCo XML report
        jacoco_xml = Path(self.java_path) / "target" / "site" / "jacoco" / "jacoco.xml"
        if jacoco_xml.exists():
            try:
                tree = ET.parse(jacoco_xml)
                root = tree.getroot()

                # Find instruction counter
                for counter in root.findall(".//counter[@type='INSTRUCTION']"):
                    missed = int(counter.get("missed", 0))
                    covered = int(counter.get("covered", 0))
                    total = missed + covered
                    if total > 0:
                        return (covered / total) * 100

            except Exception as e:
                logger.warning(f"[TestExecution] Failed to parse JaCoCo report: {e}")

        # Gradle JaCoCo
        gradle_jacoco = Path(self.java_path) / "build" / "reports" / "jacoco" / "test" / "jacocoTestReport.xml"
        if gradle_jacoco.exists():
            try:
                tree = ET.parse(gradle_jacoco)
                root = tree.getroot()

                for counter in root.findall(".//counter[@type='INSTRUCTION']"):
                    missed = int(counter.get("missed", 0))
                    covered = int(counter.get("covered", 0))
                    total = missed + covered
                    if total > 0:
                        return (covered / total) * 100

            except Exception as e:
                logger.warning(f"[TestExecution] Failed to parse Gradle JaCoCo report: {e}")

        return None

    def get_test_run(self, run_id: str) -> Optional[TestRun]:
        """Gibt einen Test-Run zurück."""
        return self._test_runs.get(run_id)

    def get_session_runs(self, session_id: str) -> List[TestRun]:
        """Gibt alle Test-Runs einer Session zurück."""
        return [r for r in self._test_runs.values() if r.session_id == session_id]


class TestFixGenerator:
    """Generiert Fixes für fehlgeschlagene Tests."""

    # Known fix patterns
    _FIX_PATTERNS = [
        {
            "pattern": r"expected:<(.+)> but was:<(.+)>",
            "type": "assertion",
            "description": "Assertion erwartet anderen Wert",
            "confidence": 0.8
        },
        {
            "pattern": r"expected:\s*<(.+)>\s*but was:\s*<(.+)>",
            "type": "assertion",
            "description": "Assertion erwartet anderen Wert",
            "confidence": 0.8
        },
        {
            "pattern": r"NullPointerException",
            "type": "null_check",
            "description": "Null-Referenz - Objekt nicht initialisiert",
            "confidence": 0.7
        },
        {
            "pattern": r"Wanted but not invoked",
            "type": "mock_setup",
            "description": "Mock-Methode wurde nicht aufgerufen",
            "confidence": 0.75
        },
        {
            "pattern": r"No interactions wanted",
            "type": "mock_verify",
            "description": "Unerwarteter Mock-Aufruf",
            "confidence": 0.75
        },
        {
            "pattern": r"Cannot invoke .* because .* is null",
            "type": "null_check",
            "description": "Methodenaufruf auf null-Objekt",
            "confidence": 0.8
        },
        {
            "pattern": r"ArrayIndexOutOfBoundsException",
            "type": "bounds_check",
            "description": "Array-Index außerhalb der Grenzen",
            "confidence": 0.7
        },
    ]

    def __init__(self, java_path: str = None):
        from app.core.config import settings
        self.java_path = java_path or settings.java.get_active_path()

    async def generate_fix(self, test_case: TestCase) -> Optional[TestFix]:
        """Generiert einen Fix für einen fehlgeschlagenen Test."""
        if test_case.status not in (TestStatus.FAILED, TestStatus.ERROR):
            return None

        error_text = test_case.failure_message or ""

        # 1. Check known patterns
        for pattern_info in self._FIX_PATTERNS:
            if re.search(pattern_info["pattern"], error_text, re.IGNORECASE):
                return await self._generate_pattern_fix(test_case, pattern_info)

        # 2. Generic LLM-based fix
        return await self._generate_llm_fix(test_case)

    async def _generate_pattern_fix(self, test_case: TestCase,
                                    pattern_info: Dict[str, Any]) -> Optional[TestFix]:
        """Generiert Fix basierend auf bekanntem Pattern."""
        fix_id = str(uuid.uuid4())

        # Try to find the source file
        source_path = await self._find_source_file(test_case.class_name)

        if not source_path:
            return None

        try:
            original_code = source_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return None

        # Generate fix suggestion based on pattern type
        fix_suggestion = self._get_pattern_fix_suggestion(
            pattern_info["type"],
            test_case.failure_message,
            test_case.stack_trace
        )

        return TestFix(
            id=fix_id,
            test_class=test_case.class_name,
            test_method=test_case.name,
            fix_type=pattern_info["type"],
            description=f"{pattern_info['description']}: {fix_suggestion}",
            confidence=pattern_info["confidence"],
            file_path=str(source_path),
            original_code="",  # Would need context analysis
            fixed_code="",  # Would need LLM for actual fix
            diff=f"// {fix_suggestion}",
            validated=False,
            validation_passed=False
        )

    def _get_pattern_fix_suggestion(self, fix_type: str, message: str,
                                    stack_trace: str) -> str:
        """Gibt Fix-Vorschlag basierend auf Pattern-Typ zurück."""
        if fix_type == "assertion":
            # Extract expected/actual values
            match = re.search(r"expected:<(.+)> but was:<(.+)>", message or "")
            if match:
                expected, actual = match.groups()
                return f"Erwarteter Wert '{expected}' stimmt nicht mit aktuellem Wert '{actual}' überein. Prüfe die Implementierung oder passe den erwarteten Wert an."

        elif fix_type == "null_check":
            return "Objekt ist null - stelle sicher dass es vor Verwendung initialisiert wird oder füge einen Null-Check hinzu."

        elif fix_type == "mock_setup":
            return "Mock-Methode wurde nicht aufgerufen - prüfe ob der Mock korrekt konfiguriert ist und die Methode tatsächlich aufgerufen wird."

        elif fix_type == "mock_verify":
            return "Unerwarteter Mock-Aufruf - entferne den Aufruf oder passe die Verification an."

        elif fix_type == "bounds_check":
            return "Array-Index außerhalb der Grenzen - prüfe die Array-Größe vor Zugriff."

        return "Prüfe den Stack-Trace für weitere Details."

    async def _generate_llm_fix(self, test_case: TestCase) -> Optional[TestFix]:
        """Generiert Fix mit LLM."""
        try:
            from app.services.llm_client import llm_client

            # Read source files
            test_source = await self._read_test_source(test_case)
            impl_source = await self._read_implementation_source(test_case)

            if not test_source:
                return None

            prompt = f"""Analysiere diesen fehlgeschlagenen JUnit-Test und schlage einen Fix vor.

TEST-KLASSE:
```java
{test_source[:3000]}
```

{f'''IMPLEMENTIERUNG:
```java
{impl_source[:3000]}
```''' if impl_source else ''}

FEHLER:
{test_case.failure_message or 'Unbekannt'}

STACK TRACE:
{(test_case.stack_trace or '')[:1000]}

Analysiere das Problem und gib eine kurze Beschreibung des Fixes.
Antworte im Format:
FIX_TYPE: [assertion|null_check|mock_setup|implementation|configuration]
BESCHREIBUNG: [Kurze Beschreibung des Problems und der Lösung]
CONFIDENCE: [0.0-1.0]
"""

            response = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=None  # Use default
            )

            content = response.get("content", "")

            # Parse response
            fix_type = "implementation"
            description = "LLM-generierter Fix-Vorschlag"
            confidence = 0.5

            type_match = re.search(r"FIX_TYPE:\s*(\w+)", content)
            if type_match:
                fix_type = type_match.group(1).lower()

            desc_match = re.search(r"BESCHREIBUNG:\s*(.+?)(?:\n|CONFIDENCE)", content, re.DOTALL)
            if desc_match:
                description = desc_match.group(1).strip()

            conf_match = re.search(r"CONFIDENCE:\s*([\d.]+)", content)
            if conf_match:
                try:
                    confidence = float(conf_match.group(1))
                except ValueError:
                    pass

            source_path = await self._find_source_file(test_case.class_name)

            return TestFix(
                id=str(uuid.uuid4()),
                test_class=test_case.class_name,
                test_method=test_case.name,
                fix_type=fix_type,
                description=description,
                confidence=min(1.0, max(0.0, confidence)),
                file_path=str(source_path) if source_path else "",
                original_code="",
                fixed_code="",
                diff=f"// {description}",
                validated=False,
                validation_passed=False
            )

        except Exception as e:
            logger.error(f"[TestFixGenerator] LLM fix generation failed: {e}")
            return None

    async def _find_source_file(self, class_name: str) -> Optional[Path]:
        """Findet die Source-Datei für eine Klasse."""
        if not self.java_path:
            return None

        # Convert class name to path
        # com.example.UserServiceTest -> com/example/UserServiceTest.java
        path_parts = class_name.replace(".", "/") + ".java"

        # Search in common source directories
        search_dirs = ["src/test/java", "src/main/java", "test", "src"]

        for search_dir in search_dirs:
            full_path = Path(self.java_path) / search_dir / path_parts
            if full_path.exists():
                return full_path

        # Fallback: search by file name
        simple_name = class_name.split(".")[-1] + ".java"
        for java_file in Path(self.java_path).rglob(simple_name):
            return java_file

        return None

    async def _read_test_source(self, test_case: TestCase) -> Optional[str]:
        """Liest den Test-Quellcode."""
        source_path = await self._find_source_file(test_case.class_name)
        if source_path and source_path.exists():
            try:
                return source_path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                pass
        return None

    async def _read_implementation_source(self, test_case: TestCase) -> Optional[str]:
        """Liest den Implementierungs-Quellcode."""
        # Derive implementation class from test class
        class_name = test_case.class_name
        if class_name.endswith("Test"):
            impl_name = class_name[:-4]
        elif class_name.endswith("Tests"):
            impl_name = class_name[:-5]
        else:
            return None

        source_path = await self._find_source_file(impl_name)
        if source_path and source_path.exists():
            try:
                return source_path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                pass
        return None

    async def validate_fix(self, fix: TestFix, execution_service: TestExecutionService) -> TestFix:
        """Validiert einen Fix durch erneuten Test-Lauf."""
        if not fix.file_path or not fix.original_code or not fix.fixed_code:
            fix.validated = True
            fix.validation_passed = False
            fix.validation_output = "Fix enthält keine Code-Änderungen"
            return fix

        source_path = Path(fix.file_path)
        if not source_path.exists():
            fix.validated = True
            fix.validation_passed = False
            fix.validation_output = f"Datei nicht gefunden: {fix.file_path}"
            return fix

        # Backup
        original_content = source_path.read_text(encoding='utf-8')

        try:
            # Apply fix
            new_content = original_content.replace(fix.original_code, fix.fixed_code)
            source_path.write_text(new_content, encoding='utf-8')

            # Run test
            async for event in execution_service.run_tests(
                fix.test_class,
                session_id="validation",
                with_coverage=False,
                test_method=fix.test_method
            ):
                if event["type"] == "finished":
                    run = event["data"]
                    fix.validated = True
                    fix.validation_passed = run["status"] == "passed"
                    fix.validation_output = f"Tests: {run['passed_tests']}/{run['total_tests']} bestanden"
                    break

        except Exception as e:
            fix.validated = True
            fix.validation_passed = False
            fix.validation_output = f"Validation fehlgeschlagen: {str(e)}"

        finally:
            # Restore original
            source_path.write_text(original_content, encoding='utf-8')

        return fix


# ══════════════════════════════════════════════════════════════════════════════
# Singleton Accessors
# ══════════════════════════════════════════════════════════════════════════════

_execution_service: Optional[TestExecutionService] = None
_fix_generator: Optional[TestFixGenerator] = None


def get_test_execution_service() -> TestExecutionService:
    """Gibt die singleton TestExecutionService-Instanz zurück."""
    global _execution_service
    if _execution_service is None:
        _execution_service = TestExecutionService()
    return _execution_service


def get_test_fix_generator() -> TestFixGenerator:
    """Gibt die singleton TestFixGenerator-Instanz zurück."""
    global _fix_generator
    if _fix_generator is None:
        _fix_generator = TestFixGenerator()
    return _fix_generator
