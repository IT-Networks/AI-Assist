# AI-Assist Evolution - Requirements Specification

**Version:** 1.0
**Datum:** 2026-03-05
**Status:** Draft - Awaiting Approval

---

## 1. Vision & Ziele

### 1.1 Vision
Transformation von AI-Assist zu einer **Claude-Code-ähnlichen intelligenten Entwicklungsumgebung** mit:
- Automatischer Kontextfindung basierend auf Benutzeranfragen
- Flexiblem Skill-System für Wissen, Prompts und Tools
- Fachlicher Dokumentationsintegration (HTML-Handbuch)
- IDE-ähnlicher Benutzeroberfläche

### 1.2 Hauptziele
1. **Intelligente Analyse** - System findet selbstständig relevante Dateien
2. **Erweiterbare Wissensbasen** - Handbücher, Richtlinien als Datenquellen
3. **Modulares Skill-System** - On-demand aktivierbare Kontexte
4. **Kontrollierte Datei-Operationen** - Schreiben mit User-Bestätigung

---

## 2. Funktionale Anforderungen

### 2.1 Handbuch-Integration (Priorität 1)

#### FR-HB-01: HTML-Handbuch Indexierung
- System kann Pfad zu Netzlaufwerk mit HTML-Handbuch konfigurieren
- Beim Systemstart: Automatische Indexierung aller HTML-Dateien
- Unterstützte Struktur:
  ```
  /handbuch/
  ├── index.html              # Hauptindex
  ├── funktionen/             # Subordner für Funktionsseiten
  │   ├── service-a/
  │   │   ├── uebersicht.htm
  │   │   ├── tab-eingabe.htm
  │   │   ├── tab-ausgabe.htm
  │   │   └── tab-aufruf.htm
  │   └── service-b/
  │       └── ...
  └── felder/                 # Feldbeschreibungen
      ├── feld-a.htm
      └── ...
  ```

#### FR-HB-02: Inhaltsextraktion
- HTML zu Text-Konvertierung unter Beibehaltung der Struktur
- Extraktion von:
  - Service-Namen und Beschreibungen
  - Eingabe-/Ausgabefelder mit Typen und Beschreibungen
  - Aufrufvarianten und Beispiele
  - Tab-Zuordnung (welcher Tab gehört zu welchem Service)

#### FR-HB-03: Volltextsuche
- FTS5-basierte Suche über alle Handbuch-Inhalte
- Suche nach: Service-Namen, Feldnamen, Beschreibungen
- Relevanz-Ranking der Suchergebnisse

#### FR-HB-04: Re-Indexierung
- Manueller Trigger für Re-Indexierung (API + UI)
- Optional: Automatische Re-Indexierung bei Änderungen (Watch-Mode)

---

### 2.2 Skill-System (Priorität 2)

#### FR-SK-01: Skill-Definition
Skills werden als YAML-Dateien definiert:
```yaml
# skills/java-coding-guidelines.yaml
id: java-coding-guidelines
name: Java Coding Guidelines
description: Programmierrichtlinien für Java-Entwicklung
version: 1.0
type: knowledge  # knowledge | tool | hybrid

# Aktivierung
activation:
  mode: on-demand  # always | on-demand | auto
  trigger_words: ["richtlinien", "coding style", "convention"]

# System-Prompt der bei Aktivierung hinzugefügt wird
system_prompt: |
  Du befolgst die folgenden Java-Programmierrichtlinien:
  - Keine Magic Numbers
  - Klassen max. 500 Zeilen
  ...

# Wissensquellen
knowledge_sources:
  - type: pdf
    path: "/path/to/coding-guidelines.pdf"
  - type: markdown
    path: "/path/to/additional-rules.md"

# Optionale Tools die der Skill bereitstellt
tools: []
```

#### FR-SK-02: Skill-Persistenz
- Skills als YAML/JSON Dateien im Ordner `./skills/`
- SQLite-Index für schnelle Suche und Metadaten
- Versionierung möglich (Git-kompatibel)

#### FR-SK-03: Skill-Aktivierung
- UI: Checkbox/Toggle für jeden Skill
- API: `POST /api/skills/{id}/activate` / `deactivate`
- Automatische Aktivierung basierend auf trigger_words (optional)

