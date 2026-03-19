# Phase 3 Feature Design Document

> Version: 1.0.0
> Erstellt: 2026-03-18
> Status: Draft

---

## Inhaltsverzeichnis

1. [Task-Progress-UI](#1-task-progress-ui)
2. [Knowledge Graph](#2-knowledge-graph)
3. [JUnit Test Execution Tool](#3-junit-test-execution-tool)

---

## 1. Task-Progress-UI

### 1.1 Übersicht

Live-Fortschrittsanzeige für laufende Agent-Tasks mit Zwischenergebnissen und Abbruch-Möglichkeit.

### 1.2 Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                         BACKEND                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────┐│
│  │  Orchestrator   │───>│  TaskTracker    │───>│ SSE Stream   ││
│  │                 │    │                 │    │              ││
│  │  - run_agent()  │    │  - progress     │    │  - events    ││
│  │  - tool_calls   │    │  - steps        │    │  - progress  ││
│  │  - sub_agents   │    │  - artifacts    │    │  - artifacts ││
│  └─────────────────┘    └─────────────────┘    └──────────────┘│
│           │                      │                      │       │
└───────────┼──────────────────────┼──────────────────────┼───────┘
            │                      │                      │
            ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    TaskProgressPanel                         ││
│  │  ┌─────────────────────────────────────────────────────────┐││
│  │  │ Task: Code-Analyse durchführen            [X] Abbrechen │││
│  │  │ ████████████████░░░░░░░░░░ 65%           ~2 min left   │││
│  │  ├─────────────────────────────────────────────────────────┤││
│  │  │ ✓ Kontext gesammelt (3 Dateien)                        │││
│  │  │ ✓ Code-Patterns erkannt (12 Matches)                   │││
│  │  │ ⟳ Generiere Bericht...                                 │││
│  │  │   └─ Analysiere: UserService.java                      │││
│  │  │ ○ Zusammenfassung erstellen                            │││
│  │  ├─────────────────────────────────────────────────────────┤││
│  │  │ [▼ Zwischenergebnisse anzeigen]                        │││
│  │  │   • Pattern: NullPointerException in line 42           │││
│  │  │   • Pattern: Unused import in line 3                   │││
│  │  └─────────────────────────────────────────────────────────┘││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 Datenmodell

```python
# app/agent/task_tracker.py

@dataclass
class TaskStep:
    """Ein einzelner Schritt innerhalb eines Tasks."""
    id: str
    name: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: float = 0.0  # 0.0 - 1.0
    details: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    sub_steps: List["TaskStep"] = field(default_factory=list)

@dataclass
class TaskProgress:
    """Gesamtfortschritt eines Tasks."""
    task_id: str
    session_id: str
    title: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Progress
    total_steps: int = 0
    completed_steps: int = 0
    current_step: Optional[str] = None
    progress_percent: float = 0.0
    estimated_remaining_seconds: Optional[int] = None

    # Steps
    steps: List[TaskStep] = field(default_factory=list)

    # Artifacts (Zwischenergebnisse)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    # Error info
    error: Optional[str] = None

class TaskTracker:
    """Verwaltet Task-Fortschritt und emittiert Events."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.tasks: Dict[str, TaskProgress] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()

    def create_task(self, title: str, steps: List[str]) -> str:
        """Erstellt einen neuen Task mit definierten Schritten."""
        task_id = str(uuid.uuid4())
        task = TaskProgress(
            task_id=task_id,
            session_id=self.session_id,
            title=title,
            status="pending",
            created_at=datetime.utcnow(),
            total_steps=len(steps),
            steps=[TaskStep(id=str(i), name=s, status="pending") for i, s in enumerate(steps)]
        )
        self.tasks[task_id] = task
        return task_id

    async def start_step(self, task_id: str, step_index: int, details: str = None):
        """Startet einen Schritt und emittiert Event."""
        task = self.tasks.get(task_id)
        if not task:
            return

        step = task.steps[step_index]
        step.status = "running"
        step.started_at = datetime.utcnow()
        step.details = details
        task.current_step = step.name

        await self._emit_progress(task)

    async def complete_step(self, task_id: str, step_index: int, artifacts: List[Dict] = None):
        """Schließt einen Schritt ab."""
        task = self.tasks.get(task_id)
        if not task:
            return

        step = task.steps[step_index]
        step.status = "completed"
        step.completed_at = datetime.utcnow()
        step.progress = 1.0

        if artifacts:
            step.artifacts.extend(artifacts)
            task.artifacts.extend(artifacts)

        task.completed_steps += 1
        task.progress_percent = task.completed_steps / task.total_steps * 100

        # Estimate remaining time
        elapsed = (datetime.utcnow() - task.started_at).total_seconds()
        if task.completed_steps > 0:
            avg_per_step = elapsed / task.completed_steps
            remaining_steps = task.total_steps - task.completed_steps
            task.estimated_remaining_seconds = int(avg_per_step * remaining_steps)

        await self._emit_progress(task)

    async def add_artifact(self, task_id: str, artifact: Dict[str, Any]):
        """Fügt ein Zwischenergebnis hinzu."""
        task = self.tasks.get(task_id)
        if task:
            task.artifacts.append(artifact)
            await self._emit_artifact(task, artifact)

    async def _emit_progress(self, task: TaskProgress):
        """Emittiert Progress-Event."""
        await self._event_queue.put({
            "type": "task_progress",
            "data": asdict(task)
        })

    async def _emit_artifact(self, task: TaskProgress, artifact: Dict):
        """Emittiert Artifact-Event."""
        await self._event_queue.put({
            "type": "task_artifact",
            "data": {
                "task_id": task.task_id,
                "artifact": artifact
            }
        })
```

### 1.4 API Endpoints

```python
# app/api/routes/tasks.py

@router.get("/api/tasks/{session_id}")
async def get_tasks(session_id: str) -> List[TaskProgress]:
    """Alle Tasks einer Session."""
    pass

@router.get("/api/tasks/{session_id}/{task_id}")
async def get_task(session_id: str, task_id: str) -> TaskProgress:
    """Einzelner Task mit Details."""
    pass

@router.post("/api/tasks/{session_id}/{task_id}/cancel")
async def cancel_task(session_id: str, task_id: str) -> Dict:
    """Bricht einen laufenden Task ab."""
    pass

@router.get("/api/tasks/{session_id}/stream")
async def task_stream(session_id: str):
    """SSE Stream für Task-Updates."""
    pass
```

### 1.5 Frontend-Komponenten

```javascript
// static/app.js - TaskProgressPanel

class TaskProgressPanel {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.tasks = new Map();
    this.expanded = new Set();
  }

  render(task) {
    const existingEl = this.container.querySelector(`[data-task-id="${task.task_id}"]`);

    const html = `
      <div class="task-progress-card ${task.status}" data-task-id="${task.task_id}">
        <div class="task-header">
          <span class="task-title">${escapeHtml(task.title)}</span>
          <span class="task-time">${this.formatRemaining(task.estimated_remaining_seconds)}</span>
          ${task.status === 'running' ? `
            <button class="task-cancel" onclick="taskPanel.cancel('${task.task_id}')" title="Abbrechen">
              &#10005;
            </button>
          ` : ''}
        </div>

        <div class="task-progress-bar">
          <div class="task-progress-fill" style="width: ${task.progress_percent}%"></div>
          <span class="task-progress-text">${Math.round(task.progress_percent)}%</span>
        </div>

        <div class="task-steps">
          ${task.steps.map((step, i) => this.renderStep(step, i)).join('')}
        </div>

        ${task.artifacts.length > 0 ? `
          <div class="task-artifacts">
            <button class="task-artifacts-toggle" onclick="taskPanel.toggleArtifacts('${task.task_id}')">
              ${this.expanded.has(task.task_id) ? '▼' : '▶'} Zwischenergebnisse (${task.artifacts.length})
            </button>
            ${this.expanded.has(task.task_id) ? `
              <div class="task-artifacts-list">
                ${task.artifacts.map(a => this.renderArtifact(a)).join('')}
              </div>
            ` : ''}
          </div>
        ` : ''}
      </div>
    `;

    if (existingEl) {
      existingEl.outerHTML = html;
    } else {
      this.container.insertAdjacentHTML('afterbegin', html);
    }
  }

  renderStep(step, index) {
    const icons = {
      pending: '○',
      running: '⟳',
      completed: '✓',
      failed: '✗',
      skipped: '◌'
    };

    return `
      <div class="task-step ${step.status}">
        <span class="step-icon">${icons[step.status]}</span>
        <span class="step-name">${escapeHtml(step.name)}</span>
        ${step.details ? `<span class="step-details">${escapeHtml(step.details)}</span>` : ''}
      </div>
    `;
  }

  renderArtifact(artifact) {
    return `
      <div class="task-artifact">
        <span class="artifact-type">${artifact.type}</span>
        <span class="artifact-content">${escapeHtml(artifact.summary || JSON.stringify(artifact.data))}</span>
      </div>
    `;
  }

  formatRemaining(seconds) {
    if (!seconds) return '';
    if (seconds < 60) return `~${seconds}s`;
    return `~${Math.round(seconds / 60)} min`;
  }
}
```

### 1.6 CSS Styles

```css
/* static/style.css - Task Progress */

.task-progress-card {
  background: var(--bg-secondary);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 8px;
  border-left: 3px solid var(--border);
}

.task-progress-card.running {
  border-left-color: var(--primary);
}

.task-progress-card.completed {
  border-left-color: var(--success);
}

.task-progress-card.failed {
  border-left-color: var(--error);
}

.task-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.task-title {
  flex: 1;
  font-weight: 500;
}

.task-time {
  color: var(--text-secondary);
  font-size: 0.85em;
}

.task-cancel {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
}

.task-cancel:hover {
  color: var(--error);
}

.task-progress-bar {
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: 3px;
  position: relative;
  margin-bottom: 12px;
}

.task-progress-fill {
  height: 100%;
  background: var(--primary);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.task-progress-text {
  position: absolute;
  right: 0;
  top: -18px;
  font-size: 0.75em;
  color: var(--text-secondary);
}

.task-steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.task-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.9em;
  padding: 4px 0;
}

.task-step.completed {
  color: var(--success);
}

.task-step.running {
  color: var(--primary);
}

.task-step.running .step-icon {
  animation: spin 1s linear infinite;
}

.task-step.pending {
  color: var(--text-secondary);
}

.task-step.failed {
  color: var(--error);
}

.step-details {
  color: var(--text-secondary);
  font-size: 0.85em;
  margin-left: auto;
}

.task-artifacts {
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.task-artifacts-toggle {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 0.85em;
  padding: 4px 0;
}

.task-artifacts-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.task-artifact {
  display: flex;
  gap: 8px;
  font-size: 0.85em;
  padding: 4px 8px;
  background: var(--bg-tertiary);
  border-radius: 4px;
}

.artifact-type {
  color: var(--primary);
  font-weight: 500;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

---

## 2. Knowledge Graph

### 2.1 Übersicht

Visualisierung der Code-Beziehungen (Klassen, Methoden, Abhängigkeiten) als interaktiver Graph.

### 2.2 Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE GRAPH SYSTEM                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │ CodeIndexer │───>│ GraphBuilder│───>│ SQLite Graph Store  │ │
│  │             │    │             │    │                     │ │
│  │ - Parse AST │    │ - Nodes     │    │ - nodes (id, type)  │ │
│  │ - Extract   │    │ - Edges     │    │ - edges (from, to)  │ │
│  │   relations │    │ - Metadata  │    │ - metadata          │ │
│  └─────────────┘    └─────────────┘    └─────────────────────┘ │
│         │                  │                      │             │
│         ▼                  ▼                      ▼             │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    GraphQueryEngine                          ││
│  │  - get_dependencies(class)                                   ││
│  │  - get_dependents(class)                                     ││
│  │  - find_path(from, to)                                       ││
│  │  - get_subgraph(center, depth)                               ││
│  │  - search_nodes(query)                                       ││
│  └─────────────────────────────────────────────────────────────┘│
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                 KnowledgeGraphViewer                         ││
│  │  ┌───────────────────────────────────────────────────────┐  ││
│  │  │                                                       │  ││
│  │  │        [UserService]◄────────[AuthController]        │  ││
│  │  │              │                      │                 │  ││
│  │  │          implements              imports              │  ││
│  │  │              ▼                      ▼                 │  ││
│  │  │       [IUserService]          [JwtUtils]             │  ││
│  │  │              │                                        │  ││
│  │  │           queries                                     │  ││
│  │  │              ▼                                        │  ││
│  │  │         [UserTable]                                   │  ││
│  │  │                                                       │  ││
│  │  └───────────────────────────────────────────────────────┘  ││
│  │  [Zoom: +/-] [Layout: Force/Tree/Radial] [Filter: ▼]        ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 Datenmodell

```python
# app/services/knowledge_graph.py

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set
import sqlite3

class NodeType(str, Enum):
    CLASS = "class"
    INTERFACE = "interface"
    METHOD = "method"
    FIELD = "field"
    TABLE = "table"
    COLUMN = "column"
    FILE = "file"
    PACKAGE = "package"

class EdgeType(str, Enum):
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

@dataclass
class SubGraph:
    """Ein Teilgraph für Visualisierung."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    center_node_id: Optional[str] = None
    depth: int = 2


class KnowledgeGraphStore:
    """SQLite-basierter Graph-Speicher."""

    def __init__(self, db_path: str = "data/knowledge_graph.db"):
        self.db_path = db_path
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
                    FOREIGN KEY (from_id) REFERENCES nodes(id),
                    FOREIGN KEY (to_id) REFERENCES nodes(id),
                    UNIQUE(from_id, to_id, type)
                );

                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
                CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
                CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
                CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            """)

    def add_node(self, node: GraphNode) -> bool:
        """Fügt einen Knoten hinzu oder aktualisiert ihn."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO nodes (id, type, name, file_path, line_number, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (node.id, node.type.value, node.name, node.file_path,
                  node.line_number, json.dumps(node.metadata)))
            return True

    def add_edge(self, edge: GraphEdge) -> bool:
        """Fügt eine Kante hinzu."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO edges (from_id, to_id, type, weight, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (edge.from_id, edge.to_id, edge.type.value,
                  edge.weight, json.dumps(edge.metadata)))
            return True

    def get_subgraph(self, center_id: str, depth: int = 2,
                     node_types: List[NodeType] = None,
                     edge_types: List[EdgeType] = None) -> SubGraph:
        """Holt einen Teilgraph um einen Knoten herum."""
        visited_nodes: Set[str] = set()
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []

        def traverse(node_id: str, current_depth: int):
            if current_depth > depth or node_id in visited_nodes:
                return

            visited_nodes.add(node_id)

            # Node holen
            node = self.get_node(node_id)
            if node and (not node_types or node.type in node_types):
                nodes.append(node)

            # Ausgehende Kanten
            for edge in self.get_edges_from(node_id, edge_types):
                edges.append(edge)
                traverse(edge.to_id, current_depth + 1)

            # Eingehende Kanten
            for edge in self.get_edges_to(node_id, edge_types):
                edges.append(edge)
                traverse(edge.from_id, current_depth + 1)

        traverse(center_id, 0)

        return SubGraph(
            nodes=nodes,
            edges=list({(e.from_id, e.to_id, e.type): e for e in edges}.values()),
            center_node_id=center_id,
            depth=depth
        )

    def find_path(self, from_id: str, to_id: str, max_depth: int = 5) -> List[GraphEdge]:
        """Findet den kürzesten Pfad zwischen zwei Knoten (BFS)."""
        from collections import deque

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
            type_filter = ""
            params = [f"%{query}%", limit]

            if node_types:
                placeholders = ",".join("?" * len(node_types))
                type_filter = f"AND type IN ({placeholders})"
                params = [f"%{query}%"] + [t.value for t in node_types] + [limit]

            rows = conn.execute(f"""
                SELECT id, type, name, file_path, line_number, metadata
                FROM nodes
                WHERE name LIKE ? {type_filter}
                ORDER BY name
                LIMIT ?
            """, params).fetchall()

            return [
                GraphNode(
                    id=r[0], type=NodeType(r[1]), name=r[2],
                    file_path=r[3], line_number=r[4],
                    metadata=json.loads(r[5]) if r[5] else {}
                )
                for r in rows
            ]
