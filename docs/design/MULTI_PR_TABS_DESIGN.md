# Multi-PR-Tabs Design

## Übersicht

Redesign des PR-Analyse-Flows: PR-Analysen werden ausschließlich im Workspace angezeigt, der Chat dient nur als Navigator mit kurzem Hinweis.

## Requirements (bestätigt)

| # | Requirement | Status |
|---|-------------|--------|
| R1 | Max. 5 PRs gleichzeitig offen | MUSS |
| R2 | PRs parallel laden | MUSS |
| R3 | X-Button im Tab zum Schließen | MUSS |
| R4 | Mittlere Maustaste zum Schließen (Browser-Verhalten) | MUSS |
| R5 | Chat zeigt nur Kurzhinweis "PR #X im Workspace geöffnet" | MUSS |
| R6 | Fehler werden als Chat-Nachricht angezeigt | MUSS |
| R7 | PR-Metadaten-Fragen (Autor, Anzahl PRs) werden im Chat beantwortet | MUSS |
| R8 | Tabs bleiben offen bis manuell geschlossen | MUSS |

---

## Architektur-Änderungen

### 1. State-Management (app.js)

**Vorher:** Singleton `prReviewState`
```javascript
const prReviewState = { active: false, prNumber: null, ... }
```

**Nachher:** Multi-PR-State-Manager
```javascript
const prTabsManager = {
  // Alle offenen PR-Tabs
  tabs: new Map(),  // Key: "owner/repo#number" → PRTabState

  // Aktiver Tab
  activeTabId: null,

  // Konstanten
  MAX_TABS: 5,

  // Methoden
  getTabId(owner, repo, number) {
    return `${owner}/${repo}#${number}`;
  },

  hasTab(tabId) {
    return this.tabs.has(tabId);
  },

  getActiveTab() {
    return this.activeTabId ? this.tabs.get(this.activeTabId) : null;
  },

  // Neuen Tab öffnen (oder existierenden aktivieren)
  openTab(owner, repo, number) {
    const tabId = this.getTabId(owner, repo, number);

    // Bereits offen? → Nur aktivieren
    if (this.hasTab(tabId)) {
      this.activateTab(tabId);
      return { isNew: false, tab: this.tabs.get(tabId) };
    }

    // Max erreicht? → Ältesten schließen
    if (this.tabs.size >= this.MAX_TABS) {
      const oldest = this.tabs.keys().next().value;
      this.closeTab(oldest);
    }

    // Neuen Tab erstellen
    const tab = new PRTabState(owner, repo, number);
    this.tabs.set(tabId, tab);
    this.activeTabId = tabId;

    return { isNew: true, tab };
  },

  closeTab(tabId) {
    this.tabs.delete(tabId);

    // Wenn aktiver Tab geschlossen wurde
    if (this.activeTabId === tabId) {
      // Nächsten Tab aktivieren oder null
      const remaining = [...this.tabs.keys()];
      this.activeTabId = remaining.length > 0 ? remaining[remaining.length - 1] : null;
    }

    this.renderTabs();
  },

  activateTab(tabId) {
    if (this.tabs.has(tabId)) {
      this.activeTabId = tabId;
      this.renderTabs();
      this.renderActivePanel();
    }
  },

  renderTabs() { /* siehe UI-Änderungen */ },
  renderActivePanel() { /* siehe UI-Änderungen */ }
};
```

**PRTabState Klasse:**
```javascript
class PRTabState {
  constructor(owner, repo, number) {
    this.id = `${owner}/${repo}#${number}`;
    this.owner = owner;
    this.repo = repo;
    this.number = number;

    // Metadaten
    this.title = `PR #${number}`;
    this.author = '';
    this.authorName = '';
    this.baseBranch = '';
    this.headBranch = '';
    this.additions = 0;
    this.deletions = 0;
    this.filesChanged = 0;
    this.state = 'open';

    // Analyse
    this.loading = true;
    this.loadingDetails = true;
    this.loadingAnalysis = true;
    this.analysisData = null;
    this.diff = null;
    this.error = null;

    // UI
    this.canApprove = false;
    this.userComments = {};
    this.dismissedComments = new Set();

    // Zeitstempel für LRU
    this.openedAt = Date.now();
    this.lastActiveAt = Date.now();
  }

