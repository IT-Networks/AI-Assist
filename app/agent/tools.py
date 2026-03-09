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

        # Required-Parameter prüfen bevor der Handler aufgerufen wird.
        # Verhindert dass Python-TypeErrors als kryptische Fehlermeldungen beim LLM ankommen.
        missing = [p.name for p in tool.parameters if p.required and p.name not in kwargs]
        if missing:
            return ToolResult(
                success=False,
                error=(
                    f"Tool '{name}': Pflichtparameter fehlen: {', '.join(missing)}. "
                    f"Bitte erneut aufrufen und alle Pflichtparameter angeben."
                )
            )

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
    if language in ("all", "java") and settings.java.get_active_path():
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
                        "repo_path": settings.java.get_active_path()
                    })
        except Exception:
            pass

    # Python durchsuchen
    if language in ("all", "python") and settings.python.get_active_path():
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
                        "repo_path": settings.python.get_active_path()
                    })
        except Exception:
            pass

    # SQL/SQLJ durchsuchen (einfache Dateisuche)
    if language in ("all", "sql", "sqlj"):
        repo_paths = []
        if settings.java.get_active_path():
            repo_paths.append(Path(settings.java.get_active_path()))
        if settings.python.get_active_path():
            repo_paths.append(Path(settings.python.get_active_path()))

        query_lower = query.lower()
        query_words = query_lower.split()

        for repo_path in repo_paths:
            if not repo_path.exists():
                continue

            # Suche SQL und SQLJ Dateien
            for ext in ("*.sql", "*.sqlj"):
                for sql_file in repo_path.rglob(ext):
                    # Skip excluded directories
                    exclude_dirs = settings.java.exclude_dirs if settings.java.get_active_path() else []
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
                    # Vollständig lesen, nur in Ausgabe Hinweis auf Länge
                    char_count = len(content)
                    output += f"```{r['language']}\n{content}\n```\n"
                    output += f"[{char_count:,} Zeichen]\n\n"
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
        if settings.java.get_active_path():
            java_full = Path(settings.java.get_active_path()) / path
            if java_full.exists():
                resolved_path = str(java_full)
        # Python Repo
        if not Path(resolved_path).exists() and settings.python.get_active_path():
            python_full = Path(settings.python.get_active_path()) / path
            if python_full.exists():
                resolved_path = str(python_full)

    try:
        # Direkt lesen ohne file_manager Permission-Check für Repo-Dateien
        file_path = Path(resolved_path)
        if not file_path.exists():
            return ToolResult(success=False, error=f"Datei nicht gefunden: {path}")

        content = file_path.read_text(encoding=encoding, errors="replace")
        char_count = len(content)

        # Vollständig lesen - keine Kürzung mehr
        return ToolResult(
            success=True,
            data=f"=== Datei: {path} ({char_count:,} Zeichen) ===\n{content}"
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

    if not class_name or not class_name.strip():
        return ToolResult(
            success=False,
            error=(
                "class_name fehlt. Bitte den genauen Java-Klassennamen angeben, "
                "z.B. 'CustomerService' oder 'com.example.CustomerService'. "
                "Frage den Nutzer nach dem Klassennamen bevor du dieses Tool erneut aufrufst."
            )
        )

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
    description="Findet Java/Python/SQL-Dateien per Volltextsuche. Nutze für: Klassennamen, Methodennamen, SQL-Patterns, Konzepte.",
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
    description="Liest Datei vollständig. Pfad: relativ zum Repo-Root (z.B. 'src/Main.java') oder absolut.",
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
    description="Liest eine SQLJ-Datei und extrahiert alle SQL-Statements (#sql { ... }) mit Methoden-Kontext und Host-Variablen (:varName). Ideal um SQL zu verstehen das ein Java-Service ausführt.",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter("path", "string", "Pfad zur SQLJ-Datei (relativ zum Repository oder absolut)"),
    ],
    handler=read_sqlj_file
)

DEBUG_JAVA_TESTDATA_TOOL = Tool(
    name="debug_java_with_testdata",
    description="Führt Java-Service mit Testdaten aus: liest Code → SQLJ → substituiert Parameter → SQL-Ergebnis. PFLICHT: class_name angeben.",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter("class_name", "string", "PFLICHT: Java-Klassenname (z.B. 'CustomerService'). Tool schlägt fehl wenn leer — zuerst search_code aufrufen um den Namen zu ermitteln."),
        ToolParameter("method_name", "string", "Name der Methode (optional, filtert SQL auf diese Methode)", required=False, default=""),
        ToolParameter("test_parameters", "object", "Testdaten als Key-Value-Objekt, z.B. {\"customerId\": \"12345\", \"date\": \"2024-01-01\"}. Keys entsprechen den SQLJ-Host-Variablen (:varName)", required=False, default={}),
    ],
    handler=debug_java_with_testdata
)

