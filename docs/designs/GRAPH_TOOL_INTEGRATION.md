# Knowledge Graph Tool Integration

## Design-Dokument v1.0

**Ziel**: Knowledge Graph als intelligente Datenquelle für alle Tools, Agents und APIs.

---

## Architektur-Übersicht

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER / FRONTEND                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   REST API   │  │  MCP Server  │  │    Agent     │  │  Sub-Agents  │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │                 │            │
│         └─────────────────┴─────────────────┴─────────────────┘            │
│                                    │                                        │
│                        ┌───────────▼───────────┐                           │
│                        │   GraphQueryService   │  ← Einheitliche Query-API │
│                        └───────────┬───────────┘                           │
│                                    │                                        │
│    ┌───────────────────────────────┼───────────────────────────────┐       │
│    │                               │                               │       │
│    ▼                               ▼                               ▼       │
│ ┌──────────────┐          ┌──────────────┐          ┌──────────────┐       │
│ │ImpactAnalyzer│          │ PathFinder   │          │SmartSearch   │       │
│ └──────────────┘          └──────────────┘          └──────────────┘       │
│                                    │                                        │
│                        ┌───────────▼───────────┐                           │
│                        │  KnowledgeGraphStore  │  ← SQLite (Multi-DB)      │
│                        └───────────────────────┘                           │
│                                                                             │
│    ┌───────────────────────────────────────────────────────────────┐       │
│    │                     FileChangeTracker                         │       │
│    │  • Trackt Änderungen während Tool-Ausführung                  │       │
│    │  • Indexiert automatisch nach Prompt-Abschluss               │       │
│    └───────────────────────────────────────────────────────────────┘       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. GraphQueryService

Zentrale API für alle Graph-Abfragen.

### Datei: `app/services/graph_query_service.py`

```python
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Set
from enum import Enum

class QueryType(str, Enum):
    IMPACT = "impact"           # Was ist betroffen wenn X ändert?
    DEPENDENTS = "dependents"   # Wer verwendet X?
    DEPENDENCIES = "dependencies"  # Was verwendet X?
    PATH = "path"               # Wie komme ich von A nach B?
    SEARCH = "search"           # Finde X nach Kriterien
    CONTEXT = "context"         # Umgebungskontext für X


@dataclass
class ImpactResult:
    """Ergebnis einer Impact-Analyse."""
    target_id: str
    direct_impacts: List[str]       # Direkt betroffene Nodes
    transitive_impacts: List[str]   # Indirekt betroffene Nodes
    risk_score: float               # 0.0-1.0 (basierend auf Verbindungen)
    affected_files: List[str]       # Betroffene Dateien
    summary: str                    # Menschenlesbare Zusammenfassung


@dataclass
class ContextResult:
    """Kontext-Informationen für einen Node."""
    node_id: str
    node_type: str
    parent_class: Optional[str]
    implements: List[str]
    extends: Optional[str]
    uses: List[str]
    used_by: List[str]
    related_tables: List[str]
    file_path: str
    line_number: int


class GraphQueryService:
    """
    Intelligente Query-Schicht über dem Knowledge Graph.
    """

    def __init__(self, store: KnowledgeGraphStore):
        self.store = store

    # ─────────────────────────────────────────────────────────────────
    # Impact Analysis
    # ─────────────────────────────────────────────────────────────────

    async def analyze_impact(
        self,
        target_id: str,
        max_depth: int = 3,
        include_transitive: bool = True
    ) -> ImpactResult:
        """
        Analysiert die Auswirkungen einer Änderung.

        Args:
            target_id: ID des zu ändernden Elements
            max_depth: Maximale Tiefe der Analyse
            include_transitive: Auch indirekte Abhängigkeiten

        Returns:
            ImpactResult mit allen betroffenen Elementen
        """
        pass

    # ─────────────────────────────────────────────────────────────────
    # Path Finding
    # ─────────────────────────────────────────────────────────────────

    async def find_connection(
        self,
        from_id: str,
        to_id: str,
        max_hops: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Findet den kürzesten Pfad zwischen zwei Elementen.

        Returns:
            Liste von Kanten die den Pfad bilden
        """
        pass

    async def find_common_ancestor(
        self,
        node_ids: List[str]
    ) -> Optional[str]:
        """
        Findet den gemeinsamen Vorfahren mehrerer Nodes.
        Nützlich für: "Was verbindet diese Klassen?"
        """
        pass

    # ─────────────────────────────────────────────────────────────────
    # Smart Search
    # ─────────────────────────────────────────────────────────────────

    async def smart_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20
    ) -> List[GraphNode]:
        """
        Intelligente Suche mit Kontext-Verständnis.

        Beispiele:
            "REST Controller" → findet Klassen mit @RestController
            "verwendet UserService" → findet alle Caller
            "implementiert Serializable" → Interface-Implementierungen
        """
        pass

    # ─────────────────────────────────────────────────────────────────
    # Context Enrichment
    # ─────────────────────────────────────────────────────────────────

    async def get_context(
        self,
        node_id: str,
        depth: int = 1
    ) -> ContextResult:
        """
        Holt reichhaltigen Kontext für einen Node.

        Perfekt für Agent-Prompts: "Hier ist der Kontext zu UserService..."
        """
        pass

    async def get_context_for_file(
        self,
        file_path: str
    ) -> List[ContextResult]:
        """
        Kontext für alle Elemente in einer Datei.
        Nützlich wenn Agent eine Datei bearbeitet.
        """
        pass
```