```

### 2.4 Graph Builder

```python
# app/services/graph_builder.py

import re
from pathlib import Path
from typing import List, Set

class JavaGraphBuilder:
    """Baut Knowledge Graph aus Java-Code."""

    # Regex patterns
    _RE_PACKAGE = re.compile(r"package\s+([\w.]+);")
    _RE_IMPORT = re.compile(r"import\s+([\w.]+);")
    _RE_CLASS = re.compile(r"(?:public|private|protected)?\s*(?:abstract|final)?\s*class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?")
    _RE_INTERFACE = re.compile(r"(?:public)?\s*interface\s+(\w+)(?:\s+extends\s+([\w,\s]+))?")
    _RE_METHOD = re.compile(r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(\w+(?:<[\w<>,\s]+>)?)\s+(\w+)\s*\(([^)]*)\)")
    _RE_FIELD = re.compile(r"(?:private|protected|public)\s+(?:static\s+)?(?:final\s+)?(\w+(?:<[\w<>,\s]+>)?)\s+(\w+)\s*[;=]")
    _RE_METHOD_CALL = re.compile(r"(\w+)\.(\w+)\s*\(")

    def __init__(self, store: KnowledgeGraphStore):
        self.store = store

    def index_file(self, file_path: Path) -> int:
        """Indexiert eine Java-Datei und gibt Anzahl der Nodes zurück."""
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        nodes_added = 0

        # Package
        package_match = self._RE_PACKAGE.search(content)
        package = package_match.group(1) if package_match else ""

        # Class/Interface
        for match in self._RE_CLASS.finditer(content):
            class_name = match.group(1)
            extends = match.group(2)
            implements = match.group(3)

            class_id = f"{package}.{class_name}" if package else class_name

            # Node erstellen
            self.store.add_node(GraphNode(
                id=class_id,
                type=NodeType.CLASS,
                name=class_name,
                file_path=str(file_path),
                line_number=content[:match.start()].count('\n') + 1,
                metadata={"package": package}
            ))
            nodes_added += 1

            # Extends Edge
            if extends:
                extends_id = self._resolve_class(extends, package, content)
                self.store.add_edge(GraphEdge(
                    from_id=class_id,
                    to_id=extends_id,
                    type=EdgeType.EXTENDS
                ))

            # Implements Edges
            if implements:
                for iface in implements.split(","):
                    iface = iface.strip()
                    if iface:
                        iface_id = self._resolve_class(iface, package, content)
                        self.store.add_edge(GraphEdge(
                            from_id=class_id,
                            to_id=iface_id,
                            type=EdgeType.IMPLEMENTS
                        ))

        # Imports as edges
        for match in self._RE_IMPORT.finditer(content):
            import_path = match.group(1)
            if package:
                self.store.add_edge(GraphEdge(
                    from_id=f"{package}.*",
                    to_id=import_path,
                    type=EdgeType.IMPORTS
                ))

        return nodes_added

    def _resolve_class(self, class_name: str, current_package: str, content: str) -> str:
        """Löst einen Klassennamen zu vollqualifiziertem Namen auf."""
        # Prüfe Imports
        for match in self._RE_IMPORT.finditer(content):
            import_path = match.group(1)
            if import_path.endswith(f".{class_name}"):
                return import_path

        # Gleiche Package
        return f"{current_package}.{class_name}" if current_package else class_name
