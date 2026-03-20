# PR Review Panel - Verbesserungen Design

## Übersicht

Verbesserungen des PR-Review-Panels für bessere Benutzerfreundlichkeit und Funktionalität.

---

## 1. Deutsche Findings-Ausgabe

### Problem
Die PR-Analyse liefert teilweise englische statt deutsche Ausgaben.

### Lösung
Prompt in `_analyze_pr_for_workspace()` explizit auf Deutsch umstellen.

### Änderungen

**Datei: `app/agent/orchestrator.py` (Zeile ~3675)**

```python
prompt = f"""Analysiere diesen Pull Request und gib eine strukturierte Bewertung.
WICHTIG: Alle Texte (title, description, summary) MÜSSEN auf Deutsch sein!

PR #{pr_number}: {title}
Status: {state}

DIFF:
```
{diff[:12000]}
```

Antworte NUR mit einem JSON-Objekt in diesem Format (keine Erklärungen):
{{
  "bySeverity": {{
    "critical": <Anzahl kritischer Issues>,
    "high": <Anzahl hoher Issues>,
    "medium": <Anzahl mittlerer Issues>,
    "low": <Anzahl niedriger Issues>,
    "info": <Anzahl Info-Hinweise>
  }},
  "verdict": "<approve|request_changes|comment>",
  "findings": [
    {{
      "severity": "<critical|high|medium|low|info>",
      "title": "<Kurzer deutscher Titel>",
      "file": "<Dateipfad>",
      "line": <Zeilennummer oder null>,
      "description": "<Kurze deutsche Beschreibung>",
      "codeSnippet": "<Betroffener Code-Ausschnitt wenn relevant>"
    }}
  ],
  "summary": "<1-2 Sätze deutsche Zusammenfassung>"
}}

Bewertungskriterien:
- critical: Sicherheitslücken, Datenverlust-Risiko
- high: Bugs, Breaking Changes, Performance-Probleme
- medium: Code-Qualität, fehlende Tests, schlechte Patterns
- low: Style-Issues, Minor Improvements
- info: Dokumentation, Kommentare

Maximal 10 Findings. Bei closed/merged PRs: verdict="comment".
ALLE TEXTE AUF DEUTSCH!"""
```

---

## 2. Klickbare Severity-Badges zum Filtern

### Anforderung
Badges oben im Panel sollen klickbar sein um Findings nach Severity zu filtern.

### Design

```
┌─────────────────────────────────────────────────────┐
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │Critical:2│ │ High: 3  │ │Medium: 5 │ │Low: 1  │ │ ← Klickbar
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│                                                     │
│  [Aktueller Filter: HIGH] [× Filter zurücksetzen]  │
│                                                     │
│  ─────────────────────────────────────────────────  │
│  Nur HIGH-Findings werden angezeigt...              │
└─────────────────────────────────────────────────────┘
```

### Implementierung

**1. State für Filter (app.js)**
```javascript
// Im prTabsManager oder pro Tab
tab.activeFilter = null;  // null = alle, oder 'critical'|'high'|'medium'|'low'|'info'
```

**2. Badge-Click-Handler (app.js)**
```javascript
function toggleSeverityFilter(severity) {
  const tab = prTabsManager.getActiveTab();
  if (!tab) return;

  // Toggle: Gleiche Severity = Reset, andere = Filter setzen
  tab.activeFilter = (tab.activeFilter === severity) ? null : severity;

  renderPRReviewPanelForTab(tab);
}
```

**3. Badge-HTML mit Onclick (app.js ~Zeile 1949)**
```javascript
if (criticalEl) {
  criticalEl.textContent = sev.critical || 0;
  criticalEl.onclick = () => toggleSeverityFilter('critical');
  criticalEl.classList.toggle('active', tab.activeFilter === 'critical');
}
// ... für alle Badges
```

**4. Findings filtern (app.js ~Zeile 2034)**
```javascript
// Vor dem Rendern der Files:
let filteredItems = allItems;
if (tab.activeFilter) {
  filteredItems = allItems.filter(item => item.severity === tab.activeFilter);
}
```

**5. CSS für klickbare Badges (style.css)**
```css
.severity-badge {
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}

.severity-badge:hover {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.severity-badge.active {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.pr-filter-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--surface);
  border-radius: 4px;
  font-size: 0.8rem;
  margin-bottom: 8px;
}

.pr-filter-reset {
  cursor: pointer;
  color: var(--accent);
}
```

---

## 3. Low-Badge Text-Anzeige Fix

