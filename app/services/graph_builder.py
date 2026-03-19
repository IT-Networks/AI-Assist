"""
Graph Builder Service.

Analysiert Code-Dateien und baut den Knowledge Graph auf.
Unterstützt Java und Python.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.services.knowledge_graph import (
    KnowledgeGraphStore,
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
    get_knowledge_graph_store
)

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Ergebnis einer Indexierung."""
    files_processed: int = 0
    nodes_added: int = 0
    edges_added: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> Dict:
        return {
            "files_processed": self.files_processed,
            "nodes_added": self.nodes_added,
            "edges_added": self.edges_added,
            "errors": self.errors[:10] if self.errors else []
        }


class JavaGraphBuilder:
    """Baut Knowledge Graph aus Java-Code."""

    # Pre-compiled Regex patterns
    _RE_PACKAGE = re.compile(r"package\s+([\w.]+);")
    _RE_IMPORT = re.compile(r"import\s+(?:static\s+)?([\w.]+)(?:\.\*)?;")
    _RE_CLASS = re.compile(
        r"(?:@\w+(?:\([^)]*\))?\s+)*"
        r"(?:public|private|protected)?\s*"
        r"(?:abstract\s+|final\s+|static\s+)*"
        r"class\s+(\w+)"
        r"(?:\s*<[^>]+>)?"
        r"(?:\s+extends\s+(\w+))?"
        r"(?:\s+implements\s+([\w,\s<>]+))?"
    )
    _RE_INTERFACE = re.compile(
        r"(?:@\w+(?:\([^)]*\))?\s+)*"
        r"(?:public\s+)?interface\s+(\w+)"
        r"(?:\s*<[^>]+>)?"
        r"(?:\s+extends\s+([\w,\s<>]+))?"
    )
    _RE_ENUM = re.compile(
        r"(?:public\s+)?enum\s+(\w+)"
    )
    _RE_METHOD = re.compile(
        r"(?:@\w+(?:\([^)]*\))?\s+)*"
        r"(?:public|private|protected)\s+"
        r"(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
        r"(?:<[^>]+>\s+)?"
        r"(\w+(?:<[\w<>,\s?]+>)?)\s+"
        r"(\w+)\s*\(([^)]*)\)"
    )
    _RE_FIELD = re.compile(
        r"(?:private|protected|public)\s+"
        r"(?:static\s+)?(?:final\s+)?"
        r"(\w+(?:<[\w<>,\s?]+>)?)\s+"
        r"(\w+)\s*[;=]"
    )
    _RE_METHOD_CALL = re.compile(r"(\w+)\.(\w+)\s*\(")
    _RE_ANNOTATION = re.compile(r"@(\w+)")
    _RE_SQL_QUERY = re.compile(
        r"(?:\"[^\"]*(?:SELECT|INSERT|UPDATE|DELETE|FROM|JOIN|WHERE)[^\"]*\")|"
        r"(?:@Query\s*\([^)]*\"([^\"]+)\"[^)]*\))",
        re.IGNORECASE
    )
    _RE_TABLE_NAME = re.compile(r"(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)", re.IGNORECASE)

    def __init__(self, store: KnowledgeGraphStore = None):
        self.store = store or get_knowledge_graph_store()
        self._nodes_added = 0
        self._edges_added = 0

    def index_file(self, file_path: Path) -> int:
        """Indexiert eine Java-Datei und gibt Anzahl der Nodes zurück."""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"[GraphBuilder] Cannot read {file_path}: {e}")
            return 0

        nodes_before = self._nodes_added

        # Package extrahieren
        package_match = self._RE_PACKAGE.search(content)
        package = package_match.group(1) if package_match else ""

        # Imports sammeln für Auflösung
        imports: Dict[str, str] = {}
        for match in self._RE_IMPORT.finditer(content):
            import_path = match.group(1)
            simple_name = import_path.split(".")[-1]
            imports[simple_name] = import_path

        # Classes indexieren
        self._index_classes(content, package, imports, file_path)

        # Interfaces indexieren
        self._index_interfaces(content, package, imports, file_path)

        # Enums indexieren
        self._index_enums(content, package, file_path)

        return self._nodes_added - nodes_before

    def _index_classes(self, content: str, package: str,
                       imports: Dict[str, str], file_path: Path) -> None:
        """Indexiert Klassen aus dem Content."""
        for match in self._RE_CLASS.finditer(content):
            class_name = match.group(1)
            extends = match.group(2)
            implements = match.group(3)

            class_id = f"{package}.{class_name}" if package else class_name
            line_number = content[:match.start()].count('\n') + 1

            # Metadata extrahieren
            class_section = content[match.start():match.start() + 500]
            annotations = self._RE_ANNOTATION.findall(class_section[:match.end() - match.start()])

            # Node erstellen
            node = GraphNode(
                id=class_id,
                type=NodeType.CLASS,
                name=class_name,
                file_path=str(file_path),
                line_number=line_number,
                metadata={
                    "package": package,
                    "annotations": annotations[:5],
                    "abstract": "abstract" in content[max(0, match.start()-50):match.start()],
                    "final": "final" in content[max(0, match.start()-30):match.start()]
                }
            )
            if self.store.add_node(node):
                self._nodes_added += 1

            # Extends Edge
            if extends:
                extends_id = self._resolve_class(extends, package, imports)
                edge = GraphEdge(
                    from_id=class_id,
                    to_id=extends_id,
                    type=EdgeType.EXTENDS
                )
                if self.store.add_edge(edge):
                    self._edges_added += 1

            # Implements Edges
            if implements:
                for iface in implements.split(","):
                    iface = iface.strip()
                    # Generic-Parameter entfernen
                    if "<" in iface:
                        iface = iface.split("<")[0]
                    if iface:
                        iface_id = self._resolve_class(iface, package, imports)
                        edge = GraphEdge(
                            from_id=class_id,
                            to_id=iface_id,
                            type=EdgeType.IMPLEMENTS
                        )
                        if self.store.add_edge(edge):
                            self._edges_added += 1

            # Methods indexieren
            self._index_methods(content, class_id, package, imports, file_path)

    def _index_interfaces(self, content: str, package: str,
                          imports: Dict[str, str], file_path: Path) -> None:
        """Indexiert Interfaces aus dem Content."""
        for match in self._RE_INTERFACE.finditer(content):
            iface_name = match.group(1)
            extends = match.group(2)

            iface_id = f"{package}.{iface_name}" if package else iface_name
            line_number = content[:match.start()].count('\n') + 1

            # Node erstellen
            node = GraphNode(
                id=iface_id,
                type=NodeType.INTERFACE,
                name=iface_name,
                file_path=str(file_path),
                line_number=line_number,
                metadata={"package": package}
            )
            if self.store.add_node(node):
                self._nodes_added += 1

            # Extends Edges (Interfaces können mehrere Interfaces erweitern)
            if extends:
                for parent in extends.split(","):
                    parent = parent.strip()
                    if "<" in parent:
                        parent = parent.split("<")[0]
                    if parent:
                        parent_id = self._resolve_class(parent, package, imports)
                        edge = GraphEdge(
                            from_id=iface_id,
                            to_id=parent_id,
                            type=EdgeType.EXTENDS
                        )
                        if self.store.add_edge(edge):
                            self._edges_added += 1

    def _index_enums(self, content: str, package: str, file_path: Path) -> None:
        """Indexiert Enums aus dem Content."""
        for match in self._RE_ENUM.finditer(content):
            enum_name = match.group(1)
            enum_id = f"{package}.{enum_name}" if package else enum_name
            line_number = content[:match.start()].count('\n') + 1

            node = GraphNode(
                id=enum_id,
                type=NodeType.ENUM,
                name=enum_name,
                file_path=str(file_path),
                line_number=line_number,
                metadata={"package": package}
            )
            if self.store.add_node(node):
                self._nodes_added += 1

    def _index_methods(self, content: str, class_id: str, package: str,
                       imports: Dict[str, str], file_path: Path) -> None:
        """Indexiert Methoden einer Klasse."""
        for match in self._RE_METHOD.finditer(content):
            return_type = match.group(1)
            method_name = match.group(2)
            params = match.group(3)

            method_id = f"{class_id}.{method_name}"
            line_number = content[:match.start()].count('\n') + 1

            # Methoden-Body für Call-Analyse finden (vereinfacht)
            method_start = match.end()
            brace_count = 0
            method_end = method_start
            in_method = False
            for i, char in enumerate(content[method_start:method_start + 2000]):
                if char == '{':
                    brace_count += 1
                    in_method = True
                elif char == '}':
                    brace_count -= 1
                    if in_method and brace_count == 0:
                        method_end = method_start + i
                        break

            method_body = content[method_start:method_end]

            # SQL-Queries finden
            for sql_match in self._RE_SQL_QUERY.finditer(method_body):
                for table_match in self._RE_TABLE_NAME.finditer(sql_match.group(0)):
                    table_name = table_match.group(1)
                    edge = GraphEdge(
                        from_id=method_id,
                        to_id=f"table.{table_name}",
                        type=EdgeType.QUERIES,
                        metadata={"table": table_name}
                    )
                    if self.store.add_edge(edge):
                        self._edges_added += 1

            # Node für Methode erstellen
            node = GraphNode(
                id=method_id,
                type=NodeType.METHOD,
                name=method_name,
                file_path=str(file_path),
                line_number=line_number,
                metadata={
                    "return_type": return_type,
                    "parameters": params[:100],
                    "class": class_id
                }
            )
            if self.store.add_node(node):
                self._nodes_added += 1

            # Contains-Edge von Klasse zu Methode
            edge = GraphEdge(
                from_id=class_id,
                to_id=method_id,
                type=EdgeType.CONTAINS
            )
            if self.store.add_edge(edge):
                self._edges_added += 1

    def _resolve_class(self, class_name: str, current_package: str,
                       imports: Dict[str, str]) -> str:
        """Löst einen Klassennamen zu vollqualifiziertem Namen auf."""
        # Direkt in Imports?
        if class_name in imports:
            return imports[class_name]

        # Standard Java-Typen
        if class_name in {"String", "Integer", "Long", "Double", "Boolean",
                          "Object", "Class", "List", "Map", "Set", "Optional",
                          "Stream", "Collection", "void", "int", "long", "double",
                          "boolean", "byte", "char", "short", "float"}:
            return f"java.lang.{class_name}"

        # Gleiche Package
        return f"{current_package}.{class_name}" if current_package else class_name

    async def index_directory(self, directory: Path,
                              extensions: List[str] = None) -> IndexResult:
        """Indexiert ein komplettes Verzeichnis."""
        if extensions is None:
            extensions = [".java"]

        result = IndexResult()
        files = []

        for ext in extensions:
            files.extend(directory.rglob(f"*{ext}"))

        logger.info(f"[GraphBuilder] Indexing {len(files)} files from {directory}")

        for file_path in files:
            try:
                nodes = self.index_file(file_path)
                result.files_processed += 1
                result.nodes_added += nodes
            except Exception as e:
                result.errors.append(f"{file_path}: {str(e)}")
                logger.warning(f"[GraphBuilder] Error indexing {file_path}: {e}")

        result.edges_added = self._edges_added
        logger.info(f"[GraphBuilder] Indexed {result.nodes_added} nodes, {result.edges_added} edges")

        return result


