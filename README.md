# AI Code Assistant

Ein lokaler AI-Assistent für Entwickler mit Claude-Code-ähnlicher Architektur. Features: Agent mit Tool-Calling, Skill-System, Handbuch-Integration und Datei-Operationen.

## Features

### Agent-System (Neu)
- **Tool-Calling** – Agent kann automatisch Tools aufrufen um Informationen zu sammeln
- **3 Modi** – `read_only`, `write_with_confirm`, `autonomous`
- **8 Tools** – Code-Suche, Handbuch, Skills, Datei-Operationen
- **Bestätigungs-Workflow** – Diff-Preview vor Schreib-Operationen

### Skill-System (Neu)
- **YAML-basierte Skills** – Prompts + Wissensquellen kombinieren
- **PDF-zu-Skill** – PDFs als Wissensbasis einbinden
- **Aktivierung pro Session** – Skills on-demand aktivieren
- **Volltextsuche** – SQLite FTS5 für Skill-Inhalte

### Handbuch-Integration (Neu)
- **HTML-Parsing** – Services, Tabs, Felder aus HTML-Handbuch
- **Netzlaufwerk-Support** – Pfade wie `//server/share/handbuch`
- **Service-Suche** – Volltextsuche über alle Dokumentation

### Code-Repositories
- **Java** – Dateibaum, Klassen-Analyse, POM-Abhängigkeiten, Index-Suche
- **Python** – Symbol-Suche, Validierung (flake8/ruff/mypy), Tests (pytest)

### Settings-UI (Neu)
- **Frontend-Konfiguration** – Alle Settings über das UI ändern
- **Live-Anwendung** – Änderungen sofort aktiv (ohne Neustart)
- **Persistenz** – In config.yaml speichern mit Backup
- **Modell-Verwaltung** – LLM-Modelle hinzufügen/entfernen

### Weitere Features
- **WLP Log Analyse** – Server-Logs hochladen, Fehler extrahieren
- **PDF Support** – PDFs als Kontext nutzen
- **Confluence** – Seiten per CQL-Suche finden und laden
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
  default_model: "gptoss120b"

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

## API-Endpunkte

### Agent (Neu)
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/api/agent/chat` | Agent-Chat mit Tool-Calling (SSE) |
| POST | `/api/agent/chat/sync` | Agent-Chat synchron |
| POST | `/api/agent/confirm/{session_id}` | Schreib-Operation bestätigen |
| GET | `/api/agent/mode/{session_id}` | Aktuellen Modus abfragen |
| PUT | `/api/agent/mode/{session_id}` | Modus ändern |
| GET | `/api/agent/tools` | Verfügbare Tools auflisten |
| POST | `/api/agent/session/new` | Neue Session erstellen |

### Skills (Neu)
| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | `/api/skills` | Alle Skills auflisten |
| GET | `/api/skills/{id}` | Skill-Details |
| POST | `/api/skills/{id}/activate` | Skill aktivieren |
| POST | `/api/skills/from-pdf` | Skill aus PDF erstellen |
| GET | `/api/skills/search/knowledge` | Skill-Wissen durchsuchen |

### Handbuch (Neu)
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

### Settings (Neu)
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
│   ├── agent/                   # NEU: Agent-System
│   │   ├── orchestrator.py      # Agent-Loop mit Tool-Calling
│   │   └── tools.py             # Tool-Definitionen
│   ├── api/routes/
│   │   ├── agent.py             # NEU: Agent-Endpunkte
│   │   ├── skills.py            # NEU: Skill-Endpunkte
│   │   ├── handbook.py          # NEU: Handbuch-Endpunkte
│   │   ├── chat.py              # LLM Chat
│   │   ├── java.py              # Java-Repo
│   │   ├── python_routes.py     # Python-Repo
│   │   └── ...
│   ├── models/
│   │   └── skill.py             # NEU: Skill-Datenmodelle
│   ├── services/
│   │   ├── skill_manager.py     # NEU: Skill-Verwaltung
│   │   ├── handbook_indexer.py  # NEU: Handbuch-Index
│   │   ├── file_manager.py      # NEU: Datei-Operationen
│   │   ├── llm_client.py
│   │   └── ...
│   ├── core/
│   │   ├── config.py
│   │   └── ...
│   └── utils/
├── skills/                      # NEU: Skill-Definitionen
│   ├── java-coding-guidelines.yaml
│   ├── python-coding-guidelines.yaml
│   └── example-with-knowledge.yaml
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