### Problem
Zwischen Info und Medium zeigt das Low-Badge nur lila Farbe ohne Text.

### Ursache
Das `#pr-low` Element existiert nicht im HTML oder CSS `::before` Pseudo-Element fehlt.

### Lösung

**HTML prüfen (index.html Zeile ~617):**
```html
<div class="pr-severity-badges">
  <span class="severity-badge critical" id="pr-critical">0</span>
  <span class="severity-badge high" id="pr-high">0</span>
  <span class="severity-badge medium" id="pr-medium">0</span>
  <span class="severity-badge low" id="pr-low">0</span>      <!-- Muss existieren -->
  <span class="severity-badge info" id="pr-info">0</span>
</div>
```

**CSS bestätigen (style.css Zeile ~7339):**
```css
.severity-badge.low {
  background: rgba(163, 113, 247, 0.15);
  color: #a371f7;
}

.severity-badge.low::before {
  content: 'Low: ';
}
```

---

## 4. Verbesserte Datei-Gruppierung: Klassenname + Pfad

### Problem
Bei gruppierten Dateien wird nur der Pfad angezeigt, nicht der Klassenname.

### Design

```
┌─────────────────────────────────────────────────────┐
│ ▼ OrderService.java                          3 issues│
│   com/example/service/order/OrderService.java       │
│   ─────────────────────────────────────────────────  │
│   [HIGH] Fehlende Null-Prüfung                      │
│   [MEDIUM] Magic Number                              │
└─────────────────────────────────────────────────────┘
```

### Implementierung

**1. Helper-Funktion (app.js)**
```javascript
/**
 * Extrahiert Klassennamen aus Dateipfad
 * Input: "com/example/service/OrderService.java"
 * Output: { className: "OrderService.java", fullPath: "com/example/service/OrderService.java" }
 */
function _extractClassName(filePath) {
  const parts = filePath.replace(/\\/g, '/').split('/');
  const fileName = parts[parts.length - 1] || filePath;

  return {
    className: fileName,
    fullPath: filePath,
    // Für Java/Kotlin: Package-Path ohne Dateiname
    packagePath: parts.length > 1 ? parts.slice(0, -1).join('/') : null
  };
}
```

**2. Angepasstes File-Header-HTML (app.js ~Zeile 2090)**
```javascript
const individualHTML = Object.entries(groupedFiles.individual).map(([filePath, items]) => {
  const { className, packagePath } = _extractClassName(filePath);

  return `
    <div class="pr-file">
      <div class="pr-file-header" onclick="togglePRFile(this)">
        <div class="pr-file-info">
          <span class="pr-file-name">${escapeHtml(className)}</span>
          ${packagePath ? `<span class="pr-file-path">${escapeHtml(packagePath)}</span>` : ''}
        </div>
        <span class="pr-file-issues">${items.length} issues</span>
      </div>
      <div class="pr-file-comments">
        ${items.map(item => renderPRFinding(item)).join('')}
      </div>
    </div>
  `;
}).join('');
```

