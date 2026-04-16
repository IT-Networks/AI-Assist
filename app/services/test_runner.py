"""
Test-Runner Service – fuehrt pytest/npm-Tests im Workspace aus.

Nutzt asyncio subprocess, parst strukturierte Ausgaben (pytest --json-report,
jest/vitest --reporter=json). Fallback auf Regex fuer plain-text Output.

Security:
- Kein shell=True (keine Command-Injection)
- workspace_path wird validiert + resolved
- Timeout verhindert hängende Tests
- stdout/stderr auf Preview-Groesse begrenzt
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_PREVIEW = 5000


@dataclass
class TestRunResult:
    """Ergebnis einer Test-Ausfuehrung."""

    success: bool = False  # Tool-Call-Erfolg (ob Prozess lief), NICHT ob Tests gruen waren
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    duration_ms: int = 0
    coverage_percent: Optional[float] = None
    exit_code: Optional[int] = None
    framework: str = ""  # "pytest", "jest", "vitest", etc.
    workspace: str = ""
    stdout_preview: str = ""
    stderr_preview: str = ""
    error: Optional[str] = None

    def summary(self) -> str:
        """Kurze textuelle Zusammenfassung."""
        if self.error:
            return f"Fehler: {self.error}"
        total = self.tests_passed + self.tests_failed + self.tests_skipped
        cov = f", Coverage {self.coverage_percent:.1f}%" if self.coverage_percent is not None else ""
        return (
            f"{self.framework}: {self.tests_passed}/{total} bestanden, "
            f"{self.tests_failed} fehler, {self.tests_skipped} skip, "
            f"Dauer {self.duration_ms}ms{cov}"
        )

    def to_dict(self) -> Dict:
        """Serialisierbar fuer ToolResult.data."""
        return {
            "success": self.success,
            "framework": self.framework,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "duration_ms": self.duration_ms,
            "coverage_percent": self.coverage_percent,
            "exit_code": self.exit_code,
            "workspace": self.workspace,
            "summary": self.summary(),
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "error": self.error,
        }


def _validate_workspace(workspace_path: str) -> Path:
    """Validiert und normalisiert den Workspace-Pfad.

    Raises:
        ValueError: Wenn Pfad leer, nicht existent oder kein Verzeichnis.
    """
    if not workspace_path or not workspace_path.strip():
        raise ValueError("workspace_path darf nicht leer sein")
    resolved = Path(workspace_path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"workspace_path existiert nicht: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"workspace_path ist kein Verzeichnis: {resolved}")
    return resolved


def _truncate(text: str, max_len: int = MAX_OUTPUT_PREVIEW) -> str:
    """Kuerzt Text auf max_len Zeichen (mit ... marker)."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n...[truncated]..."


