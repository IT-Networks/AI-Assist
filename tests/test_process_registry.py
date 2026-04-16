"""Tests fuer app/services/process_registry.py + Cancel-Flow im command_runner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.services.process_registry import (
    ProcessRegistry,
    RunningProcess,
    get_process_registry,
    reset_process_registry,
)
from app.services.command_runner import (
    DEFAULT_BINARY_WHITELIST,
    run_workspace_command,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Stellt sicher dass jeder Test mit frischer Registry startet."""
    reset_process_registry()
    yield
    reset_process_registry()


# ════════════════════════════════════════════════════════════════════════════
# ProcessRegistry: Basis
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_registry_singleton_returns_same_instance():
    a = get_process_registry()
    b = get_process_registry()
    assert a is b


@pytest.mark.asyncio
async def test_registry_register_and_get(tmp_path):
    reg = ProcessRegistry()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(0.5)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        entry = await reg.register("sess-1", proc, [sys.executable, "-c", "..."], str(tmp_path))
        assert isinstance(entry, RunningProcess)
        assert entry.session_id == "sess-1"
        assert entry.command_id  # uuid
        assert reg.get("sess-1") is entry
        assert reg.is_running("sess-1") is True
    finally:
        proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_registry_supersede_existing_session(tmp_path):
    reg = ProcessRegistry()
    proc1 = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(2)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(2)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        entry1 = await reg.register("sess-X", proc1, ["a"], str(tmp_path))
        entry2 = await reg.register("sess-X", proc2, ["b"], str(tmp_path))
        # entry1 muss als superseded markiert sein
        assert entry1.cancel_event.is_set()
        assert entry1.cancelled_by == "superseded"
        # entry2 ist der aktuelle
        assert reg.get("sess-X") is entry2
        assert not entry2.cancel_event.is_set()
    finally:
        for p in (proc1, proc2):
            try:
                p.kill()
                await p.wait()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_registry_cancel_session_sets_event(tmp_path):
    reg = ProcessRegistry()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(2)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        entry = await reg.register("sess-C", proc, ["x"], str(tmp_path))
        assert not entry.cancel_event.is_set()
        cancelled = await reg.cancel_session("sess-C")
        assert cancelled is entry
        assert entry.cancel_event.is_set()
        assert entry.cancelled_by == "user"
    finally:
        proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_registry_cancel_unknown_session_returns_none():
    reg = ProcessRegistry()
    result = await reg.cancel_session("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_registry_cleanup_removes_entry(tmp_path):
    reg = ProcessRegistry()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "print(1)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    entry = await reg.register("sess-D", proc, ["x"], str(tmp_path))
    await proc.wait()
    await reg.cleanup("sess-D", command_id=entry.command_id)
    assert reg.get("sess-D") is None
    assert reg.list_sessions() == []


@pytest.mark.asyncio
async def test_registry_cleanup_skips_on_command_id_mismatch(tmp_path):
    """Wenn nach einem cleanup der Eintrag schon einem neuen Command gehoert,
    darf der alte cleanup ihn nicht entfernen."""
    reg = ProcessRegistry()
    proc1 = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "print(1)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(0.3)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        entry1 = await reg.register("sess-E", proc1, ["a"], str(tmp_path))
        await proc1.wait()
        # neuer Command in selber Session
        entry2 = await reg.register("sess-E", proc2, ["b"], str(tmp_path))
        # alter cleanup mit alter command_id darf entry2 nicht entfernen
        await reg.cleanup("sess-E", command_id=entry1.command_id)
        assert reg.get("sess-E") is entry2
    finally:
        for p in (proc1, proc2):
            try:
                p.kill()
                await p.wait()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_registry_to_dict_serializable(tmp_path):
    import json
    reg = ProcessRegistry()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "print(1)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    entry = await reg.register("s", proc, ["python", "-c", "print(1)"], str(tmp_path))
    d = entry.to_dict()
    assert json.dumps(d)  # darf nicht werfen
    assert d["session_id"] == "s"
    assert d["command_id"]
    assert "duration_ms" in d
    proc.kill()
    await proc.wait()


# ════════════════════════════════════════════════════════════════════════════
# command_runner: Cancel-Integration
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_command_cancellable_via_registry(tmp_path):
    """Sleep-5s wird nach 200ms via registry.cancel_session abgebrochen."""
    reg = ProcessRegistry()

    async def cancel_after(delay):
        await asyncio.sleep(delay)
        await reg.cancel_session("test-cancel")

    canceller = asyncio.create_task(cancel_after(0.2))
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=10,
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        session_id="test-cancel",
        registry=reg,
    )
    await canceller
    assert result.success is False
    assert "abgebrochen" in (result.error or "").lower()
    assert result.duration_ms < 3000  # nicht 5s warten
    # Registry muss aufgeraeumt sein
    assert reg.get("test-cancel") is None


