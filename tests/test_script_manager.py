"""
Tests für Script Manager - Validierung, Speicherung und Ausführung.
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from app.services.script_manager import (
    ScriptValidator,
    ScriptStorage,
    ScriptExecutor,
    ScriptManager,
    ScriptSecurityError,
    ValidationResult,
    ExecutionResult,
    Script,
)


# ══════════════════════════════════════════════════════════════════════════════
# ScriptValidator Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptValidator:
    """Tests für Script-Validierung."""

    @pytest.fixture
    def validator(self):
        return ScriptValidator()

    def test_valid_simple_script(self, validator):
        """Test: Einfaches gültiges Script."""
        code = """
import json
data = {"key": "value"}
print(json.dumps(data))
"""
        result = validator.validate(code)
        assert result.is_safe
        assert len(result.errors) == 0
        assert "json" in result.imports_used

    def test_valid_pandas_script(self, validator):
        """Test: Script mit pandas Import."""
        code = """
import pandas as pd
df = pd.DataFrame({"a": [1, 2, 3]})
print(df.head())
"""
        result = validator.validate(code)
        assert result.is_safe
        assert "pandas" in result.imports_used

    def test_invalid_subprocess_import(self, validator):
        """Test: Blockierter subprocess Import."""
        code = """
import subprocess
subprocess.run(["ls"])
"""
        result = validator.validate(code)
        assert not result.is_safe
        assert any("subprocess" in e for e in result.errors)

    def test_invalid_os_system(self, validator):
        """Test: Blockierter os.system Aufruf."""
        code = """
import os
os.system("rm -rf /")
"""
        result = validator.validate(code)
        assert not result.is_safe
        assert any("os.system" in e or "os" in e for e in result.errors)

    def test_invalid_eval(self, validator):
        """Test: Blockierter eval Aufruf."""
        code = """
user_input = "print('hacked')"
eval(user_input)
"""
        result = validator.validate(code)
        assert not result.is_safe
        assert any("eval" in e for e in result.errors)

    def test_invalid_exec(self, validator):
        """Test: Blockierter exec Aufruf."""
        code = """
code = "import os"
exec(code)
"""
        result = validator.validate(code)
        assert not result.is_safe
        assert any("exec" in e for e in result.errors)

    def test_syntax_error(self, validator):
        """Test: Syntax-Fehler wird erkannt."""
        code = """
