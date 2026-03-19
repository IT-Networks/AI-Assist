"""
Knowledge Graph Service.

Speichert und verwaltet Code-Beziehungen (Klassen, Methoden, Abhängigkeiten)
als Graph-Struktur in SQLite.
"""

import json
import logging
import sqlite3
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class NodeType(str, Enum):
    """Typen von Knoten im Knowledge Graph."""
    CLASS = "class"
    INTERFACE = "interface"
    METHOD = "method"
    FIELD = "field"
    TABLE = "table"
    COLUMN = "column"
    FILE = "file"
    PACKAGE = "package"
    ENUM = "enum"
    ANNOTATION = "annotation"


class EdgeType(str, Enum):
    """Typen von Kanten im Knowledge Graph."""
    EXTENDS = "extends"           # class extends class
    IMPLEMENTS = "implements"     # class implements interface
    IMPORTS = "imports"           # file imports class
    CALLS = "calls"               # method calls method
    USES = "uses"                 # method uses field
    QUERIES = "queries"           # method queries table
    CONTAINS = "contains"         # package contains class
    DEPENDS_ON = "depends_on"     # generic dependency
    OVERRIDES = "overrides"       # method overrides parent
    REFERENCES = "references"     # generic reference
    ANNOTATED_BY = "annotated_by" # class/method has annotation
    RETURNS = "returns"           # method returns type


@dataclass
class GraphNode:
    """Ein Knoten im Knowledge Graph."""
    id: str                       # Unique ID (z.B. "com.example.UserService")
    type: NodeType
    name: str                     # Display name (z.B. "UserService")
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Metadata examples:
    # - class: {visibility, abstract, final, annotations}
    # - method: {visibility, static, return_type, parameters}
    # - table: {schema, columns, primary_key}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "metadata": self.metadata
        }


@dataclass
class GraphEdge:
    """Eine Kante im Knowledge Graph."""
    from_id: str
    to_id: str
    type: EdgeType
    weight: float = 1.0           # Stärke der Beziehung
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Metadata examples:
    # - calls: {count, in_loop}
    # - queries: {operation: SELECT|INSERT|UPDATE}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "type": self.type.value,
            "weight": self.weight,
            "metadata": self.metadata
        }


