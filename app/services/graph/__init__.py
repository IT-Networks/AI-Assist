"""
Graph Services - Knowledge Graph und Code-Analyse.

Dieses Paket gruppiert Services für Graph-basierte Analyse:
- KnowledgeGraph (Entity-Beziehungen)
- GraphBuilder
- GraphQueryService
- GraphAutoIndexer

Verwendung:
    from app.services.graph import get_knowledge_graph

    graph = get_knowledge_graph()
    related = graph.get_related_entities("OrderService")
"""

from app.services.knowledge_graph import (
    KnowledgeGraphStore,
    get_knowledge_graph_store,
    GraphRegistry,
    get_graph_registry,
)

from app.services.graph_builder import (
    JavaGraphBuilder,
    PythonGraphBuilder,
    get_graph_builder,
)

from app.services.graph_query_service import (
    GraphQueryService,
    get_graph_query_service,
)

# Note: graph_auto_indexer may not have exports - check if needed

__all__ = [
    # Knowledge Graph
    "KnowledgeGraphStore",
    "get_knowledge_graph_store",
    "GraphRegistry",
    "get_graph_registry",
    # Builder
    "JavaGraphBuilder",
    "PythonGraphBuilder",
    "get_graph_builder",
    # Query
    "GraphQueryService",
    "get_graph_query_service",
]