**3. CSS für zweizeilige Darstellung (style.css)**
```css
.pr-file-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;  /* Für Text-Overflow */
}

.pr-file-name {
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--text);
}

.pr-file-path {
  font-size: 0.75rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

---

## 5. Finding-Modal mit Code-Ansicht

### Anforderung
Klick auf Finding öffnet Modal mit betroffenen Code und Anmerkung.

### Design

```
┌─────────────────────────────────────────────────────────────────┐
│  [HIGH] Fehlende Null-Prüfung                             [×]  │
├─────────────────────────────────────────────────────────────────┤
│  OrderService.java  Zeile 42-48                                 │
├─────────────────────────────────────────────────────────────────┤
│  40 │                                                           │
│  41 │   public void processOrder(Order order) {                 │
│  42 │ ▶   String status = order.getStatus();  ◀──── Problem     │
│  43 │     if (status.equals("PENDING")) {     │                 │
│  44 │       // ...                            │                 │
│  45 │     }                                   │                 │
│  46 │   }                                                       │
│  47 │                                                           │
├─────────────────────────────────────────────────────────────────┤
│  ⚠ Beschreibung:                                               │
│  "order" kann null sein. Vor dem Zugriff auf getStatus()       │
│  sollte eine Null-Prüfung erfolgen.                            │
├─────────────────────────────────────────────────────────────────┤
│  💬 Kommentar hinzufügen:                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Bitte vor Zeile 42 "if (order == null) return;" einfügen │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│        [Kommentar speichern]          [Schließen]              │
└─────────────────────────────────────────────────────────────────┘
```

### Implementierung

**1. Modal-HTML Template (app.js)**
```javascript
function _createFindingModalHTML(finding, tab) {
  const { className, packagePath } = _extractClassName(finding.file || 'Unknown');
  const lineInfo = finding.line ? `Zeile ${finding.line}` : '';

  // Code-Snippet aus Finding oder Platzhalter
  const codeSnippet = finding.codeSnippet || finding.code || '// Code nicht verfügbar';

  // Escape und Syntax-Highlighting vorbereiten
  const highlightedCode = _highlightCodeLines(codeSnippet, finding.line);

  return `
    <div class="modal-overlay" id="finding-modal-overlay" onclick="closeFindingModal(event)">
      <div class="finding-modal" onclick="event.stopPropagation()">
        <div class="finding-modal-header">
          <div class="finding-modal-title">
            <span class="severity-tag ${finding.severity}">${finding.severity.toUpperCase()}</span>
            <span>${escapeHtml(finding.title)}</span>
          </div>
          <button class="modal-close" onclick="closeFindingModal()">&times;</button>
        </div>

        <div class="finding-modal-file">
          <span class="file-icon">📄</span>
          <span class="file-name">${escapeHtml(className)}</span>
          ${lineInfo ? `<span class="file-line">${lineInfo}</span>` : ''}
        </div>

        <div class="finding-modal-code">
          <pre><code>${highlightedCode}</code></pre>
        </div>

        <div class="finding-modal-description">
          <h4>⚠ Beschreibung</h4>
          <p>${escapeHtml(finding.description)}</p>
        </div>

        <div class="finding-modal-comment">
          <h4>💬 Kommentar hinzufügen</h4>
          <textarea
            id="finding-comment-input"
            placeholder="Kommentar für dieses Finding..."
            data-finding-id="${finding.id || ''}"
          ></textarea>
          <div class="finding-modal-actions">
            <button class="btn btn-primary" onclick="saveFindingComment()">Speichern</button>
            <button class="btn btn-ghost" onclick="closeFindingModal()">Schließen</button>
          </div>
        </div>
      </div>
    </div>
  `;
}
```

**2. Code-Highlighting Helper (app.js)**
```javascript
function _highlightCodeLines(code, highlightLine) {
  const lines = code.split('\n');
  const startLine = Math.max(1, (highlightLine || 1) - 3);

  return lines.map((line, idx) => {
    const lineNum = startLine + idx;
    const isHighlighted = lineNum === highlightLine;
    const lineClass = isHighlighted ? 'code-line highlighted' : 'code-line';
    const marker = isHighlighted ? '▶' : ' ';

    return `<span class="${lineClass}"><span class="line-num">${lineNum.toString().padStart(4)}</span>${marker} ${escapeHtml(line)}</span>`;
  }).join('\n');
}
```

**3. Modal öffnen/schließen (app.js)**
```javascript
function openFindingModal(findingIndex) {
  const tab = prTabsManager.getActiveTab();
  if (!tab || !tab.analysisData?.findings) return;

  const finding = tab.analysisData.findings[findingIndex];
  if (!finding) return;

  // Existierendes Modal entfernen
  const existing = document.getElementById('finding-modal-overlay');
  if (existing) existing.remove();

  // Modal einfügen
  document.body.insertAdjacentHTML('beforeend', _createFindingModalHTML(finding, tab));

  // Focus auf Textarea
  setTimeout(() => {
    const input = document.getElementById('finding-comment-input');
    if (input) input.focus();
  }, 100);
}

function closeFindingModal(event) {
  // Nur schließen wenn auf Overlay geklickt oder explizit aufgerufen
  if (event && event.target.id !== 'finding-modal-overlay') return;

  const modal = document.getElementById('finding-modal-overlay');
  if (modal) modal.remove();
}

