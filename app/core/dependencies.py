"""
Dependency Injection - FastAPI Dependencies für lose Kopplung.

Dieses Modul stellt Factory-Funktionen für alle Services bereit,
die per FastAPI Depends() injiziert werden können. Dies ermöglicht:
- Vermeidung zirkulärer Imports
- Einfaches Mocking in Tests
- Zentrale Konfiguration von Service-Instanzen

Verwendung in Routes:
    from fastapi import Depends
    from app.core.dependencies import get_code_indexer, CodeIndexer

    @router.get("/search")
    async def search(
        query: str,
        indexer: CodeIndexer  # Automatisch injiziert
    ):
        return indexer.search(query)

Verwendung in Tests:
    from app.core.dependencies import get_code_indexer

    app.dependency_overrides[get_code_indexer] = lambda: MockIndexer()

Request-Scoped Dependencies:
    from app.core.dependencies import get_session_context, SessionContext

    @router.post("/chat")
    async def chat(
        session_id: str,
        context: SessionContext  # Pro-Request Instanz
    ):
        return context.memory.recall("...")
"""

from functools import lru_cache
from typing import Annotated, Optional, TYPE_CHECKING, Generator

from fastapi import Depends, Header, Query

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
)

if TYPE_CHECKING:
    from app.core.config import Settings


# ══════════════════════════════════════════════════════════════════════════════
# Settings Dependency
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache
def get_settings() -> "Settings":
    """
    Gibt die Anwendungs-Konfiguration zurück (Singleton).

    Cached für Performance, da Settings immutable sind.
    """
    from app.core.config import settings
    return settings


Settings = Annotated["Settings", Depends(get_settings)]


# ══════════════════════════════════════════════════════════════════════════════
# Code Indexer Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_java_indexer() -> ICodeIndexer:
    """Factory für Java-Indexer."""
    from app.services.java_indexer import get_java_indexer as _get
    return _get()


def get_python_indexer() -> ICodeIndexer:
    """Factory für Python-Indexer."""
    from app.services.python_indexer import get_python_indexer as _get
    return _get()


def get_code_indexer(language: Optional[str] = None) -> ICodeIndexer:
    """
    Factory für Code-Indexer basierend auf Sprache.

    Args:
        language: 'java' oder 'python'. Wenn None, wird aus Config gelesen.

    Returns:
        Passender Code-Indexer
    """
    if language is None:
        settings = get_settings()
        # Priorität: Java wenn aktiviert, sonst Python
        if getattr(settings, 'java', None) and settings.java.enabled:
            language = 'java'
        else:
            language = 'python'

    if language == 'java':
        return get_java_indexer()
    return get_python_indexer()


# Type Aliases für FastAPI Dependency Injection
JavaIndexer = Annotated[ICodeIndexer, Depends(get_java_indexer)]
PythonIndexer = Annotated[ICodeIndexer, Depends(get_python_indexer)]
CodeIndexer = Annotated[ICodeIndexer, Depends(get_code_indexer)]


# ══════════════════════════════════════════════════════════════════════════════
# Knowledge Indexer Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_handbook_indexer() -> IKnowledgeIndexer:
    """Factory für Handbook-Indexer."""
    from app.services.handbook_indexer import get_handbook_indexer as _get
    return _get()


HandbookIndexer = Annotated[IKnowledgeIndexer, Depends(get_handbook_indexer)]


# ══════════════════════════════════════════════════════════════════════════════
# External Client Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_confluence_client() -> IConfluenceClient:
    """Factory für Confluence-Client."""
    from app.services.confluence_client import get_confluence_client as _get
    return _get()


def get_jira_client() -> IJiraClient:
    """Factory für JIRA-Client."""
    from app.services.jira_client import get_jira_client as _get
    return _get()


ConfluenceClient = Annotated[IConfluenceClient, Depends(get_confluence_client)]
JiraClient = Annotated[IJiraClient, Depends(get_jira_client)]


# ══════════════════════════════════════════════════════════════════════════════
# Storage Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_memory_store() -> IMemoryStore:
    """Factory für Memory-Store."""
    from app.services.memory_store import get_memory_store as _get
    return _get()


MemoryStore = Annotated[IMemoryStore, Depends(get_memory_store)]


# ══════════════════════════════════════════════════════════════════════════════
# LLM Client Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_llm_client() -> ILLMClient:
    """Factory für LLM-Client."""
    from app.services.llm_client import llm_client
    return llm_client


LLMClient = Annotated[ILLMClient, Depends(get_llm_client)]


# ══════════════════════════════════════════════════════════════════════════════
# Tool Registry Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_tool_registry() -> IToolRegistry:
    """Factory für Tool-Registry."""
    from app.agent.tools import get_tool_registry as _get
    return _get()


