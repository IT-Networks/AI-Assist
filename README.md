# AI-Assist

Enterprise AI-Assistent mit Claude-Code-ähnlicher Architektur. Spezialisiert auf Java/WebSphere Liberty Entwicklung mit Integration in Unternehmenstools.

## Highlights

- **Workspace Panel** - Split-View mit Code-Diffs, SQL-Ergebnissen und Research-Tabs
- **User Dashboard** - KPI-Charts, Tool-Usage, Activity-Heatmaps, Token-Tracking
- **Token/Credit Tracking** - LLM-Kosten-Tracking mit Budget-Limits, Alerts und Export
- **Self-Healing Code** - Automatische Fehlererkennung und Fix-Vorschläge mit Pattern-Matching
- **Parallel Agents** - Multi-Agent Task-Execution in isolierten Git Worktrees mit Auto-Merge
- **PR Review** - AI-gesteuerte Code Reviews mit Copy-Friendly Fixes (kein Auto-Commit)
- **Arena Mode** - Model-Vergleich mit Blind-Voting und ELO-Rating
- **Error Pattern Learning** - Automatisches Lernen von Fehler-Lösungen mit Similarity-Matching
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

## ⚠️ WICHTIG: Configuration Changes erfordern Server Restart

**Kritisch:** Änderungen in `config.yaml` werden nur wirksam, wenn der Server **neu gestartet** wird!

Dies betrifft insbesondere:
- `docker_sandbox.enabled` - Docker/Podman-Sandbox Aktivierung
- `script_execution.use_container` - Python Script Ausführung in Container
- Alle Security-relevanten Settings

**Symptom:** Fehler wie `"can only run python in sandbox"` treten auf, obwohl `use_container: false` in der Config eingestellt ist.

**Lösung:**
```bash
# Server stoppen (Ctrl+C)
# Dann neu starten:
python main.py
```

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

### Workspace Panel
| Feature | Beschreibung |
|---------|-------------|
| **Code Split-View** | Side-by-Side Diffs mit diff2html, Syntax-Highlighting |
| **SQL Split-View** | Query-Ergebnisse mit Sorting, Pagination, Export |
| **Tabbed Interface** | Wechsel zwischen Code/SQL/Research Views |
| **Live Updates** | SSE-Streaming für Echtzeit-Änderungen |

### User Dashboard
| Feature | Beschreibung |
|---------|-------------|
| KPI-Cards | Requests, Response-Time, Success-Rate mit Trends |
| Tool-Usage Chart | Top 10 Tools mit Success-Rate |
| Activity Heatmap | 7-Tage Aktivitäts-Übersicht |
| Token-Ring | Input/Output Token-Verteilung |
| Recent Errors | Fehler-Liste mit Pattern-Links |

### Error Pattern Learning
| Feature | Beschreibung |
|---------|-------------|
| Auto-Learning | Fehler-Lösungen werden automatisch gespeichert |
| Similarity-Matching | Jaccard-basierte Keyword-Ähnlichkeit |
| Confidence Score | Dynamische Bewertung mit Time-Decay |
| User Feedback | Accept/Reject/Rating für Patterns |
| Pattern-Vorschläge | Automatische Lösungs-Empfehlungen |
| Export/Import | JSON-basierter Pattern-Austausch |

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

### Agent & Chat
| Endpunkt | Beschreibung |
|----------|-------------|
| `POST /api/agent/chat` | Agent-Chat (SSE Streaming) |
| `GET /api/agent/tools` | Verfügbare Tools |
| `GET /api/enhancement/{session}` | Enhancement-Details |
| `POST /api/enhancement/{session}/confirm` | Enhancement bestätigen |

### Analytics & Dashboard
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/analytics/dashboard` | Dashboard-Metriken (KPIs, Charts, Heatmaps) |
| `GET /api/analytics/summary` | Analytics-Zusammenfassung |

### Error Patterns
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/patterns` | Pattern-Liste (Filter: minConfidence, errorType) |
| `GET /api/patterns/{id}` | Einzelnes Pattern |
| `POST /api/patterns/suggest` | Pattern-Vorschlag für Fehler |
| `POST /api/patterns/learn` | Neues Pattern lernen |
| `POST /api/patterns/{id}/feedback` | Feedback aufzeichnen |
| `DELETE /api/patterns/{id}` | Pattern löschen |
| `GET /api/patterns/export/json` | Alle Patterns exportieren |

