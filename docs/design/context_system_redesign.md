# Design: Kontext-System Redesign

**Version:** 1.0
**Datum:** 2026-03-16
**Status:** Draft

---

## 1. Zusammenfassung

Redesign des Kontext-Systems mit folgenden Kernänderungen:
1. **Explorer-Suche** - Fuzzy-Search pro Repo im Explorer-Panel
2. **@-Mention** - Inline-Dateisuche im Chat-Input über alle Repos
3. **Kein aktives Repo** - Repo-Dropdown wird zum Such-Filter, KI ermittelt Kontext selbst

---

## 2. Anforderungen

### Funktional

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| F1 | Fuzzy-Suche auf Dateinamen pro Repo im Explorer | Hoch |
| F2 | @-Mention im Chat öffnet Datei-Autocomplete | Hoch |
| F3 | Repo-Dropdown als Filter für Suche (nicht "aktives Repo") | Mittel |
| F4 | Suchergebnisse zum Kontext hinzufügbar | Hoch |
| F5 | @-Mention fügt Datei zum Chat-Kontext hinzu | Hoch |

### Nicht-Funktional

| ID | Anforderung |
|----|-------------|
| NF1 | Suche < 100ms bei 10.000 Dateien |
| NF2 | Fuzzy-Match tolerant (Tippfehler, Teilstrings) |
| NF3 | Keyboard-Navigation in Autocomplete |

---

## 3. UI-Design

### 3.1 Explorer-Panel (Vorher → Nachher)

**Vorher:**
```
┌─────────────────────────────────────────┐
│ ☕ Java Repository                       │
├─────────────────────────────────────────┤
│ Repo: [Dropdown: Aktives Repo    ▼]     │
│ Index: 1.234 Klassen                    │
│ [🏗️ Index] [📄 Baum]                    │
│ ├── 📁 src/main/java                    │
│ │   └── UserService.java          [+]   │
└─────────────────────────────────────────┘
```

**Nachher:**
```
┌─────────────────────────────────────────┐
│ ☕ Java Repository                       │
├─────────────────────────────────────────┤
│ 🔍 [Suche Datei...              ] [▼]   │
│     ↳ Dropdown: Repo-Filter             │
│ Index: 1.234 Klassen                    │
│ [🏗️ Index] [📄 Baum]                    │
│ ┌─────────────────────────────────────┐ │
│ │ Suchergebnisse:                     │ │
│ │ ├── UserService.java          [+]   │ │
│ │ ├── UserController.java       [+]   │ │
│ │ └── UserRepository.java       [+]   │ │
│ └─────────────────────────────────────┘ │
│ ├── 📁 src/main/java (Baum unten)       │
└─────────────────────────────────────────┘
```

### 3.2 Chat-Input mit @-Mention

```
┌─────────────────────────────────────────────────────────┐
│ Erkläre mir die @User|                                  │
│                 ┌────────────────────────────────────┐  │
│                 │ 📄 UserService.java      (Java)    │  │
│                 │ 📄 UserController.java   (Java)    │  │
│                 │ 📄 user_model.py         (Python)  │  │
│                 │ 📄 user_routes.py        (Python)  │  │
│                 └────────────────────────────────────┘  │
│ [Senden]                                                │
└─────────────────────────────────────────────────────────┘
```

**Verhalten:**
1. User tippt `@` → Autocomplete erscheint
2. Weitertippen filtert Ergebnisse (Fuzzy-Match)
3. Enter/Click → Datei wird als Badge im Input angezeigt
4. Beim Senden → Datei-Inhalt wird zum Kontext hinzugefügt

### 3.3 @-Mention Badge im Input

