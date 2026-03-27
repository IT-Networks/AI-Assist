"""
Agent Tools - Tool-Definitionen für den Agent Orchestrator.

Tools sind Funktionen die das LLM aufrufen kann um:
- Code zu durchsuchen
- Handbuch/Skills zu durchsuchen
- Dateien zu lesen/schreiben
- Confluence/PDFs zu durchsuchen
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable
import json
import logging

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    """Kategorien von Tools."""
    SEARCH = "search"
    FILE = "file"
    KNOWLEDGE = "knowledge"
    ANALYSIS = "analysis"
    DEVOPS = "devops"  # Jenkins, GitHub, CI/CD


@dataclass
class ToolParameter:
    """Parameter-Definition für ein Tool."""
    name: str
    type: str  # string, integer, number, boolean, array, object
    description: str
    required: bool = True
    default: Optional[Any] = None
    enum: Optional[List[str]] = None


@dataclass
class ToolResult:
    """Ergebnis einer Tool-Ausführung."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_data: Optional[Dict] = None  # Für Diff-Preview etc.

    def to_context(self) -> str:
        """Konvertiert das Ergebnis in einen String für den LLM-Kontext."""
        if self.error:
            return f"[Fehler] {self.error}"
        if isinstance(self.data, str):
            return self.data
        return json.dumps(self.data, ensure_ascii=False, indent=2)


@dataclass
class Tool:
    """
    Definition eines Tools das vom LLM aufgerufen werden kann.
    """
    name: str
    description: str
    category: ToolCategory
    parameters: List[ToolParameter] = field(default_factory=list)
    is_write_operation: bool = False  # Benötigt User-Bestätigung
    handler: Optional[Callable[..., Awaitable[ToolResult]]] = None

    def to_openai_schema(self) -> Dict:
        """Konvertiert das Tool in das OpenAI Function-Calling Schema."""
        properties = {}
        required = []

        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description,
            }
            # OpenAI requires 'items' schema for array types
            if param.type == "array":
                prop["items"] = {"type": "string"}
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default

            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }


# ══════════════════════════════════════════════════════════════════════════════
# ToolDefinition - Kompatibilitäts-Wrapper für dict-basierte Parameter
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolDefinition:
    """
    Kompatibilitäts-Wrapper für Tools mit dict-basierten Parametern.

    Verwendung für Tools die Parameter als Dict definieren:
        ToolDefinition(
            name="my_tool",
            description="...",
            parameters={
                "param1": {"type": "string", "description": "...", "required": True}
            },
            handler=my_handler
        )

    Wird intern zu einem Tool mit ToolParameter-Liste konvertiert.
    """
    name: str
    description: str
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    handler: Optional[Callable[..., Awaitable[ToolResult]]] = None
    is_write_operation: bool = False
    category: ToolCategory = ToolCategory.DEVOPS

    def to_tool(self) -> Tool:
        """Konvertiert zu einem Standard-Tool."""
        params = []
        for param_name, param_def in self.parameters.items():
            params.append(ToolParameter(
                name=param_name,
                type=param_def.get("type", "string"),
                description=param_def.get("description", ""),
                required=param_def.get("required", False),
                default=param_def.get("default"),
                enum=param_def.get("enum"),
            ))
        return Tool(
            name=self.name,
            description=self.description,
            category=self.category,
            parameters=params,
            is_write_operation=self.is_write_operation,
            handler=self.handler,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Tool Registry
# ══════════════════════════════════════════════════════════════════════════════

class ToolRegistry:
    """
    Registry für alle verfügbaren Tools.
    Verwaltet Tool-Definitionen und deren Ausführung.
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    @property
    def tools(self) -> Dict[str, Tool]:
        """Gibt alle registrierten Tools zurück."""
        return self._tools

    def register(self, tool: "Tool | ToolDefinition") -> None:
        """Registriert ein Tool. Akzeptiert Tool oder ToolDefinition."""
        if isinstance(tool, ToolDefinition):
            tool = tool.to_tool()
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Gibt ein Tool zurück."""
        return self._tools.get(name)

    def list_tools(self, category: Optional[ToolCategory] = None) -> List[Tool]:
        """Listet alle Tools auf, optional gefiltert nach Kategorie."""
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def get_openai_schemas(self, include_write_ops: bool = False) -> List[Dict]:
        """
        Gibt alle Tool-Definitionen im OpenAI-Format zurück.

        Args:
            include_write_ops: Wenn False, werden Schreib-Tools ausgeschlossen
        """
        schemas = []
        for tool in self._tools.values():
            if not include_write_ops and tool.is_write_operation:
                continue
            schemas.append(tool.to_openai_schema())
        return schemas

    async def execute(self, name: str, **kwargs) -> ToolResult:
        """
        Führt ein Tool aus.

        Args:
            name: Name des Tools
            **kwargs: Parameter für das Tool
        """
        # Entferne 'name' aus kwargs falls vorhanden (verhindert "multiple values" Fehler
        # wenn Text-Parser 'name' fälschlicherweise in arguments einfügt)
        kwargs.pop("name", None)

        # Prüfe auf JSON-Parse-Fehler (vom Orchestrator gesetzt)
        if "__parse_error__" in kwargs:
            parse_error = kwargs.pop("__parse_error__")
            raw_args = kwargs.pop("__raw_args__", "")
            return ToolResult(
                success=False,
                error=(
                    f"Tool '{name}': JSON-Parsing der Argumente fehlgeschlagen: {parse_error}\n"
                    f"Rohes Argument: {raw_args[:200]}...\n"
                    f"Bitte korrigiere die JSON-Syntax und rufe das Tool erneut auf."
                )
            )

        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Unbekanntes Tool: {name}")

        if not tool.handler:
            return ToolResult(success=False, error=f"Tool {name} hat keinen Handler")

        # Required-Parameter prüfen bevor der Handler aufgerufen wird.
        # Verhindert dass Python-TypeErrors als kryptische Fehlermeldungen beim LLM ankommen.
        missing = [p.name for p in tool.parameters if p.required and p.name not in kwargs]
        if missing:
            # Spezielle Hilfe für edit_file
            extra_hint = ""
            if name == "edit_file":
                extra_hint = (
                    "\n\nHINWEIS für edit_file: Du musst ALLE Parameter angeben:\n"
                    "- path: Pfad zur Datei\n"
                    "- old_string: Der EXAKTE Text der ersetzt werden soll (kopiere aus read_file!)\n"
                    "- new_string: Der neue Text\n"
                    "WICHTIG: Lies die Datei zuerst mit read_file um den exakten old_string zu bekommen!"
                )
            return ToolResult(
                success=False,
                error=(
                    f"Tool '{name}': Pflichtparameter fehlen: {', '.join(missing)}. "
                    f"Bitte erneut aufrufen und alle Pflichtparameter angeben.{extra_hint}"
                )
            )

        try:
            return await tool.handler(**kwargs)
        except Exception as e:
            return ToolResult(success=False, error=f"Fehler bei {name}: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# Tool Implementations
# ══════════════════════════════════════════════════════════════════════════════

async def get_project_paths() -> ToolResult:
    """
    Zeigt alle konfigurierten Projekt-Pfade (Java, Python, file_operations).
    Diese Pfade können für Dateioperationen und Code-Suche verwendet werden.
    """
    from app.core.config import settings
    from pathlib import Path

    output = "=== Konfigurierte Projekt-Pfade ===\n\n"

    # Java-Repos
    java_paths = settings.java.get_all_paths()
    if java_paths:
        output += "📁 Java-Repositories:\n"
        for path in java_paths:
            exists = "✓" if Path(path).exists() else "✗ (nicht erreichbar)"
            output += f"   • {path} {exists}\n"
    else:
        output += "📁 Java-Repositories: nicht konfiguriert\n"

    # Python-Repos
    python_paths = settings.python.get_all_paths()
    if python_paths:
        output += "\n📁 Python-Repositories:\n"
        for path in python_paths:
            exists = "✓" if Path(path).exists() else "✗ (nicht erreichbar)"
            output += f"   • {path} {exists}\n"
    else:
        output += "\n📁 Python-Repositories: nicht konfiguriert\n"

    # file_operations.allowed_paths (für Schreiboperationen)
    allowed_paths = settings.file_operations.allowed_paths
    if allowed_paths:
        output += "\n📁 Schreib-Pfade (file_operations):\n"
        for path in allowed_paths:
            exists = "✓" if Path(path).exists() else "✗ (nicht erreichbar)"
            output += f"   • {path} {exists}\n"

    output += "\n--- Verwendung ---\n"
    output += "• search_code: Durchsucht alle Java/Python-Repos\n"
    output += "• read_file/edit_file: Absoluter Pfad innerhalb der Projekt-Verzeichnisse\n"
    output += "• Pfade aus search_code Ergebnissen direkt mit read_file verwenden\n"

    return ToolResult(success=True, data=output)


async def search_code(
    query: str,
    language: str = "all",
    max_results: int = 20,
    context_lines: int = 2,
    case_sensitive: bool = False,
    file_pattern: str = "",
    subpath: str = "",
    read_files: bool = False
) -> ToolResult:
    """
    Durchsucht ALLE konfigurierten Code-Repositories mit ripgrep (rg) oder grep.

    Verwendet ripgrep als primäres Such-Tool mit GNU grep als Fallback.
    Keine Index-Abhängigkeit - durchsucht Dateien direkt.
    """
    from app.core.config import settings
    from app.services.code_search import get_code_search_engine
    from pathlib import Path
    import logging

    logger = logging.getLogger(__name__)

    # Alle Repo-Pfade sammeln
    search_paths = []

    # Java-Repos
    if language in ("all", "java", "sql"):
        for path in settings.java.get_all_paths():
            if Path(path).exists():
                search_paths.append(("Java", path))

    # Python-Repos
    if language in ("all", "python"):
        for path in settings.python.get_all_paths():
            if Path(path).exists():
                search_paths.append(("Python", path))

    if not search_paths:
        return ToolResult(
            success=False,
            error="Keine Repositories konfiguriert. Nutze get_project_paths für verfügbare Pfade."
        )

    try:
        engine = get_code_search_engine()
        all_matches = []
        total_duration = 0.0
        tool_used = ""
        searched_repos = []

        # Alle Repos durchsuchen
        for repo_type, base_path in search_paths:
            try:
                matches, tool, duration = await engine.search(
                    query=query,
                    base_path=base_path,
                    language=language,
                    file_pattern=file_pattern,
                    max_results=max_results,
                    context_lines=context_lines,
                    case_sensitive=case_sensitive,
                    subpath=subpath
                )
                all_matches.extend(matches)
                total_duration += duration
                tool_used = tool
                searched_repos.append(f"{repo_type}: {base_path}")
            except Exception as e:
                logger.warning(f"[search_code] Fehler bei {base_path}: {e}")

        if not all_matches:
            repos_info = ", ".join(searched_repos)
            return ToolResult(
                success=True,
                data=f"Keine Treffer für '{query}' in {len(search_paths)} Repos (via {tool_used}, {total_duration:.2f}s)\nDurchsucht: {repos_info}"
            )

        # Auf max_results begrenzen
        all_matches = all_matches[:max_results]

        # Formatieren (wie Claude Code Grep Output)
        output = f"Gefunden: {len(all_matches)} Treffer für '{query}' in {len(search_paths)} Repos\n"
        output += f"(via {tool_used}, {total_duration:.2f}s)\n\n"

        current_file = None
        for match in all_matches:
            # Datei-Header wenn neue Datei
            if match.file_path != current_file:
                current_file = match.file_path
                output += f"── {match.file_path} ──\n"
                output += f"  → read_file(path=\"{match.file_path}\")\n"

            # Zeilen mit Kontext
            line_num_width = len(str(match.line_number + context_lines))

            for i, ctx_line in enumerate(match.context_before):
                ctx_num = match.line_number - len(match.context_before) + i
                output += f"  {ctx_num:>{line_num_width}}│ {ctx_line}\n"

            # Match-Zeile hervorheben
            output += f"  {match.line_number:>{line_num_width}}│ {match.line_content}  ◀━━\n"

            for i, ctx_line in enumerate(match.context_after):
                ctx_num = match.line_number + 1 + i
                output += f"  {ctx_num:>{line_num_width}}│ {ctx_line}\n"

            output += "\n"

        # Dateiinhalt lesen wenn gewünscht
        if read_files and all_matches:
            unique_files = list(dict.fromkeys(m.file_path for m in all_matches))[:3]
            output += "\n=== Dateiinhalte ===\n"
            for file_path in unique_files:
                try:
                    # Vollständiger Pfad ist bereits im match enthalten
                    full_path = Path(file_path)
                    if full_path.exists():
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        ext = full_path.suffix.lstrip(".")
                        char_count = len(content)
                        output += f"\n[{file_path}] ({char_count:,} Zeichen)\n"
                        output += f"```{ext}\n{content}\n```\n"
                except Exception as e:
                    output += f"\n[{file_path}] Fehler: {e}\n"

        return ToolResult(success=True, data=output)

    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"[search_code] Error: {e}")
        return ToolResult(success=False, error=f"Suchfehler: {e}")


async def search_handbook(
    query: str,
    service_filter: Optional[str] = None,
    top_k: int = 5
) -> ToolResult:
    """Durchsucht das Handbuch nach relevanten Seiten."""
    from app.core.config import settings

    if not settings.handbook.enabled:
        return ToolResult(success=False, error="Handbuch ist nicht aktiviert")

    try:
        from app.services.handbook_indexer import get_handbook_indexer
        indexer = get_handbook_indexer()

        if not indexer.is_built():
            return ToolResult(success=False, error="Handbuch-Index nicht aufgebaut")

        results = indexer.search(query, service_filter=service_filter, top_k=top_k)

        if not results:
            return ToolResult(success=True, data="Keine relevanten Handbuch-Seiten gefunden.")

        output = f"Gefundene Handbuch-Seiten für '{query}':\n\n"
        for r in results:
            output += f"[{r['service_name'] or 'Allgemein'}] {r['title']}\n"
            output += f"  Pfad: {r['file_path']}\n"
            output += f"  {r['snippet']}\n\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def search_skills(
    query: str,
    skill_ids: Optional[List[str]] = None,
    top_k: int = 5
) -> ToolResult:
    """Durchsucht die Wissensbasen von Skills."""
    from app.core.config import settings

    if not settings.skills.enabled:
        return ToolResult(success=False, error="Skills sind nicht aktiviert")

    try:
        from app.services.skill_manager import get_skill_manager
        manager = get_skill_manager()

        results = manager.search_knowledge(query, skill_ids=skill_ids, top_k=top_k)

        if not results:
            return ToolResult(success=True, data="Keine relevanten Skill-Inhalte gefunden.")

        output = f"Gefundenes Wissen für '{query}':\n\n"
        for r in results:
            output += f"[Skill: {r.skill_name}]\n"
            output += f"  {r.snippet}\n\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def read_file(
    path: str,
    encoding: str = "utf-8",
    offset: int = 0,
    limit: int = 0,
    show_line_numbers: bool = True
) -> ToolResult:
    """
    Liest den Inhalt einer Datei (wie Claude Code Read Tool).

    Args:
        path: Pfad zur Datei (absolut oder relativ)
        encoding: Encoding (default: utf-8)
        offset: Startzeile, 1-basiert (0 = Anfang)
        limit: Max Zeilen (0 = unbegrenzt, empfohlen: 500 für große Dateien)
        show_line_numbers: Zeilennummern im Output anzeigen
    """
    import os
    import re
    from app.core.config import settings
    from pathlib import Path

    # Normalisiere Pfad (entferne doppelte Slashes, etc.)
    path = path.strip()
    if not path:
        return ToolResult(success=False, error="Kein Pfad angegeben")

    # Path Traversal Protection: Blockiere verdaechtige Muster
    # (Defense in Depth - zusaetzlich zur resolve()-Pruefung)
    if ".." in path or path.startswith("~"):
        # Pruefe ob nach resolve() der Pfad noch ok ist
        try:
            test_path = Path(path).resolve()
            # Stelle sicher dass wir in einem erlaubten Verzeichnis sind
            allowed_roots = settings.java.get_all_paths() + settings.python.get_all_paths()
            allowed_roots.append(os.getcwd())

            is_safe = any(
                str(test_path).startswith(str(Path(root).resolve()))
                for root in allowed_roots if root
            )
            if not is_safe:
                logger.warning(f"[read_file] Path traversal blocked: {path} -> {test_path}")
                return ToolResult(
                    success=False,
                    error=f"Pfad-Traversal nicht erlaubt: {path}"
                )
        except Exception as e:
            logger.warning(f"[read_file] Path resolution failed: {path} - {e}")
            return ToolResult(success=False, error=f"Ungueltiger Pfad: {path}")

    # Stacktrace-Zeilennummer extrahieren (z.B. "MyClass.java:42" → Zeile 42)
    stacktrace_line = None
    stacktrace_match = re.match(r'^(.+?):(\d+)$', path)
    if stacktrace_match:
        path = stacktrace_match.group(1)
        stacktrace_line = int(stacktrace_match.group(2))
        # Wenn keine explizite offset/limit angegeben, zeige Kontext um die Stacktrace-Zeile
        if offset == 0 and limit == 0:
            offset = max(1, stacktrace_line - 10)  # 10 Zeilen vor der Fehlerzeile
            limit = 25  # 25 Zeilen Kontext

    # Versuche den Pfad aufzulösen
    resolved_path = None

    # 1. Wenn absoluter Pfad, direkt verwenden
    p = Path(path)
    if p.is_absolute():
        if p.exists():
            resolved_path = str(p)
        else:
            return ToolResult(success=False, error=f"Datei nicht gefunden: {path}")
    else:
        # 2. Relativer Pfad - versuche alle konfigurierten Repo-Verzeichnisse
        search_bases = []

        # Alle Java-Repos
        for repo_path in settings.java.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        # Alle Python-Repos
        for repo_path in settings.python.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        # Aktuelles Arbeitsverzeichnis als Fallback
        search_bases.append(Path(os.getcwd()))

        # Durch alle Basis-Verzeichnisse suchen
        for base in search_bases:
            full_path = base / path
            if full_path.exists():
                resolved_path = str(full_path.resolve())
                break

        # 3. Falls nicht gefunden: Suche nach Dateiname in Repos (für Stacktrace-Pfade wie com/example/MyClass.java)
        if not resolved_path:
            filename = p.name  # Nur der Dateiname
            for base in search_bases:
                if not base.exists():
                    continue
                # Suche rekursiv nach dem Dateinamen
                matches = list(base.rglob(filename))
                if matches:
                    # Bei mehreren Treffern: bevorzuge den mit passendem Pfad-Suffix
                    path_parts = str(path).replace("\\", "/").split("/")
                    for match in matches:
                        match_str = str(match).replace("\\", "/")
                        # Prüfe ob der Pfad-Suffix passt (z.B. com/example/MyClass.java)
                        if match_str.endswith("/".join(path_parts[-3:])) or match_str.endswith("/".join(path_parts[-2:])):
                            resolved_path = str(match.resolve())
                            break
                    # Fallback: ersten Treffer nehmen
                    if not resolved_path and matches:
                        resolved_path = str(matches[0].resolve())
                        break

        # Wenn nicht gefunden, auch ohne Basis probieren (für lokale Dateien)
        if not resolved_path and p.exists():
            resolved_path = str(p.resolve())

        if not resolved_path:
            # Zeige Hinweis wo gesucht wurde
            searched = ", ".join(str(b) for b in search_bases[:3])
            hint = ""
            if "/" in path or "\\" in path:
                hint = "\nTIPP: Bei Stacktrace-Pfaden (z.B. 'com/example/MyClass.java') wird rekursiv im Repository gesucht."
            return ToolResult(
                success=False,
                error=f"Datei nicht gefunden: {path}\nGesucht in: {searched}{hint}"
            )

    try:
        file_path = Path(resolved_path)
        if not file_path.exists():
            return ToolResult(success=False, error=f"Datei nicht gefunden: {path}")

        content = file_path.read_text(encoding=encoding, errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)

        # Offset und Limit anwenden
        start_line = max(0, offset - 1) if offset > 0 else 0  # 1-basiert zu 0-basiert
        if limit > 0:
            end_line = min(start_line + limit, total_lines)
        else:
            end_line = total_lines

        selected_lines = lines[start_line:end_line]

        # Output formatieren (wie Claude Code)
        if show_line_numbers:
            # Format: "   123→content" mit Tab nach Nummer
            max_line_num = end_line
            num_width = len(str(max_line_num))
            formatted_lines = []
            for i, line in enumerate(selected_lines):
                line_num = start_line + i + 1  # 1-basiert
                # Stacktrace-Zeile hervorheben
                if stacktrace_line and line_num == stacktrace_line:
                    formatted_lines.append(f"{line_num:>{num_width}}→{line}  ◀━━ FEHLER")
                else:
                    formatted_lines.append(f"{line_num:>{num_width}}→{line}")
            output_content = "\n".join(formatted_lines)
        else:
            output_content = "\n".join(selected_lines)

        # Header mit Range-Info
        if stacktrace_line:
            header = f"=== Datei: {path} (Zeilen {start_line + 1}-{end_line} von {total_lines}) [Stacktrace-Kontext um Zeile {stacktrace_line}] ==="
        elif offset > 0 or (limit > 0 and end_line < total_lines):
            header = f"=== Datei: {path} (Zeilen {start_line + 1}-{end_line} von {total_lines}) ==="
        else:
            header = f"=== Datei: {path} ({total_lines} Zeilen) ==="

        result = f"{header}\n{output_content}"

        # Hinweis wenn mehr Zeilen verfügbar
        remaining = total_lines - end_line
        if remaining > 0:
            result += f"\n\n[HINWEIS: {remaining} weitere Zeilen. Nutze offset={end_line + 1} für nächsten Abschnitt]"

        return ToolResult(success=True, data=result)

    except PermissionError as e:
        return ToolResult(success=False, error=f"Zugriff verweigert: {e}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def list_files(
    path: str,
    pattern: str = "*",
    recursive: bool = False
) -> ToolResult:
    """
    Listet Dateien in einem Verzeichnis auf.

    Args:
        path: Pfad zum Verzeichnis (absolut oder relativ)
        pattern: Glob-Pattern für Dateinamen (default: *)
        recursive: Rekursiv suchen
    """
    import os
    from pathlib import Path
    from app.core.config import settings

    # Pfad normalisieren
    path = path.strip()
    if not path:
        return ToolResult(success=False, error="Kein Pfad angegeben")

    # Pfad-Auflösung (analog zu read_file)
    resolved_path = None
    p = Path(path)

    if p.is_absolute():
        if p.exists() and p.is_dir():
            resolved_path = str(p)
        else:
            return ToolResult(success=False, error=f"Verzeichnis nicht gefunden: {path}")
    else:
        # Relativer Pfad - in konfigurierten Verzeichnissen suchen
        search_bases = []

        for repo_path in settings.java.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        for repo_path in settings.python.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        search_bases.append(Path(os.getcwd()))

        # Suche existierendes Verzeichnis
        for base in search_bases:
            full_path = base / path
            if full_path.exists() and full_path.is_dir():
                resolved_path = str(full_path.resolve())
                break

        # Fallback: "." oder leerer Pfad → erstes Repo-Verzeichnis
        if not resolved_path and path in (".", ""):
            for base in search_bases:
                if base.exists() and base.is_dir():
                    resolved_path = str(base.resolve())
                    break

        if not resolved_path:
            searched = ", ".join(str(b) for b in search_bases[:3])
            return ToolResult(
                success=False,
                error=f"Verzeichnis nicht gefunden: {path}\nGesucht in: {searched}"
            )

    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        files = await manager.list_files(resolved_path, pattern=pattern, recursive=recursive)

        if not files:
            return ToolResult(success=True, data=f"Keine Dateien gefunden in {resolved_path}")

        output = f"Dateien in {resolved_path}:\n"
        for f in files[:50]:  # Max 50 Dateien
            output += f"  {f}\n"

        if len(files) > 50:
            output += f"\n  ... und {len(files) - 50} weitere"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def glob_files(
    pattern: str,
    path: str = ".",
    sort_by: str = "mtime",
    max_results: int = 100
) -> ToolResult:
    """
    Sucht Dateien nach Glob-Pattern (wie Claude Code Glob Tool).
    Durchsucht alle konfigurierten Repos wenn path="." ist.
    """
    from app.core.config import settings
    from pathlib import Path as PyPath

    # Basis-Pfad auflösen
    base_path = path
    resolved_from = None  # Track woher der Pfad aufgelöst wurde

    if not PyPath(path).is_absolute():
        # Alle konfigurierten Repo-Pfade sammeln
        all_paths = settings.java.get_all_paths() + settings.python.get_all_paths()

        # Wenn path != ".", prüfe ob Unterverzeichnis in einem Repo existiert
        if path != ".":
            for repo_path in all_paths:
                if repo_path and (PyPath(repo_path) / path).exists():
                    base_path = str(PyPath(repo_path) / path)
                    resolved_from = repo_path
                    break
        else:
            # Standard: Erstes verfügbares Repo
            for repo_path in all_paths:
                if repo_path and PyPath(repo_path).exists():
                    base_path = repo_path
                    resolved_from = repo_path
                    break

    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        results = await manager.glob_files(pattern, base_path, sort_by, max_results)

        if not results:
            hint = f" (gesucht in: {resolved_from})" if resolved_from else ""
            return ToolResult(success=True, data=f"Keine Dateien gefunden für Pattern '{pattern}'{hint}")

        # Formatieren (wie Claude Code)
        output = f"Gefunden: {len(results)} Dateien für '{pattern}'\n\n"
        for i, r in enumerate(results, 1):
            size_str = f"{r.size_bytes / 1024:.1f} KB" if r.size_bytes > 1024 else f"{r.size_bytes} B"
            date_str = r.modified.strftime("%Y-%m-%d %H:%M")
            output += f"  {i:3}. {r.path:<50} ({size_str}, {date_str})\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def grep_content(
    pattern: str,
    path: str = ".",
    file_pattern: str = "*",
    context_lines: int = 2,
    max_results: int = 50,
    case_sensitive: bool = False
) -> ToolResult:
    """
    Durchsucht Dateiinhalte nach Text/Regex mit ripgrep/grep.

    Verwendet ripgrep als primäres Such-Tool mit GNU grep als Fallback.
    Funktioniert ohne Index - durchsucht Dateien direkt.
    """
    from app.core.config import settings
    from app.services.code_search import get_code_search_engine
    from pathlib import Path as PyPath
    import logging

    logger = logging.getLogger(__name__)

    # Basis-Pfad auflösen
    base_path = path
    resolved_from = None
    subpath = ""

    if not PyPath(path).is_absolute():
        # Alle konfigurierten Repo-Pfade sammeln
        all_paths = settings.java.get_all_paths() + settings.python.get_all_paths()

        # Wenn path != ".", prüfe ob Unterverzeichnis in einem Repo existiert
        if path != ".":
            for repo_path in all_paths:
                if repo_path and (PyPath(repo_path) / path).exists():
                    base_path = repo_path
                    subpath = path
                    resolved_from = f"{repo_path}/{path}"
                    break
            else:
                # Direkter Pfad versuchen
                if PyPath(path).exists():
                    base_path = path
                    resolved_from = path
        else:
            for repo_path in all_paths:
                if repo_path and PyPath(repo_path).exists():
                    base_path = repo_path
                    resolved_from = repo_path
                    break

    try:
        engine = get_code_search_engine()
        matches, tool_used, duration = await engine.search(
            query=pattern,
            base_path=base_path,
            language="all",
            file_pattern=file_pattern if file_pattern != "*" else "",
            max_results=max_results,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
            subpath=subpath
        )

        if not matches:
            hint = f" (gesucht in: {resolved_from})" if resolved_from else ""
            return ToolResult(
                success=True,
                data=f"Keine Treffer für '{pattern}'{hint} (via {tool_used}, {duration:.2f}s)"
            )

        # Formatieren (wie Claude Code Grep Output)
        repo_hint = f" in {resolved_from}" if resolved_from else ""
        output = f"Gefunden: {len(matches)} Treffer für '{pattern}'{repo_hint}\n"
        output += f"(via {tool_used}, {duration:.2f}s)\n\n"

        current_file = None
        for match in matches:
            # Datei-Header wenn neue Datei
            if match.file_path != current_file:
                current_file = match.file_path
                output += f"── {match.file_path} ──\n"
                output += f"  → read_file(path=\"{match.file_path}\")\n"

            # Zeilen mit Kontext
            line_num_width = len(str(match.line_number + context_lines))

            for i, ctx_line in enumerate(match.context_before):
                ctx_num = match.line_number - len(match.context_before) + i
                output += f"  {ctx_num:>{line_num_width}}│ {ctx_line}\n"

            # Match-Zeile hervorheben
            output += f"  {match.line_number:>{line_num_width}}│ {match.line_content}  ◀━━\n"

            for i, ctx_line in enumerate(match.context_after):
                ctx_num = match.line_number + 1 + i
                output += f"  {ctx_num:>{line_num_width}}│ {ctx_line}\n"

            output += "\n"

        return ToolResult(success=True, data=output)

    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"[grep_content] Error: {e}")
        return ToolResult(success=False, error=f"Grep-Fehler: {e}")