async def execute_test_command(
    workspace_path: str,
    command: List[str],
    framework: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    env: Optional[Dict[str, str]] = None,
) -> TestRunResult:
    """Fuehrt einen Test-Kommando-Prozess im Workspace aus.

    Args:
        workspace_path: Absoluter Pfad zum Workspace (wird validiert)
        command: Kommando-Liste ohne shell=True (z.B. ["pytest", "tests/"])
        framework: Name fuer Logging (pytest, jest, vitest)
        timeout_seconds: Abbruchlimit
        env: Optionale zusaetzliche Umgebungsvariablen

    Returns:
        TestRunResult mit success + strukturierten Metriken
    """
    import os

    result = TestRunResult(framework=framework)

    try:
        cwd = _validate_workspace(workspace_path)
        result.workspace = str(cwd)
    except ValueError as e:
        result.error = str(e)
        return result

    if not command:
        result.error = "Leeres command uebergeben"
        return result

    # Environment: base copy + user overrides
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    logger.info(
        f"[test_runner] Starte {framework} in {cwd}: {' '.join(command)} "
        f"(timeout={timeout_seconds}s)"
    )
    start = time.time()

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run_env,
        )
    except FileNotFoundError as e:
        result.error = (
            f"Kommando nicht gefunden: {command[0]}. "
            f"Ist {framework} im PATH/Projekt installiert? ({e})"
        )
        logger.error(f"[test_runner] {result.error}")
        return result
    except Exception as e:
        result.error = f"Prozess-Start fehlgeschlagen: {e}"
        logger.exception(f"[test_runner] {result.error}")
        return result

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        result.error = f"Timeout nach {timeout_seconds}s - Test-Ausfuehrung abgebrochen"
        result.duration_ms = int((time.time() - start) * 1000)
        logger.warning(f"[test_runner] {result.error}")
        return result

    result.duration_ms = int((time.time() - start) * 1000)
    result.exit_code = process.returncode
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    result.stdout_preview = _truncate(stdout)
    result.stderr_preview = _truncate(stderr)

    # Success = Prozess ist zu Ende gelaufen (unabhaengig von exit_code).
    # exit_code != 0 bei pytest bedeutet "Tests fehlgeschlagen" - das ist
    # ein valides Ergebnis, keine Tool-Failure.
    result.success = True

    # Parsing je nach framework
    if framework == "pytest":
        _parse_pytest(stdout, result)
    elif framework in ("jest", "vitest"):
        _parse_jest_vitest(stdout, result)

    logger.info(
        f"[test_runner] {framework} fertig in {result.duration_ms}ms: "
        f"{result.tests_passed} passed, {result.tests_failed} failed, exit={result.exit_code}"
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Output-Parsing
# ══════════════════════════════════════════════════════════════════════════════

_PYTEST_JSON_HEADER = re.compile(r'^\s*\{.*"created":\s*[0-9.]+', re.MULTILINE)
_PYTEST_TEXT_SUMMARY = re.compile(
    r"(?:(\d+)\s+failed)?.*?(\d+)\s+passed(?:.*?(\d+)\s+skipped)?", re.IGNORECASE
)
_COVERAGE_PERCENT = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+(?:\.\d+)?)%")


def _parse_pytest(stdout: str, result: TestRunResult) -> None:
    """Fuellt result-Felder aus pytest-Output.

    Versucht zuerst JSON-Report-Plugin Output, dann Text-Fallback.
    """
    # 1. JSON-Report Plugin Output? Enthaelt typisch {"created": ..., "summary": {...}}
    json_data = _extract_first_json_blob(stdout)
    if json_data and isinstance(json_data.get("summary"), dict):
        s = json_data["summary"]
        result.tests_passed = int(s.get("passed", 0) or 0)
        result.tests_failed = int(s.get("failed", 0) or 0) + int(s.get("error", 0) or 0)
        result.tests_skipped = int(s.get("skipped", 0) or 0)
        # Coverage aus separatem key falls vorhanden
        if "coverage" in json_data and isinstance(json_data["coverage"], dict):
            pct = json_data["coverage"].get("percent_covered")
            if pct is not None:
                try:
                    result.coverage_percent = float(pct)
                except (TypeError, ValueError):
                    pass
        logger.debug("[test_runner] pytest-JSON erfolgreich geparst")
        return

    # 2. Text-Fallback: "===== 28 passed, 2 skipped in 1.23s ====="
    for line in reversed(stdout.splitlines()):
        if "passed" in line or "failed" in line:
            passed = re.search(r"(\d+)\s+passed", line)
            failed = re.search(r"(\d+)\s+failed", line)
            errors = re.search(r"(\d+)\s+errors?", line)
            skipped = re.search(r"(\d+)\s+skipped", line)
            if passed or failed:
                if passed:
                    result.tests_passed = int(passed.group(1))
                if failed:
                    result.tests_failed = int(failed.group(1))
                if errors:
                    result.tests_failed += int(errors.group(1))
                if skipped:
                    result.tests_skipped = int(skipped.group(1))
                break

    # Coverage aus Text: "TOTAL ... 91%"
    cov_match = _COVERAGE_PERCENT.search(stdout)
    if cov_match:
        try:
            result.coverage_percent = float(cov_match.group(1))
        except ValueError:
            pass


