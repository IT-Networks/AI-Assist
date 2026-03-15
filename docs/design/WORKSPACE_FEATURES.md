# Design-Konzept: Workspace Features

**Version:** 1.0
**Datum:** 2026-03-15
**Status:** Draft

---

## Inhaltsverzeichnis

1. [Executive Summary](#1-executive-summary)
2. [Architektur-Übersicht](#2-architektur-übersicht)
3. [Feature 1: Workspace Panel](#3-feature-1-workspace-panel)
4. [Feature 2: Code Split-View](#4-feature-2-code-split-view)
5. [Feature 3: SQL Split-View](#5-feature-3-sql-split-view)
6. [Feature 4: Research Panel](#6-feature-4-research-panel)
7. [Feature 5: User Dashboard](#7-feature-5-user-dashboard)
8. [Feature 6: Error Pattern Learning](#8-feature-6-error-pattern-learning)
9. [API-Spezifikation](#9-api-spezifikation)
10. [Datenmodelle](#10-datenmodelle)
11. [Implementierungsplan](#11-implementierungsplan)

---

## 1. Executive Summary

Dieses Design beschreibt fünf zusammenhängende Features, die das AI-Assist System um einen integrierten Workspace erweitern:

| Feature | Zweck | Priorität |
|---------|-------|-----------|
| **Workspace Panel** | Unified Container mit Tabs | P0 (Basis) |
| **Code Split-View** | Diff-Ansicht für Code-Änderungen | P1 |
| **SQL Split-View** | Query-Editor mit Result-Table | P1 |
| **Research Panel** | Recherche-Ergebnisse aggregiert | P2 |
| **User Dashboard** | Nutzungsstatistiken und Metriken | P2 |
| **Error Pattern Learning** | Automatisches Lernen aus Fehlern | P3 |

**Technologie-Stack:**
- Frontend: Vanilla JS (bestehend), diff2html, Prism.js
- Backend: FastAPI (bestehend), SQLite für Patterns
- Keine neuen Dependencies außer diff2html + Prism.js CDN

---

## 2. Architektur-Übersicht

### 2.1 Komponenten-Diagramm

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐   ┌──────────────────────────────────────────────────┐   │
│  │              │   │                 WORKSPACE PANEL                   │   │
│  │   SIDEBAR    │   │  ┌────────┬────────┬──────────┬────────┐         │   │
│  │    LEFT      │   │  │ Code   │  SQL   │ Research │ Files  │  Tabs   │   │
│  │              │   │  └────────┴────────┴──────────┴────────┘         │   │
│  │  - Chats     │   │  ┌────────────────────────────────────────────┐  │   │
│  │  - Files     │   │  │                                            │  │   │
│  │              │   │  │           TAB CONTENT AREA                 │  │   │
│  │              │   │  │                                            │  │   │
│  │              │   │  │   - CodeDiffView                          │  │   │
│  └──────────────┘   │  │   - SqlQueryView                          │  │   │
│                     │  │   - ResearchView                          │  │   │
│  ┌──────────────┐   │  │   - FileBrowserView                       │  │   │
│  │              │   │  │                                            │  │   │
│  │    CHAT      │   │  └────────────────────────────────────────────┘  │   │
│  │    AREA      │   │                                                   │   │
│  │              │   │  ┌────────────────────────────────────────────┐  │   │
│  │  - Messages  │◄──┼──│              ACTION BAR                    │  │   │
│  │  - Input     │   │  │  [Apply All] [Export] [Clear] [Minimize]   │  │   │
│  │              │   │  └────────────────────────────────────────────┘  │   │
│  └──────────────┘   └──────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         USER DASHBOARD (Modal)                        │  │
│  │   KPIs │ Tool Usage │ Activity │ Errors │ Patterns                   │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND (FastAPI)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐ │
│  │  /api/workspace │  │  /api/dashboard │  │  /api/patterns              │ │
│  │                 │  │                 │  │                             │ │
│  │  - GET state    │  │  - GET metrics  │  │  - GET patterns             │ │
│  │  - POST code    │  │  - GET usage    │  │  - POST learn               │ │
│  │  - POST sql     │  │  - GET errors   │  │  - POST feedback            │ │
│  │  - POST search  │  │  - GET trends   │  │  - GET suggestions          │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘ │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         SERVICES                                      │  │
│  │                                                                       │  │
│  │  WorkspaceManager │ AnalyticsService │ PatternLearner │ DiffService  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         STORAGE                                       │  │
│  │                                                                       │  │
│  │  SQLite: patterns.db │ analytics.db │ workspace_state.json           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Datenfluss

```
Agent Tool-Call (write_file, query_database, search_*)
         │
         ▼
   SSE Event Stream
         │
         ├──► WORKSPACE_CODE_CHANGE ──► Code Tab aktualisieren
         │
         ├──► WORKSPACE_SQL_RESULT ───► SQL Tab aktualisieren
         │
         ├──► WORKSPACE_RESEARCH ─────► Research Tab aktualisieren
         │
         └──► TOOL_ERROR ─────────────► Pattern Learning triggern
```

---

## 3. Feature 1: Workspace Panel

### 3.1 Anforderungen

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| WS-01 | Panel rechts vom Chat, resizable | Must |
| WS-02 | Tabs: Code, SQL, Research, Files | Must |
| WS-03 | Badge mit Item-Count pro Tab | Must |
| WS-04 | Collapse/Expand Toggle | Must |
| WS-05 | State persistent pro Session | Should |
| WS-06 | Keyboard Shortcut (Ctrl+B) | Should |

### 3.2 UI-Spezifikation

```
┌─────────────────────────────────────────────────────────────────┐
│ WORKSPACE                                          [_] [□] [×] │
├─────────────────────────────────────────────────────────────────┤
│ ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐          │
│ │ Code (2) │ │ SQL (1)  │ │ Research(3)│ │  Files   │          │
│ └──────────┘ └──────────┘ └────────────┘ └──────────┘          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│                    [TAB CONTENT AREA]                           │
│                                                                 │
│                    Höhe: calc(100vh - header - input)           │
│                    Min-Width: 400px                             │
│                    Max-Width: 60vw                              │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ [Apply All] [Export Session] [Clear]              Items: 6     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 CSS-Variablen (Erweiterung)

```css
:root {
  /* Workspace Panel */
  --workspace-w: 500px;
  --workspace-min-w: 400px;
  --workspace-max-w: 60vw;
  --workspace-collapsed-w: 48px;

  /* Tab Colors */
  --tab-code: #58a6ff;
  --tab-sql: #f0883e;
  --tab-research: #a371f7;
  --tab-files: #3fb950;
}
```

### 3.4 JavaScript State

```javascript
const workspaceState = {
  visible: true,
  width: 500,
  activeTab: 'code',
  tabs: {
    code: {
      items: [],      // Array of CodeChange objects
      selected: null  // Currently selected item ID
    },
    sql: {
      items: [],      // Array of SqlQuery objects
      selected: null
    },
    research: {
      items: [],      // Array of ResearchResult objects
      selected: null
    },
    files: {
      tree: [],       // File tree for session
      selected: null
    }
  }
};
```

---

## 4. Feature 2: Code Split-View

### 4.1 Anforderungen

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| CD-01 | Side-by-Side Diff mit Syntax Highlighting | Must |
| CD-02 | Unified Diff Toggle | Must |
| CD-03 | Line-by-Line Actions (Apply, Reject) | Must |
| CD-04 | Expandable Context (+/- 5 Zeilen) | Should |
| CD-05 | Copy Code Block | Should |
| CD-06 | Jump to Line in Original | Could |

### 4.2 Datenmodell: CodeChange

```typescript
interface CodeChange {
  id: string;
  timestamp: number;

  // File Info
  filePath: string;
  fileName: string;
  language: string;        // java, python, sql, etc.

  // Content
  originalContent: string;
  modifiedContent: string;
  diff: string;            // Unified diff format

  // Metadata
  toolCall: string;        // write_file, edit_file
  description: string;     // Agent's description of change

  // State
  status: 'pending' | 'applied' | 'rejected';
  appliedAt?: number;
}
```

### 4.3 UI-Komponenten

```
┌─────────────────────────────────────────────────────────────────┐
│ UserService.java                    [Split ▼] [Copy] [Expand]  │
├─────────────────────────────────────────────────────────────────┤
│ "Add null-check for user validation"            Tool: edit_file│
├────────────────────────────┬────────────────────────────────────┤
│       ORIGINAL             │         MODIFIED                  │
├────────────────────────────┼────────────────────────────────────┤
│ 41│                        │ 41│                               │
│ 42│ public void save() {   │ 42│ public void save() {          │
│ 43│   db.insert(user);     │ 43│   if (user == null) {         │
│   │                        │ 44│     throw new IllegalArg..    │
│   │                        │ 45│   }                           │
│   │                        │ 46│   db.insert(user);            │
│ 44│   log.info("saved");   │ 47│   log.info("saved");          │
│ 45│ }                      │ 48│ }                             │
├────────────────────────────┴────────────────────────────────────┤
│ +3 lines, -0 lines                                              │
├─────────────────────────────────────────────────────────────────┤
│ [✓ Apply] [✗ Reject] [Edit Before Apply]         Status: Pending│
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 Diff Rendering (diff2html)

```javascript
import { html } from 'diff2html';
import 'diff2html/bundles/css/diff2html.min.css';

function renderDiff(unifiedDiff, targetElement, options = {}) {
  const config = {
    outputFormat: options.splitView ? 'side-by-side' : 'line-by-line',
    drawFileList: false,
    matching: 'lines',
    highlight: true,
    colorScheme: 'dark',
    ...options
  };

  targetElement.innerHTML = html(unifiedDiff, config);

  // Apply Prism.js highlighting
  Prism.highlightAllUnder(targetElement);
}
```

---

## 5. Feature 3: SQL Split-View

### 5.1 Anforderungen

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| SQ-01 | Query Editor mit SQL Highlighting | Must |
| SQ-02 | Result Table mit Sortierung | Must |
| SQ-03 | Column Visibility Toggle | Must |
| SQ-04 | Export CSV/JSON | Must |
| SQ-05 | Query History (letzte 10) | Should |
| SQ-06 | Pagination für große Results | Should |
| SQ-07 | EXPLAIN Visualisierung | Could |

### 5.2 Datenmodell: SqlQuery

```typescript
interface SqlQuery {
  id: string;
  timestamp: number;

  // Query
  query: string;
  database: string;        // DB2, SQLite, etc.
  schema?: string;

  // Result
  columns: ColumnDef[];
  rows: any[][];
  rowCount: number;
  executionTimeMs: number;

  // Metadata
  toolCall: string;        // query_database
  truncated: boolean;      // True if > 1000 rows
  error?: string;
}

interface ColumnDef {
  name: string;
  type: string;            // VARCHAR, INTEGER, etc.
  nullable: boolean;
  visible: boolean;        // User can toggle
}
```

### 5.3 UI-Komponenten

```
┌─────────────────────────────────────────────────────────────────┐
│ Query 1 of 3                                    [Run ▶] [Clear]│
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ SELECT u.name, u.email, o.total                             │ │
│ │ FROM users u                                                │ │
│ │ JOIN orders o ON u.id = o.user_id                           │ │
│ │ WHERE o.total > 100                                         │ │
│ │ ORDER BY o.total DESC;                                      │ │
│ └─────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│ ✓ 847 rows in 0.23s          [Columns ▼] [Export ▼] [Copy SQL] │
├───────┬──────────────────────┬─────────────┬────────────────────┤
│   #   │ name           ▼    │ email       │ total         ▲   │
├───────┼──────────────────────┼─────────────┼────────────────────┤
│   1   │ Max Mustermann       │ max@ex.de   │         1,250.00  │
│   2   │ Anna Schmidt         │ anna@ex.de  │           890.50  │
│   3   │ Peter Müller         │ peter@ex.de │           445.00  │
│  ...  │ ...                  │ ...         │               ... │
├───────┴──────────────────────┴─────────────┴────────────────────┤
│ Page [1] of 85    [◀ Prev] [Next ▶]    Showing: [25 ▼] per page│
└─────────────────────────────────────────────────────────────────┘
```

### 5.4 Table Rendering

```javascript
class SqlResultTable {
  constructor(container, query) {
    this.container = container;
    this.query = query;
    this.sortColumn = null;
    this.sortDirection = 'asc';
    this.page = 1;
    this.pageSize = 25;
    this.visibleColumns = query.columns.filter(c => c.visible);
  }

  render() {
    const html = `
      <div class="sql-result-header">
        <span class="result-info">
          ${this.query.error
            ? `<span class="error">✗ ${this.query.error}</span>`
            : `✓ ${this.query.rowCount} rows in ${this.query.executionTimeMs}ms`}
        </span>
        <div class="result-actions">
          <button onclick="this.toggleColumns()">Columns ▼</button>
          <button onclick="this.export('csv')">Export CSV</button>
          <button onclick="this.export('json')">Export JSON</button>
        </div>
      </div>
      <table class="sql-result-table">
        <thead>${this.renderHeader()}</thead>
        <tbody>${this.renderBody()}</tbody>
      </table>
      <div class="sql-pagination">${this.renderPagination()}</div>
    `;
    this.container.innerHTML = html;
  }

  renderHeader() {
    return `<tr>
      <th class="row-num">#</th>
      ${this.visibleColumns.map(col => `
        <th class="sortable ${this.sortColumn === col.name ? this.sortDirection : ''}"
            onclick="sqlTable.sort('${col.name}')">
          ${col.name}
          <span class="sort-icon"></span>
        </th>
      `).join('')}
    </tr>`;
  }

  sort(columnName) {
    if (this.sortColumn === columnName) {
      this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
      this.sortColumn = columnName;
      this.sortDirection = 'asc';
    }
    this.render();
  }

  export(format) {
    const data = format === 'csv'
      ? this.toCSV()
      : JSON.stringify(this.query.rows, null, 2);

    const blob = new Blob([data], { type: format === 'csv' ? 'text/csv' : 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `query_result_${Date.now()}.${format}`;
    a.click();
  }
}
```

---

## 6. Feature 4: Research Panel

### 6.1 Anforderungen

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| RS-01 | Gruppierung nach Quelle (Web, Code, Wiki) | Must |
| RS-02 | Collapsible Source Groups | Must |
| RS-03 | Relevance Score anzeigen | Should |
| RS-04 | Quick Preview on Hover | Should |
| RS-05 | Open in new Tab | Should |

### 6.2 Datenmodell: ResearchResult

```typescript
interface ResearchResult {
  id: string;
  timestamp: number;
  query: string;

  sources: ResearchSource[];
  totalResults: number;
}

interface ResearchSource {
  type: 'web' | 'code' | 'wiki' | 'handbook' | 'pdf';
  name: string;           // "DuckDuckGo", "Java Repo", etc.
  results: ResearchItem[];
  searchTimeMs: number;
}

interface ResearchItem {
  title: string;
  snippet: string;
  url?: string;
  filePath?: string;
  lineNumber?: number;
  relevance: number;      // 0.0 - 1.0
}
```

### 6.3 UI-Komponenten

```
┌─────────────────────────────────────────────────────────────────┐
│ Research: "NullPointerException handling best practices"       │
│ 12 results from 4 sources                         [Collapse All]│
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ ▼ Web (DuckDuckGo) · 4 results · 230ms                         │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ ⬤ Effective Java - Null Handling                        │  │
│   │   "Use Optional instead of null for return values..."   │  │
│   │   https://example.com/effective-java       Relevance: 95%│  │
│   └─────────────────────────────────────────────────────────┘  │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ ⬤ Baeldung - Avoiding NPE in Java                       │  │
│   │   "Best practices for null-safe code in Java 11+..."    │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│ ▼ Code (Java Repo) · 5 results · 45ms                          │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ ⬤ ValidationUtils.java:142                              │  │
│   │   Objects.requireNonNull(user, "user must not be null") │  │
│   │   src/main/java/utils/ValidationUtils.java              │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│ ▶ Wiki (Confluence) · 2 results · 180ms  [Click to expand]     │
│                                                                 │
│ ▶ Handbook · 1 result · 12ms                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. Feature 5: User Dashboard

### 7.1 Anforderungen

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| DB-01 | KPI Cards (Requests, Latency, Success) | Must |
| DB-02 | Tool Usage Chart (Top 10) | Must |
| DB-03 | Activity Heatmap (7 Tage) | Should |
| DB-04 | Recent Errors List | Should |
| DB-05 | Time Range Filter | Should |
| DB-06 | Export Report | Could |

### 7.2 Datenmodell: Analytics

```typescript
interface DashboardMetrics {
  timeRange: 'day' | 'week' | 'month';

  // KPIs
  totalRequests: number;
  requestsTrend: number;        // % change vs previous period
  avgResponseTime: number;      // ms
  responseTrend: number;
  successRate: number;          // 0-100%
  successTrend: number;

  // Charts
  toolUsage: ToolUsageEntry[];
  activityHeatmap: ActivityEntry[];
  recentErrors: ErrorEntry[];
  tokenUsage: TokenUsageEntry;
}

interface ToolUsageEntry {
  tool: string;
  count: number;
  successRate: number;
  avgDuration: number;
}

interface ActivityEntry {
  date: string;            // YYYY-MM-DD
  hour: number;            // 0-23
  count: number;
}

interface ErrorEntry {
  timestamp: number;
  tool: string;
  errorType: string;
  message: string;
  count: number;           // Occurrences today
  patternId?: string;      // If matched to known pattern
}

interface TokenUsageEntry {
  input: number;
  output: number;
  total: number;
  limit: number;
}
```

### 7.3 UI-Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ DASHBOARD                                      [This Week ▼] [Export PDF]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐         │
│  │    📊 247         │ │    ⚡ 1.2s        │ │    ✓ 94.2%        │         │
│  │    Requests       │ │    Avg Response   │ │    Success Rate   │         │
│  │    ▲ +12%         │ │    ▼ -8%          │ │    ▲ +2.1%        │         │
│  └───────────────────┘ └───────────────────┘ └───────────────────┘         │
│                                                                             │
│  ┌─────────────────────────────────────┐ ┌─────────────────────────────────┐
│  │ TOP TOOLS                           │ │ ACTIVITY                        │
│  │                                     │ │                                 │
│  │ search_code      ████████████ 45%  │ │  Mo Di Mi Do Fr Sa So          │
│  │ read_file        ████████░░░ 28%   │ │  ▁▂▃▅▇█▅▃▂▁▁▁                  │
│  │ query_database   █████░░░░░░ 15%   │ │  ▂▃▅▇█▇▅▃▂▁▁▁                  │
│  │ search_jira      ███░░░░░░░░  8%   │ │  ▁▂▃▅▇▅▃▂▁▁▁▁                  │
│  │ write_file       ██░░░░░░░░░  4%   │ │                                 │
│  └─────────────────────────────────────┘ └─────────────────────────────────┘
│                                                                             │
│  ┌─────────────────────────────────────┐ ┌─────────────────────────────────┐
│  │ RECENT ERRORS                       │ │ TOKEN USAGE                     │
│  │                                     │ │                                 │
│  │ ⚠ NullPointer in UserService (3x)  │ │      ┌─────┐                    │
│  │   Pattern Match: 85% confidence     │ │      │ 68% │  Input: 45K       │
│  │   [View Pattern] [Apply Fix]        │ │      │     │  Output: 23K      │
│  │                                     │ │      └─────┘  Total: 68K/100K  │
│  │ ⚠ DB Timeout (2x)                  │ │                                 │
│  │   No pattern found                  │ │                                 │
│  │   [Learn Pattern]                   │ │                                 │
│  └─────────────────────────────────────┘ └─────────────────────────────────┘
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.4 Chart Implementierung (Vanilla JS)

```javascript
class BarChart {
  constructor(container, data, options = {}) {
    this.container = container;
    this.data = data;
    this.maxValue = Math.max(...data.map(d => d.value));
  }

  render() {
    this.container.innerHTML = this.data.map(item => `
      <div class="bar-row">
        <span class="bar-label">${item.label}</span>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${(item.value / this.maxValue) * 100}%"></div>
        </div>
        <span class="bar-value">${item.value}%</span>
      </div>
    `).join('');
  }
}

class Heatmap {
  constructor(container, data) {
    this.container = container;
    this.data = data;  // { date, hour, count }[]
  }

  render() {
    const days = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
    const hours = Array.from({length: 24}, (_, i) => i);
    const maxCount = Math.max(...this.data.map(d => d.count));

    let html = '<div class="heatmap-grid">';
    days.forEach(day => {
      html += `<div class="heatmap-row">
        <span class="heatmap-day">${day}</span>
        ${hours.map(hour => {
          const entry = this.data.find(d => d.day === day && d.hour === hour);
          const intensity = entry ? entry.count / maxCount : 0;
          return `<div class="heatmap-cell" style="opacity: ${0.2 + intensity * 0.8}"></div>`;
        }).join('')}
      </div>`;
    });
    html += '</div>';
    this.container.innerHTML = html;
  }
}
```

---

## 8. Feature 6: Error Pattern Learning

### 8.1 Anforderungen

| ID | Anforderung | Priorität |
|----|-------------|-----------|
| EP-01 | Automatische Pattern-Extraktion bei Fehler | Must |
| EP-02 | Embedding-basierte Similarity | Must |
| EP-03 | Confidence Score berechnen | Must |
| EP-04 | User Feedback Loop | Must |
| EP-05 | Pattern Suggestion UI | Must |
| EP-06 | Pattern Decay (alte löschen) | Should |
| EP-07 | Pattern Export/Import | Could |

### 8.2 Datenmodell: ErrorPattern

```python
# app/services/pattern_learner.py

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
import hashlib

@dataclass
class ErrorPattern:
    """Ein gelerntes Fehler-Muster mit Lösung."""

    id: str
    created_at: datetime
    updated_at: datetime

    # Error Identification
    error_type: str              # "NullPointerException", "TypeError", etc.
    error_regex: str             # Regex für Stack-Trace Matching
    error_hash: str              # Hash für Quick-Lookup

    # Context Embedding (für Semantic Similarity)
    context_embedding: List[float]  # 384-dim Embedding
    context_keywords: List[str]     # Extracted keywords

    # File Context
    file_patterns: List[str]     # Glob patterns: ["*Service.java", "*Controller.java"]
    code_context: str            # Surrounding code snippet

    # Solution
    solution_description: str
    solution_steps: List[str]
    solution_code: Optional[str]
    tools_used: List[str]        # ["edit_file", "search_code"]
    files_changed: List[str]

    # Statistics
    times_seen: int = 0
    times_solved: int = 0
    times_suggested: int = 0
    times_accepted: int = 0
    times_rejected: int = 0

    # Confidence
    confidence: float = 0.5      # 0.0 - 1.0

    # User Feedback
    user_ratings: List[int] = field(default_factory=list)  # 1-5 stars

    @property
    def acceptance_rate(self) -> float:
        if self.times_suggested == 0:
            return 0.0
        return self.times_accepted / self.times_suggested

    @property
    def avg_rating(self) -> float:
        if not self.user_ratings:
            return 0.0
        return sum(self.user_ratings) / len(self.user_ratings)

    def update_confidence(self):
        """Berechnet Confidence basierend auf Statistiken."""
        # Basis: Acceptance Rate
        base = self.acceptance_rate * 0.4

        # Bonus: Anzahl erfolgreicher Lösungen
        solve_bonus = min(self.times_solved / 10, 0.3)

        # Bonus: User Ratings
        rating_bonus = (self.avg_rating / 5.0) * 0.2 if self.user_ratings else 0

        # Penalty: Alter (decay)
        days_old = (datetime.now() - self.updated_at).days
        decay = max(0, 1 - (days_old / 90))  # 90 Tage bis 0

        self.confidence = (base + solve_bonus + rating_bonus) * decay
        self.confidence = max(0.1, min(1.0, self.confidence))
```

### 8.3 Pattern Learning Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ERROR PATTERN LEARNING PIPELINE                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. CAPTURE                                                                 │
│     ┌──────────────┐                                                        │
│     │ Tool Error   │──► Extract: error_type, stack_trace, file_path        │
│     └──────────────┘                                                        │
│            │                                                                │
│            ▼                                                                │
│  2. NORMALIZE                                                               │
│     ┌──────────────┐                                                        │
│     │ Clean Stack  │──► Remove line numbers, timestamps, instance IDs      │
│     │ Generate Hash│──► MD5 of normalized pattern                          │
│     └──────────────┘                                                        │
│            │                                                                │
│            ▼                                                                │
│  3. CHECK EXISTING                                                          │
│     ┌──────────────┐    ┌─────────────┐                                    │
│     │ Hash Lookup  │───►│  MATCH?     │                                    │
│     └──────────────┘    └──────┬──────┘                                    │
│                                │                                            │
│            ┌───────────────────┼───────────────────┐                        │
│            ▼ NO                ▼ YES               │                        │
│  4a. EMBED                 4b. SUGGEST             │                        │
│     ┌──────────────┐       ┌──────────────┐        │                        │
│     │ Generate     │       │ Show Pattern │        │                        │
│     │ Embedding    │       │ Suggestion   │        │                        │
│     └──────────────┘       └──────────────┘        │                        │
│            │                     │                 │                        │
│            ▼                     ▼                 │                        │
│  5a. SIMILARITY SEARCH     5b. USER ACTION         │                        │
│     ┌──────────────┐       ┌──────────────┐        │                        │
│     │ Find Similar │       │ Apply/Reject │        │                        │
│     │ Patterns     │       │ Rate Solution│        │                        │
│     │ (cosine >0.8)│       └──────────────┘        │                        │
│     └──────────────┘              │                │                        │
│            │                      ▼                │                        │
│            ▼               6b. UPDATE STATS        │                        │
│  6a. CREATE NEW            ┌──────────────┐        │                        │
│     ┌──────────────┐       │ Increment    │        │                        │
│     │ New Pattern  │       │ Counters     │        │                        │
│     │ confidence=0.5│      │ Recalc Conf. │        │                        │
│     └──────────────┘       └──────────────┘        │                        │
│            │                      │                │                        │
│            └──────────────────────┴────────────────┘                        │
│                           │                                                 │
│                           ▼                                                 │
│  7. PERSIST                                                                 │
│     ┌──────────────┐                                                        │
│     │ SQLite DB    │                                                        │
│     │ patterns.db  │                                                        │
│     └──────────────┘                                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.4 Embedding Service

```python
# app/services/embedding_service.py

from typing import List
import hashlib

class EmbeddingService:
    """Generiert Embeddings für Error-Context."""

    def __init__(self, llm_client):
        self.llm_client = llm_client
        self._cache = {}

    async def embed_error(self, error_context: str) -> List[float]:
        """
        Generiert ein Embedding für den Error-Kontext.

        Nutzt das LLM für einfache Keyword-Extraktion und
        generiert ein Pseudo-Embedding basierend auf Keywords.
        (Für Production: echtes Embedding-Modell verwenden)
        """
        cache_key = hashlib.md5(error_context.encode()).hexdigest()

        if cache_key in self._cache:
            return self._cache[cache_key]

        # Keyword-Extraktion via LLM
        keywords = await self._extract_keywords(error_context)

        # Pseudo-Embedding (Bag of Words)
        embedding = self._keywords_to_embedding(keywords)

        self._cache[cache_key] = embedding
        return embedding

    async def _extract_keywords(self, text: str) -> List[str]:
        """Extrahiert relevante Keywords aus Error-Text."""
        prompt = f"""Extract 5-10 technical keywords from this error:

{text[:500]}

Return only keywords, one per line."""

        response = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        return [kw.strip().lower() for kw in response.split('\n') if kw.strip()]

    def _keywords_to_embedding(self, keywords: List[str]) -> List[float]:
        """Konvertiert Keywords zu einem 384-dim Pseudo-Embedding."""
        # Vereinfachtes Hashing-basiertes Embedding
        embedding = [0.0] * 384

        for kw in keywords:
            # Hash keyword to indices
            h = int(hashlib.md5(kw.encode()).hexdigest(), 16)
            for i in range(10):
                idx = (h + i * 37) % 384
                embedding[idx] += 1.0

        # Normalize
        magnitude = sum(x**2 for x in embedding) ** 0.5
        if magnitude > 0:
            embedding = [x / magnitude for x in embedding]

        return embedding

    def cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Berechnet Cosine Similarity zwischen zwei Embeddings."""
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x**2 for x in a) ** 0.5
        mag_b = sum(x**2 for x in b) ** 0.5

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)
```

### 8.5 Pattern Suggestion UI

```
┌─────────────────────────────────────────────────────────────────┐
│ 💡 KNOWN ERROR PATTERN DETECTED                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ Your error matches:                                             │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ NullPointerException in Service Layer                       │ │
│ │                                                             │ │
│ │ Confidence: ████████░░ 85%                                  │ │
│ │ Solved: 12 times | Acceptance: 86%                          │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ Suggested Fix:                                                  │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 1. Add null-check before calling .getId()                   │ │
│ │ 2. Consider using Optional.ofNullable()                     │ │
│ │                                                             │ │
│ │ Code Change:                                                │ │
│ │ ```java                                                     │ │
│ │ - user.getId()                                              │ │
│ │ + Optional.ofNullable(user)                                 │ │
│ │ +   .map(User::getId)                                       │ │
│ │ +   .orElseThrow(() -> new IllegalArgumentException(...))   │ │
│ │ ```                                                         │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌───────────────────┐  │
│ │ ✓ Apply │ │ ✗ Skip  │ │ 👁 Details  │ │ ⭐⭐⭐⭐☆ Rate    │  │
│ └─────────┘ └─────────┘ └─────────────┘ └───────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. API-Spezifikation

### 9.1 Workspace API

```yaml
# /api/workspace

GET /api/workspace/state/{session_id}
  description: Aktueller Workspace-State für Session
  response:
    visible: boolean
    width: number
    activeTab: string
    tabs:
      code: { items: CodeChange[], selected: string | null }
      sql: { items: SqlQuery[], selected: string | null }
      research: { items: ResearchResult[], selected: string | null }

POST /api/workspace/code/apply/{change_id}
  description: Code-Änderung anwenden
  response:
    success: boolean
    filePath: string
    message: string

POST /api/workspace/sql/execute
  description: SQL Query ausführen
  body:
    query: string
    database: string
  response:
    SqlQuery

GET /api/workspace/research/{session_id}
  description: Research-Ergebnisse für Session
  response:
    ResearchResult[]
```

### 9.2 Dashboard API

```yaml
# /api/dashboard

GET /api/dashboard/metrics
  query:
    timeRange: day | week | month
  response:
    DashboardMetrics

GET /api/dashboard/tool-usage
  query:
    timeRange: day | week | month
    limit: number (default: 10)
  response:
    ToolUsageEntry[]

GET /api/dashboard/activity
  query:
    days: number (default: 7)
  response:
    ActivityEntry[]

GET /api/dashboard/errors
  query:
    limit: number (default: 10)
  response:
    ErrorEntry[]
```

### 9.3 Pattern Learning API

```yaml
# /api/patterns

GET /api/patterns
  query:
    minConfidence: number (default: 0.5)
    limit: number (default: 50)
  response:
    ErrorPattern[]

GET /api/patterns/suggest
  body:
    errorType: string
    stackTrace: string
    fileContext: string
  response:
    pattern: ErrorPattern | null
    confidence: number
    alternatives: ErrorPattern[]

POST /api/patterns/learn
  body:
    errorType: string
    stackTrace: string
    solution: string
    toolsUsed: string[]
    filesChanged: string[]
  response:
    patternId: string
    isNew: boolean

POST /api/patterns/{pattern_id}/feedback
  body:
    accepted: boolean
    rating: number (1-5)
    comment: string?
  response:
    success: boolean
    newConfidence: number

DELETE /api/patterns/{pattern_id}
  response:
    success: boolean
```

---

## 10. Datenmodelle

### 10.1 SQLite Schema: patterns.db

```sql
-- Error Patterns
CREATE TABLE error_patterns (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Error Identification
    error_type TEXT NOT NULL,
    error_regex TEXT,
    error_hash TEXT UNIQUE NOT NULL,

    -- Context
    context_embedding BLOB,  -- JSON array of floats
    context_keywords TEXT,   -- JSON array of strings
    file_patterns TEXT,      -- JSON array of glob patterns
    code_context TEXT,

    -- Solution
    solution_description TEXT NOT NULL,
    solution_steps TEXT,     -- JSON array
    solution_code TEXT,
    tools_used TEXT,         -- JSON array
    files_changed TEXT,      -- JSON array

    -- Statistics
    times_seen INTEGER DEFAULT 0,
    times_solved INTEGER DEFAULT 0,
    times_suggested INTEGER DEFAULT 0,
    times_accepted INTEGER DEFAULT 0,
    times_rejected INTEGER DEFAULT 0,

    -- Confidence
    confidence REAL DEFAULT 0.5
);

CREATE INDEX idx_patterns_hash ON error_patterns(error_hash);
CREATE INDEX idx_patterns_type ON error_patterns(error_type);
CREATE INDEX idx_patterns_confidence ON error_patterns(confidence DESC);

-- User Feedback
CREATE TABLE pattern_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL REFERENCES error_patterns(id),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    accepted BOOLEAN NOT NULL,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    session_id TEXT
);

CREATE INDEX idx_feedback_pattern ON pattern_feedback(pattern_id);
```

### 10.2 SQLite Schema: analytics.db

```sql
-- Tool Usage Events
CREATE TABLE tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    duration_ms INTEGER,
    error_type TEXT,
    error_message TEXT
);

CREATE INDEX idx_events_timestamp ON tool_events(timestamp DESC);
CREATE INDEX idx_events_tool ON tool_events(tool_name);
CREATE INDEX idx_events_session ON tool_events(session_id);

-- Daily Aggregates (für schnelle Dashboard-Abfragen)
CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,  -- YYYY-MM-DD
    total_requests INTEGER DEFAULT 0,
    successful_requests INTEGER DEFAULT 0,
    total_duration_ms INTEGER DEFAULT 0,
    unique_sessions INTEGER DEFAULT 0,
    tool_usage TEXT  -- JSON: { "tool_name": count }
);

-- Hourly Activity (für Heatmap)
CREATE TABLE hourly_activity (
    date TEXT NOT NULL,     -- YYYY-MM-DD
    hour INTEGER NOT NULL,  -- 0-23
    count INTEGER DEFAULT 0,
    PRIMARY KEY (date, hour)
);
```

---

## 11. Implementierungsplan

### Phase 1: Foundation (1 Woche)

| Task | Beschreibung | Dateien |
|------|-------------|---------|
| 1.1 | Workspace Panel HTML/CSS | index.html, style.css |
| 1.2 | Workspace State Management | app.js |
| 1.3 | Tab Navigation | app.js |
| 1.4 | Resize Handle | style.css, app.js |
| 1.5 | SSE Event Handler erweitern | app.js |

### Phase 2: Code Split-View (1 Woche)

| Task | Beschreibung | Dateien |
|------|-------------|---------|
| 2.1 | diff2html Integration | index.html (CDN) |
| 2.2 | CodeChange Komponente | app.js |
| 2.3 | Apply/Reject Actions | app.js, agent.py |
| 2.4 | Orchestrator Events erweitern | orchestrator.py |

### Phase 3: SQL Split-View (1 Woche)

| Task | Beschreibung | Dateien |
|------|-------------|---------|
| 3.1 | Query Editor mit Highlighting | app.js |
| 3.2 | Result Table Komponente | app.js |
| 3.3 | Sortierung & Pagination | app.js |
| 3.4 | Export CSV/JSON | app.js |
| 3.5 | API Endpoint erweitern | database.py |

### Phase 4: Dashboard (1 Woche)

| Task | Beschreibung | Dateien |
|------|-------------|---------|
| 4.1 | Analytics DB Schema | analytics.db |
| 4.2 | AnalyticsService | analytics_service.py |
| 4.3 | Dashboard API | dashboard.py (neu) |
| 4.4 | Dashboard UI | app.js, style.css |
| 4.5 | Charts (Bar, Heatmap, Donut) | app.js |

### Phase 5: Error Pattern Learning (2 Wochen)

| Task | Beschreibung | Dateien |
|------|-------------|---------|
| 5.1 | Pattern DB Schema | patterns.db |
| 5.2 | EmbeddingService | embedding_service.py |
| 5.3 | PatternLearner | pattern_learner.py |
| 5.4 | Pattern API | patterns.py (neu) |
| 5.5 | Suggestion UI | app.js |
| 5.6 | Feedback Loop | app.js, patterns.py |
| 5.7 | Orchestrator Integration | orchestrator.py |

### Phase 6: Polish & Testing (1 Woche)

| Task | Beschreibung |
|------|-------------|
| 6.1 | E2E Tests für alle Features |
| 6.2 | Performance Optimierung |
| 6.3 | Keyboard Shortcuts |
| 6.4 | Documentation |

---

## Appendix

### A. CSS für neue Komponenten

```css
/* Workspace Panel */
.workspace-panel {
  position: fixed;
  right: 0;
  top: var(--header-h);
  bottom: 0;
  width: var(--workspace-w);
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transition: width 0.2s ease;
}

.workspace-panel.collapsed {
  width: var(--workspace-collapsed-w);
}

.workspace-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  padding: 0 8px;
}

.workspace-tab {
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  display: flex;
  align-items: center;
  gap: 8px;
}

.workspace-tab.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
}

.workspace-tab .badge {
  background: var(--accent-bg);
  color: var(--accent);
  padding: 2px 6px;
  border-radius: 10px;
  font-size: 0.75rem;
}

/* Code Diff */
.code-diff-container {
  flex: 1;
  overflow: auto;
}

.d2h-file-wrapper {
  border: none;
  background: var(--bg);
}

.d2h-code-line {
  font-family: var(--font-mono);
  font-size: 0.85rem;
}

/* SQL Result Table */
.sql-result-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.sql-result-table th {
  background: var(--surface);
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}

.sql-result-table th.sortable:hover {
  background: var(--surface-hover);
}

.sql-result-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}

.sql-result-table tr:hover {
  background: var(--surface-hover);
}

/* Dashboard */
.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  padding: 16px;
}

.kpi-card {
  background: var(--surface);
  border-radius: 8px;
  padding: 20px;
  text-align: center;
}

.kpi-value {
  font-size: 2rem;
  font-weight: 600;
}

.kpi-trend {
  font-size: 0.85rem;
  margin-top: 8px;
}

.kpi-trend.positive { color: var(--success); }
.kpi-trend.negative { color: var(--danger); }

/* Pattern Suggestion */
.pattern-suggestion {
  background: var(--warning-bg);
  border: 1px solid var(--warning);
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
}

.pattern-confidence {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0;
}

.confidence-bar {
  flex: 1;
  height: 8px;
  background: var(--surface);
  border-radius: 4px;
  overflow: hidden;
}

.confidence-fill {
  height: 100%;
  background: var(--success);
  transition: width 0.3s ease;
}
```

### B. Event-Typen (Erweiterung)

```python
# app/agent/orchestrator.py

class AgentEventType(str, Enum):
    # Existing...

    # Workspace Events (NEU)
    WORKSPACE_CODE_CHANGE = "workspace_code_change"
    WORKSPACE_SQL_RESULT = "workspace_sql_result"
    WORKSPACE_RESEARCH = "workspace_research"

    # Pattern Events (NEU)
    PATTERN_MATCH = "pattern_match"
    PATTERN_LEARNED = "pattern_learned"
```

---

**Ende des Design-Dokuments**
