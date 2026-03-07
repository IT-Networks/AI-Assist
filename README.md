# AI Code Assistant

Ein lokaler AI-Assistent für Entwickler mit Claude-Code-ähnlicher Architektur. Features: Agent mit Tool-Calling, Skill-System, Handbuch-Integration und Datei-Operationen.

## Features

### Agent-System
- **Tool-Calling** – Agent kann automatisch Tools aufrufen um Informationen zu sammeln
- **3 Modi** – `read_only`, `write_with_confirm`, `autonomous`
- **8+ Tools** – Code-Suche, Handbuch, Skills, Datei-Operationen, Datenquellen
- **Bestätigungs-Workflow** – Diff-Preview vor Schreib-Operationen
- **Token-Budget** – Verhindert unkontrolliertes Kontextwachstum
- **Kontext-Kompression** – Automatische Zusammenfassung langer Konversationen

### Skill-System
- **YAML-basierte Skills** – Prompts + Wissensquellen kombinieren
- **PDF-zu-Skill** – PDFs als Wissensbasis einbinden
- **Aktivierung pro Session** – Skills on-demand aktivieren
- **Volltextsuche** – SQLite FTS5 für Skill-Inhalte
- **Automatische Aktivierung** – Trigger-Wörter für auto-Aktivierung

### Handbuch-Integration
- **HTML-Parsing** – Services, Tabs, Felder aus HTML-Handbuch
- **Netzlaufwerk-Support** – Pfade wie `//server/share/handbuch`
- **Service-Suche** – Volltextsuche über alle Dokumentation

### Code-Repositories
- **Java** – Dateibaum, Klassen-Analyse, POM-Abhängigkeiten, Index-Suche
- **Python** – Symbol-Suche, Validierung (flake8/ruff/mypy), Tests (pytest)

### Settings-UI
- **Frontend-Konfiguration** – Alle Settings über das UI ändern
- **Live-Anwendung** – Änderungen sofort aktiv (ohne Neustart)
- **Persistenz** – In config.yaml speichern mit Backup
- **Modell-Verwaltung** – LLM-Modelle hinzufügen/entfernen, pro Tool konfigurierbar

### Weitere Features
- **WLP Log Analyse** – Server-Logs hochladen, Fehler extrahieren
- **PDF Support** – PDFs als Kontext nutzen
- **Confluence/Jira** – Seiten per CQL-Suche finden und laden
- **DB2-Datenbank** – Read-only Datenbankabfragen (optional)
- **Externe Datenquellen** – Generische HTTP-APIs einbinden
- **Streaming** – Echtzeit-Token-Ausgabe im Browser
- **Health-Check** – `/api/health` für System-Monitoring

## Installation

```bash
git clone https://github.com/IT-Networks/AI-Assist.git
cd AI-Assist
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Konfiguration

Passe `config.yaml` an:

```yaml
# LLM-Verbindung (OpenAI-kompatibler Endpunkt)
llm:
  base_url: "http://dein-llm-server/v1"
  api_key: "dein-api-key"
  default_model: "mistral-678b"       # Haupt-Modell für Antworten
  tool_model: "gptoss120b"            # Schnelles Modell für Tool-Calls
  analysis_model: ""                  # Großes Modell für finale Analyse (leer = default_model)

# Code-Repositories (optional)
java:
  repo_path: "/pfad/zu/java-projekt"

python:
  repo_path: "/pfad/zu/python-projekt"

# Handbuch (optional)
handbook:
  enabled: true
  path: "//server/share/handbuch"

# Datei-Operationen (optional, Vorsicht!)
file_operations:
  enabled: true
  default_mode: "write_with_confirm"
  allowed_paths:
    - "/pfad/zu/erlaubtem/ordner"
```

Alternativ über `.env`:
```bash
cp .env.example .env
# Werte in .env eintragen
```

## Starten

```bash
python main.py
# oder:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Browser öffnen: **http://localhost:8000**

## Modelle & Prompting-Hinweise

Das System unterstützt mehrere Modelle, die über die Settings-UI oder `config.yaml` verwaltet werden. Modelle können global gesetzt oder **pro Tool individuell konfiguriert** werden.

### Konfigurierte Modelle

| Modell-ID | Anzeigename | Empfohlene Verwendung |
|-----------|-------------|----------------------|
| `mistral-678b` | Mistral 678B | Hauptverarbeitung, komplexe Analysen |
| `gptoss120b` | GPT OSS 120B | Tool-Calls, strukturierte Ausgaben |
| `qwen-7b` | Qwen 7B | Schnelle Tool-Calls, einfache Suchen |
| `qwen-428b` | Qwen 428B | Komplexe Tool-Calls, Zwischen-Analysen |