---

## 2. Graph Tools für Agents

Neue Tool-Kategorie: `GRAPH`

### Datei: `app/agent/graph_tools.py`

```python
from app.agent.tools import Tool, ToolParameter, ToolResult, ToolCategory

# Neue Kategorie
class ToolCategory(str, Enum):
    SEARCH = "search"
    FILE = "file"
    KNOWLEDGE = "knowledge"
    ANALYSIS = "analysis"
    DEVOPS = "devops"
    GRAPH = "graph"  # NEU


def register_graph_tools(registry: ToolRegistry):
    """Registriert alle Graph-Tools."""

    # ─────────────────────────────────────────────────────────────────
    # graph_impact - Impact-Analyse
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_impact",
        description="""Analysiert die Auswirkungen einer Änderung.

Zeigt welche anderen Klassen/Methoden/Dateien betroffen wären,
wenn das angegebene Element geändert wird.

Beispiel: graph_impact("com.example.UserService.save")
→ Zeigt alle Caller, abhängige Tests, betroffene Controller""",
        category=ToolCategory.GRAPH,
        parameters=[
            ToolParameter("target", "string", "Vollqualifizierte ID des Elements", required=True),
            ToolParameter("depth", "integer", "Analysetiefe (1-5)", required=False, default=2),
        ],
        handler=handle_graph_impact
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_context - Kontext holen
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_context",
        description="""Holt Kontext-Informationen zu einem Code-Element.

Zeigt: Parent-Klasse, Interfaces, Abhängigkeiten, Verwendungen, etc.
Nützlich um den Kontext zu verstehen bevor man Änderungen macht.

Beispiel: graph_context("com.example.PaymentService")
→ Zeigt dass es PaymentGateway implementiert, DatabaseService verwendet, etc.""",
        category=ToolCategory.GRAPH,
        parameters=[
            ToolParameter("element_id", "string", "ID des Elements", required=True),
        ],
        handler=handle_graph_context
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_find_path - Verbindung finden
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_find_path",
        description="""Findet die Verbindung zwischen zwei Code-Elementen.

Zeigt wie A mit B zusammenhängt (über Vererbung, Aufrufe, etc.)

Beispiel: graph_find_path("UserController", "DatabaseService")
→ UserController → UserService → UserRepository → DatabaseService""",
        category=ToolCategory.GRAPH,
        parameters=[
            ToolParameter("from_element", "string", "Start-Element", required=True),
            ToolParameter("to_element", "string", "Ziel-Element", required=True),
        ],
        handler=handle_graph_find_path
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_search - Intelligente Suche
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_search",
        description="""Sucht im Knowledge Graph nach Code-Elementen.

Unterstützt natürliche Sprache:
- "alle REST Controller"
- "Klassen die UserService verwenden"
- "Methoden die save aufrufen"
- "Implementierungen von PaymentGateway\"""",
        category=ToolCategory.GRAPH,
        parameters=[
            ToolParameter("query", "string", "Suchanfrage", required=True),
            ToolParameter("type_filter", "string", "Optional: class, method, interface", required=False),
            ToolParameter("limit", "integer", "Max. Ergebnisse", required=False, default=10),
        ],
        handler=handle_graph_search
    ))

    # ─────────────────────────────────────────────────────────────────
    # graph_dependents - Wer verwendet X?
    # ─────────────────────────────────────────────────────────────────
    registry.register(Tool(
        name="graph_dependents",
        description="""Zeigt alle Elemente die das angegebene Element verwenden.

Beispiel: graph_dependents("UserRepository")
→ Zeigt: UserService, UserServiceTest, AdminService, ...""",
        category=ToolCategory.GRAPH,
        parameters=[
            ToolParameter("element_id", "string", "ID des Elements", required=True),
            ToolParameter("include_tests", "boolean", "Auch Test-Klassen", required=False, default=True),
        ],
        handler=handle_graph_dependents
    ))
```

