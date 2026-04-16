"""Tests fuer app/services/test_runner.py."""

from __future__ import annotations

import asyncio
import json
import tempfile
import pytest
from pathlib import Path

from app.services.test_runner import (
    TestRunResult,
    _extract_first_json_blob,
    _parse_pytest,
    _parse_jest_vitest,
    _validate_workspace,
    _truncate,
    execute_test_command,
)


# ════════════════════════════════════════════════════════════════════════════
# Unit Tests: Parsing
# ════════════════════════════════════════════════════════════════════════════

def test_extract_first_json_blob_pure_json():
    text = '{"a": 1, "b": "two"}'
    assert _extract_first_json_blob(text) == {"a": 1, "b": "two"}


def test_extract_first_json_blob_embedded():
    text = 'pytest output lines\n{"summary": {"passed": 5}}\nmore output'
    result = _extract_first_json_blob(text)
    assert result == {"summary": {"passed": 5}}


def test_extract_first_json_blob_no_json():
    text = 'plain text without any json'
    assert _extract_first_json_blob(text) is None


def test_extract_first_json_blob_malformed():
    text = '{ "broken": "'
    assert _extract_first_json_blob(text) is None


def test_extract_first_json_blob_with_braces_in_strings():
    text = '{"msg": "hello { world }"}'
    assert _extract_first_json_blob(text) == {"msg": "hello { world }"}


def test_parse_pytest_json_summary():
    result = TestRunResult(framework="pytest")
    stdout = json.dumps({
        "created": 123.4,
        "summary": {"passed": 28, "failed": 1, "skipped": 2, "error": 0},
    })
    _parse_pytest(stdout, result)
    assert result.tests_passed == 28
    assert result.tests_failed == 1
    assert result.tests_skipped == 2


def test_parse_pytest_json_with_errors():
    result = TestRunResult(framework="pytest")
    stdout = json.dumps({"summary": {"passed": 5, "error": 2, "failed": 1}})
    _parse_pytest(stdout, result)
    # error + failed werden zusammengefasst
    assert result.tests_failed == 3


def test_parse_pytest_text_fallback():
    result = TestRunResult(framework="pytest")
    stdout = "====== 28 passed, 2 skipped in 1.23s ======"
    _parse_pytest(stdout, result)
    assert result.tests_passed == 28
    assert result.tests_skipped == 2


def test_parse_pytest_text_with_failures():
    result = TestRunResult(framework="pytest")
    stdout = "====== 3 failed, 25 passed in 2.10s ======"
    _parse_pytest(stdout, result)
    assert result.tests_passed == 25
    assert result.tests_failed == 3


def test_parse_pytest_coverage_text():
    result = TestRunResult(framework="pytest")
    stdout = (
        "====== 10 passed in 0.5s ======\n"
        "---------- coverage: platform linux ----------\n"
        "Name       Stmts   Miss  Cover\n"
        "my_mod.py     50      5    90%\n"
        "TOTAL         50      5    91.5%\n"
    )
    _parse_pytest(stdout, result)
    assert result.tests_passed == 10
    assert result.coverage_percent == 91.5


def test_parse_jest_json():
    result = TestRunResult(framework="jest")
    stdout = json.dumps({
        "numPassedTests": 15,
        "numFailedTests": 2,
        "numPendingTests": 1,
    })
    _parse_jest_vitest(stdout, result)
    assert result.tests_passed == 15
    assert result.tests_failed == 2
    assert result.tests_skipped == 1


def test_parse_jest_text_fallback():
    result = TestRunResult(framework="jest")
    stdout = "Tests: 2 failed, 1 skipped, 10 passed, 13 total"
    _parse_jest_vitest(stdout, result)
    assert result.tests_passed == 10
    assert result.tests_failed == 2
    assert result.tests_skipped == 1


# ════════════════════════════════════════════════════════════════════════════
# Unit Tests: Helpers
# ════════════════════════════════════════════════════════════════════════════

def test_validate_workspace_valid(tmp_path):
    resolved = _validate_workspace(str(tmp_path))
    assert resolved == tmp_path.resolve()


def test_validate_workspace_empty():
    with pytest.raises(ValueError, match="nicht leer"):
        _validate_workspace("")


def test_validate_workspace_nonexistent(tmp_path):
    missing = tmp_path / "does_not_exist_12345"
    with pytest.raises(ValueError, match="existiert nicht"):
        _validate_workspace(str(missing))


def test_validate_workspace_not_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ValueError, match="kein Verzeichnis"):
        _validate_workspace(str(f))


def test_truncate_short():
    assert _truncate("hello", 100) == "hello"


def test_truncate_long():
    s = "x" * 500
    result = _truncate(s, 100)
    assert len(result) <= 100
    assert "[truncated]" in result


def test_truncate_empty():
    assert _truncate("") == ""
    assert _truncate(None) == ""


# ════════════════════════════════════════════════════════════════════════════
# Integration Tests: execute_test_command
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_command_missing_workspace():
    result = await execute_test_command(
        workspace_path="/nonexistent/path/12345",
        command=["echo", "hi"],
        framework="pytest",
    )
    assert result.success is False
    assert "existiert nicht" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_command_empty_command(tmp_path):
    result = await execute_test_command(
        workspace_path=str(tmp_path),
        command=[],
        framework="pytest",
    )
    assert result.success is False
    assert "Leeres command" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_command_nonexistent_binary(tmp_path):
    result = await execute_test_command(
        workspace_path=str(tmp_path),
        command=["definitely_not_a_real_binary_xyz_12345"],
        framework="pytest",
    )
    assert result.success is False
    assert "nicht gefunden" in (result.error or "").lower() or "nicht gefunden" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_command_captures_exit_code(tmp_path):
    # 'python -c "exit(0)"' -> exit_code 0
    import sys
    result = await execute_test_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        framework="pytest",
    )
    assert result.success is True
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_execute_command_captures_stdout(tmp_path):
    import sys
    result = await execute_test_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('hello from test')"],
        framework="pytest",
    )
    assert result.success is True
    assert "hello from test" in result.stdout_preview


@pytest.mark.asyncio
async def test_execute_command_timeout(tmp_path):
    import sys
    result = await execute_test_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
        framework="pytest",
        timeout_seconds=1,
    )
    assert result.success is False
    assert "Timeout" in (result.error or "")


# ════════════════════════════════════════════════════════════════════════════
# TestRunResult API
# ════════════════════════════════════════════════════════════════════════════

def test_testrunresult_summary_error():
    r = TestRunResult(framework="pytest", error="something broke")
    assert "something broke" in r.summary()


def test_testrunresult_summary_success():
    r = TestRunResult(
        framework="pytest",
        success=True,
        tests_passed=10,
        tests_failed=1,
        tests_skipped=2,
        duration_ms=500,
        coverage_percent=85.3,
    )
    s = r.summary()
    assert "10/13 bestanden" in s
    assert "1 fehler" in s
    assert "Coverage 85.3%" in s


def test_testrunresult_to_dict_serializable():
    r = TestRunResult(
        framework="jest",
        success=True,
        tests_passed=5,
        duration_ms=123,
    )
    d = r.to_dict()
    # Muss JSON-serialisierbar sein (fuer ToolResult.data)
    serialized = json.dumps(d)
    assert "jest" in serialized
    assert '"tests_passed": 5' in serialized