#### FR-SK-04: PDF-zu-Skill Transformation (Geführt)
Workflow:
1. User lädt PDF hoch
2. System extrahiert Text und Struktur
3. Dialog:
   - "Wofür soll dieser Skill verwendet werden?" (Beschreibung)
   - "Soll das gesamte Dokument oder nur Teile verwendet werden?"
   - "Welche Schlüsselwörter sollen den Skill aktivieren?"
4. System generiert Skill-Definition
5. User kann Skill-YAML anpassen
6. Skill wird gespeichert und ist verfügbar

#### FR-SK-05: Skill-Kontext-Injektion
- Aktive Skills fügen ihren `system_prompt` zur LLM-Anfrage hinzu
- Wissensquellen werden durchsucht wenn relevant
- Token-Limit-Management: Priorisierung bei zu viel Kontext

---

### 2.3 Intelligente Suche (Priorität 3)

#### FR-IS-01: Agent-basierte Kontextfindung
- Bei jeder Anfrage: System analysiert was benötigt wird
- Automatische Suche in:
  - Java/Python Repository (existierend)
  - Handbuch (neu)
  - Aktive Skills/Wissensbasen
  - PDFs im Kontext
  - Confluence (existierend)

#### FR-IS-02: Tool-basierte Architektur
Interne Tools (wie Claude Code):
```
- search_handbook(query) → relevante Handbuch-Seiten
- search_code(query, language) → relevante Code-Dateien
- search_skills(query) → relevante Skill-Inhalte
- read_file(path) → Dateiinhalt
- list_files(path, pattern) → Dateiliste
```

#### FR-IS-03: Relevanz-Ranking
- Kombination der Suchergebnisse aus allen Quellen
- Ranking nach Relevanz zur Anfrage
- Token-Budget-Verwaltung (max. Kontext-Größe)

#### FR-IS-04: Transparenz
- UI zeigt an welche Quellen durchsucht wurden
- Welche Dateien/Seiten in den Kontext geladen wurden
- Option: User kann Kontext manuell anpassen

---

### 2.4 Datei-Operationen (Priorität 4)

#### FR-DO-01: Modi
- **Nur-Lesen-Modus** (Standard): Analysiert und gibt Vorschläge
- **Schreib-Modus**: Kann Dateien erstellen/editieren mit Bestätigung

#### FR-DO-02: Operationen
```
- read_file(path) → Dateiinhalt lesen
- write_file(path, content) → Neue Datei erstellen
- edit_file(path, changes) → Bestehende Datei ändern
- delete_file(path) → Datei löschen (nur mit expliziter Bestätigung)
```

#### FR-DO-03: Bestätigungs-Workflow
1. System generiert Vorschlag (z.B. Code-Änderung)
2. UI zeigt Diff-Ansicht (vorher/nachher)
3. User bestätigt oder lehnt ab
4. Bei Bestätigung: Änderung wird ausgeführt

#### FR-DO-04: Sicherheit
- Konfigurierbare erlaubte Pfade (Whitelist)
- Keine Operationen außerhalb definierter Bereiche
- Backup vor Änderungen (optional)

---

## 3. Nicht-Funktionale Anforderungen

### 3.1 Performance
- **NFR-01**: Handbuch-Indexierung < 60 Sekunden für 1000 HTML-Dateien
- **NFR-02**: Suchanfragen < 500ms Antwortzeit
- **NFR-03**: Netzlaufwerk-Zugriff mit Timeout-Handling

### 3.2 Benutzerfreundlichkeit
- **NFR-04**: Single-User-System (keine Authentifizierung)
- **NFR-05**: Intuitive UI für Skill-Verwaltung
- **NFR-06**: Klare Feedback-Meldungen bei Aktionen

### 3.3 Erweiterbarkeit
- **NFR-07**: Neue Datenquellen-Typen einfach hinzufügbar
- **NFR-08**: Skill-Format dokumentiert und erweiterbar
- **NFR-09**: Plugin-Architektur für Tools

---

## 4. UI/UX Anforderungen

### 4.1 IDE-ähnliches Layout
```
┌─────────────────────────────────────────────────────────────┐
│ [Logo] AI-Assist              [Skills ▼] [Settings] [Mode] │
├───────────┬─────────────────────────────┬───────────────────┤
│           │                             │                   │
│  Datei-   │       Chat-Bereich          │    Kontext-       │
│  Explorer │                             │    Panel          │
│           │                             │                   │
│  - Java   │  User: Wie rufe ich...      │  Aktive Quellen:  │
│  - Python │                             │  ✓ Handbuch       │
│  - Handb. │  AI: Basierend auf dem      │  ✓ Java-Code      │
│  - Skills │      Handbuch...            │  □ PDF xyz        │
│           │                             │                   │
│           │  [Vorschlag anwenden?]      │  Geladene Dateien:│
│           │  [✓ Ja] [✗ Nein] [Diff]    │  - Service-A.htm  │
│           │                             │  - MyClass.java   │
│           ├─────────────────────────────┤                   │
│           │ [Nachricht eingeben...]     │                   │
└───────────┴─────────────────────────────┴───────────────────┘
```