function saveFindingComment() {
  const input = document.getElementById('finding-comment-input');
  if (!input) return;

  const findingId = input.dataset.findingId;
  const comment = input.value.trim();

  const tab = prTabsManager.getActiveTab();
  if (tab && comment) {
    if (!tab.findingComments) tab.findingComments = {};
    tab.findingComments[findingId] = comment;
    showToast('Kommentar gespeichert', 'success');
  }

  closeFindingModal();
}
```

**4. Finding klickbar machen (app.js ~Zeile 2175)**
```javascript
function renderPRFinding(item, index) {
  const severity = item.severity || 'info';
  const title = item.title || 'Issue';
  const description = item.description || item.body || '';
  const line = item.line;

  return `
    <div class="pr-comment ${severity} clickable" onclick="openFindingModal(${index})">
      <div class="pr-comment-header">
        ${line ? `<span class="pr-comment-line">Zeile ${line}</span>` : ''}
        <span class="pr-comment-severity severity-${severity}">${severity.toUpperCase()}</span>
        <span class="pr-comment-title">${escapeHtml(title)}</span>
      </div>
      <div class="pr-comment-body">${escapeHtml(description)}</div>
      <div class="pr-comment-hint">Klicken für Details</div>
    </div>
  `;
}
```

**5. Modal CSS (style.css)**
```css
/* Finding Modal */
.finding-modal {
  background: var(--bg);
  border-radius: 12px;
  max-width: 800px;
  width: 90%;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.4);
}

.finding-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.finding-modal-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}

.severity-tag {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 700;
}