ToolRegistry = Annotated[IToolRegistry, Depends(get_tool_registry)]


# ══════════════════════════════════════════════════════════════════════════════
# Code Reader Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_java_reader() -> ICodeReader:
    """Factory für Java-Reader."""
    from app.services.java_reader import JavaReader
    settings = get_settings()
    return JavaReader(settings.java.get_active_path())


def get_python_reader() -> ICodeReader:
    """Factory für Python-Reader."""
    from app.services.python_reader import PythonReader
    settings = get_settings()
    return PythonReader(
        settings.python.repo_path,
        exclude_dirs=settings.python.exclude_dirs,
        max_file_size_kb=settings.python.max_file_size_kb,
    )


JavaReader = Annotated[ICodeReader, Depends(get_java_reader)]
PythonReader = Annotated[ICodeReader, Depends(get_python_reader)]


# ══════════════════════════════════════════════════════════════════════════════
# Event Bridge Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_event_bridge() -> IEventEmitter:
    """Factory für Event-Bridge."""
    from app.mcp.event_bridge import get_event_bridge as _get
    return _get()


EventBridge = Annotated[IEventEmitter, Depends(get_event_bridge)]


# ══════════════════════════════════════════════════════════════════════════════
# Composite Dependencies (für komplexe Use Cases)
# ══════════════════════════════════════════════════════════════════════════════

class SearchServices:
    """
    Aggregiert alle Such-Services für einfache Injection.

    Verwendung:
        @router.get("/unified-search")
        async def search(services: SearchServicesDep):
            java_results = services.code_indexer.search(query)
            wiki_results = await services.confluence.search(query)
    """

    def __init__(
        self,
        code_indexer: ICodeIndexer,
        handbook_indexer: IKnowledgeIndexer,
        confluence: IConfluenceClient,
        memory: IMemoryStore,
    ):
        self.code_indexer = code_indexer
        self.handbook_indexer = handbook_indexer
        self.confluence = confluence
        self.memory = memory


def get_search_services(
    code_indexer: CodeIndexer,
    handbook: HandbookIndexer,
    confluence: ConfluenceClient,
    memory: MemoryStore,
) -> SearchServices:
    """Factory für aggregierte Such-Services."""
    return SearchServices(
        code_indexer=code_indexer,
        handbook_indexer=handbook,
        confluence=confluence,
        memory=memory,
    )


SearchServicesDep = Annotated[SearchServices, Depends(get_search_services)]


# ══════════════════════════════════════════════════════════════════════════════
# Testing Utilities
# ══════════════════════════════════════════════════════════════════════════════

def create_mock_indexer(search_results: list = None) -> ICodeIndexer:
    """
    Erstellt einen Mock-Indexer für Tests.

    Args:
        search_results: Vordefinierte Suchergebnisse

    Beispiel:
        app.dependency_overrides[get_java_indexer] = lambda: create_mock_indexer([
            {"file_path": "Test.java", "snippet": "class Test"}
        ])
    """
    class MockIndexer:
        def search(self, query: str, top_k: int = 5):
            return (search_results or [])[:top_k]

        def is_built(self):
            return True

        def build(self, repo_path, reader, force=False):
            return {"indexed": 0, "skipped": 0, "errors": 0}

    return MockIndexer()


def create_mock_memory_store() -> IMemoryStore:
    """Erstellt einen In-Memory Mock für Tests."""
    class MockMemoryStore:
        def __init__(self):
            self._store = {}

        async def remember(self, key, value, **kwargs):
            import uuid
            id_ = str(uuid.uuid4())
            self._store[id_] = {"id": id_, "key": key, "value": value, **kwargs}
            return id_

        async def recall(self, query, **kwargs):
            return [
                v for v in self._store.values()
                if query.lower() in v.get("value", "").lower()
            ]

        async def forget(self, memory_id):
            if memory_id in self._store:
                del self._store[memory_id]
                return True
            return False

        # Sync convenience methods for testing
        def remember_sync(self, key, value, **kwargs):
            import uuid
            id_ = str(uuid.uuid4())
            self._store[id_] = {"id": id_, "key": key, "value": value, **kwargs}
            return id_

        def recall_sync(self, query, **kwargs):
            return [
                v for v in self._store.values()
                if query.lower() in v.get("value", "").lower()
            ]

    return MockMemoryStore()


# ══════════════════════════════════════════════════════════════════════════════
# Request-Scoped Dependencies (pro Request instanziiert)
# ══════════════════════════════════════════════════════════════════════════════