  updateLastActive() {
    this.lastActiveAt = Date.now();
  }
}
```

---

### 2. HTML-Änderungen (index.html)

**Vorher:** Statischer PR-Tab
```html
<button class="workspace-tab" data-tab="pr" id="workspace-pr-tab" style="display:none">
  <span class="tab-icon">&#128209;</span>
  <span class="tab-label" id="workspace-pr-label">PR</span>
  <span class="tab-badge pr-badge" id="workspace-pr-badge">0</span>
</button>
```

**Nachher:** Dynamischer PR-Tab-Container
```html
<!-- PR-Tabs werden dynamisch eingefügt -->
<div class="workspace-pr-tabs" id="workspace-pr-tabs">
  <!-- Generiert per JavaScript:
  <button class="workspace-tab pr-tab" data-pr-tab="owner/repo#123">
    <span class="tab-icon">&#128209;</span>
    <span class="tab-label">PR #123</span>
    <span class="tab-badge pr-badge" data-severity="critical">2</span>
    <button class="tab-close" onclick="closePRTab(event, 'owner/repo#123')">&times;</button>
  </button>
  -->
</div>
```

**PR-Content Container:**
```html
<div class="workspace-tab-content" id="workspace-pr-content">
  <!-- Wird dynamisch für aktiven Tab gerendert -->
  <div class="pr-review-panel" id="pr-review-panel"></div>
</div>
```

---

### 3. CSS-Änderungen (style.css)

```css
/* PR-Tab-Container: scrollbar bei vielen Tabs */
.workspace-pr-tabs {
  display: flex;
  flex-shrink: 0;
  overflow-x: auto;
  max-width: 400px;  /* Begrenzt, um andere Tabs nicht zu verdrängen */
}

/* PR-Tab mit Close-Button */
.workspace-tab.pr-tab {
  position: relative;
  padding-right: 24px;  /* Platz für X */
  min-width: 80px;
  max-width: 150px;
}

.workspace-tab.pr-tab .tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Close-Button (X) */
.workspace-tab .tab-close {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0;
  border-radius: 3px;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}

.workspace-tab.pr-tab:hover .tab-close {
  opacity: 1;
}

.workspace-tab .tab-close:hover {
  background: rgba(255,255,255,0.1);
  color: var(--text-primary);
}

