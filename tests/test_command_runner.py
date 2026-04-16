"""Tests fuer app/services/command_runner.py + app/agent/command_tools.py."""

from __future__ import annotations

import sys
import pytest
from pathlib import Path

from app.services.command_runner import (
    CommandResult,
    DEFAULT_BINARY_WHITELIST,
    _binary_name,
    _validate_workspace,
    _truncate,
    validate_command,
    run_workspace_command,
)


# ════════════════════════════════════════════════════════════════════════════
# Whitelist + Validation
# ════════════════════════════════════════════════════════════════════════════

def test_binary_name_simple():
    assert _binary_name("python") == "python"


def test_binary_name_with_path():
    assert _binary_name("/usr/bin/python3") == "python3"
    assert _binary_name("C:\\Users\\x\\python.exe") in ("python.exe", "C:\\Users\\x\\python.exe")


def test_binary_name_empty():
    assert _binary_name("") == ""


def test_validate_command_python_ok():
    assert validate_command(["python", "main.py"]) is None


def test_validate_command_npm_ok():
    assert validate_command(["npm", "run", "dev"]) is None


def test_validate_command_mvn_ok():
    assert validate_command(["mvn", "package"]) is None


def test_validate_command_case_insensitive():
    # Windows: NPM.CMD sollte matchen
    err = validate_command(["NPM.CMD", "test"])
    assert err is None


def test_validate_command_not_in_whitelist():
    err = validate_command(["rm", "-rf", "/"])
    assert err is not None
    assert "nicht in der Whitelist" in err


def test_validate_command_unknown_binary():
    err = validate_command(["evil_binary_xyz"])
    assert err is not None


def test_validate_command_empty():
    assert validate_command([]) is not None
    assert validate_command([""]) is not None


def test_validate_command_not_a_list():
    err = validate_command("python main.py")  # type: ignore
    assert err is not None


def test_validate_command_nul_byte():
    err = validate_command(["python", "main.py\x00"])
    assert err is not None
    assert "NUL-Zeichen" in err


def test_validate_command_custom_whitelist():
    custom = {"only_allowed_bin"}
    assert validate_command(["only_allowed_bin", "arg"], whitelist=custom) is None
    assert validate_command(["python"], whitelist=custom) is not None


def test_validate_command_non_string_arg():
    err = validate_command(["python", 123])  # type: ignore
    assert err is not None
    assert "int" in err


# ════════════════════════════════════════════════════════════════════════════
# Workspace Validation
# ════════════════════════════════════════════════════════════════════════════

def test_validate_workspace_valid(tmp_path):
    resolved = _validate_workspace(str(tmp_path))
    assert resolved == tmp_path.resolve()


def test_validate_workspace_empty():
    with pytest.raises(ValueError, match="nicht leer"):
        _validate_workspace("")


def test_validate_workspace_nonexistent(tmp_path):
    with pytest.raises(ValueError, match="existiert nicht"):
        _validate_workspace(str(tmp_path / "not_exist_xyz"))


def test_validate_workspace_not_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ValueError, match="kein Verzeichnis"):
        _validate_workspace(str(f))


# ════════════════════════════════════════════════════════════════════════════
# Truncate Helper
# ════════════════════════════════════════════════════════════════════════════

def test_truncate_short():
    assert _truncate("hi") == "hi"


def test_truncate_long():
    s = "x" * 10000
    out = _truncate(s, 100)
    assert len(out) <= 100
    assert "[truncated]" in out


def test_truncate_empty():
    assert _truncate("") == ""


# ════════════════════════════════════════════════════════════════════════════
# CommandResult API
# ════════════════════════════════════════════════════════════════════════════

def test_command_result_default():
    r = CommandResult()
    assert r.success is False
    assert r.command == []
    assert r.exit_code is None


def test_command_result_summary_error():
    r = CommandResult(error="xyz")
    assert "xyz" in r.summary()


