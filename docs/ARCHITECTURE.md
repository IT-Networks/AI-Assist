# AI-Assist Evolution - System Architecture

**Version:** 1.0
**Datum:** 2026-03-05
**Status:** Design Specification

---

## 1. Architektur-Übersicht

### 1.1 High-Level Architektur

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (SPA)                                  │
│  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────────────────────┐  │
│  │   Explorer  │  │       Chat          │  │      Context Panel          │  │
│  │   Panel     │  │       Panel         │  │      + Skill Manager        │  │
│  └─────────────┘  └─────────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI BACKEND                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         API Layer                                    │    │
│  │  /api/chat  /api/skills  /api/handbook  /api/files  /api/agent      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      Agent Orchestrator                              │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │    │
│  │  │ Tool Router  │  │ Context      │  │ Permission   │               │    │
│  │  │              │  │ Assembler    │  │ Manager      │               │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         Tool Layer                                   │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ │    │
│  │  │Search  │ │Read    │ │Write   │ │Handbook│ │Skill   │ │Code    │ │    │
│  │  │Index   │ │File    │ │File    │ │Search  │ │Query   │ │Analysis│ │    │
│  │  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                       Service Layer                                  │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │    │
│  │  │ LLM Client   │  │ Skill Manager│  │ File Manager │               │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │    │
│  │  │ Handbook     │  │ Code Indexer │  │ PDF Reader   │               │    │
│  │  │ Indexer      │  │ (Java/Python)│  │              │               │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                       Storage Layer                                  │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │    │
│  │  │ SQLite FTS5  │  │ Skill Files  │  │ Session      │               │    │
│  │  │ Indexes      │  │ (YAML)       │  │ Store        │               │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  Java/Python    │      │  HTML Handbuch  │      │  LLM Server     │
│  Repositories   │      │  (Netzlaufwerk) │      │  (OpenAI API)   │
└─────────────────┘      └─────────────────┘      └─────────────────┘
```

---

## 2. Komponenten-Design

### 2.1 Agent Orchestrator (NEU)

Der Agent Orchestrator ist das Herzstück der Claude-Code-ähnlichen Funktionalität.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Agent Orchestrator                           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    Agent Loop                            │    │
│  │                                                          │    │
│  │  1. User Message empfangen                               │    │
│  │  2. Aktive Skills laden (System Prompts + Wissen)        │    │
│  │  3. LLM aufrufen mit Tool-Definitionen                   │    │
│  │  4. Tool Calls auswerten und ausführen                   │    │
│  │  5. Bei Schreib-Ops: User-Bestätigung einholen           │    │
│  │  6. Ergebnisse in Kontext einfügen                       │    │
│  │  7. Wiederholen bis fertig oder max_iterations           │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   Tool Router                            │    │
│  │                                                          │    │
│  │  Verfügbare Tools:                                       │    │
│  │  - search_code(query, language) → Relevante Dateien      │    │
│  │  - search_handbook(query) → Handbuch-Seiten              │    │
│  │  - search_skills(query) → Skill-Wissensbasen             │    │
│  │  - read_file(path) → Dateiinhalt                         │    │
│  │  - write_file(path, content) → Datei erstellen*          │    │
│  │  - edit_file(path, changes) → Datei ändern*              │    │
│  │  - list_files(path, pattern) → Dateiliste                │    │
│  │  - search_confluence(query) → Confluence-Seiten          │    │
│  │  - search_pdf(pdf_id, query) → PDF-Inhalte               │    │
│  │                                                          │    │
│  │  * = Benötigt User-Bestätigung                           │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

#### 2.1.1 Agent Loop Implementierung

```python
# app/core/agent.py

class AgentOrchestrator:
    """
    Koordiniert den Agent-Loop ähnlich wie Claude Code.
    Verarbeitet User-Anfragen, ruft Tools auf, sammelt Kontext.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        skill_manager: SkillManager,
        permission_manager: PermissionManager,
    ):
        self.llm = llm_client
        self.tools = tool_registry
        self.skills = skill_manager
        self.permissions = permission_manager
        self.max_iterations = 10

    async def process(
        self,
        session_id: str,
        user_message: str,
        active_skills: List[str],
        mode: AgentMode,  # READ_ONLY | WRITE_WITH_CONFIRM | AUTONOMOUS
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Verarbeitet eine User-Anfrage im Agent-Loop.
        Yieldet Events für Frontend (Streaming, Tool-Calls, Confirmations).
        """
        # 1. System Prompt aus aktiven Skills zusammenbauen
        system_prompt = self._build_system_prompt(active_skills)

        # 2. Tool-Definitionen basierend auf Mode
        available_tools = self.tools.get_definitions(mode)

        # 3. Agent Loop
        messages = [{"role": "system", "content": system_prompt}]
        context_items = []

        for iteration in range(self.max_iterations):
            # Kontext hinzufügen
            if context_items:
                context_block = self._format_context(context_items)
                messages.append({"role": "system", "content": context_block})

            messages.append({"role": "user", "content": user_message})

            # LLM aufrufen
            async for event in self.llm.chat_with_tools(
                messages=messages,
                tools=available_tools,
            ):
                if event.type == "token":
                    yield AgentEvent(type="token", data=event.token)

                elif event.type == "tool_call":
                    yield AgentEvent(type="tool_start", data=event.tool_call)

                    # Schreib-Operation? Bestätigung einholen
                    if self._requires_confirmation(event.tool_call, mode):
                        yield AgentEvent(
                            type="confirm_required",
                            data=event.tool_call,
                        )
                        # Warte auf Bestätigung vom Frontend
                        confirmed = yield  # Coroutine receives confirmation
                        if not confirmed:
                            yield AgentEvent(type="tool_cancelled", data=event.tool_call)
                            continue

                    # Tool ausführen
                    result = await self.tools.execute(event.tool_call)
                    context_items.append(result)

                    yield AgentEvent(type="tool_result", data=result)

                elif event.type == "done":
                    yield AgentEvent(type="done", data=event.response)
                    return