/* Badge mit Severity-Farben */
.workspace-tab .tab-badge[data-severity="critical"] {
  background: var(--severity-critical, #dc2626);
}
.workspace-tab .tab-badge[data-severity="high"] {
  background: var(--severity-high, #f97316);
}
.workspace-tab .tab-badge[data-severity="medium"] {
  background: var(--severity-medium, #eab308);
}
.workspace-tab .tab-badge[data-severity="ok"] {
  background: var(--severity-ok, #22c55e);
}

/* Loading-Animation im Tab */
.workspace-tab.pr-tab.loading .tab-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

---

### 4. Event-Handling (app.js)

**Mittlere Maustaste zum Schließen:**
```javascript
function initPRTabEvents() {
  const tabContainer = document.getElementById('workspace-pr-tabs');
  if (!tabContainer) return;

  // Mittlere Maustaste (auxclick mit button === 1)
  tabContainer.addEventListener('auxclick', (e) => {
    if (e.button === 1) {  // Mittlere Maustaste
      const tab = e.target.closest('.pr-tab');
      if (tab) {
        e.preventDefault();
        const tabId = tab.dataset.prTab;
        prTabsManager.closeTab(tabId);
      }
    }
  });

  // Verhindere Browser-Autoscroll bei mittlerer Maustaste
  tabContainer.addEventListener('mousedown', (e) => {
    if (e.button === 1) e.preventDefault();
  });
}
```

**Tab-Rendering:**
```javascript
prTabsManager.renderTabs = function() {
  const container = document.getElementById('workspace-pr-tabs');
  if (!container) return;

  // Container leeren und neu aufbauen
  container.innerHTML = '';

  if (this.tabs.size === 0) {
    container.style.display = 'none';
    return;
  }

  container.style.display = 'flex';

  for (const [tabId, tab] of this.tabs) {
    const isActive = tabId === this.activeTabId;
    const severity = this.getHighestSeverity(tab);

    const button = document.createElement('button');
    button.className = `workspace-tab pr-tab ${isActive ? 'active' : ''} ${tab.loading ? 'loading' : ''}`;
    button.dataset.prTab = tabId;
    button.onclick = () => this.activateTab(tabId);

    button.innerHTML = `
      <span class="tab-icon">${tab.loading ? '&#8987;' : '&#128209;'}</span>
      <span class="tab-label" title="${tab.title}">PR #${tab.number}</span>
      ${!tab.loading && tab.analysisData ?
        `<span class="tab-badge" data-severity="${severity}">${this.getIssueCount(tab)}</span>`
        : ''}
      <button class="tab-close" onclick="event.stopPropagation(); prTabsManager.closeTab('${tabId}')">&times;</button>
    `;

    container.appendChild(button);
  }
};

prTabsManager.getHighestSeverity = function(tab) {
  if (!tab.analysisData?.bySeverity) return 'ok';
  const s = tab.analysisData.bySeverity;
  if (s.critical > 0) return 'critical';
  if (s.high > 0) return 'high';
  if (s.medium > 0) return 'medium';
  return 'ok';
};

prTabsManager.getIssueCount = function(tab) {
  if (!tab.analysisData?.bySeverity) return 0;
  const s = tab.analysisData.bySeverity;
  return (s.critical || 0) + (s.high || 0) + (s.medium || 0);
};
```

---

### 5. Paralleles Laden

```javascript
/**
 * Lädt einen PR parallel (Details + Analyse gleichzeitig)
 */
async function loadPRParallel(owner, repo, number) {
  const { isNew, tab } = prTabsManager.openTab(owner, repo, number);

  // Workspace öffnen wenn nicht sichtbar
  if (!workspaceState.visible) toggleWorkspace();

  // Immer PR-Content-Tab aktivieren
  switchWorkspaceTab('pr');
  prTabsManager.renderTabs();
  prTabsManager.renderActivePanel();

  if (!isNew) {
    // Tab existiert bereits, nur aktivieren
    return tab;
  }

  // Parallele Requests starten
  const detailsPromise = fetchPRDetails(owner, repo, number);
  const analysisPromise = fetchPRAnalysis(owner, repo, number);

  // Details verarbeiten sobald verfügbar
  detailsPromise.then(details => {
    if (!details.error) {
      Object.assign(tab, {
        title: details.title || `PR #${number}`,
        author: details.author || '',
        authorName: details.authorName || details.author || '',
        baseBranch: details.baseBranch || 'main',
        headBranch: details.headBranch || '',
        additions: details.additions || 0,
        deletions: details.deletions || 0,
        filesChanged: details.filesChanged || 0,
        state: details.state || 'open',
        loadingDetails: false
      });
    } else {
      tab.error = details.error;
      tab.loadingDetails = false;
    }
    tab.loading = tab.loadingDetails || tab.loadingAnalysis;
    prTabsManager.renderTabs();
    if (prTabsManager.activeTabId === tab.id) {
      prTabsManager.renderActivePanel();
    }
  });

  // Analyse verarbeiten sobald verfügbar
  analysisPromise.then(analysis => {
    if (!analysis.error) {
      tab.analysisData = analysis;
      tab.canApprove = analysis.canApprove !== false && tab.state === 'open';
    }
    tab.loadingAnalysis = false;
    tab.loading = tab.loadingDetails || tab.loadingAnalysis;
    prTabsManager.renderTabs();
    if (prTabsManager.activeTabId === tab.id) {
      prTabsManager.renderActivePanel();
    }
  });

  return tab;
}

