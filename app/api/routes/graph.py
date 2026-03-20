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
    get_graph_registry,
    switch_graph,
    KnowledgeGraphStore,
    GraphNode,
    GraphEdge,
    SubGraph,
    NodeType,
    EdgeType,
    GraphInfo
)
from app.services.graph_query_service import get_graph_query_service
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


class GraphInfoResponse(BaseModel):
    id: str
    name: str
    path: str
    db_path: str
    created_at: str
    node_count: int = 0
    edge_count: int = 0


class CreateGraphRequest(BaseModel):
    name: str
    path: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# Multi-Graph Management Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/graphs", response_model=List[GraphInfoResponse])
async def list_graphs():
    """
    Listet alle verfügbaren Knowledge Graphs.
    """
    registry = get_graph_registry()
    return [
        GraphInfoResponse(
            id=g.id,
            name=g.name,
            path=g.path,
            db_path=g.db_path,
            created_at=g.created_at,
            node_count=g.node_count,
            edge_count=g.edge_count
        )
        for g in registry.list_graphs()
    ]


@router.get("/graphs/active")
async def get_active_graph():
    """
    Gibt den aktuell aktiven Graph zurück.
    """
    registry = get_graph_registry()
    active = registry.get_active()

    if not active:
        return {"active": None}

    return {
        "active": GraphInfoResponse(
            id=active.id,
            name=active.name,
            path=active.path,
            db_path=active.db_path,
            created_at=active.created_at,
            node_count=active.node_count,
            edge_count=active.edge_count
        )
    }


@router.post("/graphs", response_model=GraphInfoResponse)
async def create_graph(request: CreateGraphRequest):
    """
    Erstellt einen neuen Knowledge Graph.
    """
    registry = get_graph_registry()
    graph = registry.create_graph(request.name, request.path)

    return GraphInfoResponse(
        id=graph.id,
        name=graph.name,
        path=graph.path,
        db_path=graph.db_path,
        created_at=graph.created_at,
        node_count=graph.node_count,
        edge_count=graph.edge_count
    )


@router.post("/graphs/{graph_id}/activate")
async def activate_graph(graph_id: str):
    """
    Aktiviert einen Graph als aktuellen Arbeitsgraph.
    """
    store = switch_graph(graph_id)

    if not store:
        raise HTTPException(status_code=404, detail=f"Graph not found: {graph_id}")

    return {"status": "activated", "graph_id": graph_id}


@router.delete("/graphs/{graph_id}")
async def delete_graph(graph_id: str):
    """
    Löscht einen Knowledge Graph.

    WARNUNG: Diese Operation ist nicht rückgängig zu machen!
    """
    registry = get_graph_registry()

    if not registry.delete_graph(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph not found: {graph_id}")

    return {"status": "deleted", "graph_id": graph_id}


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
    clear: bool = Query(False, description="Vorhandenen Graph löschen"),
    graph_id: Optional[str] = Query(None, description="Graph ID (optional, verwendet aktiven)")
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

    # Wenn graph_id angegeben, zu diesem Graph wechseln
    if graph_id:
        store = switch_graph(graph_id)
        if not store:
            raise HTTPException(status_code=404, detail=f"Graph not found: {graph_id}")
    else:
        store = get_knowledge_graph_store()

    if clear:
        store.clear()

    builder = get_graph_builder(language, store)

    # Indexierung
    result = await builder.index_directory(directory)

    # Stats im Registry aktualisieren
    registry = get_graph_registry()
    active = registry.get_active()
    if active:
        stats = store.get_stats()
        registry.update_stats(active.id, stats["total_nodes"], stats["total_edges"])

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


# ══════════════════════════════════════════════════════════════════════════════
# Query Service Endpoints (Impact, Context, Path)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/impact/{node_id:path}")
async def get_impact_analysis(
    node_id: str,
    depth: int = Query(2, ge=1, le=5, description="Analysetiefe")
):
    """
    Impact-Analyse: Was ist betroffen wenn dieses Element geändert wird?

    Gibt zurück:
    - Direkte Abhängigkeiten
    - Transitive Abhängigkeiten
    - Betroffene Dateien
    - Risiko-Score
    """
    service = get_graph_query_service()
    result = await service.analyze_impact(node_id, max_depth=depth)
    return result.to_dict()


@router.get("/context/{node_id:path}")
async def get_element_context(node_id: str):
    """
    Reichhaltiger Kontext für ein Code-Element.

    Gibt zurück:
    - Vererbung (extends, implements)
    - Abhängigkeiten (uses, calls)
    - Verwender (used_by, called_by)
    - Datei-Position
    """
    service = get_graph_query_service()
    result = await service.get_context(node_id)

    if not result:
        raise HTTPException(status_code=404, detail=f"Element not found: {node_id}")

    return result.to_dict()


@router.get("/context/file")
async def get_file_context(path: str = Query(..., description="Dateipfad")):
    """
    Kontext für alle Elemente in einer Datei.
    """
    service = get_graph_query_service()
    results = await service.get_context_for_file(path)
    return [r.to_dict() for r in results]


@router.get("/connection")
async def find_connection(
    from_id: str = Query(..., alias="from", description="Start-Element"),
    to_id: str = Query(..., alias="to", description="Ziel-Element"),
    max_hops: int = Query(5, ge=1, le=10, description="Maximale Pfadlänge")
):
    """
    Findet die Verbindung zwischen zwei Code-Elementen.
    """
    service = get_graph_query_service()
    result = await service.find_connection(from_id, to_id, max_hops)
    return result.to_dict()


@router.post("/smart-search")
async def smart_search(
    query: str = Query(..., description="Suchanfrage"),
    type_filter: Optional[str] = Query(None, description="Typ-Filter (class, method, etc.)"),
    limit: int = Query(20, ge=1, le=100, description="Max. Ergebnisse")
):
    """
    Intelligente Suche mit natürlicher Sprache.

    Unterstützt Patterns wie:
    - "verwendet UserService" → findet alle Verwender
    - "implementiert PaymentGateway" → findet Implementierungen
    """
    service = get_graph_query_service()
    filters = {"type": type_filter} if type_filter else None
    results = await service.smart_search(query, filters, limit)

    return [
        NodeResponse(
            id=n.id,
            type=n.type.value,
            name=n.name,
            file_path=n.file_path,
            line_number=n.line_number,
            metadata=n.metadata
        )
        for n in results
    ]


@router.get("/dependents/{node_id:path}")
async def get_dependents_extended(
    node_id: str,
    include_tests: bool = Query(True, description="Auch Test-Klassen")
):
    """
    Zeigt alle Elemente die das angegebene Element verwenden.
    """
    service = get_graph_query_service()
    nodes = await service.get_dependents(node_id, include_tests=include_tests)

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