### 4.2 Komponenten

#### Datei-Explorer (Links)
- Baum-Ansicht für alle Datenquellen
- Ordner: Java-Repo, Python-Repo, Handbuch, Skills, Uploads
- Dateien anklicken → in Kontext laden oder anzeigen

#### Chat-Bereich (Mitte)
- Bestehende Chat-Funktionalität
- Erweitert um:
  - Diff-Ansichten für Code-Vorschläge
  - "Anwenden"-Buttons mit Bestätigung
  - Quellen-Zitate (welche Datei lieferte Info)

#### Kontext-Panel (Rechts)
- Liste aktiver Skills (mit Toggle)
- Liste aktiver Datenquellen
- Liste geladener Dateien im aktuellen Kontext
- Token-Zähler (verwendete/verfügbare Tokens)

#### Skill-Verwaltung
- Modal/Drawer für Skill-Übersicht
- Skill erstellen/bearbeiten/löschen
- PDF-Upload mit geführtem Skill-Dialog

---

## 5. User Stories

### US-01: Als Entwickler möchte ich...
...nach einem Service im Handbuch fragen und automatisch die relevanten Dokumentationsseiten erhalten.

**Akzeptanzkriterien:**
- Frage: "Wie sind die Eingabeparameter für Service XYZ?"
- System durchsucht Handbuch automatisch
- Relevante Tab-Seiten werden in Kontext geladen
- Antwort enthält Feldnamen, Typen, Beschreibungen

### US-02: Als Entwickler möchte ich...
...Programmierrichtlinien als Skill aktivieren können, sodass Code-Vorschläge diese befolgen.

**Akzeptanzkriterien:**
- Skill "Java Guidelines" in UI sichtbar
- Toggle aktiviert Skill
- Nachfolgende Code-Vorschläge beachten Richtlinien
- Skill kann wieder deaktiviert werden

### US-03: Als Entwickler möchte ich...
...ein PDF mit Projektrichtlinien in einen wiederverwendbaren Skill umwandeln.

**Akzeptanzkriterien:**
- PDF-Upload startet Skill-Erstellungs-Dialog
- System fragt nach Zweck und Trigger-Wörtern
- Skill wird erstellt und ist sofort nutzbar
- Skill-Definition kann nachträglich angepasst werden

### US-04: Als Entwickler möchte ich...
...dass das System automatisch relevante Code-Dateien findet wenn ich eine Frage stelle.

**Akzeptanzkriterien:**
- Frage: "Wo wird UserService verwendet?"
- System sucht in Java-Repo nach Referenzen
- Relevante Dateien werden aufgelistet
- Code-Ausschnitte werden in Antwort eingebunden

### US-05: Als Entwickler möchte ich...
...Code-Änderungen vor dem Anwenden als Diff sehen und bestätigen können.

**Akzeptanzkriterien:**
- AI schlägt Code-Änderung vor
- Diff-Ansicht zeigt alt vs. neu
- Button "Anwenden" mit Bestätigung
- Bei Ablehnung: nur Vorschlag bleibt im Chat

---

## 6. Offene Fragen

| # | Frage | Status |
|---|-------|--------|
| 1 | Sollen mehrere Handbücher (verschiedene Netzlaufwerke) unterstützt werden? | Offen |
| 2 | Wie soll mit großen PDFs (>100 Seiten) umgegangen werden bei Skill-Erstellung? | Offen |
| 3 | Soll es vordefinierte Skill-Templates geben? | Offen |
| 4 | Wie granular soll die Pfad-Whitelist für Datei-Operationen sein? | Offen |

---

## 7. Nächste Schritte

1. **Review** dieses Requirements-Dokuments
2. **Klärung** der offenen Fragen
3. **/sc:design** für Architektur-Entwurf
4. **/sc:workflow** für Implementierungsplan

---

*Erstellt durch /sc:brainstorm am 2026-03-05*
