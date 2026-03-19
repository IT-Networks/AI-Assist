"""
Knowledge Graph API Routes.

Endpoints für Graph-Abfragen und -Visualisierung.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from app.services.knowledge_graph import (
    get_knowledge_graph_store,
    KnowledgeGraphStore,
    GraphNode,
    GraphEdge,
    SubGraph,
    NodeType,
    EdgeType
)
from app.services.graph_builder import (
    get_graph_builder,
    IndexResult
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


# ══════════════════════════════════════════════════════════════════════════════
# Response Models
# ══════════════════════════════════════════════════════════════════════════════

class NodeResponse(BaseModel):
    id: str
    type: str
    name: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    metadata: Dict[str, Any] = {}


class EdgeResponse(BaseModel):
    from_id: str
    to_id: str
    type: str
    weight: float = 1.0
    metadata: Dict[str, Any] = {}


class SubGraphResponse(BaseModel):
    nodes: List[NodeResponse]
    edges: List[EdgeResponse]
    center_node_id: Optional[str] = None
    depth: int = 2


class PathResponse(BaseModel):
    edges: List[EdgeResponse]
    length: int


class IndexResponse(BaseModel):
    status: str
    files_processed: int = 0
    nodes_added: int = 0
    edges_added: int = 0
    errors: List[str] = []


class StatsResponse(BaseModel):
    total_nodes: int
    total_edges: int
    nodes_by_type: Dict[str, int]
    edges_by_type: Dict[str, int]
    top_connected_nodes: List[Dict[str, Any]]


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/node/{node_id:path}", response_model=NodeResponse)
async def get_node(node_id: str):
    """
    Einzelner Knoten mit Metadata.

    Args:
        node_id: Vollqualifizierte Node-ID (z.B. "com.example.UserService")
    """
    store = get_knowledge_graph_store()
    node = store.get_node(node_id)

    if not node:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    return NodeResponse(
        id=node.id,
        type=node.type.value,
        name=node.name,
        file_path=node.file_path,
        line_number=node.line_number,
        metadata=node.metadata
    )


@router.get("/subgraph", response_model=SubGraphResponse)
async def get_subgraph(
    center: str = Query(..., description="Zentrale Node-ID"),
    depth: int = Query(2, ge=1, le=5, description="Tiefe der Traversierung"),
    node_types: Optional[str] = Query(None, description="Kommagetrennte Node-Typen"),
    edge_types: Optional[str] = Query(None, description="Kommagetrennte Edge-Typen")
):
    """
    Teilgraph um einen Knoten herum.

    Gibt alle Knoten und Kanten im Umkreis von `depth` Schritten zurück.
    """
    store = get_knowledge_graph_store()

    # Typen parsen
    parsed_node_types = None
    if node_types:
        try:
            parsed_node_types = [NodeType(t.strip()) for t in node_types.split(",")]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid node type: {e}")

    parsed_edge_types = None
    if edge_types:
        try:
            parsed_edge_types = [EdgeType(t.strip()) for t in edge_types.split(",")]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid edge type: {e}")

    subgraph = store.get_subgraph(
        center_id=center,
        depth=depth,
        node_types=parsed_node_types,
        edge_types=parsed_edge_types
    )

    return SubGraphResponse(
        nodes=[
            NodeResponse(
                id=n.id,
                type=n.type.value,
                name=n.name,
                file_path=n.file_path,
                line_number=n.line_number,
                metadata=n.metadata
            )
            for n in subgraph.nodes
        ],
        edges=[
            EdgeResponse(
                from_id=e.from_id,
                to_id=e.to_id,
                type=e.type.value,
                weight=e.weight,
                metadata=e.metadata
            )
            for e in subgraph.edges
        ],
        center_node_id=subgraph.center_node_id,
        depth=subgraph.depth
    )


@router.get("/path", response_model=PathResponse)
async def find_path(
    from_id: str = Query(..., alias="from", description="Start-Node-ID"),
    to_id: str = Query(..., alias="to", description="Ziel-Node-ID"),
    max_depth: int = Query(5, ge=1, le=10, description="Maximale Pfadlänge")
):
    """
    Findet den kürzesten Pfad zwischen zwei Knoten.

    Verwendet Breadth-First Search (BFS).
    """
    store = get_knowledge_graph_store()

    edges = store.find_path(from_id, to_id, max_depth)

    return PathResponse(
        edges=[
            EdgeResponse(
                from_id=e.from_id,
                to_id=e.to_id,
                type=e.type.value,
                weight=e.weight,
                metadata=e.metadata
            )
            for e in edges
        ],
        length=len(edges)
    )


@router.get("/search", response_model=List[NodeResponse])
async def search_nodes(
    q: str = Query(..., min_length=1, description="Suchbegriff"),
    types: Optional[str] = Query(None, description="Kommagetrennte Node-Typen"),
    limit: int = Query(50, ge=1, le=200, description="Max. Ergebnisse")
):
    """
    Sucht Knoten nach Name.

    Unterstützt partielle Matches (LIKE %query%).
    """
    store = get_knowledge_graph_store()

    parsed_types = None
    if types:
        try:
            parsed_types = [NodeType(t.strip()) for t in types.split(",")]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid node type: {e}")

    nodes = store.search_nodes(q, parsed_types, limit)

    return [
        NodeResponse(
            id=n.id,
            type=n.type.value,
            name=n.name,
            file_path=n.file_path,
            line_number=n.line_number,
            metadata=n.metadata
        )
        for n in nodes
    ]


@router.get("/dependencies/{node_id:path}", response_model=List[NodeResponse])
async def get_dependencies(node_id: str):
    """
    Gibt alle Abhängigkeiten (ausgehende Kanten) eines Knotens zurück.
    """
    store = get_knowledge_graph_store()
    nodes = store.get_dependencies(node_id)

    return [
        NodeResponse(
            id=n.id,
            type=n.type.value,
            name=n.name,
            file_path=n.file_path,
            line_number=n.line_number,
            metadata=n.metadata
        )
        for n in nodes
    ]


@router.get("/dependents/{node_id:path}", response_model=List[NodeResponse])
async def get_dependents(node_id: str):
    """
    Gibt alle Knoten zurück, die von diesem abhängen (eingehende Kanten).
    """
    store = get_knowledge_graph_store()
    nodes = store.get_dependents(node_id)

    return [
        NodeResponse(
            id=n.id,
            type=n.type.value,
            name=n.name,
            file_path=n.file_path,
            line_number=n.line_number,
            metadata=n.metadata
        )
        for n in nodes
    ]


@router.post("/index", response_model=IndexResponse)
async def reindex(
    background_tasks: BackgroundTasks,
    path: str = Query(..., description="Verzeichnis zum Indexieren"),
    language: str = Query("java", description="Sprache (java, python)"),
    clear: bool = Query(False, description="Vorhandenen Graph löschen")
):
    """
    Re-indexiert den Knowledge Graph.

    Scannt das angegebene Verzeichnis und baut den Graph auf.
    """
    directory = Path(path)

    if not directory.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    if not directory.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    store = get_knowledge_graph_store()

    if clear:
        store.clear()

    builder = get_graph_builder(language, store)

    # Indexierung im Hintergrund für große Projekte
    if language.lower() == "python":
        result = await builder.index_directory(directory)
    else:
        result = await builder.index_directory(directory)

    return IndexResponse(
        status="completed",
        files_processed=result.files_processed,
        nodes_added=result.nodes_added,
        edges_added=result.edges_added,
        errors=result.errors[:10]  # Maximal 10 Fehler zurückgeben
    )


@router.get("/stats", response_model=StatsResponse)
async def graph_stats():
    """
    Graph-Statistiken.

    Gibt Übersicht über Anzahl Nodes, Edges und Top-vernetzte Knoten.
    """
    store = get_knowledge_graph_store()
    stats = store.get_stats()

    return StatsResponse(
        total_nodes=stats["total_nodes"],
        total_edges=stats["total_edges"],
        nodes_by_type=stats["nodes_by_type"],
        edges_by_type=stats["edges_by_type"],
        top_connected_nodes=stats["top_connected_nodes"]
    )


@router.delete("/clear")
async def clear_graph():
    """
    Löscht den gesamten Graph.

    WARNUNG: Diese Operation ist nicht rückgängig zu machen!
    """
    store = get_knowledge_graph_store()
    store.clear()

    return {"status": "cleared", "message": "Knowledge Graph wurde gelöscht"}


@router.get("/edges/{node_id:path}")
async def get_edges(
    node_id: str,
    direction: str = Query("both", description="in, out, oder both")
):
    """
    Gibt alle Kanten eines Knotens zurück.
    """
    store = get_knowledge_graph_store()

    edges = []
    if direction in ("out", "both"):
        edges.extend(store.get_edges_from(node_id))
    if direction in ("in", "both"):
        edges.extend(store.get_edges_to(node_id))

    return [
        EdgeResponse(
            from_id=e.from_id,
            to_id=e.to_id,
            type=e.type.value,
            weight=e.weight,
            metadata=e.metadata
        )
        for e in edges
    ]