class SessionContext:
    """
    Request-scoped Kontext für eine Session.

    Aggregiert alle session-bezogenen Services für einfachen Zugriff.
    Wird pro Request mit der session_id aus dem Request erstellt.

    Verwendung:
        @router.post("/chat/{session_id}")
        async def chat(context: SessionContext):
            memories = await context.memory.recall("...")
            context.transcript.log(...)
    """

    def __init__(
        self,
        session_id: str,
        memory: IMemoryStore,
        settings: "Settings",
    ):
        self.session_id = session_id
        self.memory = memory
        self._settings = settings
        self._task_tracker = None
        self._transcript_logger = None

    @property
    def task_tracker(self):
        """Lazy-loaded TaskTracker für die Session."""
        if self._task_tracker is None:
            from app.services.task_tracker import get_task_tracker
            self._task_tracker = get_task_tracker(self.session_id)
        return self._task_tracker

    @property
    def transcript_logger(self):
        """Lazy-loaded TranscriptLogger."""
        if self._transcript_logger is None:
            from app.services.transcript_logger import get_transcript_logger
            self._transcript_logger = get_transcript_logger()
        return self._transcript_logger


def get_session_context(
    session_id: str = Query(..., description="Session ID"),
    memory: MemoryStore = None,  # Will be injected
    settings: Settings = None,  # Will be injected
) -> SessionContext:
    """
    Factory für SessionContext - wird pro Request erstellt.

    Verwendet Query-Parameter für session_id, kann auch aus Header kommen.
    """
    # Resolve dependencies if not provided (for direct calls)
    if memory is None:
        memory = get_memory_store()
    if settings is None:
        settings = get_settings()

    return SessionContext(
        session_id=session_id,
        memory=memory,
        settings=settings,
    )


def get_session_context_from_header(
    x_session_id: str = Header(..., alias="X-Session-ID"),
    memory: MemoryStore = None,
    settings: Settings = None,
) -> SessionContext:
    """Factory für SessionContext aus Header."""
    if memory is None:
        memory = get_memory_store()
    if settings is None:
        settings = get_settings()

    return SessionContext(
        session_id=x_session_id,
        memory=memory,
        settings=settings,
    )


SessionContextDep = Annotated[SessionContext, Depends(get_session_context)]
SessionContextHeaderDep = Annotated[SessionContext, Depends(get_session_context_from_header)]


# ══════════════════════════════════════════════════════════════════════════════
# PDF & Log Store Dependencies (für Chat-Attachments)
# ══════════════════════════════════════════════════════════════════════════════

# In-Memory Stores (werden von Routes befüllt)
_pdf_store: dict = {}
_log_store: dict = {}


def get_pdf_store() -> dict:
    """Gibt den PDF-Store zurück."""
    return _pdf_store


def get_log_store() -> dict:
    """Gibt den Log-Store zurück."""
    return _log_store


PdfStore = Annotated[dict, Depends(get_pdf_store)]
LogStore = Annotated[dict, Depends(get_log_store)]


# ══════════════════════════════════════════════════════════════════════════════
# Agent Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_agent_orchestrator():
    """Factory für Agent-Orchestrator."""
    from app.agent import get_agent_orchestrator as _get
    return _get()


AgentOrchestrator = Annotated[object, Depends(get_agent_orchestrator)]


# ══════════════════════════════════════════════════════════════════════════════
# Skill Manager Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_skill_manager():
    """Factory für Skill-Manager."""
    from app.services.skill_manager import get_skill_manager as _get
    return _get()


SkillManager = Annotated[object, Depends(get_skill_manager)]


# ══════════════════════════════════════════════════════════════════════════════
# Analytics Dependencies
# ══════════════════════════════════════════════════════════════════════════════

def get_analytics_logger():
    """Factory für Analytics-Logger."""
    from app.services.analytics_logger import get_analytics_logger as _get
    return _get()


def get_token_tracker():
    """Factory für Token-Tracker."""
    from app.services.token_tracker import get_token_tracker as _get
    return _get()


AnalyticsLogger = Annotated[object, Depends(get_analytics_logger)]
TokenTracker = Annotated[object, Depends(get_token_tracker)]


# ══════════════════════════════════════════════════════════════════════════════
# Chat Context Builder (komplexe Attachment-Sammlung)
# ══════════════════════════════════════════════════════════════════════════════