```

#### 2.1.2 Tool Registry

```python
# app/core/tools.py

class ToolRegistry:
    """Registry für alle verfügbaren Tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get_definitions(self, mode: AgentMode) -> List[dict]:
        """Gibt Tool-Definitionen im OpenAI-Format zurück."""
        definitions = []
        for tool in self._tools.values():
            if mode == AgentMode.READ_ONLY and tool.is_write_operation:
                continue
            definitions.append(tool.to_openai_schema())
        return definitions

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if not tool:
            return ToolResult(error=f"Unknown tool: {call.name}")
        return await tool.execute(**call.arguments)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema
    is_write_operation: bool
    handler: Callable[..., Awaitable[ToolResult]]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

    async def execute(self, **kwargs) -> ToolResult:
        return await self.handler(**kwargs)
```

---

### 2.2 Handbuch-Indexer (NEU)

```
┌─────────────────────────────────────────────────────────────────┐
│                     Handbook Indexer                             │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    HTML Parser                           │    │
│  │                                                          │    │
│  │  - BeautifulSoup für HTML-Parsing                        │    │
│  │  - Extraktion von: Title, Headings, Tables, Text         │    │
│  │  - Tab-Struktur erkennen (Subordner = Tabs)              │    │
│  │  - Service-Metadaten aus Struktur ableiten               │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  SQLite FTS5 Index                       │    │
│  │                                                          │    │
│  │  handbook_fts:                                           │    │
│  │  - file_path (UNINDEXED)                                 │    │
│  │  - service_name                                          │    │
│  │  - tab_name                                              │    │
│  │  - title                                                 │    │
│  │  - headings                                              │    │
│  │  - content                                               │    │
│  │  - tables_json                                           │    │
│  │                                                          │    │
│  │  handbook_services:                                      │    │
│  │  - service_id (PK)                                       │    │
│  │  - service_name                                          │    │
│  │  - description                                           │    │
│  │  - input_fields_json                                     │    │
│  │  - output_fields_json                                    │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

#### 2.2.1 Handbuch-Struktur Parser

```python
# app/services/handbook_indexer.py

class HandbookIndexer:
    """
    Indexiert HTML-Handbücher von Netzlaufwerken.
    Unterstützt Tab-basierte Service-Dokumentation.
    """

    def __init__(self, db_path: str = "./index/handbook_index.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript("""
                -- Volltextsuche über alle Handbuch-Seiten
                CREATE VIRTUAL TABLE IF NOT EXISTS handbook_fts USING fts5(
                    file_path UNINDEXED,
                    service_name,
                    tab_name,
                    title,
                    headings,
                    content,
                    tables_text,
                    tokenize='porter unicode61'
                );

                -- Service-Übersicht mit strukturierten Daten
                CREATE TABLE IF NOT EXISTS handbook_services (
                    service_id TEXT PRIMARY KEY,
                    service_name TEXT,
                    description TEXT,
                    tabs_json TEXT,          -- [{name, file_path}]
                    input_fields_json TEXT,  -- [{name, type, description, required}]
                    output_fields_json TEXT, -- [{name, type, description}]
                    call_variants_json TEXT  -- [{method, url, description}]
                );

                -- Feld-Definitionen (für Feld-Seiten)
                CREATE TABLE IF NOT EXISTS handbook_fields (
                    field_id TEXT PRIMARY KEY,
                    field_name TEXT,
                    field_type TEXT,
                    description TEXT,
                    used_in_services TEXT,   -- JSON array of service_ids
                    source_file TEXT
                );

                -- Metadaten
                CREATE TABLE IF NOT EXISTS handbook_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    def build(self, handbook_path: str, force: bool = False) -> Dict:
        """
        Indexiert alle HTML-Dateien im Handbuch-Verzeichnis.

        Erwartete Struktur:
        /handbook/
        ├── index.html
        ├── funktionen/
        │   ├── service-a/
        │   │   ├── uebersicht.htm
        │   │   ├── eingabe.htm
        │   │   └── ausgabe.htm
        │   └── service-b/
        │       └── ...
        └── felder/
            ├── feld-xyz.htm
            └── ...
        """
        start = time.time()
        handbook = Path(handbook_path)

        if not handbook.exists():
            raise ValueError(f"Handbuch-Pfad existiert nicht: {handbook_path}")

        stats = {"indexed": 0, "services": 0, "fields": 0, "errors": 0}

        # 1. Alle HTML/HTM Dateien finden
        html_files = list(handbook.rglob("*.htm")) + list(handbook.rglob("*.html"))

        # 2. Service-Struktur analysieren
        services = self._analyze_service_structure(handbook, html_files)
        stats["services"] = len(services)

        # 3. Jede Datei indexieren
        for html_file in html_files:
            try:
                self._index_html_file(html_file, handbook, services)
                stats["indexed"] += 1
            except Exception as e:
                stats["errors"] += 1
                print(f"Fehler bei {html_file}: {e}")

        # 4. Services speichern
        for service in services.values():
            self._save_service(service)

        stats["duration_s"] = round(time.time() - start, 2)
        return stats

    def _analyze_service_structure(
        self,
        root: Path,
        html_files: List[Path]
    ) -> Dict[str, ServiceInfo]:
        """Analysiert die Ordnerstruktur um Services und Tabs zu erkennen."""
        services = {}

        funktionen_dir = root / "funktionen"
        if funktionen_dir.exists():
            for service_dir in funktionen_dir.iterdir():
                if service_dir.is_dir():
                    service_id = service_dir.name
                    tabs = []
                    for htm_file in service_dir.glob("*.htm"):
                        tabs.append({
                            "name": htm_file.stem,
                            "file_path": str(htm_file.relative_to(root))
                        })
                    services[service_id] = ServiceInfo(
                        service_id=service_id,
                        service_name=service_id.replace("-", " ").title(),
                        tabs=tabs
                    )

        return services

    def _index_html_file(
        self,
        file_path: Path,
        root: Path,
        services: Dict
    ) -> None:
        """Parsed eine HTML-Datei und fügt sie zum Index hinzu."""
        from bs4 import BeautifulSoup

        content = file_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(content, "lxml")

        # Metadaten extrahieren
        title = soup.title.string if soup.title else file_path.stem
        headings = " ".join(h.get_text() for h in soup.find_all(["h1", "h2", "h3"]))

        # Text extrahieren (ohne Scripts/Styles)
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text_content = soup.get_text(separator=" ", strip=True)

        # Tabellen extrahieren
        tables_text = self._extract_tables(soup)

        # Service/Tab ermitteln
        rel_path = file_path.relative_to(root)
        service_name, tab_name = self._detect_service_tab(rel_path)

        # In FTS5 Index einfügen
        with self._connect() as con:
            con.execute("DELETE FROM handbook_fts WHERE file_path=?", (str(rel_path),))
            con.execute(
                """INSERT INTO handbook_fts
                   (file_path, service_name, tab_name, title, headings, content, tables_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(rel_path), service_name, tab_name, title, headings,
                 text_content[:100000], tables_text[:50000])
            )

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Volltext-Suche über das gesamte Handbuch."""
        safe_query = query.replace('"', '""')

        with self._connect() as con:
            rows = con.execute(
                """
                SELECT file_path, service_name, tab_name, title,
                       snippet(handbook_fts, 5, '>>>', '<<<', '...', 30) AS snippet,
                       rank
                FROM handbook_fts
                WHERE handbook_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, top_k)
            ).fetchall()

        return [
            {
                "file_path": row[0],
                "service_name": row[1],
                "tab_name": row[2],
                "title": row[3],
                "snippet": row[4],
                "rank": row[5],
            }
            for row in rows
        ]

    def get_service_info(self, service_id: str) -> Optional[Dict]:
        """Gibt strukturierte Service-Informationen zurück."""
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM handbook_services WHERE service_id=?",
                (service_id,)
            ).fetchone()

        if not row:
            return None

        return {
            "service_id": row[0],
            "service_name": row[1],
            "description": row[2],
            "tabs": json.loads(row[3] or "[]"),
            "input_fields": json.loads(row[4] or "[]"),
            "output_fields": json.loads(row[5] or "[]"),
            "call_variants": json.loads(row[6] or "[]"),
        }
```

---

### 2.3 Skill-System (NEU)

```
┌─────────────────────────────────────────────────────────────────┐
│                      Skill Manager                               │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  Skill Definitions                       │    │
│  │                                                          │    │
│  │  ./skills/                                               │    │
│  │  ├── java-guidelines.yaml                                │    │
│  │  ├── project-standards.yaml                              │    │
│  │  └── api-documentation.yaml                              │    │
│  │                                                          │    │
│  │  Struktur:                                               │    │
│  │  - id, name, description                                 │    │
│  │  - type: knowledge | prompt | tool | hybrid              │    │
│  │  - activation: always | on-demand | auto                 │    │
│  │  - system_prompt: String                                 │    │
│  │  - knowledge_sources: [{type, path}]                     │    │
│  │  - tools: [tool_definitions]                             │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   Skill Index (SQLite)                   │    │
│  │                                                          │    │
│  │  skills:                                                 │    │
│  │  - skill_id (PK)                                         │    │
│  │  - name, description                                     │    │
│  │  - type, activation_mode                                 │    │
│  │  - system_prompt                                         │    │
│  │  - file_path                                             │    │
│  │                                                          │    │
│  │  skill_knowledge_fts:                                    │    │
│  │  - skill_id, chunk_id                                    │    │
│  │  - content (FTS5)                                        │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              PDF to Skill Transformer                    │    │
│  │                                                          │    │
│  │  1. PDF hochladen                                        │    │
│  │  2. Text extrahieren (mit Chunking für große PDFs)       │    │
│  │  3. Geführter Dialog:                                    │    │
│  │     - Zweck des Skills?                                  │    │
│  │     - Trigger-Wörter?                                    │    │
│  │     - Welche Abschnitte relevant?                        │    │
│  │  4. Skill-YAML generieren                                │    │
│  │  5. Wissensbasis indexieren (Chunks in FTS5)             │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

#### 2.3.1 Skill Definition Schema

```yaml
# skills/java-coding-guidelines.yaml

id: java-coding-guidelines
name: Java Coding Guidelines
description: Programmierrichtlinien für Java Enterprise Entwicklung
version: "1.0"
type: hybrid  # knowledge + prompt

# Wann wird der Skill aktiviert?
activation:
  mode: on-demand  # always | on-demand | auto
  trigger_words:   # Nur für mode=auto
    - richtlinie
    - coding style
    - convention
    - best practice

# System-Prompt der bei Aktivierung hinzugefügt wird
system_prompt: |
  Du befolgst die Java-Programmierrichtlinien aus dem beigefügten Kontext.
  Wichtige Regeln:
  - Keine Magic Numbers - verwende Konstanten
  - Klassen max. 500 Zeilen
  - Methoden max. 30 Zeilen
  - Javadoc für alle public Methoden
  - Exceptions nie verschlucken

  Bei Code-Vorschlägen: Beachte diese Richtlinien und erkläre Abweichungen.

# Wissensquellen die durchsucht werden
knowledge_sources:
  - type: pdf
    path: "./skills/data/java-guidelines.pdf"
    chunk_size: 1000  # Tokens pro Chunk
    chunk_overlap: 100
  - type: markdown
    path: "./skills/data/additional-rules.md"
  - type: text
    content: |
      Zusätzliche projektspezifische Regeln:
      - Lombok nur für @Data und @Builder
      - Optional statt null zurückgeben

# Optionale Tools die der Skill bereitstellt
tools: []

# Metadaten
metadata:
  author: "Team Architecture"
  created: "2026-01-15"
  tags: ["java", "guidelines", "coding-standards"]
```

#### 2.3.2 Skill Manager Implementierung

```python
# app/services/skill_manager.py

class SkillManager:
    """
    Verwaltet Skills: Laden, Aktivieren, Wissenssuche.
    """

    def __init__(
        self,
        skills_dir: str = "./skills",
        db_path: str = "./index/skills_index.db"
    ):
        self.skills_dir = Path(skills_dir)
        self.db_path = Path(db_path)
        self._skills: Dict[str, Skill] = {}
        self._init_db()
        self._load_skills()

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    type TEXT,
                    activation_mode TEXT,
                    system_prompt TEXT,
                    file_path TEXT,
                    metadata_json TEXT
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS skill_knowledge_fts USING fts5(
                    skill_id UNINDEXED,
                    chunk_id UNINDEXED,
                    source_path UNINDEXED,
                    content,
                    tokenize='porter unicode61'
                );
            """)

    def _load_skills(self) -> None:
        """Lädt alle Skill-Definitionen aus dem Skills-Verzeichnis."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        for yaml_file in self.skills_dir.glob("*.yaml"):
            try:
                skill = Skill.from_yaml(yaml_file)
                self._skills[skill.id] = skill
                self._index_skill(skill)
            except Exception as e:
                print(f"Fehler beim Laden von {yaml_file}: {e}")

    def _index_skill(self, skill: Skill) -> None:
        """Indexiert Skill-Metadaten und Wissensquellen."""
        with self._connect() as con:
            # Skill-Metadaten speichern
            con.execute(
                """INSERT OR REPLACE INTO skills
                   (skill_id, name, description, type, activation_mode,
                    system_prompt, file_path, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill.id, skill.name, skill.description, skill.type,
                 skill.activation.mode, skill.system_prompt,
                 str(skill.file_path), json.dumps(skill.metadata))
            )

            # Wissensquellen chunken und indexieren
            con.execute(
                "DELETE FROM skill_knowledge_fts WHERE skill_id=?",
                (skill.id,)
            )

            for source in skill.knowledge_sources:
                chunks = self._chunk_source(source)
                for i, chunk in enumerate(chunks):
                    con.execute(
                        """INSERT INTO skill_knowledge_fts
                           (skill_id, chunk_id, source_path, content)
                           VALUES (?, ?, ?, ?)""",
                        (skill.id, f"{skill.id}_{i}", source.path, chunk)
                    )

    def _chunk_source(self, source: KnowledgeSource) -> List[str]:
        """Chunked eine Wissensquelle für Embedding/Suche."""
        if source.type == "pdf":
            text = self._extract_pdf_text(source.path)
        elif source.type == "markdown" or source.type == "text":
            if source.content:
                text = source.content
            else:
                text = Path(source.path).read_text(encoding="utf-8")
        else:
            return []

        # Einfaches Chunking nach Token-Anzahl
        chunk_size = source.chunk_size or 1000
        overlap = source.chunk_overlap or 100

        return self._split_into_chunks(text, chunk_size, overlap)

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Dict]:
        return [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "type": s.type,
                "activation_mode": s.activation.mode,
            }
            for s in self._skills.values()
        ]

    def search_knowledge(
        self,
        skill_ids: List[str],
        query: str,
        top_k: int = 5
    ) -> List[Dict]:
        """Durchsucht die Wissensbasen der angegebenen Skills."""
        if not skill_ids:
            return []

        placeholders = ",".join("?" * len(skill_ids))
        safe_query = query.replace('"', '""')

        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT skill_id, source_path,
                       snippet(skill_knowledge_fts, 3, '>>>', '<<<', '...', 30) AS snippet,
                       rank
                FROM skill_knowledge_fts
                WHERE skill_id IN ({placeholders})
                  AND skill_knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (*skill_ids, safe_query, top_k)
            ).fetchall()

        return [
            {
                "skill_id": row[0],
                "source_path": row[1],
                "snippet": row[2],
                "rank": row[3],
            }
            for row in rows
        ]

    def build_system_prompt(self, active_skill_ids: List[str]) -> str:
        """
        Baut den System-Prompt aus allen aktiven Skills zusammen.
        """
        prompts = []
        for skill_id in active_skill_ids:
            skill = self._skills.get(skill_id)
            if skill and skill.system_prompt:
                prompts.append(f"=== {skill.name} ===\n{skill.system_prompt}")

        if not prompts:
            return ""

        return "\n\n".join(prompts)

    async def create_skill_from_pdf(
        self,
        pdf_path: str,
        name: str,
        description: str,
        trigger_words: List[str],
        system_prompt: str,
    ) -> Skill:
        """
        Erstellt einen neuen Skill aus einer PDF-Datei.
        Wird vom geführten Dialog aufgerufen.
        """
        skill_id = slugify(name)
        skill_file = self.skills_dir / f"{skill_id}.yaml"

        # PDF in skills/data/ kopieren
        data_dir = self.skills_dir / "data"
        data_dir.mkdir(exist_ok=True)
        pdf_dest = data_dir / Path(pdf_path).name
        shutil.copy(pdf_path, pdf_dest)

        # Skill-Definition erstellen
        skill_def = {
            "id": skill_id,
            "name": name,
            "description": description,
            "version": "1.0",
            "type": "knowledge",
            "activation": {
                "mode": "on-demand",
                "trigger_words": trigger_words,
            },
            "system_prompt": system_prompt,
            "knowledge_sources": [
                {
                    "type": "pdf",
                    "path": str(pdf_dest.relative_to(self.skills_dir.parent)),
                    "chunk_size": 1000,
                    "chunk_overlap": 100,
                }
            ],
            "tools": [],
            "metadata": {
                "created": datetime.now().isoformat(),
                "source_pdf": Path(pdf_path).name,
            }
        }

        # YAML speichern
        with open(skill_file, "w", encoding="utf-8") as f:
            yaml.dump(skill_def, f, default_flow_style=False, allow_unicode=True)

        # Skill laden und indexieren
        skill = Skill.from_yaml(skill_file)
        self._skills[skill.id] = skill
        self._index_skill(skill)

        return skill