def _parse_jest_vitest(stdout: str, result: TestRunResult) -> None:
    """Fuellt result-Felder aus jest/vitest-Output.

    Versucht JSON-Output, fallback Text.
    """
    json_data = _extract_first_json_blob(stdout)
    if json_data:
        # Jest/Vitest JSON Format: numPassedTests, numFailedTests, numTotalTests, ...
        if "numPassedTests" in json_data:
            result.tests_passed = int(json_data.get("numPassedTests", 0) or 0)
            result.tests_failed = int(json_data.get("numFailedTests", 0) or 0)
            result.tests_skipped = int(json_data.get("numPendingTests", 0) or 0)
            # Coverage summary falls vorhanden
            cov = json_data.get("coverageMap") or json_data.get("coverage")
            if isinstance(cov, dict):
                total = cov.get("total") if isinstance(cov.get("total"), dict) else None
                if total and isinstance(total.get("lines"), dict):
                    pct = total["lines"].get("pct")
                    if isinstance(pct, (int, float)):
                        result.coverage_percent = float(pct)
            logger.debug("[test_runner] jest-JSON erfolgreich geparst")
            return

    # Text-Fallback
    # "Tests: 5 failed, 23 passed, 28 total"
    text_match = re.search(
        r"Tests:\s*(?:(\d+)\s+failed,?\s*)?(?:(\d+)\s+skipped,?\s*)?(\d+)\s+passed",
        stdout,
    )
    if text_match:
        if text_match.group(1):
            result.tests_failed = int(text_match.group(1))
        if text_match.group(2):
            result.tests_skipped = int(text_match.group(2))
        result.tests_passed = int(text_match.group(3))


def _extract_first_json_blob(text: str) -> Optional[Dict]:
    """Extrahiert das erste balancierte JSON-Objekt aus Text.

    pytest --json-report und jest --json mischen manchmal Normal-Output
    mit JSON. Wir suchen das erste { ... } das valide JSON ist.
    """
    if not text:
        return None
    # Fast-Path: gesamter Output ist JSON
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Balance-Scanner: finde passendes } zum ersten {
    start_idx = text.find("{")
    while start_idx != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start_idx : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start_idx = text.find("{", start_idx + 1)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# High-Level API
# ══════════════════════════════════════════════════════════════════════════════

async def run_pytest(
    workspace_path: str,
    test_path: str = "tests",
    pattern: Optional[str] = None,
    coverage: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> TestRunResult:
    """Fuehrt pytest im Workspace aus.

    Args:
        workspace_path: Absoluter Workspace-Pfad
        test_path: Relativ zum Workspace (default "tests")
        pattern: pytest -k Pattern (optional)
        coverage: --cov hinzufuegen
        timeout_seconds: Abbruchlimit
    """
    cmd: List[str] = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"]
    # JSON-Report-Plugin nutzen wenn verfuegbar; stdout-Route (--json-report-file=-)
    cmd += ["--json-report", "--json-report-file=/dev/stdout"] if sys.platform != "win32" else [
        "--json-report",
        "--json-report-file=-",
    ]
    if pattern:
        cmd += ["-k", pattern]
    if coverage:
        cmd += ["--cov", "--cov-report=term"]
    return await execute_test_command(
        workspace_path=workspace_path,
        command=cmd,
        framework="pytest",
        timeout_seconds=timeout_seconds,
    )


async def run_npm_tests(
    workspace_path: str,
    framework: str = "auto",
    coverage: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> TestRunResult:
    """Fuehrt npm-Tests im Workspace aus.

    Args:
        workspace_path: Absoluter Workspace-Pfad
        framework: "auto" | "jest" | "vitest"
        coverage: --coverage hinzufuegen
        timeout_seconds: Abbruchlimit
    """
    resolved_framework = framework
    if framework == "auto":
        resolved_framework = _detect_frontend_framework(workspace_path) or "jest"

    # npm test leitet argumente via -- weiter
    cmd: List[str] = ["npm", "test", "--", "--reporters=default", "--json"]
    if coverage:
        cmd += ["--coverage"]

    # Windows: npm ist meist npm.cmd
    if sys.platform == "win32":
        cmd[0] = "npm.cmd"

    return await execute_test_command(
        workspace_path=workspace_path,
        command=cmd,
        framework=resolved_framework,
        timeout_seconds=timeout_seconds,
    )


def _detect_frontend_framework(workspace_path: str) -> Optional[str]:
    """Erkennt jest vs vitest anhand package.json."""
    try:
        pkg = Path(workspace_path) / "package.json"
        if not pkg.exists():
            return None
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "vitest" in deps:
            return "vitest"
        if "jest" in deps:
            return "jest"
    except Exception as e:
        logger.debug(f"[test_runner] framework-detection failed: {e}")
    return None