---

## 3. FileChangeTracker

Trackt Dateiänderungen während Tool-Ausführungen.

### Datei: `app/services/file_change_tracker.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Set, Optional
from enum import Enum
import threading


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChange:
    path: str
    change_type: ChangeType
    timestamp: datetime
    tool_name: Optional[str] = None  # Welches Tool hat die Änderung gemacht


@dataclass
class SessionChanges:
    """Änderungen innerhalb einer Session."""
    session_id: str
    changes: Dict[str, FileChange] = field(default_factory=dict)
    is_locked: bool = False  # Während Tool-Calls gesperrt

    def add_change(self, path: str, change_type: ChangeType, tool_name: str = None):
        # Nur wenn nicht gesperrt
        if not self.is_locked:
            self.changes[path] = FileChange(
                path=path,
                change_type=change_type,
                timestamp=datetime.now(),
                tool_name=tool_name
            )

    def get_modified_files(self) -> Set[str]:
        return {c.path for c in self.changes.values()
                if c.change_type in (ChangeType.CREATED, ChangeType.MODIFIED)}


class FileChangeTracker:
    """
    Singleton das Dateiänderungen pro Session trackt.

    Lifecycle:
    1. Session startet → track_session(session_id)
    2. Tool schreibt Datei → record_change(session_id, path)
    3. Alle Tools fertig → get_pending_changes(session_id)
    4. Auto-Indexer läuft → clear_changes(session_id)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._sessions: Dict[str, SessionChanges] = {}
            return cls._instance

    def track_session(self, session_id: str) -> None:
        """Startet Tracking für eine Session."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionChanges(session_id=session_id)

    def lock_session(self, session_id: str) -> None:
        """Sperrt Session während Tool-Calls (keine Index-Updates)."""
        if session_id in self._sessions:
            self._sessions[session_id].is_locked = True

    def unlock_session(self, session_id: str) -> None:
        """Entsperrt Session nach Tool-Calls."""
        if session_id in self._sessions:
            self._sessions[session_id].is_locked = False

    def record_change(
        self,
        session_id: str,
        file_path: str,
        change_type: ChangeType,
        tool_name: str = None
    ) -> None:
        """Zeichnet eine Dateiänderung auf."""
        if session_id in self._sessions:
            self._sessions[session_id].add_change(file_path, change_type, tool_name)

    def get_pending_changes(self, session_id: str) -> Set[str]:
        """Gibt alle noch nicht indexierten Änderungen zurück."""
        if session_id not in self._sessions:
            return set()
        return self._sessions[session_id].get_modified_files()

    def clear_changes(self, session_id: str) -> None:
        """Löscht alle getrackte Änderungen (nach Index)."""
        if session_id in self._sessions:
            self._sessions[session_id].changes.clear()

    def is_locked(self, session_id: str) -> bool:
        """Prüft ob Session gesperrt ist."""
        return self._sessions.get(session_id, SessionChanges("")).is_locked


def get_change_tracker() -> FileChangeTracker:
    return FileChangeTracker()
```

---

## 4. Auto-Indexer Integration

Integration in den Agent Orchestrator.

### Änderungen in `app/agent/orchestrator.py`

```python
from app.services.file_change_tracker import get_change_tracker, ChangeType
from app.services.graph_query_service import get_graph_query_service