```

---

### 2.4 File Operations (NEU)

```
┌─────────────────────────────────────────────────────────────────┐
│                     File Manager                                 │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  Permission System                       │    │
│  │                                                          │    │
│  │  Modes:                                                  │    │
│  │  - READ_ONLY: Nur Lesen, keine Schreiboperationen        │    │
│  │  - WRITE_WITH_CONFIRM: Schreiben mit User-Bestätigung    │    │
│  │  - AUTONOMOUS: Schreiben ohne Bestätigung (gefährlich)   │    │
│  │                                                          │    │
│  │  Whitelist:                                              │    │
│  │  - allowed_paths: ["/path/to/project", ...]              │    │
│  │  - allowed_extensions: [".java", ".py", ".md", ...]      │    │
│  │  - denied_patterns: ["**/node_modules/**", "**/.git/**"] │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    Operations                            │    │
│  │                                                          │    │
│  │  read_file(path) → content                               │    │
│  │  - Validiert gegen Whitelist                             │    │
│  │  - Liest Datei, gibt Inhalt zurück                       │    │
│  │                                                          │    │
│  │  write_file(path, content) → success                     │    │
│  │  - Validiert gegen Whitelist                             │    │
│  │  - Bei WRITE_WITH_CONFIRM: Wartet auf Bestätigung        │    │
│  │  - Erstellt Backup (optional)                            │    │
│  │  - Schreibt Datei                                        │    │
│  │                                                          │    │
│  │  edit_file(path, old, new) → success                     │    │
│  │  - Validiert gegen Whitelist                             │    │
│  │  - Generiert Diff für Preview                            │    │
│  │  - Bei Bestätigung: Führt Ersetzung durch                │    │
│  │                                                          │    │
│  │  list_files(path, pattern) → files                       │    │
│  │  - Glob-basierte Dateiliste                              │    │
│  │  - Respektiert denied_patterns                           │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