### Prompting-Unterschiede je Modell

#### Mistral Instruct 678B (Hauptverarbeitung)
- Reagiert sehr gut auf **strukturierte System-Prompts** mit klaren Rollenangaben
- Unterstützt Tool-Calling zuverlässig auch über mehrere Runden
- Versteht deutschsprachige Prompts problemlos
- System-Prompts dürfen ausführlich sein – das Modell nutzt den Kontext effektiv
- Empfehlung: Skill-System-Prompts mit Abschnitten und Aufzählungen strukturieren

#### GPT OSS 120B (Tool-Calls)
- Sehr zuverlässig bei Tool-Selektion und Parameter-Extraktion
- Verarbeitet komplexe Tool-Schemas mit vielen Parametern sicher
- Ideal für strukturierte Ausgaben (JSON, XML) und Multi-Tool-Workflows

#### Qwen 7B (schnelle Tool-Calls)
- **Kürzere, direktere Prompts** sind effizienter – System-Prompt unter ~500 Token halten
- Tool-Definitionen vereinfachen: kurze `description`, maximal 3–4 Tools gleichzeitig
- Weniger zuverlässig bei komplexen Parametern oder verschachtelten Tool-Calls
- Ideal für: einfache Suchen, schnelle Code-Lookups, eindeutige Abfragen
- Bei zu langem Kontext kann das Modell die Tool-Auswahl "vergessen"

#### Qwen 480B (komplexe Tool-Calls)
- Ähnlich leistungsfähig wie große Frontier-Modelle
- Verarbeitet längere Prompts und komplexere Tool-Schemas zuverlässig
- Gute Wahl wenn Qwen 7B zu unzuverlässig aber GPT OSS zu langsam ist

### Modell-Aufteilung in config.yaml

```yaml
llm:
  default_model: "mistral-678b"     # Für finale Antworten/Analysen
  tool_model: "gptoss120b"          # Für alle Tool-Calls im Agent-Loop
  analysis_model: ""                # Leer = default_model wird verwendet
```

**Tipp:** Wenn einzelne Tools mit einem bestimmten Modell besser funktionieren,
kann das über die Settings-UI pro Tool konfiguriert werden.

## API-Endpunkte

### Agent
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/api/agent/chat` | Agent-Chat mit Tool-Calling (SSE) |
| POST | `/api/agent/chat/sync` | Agent-Chat synchron |
| POST | `/api/agent/confirm/{session_id}` | Schreib-Operation bestätigen |
| GET | `/api/agent/mode/{session_id}` | Aktuellen Modus abfragen |
| PUT | `/api/agent/mode/{session_id}` | Modus ändern |
| GET | `/api/agent/tools` | Verfügbare Tools auflisten |
| POST | `/api/agent/session/new` | Neue Session erstellen |

### Skills
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/api/skills` | Alle Skills auflisten |
| GET | `/api/skills/{id}` | Skill-Details |
| POST | `/api/skills/{id}/activate` | Skill aktivieren |
| POST | `/api/skills/from-pdf` | Skill aus PDF erstellen |
| GET | `/api/skills/search/knowledge` | Skill-Wissen durchsuchen |

### Handbuch
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/api/handbook/status` | Index-Status |
| POST | `/api/handbook/index/build` | Index aufbauen |
| GET | `/api/handbook/search` | Volltextsuche |
| GET | `/api/handbook/services` | Alle Services |
| GET | `/api/handbook/service/{id}` | Service-Details |

### Chat (Legacy)
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/api/chat` | Chat (nicht-streamend) |
| POST | `/api/chat/stream` | Chat mit SSE-Streaming |

### Java
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/api/java/tree` | Dateibaum |
| GET | `/api/java/file?path=...` | Dateiinhalt |
| GET | `/api/java/search?q=...` | Klasse suchen |
| POST | `/api/java/index/build` | Index aufbauen |

### Python
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/api/python/tree` | Dateibaum |
| GET | `/api/python/search?q=...` | Symbol suchen |
| POST | `/api/python/validate` | Code validieren |
| POST | `/api/python/test` | Tests ausführen |

