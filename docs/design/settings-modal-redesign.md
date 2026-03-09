# Settings Modal Redesign

## Problem

Die aktuelle Settings-Navigation hat **23 Items** in einer flachen Liste mit nur einem Separator. Das führt zu:
- Cognitive Overload - zu viele Optionen auf einmal
- Schwierige Navigation - User müssen scrollen
- Unklare Gruppierung - verwandte Settings sind verstreut

## Aktuelle Struktur

```
LLM
Modelle
Agent Tools
Java
Python
Confluence
Handbuch
Datenbank
Jira
Skills
Sub-Agenten
Datei-Ops
Index
Server
Datenquellen
─────────────
MQ Series
Test-Tool
Log-Server
WLP Server
Maven
Jenkins
GitHub
🔍 Suche
```

## Vorgeschlagene Struktur: Kategorien mit Collapsibles

### Kategorien (6 Gruppen)

```
┌─────────────────────────┐
│ ▼ KI & Modelle          │  ← Expanded by default
│   ├─ LLM                │
│   ├─ Modelle            │
│   └─ Sub-Agenten        │
│                         │
│ ▶ Agent                 │  ← Collapsed
│                         │
│ ▶ Entwicklung           │
│                         │
│ ▶ Datenquellen          │
│                         │
│ ▶ Server & Infra        │
│                         │
│ ▶ Integrationen         │
└─────────────────────────┘
```

### Detail-Zuordnung

| Kategorie          | Items                                        | Icon |
|--------------------|----------------------------------------------|------|
| **KI & Modelle**   | LLM, Modelle, Sub-Agenten                   | 🤖   |
| **Agent**          | Agent Tools, Skills, Datei-Ops              | ⚙️   |
| **Entwicklung**    | Java, Python, Maven, GitHub                 | 💻   |
| **Datenquellen**   | Datenbank, Confluence, Handbuch, Jira, Index| 📚   |
| **Server & Infra** | Server, WLP, Log-Server, Test-Tool          | 🖥️   |
| **Integrationen**  | MQ Series, Jenkins, Web-Suche               | 🔗   |

## UI-Konzept

### Option A: Accordion-Navigation (empfohlen)

```
┌────────────────────────────────────────────────────────────┐
│  Einstellungen                                    [X]      │
├──────────────┬─────────────────────────────────────────────┤
│              │                                             │
│ 🤖 KI & Mod. │  LLM Einstellungen                         │
│   ├ LLM      │  ─────────────────                         │
│   ├ Modelle  │                                            │
│   └ Sub-Ag.  │  API Base URL                              │
│              │  [https://api.openai.com/v1          ]     │
│ ⚙️ Agent     │                                            │
│   ├ Tools    │  API Key                                   │
│   ├ Skills   │  [••••••••••••••••••••                ]     │
│   └ Datei-Op │                                            │
│              │  Tool Model                                │
│ 💻 Entwickl. │  [gpt-4o-mini                         ]     │
│              │                                            │
│ 📚 Datenque. │  Analysis Model                            │
│              │  [gpt-4o                              ]     │
│ 🖥️ Server    │                                            │
│              │  Max Tokens                                │
│ 🔗 Integrat. │  [────────●─────────] 128000               │
│              │                                            │
├──────────────┴─────────────────────────────────────────────┤
│ [Neu laden]                    [Abbrechen]   [Speichern]  │
└────────────────────────────────────────────────────────────┘
```

### Option B: Tab-Groups mit Sub-Tabs

```
┌────────────────────────────────────────────────────────────┐
│  Einstellungen                                    [X]      │
├────────────────────────────────────────────────────────────┤
│ [🤖 KI] [⚙️ Agent] [💻 Dev] [📚 Daten] [🖥️ Server] [🔗 Int]│
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌─────┐ ┌────────┐ ┌───────────┐                         │
│  │ LLM │ │ Modelle│ │ Sub-Agent │                         │
│  └─────┘ └────────┘ └───────────┘                         │
│                                                            │
│  LLM Einstellungen                                        │
│  ─────────────────                                        │
│  ...                                                      │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

## Empfehlung: Option A (Accordion)

**Vorteile:**
- Weniger horizontaler Platz benötigt
- Natürliches Scanning von oben nach unten
- Kategorien können expanded bleiben während man scrollt
- Konsistent mit üblichen Settings-Patterns (VS Code, IDE-artig)

## Zusätzliche Verbesserungen

### 1. Quick Search
```
┌────────────────────────┐
│ 🔍 Einstellung suchen  │  ← Filtert alle Items
├────────────────────────┤
│ ...                    │
```

### 2. Recently Used
```
│ 📌 Zuletzt verwendet   │
│   ├ LLM               │
│   └ GitHub            │
```

### 3. Visual Indicators
- 🟢 Grüner Punkt = Konfiguriert/Aktiv
- 🔴 Roter Punkt = Nicht konfiguriert/Fehler
- ⚠️ Warnung = Unvollständig

```
│ 🤖 KI & Modelle        │
│   ├ 🟢 LLM            │  ← API Key gesetzt
│   ├ 🟢 Modelle        │  ← 3 Modelle konfiguriert
│   └ ⚪ Sub-Agenten    │  ← Noch keine
```

## Implementation Plan

### Phase 1: HTML-Struktur
- Navigation in collapsible sections umbauen
- Category-Header mit Icons hinzufügen
- Collapse/Expand Logik

### Phase 2: CSS
- Category-Styles
- Smooth animations für expand/collapse
- Active/Hover states für nested items

### Phase 3: JavaScript
- State für expanded categories (localStorage persistieren)
- Keyboard navigation (Arrow keys)
- Optional: Quick search

## Datei-Änderungen

| Datei        | Änderungen                                    |
|--------------|-----------------------------------------------|
| index.html   | Navigation neu strukturieren mit categories  |
| style.css    | Neue CSS-Klassen für categories              |
| app.js       | Category expand/collapse Logik               |

## Zeitschätzung

- Phase 1: ~1h
- Phase 2: ~30min
- Phase 3: ~1h
- **Gesamt: ~2.5h**