```
┌─────────────────────────────────────────────────────────┐
│ Erkläre mir die [📄 UserService.java ✕] Klasse         │
│ [Senden]                                                │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Technisches Design

### 4.1 Komponenten-Übersicht

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (app.js)                        │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  ExplorerSearch  │  │  @MentionInput   │  │ ContextChips  │  │
│  │  - fuzzySearch() │  │  - detectMention │  │ (existing)    │  │
│  │  - renderResults │  │  - showDropdown  │  │               │  │
│  └────────┬─────────┘  └────────┬─────────┘  └───────────────┘  │
│           │                     │                               │
│           ▼                     ▼                               │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   FileSearchService                      │    │
│  │  - searchFiles(query, repoFilter) → Promise<File[]>     │    │
│  │  - fuzzyMatch(filename, query) → score                  │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ API Calls
┌─────────────────────────────────────────────────────────────────┐
│                        Backend (FastAPI)                        │
├─────────────────────────────────────────────────────────────────┤
│  GET /api/files/search?q={query}&repo={java|python|all}        │
│  Response: [{ path, name, type, repo }]                         │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 API-Spezifikation

#### `GET /api/files/search`

**Request:**
```
GET /api/files/search?q=UserServ&repo=java&limit=10
```

**Response:**
```json
{
  "results": [
    {
      "path": "src/main/java/com/example/UserService.java",
      "name": "UserService.java",
      "type": "java",
      "repo": "backend-api",
      "score": 0.95
    },
    {
      "path": "src/main/java/com/example/UserServiceImpl.java",
      "name": "UserServiceImpl.java",
      "type": "java",
      "repo": "backend-api",
      "score": 0.85
    }
  ],
  "total": 2,
  "query": "UserServ"
}
```

### 4.3 Fuzzy-Search Algorithmus

```javascript
// Frontend: Einfacher Fuzzy-Match für schnelle Filterung
function fuzzyMatch(filename, query) {
  const name = filename.toLowerCase();
  const q = query.toLowerCase();

  // Exact substring match → höchste Priorität
  if (name.includes(q)) {
    return 1.0 - (name.indexOf(q) / name.length) * 0.1;
  }

  // Character-by-character fuzzy match
  let qi = 0;
  let score = 0;
  for (let i = 0; i < name.length && qi < q.length; i++) {
    if (name[i] === q[qi]) {
      score += 1;
      qi++;
    }
  }

  return qi === q.length ? score / name.length : 0;
}
```

### 4.4 @-Mention Detection

```javascript
// Im message-input Event-Handler
function handleInput(e) {
  const text = e.target.value;
  const cursorPos = e.target.selectionStart;

  // Finde @ vor Cursor
  const beforeCursor = text.slice(0, cursorPos);
  const atMatch = beforeCursor.match(/@(\w*)$/);

  if (atMatch) {
    const query = atMatch[1];
    showMentionDropdown(query, cursorPos);
  } else {
    hideMentionDropdown();
  }
}
```

### 4.5 State-Änderungen

```javascript
// Entfernt: "aktives Repo" Konzept
// state.activeJavaRepo  → ENTFERNT
// state.activePythonRepo → ENTFERNT

// Neu: Mention-State
state.mentionState = {
  active: false,
  query: '',
  cursorPosition: 0,
  selectedIndex: 0,
  results: []
};

// Neu: Pending mentions (vor dem Senden)
state.pendingMentions = [
  // { path: '...', name: '...', type: 'java' }
];
```

---

## 5. Implementierungsplan

### Phase 1: Backend API (2h)

```
[ ] GET /api/files/search Endpoint
    - Durchsucht Java + Python Index
    - Fuzzy-Match auf Dateinamen
    - Repo-Filter Parameter