async def write_file(path: str, content: str) -> ToolResult:
    """
    Schreibt Inhalt in eine Datei (benötigt Bestätigung).

    Args:
        path: Pfad zur Datei (absolut oder relativ)
        content: Inhalt der geschrieben werden soll
    """
    import os
    from pathlib import Path
    from app.core.config import settings

    # Pfad normalisieren
    path = path.strip()
    if not path:
        return ToolResult(success=False, error="Kein Pfad angegeben")

    # Pfad-Auflösung (analog zu read_file)
    resolved_path = None
    p = Path(path)

    if p.is_absolute():
        # Absoluter Pfad - direkt verwenden
        resolved_path = str(p)
    else:
        # Relativer Pfad - in konfigurierten Verzeichnissen suchen
        search_bases = []

        # Alle Java-Repos
        for repo_path in settings.java.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        # Alle Python-Repos
        for repo_path in settings.python.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        # Aktuelles Arbeitsverzeichnis als Fallback
        search_bases.append(Path(os.getcwd()))

        # 1. Prüfe ob Datei bereits existiert (für Überschreiben)
        for base in search_bases:
            full_path = base / path
            if full_path.exists():
                resolved_path = str(full_path.resolve())
                break

        # 2. Wenn Datei nicht existiert: nehme erstes gültiges Basis-Verzeichnis für neue Datei
        if not resolved_path:
            for base in search_bases:
                if base.exists() and base.is_dir():
                    # Stelle sicher dass Parent-Verzeichnis existiert oder erstellt werden kann
                    target = base / path
                    resolved_path = str(target.resolve())
                    break

        # 3. Fallback: relativer Pfad vom cwd
        if not resolved_path:
            resolved_path = str((Path(os.getcwd()) / path).resolve())

    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        preview = await manager.write_file(resolved_path, content)

        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Datei wird erstellt/überschrieben: {resolved_path}",
            confirmation_data={
                "operation": "write_file",
                "path": resolved_path,
                "is_new": preview.is_new,
                "diff": preview.diff,
                "content": content,
            }
        )
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def create_directory(path: str) -> ToolResult:
    """
    Erstellt ein Verzeichnis (Ordner).

    Args:
        path: Pfad zum Verzeichnis (absolut oder relativ)
    """
    import os
    from pathlib import Path
    from app.core.config import settings

    # Pfad normalisieren
    path = path.strip()
    if not path:
        return ToolResult(success=False, error="Kein Pfad angegeben")

    # Pfad-Auflösung (analog zu read_file/write_file)
    p = Path(path)

    if not p.is_absolute():
        # Relativer Pfad - erstes gültiges Basis-Verzeichnis verwenden
        search_bases = []

        for repo_path in settings.java.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        for repo_path in settings.python.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        search_bases.append(Path(os.getcwd()))

        # Nehme erstes existierendes Basis-Verzeichnis
        for base in search_bases:
            if base.exists() and base.is_dir():
                path = str((base / path).resolve())
                break

    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        result = await manager.create_directory(path)

        if result["already_existed"]:
            return ToolResult(
                success=True,
                data=f"Verzeichnis existiert bereits: {result['path']}"
            )
        else:
            return ToolResult(
                success=True,
                data=f"Verzeichnis erstellt: {result['path']}"
            )
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False
) -> ToolResult:
    """
    Bearbeitet eine Datei durch String-Ersetzung (benötigt Bestätigung).

    Args:
        path: Pfad zur Datei (absolut oder relativ)
        old_string: Zu ersetzender Text (muss eindeutig sein bei replace_all=False)
        new_string: Neuer Text
        replace_all: Wenn True, werden ALLE Vorkommen ersetzt
    """
    import os
    from pathlib import Path
    from app.core.config import settings

    # Pfad normalisieren
    path = path.strip()
    if not path:
        return ToolResult(success=False, error="Kein Pfad angegeben")

    # Pfad-Auflösung (analog zu read_file)
    resolved_path = None
    p = Path(path)

    if p.is_absolute():
        resolved_path = str(p)
    else:
        # Relativer Pfad - in konfigurierten Verzeichnissen suchen
        search_bases = []

        for repo_path in settings.java.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        for repo_path in settings.python.get_all_paths():
            if repo_path:
                search_bases.append(Path(repo_path))

        search_bases.append(Path(os.getcwd()))

        # Suche existierende Datei
        for base in search_bases:
            full_path = base / path
            if full_path.exists():
                resolved_path = str(full_path.resolve())
                break

        if not resolved_path:
            searched = ", ".join(str(b) for b in search_bases[:3])
            return ToolResult(
                success=False,
                error=f"Datei nicht gefunden: {path}\nGesucht in: {searched}"
            )

    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        preview = await manager.edit_file(resolved_path, old_string, new_string, replace_all)

        # Info über Anzahl der Ersetzungen
        count_info = ""
        if preview.replacements_count > 1:
            count_info = f" ({preview.replacements_count} Ersetzungen)"

        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Datei wird bearbeitet: {resolved_path}{count_info}",
            confirmation_data={
                "operation": "edit_file",
                "path": resolved_path,
                "diff": preview.diff,
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": replace_all,
                "replacements_count": preview.replacements_count,
            }
        )
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def get_service_info(service_id: str) -> ToolResult:
    """Holt detaillierte Informationen zu einem Service aus dem Handbuch."""
    from app.core.config import settings

    if not settings.handbook.enabled:
        return ToolResult(success=False, error="Handbuch ist nicht aktiviert")

    try:
        from app.services.handbook_indexer import get_handbook_indexer
        indexer = get_handbook_indexer()
        service = indexer.get_service_info(service_id)

        if not service:
            return ToolResult(success=False, error=f"Service '{service_id}' nicht gefunden")

        output = f"=== Service: {service['service_name']} ===\n"
        output += f"ID: {service['service_id']}\n"

        if service.get('description'):
            output += f"Beschreibung: {service['description']}\n"

        if service.get('tabs'):
            output += f"\nTabs:\n"
            for tab in service['tabs']:
                output += f"  - {tab['name']} ({tab['file_path']})\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def search_pdf(
    query: str,
    filename_filter: Optional[str] = None,
    top_k: int = 5
) -> ToolResult:
    """Durchsucht hochgeladene PDF-Dokumente."""
    from app.core.config import settings
    from pathlib import Path
    import re

    uploads_dir = Path(settings.uploads.directory)
    if not uploads_dir.exists():
        return ToolResult(success=False, error="Upload-Verzeichnis nicht gefunden")

    # Versuche PyMuPDF, fallback auf pdfplumber
    pdf_reader = None
    reader_type = None

    try:
        import fitz  # PyMuPDF
        reader_type = "pymupdf"
    except ImportError:
        try:
            import pdfplumber
            reader_type = "pdfplumber"
        except ImportError:
            return ToolResult(
                success=False,
                error="Kein PDF-Reader installiert. Bitte 'pip install PyMuPDF' oder 'pip install pdfplumber' ausführen."
            )

    results = []
    query_lower = query.lower()
    query_words = query_lower.split()

    # Alle PDFs durchsuchen
    pdf_files = list(uploads_dir.glob("**/*.pdf"))
    if filename_filter:
        pdf_files = [f for f in pdf_files if filename_filter.lower() in f.name.lower()]

    for pdf_path in pdf_files[:20]:  # Max 20 PDFs durchsuchen
        try:
            file_matches = []

            if reader_type == "pymupdf":
                import fitz
                doc = fitz.open(str(pdf_path))
                for page_num, page in enumerate(doc, 1):
                    text = page.get_text()
                    text_lower = text.lower()

                    if any(word in text_lower for word in query_words):
                        for word in query_words:
                            idx = text_lower.find(word)
                            if idx >= 0:
                                start = max(0, idx - 200)
                                end = min(len(text), idx + 300)
                                snippet = text[start:end].strip()
                                snippet = re.sub(r'\s+', ' ', snippet)
                                file_matches.append({"page": page_num, "snippet": snippet})
                                break
                doc.close()

            else:  # pdfplumber
                import pdfplumber
                with pdfplumber.open(str(pdf_path)) as pdf:
                    for page_num, page in enumerate(pdf.pages, 1):
                        text = page.extract_text() or ""
                        text_lower = text.lower()

                        if any(word in text_lower for word in query_words):
                            for word in query_words:
                                idx = text_lower.find(word)
                                if idx >= 0:
                                    start = max(0, idx - 200)
                                    end = min(len(text), idx + 300)
                                    snippet = text[start:end].strip()
                                    snippet = re.sub(r'\s+', ' ', snippet)
                                    file_matches.append({"page": page_num, "snippet": snippet})
                                    break

            if file_matches:
                results.append({
                    "filename": pdf_path.name,
                    "path": str(pdf_path),
                    "matches": file_matches[:3]
                })

        except Exception as e:
            continue

    if not results:
        return ToolResult(success=True, data=f"Keine relevanten PDF-Inhalte für '{query}' gefunden.")

    # Formatierte Ausgabe
    output = f"Gefundene PDF-Inhalte für '{query}':\n\n"
    for r in results[:top_k]:
        output += f"=== {r['filename']} ===\n"
        for match in r['matches']:
            output += f"[Seite {match['page']}] {match['snippet']}\n\n"

    return ToolResult(success=True, data=output)


async def get_pdf_info(filename: str) -> ToolResult:
    """Gibt Metadaten und Seitenanzahl einer PDF-Datei zurück."""
    from app.core.config import settings
    from pathlib import Path
    from app.services.pdf_reader import PDFReader
    from app.core.exceptions import PDFReadError

    uploads_dir = Path(settings.uploads.directory)
    if not uploads_dir.exists():
        return ToolResult(success=False, error="Upload-Verzeichnis nicht gefunden")

    pdf_files = list(uploads_dir.glob("**/*.pdf"))
    match = next((f for f in pdf_files if filename.lower() in f.name.lower()), None)
    if not match:
        available = ", ".join(f.name for f in pdf_files[:10]) or "keine"
        return ToolResult(success=False, error=f"PDF '{filename}' nicht gefunden. Verfügbare PDFs: {available}")

    try:
        reader = PDFReader()
        meta = reader.get_metadata(str(match))
        output = f"=== PDF-Info: {match.name} ===\n"
        output += f"Seiten: {meta['page_count']}\n"
        if meta.get("title"):
            output += f"Titel: {meta['title']}\n"
        if meta.get("author"):
            output += f"Autor: {meta['author']}\n"
        if meta.get("subject"):
            output += f"Betreff: {meta['subject']}\n"
        output += f"\nDateipfad: {match.name}"
        return ToolResult(success=True, data=output)
    except PDFReadError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        return ToolResult(success=False, error=f"Fehler beim Lesen der PDF-Metadaten: {e}")