### Token Tracking
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/tokens/usage` | Nutzungs-Summary (day, week, month) |
| `GET /api/tokens/breakdown` | Breakdown nach Model/Type/Session |
| `GET /api/tokens/recent` | Letzte Token-Records |
| `GET /api/tokens/stats` | Statistiken und Trends |
| `GET /api/tokens/budget` | Budget-Konfiguration |
| `PUT /api/tokens/budget` | Budget setzen |
| `GET /api/tokens/alerts` | Budget-Warnungen |
| `GET /api/tokens/export` | Export (JSON/CSV) |

### Self-Healing Code
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/healing/config` | Self-Healing Konfiguration |
| `PUT /api/healing/config` | Konfiguration setzen |
| `GET /api/healing/attempts` | Healing-Versuche Liste |
| `GET /api/healing/attempts/pending` | Ausstehende Fixes |
| `POST /api/healing/analyze` | Fehler analysieren |
| `POST /api/healing/apply/{id}` | Fix anwenden |
| `POST /api/healing/dismiss/{id}` | Fix ablehnen |
| `GET /api/healing/stats` | Healing-Statistiken |

### Parallel Agents
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/agents/config` | Parallel Agents Konfiguration |
| `PUT /api/agents/config` | Konfiguration setzen |
| `GET /api/agents/tasks` | Task-Liste (Filter: status, limit) |
| `POST /api/agents/tasks` | Neuen Task erstellen |
| `GET /api/agents/tasks/{id}` | Task-Details |
| `DELETE /api/agents/tasks/{id}` | Task löschen/abbrechen |
| `POST /api/agents/tasks/{id}/start` | Task manuell starten |
| `POST /api/agents/tasks/{id}/progress` | Fortschritt aktualisieren |
| `POST /api/agents/tasks/{id}/complete` | Task abschließen |
| `GET /api/agents/pool` | Agent-Pool Status |
| `POST /api/agents/merge/{id}` | Task-Ergebnisse mergen |
| `GET /api/agents/conflicts/{id}` | Merge-Konflikte abrufen |
| `POST /api/agents/conflicts/{id}/resolve` | Konflikt auflösen |
| `GET /api/agents/stats` | Statistiken |

### PR Review (Copy-Friendly)
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/reviews/config` | Review-Konfiguration |
| `PUT /api/reviews/config` | Konfiguration setzen |
| `POST /api/reviews/trigger` | Review manuell starten |
| `GET /api/reviews/list` | Reviews mit Filter |
| `GET /api/reviews/history` | Letzte Reviews |
| `GET /api/reviews/{id}` | Review-Details |
| `GET /api/reviews/{id}/fixes` | Copyable Fixes abrufen |
| `GET /api/reviews/{id}/fixes/{cid}/patch` | Fix als Git-Patch |
| `POST /api/reviews/{id}/fixes/{cid}/copied` | Fix als kopiert markieren |
| `POST /api/reviews/{id}/comments/{cid}/dismiss` | Kommentar verwerfen |
| `GET /api/reviews/rules` | Custom Rules abrufen |
| `POST /api/reviews/rules` | Custom Rule erstellen |
| `PUT /api/reviews/rules/{id}` | Rule aktualisieren |
| `DELETE /api/reviews/rules/{id}` | Rule löschen |
| `GET /api/reviews/stats` | Statistiken |

### Arena Mode (Model-Vergleich)
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/arena/config` | Arena-Konfiguration |
| `PUT /api/arena/config` | Aktivieren/Modelle setzen |
| `GET /api/arena/enabled` | Schnelle Enable-Prüfung |
| `POST /api/arena/start` | Neuen Match starten |
| `GET /api/arena/match/{id}` | Match-Details |
| `POST /api/arena/match/{id}/response` | Model-Response setzen |
| `POST /api/arena/match/{id}/vote` | Abstimmen (A/B/Tie) |
| `POST /api/arena/match/{id}/skip` | Match überspringen |
| `GET /api/arena/session/{sid}/pending` | Offene Matches für Session |
| `GET /api/arena/history` | Match-Historie |
| `GET /api/arena/stats` | Gesamtstatistiken |
| `GET /api/arena/leaderboard` | ELO-Rangliste |
| `GET /api/arena/models/{m}/stats` | Model-Statistiken |

### System
| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/settings` | Konfiguration |
| `GET /api/health` | System-Status |