```

### 2.5 API Endpoints

```python
# app/api/routes/graph.py

@router.get("/api/graph/node/{node_id}")
async def get_node(node_id: str) -> GraphNode:
    """Einzelner Knoten mit Metadata."""
    pass

@router.get("/api/graph/subgraph")
async def get_subgraph(
    center: str,
    depth: int = 2,
    node_types: List[NodeType] = None,
    edge_types: List[EdgeType] = None
) -> SubGraph:
    """Teilgraph um einen Knoten."""
    pass

@router.get("/api/graph/path")
async def find_path(from_id: str, to_id: str) -> List[GraphEdge]:
    """Pfad zwischen zwei Knoten."""
    pass

@router.get("/api/graph/search")
async def search_nodes(query: str, types: List[NodeType] = None) -> List[GraphNode]:
    """Knoten-Suche."""
    pass

@router.post("/api/graph/index")
async def reindex(repo_type: str = "java") -> Dict:
    """Re-indexiert den Knowledge Graph."""
    pass

@router.get("/api/graph/stats")
async def graph_stats() -> Dict:
    """Graph-Statistiken (Anzahl Nodes, Edges, etc.)."""
    pass
```

### 2.6 Frontend Visualization

```javascript
// static/knowledge-graph.js

class KnowledgeGraphViewer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.svg = null;
    this.simulation = null;
    this.nodes = [];
    this.edges = [];
    this.selectedNode = null;
    this.layout = 'force'; // force, tree, radial
    this.zoom = d3.zoom().scaleExtent([0.1, 4]).on('zoom', (e) => this.handleZoom(e));
  }

  async loadSubgraph(centerId, depth = 2) {
    const response = await fetch(`/api/graph/subgraph?center=${centerId}&depth=${depth}`);
    const data = await response.json();

    this.nodes = data.nodes;
    this.edges = data.edges;

    this.render();
  }

  render() {
    // Clear existing
    this.container.innerHTML = '';

    const width = this.container.offsetWidth;
    const height = this.container.offsetHeight || 500;

    // Create SVG
    this.svg = d3.select(this.container)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .call(this.zoom);

    const g = this.svg.append('g');

    // Force simulation
    this.simulation = d3.forceSimulation(this.nodes)
      .force('link', d3.forceLink(this.edges).id(d => d.id).distance(100))
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(40));

    // Edges
    const link = g.append('g')
      .attr('class', 'links')
      .selectAll('line')
      .data(this.edges)
      .join('line')
      .attr('class', d => `link ${d.type}`)
      .attr('stroke-width', d => Math.sqrt(d.weight));

    // Edge labels
    const linkLabel = g.append('g')
      .attr('class', 'link-labels')
      .selectAll('text')
      .data(this.edges)
      .join('text')
      .attr('class', 'link-label')
      .text(d => d.type);

    // Nodes
    const node = g.append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(this.nodes)
      .join('g')
      .attr('class', d => `node ${d.type}`)
      .call(this.drag(this.simulation))
      .on('click', (e, d) => this.selectNode(d))
      .on('dblclick', (e, d) => this.expandNode(d));

    // Node circles
    node.append('circle')
      .attr('r', d => this.getNodeRadius(d))
      .attr('fill', d => this.getNodeColor(d));

    // Node labels
    node.append('text')
      .attr('dx', 15)
      .attr('dy', 5)
      .text(d => d.name);

    // Simulation tick
    this.simulation.on('tick', () => {
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);

      linkLabel
        .attr('x', d => (d.source.x + d.target.x) / 2)
        .attr('y', d => (d.source.y + d.target.y) / 2);

      node.attr('transform', d => `translate(${d.x},${d.y})`);
    });
  }

  getNodeColor(node) {
    const colors = {
      'class': '#6366f1',
      'interface': '#8b5cf6',
      'method': '#22c55e',
      'field': '#f59e0b',
      'table': '#ef4444',
      'file': '#64748b',
      'package': '#0ea5e9'
    };
    return colors[node.type] || '#6b7280';
  }

  getNodeRadius(node) {
    const sizes = {
      'class': 12,
      'interface': 10,
      'method': 6,
      'field': 5,
      'table': 10,
      'package': 14
    };
    return sizes[node.type] || 8;
  }

  selectNode(node) {
    this.selectedNode = node;
    this.container.dispatchEvent(new CustomEvent('nodeSelected', { detail: node }));
  }

  async expandNode(node) {
    await this.loadSubgraph(node.id, 1);
  }

  drag(simulation) {
    return d3.drag()
      .on('start', (e, d) => {
        if (!e.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (e, d) => {
        d.fx = e.x;
        d.fy = e.y;
      })
      .on('end', (e, d) => {
        if (!e.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
  }

  handleZoom(event) {
    this.svg.select('g').attr('transform', event.transform);
  }
}
```

---

## 3. JUnit Test Execution Tool

### 3.1 Übersicht

Tool zum Ausführen von JUnit-Tests mit automatischer Validierung der Ergebnisse und KI-gestützter Fix-Generierung bei Fehlern.

### 3.2 Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                  JUNIT TEST EXECUTION SYSTEM                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  User Request: "Führe Tests für UserService aus"                │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   TestDiscoveryService                       ││
│  │  - Findet Test-Klassen für Target-Klasse                    ││
│  │  - Analysiert Test-Annotations (@Test, @BeforeEach, etc.)   ││
│  │  - Erkennt Test-Dependencies (Mocks, TestContainers)        ││
│  └─────────────────────────────────────────────────────────────┘│
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   TestExecutionService                       ││
│  │  - Maven/Gradle Test-Runner                                  ││
│  │  - Streaming Output (SSE)                                    ││
│  │  - JUnit XML Report Parsing                                  ││
│  │  - Coverage-Erfassung (JaCoCo)                               ││
│  └─────────────────────────────────────────────────────────────┘│
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   TestResultAnalyzer                         ││
│  │  - Parse Failures/Errors                                     ││
│  │  - Extract Stack Traces                                      ││
│  │  - Map to Source Lines                                       ││
│  │  - Pattern Matching (bekannte Fehler)                        ││
│  └─────────────────────────────────────────────────────────────┘│
│         │                                                        │
│         ▼ (bei Fehlern)                                         │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   TestFixGenerator                           ││
│  │  - LLM-basierte Fix-Generierung                              ││
│  │  - Pattern-basierte Quick-Fixes                              ││
│  │  - Diff-Preview                                              ││
│  │  - Validation durch erneuten Test-Lauf                       ││
│  └─────────────────────────────────────────────────────────────┘│
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   Frontend: TestResultsPanel                 ││
│  │  ┌─────────────────────────────────────────────────────────┐││
│  │  │ Tests: UserServiceTest                     [▶ Run All] │││
│  │  ├─────────────────────────────────────────────────────────┤││
│  │  │ ✓ testCreateUser (0.12s)                               │││
│  │  │ ✓ testFindUserById (0.08s)                             │││
│  │  │ ✗ testUpdateUser (0.15s)                    [🔧 Fix]   │││
│  │  │   └─ AssertionError: expected:<John> but was:<Jane>    │││
│  │  │ ○ testDeleteUser (skipped)                             │││
│  │  ├─────────────────────────────────────────────────────────┤││
│  │  │ Coverage: 78%  ████████████████░░░░                    │││
│  │  │ Passed: 2/4 | Failed: 1 | Skipped: 1 | Time: 0.35s     │││
│  │  └─────────────────────────────────────────────────────────┘││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 Datenmodell

```python
# app/services/test_execution.py

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime

class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"

@dataclass
class TestCase:
    """Ein einzelner Test-Case."""
    name: str
    class_name: str
    status: TestStatus
    duration_seconds: float = 0.0

    # Failure/Error info
    failure_message: Optional[str] = None
    failure_type: Optional[str] = None  # AssertionError, NullPointerException, etc.
    stack_trace: Optional[str] = None

    # Source location
    file_path: Optional[str] = None
    line_number: Optional[int] = None

    # Fix suggestion
    suggested_fix: Optional[Dict[str, Any]] = None

@dataclass
class TestSuite:
    """Eine Test-Suite (Test-Klasse)."""
    name: str
    file_path: str
    tests: List[TestCase] = field(default_factory=list)

    # Timing
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    # Stats
    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.FAILED)

    @property
    def errors(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.ERROR)

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.SKIPPED)

@dataclass
class TestRun:
    """Ein kompletter Test-Lauf."""
    id: str
    session_id: str
    target: str  # Class/Package being tested
    status: TestStatus

    suites: List[TestSuite] = field(default_factory=list)

    # Timing
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # Coverage
    coverage_percent: Optional[float] = None
    coverage_report_path: Optional[str] = None

    # Build info
    build_tool: str = "maven"  # maven, gradle
    build_command: str = ""
    build_output: str = ""

    @property
    def total_tests(self) -> int:
        return sum(s.total for s in self.suites)

    @property
    def passed_tests(self) -> int:
        return sum(s.passed for s in self.suites)

    @property
    def failed_tests(self) -> int:
        return sum(s.failed for s in self.suites)

@dataclass
class TestFix:
    """Ein generierter Fix für einen fehlgeschlagenen Test."""
    id: str
    test_case: TestCase

    # Fix details
    fix_type: str  # "assertion", "null_check", "mock_setup", "implementation"
    description: str
    confidence: float  # 0.0 - 1.0

    # Code changes
    file_path: str
    original_code: str
    fixed_code: str
    diff: str

    # Validation
    validated: bool = False
    validation_passed: bool = False
    validation_output: Optional[str] = None


class TestExecutionService:
    """Führt Tests aus und sammelt Ergebnisse."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.java_path = config.get("java", {}).get("active_path")
        self.build_tool = self._detect_build_tool()

    def _detect_build_tool(self) -> str:
        """Erkennt Maven oder Gradle."""
        if self.java_path:
            path = Path(self.java_path)
            if (path / "pom.xml").exists():
                return "maven"
            if (path / "build.gradle").exists() or (path / "build.gradle.kts").exists():
                return "gradle"
        return "maven"

    async def run_tests(
        self,
        target: str,  # Class name or package
        session_id: str,
        with_coverage: bool = True
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Führt Tests aus und streamt Ergebnisse.

        Yields:
            - {"type": "started", "data": TestRun}
            - {"type": "test_started", "data": TestCase}
            - {"type": "test_finished", "data": TestCase}
            - {"type": "suite_finished", "data": TestSuite}
            - {"type": "finished", "data": TestRun}
            - {"type": "error", "data": {"message": str}}
        """
        run_id = str(uuid.uuid4())
        test_run = TestRun(
            id=run_id,
            session_id=session_id,
            target=target,
            status=TestStatus.RUNNING,
            started_at=datetime.utcnow(),
            build_tool=self.build_tool
        )

        yield {"type": "started", "data": asdict(test_run)}

        try:
            # Build command
            if self.build_tool == "maven":
                cmd = self._build_maven_command(target, with_coverage)
            else:
                cmd = self._build_gradle_command(target, with_coverage)

            test_run.build_command = cmd

            # Execute
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=self.java_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            output_lines = []
            async for line in process.stdout:
                line = line.decode('utf-8', errors='replace')
                output_lines.append(line)

                # Parse test events from output
                event = self._parse_test_output(line)
                if event:
                    yield event

            await process.wait()
            test_run.build_output = "".join(output_lines)

            # Parse JUnit XML reports
            suites = await self._parse_junit_reports()
            test_run.suites = suites

            # Parse coverage
            if with_coverage:
                coverage = await self._parse_coverage_report()
                test_run.coverage_percent = coverage

            # Final status
            test_run.status = TestStatus.PASSED if test_run.failed_tests == 0 else TestStatus.FAILED
            test_run.finished_at = datetime.utcnow()

            yield {"type": "finished", "data": asdict(test_run)}

        except Exception as e:
            test_run.status = TestStatus.ERROR
            yield {"type": "error", "data": {"message": str(e)}}

    def _build_maven_command(self, target: str, with_coverage: bool) -> str:
        """Baut Maven Test-Command."""
        cmd = "mvn test"

        # Target filter
        if target:
            if "." in target:
                # Package
                cmd += f" -Dtest={target}.**"
            else:
                # Class
                cmd += f" -Dtest={target}"

        # Coverage
        if with_coverage:
            cmd += " -Djacoco.destFile=target/jacoco.exec"

        cmd += " -Dsurefire.useFile=true"

        return cmd

    async def _parse_junit_reports(self) -> List[TestSuite]:
        """Parst JUnit XML Reports aus target/surefire-reports."""
        suites = []
        reports_dir = Path(self.java_path) / "target" / "surefire-reports"

        if not reports_dir.exists():
            return suites

        for xml_file in reports_dir.glob("TEST-*.xml"):
            suite = await self._parse_junit_xml(xml_file)
            if suite:
                suites.append(suite)

        return suites

    async def _parse_junit_xml(self, xml_path: Path) -> Optional[TestSuite]:
        """Parst eine JUnit XML Datei."""
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            suite = TestSuite(
                name=root.get("name", "Unknown"),
                file_path=str(xml_path),
                duration_seconds=float(root.get("time", 0))
            )

            for testcase in root.findall("testcase"):
                tc = TestCase(
                    name=testcase.get("name"),
                    class_name=testcase.get("classname"),
                    duration_seconds=float(testcase.get("time", 0)),
                    status=TestStatus.PASSED
                )

                # Check for failure
                failure = testcase.find("failure")
                if failure is not None:
                    tc.status = TestStatus.FAILED
                    tc.failure_type = failure.get("type")
                    tc.failure_message = failure.get("message")
                    tc.stack_trace = failure.text

                # Check for error
                error = testcase.find("error")
                if error is not None:
                    tc.status = TestStatus.ERROR
                    tc.failure_type = error.get("type")
                    tc.failure_message = error.get("message")
                    tc.stack_trace = error.text

                # Check for skipped
                skipped = testcase.find("skipped")
                if skipped is not None:
                    tc.status = TestStatus.SKIPPED

                suite.tests.append(tc)

            return suite

        except Exception as e:
            logger.error(f"Failed to parse JUnit XML {xml_path}: {e}")
            return None


class TestFixGenerator:
    """Generiert Fixes für fehlgeschlagene Tests."""

    # Known fix patterns
    _FIX_PATTERNS = [
        {
            "pattern": r"expected:<(.+)> but was:<(.+)>",
            "type": "assertion",
            "fix_template": "Update assertion or fix implementation"
        },
        {
            "pattern": r"NullPointerException",
            "type": "null_check",
            "fix_template": "Add null check or initialize object"
        },
        {
            "pattern": r"Wanted but not invoked",
            "type": "mock_setup",
            "fix_template": "Verify mock setup and method calls"
        },
    ]

    def __init__(self, llm_client, pattern_learner):
        self.llm_client = llm_client
        self.pattern_learner = pattern_learner

    async def generate_fix(self, test_case: TestCase) -> Optional[TestFix]:
        """Generiert einen Fix für einen fehlgeschlagenen Test."""
        if not test_case.stack_trace:
            return None

        # 1. Check known patterns
        for pattern in self._FIX_PATTERNS:
            if re.search(pattern["pattern"], test_case.failure_message or ""):
                return await self._generate_pattern_fix(test_case, pattern)

        # 2. Check learned patterns
        learned_pattern = await self.pattern_learner.suggest(
            test_case.failure_message,
            test_case.stack_trace
        )
        if learned_pattern and learned_pattern.confidence > 0.7:
            return await self._apply_learned_pattern(test_case, learned_pattern)

        # 3. LLM-based fix generation
        return await self._generate_llm_fix(test_case)

    async def _generate_llm_fix(self, test_case: TestCase) -> Optional[TestFix]:
        """Generiert einen Fix mit LLM."""
        # Read source files
        test_source = await self._read_test_source(test_case)
        impl_source = await self._read_implementation_source(test_case)

        prompt = f"""Analysiere diesen fehlgeschlagenen JUnit-Test und generiere einen Fix.

TEST-KLASSE:
```java
{test_source}
```

IMPLEMENTIERUNG:
```java
{impl_source}
```

FEHLER:
{test_case.failure_message}

STACK TRACE:
{test_case.stack_trace}

Generiere einen minimalen Fix. Antworte im Format:
FIX_TYPE: [assertion|null_check|mock_setup|implementation]
DESCRIPTION: [Kurze Beschreibung]
FILE: [Pfad zur Datei die geändert werden soll]
ORIGINAL:
```java
[Original Code Block]
```
FIXED:
```java
[Fixed Code Block]
```
"""

        response = await self.llm_client.chat([{"role": "user", "content": prompt}])

        # Parse response
        fix = self._parse_fix_response(response, test_case)
        return fix

    async def validate_fix(self, fix: TestFix) -> TestFix:
        """Validiert einen Fix durch erneuten Test-Lauf."""
        # 1. Backup original file
        original_content = Path(fix.file_path).read_text()

        try:
            # 2. Apply fix
            new_content = original_content.replace(fix.original_code, fix.fixed_code)
            Path(fix.file_path).write_text(new_content)

            # 3. Run test
            execution_service = TestExecutionService(self.config)
            async for event in execution_service.run_tests(
                fix.test_case.class_name,
                session_id="validation",
                with_coverage=False
            ):
                if event["type"] == "finished":
                    run = event["data"]
                    fix.validated = True
                    fix.validation_passed = run["status"] == "passed"
                    break

        finally:
            # 4. Restore original (user decides whether to keep fix)
            Path(fix.file_path).write_text(original_content)

        return fix
```

### 3.4 Agent Tool

```python
# app/agent/test_tools.py (Erweiterung)

def register_test_execution_tools(registry: ToolRegistry) -> int:
    """Registriert Test-Ausführungs-Tools."""

    count = 0

    # ══════════════════════════════════════════════════════════════════════════════
    # run_junit_tests
    # ══════════════════════════════════════════════════════════════════════════════

    async def run_junit_tests(**kwargs: Any) -> ToolResult:
        """
        Führt JUnit-Tests aus und zeigt Ergebnisse.

        Nutze dieses Tool um:
        - Tests für eine Klasse/Package auszuführen
        - Test-Ergebnisse und Coverage zu sehen
        - Fehlgeschlagene Tests zu analysieren

        Bei Fehlern: Nutze suggest_test_fix für Lösungsvorschläge.
        """
        target: str = kwargs.get("target", "").strip()
        with_coverage: bool = kwargs.get("with_coverage", True)

        if not target:
            return ToolResult(
                success=False,
                error="target ist erforderlich. Beispiel: run_junit_tests(target=\"UserService\")"
            )

        session_id = kwargs.get("_session_id", "default")
        service = TestExecutionService(settings.model_dump())

        results = []
        async for event in service.run_tests(target, session_id, with_coverage):
            results.append(event)

        # Format output
        final_event = next((e for e in reversed(results) if e["type"] == "finished"), None)

        if not final_event:
            return ToolResult(success=False, error="Test-Lauf fehlgeschlagen")

        run = final_event["data"]

        output = [
            f"# Test-Ergebnisse: {run['target']}",
            f"Status: {run['status'].upper()}",
            f"Tests: {run['passed_tests']}/{run['total_tests']} bestanden",
            ""
        ]

        for suite in run["suites"]:
            output.append(f"## {suite['name']}")
            for tc in suite["tests"]:
                icon = {"passed": "✓", "failed": "✗", "error": "⚠", "skipped": "○"}[tc["status"]]
                output.append(f"  {icon} {tc['name']} ({tc['duration_seconds']:.2f}s)")
                if tc["failure_message"]:
                    output.append(f"    └─ {tc['failure_message']}")
            output.append("")

        if run.get("coverage_percent"):
            output.append(f"Coverage: {run['coverage_percent']:.1f}%")

        return ToolResult(
            success=run["status"] == "passed",
            data="\n".join(output),
            confirmation_data={"test_run": run}
        )

    registry.register(Tool(
        name="run_junit_tests",
        description="Führt JUnit-Tests aus. Parameter: target (Klasse/Package), with_coverage (bool, default: true)",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter("target", "string", "Klasse oder Package zum Testen", required=True),
            ToolParameter("with_coverage", "boolean", "Coverage-Report generieren", required=False, default=True),
        ],
        handler=run_junit_tests
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════════
    # suggest_test_fix
    # ══════════════════════════════════════════════════════════════════════════════

    async def suggest_test_fix(**kwargs: Any) -> ToolResult:
        """
        Generiert Fix-Vorschläge für fehlgeschlagene Tests.

        Nutze dieses Tool nach run_junit_tests wenn Tests fehlschlagen.
        """
        test_class: str = kwargs.get("test_class", "").strip()
        test_method: str = kwargs.get("test_method", "").strip()
        error_message: str = kwargs.get("error_message", "").strip()

        if not test_class or not error_message:
            return ToolResult(
                success=False,
                error="test_class und error_message sind erforderlich"
            )

        # Create TestCase from params
        test_case = TestCase(
            name=test_method or "unknown",
            class_name=test_class,
            status=TestStatus.FAILED,
            failure_message=error_message
        )

        fix_generator = TestFixGenerator(llm_client, pattern_learner)
        fix = await fix_generator.generate_fix(test_case)

        if not fix:
            return ToolResult(
                success=False,
                error="Konnte keinen Fix generieren"
            )

        output = [
            f"# Fix-Vorschlag für {test_class}.{test_method}",
            f"Typ: {fix.fix_type}",
            f"Confidence: {fix.confidence:.0%}",
            f"Beschreibung: {fix.description}",
            "",
            f"## Änderung in: {fix.file_path}",
            "```diff",
            fix.diff,
            "```"
        ]

        return ToolResult(
            success=True,
            data="\n".join(output),
            requires_confirmation=True,
            confirmation_data={
                "fix": asdict(fix),
                "action": "apply_fix"
            }
        )

    registry.register(Tool(
        name="suggest_test_fix",
        description="Generiert Fix-Vorschläge für fehlgeschlagene Tests",
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter("test_class", "string", "Name der Test-Klasse", required=True),
            ToolParameter("test_method", "string", "Name der Test-Methode", required=False),
            ToolParameter("error_message", "string", "Fehlermeldung", required=True),
        ],
        handler=suggest_test_fix
    ))
    count += 1

    return count
```

### 3.5 API Endpoints

```python
# app/api/routes/tests.py

@router.post("/api/tests/run")
async def run_tests(request: TestRunRequest) -> StreamingResponse:
    """Startet Test-Lauf mit SSE Streaming."""
    pass

@router.get("/api/tests/runs/{session_id}")
async def get_test_runs(session_id: str) -> List[TestRun]:
    """Alle Test-Läufe einer Session."""
    pass

@router.get("/api/tests/runs/{session_id}/{run_id}")
async def get_test_run(session_id: str, run_id: str) -> TestRun:
    """Details eines Test-Laufs."""
    pass

@router.post("/api/tests/fix/generate")
async def generate_fix(request: FixRequest) -> TestFix:
    """Generiert Fix für fehlgeschlagenen Test."""
    pass

@router.post("/api/tests/fix/validate")
async def validate_fix(request: ValidateFixRequest) -> TestFix:
    """Validiert Fix durch Test-Lauf."""
    pass

@router.post("/api/tests/fix/apply")
async def apply_fix(request: ApplyFixRequest) -> Dict:
    """Wendet Fix an (schreibt Datei)."""
    pass
```

---

## Implementierungs-Reihenfolge

### Phase 3.1: Task-Progress-UI (3-5 Tage)

1. `TaskTracker` Backend-Service
2. SSE Integration in Orchestrator
3. `TaskProgressPanel` Frontend-Komponente
4. CSS Styling
5. Integration Tests

### Phase 3.2: JUnit Test Execution (5-7 Tage)

1. `TestExecutionService` mit Maven-Support
2. JUnit XML Parser
3. `TestFixGenerator` mit Pattern-Matching
4. Agent Tools (`run_junit_tests`, `suggest_test_fix`)
5. Frontend `TestResultsPanel`
6. LLM-Fix-Integration

### Phase 3.3: Knowledge Graph (7-10 Tage)

1. `KnowledgeGraphStore` SQLite-Backend
2. `JavaGraphBuilder` Parser
3. `GraphQueryEngine` Abfrage-Engine
4. API Endpoints
5. D3.js Frontend-Visualisierung
6. Integration in Workspace Panel

---

## Abhängigkeiten

### Externe Libraries

```txt
# requirements.txt Ergänzungen
d3==7.8.5  # Frontend (CDN oder npm)
```

### Interne Module

- Task-Progress: `orchestrator.py`, `sse_utils.py`
- JUnit: `junit_tools.py`, `pattern_learner.py`, `llm_client.py`
- Knowledge Graph: `java_indexer.py`, `code_search.py`

---

## Offene Fragen

1. **Knowledge Graph Skalierung**: Wie viele Nodes/Edges bei großen Repos? Index-Strategie?
2. **Test-Isolation**: Docker-Container für Test-Ausführung?
3. **Fix-Validation**: Wie mit Side-Effects umgehen?
4. **Graph-Persistenz**: SQLite ausreichend oder Neo4j?

---

> **Nächster Schritt**: `/sc:implement` für Phase 3.1 (Task-Progress-UI)
