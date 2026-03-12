"""
Code Search Engine - Bash-basierte Code-Suche mit ripgrep/grep.

Verwendet ripgrep (rg) als primäres Such-Tool mit GNU grep als Fallback.
Keine Index-Abhängigkeit - durchsucht Dateien direkt.

Features:
- Ripgrep für schnelle Regex-Suche
- GNU grep als Fallback
- Kontext-Zeilen Unterstützung
- Glob-Pattern für Dateifilter
- JSON-Output-Parsing (ripgrep)
"""

import asyncio
import json
import logging
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SearchMatch:
    """Ein Suchtreffer."""
    file_path: str              # Relativer Pfad zur Datei
    line_number: int            # 1-basiert
    line_content: str           # Die Matching-Zeile
    context_before: List[str] = field(default_factory=list)
    context_after: List[str] = field(default_factory=list)
    repo_type: str = ""         # "java" | "python" | ""


class CodeSearchEngine:
    """
    Bash-basierte Code-Suche mit ripgrep (primary) und grep (fallback).

    Usage:
        engine = CodeSearchEngine()
        matches = await engine.search("OrderService", "/path/to/repo")
    """

    # Standard-Exclude-Patterns
    EXCLUDE_DIRS = [
        ".git", ".svn", ".hg",
        "node_modules", "bower_components",
        "target", "build", "dist", "out",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        ".venv", "venv", ".env",
        ".idea", ".vscode", ".eclipse",
        "vendor", "packages",
    ]

    # Datei-Patterns nach Sprache
    LANGUAGE_PATTERNS = {
        "java": ["*.java", "*.xml", "*.properties", "*.yaml", "*.yml"],
        "python": ["*.py", "*.pyi", "*.yaml", "*.yml", "*.toml"],
        "sql": ["*.sql", "*.sqlj"],
        "all": ["*"],
    }

    def __init__(self):
        self._rg_path: Optional[str] = None
        self._grep_path: Optional[str] = None
        self._checked = False

    async def _check_tools(self) -> None:
        """Prüft verfügbare Such-Tools (einmalig)."""
        if self._checked:
            return

        # Check ripgrep
        self._rg_path = shutil.which("rg")
        if self._rg_path:
            logger.debug(f"[code_search] ripgrep found: {self._rg_path}")

        # Check grep
        self._grep_path = shutil.which("grep")
        if self._grep_path:
            logger.debug(f"[code_search] grep found: {self._grep_path}")

        self._checked = True

        if not self._rg_path and not self._grep_path:
            logger.warning("[code_search] Weder ripgrep noch grep gefunden!")

    @property
    def has_ripgrep(self) -> bool:
        return self._rg_path is not None

    @property
    def has_grep(self) -> bool:
        return self._grep_path is not None

    async def search(
        self,
        query: str,
        base_path: str,
        language: str = "all",
        file_pattern: str = "",
        max_results: int = 20,
        context_lines: int = 2,
        case_sensitive: bool = False,
        subpath: str = ""
    ) -> Tuple[List[SearchMatch], str, float]:
        """
        Führt Code-Suche mit bestem verfügbarem Tool aus.

        Args:
            query: Suchbegriff oder Regex-Pattern
            base_path: Basis-Verzeichnis für Suche
            language: Sprache für Dateifilter ("all", "java", "python", "sql")
            file_pattern: Optionales Glob-Pattern (überschreibt language)
            max_results: Maximale Anzahl Treffer
            context_lines: Kontext-Zeilen vor/nach Match
            case_sensitive: Groß-/Kleinschreibung beachten
            subpath: Optionales Unterverzeichnis

        Returns:
            Tuple von (matches, tool_used, duration_seconds)
        """
        import time
        start = time.time()

        await self._check_tools()

        # Pfad auflösen
        search_path = Path(base_path)
        if subpath:
            search_path = search_path / subpath

        if not search_path.exists():
            raise FileNotFoundError(f"Suchpfad nicht gefunden: {search_path}")

        # Datei-Pattern bestimmen
        patterns = self._get_file_patterns(language, file_pattern)

        # Suche ausführen
        tool_used = "none"
        matches: List[SearchMatch] = []

        if self._rg_path:
            try:
                matches = await self._search_ripgrep(
                    query, str(search_path), patterns,
                    max_results, context_lines, case_sensitive
                )
                tool_used = "ripgrep"
            except Exception as e:
                logger.warning(f"[code_search] ripgrep failed: {e}, trying grep fallback")

        if not matches and self._grep_path:
            try:
                matches = await self._search_grep(
                    query, str(search_path), patterns,
                    max_results, context_lines, case_sensitive
                )
                tool_used = "grep"
            except Exception as e:
                logger.warning(f"[code_search] grep failed: {e}")

        duration = time.time() - start
        logger.debug(f"[code_search] {len(matches)} matches with {tool_used} in {duration:.2f}s")

        return matches, tool_used, duration

    def _get_file_patterns(self, language: str, custom_pattern: str) -> List[str]:
        """Bestimmt die Datei-Patterns für die Suche."""
        if custom_pattern:
            return [custom_pattern]
        return self.LANGUAGE_PATTERNS.get(language, self.LANGUAGE_PATTERNS["all"])

    async def _search_ripgrep(
        self,
        query: str,
        path: str,
        patterns: List[str],
        max_results: int,
        context_lines: int,
        case_sensitive: bool
    ) -> List[SearchMatch]:
        """
        Führt ripgrep-Suche aus.

        ripgrep JSON output format:
        {"type":"match","data":{"path":{"text":"..."},"lines":{"text":"..."},"line_number":42,...}}
        """
        cmd = [self._rg_path, "--json"]

        # Context lines
        if context_lines > 0:
            cmd.extend(["-C", str(context_lines)])

        # Max count per file (we'll limit total later)
        cmd.extend(["--max-count", str(max_results * 2)])

        # Case sensitivity
        if not case_sensitive:
            cmd.append("-i")

        # File patterns (glob)
        for pattern in patterns:
            if pattern != "*":
                cmd.extend(["-g", pattern])

        # Exclude directories
        for exclude in self.EXCLUDE_DIRS:
            cmd.extend(["-g", f"!{exclude}/**"])

        # Query and path
        cmd.append("--")
        cmd.append(query)
        cmd.append(path)

        logger.debug(f"[code_search] Running: {' '.join(cmd[:10])}...")

        # Execute
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=30.0
        )

        if stderr:
            stderr_text = stderr.decode(errors="replace").strip()
            if stderr_text and "No files were searched" not in stderr_text:
                logger.debug(f"[code_search] rg stderr: {stderr_text[:200]}")

        return self._parse_rg_json(stdout.decode(errors="replace"), path, max_results)

    def _parse_rg_json(self, output: str, base_path: str, max_results: int) -> List[SearchMatch]:
        """Parst ripgrep JSON-Output."""
        matches: List[SearchMatch] = []
        context_buffer: dict = {}  # file_path -> {line_num: content}

        base = Path(base_path)

        for line in output.strip().split("\n"):
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "context":
                # Kontext-Zeile
                ctx_data = data.get("data", {})
                file_path = ctx_data.get("path", {}).get("text", "")
                line_num = ctx_data.get("line_number", 0)
                line_text = ctx_data.get("lines", {}).get("text", "").rstrip("\n\r")

                if file_path not in context_buffer:
                    context_buffer[file_path] = {}
                context_buffer[file_path][line_num] = line_text

            elif msg_type == "match":
                # Match-Zeile
                match_data = data.get("data", {})
                file_path = match_data.get("path", {}).get("text", "")
                line_num = match_data.get("line_number", 0)
                line_text = match_data.get("lines", {}).get("text", "").rstrip("\n\r")

                # Relativen Pfad berechnen
                try:
                    rel_path = str(Path(file_path).relative_to(base))
                except ValueError:
                    rel_path = file_path

                # Kontext extrahieren
                ctx = context_buffer.get(file_path, {})
                before = []
                after = []

                for i in range(line_num - 3, line_num):
                    if i in ctx:
                        before.append(ctx[i])
                for i in range(line_num + 1, line_num + 4):
                    if i in ctx:
                        after.append(ctx[i])

                matches.append(SearchMatch(
                    file_path=rel_path,
                    line_number=line_num,
                    line_content=line_text,
                    context_before=before,
                    context_after=after
                ))

                if len(matches) >= max_results:
                    break

            elif msg_type == "end":
                # File ended - clear context buffer for this file
                end_data = data.get("data", {})
                file_path = end_data.get("path", {}).get("text", "")
                context_buffer.pop(file_path, None)

        return matches

    async def _search_grep(
        self,
        query: str,
        path: str,
        patterns: List[str],
        max_results: int,
        context_lines: int,
        case_sensitive: bool
    ) -> List[SearchMatch]:
        """
        Fallback-Suche mit GNU grep + find.
        """
        # Build find command
        find_parts = ["find", shlex.quote(path), "-type", "f"]

        # File patterns
        if patterns and patterns != ["*"]:
            find_parts.append("\\(")
            for i, pattern in enumerate(patterns):
                if i > 0:
                    find_parts.append("-o")
                find_parts.extend(["-name", shlex.quote(pattern)])
            find_parts.append("\\)")

        # Exclude directories
        for exclude in self.EXCLUDE_DIRS:
            find_parts.extend(["-not", "-path", f"'*/{exclude}/*'"])

        find_cmd = " ".join(find_parts)

        # Build grep flags
        grep_flags = ["-n", "-H"]  # Line numbers, filename
        if not case_sensitive:
            grep_flags.append("-i")
        if context_lines > 0:
            grep_flags.append(f"-C{context_lines}")

        grep_flags_str = " ".join(grep_flags)

        # Full command with pipe
        full_cmd = (
            f"{find_cmd} 2>/dev/null | "
            f"xargs grep {grep_flags_str} -- {shlex.quote(query)} 2>/dev/null | "
            f"head -{max_results * 10}"
        )

        logger.debug(f"[code_search] Running grep: {full_cmd[:100]}...")

        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=60.0
        )

        return self._parse_grep_output(stdout.decode(errors="replace"), path, max_results)

    def _parse_grep_output(self, output: str, base_path: str, max_results: int) -> List[SearchMatch]:
        """
        Parst GNU grep Output.

        Format mit -n -H -C:
        path/to/file.java-10-context before
        path/to/file.java:11:matching line
        path/to/file.java-12-context after
        --
        """
        matches: List[SearchMatch] = []
        base = Path(base_path)

        current_match: Optional[SearchMatch] = None
        context_mode = "before"

        for line in output.split("\n"):
            if not line:
                continue

            if line == "--":
                # Separator between matches
                if current_match:
                    matches.append(current_match)
                    if len(matches) >= max_results:
                        break
                    current_match = None
                context_mode = "before"
                continue

            # Parse line: file:num:content or file-num-content
            if ":" in line:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    file_path, line_num_str, content = parts[0], parts[1], parts[2]
                    try:
                        line_num = int(line_num_str)

                        # Relativen Pfad
                        try:
                            rel_path = str(Path(file_path).relative_to(base))
                        except ValueError:
                            rel_path = file_path

                        # This is a match line
                        if current_match:
                            matches.append(current_match)
                            if len(matches) >= max_results:
                                break

                        current_match = SearchMatch(
                            file_path=rel_path,
                            line_number=line_num,
                            line_content=content,
                            context_before=[],
                            context_after=[]
                        )
                        context_mode = "after"
                    except ValueError:
                        pass

            elif "-" in line and current_match:
                # Context line (file-num-content)
                parts = line.split("-", 2)
                if len(parts) >= 3:
                    content = parts[2]
                    if context_mode == "before":
                        current_match.context_before.append(content)
                    else:
                        current_match.context_after.append(content)

        # Don't forget last match
        if current_match and len(matches) < max_results:
            matches.append(current_match)

        return matches


# Singleton
_code_search_engine: Optional[CodeSearchEngine] = None


def get_code_search_engine() -> CodeSearchEngine:
    """Returns the singleton CodeSearchEngine instance."""
    global _code_search_engine
    if _code_search_engine is None:
        _code_search_engine = CodeSearchEngine()
    return _code_search_engine