def broken(
    print("missing closing paren"
"""
        result = validator.validate(code)
        assert not result.is_safe
        assert any("Syntax" in e for e in result.errors)

    def test_warning_while_true(self, validator):
        """Test: Warnung bei while True."""
        code = """
counter = 0
while True:
    counter += 1
    if counter > 10:
        break
"""
        result = validator.validate(code)
        assert result.is_safe  # Kein Fehler, nur Warnung
        assert any("Endlosschleife" in w for w in result.warnings)

    def test_warning_sleep(self, validator):
        """Test: Warnung bei sleep."""
        code = """
import time
time.sleep(1)
"""
        result = validator.validate(code)
        # time ist nicht in allowed_imports, sollte Fehler sein
        assert not result.is_safe

    def test_multiple_imports(self, validator):
        """Test: Mehrere Imports werden erkannt."""
        code = """
import json
import csv
from pathlib import Path
from collections import defaultdict
"""
        result = validator.validate(code)
        assert result.is_safe
        assert "json" in result.imports_used
        assert "csv" in result.imports_used
        assert "pathlib" in result.imports_used
        assert "collections" in result.imports_used

    def test_blocked_socket(self, validator):
        """Test: Blockierter socket Import."""
        code = """
import socket
s = socket.socket()
"""
        result = validator.validate(code)
        assert not result.is_safe

    def test_blocked_urllib(self, validator):
        """Test: Blockierter urllib.request."""
        code = """
from urllib.request import urlopen
data = urlopen("http://evil.com").read()
"""
        result = validator.validate(code)
        assert not result.is_safe


# ══════════════════════════════════════════════════════════════════════════════
# ScriptStorage Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptStorage:
    """Tests für Script-Speicherung."""

    @pytest.fixture
    def temp_dir(self):
        """Temporäres Verzeichnis für Tests."""
        dir_path = tempfile.mkdtemp()
        yield dir_path
        shutil.rmtree(dir_path, ignore_errors=True)

    @pytest.fixture
    def storage(self, temp_dir):
        return ScriptStorage(temp_dir)

    def test_save_and_get(self, storage):
        """Test: Script speichern und laden."""
        code = "print('Hello World')"
        script = storage.save(
            code=code,
            name="Test Script",
            description="Ein Testscript"
        )

        assert script.id is not None
        assert script.name == "Test Script"
        assert script.description == "Ein Testscript"

        # Laden
        loaded = storage.get(script.id)
        assert loaded is not None
        assert loaded.code == code
        assert loaded.name == "Test Script"

    def test_list_all(self, storage):
        """Test: Alle Scripte auflisten."""
        storage.save("print(1)", "Script 1", "Erstes Script")
        storage.save("print(2)", "Script 2", "Zweites Script")

        scripts = storage.list_all()
        assert len(scripts) == 2

    def test_list_with_filter(self, storage):
        """Test: Scripte filtern."""
        storage.save("print(1)", "CSV Parser", "Parst CSV-Dateien")
        storage.save("print(2)", "JSON Builder", "Baut JSON")

        scripts = storage.list_all(filter_text="CSV")
        assert len(scripts) == 1
        assert scripts[0].name == "CSV Parser"

    def test_delete(self, storage):
        """Test: Script löschen."""
        script = storage.save("print('delete me')", "To Delete", "Wird gelöscht")

        assert storage.delete(script.id)
        assert storage.get(script.id) is None

    def test_delete_nonexistent(self, storage):
        """Test: Nicht existierendes Script löschen."""
        assert not storage.delete("nonexistent-id")

    def test_update_execution(self, storage):
        """Test: Ausführungs-Statistik aktualisieren."""
        script = storage.save("print('exec')", "Exec Test", "Test")

        storage.update_execution(script.id)

        loaded = storage.get(script.id)
        assert loaded.execution_count == 1
        assert loaded.last_executed is not None

    def test_stats(self, storage):
        """Test: Statistiken abrufen."""
        storage.save("print(1)", "Script 1", "Test")
        storage.save("print(2)", "Script 2", "Test")

        stats = storage.get_stats()
        assert stats["script_count"] == 2
        assert stats["total_size_kb"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# ScriptExecutor Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptExecutor:
    """Tests für Script-Ausführung."""

    @pytest.fixture
    def executor(self):
        return ScriptExecutor()

    @pytest.mark.asyncio
    async def test_simple_execution(self, executor):
        """Test: Einfaches Script ausführen."""
        script = Script(
            id="test-1",
            name="Simple",
            description="Simple test",
            code="print('Hello from script')",
            created_at=None
        )

        result = await executor.run(script)
        assert result.success
        assert "Hello from script" in result.stdout

    @pytest.mark.asyncio
    async def test_execution_with_args(self, executor):
        """Test: Script mit Argumenten."""
        script = Script(
            id="test-2",
            name="With Args",
            description="Test with args",
            code="""
name = SCRIPT_ARGS.get('name', 'World')
print(f'Hello {name}!')
""",
            created_at=None
        )

        result = await executor.run(script, args={"name": "Test"})
        assert result.success
        assert "Hello Test!" in result.stdout

    @pytest.mark.asyncio
    async def test_execution_error(self, executor):
        """Test: Script mit Fehler."""
        script = Script(
            id="test-3",
            name="Error",
            description="Script with error",
            code="raise ValueError('Test error')",
            created_at=None
        )

        result = await executor.run(script)
        assert not result.success
        assert "ValueError" in result.stderr or "ValueError" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execution_json_output(self, executor):
        """Test: Script mit JSON-Output."""
        script = Script(
            id="test-4",
            name="JSON",
            description="JSON output",
            code="""
import json
data = {"result": 42, "items": [1, 2, 3]}
print(json.dumps(data))
""",
            created_at=None
        )

        result = await executor.run(script)
        assert result.success
        assert '"result": 42' in result.stdout


# ══════════════════════════════════════════════════════════════════════════════
# ScriptManager Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptManager:
    """Integration-Tests für ScriptManager."""

    @pytest.fixture
    def temp_dir(self):
        dir_path = tempfile.mkdtemp()
        yield dir_path
        shutil.rmtree(dir_path, ignore_errors=True)

    @pytest.fixture
    def manager(self, temp_dir, monkeypatch):
        """ScriptManager mit temporärem Verzeichnis."""
        # Mock settings
        class MockConfig:
            enabled = True
            scripts_directory = temp_dir
            max_scripts = 100
            max_script_size_kb = 100
            max_total_size_mb = 50
            cleanup_days = 30
            require_confirmation = True
            allowed_imports = [
                "json", "csv", "pathlib", "re", "datetime", "collections",
                "itertools", "functools", "math", "statistics", "typing",
                "dataclasses", "enum", "copy", "io", "base64", "hashlib",
                "uuid", "random", "string", "textwrap", "difflib", "decimal",
                "fractions", "operator", "contextlib", "abc", "struct",
                "pandas", "numpy", "yaml", "xml", "html", "pprint",
            ]
            blocked_patterns = [
                r"subprocess", r"os\.system", r"os\.popen", r"os\.exec",
                r"eval\s*\(", r"exec\s*\(", r"__import__", r"compile\s*\(",
                r"open\s*\([^)]*['\"][wa]", r"shutil\.rmtree", r"shutil\.move",
                r"socket\.", r"urllib\.request", r"http\.client",
                r"importlib", r"builtins", r"globals\s*\(", r"locals\s*\(",
                r"getattr\s*\(", r"setattr\s*\(", r"delattr\s*\(",
            ]
            use_container = False
            timeout_seconds = 10
            max_output_size_kb = 256

        class MockSettings:
            script_execution = MockConfig()
            class docker_sandbox:
                enabled = False

        # Monkeypatch settings
        import app.services.script_manager as sm_module
        monkeypatch.setattr(sm_module, 'settings', MockSettings())

        return ScriptManager()

    @pytest.mark.asyncio
    async def test_generate_and_execute(self, manager):
        """Test: Script generieren und ausführen."""
        code = """
import json
result = {"sum": 1 + 2 + 3}
print(json.dumps(result))
"""
        # Generieren
        script, validation = await manager.generate_and_save(
            code=code,
            name="Sum Calculator",
            description="Berechnet eine Summe"
        )

        assert script.id is not None
        assert validation.is_safe

        # Ausführen
        result = await manager.execute(script.id)
        assert result.success
        assert '"sum": 6' in result.stdout

    @pytest.mark.asyncio
    async def test_reject_dangerous_script(self, manager):
        """Test: Gefährliches Script wird abgelehnt."""
        code = """
import subprocess
subprocess.run(["rm", "-rf", "/"])
"""
        with pytest.raises(ScriptSecurityError) as exc_info:
            await manager.generate_and_save(
                code=code,
                name="Dangerous",
                description="Should be rejected"
            )

        assert "subprocess" in str(exc_info.value)

    def test_list_and_delete(self, manager):
        """Test: Scripte auflisten und löschen."""
        # Erstelle Script (sync)
        import asyncio
        loop = asyncio.get_event_loop()
        script, _ = loop.run_until_complete(
            manager.generate_and_save("print('test')", "Test", "Test script")
        )

        # Liste
        scripts = manager.list_scripts()
        assert len(scripts) == 1

        # Löschen
        assert manager.delete_script(script.id)
        assert len(manager.list_scripts()) == 0

    def test_validate_only(self, manager):
        """Test: Nur Validierung ohne Speichern."""
        # Gültiger Code
        result = manager.validate_code("import json\nprint(json.dumps({}))")
        assert result.is_safe

        # Ungültiger Code
        result = manager.validate_code("import subprocess")
        assert not result.is_safe
