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
    top_k: int = 5,
    read_files: bool = True
) -> ToolResult:
    """Durchsucht Code-Repositories und liest gefundene Dateien."""
    from app.core.config import settings
    from pathlib import Path
    import re

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
                        "repo_path": settings.java.repo_path
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
                        "repo_path": settings.python.repo_path
                    })
        except Exception:
            pass

    # SQL/SQLJ durchsuchen (einfache Dateisuche)
    if language in ("all", "sql", "sqlj"):
        repo_paths = []
        if settings.java.repo_path:
            repo_paths.append(Path(settings.java.repo_path))
        if settings.python.repo_path:
            repo_paths.append(Path(settings.python.repo_path))

        query_lower = query.lower()
        query_words = query_lower.split()

        for repo_path in repo_paths:
            if not repo_path.exists():
                continue

            # Suche SQL und SQLJ Dateien
            for ext in ("*.sql", "*.sqlj"):
                for sql_file in repo_path.rglob(ext):
                    # Skip excluded directories
                    exclude_dirs = settings.java.exclude_dirs if settings.java.repo_path else []
                    if any(ex in str(sql_file) for ex in exclude_dirs):
                        continue

                    try:
                        content = sql_file.read_text(encoding="utf-8", errors="replace")
                        content_lower = content.lower()

                        # Prüfen ob Query-Begriffe vorkommen
                        if any(word in content_lower for word in query_words):
                            # Relevanten Snippet extrahieren
                            snippet = ""
                            for word in query_words:
                                idx = content_lower.find(word)
                                if idx >= 0:
                                    start = max(0, idx - 50)
                                    end = min(len(content), idx + 150)
                                    snippet = content[start:end].strip()
                                    snippet = re.sub(r'\s+', ' ', snippet)
                                    break

                            results.append({
                                "language": "sql",
                                "file_path": str(sql_file.relative_to(repo_path)),
                                "snippet": snippet,
                                "repo_path": str(repo_path)
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

        # Datei lesen wenn gewünscht
        if read_files:
            try:
                full_path = Path(r['repo_path']) / r['file_path']
                if full_path.exists():
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    # Auf 10000 Zeichen pro Datei begrenzen
                    if len(content) > 10000:
                        content = content[:10000] + "\n... [Datei gekürzt]"
                    output += f"```{r['language']}\n{content}\n```\n\n"
                else:
                    output += f"  [Datei nicht lesbar]\n\n"
            except Exception as e:
                output += f"  [Fehler beim Lesen: {e}]\n\n"
        else:
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
    from app.core.config import settings
    from pathlib import Path

    # Versuche den Pfad aufzulösen
    resolved_path = path

    # Wenn relativer Pfad, versuche in Repos zu finden
    if not Path(path).is_absolute():
        # Java Repo
        if settings.java.repo_path:
            java_full = Path(settings.java.repo_path) / path
            if java_full.exists():
                resolved_path = str(java_full)
        # Python Repo
        if not Path(resolved_path).exists() and settings.python.repo_path:
            python_full = Path(settings.python.repo_path) / path
            if python_full.exists():
                resolved_path = str(python_full)

    try:
        # Direkt lesen ohne file_manager Permission-Check für Repo-Dateien
        file_path = Path(resolved_path)
        if not file_path.exists():
            return ToolResult(success=False, error=f"Datei nicht gefunden: {path}")

        content = file_path.read_text(encoding=encoding, errors="replace")

        # Auf 50000 Zeichen begrenzen
        if len(content) > 50000:
            content = content[:50000] + "\n\n... [Datei gekürzt, zu lang]"

        return ToolResult(
            success=True,
            data=f"=== Datei: {path} ===\n{content}"
        )
    except PermissionError as e:
        return ToolResult(success=False, error=f"Zugriff verweigert: {e}")
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

    if not settings.java.repo_path:
        return ToolResult(success=False, error="Java Repository nicht konfiguriert")

    repo_path = Path(settings.java.repo_path)
    if not repo_path.exists():
        return ToolResult(success=False, error=f"Repository nicht gefunden: {repo_path}")

    found_classes = {}
    visited = set()

    def find_java_file(name: str) -> Optional[Path]:
        """Findet eine Java-Datei nach Klassennamen."""
        # Einfache Klasse oder vollqualifizierter Name
        simple_name = name.split(".")[-1]
        for java_file in repo_path.rglob(f"{simple_name}.java"):
            # Prüfen ob in excluded dirs
            if any(ex in str(java_file) for ex in settings.java.exclude_dirs):
                continue
            return java_file
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
    BENÖTIGT USER-BESTÄTIGUNG vor Ausführung.
    Nur SELECT-Statements erlaubt (readonly).
    """
    from app.core.config import settings

    if not settings.database.enabled:
        return ToolResult(success=False, error="Datenbank ist nicht aktiviert")

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return ToolResult(success=False, error="DB-Client konnte nicht initialisiert werden")

        # Query validieren
        is_valid, error = client.validate_query(query)
        if not is_valid:
            return ToolResult(success=False, error=error)

        # Preview erstellen für Bestätigung
        preview = client.preview_query(query)

        return ToolResult(
            success=True,
            requires_confirmation=True,
            data=f"Datenbank-Abfrage bereit zur Ausführung",
            confirmation_data={
                "operation": "query_database",
                "query": preview.query,
                "query_type": preview.query_type,
                "tables": preview.tables,
                "description": preview.estimated_description,
                "max_rows": min(max_rows, settings.database.max_rows)
            }
        )
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

        tables = await client.get_tables(schema)

        if not tables:
            return ToolResult(success=True, data="Keine Tabellen gefunden")

        output = f"=== Tabellen im Schema {schema or client.schema} ===\n\n"
        for table in tables:
            output += f"  - {table}\n"

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
# Tool Definitions
# ══════════════════════════════════════════════════════════════════════════════

SEARCH_CODE_TOOL = Tool(
    name="search_code",
    description="Durchsucht Java-, Python- und SQL/SQLJ-Code nach relevanten Dateien und liest deren Inhalt. Gibt den vollständigen Code der gefundenen Dateien zurück.",
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff (Klassenname, Methode, Tabellenname, Konzept)"),
        ToolParameter("language", "string", "Sprache: 'java', 'python', 'sql', 'sqlj' oder 'all'", required=False, default="all", enum=["java", "python", "sql", "sqlj", "all"]),
        ToolParameter("top_k", "integer", "Maximale Anzahl Ergebnisse", required=False, default=3),
        ToolParameter("read_files", "boolean", "Ob Dateiinhalt gelesen werden soll (default: true)", required=False, default=True),
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

SEARCH_PDF_TOOL = Tool(
    name="search_pdf",
    description="Durchsucht hochgeladene PDF-Dokumente nach relevantem Text.",
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff"),
        ToolParameter("filename_filter", "string", "Optional: Nur PDFs mit diesem Namen durchsuchen", required=False),
        ToolParameter("top_k", "integer", "Maximale Anzahl Ergebnisse", required=False, default=5),
    ],
    handler=search_pdf
)

TRACE_JAVA_REFERENCES_TOOL = Tool(
    name="trace_java_references",
    description="Verfolgt Java-Klassenhierarchien und findet Interfaces, Parent-Klassen und deren Implementierungen im Repository.",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter("class_name", "string", "Name der Klasse (einfach oder vollqualifiziert)"),
        ToolParameter("include_interfaces", "boolean", "Interfaces verfolgen", required=False, default=True),
        ToolParameter("include_parent_classes", "boolean", "Parent-Klassen verfolgen", required=False, default=True),
        ToolParameter("max_depth", "integer", "Maximale Tiefe der Hierarchie", required=False, default=5),
    ],
    handler=trace_java_references
)

QUERY_DATABASE_TOOL = Tool(
    name="query_database",
    description="Führt eine SELECT-Abfrage auf der DB2-Datenbank aus. BENÖTIGT USER-BESTÄTIGUNG vor Ausführung. Nur SELECT-Statements erlaubt.",
    category=ToolCategory.SEARCH,
    is_write_operation=True,  # Benötigt Bestätigung
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
    registry.register(WRITE_FILE_TOOL)
    registry.register(EDIT_FILE_TOOL)

    # Knowledge Tools
    registry.register(GET_SERVICE_INFO_TOOL)

    # Analysis Tools
    registry.register(TRACE_JAVA_REFERENCES_TOOL)

    # Database Tools
    registry.register(QUERY_DATABASE_TOOL)
    registry.register(LIST_DATABASE_TABLES_TOOL)
    registry.register(DESCRIBE_DATABASE_TABLE_TOOL)

    return registry


# Singleton
_default_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Gibt die Standard Tool-Registry zurück."""
    global _default_registry
    if _default_registry is None:
        _default_registry = create_default_registry()
    return _default_registry
