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

    # Lokaler Tools-Ordner relativ zum Projekt
    LOCAL_TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"

    def __init__(self):
        self._rg_path: Optional[str] = None
        self._grep_path: Optional[str] = None
        self._checked = False

    async def _check_tools(self) -> None:
        """Prüft verfügbare Such-Tools (einmalig).

        Sucht zuerst im lokalen tools/-Ordner, dann im System-PATH.
        """
        if self._checked:
            return

        # Check ripgrep - erst lokal, dann PATH
        local_rg = self.LOCAL_TOOLS_DIR / ("rg.exe" if shutil.os.name == "nt" else "rg")
        if local_rg.exists():
            self._rg_path = str(local_rg)
            logger.info(f"[code_search] ripgrep found (local): {self._rg_path}")
        else:
            self._rg_path = shutil.which("rg")
            if self._rg_path:
                logger.debug(f"[code_search] ripgrep found (PATH): {self._rg_path}")

        # Check grep - erst lokal, dann PATH
        local_grep = self.LOCAL_TOOLS_DIR / ("grep.exe" if shutil.os.name == "nt" else "grep")
        if local_grep.exists():
            self._grep_path = str(local_grep)
            logger.info(f"[code_search] grep found (local): {self._grep_path}")
        else:
            self._grep_path = shutil.which("grep")
            if self._grep_path:
                logger.debug(f"[code_search] grep found (PATH): {self._grep_path}")

        self._checked = True

        if not self._rg_path and not self._grep_path:
            logger.warning(f"[code_search] Weder ripgrep noch grep gefunden! (Lokaler Ordner: {self.LOCAL_TOOLS_DIR})")

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
        Fallback-Suche mit GNU grep (rekursiv).

        Verwendet grep -r direkt statt find+xargs für bessere Windows-Kompatibilität.
        """
        # Build grep command
        cmd = [self._grep_path, "-r", "-n", "-H"]  # recursive, line numbers, filename

        if not case_sensitive:
            cmd.append("-i")

        if context_lines > 0:
            cmd.append(f"-C{context_lines}")

        # File patterns (--include)
        if patterns and patterns != ["*"]:
            for pattern in patterns:
                cmd.append(f"--include={pattern}")

        # Exclude directories
        for exclude in self.EXCLUDE_DIRS:
            cmd.append(f"--exclude-dir={exclude}")

        # Query and path
        cmd.append("--")
        cmd.append(query)
        cmd.append(path)

        logger.debug(f"[code_search] Running: {' '.join(cmd[:8])}...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=60.0
            )

            if stderr:
                stderr_text = stderr.decode(errors="replace").strip()
                if stderr_text:
                    logger.debug(f"[code_search] grep stderr: {stderr_text[:200]}")

        except asyncio.TimeoutError:
            logger.warning("[code_search] grep timed out after 60s")
            return []

        return self._parse_grep_output(stdout.decode(errors="replace"), path, max_results)

    def _parse_grep_output(self, output: str, base_path: str, max_results: int) -> List[SearchMatch]:
        """
        Parst GNU grep Output.

        Format mit -n -H -C:
        path/to/file.java-10-context before
        path/to/file.java:11:matching line
        path/to/file.java-12-context after
        --

        Windows format (with drive letter):
        C:/path/to/file.java:11:matching line
        C:/path/to/file.java-10-context before
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
            # Handle Windows paths with drive letter (C:/path/file.py:42:content)
            file_path = None
            line_num_str = None
            content = None
            is_match_line = False

            # Check for Windows drive letter (e.g., "C:" at start)
            if len(line) > 2 and line[1] == ':' and line[0].isalpha():
                # Windows path - parse after drive letter
                rest = line[2:]  # Everything after "C:"

                # Try match line first (file:num:content)
                if ':' in rest:
                    parts = rest.split(":", 2)
                    if len(parts) >= 3:
                        try:
                            line_num = int(parts[1])
                            file_path = line[0:2] + parts[0]  # "C:" + "/path/file.py"
                            line_num_str = parts[1]
                            content = parts[2]
                            is_match_line = True
                        except ValueError:
                            pass

                # Try context line (file-num-content)
                if not is_match_line and '-' in rest and current_match:
                    parts = rest.split("-", 2)
                    if len(parts) >= 3:
                        try:
                            int(parts[1])  # Validate it's a line number
                            content = parts[2]
                            if context_mode == "before":
                                current_match.context_before.append(content)
                            else:
                                current_match.context_after.append(content)
                        except ValueError:
                            pass
                    continue

            elif ":" in line:
                # Unix path - match line
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    file_path = parts[0]
                    line_num_str = parts[1]
                    content = parts[2]
                    is_match_line = True

            elif "-" in line and current_match:
                # Unix path - context line
                parts = line.split("-", 2)
                if len(parts) >= 3:
                    try:
                        int(parts[1])  # Validate it's a line number
                        content = parts[2]
                        if context_mode == "before":
                            current_match.context_before.append(content)
                        else:
                            current_match.context_after.append(content)
                    except ValueError:
                        pass
                continue

            # Process match line
            if is_match_line and file_path and line_num_str and content is not None:
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
