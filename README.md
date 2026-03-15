# AI-Assist

Enterprise AI-Assistent mit Claude-Code-ähnlicher Architektur. Spezialisiert auf Java/WebSphere Liberty Entwicklung mit Integration in Unternehmenstools.

## Highlights

- **MCP-Enhancement Pipeline** - Kontext-Sammlung vor Task-Verarbeitung mit User-Confirmation
- **Task-Decomposition** - Komplexe Anfragen in parallele Sub-Tasks zerlegen
- **40+ Agent Tools** - Code, Datenbanken, Confluence, Jira, Jenkins, GitHub, WLP, Maven
- **Container Sandbox** - Sichere Code-Ausführung in Docker/Podman/WSL
- **Multi-Chat** - Parallele Sessions mit Persistenz

## Quick Start

```bash
git clone https://github.com/IT-Networks/AI-Assist.git
cd AI-Assist
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.yaml.example config.yaml  # Anpassen
python main.py
```

Browser: **http://localhost:8000**

## Architektur

```
User Query
    ↓
┌─────────────────────────────────────────────────────┐
│  MCP-ENHANCEMENT PIPELINE                           │
│  ├─ Research MCP (Wiki, Web, Code)                  │
│  ├─ Sequential Thinking MCP (komplexe Probleme)     │
│  └─ User Confirmation UI                            │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  TASK-DECOMPOSITION                                 │
│  ├─ Task-Planner (Dependency Graph)                 │
│  ├─ Parallel Task-Execution                         │
│  └─ Result Synthesis                                │
└─────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────┐
│  AGENT-LOOP                                         │
│  ├─ Tool-Calling (40+ Tools)                        │
│  ├─ Sub-Agents (parallel)                           │
│  └─ Context Compaction                              │
└─────────────────────────────────────────────────────┘
    ↓
Response
```

## Features

### Agent-System
| Feature | Beschreibung |
|---------|-------------|
| Tool-Calling | Automatische Tool-Auswahl und -Ausführung |
| 3 Modi | `read_only`, `write_with_confirm`, `autonomous` |
| Token-Budget | Verhindert Kontext-Overflow |
| Context Compaction | Automatische Zusammenfassung langer Chats |

### MCP Integration
| Capability | Beschreibung |
|------------|-------------|
| Sequential Thinking | Schritt-für-Schritt Reasoning bei komplexen Problemen |
| Research | Multi-Source Recherche (Web, Code, Docs, Wiki) |
| Brainstorm/Design | Ideen-Exploration und Architektur-Design |

### Enterprise Integrations
| System | Features |
|--------|----------|
| **Confluence** | Wiki-Suche, Seiten-Abruf, Space-Browser |
| **Jira** | Issue-Suche, Kommentare, Status-Updates |
| **Jenkins** | Build-Trigger, Job-Status, Logs |
| **GitHub Enterprise** | Repos, PRs, Code-Suche, Diff-Analyse |
| **ServiceNow** | Service Portal Integration |
| **DB2** | Read-only Queries (z/OS, LUW, iSeries) |

### WLP & Maven
| Tool | Beschreibung |
|------|-------------|
| WLP Start/Stop | Server-Management mit Status-Tracking |
| Log-Analyse | Error-Extraction mit Pattern-Matching |
| Feature-Validation | server.xml Analyse |
| Maven Build | Dependency-Analyse, pom.xml Parsing |
| Deploy-Workflow | Build → Deploy Pipeline |

### Container Sandbox
| Backend | Beschreibung |
|---------|-------------|
| Docker | Native Docker-Support |
| Podman | Rootless Container |
| WSL | Windows Subsystem for Linux (Default) |

Sichere Ausführung von: Python, Java, SQL, Shell-Commands

## Konfiguration

Minimale `config.yaml`:

```yaml
llm:
  base_url: "http://your-llm-server/v1"
  api_key: "your-api-key"
  default_model: "mistral-678b"
  tool_model: "gptoss120b"

# Task-Decomposition
task_agents:
  enabled: true
  min_tasks_for_decomposition: 2

# MCP Enhancement
mcp:
  thinking_enabled: true
  research_enabled: true
```

Vollständige Optionen: siehe `config.yaml.example`

## Agent-Tools (Auswahl)

### Code & Dateien
- `search_code` - ripgrep-basierte Code-Suche
- `read_file` / `write_file` / `edit_file` - Datei-Operationen
- `glob` / `grep` - Pattern-Matching
- `compile_validate` - Syntax-Check (Python, Java, SQL, XML)

### Enterprise
- `search_confluence` / `get_confluence_page`
- `search_jira` / `get_jira_issue` / `add_jira_comment`
- `trigger_jenkins_build` / `get_jenkins_build_status`
- `github_search_code` / `github_pr_diff`
- `query_database` - DB2 Read-only

### WLP & Maven
- `wlp_start` / `wlp_stop` / `wlp_status`
- `wlp_analyze_logs` / `wlp_parse_server_xml`
- `maven_build` / `maven_dependency_tree`

### Container
- `run_in_container` - Sichere Code-Ausführung
- `shell_execute` - Shell-Commands (mit Confirmation)

### Web & HTTP
- `search_web` - DuckDuckGo Suche
- `fetch_webpage` - URL abrufen
- `http_request` - REST/SOAP Calls

## API

| Endpunkt | Beschreibung |
|----------|-------------|
| `POST /api/agent/chat` | Agent-Chat (SSE Streaming) |
| `GET /api/agent/tools` | Verfügbare Tools |
| `GET /api/enhancement/{session}` | Enhancement-Details |
| `POST /api/enhancement/{session}/confirm` | Enhancement bestätigen |
| `GET /api/settings` | Konfiguration |
| `GET /api/health` | System-Status |

Vollständige API-Docs: **http://localhost:8000/docs**

## Tests

```bash
# Alle Tests
python -m pytest tests/ -v

# Mit Coverage
python -m pytest tests/ --cov=app --cov-report=html

# 442 Tests
```

## Projektstruktur

```
AI-Assist/
├── app/
│   ├── agent/           # Agent-System
│   │   ├── orchestrator.py      # Haupt-Orchestration
│   │   ├── prompt_enhancer.py   # MCP-Enhancement Pipeline
│   │   ├── task_*.py            # Task-Decomposition System
│   │   ├── tools.py             # Tool-Definitionen
│   │   └── constants.py         # Control-Marker
│   ├── mcp/             # MCP Capabilities
│   │   ├── thinking_engine.py
│   │   ├── sequential_thinking.py
│   │   └── capabilities/
│   ├── api/routes/      # FastAPI Endpoints
│   └── services/        # Business Logic
├── static/              # Frontend (Vanilla JS)
├── tests/               # pytest Tests
├── skills/              # YAML Skill-Definitionen
└── docs/                # Design-Dokumente
```

## Roadmap

Siehe [ROADMAP.md](ROADMAP.md) für Changelog und geplante Features.

## Lizenz

MIT