async def read_pdf_pages(filename: str, start_page: int, end_page: int) -> ToolResult:
    """Liest einen Seitenbereich einer PDF (1-basiert, inklusiv, max 30 Seiten pro Aufruf)."""
    from app.core.config import settings
    from pathlib import Path
    from app.services.pdf_reader import PDFReader
    from app.core.exceptions import PDFReadError

    uploads_dir = Path(settings.uploads.directory)
    if not uploads_dir.exists():
        return ToolResult(success=False, error="Upload-Verzeichnis nicht gefunden")

    pdf_files = list(uploads_dir.glob("**/*.pdf"))
    match = next((f for f in pdf_files if filename.lower() in f.name.lower()), None)
    if not match:
        available = ", ".join(f.name for f in pdf_files[:10]) or "keine"
        return ToolResult(success=False, error=f"PDF '{filename}' nicht gefunden. Verfügbare PDFs: {available}")

    # Eingabe validieren
    if start_page < 1:
        start_page = 1
    # Hard-Cap: maximal 30 Seiten pro Aufruf
    end_page = min(end_page, start_page + 29)

    try:
        reader = PDFReader()
        page_count = reader.get_page_count(str(match))
        if start_page > page_count:
            return ToolResult(
                success=False,
                error=f"Startseite {start_page} überschreitet Seitenanzahl ({page_count}) der PDF."
            )
        end_page = min(end_page, page_count)

        text = reader.extract_pages(str(match), start_page, end_page)
        header = (
            f"=== {match.name} – Seiten {start_page}–{end_page} "
            f"(von {page_count} gesamt) ===\n\n"
        )
        return ToolResult(success=True, data=header + text)
    except PDFReadError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        return ToolResult(success=False, error=f"Fehler beim Lesen der PDF-Seiten: {e}")


async def trace_java_references(
    class_name: str,
    include_interfaces: bool = True,
    include_parent_classes: bool = True,
    max_depth: int = 5
) -> ToolResult:
    """
    Verfolgt Verweise auf andere Klassen in Java-Code.
    Findet Interfaces, Parent-Klassen und deren Implementierungen.
    """
    from app.core.config import settings
    from pathlib import Path
    import re

    if not settings.java.get_active_path():
        return ToolResult(success=False, error="Java Repository nicht konfiguriert")

    repo_path = Path(settings.java.get_active_path())
    if not repo_path.exists():
        return ToolResult(success=False, error=f"Repository nicht gefunden: {repo_path}")

    found_classes = {}
    visited = set()

    def is_excluded(file_path: Path) -> bool:
        """Prüft ob eine Datei in einem ausgeschlossenen Verzeichnis liegt."""
        path_parts = file_path.parts
        for ex_dir in settings.java.exclude_dirs:
            if ex_dir in path_parts:
                return True
        return False

    def find_java_file(name: str) -> Optional[Path]:
        """Findet eine Java-Datei nach Klassennamen oder Dateipfad."""
        import logging
        logger = logging.getLogger(__name__)

        logger.debug(f"[trace_java] Suche nach: '{name}' in repo: {repo_path}")

        # Prüfe ob es ein Dateipfad ist (enthält / oder \ oder endet mit .java)
        if "/" in name or "\\" in name or name.endswith(".java"):
            # Es ist ein Pfad - Klassennamen extrahieren
            path_obj = Path(name)
            simple_name = path_obj.stem  # z.B. "MyClass" aus "MyClass.java"
            logger.debug(f"[trace_java] Pfad erkannt, Klassenname: {simple_name}")

            # Prüfe ob der exakte Pfad existiert (relativ zum repo)
            exact_path = repo_path / name
            if exact_path.exists():
                logger.debug(f"[trace_java] Exakter Pfad gefunden: {exact_path}")
                return exact_path

            # Sonst nach dem Dateinamen suchen
            for java_file in repo_path.rglob(f"{simple_name}.java"):
                if is_excluded(java_file):
                    logger.debug(f"[trace_java] Übersprungen (excluded): {java_file}")
                    continue
                logger.debug(f"[trace_java] Gefunden: {java_file}")
                return java_file
        else:
            # Klassennamen - einfache oder vollqualifizierte (com.example.MyClass)
            simple_name = name.split(".")[-1]
            logger.debug(f"[trace_java] Klassenname: {simple_name}")

            # Suche mit rglob
            search_pattern = f"**/{simple_name}.java"
            found_files = list(repo_path.glob(search_pattern))
            logger.debug(f"[trace_java] glob '{search_pattern}' fand {len(found_files)} Dateien")

            for java_file in found_files:
                if is_excluded(java_file):
                    logger.debug(f"[trace_java] Übersprungen (excluded): {java_file}")
                    continue
                logger.debug(f"[trace_java] Gefunden: {java_file}")
                return java_file

            # Fallback: rglob mit *simple_name* (falls Dateiname anders geschrieben)
            if not found_files:
                logger.debug(f"[trace_java] Fallback-Suche mit rglob...")
                for java_file in repo_path.rglob("*.java"):
                    if simple_name.lower() in java_file.stem.lower():
                        if is_excluded(java_file):
                            continue
                        logger.debug(f"[trace_java] Fallback gefunden: {java_file}")
                        return java_file

        logger.debug(f"[trace_java] Keine Datei gefunden für: {name}")
        return None

    def extract_class_info(file_path: Path) -> Dict:
        """Extrahiert Klassen-Informationen aus einer Java-Datei."""
        content = file_path.read_text(encoding="utf-8", errors="replace")
        info = {
            "file": str(file_path.relative_to(repo_path)),
            "extends": None,
            "implements": [],
            "content": content[:5000]  # Erste 5000 Zeichen
        }

        # Extends finden
        extends_match = re.search(r'\bextends\s+([\w.]+)', content)
        if extends_match:
            info["extends"] = extends_match.group(1)

        # Implements finden
        implements_match = re.search(r'\bimplements\s+([\w.,\s<>]+?)(?:\s*\{|\s+extends)', content)
        if implements_match:
            implements_str = implements_match.group(1)
            info["implements"] = [i.strip() for i in re.split(r',\s*', implements_str) if i.strip()]

        return info

    def trace_hierarchy(name: str, depth: int = 0):
        """Rekursiv die Klassenhierarchie verfolgen."""
        if depth >= max_depth or name in visited:
            return
        visited.add(name)

        file_path = find_java_file(name)
        if not file_path:
            return

        info = extract_class_info(file_path)
        found_classes[name] = info

        # Parent-Klasse verfolgen
        if include_parent_classes and info["extends"]:
            trace_hierarchy(info["extends"], depth + 1)

        # Interfaces verfolgen
        if include_interfaces:
            for iface in info["implements"]:
                # Generics entfernen
                iface_name = re.sub(r'<.*>', '', iface)
                trace_hierarchy(iface_name, depth + 1)

    # Start-Klasse finden und Hierarchie verfolgen
    trace_hierarchy(class_name)

    if not found_classes:
        return ToolResult(
            success=False,
            error=f"Klasse '{class_name}' nicht im Repository gefunden"
        )

    # Formatierte Ausgabe
    output = f"=== Java-Klassenhierarchie für {class_name} ===\n\n"

    for name, info in found_classes.items():
        output += f"### {name}\n"
        output += f"Datei: {info['file']}\n"
        if info["extends"]:
            output += f"Extends: {info['extends']}\n"
        if info["implements"]:
            output += f"Implements: {', '.join(info['implements'])}\n"
        output += f"\n```java\n{info['content']}\n```\n\n"

    return ToolResult(success=True, data=output)


async def query_database(
    query: str,
    max_rows: int = 100
) -> ToolResult:
    """
    Führt eine SQL-Abfrage auf der DB2-Datenbank aus.
    Nur SELECT-Statements erlaubt (readonly).
    """
    from app.core.config import settings

    if not settings.database.enabled:
        return ToolResult(success=False, error="Datenbank ist nicht aktiviert. Aktiviere database.enabled in config.yaml")

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return ToolResult(success=False, error="DB-Client konnte nicht initialisiert werden. Prüfe die Datenbank-Konfiguration.")

        # Query validieren (nur SELECT erlaubt)
        is_valid, error = client.validate_query(query)
        if not is_valid:
            return ToolResult(success=False, error=error)

        # Query direkt ausführen (keine Bestätigung nötig da nur SELECT)
        effective_max_rows = min(max_rows, settings.database.max_rows)
        original_max = client.max_rows
        client.max_rows = effective_max_rows

        result = await client.execute(query)

        client.max_rows = original_max

        if not result.success:
            return ToolResult(success=False, error=result.error)

        # Formatierte Ausgabe
        output = f"=== Query-Ergebnis ===\n"
        output += f"Query: {query}\n"
        output += f"Zeilen: {result.row_count}"
        if result.truncated:
            output += f" (begrenzt auf {effective_max_rows})"
        output += "\n\n"

        if result.columns and result.rows:
            # Header
            output += " | ".join(result.columns) + "\n"
            output += "-" * (len(" | ".join(result.columns))) + "\n"

            # Rows
            for row in result.rows:
                output += " | ".join(str(v) if v is not None else "NULL" for v in row) + "\n"
        elif result.row_count == 0:
            output += "(Keine Ergebnisse)\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def execute_confirmed_query(query: str, max_rows: int = 100) -> ToolResult:
    """
    Führt eine bestätigte Datenbank-Abfrage aus.
    Wird nur nach User-Bestätigung aufgerufen.
    """
    from app.core.config import settings

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return ToolResult(success=False, error="DB-Client nicht verfügbar")

        # Überschreibe max_rows temporär
        original_max = client.max_rows
        client.max_rows = min(max_rows, settings.database.max_rows)

        result = await client.execute(query)

        client.max_rows = original_max

        if not result.success:
            return ToolResult(success=False, error=result.error)

        # Formatierte Ausgabe
        output = f"=== Query-Ergebnis ===\n"
        output += f"Zeilen: {result.row_count}"
        if result.truncated:
            output += f" (begrenzt auf {client.max_rows})"
        output += "\n\n"

        if result.columns and result.rows:
            # Header
            output += " | ".join(result.columns) + "\n"
            output += "-" * (len(" | ".join(result.columns))) + "\n"

            # Rows
            for row in result.rows:
                output += " | ".join(str(v) if v is not None else "NULL" for v in row) + "\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def list_database_tables(schema: str = None) -> ToolResult:
    """Listet verfügbare Tabellen in der DB2-Datenbank auf."""
    from app.core.config import settings

    if not settings.database.enabled:
        return ToolResult(success=False, error="Datenbank ist nicht aktiviert")

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return ToolResult(success=False, error="DB-Client nicht verfügbar")

        effective_schema = schema or client.schema
        tables = await client.get_tables(schema)

        if not tables:
            hint = "Kein Schema konfiguriert. " if not effective_schema else ""
            return ToolResult(
                success=True,
                data=f"Keine Tabellen gefunden. {hint}Versuche: query_database(query=\"SELECT DISTINCT TABSCHEMA FROM SYSCAT.TABLES WHERE TYPE='T' FETCH FIRST 20 ROWS ONLY\")"
            )

        if effective_schema:
            output = f"=== Tabellen im Schema {effective_schema} ===\n\n"
        else:
            output = f"=== Tabellen (alle Schemas) ===\n\n"

        for table in tables:
            output += f"  - {table}\n"

        output += f"\n({len(tables)} Tabellen gefunden)"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def describe_database_table(table_name: str, schema: str = None) -> ToolResult:
    """Beschreibt die Struktur einer DB2-Tabelle."""
    from app.core.config import settings

    if not settings.database.enabled:
        return ToolResult(success=False, error="Datenbank ist nicht aktiviert")

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return ToolResult(success=False, error="DB-Client nicht verfügbar")

        info = await client.describe_table(table_name, schema)

        if "error" in info:
            return ToolResult(success=False, error=info["error"])

        output = f"=== Tabelle: {info['schema']}.{info['table']} ===\n\n"
        output += "Spalten:\n"

        for col in info["columns"]:
            nullable = "NULL" if col["nullable"] else "NOT NULL"
            type_str = col["type"]
            if col["length"]:
                type_str += f"({col['length']}"
                if col["scale"]:
                    type_str += f",{col['scale']}"
                type_str += ")"
            default = f" DEFAULT {col['default']}" if col["default"] else ""
            output += f"  {col['name']:30} {type_str:20} {nullable}{default}\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# SQLJ & Java Debug Tools
# ══════════════════════════════════════════════════════════════════════════════