class AgentOrchestrator:

    def __init__(self, ...):
        # ... existing code ...
        self._change_tracker = get_change_tracker()
        self._graph_service = get_graph_query_service()

    async def run(self, session_id: str, user_message: str, ...):
        """Haupt-Loop mit Auto-Index Integration."""

        # 1. Session für Change-Tracking starten
        self._change_tracker.track_session(session_id)

        try:
            # 2. Während Tool-Calls: Session sperren
            self._change_tracker.lock_session(session_id)

            # ... existing agent loop ...
            result = await self._agent_loop(session_id, user_message, ...)

            # 3. Nach allen Tools: Session entsperren
            self._change_tracker.unlock_session(session_id)

            # 4. Pending Changes indexieren (nach Prompt-Abschluss!)
            await self._auto_index_changes(session_id)

            return result

        finally:
            self._change_tracker.unlock_session(session_id)

    async def _auto_index_changes(self, session_id: str) -> None:
        """Indexiert geänderte Dateien nach Prompt-Abschluss."""

        pending = self._change_tracker.get_pending_changes(session_id)
        if not pending:
            return

        logger.info(f"[AutoIndex] {len(pending)} Dateien zu re-indexieren")

        try:
            store = get_knowledge_graph_store()
            builder = get_graph_builder("auto", store)  # Auto-detect language

            for file_path in pending:
                path = Path(file_path)
                if path.exists() and path.suffix in ('.java', '.py', '.ts', '.js'):
                    # Alte Einträge löschen
                    store.delete_by_file(str(path))
                    # Neu indexieren
                    await builder.index_file(path)

            # Stats aktualisieren
            registry = get_graph_registry()
            active = registry.get_active()
            if active:
                stats = store.get_stats()
                registry.update_stats(active.id, stats["total_nodes"], stats["total_edges"])

            # Changes als verarbeitet markieren
            self._change_tracker.clear_changes(session_id)

            logger.info(f"[AutoIndex] Fertig: {len(pending)} Dateien indexiert")

        except Exception as e:
            logger.warning(f"[AutoIndex] Fehler: {e}")
```

---

## 5. Tool-Handler Integration

Schreib-Tools müssen Änderungen melden.

### Änderungen in File-Write Tools

```python
# In app/agent/tools.py oder shell_tools.py

async def handle_file_write(path: str, content: str, session_id: str = None) -> ToolResult:
    """Schreibt eine Datei und meldet die Änderung."""

    try:
        file_path = Path(path)
        existed = file_path.exists()

        # Datei schreiben
        file_path.write_text(content, encoding="utf-8")

        # Änderung tracken
        if session_id:
            tracker = get_change_tracker()
            change_type = ChangeType.MODIFIED if existed else ChangeType.CREATED
            tracker.record_change(session_id, str(file_path), change_type, "file_write")

        return ToolResult(success=True, data=f"Datei geschrieben: {path}")

    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

---

## 6. REST API Erweiterungen

### Neue Endpoints in `app/api/routes/graph.py`

```python
@router.get("/impact/{node_id:path}")
async def get_impact_analysis(
    node_id: str,
    depth: int = Query(2, ge=1, le=5)
):
    """
    Impact-Analyse: Was ist betroffen wenn dieses Element geändert wird?
    """
    service = get_graph_query_service()
    result = await service.analyze_impact(node_id, depth)
    return result


@router.get("/context/{node_id:path}")
async def get_element_context(node_id: str):
    """
    Reichhaltiger Kontext für ein Code-Element.
    """
    service = get_graph_query_service()
    return await service.get_context(node_id)


@router.get("/context/file")
async def get_file_context(path: str = Query(...)):
    """
    Kontext für alle Elemente in einer Datei.
    """
    service = get_graph_query_service()
    return await service.get_context_for_file(path)


@router.get("/connection")
async def find_connection(
    from_id: str = Query(..., alias="from"),
    to_id: str = Query(..., alias="to"),
    max_hops: int = Query(5, ge=1, le=10)
):
    """
    Findet die Verbindung zwischen zwei Elementen.
    """
    service = get_graph_query_service()
    return await service.find_connection(from_id, to_id, max_hops)


@router.post("/smart-search")
async def smart_search(
    query: str = Query(...),
    type_filter: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Intelligente Suche mit natürlicher Sprache.
    """
    service = get_graph_query_service()
    return await service.smart_search(query, {"type": type_filter}, limit)
```