.severity-tag.critical { background: var(--danger-bg); color: var(--danger); }
.severity-tag.high { background: rgba(255,140,0,0.15); color: #ff8c00; }
.severity-tag.medium { background: var(--warning-bg); color: var(--warning); }
.severity-tag.low { background: rgba(163,113,247,0.15); color: #a371f7; }
.severity-tag.info { background: var(--surface); color: var(--text-secondary); }

.finding-modal-file {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  background: var(--bg-secondary);
  font-family: var(--font-mono);
  font-size: 0.85rem;
}

.file-line {
  color: var(--accent);
  margin-left: auto;
}

.finding-modal-code {
  padding: 0;
  background: #1e1e1e;
  overflow-x: auto;
}

.finding-modal-code pre {
  margin: 0;
  padding: 16px;
  font-size: 0.85rem;
  line-height: 1.5;
}

.code-line {
  display: block;
}

.code-line.highlighted {
  background: rgba(255, 200, 0, 0.15);
  border-left: 3px solid var(--warning);
  margin-left: -3px;
}

.line-num {
  color: var(--text-muted);
  margin-right: 12px;
  user-select: none;
}

.finding-modal-description {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.finding-modal-description h4 {
  margin: 0 0 8px 0;
  font-size: 0.9rem;
}

.finding-modal-comment {
  padding: 16px 20px;
}

.finding-modal-comment h4 {
  margin: 0 0 10px 0;
  font-size: 0.9rem;
}

.finding-modal-comment textarea {
  width: 100%;
  min-height: 80px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font-family: inherit;
  resize: vertical;
}

.finding-modal-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
  justify-content: flex-end;
}

/* Klickbares Finding */
.pr-comment.clickable {
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
}

.pr-comment.clickable:hover {
  background: var(--surface);
  transform: translateX(4px);
}

.pr-comment-hint {
  font-size: 0.7rem;
  color: var(--text-muted);
  margin-top: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}

.pr-comment.clickable:hover .pr-comment-hint {
  opacity: 1;
}
```

---

## 6. Inline-Kommentare an Codezeilen

### Erweiterung des Modals

Ermöglicht das Hinzufügen von Kommentaren direkt an spezifischen Codezeilen.

### Design

```
┌─────────────────────────────────────────────────────────────────┐
│  40 │                                                           │
│  41 │   public void processOrder(Order order) {                 │
│  42 │ ▶   String status = order.getStatus();    [+💬]          │ ← Hover zeigt Button
│     │   ┌──────────────────────────────────────────────────┐   │
│     │   │ 💬 Null-Check hier einfügen: if (order == null)  │   │ ← Inline-Kommentar
│     │   └──────────────────────────────────────────────────┘   │
│  43 │     if (status.equals("PENDING")) {                       │
│  44 │       // ...                                              │
└─────────────────────────────────────────────────────────────────┘
```

### Implementierung

**1. Inline-Kommentar State (Tab-Level)**
```javascript
// Pro Tab: Inline-Kommentare nach Zeile
tab.inlineComments = {
  // findingId -> { lineNumber: commentText }
  "finding-1": {
    42: "Null-Check hier einfügen",
    45: "Exception-Handling fehlt"
  }
};
```

**2. Erweitertes Code-Rendering**
```javascript
function _highlightCodeLinesWithComments(code, highlightLine, inlineComments = {}) {
  const lines = code.split('\n');
  const startLine = Math.max(1, (highlightLine || 1) - 3);

  return lines.map((line, idx) => {
    const lineNum = startLine + idx;
    const isHighlighted = lineNum === highlightLine;
    const comment = inlineComments[lineNum];
    const lineClass = isHighlighted ? 'code-line highlighted' : 'code-line';

    let html = `
      <span class="${lineClass}" data-line="${lineNum}">
        <span class="line-num">${lineNum.toString().padStart(4)}</span>
        <span class="line-content">${escapeHtml(line)}</span>
        <button class="inline-comment-btn" onclick="addInlineComment(${lineNum})" title="Kommentar hinzufügen">💬</button>
      </span>
    `;

    // Existierender Kommentar
    if (comment) {
      html += `
        <span class="inline-comment" data-line="${lineNum}">
          <span class="inline-comment-icon">💬</span>
          <span class="inline-comment-text">${escapeHtml(comment)}</span>
          <button class="inline-comment-remove" onclick="removeInlineComment(${lineNum})">&times;</button>
        </span>
      `;
    }

    return html;
  }).join('\n');
}
```

**3. Inline-Kommentar Handler**
```javascript
function addInlineComment(lineNumber) {
  const comment = prompt(`Kommentar für Zeile ${lineNumber}:`);
  if (!comment) return;

  const tab = prTabsManager.getActiveTab();
  const findingId = document.getElementById('finding-comment-input')?.dataset.findingId;

  if (tab && findingId) {
    if (!tab.inlineComments) tab.inlineComments = {};
    if (!tab.inlineComments[findingId]) tab.inlineComments[findingId] = {};
    tab.inlineComments[findingId][lineNumber] = comment;

    // Modal neu rendern
    refreshFindingModal();
  }
}

function removeInlineComment(lineNumber) {
  const tab = prTabsManager.getActiveTab();
  const findingId = document.getElementById('finding-comment-input')?.dataset.findingId;

  if (tab?.inlineComments?.[findingId]) {
    delete tab.inlineComments[findingId][lineNumber];
    refreshFindingModal();
  }
}
```

**4. CSS für Inline-Kommentare**
```css
.code-line {
  display: flex;
  align-items: center;
  position: relative;
}

.line-content {
  flex: 1;
}

.inline-comment-btn {
  opacity: 0;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
  font-size: 0.8rem;
  transition: opacity 0.15s;
}

.code-line:hover .inline-comment-btn {
  opacity: 0.6;
}

.inline-comment-btn:hover {
  opacity: 1 !important;
}

.inline-comment {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px 6px 50px;
  background: var(--info-bg, rgba(100, 150, 255, 0.1));
  border-left: 3px solid var(--accent);
  font-size: 0.85rem;
}

.inline-comment-icon {
  font-size: 0.9rem;
}

.inline-comment-text {
  flex: 1;
  color: var(--text);
}

.inline-comment-remove {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 2px 6px;
}

.inline-comment-remove:hover {
  color: var(--danger);
}
```

---

## Zusammenfassung der Änderungen

| Komponente | Datei | Änderung |
|------------|-------|----------|
| Deutsche Ausgabe | `orchestrator.py` | Prompt explizit auf Deutsch |
| Klickbare Badges | `app.js`, `style.css` | Filter-State, onclick, active-Style |
| Low-Badge Fix | `index.html`, `style.css` | Element prüfen, CSS-Regel prüfen |
| Datei-Gruppierung | `app.js`, `style.css` | `_extractClassName()`, zweizeiliges Layout |
| Finding-Modal | `app.js`, `style.css` | Neue Modal-Komponente |
| Inline-Kommentare | `app.js`, `style.css` | Line-Level Comments |

---

## Implementierungsreihenfolge

1. **Phase 1 - Quick Fixes** (30 min)
   - Deutsche Prompt-Anpassung
   - Low-Badge CSS Fix

2. **Phase 2 - Filter** (45 min)
   - Klickbare Severity-Badges
   - Filter-State und -Logik

3. **Phase 3 - Datei-Darstellung** (30 min)
   - `_extractClassName()` Helper
   - Zweizeiliges File-Header Layout

4. **Phase 4 - Finding-Modal** (60 min)
   - Modal-HTML und -CSS
   - Open/Close Handler
   - Code-Highlighting

5. **Phase 5 - Inline-Kommentare** (45 min)
   - Erweitertes Code-Rendering
   - Kommentar-Handler
   - State-Management

**Geschätzte Gesamtzeit: ~3.5 Stunden**

---

## Nächste Schritte

Nach Genehmigung dieses Designs: `/sc:implement` für die Umsetzung.
