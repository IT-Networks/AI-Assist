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


class ToolCategory(str, Enum):
    """Kategorien von Tools."""
    SEARCH = "search"
    FILE = "file"
    KNOWLEDGE = "knowledge"
    ANALYSIS = "analysis"


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

    def register(self, tool: Tool) -> None:
        """Registriert ein Tool."""
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
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Unbekanntes Tool: {name}")

        if not tool.handler:
            return ToolResult(success=False, error=f"Tool {name} hat keinen Handler")

        try:
            return await tool.handler(**kwargs)
        except Exception as e:
            return ToolResult(success=False, error=f"Fehler bei {name}: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# Tool Implementations
# ══════════════════════════════════════════════════════════════════════════════

async def search_code(
    query: str,
    language: str = "all",
    top_k: int = 5
) -> ToolResult:
    """Durchsucht Code-Repositories nach relevanten Dateien."""
    from app.core.config import settings

    results = []

    # Java durchsuchen
    if language in ("all", "java") and settings.java.repo_path:
        try:
            from app.services.java_indexer import get_java_indexer
            indexer = get_java_indexer()
            if indexer.is_built():
                java_results = indexer.search(query, top_k=top_k)
                for r in java_results:
                    results.append({
                        "language": "java",
                        "file_path": r["file_path"],
                        "snippet": r["snippet"],
                    })
        except Exception:
            pass

    # Python durchsuchen
    if language in ("all", "python") and settings.python.repo_path:
        try:
            from app.services.python_indexer import get_python_indexer
            indexer = get_python_indexer()
            if indexer.is_built():
                py_results = indexer.search(query, top_k=top_k)
                for r in py_results:
                    results.append({
                        "language": "python",
                        "file_path": r["file_path"],
                        "snippet": r.get("snippet", ""),
                    })
        except Exception:
            pass

    if not results:
        return ToolResult(
            success=True,
            data="Keine relevanten Code-Dateien gefunden."
        )

    # Formatieren
    output = f"Gefundene Code-Dateien für '{query}':\n\n"
    for r in results[:top_k]:
        output += f"[{r['language'].upper()}] {r['file_path']}\n"
        if r.get('snippet'):
            output += f"  {r['snippet'][:200]}...\n\n"

    return ToolResult(success=True, data=output)


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


async def read_file(path: str, encoding: str = "utf-8") -> ToolResult:
    """Liest den Inhalt einer Datei."""
    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        result = await manager.read_file(path)
        return ToolResult(
            success=True,
            data=f"=== Datei: {path} ===\n{result.content}"
        )
    except PermissionError as e:
        return ToolResult(success=False, error=f"Zugriff verweigert: {e}")
    except FileNotFoundError as e:
        return ToolResult(success=False, error=f"Datei nicht gefunden: {e}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def list_files(
    path: str,
    pattern: str = "*",
    recursive: bool = False
) -> ToolResult:
    """Listet Dateien in einem Verzeichnis auf."""
    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        files = await manager.list_files(path, pattern=pattern, recursive=recursive)

        if not files:
            return ToolResult(success=True, data=f"Keine Dateien gefunden in {path}")

        output = f"Dateien in {path}:\n"
        for f in files[:50]:  # Max 50 Dateien
            output += f"  {f}\n"

        if len(files) > 50:
            output += f"\n  ... und {len(files) - 50} weitere"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def write_file(path: str, content: str) -> ToolResult:
    """
    Schreibt Inhalt in eine Datei (benötigt Bestätigung).
    """
    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        preview = await manager.write_file(path, content)

        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Datei wird erstellt/überschrieben: {path}",
            confirmation_data={
                "operation": "write_file",
                "path": path,
                "is_new": preview.is_new,
                "diff": preview.diff,
                "content": content,
            }
        )
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def edit_file(path: str, old_string: str, new_string: str) -> ToolResult:
    """
    Bearbeitet eine Datei durch String-Ersetzung (benötigt Bestätigung).
    """
    try:
        from app.services.file_manager import get_file_manager
        manager = get_file_manager()
        preview = await manager.edit_file(path, old_string, new_string)

        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Datei wird bearbeitet: {path}",
            confirmation_data={
                "operation": "edit_file",
                "path": path,
                "diff": preview.diff,
                "old_string": old_string,
                "new_string": new_string,
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


# ══════════════════════════════════════════════════════════════════════════════
# Tool Definitions
# ══════════════════════════════════════════════════════════════════════════════

SEARCH_CODE_TOOL = Tool(
    name="search_code",
    description="Durchsucht Java- und Python-Code nach relevanten Dateien basierend auf einem Suchbegriff. Gibt Dateipfade und Code-Snippets zurück.",
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff (Klassenname, Methode, Konzept)"),
        ToolParameter("language", "string", "Sprache: 'java', 'python' oder 'all'", required=False, default="all", enum=["java", "python", "all"]),
        ToolParameter("top_k", "integer", "Maximale Anzahl Ergebnisse", required=False, default=5),
    ],
    handler=search_code
)

SEARCH_HANDBOOK_TOOL = Tool(
    name="search_handbook",
    description="Durchsucht das Handbuch nach relevanten Service-Dokumentationen, Feldbeschreibungen und Aufrufvarianten.",
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
    description="Liest den kompletten Inhalt einer Datei. Pfad muss relativ zum Repository oder absolut sein.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter("path", "string", "Pfad zur Datei"),
        ToolParameter("encoding", "string", "Encoding der Datei", required=False, default="utf-8"),
    ],
    handler=read_file
)

LIST_FILES_TOOL = Tool(
    name="list_files",
    description="Listet Dateien in einem Verzeichnis auf. Unterstützt Glob-Patterns.",
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter("path", "string", "Verzeichnispfad"),
        ToolParameter("pattern", "string", "Glob-Pattern (z.B. '*.java')", required=False, default="*"),
        ToolParameter("recursive", "boolean", "Auch Unterverzeichnisse durchsuchen", required=False, default=False),
    ],
    handler=list_files
)

WRITE_FILE_TOOL = Tool(
    name="write_file",
    description="Erstellt oder überschreibt eine Datei. BENÖTIGT USER-BESTÄTIGUNG.",
    category=ToolCategory.FILE,
    is_write_operation=True,
    parameters=[
        ToolParameter("path", "string", "Pfad zur Datei"),
        ToolParameter("content", "string", "Neuer Dateiinhalt"),
    ],
    handler=write_file
)

EDIT_FILE_TOOL = Tool(
    name="edit_file",
    description="Bearbeitet eine Datei durch Ersetzen eines Strings. BENÖTIGT USER-BESTÄTIGUNG.",
    category=ToolCategory.FILE,
    is_write_operation=True,
    parameters=[
        ToolParameter("path", "string", "Pfad zur Datei"),
        ToolParameter("old_string", "string", "Zu ersetzender Text"),
        ToolParameter("new_string", "string", "Neuer Text"),
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

    # File Tools
    registry.register(READ_FILE_TOOL)
    registry.register(LIST_FILES_TOOL)
    registry.register(WRITE_FILE_TOOL)
    registry.register(EDIT_FILE_TOOL)

    # Knowledge Tools
    registry.register(GET_SERVICE_INFO_TOOL)

    return registry


# Singleton
_default_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Gibt die Standard Tool-Registry zurück."""
    global _default_registry
    if _default_registry is None:
        _default_registry = create_default_registry()
    return _default_registry