@pytest.mark.asyncio
async def test_run_command_without_session_id_skips_registry(tmp_path):
    """Wenn kein session_id uebergeben -> kein Cancel-Support, registry ignored."""
    reg = ProcessRegistry()
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('ok')"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        registry=reg,  # registry da, aber session_id fehlt
    )
    assert result.success is True
    assert reg.list_sessions() == []  # nichts registriert


@pytest.mark.asyncio
async def test_run_command_registers_then_cleanups(tmp_path):
    """Normaler Run: Registry hat Eintrag waehrend Lauf, weg danach."""
    reg = ProcessRegistry()
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "print('hi')"],
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        session_id="cleanup-test",
        registry=reg,
    )
    assert result.success is True
    # Nach Ende muss cleanup gelaufen sein
    assert reg.get("cleanup-test") is None


@pytest.mark.asyncio
async def test_run_command_timeout_still_cleans_registry(tmp_path):
    reg = ProcessRegistry()
    result = await run_workspace_command(
        workspace_path=str(tmp_path),
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
        whitelist=DEFAULT_BINARY_WHITELIST | {Path(sys.executable).name},
        session_id="timeout-test",
        registry=reg,
    )
    assert result.success is False
    assert "Timeout" in (result.error or "")
    assert reg.get("timeout-test") is None


# ════════════════════════════════════════════════════════════════════════════
# AgentContext ContextVar
# ════════════════════════════════════════════════════════════════════════════

def test_agent_context_default_is_none():
    from app.agent.agent_context import current_session_id
    assert current_session_id() is None


def test_agent_context_set_and_reset():
    from app.agent.agent_context import (
        current_session_id,
        set_current_session_id,
        reset_current_session_id,
    )
    token = set_current_session_id("sess-99")
    try:
        assert current_session_id() == "sess-99"
    finally:
        reset_current_session_id(token)
    assert current_session_id() is None


@pytest.mark.asyncio
async def test_processes_endpoint_filtered_by_session(tmp_path):
    """GET /api/agent/processes?session_id=X liefert nur Procs der Session."""
    from fastapi.testclient import TestClient
    from main import app
    from app.services.process_registry import get_process_registry

    reg = get_process_registry()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(2)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        await reg.register("recover-sess", proc, ["python", "x"], str(tmp_path))
        client = TestClient(app)
        # Filter: nur die Session
        r = client.get("/api/agent/processes?session_id=recover-sess")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["processes"][0]["session_id"] == "recover-sess"
        # Filter: andere Session -> leer
        r = client.get("/api/agent/processes?session_id=other-sess")
        assert r.status_code == 200
        assert r.json()["count"] == 0
    finally:
        proc.kill()
        await proc.wait()
        await reg.cleanup("recover-sess")


@pytest.mark.asyncio
async def test_agent_context_isolated_per_task():
    """ContextVars sind per asyncio-Task isoliert."""
    from app.agent.agent_context import current_session_id, set_current_session_id

    captured = {}

    async def task_a():
        token = set_current_session_id("A")
        await asyncio.sleep(0.05)
        captured["a"] = current_session_id()

    async def task_b():
        token = set_current_session_id("B")
        await asyncio.sleep(0.05)
        captured["b"] = current_session_id()

    await asyncio.gather(task_a(), task_b())
    assert captured == {"a": "A", "b": "B"}
