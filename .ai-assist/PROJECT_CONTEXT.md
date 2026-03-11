# AI-Assist - Projekt-Kontext

> Automatisch geladen bei jeder Chat-Session in diesem Projekt.
> Manuell gepflegt - beschreibt Architektur, Konventionen und kritisches Wissen.

## Projekt-Übersicht

**Name:** AI-Assist
**Typ:** FastAPI-basierter AI-Agent mit Tool-Orchestrierung
**Sprache:** Python 3.11
**Framework:** FastAPI + Uvicorn

## Architektur

### Pattern: Clean Architecture + Domain Services

```
app/
├── agent/           # Agent-Orchestrierung
│   ├── orchestrator.py   # Haupt-Agent-Loop (process-Methode)
│   ├── tools.py          # ToolRegistry + Tool-Definitionen
│   ├── shell_tools.py    # Container-First Shell-Ausführung
│   └── entity_tracker.py # Cross-Domain Entity-Tracking
├── api/routes/      # FastAPI Router
├── services/        # Domain Services
│   ├── memory_store.py      # SQLite + FTS5 Memory (3-Schichten)
│   ├── context_manager.py   # Projekt-Kontext-System
│   ├── transcript_logger.py # Session-Logging (JSONL)
│   └── llm_client.py        # LLM API Client
├── mcp/             # MCP Server Integration
│   └── tool_bridge.py  # MCPToolBridge für externe Tools
└── core/            # Konfiguration + Utilities
    ├── config.py
    └── token_budget.py
```

### Kern-Komponenten

| Komponente | Pfad | Beschreibung |
|------------|------|--------------|
| Orchestrator | `app/agent/orchestrator.py` | Agent-Loop mit Tool-Calls, Streaming, Compaction |
| ToolRegistry | `app/agent/tools.py` | Zentrale Tool-Verwaltung, 60+ Tools |
| MCPBridge | `app/mcp/tool_bridge.py` | MCP-Server-Integration |
| MemoryStore | `app/services/memory_store.py` | 3-Schichten Memory (Global→Project→Session) |
| ContextManager | `app/services/context_manager.py` | Lädt PROJECT_CONTEXT.md + MEMORY.md |

### Schlüssel-Patterns

- **Container-First Shell:** Befehle erst im Container testen, lokale Ausführung nur mit Bestätigung
- **Tool-Results:** Immer als `ToolResult` Dataclass zurückgeben
- **Streaming:** Via `AsyncGenerator[AgentEvent]`
- **Token-Budget:** Automatische Compaction bei >80% Auslastung

## Konventionen

### Code-Style
- Type Hints für alle Funktionen
- Async für I/O-Operationen
- Dataclasses für DTOs
- Deutsche Kommentare, englische Variablennamen

### Testing
- pytest mit >80% Coverage Ziel
- Tests in `tests/` Verzeichnis
- Naming: `test_<module>.py`

### Git
- Conventional Commits (deutsch)
- Feature-Branches + PR

## Kritisches Wissen

- **MCP-Tools** sind über `MCPToolBridge` integriert (app/mcp/tool_bridge.py)
- **Shell-Tools** blockieren Git-Befehle - nutze die separaten `git_*` Tools
- **MemoryStore** verwendet SQLite mit FTS5 für Volltextsuche
- **Container-First** für Shell-Ausführung wegen Sicherheit (kein sudo, kein rm -rf /)
- **Token-Budget** wird automatisch verwaltet, Compaction via ConversationSummarizer

## Verbotene Aktionen

- Keine direkten Git-Befehle in Shell-Tools (Commits würden im Container erzeugt)
- Keine sudo/root-Operationen
- Keine hardcodierten Credentials
- Keine rm -rf auf Root-Verzeichnisse

## API-Endpunkte (Haupt)

| Endpunkt | Beschreibung |
|----------|--------------|
| `POST /api/agent/chat` | Haupt-Chat-Endpunkt mit Streaming |
| `GET /api/agent/tools` | Liste aller verfügbaren Tools |
| `GET /docs` | OpenAPI Swagger UI |

## Abhängigkeiten (kritisch)

- `fastapi`, `uvicorn` - Web-Framework
- `httpx` - Async HTTP Client
- `pydantic` - Datenvalidierung
- `lxml` - XML Parsing (für POM, SOAP)
- `aiofiles` - Async File I/O