async def read_sqlj_file(path: str) -> ToolResult:
    """
    Liest eine SQLJ-Datei und extrahiert alle SQL-Statements mit Methoden-Kontext.
    Gibt strukturierte Liste aller #sql { ... } Blöcke zurück.
    """
    import re
    from pathlib import Path
    from app.core.config import settings

    # Pfad auflösen
    file_path = Path(path)
    if not file_path.is_absolute():
        for base in [settings.java.get_active_path(), "."]:
            candidate = Path(base) / path
            if candidate.exists():
                file_path = candidate
                break

    if not file_path.exists():
        return ToolResult(success=False, error=f"SQLJ-Datei nicht gefunden: {path}")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolResult(success=False, error=f"Lesefehler: {e}")

    lines = content.splitlines()
    output = f"=== SQLJ-Datei: {file_path.name} ===\n\n"

    # SQL-Blöcke extrahieren: #sql [optionalCtx] { ... };
    sql_pattern = re.compile(
        r'#sql\s*(?:\[\s*\w+\s*\])?\s*\{(.*?)\}\s*;',
        re.DOTALL | re.IGNORECASE
    )
    matches = list(sql_pattern.finditer(content))

    if not matches:
        output += "Keine SQL-Statements (#sql { ... }) gefunden.\n\n"
        output += "=== Vollständiger Inhalt ===\n"
        output += content
        return ToolResult(success=True, data=output)

    output += f"Gefundene SQL-Blöcke: {len(matches)}\n\n"

    for i, match in enumerate(matches, 1):
        sql_raw = match.group(1).strip()
        start_pos = match.start()

        # Zeile des Matches bestimmen
        line_num = content[:start_pos].count('\n') + 1

        # Umgebende Methode suchen (letzte method-Deklaration vor diesem SQL)
        method_context = "unbekannte Methode"
        method_pattern = re.compile(
            r'(?:public|private|protected|static|\s)+\w+[\w<>, \[\]]*\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
            re.MULTILINE
        )
        for m in method_pattern.finditer(content[:start_pos]):
            method_context = m.group(1)

        # SQL normalisieren (Whitespace vereinfachen)
        sql_clean = re.sub(r'\s+', ' ', sql_raw).strip()

        # Host-Variablen (:varName) extrahieren
        host_vars = re.findall(r':(\w+)', sql_clean)

        output += f"--- SQL #{i} (Zeile {line_num}, Methode: {method_context}) ---\n"
        output += f"```sql\n{sql_clean}\n```\n"
        if host_vars:
            output += f"Host-Variablen (SQLJ :var): {', '.join(f':{v}' for v in host_vars)}\n"
        output += "\n"

    return ToolResult(success=True, data=output)


async def debug_java_with_testdata(
    class_name: str = "",
    method_name: str = "",
    test_parameters: Optional[Dict[str, Any]] = None
) -> ToolResult:
    """
    Debuggt einen Java-Service mit Testdaten:
    1. Sucht und liest die Java-Klasse
    2. Findet zugehörige SQLJ-Dateien
    3. Extrahiert SQL der Zielmethode
    4. Substituiert Testparameter (SQLJ :varName → Werte)
    5. Führt SQL gegen die DB aus und zeigt Ergebnisse
    """
    # Explizite Prüfung hier als zweite Verteidigungslinie – gibt dem LLM
    # eine klare, handlungsanleitende Fehlermeldung statt eines Python-Tracebacks.
    if not class_name or not class_name.strip():
        return ToolResult(
            success=False,
            error=(
                "class_name ist ein Pflichtparameter und darf nicht leer sein. "
                "Bitte frage den Nutzer nach dem genauen Java-Klassennamen "
                "(z.B. 'CustomerService') und rufe das Tool dann erneut auf."
            )
        )

    import re
    from pathlib import Path
    from app.core.config import settings

    if test_parameters is None:
        test_parameters = {}

    class_name = class_name.strip()
    output = f"=== Java Debug: {class_name}"
    if method_name:
        output += f".{method_name}()"
    output += " ===\n\n"

    repo_path = Path(settings.java.get_active_path()) if settings.java.get_active_path() else Path(".")

    # 1. Java-Klasse finden
    simple_name = class_name.split(".")[-1]  # com.example.Foo → Foo
    java_files = list(repo_path.rglob(f"{simple_name}.java"))
    sqlj_files_same_dir: list = []
    java_content = ""

    if not java_files:
        output += f"[WARNUNG] Java-Klasse '{simple_name}.java' nicht im Repository gefunden.\n"
        output += "Suche nach SQLJ-Dateien mit ähnlichem Namen...\n\n"
    else:
        java_file = java_files[0]
        output += f"**Java-Datei:** {java_file.relative_to(repo_path)}\n\n"
        try:
            java_content = java_file.read_text(encoding="utf-8", errors="replace")
            output += f"```java\n{java_content[:8000]}"
            if len(java_content) > 8000:
                output += f"\n... [+{len(java_content)-8000} Zeichen, nutze read_file für vollständigen Inhalt]"
            output += "\n```\n\n"
        except Exception as e:
            output += f"[Lesefehler Java: {e}]\n\n"

        # SQLJ-Dateien im selben Verzeichnis suchen
        sqlj_files_same_dir = list(java_file.parent.glob("*.sqlj"))
        # Auch nach SQLJ mit ähnlichem Namen suchen
        sqlj_files_same_dir += [
            f for f in repo_path.rglob(f"*{simple_name}*.sqlj")
            if f not in sqlj_files_same_dir
        ]

    # Wenn keine SQLJ im selben Verzeichnis, im ganzen Repo suchen
    if not sqlj_files_same_dir:
        sqlj_files_same_dir = list(repo_path.rglob("*.sqlj"))

    # 2. SQLJ-Dateien lesen und SQL für Zielmethode extrahieren
    sql_pattern = re.compile(
        r'#sql\s*(?:\[\s*\w+\s*\])?\s*\{(.*?)\}\s*;',
        re.DOTALL | re.IGNORECASE
    )
    method_pattern = re.compile(
        r'(?:public|private|protected|static|\s)+\w+[\w<>, \[\]]*\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
        re.MULTILINE
    )

    relevant_sql_blocks: list = []

    for sqlj_file in sqlj_files_same_dir[:5]:  # max 5 SQLJ-Dateien
        try:
            sqlj_content = sqlj_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        output += f"**SQLJ-Datei:** {sqlj_file.name}\n"

        for match in sql_pattern.finditer(sqlj_content):
            sql_raw = match.group(1).strip()
            sql_clean = re.sub(r'\s+', ' ', sql_raw).strip()
            start_pos = match.start()

            # Methode des SQL-Blocks bestimmen
            current_method = ""
            for m in method_pattern.finditer(sqlj_content[:start_pos]):
                current_method = m.group(1)

            # Nur Methode filtern wenn angegeben
            if method_name and current_method.lower() != method_name.lower():
                continue

            host_vars = re.findall(r':(\w+)', sql_clean)
            relevant_sql_blocks.append({
                "file": sqlj_file.name,
                "method": current_method,
                "sql": sql_clean,
                "host_vars": host_vars,
            })

    if not relevant_sql_blocks:
        output += f"\nKeine SQL-Statements für Methode '{method_name or 'alle'}' gefunden.\n"
    else:
        output += f"\n**Gefundene SQL-Statements:** {len(relevant_sql_blocks)}\n\n"

    # 3. SQL mit Testdaten substituieren und ausführen
    if relevant_sql_blocks and test_parameters and settings.database.enabled:
        try:
            from app.services.db_client import get_db_client
            db_client = get_db_client()
        except Exception:
            db_client = None

        output += "## SQL-Ausführung mit Testdaten\n\n"

        for i, block in enumerate(relevant_sql_blocks, 1):
            sql = block["sql"]
            host_vars = block["host_vars"]

            # :varName → SQL-Literal substituieren
            sql_exec = sql
            for var in host_vars:
                val = test_parameters.get(var, test_parameters.get(var.lower()))
                if val is not None:
                    if isinstance(val, str):
                        sql_literal = f"'{val}'"
                    elif isinstance(val, bool):
                        sql_literal = "1" if val else "0"
                    else:
                        sql_literal = str(val)
                    sql_exec = re.sub(rf':{var}\b', sql_literal, sql_exec)

            output += f"### SQL #{i} ({block['method']})\n"
            output += f"Original: `{sql[:200]}...`\n" if len(sql) > 200 else f"Original: `{sql}`\n"
            output += f"Mit Testdaten: `{sql_exec[:300]}`\n\n"

            if db_client:
                try:
                    result = await db_client.execute(sql_exec)
                    if result.success:
                        rows = result.data if isinstance(result.data, list) else []
                        output += f"**Ergebnis:** {len(rows)} Zeile(n)\n"
                        if rows:
                            # Header
                            if isinstance(rows[0], dict):
                                cols = list(rows[0].keys())
                                output += "| " + " | ".join(cols) + " |\n"
                                output += "|" + "|".join(["---"] * len(cols)) + "|\n"
                                for row in rows[:20]:
                                    output += "| " + " | ".join(str(row.get(c, "")) for c in cols) + " |\n"
                            else:
                                for row in rows[:20]:
                                    output += f"- {row}\n"
                    else:
                        output += f"**DB-Fehler:** {result.error}\n"
                except Exception as e:
                    output += f"**Ausführungsfehler:** {e}\n"
            else:
                output += "_Datenbank nicht verfügbar - SQL konnte nicht ausgeführt werden._\n"
            output += "\n"
    elif relevant_sql_blocks and not test_parameters:
        output += "\n_Keine Testparameter übergeben - SQL nicht ausgeführt. Übergebe test_parameters um SQL auszuführen._\n"
    elif relevant_sql_blocks and not settings.database.enabled:
        output += "\n_Datenbank nicht aktiviert - SQL nicht ausgeführt._\n"

    return ToolResult(success=True, data=output)


# ══════════════════════════════════════════════════════════════════════════════
# Tool Definitions
# ══════════════════════════════════════════════════════════════════════════════

SEARCH_CODE_TOOL = Tool(
    name="search_code",
    description=(
        "PRIMÄRES SUCH-TOOL für Code im LOKALEN Repository. "
        "Durchsucht alle Dateien per ripgrep mit Sprach-/Dateifilter. "
        "BEVORZUGE DIESES TOOL für: Klassennamen, Methodennamen, Fehlermeldungen, Code-Patterns. "
        "Gibt relative Pfade zurück → direkt mit read_file oder trace_java_references verwendbar. "
        "UNTERSCHIED zu grep_content: search_code hat Sprachfilter (java/python/sql), grep_content ist flexibler für Pfade. "
        "WICHTIG: Nur LOKALE Dateien! Für GitHub: github_pr_diff verwenden."
    ),
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff oder Regex-Pattern (z.B. 'OrderService', 'def process_', 'class.*Handler')"),
        ToolParameter("language", "string", "Sprache für Dateifilter: 'java', 'python', 'sql' oder 'all'", required=False, default="all", enum=["java", "python", "sql", "all"]),
        ToolParameter("max_results", "integer", "Maximale Anzahl Treffer", required=False, default=20),
        ToolParameter("context_lines", "integer", "Kontext-Zeilen vor/nach Match", required=False, default=2),
        ToolParameter("case_sensitive", "boolean", "Groß-/Kleinschreibung beachten", required=False, default=False),
        ToolParameter("file_pattern", "string", "Optionales Glob-Pattern (überschreibt language)", required=False, default=""),
        ToolParameter("subpath", "string", "Optionales Unterverzeichnis für gezielte Suche", required=False, default=""),
        ToolParameter("read_files", "boolean", "Gefundene Dateien vollständig lesen (max 3)", required=False, default=False),
    ],
    handler=search_code
)

SEARCH_HANDBOOK_TOOL = Tool(
    name="search_handbook",
    description=(
        "Durchsucht das HTML-Handbuch (Netzlaufwerk) nach Service-Dokumentationen und Feldbeschreibungen. "
        "HINWEIS: Internes Handbuch, nicht Confluence oder GitHub. "
        "Für Confluence: search_confluence verwenden."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff"),
        ToolParameter("service_filter", "string", "Optional: Nur in diesem Service suchen", required=False),
        ToolParameter("top_k", "integer", "Maximale Anzahl Ergebnisse", required=False, default=5),
    ],
    handler=search_handbook
)

SEARCH_SKILLS_TOOL = Tool(
    name="search_skills",
    description="Durchsucht die Wissensbasen der aktiven Skills nach relevantem Wissen aus Dokumenten und Richtlinien.",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff"),
        ToolParameter("skill_ids", "array", "Optional: Nur in diesen Skills suchen", required=False),
        ToolParameter("top_k", "integer", "Maximale Anzahl Ergebnisse", required=False, default=5),
    ],
    handler=search_skills
)

READ_FILE_TOOL = Tool(
    name="read_file",
    description=(
        "Liest eine LOKALE Datei mit Zeilennummern. Für große Dateien: nutze offset/limit. "
        "PFAD-AUFLÖSUNG: "
        "1) Absoluter Pfad → direkt verwenden. "
        "2) Relativer Pfad → wird in Java-Repo, Python-Repo, dann CWD gesucht. "
        "3) Stacktrace-Pfad → Pfade aus Stacktraces (z.B. 'com/example/Service.java:42') direkt verwenden. "
        "EMPFEHLUNG: Pfade aus search_code oder Stacktraces unverändert übernehmen. "
        "WICHTIG: Für GitHub-Dateien verwende github_get_file."
    ),
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter("path", "string",
            "Pfad zur Datei. Akzeptiert: "
            "1) Absoluter Pfad (z.B. 'C:/repo/src/File.java'), "
            "2) Relativer Pfad aus search_code (z.B. 'src/main/java/Service.java'), "
            "3) Pfad aus Stacktrace (z.B. 'com/example/Service.java')"
        ),
        ToolParameter("encoding", "string", "Encoding der Datei", required=False, default="utf-8"),
        ToolParameter("offset", "integer", "Startzeile, 1-basiert (0=Anfang)", required=False, default=0),
        ToolParameter("limit", "integer", "Max Zeilen (0=alle, empfohlen: 500)", required=False, default=0),
        ToolParameter("show_line_numbers", "boolean", "Zeilennummern anzeigen", required=False, default=True),
    ],
    handler=read_file
)