class PythonGraphBuilder:
    """Baut Knowledge Graph aus Python-Code."""

    # Pre-compiled Regex patterns
    _RE_IMPORT = re.compile(r"^(?:from\s+([\w.]+)\s+)?import\s+([\w.,\s]+)", re.MULTILINE)
    _RE_CLASS = re.compile(
        r"^class\s+(\w+)(?:\(([\w,\s.]+)\))?:",
        re.MULTILINE
    )
    _RE_FUNCTION = re.compile(
        r"^(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([^:]+))?:",
        re.MULTILINE
    )
    _RE_DECORATOR = re.compile(r"^@(\w+)", re.MULTILINE)

    def __init__(self, store: KnowledgeGraphStore = None):
        self.store = store or get_knowledge_graph_store()
        self._nodes_added = 0
        self._edges_added = 0

    def index_file(self, file_path: Path) -> int:
        """Indexiert eine Python-Datei."""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"[GraphBuilder] Cannot read {file_path}: {e}")
            return 0

        nodes_before = self._nodes_added

        # Module-Name aus Pfad ableiten
        module = file_path.stem
        if file_path.parent.name != ".":
            module = f"{file_path.parent.name}.{module}"

        # Classes indexieren
        for match in self._RE_CLASS.finditer(content):
            class_name = match.group(1)
            bases = match.group(2)
            line_number = content[:match.start()].count('\n') + 1

            class_id = f"{module}.{class_name}"

            node = GraphNode(
                id=class_id,
                type=NodeType.CLASS,
                name=class_name,
                file_path=str(file_path),
                line_number=line_number,
                metadata={"module": module}
            )
            if self.store.add_node(node):
                self._nodes_added += 1

            # Vererbung
            if bases:
                for base in bases.split(","):
                    base = base.strip()
                    if base and base not in {"object", "ABC"}:
                        edge = GraphEdge(
                            from_id=class_id,
                            to_id=base,
                            type=EdgeType.EXTENDS
                        )
                        if self.store.add_edge(edge):
                            self._edges_added += 1

        # Top-level Funktionen indexieren
        for match in self._RE_FUNCTION.finditer(content):
            func_name = match.group(1)
            params = match.group(2)
            return_type = match.group(3)
            line_number = content[:match.start()].count('\n') + 1

            func_id = f"{module}.{func_name}"

            node = GraphNode(
                id=func_id,
                type=NodeType.METHOD,
                name=func_name,
                file_path=str(file_path),
                line_number=line_number,
                metadata={
                    "module": module,
                    "parameters": params[:100],
                    "return_type": return_type.strip() if return_type else None
                }
            )
            if self.store.add_node(node):
                self._nodes_added += 1

        return self._nodes_added - nodes_before

    async def index_directory(self, directory: Path) -> IndexResult:
        """Indexiert ein Python-Verzeichnis."""
        result = IndexResult()
        files = list(directory.rglob("*.py"))

        logger.info(f"[GraphBuilder] Indexing {len(files)} Python files from {directory}")

        for file_path in files:
            # __pycache__ überspringen
            if "__pycache__" in str(file_path):
                continue

            try:
                nodes = self.index_file(file_path)
                result.files_processed += 1
                result.nodes_added += nodes
            except Exception as e:
                result.errors.append(f"{file_path}: {str(e)}")

        result.edges_added = self._edges_added
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════════════════

def get_graph_builder(language: str = "java",
                      store: KnowledgeGraphStore = None) -> JavaGraphBuilder | PythonGraphBuilder:
    """Factory für Graph Builder."""
    if language.lower() == "python":
        return PythonGraphBuilder(store)
    return JavaGraphBuilder(store)