```

### Phase 2: Explorer-Suche (3h)

```
[ ] Such-Input im Explorer-Panel hinzufügen
[ ] Repo-Dropdown als Filter umbauen
[ ] Suchergebnisse rendern mit [+] Button
[ ] "Aktives Repo" Logik entfernen
```

### Phase 3: @-Mention (4h)

```
[ ] @-Detection im Input-Handler
[ ] Dropdown-Komponente (Position, Styling)
[ ] Keyboard-Navigation (↑↓, Enter, Esc)
[ ] Badge-Rendering im Input
[ ] Integration mit sendMessage()
```

### Phase 4: Cleanup (1h)

```
[ ] Alte setActiveRepo() Funktionen entfernen
[ ] Settings-Panel "Aktives Repo" entfernen
[ ] Tests anpassen
```

---

## 6. HTML-Änderungen

### Explorer-Section (vorher)

```html
<div id="java-repo-selector" class="repo-selector-bar">
  <label>Repo:</label>
  <select id="java-repo-select" onchange="setActiveRepo('java', this.value)">
  </select>
</div>
```

### Explorer-Section (nachher)

```html
<div class="explorer-search-bar">
  <input type="text"
         id="java-search-input"
         placeholder="🔍 Datei suchen..."
         oninput="searchExplorerFiles('java', this.value)">
  <select id="java-repo-filter"
          onchange="filterSearchResults('java')"
          title="Repo-Filter">
    <option value="all">Alle Repos</option>
    <!-- Dynamisch gefüllt -->
  </select>
</div>
<div id="java-search-results" class="search-results" style="display:none">
  <!-- Dynamisch gefüllt -->
</div>
```

### Chat-Input (nachher)

```html
<div id="input-row">
  <div id="mention-badges"></div>
  <textarea id="message-input"
            placeholder="Frage stellen... (@datei für Kontext)"
            oninput="handleInputWithMention(event)">
  </textarea>
  <div id="mention-dropdown" class="mention-dropdown" style="display:none">
    <!-- Dynamisch gefüllt -->
  </div>
  <button id="send-btn">...</button>
</div>
```

---

## 7. CSS-Ergänzungen

```css
/* Explorer Search */
.explorer-search-bar {
  display: flex;
  gap: 4px;
  padding: 4px;
}

.explorer-search-bar input {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
}

.search-results {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 4px;
  margin: 4px;
}

/* @-Mention Dropdown */
.mention-dropdown {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 200px;
  overflow-y: auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
}

.mention-item {
  padding: 8px 12px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
}

.mention-item:hover,
.mention-item.selected {
  background: var(--bg-hover);
}

.mention-item .file-type {
  color: var(--text-muted);
  font-size: 0.8em;
}

/* Mention Badge im Input */
.mention-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--primary-light);
  border-radius: 12px;
  font-size: 0.85em;
  margin: 2px;
}

.mention-badge .remove {
  cursor: pointer;
  opacity: 0.7;
}

.mention-badge .remove:hover {
  opacity: 1;
}
```

---

## 8. Entscheidungen

| Frage | Entscheidung |
|-------|--------------|
| Caching | ✅ Ja - Dateiliste wird gecacht für schnelle Suche |
| Limit | ✅ Max. 10 Ergebnisse im @-Dropdown |
| Multi-Select | ✅ Ja - Mehrere Dateien per @ selektierbar |

### Multi-Select Verhalten

```
@User|
┌────────────────────────────────────┐
│ ☑ UserService.java         (Java) │  ← ausgewählt
│ ☐ UserController.java      (Java) │
│ ☑ user_model.py          (Python) │  ← ausgewählt
│ ☐ user_routes.py         (Python) │
└────────────────────────────────────┘
        [2 ausgewählt] [Hinzufügen]
```

### Caching-Strategie

```javascript
// FileSearchCache - 5 Minuten TTL
const fileCache = {
  java: { files: [], timestamp: 0 },
  python: { files: [], timestamp: 0 },
  TTL: 5 * 60 * 1000  // 5 Minuten
};

async function getCachedFiles(type) {
  const cache = fileCache[type];
  if (Date.now() - cache.timestamp < fileCache.TTL) {
    return cache.files;
  }
  // Refresh from API
  cache.files = await fetchFileList(type);
  cache.timestamp = Date.now();
  return cache.files;
}
```

---

## 9. Nächste Schritte

Nach Genehmigung:
```
/sc:implement context-redesign --phase 1
```
