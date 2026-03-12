"""
Tests für den Code Search Engine (ripgrep/grep-basiert).
"""

import pytest
from pathlib import Path
from app.services.code_search import CodeSearchEngine, SearchMatch, get_code_search_engine


class TestSearchMatch:
    """Tests für SearchMatch Dataclass."""

    def test_create_basic_match(self):
        """Test: Einfaches SearchMatch erstellen."""
        match = SearchMatch(
            file_path="src/main.py",
            line_number=42,
            line_content="def process_data():"
        )
        assert match.file_path == "src/main.py"
        assert match.line_number == 42
        assert match.line_content == "def process_data():"
        assert match.context_before == []
        assert match.context_after == []
        assert match.repo_type == ""

    def test_create_match_with_context(self):
        """Test: SearchMatch mit Kontext erstellen."""
        match = SearchMatch(
            file_path="src/service.java",
            line_number=100,
            line_content="public void execute() {",
            context_before=["// comment", ""],
            context_after=["    logger.info(\"start\");", "}"],
            repo_type="java"
        )
        assert len(match.context_before) == 2
        assert len(match.context_after) == 2
        assert match.repo_type == "java"


class TestCodeSearchEngine:
    """Tests für CodeSearchEngine."""

    def test_engine_initialization(self):
        """Test: Engine kann initialisiert werden."""
        engine = CodeSearchEngine()
        assert engine._rg_path is None
        assert engine._grep_path is None
        assert engine._checked is False

    def test_singleton_pattern(self):
        """Test: Singleton Pattern funktioniert."""
        engine1 = get_code_search_engine()
        engine2 = get_code_search_engine()
        assert engine1 is engine2

    def test_exclude_dirs_defined(self):
        """Test: Exclude-Verzeichnisse sind definiert."""
        assert ".git" in CodeSearchEngine.EXCLUDE_DIRS
        assert "node_modules" in CodeSearchEngine.EXCLUDE_DIRS
        assert "__pycache__" in CodeSearchEngine.EXCLUDE_DIRS
        assert "target" in CodeSearchEngine.EXCLUDE_DIRS

    def test_language_patterns_defined(self):
        """Test: Sprach-Patterns sind definiert."""
        patterns = CodeSearchEngine.LANGUAGE_PATTERNS

        assert "java" in patterns
        assert "*.java" in patterns["java"]

        assert "python" in patterns
        assert "*.py" in patterns["python"]

        assert "sql" in patterns
        assert "*.sql" in patterns["sql"]

        assert "all" in patterns
        assert "*" in patterns["all"]

    def test_get_file_patterns_custom(self):
        """Test: Custom Pattern überschreibt Language."""
        engine = CodeSearchEngine()
        patterns = engine._get_file_patterns("java", "*.txt")
        assert patterns == ["*.txt"]

    def test_get_file_patterns_language(self):
        """Test: Language Pattern wird verwendet."""
        engine = CodeSearchEngine()
        patterns = engine._get_file_patterns("python", "")
        assert "*.py" in patterns

    def test_get_file_patterns_default(self):
        """Test: Default Pattern bei unbekannter Sprache."""
        engine = CodeSearchEngine()
        patterns = engine._get_file_patterns("unknown", "")
        assert patterns == ["*"]


class TestRipgrepJsonParsing:
    """Tests für ripgrep JSON Output Parsing."""

    def test_parse_empty_output(self):
        """Test: Leerer Output wird korrekt geparst."""
        engine = CodeSearchEngine()
        result = engine._parse_rg_json("", "/tmp", 10)
        assert result == []

    def test_parse_single_match(self):
        """Test: Einzelner Match wird geparst."""
        engine = CodeSearchEngine()
        json_output = '{"type":"match","data":{"path":{"text":"/tmp/test.py"},"lines":{"text":"def test():"},"line_number":1}}'
        result = engine._parse_rg_json(json_output, "/tmp", 10)
        assert len(result) == 1
        assert result[0].file_path == "test.py"
        assert result[0].line_number == 1
        assert result[0].line_content == "def test():"

    def test_parse_invalid_json_lines_skipped(self):
        """Test: Ungültige JSON-Zeilen werden übersprungen."""
        engine = CodeSearchEngine()
        json_output = 'not json\n{"type":"match","data":{"path":{"text":"/tmp/test.py"},"lines":{"text":"code"},"line_number":1}}'
        result = engine._parse_rg_json(json_output, "/tmp", 10)
        assert len(result) == 1

    def test_parse_max_results_respected(self):
        """Test: max_results Limit wird eingehalten."""
        engine = CodeSearchEngine()
        lines = []
        for i in range(1, 20):
            lines.append(f'{{"type":"match","data":{{"path":{{"text":"/tmp/test.py"}},"lines":{{"text":"line {i}"}},"line_number":{i}}}}}')
        json_output = "\n".join(lines)
        result = engine._parse_rg_json(json_output, "/tmp", 5)
        assert len(result) == 5


class TestGrepOutputParsing:
    """Tests für GNU grep Output Parsing."""

    def test_parse_empty_output(self):
        """Test: Leerer Output wird korrekt geparst."""
        engine = CodeSearchEngine()
        result = engine._parse_grep_output("", "/tmp", 10)
        assert result == []

    def test_parse_simple_match(self):
        """Test: Einfacher Match ohne Kontext."""
        engine = CodeSearchEngine()
        output = "/tmp/test.py:10:def hello():"
        result = engine._parse_grep_output(output, "/tmp", 10)
        assert len(result) == 1
        assert result[0].file_path == "test.py"
        assert result[0].line_number == 10
        assert result[0].line_content == "def hello():"

    def test_parse_with_context(self):
        """Test: Match mit Kontext-Zeilen."""
        engine = CodeSearchEngine()
        output = """/tmp/test.py-9-# comment
/tmp/test.py:10:def hello():
/tmp/test.py-11-    pass
--"""
        result = engine._parse_grep_output(output, "/tmp", 10)
        assert len(result) == 1
        match = result[0]
        assert match.line_number == 10
        # Grep-Parser fügt Kontext hinzu
        assert "def hello():" in match.line_content


@pytest.mark.asyncio
class TestCodeSearchIntegration:
    """Integrationstests für CodeSearchEngine."""

    async def test_search_nonexistent_path_raises(self):
        """Test: Suche in nicht-existentem Pfad wirft Fehler."""
        engine = CodeSearchEngine()
        with pytest.raises(FileNotFoundError):
            await engine.search("test", "/path/does/not/exist/12345")

    async def test_check_tools_runs_once(self):
        """Test: Tool-Check läuft nur einmal."""
        engine = CodeSearchEngine()
        await engine._check_tools()
        assert engine._checked is True

        # Zweiter Aufruf sollte schnell sein (gecached)
        await engine._check_tools()
        assert engine._checked is True