LIST_FILES_TOOL = Tool(
    name="list_files",
    description=(
        "Listet Dateien in einem LOKALEN Verzeichnis auf. Für Pattern-Suche nutze glob_files. "
        "PFAD: Absoluter Pfad zum Verzeichnis. "
        "WICHTIG: Für GitHub-Repos verwende github_list_repos."
    ),
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter("path", "string",
            "Absoluter Verzeichnispfad (z.B. 'C:/repo/src'). "
            "Nutze get_project_paths um indexierte Projekt-Pfade zu sehen."
        ),
        ToolParameter("pattern", "string", "Glob-Pattern (z.B. '*.java')", required=False, default="*"),
        ToolParameter("recursive", "boolean", "Auch Unterverzeichnisse durchsuchen", required=False, default=False),
    ],
    handler=list_files
)

GLOB_FILES_TOOL = Tool(
    name="glob_files",
    description=(
        "Sucht Dateien nach Glob-Pattern (wie Claude Code). Schneller als list_files für Pattern-Suche. "
        "Patterns: '**/*.py' (rekursiv), 'src/**/*.java', '*.md'. "
        "PFAD: Absoluter Pfad zum Basisverzeichnis. "
        "Sortiert standardmäßig nach Änderungsdatum (neueste zuerst)."
    ),
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter("pattern", "string", "Glob-Pattern (z.B. '**/*.py', 'src/**/*.java')"),
        ToolParameter("path", "string",
            "Absoluter Pfad zum Basisverzeichnis. "
            "Nutze get_project_paths für indexierte Projekt-Pfade.",
            required=False, default="."
        ),
        ToolParameter("sort_by", "string", "Sortierung: mtime|name|size", required=False, default="mtime"),
        ToolParameter("max_results", "integer", "Max Ergebnisse", required=False, default=100),
    ],
    handler=glob_files
)

GREP_CONTENT_TOOL = Tool(
    name="grep_content",
    description=(
        "FLEXIBLES SUCH-TOOL mit frei wählbarem Pfad und Glob-Pattern. "
        "NUTZE WENN: Du in einem SPEZIFISCHEN Verzeichnis/Pfad suchen willst. "
        "Für ALLGEMEINE Code-Suche: search_code ist einfacher (hat Sprachfilter). "
        "UNTERSCHIED zu search_code: grep_content erlaubt freie Pfadangabe + beliebige Glob-Patterns. "
        "PFAD: Absoluter Pfad zum Verzeichnis. "
        "Zeigt Kontext-Zeilen vor/nach dem Treffer."
    ),
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("pattern", "string", "Suchtext oder Regex (z.B. 'def process_', 'class.*Handler', Fehlermeldung)"),
        ToolParameter("path", "string",
            "Absoluter Pfad zum Verzeichnis oder zur Datei. "
            "Bei Stacktrace: Verzeichnis aus Stacktrace-Pfad (z.B. 'src/main/java/com/example').",
            required=False, default="."
        ),
        ToolParameter("file_pattern", "string", "Glob-Filter (z.B. '*.py', '*.java')", required=False, default="*"),
        ToolParameter("context_lines", "integer", "Zeilen vor/nach Match", required=False, default=2),
        ToolParameter("max_results", "integer", "Max Treffer", required=False, default=50),
        ToolParameter("case_sensitive", "boolean", "Groß-/Kleinschreibung beachten", required=False, default=False),
    ],
    handler=grep_content
)

GET_PROJECT_PATHS_TOOL = Tool(
    name="get_project_paths",
    description=(
        "Zeigt die indexierten Projekt-Pfade für Dateioperationen. "
        "NUTZE DIESES TOOL wenn du wissen willst, welche Verzeichnisse für Lese-/Schreiboperationen verfügbar sind. "
        "Hilft bei Stacktrace-Analyse und gezielter Dateisuche."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[],
    handler=get_project_paths
)

WRITE_FILE_TOOL = Tool(
    name="write_file",
    description=(
        "Erstellt oder überschreibt eine LOKALE DATEI (keine Ordner!). BENÖTIGT USER-BESTÄTIGUNG. "
        "WICHTIG: Für Ordner verwende create_directory! Pfad muss Dateiendung haben (z.B. .py, .java, .md). "
        "Pfad: Absolut oder relativ zum indexierten Projekt-Verzeichnis. "
        "NICHT für GitHub-Repos - diese können nicht direkt beschrieben werden."
    ),
    category=ToolCategory.FILE,
    is_write_operation=True,
    parameters=[
        ToolParameter("path", "string", "Absoluter Pfad zur Datei (mit Dateiendung!)"),
        ToolParameter("content", "string", "Neuer Dateiinhalt"),
    ],
    handler=write_file
)

CREATE_DIRECTORY_TOOL = Tool(
    name="create_directory",
    description=(
        "Erstellt ein Verzeichnis (Ordner). Erstellt auch alle Elternverzeichnisse falls nötig. "
        "WICHTIG: Für Ordner, NICHT für Dateien! Für Dateien verwende write_file."
    ),
    category=ToolCategory.FILE,
    is_write_operation=True,
    parameters=[
        ToolParameter("path", "string", "Pfad zum Verzeichnis das erstellt werden soll"),
    ],
    handler=create_directory
)

EDIT_FILE_TOOL = Tool(
    name="edit_file",
    description=(
        "Bearbeitet eine LOKALE Datei durch String-Ersetzung (wie Claude Code Edit). BENÖTIGT BESTÄTIGUNG.\n\n"
        "KRITISCH - IMMER ZUERST read_file AUSFÜHREN!\n"
        "Der old_string muss EXAKT mit dem Dateiinhalt übereinstimmen - inkl. Leerzeichen, Tabs, Zeilenumbrüche.\n"
        "Kopiere old_string DIREKT aus dem read_file Output, nicht aus dem Gedächtnis rekonstruieren!\n\n"
        "Bei mehrfachem Vorkommen: 1) Mehr Kontext in old_string, oder 2) replace_all=true.\n"
        "Pfad: Absolut oder relativ. NICHT für GitHub-Repos - diese können nicht direkt bearbeitet werden."
    ),
    category=ToolCategory.FILE,
    is_write_operation=True,
    parameters=[
        ToolParameter("path", "string", "Pfad zur Datei (relativ oder absolut)"),
        ToolParameter("old_string", "string", "EXAKTER zu ersetzender Text - muss 1:1 aus read_file kopiert werden inkl. aller Whitespaces!"),
        ToolParameter("new_string", "string", "Neuer Text der old_string ersetzt"),
        ToolParameter("replace_all", "boolean", "Alle Vorkommen ersetzen (default: false)", required=False, default=False),
    ],
    handler=edit_file
)

GET_SERVICE_INFO_TOOL = Tool(
    name="get_service_info",
    description="Holt detaillierte Informationen zu einem Service aus dem Handbuch (Tabs, Felder, Aufrufvarianten).",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("service_id", "string", "ID des Services"),
    ],
    handler=get_service_info
)

SEARCH_PDF_TOOL = Tool(
    name="search_pdf",
    description=(
        "Durchsucht in dieser Session hochgeladene PDF-Dokumente nach Text. "
        "HINWEIS: Nur PDFs die der User in diesem Chat hochgeladen hat. "
        "Für Confluence-PDFs: Seite über search_confluence finden, dann read_confluence_page."
    ),
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff"),
        ToolParameter("filename_filter", "string", "Optional: Nur PDFs mit diesem Namen durchsuchen", required=False),
        ToolParameter("top_k", "integer", "Maximale Anzahl Ergebnisse", required=False, default=5),
    ],
    handler=search_pdf
)

GET_PDF_INFO_TOOL = Tool(
    name="get_pdf_info",
    description=(
        "Gibt Metadaten und Seitenanzahl einer PDF-Datei zurück. "
        "Nutze dieses Tool bevor du read_pdf_pages aufrufst, um die Gesamtseitenzahl zu kennen."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("filename", "string", "Dateiname oder Teil des Dateinamens der PDF"),
    ],
    handler=get_pdf_info
)

READ_PDF_PAGES_TOOL = Tool(
    name="read_pdf_pages",
    description=(
        "Liest einen bestimmten Seitenbereich einer PDF-Datei (1-basiert, inklusiv). "
        "Maximal 30 Seiten pro Aufruf. Für große PDFs: Nutze zuerst get_pdf_info für die "
        "Seitenanzahl, dann lies relevante Abschnitte sequenziell mit mehreren Aufrufen."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("filename", "string", "Dateiname oder Teil des Dateinamens der PDF"),
        ToolParameter("start_page", "integer", "Erste Seite (1-basiert)"),
        ToolParameter("end_page", "integer", "Letzte Seite (inklusiv, max. start_page + 29)"),
    ],
    handler=read_pdf_pages
)

READ_SQLJ_FILE_TOOL = Tool(
    name="read_sqlj_file",
    description=(
        "Liest eine LOKALE SQLJ-Datei und extrahiert SQL-Statements (#sql { ... }) mit Methoden-Kontext. "
        "WICHTIG: Nur für LOKALE Dateien! Für GitHub-Dateien: github_get_file verwenden."
    ),
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter("path", "string",
            "Pfad zur SQLJ-Datei. Akzeptiert: "
            "1) Relativer Pfad aus search_code: 'src/main/java/Dao.sqlj', "
            "2) Absoluter Pfad: 'C:/repo/src/Dao.sqlj', "
            "3) Klassenname mit .sqlj: 'CustomerDao.sqlj'"
        ),
    ],
    handler=read_sqlj_file
)

DEBUG_JAVA_TESTDATA_TOOL = Tool(
    name="debug_java_with_testdata",
    description=(
        "Analysiert LOKALEN Java-Code mit Testdaten: liest Code → SQLJ → substituiert Parameter → SQL-Ergebnis. "
        "WICHTIG: Arbeitet mit LOKALEM Repository! Für GitHub-Code erst mit github_get_file holen. "
        "PFLICHT: class_name angeben (zuerst search_code aufrufen)."
    ),
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter("class_name", "string",
            "PFLICHT: Java-Klasse. Akzeptiert: "
            "1) Klassenname: 'CustomerService', "
            "2) Vollqualifiziert: 'com.example.CustomerService', "
            "3) Dateipfad: 'src/main/java/CustomerService.java'. "
            "Tool schlägt fehl wenn leer — zuerst search_code aufrufen."
        ),
        ToolParameter("method_name", "string", "Name der Methode (optional, filtert SQL auf diese Methode)", required=False, default=""),
        ToolParameter("test_parameters", "object", "Testdaten als Key-Value-Objekt, z.B. {\"customerId\": \"12345\", \"date\": \"2024-01-01\"}. Keys entsprechen den SQLJ-Host-Variablen (:varName)", required=False, default={}),
    ],
    handler=debug_java_with_testdata
)

TRACE_JAVA_REFERENCES_TOOL = Tool(
    name="trace_java_references",
    description=(
        "Findet Interfaces, Parent-Klassen und Implementierungen im LOKALEN Repository. "
        "WICHTIG: Analysiert nur LOKALE Dateien! Für GitHub: github_get_file + manuelle Analyse. "
        "Nutze NACH search_code wenn Vererbung relevant ist."
    ),
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter("class_name", "string",
            "Klassenname ODER Dateipfad. Akzeptiert: "
            "1) Einfacher Name: 'CustomerService', "
            "2) Vollqualifiziert: 'com.example.CustomerService', "
            "3) Dateipfad: 'src/main/java/CustomerService.java'"
        ),
        ToolParameter("include_interfaces", "boolean", "Interfaces verfolgen", required=False, default=True),
        ToolParameter("include_parent_classes", "boolean", "Parent-Klassen verfolgen", required=False, default=True),
        ToolParameter("max_depth", "integer", "Maximale Tiefe der Hierarchie", required=False, default=5),
    ],
    handler=trace_java_references
)

QUERY_DATABASE_TOOL = Tool(
    name="query_database",
    description="Führt DB2 SELECT-Query aus (NUR SELECT erlaubt). Beispiel: SELECT * FROM ORDERS FETCH FIRST 10 ROWS ONLY",
    category=ToolCategory.SEARCH,
    is_write_operation=False,  # Nur SELECT erlaubt, daher keine Bestätigung nötig
    parameters=[
        ToolParameter("query", "string", "SQL SELECT-Query"),
        ToolParameter("max_rows", "integer", "Maximale Anzahl Zeilen", required=False, default=100),
    ],
    handler=query_database
)

LIST_DATABASE_TABLES_TOOL = Tool(
    name="list_database_tables",
    description="Listet alle verfügbaren Tabellen in der DB2-Datenbank auf.",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("schema", "string", "Optional: Schema-Name (sonst Standard-Schema)", required=False),
    ],
    handler=list_database_tables
)

DESCRIBE_DATABASE_TABLE_TOOL = Tool(
    name="describe_database_table",
    description="Zeigt die Struktur einer DB2-Tabelle (Spalten, Typen, Constraints).",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("table_name", "string", "Name der Tabelle"),
        ToolParameter("schema", "string", "Optional: Schema-Name", required=False),
    ],
    handler=describe_database_table
)