def test_command_result_summary_ok():
    r = CommandResult(success=True, exit_code=0, duration_ms=500, command=["python","a.py"], workspace="/tmp")
    s = r.summary()
    assert "python a.py" in s
    assert "exit=0" in s


def test_command_result_to_dict_serializable():
    import json
    r = CommandResult(command=["npm","run","dev"], workspace="/tmp", duration_ms=100, exit_code=0, success=True)
    d = r.to_dict()
    assert json.dumps(d)  # darf nicht werfen


# ════════════════════════════════════════════════════════════════════════════
# run_workspace_command Integration
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_command_nonexistent_workspace():
    result = await run_workspace_command(
        workspace_path="/nonexistent/path/xyz12345",
        command=["python", "-c", "print(1)"],
    )
    assert result.success is False
    assert "existiert nicht" in (result.error or "")


@pytest.mark.asyncio
async def test_run_command_not_whitelisted(tmp_path):
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=["evil_bin_xyz"],
    )
    assert result.success is False
    assert "Whitelist" in (result.error or "")


@pytest.mark.asyncio
async def test_run_command_python_echo(tmp_path):
    # python existiert auf Testsystem (wir laufen ja damit)
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('hallo welt')"],
    )
    # sys.executable-basename ist python/python.exe → in whitelist
    # ABER: sys.executable kann /home/user/.../python sein - wir brauchen das _binary_name
    # Wenn sys.executable nicht whitelisted ist, schlaegt es fehl - dann custom whitelist
    if result.success is False and "Whitelist" in (result.error or ""):
        # Fallback mit erweiterten Whitelist fuer Test
        result = await run_workspace_command(
            workspace_path=str(tmp_path),
            command=[sys.executable, "-c", "print('hallo welt')"],
            whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        )
    assert result.success is True
    assert result.exit_code == 0
    assert "hallo welt" in result.stdout_preview


@pytest.mark.asyncio
async def test_run_command_timeout(tmp_path):
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
    )
    assert result.success is False
    assert "Timeout" in (result.error or "")


@pytest.mark.asyncio
async def test_run_command_captures_stderr(tmp_path):
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import sys; sys.stderr.write('err-out')"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
    )
    assert result.success is True
    assert "err-out" in result.stderr_preview


@pytest.mark.asyncio
async def test_run_command_captures_nonzero_exit(tmp_path):
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import sys; sys.exit(42)"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
    )
    # Prozess-Erfolg != Tests-Erfolg: success=True, aber exit_code=42
    assert result.success is True
    assert result.exit_code == 42


# ════════════════════════════════════════════════════════════════════════════
# Tool-Handler Tests
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tool_handler_requires_confirmation_first():
    from app.agent.command_tools import _handle_run_workspace_command
    result = await _handle_run_workspace_command(
        path="/tmp" if sys.platform != "win32" else str(Path.cwd()),
        command=["python", "--version"],
    )
    # Erste Call: confirmation required
    assert result.requires_confirmation is True
    assert result.confirmation_data is not None
    assert result.confirmation_data["action"] == "run_workspace_command"


@pytest.mark.asyncio
async def test_tool_handler_rejects_unknown_binary():
    from app.agent.command_tools import _handle_run_workspace_command
    # Auch VOR confirm wird gegen whitelist geprueft
    result = await _handle_run_workspace_command(
        path="/tmp" if sys.platform != "win32" else str(Path.cwd()),
        command=["unknown_evil_bin_xyz"],
    )
    assert result.success is False
    assert "Whitelist" in (result.error or "")


@pytest.mark.asyncio
async def test_tool_handler_rejects_missing_command():
    from app.agent.command_tools import _handle_run_workspace_command
    result = await _handle_run_workspace_command(
        path="/tmp",
        command=[],
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_tool_handler_rejects_missing_path():
    from app.agent.command_tools import _handle_run_workspace_command
    result = await _handle_run_workspace_command(
        path="",
        command=["python"],
    )
    assert result.success is False