#### 2.4.1 File Manager Implementierung

```python
# app/services/file_manager.py

class FileManager:
    """
    Verwaltet Datei-Operationen mit Sicherheits-Checks.
    """

    def __init__(self, config: FileOperationsConfig):
        self.allowed_paths = [Path(p).resolve() for p in config.allowed_paths]
        self.allowed_extensions = set(config.allowed_extensions)
        self.denied_patterns = config.denied_patterns
        self.backup_enabled = config.backup_enabled
        self.backup_dir = Path(config.backup_dir)

    def _validate_path(self, path: str) -> Path:
        """Validiert ob der Pfad erlaubt ist."""
        resolved = Path(path).resolve()

        # Muss in erlaubtem Pfad liegen
        if not any(self._is_subpath(resolved, allowed) for allowed in self.allowed_paths):
            raise PermissionError(f"Pfad nicht erlaubt: {path}")

        # Extension prüfen (nur für Dateien)
        if resolved.suffix and resolved.suffix not in self.allowed_extensions:
            raise PermissionError(f"Dateityp nicht erlaubt: {resolved.suffix}")

        # Denied patterns prüfen
        for pattern in self.denied_patterns:
            if resolved.match(pattern):
                raise PermissionError(f"Pfad durch Pattern blockiert: {pattern}")

        return resolved

    async def read_file(self, path: str) -> FileContent:
        """Liest eine Datei."""
        resolved = self._validate_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {path}")

        if not resolved.is_file():
            raise ValueError(f"Kein reguläre Datei: {path}")

        content = resolved.read_text(encoding="utf-8", errors="replace")

        return FileContent(
            path=str(resolved),
            content=content,
            size_bytes=resolved.stat().st_size,
            modified=datetime.fromtimestamp(resolved.stat().st_mtime),
        )

    async def write_file(self, path: str, content: str) -> WriteResult:
        """
        Schreibt eine Datei.
        Gibt WriteResult mit Preview zurück - Ausführung erst nach Bestätigung.
        """
        resolved = self._validate_path(path)

        is_new = not resolved.exists()
        old_content = None if is_new else resolved.read_text(encoding="utf-8", errors="replace")

        return WriteResult(
            path=str(resolved),
            is_new=is_new,
            old_content=old_content,
            new_content=content,
            diff=self._generate_diff(old_content, content, str(resolved)) if not is_new else None,
        )

    async def execute_write(self, path: str, content: str) -> bool:
        """Führt die Schreiboperation nach Bestätigung aus."""
        resolved = self._validate_path(path)

        # Backup erstellen
        if self.backup_enabled and resolved.exists():
            self._create_backup(resolved)

        # Verzeichnis erstellen falls nötig
        resolved.parent.mkdir(parents=True, exist_ok=True)

        # Datei schreiben
        resolved.write_text(content, encoding="utf-8")

        return True

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str
    ) -> EditResult:
        """
        Bearbeitet eine Datei durch String-Ersetzung.
        Gibt EditResult mit Diff zurück.
        """
        resolved = self._validate_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {path}")

        content = resolved.read_text(encoding="utf-8")

        if old_string not in content:
            raise ValueError(f"String nicht gefunden in {path}")

        # Prüfen ob eindeutig
        count = content.count(old_string)
        if count > 1:
            raise ValueError(
                f"String kommt {count}x vor in {path}. "
                "Bitte mehr Kontext angeben für eindeutige Ersetzung."
            )

        new_content = content.replace(old_string, new_string, 1)

        return EditResult(
            path=str(resolved),
            old_string=old_string,
            new_string=new_string,
            diff=self._generate_diff(content, new_content, str(resolved)),
            new_content=new_content,
        )

    def _generate_diff(
        self,
        old: Optional[str],
        new: str,
        filename: str
    ) -> str:
        """Generiert einen unified diff."""
        import difflib

        if old is None:
            return f"+++ {filename} (new file)\n" + "\n".join(f"+{line}" for line in new.splitlines())

        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
        return "".join(diff)

    def _create_backup(self, path: Path) -> None:
        """Erstellt ein Backup der Datei."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{path.stem}_{timestamp}{path.suffix}"
        backup_path = self.backup_dir / backup_name
        shutil.copy(path, backup_path)
```

