"""
Protocol Definitions - Interface-Abstraktionen für lose Kopplung.

Dieses Modul definiert Protokolle (strukturelle Subtypen) für die
Hauptkomponenten des Systems. Sie ermöglichen:
- Dependency Injection ohne zirkuläre Imports
- Einfacheres Testing mit Mocks
- Klare Verträge zwischen Komponenten

Verwendung:
    from app.core.protocols import ICodeIndexer

    def process_query(indexer: ICodeIndexer, query: str):
        if indexer.is_built():
            return indexer.search(query, top_k=10)

Python 3.8+ Protocol-Pattern: Klassen müssen nicht explizit erben,
sie müssen nur die Methoden-Signaturen implementieren.
"""

from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)


# ══════════════════════════════════════════════════════════════════════════════
# Code Indexer Protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class ICodeIndexer(Protocol):
    """
    Interface für Code-Indexer (Java, Python, etc.).

    Implementiert von:
    - JavaIndexer
    - PythonIndexer

    Beispiel:
        indexer: ICodeIndexer = get_java_indexer()
        results = indexer.search("OrderService", top_k=5)
    """

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Volltextsuche über indexierte Dateien.

        Args:
            query: Suchbegriff (unterstützt FTS5-Syntax)
            top_k: Maximale Anzahl Ergebnisse

        Returns:
            Liste von Ergebnissen mit mindestens:
            - file_path: str
            - snippet: str (optional)
            - rank: float (optional)
        """
        ...

    def is_built(self) -> bool:
        """Prüft ob der Index existiert und Daten enthält."""
        ...

    def build(self, repo_path: str, reader: Any, force: bool = False) -> Dict[str, Any]:
        """
        Baut oder aktualisiert den Index.

        Args:
            repo_path: Pfad zum Repository
            reader: Reader-Instanz (JavaReader/PythonReader)
            force: True = alle Dateien neu indexieren

        Returns:
            Build-Statistiken (indexed, skipped, errors, duration_s)
        """
        ...


@runtime_checkable
class IKnowledgeIndexer(Protocol):
    """
    Interface für Wissens-Indexer (Handbook, Skills, etc.).

    Implementiert von:
    - HandbookIndexer
    - SkillManager (für Knowledge-Suche)
    """

    def search(
        self,
        query: str,
        service_filter: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Sucht im Wissensindex.

        Args:
            query: Suchbegriff
            service_filter: Optional - filtert nach Service/Kategorie
            top_k: Maximale Anzahl Ergebnisse

        Returns:
            Liste von Ergebnissen mit:
            - file_path: str
            - title: str
            - snippet: str
            - rank: float
        """
        ...

    def is_built(self) -> bool:
        """Prüft ob der Index existiert und Daten enthält."""
        ...

    def get_page_content(self, file_path: str) -> Optional[str]:
        """Lädt den vollständigen Inhalt einer Seite."""
        ...