---

## 7. Automatische Kontext-Anreicherung

Optional: Agent-Prompts automatisch mit Graph-Kontext anreichern.

### In `app/agent/prompt_enhancer.py`

```python
async def enhance_with_graph_context(
    message: str,
    mentioned_files: List[str],
    session_id: str
) -> str:
    """
    Reichert User-Message mit Graph-Kontext an.

    Wenn der User "ändere UserService" sagt, fügt automatisch
    Kontext hinzu: Interfaces, Dependencies, Callers etc.
    """

    # Nur wenn Graph verfügbar und aktiviert
    registry = get_graph_registry()
    if not registry.get_active():
        return message

    service = get_graph_query_service()
    context_parts = []

    for file_path in mentioned_files:
        try:
            contexts = await service.get_context_for_file(file_path)
            if contexts:
                context_parts.append(f"\n=== Graph-Kontext für {file_path} ===")
                for ctx in contexts[:5]:  # Max 5 pro Datei
                    context_parts.append(f"- {ctx.node_type} {ctx.node_id}")
                    if ctx.implements:
                        context_parts.append(f"  Implements: {', '.join(ctx.implements)}")
                    if ctx.used_by:
                        context_parts.append(f"  Verwendet von: {', '.join(ctx.used_by[:3])}")
        except Exception:
            pass

    if context_parts:
        return message + "\n" + "\n".join(context_parts)

    return message
```

---

## 8. Implementierungs-Reihenfolge

### Phase 1: Core (2-3 Stunden)
1. `GraphQueryService` mit `analyze_impact()` und `get_context()`
2. REST API Endpoints
3. Unit Tests

### Phase 2: Tools (1-2 Stunden)
4. `graph_tools.py` mit Tool-Registrierung
5. Integration in `ToolRegistry`

### Phase 3: Auto-Index (1-2 Stunden)
6. `FileChangeTracker`
7. Integration in Orchestrator
8. Integration in File-Write Tools

### Phase 4: Polish (1 Stunde)
9. Prompt-Enhancer Integration
10. Frontend: Graph-Kontext in Chat anzeigen

---

## Beispiel-Workflows

### Workflow 1: Code-Änderung mit Impact-Check

```
User: "Ändere UserService.save() so dass es auch den Timestamp setzt"

Agent:
1. graph_impact("UserService.save")
   → Findet: UserController, AdminService, 3 Tests betroffen

2. Agent weiß jetzt: "Ich muss auch die Tests anpassen"

3. file_write("UserService.java", ...)
   → FileChangeTracker notiert Änderung

4. file_write("UserServiceTest.java", ...)
   → FileChangeTracker notiert Änderung

5. Prompt fertig → Auto-Index läuft
   → UserService.java und UserServiceTest.java werden re-indexiert
```

### Workflow 2: Refactoring mit Navigation

```
User: "Benenne PaymentGateway in PaymentProcessor um"

Agent:
1. graph_dependents("PaymentGateway")
   → Findet: PaymentService, OrderProcessor, StripePaymentGateway

2. Für jede gefundene Datei: Umbenennung durchführen

3. Am Ende: Alle geänderten Dateien werden auto-indexiert
```

---

## Offene Entscheidungen

1. **Kontext-Tiefe**: Wie viel Graph-Kontext soll automatisch in Prompts?
   - Option A: Immer minimal (nur direkte Abhängigkeiten)
   - Option B: Adaptiv (mehr bei komplexen Aufgaben)
   - Option C: User-konfigurierbar

2. **Index-Strategie**: Wie mit großen Projekten umgehen?
   - Option A: Nur geänderte Dateien (schnell, aber evtl. inkonsistent)
   - Option B: Betroffene + Abhängige (gründlicher)
   - Option C: Komplett re-index nach X Änderungen

3. **MCP-Integration**: Sollen Graph-Tools als MCP-Server exponiert werden?
   - Pro: Andere Tools können sie nutzen
   - Con: Mehr Komplexität

---

## Nächste Schritte

Nach Freigabe dieses Designs:
1. `/sc:implement Phase 1` - GraphQueryService
2. `/sc:implement Phase 2` - Graph Tools
3. `/sc:implement Phase 3` - Auto-Index
4. `/sc:test` - Integration Tests