---

## 3. API Design

### 3.1 Neue Endpunkte

```yaml
# Skill Management
GET    /api/skills                    # Liste aller Skills
GET    /api/skills/{id}               # Skill-Details
POST   /api/skills                    # Skill erstellen (YAML upload)
PUT    /api/skills/{id}               # Skill aktualisieren
DELETE /api/skills/{id}               # Skill löschen
POST   /api/skills/{id}/activate      # Skill für Session aktivieren
POST   /api/skills/{id}/deactivate    # Skill deaktivieren
POST   /api/skills/from-pdf           # PDF zu Skill (geführter Dialog)

# Handbuch
GET    /api/handbook/status           # Index-Status
POST   /api/handbook/index/build      # Index aufbauen
DELETE /api/handbook/index            # Index löschen
GET    /api/handbook/search           # Volltextsuche
GET    /api/handbook/services         # Liste aller Services
GET    /api/handbook/services/{id}    # Service-Details
GET    /api/handbook/page             # HTML-Seite laden (path als Query)

# Agent Chat (ersetzt /api/chat)
POST   /api/agent/chat                # Agent-Loop mit Tools
POST   /api/agent/confirm             # Bestätigung für Schreib-Op
POST   /api/agent/cancel              # Abbruch einer Operation
GET    /api/agent/mode                # Aktueller Modus
PUT    /api/agent/mode                # Modus ändern (read/write)

# File Operations
GET    /api/files/list                # Dateien auflisten
GET    /api/files/read                # Datei lesen
POST   /api/files/write               # Datei schreiben (Preview)
POST   /api/files/write/execute       # Schreiben ausführen
POST   /api/files/edit                # Datei editieren (Preview)
POST   /api/files/edit/execute        # Edit ausführen

# Explorer
GET    /api/explorer/tree             # Kombinierter Baum (Java, Python, Handbuch, Skills)
```

