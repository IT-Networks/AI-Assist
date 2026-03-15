"""
Tests für Meta-Tools (combined_search, batch_read_files).

Testet die kombinierten Tools auf korrekte Funktionalität.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.agent.tools import ToolResult, ToolRegistry
from app.agent.meta_tools import (
    combined_search,
    batch_read_files,
    register_meta_tools,
)


class TestCombinedSearch:
    """Tests für combined_search Tool."""

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        """Leere Query sollte Fehler zurückgeben."""
        result = await combined_search(query="")
        assert not result.success
        assert "nicht leer" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_source_returns_error(self):
        """Ungültige Quelle sollte Fehler zurückgeben."""
        result = await combined_search(
            query="test",
            sources="code,invalid_source"
        )
        assert not result.success
        assert "ungültige" in result.error.lower()

    @pytest.mark.asyncio
    async def test_valid_sources_accepted(self):
        """Gültige Quellen sollten akzeptiert werden."""
        # Patch in app.agent.tools da dort die Funktionen definiert sind
        with patch("app.agent.tools.search_code") as mock_code, \
             patch("app.agent.tools.search_handbook") as mock_handbook:

            mock_code.return_value = ToolResult(success=True, data="Code gefunden")
            mock_handbook.return_value = ToolResult(success=True, data="Handbuch gefunden")

            result = await combined_search(
                query="test",
                sources="code,handbook"
            )

            assert result.success
            assert "Code" in result.data
            assert "Handbuch" in result.data

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """Suchen sollten parallel ausgeführt werden."""
        call_times = []

        async def slow_search(**kwargs):
            import time
            call_times.append(time.time())
            await asyncio.sleep(0.1)
            return ToolResult(success=True, data="Ergebnis")

        with patch("app.agent.tools.search_code", slow_search), \
             patch("app.agent.tools.search_handbook", slow_search), \
             patch("app.agent.tools.search_skills", slow_search):

            start = asyncio.get_event_loop().time()
            result = await combined_search(
                query="test",
                sources="code,handbook,skills"
            )
            duration = asyncio.get_event_loop().time() - start

            assert result.success
            # Parallel: ~0.1s, Sequential: ~0.3s
            # Mit etwas Overhead sollte es unter 0.25s bleiben
            assert duration < 0.25, f"Dauerte {duration}s - nicht parallel?"

    @pytest.mark.asyncio
    async def test_handles_partial_failures(self):
        """Sollte auch bei teilweisen Fehlern Ergebnisse zurückgeben."""
        with patch("app.agent.tools.search_code") as mock_code, \
             patch("app.agent.tools.search_handbook") as mock_handbook:

            mock_code.return_value = ToolResult(success=True, data="Code gefunden")
            mock_handbook.return_value = ToolResult(success=False, error="Index fehlt")

            result = await combined_search(
                query="test",
                sources="code,handbook"
            )

            # Sollte erfolgreich sein, da Code gefunden wurde
            assert result.success
            assert "Code" in result.data

    @pytest.mark.asyncio
    async def test_include_content_parameter(self):
        """include_content sollte an search_code weitergegeben werden."""
        with patch("app.agent.tools.search_code") as mock_code:
            mock_code.return_value = ToolResult(success=True, data="Mit Content")

            await combined_search(
                query="test",
                sources="code",
                include_content=True
            )

            # Prüfe ob read_files=True übergeben wurde
            call_kwargs = mock_code.call_args[1]
            assert call_kwargs.get("read_files") is True


class TestBatchReadFiles:
    """Tests für batch_read_files Tool."""

    @pytest.mark.asyncio
    async def test_empty_paths_returns_error(self):
        """Leere Pfade sollten Fehler zurückgeben."""
        result = await batch_read_files(paths="")
        assert not result.success
        assert "keine pfade" in result.error.lower()

    @pytest.mark.asyncio
    async def test_too_many_files_returns_error(self):
        """Mehr als 10 Dateien sollten abgelehnt werden."""
        paths = ", ".join([f"file{i}.txt" for i in range(15)])
        result = await batch_read_files(paths=paths)
        assert not result.success
        assert "maximal 10" in result.error.lower()

    @pytest.mark.asyncio
    async def test_comma_separated_paths(self):
        """Komma-getrennte Pfade sollten erkannt werden."""
        with patch("app.agent.tools.read_file") as mock_read:
            mock_read.return_value = ToolResult(success=True, data="Datei-Inhalt")

            result = await batch_read_files(paths="file1.txt, file2.txt")

            assert result.success
            assert mock_read.call_count == 2

    @pytest.mark.asyncio
    async def test_semicolon_separated_paths(self):
        """Semikolon-getrennte Pfade sollten erkannt werden."""
        with patch("app.agent.tools.read_file") as mock_read:
            mock_read.return_value = ToolResult(success=True, data="Datei-Inhalt")

            result = await batch_read_files(paths="file1.txt; file2.txt")

            assert result.success
            assert mock_read.call_count == 2

    @pytest.mark.asyncio
    async def test_parallel_reading(self):
        """Dateien sollten parallel gelesen werden."""
        call_count = 0

        async def slow_read(**kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return ToolResult(success=True, data=f"Datei {call_count}")

        with patch("app.agent.tools.read_file", slow_read):
            start = asyncio.get_event_loop().time()
            result = await batch_read_files(paths="f1.txt, f2.txt, f3.txt")
            duration = asyncio.get_event_loop().time() - start

            assert result.success
            # Parallel: ~0.1s, Sequential: ~0.3s
            assert duration < 0.2

    @pytest.mark.asyncio
    async def test_max_lines_parameter(self):
        """max_lines_per_file sollte an read_file weitergegeben werden."""
        with patch("app.agent.tools.read_file") as mock_read:
            mock_read.return_value = ToolResult(success=True, data="Content")

            await batch_read_files(
                paths="file1.txt",
                max_lines_per_file=50
            )

            call_kwargs = mock_read.call_args[1]
            assert call_kwargs.get("limit") == 50

    @pytest.mark.asyncio
    async def test_handles_partial_failures(self):
        """Sollte auch bei teilweisen Fehlern Ergebnisse zurückgeben."""
        async def partial_read(**kwargs):
            if "good" in kwargs.get("path", ""):
                return ToolResult(success=True, data="Inhalt")
            return ToolResult(success=False, error="Datei nicht gefunden")

        with patch("app.agent.tools.read_file", partial_read):
            result = await batch_read_files(paths="good.txt, bad.txt")

            assert result.success  # Mindestens eine erfolgreich
            assert "1/2" in result.data


class TestRegisterMetaTools:
    """Tests für Tool-Registrierung."""

    def test_registers_all_tools(self):
        """Alle Meta-Tools sollten registriert werden."""
        registry = ToolRegistry()
        count = register_meta_tools(registry)

        assert count == 3
        assert registry.get("combined_search") is not None
        assert registry.get("batch_read_files") is not None
        assert registry.get("batch_write_files") is not None

    def test_tools_have_descriptions(self):
        """Tools sollten aussagekräftige Beschreibungen haben."""
        registry = ToolRegistry()
        register_meta_tools(registry)

        combined = registry.get("combined_search")
        batch = registry.get("batch_read_files")

        assert "parallel" in combined.description.lower()
        assert "mehrere" in batch.description.lower()

    def test_tools_have_required_parameters(self):
        """Tools sollten die korrekten Parameter haben."""
        registry = ToolRegistry()
        register_meta_tools(registry)

        combined = registry.get("combined_search")
        batch = registry.get("batch_read_files")

        # combined_search: query required
        combined_params = {p.name: p for p in combined.parameters}
        assert "query" in combined_params
        assert combined_params["query"].required is True

        # batch_read_files: paths required
        batch_params = {p.name: p for p in batch.parameters}
        assert "paths" in batch_params
        assert batch_params["paths"].required is True
