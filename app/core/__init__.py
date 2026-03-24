"""
Core Module - Zentrale Komponenten und Abstraktionen.

Exports:
- protocols: Interface-Definitionen (Protocol classes)
- dependencies: FastAPI Dependency Injection factories
- lifespan: Application Lifecycle Management
- config: Anwendungskonfiguration
- exceptions: Custom Exceptions
"""

from app.core.protocols import (
    ICodeIndexer,
    IKnowledgeIndexer,
    IConfluenceClient,
    IJiraClient,
    IMemoryStore,
    ILLMClient,
    IToolRegistry,
    ICodeReader,
    IEventEmitter,
    ISessionStore,
    IAsyncSearchClient,
    IToolResult,
)

from app.core.dependencies import (
    get_settings,
    get_code_indexer,
    get_java_indexer,
    get_python_indexer,
    get_handbook_indexer,
    get_confluence_client,
    get_jira_client,
    get_memory_store,
    get_llm_client,
    get_tool_registry,
    get_event_bridge,
    # Type aliases for Depends()
    CodeIndexer,
    JavaIndexer,
    PythonIndexer,
    HandbookIndexer,
    ConfluenceClient,
    JiraClient,
    MemoryStore,
    LLMClient,
    ToolRegistry,
    EventBridge,
    SearchServicesDep,
    # Request-Scoped Dependencies
    SessionContext,
    SessionContextDep,
    AttachmentCollector,
    AttachmentCollectorDep,
    # Testing utilities
    create_mock_indexer,
    create_mock_memory_store,
)

from app.core.lifespan import (
    ServiceRegistry,
    get_service_registry,
    create_lifespan,
    get_system_health,
)

__all__ = [
    # Protocols
    "ICodeIndexer",
    "IKnowledgeIndexer",
    "IConfluenceClient",
    "IJiraClient",
    "IMemoryStore",
    "ILLMClient",
    "IToolRegistry",
    "ICodeReader",
    "IEventEmitter",
    "ISessionStore",
    "IAsyncSearchClient",
    "IToolResult",
    # Factory functions
    "get_settings",
    "get_code_indexer",
    "get_java_indexer",
    "get_python_indexer",
    "get_handbook_indexer",
    "get_confluence_client",
    "get_jira_client",
    "get_memory_store",
    "get_llm_client",
    "get_tool_registry",
    "get_event_bridge",
    # Type aliases
    "CodeIndexer",
    "JavaIndexer",
    "PythonIndexer",
    "HandbookIndexer",
    "ConfluenceClient",
    "JiraClient",
    "MemoryStore",
    "LLMClient",
    "ToolRegistry",
    "EventBridge",
    "SearchServicesDep",
    # Request-Scoped
    "SessionContext",
    "SessionContextDep",
    "AttachmentCollector",
    "AttachmentCollectorDep",
    # Lifespan
    "ServiceRegistry",
    "get_service_registry",
    "create_lifespan",
    "get_system_health",
    # Testing
    "create_mock_indexer",
    "create_mock_memory_store",
]