### 3.2 WebSocket für Agent-Events (Alternative zu SSE)

```python
# app/api/routes/agent_ws.py

@router.websocket("/api/agent/ws")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            # Nachricht vom Client empfangen
            data = await websocket.receive_json()

            if data["type"] == "chat":
                # Agent-Loop starten
                async for event in agent.process(
                    session_id=data["session_id"],
                    user_message=data["message"],
                    active_skills=data.get("active_skills", []),
                    mode=AgentMode(data.get("mode", "read_only")),
                ):
                    await websocket.send_json(event.to_dict())

            elif data["type"] == "confirm":
                # Bestätigung für Schreib-Operation
                await agent.confirm(data["operation_id"], data["confirmed"])

            elif data["type"] == "cancel":
                # Operation abbrechen
                await agent.cancel(data["operation_id"])

    except WebSocketDisconnect:
        pass
```

---

## 4. Frontend Design

### 4.1 Komponenten-Hierarchie

```
App
├── Header
│   ├── Logo
│   ├── SkillSelector (Dropdown mit Toggles)
│   ├── ModeSelector (Read-Only / Write)
│   └── SettingsButton
│
├── MainLayout (3-Panel)
│   ├── ExplorerPanel (links, resizable)
│   │   ├── TabBar (Java | Python | Handbuch | Skills)
│   │   ├── SearchInput
│   │   └── TreeView
│   │       ├── TreeNode (Folder)
│   │       └── TreeNode (File) → onClick: addToContext
│   │
│   ├── ChatPanel (mitte)
│   │   ├── MessageList
│   │   │   ├── Message (user)
│   │   │   ├── Message (assistant)
│   │   │   │   ├── MarkdownContent
│   │   │   │   ├── CodeBlock (mit Syntax Highlighting)
│   │   │   │   └── ToolCallDisplay
│   │   │   │       ├── ToolName + Arguments
│   │   │   │       ├── Result/Error
│   │   │   │       └── ConfirmationDialog (für Write-Ops)
│   │   │   │           ├── DiffView
│   │   │   │           └── [Anwenden] [Ablehnen]
│   │   │   └── Message (system)
│   │   │
│   │   └── InputArea
│   │       ├── Textarea (auto-resize)
│   │       ├── AttachmentBar (zeigt angehängte Dateien)
│   │       └── SendButton
│   │
│   └── ContextPanel (rechts, resizable)
│       ├── ActiveSkillsList
│       │   └── SkillChip (mit Toggle)
│       │
│       ├── LoadedFilesListe
│       │   └── FileChip (mit Remove-Button)
│       │
│       ├── TokenCounter
│       │   ├── ProgressBar
│       │   └── "12,345 / 32,000 Tokens"
│       │
│       └── SourcesUsed (nach Antwort)
│           └── SourceLink (klickbar → öffnet Datei)
│
└── Modals
    ├── SkillCreatorModal (PDF zu Skill Dialog)
    ├── SettingsModal
    └── FilePreviewModal
```