TRACE_JAVA_REFERENCES_TOOL = Tool(
    name="trace_java_references",
    description="Findet Interfaces, Parent-Klassen und Implementierungen. Nutze NACH search_code wenn Vererbung relevant ist.",
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
    from app.services.confluence_client import ConfluenceClient
    from app.core.config import settings

    if not settings.confluence.base_url:
        return ToolResult(success=False, error="Confluence ist nicht konfiguriert (base_url fehlt)")

    try:
        client = ConfluenceClient()
        results = await client.search(
            query=query,
            space_key=space or settings.confluence.default_space or None,
            limit=min(limit, 20),
        )

        if not results:
            return ToolResult(success=True, data=f"Keine Confluence-Ergebnisse für: {query}")

        output = f"=== Confluence-Suche: {query} ===\n"
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
    from app.services.confluence_client import ConfluenceClient
    from app.core.config import settings

    if not settings.confluence.base_url:
        return ToolResult(success=False, error="Confluence ist nicht konfiguriert (base_url fehlt)")

    try:
        client = ConfluenceClient()
        page = await client.get_page_by_id(page_id)

        output = f"=== Confluence-Seite ===\n"
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


# ── Jira Tools ──

async def search_jira(query: str, project: str = "", max_results: int = 15) -> ToolResult:
    """Durchsucht Jira per JQL oder Freitext."""
    from app.services.jira_client import get_jira_client
    from app.core.config import settings

    if not settings.jira.enabled or not settings.jira.base_url:
        return ToolResult(success=False, error="Jira ist nicht konfiguriert oder deaktiviert")

    try:
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
            output += f"   URL: {r['url']}\n\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Jira-Fehler: {str(e)}")


async def read_jira_issue(issue_key: str) -> ToolResult:
    """Liest ein einzelnes Jira-Issue mit Details und Kommentaren."""
    from app.services.jira_client import get_jira_client
    from app.core.config import settings

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
        output += f"URL: {issue['url']}\n"
        output += f"\n--- Beschreibung ---\n{issue['description'] or '(keine Beschreibung)'}\n"

        if issue['comments']:
            output += f"\n--- Kommentare ({len(issue['comments'])}) ---\n"
            for c in issue['comments']:
                output += f"\n[{c['created'][:10] if c['created'] else '?'}] {c['author']}:\n{c['body']}\n"

        return ToolResult(success=True, data=output)
    except Exception as e:
        return ToolResult(success=False, error=f"Jira-Fehler: {str(e)}")


SEARCH_JIRA_TOOL = Tool(
    name="search_jira",
    description="Durchsucht Jira nach Issues. Unterstützt JQL-Queries und Freitext-Suche. Gibt Issue-Keys, Titel, Status und Zuweisungen zurück.",
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter("query", "string", "Suchbegriff oder JQL-Query (z.B. 'project=PROJ AND status=Open')"),
        ToolParameter("project", "string", "Optional: Projekt-Key für Freitext-Suche", required=False, default=""),
        ToolParameter("max_results", "integer", "Maximale Anzahl Ergebnisse", required=False, default=15),
    ],
    handler=search_jira
)

READ_JIRA_ISSUE_TOOL = Tool(
    name="read_jira_issue",
    description="Liest ein einzelnes Jira-Issue mit vollständiger Beschreibung, Details und den letzten Kommentaren.",
    category=ToolCategory.KNOWLEDGE,
    parameters=[
        ToolParameter("issue_key", "string", "Issue-Schlüssel (z.B. 'PROJ-123')"),
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
    registry.register(WRITE_FILE_TOOL)
    registry.register(EDIT_FILE_TOOL)

    # Knowledge Tools
    registry.register(GET_SERVICE_INFO_TOOL)
    registry.register(GET_PDF_INFO_TOOL)
    registry.register(READ_PDF_PAGES_TOOL)

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

    # Jira Tools
    registry.register(SEARCH_JIRA_TOOL)
    registry.register(READ_JIRA_ISSUE_TOOL)

    # Debug-Modus: Interaktives Rückfrage-Tool
    registry.register(SUGGEST_ANSWERS_TOOL)

    return registry


# Singleton
_default_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Gibt die Standard Tool-Registry zurück."""
    global _default_registry
    if _default_registry is None:
        _default_registry = create_default_registry()
    return _default_registry