@dataclass
class SubGraph:
    """Ein Teilgraph für Visualisierung."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    center_node_id: Optional[str] = None
    depth: int = 2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "center_node_id": self.center_node_id,
            "depth": self.depth
        }


class KnowledgeGraphStore:
    """SQLite-basierter Graph-Speicher."""

    def __init__(self, db_path: str = "data/knowledge_graph.db"):
        self.db_path = db_path
        # Sicherstellen dass data/ Verzeichnis existiert
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialisiert das Datenbankschema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    file_path TEXT,
                    line_number INTEGER,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(from_id, to_id, type)
                );

                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
                CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
                CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
                CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
                CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
            """)
        logger.debug(f"[KnowledgeGraph] Database initialized: {self.db_path}")

    def add_node(self, node: GraphNode) -> bool:
        """Fügt einen Knoten hinzu oder aktualisiert ihn."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO nodes
                    (id, type, name, file_path, line_number, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    node.id,
                    node.type.value,
                    node.name,
                    node.file_path,
                    node.line_number,
                    json.dumps(node.metadata)
                ))
                return True
        except Exception as e:
            logger.error(f"[KnowledgeGraph] Failed to add node {node.id}: {e}")
            return False

    def add_edge(self, edge: GraphEdge) -> bool:
        """Fügt eine Kante hinzu."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO edges
                    (from_id, to_id, type, weight, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    edge.from_id,
                    edge.to_id,
                    edge.type.value,
                    edge.weight,
                    json.dumps(edge.metadata)
                ))
                return True
        except Exception as e:
            logger.error(f"[KnowledgeGraph] Failed to add edge {edge.from_id}->{edge.to_id}: {e}")
            return False

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Holt einen einzelnen Knoten."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT id, type, name, file_path, line_number, metadata
                FROM nodes WHERE id = ?
            """, (node_id,)).fetchone()

            if row:
                return GraphNode(
                    id=row[0],
                    type=NodeType(row[1]),
                    name=row[2],
                    file_path=row[3],
                    line_number=row[4],
                    metadata=json.loads(row[5]) if row[5] else {}
                )
            return None

    def get_edges_from(self, node_id: str,
                       edge_types: List[EdgeType] = None) -> List[GraphEdge]:
        """Holt alle ausgehenden Kanten eines Knotens."""
        with sqlite3.connect(self.db_path) as conn:
            if edge_types:
                placeholders = ",".join("?" * len(edge_types))
                rows = conn.execute(f"""
                    SELECT from_id, to_id, type, weight, metadata
                    FROM edges
                    WHERE from_id = ? AND type IN ({placeholders})
                """, [node_id] + [t.value for t in edge_types]).fetchall()
            else:
                rows = conn.execute("""
                    SELECT from_id, to_id, type, weight, metadata
                    FROM edges WHERE from_id = ?
                """, (node_id,)).fetchall()

            return [
                GraphEdge(
                    from_id=r[0],
                    to_id=r[1],
                    type=EdgeType(r[2]),
                    weight=r[3],
                    metadata=json.loads(r[4]) if r[4] else {}
                )
                for r in rows
            ]

    def get_edges_to(self, node_id: str,
                     edge_types: List[EdgeType] = None) -> List[GraphEdge]:
        """Holt alle eingehenden Kanten eines Knotens."""
        with sqlite3.connect(self.db_path) as conn:
            if edge_types:
                placeholders = ",".join("?" * len(edge_types))
                rows = conn.execute(f"""
                    SELECT from_id, to_id, type, weight, metadata
                    FROM edges
                    WHERE to_id = ? AND type IN ({placeholders})
                """, [node_id] + [t.value for t in edge_types]).fetchall()
            else:
                rows = conn.execute("""
                    SELECT from_id, to_id, type, weight, metadata
                    FROM edges WHERE to_id = ?
                """, (node_id,)).fetchall()

            return [
                GraphEdge(
                    from_id=r[0],
                    to_id=r[1],
                    type=EdgeType(r[2]),
                    weight=r[3],
                    metadata=json.loads(r[4]) if r[4] else {}
                )
                for r in rows
            ]

    def get_subgraph(self, center_id: str, depth: int = 2,
                     node_types: List[NodeType] = None,
                     edge_types: List[EdgeType] = None) -> SubGraph:
        """Holt einen Teilgraph um einen Knoten herum."""
        visited_nodes: Set[str] = set()
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        edge_set: Set[tuple] = set()  # Für Deduplizierung

        def traverse(node_id: str, current_depth: int):
            if current_depth > depth or node_id in visited_nodes:
                return

            visited_nodes.add(node_id)

            # Node holen
            node = self.get_node(node_id)
            if node:
                if not node_types or node.type in node_types:
                    nodes.append(node)

                # Nur wenn wir noch tiefer gehen dürfen
                if current_depth < depth:
                    # Ausgehende Kanten
                    for edge in self.get_edges_from(node_id, edge_types):
                        edge_key = (edge.from_id, edge.to_id, edge.type.value)
                        if edge_key not in edge_set:
                            edge_set.add(edge_key)
                            edges.append(edge)
                        traverse(edge.to_id, current_depth + 1)

                    # Eingehende Kanten
                    for edge in self.get_edges_to(node_id, edge_types):
                        edge_key = (edge.from_id, edge.to_id, edge.type.value)
                        if edge_key not in edge_set:
                            edge_set.add(edge_key)
                            edges.append(edge)
                        traverse(edge.from_id, current_depth + 1)

        traverse(center_id, 0)

        return SubGraph(
            nodes=nodes,
            edges=edges,
            center_node_id=center_id,
            depth=depth
        )

    def find_path(self, from_id: str, to_id: str,
                  max_depth: int = 5) -> List[GraphEdge]:
        """Findet den kürzesten Pfad zwischen zwei Knoten (BFS)."""
        queue = deque([(from_id, [])])
        visited = {from_id}

        while queue:
            current_id, path = queue.popleft()

            if current_id == to_id:
                return path

            if len(path) >= max_depth:
                continue

            for edge in self.get_edges_from(current_id):
                if edge.to_id not in visited:
                    visited.add(edge.to_id)
                    queue.append((edge.to_id, path + [edge]))

        return []  # Kein Pfad gefunden

    def search_nodes(self, query: str, node_types: List[NodeType] = None,
                     limit: int = 50) -> List[GraphNode]:
        """Sucht Knoten nach Name."""
        with sqlite3.connect(self.db_path) as conn:
            if node_types:
                placeholders = ",".join("?" * len(node_types))
                rows = conn.execute(f"""
                    SELECT id, type, name, file_path, line_number, metadata
                    FROM nodes
                    WHERE name LIKE ? AND type IN ({placeholders})
                    ORDER BY
                        CASE WHEN name = ? THEN 0
                             WHEN name LIKE ? THEN 1
                             ELSE 2 END,
                        name
                    LIMIT ?
                """, [f"%{query}%"] + [t.value for t in node_types] + [query, f"{query}%", limit]).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, type, name, file_path, line_number, metadata
                    FROM nodes
                    WHERE name LIKE ?
                    ORDER BY
                        CASE WHEN name = ? THEN 0
                             WHEN name LIKE ? THEN 1
                             ELSE 2 END,
                        name
                    LIMIT ?
                """, (f"%{query}%", query, f"{query}%", limit)).fetchall()

            return [
                GraphNode(
                    id=r[0],
                    type=NodeType(r[1]),
                    name=r[2],
                    file_path=r[3],
                    line_number=r[4],
                    metadata=json.loads(r[5]) if r[5] else {}
                )
                for r in rows
            ]

    def get_dependencies(self, node_id: str) -> List[GraphNode]:
        """Gibt alle Abhängigkeiten eines Knotens zurück (ausgehende Kanten)."""
        edges = self.get_edges_from(node_id)
        node_ids = {e.to_id for e in edges}
        return [self.get_node(nid) for nid in node_ids if self.get_node(nid)]

    def get_dependents(self, node_id: str) -> List[GraphNode]:
        """Gibt alle Knoten zurück, die von diesem abhängen (eingehende Kanten)."""
        edges = self.get_edges_to(node_id)
        node_ids = {e.from_id for e in edges}
        return [self.get_node(nid) for nid in node_ids if self.get_node(nid)]

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken über den Graph zurück."""
        with sqlite3.connect(self.db_path) as conn:
            node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

            # Nodes pro Typ
            node_types = conn.execute("""
                SELECT type, COUNT(*) FROM nodes GROUP BY type ORDER BY COUNT(*) DESC
            """).fetchall()

            # Edges pro Typ
            edge_types = conn.execute("""
                SELECT type, COUNT(*) FROM edges GROUP BY type ORDER BY COUNT(*) DESC
            """).fetchall()

            # Meistvernetzte Knoten
            top_nodes = conn.execute("""
                SELECT n.id, n.name, n.type,
                       (SELECT COUNT(*) FROM edges WHERE from_id = n.id) +
                       (SELECT COUNT(*) FROM edges WHERE to_id = n.id) as connections
                FROM nodes n
                ORDER BY connections DESC
                LIMIT 10
            """).fetchall()

            return {
                "total_nodes": node_count,
                "total_edges": edge_count,
                "nodes_by_type": {t[0]: t[1] for t in node_types},
                "edges_by_type": {t[0]: t[1] for t in edge_types},
                "top_connected_nodes": [
                    {"id": t[0], "name": t[1], "type": t[2], "connections": t[3]}
                    for t in top_nodes
                ]
            }

    def clear(self) -> None:
        """Löscht alle Daten aus dem Graph."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
        logger.info("[KnowledgeGraph] Graph cleared")

    def delete_by_file(self, file_path: str) -> int:
        """Löscht alle Knoten und Kanten einer Datei."""
        with sqlite3.connect(self.db_path) as conn:
            # Erst IDs holen
            node_ids = [r[0] for r in conn.execute(
                "SELECT id FROM nodes WHERE file_path = ?", (file_path,)
            ).fetchall()]

            if not node_ids:
                return 0

            placeholders = ",".join("?" * len(node_ids))

            # Kanten löschen
            conn.execute(f"""
                DELETE FROM edges
                WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})
            """, node_ids + node_ids)

            # Knoten löschen
            deleted = conn.execute(
                "DELETE FROM nodes WHERE file_path = ?", (file_path,)
            ).rowcount

            return deleted


# ══════════════════════════════════════════════════════════════════════════════
# Singleton-Accessor
# ══════════════════════════════════════════════════════════════════════════════

_store: Optional[KnowledgeGraphStore] = None


def get_knowledge_graph_store(db_path: str = "data/knowledge_graph.db") -> KnowledgeGraphStore:
    """Gibt die singleton KnowledgeGraphStore-Instanz zurück."""
    global _store
    if _store is None:
        _store = KnowledgeGraphStore(db_path)
    return _store