# ══════════════════════════════════════════════════════════════════════════════
# External Client Protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class IAsyncSearchClient(Protocol):
    """
    Interface für asynchrone Such-Clients.

    Implementiert von:
    - ConfluenceClient
    - JiraClient
    """

    async def search(
        self,
        query: str,
        max_results: int = 20,
        **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """
        Führt eine asynchrone Suche durch.

        Args:
            query: Suchbegriff oder JQL
            max_results: Maximale Anzahl Ergebnisse
            **kwargs: Client-spezifische Parameter

        Returns:
            Liste von Ergebnissen
        """
        ...


@runtime_checkable
class IConfluenceClient(Protocol):
    """
    Interface für Confluence-Client.

    Implementiert von:
    - ConfluenceClient
    """

    async def search(
        self,
        query: str,
        space_key: Optional[str] = None,
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """Sucht Confluence-Seiten."""
        ...

    async def get_page_by_id(self, page_id: str) -> Dict[str, Any]:
        """Lädt eine Seite nach ID."""
        ...

    async def get_page_content(self, page_id: str) -> str:
        """Lädt den Inhalt einer Seite."""
        ...


@runtime_checkable
class IJiraClient(Protocol):
    """
    Interface für JIRA-Client.

    Implementiert von:
    - JiraClient
    """

    async def search(
        self,
        jql: str,
        max_results: int = 20,
        fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Sucht Issues per JQL."""
        ...

    async def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Lädt ein Issue nach Key."""
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Storage Protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class ISessionStore(Protocol):
    """
    Interface für Session-Speicherung.

    Ermöglicht austauschbare Backends (Memory, SQLite, Redis).
    """

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Lädt Session-Daten."""
        ...

    def save(self, session_id: str, data: Dict[str, Any]) -> None:
        """Speichert Session-Daten."""
        ...

    def delete(self, session_id: str) -> bool:
        """Löscht eine Session. Gibt True zurück wenn existierte."""
        ...

    def exists(self, session_id: str) -> bool:
        """Prüft ob Session existiert."""
        ...


@runtime_checkable
class IMemoryStore(Protocol):
    """
    Interface für Memory-Store (hierarchisches Wissen).

    Implementiert von:
    - MemoryStore

    Hinweis: Methoden sind async in der echten Implementierung.
    """

    async def remember(
        self,
        key: str,
        value: str,
        category: str = "fact",
        scope: str = "session",
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        importance: float = 0.5,
        **kwargs: Any
    ) -> str:
        """
        Speichert einen Memory-Eintrag.

        Returns:
            ID des gespeicherten Eintrags
        """
        ...

    async def recall(
        self,
        query: str,
        scopes: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        top_k: int = 10
    ) -> List[Any]:
        """Sucht in Memories nach Schlüsselwörtern."""
        ...

    async def forget(
        self,
        memory_id: str
    ) -> bool:
        """Löscht einen Memory-Eintrag."""
        ...


# ══════════════════════════════════════════════════════════════════════════════
# LLM Client Protocol
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class ILLMClient(Protocol):
    """
    Interface für LLM-Client.

    Ermöglicht austauschbare LLM-Backends.
    """

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        """
        Sendet Chat-Request an LLM.

        Args:
            messages: Liste von {role, content}
            model: Optional - überschreibt Standardmodell

        Returns:
            Antwort-Text
        """
        ...

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs: Any
    ):
        """
        Streaming Chat-Request.

        Yields:
            Token-Strings
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Tool Protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class IToolRegistry(Protocol):
    """
    Interface für Tool-Registry.

    Implementiert von:
    - ToolRegistry
    """

    def get_definitions(self, mode: Any) -> List[Dict[str, Any]]:
        """Gibt Tool-Definitionen im OpenAI-Format zurück."""
        ...

    async def execute(self, tool_call: Any) -> Any:
        """Führt einen Tool-Call aus."""
        ...

    def register(self, tool: Any) -> None:
        """Registriert ein neues Tool."""
        ...


@runtime_checkable
class IToolResult(Protocol):
    """
    Interface für Tool-Ergebnisse.

    Implementiert von:
    - ToolResult
    """

    success: bool
    data: Any
    error: Optional[str]

    def to_context(self) -> str:
        """Konvertiert zu String für LLM-Kontext."""
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Code Reader Protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class ICodeReader(Protocol):
    """
    Interface für Code-Reader (Java, Python).

    Implementiert von:
    - JavaReader
    - PythonReader
    """

    def read_file(self, rel_path: str) -> str:
        """Liest eine Datei relativ zum Repo-Root."""
        ...

    def list_files(self, pattern: str = "**/*") -> List[str]:
        """Listet Dateien nach Pattern."""
        ...

    @property
    def base_path(self) -> str:
        """Basis-Pfad des Repositories."""
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Event Protocols
# ══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class IEventEmitter(Protocol):
    """
    Interface für Event-Emitter.

    Implementiert von:
    - MCPEventBridge
    """

    async def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emittiert ein Event."""
        ...

    def subscribe(self, callback: Any) -> None:
        """Registriert einen Callback."""
        ...


# ══════════════════════════════════════════════════════════════════════════════
# Type Aliases for Convenience
# ══════════════════════════════════════════════════════════════════════════════

# Für Type Hints in Funktions-Signaturen
SearchResult = Dict[str, Any]
BuildStats = Dict[str, Any]