Vollständige API-Docs: **http://localhost:8000/docs**

## Performance Optimierungen

### Backend (Python)
| Optimierung | Datei | Verbesserung |
|-------------|-------|--------------|
| Pre-compiled Regex | `pattern_learner.py` | 10-50x schnellere Pattern-Erkennung |
| orjson Serialization | `json_utils.py` | 3-10x schnellere JSON-Verarbeitung |
| SQL Column Constants | `arena_mode.py`, `token_tracker.py`, etc. | Vermeidet `SELECT *` |
| Connection Pooling | `llm_client.py` | Weniger TCP-Overhead |

### Frontend (JavaScript)
| Optimierung | Datei | Beschreibung |
|-------------|-------|--------------|
| Debug Logger | `app.js` | `DEBUG=false` deaktiviert alle `log.info/warn` |
| Timing Constants | `app.js` | Zentrale `TIMING.*` Konstanten |
| Duplicate Removal | `app.js` | 3 doppelte Funktionen entfernt |

```javascript
// Debug-Modus aktivieren (app.js Zeile 7)
const DEBUG = true;  // false = silent mode
```

## Tests

```bash
# Alle Tests
python -m pytest tests/ -v

# Mit Coverage
python -m pytest tests/ --cov=app --cov-report=html

# 808 Tests, 33% Coverage
```

### Test-Module
| Modul | Tests | Beschreibung |
|-------|-------|-------------|
| `test_pattern_learner.py` | 54 | ErrorPattern, Similarity, Persistence |
| `test_patterns_api.py` | 32 | Pattern REST API |
| `test_dashboard_api.py` | 26 | Dashboard-Metriken |
| `test_parallel_agents.py` | 42 | Parallel Agents, Git Worktrees |
| `test_pr_review.py` | 51 | PR Review, Copy-Friendly Fixes |
| `test_arena_mode.py` | 48 | Arena Mode, ELO, Blind Voting |
| `test_workspace_events.py` | 14 | Code/SQL Events |
| `test_analytics*.py` | 200+ | Analytics-System |
| `test_*.py` | 240+ | Weitere Module |

## Projektstruktur

```
AI-Assist/
├── app/
│   ├── agent/           # Agent-System
│   │   ├── orchestrator.py      # Haupt-Orchestration + Workspace Events
│   │   ├── prompt_enhancer.py   # MCP-Enhancement Pipeline
│   │   ├── task_*.py            # Task-Decomposition System
│   │   ├── tools.py             # Tool-Definitionen
│   │   └── constants.py         # Control-Marker
│   ├── mcp/             # MCP Capabilities
│   │   ├── thinking_engine.py
│   │   ├── sequential_thinking.py
│   │   └── capabilities/
│   ├── api/routes/      # FastAPI Endpoints
│   │   ├── analytics.py         # Dashboard API
│   │   ├── patterns.py          # Error Pattern API
│   │   └── ...
│   └── services/        # Business Logic
│       ├── pattern_learner.py   # Error Pattern Learning
│       ├── analytics_logger.py  # Metrics & Tracking
│       └── ...
├── static/              # Frontend (Vanilla JS)
│   ├── app.js           # Main App + Workspace Logic
│   ├── index.html       # UI mit Dashboard Modal
│   └── style.css        # Styling inkl. Charts
├── tests/               # pytest Tests (568 Tests)
├── skills/              # YAML Skill-Definitionen
└── docs/                # Design-Dokumente
    └── design/          # Feature-Designs (WORKSPACE_FEATURES.md)
```

## Roadmap

Siehe [ROADMAP.md](ROADMAP.md) für Changelog und geplante Features.

## Lizenz

MIT
