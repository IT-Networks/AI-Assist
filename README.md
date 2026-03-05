# Java AI Code Assistant

Ein lokaler AI-Assistent für Java-Entwickler mit Unterstützung für Code-Review, WLP-Log-Analyse, PDF- und Confluence-Integration.

## Features

- **Java Repository** – Dateibaum durchsuchen, Klassen analysieren, POM-Abhängigkeiten anzeigen
- **Code Review** – Java-Dateien als Kontext an das LLM übergeben, Code-Verbesserungen erhalten
- **WLP Log Analyse** – Server-Logs hochladen, Fehler & IBM-Codes extrahieren und erklären lassen
- **PDF Support** – PDFs hochladen und seitenweise als Kontext nutzen
- **Confluence** – Seiten per Volltext-CQL-Suche finden und als Anforderungskontext laden
- **Modellauswahl** – GPT OSS 120B, Mistral 678B, Qwen 7B/428B (konfigurierbar)
- **Streaming** – Echtzeit-Token-Ausgabe im Browser

## Installation

```bash
cd /home/user/AI-Assist
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Konfiguration

Passe `config.yaml` an:

```yaml
llm:
  base_url: "http://dein-interner-llm-server/v1"  # OpenAI-kompatibler Endpunkt
  api_key: "dein-api-key"
  default_model: "gptoss120b"

java:
  repo_path: "/pfad/zu/deinem/java-projekt"

confluence:
  base_url: "https://deine-confluence-instanz.com"
  username: "benutzer@firma.de"
  api_token: "dein-atlassian-api-token"
```

Alternativ über `.env` (aus `.env.example` kopieren):

```bash
cp .env.example .env
# .env mit deinen Werten befüllen
```

## Starten

```bash
python main.py
# oder:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Dann im Browser: **http://localhost:8000**

## API-Endpunkte

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| POST | `/api/chat` | Chat (nicht-streamend) |
| POST | `/api/chat/stream` | Chat mit SSE-Streaming |
| GET | `/api/java/tree` | Dateibaum des Java-Repos |
| GET | `/api/java/file?path=...` | Dateiinhalt lesen |
| GET | `/api/java/summary?path=...` | AST-Zusammenfassung |
| GET | `/api/java/search?q=...` | Klasse/Interface suchen |
| GET | `/api/java/pom` | POM-Abhängigkeiten |
| POST | `/api/logs/upload` | WLP-Log hochladen |
| GET | `/api/logs/{id}/errors` | Extrahierte Fehler |
| POST | `/api/pdf/upload` | PDF hochladen |
| GET | `/api/pdf/{id}/text` | PDF-Text extrahieren |
| GET | `/api/confluence/search` | Confluence-Volltext-Suche (CQL) |
| GET | `/api/confluence/page/{id}` | Confluence-Seite laden |
| GET | `/api/models` | Verfügbare Modelle |

Interaktive API-Dokumentation: **http://localhost:8000/docs**

## Projektstruktur

```
AI-Assist/
├── main.py                     # FastAPI Einstiegspunkt
├── config.yaml                 # Hauptkonfiguration
├── requirements.txt
├── app/
│   ├── core/
│   │   ├── config.py           # Einstellungen laden
│   │   ├── context_manager.py  # Session-History & Kontext-Injektion
│   │   └── exceptions.py
│   ├── api/
│   │   ├── schemas.py
│   │   └── routes/
│   │       ├── chat.py         # LLM Chat
│   │       ├── java.py         # Java-Repo
│   │       ├── logs.py         # WLP-Logs
│   │       ├── pdf.py          # PDFs
│   │       ├── confluence.py   # Confluence
│   │       └── models.py       # Modelle
│   ├── services/
│   │   ├── llm_client.py       # OpenAI-kompatibler HTTP-Client
│   │   ├── java_reader.py      # Repo-Traversierung + Code-Analyse
│   │   ├── pom_parser.py       # Maven POM Analyse
│   │   ├── log_parser.py       # WLP-Log-Parser
│   │   ├── pdf_reader.py       # PDF-Textextraktion
│   │   └── confluence_client.py # Confluence REST API + CQL-Suche
│   └── utils/
│       ├── token_counter.py
│       └── text_utils.py
└── static/
    ├── index.html              # Chat-UI
    ├── style.css
    └── app.js
```