### 4.2 State Management

```typescript
// Frontend State (React/Vue/Svelte)

interface AppState {
  // Session
  sessionId: string;
  mode: 'read_only' | 'write_with_confirm';

  // Skills
  availableSkills: Skill[];
  activeSkillIds: string[];

  // Context
  loadedFiles: ContextFile[];
  tokenCount: { used: number; max: number };

  // Chat
  messages: Message[];
  isStreaming: boolean;
  pendingConfirmation: ConfirmationRequest | null;

  // Explorer
  activeExplorerTab: 'java' | 'python' | 'handbook' | 'skills';
  explorerTree: TreeNode;
  searchQuery: string;

  // UI
  explorerWidth: number;
  contextPanelWidth: number;
}

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  toolCalls?: ToolCall[];
  sourcesUsed?: SourceReference[];
  timestamp: Date;
}

interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, any>;
  status: 'running' | 'success' | 'error' | 'pending_confirmation' | 'cancelled';
  result?: any;
  error?: string;
  confirmationRequired?: boolean;
  diff?: string;
}

interface ConfirmationRequest {
  operationId: string;
  toolCall: ToolCall;
  preview: {
    type: 'diff' | 'new_file';
    content: string;
  };
}
```

---

## 5. Konfiguration

### 5.1 Erweiterte config.yaml

```yaml
# config.yaml - Erweitert für AI-Assist Evolution

llm:
  base_url: "http://internal-llm-gateway/v1"
  api_key: "none"
  default_model: "gptoss120b"
  timeout_seconds: 120
  max_tokens: 4096
  temperature: 0.2
  # NEU: Tool-Calling Unterstützung
  supports_tools: true
  tool_choice: "auto"  # auto | none | required

# ... bestehende Konfiguration ...

# NEU: Handbuch-Konfiguration
handbook:
  enabled: true
  path: "//server/share/handbuch"  # Netzlaufwerk
  index_on_start: true
  watch_for_changes: false  # Optional: Änderungen überwachen
  exclude_patterns:
    - "**/archiv/**"
    - "**/backup/**"

# NEU: Skill-System
skills:
  directory: "./skills"
  index_path: "./index/skills_index.db"
  auto_activation:
    enabled: true
    confidence_threshold: 0.8

# NEU: File Operations
file_operations:
  enabled: true
  default_mode: "read_only"  # read_only | write_with_confirm | autonomous
  allowed_paths:
    - "/home/user/projects"
    - "//server/share/development"
  allowed_extensions:
    - ".java"
    - ".py"
    - ".xml"
    - ".yaml"
    - ".yml"
    - ".json"
    - ".md"
    - ".properties"
  denied_patterns:
    - "**/node_modules/**"
    - "**/.git/**"
    - "**/target/**"
    - "**/__pycache__/**"
  backup:
    enabled: true
    directory: "./backups"
    max_age_days: 7

# NEU: Agent-Konfiguration
agent:
  max_iterations: 10
  max_tool_calls_per_iteration: 5
  context_window_tokens: 32000
  reserved_response_tokens: 4096
```

---

## 6. Datenbank-Schema

### 6.1 Neue Tabellen