# ── Confluence Tools ──

async def search_confluence(query: str, space: str = "", limit: int = 10) -> ToolResult:
    """Durchsucht Confluence per CQL nach Seiten."""
    from app.services.confluence_client import get_confluence_client
    from app.services.confluence_cache import get_confluence_cache
    from app.core.config import settings

    if not settings.confluence.base_url:
        return ToolResult(success=False, error="Confluence ist nicht konfiguriert (base_url fehlt)")

    try:
        cache = get_confluence_cache()
        space_key = space or settings.confluence.default_space or ""

        # Cache-Check
        cached = cache.get_search(query, space_key, limit)
        if cached is not None:
            results = cached
            cache_info = " (cached)"
        else:
            client = get_confluence_client()
            results = await client.search(
                query=query,
                space_key=space_key or None,
                limit=min(limit, 20),
            )
            cache.set_search(query, space_key, limit, results)
            cache_info = ""

        if not results:
            return ToolResult(success=True, data=f"Keine Confluence-Ergebnisse für: {query}")

        output = f"=== Confluence-Suche: {query}{cache_info} ===\n"
        output += f"{len(results)} Ergebnisse gefunden:\n\n"
        for r in results:
            output += f"📄 {r['title']}\n"
            output += f"   ID: {r['id']} | Space: {r['space']}\n"
            output += f"   URL: {r['url']}\n"
            if r.get("excerpt"):
                excerpt = r["excerpt"][:200].replace("\n", " ")
                output += f"   {excerpt}\n"
            output += "\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Confluence-Fehler: {str(e)}")


async def read_confluence_page(page_id: str) -> ToolResult:
    """Liest den Inhalt einer Confluence-Seite."""
    from app.services.confluence_client import get_confluence_client
    from app.services.confluence_cache import get_confluence_cache, get_current_session
    from app.core.config import settings

    if not settings.confluence.base_url:
        return ToolResult(success=False, error="Confluence ist nicht konfiguriert (base_url fehlt)")

    try:
        cache = get_confluence_cache()
        session_id = get_current_session()

        # Prüfen ob Seite bereits in dieser Session gelesen wurde
        already_read_warning = ""
        if session_id and cache.was_page_read(session_id, page_id):
            read_pages = cache.get_read_pages(session_id)
            already_read_warning = (
                f"\n⚠️ HINWEIS: Diese Seite wurde bereits gelesen!\n"
                f"Wenn du nach anderen Informationen suchst, nutze search_confluence mit anderen Suchbegriffen.\n"
                f"Bereits gelesene Seiten in dieser Session:\n"
            )
            for pid, title in list(read_pages.items())[:5]:
                already_read_warning += f"  - {title} (ID: {pid})\n"
            already_read_warning += "\n---\n\n"

        # Cache-Check
        cached = cache.get_page(page_id)
        if cached is not None:
            page = cached
            cache_info = " (cached)"
        else:
            client = get_confluence_client()
            page = await client.get_page_by_id(page_id)
            cache.set_page(page_id, page)
            cache_info = ""

        # Seite als gelesen markieren
        if session_id:
            cache.mark_page_read(session_id, page_id, page.get('title', 'Unbekannt'))

        output = f"=== Confluence-Seite{cache_info} ===\n"
        output += already_read_warning
        output += f"Titel: {page['title']}\n"
        output += f"URL: {page['url']}\n"
        output += f"Space: {page['space']}\n"
        output += f"---\n\n"
        output += page.get("content", "")

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Confluence-Fehler: {str(e)}")


SEARCH_CONFLUENCE_TOOL = Tool(
    name="search_confluence",
    description=(
        "Durchsucht das Confluence-Wiki nach Seiten. "
        "Sucht in Titel UND Seiteninhalt (Volltextsuche). "
        "Verwende Stichworte oder Phrasen, z.B. 'Installation Anleitung' oder 'API Dokumentation'. "
        "Gibt Titel, IDs und Textauszüge zurück. "
        "Nutze read_confluence_page mit der ID um den vollständigen Seiteninhalt zu lesen."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("query", "string", "Suchbegriffe (z.B. 'API Dokumentation' oder 'Installation Guide')"),
        ToolParameter("space", "string", "Optional: Confluence Space Key zum Einschränken", required=False, default=""),
        ToolParameter("limit", "integer", "Maximale Anzahl Ergebnisse (1-20)", required=False, default=10),
    ],
    handler=search_confluence
)

READ_CONFLUENCE_PAGE_TOOL = Tool(
    name="read_confluence_page",
    description="Liest den vollständigen Inhalt einer Confluence-Seite anhand ihrer ID. Die ID erhältst du aus search_confluence.",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("page_id", "string", "Confluence Seiten-ID"),
    ],
    handler=read_confluence_page
)


# ── Confluence PDF Tools ──

async def list_confluence_pdfs(page_id: str) -> ToolResult:
    """
    Listet alle PDF-Attachments einer Confluence-Seite auf.

    Gibt Metadaten wie Titel, Größe und Download-URL zurück.
    Nützlich um zu prüfen welche PDFs an einer Seite hängen
    bevor man sie mit read_confluence_pdf liest.
    """
    from app.services.confluence_client import get_confluence_client
    from app.services.confluence_cache import get_confluence_cache
    from app.core.config import settings

    if not settings.confluence.base_url:
        return ToolResult(success=False, error="Confluence ist nicht konfiguriert")

    try:
        cache = get_confluence_cache()

        # Cache-Check
        cached = cache.get_attachments(page_id, "application/pdf")
        if cached is not None:
            attachments = cached
            cache_info = " (cached)"
        else:
            client = get_confluence_client()
            attachments = await client.get_pdf_attachments(page_id)
            cache.set_attachments(page_id, "application/pdf", attachments)
            cache_info = ""

        if not attachments:
            return ToolResult(
                success=True,
                data=f"Keine PDF-Attachments auf Seite {page_id} gefunden."
            )

        output = f"=== PDF-Attachments auf Seite {page_id}{cache_info} ===\n\n"
        for att in attachments:
            size_kb = att['size_bytes'] / 1024
            output += f"📎 {att['title']}\n"
            output += f"   ID: {att['id']} | Größe: {size_kb:.1f} KB\n"
            output += f"   Typ: {att['media_type']}\n\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Confluence PDF-Fehler: {str(e)}")


async def read_confluence_pdf(
    page_id: str,
    pdf_title: str,
    max_pages: int = 10,
    query: str = ""
) -> ToolResult:
    """
    Liest ein PDF-Attachment von einer Confluence-Seite und extrahiert den Text.

    Bei Angabe von 'query' wird eine Relevanz-Bewertung durchgeführt
    und nur relevante Abschnitte zurückgegeben.
    """
    import tempfile
    from pathlib import Path
    from app.services.confluence_client import get_confluence_client
    from app.services.pdf_reader import PDFReader
    from app.core.config import settings

    if not settings.confluence.base_url:
        return ToolResult(success=False, error="Confluence ist nicht konfiguriert")

    try:
        client = get_confluence_client()
        attachments = await client.get_pdf_attachments(page_id)

        # PDF nach Titel finden
        target_pdf = None
        for att in attachments:
            if pdf_title.lower() in att['title'].lower():
                target_pdf = att
                break

        if not target_pdf:
            available = ", ".join(a['title'] for a in attachments) if attachments else "keine"
            return ToolResult(
                success=False,
                error=f"PDF '{pdf_title}' nicht gefunden. Verfügbar: {available}"
            )

        # PDF herunterladen
        pdf_bytes = await client.download_attachment(target_pdf['download_url'])

        # Temporär speichern und extrahieren
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            reader = PDFReader()
            text = reader.extract_text(tmp_path, max_pages=max_pages)
            metadata = reader.get_metadata(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # Relevanz-Bewertung wenn Query angegeben
        relevance_info = ""
        if query:
            relevance_score = _calculate_pdf_relevance(text, query)
            relevance_info = f"\n📊 Relevanz für '{query}': {relevance_score:.0%}\n"

            # Bei niedriger Relevanz warnen
            if relevance_score < 0.2:
                relevance_info += "⚠️ Geringe Relevanz - PDF enthält wenig zur Anfrage\n"

        # Output formatieren
        output = f"=== PDF: {target_pdf['title']} ===\n"
        output += f"Seiten: {metadata.get('page_count', '?')} | "
        output += f"Autor: {metadata.get('author', '-')}\n"
        output += relevance_info
        output += f"---\n\n{text}"

        return ToolResult(success=True, data=output)

    except Exception as e:
        logger.error(f"PDF-Lesefeher: {e}")
        return ToolResult(success=False, error=f"PDF-Fehler: {str(e)}")


def _calculate_pdf_relevance(text: str, query: str) -> float:
    """
    Berechnet einen einfachen Relevanz-Score für PDF-Inhalt.

    Args:
        text: Extrahierter PDF-Text
        query: Suchanfrage

    Returns:
        Score zwischen 0.0 und 1.0
    """
    import re

    if not text or not query:
        return 0.0

    text_lower = text.lower()
    query_lower = query.lower()

    # Query-Terme extrahieren (min 3 Zeichen)
    query_terms = set(re.findall(r'\b\w{3,}\b', query_lower))
    if not query_terms:
        return 0.0

    # Zähle Treffer
    matches = 0
    total_occurrences = 0

    for term in query_terms:
        count = text_lower.count(term)
        if count > 0:
            matches += 1
            total_occurrences += min(count, 10)  # Cap bei 10 pro Term

    # Score berechnen
    # 60% basierend auf Anteil gefundener Terme
    term_coverage = matches / len(query_terms)

    # 40% basierend auf Häufigkeit (normalisiert)
    frequency_score = min(total_occurrences / (len(query_terms) * 5), 1.0)

    return 0.6 * term_coverage + 0.4 * frequency_score


LIST_CONFLUENCE_PDFS_TOOL = Tool(
    name="list_confluence_pdfs",
    description=(
        "Listet alle PDF-Attachments einer Confluence-Seite auf. "
        "Zeigt Titel, ID und Größe. "
        "Nutze die Seiten-ID aus search_confluence."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("page_id", "string", "Confluence Seiten-ID"),
    ],
    handler=list_confluence_pdfs
)

READ_CONFLUENCE_PDF_TOOL = Tool(
    name="read_confluence_pdf",
    description=(
        "Liest ein PDF-Attachment von einer Confluence-Seite und extrahiert den Text. "
        "Unterstützt Relevanz-Bewertung: Gib 'query' an um zu prüfen wie relevant "
        "das PDF für eine bestimmte Frage ist. "
        "Nutze list_confluence_pdfs um verfügbare PDFs zu sehen."
    ),
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("page_id", "string", "Confluence Seiten-ID"),
        ToolParameter("pdf_title", "string", "Name/Titel des PDFs (Teil-Match möglich)"),
        ToolParameter("max_pages", "integer", "Max. Seiten extrahieren (default: 10)", required=False, default=10),
        ToolParameter("query", "string", "Optional: Query für Relevanz-Bewertung", required=False, default=""),
    ],
    handler=read_confluence_pdf
)


# ── Jira Tools ──

async def search_jira(query: str, project: str = "", max_results: int = 15) -> ToolResult:
    """Durchsucht Jira per JQL oder Freitext."""
    import re
    from app.services.jira_client import get_jira_client
    from app.core.config import settings

    if not settings.jira.enabled or not settings.jira.base_url:
        return ToolResult(success=False, error="Jira ist nicht konfiguriert oder deaktiviert")

    try:
        # Issue-Key aus URL extrahieren (z.B. https://jira.example.com/browse/DIKA-123 oder AB-CD-456)
        # Pattern: Projekt-Key (Buchstaben mit optionalen Bindestrichen) + Bindestrich + Zahl
        url_match = re.search(r'/browse/([A-Z]+(?:-[A-Z]+)*-\d+)', query, re.IGNORECASE)
        if url_match:
            issue_key = url_match.group(1).upper()
            # Bei direktem Issue-Key: Subtasks automatisch mitladen
            return await read_jira_issue(issue_key, include_subtasks=True)

        # Direkter Issue-Key (z.B. "DIKA-123" oder "AB-CD-456") - direkt lesen statt suchen
        # Erlaubt Projekt-Keys mit Bindestrichen: ABC-123, AB-CD-123, A-B-C-123
        if re.match(r'^[A-Z]+(?:-[A-Z]+)*-\d+$', query.strip(), re.IGNORECASE):
            # Bei direktem Issue-Key: Subtasks automatisch mitladen
            return await read_jira_issue(query.strip().upper(), include_subtasks=True)

        client = get_jira_client()

        # Wenn die Query kein JQL-Operator enthält, als Textsuche behandeln
        jql_operators = ["=", "~", "in", "is", "was", "changed", "not", "AND", "OR", "ORDER BY"]
        is_jql = any(op in query for op in jql_operators)

        if is_jql:
            jql = query
        else:
            # Freitext-Suche
            proj = project or settings.jira.default_project
            if proj:
                jql = f'project = "{proj}" AND text ~ "{query}" ORDER BY updated DESC'
            else:
                jql = f'text ~ "{query}" ORDER BY updated DESC'

        results = await client.search(jql=jql, max_results=min(max_results, 50))

        if not results:
            return ToolResult(success=True, data=f"Keine Jira-Issues gefunden für: {query}")

        output = f"=== Jira-Suche: {query} ===\n"
        output += f"{len(results)} Issues gefunden:\n\n"
        for r in results:
            output += f"🎫 {r['key']}: {r['summary']}\n"
            output += f"   Status: {r['status']} | Typ: {r['type']} | Priorität: {r['priority']}\n"
            output += f"   Zugewiesen: {r['assignee']} | Aktualisiert: {r['updated'][:10] if r['updated'] else '-'}\n"
            # Parent anzeigen (falls Subtask)
            parent_key = r.get('parent_key', '')
            if parent_key:
                output += f"   ↳ Subtask von: {parent_key}\n"
            # Subtask-Anzahl anzeigen
            subtask_count = r.get('subtask_count', 0)
            if subtask_count > 0:
                output += f"   📎 {subtask_count} Subtask(s)\n"
            output += f"   URL: {r['url']}\n\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Jira-Fehler: {str(e)}")