### Settings
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/api/settings` | Alle Settings abrufen |
| GET | `/api/settings/section/{section}` | Eine Section abrufen |
| PUT | `/api/settings/section/{section}` | Section aktualisieren |
| POST | `/api/settings/save` | In config.yaml speichern |
| POST | `/api/settings/reload` | Aus config.yaml neu laden |
| GET | `/api/settings/models` | Modelle auflisten |
| POST | `/api/settings/models` | Modell hinzufügen |
| DELETE | `/api/settings/models/{id}` | Modell entfernen |
| GET | `/api/health` | Health-Check aller Subsysteme |

### Weitere
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/api/logs/upload` | WLP-Log hochladen |
| POST | `/api/pdf/upload` | PDF hochladen |
| GET | `/api/confluence/search` | Confluence-Suche |
| GET | `/api/models` | Verfügbare Modelle |
| GET | `/api/health` | Health-Check |

Interaktive API-Dokumentation: **http://localhost:8000/docs**

## Agent-Modi

| Modus | Beschreibung |
|-------|-------------|
| `read_only` | Nur Lese-Operationen (Standard, sicher) |
| `write_with_confirm` | Schreiben mit Bestätigung (empfohlen) |
| `autonomous` | Schreiben ohne Bestätigung (Vorsicht!) |

## Verfügbare Tools

| Tool | Kategorie | Beschreibung |
|------|-----------|-------------|
| `search_code` | Suche | Java/Python Code durchsuchen |
| `search_handbook` | Wissen | Handbuch durchsuchen |
| `search_skills` | Wissen | Skill-Wissensbasen durchsuchen |
| `read_file` | Datei | Datei lesen |
| `list_files` | Datei | Verzeichnis auflisten |
| `write_file` | Datei* | Datei schreiben |
| `edit_file` | Datei* | Datei bearbeiten |
| `get_service_info` | Wissen | Service-Details aus Handbuch |

*Benötigt `file_operations.enabled: true` und entsprechenden Modus

## Skills erstellen

Skills werden als YAML-Dateien in `./skills/` definiert:

```yaml
id: mein-skill
name: Mein Custom Skill
description: Beschreibung des Skills
type: knowledge  # knowledge | prompt | tool | hybrid

activation:
  mode: on-demand  # on-demand | auto | always
  trigger_words:
    - keyword1
    - keyword2

system_prompt: |
  Du bist ein Experte für...

knowledge_sources:
  - type: text
    content: |
      Inline-Wissen hier...
    chunk_size: 500

  # Oder externe Datei:
  - type: pdf
    path: "data/dokument.pdf"
    chunk_size: 1000

metadata:
  author: Dein Name
  tags: [tag1, tag2]
```

## Projektstruktur

```
AI-Assist/
├── main.py                      # FastAPI Einstiegspunkt
├── config.yaml                  # Hauptkonfiguration
├── requirements.txt
├── app/
│   ├── agent/                   # Agent-System
│   │   ├── orchestrator.py      # Agent-Loop mit Tool-Calling
│   │   ├── tools.py             # Tool-Definitionen
│   │   ├── datasource_tools.py  # Datenquellen-Tools
│   │   └── entity_tracker.py   # Entity-Tracking
│   ├── api/routes/
│   │   ├── agent.py             # Agent-Endpunkte (SSE)
│   │   ├── skills.py            # Skill-Endpunkte
│   │   ├── handbook.py          # Handbuch-Endpunkte
│   │   ├── settings.py          # Settings-UI
│   │   ├── chat.py              # LLM Chat (Legacy)
│   │   ├── java.py              # Java-Repo
│   │   ├── python_routes.py     # Python-Repo
│   │   └── ...
│   ├── models/
│   │   └── skill.py             # Skill-Datenmodelle
│   ├── services/
│   │   ├── skill_manager.py     # Skill-Verwaltung
│   │   ├── handbook_indexer.py  # Handbuch-Index
│   │   ├── file_manager.py      # Datei-Operationen
│   │   ├── llm_client.py        # LLM-Kommunikation
│   │   └── ...
│   ├── core/
│   │   ├── config.py            # Pydantic-Konfigurationsmodelle
│   │   ├── token_budget.py      # Token-Budget-Verwaltung
│   │   ├── context_manager.py   # Kontext-Zusammenstellung
│   │   └── ...
│   └── utils/
├── skills/                      # Skill-Definitionen (YAML)
│   ├── java-coding-guidelines.yaml
│   ├── python-coding-guidelines.yaml
│   ├── java-debug.yaml
│   ├── junit-generator.yaml
│   └── fehler-analyse.yaml
├── static/
│   ├── index.html               # IDE-ähnliches Frontend
│   ├── style.css
│   └── app.js
└── docs/
    ├── ARCHITECTURE.md
    ├── REQUIREMENTS.md
    └── SCHEMA.md
```

## Lizenz

MIT