async function fetchPRDetails(owner, repo, number) {
  try {
    const res = await fetch(`/api/github/pr/${owner}/${repo}/${number}`);
    if (!res.ok) {
      const err = await res.json();
      return { error: err.detail || 'PR nicht gefunden' };
    }
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
}

async function fetchPRAnalysis(owner, repo, number) {
  try {
    const res = await fetch(`/api/github/pr/${owner}/${repo}/${number}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    if (!res.ok) {
      return { error: 'Analyse fehlgeschlagen' };
    }
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
}
```

---

### 6. Chat-Integration

**Backend (orchestrator.py):**
Neue Event-Typen für Chat-Kurzhinweis:
```python
class AgentEventType(str, Enum):
    # ... existing ...
    PR_OPENED_HINT = "pr_opened_hint"  # Kurzhinweis für Chat
```

Bei PR-Tool-Aufruf:
```python
# Statt langer Analyse-Antwort im Chat:
yield AgentEvent(AgentEventType.PR_OPENED_HINT, {
    "prNumber": pr_number,
    "repoOwner": owner,
    "repoName": repo,
    "message": f"PR #{pr_number} im Workspace geöffnet"
})
```

**Frontend (app.js):**
```javascript
function handlePROpenedHint(data) {
  // Kurze Nachricht im Chat anzeigen
  const hint = document.createElement('div');
  hint.className = 'chat-message assistant pr-hint';
  hint.innerHTML = `
    <div class="pr-hint-content">
      <span class="pr-hint-icon">&#128209;</span>
      <span class="pr-hint-text">
        PR #${data.prNumber} im
        <a href="#" onclick="prTabsManager.activateTab('${data.repoOwner}/${data.repoName}#${data.prNumber}'); return false;">
          Workspace
        </a> geöffnet
      </span>
    </div>
  `;

  appendToChat(hint);

  // PR laden
  loadPRParallel(data.repoOwner, data.repoName, data.prNumber);
}
```

---

### 7. Migration

**Schritt 1:** `prReviewState` durch `prTabsManager` ersetzen
**Schritt 2:** HTML-Struktur anpassen (dynamische Tabs)
**Schritt 3:** CSS hinzufügen
**Schritt 4:** Event-Handler für Mittlere Maustaste
**Schritt 5:** Backend-Events anpassen

---

## Implementierungsreihenfolge

1. **Phase 1: State-Management** (~2h)
   - `prTabsManager` implementieren
   - `PRTabState` Klasse
   - Migration von `prReviewState`

2. **Phase 2: UI-Rendering** (~2h)
   - Dynamische Tab-Generierung
   - CSS für Close-Button und Severity-Badges
   - Panel-Rendering für aktiven Tab

3. **Phase 3: Event-Handling** (~1h)
   - X-Button Click
   - Mittlere Maustaste
   - Tab-Aktivierung

4. **Phase 4: Paralleles Laden** (~1h)
   - Concurrent API-Calls
   - Progressive UI-Updates

5. **Phase 5: Chat-Integration** (~1h)
   - Backend: `PR_OPENED_HINT` Event
   - Frontend: Kurzhinweis-Rendering
   - Differenzierung PR-Analyse vs PR-Metadaten-Fragen

---

## Offene Entscheidungen

| Frage | Vorschlag |
|-------|-----------|
| Tab-Reihenfolge | Neueste rechts (Browser-Verhalten) |
| Max-Tabs-Strategie | LRU (älteste inaktive zuerst schließen) |
| Badge-Inhalt | Summe: Critical + High + Medium |
| Tab-Label-Format | "PR #123" (kurz), Tooltip: "owner/repo: Title" |

---

## Dateien die geändert werden

| Datei | Änderungen |
|-------|------------|
| `static/app.js` | prTabsManager, loadPRParallel, Event-Handler |
| `static/index.html` | workspace-pr-tabs Container |
| `static/style.css` | PR-Tab-Styles, Close-Button, Severity-Badges |
| `app/agent/orchestrator.py` | PR_OPENED_HINT Event |
| `app/services/llm_client.py` | System-Prompt für PR-Kurzhinweis |