async def read_jira_issue(issue_key: str, include_subtasks: bool = True) -> ToolResult:
    """Liest ein einzelnes Jira-Issue mit Details und Kommentaren."""
    import logging
    from app.services.jira_client import get_jira_client
    from app.core.config import settings

    logger = logging.getLogger(__name__)
    logger.info(f"[read_jira_issue] Lese Issue: {issue_key}, include_subtasks={include_subtasks}")

    if not settings.jira.enabled or not settings.jira.base_url:
        return ToolResult(success=False, error="Jira ist nicht konfiguriert oder deaktiviert")

    try:
        client = get_jira_client()
        issue = await client.get_issue(issue_key)

        output = f"=== Jira-Issue: {issue['key']} ===\n"
        output += f"Titel: {issue['summary']}\n"
        output += f"Typ: {issue['type']} | Status: {issue['status']} | Priorität: {issue['priority']}\n"
        output += f"Ersteller: {issue['reporter']} | Zugewiesen: {issue['assignee']}\n"
        output += f"Erstellt: {issue['created'][:10] if issue['created'] else '-'} | "
        output += f"Aktualisiert: {issue['updated'][:10] if issue['updated'] else '-'}\n"
        if issue['labels']:
            output += f"Labels: {', '.join(issue['labels'])}\n"
        if issue['components']:
            output += f"Komponenten: {', '.join(issue['components'])}\n"

        # Parent-Issue anzeigen (falls Subtask)
        parent = issue.get('parent')
        if parent:
            output += f"\n--- Übergeordnetes Issue ---\n"
            output += f"  {parent['key']}: {parent['summary']} ({parent['status']})\n"
            output += f"  URL: {parent['url']}\n"

        output += f"URL: {issue['url']}\n"
        output += f"\n--- Beschreibung ---\n{issue['description'] or '(keine Beschreibung)'}\n"

        # Subtasks verarbeiten
        subtasks = issue.get('subtasks', [])
        if subtasks:
            if include_subtasks:
                # Subtask-Details automatisch laden
                output += f"\n{'='*60}\n"
                output += f"SUBTASK-DETAILS ({len(subtasks)} Subtasks)\n"
                output += f"{'='*60}\n"

                for i, st in enumerate(subtasks, 1):
                    st_key = st['key']
                    logger.info(f"[read_jira_issue] Lade Subtask {i}/{len(subtasks)}: {st_key}")
                    try:
                        st_issue = await client.get_issue(st_key)
                        output += f"\n--- Subtask {i}: {st_key} ---\n"
                        output += f"Titel: {st_issue['summary']}\n"
                        output += f"Status: {st_issue['status']} | Zugewiesen: {st_issue['assignee']}\n"
                        output += f"Beschreibung: {st_issue['description'][:500] if st_issue['description'] else '(keine)'}\n"
                        if st_issue['comments']:
                            output += f"Letzter Kommentar: {st_issue['comments'][-1]['body'][:200]}...\n"
                    except Exception as e:
                        output += f"\n--- Subtask {i}: {st_key} ---\n"
                        output += f"FEHLER beim Laden: {str(e)}\n"
            else:
                # Nur Übersicht zeigen mit Hinweis
                output += f"\n--- Subtasks ({len(subtasks)}) ---\n"
                output += "INFO: Subtask-Details nicht geladen. Für vollständige Details:\n"
                output += f"      read_jira_issue(issue_key='{issue_key}', include_subtasks=true)\n\n"
                for st in subtasks:
                    output += f"  - {st['key']}: {st['summary'][:60]} [{st['status']}]\n"

        if issue['comments']:
            output += f"\n--- Kommentare ({len(issue['comments'])}) ---\n"
            for c in issue['comments']:
                output += f"\n[{c['created'][:10] if c['created'] else '?'}] {c['author']}:\n{c['body']}\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Jira-Fehler: {str(e)}")


SEARCH_JIRA_TOOL = Tool(
    name="search_jira",
    description="""Durchsucht Jira nach Issues. Unterstützt JQL-Queries, Freitext-Suche und direkte Issue-Keys.

WICHTIG: Bei direktem Issue-Key oder Jira-URL werden automatisch ALLE Details inkl. Subtask-Details geladen!

Beispiele:
- "DIKA-123" → Lädt Issue + alle Subtask-Details
- "https://jira.example.com/browse/DIKA-123" → Lädt Issue + alle Subtask-Details
- "Login Fehler" → Textsuche (nur Übersicht)
- "project=DIKA AND status=Open" → JQL-Suche (nur Übersicht)""",
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Issue-Key (PROJ-123), Jira-URL, Suchbegriff oder JQL-Query"),
        ToolParameter("project", "string", "Optional: Projekt-Key für Freitext-Suche", required=False, default=""),
        ToolParameter("max_results", "integer", "Maximale Anzahl Ergebnisse", required=False, default=15),
    ],
    handler=search_jira
)

READ_JIRA_ISSUE_TOOL = Tool(
    name="read_jira_issue",
    description="""Liest ein einzelnes Jira-Issue mit vollständiger Beschreibung, Details und den letzten Kommentaren.

PARAMETER:
- issue_key: Der Issue-Schlüssel (z.B. 'PROJ-123')
- include_subtasks: true (DEFAULT) = Lädt automatisch Details ALLER Subtasks mit
                    false = Zeigt nur Subtask-Übersicht ohne Details

STANDARD-VERHALTEN: Subtasks werden automatisch mitgeladen.
Falls ein Issue Subtasks hat, werden deren vollständige Details (Beschreibung, Status, etc.) direkt mitgeliefert.""",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("issue_key", "string", "Issue-Schlüssel (z.B. 'PROJ-123')"),
        ToolParameter("include_subtasks", "boolean", "Subtask-Details automatisch mitladen (Default: true)", required=False, default=True),
    ],
    handler=read_jira_issue
)


# ══════════════════════════════════════════════════════════════════════════════
# Debug-Modus: suggest_answers (interaktive Rückfrage mit Vorschlägen)
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_suggest_answers(question: str, options: list) -> ToolResult:
    """Pseudo-Handler – die eigentliche Verarbeitung erfolgt im Orchestrator."""
    return ToolResult(
        success=True,
        data={"status": "options_presented_to_user", "question": question, "count": len(options)}
    )


SUGGEST_ANSWERS_TOOL = Tool(
    name="suggest_answers",
    description=(
        "Stellt dem User eine Rückfrage und zeigt ihm Antwort-Optionen als klickbare Buttons an. "
        "Nutze dieses Tool wenn du vor der Analyse mehr Kontext benötigst. "
        "Der User kann eine Option klicken oder eine eigene Antwort eingeben. "
        "Rufe dieses Tool auf BEVOR du mit der eigentlichen Fehleranalyse beginnst."
    ),
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter(
            name="question",
            type="string",
            description="Die Frage die dem User gestellt wird (kurz und präzise)",
            required=True,
        ),
        ToolParameter(
            name="options",
            type="array",
            description="Liste von 2-5 Antwort-Optionen die dem User als Buttons angezeigt werden",
            required=True,
        ),
    ],
    is_write_operation=False,
    handler=_handle_suggest_answers,
)


# ══════════════════════════════════════════════════════════════════════════════
# Default Registry
# ══════════════════════════════════════════════════════════════════════════════

def create_default_registry() -> ToolRegistry:
    """Erstellt eine Registry mit allen Standard-Tools."""
    registry = ToolRegistry()

    # Search Tools
    registry.register(SEARCH_CODE_TOOL)
    registry.register(SEARCH_HANDBOOK_TOOL)
    registry.register(SEARCH_SKILLS_TOOL)
    registry.register(SEARCH_PDF_TOOL)

    # File Tools
    registry.register(READ_FILE_TOOL)
    registry.register(LIST_FILES_TOOL)
    registry.register(GLOB_FILES_TOOL)
    registry.register(GREP_CONTENT_TOOL)
    registry.register(WRITE_FILE_TOOL)
    registry.register(EDIT_FILE_TOOL)
    registry.register(CREATE_DIRECTORY_TOOL)

    # Knowledge Tools
    registry.register(GET_SERVICE_INFO_TOOL)
    registry.register(GET_PDF_INFO_TOOL)
    registry.register(READ_PDF_PAGES_TOOL)
    registry.register(GET_PROJECT_PATHS_TOOL)

    # Analysis Tools
    registry.register(TRACE_JAVA_REFERENCES_TOOL)
    registry.register(READ_SQLJ_FILE_TOOL)
    registry.register(DEBUG_JAVA_TESTDATA_TOOL)

    # Database Tools
    registry.register(QUERY_DATABASE_TOOL)
    registry.register(LIST_DATABASE_TABLES_TOOL)
    registry.register(DESCRIBE_DATABASE_TABLE_TOOL)

    # Confluence Tools
    registry.register(SEARCH_CONFLUENCE_TOOL)
    registry.register(READ_CONFLUENCE_PAGE_TOOL)
    registry.register(LIST_CONFLUENCE_PDFS_TOOL)
    registry.register(READ_CONFLUENCE_PDF_TOOL)

    # Jira Tools
    registry.register(SEARCH_JIRA_TOOL)
    registry.register(READ_JIRA_ISSUE_TOOL)

    # Debug-Modus: Interaktives Rückfrage-Tool
    registry.register(SUGGEST_ANSWERS_TOOL)

    # Web-Suche Tools (mit Bestätigungspflicht)
    from app.agent.search_tools import register_search_tools
    register_search_tools(registry)

    # GitHub Tools (PR-Diff, File-Read, Issue-Search etc.)
    from app.agent.github_tools import register_github_tools
    register_github_tools(registry)

    # Maven Build Tools
    from app.agent.maven_tools import register_maven_tools
    register_maven_tools(registry)

    # WLP Server Tools
    from app.agent.wlp_tools import register_wlp_tools
    register_wlp_tools(registry)

    # Jenkins CI/CD Tools
    from app.agent.jenkins_tools import register_jenkins_tools
    register_jenkins_tools(registry)

    # HP ALM/Quality Center Tools
    from app.agent.alm_tools import register_alm_tools
    register_alm_tools(registry)

    # Test-Tool (SOAP Services mit Session-Management)
    from app.agent.test_tools import register_test_tools
    register_test_tools(registry)

    # Log Server Tools
    from app.agent.log_tools import register_log_tools
    register_log_tools(registry)

    # MQ Tools (Message Queue)
    from app.agent.mq_tools import register_mq_tools
    register_mq_tools(registry)

    # Internal Fetch Tools (Intranet URLs)
    from app.agent.internal_fetch_tools import register_internal_fetch_tools
    register_internal_fetch_tools(registry)

    # Datasource Tools (HTTP APIs)
    from app.agent.datasource_tools import register_datasource_tools
    register_datasource_tools(registry)

    # Docker/Podman Sandbox Tools (sichere Code-Ausführung)
    from app.agent.docker_tools import register_docker_tools
    register_docker_tools(registry)

    # Shell Execution Tools (Container-First Shell-Befehle)
    from app.agent.shell_tools import register_shell_tools
    register_shell_tools(registry)

    # Git Tools (lokale Git-Operationen)
    from app.agent.git_tools import register_git_tools
    register_git_tools(registry)

    # Graph Tools (lokale Code-Analyse über Knowledge Graph)
    from app.agent.graph_tools import register_graph_tools
    register_graph_tools(registry)

    # API Tools (SOAP/REST)
    from app.agent.api_tools import register_api_tools
    register_api_tools(registry)

    # Compile/Validate Tools
    from app.agent.compile_tools import register_compile_tools
    register_compile_tools(registry)

    # JUnit Test Generator Tools
    from app.agent.junit_tools import register_junit_tools
    register_junit_tools(registry)

    # ServiceNow Service Portal Tools
    try:
        from app.agent.servicenow_tools import register_servicenow_tools
        register_servicenow_tools(registry)
    except ImportError as e:
        import logging
        logging.getLogger(__name__).debug(f"ServiceNow tools not available: {e}")

    # Meta-Tools (kombinierte Operationen für effizientere Tool-Nutzung)
    from app.agent.meta_tools import register_meta_tools
    register_meta_tools(registry)

    # Script Execution Tools (Python-Script-Generierung und -Ausführung)
    from app.agent.script_tools import register_script_tools
    register_script_tools(registry)

    return registry


# Singleton
_default_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Gibt die Standard Tool-Registry zurück."""
    global _default_registry
    if _default_registry is None:
        _default_registry = create_default_registry()
    return _default_registry