class AttachmentCollector:
    """
    Sammelt Attachments für Chat-Kontext.

    Kapselt die Logik aus chat.py._collect_attachments() in einer
    testbaren, injizierbaren Klasse.

    Verwendung:
        @router.post("/chat")
        async def chat(
            collector: AttachmentCollectorDep,
            sources: ContextSources,
        ):
            attachments = await collector.collect(sources, user_message)
    """

    def __init__(
        self,
        java_indexer: ICodeIndexer,
        python_indexer: ICodeIndexer,
        handbook_indexer: IKnowledgeIndexer,
        pdf_store: dict,
        log_store: dict,
        settings: "Settings",
    ):
        self.java_indexer = java_indexer
        self.python_indexer = python_indexer
        self.handbook_indexer = handbook_indexer
        self.pdf_store = pdf_store
        self.log_store = log_store
        self.settings = settings

    async def collect_code_files(
        self,
        language: str,
        file_paths: list,
        auto_search: bool,
        query: str,
    ) -> list:
        """Sammelt Code-Dateien als Attachments."""
        from app.core.context_manager import ContextAttachment

        attachments = []
        paths = list(file_paths) if file_paths else []

        # Auto-Search wenn keine Pfade aber aktiviert
        if auto_search and not paths:
            indexer = self.java_indexer if language == "java" else self.python_indexer
            if indexer.is_built():
                results = indexer.search(query, top_k=self.settings.index.max_search_results)
                paths = [r["file_path"] for r in results]

        # Dateien lesen
        if paths:
            if language == "java":
                from app.services.java_reader import JavaReader
                reader = JavaReader(self.settings.java.get_active_path())
            else:
                from app.services.python_reader import PythonReader
                reader = PythonReader(
                    self.settings.python.repo_path,
                    exclude_dirs=self.settings.python.exclude_dirs,
                )

            for path in paths[:5]:
                try:
                    content = reader.read_file(path)
                    attachments.append(ContextAttachment(
                        label=f"DATEI: {path}",
                        content=content,
                        priority=1,
                    ))
                except Exception:
                    pass

        return attachments

    async def collect_pdf_attachments(self, pdf_ids: list, query: str) -> list:
        """Sammelt PDF-Inhalte als Attachments."""
        from app.core.context_manager import ContextAttachment

        attachments = []

        for pdf_id in pdf_ids[:3]:
            if pdf_id not in self.pdf_store:
                continue

            pdf_data = self.pdf_store[pdf_id]
            content = ""

            # Index-basierte Suche versuchen
            if query:
                try:
                    from app.services.pdf_indexer import get_pdf_indexer
                    indexer = get_pdf_indexer()
                    if indexer.has_pdf(pdf_id):
                        content = indexer.search(
                            pdf_id, query,
                            top_k=self.settings.index.max_search_results
                        )
                except Exception:
                    pass

            # Fallback: Volltext
            if not content:
                content = pdf_data.get("text", "")

            attachments.append(ContextAttachment(
                label=f"PDF: {pdf_data.get('filename', pdf_id)}",
                content=content,
                priority=4,
            ))

        return attachments


def get_attachment_collector(
    java_indexer: JavaIndexer,
    python_indexer: PythonIndexer,
    handbook: HandbookIndexer,
    pdf_store: PdfStore,
    log_store: LogStore,
    settings: Settings,
) -> AttachmentCollector:
    """Factory für AttachmentCollector mit allen Dependencies."""
    return AttachmentCollector(
        java_indexer=java_indexer,
        python_indexer=python_indexer,
        handbook_indexer=handbook,
        pdf_store=pdf_store,
        log_store=log_store,
        settings=settings,
    )


AttachmentCollectorDep = Annotated[AttachmentCollector, Depends(get_attachment_collector)]


# ══════════════════════════════════════════════════════════════════════════════
# Convenience Re-Exports
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Settings
    "get_settings",
    "Settings",
    # Code Indexers
    "get_java_indexer",
    "get_python_indexer",
    "get_code_indexer",
    "JavaIndexer",
    "PythonIndexer",
    "CodeIndexer",
    # Knowledge
    "get_handbook_indexer",
    "HandbookIndexer",
    # External
    "get_confluence_client",
    "get_jira_client",
    "ConfluenceClient",
    "JiraClient",
    # Storage
    "get_memory_store",
    "MemoryStore",
    # LLM
    "get_llm_client",
    "LLMClient",
    # Tools
    "get_tool_registry",
    "ToolRegistry",
    # Code Readers
    "get_java_reader",
    "get_python_reader",
    "JavaReader",
    "PythonReader",
    # Events
    "get_event_bridge",
    "EventBridge",
    # Composite
    "SearchServices",
    "SearchServicesDep",
    # Request-Scoped
    "SessionContext",
    "SessionContextDep",
    "SessionContextHeaderDep",
    # Stores
    "PdfStore",
    "LogStore",
    # Agent
    "AgentOrchestrator",
    "SkillManager",
    # Analytics
    "AnalyticsLogger",
    "TokenTracker",
    # Attachments
    "AttachmentCollector",
    "AttachmentCollectorDep",
    # Testing
    "create_mock_indexer",
    "create_mock_memory_store",
]