```sql
-- skills_index.db

-- Skill-Definitionen
CREATE TABLE skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL,  -- knowledge | prompt | tool | hybrid
    activation_mode TEXT NOT NULL,  -- always | on-demand | auto
    trigger_words_json TEXT,
    system_prompt TEXT,
    file_path TEXT,
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Skill-Wissensbasen (FTS5)
CREATE VIRTUAL TABLE skill_knowledge_fts USING fts5(
    skill_id UNINDEXED,
    chunk_id UNINDEXED,
    source_path UNINDEXED,
    source_type UNINDEXED,
    content,
    tokenize='porter unicode61'
);

-- handbook_index.db

-- Handbuch-Seiten (FTS5)
CREATE VIRTUAL TABLE handbook_fts USING fts5(
    file_path UNINDEXED,
    service_name,
    tab_name,
    title,
    headings,
    content,
    tables_text,
    tokenize='porter unicode61'
);

-- Service-Strukturen
CREATE TABLE handbook_services (
    service_id TEXT PRIMARY KEY,
    service_name TEXT NOT NULL,
    description TEXT,
    tabs_json TEXT,
    input_fields_json TEXT,
    output_fields_json TEXT,
    call_variants_json TEXT
);

-- Feld-Definitionen
CREATE TABLE handbook_fields (
    field_id TEXT PRIMARY KEY,
    field_name TEXT NOT NULL,
    field_type TEXT,
    description TEXT,
    validation_rules TEXT,
    used_in_services_json TEXT,
    source_file TEXT
);

-- sessions.db (optional, für Persistenz)

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    active_skills_json TEXT,
    mode TEXT DEFAULT 'read_only'
);

CREATE TABLE session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls_json TEXT,
    sources_used_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

---

## 7. Sequenzdiagramme

### 7.1 Agent Chat mit Tool-Calls

```
┌─────┐          ┌─────────┐          ┌───────┐          ┌─────────┐          ┌─────┐
│User │          │Frontend │          │Backend│          │Agent    │          │ LLM │
└──┬──┘          └────┬────┘          └───┬───┘          └────┬────┘          └──┬──┘
   │                  │                   │                   │                  │
   │ Frage eingeben   │                   │                   │                  │
   │─────────────────>│                   │                   │                  │
   │                  │                   │                   │                  │
   │                  │ POST /api/agent/chat                  │                  │
   │                  │──────────────────>│                   │                  │
   │                  │                   │                   │                  │
   │                  │                   │ process()         │                  │
   │                  │                   │──────────────────>│                  │
   │                  │                   │                   │                  │
   │                  │                   │                   │ chat_with_tools()│
   │                  │                   │                   │─────────────────>│
   │                  │                   │                   │                  │
   │                  │                   │                   │<── tool_call ────│
   │                  │                   │                   │  search_code()   │
   │                  │                   │                   │                  │
   │                  │<── SSE: tool_start ────────────────────                  │
   │                  │                   │                   │                  │
   │                  │                   │                   │ execute tool     │
   │                  │                   │                   │────────┐         │
   │                  │                   │                   │<───────┘         │
   │                  │                   │                   │                  │
   │                  │<── SSE: tool_result ───────────────────                  │
   │                  │                   │                   │                  │
   │                  │                   │                   │ continue with    │
   │                  │                   │                   │ tool result      │
   │                  │                   │                   │─────────────────>│
   │                  │                   │                   │                  │
   │                  │                   │                   │<── response ─────│
   │                  │                   │                   │                  │
   │                  │<── SSE: tokens (streaming) ────────────                  │
   │                  │                   │                   │                  │
   │                  │<── SSE: done ──────────────────────────                  │
   │                  │                   │                   │                  │
   │<─ Antwort zeigen │                   │                   │                  │
   │                  │                   │                   │                  │
```

### 7.2 Datei-Schreiben mit Bestätigung

```
┌─────┐          ┌─────────┐          ┌───────┐          ┌─────────┐
│User │          │Frontend │          │Backend│          │FileManager│
└──┬──┘          └────┬────┘          └───┬───┘          └────┬─────┘
   │                  │                   │                   │
   │                  │<── SSE: confirm_required ─────────────│
   │                  │    {tool: write_file, diff: "..."}    │
   │                  │                   │                   │
   │<─ Diff anzeigen  │                   │                   │
   │   [Ja] [Nein]    │                   │                   │
   │                  │                   │                   │
   │ Klick [Ja]       │                   │                   │
   │─────────────────>│                   │                   │
   │                  │                   │                   │
   │                  │ POST /api/agent/confirm               │
   │                  │ {operation_id, confirmed: true}       │
   │                  │──────────────────>│                   │
   │                  │                   │                   │
   │                  │                   │ execute_write()   │
   │                  │                   │──────────────────>│
   │                  │                   │                   │
   │                  │                   │<── success ───────│
   │                  │                   │                   │
   │                  │<── SSE: write_success ────────────────│
   │                  │                   │                   │
   │<─ "Datei geschrieben" ───────────────│                   │
   │                  │                   │                   │
```

---

## 8. Implementierungsreihenfolge

### Phase 1: Handbuch-Integration (1-2 Wochen)
1. `HandbookIndexer` implementieren
2. HTML-Parser mit BeautifulSoup
3. API-Endpunkte für Handbuch
4. Frontend: Handbuch-Tab im Explorer

### Phase 2: Skill-System (2-3 Wochen)
1. Skill-Definition Schema (YAML)
2. `SkillManager` implementieren
3. PDF-Chunking und Indexierung
4. API-Endpunkte für Skills
5. Frontend: Skill-Selector und Manager-UI
6. PDF-zu-Skill Wizard (geführter Dialog)

### Phase 3: Intelligente Suche / Agent (2-3 Wochen)
1. `ToolRegistry` und Tool-Definitionen
2. `AgentOrchestrator` mit Loop
3. LLM-Client erweitern für Tool-Calling
4. Kontext-Assembler
5. API: `/api/agent/chat` mit SSE/WebSocket
6. Frontend: Tool-Call-Anzeige

### Phase 4: Datei-Operationen (1-2 Wochen)
1. `FileManager` mit Permission-System
2. Diff-Generierung
3. Backup-System
4. Frontend: Bestätigungs-Dialog mit Diff-View

### Phase 5: Frontend-Redesign (2-3 Wochen)
1. 3-Panel-Layout implementieren
2. Resizable Panels
3. Explorer mit Tabs
4. Context-Panel mit Token-Counter
5. Verbessertes Message-Rendering

---

## 9. Nächste Schritte

1. **Review** dieses Architecture-Dokuments
2. **Feedback** zu Design-Entscheidungen
3. **/sc:workflow** für detaillierten Implementierungsplan
4. **Start Phase 1**: Handbuch-Integration

---

*Erstellt durch /sc:design am 2026-03-05*
