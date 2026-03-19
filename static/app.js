// ══════════════════════════════════════════════════════════════════════════════
// AI Code Assistant - Frontend Application
// Agent-basierte Architektur mit Tool-Calling und Bestätigungs-Workflow
// ══════════════════════════════════════════════════════════════════════════════

// ── Debug Logger (toggle DEBUG to enable/disable console output) ──
const DEBUG = false;
const log = {
  info: DEBUG ? console.log.bind(console) : () => {},
  warn: DEBUG ? console.warn.bind(console) : () => {},
  error: console.error.bind(console),  // Always log errors
};

// ── Timing Constants (ms) ──
const TIMING = {
  TOAST_DEFAULT: 3000,
  TOAST_ERROR: 5000,
  DEBOUNCE: 300,
  ANIMATION_FAST: 200,
  ANIMATION_SLOW: 500,
  HIGHLIGHT_DURATION: 2000,
  POLL_INTERVAL: 5000,
  SCROLL_DELAY: 100,
};

// ── Focus Trap for Modals (Accessibility) ──
const focusTrap = {
  _activeModal: null,
  _previousFocus: null,
  _boundHandler: null,

  /**
   * Aktiviert Focus-Trap für ein Modal
   * @param {HTMLElement} modal - Das Modal-Element
   */
  activate(modal) {
    if (this._activeModal) this.deactivate();
    this._activeModal = modal;
    this._previousFocus = document.activeElement;

    // Fokussierbare Elemente im Modal finden
    const focusableSelector = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
    const focusables = Array.from(modal.querySelectorAll(focusableSelector))
      .filter(el => !el.disabled && el.offsetParent !== null);

    if (focusables.length === 0) return;

    // Erstes Element fokussieren
    focusables[0].focus();

    // Tab-Trap Handler
    this._boundHandler = (e) => {
      if (e.key !== 'Tab') return;
      const currentFocusables = Array.from(modal.querySelectorAll(focusableSelector))
        .filter(el => !el.disabled && el.offsetParent !== null);
      if (currentFocusables.length === 0) return;

      const first = currentFocusables[0];
      const last = currentFocusables[currentFocusables.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', this._boundHandler);
  },

  /**
   * Deaktiviert Focus-Trap und stellt vorherigen Fokus wieder her
   */
  deactivate() {
    if (this._boundHandler) {
      document.removeEventListener('keydown', this._boundHandler);
      this._boundHandler = null;
    }
    if (this._previousFocus && this._previousFocus.focus) {
      this._previousFocus.focus();
    }
    this._activeModal = null;
    this._previousFocus = null;
  }
};

// ── DOM Helper for efficient element creation ──
const dom = {
  /**
   * Erstellt ein Element mit Attributen und Kindern
   * @param {string} tag - Tag-Name
   * @param {Object} attrs - Attribute (class, id, data-*, onclick, etc.)
   * @param {...(string|Node)} children - Text oder Child-Nodes
   * @returns {HTMLElement}
   */
  el(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class') el.className = value;
      else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
      else if (key.startsWith('on') && typeof value === 'function') el[key] = value;
      else if (key.startsWith('data-')) el.setAttribute(key, value);
      else if (value !== null && value !== undefined) el.setAttribute(key, value);
    }
    for (const child of children) {
      if (child == null) continue;
      el.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return el;
  },

  /**
   * Ersetzt innerHTML effizient für Listen
   * @param {HTMLElement} container - Container-Element
   * @param {Array} items - Array von Elementen oder HTML-Strings
   */
  replaceChildren(container, items) {
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      if (typeof item === 'string') {
        const temp = document.createElement('div');
        temp.innerHTML = item;
        while (temp.firstChild) fragment.appendChild(temp.firstChild);
      } else if (item instanceof Node) {
        fragment.appendChild(item);
      }
    }
    container.innerHTML = '';
    container.appendChild(fragment);
  }
};

// ── File Cache for @-Mention and Explorer Search ──
const fileCache = {
  java: { files: [], timestamp: 0 },
  python: { files: [], timestamp: 0 },
  TTL: 5 * 60 * 1000,  // 5 minutes
};

// ── @-Mention State ──
const mentionState = {
  active: false,
  query: '',
  cursorPosition: 0,
  selectedIndex: 0,      // Currently highlighted item
  selectedFiles: [],     // Multi-selected files (spacebar)
  results: [],
  triggerPosition: 0,    // Position of @ in text
};

// ── State ──
const state = {
  sessionId: null,         // Set by active chat
  currentModel: null,
  mode: 'read_only',
  activeSkills: [],
  availableSkills: [],
  pendingConfirmation: null,
  toolHistory: [],
  context: {
    javaFiles: [],
    pythonFiles: [],
    pdfIds: [],
    handbookServices: [],
  },
  // Streaming-State ist jetzt pro-Chat in chat.streamingState
};

// ── MultiChat State ──
const chatManager = {
  chats: [],       // Array of chat objects
  activeId: null,  // ID of currently shown chat

  createChat(sessionId, title = 'Neuer Chat') {
    const id = crypto.randomUUID();
    // Jeder Chat bekommt ein eigenes, persistentes DOM-Pane.
    // Es wird nie als innerHTML serialisiert – stattdessen per removeChild/appendChild
    // in und aus #messages getauscht. Laufende Stream-Referenzen (bubble, statusBar)
    // bleiben dadurch immer gültig, auch wenn der Chat im Hintergrund läuft.
    const pane = document.createElement('div');
    pane.className = 'messages-pane';
    // Context bar + welcome content - context bar is sticky at top of pane
    pane.innerHTML = _contextBarHTML() + welcomeHTML();
    const chat = {
      id,
      sessionId,
      title,
      pane,
      // streamingState ist null wenn idle, sonst:
      // { abortController, statusBar, startTime, liveTokenCount, timerInterval }
      streamingState: null,
      toolHistory: [],
      context: { javaFiles: [], pythonFiles: [], pdfIds: [], handbookServices: [] },
      pendingConfirmation: null,
      pendingEnhancement: null,  // MCP context enhancement state
      contextStatus: null,  // Per-chat context/token status
      createdAt: Date.now(),
    };
    this.chats.push(chat);
    return chat;
  },

  getActive() {
    return this.chats.find(c => c.id === this.activeId) || null;
  },

  get(chatId) {
    return this.chats.find(c => c.id === chatId) || null;
  },

  remove(chatId) {
    this.chats = this.chats.filter(c => c.id !== chatId);
  },

  // Speichert nicht-DOM-State des aktiven Chats zurück ins Chat-Objekt
  saveActiveState() {
    const chat = this.getActive();
    if (!chat) return;
    chat.toolHistory = [...state.toolHistory];
    // structuredClone is faster than JSON.parse/stringify for deep cloning
    chat.context = typeof structuredClone === 'function'
      ? structuredClone(state.context)
      : JSON.parse(JSON.stringify(state.context));
    chat.pendingConfirmation = state.pendingConfirmation;
    chat.mode = state.mode;  // Mode speichern
  },
};

// ══════════════════════════════════════════════════════════════════════════════
// Workspace Panel State & Management
// ══════════════════════════════════════════════════════════════════════════════

const workspaceState = {
  visible: false,
  width: 500,
  activeTab: 'code',
  collapsed: false,
  tabs: {
    code: { items: [], selected: null },
    sql: { items: [], selected: null },
    research: { items: [], selected: null },
    files: { items: [], selected: null }
  }
};

// Workspace Panel Functions
function toggleWorkspace() {
  const panel = document.getElementById('workspace-panel');
  const btn = document.getElementById('workspace-btn');

  workspaceState.visible = !workspaceState.visible;

  if (workspaceState.visible) {
    panel.style.display = 'flex';
    btn.classList.add('active');
    // Restore width from state
    panel.style.width = workspaceState.width + 'px';
  } else {
    panel.style.display = 'none';
    btn.classList.remove('active');
  }

  // Adjust main layout
  updateMainLayout();
}

function closeWorkspace() {
  workspaceState.visible = false;
  document.getElementById('workspace-panel').style.display = 'none';
  document.getElementById('workspace-btn').classList.remove('active');
  updateMainLayout();
}

function toggleWorkspaceCollapse() {
  const panel = document.getElementById('workspace-panel');
  const collapseBtn = document.getElementById('workspace-collapse-btn');

  workspaceState.collapsed = !workspaceState.collapsed;

  if (workspaceState.collapsed) {
    panel.classList.add('collapsed');
    collapseBtn.innerHTML = '&#9744;'; // Expand icon
    collapseBtn.title = 'Maximieren';
  } else {
    panel.classList.remove('collapsed');
    collapseBtn.innerHTML = '&#9866;'; // Minimize icon
    collapseBtn.title = 'Minimieren';
  }

  updateMainLayout();
}

function switchWorkspaceTab(tabName) {
  workspaceState.activeTab = tabName;

  // Update tab buttons
  document.querySelectorAll('.workspace-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.tab === tabName);
  });

  // Update tab content
  document.querySelectorAll('.workspace-tab-content').forEach(content => {
    content.classList.toggle('active', content.id === `workspace-${tabName}-content`);
  });
}

function updateWorkspaceBadges() {
  const tabs = ['code', 'sql', 'research'];
  tabs.forEach(tab => {
    const count = workspaceState.tabs[tab].items.length;
    const badge = document.getElementById(`workspace-${tab}-badge`);
    if (badge) {
      badge.textContent = count;
      badge.style.display = count > 0 ? 'inline-block' : 'none';
    }
  });

  // Update total item count
  const totalItems = Object.values(workspaceState.tabs).reduce((sum, t) => sum + t.items.length, 0);
  document.getElementById('workspace-item-count').textContent = `${totalItems} Items`;
}

function clearWorkspace() {
  workspaceState.tabs = {
    code: { items: [], selected: null },
    sql: { items: [], selected: null },
    research: { items: [], selected: null },
    files: { items: [], selected: null }
  };
  renderWorkspaceTab('code');
  renderWorkspaceTab('sql');
  renderWorkspaceTab('research');
  renderWorkspaceTab('files');
  updateWorkspaceBadges();
}

function exportWorkspace() {
  const exportData = {
    timestamp: new Date().toISOString(),
    sessionId: chatManager.getActive()?.sessionId,
    tabs: workspaceState.tabs
  };

  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `workspace_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function updateMainLayout() {
  // This function can be extended to adjust the main chat area width
  // when workspace panel is shown/hidden
}

// ── Workspace Resize Handle ──
function setupWorkspaceResize() {
  const handle = document.getElementById('workspace-resize-handle');
  const panel = document.getElementById('workspace-panel');

  if (!handle || !panel) return;

  let isResizing = false;
  let startX = 0;
  let startWidth = 0;

  handle.addEventListener('mousedown', (e) => {
    isResizing = true;
    startX = e.clientX;
    startWidth = panel.offsetWidth;
    handle.classList.add('active');
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;

    const deltaX = startX - e.clientX;
    let newWidth = startWidth + deltaX;

    // Enforce min/max constraints
    const minWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--workspace-min-w')) || 400;
    const maxWidth = window.innerWidth * 0.6;

    newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));

    panel.style.width = newWidth + 'px';
    workspaceState.width = newWidth;
  });

  document.addEventListener('mouseup', () => {
    if (isResizing) {
      isResizing = false;
      handle.classList.remove('active');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
}

// ── Workspace Content Rendering ──
function renderWorkspaceTab(tabName) {
  const items = workspaceState.tabs[tabName].items;
  const container = document.getElementById(`workspace-${tabName}-items`);
  const emptyState = document.getElementById(`workspace-${tabName}-empty`);

  if (!container) return;

  if (items.length === 0) {
    container.innerHTML = '';
    if (emptyState) emptyState.style.display = 'flex';
    return;
  }

  if (emptyState) emptyState.style.display = 'none';

  switch (tabName) {
    case 'code':
      container.innerHTML = items.map(item => renderCodeItem(item)).join('');
      break;
    case 'sql':
      container.innerHTML = items.map(item => renderSqlItem(item)).join('');
      break;
    case 'research':
      container.innerHTML = renderResearchItems(items);
      break;
    case 'files':
      container.innerHTML = items.map(item => renderFileItem(item)).join('');
      break;
  }
}

function renderCodeItem(item) {
  const statusClass = item.status || 'pending';
  const statusLabels = { pending: 'Ausstehend', applied: 'Angewendet', rejected: 'Abgelehnt' };
  const isExpanded = item.expanded || false;
  const viewMode = item.viewMode || 'split'; // 'split' or 'unified'

  // Calculate diff stats
  const diffStats = calculateDiffStats(item.diff || '');

  return `
    <div class="workspace-item ${item.id === workspaceState.tabs.code.selected ? 'selected' : ''} ${isExpanded ? 'expanded' : ''}"
         data-id="${item.id}">
      <div class="workspace-item-header" onclick="selectWorkspaceItem('code', '${item.id}')">
        <div class="workspace-item-title">
          <span class="file-icon">${getLanguageIcon(item.language)}</span>
          <span>${escapeHtml(item.fileName || item.filePath)}</span>
        </div>
        <span class="item-status ${statusClass}">${statusLabels[statusClass]}</span>
      </div>

      <div class="code-diff-toolbar">
        <div class="code-diff-toolbar-left">
          <span class="workspace-item-meta">
            ${item.description ? escapeHtml(item.description.substring(0, 60)) + (item.description.length > 60 ? '...' : '') : item.toolCall || 'edit_file'}
          </span>
        </div>
        <div class="code-diff-toolbar-right">
          <div class="diff-stats">
            <span class="additions">+${diffStats.additions}</span>
            <span class="deletions">-${diffStats.deletions}</span>
          </div>
          <div class="diff-view-toggle">
            <button class="${viewMode === 'split' ? 'active' : ''}"
                    onclick="setCodeDiffViewMode('${item.id}', 'split'); event.stopPropagation();"
                    title="Side-by-Side">Split</button>
            <button class="${viewMode === 'unified' ? 'active' : ''}"
                    onclick="setCodeDiffViewMode('${item.id}', 'unified'); event.stopPropagation();"
                    title="Unified">Unified</button>
          </div>
          <button class="btn btn-sm btn-secondary" onclick="copyDiffToClipboard('${item.id}'); event.stopPropagation();" title="Diff kopieren">
            &#128203;
          </button>
          <button class="btn btn-sm btn-secondary" onclick="toggleCodeItemExpand('${item.id}'); event.stopPropagation();" title="${isExpanded ? 'Minimieren' : 'Expandieren'}">
            ${isExpanded ? '&#9660;' : '&#9654;'}
          </button>
        </div>
      </div>

      <div class="workspace-item-content">
        <div class="diff-container" id="diff-container-${item.id}">
          ${renderDiff2Html(item.diff || '', item.fileName || item.filePath, viewMode)}
        </div>
      </div>

      <div class="workspace-item-actions" style="padding: 8px 12px; border-top: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
        <div>
          ${statusClass === 'pending' ? `
            <button class="btn btn-sm btn-success" onclick="applyCodeChange('${item.id}'); event.stopPropagation();">
              &#10003; Anwenden
            </button>
            <button class="btn btn-sm btn-danger" onclick="rejectCodeChange('${item.id}'); event.stopPropagation();">
              &#10005; Ablehnen
            </button>
          ` : `<span style="font-size: 0.75rem; color: var(--text-muted);">${statusClass === 'applied' ? 'Angewendet' : 'Abgelehnt'}</span>`}
        </div>
        <div style="font-size: 0.75rem; color: var(--text-muted);">
          ${item.language ? item.language.toUpperCase() : ''}
        </div>
      </div>
    </div>
  `;
}

// Render diff using diff2html library
function renderDiff2Html(diffString, fileName, viewMode = 'split') {
  if (!diffString) {
    return '<div style="padding: 20px; text-align: center; color: var(--text-muted);">Kein Diff verfügbar</div>';
  }

  // Check if diff2html is available
  if (typeof Diff2Html === 'undefined') {
    log.warn('diff2html not loaded, falling back to simple diff');
    return `<pre class="workspace-diff">${formatDiff(diffString)}</pre>`;
  }

  try {
    const outputFormat = viewMode === 'split' ? 'side-by-side' : 'line-by-line';

    const html = Diff2Html.html(diffString, {
      outputFormat: outputFormat,
      drawFileList: false,
      matching: 'lines',
      matchWordsThreshold: 0.25,
      diffStyle: 'word',
      renderNothingWhenEmpty: false,
      rawTemplates: {}
    });

    return html;
  } catch (e) {
    log.error('diff2html rendering failed:', e);
    return `<pre class="workspace-diff">${formatDiff(diffString)}</pre>`;
  }
}

// Calculate additions and deletions from diff
function calculateDiffStats(diffString) {
  if (!diffString) return { additions: 0, deletions: 0 };

  let additions = 0;
  let deletions = 0;

  const lines = diffString.split('\n');
  for (const line of lines) {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      additions++;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      deletions++;
    }
  }

  return { additions, deletions };
}

// Get language icon
function getLanguageIcon(language) {
  const icons = {
    java: '&#9749;',      // Coffee cup
    python: '&#128013;',  // Snake
    javascript: '&#128312;', // Yellow circle
    typescript: '&#128309;', // Blue circle
    sql: '&#128202;',     // Chart
    xml: '&#128196;',     // Document
    json: '&#123;&#125;', // Braces
    yaml: '&#128203;',    // Clipboard
    html: '&#127760;',    // Globe
    css: '&#127912;',     // Palette
    bash: '&#128187;',    // Computer
    markdown: '&#128221;' // Memo
  };
  return icons[language] || '&#128196;';
}

// Set diff view mode (split/unified)
function setCodeDiffViewMode(itemId, mode) {
  const item = workspaceState.tabs.code.items.find(i => i.id === itemId);
  if (!item) return;

  item.viewMode = mode;
  renderWorkspaceTab('code');
}

// Toggle code item expand
function toggleCodeItemExpand(itemId) {
  const item = workspaceState.tabs.code.items.find(i => i.id === itemId);
  if (!item) return;

  item.expanded = !item.expanded;
  renderWorkspaceTab('code');
}

// Copy diff to clipboard
function copyDiffToClipboard(itemId) {
  const item = workspaceState.tabs.code.items.find(i => i.id === itemId);
  if (!item || !item.diff) {
    showToast('Kein Diff zum Kopieren', 'warning');
    return;
  }

  navigator.clipboard.writeText(item.diff).then(() => {
    showToast('Diff kopiert', 'success');
  }).catch(err => {
    log.error('Failed to copy diff:', err);
    showToast('Kopieren fehlgeschlagen', 'error');
  });
}

function renderSqlItem(item) {
  const hasError = !!item.error;
  const rowCount = item.rowCount || (item.rows ? item.rows.length : 0);
  const execTime = item.executionTimeMs || 0;
  const isExpanded = item.expanded || false;

  // Initialize item state if needed
  if (!item.sortColumn) item.sortColumn = null;
  if (!item.sortDirection) item.sortDirection = 'asc';
  if (!item.page) item.page = 1;
  if (!item.pageSize) item.pageSize = 25;
  if (!item.visibleColumns) {
    item.visibleColumns = (item.columns || []).map(c => c.name || c);
  }

  return `
    <div class="workspace-item sql-item ${item.id === workspaceState.tabs.sql.selected ? 'selected' : ''} ${isExpanded ? 'expanded' : ''}"
         data-id="${item.id}">
      <div class="workspace-item-header" onclick="selectWorkspaceItem('sql', '${item.id}')">
        <div class="workspace-item-title">
          <span class="file-icon">&#128202;</span>
          <span>Query ${item.id ? item.id.substring(0, 8) : 'N/A'}</span>
        </div>
        <span class="workspace-item-meta">
          ${hasError
            ? `<span style="color:var(--danger)">&#10005; Error</span>`
            : `<span style="color:var(--success)">&#10003;</span> ${rowCount} Zeilen in ${execTime}ms`}
        </span>
      </div>

      <div class="sql-query-toolbar">
        <div class="sql-query-toolbar-left">
          <span class="sql-db-badge">${escapeHtml(item.database || 'DB2')}</span>
          ${item.schema ? `<span class="sql-schema-badge">${escapeHtml(item.schema)}</span>` : ''}
          ${item.truncated ? `<span class="sql-truncated-badge" title="Ergebnis wurde abgeschnitten">&#9888; Truncated</span>` : ''}
        </div>
        <div class="sql-query-toolbar-right">
          <button class="btn btn-sm btn-secondary" onclick="copySqlToClipboard('${item.id}'); event.stopPropagation();" title="SQL kopieren">
            &#128203; Copy SQL
          </button>
          <button class="btn btn-sm btn-secondary" onclick="toggleSqlItemExpand('${item.id}'); event.stopPropagation();" title="${isExpanded ? 'Minimieren' : 'Expandieren'}">
            ${isExpanded ? '&#9660;' : '&#9654;'}
          </button>
        </div>
      </div>

      <div class="sql-query-editor">
        <pre><code class="language-sql">${escapeHtml(item.query || '')}</code></pre>
      </div>

      <div class="workspace-item-content">
        ${hasError
          ? `<div class="sql-error-message">&#10005; ${escapeHtml(item.error)}</div>`
          : renderSqlResultTable(item)
        }
      </div>
    </div>
  `;
}

function renderSqlResultTable(item) {
  if (!item.columns || item.columns.length === 0 || !item.rows || item.rows.length === 0) {
    return '<div class="sql-no-results">Keine Ergebnisse</div>';
  }

  const columns = item.columns.map(c => typeof c === 'string' ? { name: c, visible: true } : c);
  const visibleCols = columns.filter(c => item.visibleColumns.includes(c.name));

  // Sort rows if needed
  let sortedRows = [...item.rows];
  if (item.sortColumn !== null) {
    const colIndex = columns.findIndex(c => c.name === item.sortColumn);
    if (colIndex >= 0) {
      sortedRows.sort((a, b) => {
        const aVal = Array.isArray(a) ? a[colIndex] : a[item.sortColumn];
        const bVal = Array.isArray(b) ? b[colIndex] : b[item.sortColumn];
        if (aVal === null) return 1;
        if (bVal === null) return -1;
        if (typeof aVal === 'number' && typeof bVal === 'number') {
          return item.sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
        }
        const aStr = String(aVal).toLowerCase();
        const bStr = String(bVal).toLowerCase();
        return item.sortDirection === 'asc' ? aStr.localeCompare(bStr) : bStr.localeCompare(aStr);
      });
    }
  }

  // Pagination
  const pageSize = item.pageSize || 25;
  const page = item.page || 1;
  const totalPages = Math.ceil(sortedRows.length / pageSize);
  const startIdx = (page - 1) * pageSize;
  const pageRows = sortedRows.slice(startIdx, startIdx + pageSize);

  return `
    <div class="sql-result-header">
      <span class="sql-result-info">
        ${item.rowCount || sortedRows.length} Zeilen${item.executionTimeMs ? ` in ${item.executionTimeMs}ms` : ''}
      </span>
      <div class="sql-result-actions">
        <div class="sql-columns-dropdown">
          <button class="btn btn-sm btn-secondary" onclick="toggleSqlColumnsDropdown('${item.id}'); event.stopPropagation();">
            Spalten &#9660;
          </button>
          <div class="sql-columns-menu" id="sql-columns-menu-${item.id}" style="display: none;">
            ${columns.map(col => `
              <label onclick="event.stopPropagation();">
                <input type="checkbox" ${item.visibleColumns.includes(col.name) ? 'checked' : ''}
                       onchange="toggleSqlColumn('${item.id}', '${col.name}', this.checked)">
                ${escapeHtml(col.name)}
              </label>
            `).join('')}
          </div>
        </div>
        <button class="btn btn-sm btn-secondary" onclick="exportSqlResult('${item.id}', 'csv'); event.stopPropagation();">
          &#128190; CSV
        </button>
        <button class="btn btn-sm btn-secondary" onclick="exportSqlResult('${item.id}', 'json'); event.stopPropagation();">
          &#123;&#125; JSON
        </button>
      </div>
    </div>
    <div class="sql-result-table-wrapper">
      <table class="sql-result-table">
        <thead>
          <tr>
            <th class="row-num">#</th>
            ${visibleCols.map(col => `
              <th class="sortable ${item.sortColumn === col.name ? item.sortDirection : ''}"
                  onclick="sortSqlResult('${item.id}', '${col.name}'); event.stopPropagation();">
                ${escapeHtml(col.name)}
                <span class="sort-icon">${item.sortColumn === col.name ? (item.sortDirection === 'asc' ? '&#9650;' : '&#9660;') : ''}</span>
              </th>
            `).join('')}
          </tr>
        </thead>
        <tbody>
          ${pageRows.map((row, idx) => {
            const rowData = Array.isArray(row) ? row : Object.values(row);
            const visibleColIndices = visibleCols.map(vc => columns.findIndex(c => c.name === vc.name));
            return `
              <tr>
                <td class="row-num">${startIdx + idx + 1}</td>
                ${visibleColIndices.map(colIdx => {
                  const cell = rowData[colIdx];
                  const cellClass = cell === null ? 'cell-null' : typeof cell === 'number' ? 'cell-number' : '';
                  const cellValue = cell === null ? 'NULL' : escapeHtml(String(cell));
                  return `<td class="${cellClass}">${cellValue}</td>`;
                }).join('')}
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    </div>
    ${totalPages > 1 ? `
      <div class="sql-pagination">
        <button class="btn btn-sm" ${page <= 1 ? 'disabled' : ''} onclick="setSqlPage('${item.id}', ${page - 1}); event.stopPropagation();">
          &#9664; Prev
        </button>
        <span class="sql-page-info">Seite ${page} von ${totalPages}</span>
        <button class="btn btn-sm" ${page >= totalPages ? 'disabled' : ''} onclick="setSqlPage('${item.id}', ${page + 1}); event.stopPropagation();">
          Next &#9654;
        </button>
        <select class="sql-page-size" onchange="setSqlPageSize('${item.id}', parseInt(this.value)); event.stopPropagation();">
          <option value="10" ${pageSize === 10 ? 'selected' : ''}>10</option>
          <option value="25" ${pageSize === 25 ? 'selected' : ''}>25</option>
          <option value="50" ${pageSize === 50 ? 'selected' : ''}>50</option>
          <option value="100" ${pageSize === 100 ? 'selected' : ''}>100</option>
        </select>
        <span>pro Seite</span>
      </div>
    ` : ''}
  `;
}

// SQL Table Helper Functions
function sortSqlResult(itemId, columnName) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item) return;

  if (item.sortColumn === columnName) {
    item.sortDirection = item.sortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    item.sortColumn = columnName;
    item.sortDirection = 'asc';
  }
  item.page = 1; // Reset to first page on sort
  renderWorkspaceTab('sql');
}

function setSqlPage(itemId, page) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item) return;
  item.page = Math.max(1, page);
  renderWorkspaceTab('sql');
}

function setSqlPageSize(itemId, pageSize) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item) return;
  item.pageSize = pageSize;
  item.page = 1; // Reset to first page
  renderWorkspaceTab('sql');
}

function toggleSqlColumn(itemId, columnName, visible) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item) return;

  if (!item.visibleColumns) {
    item.visibleColumns = (item.columns || []).map(c => c.name || c);
  }

  if (visible) {
    if (!item.visibleColumns.includes(columnName)) {
      item.visibleColumns.push(columnName);
    }
  } else {
    item.visibleColumns = item.visibleColumns.filter(c => c !== columnName);
  }
  renderWorkspaceTab('sql');
}

function toggleSqlColumnsDropdown(itemId) {
  const menu = document.getElementById(`sql-columns-menu-${itemId}`);
  if (menu) {
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
  }
}

function exportSqlResult(itemId, format) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item || !item.rows || item.rows.length === 0) {
    showToast('Keine Daten zum Exportieren', 'warning');
    return;
  }

  const columns = (item.columns || []).map(c => c.name || c);
  let data;

  if (format === 'csv') {
    // CSV export
    const header = columns.join(',');
    const rows = item.rows.map(row => {
      const rowData = Array.isArray(row) ? row : Object.values(row);
      return rowData.map(cell => {
        if (cell === null) return '';
        const str = String(cell);
        // Escape quotes and wrap in quotes if contains comma or quote
        if (str.includes(',') || str.includes('"') || str.includes('\n')) {
          return '"' + str.replace(/"/g, '""') + '"';
        }
        return str;
      }).join(',');
    });
    data = header + '\n' + rows.join('\n');
  } else {
    // JSON export
    const jsonRows = item.rows.map(row => {
      const rowData = Array.isArray(row) ? row : Object.values(row);
      const obj = {};
      columns.forEach((col, idx) => {
        obj[col] = rowData[idx];
      });
      return obj;
    });
    data = JSON.stringify(jsonRows, null, 2);
  }

  // Download
  const blob = new Blob([data], { type: format === 'csv' ? 'text/csv' : 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `query_result_${itemId.substring(0, 8)}_${Date.now()}.${format}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  showToast(`${format.toUpperCase()} exportiert`, 'success');
}

function copySqlToClipboard(itemId) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item || !item.query) {
    showToast('Keine SQL-Abfrage zum Kopieren', 'warning');
    return;
  }

  navigator.clipboard.writeText(item.query).then(() => {
    showToast('SQL kopiert', 'success');
  }).catch(err => {
    log.error('Failed to copy SQL:', err);
    showToast('Kopieren fehlgeschlagen', 'error');
  });
}

function toggleSqlItemExpand(itemId) {
  const item = workspaceState.tabs.sql.items.find(i => i.id === itemId);
  if (!item) return;
  item.expanded = !item.expanded;
  renderWorkspaceTab('sql');
}

function renderResearchItems(items) {
  // Group by source type
  const groups = {};
  items.forEach(item => {
    const source = item.source || 'other';
    if (!groups[source]) groups[source] = [];
    groups[source].push(item);
  });

  const sourceIcons = {
    web: '&#127760;',
    code: '&#128196;',
    wiki: '&#128214;',
    handbook: '&#128218;',
    pdf: '&#128196;',
    other: '&#128269;'
  };

  const sourceLabels = {
    web: 'Web-Suche',
    code: 'Code-Suche',
    wiki: 'Wiki/Confluence',
    handbook: 'Handbuch',
    pdf: 'PDF-Dokumente',
    other: 'Andere'
  };

  return Object.entries(groups).map(([source, sourceItems]) => `
    <div class="workspace-research-group">
      <div class="workspace-research-group-header" onclick="toggleResearchGroup('${source}')">
        <span class="group-icon">${sourceIcons[source] || sourceIcons.other}</span>
        <span>${sourceLabels[source] || source}</span>
        <span class="group-count">${sourceItems.length} Ergebnisse</span>
      </div>
      <div class="workspace-research-items" id="research-group-${source}">
        ${sourceItems.map(item => `
          <div class="workspace-research-item">
            <div class="workspace-research-item-title">${escapeHtml(item.title || 'Untitled')}</div>
            <div class="workspace-research-item-snippet">${escapeHtml(item.snippet || item.content || '')}</div>
            ${item.url ? `<div class="workspace-research-item-source"><a href="${item.url}" target="_blank">${escapeHtml(item.url)}</a></div>` : ''}
            ${item.filePath ? `<div class="workspace-research-item-source">${escapeHtml(item.filePath)}${item.lineNumber ? ':' + item.lineNumber : ''}</div>` : ''}
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

function renderFileItem(item) {
  return `
    <div class="workspace-item" data-id="${item.id}">
      <div class="workspace-item-header">
        <div class="workspace-item-title">
          <span class="file-icon">&#128193;</span>
          <span>${escapeHtml(item.filePath)}</span>
        </div>
        <span class="workspace-item-meta">${item.operation || 'read'}</span>
      </div>
    </div>
  `;
}

function toggleResearchGroup(source) {
  const group = document.getElementById(`research-group-${source}`);
  if (group) {
    group.style.display = group.style.display === 'none' ? 'block' : 'none';
  }
}

function selectWorkspaceItem(tabName, itemId) {
  workspaceState.tabs[tabName].selected = itemId;
  renderWorkspaceTab(tabName);
}

function formatDiff(diff) {
  if (!diff) return '';
  return diff.split('\n').map(line => {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      return `<span class="diff-add">${escapeHtml(line)}</span>`;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      return `<span class="diff-remove">${escapeHtml(line)}</span>`;
    } else if (line.startsWith('@@')) {
      return `<span class="diff-header">${escapeHtml(line)}</span>`;
    }
    return escapeHtml(line);
  }).join('\n');
}

// ── Workspace Event Handlers ──
function addCodeChangeToWorkspace(data) {
  const item = {
    id: data.id || crypto.randomUUID(),
    timestamp: data.timestamp || Date.now(),
    filePath: data.filePath || data.path,
    fileName: data.fileName || (data.filePath || data.path || '').split(/[/\\]/).pop(),
    language: data.language || detectLanguage(data.filePath || data.path || ''),
    originalContent: data.originalContent || '',
    modifiedContent: data.modifiedContent || data.content || '',
    diff: data.diff || '',
    toolCall: data.toolCall || 'write_file',
    description: data.description || '',
    status: data.status || 'applied',  // Files are already applied when user confirmed
    appliedAt: data.appliedAt || Date.now(),
    isNew: data.isNew || false
  };

  workspaceState.tabs.code.items.unshift(item);
  renderWorkspaceTab('code');
  updateWorkspaceBadges();

  // Auto-show workspace if hidden
  if (!workspaceState.visible) {
    toggleWorkspace();
  }
  switchWorkspaceTab('code');
}

function addSqlResultToWorkspace(data) {
  const item = {
    id: data.id || crypto.randomUUID(),
    timestamp: Date.now(),
    query: data.query || '',
    database: data.database || '',
    columns: data.columns || [],
    rows: data.rows || [],
    rowCount: data.rowCount || (data.rows ? data.rows.length : 0),
    executionTimeMs: data.executionTimeMs || 0,
    error: data.error || null
  };

  workspaceState.tabs.sql.items.unshift(item);
  renderWorkspaceTab('sql');
  updateWorkspaceBadges();

  if (!workspaceState.visible) {
    toggleWorkspace();
  }
  switchWorkspaceTab('sql');
}

function addResearchResultToWorkspace(data) {
  const item = {
    id: data.id || crypto.randomUUID(),
    timestamp: Date.now(),
    source: data.source || 'other',
    title: data.title || '',
    snippet: data.snippet || data.content || '',
    url: data.url || null,
    filePath: data.filePath || null,
    lineNumber: data.lineNumber || null,
    relevance: data.relevance || 0
  };

  workspaceState.tabs.research.items.push(item);
  renderWorkspaceTab('research');
  updateWorkspaceBadges();
}

function addFileToWorkspace(data) {
  // Check if file already exists
  const existing = workspaceState.tabs.files.items.find(f => f.filePath === data.filePath);
  if (existing) return;

  const item = {
    id: data.id || crypto.randomUUID(),
    timestamp: Date.now(),
    filePath: data.filePath || data.path,
    operation: data.operation || 'read'
  };

  workspaceState.tabs.files.items.push(item);
  renderWorkspaceTab('files');
}

async function applyCodeChange(itemId) {
  const item = workspaceState.tabs.code.items.find(i => i.id === itemId);
  if (!item) return;

  try {
    // Call API to apply the change
    const response = await fetch(`/api/workspace/code/apply/${itemId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filePath: item.filePath,
        content: item.modifiedContent
      })
    });

    if (response.ok) {
      item.status = 'applied';
      item.appliedAt = Date.now();
      renderWorkspaceTab('code');
      showToast('Code-Änderung angewendet', 'success');
    } else {
      const err = await response.json();
      showToast(`Fehler: ${err.detail || 'Unbekannt'}`, 'error');
    }
  } catch (e) {
    log.error('Apply code change failed:', e);
    showToast('Fehler beim Anwenden der Änderung', 'error');
  }
}

function rejectCodeChange(itemId) {
  const item = workspaceState.tabs.code.items.find(i => i.id === itemId);
  if (!item) return;

  item.status = 'rejected';
  renderWorkspaceTab('code');
}

// ══════════════════════════════════════════════════════════════════════════════
// PR Review Workspace Tab
// ══════════════════════════════════════════════════════════════════════════════

const prReviewState = {
  active: false,
  reviewId: null,
  prNumber: null,
  repoOwner: null,
  repoName: null,
  title: '',
  author: '',
  baseBranch: '',
  headBranch: '',
  additions: 0,
  deletions: 0,
  filesChanged: 0,
  comments: [],
  summary: null,
  userComments: {},  // Map of commentId -> user edited text
  dismissedComments: new Set(),
};

// Detect PR links in messages
const PR_LINK_REGEX = /https?:\/\/github\.com\/([^\/]+)\/([^\/]+)\/pull\/(\d+)/gi;
const PR_MENTION_REGEX = /(?:PR|pull request)\s*#?(\d+)/gi;

function detectPRInMessage(text) {
  // Check for full GitHub URL
  const urlMatch = PR_LINK_REGEX.exec(text);
  if (urlMatch) {
    return {
      repoOwner: urlMatch[1],
      repoName: urlMatch[2],
      prNumber: parseInt(urlMatch[3]),
    };
  }

  // Check for PR #123 mention (requires repo context)
  const mentionMatch = PR_MENTION_REGEX.exec(text);
  if (mentionMatch) {
    // Would need repo context from settings or prior conversation
    return {
      prNumber: parseInt(mentionMatch[1]),
      repoOwner: null,  // Need to resolve from context
      repoName: null,
    };
  }

  return null;
}

async function loadPRReview(repoOwner, repoName, prNumber) {
  showToast('Lade PR-Daten...', 'info');

  try {
    // First, fetch PR info from GitHub (mock for now, would need GitHub API)
    // For now, trigger a review with placeholder data
    const res = await fetch('/api/reviews/trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repoOwner,
        repoName,
        prNumber,
        headSha: 'HEAD',
        baseSha: 'BASE',
        files: {},  // Would be populated from GitHub diff
        reviewTypes: ['security', 'quality', 'style'],
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to load PR');
    }

    const review = await res.json();
    openPRReviewTab(review);

  } catch (e) {
    log.error('Failed to load PR review:', e);
    showToast(`PR-Review fehlgeschlagen: ${e.message}`, 'error');
  }
}

// Handle workspace_pr event from backend (when github_pr_details/github_pr_diff is called)
function openPRFromEvent(data) {
  // Update prReviewState with data from the event
  prReviewState.active = true;
  prReviewState.prNumber = data.prNumber;
  prReviewState.repoOwner = data.repoOwner || '';
  prReviewState.repoName = data.repoName || '';
  prReviewState.title = data.title || `PR #${data.prNumber}`;
  prReviewState.author = data.author || '';
  prReviewState.baseBranch = data.baseBranch || 'main';
  prReviewState.headBranch = data.headBranch || 'feature';
  prReviewState.additions = data.additions || 0;
  prReviewState.deletions = data.deletions || 0;
  prReviewState.filesChanged = data.filesChanged || 0;
  prReviewState.comments = [];
  prReviewState.summary = null;
  prReviewState.userComments = {};
  prReviewState.dismissedComments = new Set();

  // Store diff if available (from github_pr_diff)
  if (data.diff) {
    prReviewState.diff = data.diff;
  }

  // Show PR tab
  const prTab = document.getElementById('workspace-pr-tab');
  const prLabel = document.getElementById('workspace-pr-label');
  if (prTab) {
    prTab.style.display = 'flex';
    prLabel.textContent = `PR #${data.prNumber}`;
  }

  // Render panel content
  renderPRReviewPanel();

  // Switch to PR tab and show workspace
  if (!workspaceState.visible) {
    toggleWorkspace();
  }
  switchWorkspaceTab('pr');
}

function openPRReviewTab(review) {
  // Store review data
  prReviewState.active = true;
  prReviewState.reviewId = review.id;
  prReviewState.prNumber = review.prNumber;
  prReviewState.repoOwner = review.repoOwner;
  prReviewState.repoName = review.repoName;
  prReviewState.comments = review.comments || [];
  prReviewState.summary = review.summary;
  prReviewState.userComments = {};
  prReviewState.dismissedComments = new Set();

  // Initialize user comments with AI suggestions
  for (const c of prReviewState.comments) {
    prReviewState.userComments[c.id] = c.body;
  }

  // Show PR tab
  const prTab = document.getElementById('workspace-pr-tab');
  const prLabel = document.getElementById('workspace-pr-label');
  if (prTab) {
    prTab.style.display = 'flex';
    prLabel.textContent = `PR #${review.prNumber}`;
  }

  // Update badge with issue count
  const badge = document.getElementById('workspace-pr-badge');
  if (badge && review.summary) {
    const total = review.summary.totalComments || 0;
    badge.textContent = total;
    badge.style.display = total > 0 ? 'inline' : 'none';
  }

  // Render panel content
  renderPRReviewPanel();

  // Switch to PR tab
  if (!workspaceState.visible) {
    toggleWorkspace();
  }
  switchWorkspaceTab('pr');
}

function renderPRReviewPanel() {
  const state = prReviewState;

  // Header
  document.getElementById('pr-number').textContent = `#${state.prNumber}`;
  document.getElementById('pr-title').textContent = state.title || `${state.repoOwner}/${state.repoName}`;
  document.getElementById('pr-branches').textContent = `${state.baseBranch || 'main'} ← ${state.headBranch || 'feature'}`;
  document.getElementById('pr-stats').textContent = `+${state.additions} -${state.deletions} | ${state.filesChanged} files`;
  document.getElementById('pr-author').textContent = `@${state.author || 'unknown'}`;

  // Summary
  if (state.summary) {
    const s = state.summary;
    document.getElementById('pr-critical').textContent = s.bySeverity?.critical || 0;
    document.getElementById('pr-high').textContent = s.bySeverity?.high || 0;
    document.getElementById('pr-medium').textContent = s.bySeverity?.medium || 0;
    document.getElementById('pr-low').textContent = s.bySeverity?.low || 0;
    document.getElementById('pr-info').textContent = s.bySeverity?.info || 0;

    const verdictEl = document.getElementById('pr-verdict');
    const verdict = s.verdict || 'comment';
    verdictEl.className = `pr-verdict ${verdict}`;
    verdictEl.innerHTML = verdict === 'approve'
      ? '<span class="verdict-icon">&#9989;</span><span class="verdict-text">APPROVE</span>'
      : verdict === 'request_changes'
        ? '<span class="verdict-icon">&#9888;</span><span class="verdict-text">REQUEST CHANGES</span>'
        : '<span class="verdict-icon">&#128172;</span><span class="verdict-text">COMMENT</span>';
  }

  // Group comments by file
  const fileComments = {};
  for (const c of state.comments) {
    if (!fileComments[c.filePath]) {
      fileComments[c.filePath] = [];
    }
    fileComments[c.filePath].push(c);
  }

  // Render files
  const filesContainer = document.getElementById('pr-files');
  filesContainer.innerHTML = Object.entries(fileComments).map(([filePath, comments]) => `
    <div class="pr-file">
      <div class="pr-file-header" onclick="togglePRFile(this)">
        <span class="pr-file-name">${escapeHtml(filePath)}</span>
        <span class="pr-file-issues">${comments.length} issues</span>
      </div>
      <div class="pr-file-comments">
        ${comments.map(c => renderPRComment(c)).join('')}
      </div>
    </div>
  `).join('');
}

function renderPRComment(comment) {
  const isDismissed = prReviewState.dismissedComments.has(comment.id);
  const userText = prReviewState.userComments[comment.id] || comment.body;

  return `
    <div class="pr-comment ${comment.severity} ${isDismissed ? 'dismissed' : ''}" data-comment-id="${comment.id}">
      <div class="pr-comment-header">
        <span class="pr-comment-line">Line ${comment.line}</span>
        <span class="pr-comment-severity" style="background: var(--${comment.severity === 'critical' ? 'danger' : comment.severity === 'high' ? 'warning' : 'accent'}-bg); color: var(--${comment.severity === 'critical' ? 'danger' : comment.severity === 'high' ? 'warning' : 'accent'});">${comment.severity.toUpperCase()}</span>
        <span class="pr-comment-title">${escapeHtml(comment.title)}</span>
      </div>
      ${comment.suggestedFix ? `
        <div class="pr-comment-code"><pre>${escapeHtml(comment.suggestedFix.originalCode)}</pre></div>
        <div class="pr-comment-body">💡 ${escapeHtml(comment.suggestedFix.description)}</div>
      ` : `
        <div class="pr-comment-body">${escapeHtml(comment.body)}</div>
      `}
      <textarea class="pr-comment-input"
                placeholder="Kommentar bearbeiten..."
                onchange="updatePRComment('${comment.id}', this.value)">${escapeHtml(userText)}</textarea>
      <div class="pr-comment-actions">
        <button class="pr-comment-dismiss" onclick="dismissPRComment('${comment.id}')">
          ${isDismissed ? 'Wiederherstellen' : 'Dismiss'}
        </button>
        ${comment.suggestedFix ? `
          <button class="pr-comment-fix" onclick="applyPRFix('${comment.id}')">Fix anzeigen</button>
        ` : ''}
      </div>
    </div>
  `;
}

function togglePRFile(headerEl) {
  const commentsEl = headerEl.nextElementSibling;
  commentsEl.style.display = commentsEl.style.display === 'none' ? 'block' : 'none';
}

function updatePRComment(commentId, text) {
  prReviewState.userComments[commentId] = text;
}

function dismissPRComment(commentId) {
  if (prReviewState.dismissedComments.has(commentId)) {
    prReviewState.dismissedComments.delete(commentId);
  } else {
    prReviewState.dismissedComments.add(commentId);
  }
  renderPRReviewPanel();
}

function applyPRFix(commentId) {
  const comment = prReviewState.comments.find(c => c.id === commentId);
  if (comment && comment.suggestedFix) {
    // Add to code workspace tab
    addCodeChangeToWorkspace({
      filePath: comment.filePath,
      originalContent: comment.suggestedFix.originalCode,
      modifiedContent: comment.suggestedFix.fixedCode,
      diff: comment.suggestedFix.diff,
      description: `PR Fix: ${comment.title}`,
    });
    showToast('Fix zum Code-Tab hinzugefügt', 'success');
  }
}

async function submitPRReview(verdict) {
  const state = prReviewState;

  // Collect non-dismissed comments with user edits
  const comments = state.comments
    .filter(c => !state.dismissedComments.has(c.id))
    .map(c => ({
      filePath: c.filePath,
      line: c.line,
      body: state.userComments[c.id] || c.body,
    }));

  const overallComment = document.getElementById('pr-overall-comment-input')?.value || '';

  try {
    const res = await fetch('/api/reviews/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reviewId: state.reviewId,
        verdict,
        overallComment,
        comments,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Submit failed');
    }

    const result = await res.json();
    showToast(`Review submitted: ${verdict.replace('_', ' ')}`, 'success');
    closePRReview();

    // Add confirmation to chat
    appendMessage('system', `PR #${state.prNumber} Review: **${verdict.toUpperCase().replace('_', ' ')}** (${comments.length} Kommentare)`);

  } catch (e) {
    log.error('Submit PR review failed:', e);
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

function closePRReview() {
  prReviewState.active = false;

  // Hide PR tab
  const prTab = document.getElementById('workspace-pr-tab');
  if (prTab) {
    prTab.style.display = 'none';
  }

  // Switch to another tab
  switchWorkspaceTab('code');
}

// ══════════════════════════════════════════════════════════════════════════════
// ARENA MODE
// ══════════════════════════════════════════════════════════════════════════════

const arenaState = {
  matchId: null,
  status: 'idle',  // idle, waiting, voting, completed
  prompt: '',
  modelA: '',
  modelB: '',
  responseA: '',
  responseB: '',
};

function openArenaModal() {
  const modal = document.getElementById('arena-modal');
  modal.style.display = 'flex';
  loadArenaLeaderboard();
  focusTrap.activate(modal);
}

function closeArenaModal() {
  focusTrap.deactivate();
  document.getElementById('arena-modal').style.display = 'none';
}

async function startArenaMatch() {
  const prompt = document.getElementById('arena-prompt').value.trim();
  if (!prompt) {
    showToast('Bitte gib einen Prompt ein', 'warning');
    return;
  }

  arenaState.status = 'waiting';
  arenaState.prompt = prompt;
  updateArenaStatus('Starte Match...');

  // Show response panels
  document.getElementById('arena-prompt-section').style.display = 'none';
  document.getElementById('arena-responses').style.display = 'grid';
  document.getElementById('arena-response-a').innerHTML = '<div class="arena-loading">Generiere Antwort...</div>';
  document.getElementById('arena-response-b').innerHTML = '<div class="arena-loading">Generiere Antwort...</div>';

  try {
    const res = await fetch('/api/arena/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt,
        sessionId: state.sessionId || 'arena-session',
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to start match');
    }

    const match = await res.json();
    arenaState.matchId = match.id;
    arenaState.modelA = match.modelA;
    arenaState.modelB = match.modelB;

    // Poll for responses
    pollArenaResponses(match.id);

  } catch (e) {
    log.error('Arena start failed:', e);
    showToast(`Arena Fehler: ${e.message}`, 'error');
    resetArena();
  }
}

async function pollArenaResponses(matchId) {
  const maxAttempts = 60;
  let attempts = 0;

  const poll = async () => {
    try {
      const res = await fetch(`/api/arena/match/${matchId}`);
      if (!res.ok) throw new Error('Failed to fetch match');

      const match = await res.json();

      // Update responses
      if (match.responseA) {
        document.getElementById('arena-response-a').innerHTML = renderMarkdown(match.responseA);
        document.getElementById('arena-meta-a').textContent = `${match.latencyA}ms | ${match.tokensA} tokens`;
      }
      if (match.responseB) {
        document.getElementById('arena-response-b').innerHTML = renderMarkdown(match.responseB);
        document.getElementById('arena-meta-b').textContent = `${match.latencyB}ms | ${match.tokensB} tokens`;
      }

      // Check if both responses are ready
      if (match.responseA && match.responseB) {
        arenaState.status = 'voting';
        updateArenaStatus('Warte auf Bewertung');
        document.getElementById('arena-voting').style.display = 'block';
        return;
      }

      // Continue polling
      attempts++;
      if (attempts < maxAttempts) {
        setTimeout(poll, 1000);
      } else {
        showToast('Timeout beim Warten auf Antworten', 'error');
      }

    } catch (e) {
      log.error('Arena poll error:', e);
    }
  };

  poll();
}

async function voteArena(vote) {
  if (!arenaState.matchId) return;

  const feedback = document.getElementById('arena-feedback').value.trim();

  try {
    const res = await fetch(`/api/arena/match/${arenaState.matchId}/vote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vote, feedback }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Vote failed');
    }

    const result = await res.json();

    // Show result
    arenaState.status = 'completed';
    document.getElementById('arena-voting').style.display = 'none';
    document.getElementById('arena-result').style.display = 'block';
    document.getElementById('arena-model-a-reveal').textContent = result.modelA || arenaState.modelA;
    document.getElementById('arena-model-b-reveal').textContent = result.modelB || arenaState.modelB;

    const winnerText = vote === 'A' ? 'Modell A gewinnt!' :
                       vote === 'B' ? 'Modell B gewinnt!' : 'Unentschieden!';
    document.getElementById('arena-winner').textContent = winnerText;

    updateArenaStatus('Abgeschlossen');
    loadArenaLeaderboard();

  } catch (e) {
    log.error('Arena vote failed:', e);
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

function resetArena() {
  arenaState.matchId = null;
  arenaState.status = 'idle';

  document.getElementById('arena-prompt').value = '';
  document.getElementById('arena-prompt-section').style.display = 'block';
  document.getElementById('arena-responses').style.display = 'none';
  document.getElementById('arena-voting').style.display = 'none';
  document.getElementById('arena-result').style.display = 'none';
  document.getElementById('arena-feedback').value = '';

  updateArenaStatus('Bereit');
}

function updateArenaStatus(text) {
  document.getElementById('arena-status-value').textContent = text;
}

async function loadArenaLeaderboard() {
  try {
    const res = await fetch('/api/arena/leaderboard');
    if (!res.ok) return;

    const data = await res.json();
    const tbody = document.getElementById('arena-leaderboard-body');

    if (!data.models || data.models.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="arena-leaderboard-empty">Keine Daten</td></tr>';
      return;
    }

    tbody.innerHTML = data.models.map(m => `
      <tr>
        <td>${escapeHtml(m.model)}</td>
        <td>${Math.round(m.elo)}</td>
        <td>${m.wins}</td>
        <td>${m.total}</td>
        <td>${m.total > 0 ? Math.round(m.wins / m.total * 100) : 0}%</td>
      </tr>
    `).join('');

  } catch (e) {
    log.error('Failed to load leaderboard:', e);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// TOKEN USAGE MODAL
// ══════════════════════════════════════════════════════════════════════════════

function openTokensModal() {
  const modal = document.getElementById('tokens-modal');
  modal.style.display = 'flex';
  loadTokenUsage();
  focusTrap.activate(modal);
}

function closeTokensModal() {
  focusTrap.deactivate();
  document.getElementById('tokens-modal').style.display = 'none';
}

async function loadTokenUsage() {
  const period = document.getElementById('tokens-period').value;

  try {
    const res = await fetch(`/api/tokens/usage?period=${period}`);
    if (!res.ok) throw new Error('Failed to load token usage');

    const data = await res.json();

    // Update summary cards
    document.getElementById('tokens-total').textContent = formatNumber(data.totalTokens);
    document.getElementById('tokens-requests').textContent = formatNumber(data.totalRequests);
    document.getElementById('tokens-cost').textContent = `$${data.estimatedCostUsd.toFixed(2)}`;

    // Budget
    if (data.budgetLimit) {
      const pct = Math.min(100, (data.budgetUsed / data.budgetLimit) * 100);
      document.getElementById('tokens-budget').textContent = `${Math.round(pct)}%`;
      document.getElementById('tokens-budget-fill').style.width = `${pct}%`;
    } else {
      document.getElementById('tokens-budget').textContent = '-';
      document.getElementById('tokens-budget-fill').style.width = '0%';
    }

    // By Model - Enhanced display with donut chart and details
    const byModelContainer = document.getElementById('tokens-by-model');
    const donutContainer = document.getElementById('tokens-model-donut');

    if (data.byModel && Object.keys(data.byModel).length > 0) {
      const totalAllTokens = data.totalTokens || Object.values(data.byModel).reduce((sum, m) => sum + m.totalTokens, 0);
      const maxTokens = Math.max(...Object.values(data.byModel).map(m => m.totalTokens));

      // Sort by total tokens descending
      const sortedModels = Object.entries(data.byModel).sort((a, b) => b[1].totalTokens - a[1].totalTokens);

      // Render Donut Chart
      if (donutContainer) {
        const donutColors = ['#6366f1', '#8b5cf6', '#a855f7', '#d946ef', '#ec4899', '#f43f5e'];
        const radius = 60;
        const circumference = 2 * Math.PI * radius;
        let offset = 0;

        const segments = sortedModels.slice(0, 6).map(([model, stats], i) => {
          const pct = totalAllTokens > 0 ? (stats.totalTokens / totalAllTokens) : 0;
          const dashLength = pct * circumference;
          const segment = `
            <circle class="tokens-donut-segment"
                    cx="80" cy="80" r="${radius}"
                    stroke="${donutColors[i % donutColors.length]}"
                    stroke-dasharray="${dashLength} ${circumference - dashLength}"
                    stroke-dashoffset="${-offset}"
                    title="${escapeHtml(model)}: ${(pct * 100).toFixed(1)}%"/>
          `;
          offset += dashLength;
          return segment;
        }).join('');

        const legend = sortedModels.slice(0, 6).map(([model], i) => `
          <span class="tokens-donut-legend-item">
            <span class="tokens-donut-legend-color" style="background: ${donutColors[i % donutColors.length]}"></span>
            ${escapeHtml(model.length > 12 ? model.substring(0, 12) + '...' : model)}
          </span>
        `).join('');

        donutContainer.innerHTML = `
          <div class="tokens-donut">
            <svg class="tokens-donut-svg" viewBox="0 0 160 160">
              <circle cx="80" cy="80" r="${radius}" fill="none" stroke="var(--border)" stroke-width="28"/>
              ${segments}
            </svg>
            <div class="tokens-donut-center">
              <div class="tokens-donut-center-value">${sortedModels.length}</div>
              <div class="tokens-donut-center-label">Modelle</div>
            </div>
          </div>
          <div class="tokens-donut-legend">${legend}</div>
        `;
      }

      byModelContainer.innerHTML = sortedModels.map(([model, stats]) => {
        const pct = totalAllTokens > 0 ? (stats.totalTokens / totalAllTokens) * 100 : 0;
        const barPct = maxTokens > 0 ? (stats.totalTokens / maxTokens) * 100 : 0;
        const costStr = stats.costUsd !== undefined ? `$${stats.costUsd.toFixed(3)}` : '';

        return `
        <div class="tokens-breakdown-item">
          <div class="tokens-breakdown-main">
            <div class="tokens-breakdown-header">
              <span class="tokens-breakdown-label">${escapeHtml(model)}</span>
              <span class="tokens-breakdown-pct">${pct.toFixed(1)}%</span>
            </div>
            <div class="tokens-breakdown-bar">
              <div class="tokens-breakdown-bar-fill" style="width: ${barPct}%"></div>
            </div>
            <div class="tokens-breakdown-stats">
              <span class="tokens-breakdown-stat">
                <span class="tokens-breakdown-stat-icon">↓</span>
                <span class="tokens-breakdown-stat-value">${formatNumber(stats.inputTokens || 0)}</span> Input
              </span>
              <span class="tokens-breakdown-stat">
                <span class="tokens-breakdown-stat-icon">↑</span>
                <span class="tokens-breakdown-stat-value">${formatNumber(stats.outputTokens || 0)}</span> Output
              </span>
              <span class="tokens-breakdown-stat">
                <span class="tokens-breakdown-stat-icon">📝</span>
                <span class="tokens-breakdown-stat-value">${formatNumber(stats.requests || 0)}</span> Requests
              </span>
            </div>
          </div>
          <div class="tokens-breakdown-value">
            <span class="tokens-breakdown-total">${formatNumber(stats.totalTokens)}</span>
            ${costStr ? `<span class="tokens-breakdown-cost">${costStr}</span>` : ''}
          </div>
        </div>
      `;
      }).join('');
    } else {
      byModelContainer.innerHTML = '<div class="tokens-breakdown-empty">Keine Modell-Daten verfügbar</div>';
      if (donutContainer) donutContainer.innerHTML = '';
    }

    // By Type - Enhanced display with details
    const byTypeContainer = document.getElementById('tokens-by-type');
    if (data.byRequestType && Object.keys(data.byRequestType).length > 0) {
      const totalAllTokens = data.totalTokens || Object.values(data.byRequestType).reduce((sum, t) => sum + t.totalTokens, 0);
      const maxTokens = Math.max(...Object.values(data.byRequestType).map(t => t.totalTokens));

      // Sort by total tokens descending
      const sortedTypes = Object.entries(data.byRequestType).sort((a, b) => b[1].totalTokens - a[1].totalTokens);

      byTypeContainer.innerHTML = sortedTypes.map(([type, stats]) => {
        const pct = totalAllTokens > 0 ? (stats.totalTokens / totalAllTokens) * 100 : 0;
        const barPct = maxTokens > 0 ? (stats.totalTokens / maxTokens) * 100 : 0;
        const costStr = stats.costUsd !== undefined ? `$${stats.costUsd.toFixed(3)}` : '';

        // Friendly type labels
        const typeLabels = {
          'chat': 'Chat',
          'agent': 'Agent',
          'task': 'Task',
          'enhancement': 'Enhancement',
          'planning': 'Planning',
          'research': 'Research',
          'analysis': 'Analysis'
        };
        const displayType = typeLabels[type.toLowerCase()] || type;

        return `
        <div class="tokens-breakdown-item">
          <div class="tokens-breakdown-main">
            <div class="tokens-breakdown-header">
              <span class="tokens-breakdown-label">${escapeHtml(displayType)}</span>
              <span class="tokens-breakdown-pct">${pct.toFixed(1)}%</span>
            </div>
            <div class="tokens-breakdown-bar">
              <div class="tokens-breakdown-bar-fill" style="width: ${barPct}%"></div>
            </div>
            <div class="tokens-breakdown-stats">
              <span class="tokens-breakdown-stat">
                <span class="tokens-breakdown-stat-icon">↓</span>
                <span class="tokens-breakdown-stat-value">${formatNumber(stats.inputTokens || 0)}</span> Input
              </span>
              <span class="tokens-breakdown-stat">
                <span class="tokens-breakdown-stat-icon">↑</span>
                <span class="tokens-breakdown-stat-value">${formatNumber(stats.outputTokens || 0)}</span> Output
              </span>
              <span class="tokens-breakdown-stat">
                <span class="tokens-breakdown-stat-icon">📝</span>
                <span class="tokens-breakdown-stat-value">${formatNumber(stats.requests || 0)}</span> Requests
              </span>
            </div>
          </div>
          <div class="tokens-breakdown-value">
            <span class="tokens-breakdown-total">${formatNumber(stats.totalTokens)}</span>
            ${costStr ? `<span class="tokens-breakdown-cost">${costStr}</span>` : ''}
          </div>
        </div>
      `;
      }).join('');
    } else {
      byTypeContainer.innerHTML = '<div class="tokens-breakdown-empty">Keine Typ-Daten verfügbar</div>';
    }

    // Hourly chart with stacked bars by model
    const chartContainer = document.getElementById('tokens-hourly-chart');
    const xAxisContainer = document.getElementById('tokens-x-axis');
    const yAxisContainer = document.getElementById('tokens-y-axis');

    if (data.byHour && data.byHour.length > 0) {
      const maxHourly = Math.max(...data.byHour.map(h => h.tokens));

      // Collect all unique models for color mapping
      const allModels = new Set();
      data.byHour.forEach(h => {
        if (h.byModel) Object.keys(h.byModel).forEach(m => allModels.add(m));
      });
      const modelColors = {};
      const colorPalette = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'];
      Array.from(allModels).forEach((model, i) => {
        modelColors[model] = colorPalette[i % colorPalette.length];
      });

      // Y-Axis labels
      if (yAxisContainer) {
        yAxisContainer.innerHTML = `
          <span>${formatNumber(maxHourly)}</span>
          <span>${formatNumber(Math.round(maxHourly / 2))}</span>
          <span>0</span>
        `;
      }

      // Chart bars - stacked by model
      chartContainer.innerHTML = data.byHour.map(h => {
        const barHeight = maxHourly > 0 ? (h.tokens / maxHourly) * 100 : 0;

        // Build stacked segments for this hour
        let segments = '';
        if (h.byModel && Object.keys(h.byModel).length > 0 && h.tokens > 0) {
          // Sort models by token count (largest at bottom)
          const sortedModels = Object.entries(h.byModel).sort((a, b) => b[1] - a[1]);
          segments = sortedModels.map(([model, tokens]) => {
            const segmentPct = (tokens / h.tokens) * 100;
            const color = modelColors[model] || '#888';
            return `<div class="tokens-chart-segment" style="height:${segmentPct}%; background:${color};" title="${model}: ${formatNumber(tokens)}"></div>`;
          }).join('');
        }

        // Build tooltip with model breakdown
        let tooltip = `${h.hour}: ${formatNumber(h.tokens)} tokens`;
        if (h.byModel && Object.keys(h.byModel).length > 0) {
          const breakdown = Object.entries(h.byModel)
            .sort((a, b) => b[1] - a[1])
            .map(([m, t]) => `${m}: ${formatNumber(t)}`)
            .join(', ');
          tooltip += ` (${breakdown})`;
        }

        return `
          <div class="tokens-chart-bar tokens-chart-bar-stacked"
               style="height: ${barHeight}%"
               title="${tooltip}">
            ${segments}
          </div>
        `;
      }).join('');

      // X-Axis labels (show every 3rd hour)
      if (xAxisContainer) {
        xAxisContainer.innerHTML = data.byHour.map((h, i) =>
          `<span>${i % 3 === 0 ? h.hour.split('T')[1] || h.hour : ''}</span>`
        ).join('');
      }

      // Model legend below chart
      const legendContainer = document.getElementById('tokens-model-legend');
      if (legendContainer && allModels.size > 0) {
        legendContainer.innerHTML = Array.from(allModels).map(model =>
          `<span class="tokens-legend-item">
            <span class="tokens-legend-color" style="background:${modelColors[model]}"></span>
            ${model}
          </span>`
        ).join('');
      }
    } else {
      chartContainer.innerHTML = '<div class="tokens-chart-placeholder">Keine Daten</div>';
      if (xAxisContainer) xAxisContainer.innerHTML = '';
      if (yAxisContainer) yAxisContainer.innerHTML = '';
    }

  } catch (e) {
    log.error('Failed to load token usage:', e);
    showToast('Token-Daten konnten nicht geladen werden', 'error');
  }
}

function formatNumber(num) {
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return String(num);
}

// ══════════════════════════════════════════════════════════════════════════════
// SELF-HEALING MODAL
// ══════════════════════════════════════════════════════════════════════════════

const healingState = {
  attemptId: null,
  fixId: null,
  toolName: '',
};

function showHealingModal(attempt) {
  healingState.attemptId = attempt.id;
  healingState.fixId = attempt.suggestedFix?.id;
  healingState.toolName = attempt.originalError?.tool || 'unknown';

  // Populate modal
  document.getElementById('healing-tool').textContent = healingState.toolName;
  document.getElementById('healing-error-message').textContent = attempt.originalError?.errorMessage || 'Unbekannter Fehler';

  if (attempt.suggestedFix) {
    const fix = attempt.suggestedFix;
    document.getElementById('healing-fix-description').textContent = fix.description;

    // Show code diff
    const codeEl = document.getElementById('healing-fix-code').querySelector('code');
    if (fix.changes && fix.changes.length > 0) {
      const change = fix.changes[0];
      codeEl.textContent = `- ${change.oldContent || ''}\n+ ${change.newContent || ''}`;
      codeEl.className = 'language-diff';
      if (window.hljs) hljs.highlightElement(codeEl);
    } else {
      codeEl.textContent = fix.command || fix.description;
    }

    document.getElementById('healing-confidence').textContent = Math.round(fix.confidence * 100);

    const safeEl = document.getElementById('healing-safe');
    if (fix.safeToAutoApply) {
      safeEl.textContent = '✓ Sicher für Auto-Apply';
      safeEl.style.color = 'var(--success)';
    } else {
      safeEl.textContent = '⚠ Manuelle Prüfung empfohlen';
      safeEl.style.color = 'var(--warning)';
    }

    document.getElementById('healing-fix').style.display = 'block';
  } else {
    document.getElementById('healing-fix').style.display = 'none';
  }

  // Pattern info
  if (attempt.patternMatch) {
    document.getElementById('healing-pattern-text').textContent =
      `Pattern Match: "${attempt.patternMatch.name}" (${attempt.patternMatch.occurrences}x gesehen, ${Math.round(attempt.patternMatch.successRate * 100)}% Erfolg)`;
    document.getElementById('healing-pattern').style.display = 'flex';
  } else {
    document.getElementById('healing-pattern').style.display = 'none';
  }

  const modal = document.getElementById('healing-modal');
  modal.style.display = 'flex';
  focusTrap.activate(modal);
}

function closeHealingModal() {
  focusTrap.deactivate();
  document.getElementById('healing-modal').style.display = 'none';
  healingState.attemptId = null;
  healingState.fixId = null;
}

async function applyHealing() {
  if (!healingState.attemptId || !healingState.fixId) {
    showToast('Keine Fix-ID vorhanden', 'error');
    return;
  }

  try {
    const res = await fetch(`/api/healing/attempts/${healingState.attemptId}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fixId: healingState.fixId }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Apply failed');
    }

    showToast('Fix erfolgreich angewendet', 'success');
    closeHealingModal();

  } catch (e) {
    log.error('Apply healing failed:', e);
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

function skipHealing() {
  closeHealingModal();
  showToast('Fix übersprungen', 'info');
}

async function alwaysAutoFix() {
  // Update config to auto-apply this pattern
  try {
    await fetch('/api/healing/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ autoApplyLevel: 'safe' }),
    });

    showToast('Auto-Fix aktiviert für sichere Patterns', 'success');
    await applyHealing();

  } catch (e) {
    log.error('Failed to enable auto-fix:', e);
  }
}

// ══════════════════════════════════════════════════════════════════════════════

function detectLanguage(filePath) {
  const ext = (filePath || '').split('.').pop().toLowerCase();
  const langMap = {
    java: 'java', py: 'python', js: 'javascript', ts: 'typescript',
    sql: 'sql', xml: 'xml', json: 'json', yaml: 'yaml', yml: 'yaml',
    html: 'html', css: 'css', md: 'markdown', sh: 'bash'
  };
  return langMap[ext] || 'text';
}

// ── Initialization ──
document.addEventListener('DOMContentLoaded', async () => {
  // Marked.js konfigurieren - Links öffnen in neuem Tab
  const renderer = new marked.Renderer();
  const originalLinkRenderer = renderer.link.bind(renderer);
  renderer.link = (href, title, text) => {
    const html = originalLinkRenderer(href, title, text);
    // Füge target="_blank" und rel="noopener noreferrer" hinzu
    return html.replace(/^<a /, '<a target="_blank" rel="noopener noreferrer" ');
  };
  marked.setOptions({ breaks: true, gfm: true, renderer: renderer });

  // Initialize UI
  setupSidebarTabs();
  setupModeSwitch();
  setupInputHandlers();
  setupKeyboardShortcuts();
  setupWorkspaceResize();

  // KRITISCH: Nur Models und Chats blockieren - Rest im Hintergrund
  // Dies reduziert Initial Load von ~2s auf ~500ms
  await Promise.all([
    loadModels(),
    loadPersistedChats(),
  ]);

  // Initialize @-Mention system
  initMentionSystem();

  // Nicht-kritische Daten im Hintergrund laden (non-blocking)
  // Fehler werden geloggt aber blockieren UI nicht
  Promise.all([
    loadSkills().catch(e => log.warn('[init] Skills load failed:', e)),
    loadJavaIndexStatus().catch(e => log.warn('[init] Java index status failed:', e)),
    loadPythonIndexStatus().catch(e => log.warn('[init] Python index status failed:', e)),
    loadExplorerRepos('java').catch(e => log.warn('[init] Java repos load failed:', e)),
    loadExplorerRepos('python').catch(e => log.warn('[init] Python repos load failed:', e)),
    loadHandbookStatus().catch(e => log.warn('[init] Handbook status failed:', e)),
    scanExistingPdfs().catch(e => log.warn('[init] PDF scan failed:', e)),
    refreshFileCache('all').catch(e => log.warn('[init] File cache refresh failed:', e)),
  ]);
});

// ── UI Setup ──
function setupSidebarTabs() {
  document.querySelectorAll('.sidebar-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const panelId = tab.dataset.panel;
      const sidebar = tab.closest('.sidebar');

      sidebar.querySelectorAll('.sidebar-tab').forEach(t => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
      });
      sidebar.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));

      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');
      document.getElementById(panelId).classList.add('active');
    });
  });
}

/**
 * Programmtisch zu einem Panel in der rechten Sidebar wechseln.
 * @param {string} panelId - Die ID des Panels (z.B. 'mcp-panel', 'tools-panel')
 */
function switchRightPanel(panelId) {
  const sidebar = document.getElementById('sidebar-right');
  if (!sidebar) return;

  // Alle Tabs und Panels deaktivieren
  sidebar.querySelectorAll('.sidebar-tab').forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
  });
  sidebar.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));

  // Ziel-Tab und Panel aktivieren
  const tab = sidebar.querySelector(`.sidebar-tab[data-panel="${panelId}"]`);
  const panel = document.getElementById(panelId);

  if (tab) {
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
  }
  if (panel) panel.classList.add('active');
}

function setupModeSwitch() {
  document.querySelectorAll('input[name="agent-mode"]').forEach(radio => {
    radio.addEventListener('change', async (e) => {
      await setAgentMode(e.target.value);
    });
  });
}

function setupInputHandlers() {
  const input = document.getElementById('message-input');

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      // Don't send message if @-mention dropdown is active
      if (mentionState.active) return;
      e.preventDefault();
      sendMessage();
    }
  });

  input.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    _updateCommandSuggestions(this.value);
  });

  input.addEventListener('keydown', (e) => {
    if (_commandDropdownVisible()) {
      if (e.key === 'ArrowDown') { e.preventDefault(); _commandSelectNext(1); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); _commandSelectNext(-1); return; }
      if (e.key === 'Tab' || e.key === 'Enter') {
        const active = document.querySelector('.cmd-suggestion.active');
        if (active) { e.preventDefault(); _applyCommandSuggestion(active.dataset.cmd); return; }
      }
      if (e.key === 'Escape') { _hideCommandSuggestions(); return; }
    }
  });
}

// ── Keyboard Shortcuts ──
function setupKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Escape: Modals/Panels schliessen
    if (e.key === 'Escape') {
      // Confirmation Panel schliessen
      const confirmPanel = document.getElementById('confirmation-panel');
      if (confirmPanel && !confirmPanel.classList.contains('hidden')) {
        hideConfirmationPanel();
        return;
      }
      // Settings Modal schliessen
      const settingsModal = document.querySelector('.settings-modal');
      if (settingsModal) {
        settingsModal.remove();
        return;
      }
      // Command Suggestions schliessen
      if (_commandDropdownVisible()) {
        _hideCommandSuggestions();
        return;
      }
    }

    // Ctrl+N: Neuer Chat
    if (e.ctrlKey && e.key === 'n' && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      createNewChat();
      return;
    }

    // Ctrl+B: Workspace Panel toggle
    if (e.ctrlKey && e.key === 'b' && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      toggleWorkspace();
      return;
    }

    // Ctrl+/: Focus auf Input
    if (e.ctrlKey && e.key === '/') {
      e.preventDefault();
      document.getElementById('message-input')?.focus();
      return;
    }
  });
}

// ── Agent Session Management ──
async function createAgentSession() {
  const skillIds = state.activeSkills.join(',');
  const res = await fetch(`/api/agent/session/new?mode=${state.mode}${skillIds ? '&skill_ids=' + skillIds : ''}`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`Session creation failed: ${res.status}`);
  const data = await res.json();
  return data.session_id;
}

// ── MultiChat Functions ──
async function loadPersistedChats() {
  try {
    const res = await fetch('/api/agent/chats');
    if (!res.ok) throw new Error('chats endpoint failed');
    const { chats } = await res.json();
    if (!chats || chats.length === 0) {
      await createNewChat();
      return;
    }
    // Chats in Reihenfolge (älteste zuerst) anlegen, needsRestore markieren
    for (const c of chats) {
      const chat = chatManager.createChat(c.session_id, c.title || 'Chat');
      chat.needsRestore = true;
    }
    // Neuesten Chat aktivieren (letzter in der sortierten Liste)
    const last = chatManager.chats[chatManager.chats.length - 1];
    await switchToChat(last.id);
    renderChatList();
  } catch (e) {
    log.error('Failed to load persisted chats:', e);
    showErrorToast('Chat-Verlauf konnte nicht geladen werden');
    await createNewChat();
  }
}

async function createNewChat() {
  try {
    const sessionId = await createAgentSession();
    const chat = chatManager.createChat(sessionId);
    // switchToChat übernimmt Pane-Swap, State-Restore und UI-Updates
    await switchToChat(chat.id);
    log.info('New chat created:', chat.id, 'session:', sessionId);
  } catch (e) {
    log.error('Failed to create new chat:', e);
    showErrorToast('Neuer Chat konnte nicht erstellt werden');
  }
}

async function switchToChat(chatId) {
  if (chatId === chatManager.activeId) return;

  // Race Condition Fix: Vorherigen Switch abbrechen
  if (_switchChatAbortController) {
    _switchChatAbortController.abort();
  }
  const switchAc = new AbortController();
  _switchChatAbortController = switchAc;

  const outgoingChat = chatManager.getActive();
  const incomingChat = chatManager.get(chatId);
  if (!incomingChat) return;

  const messagesContainer = document.getElementById('messages');

  if (outgoingChat) {
    // Nicht-DOM-State des ausgehenden Chats sichern
    chatManager.saveActiveState();
    // Pane aus DOM entfernen – alle DOM-Referenzen (bubble, statusBar) bleiben
    // im Speicher gültig. Ein laufender Stream schreibt weiter ins detachte Pane.
    if (outgoingChat.pane.parentNode === messagesContainer) {
      messagesContainer.removeChild(outgoingChat.pane);
    }
    // Cancel-Button gehört jetzt niemandem mehr – wird unten neu gesetzt
    _chatAbortController = null;
  } else {
    // Erster Switch: statisches HTML aus dem initialen Seitenload leeren
    messagesContainer.innerHTML = '';
  }

  // Aktiven Chat umschalten
  chatManager.activeId = chatId;

  // State des eingehenden Chats wiederherstellen
  state.sessionId = incomingChat.sessionId;
  state.toolHistory = [...incomingChat.toolHistory];
  state.pendingConfirmation = incomingChat.pendingConfirmation;
  // structuredClone is faster than JSON.parse/stringify for deep cloning
  state.context = typeof structuredClone === 'function'
    ? structuredClone(incomingChat.context)
    : JSON.parse(JSON.stringify(incomingChat.context));

  // Nachrichten-Historie und Mode vom Server laden wenn Chat vom Disk wiederhergestellt wird
  if (incomingChat.needsRestore) {
    // Loading-State im Chat-Objekt speichern (überlebt renderChatList)
    incomingChat.isLoading = true;
    _updateChatLoadingState(chatId, true);

    // Context bar first, then messages
    incomingChat.pane.innerHTML = _contextBarHTML();
    try {
      const res = await fetch(`/api/agent/session/${incomingChat.sessionId}/history`, {
        signal: switchAc.signal
      });
      // Aborted? Stop processing - needsRestore bleibt true für nächsten Versuch!
      if (switchAc.signal.aborted) return;
      if (res.ok) {
        const data = await res.json();
        const { messages, mode } = data;
        if (messages && messages.length > 0) {
          for (const msg of messages) {
            if (msg.role === 'user' || msg.role === 'assistant') {
              appendMessageToPane(incomingChat.pane, msg.role, msg.content);
            }
          }
        } else {
          // Keine Nachrichten - Welcome Screen zeigen
          incomingChat.pane.innerHTML = _contextBarHTML() + welcomeHTML();
        }
        // Mode vom Server synchronisieren
        if (mode) {
          log.info(`[switchToChat] Restored mode from server: ${mode}`);
          syncModeUI(mode);
        }
        // Restore erfolgreich - Flag erst JETZT setzen
        incomingChat.needsRestore = false;
        incomingChat.isLoading = false;
        _updateChatLoadingState(chatId, false);
      } else {
        // Server-Fehler (4xx/5xx) - Fallback auf Welcome Screen
        log.error(`[switchToChat] History fetch failed: ${res.status} ${res.statusText}`);
        incomingChat.pane.innerHTML = _contextBarHTML() + welcomeHTML();
        // Bei Fehler auch needsRestore = false setzen, um Endlosschleife zu vermeiden
        incomingChat.needsRestore = false;
        incomingChat.isLoading = false;
        _updateChatLoadingState(chatId, false);
        if (res.status !== 404) {
          showErrorToast('Chat-Historie konnte nicht geladen werden');
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        // Switch wurde abgebrochen - needsRestore bleibt true, Loading entfernen
        incomingChat.isLoading = false;
        _updateChatLoadingState(chatId, false);
        return;
      }
      incomingChat.pane.innerHTML = _contextBarHTML() + welcomeHTML();
      incomingChat.needsRestore = false;
      incomingChat.isLoading = false;
      _updateChatLoadingState(chatId, false);
      log.error('Failed to restore chat history:', e);
      showErrorToast('Chat-Historie konnte nicht geladen werden');
    }
  } else {
    // Bestehender Chat - Mode aus Chat-Objekt oder Server laden
    if (incomingChat.mode) {
      log.info(`[switchToChat] Using cached mode: ${incomingChat.mode}`);
      syncModeUI(incomingChat.mode);
    } else {
      // Fallback: Mode vom Server laden
      try {
        const res = await fetch(`/api/agent/mode/${incomingChat.sessionId}`, {
          signal: switchAc.signal
        });
        if (switchAc.signal.aborted) return;
        if (res.ok) {
          const { mode } = await res.json();
          log.info(`[switchToChat] Fetched mode from server: ${mode}`);
          syncModeUI(mode);
        }
      } catch (e) {
        if (e.name === 'AbortError') return;
        console.debug('Could not fetch mode:', e);
      }
    }
  }

  // Nochmal pruefen ob aborted bevor DOM manipuliert wird
  if (switchAc.signal.aborted) return;

  // Pane des eingehenden Chats in den DOM hängen (inklusive aller laufenden DOM-Updates)
  messagesContainer.appendChild(incomingChat.pane);

  // Context status des eingehenden Chats wiederherstellen
  if (incomingChat.contextStatus) {
    updateContextIndicator(incomingChat.contextStatus, incomingChat);
  }

  // Cancel-Button: aktiv wenn eingehender Chat gerade streamt
  if (incomingChat.streamingState) {
    _chatAbortController = incomingChat.streamingState.abortController;
    _setStreamingMode(true);
  } else {
    _chatAbortController = null;
    _setStreamingMode(false);
  }

  updateModeIndicator();
  renderToolHistory();
  renderContextChips();
  hideConfirmationPanel();
  renderChatList();

  // Task Progress Panel mit Session verbinden
  if (incomingChat.sessionId && taskProgressPanel) {
    taskProgressPanel.connect(incomingChat.sessionId);
  }

  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

async function deleteChat(chatId) {
  const chat = chatManager.get(chatId);
  if (!chat) return;

  // Laufenden Stream für diesen Chat abbrechen
  if (chat.streamingState) {
    chat.streamingState.abortController?.abort();
    if (chat.streamingState.timerInterval) clearInterval(chat.streamingState.timerInterval);
    chat.streamingState = null;
    fetch(`/api/agent/cancel/${chat.sessionId}`, { method: 'POST' }).catch(() => {});
  }

  // Pane aus DOM entfernen falls sichtbar
  if (chat.pane.parentNode) chat.pane.parentNode.removeChild(chat.pane);

  await fetch(`/api/agent/session/${chat.sessionId}`, { method: 'DELETE' }).catch(() => {});

  const wasActive = chatId === chatManager.activeId;
  chatManager.remove(chatId);

  if (wasActive) {
    _chatAbortController = null;
    _setStreamingMode(false);
    if (chatManager.chats.length === 0) {
      await createNewChat();
    } else {
      await switchToChat(chatManager.chats[chatManager.chats.length - 1].id);
    }
  } else {
    renderChatList();
  }
}

/**
 * Aktualisiert den Loading-State eines Chat-Items im DOM
 */
function _updateChatLoadingState(chatId, isLoading) {
  const chatItem = document.querySelector(`.chat-item[data-chat-id="${chatId}"]`);
  if (chatItem) {
    if (isLoading) {
      chatItem.classList.add('loading');
    } else {
      chatItem.classList.remove('loading');
    }
  }
}

function renderChatList() {
  const listEl = document.getElementById('chat-list');
  if (!listEl) return;

  if (chatManager.chats.length === 0) {
    listEl.innerHTML = '<div class="chat-list-empty">Keine Chats</div>';
    return;
  }

  // Newest first
  const sorted = [...chatManager.chats].reverse();
  const existingItems = new Map();

  // Existierende Items sammeln
  listEl.querySelectorAll('.chat-item[data-chat-id]').forEach(el => {
    existingItems.set(el.dataset.chatId, el);
  });

  // Empty placeholder entfernen falls vorhanden
  const emptyPlaceholder = listEl.querySelector('.chat-list-empty');
  if (emptyPlaceholder) emptyPlaceholder.remove();

  // DocumentFragment fuer Batch-Insert
  const fragment = document.createDocumentFragment();
  const newIds = new Set(sorted.map(c => c.id));

  sorted.forEach(chat => {
    const isActive = chat.id === chatManager.activeId;
    const isLoading = chat.isLoading === true;
    let item = existingItems.get(chat.id);

    if (item) {
      // Active-Class updaten
      if (isActive && !item.classList.contains('active')) {
        item.classList.add('active');
      } else if (!isActive && item.classList.contains('active')) {
        item.classList.remove('active');
      }
      // Loading-Class updaten (aus Chat-Objekt, nicht DOM)
      if (isLoading && !item.classList.contains('loading')) {
        item.classList.add('loading');
      } else if (!isLoading && item.classList.contains('loading')) {
        item.classList.remove('loading');
      }
      // Titel updaten wenn geaendert
      const titleEl = item.querySelector('.chat-item-title');
      if (titleEl && titleEl.textContent !== chat.title) {
        titleEl.textContent = chat.title;
        titleEl.title = chat.title;
      }
      // Aus DOM entfernen fuer Reorder
      if (item.parentNode === listEl) {
        listEl.removeChild(item);
      }
      fragment.appendChild(item);
    } else {
      // Neues Item erstellen
      item = document.createElement('div');
      item.className = 'chat-item' + (isActive ? ' active' : '') + (isLoading ? ' loading' : '');
      item.dataset.chatId = chat.id;

      item.innerHTML = `
        <span class="chat-item-spinner"></span>
        <span class="chat-item-icon">💬</span>
        <span class="chat-item-title" title="${escapeHtml(chat.title)}">${escapeHtml(chat.title)}</span>
        <button class="chat-item-rename" title="Umbenennen">✏</button>
        <button class="chat-item-delete" title="Chat löschen">✕</button>`;

      // Click auf gesamtes Item (außer Buttons) wechselt zum Chat
      item.addEventListener('click', (e) => {
        // Nicht auslösen wenn auf Button geklickt
        if (e.target.closest('.chat-item-rename') || e.target.closest('.chat-item-delete')) {
          return;
        }
        switchToChat(chat.id);
      });
      item.querySelector('.chat-item-rename').addEventListener('click', (e) => {
        e.stopPropagation();
        startInlineRename(chat.id, item);
      });
      item.querySelector('.chat-item-delete').addEventListener('click', (e) => {
        e.stopPropagation();
        deleteChat(chat.id);
      });

      fragment.appendChild(item);
    }
  });

  // Geloeschte Chats entfernen
  existingItems.forEach((el, id) => {
    if (!newIds.has(id) && el.parentNode) {
      el.parentNode.removeChild(el);
    }
  });

  // Alle Items auf einmal einfuegen
  listEl.appendChild(fragment);
}

function startInlineRename(chatId, itemEl) {
  const chat = chatManager.get(chatId);
  if (!chat) return;

  const titleEl = itemEl.querySelector('.chat-item-title');
  const renameBtn = itemEl.querySelector('.chat-item-rename');

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'chat-item-rename-input';
  input.value = chat.title;

  titleEl.replaceWith(input);
  renameBtn.style.display = 'none';
  input.focus();
  input.select();

  const commit = () => {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== chat.title) {
      chat.title = newTitle;
      fetch(`/api/agent/session/${chat.sessionId}/title`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      }).catch(() => {});
    }
    renderChatList();
  };

  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = chat.title; input.blur(); }
  });
}

function renameChatPrompt() {
  const chat = chatManager.getActive();
  if (!chat) return;
  // Find the active item in the list and trigger inline rename
  const itemEl = document.querySelector(`.chat-item[data-chat-id="${chat.id}"]`);
  if (itemEl) startInlineRename(chat.id, itemEl);
}

function updateActiveChatTitle(firstUserMessage) {
  const chat = chatManager.getActive();
  if (!chat || chat.title !== 'Neuer Chat') return;
  chat.title = firstUserMessage.length > 40
    ? firstUserMessage.substring(0, 40) + '…'
    : firstUserMessage;
  // Titel auch auf dem Server persistieren
  fetch(`/api/agent/session/${chat.sessionId}/title`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: chat.title }),
  }).catch(() => {});
  renderChatList();
}

// escapeHtml defined in Utilities section (line ~4251) with null-safety

// ── Error UI Feedback ──
function showErrorToast(message, duration = TIMING.TOAST_ERROR) {
  // Existierenden Toast entfernen
  const existing = document.querySelector('.error-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = 'error-toast';
  toast.innerHTML = `
    <span class="error-toast-icon">⚠</span>
    <span class="error-toast-message">${escapeHtml(message)}</span>
    <button class="error-toast-close" title="Schliessen">✕</button>
  `;
  toast.querySelector('.error-toast-close').addEventListener('click', () => toast.remove());

  document.body.appendChild(toast);

  // Auto-dismiss
  if (duration > 0) {
    setTimeout(() => {
      if (toast.parentNode) {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), TIMING.DEBOUNCE);
      }
    }, duration);
  }
}

// Generic toast for success/error messages
function showToast(message, type = 'info', duration = TIMING.TOAST_DEFAULT) {
  // Existierenden Toast entfernen
  const existing = document.querySelector('.generic-toast');
  if (existing) existing.remove();

  const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
  const colors = {
    success: 'var(--success)',
    error: 'var(--danger)',
    info: 'var(--accent)',
    warning: 'var(--warning)'
  };

  const toast = document.createElement('div');
  toast.className = 'generic-toast';
  toast.style.cssText = `
    position: fixed;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--surface);
    border: 1px solid ${colors[type] || colors.info};
    color: var(--text);
    padding: 12px 20px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    gap: 10px;
    z-index: 10000;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: toast-slide-up 0.3s ease-out;
  `;
  toast.innerHTML = `
    <span style="color: ${colors[type] || colors.info}; font-size: 1.2em;">${icons[type] || icons.info}</span>
    <span>${escapeHtml(message)}</span>
  `;

  document.body.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => {
      if (toast.parentNode) {
        toast.style.animation = 'toast-fade-out 0.3s ease-in forwards';
        setTimeout(() => toast.remove(), 300);
      }
    }, duration);
  }
}

// Context bar HTML für jeden Chat-Pane
function _contextBarHTML() {
  return `
    <div class="chat-context-bar">
      <span class="context-status">
        <span class="context-icon">📊</span>
        <span class="context-text">– / –</span>
      </span>
    </div>
  `;
}

function welcomeHTML() {
  return `<div class="message system">
    <div class="message-bubble">
      <strong>Willkommen beim AI Code Assistant!</strong><br>
      Ich kann Code durchsuchen, das Handbuch nutzen und Dateien bearbeiten.<br>
      <small>Modus: <span id="welcome-mode">Nur Lesen</span> | Skills aktivieren im Header</small>
    </div>
  </div>`;
}

async function setAgentMode(mode) {
  // Session-ID des aktiven Chats verwenden
  const chat = chatManager.getActive();
  const sessionId = chat?.sessionId || state.sessionId;

  if (!sessionId) {
    log.error('No session ID available for mode change');
    appendMessage('error', 'Kein aktiver Chat für Modus-Wechsel');
    syncModeRadioButtons(state.mode);
    return;
  }

  try {
    const res = await fetch(`/api/agent/mode/${sessionId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });

    if (!res.ok) {
      const err = await res.json();
      appendMessage('error', `Modus-Wechsel fehlgeschlagen: ${err.detail}`);
      // Reset radio to current mode
      syncModeRadioButtons(state.mode);
      return;
    }

    const data = await res.json();
    const prevMode = state.mode;
    state.mode = data.mode;

    // Mode im aktiven Chat-Objekt speichern
    if (chat) {
      chat.mode = data.mode;
    }

    updateModeIndicator();
    log.info(`[setAgentMode] Mode changed from ${prevMode} to ${data.mode} for session ${sessionId}`);
  } catch (e) {
    appendMessage('error', 'Modus-Wechsel fehlgeschlagen: ' + e.message);
    syncModeRadioButtons(state.mode);
  }
}

// Radio-Buttons mit dem aktuellen Mode synchronisieren
function syncModeRadioButtons(mode) {
  const radio = document.querySelector(`input[name="agent-mode"][value="${mode}"]`);
  if (radio && !radio.checked) {
    radio.checked = true;
    log.info(`[Mode] Radio synced to: ${mode}`);
  }
}

// Vollständige Mode-Synchronisation (alle UI-Elemente)
function syncModeUI(mode) {
  state.mode = mode;
  syncModeRadioButtons(mode);
  updateModeIndicator();
  // Chat-Objekt updaten falls vorhanden
  const chat = chatManager.getActive();
  if (chat) {
    chat.mode = mode;
  }
}

function updateModeIndicator() {
  const indicator = document.getElementById('mode-indicator');
  const welcomeMode = document.getElementById('welcome-mode');

  indicator.className = 'mode-badge';

  switch (state.mode) {
    case 'read_only':
      indicator.classList.add('mode-read-only');
      indicator.innerHTML = '<span class="mode-icon">&#128274;</span><span class="mode-text">Nur Lesen</span>';
      if (welcomeMode) welcomeMode.textContent = 'Nur Lesen';
      break;
    case 'write_with_confirm':
      indicator.classList.add('mode-write');
      indicator.innerHTML = '<span class="mode-icon">&#128221;</span><span class="mode-text">Mit Bestätigung</span>';
      if (welcomeMode) welcomeMode.textContent = 'Mit Bestätigung';
      break;
    case 'autonomous':
      indicator.classList.add('mode-autonomous');
      indicator.innerHTML = '<span class="mode-icon">&#9888;</span><span class="mode-text">Autonom</span>';
      if (welcomeMode) welcomeMode.textContent = 'Autonom';
      break;
    case 'plan_then_execute':
      indicator.classList.add('mode-plan');
      indicator.innerHTML = '<span class="mode-icon">&#128203;</span><span class="mode-text">Plan &amp; Ausführen</span>';
      if (welcomeMode) welcomeMode.textContent = 'Plan & Ausführen';
      break;
    case 'debug':
      indicator.classList.add('mode-debug');
      indicator.innerHTML = '<span class="mode-icon">&#128269;</span><span class="mode-text">Debug</span>';
      if (welcomeMode) welcomeMode.textContent = 'Debug';
      break;
  }
}

// ── Suggestion Bar (Debug-Modus Rückfragen) ──────────────────────────────────

/**
 * Zeigt Antwort-Vorschläge als klickbare Chips über dem Input-Feld an.
 * Wird vom QUESTION-SSE-Event getriggert wenn der Agent suggest_answers aufruft.
 */
function showSuggestions(question, options) {
  const bar = document.getElementById('suggestion-bar');
  const questionEl = document.getElementById('suggestion-question');
  const chipsEl = document.getElementById('suggestion-chips');

  if (!bar || !chipsEl) return;

  questionEl.textContent = question || '';
  chipsEl.innerHTML = '';

  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.className = 'suggestion-chip';
    btn.textContent = opt;
    btn.addEventListener('click', () => {
      hideSuggestions();
      const input = document.getElementById('message-input');
      input.value = opt;
      sendMessage();
    });
    chipsEl.appendChild(btn);
  });

  bar.style.display = 'flex';
}

function hideSuggestions() {
  const bar = document.getElementById('suggestion-bar');
  if (bar) bar.style.display = 'none';
}

// Suggestions ausblenden wenn User selbst zu tippen beginnt
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('message-input');
  if (input) {
    input.addEventListener('keydown', (e) => {
      // Nur ausblenden wenn User echten Text tippt (keine Navigation)
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
        hideSuggestions();
      }
    });
  }
});

// ── Models ──
async function loadModels() {
  try {
    const res = await fetch('/api/models');
    const data = await res.json();
    const sel = document.getElementById('model-select');
    sel.innerHTML = '';

    data.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.display_name;
      if (m.id === data.default) opt.selected = true;
      sel.appendChild(opt);
    });

    state.currentModel = sel.value;
    sel.addEventListener('change', () => {
      const prevModel = state.currentModel;
      state.currentModel = sel.value;
      log.info(`[Model] Changed from ${prevModel} to ${sel.value}, mode remains: ${state.mode}`);
      // Mode UI-Sync sicherstellen (defensiv - sollte nicht nötig sein)
      syncModeRadioButtons(state.mode);
    });
  } catch (e) {
    log.error('Failed to load models:', e);
    showErrorToast('Modelle konnten nicht geladen werden');
  }
}

// ── Skills ──
async function loadSkills() {
  try {
    const res = await fetch('/api/skills');
    if (!res.ok) {
      document.getElementById('skills-list').innerHTML = '<div class="empty-state"><p>Skills nicht verfügbar</p></div>';
      return;
    }

    const skills = await res.json();
    state.availableSkills = skills;
    renderSkillsList(skills);
  } catch (e) {
    document.getElementById('skills-list').innerHTML = '<div class="empty-state"><p>Skills nicht verfügbar</p></div>';
  }
}

function renderSkillsList(skills) {
  const container = document.getElementById('skills-list');

  if (!skills || skills.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>Keine Skills verfügbar</p></div>';
    return;
  }

  container.innerHTML = skills.map(skill => `
    <div class="skill-item ${state.activeSkills.includes(skill.id) ? 'active' : ''}"
         onclick="toggleSkill('${skill.id}')">
      <input type="checkbox" class="skill-checkbox"
             ${state.activeSkills.includes(skill.id) ? 'checked' : ''}>
      <div class="skill-info">
        <div class="skill-name">${escapeHtml(skill.name)}</div>
        <div class="skill-desc">${escapeHtml(skill.description || '')}</div>
      </div>
      <span class="skill-type">${skill.type}</span>
    </div>
  `).join('');
}

async function toggleSkill(skillId) {
  const isActive = state.activeSkills.includes(skillId);

  try {
    const endpoint = isActive ? 'deactivate' : 'activate';
    const res = await fetch(`/api/skills/${skillId}/${endpoint}?session_id=${state.sessionId}`, {
      method: 'POST'
    });

    if (res.ok) {
      if (isActive) {
        state.activeSkills = state.activeSkills.filter(id => id !== skillId);
      } else {
        state.activeSkills.push(skillId);
      }
      renderSkillsList(state.availableSkills);
      updateActiveSkillsCount();
    }
  } catch (e) {
    log.error('Failed to toggle skill:', e);
    showErrorToast('Skill konnte nicht aktiviert werden');
  }
}

function updateActiveSkillsCount() {
  document.getElementById('active-skills-count').textContent = `(${state.activeSkills.length})`;
}

function toggleSkillsDropdown() {
  const dropdown = document.getElementById('skills-dropdown');
  dropdown.classList.toggle('open');

  // Close on outside click
  if (dropdown.classList.contains('open')) {
    setTimeout(() => {
      document.addEventListener('click', closeSkillsDropdown);
    }, 0);
  }
}

function closeSkillsDropdown(e) {
  const dropdown = document.getElementById('skills-dropdown');
  const btn = document.getElementById('skills-btn');

  if (!dropdown.contains(e.target) && !btn.contains(e.target)) {
    dropdown.classList.remove('open');
    document.removeEventListener('click', closeSkillsDropdown);
  }
}

// ── Chat / Agent Communication ──

// Aktiver AbortController für laufende Anfragen
let _chatAbortController = null;
// AbortController für switchToChat - verhindert Race Conditions bei schnellem Wechseln
let _switchChatAbortController = null;

function _setStreamingMode(active) {
  const sendBtn = document.getElementById('send-btn');
  if (active) {
    sendBtn.title = 'Anfrage abbrechen';
    sendBtn.classList.add('cancel-mode');
    sendBtn.innerHTML = '<span class="cancel-icon">&#9632;</span>';
    sendBtn.onclick = cancelRequest;
  } else {
    sendBtn.title = '';
    sendBtn.classList.remove('cancel-mode');
    sendBtn.innerHTML = '<span class="send-icon">&#10148;</span>';
    sendBtn.onclick = sendMessage;
  }
}

async function cancelRequest() {
  const activeChat = chatManager.getActive();
  const ac = activeChat?.streamingState?.abortController ?? _chatAbortController;
  if (ac) ac.abort();
  _chatAbortController = null;
  try {
    await fetch(`/api/agent/cancel/${state.sessionId}`, { method: 'POST' });
  } catch (_) { /* ignore */ }
  _setStreamingMode(false);
}

// ══════════════════════════════════════════════════════════════════════════════
// Chat Slash-Command-Router + Autocomplete
// ══════════════════════════════════════════════════════════════════════════════

// Alle Befehle mit Beschreibung (für Autocomplete)
const _CMD_LIST = [
  // Modus-Befehle
  { cmd: '/lesen',      desc: 'Modus: Nur Lesen 🔒',             alias: '/r' },
  { cmd: '/schreiben',  desc: 'Modus: Schreiben mit Bestätigung ✏️', alias: '/s' },
  { cmd: '/plan',       desc: 'Modus: Plan & Ausführen 📋',       alias: '/p' },
  { cmd: '/auto',       desc: 'Modus: Autonom ⚠️',               alias: '/a' },
  { cmd: '/debug',      desc: 'Modus: Debug & Fehleranalyse 🔍',  alias: '/d' },
  // MCP Capabilities
  { cmd: '/brainstorm', desc: 'MCP: Ideen & Requirements 💡',     alias: '/bs' },
  { cmd: '/design',     desc: 'MCP: Architektur & Design 📐',     alias: '/des' },
  { cmd: '/implement',  desc: 'MCP: Code-Generierung 💻',         alias: '/impl' },
  { cmd: '/analyze',    desc: 'MCP: Code-Analyse 🔍',             alias: '/ana' },
  { cmd: '/seq',        desc: 'MCP: Sequential Thinking 🧠',      alias: null },
  // Sonstige
  { cmd: '/suche an',   desc: 'Web-Suche aktivieren 🔍',          alias: null },
  { cmd: '/suche aus',  desc: 'Web-Suche deaktivieren',           alias: null },
  { cmd: '/neu',        desc: 'Neuen Chat öffnen',                alias: '/neuer chat' },
  { cmd: '/hilfe',      desc: 'Alle Befehle anzeigen',            alias: '/?' },
];

function _commandDropdownVisible() {
  const el = document.getElementById('cmd-suggestions');
  return el && el.style.display !== 'none';
}

function _updateCommandSuggestions(text) {
  let el = document.getElementById('cmd-suggestions');
  if (!el) {
    el = document.createElement('div');
    el.id = 'cmd-suggestions';
    el.className = 'cmd-suggestions-dropdown';
    const input = document.getElementById('message-input');
    input.parentElement.style.position = 'relative';
    input.parentElement.appendChild(el);
  }

  if (!text.startsWith('/') || text.includes('\n')) {
    el.style.display = 'none';
    return;
  }

  const filter = text.toLowerCase();
  const matches = _CMD_LIST.filter(c =>
    c.cmd.startsWith(filter) || (c.alias && c.alias.startsWith(filter))
  );

  if (!matches.length) { el.style.display = 'none'; return; }

  el.innerHTML = matches.map((c, i) => `
    <div class="cmd-suggestion ${i === 0 ? 'active' : ''}" data-cmd="${escapeHtml(c.cmd)}"
         onclick="_applyCommandSuggestion('${escapeHtml(c.cmd)}')">
      <span class="cmd-name">${escapeHtml(c.cmd)}</span>
      <span class="cmd-desc">${c.desc}</span>
      ${c.alias ? `<span class="cmd-alias">${escapeHtml(c.alias)}</span>` : ''}
    </div>
  `).join('');
  el.style.display = 'block';
}

function _hideCommandSuggestions() {
  const el = document.getElementById('cmd-suggestions');
  if (el) el.style.display = 'none';
}

function _commandSelectNext(dir) {
  const items = document.querySelectorAll('.cmd-suggestion');
  if (!items.length) return;
  const current = [...items].findIndex(i => i.classList.contains('active'));
  const next = (current + dir + items.length) % items.length;
  items.forEach((el, i) => el.classList.toggle('active', i === next));
}

function _applyCommandSuggestion(cmd) {
  const input = document.getElementById('message-input');
  input.value = cmd + ' ';
  input.focus();
  _hideCommandSuggestions();
  // Dropdown erneut triggern für Unterparameter (z.B. "/mode ")
  _updateCommandSuggestions(input.value);
}

const _COMMANDS = {
  // Modus-Befehle
  'lesen':     () => setAgentMode('read_only'),
  'r':         () => setAgentMode('read_only'),
  'schreiben': () => setAgentMode('write_with_confirm'),
  's':         () => setAgentMode('write_with_confirm'),
  'plan':      () => setAgentMode('plan_then_execute'),
  'p':         () => setAgentMode('plan_then_execute'),
  'auto':      () => setAgentMode('autonomous'),
  'a':         () => setAgentMode('autonomous'),

  // Web-Suche
  'suche an':    () => _searchSetEnabled(true),
  'suche aus':   () => _searchSetEnabled(false),
  'search on':   () => _searchSetEnabled(true),
  'search off':  () => _searchSetEnabled(false),

  // Chat-Management
  'neu':         () => chatManager.createChat(),
  'neuer chat':  () => chatManager.createChat(),
  'new':         () => chatManager.createChat(),

  // Hilfe
  'hilfe':  null,
  'help':   null,
  '?':      null,
};

const _MODE_LABELS = {
  read_only:          '&#128274; Nur Lesen',
  write_with_confirm: '&#128221; Schreiben (mit Bestätigung)',
  plan_then_execute:  '&#128203; Plan & Ausführen',
  autonomous:         '&#9888; Autonom',
  debug:              '&#128269; Debug & Fehleranalyse',
};

async function _searchSetEnabled(enabled) {
  await fetch('/api/search/toggle', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  // Sync toggle-Elemente
  ['search-enabled-toggle', 'search-settings-toggle'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.checked = enabled;
  });
  const txt = document.getElementById('search-status-text');
  if (txt) {
    txt.textContent = enabled
      ? 'Aktiviert – Agent kann Internet-Suchen anfragen (Bestätigung erforderlich)'
      : 'Deaktiviert – Agent kann keine Internet-Suchen durchführen';
    txt.style.color = enabled ? 'var(--success)' : 'var(--text-muted)';
  }
  return `Web-Suche ${enabled ? 'aktiviert ✓' : 'deaktiviert'}.`;
}

function _buildHelpText() {
  return `**Chat-Befehle** (beginnen mit \`/\`)

**Modus wechseln:**
\`/lesen\` \`/r\`  → Nur Lesen &#128274;
\`/schreiben\` \`/s\`  → Schreiben mit Bestätigung &#128221;
\`/plan\` \`/p\`  → Plan & Ausführen &#128203;
\`/auto\` \`/a\`  → Autonom &#9888;
\`/debug\` \`/d\`  → Debug & Fehleranalyse &#128269;

**MCP Capabilities:**
\`/brainstorm\` \`/bs\`  → Ideen & Requirements Discovery 💡
\`/design\` \`/des\`  → Architektur & System-Design 📐
\`/implement\` \`/impl\`  → Code-Generierung 💻
\`/analyze\` \`/ana\`  → Code-Analyse & Review 🔍
\`/seq\`  → Sequential Thinking (tiefgehende Analyse) 🧠

_Beispiel: \`/brainstorm Neues Feature für User-Login\`_

**Web-Suche:**
\`/suche an\`  → Web-Suche aktivieren
\`/suche aus\`  → Web-Suche deaktivieren

**Chat:**
\`/neu\`  → Neuen Chat öffnen

\`/hilfe\` \`/?\`  → Diese Hilfe anzeigen`;
}

/**
 * Verarbeitet Slash-Befehle aus dem Chat-Eingabefeld.
 * @returns {boolean} true wenn der Befehl erkannt und verarbeitet wurde
 */
async function handleChatCommand(text) {
  // Normalisieren: "/Mode Lesen" → "mode lesen", "/ plan" → "plan"
  const raw = text.slice(1).replace(/\s+/g, ' ').trim().toLowerCase();
  log.info('[cmd] Befehl erkannt:', { original: text, normalized: raw });

  // Hilfe
  if (raw === 'hilfe' || raw === 'help' || raw === '?') {
    appendMessage('system', _buildHelpText());
    return true;
  }

  // Modus-Shortcuts: /mode lesen | /lesen | /r | usw.
  // Unterstütze auch "/mode schreiben" als Alias
  const modePrefix = raw.startsWith('mode ') ? raw.slice(5).trim() : raw;

  const modeMap = {
    // Lesen
    'lesen': 'read_only',
    'r': 'read_only',
    'read': 'read_only',
    'read_only': 'read_only',
    'readonly': 'read_only',
    // Schreiben
    'schreiben': 'write_with_confirm',
    's': 'write_with_confirm',
    'write': 'write_with_confirm',
    'write_with_confirm': 'write_with_confirm',
    // Plan
    'plan': 'plan_then_execute',
    'p': 'plan_then_execute',
    'planning': 'plan_then_execute',
    'plan_then_execute': 'plan_then_execute',
    // Auto
    'auto': 'autonomous',
    'a': 'autonomous',
    'autonomous': 'autonomous',
    // Debug
    'debug': 'debug',
    'd': 'debug',
  };

  if (Object.prototype.hasOwnProperty.call(modeMap, modePrefix)) {
    const modeKey = modeMap[modePrefix];
    log.info('[cmd] Modus-Befehl erkannt:', { modePrefix, modeKey });
    await setAgentMode(modeKey);
    appendMessage('system', `Modus gewechselt: ${_MODE_LABELS[modeKey] || modeKey}`);
    return true;
  }

  // Suche
  if (raw === 'suche an' || raw === 'search on') {
    const msg = await _searchSetEnabled(true);
    appendMessage('system', msg);
    return true;
  }
  if (raw === 'suche aus' || raw === 'search off') {
    const msg = await _searchSetEnabled(false);
    appendMessage('system', msg);
    return true;
  }

  // Neuer Chat
  if (raw === 'neu' || raw === 'new' || raw === 'neuer chat' || raw === 'new chat') {
    chatManager.createChat();
    appendMessage('system', 'Neuer Chat geöffnet.');
    return true;
  }

  // ── MCP Capability Commands ───────────────────────────────────────────────
  const capabilityMap = {
    'brainstorm': { name: 'brainstorm', icon: '💡', label: 'Brainstorm' },
    'bs': { name: 'brainstorm', icon: '💡', label: 'Brainstorm' },
    'brain': { name: 'brainstorm', icon: '💡', label: 'Brainstorm' },
    'design': { name: 'design', icon: '📐', label: 'Design' },
    'des': { name: 'design', icon: '📐', label: 'Design' },
    'arch': { name: 'design', icon: '📐', label: 'Design' },
    'implement': { name: 'implement', icon: '💻', label: 'Implement' },
    'impl': { name: 'implement', icon: '💻', label: 'Implement' },
    'code': { name: 'implement', icon: '💻', label: 'Implement' },
    'analyze': { name: 'analyze', icon: '🔍', label: 'Analyze' },
    'ana': { name: 'analyze', icon: '🔍', label: 'Analyze' },
    'review': { name: 'analyze', icon: '🔍', label: 'Analyze' },
    // /seq für explizites Sequential Thinking MCP (tiefgehende Analyse)
    'seq': { name: 'sequential_thinking', icon: '🧠', label: 'Sequential Thinking' },
  };

  // Parse: /brainstorm Was soll das Feature können?
  const parts = raw.split(' ');
  const cmdKey = parts[0];
  const capQuery = parts.slice(1).join(' ').trim();

  if (capabilityMap[cmdKey]) {
    const cap = capabilityMap[cmdKey];
    log.info('[cmd] MCP Capability:', { capability: cap.name, query: capQuery });

    if (!capQuery) {
      appendMessage('system',
        `${cap.icon} **${cap.label}** benötigt eine Anfrage.\n` +
        `Beispiel: \`/${cmdKey} Beschreibe hier dein Vorhaben\``
      );
      return true;
    }

    // Capability-spezifischen Prefix an die Nachricht anhängen
    // Das signalisiert dem Agent, welches Tool forciert werden soll
    const prefixedMessage = `[MCP:${cap.name}] ${capQuery}`;

    appendMessage('system', `${cap.icon} **${cap.label}** wird ausgeführt...`);

    // Message mit Capability-Marker senden (wird nicht als handled markiert)
    const input = document.getElementById('message-input');
    input.value = prefixedMessage;
    return false;  // false = normal senden mit prefixed message
  }
  // ─────────────────────────────────────────────────────────────────────────

  // Unbekannter Befehl → System-Hinweis, aber trotzdem als normaler Text weiterleiten
  log.info('[cmd] Unbekannter Befehl:', { raw, modePrefix });
  appendMessage('system',
    `Unbekannter Befehl \`/${raw}\`. Tippe \`/hilfe\` für alle Befehle.\n` +
    `Die Nachricht wird dennoch an den Agenten gesendet.`
  );
  return false;  // false = weiter normal senden
}

async function sendMessage() {
  const input = document.getElementById('message-input');
  const text = input.value.trim();
  if (!text) return;

  // ── Slash-Command-Router ─────────────────────────────────────────────────
  if (text.startsWith('/')) {
    const handled = await handleChatCommand(text);
    if (handled) {
      input.value = '';
      input.style.height = 'auto';
      return;
    }
  }
  // ─────────────────────────────────────────────────────────────────────────

  // ── PR Detection: Load PR Review Panel when PR link is sent ──────────────
  const prInfo = detectPRInMessage(text);
  if (prInfo && prInfo.repoOwner && prInfo.repoName) {
    // Load PR review in background - don't block message sending
    loadPRReview(prInfo.repoOwner, prInfo.repoName, prInfo.prNumber);
  }
  // ─────────────────────────────────────────────────────────────────────────

  const activeChat = chatManager.getActive();
  // Verhindere Doppel-Senden wenn dieser Chat bereits streamt
  if (activeChat?.streamingState || _chatAbortController) return;

  input.value = '';
  input.style.height = 'auto';
  hideSuggestions();
  appendMessage('user', text);

  updateActiveChatTitle(text);

  const ac = new AbortController();
  activeChat.streamingState = {
    abortController: ac,
    statusBar: null,
    startTime: null,
    liveTokenCount: 0,
    timerInterval: null,
  };
  _chatAbortController = ac;
  _setStreamingMode(true);

  try {
    await sendAgentChat(text, ac.signal, activeChat);
  } catch (e) {
    if (e.name !== 'AbortError') {
      appendMessageToPane(activeChat.pane, 'error', 'Fehler: ' + e.message);
    }
  } finally {
    activeChat.streamingState = null;
    // _chatAbortController nur zurücksetzen wenn dieser Chat noch aktiv ist
    if (chatManager.activeId === activeChat.id) {
      _chatAbortController = null;
      _setStreamingMode(false);
    }
  }
}

/**
 * Sendet eine interne Chat-Nachricht (z.B. für Continue nach Bestätigung)
 * Zeigt die Nachricht nicht im Chat an, wenn sie mit [ beginnt.
 */
async function sendChatInternal(message) {
  const activeChat = chatManager.getActive();
  if (!activeChat) return;

  // Verhindere Doppel-Senden wenn bereits am Streamen
  if (activeChat.streamingState || _chatAbortController) {
    log.info('[sendChatInternal] Bereits am Streamen, überspringe');
    return;
  }

  // Nur interne Nachrichten ([CONTINUE], etc.) nicht anzeigen
  if (!message.startsWith('[')) {
    appendMessage('user', message);
    updateActiveChatTitle(message);
  }

  const ac = new AbortController();
  activeChat.streamingState = {
    abortController: ac,
    statusBar: null,
    startTime: null,
    liveTokenCount: 0,
    timerInterval: null,
  };
  _chatAbortController = ac;
  _setStreamingMode(true);

  try {
    await sendAgentChat(message, ac.signal, activeChat);
  } catch (e) {
    if (e.name !== 'AbortError') {
      appendMessageToPane(activeChat.pane, 'error', 'Fehler: ' + e.message);
    }
  } finally {
    activeChat.streamingState = null;
    if (chatManager.activeId === activeChat.id) {
      _chatAbortController = null;
      _setStreamingMode(false);
    }
  }
}

async function sendAgentChat(message, abortSignal, chat) {
  const ctx = chat.context || state.context;
  const payload = {
    message,
    session_id: chat.sessionId,
    model: state.currentModel,
    skill_ids: state.activeSkills.length > 0 ? state.activeSkills : null,
    context: {
      java_files: ctx.javaFiles.map(f => f.path),
      python_files: ctx.pythonFiles.map(f => f.path),
      pdf_ids: ctx.pdfIds.map(p => p.id),
      handbook_services: ctx.handbookServices.map(s => s.id),
    },
  };

  const res = await fetch('/api/agent/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: abortSignal,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    stopChatTimer(chat);
    throw new Error(err.detail || res.statusText);
  }

  // Nachrichten-Div im Chat-Pane erstellen (funktioniert auch wenn Pane detached ist)
  const msgDiv = appendMessageToPane(chat.pane, 'assistant', '');
  const bubble = msgDiv.querySelector('.message-bubble');
  let fullText = '';

  // Status-Bar und Timer per-Chat starten
  const statusBar = createLiveStatusBar();
  msgDiv.appendChild(statusBar);
  chat.streamingState.statusBar = statusBar;
  chat.streamingState.liveTokenCount = 0;
  startChatTimer(chat);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    let value, done;
    try {
      ({ value, done } = await reader.read());
    } catch (e) {
      if (e.name === 'AbortError') break;
      throw e;
    }

    if (done) {
      if (buffer.trim()) {
        for (const line of buffer.split('\n')) {
          if (!line.startsWith('data:')) continue;
          try {
            const event = JSON.parse(line.slice(5).trim());
            await processAgentEvent(event, bubble, msgDiv, chat);
            if (event.type === 'token' && event.data) {
              fullText += event.data;
              bubble.innerHTML = marked.parse(fullText);
              applyHighlight(bubble);
              if (document.contains(chat.pane)) scrollToBottom();
              chat.streamingState.liveTokenCount += countTokensApprox(event.data);
              updateChatStatusBar(chat);
            }
          } catch (e) { /* ignore */ }
        }
      }
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      try {
        const event = JSON.parse(line.slice(5).trim());
        await processAgentEvent(event, bubble, msgDiv, chat);
        if (event.type === 'token' && event.data) {
          fullText += event.data;
          bubble.innerHTML = marked.parse(fullText);
          applyHighlight(bubble);
          if (document.contains(chat.pane)) scrollToBottom();
          chat.streamingState.liveTokenCount += countTokensApprox(event.data);
          updateChatStatusBar(chat);
        }
      } catch (e) {
        // Ignore parse errors for partial chunks
      }
    }
  }

  stopChatTimer(chat);

  if (fullText) {
    bubble.innerHTML = marked.parse(fullText);
    applyHighlight(bubble);
  }
  if (document.contains(chat.pane)) scrollToBottom();
}

// ── Live Status Bar ──
function createLiveStatusBar() {
  const statusBar = document.createElement('div');
  statusBar.className = 'live-status-bar';
  statusBar.innerHTML = `
    <div class="status-timer">
      <span class="status-icon">⏱️</span>
      <span class="timer-value">0:00</span>
    </div>
    <div class="status-tokens">
      <span class="status-icon">📊</span>
      <span class="tokens-value">0 tokens</span>
    </div>
    <div class="status-reasoning" style="display:none">
      <span class="status-icon">🧠</span>
      <span class="reasoning-level">Reasoning</span>
    </div>
    <div class="status-indicator">
      <span class="pulse-dot"></span>
      <span>Verarbeite...</span>
    </div>
  `;
  return statusBar;
}

// ── Per-Chat Timer (läuft auch wenn Pane detached ist) ──

function startChatTimer(chat) {
  if (!chat.streamingState) return;
  chat.streamingState.startTime = Date.now();
  // Interval hält eine Closure auf chat – kein globaler State nötig
  chat.streamingState.timerInterval = setInterval(() => updateChatStatusBar(chat), 100);
}

function stopChatTimer(chat) {
  const ss = chat.streamingState;
  if (!ss) return;
  if (ss.timerInterval) {
    clearInterval(ss.timerInterval);
    ss.timerInterval = null;
  }
  if (ss.statusBar) {
    const indicator = ss.statusBar.querySelector('.status-indicator');
    if (indicator) indicator.innerHTML = `<span class="status-done">✓</span><span>Fertig</span>`;
    ss.statusBar.classList.add('done');
  }
}

function updateChatStatusBar(chat) {
  const ss = chat.streamingState;
  if (!ss?.statusBar || !ss.startTime) return;

  const elapsed = Date.now() - ss.startTime;
  const seconds = Math.floor(elapsed / 1000);
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  const ms = Math.floor((elapsed % 1000) / 100);

  const timerEl = ss.statusBar.querySelector('.timer-value');
  if (timerEl) timerEl.textContent = `${minutes}:${secs.toString().padStart(2, '0')}.${ms}`;

  const tokensEl = ss.statusBar.querySelector('.tokens-value');
  if (tokensEl) tokensEl.textContent = `~${ss.liveTokenCount} tokens`;
}

function countTokensApprox(text) {
  // Grobe Schätzung: ~4 Zeichen pro Token (für Deutsch/Englisch)
  return Math.ceil(text.length / 4);
}

async function processAgentEvent(event, bubble, msgDiv, chat) {
  const { type, data } = event;
  const isActive = chat.id === chatManager.activeId;

  switch (type) {
    case 'tool_start': {
      const toolCard = createToolCard(data.name, data.arguments, 'running', data.model);
      bubble.appendChild(toolCard);
      // Tool-History per-Chat pflegen, bei aktivem Chat in state spiegeln
      chat.toolHistory.unshift({ id: data.id, name: data.name, args: data.arguments, status: 'running', result: null });
      if (isActive) { state.toolHistory = [...chat.toolHistory]; renderToolHistory(); }
      if (document.contains(chat.pane)) scrollToBottom();
      break;
    }
    case 'tool_result': {
      updateToolCard(data.id, data.success ? 'success' : 'error', data.data, chat.pane);
      const tool = chat.toolHistory[0];
      if (tool) { tool.status = data.success ? 'success' : 'error'; tool.result = data.data; }
      if (isActive) { state.toolHistory = [...chat.toolHistory]; renderToolHistory(); }
      break;
    }
    case 'confirm_required':
      chat.pendingConfirmation = data;
      if (isActive) {
        state.pendingConfirmation = data;
        showConfirmationPanel(data);
        switchRightPanel('confirm-panel');
      }
      break;

    case 'waiting_for_confirmation':
      appendMessageToPane(chat.pane, 'system', 'Warte auf Bestätigung für Schreib-Operation...');
      break;

    case 'confirmed':
      chat.pendingConfirmation = null;
      if (isActive) { state.pendingConfirmation = null; hideConfirmationPanel(); }
      appendMessageToPane(chat.pane, 'system', `✓ ${data.message}`);
      break;

    case 'cancelled':
      if (isActive) hideConfirmationPanel();
      stopChatTimer(chat);
      appendMessageToPane(chat.pane, 'system', `⏹ ${data.message || 'Anfrage abgebrochen'}`);
      break;

    case 'subagent_start': {
      // Routing läuft – noch keine Agenten bekannt → Karte mit Routing-Indikator
      const card = createSubAgentCard([], data.routing_model || '');
      bubble.appendChild(card);
      chat.subAgentCard = card;
      if (document.contains(chat.pane)) scrollToBottom();
      break;
    }
    case 'subagent_routing': {
      // Routing fertig – ausgewählte Agenten jetzt bekannt
      if (chat.subAgentCard) {
        populateSubAgentCard(chat.subAgentCard, data.agents || [], data.routing_model || '');
      }
      if (document.contains(chat.pane)) scrollToBottom();
      break;
    }
    case 'subagent_done': {
      if (chat.subAgentCard) {
        updateSubAgentCard(chat.subAgentCard, 'done', data);
      }
      break;
    }
    case 'subagent_error': {
      if (chat.subAgentCard) {
        updateSubAgentCard(chat.subAgentCard, 'error', data);
      } else if (data.error && !data.agent) {
        // Globaler Fehler (kein Agent-Name) → Systemmeldung
        appendMessageToPane(chat.pane, 'error', `Sub-Agent Fehler: ${data.error}`);
      }
      break;
    }

    // Enhancement Events
    case 'enhancement_start':
      handleEnhancementStart(data, chat);
      break;

    case 'enhancement_complete':
      handleEnhancementComplete(data, chat);
      break;

    case 'enhancement_confirmed':
      handleEnhancementConfirmed(data, chat);
      break;

    case 'enhancement_rejected':
      handleEnhancementRejected(data, chat);
      break;

    // Web Fallback Events
    case 'web_fallback_required':
      handleWebFallbackRequired(data, chat);
      break;

    // Workspace Events
    case 'workspace_code_change':
      addCodeChangeToWorkspace(data);
      break;

    case 'workspace_sql_result':
      addSqlResultToWorkspace(data);
      break;

    case 'workspace_research':
      addResearchResultToWorkspace(data);
      break;

    case 'workspace_file':
      addFileToWorkspace(data);
      break;

    case 'workspace_pr':
      openPRFromEvent(data);
      break;

    case 'compaction': {
      const saved = data.savings ? ` (−${data.savings} Tokens)` : '';
      appendMessageToPane(chat.pane, 'system', `♻ Konversation komprimiert${saved}`);
      break;
    }

    case 'plan_ready': {
      const planCard = createPlanCard(data.plan, chat);
      bubble.appendChild(planCard);
      chat.pendingPlan = data;
      if (isActive) state.pendingPlan = data;
      if (document.contains(chat.pane)) scrollToBottom();
      break;
    }

    case 'error':
      appendMessageToPane(chat.pane, 'error', data.error || 'Unbekannter Fehler');
      break;

    case 'usage':
      displayTokenUsage(data, chat);
      break;

    case 'context_status':
      updateContextIndicator(data, chat);
      break;

    case 'reasoning_status':
      updateReasoningIndicator(data, chat);
      break;

    case 'compaction':
      showCompactionNotification(data);
      break;

    case 'done':
      if (data.usage) displayTokenUsage(data.usage, chat);
      hideSuggestions();  // Rückfrage-Buttons ausblenden wenn Antwort fertig
      break;

    case 'question':
      showSuggestions(data.question, data.options || []);
      break;

    // ── MCP Thinking Events (explizit via /seq) ──
    case 'mcp_start': {
      showThinkingPanel(data, chat);
      break;
    }
    case 'mcp_step': {
      addThinkingStep(data, chat);
      break;
    }
    case 'mcp_progress': {
      updateThinkingProgress(data, chat);
      break;
    }
    case 'mcp_complete': {
      completeThinking(data, chat);
      break;
    }
    case 'mcp_error': {
      showThinkingError(data, chat);
      break;
    }
    // v2: Extended MCP Events
    case 'mcp_branch_start': {
      handleMcpBranchStart(data, chat);
      break;
    }
    case 'mcp_branch_end': {
      handleMcpBranchEnd(data, chat);
      break;
    }
    case 'mcp_assumption_created': {
      handleMcpAssumption(data, chat);
      break;
    }
    case 'mcp_tool_recommendation': {
      handleMcpToolRec(data, chat);
      break;
    }

    // ── Task Decomposition Events ──
    case 'task_plan_created': {
      handleTaskPlanCreated(data, chat, bubble);
      break;
    }
    case 'task_started': {
      handleTaskStarted(data, chat);
      break;
    }
    case 'task_completed': {
      handleTaskCompleted(data, chat);
      break;
    }
    case 'task_failed': {
      handleTaskFailed(data, chat);
      break;
    }
    case 'task_execution_complete': {
      handleTaskExecutionComplete(data, chat);
      break;
    }
  }
}

// Kontext-Anzeige aktualisieren (per-chat)
function updateContextIndicator(data, chat) {
  // Store in chat object for persistence across switches
  if (chat) {
    chat.contextStatus = data;
  }

  // Find context bar in chat's pane
  const pane = chat?.pane || chatManager.getActive()?.pane;
  if (!pane) {
    console.debug('[Context] No pane found for context indicator');
    return;
  }

  const bar = pane.querySelector('.chat-context-bar');
  if (!bar) {
    console.debug('[Context] No context bar found in pane');
    return;
  }

  const percent = data.percent || 0;
  const current = (data.current_tokens || 0).toLocaleString();
  const limit = (data.limit_tokens || 0).toLocaleString();

  // Icon basierend auf Auslastung
  let icon = '📊';
  let colorClass = '';
  if (percent > 95) {
    icon = '🔴';
    colorClass = 'context-critical';
  } else if (percent > 80) {
    icon = '🟡';
    colorClass = 'context-warning';
  } else if (percent > 60) {
    icon = '🟢';
    colorClass = 'context-ok';
  }

  // Update icon and text
  const iconSpan = bar.querySelector('.context-icon');
  const textSpan = bar.querySelector('.context-text');
  if (iconSpan) iconSpan.textContent = icon;
  if (textSpan) textSpan.textContent = `${current} / ${limit} (${percent}%)`;

  // Update color class on status span
  const statusSpan = bar.querySelector('.context-status');
  if (statusSpan) {
    statusSpan.classList.remove('context-critical', 'context-warning', 'context-ok');
    if (colorClass) statusSpan.classList.add(colorClass);
  }

  // Iteration anzeigen wenn vorhanden
  let iterSpan = bar.querySelector('.context-iteration');
  if (data.iteration && data.max_iterations) {
    if (!iterSpan) {
      iterSpan = document.createElement('span');
      iterSpan.className = 'context-iteration';
      bar.appendChild(iterSpan);
    }
    iterSpan.textContent = `Step ${data.iteration}/${data.max_iterations}`;
    iterSpan.style.display = '';
  } else if (iterSpan) {
    iterSpan.style.display = 'none';
  }
}

// Reasoning-Indikator in der Status-Bar aktualisieren
function updateReasoningIndicator(data, chat) {
  const ss = chat?.streamingState;
  if (!ss?.statusBar) return;

  const reasoningDiv = ss.statusBar.querySelector('.status-reasoning');
  if (!reasoningDiv) return;

  if (data.active && data.level) {
    // Reasoning aktiv - anzeigen
    const levelSpan = reasoningDiv.querySelector('.reasoning-level');
    const levelLabels = {
      'low': 'Reasoning: Low',
      'medium': 'Reasoning: Medium',
      'high': 'Reasoning: High'
    };
    if (levelSpan) {
      levelSpan.textContent = levelLabels[data.level] || `Reasoning: ${data.level}`;
    }
    reasoningDiv.style.display = 'flex';
    reasoningDiv.classList.add('reasoning-active');
  } else {
    // Reasoning inaktiv - ausblenden
    reasoningDiv.style.display = 'none';
    reasoningDiv.classList.remove('reasoning-active');
  }
}

// Komprimierungs-Benachrichtigung
function showCompactionNotification(data) {
  const saved = (data.saved_tokens || 0).toLocaleString();
  const count = data.compaction_count || 1;

  // Toast-artige Benachrichtigung
  const toast = document.createElement('div');
  toast.className = 'compaction-toast';
  toast.innerHTML = `
    <span>🗜️ Kontext komprimiert: ${saved} Tokens eingespart</span>
    <small>(Komprimierung #${count})</small>
  `;
  toast.style.cssText = `
    position: fixed;
    bottom: 80px;
    right: 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    z-index: 1000;
    animation: slideIn 0.3s ease-out;
  `;

  document.body.appendChild(toast);

  // Nach 3 Sekunden ausblenden
  setTimeout(() => {
    toast.style.animation = 'slideOut 0.3s ease-in';
    setTimeout(() => toast.remove(), 300);
  }, 3000);

  // Context Indicator aktualisieren
  updateContextIndicator({
    current_tokens: data.new_tokens,
    limit_tokens: data.limit_tokens || 32000,
    percent: Math.round((data.new_tokens / (data.limit_tokens || 32000)) * 100)
  });
}

function displayTokenUsage(usage, chat) {
  const ss = chat.streamingState;
  if (ss?.statusBar) {
    const statusBar = ss.statusBar;
    statusBar.classList.add('done', 'final');

    const truncatedWarning = usage.truncated
      ? `<div class="truncated-warning">⚠️ Abgebrochen wegen max_tokens (${usage.max_tokens})</div>`
      : '';

    const elapsed = ss.startTime ? Date.now() - ss.startTime : 0;
    const seconds = (elapsed / 1000).toFixed(1);

    statusBar.innerHTML = `
      <div class="status-timer">
        <span class="status-icon">⏱️</span>
        <span class="timer-value">${seconds}s</span>
      </div>
      <div class="status-tokens">
        <span class="status-icon">📊</span>
        <span class="tokens-value">${usage.prompt_tokens || 0} + ${usage.completion_tokens || 0} = ${usage.total_tokens || 0} tokens</span>
      </div>
      ${usage.model ? `<div class="status-model">${usage.model}</div>` : ''}
      ${truncatedWarning}
      <div class="status-indicator">
        <span class="status-done">✓</span>
      </div>
    `;

    ss.statusBar = null;
    if (document.contains(chat.pane)) scrollToBottom();
    return;
  }

  // Fallback: Token-Anzeige direkt ins Chat-Pane hängen
  const usageDiv = document.createElement('div');
  usageDiv.className = 'token-usage';

  const truncatedWarning = usage.truncated
    ? `<span class="truncated-warning">⚠️ Antwort wegen max_tokens (${usage.max_tokens}) abgebrochen</span>`
    : '';

  usageDiv.innerHTML = `
    <div class="token-usage-row">
      <span class="token-label">Tokens:</span>
      <span class="token-value">${usage.prompt_tokens || 0} prompt + ${usage.completion_tokens || 0} completion = ${usage.total_tokens || 0}</span>
      ${usage.model ? `<span class="token-model">${usage.model}</span>` : ''}
      ${truncatedWarning}
    </div>
  `;

  chat.pane.appendChild(usageDiv);
  if (document.contains(chat.pane)) scrollToBottom();
}

function createToolCard(toolName, args, status, model = null) {
  const card = document.createElement('div');
  card.className = 'tool-call-card';
  card.dataset.toolId = toolName + Date.now();

  const statusClass = status === 'running' ? 'running' : (status === 'success' ? 'success' : 'error');
  const statusText = status === 'running' ? 'Läuft...' : (status === 'success' ? 'Fertig' : 'Fehler');
  const modelBadge = model ? `<span class="tool-call-model">${escapeHtml(model)}</span>` : '';

  card.innerHTML = `
    <div class="tool-call-header">
      <span class="tool-call-icon">&#128295;</span>
      <span class="tool-call-name">${escapeHtml(toolName)}</span>
      ${modelBadge}
      <span class="tool-call-status ${statusClass}">${statusText}</span>
    </div>
    <div class="tool-call-body">
      <div class="tool-call-args">${escapeHtml(JSON.stringify(args, null, 2))}</div>
      <div class="tool-call-result"></div>
    </div>
  `;

  return card;
}

function updateToolCard(toolId, status, result, pane) {
  // Im Chat-Pane suchen (funktioniert auch wenn Pane detached ist)
  const root = pane || document;
  const cards = root.querySelectorAll('.tool-call-card');
  const card = cards[cards.length - 1]; // Last card

  if (card) {
    const statusEl = card.querySelector('.tool-call-status');
    statusEl.className = `tool-call-status ${status}`;
    statusEl.textContent = status === 'success' ? 'Fertig' : 'Fehler';

    const resultEl = card.querySelector('.tool-call-result');
    if (result) {
      resultEl.textContent = typeof result === 'string' ? result.slice(0, 500) : JSON.stringify(result).slice(0, 500);
    }
  }
}

// ── Sub-Agent Cards ──

const _SA_DISPLAY_NAMES = {
  code_explorer:    'Code Explorer',
  wiki_agent:       'Wiki Agent',
  jira_agent:       'Jira Agent',
  database_agent:   'Database Agent',
  knowledge_agent:  'Knowledge Agent',
  datasource_agent: 'Datenquellen-Agent',
};

function _saLabel(agentId) {
  return _SA_DISPLAY_NAMES[agentId] || agentId;
}

function createSubAgentCard(agents, routingModel) {
  const card = document.createElement('div');
  card.className = 'subagent-card';
  const modelHint = routingModel ? `<span class="subagent-routing-badge">Routing via ${escapeHtml(routingModel)}</span>` : '';

  card.innerHTML = `
    <div class="subagent-header">
      <span class="subagent-icon">&#128269;</span>
      <span class="subagent-title">Parallele Recherche</span>
      ${modelHint}
      <span class="subagent-status running">Routing...</span>
    </div>
    <div class="subagent-agents"></div>
    <div class="subagent-results"></div>
  `;
  if (agents.length > 0) populateSubAgentCard(card, agents, routingModel);
  return card;
}

function populateSubAgentCard(card, agents, routingModel) {
  const agentsEl = card.querySelector('.subagent-agents');
  const statusEl = card.querySelector('.subagent-status');
  const modelHint = routingModel ? `<span class="subagent-routing-badge">${escapeHtml(routingModel)}</span>` : '';

  // Header aktualisieren
  const header = card.querySelector('.subagent-header');
  // Bestehende routing-badges entfernen
  header.querySelectorAll('.subagent-routing-badge').forEach(b => b.remove());
  if (routingModel) {
    const badge = document.createElement('span');
    badge.className = 'subagent-routing-badge';
    badge.textContent = routingModel;
    header.insertBefore(badge, statusEl);
  }

  agentsEl.innerHTML = agents
    .map(a => `<span class="subagent-badge running" data-agent="${escapeHtml(a)}" title="${escapeHtml(a)}">${escapeHtml(_saLabel(a))}</span>`)
    .join('');
  statusEl.className = 'subagent-status running';
  statusEl.textContent = `Läuft (${agents.length})...`;
}

function updateSubAgentCard(card, type, data) {
  if (!card) return;
  const resultsEl = card.querySelector('.subagent-results');
  const statusEl = card.querySelector('.subagent-status');

  // Backend sendet agent_name (display name), Badge hat data-agent (id) und zeigt _saLabel
  // Wir matchen über das title-Attribut (=id) oder den Text (=display name)
  const agentRaw = data.agent || 'Unbekannt';
  const badge = card.querySelector(`.subagent-badge[data-agent="${CSS.escape(agentRaw)}"]`)
    || [...card.querySelectorAll('.subagent-badge')].find(b =>
        b.title === agentRaw || b.textContent.trim() === agentRaw
       );

  if (type === 'done') {
    const duration = data.duration_ms ? `${(data.duration_ms / 1000).toFixed(1)}s` : '';
    const findings = data.findings_count != null ? `${data.findings_count} Findings` : '';
    if (badge) badge.className = 'subagent-badge done';

    const row = document.createElement('div');
    row.className = 'subagent-result-row success';
    row.textContent = `✓ ${agentRaw}${findings ? ' · ' + findings : ''}${duration ? ' · ' + duration : ''}`;
    resultsEl.appendChild(row);

    // Alle Badges fertig?
    const allDone = [...card.querySelectorAll('.subagent-badge')].every(b => !b.classList.contains('running'));
    if (allDone) {
      const total = card.querySelectorAll('.subagent-result-row.success').length;
      statusEl.className = 'subagent-status done';
      statusEl.textContent = `Fertig (${total} Quellen)`;
    }
  } else if (type === 'error') {
    if (badge) badge.className = 'subagent-badge error';

    const row = document.createElement('div');
    row.className = 'subagent-result-row error';
    row.textContent = `✗ ${agentRaw}: ${data.error || 'Fehler'}`;
    resultsEl.appendChild(row);

    // Prüfen ob alle fertig (done oder error)
    const allFinished = [...card.querySelectorAll('.subagent-badge')].every(
      b => !b.classList.contains('running')
    );
    if (allFinished) {
      statusEl.className = 'subagent-status error';
      statusEl.textContent = 'Teilweise fehlgeschlagen';
    }
  }
}

// ── Tool History ──
function addToolToHistory(id, name, args, status) {
  state.toolHistory.unshift({ id, name, args, status, result: null });
  renderToolHistory();
}

function updateToolHistory(id, status, result) {
  const tool = state.toolHistory.find(t => t.id === id) || state.toolHistory[0];
  if (tool) {
    tool.status = status;
    tool.result = result;
    renderToolHistory();
  }
}

function renderToolHistory() {
  const container = document.getElementById('tool-history');

  if (state.toolHistory.length === 0) {
    container.innerHTML = '<div class="empty-state"><span>&#128295;</span><p>Noch keine Tool-Aufrufe</p></div>';
    return;
  }

  container.innerHTML = state.toolHistory.slice(0, 20).map((tool, i) => `
    <div class="tool-history-item">
      <div class="tool-history-header" onclick="toggleToolHistoryItem(${i})">
        <span class="tool-history-icon">&#128295;</span>
        <span class="tool-history-name">${escapeHtml(tool.name)}</span>
        <span class="tool-history-status tool-call-status ${tool.status}">${
          tool.status === 'running' ? 'Läuft' : (tool.status === 'success' ? '✓' : '✗')
        }</span>
      </div>
      <div class="tool-history-body" id="tool-body-${i}">
        <strong>Args:</strong> ${escapeHtml(JSON.stringify(tool.args))}
        ${tool.result ? `<br><strong>Result:</strong> ${escapeHtml(typeof tool.result === 'string' ? tool.result.slice(0, 300) : JSON.stringify(tool.result).slice(0, 300))}` : ''}
      </div>
    </div>
  `).join('');
}

function toggleToolHistoryItem(index) {
  const body = document.getElementById(`tool-body-${index}`);
  if (body) {
    body.classList.toggle('open');
  }
}

function clearToolHistory() {
  state.toolHistory = [];
  renderToolHistory();
}

// ── Confirmation Panel ──
function showConfirmationPanel(data) {
  document.getElementById('no-confirmation').style.display = 'none';
  document.getElementById('pending-confirmation').style.display = 'block';
  document.getElementById('pending-count').style.display = 'inline';

  document.getElementById('confirm-operation').textContent = data.name || data.confirmation_data?.operation || '-';
  document.getElementById('confirm-path').textContent = data.confirmation_data?.path || '-';

  // Show diff
  const diffContent = document.getElementById('diff-content');
  if (data.confirmation_data?.diff) {
    diffContent.textContent = data.confirmation_data.diff;
    hljs.highlightElement(diffContent);
  } else {
    diffContent.textContent = 'Keine Diff-Vorschau verfügbar';
  }
}

function hideConfirmationPanel() {
  document.getElementById('no-confirmation').style.display = 'flex';
  document.getElementById('pending-confirmation').style.display = 'none';
  document.getElementById('pending-count').style.display = 'none';
  state.pendingConfirmation = null;
}

async function confirmOperation(confirmed) {
  if (!state.pendingConfirmation) return;

  try {
    const res = await fetch(`/api/agent/confirm/${state.sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed })
    });

    const data = await res.json();

    if (confirmed && data.status === 'executed') {
      appendMessage('system', `✓ Operation ausgeführt: ${data.message}`);
    } else if (!confirmed) {
      appendMessage('system', `✗ Operation abgebrochen`);
    } else {
      appendMessage('error', data.message || 'Fehler bei der Ausführung');
    }

    hideConfirmationPanel();
    switchRightPanel('tools-panel');

    // Automatisch weitermachen wenn noch Arbeit offen ist
    // Das Backend signalisiert dies mit continue: true
    if (data.continue) {
      // Kurze Pause für visuelle Feedback, dann weitermachen
      setTimeout(() => {
        // Sende eine unsichtbare "continue" Anfrage
        sendChatInternal('[CONTINUE]');
      }, 500);
    }
  } catch (e) {
    appendMessage('error', 'Bestätigung fehlgeschlagen: ' + e.message);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Enhancement Confirmation Handling
// ══════════════════════════════════════════════════════════════════════════════

const ENHANCEMENT_ICONS = {
  research: '🔍',
  sequential: '🧠',
  analyze: '📊',
  brainstorm: '💡',
  none: '➡️'
};

const ENHANCEMENT_LABELS = {
  research: 'Recherche-Kontext',
  sequential: 'Strukturierte Analyse',
  analyze: 'Code-Analyse',
  brainstorm: 'Requirements Discovery',
  none: 'Direkte Verarbeitung'
};

/**
 * Handle ENHANCEMENT_START event - show progress indicator
 */
function handleEnhancementStart(data, chat) {
  const isActive = chat.id === chatManager.activeId;

  chat.pendingEnhancement = {
    type: data.detection_type,
    query_preview: data.query_preview,
    status: 'collecting'
  };

  if (isActive) {
    showEnhancementProgress(data);
    switchRightPanel('confirm-panel');
  }
}

/**
 * Show progress indicator during context collection
 */
function showEnhancementProgress(data) {
  // Hide other confirmation types
  document.getElementById('no-confirmation').style.display = 'none';
  document.getElementById('pending-confirmation').style.display = 'none';
  document.getElementById('pending-enhancement').style.display = 'none';

  // Show progress
  const progress = document.getElementById('enhancement-progress');
  if (progress) {
    progress.style.display = 'block';
    const typeLabel = document.getElementById('enhancement-progress-type');
    if (typeLabel) {
      typeLabel.textContent = ENHANCEMENT_LABELS[data.detection_type] || data.detection_type;
    }
  }

  // Show pending badge
  const badge = document.getElementById('pending-count');
  if (badge) badge.style.display = 'inline';
}

/**
 * Handle ENHANCEMENT_COMPLETE event - show confirmation panel
 */
function handleEnhancementComplete(data, chat) {
  const isActive = chat.id === chatManager.activeId;

  chat.pendingEnhancement = {
    ...chat.pendingEnhancement,
    status: 'pending_confirmation',
    context_count: data.context_count,
    sources: data.sources,
    summary: data.summary,
    confirmation_message: data.confirmation_message,
    context_items: data.context_items || []  // Store context items for inline display
  };

  if (isActive) {
    showEnhancementConfirmation(chat.pendingEnhancement);
  }
}

/**
 * Show the enhancement confirmation panel with context items
 */
function showEnhancementConfirmation(enhancementData) {
  // Hide progress
  const progress = document.getElementById('enhancement-progress');
  if (progress) progress.style.display = 'none';
  document.getElementById('no-confirmation').style.display = 'none';
  document.getElementById('pending-confirmation').style.display = 'none';

  // Show enhancement panel
  const panel = document.getElementById('pending-enhancement');
  if (!panel) return;
  panel.style.display = 'block';

  // Set type icon and label
  const type = enhancementData.type || 'research';
  const iconEl = document.getElementById('enhancement-type-icon');
  const labelEl = document.getElementById('enhancement-type-label');
  if (iconEl) iconEl.textContent = ENHANCEMENT_ICONS[type] || '🔍';
  if (labelEl) labelEl.textContent = ENHANCEMENT_LABELS[type] || 'Kontext-Sammlung';

  // Set source count
  const countEl = document.getElementById('enhancement-source-count');
  if (countEl) countEl.textContent = `${enhancementData.context_count || 0} Elemente`;

  // Set query preview
  const queryEl = document.getElementById('enhancement-query-text');
  if (queryEl && enhancementData.query_preview) {
    queryEl.textContent = enhancementData.query_preview.substring(0, 100) + '...';
  }

  // Set summary
  const summaryEl = document.getElementById('enhancement-summary');
  if (summaryEl && enhancementData.summary) {
    summaryEl.innerHTML = marked.parse(enhancementData.summary);
  }

  // Reset context list
  const listEl = document.getElementById('enhancement-context-list');
  if (listEl) {
    listEl.innerHTML = '';
    listEl.classList.remove('expanded');
    delete listEl.dataset.loaded;
  }

  // Show pending badge
  const badge = document.getElementById('pending-count');
  if (badge) badge.style.display = 'inline';
}

/**
 * Confirm or reject the enhancement context
 */
async function confirmEnhancement(confirmed) {
  const chat = chatManager.getActive();
  if (!chat?.pendingEnhancement) return;

  try {
    const res = await fetch(`/api/agent/enhancement/${chat.sessionId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed })
    });

    if (!res.ok) {
      log.error('[enhancement] Confirmation failed:', await res.text());
    }

    const result = await res.json();

    // Clear pending state
    chat.pendingEnhancement = null;
    hideEnhancementPanel();

    // Show feedback
    if (confirmed) {
      const contextLen = result.context_length ? ` (${result.context_length} Zeichen)` : '';
      appendMessageToPane(chat.pane, 'system', `✓ Kontext bestätigt${contextLen}`);
    } else {
      appendMessageToPane(chat.pane, 'system', '⚠ Ohne Kontext fortfahren...');
    }

    // Continue processing if backend signals continue
    if (result.continue) {
      // Resume the chat processing with the enriched context
      sendChatInternal('[CONTINUE_ENHANCED]');
    }

  } catch (err) {
    log.error('[enhancement] Confirmation error:', err);
    appendMessageToPane(chat.pane, 'error', `Enhancement-Bestätigung fehlgeschlagen: ${err.message}`);
  }
}

/**
 * Hide the enhancement confirmation panel
 */
function hideEnhancementPanel() {
  const progress = document.getElementById('enhancement-progress');
  const pending = document.getElementById('pending-enhancement');
  const noConfirm = document.getElementById('no-confirmation');
  const badge = document.getElementById('pending-count');

  if (progress) progress.style.display = 'none';
  if (pending) pending.style.display = 'none';
  if (noConfirm) noConfirm.style.display = 'block';
  if (badge) badge.style.display = 'none';
}

/**
 * Handle ENHANCEMENT_CONFIRMED event
 */
function handleEnhancementConfirmed(data, chat) {
  const isActive = chat.id === chatManager.activeId;

  chat.pendingEnhancement = null;

  if (isActive) {
    hideEnhancementPanel();
  }
}

/**
 * Handle ENHANCEMENT_REJECTED event
 */
function handleEnhancementRejected(data, chat) {
  const isActive = chat.id === chatManager.activeId;

  chat.pendingEnhancement = null;

  if (isActive) {
    hideEnhancementPanel();
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Web Fallback Confirmation
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Handle WEB_FALLBACK_REQUIRED event - show confirmation dialog
 */
function handleWebFallbackRequired(data, chat) {
  const isActive = chat.id === chatManager.activeId;

  chat.pendingWebFallback = {
    originalQuery: data.original_query,
    sanitizedQuery: data.sanitized_query,
    removedTerms: data.removed_terms || [],
    message: data.message
  };

  if (isActive) {
    showWebFallbackConfirmation(data);
  }
}

/**
 * Show the web fallback confirmation panel
 */
function showWebFallbackConfirmation(data) {
  const chat = chatManager.getActive();
  if (!chat) return;

  // Create inline confirmation message in chat
  const confirmHtml = `
    <div class="web-fallback-confirm" style="
      background: linear-gradient(135deg, #1a365d 0%, #2d3748 100%);
      border: 1px solid #4299e1;
      border-radius: 8px;
      padding: 16px;
      margin: 8px 0;
    ">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px;">
        <span style="font-size: 1.2em;">🌐</span>
        <span style="font-weight: 600; color: #e2e8f0;">Web-Suche erforderlich</span>
      </div>

      <p style="color: #a0aec0; margin-bottom: 12px;">
        Keine Ergebnisse in internen Quellen gefunden. Im Web suchen?
      </p>

      <div style="background: #1a202c; border-radius: 4px; padding: 12px; margin-bottom: 12px;">
        <div style="color: #68d391; font-size: 0.85em; margin-bottom: 4px;">Bereinigte Query (ohne interne Daten):</div>
        <div style="color: #e2e8f0; font-style: italic;">"${escapeHtml(data.sanitized_query)}"</div>
        ${data.removed_terms && data.removed_terms.length > 0 ? `
          <div style="color: #f56565; font-size: 0.8em; margin-top: 8px;">
            ⚠ Entfernt: ${data.removed_terms.join(', ')}
          </div>
        ` : ''}
      </div>

      <div style="display: flex; gap: 8px;">
        <button onclick="confirmWebFallback(true)" class="btn btn-success" style="flex: 1;">
          ✓ Mit Web-Suche fortfahren
        </button>
        <button onclick="confirmWebFallback(false)" class="btn btn-secondary" style="flex: 1;">
          ✗ Ohne Web-Suche
        </button>
      </div>
    </div>
  `;

  appendMessageToPane(chat.pane, 'system', confirmHtml, { isHtml: true });
  scrollToBottom();
}

/**
 * Confirm or reject web fallback search
 */
async function confirmWebFallback(confirmed) {
  const chat = chatManager.getActive();
  if (!chat?.pendingWebFallback) return;

  try {
    const res = await fetch(`/api/agent/web-fallback/${chat.sessionId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed })
    });

    if (!res.ok) {
      log.error('[web-fallback] Confirmation failed:', await res.text());
      return;
    }

    const result = await res.json();

    // Clear pending state
    chat.pendingWebFallback = null;

    // Show feedback
    if (confirmed) {
      appendMessageToPane(chat.pane, 'system', '✓ Web-Suche gestartet...');
      // Retry the research with web fallback approved
      if (result.retry_with_web) {
        sendChatInternal('[RETRY_WITH_WEB]');
      }
    } else {
      appendMessageToPane(chat.pane, 'system', '⚠ Ohne Web-Ergebnisse fortfahren...');
      sendChatInternal('[CONTINUE_WITHOUT_WEB]');
    }

  } catch (err) {
    log.error('[web-fallback] Confirmation error:', err);
    appendMessageToPane(chat.pane, 'error', `Web-Fallback-Bestätigung fehlgeschlagen: ${err.message}`);
  }
}

/**
 * Toggle detailed context view
 */
function toggleEnhancementDetails() {
  const list = document.getElementById('enhancement-context-list');
  if (!list) return;

  list.classList.toggle('expanded');

  // If expanded for first time, load full details
  if (list.classList.contains('expanded') && !list.dataset.loaded) {
    loadEnhancementDetails();
    list.dataset.loaded = 'true';
  }
}

/**
 * Load full context details via API
 */
async function loadEnhancementDetails() {
  const chat = chatManager.getActive();
  if (!chat?.sessionId) return;

  const list = document.getElementById('enhancement-context-list');
  if (!list) return;

  // Show loading state
  list.innerHTML = '<div style="padding: 12px; color: #a0aec0;">Lade Details...</div>';

  try {
    const res = await fetch(`/api/agent/enhancement/${chat.sessionId}`);
    if (!res.ok) throw new Error('Failed to load details');

    const data = await res.json();
    if (!data.has_enhancement) {
      list.innerHTML = '<div style="padding: 12px; color: #a0aec0;">Kein Enhancement verfügbar</div>';
      return;
    }
    renderContextItems(data.enhancement?.context_items || []);

  } catch (err) {
    log.error('[enhancement] Failed to load details:', err);
    list.innerHTML = '<div style="padding: 12px; color: #f56565;">Details konnten nicht geladen werden</div>';
  }
}

/**
 * Render context items in the list
 */
function renderContextItems(items) {
  const list = document.getElementById('enhancement-context-list');
  if (!list) return;

  if (items.length === 0) {
    list.innerHTML = '<div style="padding: 12px; color: #a0aec0;">Keine Kontext-Elemente</div>';
    return;
  }

  list.innerHTML = '';

  items.forEach(item => {
    const sourceClass = getEnhancementSourceClass(item.source);
    const relevancePercent = Math.round((item.relevance || 0.5) * 100);
    const displayContent = item.content_preview || item.content || '';

    const el = document.createElement('div');
    el.className = 'enhancement-context-item';
    el.innerHTML = `
      <div class="context-item-header">
        <span class="context-source-badge ${sourceClass}">${escapeHtml(item.source || 'unknown')}</span>
        <span class="context-item-title">${escapeHtml(item.title || 'Untitled')}</span>
        <span class="context-relevance">${relevancePercent}%</span>
      </div>
      <div class="context-item-content">
        ${escapeHtml(displayContent.substring(0, 300))}${displayContent.length > 300 ? '...' : ''}
      </div>
      ${item.url || item.file_path ? `
        <div class="context-item-meta">
          ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Öffnen</a>` : ''}
          ${item.file_path ? `<span>${escapeHtml(item.file_path)}</span>` : ''}
        </div>
      ` : ''}
    `;

    list.appendChild(el);
  });
}

/**
 * Get CSS class for source type
 */
function getEnhancementSourceClass(source) {
  const mapping = {
    'wiki': 'source-wiki',
    'confluence': 'source-wiki',
    'code': 'source-code',
    'code_java': 'source-code',
    'code_python': 'source-code',
    'web': 'source-web',
    'handbook': 'source-handbook',
    'memory': 'source-memory',
    'sequential': 'source-sequential',
    'hypothesis': 'source-hypothesis',
    'research_report': 'source-research',
    'sequential_analysis': 'source-sequential',
    'sequential_conclusion': 'source-sequential',
    'brainstorm_exploration': 'source-hypothesis',
    'code_analysis': 'source-code',
    'analysis_insight': 'source-code'
  };
  return mapping[source] || 'source-web';
}

// ── Plan Card (Planungsphase) ──
function createPlanCard(planText, chat) {
  const card = document.createElement('div');
  card.className = 'plan-card';

  const header = document.createElement('div');
  header.className = 'plan-card-header';
  header.innerHTML = '<span class="plan-icon">&#128203;</span><span class="plan-title">Implementierungsplan</span>';

  const body = document.createElement('div');
  body.className = 'plan-card-body';
  // white-space: pre-wrap im CSS übernimmt Zeilenumbrüche; nur Inline-Code ersetzen
  const escaped = escapeHtml(planText);
  body.innerHTML = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');

  const footer = document.createElement('div');
  footer.className = 'plan-card-footer';

  const approveBtn = document.createElement('button');
  approveBtn.className = 'plan-btn plan-btn-approve';
  approveBtn.textContent = 'Plan ausführen';
  approveBtn.onclick = () => approvePlan(card, chat);

  const rejectBtn = document.createElement('button');
  rejectBtn.className = 'plan-btn plan-btn-reject';
  rejectBtn.textContent = 'Plan ablehnen';
  rejectBtn.onclick = () => rejectPlan(card, chat);

  footer.appendChild(approveBtn);
  footer.appendChild(rejectBtn);

  card.appendChild(header);
  card.appendChild(body);
  card.appendChild(footer);

  return card;
}

async function approvePlan(card, chat) {
  const sessionId = chat?.id || state.sessionId;
  try {
    const res = await fetch(`/api/agent/plan/${sessionId}/approve`, { method: 'POST' });
    if (!res.ok) {
      appendMessage('error', 'Plan-Genehmigung fehlgeschlagen: ' + res.statusText);
      return;
    }

    // Buttons deaktivieren
    card.querySelectorAll('.plan-btn').forEach(b => b.disabled = true);
    card.querySelector('.plan-btn-approve').textContent = '✓ Plan genehmigt';
    card.querySelector('.plan-btn-reject').style.display = 'none';

    // Neue Chat-Anfrage starten: Input befüllen und sendMessage() aufrufen
    // sendMessage() liest aus #message-input, prüft Guards und sendet die Anfrage
    const input = document.getElementById('message-input');
    input.value = 'Bitte führe den genehmigten Plan jetzt aus.';
    await sendMessage();
  } catch (e) {
    appendMessage('error', 'Plan-Genehmigung fehlgeschlagen: ' + e.message);
  }
}

async function rejectPlan(card, chat) {
  const sessionId = chat?.id || state.sessionId;
  try {
    await fetch(`/api/agent/plan/${sessionId}/reject`, { method: 'POST' });

    // Karte als abgelehnt markieren
    card.querySelectorAll('.plan-btn').forEach(b => b.disabled = true);
    card.querySelector('.plan-btn-reject').textContent = '✗ Plan abgelehnt';
    card.querySelector('.plan-btn-approve').style.display = 'none';
    card.classList.add('plan-rejected');

    if (chat) chat.pendingPlan = null;
    state.pendingPlan = null;
    appendMessageToPane(
      chat?.pane || document.getElementById('messages-container'),
      'system',
      'Plan abgelehnt. Du kannst eine neue Anfrage stellen.'
    );
  } catch (e) {
    appendMessage('error', 'Plan-Ablehnung fehlgeschlagen: ' + e.message);
  }
}

// switchRightPanel defined earlier (line ~1055) with null-safety checks

// ── Messages ──
function appendMessage(role, text) {
  // Schreibt in den aktiven Chat-Pane (Fallback: #messages)
  const pane = chatManager.getActive()?.pane || document.getElementById('messages');
  return appendMessageToPane(pane, role, text);
}

function appendMessageToPane(pane, role, text) {
  const div = document.createElement('div');
  div.className = `message ${role}`;

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';

  if (role === 'assistant') {
    bubble.innerHTML = text ? marked.parse(text) : '';
    applyHighlight(bubble);
  } else {
    bubble.textContent = text;
  }

  div.appendChild(bubble);
  pane.appendChild(div);
  // Nur scrollen wenn Pane gerade sichtbar (im DOM) ist
  if (document.contains(pane)) scrollToBottom();
  return div;
}

function applyHighlight(el) {
  el.querySelectorAll('pre code').forEach(block => {
    hljs.highlightElement(block);
  });
}

function scrollToBottom() {
  const m = document.getElementById('messages');
  m.scrollTop = m.scrollHeight;
}

// ── Session ──
document.getElementById('clear-btn').addEventListener('click', async () => {
  await createNewChat();
});

// ── Explorer Sections ──
function toggleExplorerSection(section) {
  const content = document.getElementById(`${section}-content`);
  const arrow = document.getElementById(`${section}-arrow`);

  content.classList.toggle('open');
  arrow.style.transform = content.classList.contains('open') ? 'rotate(90deg)' : '';
}

// ── Repo Selector (Sidebar) ──

// DEPRECATED: loadRepoSelector and setActiveRepo removed
// The "active repo" concept has been replaced by file search with repo filter
// See: searchExplorerFiles() and @-mention system

// ── Java Index ──
async function loadJavaIndexStatus() {
  const el = document.getElementById('java-index-status');
  try {
    const res = await fetch('/api/java/index/status');
    if (!res.ok) {
      el.textContent = 'Status nicht verfügbar';
      return;
    }
    const d = await res.json();
    if (d.is_built) {
      el.textContent = `${d.indexed_files} Dateien indexiert`;
      el.classList.add('success');
    } else {
      el.textContent = 'Kein Index - bitte aufbauen';
    }
  } catch {
    el.textContent = 'Status nicht verfügbar';
  }
}

async function buildJavaIndex() {
  const el = document.getElementById('java-index-status');
  el.textContent = 'Index wird aufgebaut...';
  el.classList.remove('success');

  try {
    const res = await fetch('/api/java/index/build?background=false', { method: 'POST' });
    const d = await res.json();
    if (res.ok) {
      appendMessage('system', `Java-Index: ${d.indexed} Dateien indexiert (${d.duration_s}s)`);
      await loadJavaIndexStatus();
    } else {
      el.textContent = d.detail || 'Fehler';
    }
  } catch (e) {
    el.textContent = e.message;
  }
}

async function loadJavaTree() {
  const container = document.getElementById('java-tree');
  container.innerHTML = '<span style="color:var(--text-muted);font-size:0.8rem">Lade...</span>';

  try {
    const res = await fetch('/api/java/tree');
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      container.innerHTML = `<span style="color:var(--danger)">${e.detail}</span>`;
      return;
    }
    const tree = await res.json();
    container.innerHTML = '';
    container.appendChild(renderFileTree(tree, 'java'));
  } catch (e) {
    container.innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
  }
}

// ── Python Index ──
async function loadPythonIndexStatus() {
  const el = document.getElementById('python-index-status');
  try {
    const res = await fetch('/api/python/index/status');
    if (!res.ok) {
      el.textContent = 'Status nicht verfügbar';
      return;
    }
    const d = await res.json();
    if (d.is_built) {
      el.textContent = `${d.indexed_files} Dateien indexiert`;
      el.classList.add('success');
    } else {
      el.textContent = 'Kein Index - bitte aufbauen';
    }
  } catch {
    el.textContent = 'Status nicht verfügbar';
  }
}

async function buildPythonIndex() {
  const el = document.getElementById('python-index-status');
  el.textContent = 'Index wird aufgebaut...';
  el.classList.remove('success');

  try {
    const res = await fetch('/api/python/index/build?background=false', { method: 'POST' });
    const d = await res.json();
    if (res.ok) {
      appendMessage('system', `Python-Index: ${d.indexed} Dateien indexiert (${d.duration_s}s)`);
      await loadPythonIndexStatus();
    } else {
      el.textContent = d.detail || 'Fehler';
    }
  } catch (e) {
    el.textContent = e.message;
  }
}

async function loadPythonTree() {
  const container = document.getElementById('python-tree');
  container.innerHTML = '<span style="color:var(--text-muted);font-size:0.8rem">Lade...</span>';

  try {
    const res = await fetch('/api/python/tree');
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      container.innerHTML = `<span style="color:var(--danger)">${e.detail}</span>`;
      return;
    }
    const tree = await res.json();
    container.innerHTML = '';
    container.appendChild(renderFileTree(tree, 'python'));
  } catch (e) {
    container.innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
  }
}

// ── File Tree Rendering ──
function renderFileTree(node, type, isRoot = true) {
  if (node.type === 'file') {
    const div = document.createElement('div');
    div.className = 'tree-item';
    div.dataset.path = node.path;
    div.innerHTML = `
      <span class="tree-icon">${type === 'java' ? '&#9749;' : '&#128013;'}</span>
      <span class="tree-name">${escapeHtml(node.name)}</span>
      <span class="tree-size">${node.size_kb}KB</span>
    `;
    div.addEventListener('click', () => addFileToContext(node.path, node.name, type, div));
    return div;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'tree-node';

  if (!isRoot) {
    const header = document.createElement('div');
    header.className = 'tree-item';
    header.innerHTML = `
      <span class="tree-icon">&#128193;</span>
      <span class="tree-name">${escapeHtml(node.name)}</span>
    `;

    const children = document.createElement('div');
    children.style.display = 'none';
    children.style.marginLeft = '12px';

    header.addEventListener('click', () => {
      children.style.display = children.style.display === 'none' ? 'block' : 'none';
    });

    wrapper.appendChild(header);
    (node.children || []).forEach(child => children.appendChild(renderFileTree(child, type, false)));
    wrapper.appendChild(children);
  } else {
    (node.children || []).forEach(child => wrapper.appendChild(renderFileTree(child, type, false)));
  }

  return wrapper;
}

function addFileToContext(path, name, type, el) {
  const contextArray = type === 'java' ? state.context.javaFiles : state.context.pythonFiles;

  if (contextArray.find(f => f.path === path)) {
    // Remove from context
    if (type === 'java') {
      state.context.javaFiles = state.context.javaFiles.filter(f => f.path !== path);
    } else {
      state.context.pythonFiles = state.context.pythonFiles.filter(f => f.path !== path);
    }
    el.classList.remove('selected');
  } else {
    // Add to context
    contextArray.push({ path, label: name, type });
    el.classList.add('selected');
  }

  // Sync to active chat's context
  syncContextToActiveChat();
  renderContextChips();
}

// ── Handbook ──
let handbookBuildController = null;

async function loadHandbookStatus() {
  const el = document.getElementById('handbook-status');
  try {
    const res = await fetch('/api/handbook/status');
    if (!res.ok) {
      el.innerHTML = '<span class="status-icon">&#128214;</span><span>Handbuch nicht verfügbar</span>';
      el.classList.add('error');
      return;
    }
    const d = await res.json();
    if (d.indexed) {
      el.innerHTML = `<span class="status-icon">&#128214;</span><span>${d.services_count} Services, ${d.indexed_pages} Seiten indexiert</span>`;
      el.classList.add('success');
      await loadHandbookServices();
    } else {
      el.innerHTML = `
        <span class="status-icon">&#128214;</span>
        <span>Index nicht aufgebaut</span>
        <button class="sb-btn" style="margin-left:8px" onclick="buildHandbookIndex()">Index aufbauen</button>
      `;
    }
  } catch {
    el.innerHTML = '<span class="status-icon">&#128214;</span><span>Handbuch nicht verfügbar</span>';
    el.classList.add('error');
  }
}

async function buildHandbookIndex(force = false) {
  const el = document.getElementById('handbook-status');

  el.innerHTML = `
    <div class="handbook-progress">
      <div class="progress-header">
        <span class="status-icon">&#128214;</span>
        <span id="handbook-progress-phase">Starte Indexierung...</span>
        <button class="sb-btn btn-danger" onclick="cancelHandbookIndex()" style="margin-left:auto">Abbrechen</button>
      </div>
      <div class="progress-bar-container">
        <div class="progress-bar" id="handbook-progress-bar" style="width:0%"></div>
      </div>
      <div class="progress-details" id="handbook-progress-details">
        <span>0 / 0 Dateien</span>
      </div>
    </div>
  `;

  try {
    handbookBuildController = new AbortController();

    const res = await fetch(`/api/handbook/index/build?force=${force}&stream=true`, {
      signal: handbookBuildController.signal
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            updateHandbookProgress(data);
          } catch (e) {
            log.warn('Handbook progress parse error:', e);
          }
        }
      }
    }

    // Fertig - Status neu laden
    handbookBuildController = null;
    await loadHandbookStatus();

  } catch (e) {
    if (e.name === 'AbortError') {
      el.innerHTML = '<span class="status-icon">&#128214;</span><span>Indexierung abgebrochen</span>';
    } else {
      el.innerHTML = `<span class="status-icon">&#128214;</span><span style="color:var(--danger)">Fehler: ${e.message}</span>`;
    }
    handbookBuildController = null;
  }
}

function updateHandbookProgress(data) {
  const phaseEl = document.getElementById('handbook-progress-phase');
  const barEl = document.getElementById('handbook-progress-bar');
  const detailsEl = document.getElementById('handbook-progress-details');

  if (!phaseEl) return;

  const phaseNames = {
    'scanning': 'Suche Dateien...',
    'analyzing': 'Analysiere Struktur...',
    'indexing': 'Indexiere...',
    'saving': 'Speichere...',
    'done': 'Fertig!',
    'cancelled': 'Abgebrochen',
    'error': 'Fehler'
  };

  phaseEl.textContent = phaseNames[data.phase] || data.message || data.phase;
  barEl.style.width = `${data.percent || 0}%`;

  if (data.phase === 'indexing') {
    const eta = data.estimated_remaining_seconds > 0
      ? ` (ca. ${Math.ceil(data.estimated_remaining_seconds / 60)} Min. verbleibend)`
      : '';
    detailsEl.innerHTML = `
      <span>${data.processed_files} / ${data.total_files} Dateien${eta}</span>
      <span>${data.services_found} Services</span>
      <span>${data.errors} Fehler</span>
    `;
  } else if (data.phase === 'done') {
    detailsEl.innerHTML = `<span style="color:var(--success)">${data.message}</span>`;
  } else {
    detailsEl.innerHTML = `<span>${data.message || ''}</span>`;
  }
}

async function cancelHandbookIndex() {
  if (handbookBuildController) {
    handbookBuildController.abort();
  }
  try {
    await fetch('/api/handbook/index/cancel', { method: 'POST' });
  } catch (e) {
    // Ignore
  }
}

async function loadHandbookServices() {
  try {
    const res = await fetch('/api/handbook/services');
    if (!res.ok) return;

    const services = await res.json();
    const container = document.getElementById('handbook-services');

    if (!services || services.length === 0) {
      container.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem">Keine Services gefunden</p>';
      return;
    }

    container.innerHTML = services.slice(0, 20).map(s => `
      <div class="service-item" onclick="addServiceToContext('${escapeHtml(s.service_id)}', '${escapeHtml(s.service_name)}')">
        <div class="service-name">${escapeHtml(s.service_name)}</div>
        <div class="service-id">${escapeHtml(s.service_id)}</div>
      </div>
    `).join('');
  } catch {
    // Ignore
  }
}

async function searchHandbook() {
  const q = document.getElementById('handbook-search').value.trim();
  if (!q) return;

  const container = document.getElementById('handbook-results');
  container.innerHTML = '<span style="color:var(--text-muted)">Suche...</span>';

  try {
    const res = await fetch(`/api/handbook/search?q=${encodeURIComponent(q)}`);
    const results = await res.json();

    if (!results || results.length === 0) {
      container.innerHTML = '<span style="color:var(--text-muted)">Keine Ergebnisse</span>';
      return;
    }

    container.innerHTML = results.map(r => `
      <div class="search-result" onclick="addServiceToContext('${escapeHtml(r.service_id)}', '${escapeHtml(r.title)}')">
        <div class="search-result-title">${escapeHtml(r.title)}</div>
        <div class="search-result-path">${escapeHtml(r.service_name || '')}</div>
        <div class="search-result-snippet">${escapeHtml((r.snippet || '').slice(0, 100))}</div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
  }
}

function addServiceToContext(serviceId, serviceName) {
  if (state.context.handbookServices.find(s => s.id === serviceId)) return;

  state.context.handbookServices.push({ id: serviceId, label: serviceName });
  syncContextToActiveChat();
  renderContextChips();
}

// ── PDF Management ──
// Available PDFs (not automatically in context)
const availablePdfs = [];

async function scanExistingPdfs() {
  try {
    const res = await fetch('/api/pdf/scan', { method: 'POST' });
    if (!res.ok) return;

    const data = await res.json();
    if (data.pdfs && data.pdfs.length > 0) {
      // Store as available PDFs (NOT in context automatically)
      availablePdfs.length = 0;
      availablePdfs.push(...data.pdfs.map(pdf => ({
        id: pdf.id,
        label: pdf.filename
      })));
      renderPdfList();
      log.info(`${data.loaded} PDFs aus Upload-Ordner verfügbar`);
    }
  } catch (e) {
    console.debug('PDF-Scan fehlgeschlagen:', e);
  }
}

async function uploadPDF() {
  const fileInput = document.getElementById('pdf-file-input');
  const file = fileInput.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/pdf/upload', { method: 'POST', body: formData });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      appendMessage('error', `PDF-Upload fehlgeschlagen: ${e.detail}`);
      return;
    }

    const data = await res.json();
    // Add to available list
    availablePdfs.push({ id: data.id, label: file.name });
    // Auto-add newly uploaded PDF to context
    state.context.pdfIds.push({ id: data.id, label: file.name });
    syncContextToActiveChat();
    renderContextChips();
    renderPdfList();
    appendMessage('system', `PDF hochgeladen: ${file.name}`);
  } catch (e) {
    appendMessage('error', 'PDF-Upload fehlgeschlagen: ' + e.message);
  }

  fileInput.value = '';
}

function renderPdfList() {
  const container = document.getElementById('pdf-list');
  if (!container) return;

  container.innerHTML = availablePdfs.map(pdf => {
    const isInContext = state.context.pdfIds.some(p => p.id === pdf.id);
    return `
      <div class="item-list-item ${isInContext ? 'selected' : ''}" onclick="togglePdfContext('${pdf.id}', '${escapeHtml(pdf.label)}')">
        <span class="item-icon">&#128196;</span>
        <span class="item-name">${escapeHtml(pdf.label)}</span>
        <span class="item-toggle">${isInContext ? '&#10003;' : '+'}</span>
      </div>
    `;
  }).join('');
}

function togglePdfContext(id, label) {
  const idx = state.context.pdfIds.findIndex(p => p.id === id);
  if (idx >= 0) {
    // Remove from context
    state.context.pdfIds.splice(idx, 1);
    showToast(`${label} aus Kontext entfernt`, 'info');
  } else {
    // Add to context
    state.context.pdfIds.push({ id, label });
    showToast(`${label} zum Kontext hinzugefügt`, 'success');
  }
  syncContextToActiveChat();
  renderPdfList();
  renderContextChips();
}

function removePdf(id) {
  state.context.pdfIds = state.context.pdfIds.filter(p => p.id !== id);
  syncContextToActiveChat();
  renderPdfList();
  renderContextChips();
}

// ── Global Search ──
async function globalSearch() {
  const q = document.getElementById('global-search').value.trim();
  if (!q) return;

  const activeType = document.querySelector('.search-type.active')?.dataset.type || 'code';
  const container = document.getElementById('global-search-results');
  container.innerHTML = '<span style="color:var(--text-muted)">Suche...</span>';

  let endpoint;
  switch (activeType) {
    case 'code':
      endpoint = `/api/java/search?q=${encodeURIComponent(q)}`;
      break;
    case 'handbook':
      endpoint = `/api/handbook/search?q=${encodeURIComponent(q)}`;
      break;
    case 'skills':
      endpoint = `/api/skills/search/knowledge?q=${encodeURIComponent(q)}`;
      break;
    default:
      endpoint = `/api/java/search?q=${encodeURIComponent(q)}`;
  }

  try {
    const res = await fetch(endpoint);

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const detail = errData.detail || `HTTP ${res.status}`;
      container.innerHTML = `
        <div class="search-error-hint">
          <strong>Suche fehlgeschlagen:</strong> ${escapeHtml(detail)}<br>
          <em>Tipp: Für Code-Suche muss der Index im Explorer aufgebaut sein.</em>
        </div>`;
      return;
    }

    const data = await res.json();
    const results = data.matches || data.results || data || [];

    if (!results || results.length === 0) {
      container.innerHTML = `
        <div class="search-empty-hint">
          Keine Ergebnisse für "<strong>${escapeHtml(q)}</strong>"<br>
          <em>Versuche andere Suchbegriffe oder prüfe den Index-Status.</em>
        </div>`;
      return;
    }

    container.innerHTML = results.slice(0, 10).map(r => {
      const title = r.title || r.symbol_name || r.file_path?.split('/').pop() || r.skill_name || 'Ergebnis';
      const path = r.file_path || r.path || '';
      const snippet = r.snippet || r.excerpt || '';

      return `
        <div class="search-result">
          <div class="search-result-title">${escapeHtml(title)}</div>
          ${path ? `<div class="search-result-path">${escapeHtml(path)}</div>` : ''}
          ${snippet ? `<div class="search-result-snippet">${escapeHtml(snippet.slice(0, 100))}</div>` : ''}
        </div>
      `;
    }).join('');
  } catch (e) {
    container.innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
  }
}

// Setup search type tabs
document.querySelectorAll('.search-type').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.search-type').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
  });
});

// ── Context Chips ──
function renderContextChips() {
  const container = document.getElementById('context-chips');

  const all = [
    ...state.context.javaFiles.map(f => ({ key: 'java:' + f.path, label: f.label, type: 'Java', remove: () => { state.context.javaFiles = state.context.javaFiles.filter(x => x.path !== f.path); } })),
    ...state.context.pythonFiles.map(f => ({ key: 'py:' + f.path, label: f.label, type: 'Python', remove: () => { state.context.pythonFiles = state.context.pythonFiles.filter(x => x.path !== f.path); } })),
    ...state.context.pdfIds.map(p => ({ key: 'pdf:' + p.id, label: p.label, type: 'PDF', remove: () => { state.context.pdfIds = state.context.pdfIds.filter(x => x.id !== p.id); renderPdfList(); } })),
    ...state.context.handbookServices.map(s => ({ key: 'hb:' + s.id, label: s.label, type: 'Service', remove: () => { state.context.handbookServices = state.context.handbookServices.filter(x => x.id !== s.id); } })),
  ];

  if (all.length === 0) {
    container.innerHTML = '<span style="font-size:0.75rem;color:var(--text-muted)">Kein Kontext ausgewählt</span>';
    return;
  }

  container.innerHTML = all.map(item => `
    <div class="context-chip">
      <span class="chip-type">${item.type}</span>
      <span class="chip-name" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</span>
      <span class="chip-remove" onclick="removeContextItem('${item.key}')">&times;</span>
    </div>
  `).join('');
}

function removeContextItem(key) {
  const [type, ...rest] = key.split(':');
  const id = rest.join(':');

  switch (type) {
    case 'java':
      state.context.javaFiles = state.context.javaFiles.filter(f => f.path !== id);
      break;
    case 'py':
      state.context.pythonFiles = state.context.pythonFiles.filter(f => f.path !== id);
      break;
    case 'pdf':
      state.context.pdfIds = state.context.pdfIds.filter(p => p.id !== id);
      renderPdfList();
      break;
    case 'hb':
      state.context.handbookServices = state.context.handbookServices.filter(s => s.id !== id);
      break;
  }

  // Sync to active chat's context
  syncContextToActiveChat();
  renderContextChips();
}

// Sync state.context to the active chat's context
function syncContextToActiveChat() {
  const chat = chatManager.getActive();
  if (chat) {
    chat.context = typeof structuredClone === 'function'
      ? structuredClone(state.context)
      : JSON.parse(JSON.stringify(state.context));
  }
}

// ── Utilities ──
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}


// ══════════════════════════════════════════════════════════════════════════════
// File Search & @-Mention System
// ══════════════════════════════════════════════════════════════════════════════

// ── File Cache Management ──

async function refreshFileCache(repoType = 'all') {
  try {
    const res = await fetch(`/api/files/list?repo=${repoType}`);
    if (!res.ok) return;
    const data = await res.json();

    const now = Date.now();
    if (repoType === 'all' || repoType === 'java') {
      fileCache.java.files = data.files.filter(f => f.type === 'java');
      fileCache.java.timestamp = now;
    }
    if (repoType === 'all' || repoType === 'python') {
      fileCache.python.files = data.files.filter(f => f.type === 'python');
      fileCache.python.timestamp = now;
    }
    log.info(`[FileCache] Refreshed: ${data.files.length} files`);
  } catch (e) {
    log.error('[FileCache] Refresh failed:', e);
  }
}

function isCacheValid(repoType) {
  const cache = fileCache[repoType];
  if (!cache || !cache.files.length) return false;
  return (Date.now() - cache.timestamp) < fileCache.TTL;
}

async function getCachedFiles(repoType = 'all') {
  const needsRefresh = [];
  if ((repoType === 'all' || repoType === 'java') && !isCacheValid('java')) {
    needsRefresh.push('java');
  }
  if ((repoType === 'all' || repoType === 'python') && !isCacheValid('python')) {
    needsRefresh.push('python');
  }

  if (needsRefresh.length > 0) {
    await refreshFileCache(repoType);
  }

  let files = [];
  if (repoType === 'all' || repoType === 'java') {
    files = files.concat(fileCache.java.files);
  }
  if (repoType === 'all' || repoType === 'python') {
    files = files.concat(fileCache.python.files);
  }
  return files;
}

// ── Fuzzy Search ──

function fuzzyMatch(filename, query) {
  if (!query) return 0;

  const name = filename.toLowerCase();
  const q = query.toLowerCase();

  // Exact match
  if (name === q) return 1.0;

  // Starts with query
  if (name.startsWith(q)) {
    return 0.95 - (q.length / name.length) * 0.05;
  }

  // Contains query
  if (name.includes(q)) {
    const pos = name.indexOf(q);
    return 0.85 - (pos / name.length) * 0.1;
  }

  // Character fuzzy match
  let qi = 0;
  let matched = 0;
  let consecutive = 0;
  let lastPos = -2;

  for (let i = 0; i < name.length && qi < q.length; i++) {
    if (name[i] === q[qi]) {
      matched++;
      if (i === lastPos + 1) consecutive += 0.1;
      lastPos = i;
      qi++;
    }
  }

  if (qi === q.length) {
    return Math.min(0.7, matched / name.length + consecutive);
  }

  return 0;
}

async function searchFiles(query, lang = 'all', limit = 10, repoName = null) {
  const files = await getCachedFiles(lang);

  // Filter by repo name if specified
  const filtered = repoName
    ? files.filter(f => f.repo === repoName)
    : files;

  const results = filtered
    .map(f => ({ ...f, score: fuzzyMatch(f.name, query) }))
    .filter(f => f.score > 0.1)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);

  return results;
}

// ── Explorer Repo List ──

const explorerRepoState = {
  java: { repos: [], selectedRepo: null, searchCounts: null },
  python: { repos: [], selectedRepo: null, searchCounts: null },
};

async function loadExplorerRepos(lang) {
  try {
    const res = await fetch(`/api/files/repos/${lang}`);
    if (!res.ok) return;

    const data = await res.json();
    explorerRepoState[lang].repos = data.repos || [];

    renderExplorerRepoList(lang);
    updateRepoFilterDropdown(lang);
  } catch (e) {
    console.error(`[Explorer] Error loading ${lang} repos:`, e);
  }
}

function renderExplorerRepoList(lang) {
  const listEl = document.getElementById(`${lang}-repo-list`);
  if (!listEl) return;

  const repos = explorerRepoState[lang].repos;
  const selected = explorerRepoState[lang].selectedRepo;
  const searchCounts = explorerRepoState[lang].searchCounts;

  if (repos.length === 0) {
    listEl.innerHTML = '';
    return;
  }

  listEl.innerHTML = repos.map(r => {
    // Show search count if searching, otherwise total file count
    const count = searchCounts ? (searchCounts[r.name] || 0) : r.file_count;
    const countClass = searchCounts && searchCounts[r.name] ? 'repo-list-count search-match' : 'repo-list-count';

    return `
      <div class="repo-list-item ${selected === r.name ? 'active' : ''}"
           onclick="selectExplorerRepo('${lang}', '${escapeHtml(r.name)}')"
           title="${escapeHtml(r.path)}">
        <span class="repo-list-name">${escapeHtml(r.name)}</span>
        <span class="${countClass}">${count}</span>
      </div>
    `;
  }).join('');
}

function updateRepoFilterDropdown(lang) {
  const select = document.getElementById(`${lang}-repo-filter`);
  if (!select) return;

  const repos = explorerRepoState[lang].repos;
  const selected = explorerRepoState[lang].selectedRepo;

  select.innerHTML = '<option value="">Alle</option>' +
    repos.map(r => `<option value="${escapeHtml(r.name)}" ${selected === r.name ? 'selected' : ''}>${escapeHtml(r.name)}</option>`).join('');
}

function selectExplorerRepo(lang, repoName) {
  const current = explorerRepoState[lang].selectedRepo;
  // Toggle off if clicking same repo
  explorerRepoState[lang].selectedRepo = (current === repoName) ? null : repoName;

  renderExplorerRepoList(lang);
  updateRepoFilterDropdown(lang);

  // Re-run search if there's a query
  const input = document.getElementById(`${lang}-search-input`);
  if (input && input.value.length >= 2) {
    searchExplorerFiles(lang, input.value);
  }
}

function onRepoFilterChange(lang) {
  const select = document.getElementById(`${lang}-repo-filter`);
  if (!select) return;

  const repoName = select.value || null;
  explorerRepoState[lang].selectedRepo = repoName;

  renderExplorerRepoList(lang);

  // Re-run search if there's a query
  const input = document.getElementById(`${lang}-search-input`);
  if (input && input.value.length >= 2) {
    searchExplorerFiles(lang, input.value);
  }
}

// ── Explorer Search ──

let explorerSearchTimeout = null;

async function searchExplorerFiles(lang, query) {
  const resultsEl = document.getElementById(`${lang}-search-results`);
  if (!resultsEl) return;

  if (!query || query.length < 2) {
    resultsEl.style.display = 'none';
    resultsEl.innerHTML = '';
    // Reset repo list to show total counts
    explorerRepoState[lang].searchCounts = null;
    renderExplorerRepoList(lang);
    return;
  }

  // Debounce
  clearTimeout(explorerSearchTimeout);
  explorerSearchTimeout = setTimeout(async () => {
    const repoName = explorerRepoState[lang].selectedRepo;

    // Search all files (not limited) to get accurate counts per repo
    const allResults = await searchFiles(query, lang, 100, repoName);

    // Calculate counts per repo
    const repoCounts = {};
    for (const f of allResults) {
      repoCounts[f.repo] = (repoCounts[f.repo] || 0) + 1;
    }
    explorerRepoState[lang].searchCounts = repoCounts;

    // Update repo list with search counts
    renderExplorerRepoList(lang);

    // Display limited results
    const results = allResults.slice(0, 15);

    if (results.length === 0) {
      resultsEl.innerHTML = '<div class="search-no-results">Keine Treffer</div>';
    } else {
      resultsEl.innerHTML = results.map(f => {
        // Get directory path (without filename)
        const pathParts = f.path.split('/');
        pathParts.pop();
        const dirPath = pathParts.join('/') || '.';

        return `
          <div class="search-result-item" onclick="addSearchResultToContext('${escapeHtml(f.path)}', '${escapeHtml(f.name)}', '${f.type}', '${escapeHtml(f.repo)}')">
            <span class="search-result-icon">${f.type === 'java' ? '&#9749;' : '&#128013;'}</span>
            <div class="search-result-info">
              <span class="search-result-name">${escapeHtml(f.name)}</span>
              <span class="search-result-path">${escapeHtml(dirPath)}</span>
              <span class="search-result-repo">${escapeHtml(f.repo)}</span>
            </div>
            <span class="search-result-add">+</span>
          </div>
        `;
      }).join('');
    }
    resultsEl.style.display = 'block';
  }, 200);
}

function addSearchResultToContext(path, name, type, repo = '') {
  // Check if already in context
  const contextArray = type === 'java' ? state.context.javaFiles : state.context.pythonFiles;
  if (contextArray.find(f => f.path === path && f.repo === repo)) {
    showToast('Datei bereits im Kontext', 'info');
    return;
  }

  contextArray.push({ path, label: name, type, repo });
  syncContextToActiveChat();
  renderContextChips();
  showToast(`${name} zum Kontext hinzugefügt`, 'success');

  // Clear search
  const input = document.getElementById(`${type}-search-input`);
  const results = document.getElementById(`${type}-search-results`);
  if (input) input.value = '';
  if (results) {
    results.style.display = 'none';
    results.innerHTML = '';
  }
}

// ── @-Mention System ──

function initMentionSystem() {
  const input = document.getElementById('message-input');
  if (!input) return;

  input.addEventListener('input', handleMentionInput);
  input.addEventListener('keydown', handleMentionKeydown);
  input.addEventListener('blur', () => {
    // Delay to allow click on dropdown
    setTimeout(() => {
      if (!document.activeElement?.closest('.mention-dropdown')) {
        hideMentionDropdown();
      }
    }, 150);
  });

  // Create dropdown element if not exists
  if (!document.getElementById('mention-dropdown')) {
    const dropdown = document.createElement('div');
    dropdown.id = 'mention-dropdown';
    dropdown.className = 'mention-dropdown';
    dropdown.style.display = 'none';
    input.parentElement.appendChild(dropdown);
  }
}

async function handleMentionInput(e) {
  const text = e.target.value;
  const cursorPos = e.target.selectionStart;

  // Find @ before cursor
  const beforeCursor = text.slice(0, cursorPos);
  const atMatch = beforeCursor.match(/@(\w*)$/);

  if (atMatch) {
    mentionState.active = true;
    mentionState.query = atMatch[1];
    mentionState.cursorPosition = cursorPos;
    mentionState.triggerPosition = cursorPos - atMatch[0].length;

    // Search files
    const results = await searchFiles(mentionState.query, 'all', 10);
    mentionState.results = results;
    mentionState.selectedIndex = 0;

    showMentionDropdown();
  } else {
    hideMentionDropdown();
  }
}

function handleMentionKeydown(e) {
  if (!mentionState.active) return;

  const dropdown = document.getElementById('mention-dropdown');
  if (!dropdown || dropdown.style.display === 'none') return;

  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault();
      mentionState.selectedIndex = Math.min(
        mentionState.selectedIndex + 1,
        mentionState.results.length - 1
      );
      updateMentionSelection();
      break;

    case 'ArrowUp':
      e.preventDefault();
      mentionState.selectedIndex = Math.max(mentionState.selectedIndex - 1, 0);
      updateMentionSelection();
      break;

    case ' ':  // Spacebar - toggle selection
      if (mentionState.results.length > 0) {
        e.preventDefault();
        toggleMentionSelection(mentionState.selectedIndex);
      }
      break;

    case 'Enter':
      if (mentionState.results.length > 0) {
        e.preventDefault();
        confirmMentionSelection();
      }
      break;

    case 'Escape':
      e.preventDefault();
      hideMentionDropdown();
      break;

    case 'Tab':
      if (mentionState.results.length > 0) {
        e.preventDefault();
        confirmMentionSelection();
      }
      break;
  }
}

function showMentionDropdown() {
  const dropdown = document.getElementById('mention-dropdown');
  const input = document.getElementById('message-input');
  if (!dropdown || !input) return;

  if (mentionState.results.length === 0) {
    dropdown.innerHTML = '<div class="mention-no-results">Keine Dateien gefunden</div>';
  } else {
    dropdown.innerHTML = mentionState.results.map((f, idx) => {
      const isHighlighted = idx === mentionState.selectedIndex;
      const isSelected = mentionState.selectedFiles.some(sf => sf.path === f.path);
      return `
        <div class="mention-item ${isHighlighted ? 'highlighted' : ''} ${isSelected ? 'selected' : ''}"
             data-index="${idx}"
             onclick="toggleMentionSelection(${idx})"
             ondblclick="confirmSingleMention(${idx})">
          <span class="mention-checkbox">${isSelected ? '☑' : '☐'}</span>
          <span class="mention-icon">${f.type === 'java' ? '&#9749;' : '&#128013;'}</span>
          <span class="mention-name">${escapeHtml(f.name)}</span>
          <span class="mention-type">${f.type}</span>
        </div>
      `;
    }).join('');

    // Add footer with selection count and confirm button
    const selectedCount = mentionState.selectedFiles.length;
    dropdown.innerHTML += `
      <div class="mention-footer">
        <span class="mention-count">${selectedCount} ausgewählt</span>
        <button class="mention-confirm-btn" onclick="confirmMentionSelection()" ${selectedCount === 0 ? 'disabled' : ''}>
          Hinzufügen
        </button>
      </div>
    `;
  }

  dropdown.style.display = 'block';
}

function updateMentionSelection() {
  const dropdown = document.getElementById('mention-dropdown');
  if (!dropdown) return;

  const items = dropdown.querySelectorAll('.mention-item');
  items.forEach((item, idx) => {
    item.classList.toggle('highlighted', idx === mentionState.selectedIndex);
  });

  // Scroll into view
  const highlighted = dropdown.querySelector('.mention-item.highlighted');
  if (highlighted) {
    highlighted.scrollIntoView({ block: 'nearest' });
  }
}

function toggleMentionSelection(index) {
  const file = mentionState.results[index];
  if (!file) return;

  const existingIdx = mentionState.selectedFiles.findIndex(f => f.path === file.path);
  if (existingIdx >= 0) {
    mentionState.selectedFiles.splice(existingIdx, 1);
  } else {
    mentionState.selectedFiles.push(file);
  }

  mentionState.selectedIndex = index;
  showMentionDropdown();  // Re-render
}

function confirmSingleMention(index) {
  const file = mentionState.results[index];
  if (!file) return;

  // Add just this file
  insertMentionFiles([file]);
}

function confirmMentionSelection() {
  // If nothing selected, add the highlighted item
  if (mentionState.selectedFiles.length === 0) {
    const highlighted = mentionState.results[mentionState.selectedIndex];
    if (highlighted) {
      insertMentionFiles([highlighted]);
    }
  } else {
    insertMentionFiles(mentionState.selectedFiles);
  }
}

function insertMentionFiles(files) {
  const input = document.getElementById('message-input');
  if (!input || files.length === 0) return;

  // Add files to context
  for (const file of files) {
    const contextArray = file.type === 'java' ? state.context.javaFiles : state.context.pythonFiles;
    if (!contextArray.find(f => f.path === file.path)) {
      contextArray.push({ path: file.path, label: file.name, type: file.type });
    }
  }
  syncContextToActiveChat();
  renderContextChips();

  // Remove @query from input and add file badges
  const text = input.value;
  const before = text.slice(0, mentionState.triggerPosition);
  const after = text.slice(mentionState.cursorPosition);
  const fileNames = files.map(f => f.name).join(', ');

  input.value = before + fileNames + ' ' + after;
  input.focus();

  // Show toast
  if (files.length === 1) {
    showToast(`${files[0].name} zum Kontext hinzugefügt`, 'success');
  } else {
    showToast(`${files.length} Dateien zum Kontext hinzugefügt`, 'success');
  }

  hideMentionDropdown();
}

function hideMentionDropdown() {
  mentionState.active = false;
  mentionState.query = '';
  mentionState.results = [];
  mentionState.selectedFiles = [];
  mentionState.selectedIndex = 0;

  const dropdown = document.getElementById('mention-dropdown');
  if (dropdown) {
    dropdown.style.display = 'none';
  }
}


// ══════════════════════════════════════════════════════════════════════════════
// Settings Modal
// ══════════════════════════════════════════════════════════════════════════════

const settingsState = {
  currentSection: 'llm',
  settings: null,
  modified: false,
  descriptions: {
    llm: 'LLM-Verbindung und Modell-Einstellungen',
    models: 'Verfügbare LLM-Modelle für die Auswahl',
    java: 'Java-Repository-Pfad und Ausschlüsse',
    python: 'Python-Repository-Pfad und Ausschlüsse',
    confluence: 'Confluence-Verbindung für Dokumentation',
    handbook: 'HTML-Handbuch auf Netzlaufwerk',
    skills: 'Skill-System für Spezialwissen',
    file_operations: 'Datei-Operationen (Lesen/Schreiben)',
    index: 'Such-Index-Einstellungen',
    server: 'Server-Konfiguration',
    tools: 'Pfade zu Entwickler-Tools',
    task_agents: 'Task-Decomposition Agent System',
    jira: 'Jira-Anbindung für Issue-Suche',
    context: 'Kontext-Limits für LLM',
    uploads: 'Upload-Verzeichnis und Limits',
    jenkins: 'Jenkins CI/CD Server (intern gehostet)',
    github: 'GitHub Enterprise Server (intern gehostet)',
    internal_fetch: 'Intranet-URLs abrufen (HTTP Fetch)',
    docker_sandbox: 'Container-Sandbox (Docker/Podman)',
    data_sources: 'Interne HTTP-Systeme (Jenkins, GitHub, APIs)',
    mq: 'IBM MQ Series Messaging',
    test_tool: 'Test-Tool (SOAP Services)',
    log_servers: 'Log-Server für Analyse',
    wlp: 'WebSphere Liberty Profile Server',
    maven: 'Maven Build-Konfigurationen',
    sub_agents: 'Parallele Sub-Agenten für Recherche',
    search: 'Suche-Einstellungen',
    database: 'DB2-Datenbankverbindung für Abfragen'
  }
};

// ── Settings Navigation Categories ─────────────────────────────────────────────

function toggleSettingsCategory(categoryId) {
  const category = document.querySelector(`.settings-nav-category[data-category="${categoryId}"]`);
  if (!category) return;

  category.classList.toggle('expanded');

  // Update arrow
  const arrow = category.querySelector('.category-arrow');
  if (arrow) {
    arrow.innerHTML = category.classList.contains('expanded') ? '&#9662;' : '&#9656;';
  }

  // Save state to localStorage
  saveSettingsCategoryState();
}

function saveSettingsCategoryState() {
  const expandedCategories = [];
  document.querySelectorAll('.settings-nav-category.expanded').forEach(cat => {
    expandedCategories.push(cat.dataset.category);
  });
  localStorage.setItem('settings-expanded-categories', JSON.stringify(expandedCategories));
}

function restoreSettingsCategoryState() {
  const saved = localStorage.getItem('settings-expanded-categories');
  if (saved) {
    try {
      const expandedCategories = JSON.parse(saved);
      document.querySelectorAll('.settings-nav-category').forEach(cat => {
        const isExpanded = expandedCategories.includes(cat.dataset.category);
        cat.classList.toggle('expanded', isExpanded);
        const arrow = cat.querySelector('.category-arrow');
        if (arrow) {
          arrow.innerHTML = isExpanded ? '&#9662;' : '&#9656;';
        }
      });
    } catch (e) {
      log.warn('Could not restore settings category state:', e);
    }
  }
}

function expandCategoryForSection(section) {
  // Find the category containing this section and expand it
  const item = document.querySelector(`.settings-nav-item[data-section="${section}"]`);
  if (item) {
    const category = item.closest('.settings-nav-category');
    if (category && !category.classList.contains('expanded')) {
      category.classList.add('expanded');
      const arrow = category.querySelector('.category-arrow');
      if (arrow) {
        arrow.innerHTML = '&#9662;';
      }
    }
  }
}

function filterSettingsNav(query) {
  const q = query.toLowerCase().trim();

  document.querySelectorAll('.settings-nav-category').forEach(category => {
    let hasVisibleItems = false;

    category.querySelectorAll('.settings-nav-item').forEach(item => {
      const text = item.textContent.toLowerCase();
      const section = item.dataset.section.toLowerCase();
      const matches = !q || text.includes(q) || section.includes(q);

      item.classList.toggle('search-hidden', !matches);
      if (matches) hasVisibleItems = true;
    });

    // Also check category label
    const label = category.querySelector('.category-label');
    if (label && label.textContent.toLowerCase().includes(q)) {
      hasVisibleItems = true;
      // Show all items in matching category
      category.querySelectorAll('.settings-nav-item').forEach(item => {
        item.classList.remove('search-hidden');
      });
    }

    category.classList.toggle('search-hidden', !hasVisibleItems);

    // Auto-expand categories with matches during search
    if (q && hasVisibleItems) {
      category.classList.add('expanded');
      const arrow = category.querySelector('.category-arrow');
      if (arrow) arrow.innerHTML = '&#9662;';
    }
  });
}

async function openSettings() {
  const modal = document.getElementById('settings-modal');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  // Restore category expansion state
  restoreSettingsCategoryState();

  // Expand category for current section
  expandCategoryForSection(settingsState.currentSection);

  // Setup navigation
  document.querySelectorAll('.settings-nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.settings-nav-item').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      settingsState.currentSection = btn.dataset.section;
      renderSettingsSection();
    });
  });

  await loadSettings();
  focusTrap.activate(modal);
}

function closeSettings() {
  focusTrap.deactivate();
  const modal = document.getElementById('settings-modal');
  modal.style.display = 'none';
  document.body.style.overflow = '';
  settingsState.modified = false;
  updateSettingsStatus('');

  // Reset search field
  const searchInput = document.getElementById('settings-search');
  if (searchInput) {
    searchInput.value = '';
    filterSettingsNav('');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Dashboard Functions - Phase 4 User Dashboard
// ═══════════════════════════════════════════════════════════════════════════════

let dashboardState = {
  timeRange: 'week',
  data: null,
  loading: false
};

async function openDashboard() {
  const modal = document.getElementById('dashboard-modal');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  await loadDashboardData(dashboardState.timeRange);
  focusTrap.activate(modal);
}

function closeDashboard() {
  focusTrap.deactivate();
  const modal = document.getElementById('dashboard-modal');
  modal.style.display = 'none';
  document.body.style.overflow = '';
}

async function loadDashboardData(timeRange = 'week') {
  dashboardState.timeRange = timeRange;
  dashboardState.loading = true;

  // Show loading state
  document.querySelectorAll('.chart-body').forEach(el => {
    el.innerHTML = '<div class="chart-loading">Lade Daten...</div>';
  });

  try {
    // Fetch analytics dashboard and token usage in parallel
    const [analyticsRes, tokensRes] = await Promise.all([
      fetch(`/api/analytics/dashboard?timeRange=${timeRange}`).catch(() => null),
      fetch(`/api/tokens/usage?period=${timeRange}`).catch(() => null)
    ]);

    // Parse responses
    let analyticsData = null;
    let tokensData = null;

    if (analyticsRes?.ok) {
      analyticsData = await analyticsRes.json();
    }
    if (tokensRes?.ok) {
      tokensData = await tokensRes.json();
    }

    // Merge data - prefer analytics but fallback to token data
    const data = analyticsData || {
      totalRequests: tokensData?.totalRequests || 0,
      requestsTrend: 0,
      avgResponseTime: 0,
      responseTrend: 0,
      successRate: 100,
      successTrend: 0,
      toolUsage: [],
      activityHeatmap: [],
      recentErrors: [],
      tokenUsage: { input: 0, output: 0, total: 0, limit: 100000 }
    };

    // Enhance with token tracker data if available
    if (tokensData) {
      data.tokenUsage = {
        input: tokensData.inputTokens || 0,
        output: tokensData.outputTokens || 0,
        total: tokensData.totalTokens || 0,
        limit: tokensData.budgetLimit || 100000
      };
      // Update request count from token data if analytics was empty
      if (!analyticsData && tokensData.totalRequests > 0) {
        data.totalRequests = tokensData.totalRequests;
      }
    }

    dashboardState.data = data;

    renderDashboardKPIs(data);
    renderToolUsageChart(data.toolUsage);
    renderActivityHeatmap(data.activityHeatmap);
    renderErrorsList(data.recentErrors);
    renderTokenUsage(data.tokenUsage);

  } catch (err) {
    log.error('Dashboard load error:', err);
    document.querySelectorAll('.chart-body').forEach(el => {
      el.innerHTML = '<div class="chart-error">Fehler beim Laden der Daten</div>';
    });
  } finally {
    dashboardState.loading = false;
  }
}

async function refreshDashboard() {
  await loadDashboardData(dashboardState.timeRange);
  showToast('Dashboard aktualisiert', 'success');
}

function renderDashboardKPIs(data) {
  // Total Requests
  document.getElementById('kpi-requests').textContent = formatNumber(data.totalRequests);
  renderKpiTrend('kpi-requests-trend', data.requestsTrend);

  // Avg Response Time
  document.getElementById('kpi-response-time').textContent = formatDuration(data.avgResponseTime);
  renderKpiTrend('kpi-response-trend', data.responseTrend, true); // inverted - lower is better

  // Success Rate
  document.getElementById('kpi-success-rate').textContent = data.successRate.toFixed(1) + '%';
  renderKpiTrend('kpi-success-trend', data.successTrend);

  // Token Usage
  const tokenPercent = data.tokenUsage.limit > 0
    ? Math.round((data.tokenUsage.total / data.tokenUsage.limit) * 100)
    : 0;
  document.getElementById('kpi-tokens').textContent = formatNumber(data.tokenUsage.total);
  document.getElementById('kpi-tokens-trend').innerHTML =
    `<span class="token-limit">${tokenPercent}% von ${formatNumber(data.tokenUsage.limit)}</span>`;
}

function renderKpiTrend(elementId, trend, inverted = false) {
  const el = document.getElementById(elementId);
  if (!el) return;

  const isPositive = inverted ? trend < 0 : trend > 0;
  const arrow = trend > 0 ? '&#9650;' : trend < 0 ? '&#9660;' : '';
  const trendClass = isPositive ? 'trend-positive' : trend < 0 ? 'trend-negative' : '';

  el.innerHTML = `<span class="${trendClass}">${arrow} ${Math.abs(trend).toFixed(1)}%</span>`;
}

// formatNumber defined earlier in file (Token Usage section)

function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const mins = Math.floor(ms / 60000);
  const secs = Math.round((ms % 60000) / 1000);
  return `${mins}:${secs.toString().padStart(2, '0')}min`;
}

function renderToolUsageChart(toolUsage) {
  const container = document.getElementById('chart-tool-usage');
  if (!container) return;

  if (!toolUsage || toolUsage.length === 0) {
    container.innerHTML = '<div class="chart-empty">Keine Tool-Nutzung im Zeitraum</div>';
    return;
  }

  const maxCount = Math.max(...toolUsage.map(t => t.count));

  const html = toolUsage.map(tool => {
    const percent = maxCount > 0 ? (tool.count / maxCount) * 100 : 0;
    const successColor = tool.successRate >= 90 ? 'var(--success)' :
                         tool.successRate >= 70 ? 'var(--warning)' : 'var(--danger)';
    return `
      <div class="bar-row">
        <span class="bar-label" title="${tool.tool}">${truncate(tool.tool, 20)}</span>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${percent}%; background: ${successColor};"></div>
        </div>
        <span class="bar-value">${tool.count}</span>
        <span class="bar-success" title="Success Rate">${tool.successRate.toFixed(0)}%</span>
      </div>
    `;
  }).join('');

  container.innerHTML = `<div class="bar-chart">${html}</div>`;
}

function renderActivityHeatmap(activity) {
  const container = document.getElementById('chart-activity');
  if (!container) return;

  if (!activity || activity.length === 0) {
    container.innerHTML = '<div class="chart-empty">Keine Aktivität im Zeitraum</div>';
    return;
  }

  // Group by day and hour
  const days = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
  const hours = Array.from({length: 24}, (_, i) => i);
  const maxCount = Math.max(...activity.map(a => a.count), 1);

  // Build heatmap data structure
  const heatmapData = {};
  activity.forEach(entry => {
    const date = new Date(entry.date);
    const dayIdx = date.getDay();
    const key = `${dayIdx}-${entry.hour}`;
    heatmapData[key] = (heatmapData[key] || 0) + entry.count;
  });

  let html = '<div class="heatmap">';
  html += '<div class="heatmap-header"><span></span>';
  for (let h = 0; h < 24; h += 3) {
    html += `<span class="heatmap-hour-label">${h}</span>`;
  }
  html += '</div>';

  for (let d = 1; d <= 7; d++) {
    const dayIdx = d % 7; // Start with Monday
    html += `<div class="heatmap-row">`;
    html += `<span class="heatmap-day">${days[dayIdx]}</span>`;
    for (let h = 0; h < 24; h++) {
      const key = `${dayIdx}-${h}`;
      const count = heatmapData[key] || 0;
      const intensity = count / maxCount;
      const opacity = 0.1 + intensity * 0.9;
      html += `<div class="heatmap-cell" style="opacity: ${opacity};" title="${days[dayIdx]} ${h}:00 - ${count} Aktivitäten"></div>`;
    }
    html += '</div>';
  }
  html += '</div>';

  container.innerHTML = html;
}

function renderErrorsList(errors) {
  const container = document.getElementById('chart-errors');
  if (!container) return;

  if (!errors || errors.length === 0) {
    container.innerHTML = '<div class="chart-empty chart-success">&#10003; Keine Fehler im Zeitraum</div>';
    return;
  }

  const html = errors.map((err, index) => {
    // Zeitstempel formatieren
    const timestamp = err.timestamp ? new Date(err.timestamp).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    }) : '';

    // Status-Badge
    const statusBadge = err.wasResolved
      ? '<span class="status-badge status-success">Behoben</span>'
      : err.hasSuggestedFix
        ? '<span class="status-badge status-warning">Fix verfuegbar</span>'
        : '<span class="status-badge status-error">Offen</span>';

    // Location Info
    const locationInfo = err.filePath
      ? `<div class="error-location">
           <span class="location-icon">&#128196;</span>
           <span class="location-path">${escapeHtml(err.filePath)}${err.lineNumber ? ':' + err.lineNumber : ''}</span>
         </div>`
      : '';

    // Tool Info
    const toolInfo = err.tool && err.tool !== 'unknown'
      ? `<span class="error-tool" title="Tool: ${escapeHtml(err.tool)}">${escapeHtml(err.tool)}</span>`
      : '';

    // Fix Info
    const fixInfo = err.hasSuggestedFix && err.suggestedFix
      ? `<div class="error-fix-suggestion">
           <span class="fix-icon">&#128161;</span>
           <span class="fix-text">${escapeHtml(err.suggestedFix)}</span>
           ${err.fixConfidence ? `<span class="fix-confidence">${(err.fixConfidence * 100).toFixed(0)}%</span>` : ''}
         </div>`
      : '';

    // Session/Context Info (klappbar)
    const contextInfo = err.sessionId
      ? `<div class="error-context" title="Session: ${escapeHtml(err.sessionId)}">
           <span class="context-label">Session:</span>
           <span class="context-value">${escapeHtml(err.sessionId.substring(0, 8))}...</span>
         </div>`
      : '';

    return `
      <div class="error-item ${err.wasResolved ? 'resolved' : ''}" data-error-id="${err.id || index}">
        <div class="error-header">
          <span class="error-icon">&#9888;</span>
          <span class="error-type">${escapeHtml(err.errorType)}</span>
          ${toolInfo}
          ${statusBadge}
          <span class="error-time">${timestamp}</span>
        </div>
        ${locationInfo}
        <div class="error-message" onclick="toggleErrorDetails('${err.id || index}')">${escapeHtml(err.message)}</div>
        <div class="error-details" id="error-details-${err.id || index}" style="display:none;">
          ${err.stackTrace ? `<pre class="error-stack">${escapeHtml(err.stackTrace)}</pre>` : ''}
          ${err.toolArgs ? `<div class="error-args"><strong>Argumente:</strong> <code>${escapeHtml(JSON.stringify(err.toolArgs))}</code></div>` : ''}
          ${contextInfo}
        </div>
        ${fixInfo}
        <div class="error-actions">
          ${err.patternId ? `
            <span class="pattern-badge">Pattern: ${escapeHtml(err.patternName || err.patternId.substring(0, 8))}</span>
            <button class="btn btn-sm" onclick="viewErrorPattern('${err.patternId}')">Pattern ansehen</button>
          ` : `
            <button class="btn btn-sm btn-secondary" onclick="learnErrorPattern('${err.errorType}', '${err.id || ''}', '${escapeHtml(err.message || '')}')">Pattern lernen</button>
          `}
          ${err.hasSuggestedFix && !err.wasResolved ? `
            <button class="btn btn-sm btn-primary" onclick="applyErrorFix('${err.id}')">Fix anwenden</button>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');

  container.innerHTML = `<div class="errors-list">${html}</div>`;
}

function toggleErrorDetails(errorId) {
  const details = document.getElementById(`error-details-${errorId}`);
  if (details) {
    details.style.display = details.style.display === 'none' ? 'block' : 'none';
  }
}

async function applyErrorFix(attemptId) {
  if (!attemptId) return;

  try {
    const res = await fetch(`/api/healing/attempts/${attemptId}/apply`, { method: 'POST' });
    const data = await res.json();

    if (data.success) {
      showNotification('Fix erfolgreich angewendet', 'success');
      // Dashboard neu laden
      loadDashboardData();
    } else {
      showNotification('Fix fehlgeschlagen: ' + (data.message || 'Unbekannter Fehler'), 'error');
    }
  } catch (e) {
    showNotification('Fehler beim Anwenden: ' + e.message, 'error');
  }
}

function renderTokenUsage(tokenUsage) {
  const container = document.getElementById('chart-tokens');
  if (!container) return;

  const total = tokenUsage.total || 0;
  const limit = tokenUsage.limit || 100000;
  const input = tokenUsage.input || 0;
  const output = tokenUsage.output || 0;
  const percent = Math.min((total / limit) * 100, 100);

  const ringColor = percent >= 90 ? 'var(--danger)' :
                    percent >= 70 ? 'var(--warning)' : 'var(--success)';

  const html = `
    <div class="token-usage-chart">
      <div class="token-ring" style="--percent: ${percent}; --ring-color: ${ringColor};">
        <div class="token-ring-inner">
          <span class="token-percent">${percent.toFixed(0)}%</span>
        </div>
      </div>
      <div class="token-details">
        <div class="token-row">
          <span class="token-label">Input:</span>
          <span class="token-value">${formatNumber(input)}</span>
        </div>
        <div class="token-row">
          <span class="token-label">Output:</span>
          <span class="token-value">${formatNumber(output)}</span>
        </div>
        <div class="token-row token-total">
          <span class="token-label">Total:</span>
          <span class="token-value">${formatNumber(total)} / ${formatNumber(limit)}</span>
        </div>
      </div>
    </div>
  `;

  container.innerHTML = html;
}

function truncate(str, maxLen) {
  if (str.length <= maxLen) return str;
  return str.substring(0, maxLen - 3) + '...';
}

async function exportDashboard() {
  try {
    const res = await fetch('/api/analytics/report?days=' + (dashboardState.timeRange === 'day' ? 1 : dashboardState.timeRange === 'week' ? 7 : 30));
    if (!res.ok) throw new Error('Export failed');

    const text = await res.text();
    const blob = new Blob([text], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `dashboard_report_${new Date().toISOString().split('T')[0]}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showToast('Report exportiert', 'success');
  } catch (err) {
    log.error('Export error:', err);
    showToast('Export fehlgeschlagen', 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Pattern Learning Functions - Phase 5
// ═══════════════════════════════════════════════════════════════════════════════

let patternState = {
  currentPattern: null,
  currentError: null
};

async function viewErrorPattern(patternId) {
  try {
    const res = await fetch(`/api/patterns/${patternId}`);
    if (!res.ok) throw new Error('Pattern nicht gefunden');

    const pattern = await res.json();
    showPatternModal(pattern);
  } catch (err) {
    log.error('Pattern load error:', err);
    showToast('Pattern konnte nicht geladen werden', 'error');
  }
}

function showPatternModal(pattern, confidence = null) {
  patternState.currentPattern = pattern;

  const modal = document.getElementById('pattern-modal');
  const content = document.getElementById('pattern-modal-content');

  const confidenceValue = confidence !== null ? confidence : pattern.confidence;
  const confidencePercent = Math.round(confidenceValue * 100);
  const confidenceClass = confidencePercent >= 80 ? 'high' : confidencePercent >= 50 ? 'medium' : 'low';

  content.innerHTML = `
    <div class="pattern-match-info">
      <div class="pattern-type">${escapeHtml(pattern.error_type)}</div>
      <div class="pattern-confidence ${confidenceClass}">
        <div class="confidence-bar">
          <div class="confidence-fill" style="width: ${confidencePercent}%"></div>
        </div>
        <span class="confidence-value">${confidencePercent}% Confidence</span>
      </div>
      <div class="pattern-stats">
        <span>Gesehen: ${pattern.times_seen}x</span>
        <span>Gelöst: ${pattern.times_solved}x</span>
        <span>Akzeptiert: ${Math.round(pattern.acceptance_rate * 100)}%</span>
        ${pattern.avg_rating > 0 ? `<span>Rating: ${'⭐'.repeat(Math.round(pattern.avg_rating))}</span>` : ''}
      </div>
    </div>

    <div class="pattern-solution">
      <h4>Lösungsvorschlag</h4>
      <div class="solution-description">${escapeHtml(pattern.solution_description)}</div>

      ${pattern.solution_steps && pattern.solution_steps.length > 0 ? `
        <div class="solution-steps">
          <h5>Schritte:</h5>
          <ol>
            ${pattern.solution_steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}
          </ol>
        </div>
      ` : ''}

      ${pattern.solution_code ? `
        <div class="solution-code">
          <h5>Code-Änderung:</h5>
          <pre><code>${escapeHtml(pattern.solution_code)}</code></pre>
        </div>
      ` : ''}

      ${pattern.tools_used && pattern.tools_used.length > 0 ? `
        <div class="solution-tools">
          <span class="tools-label">Tools:</span>
          ${pattern.tools_used.map(t => `<span class="tool-badge">${escapeHtml(t)}</span>`).join('')}
        </div>
      ` : ''}
    </div>

    <div class="pattern-actions">
      <div class="pattern-action-buttons">
        <button class="btn btn-success" onclick="applyPattern('${pattern.id}')">
          &#10003; Anwenden
        </button>
        <button class="btn btn-secondary" onclick="skipPattern('${pattern.id}')">
          &#10005; Überspringen
        </button>
      </div>
      <div class="pattern-rating">
        <span>Bewerten:</span>
        <div class="rating-stars">
          ${[1, 2, 3, 4, 5].map(star => `
            <button class="star-btn" onclick="ratePattern('${pattern.id}', ${star})" title="${star} Sterne">
              &#9733;
            </button>
          `).join('')}
        </div>
      </div>
    </div>
  `;

  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  focusTrap.activate(modal);
}

function closePatternModal() {
  focusTrap.deactivate();
  const modal = document.getElementById('pattern-modal');
  modal.style.display = 'none';
  document.body.style.overflow = '';
  patternState.currentPattern = null;
}

async function applyPattern(patternId) {
  try {
    // Record acceptance feedback
    await fetch(`/api/patterns/${patternId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accepted: true })
    });

    closePatternModal();
    showToast('Pattern angewendet', 'success');

    // Insert solution as assistant suggestion in chat
    if (patternState.currentPattern) {
      const pattern = patternState.currentPattern;
      let message = `**Lösungsvorschlag basierend auf bekanntem Pattern:**\n\n`;
      message += pattern.solution_description + '\n\n';

      if (pattern.solution_steps && pattern.solution_steps.length > 0) {
        message += '**Schritte:**\n';
        pattern.solution_steps.forEach((step, i) => {
          message += `${i + 1}. ${step}\n`;
        });
        message += '\n';
      }

      if (pattern.solution_code) {
        message += '**Code:**\n```\n' + pattern.solution_code + '\n```\n';
      }

      // Add to current chat
      const activeChat = state.chats.find(c => c.id === state.activeChat);
      if (activeChat) {
        appendMessageToPane(activeChat.pane, 'assistant', message);
      }
    }
  } catch (err) {
    log.error('Apply pattern error:', err);
    showToast('Fehler beim Anwenden', 'error');
  }
}

async function skipPattern(patternId) {
  try {
    await fetch(`/api/patterns/${patternId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accepted: false })
    });

    closePatternModal();
    showToast('Pattern übersprungen', 'info');
  } catch (err) {
    log.error('Skip pattern error:', err);
  }
}

async function ratePattern(patternId, rating) {
  try {
    const res = await fetch(`/api/patterns/${patternId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accepted: true, rating: rating })
    });

    if (res.ok) {
      showToast(`Bewertung gespeichert: ${rating} Sterne`, 'success');
      closePatternModal();
    }
  } catch (err) {
    log.error('Rate pattern error:', err);
    showToast('Bewertung fehlgeschlagen', 'error');
  }
}

async function learnErrorPattern(errorType, errorId = '', errorMessage = '') {
  // Open a learning dialog mit mehr Kontext
  const dialogHtml = `
    <div class="pattern-learn-dialog">
      <h3>Pattern für Fehler lernen</h3>
      <div class="dialog-section">
        <label>Fehlertyp:</label>
        <input type="text" id="pattern-error-type" value="${escapeHtml(errorType)}" readonly />
      </div>
      <div class="dialog-section">
        <label>Fehlermeldung:</label>
        <textarea id="pattern-error-text" rows="3" readonly>${escapeHtml(errorMessage)}</textarea>
      </div>
      <div class="dialog-section">
        <label>Lösung beschreiben: *</label>
        <textarea id="pattern-solution" rows="3" placeholder="Kurze Beschreibung wie der Fehler behoben wird..."></textarea>
      </div>
      <div class="dialog-section">
        <label>Lösungsschritte (einer pro Zeile):</label>
        <textarea id="pattern-steps" rows="4" placeholder="1. Schritt eins&#10;2. Schritt zwei&#10;3. ..."></textarea>
      </div>
      <div class="dialog-section">
        <label>Betroffene Tools (kommagetrennt):</label>
        <input type="text" id="pattern-tools" placeholder="z.B. edit_file, search_code" />
      </div>
    </div>
  `;

  // Modal anzeigen
  const result = await showConfirmDialog('Pattern lernen', dialogHtml, 'Speichern', 'Abbrechen');

  if (!result) return;

  const solution = document.getElementById('pattern-solution')?.value?.trim();
  if (!solution) {
    showNotification('Bitte eine Lösung beschreiben', 'warning');
    return;
  }

  const stepsText = document.getElementById('pattern-steps')?.value || '';
  const steps = stepsText.split('\n').map(s => s.trim()).filter(s => s);

  const toolsText = document.getElementById('pattern-tools')?.value || '';
  const tools = toolsText.split(',').map(t => t.trim()).filter(t => t);

  try {
    const res = await fetch('/api/patterns/learn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        errorType: errorType,
        errorText: errorMessage || errorType,
        solutionDescription: solution,
        solutionSteps: steps,
        toolsUsed: tools,
        filesChanged: []
      })
    });

    if (res.ok) {
      const data = await res.json();
      showNotification(data.isNew ? 'Neues Pattern gelernt!' : 'Pattern aktualisiert!', 'success');
      // Dashboard neu laden
      loadDashboardData();
    } else {
      throw new Error('Learning failed');
    }
  } catch (err) {
    log.error('Learn pattern error:', err);
    showNotification('Pattern-Learning fehlgeschlagen', 'error');
  }
}

async function suggestPatternForError(errorType, stackTrace = '', fileContext = '') {
  try {
    const res = await fetch('/api/patterns/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        errorType: errorType,
        stackTrace: stackTrace,
        fileContext: fileContext
      })
    });

    if (!res.ok) return null;

    const data = await res.json();

    if (data.pattern && data.confidence >= 0.5) {
      patternState.currentError = errorType;
      showPatternModal(data.pattern, data.confidence);
      return data.pattern;
    }

    return null;
  } catch (err) {
    log.error('Suggest pattern error:', err);
    return null;
  }
}

// Auto-detect errors and suggest patterns (hook into tool results)
function checkForErrorPatterns(toolResult) {
  if (!toolResult || !toolResult.error) return;

  const errorText = toolResult.error;

  // Extract error type
  const errorTypes = [
    /NullPointerException/,
    /IllegalArgumentException/,
    /SQLException/,
    /IOException/,
    /TypeError/,
    /ValueError/,
    /KeyError/,
    /AttributeError/
  ];

  for (const regex of errorTypes) {
    const match = errorText.match(regex);
    if (match) {
      // Delay to not interrupt flow
      setTimeout(() => {
        suggestPatternForError(match[0], errorText, '');
      }, 500);
      break;
    }
  }
}

async function loadSettings() {
  showSettingsLoading(true);
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    settingsState.settings = data.settings;
    renderSettingsSection();
  } catch (err) {
    log.error('Settings load error:', err);
    updateSettingsStatus('Fehler beim Laden', 'error');
  } finally {
    showSettingsLoading(false);
  }
}

async function reloadSettings() {
  try {
    await fetch('/api/settings/reload', { method: 'POST' });
    await loadSettings();
    updateSettingsStatus('Neu geladen', 'success');
    // Auch Modell-Dropdown aktualisieren
    await loadModels();
  } catch (err) {
    updateSettingsStatus('Fehler beim Neuladen', 'error');
  }
}

function showSettingsLoading(show) {
  document.getElementById('settings-loading').style.display = show ? 'flex' : 'none';
  document.getElementById('settings-form').style.display = show ? 'none' : 'block';
}

function updateSettingsStatus(msg, type = '') {
  const el = document.getElementById('settings-status');
  el.textContent = msg;
  el.className = 'settings-status ' + type;
  if (msg) {
    setTimeout(() => { el.textContent = ''; el.className = 'settings-status'; }, TIMING.TOAST_DEFAULT);
  }
}

function renderSettingsSection() {
  const section = settingsState.currentSection;

  if (section === 'data_sources') {
    renderDataSourcesSection();
    return;
  }

  if (section === 'mq') {
    renderMQSection();
    return;
  }

  if (section === 'test_tool') {
    renderTestToolSection();
    return;
  }

  if (section === 'log_servers') {
    renderLogServersSection();
    return;
  }

  if (section === 'wlp') {
    renderWLPSection();
    return;
  }

  if (section === 'maven') {
    renderMavenSection();
    return;
  }

  if (section === 'search') {
    renderSearchSettingsSection();
    return;
  }

  if (section === 'jenkins') {
    renderJenkinsSection();
    return;
  }

  if (section === 'servicenow') {
    renderServiceNowSection();
    return;
  }

  if (section === 'github') {
    renderGitHubSection();
    return;
  }

  if (section === 'internal_fetch') {
    renderInternalFetchSection();
    return;
  }

  if (section === 'docker_sandbox') {
    renderDockerSandboxSection();
    return;
  }

  if (section === 'proxy') {
    renderProxySection();
    return;
  }

  if (section === 'update') {
    renderUpdateSection();
    return;
  }

  if (section === 'arena') {
    renderArenaSettingsSection();
    return;
  }

  if (section === 'analytics') {
    renderAnalyticsSettingsSection();
    return;
  }

  const values = settingsState.settings[section];
  const desc = settingsState.descriptions[section] || '';

  if (section === 'models') {
    renderModelsSection();
    return;
  }

  if (section === 'task_agents') {
    renderTaskAgentsSection();
    return;
  }

  if (section === 'java' || section === 'python') {
    renderReposSection(section);
    return;
  }

  if (section === 'sub_agents') {
    renderSubAgentsSection();
    return;
  }

  // Section-spezifische Beschreibungen
  const sectionDescriptions = {
    server: 'Konfiguration des FastAPI-Servers (Host, Port). Änderungen erfordern einen Neustart.',
    database: 'DB2-Datenbankverbindung für SQL-Abfragen. Der Agent kann Tabellen abfragen (nur SELECT).',
    llm: 'LLM-Verbindungseinstellungen. tool_model = schnelles Modell für Suche, analysis_model = großes Modell für Antworten.',
  };

  let html = `
    <div class="settings-section">
      <h3 class="settings-section-title">${section.toUpperCase()}</h3>
      <p class="settings-section-desc">${sectionDescriptions[section] || desc}</p>
    </div>
  `;

  if (!values || typeof values !== 'object') {
    html += '<p>Keine Einstellungen verfügbar.</p>';
    document.getElementById('settings-form').innerHTML = html;
    return;
  }

  html += renderSettingsFields(section, values);

  // Spezielle Buttons für bestimmte Sections
  if (section === 'database') {
    html += `
      <div class="settings-actions-section">
        <button class="btn btn-secondary" onclick="testDatabaseConnection()">
          🔌 Verbindung testen
        </button>
        <span id="db-test-result" class="test-result"></span>
      </div>
    `;
  }

  document.getElementById('settings-form').innerHTML = html;
}

async function testDatabaseConnection() {
  const resultEl = document.getElementById('db-test-result');
  resultEl.textContent = '⏳ Teste Verbindung...';
  resultEl.className = 'test-result testing';

  try {
    const res = await fetch('/api/database/test', { method: 'POST' });
    const data = await res.json();

    if (data.success) {
      resultEl.textContent = `✓ ${data.message}`;
      resultEl.className = 'test-result success';
    } else {
      resultEl.textContent = `✗ ${data.error}`;
      resultEl.className = 'test-result error';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

// ============================================================================
// ServiceNow Section
// ============================================================================

function renderServiceNowSection() {
  const values = settingsState.settings.servicenow || {};

  let html = `
    <div class="settings-section">
      <h3 class="settings-section-title">SERVICENOW</h3>
      <p class="settings-section-desc">
        ServiceNow Service Portal Integration. Ermoeglicht Abfragen von Anwendungen,
        Changes, Incidents und Knowledge Base Artikeln.
      </p>
    </div>
  `;

  // Status-Anzeige
  html += `
    <div class="settings-field">
      <label>Status</label>
      <div id="servicenow-status" class="status-indicator">
        <span class="status-icon">⏳</span>
        <span class="status-text">Lade Status...</span>
      </div>
    </div>
  `;

  // Enabled Toggle
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-enabled">Aktiviert</label>
      <label class="checkbox-label">
        <input type="checkbox" id="setting-servicenow-enabled"
          data-section="servicenow" data-key="enabled"
          ${values.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        ${values.enabled ? 'Aktiviert' : 'Deaktiviert'}
      </label>
    </div>
  `;

  // Instance URL
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-instance_url">Instance URL</label>
      <input type="text" id="setting-servicenow-instance_url"
        data-section="servicenow" data-key="instance_url"
        value="${escapeHtml(values.instance_url || '')}"
        placeholder="http://localhost:8080"
        onchange="markSettingsModified()"
        style="font-family: var(--font-mono);">
    </div>
  `;

  // Auth Type
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-auth_type">Auth Type</label>
      <select id="setting-servicenow-auth_type"
        data-section="servicenow" data-key="auth_type"
        onchange="markSettingsModified()">
        <option value="basic" ${values.auth_type === 'basic' ? 'selected' : ''}>Basic Auth</option>
        <option value="oauth2" ${values.auth_type === 'oauth2' ? 'selected' : ''}>OAuth2</option>
      </select>
    </div>
  `;

  // Username
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-username">Username</label>
      <input type="text" id="setting-servicenow-username"
        data-section="servicenow" data-key="username"
        value="${escapeHtml(values.username || '')}"
        placeholder="admin"
        onchange="markSettingsModified()">
    </div>
  `;

  // Password
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-password">Password</label>
      <input type="password" id="setting-servicenow-password"
        data-section="servicenow" data-key="password"
        value="${escapeHtml(values.password || '')}"
        onchange="markSettingsModified()" autocomplete="off">
    </div>
  `;

  // Cache TTL
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-cache_ttl_seconds">Cache TTL (Sekunden)</label>
      <input type="number" id="setting-servicenow-cache_ttl_seconds"
        data-section="servicenow" data-key="cache_ttl_seconds"
        value="${values.cache_ttl_seconds || 300}"
        onchange="markSettingsModified()">
    </div>
  `;

  // Max Results
  html += `
    <div class="settings-field">
      <label for="setting-servicenow-max_results_default">Max Results</label>
      <input type="number" id="setting-servicenow-max_results_default"
        data-section="servicenow" data-key="max_results_default"
        value="${values.max_results_default || 20}"
        onchange="markSettingsModified()">
    </div>
  `;

  // Test Connection Button
  html += `
    <div class="settings-actions-section">
      <button class="btn btn-secondary" onclick="testServiceNowConnection()">
        🔌 Verbindung testen
      </button>
      <button class="btn btn-secondary" onclick="clearServiceNowCache()">
        🗑️ Cache leeren
      </button>
      <span id="servicenow-test-result" class="test-result"></span>
    </div>
  `;

  document.getElementById('settings-form').innerHTML = html;

  // Status laden
  loadServiceNowStatus();
}

async function loadServiceNowStatus() {
  const statusEl = document.getElementById('servicenow-status');
  if (!statusEl) return;

  try {
    const res = await fetch('/api/servicenow/status');
    const data = await res.json();

    if (data.enabled) {
      statusEl.innerHTML = `
        <span class="status-icon status-enabled">✓</span>
        <span class="status-text">Aktiviert - ${data.instance_url || 'Keine URL'}</span>
      `;
    } else {
      statusEl.innerHTML = `
        <span class="status-icon status-disabled">○</span>
        <span class="status-text">Deaktiviert</span>
      `;
    }
  } catch (e) {
    statusEl.innerHTML = `
      <span class="status-icon status-error">✗</span>
      <span class="status-text">Fehler beim Laden</span>
    `;
  }
}

async function testServiceNowConnection() {
  const resultEl = document.getElementById('servicenow-test-result');
  resultEl.textContent = '⏳ Teste Verbindung...';
  resultEl.className = 'test-result testing';

  try {
    const res = await fetch('/api/servicenow/test-connection', { method: 'POST' });
    const data = await res.json();

    if (data.success) {
      resultEl.textContent = `✓ ${data.message} (${data.response_time_ms}ms)`;
      resultEl.className = 'test-result success';
    } else {
      resultEl.textContent = `✗ ${data.message || data.error}`;
      resultEl.className = 'test-result error';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

async function clearServiceNowCache() {
  const resultEl = document.getElementById('servicenow-test-result');
  resultEl.textContent = '⏳ Leere Cache...';
  resultEl.className = 'test-result testing';

  try {
    const res = await fetch('/api/servicenow/clear-cache', { method: 'POST' });
    const data = await res.json();

    resultEl.textContent = `✓ ${data.message}`;
    resultEl.className = 'test-result success';
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

function renderSettingsFields(section, values) {
  let html = '';

  for (const [key, value] of Object.entries(values)) {
    const fieldId = `setting-${section}-${key}`;
    const labelText = formatFieldLabel(key);

    html += `<div class="settings-field">`;
    html += `<label for="${fieldId}">${labelText}</label>`;

    if (typeof value === 'boolean') {
      html += `
        <label class="checkbox-label">
          <input type="checkbox" id="${fieldId}" data-section="${section}" data-key="${key}"
            ${value ? 'checked' : ''} onchange="markSettingsModified()">
          ${value ? 'Aktiviert' : 'Deaktiviert'}
        </label>
      `;
    } else if (Array.isArray(value)) {
      html += renderArrayField(fieldId, section, key, value);
    } else if (value !== null && typeof value === 'object') {
      // Dict/Object Felder als Key-Value-Paare darstellen
      html += renderDictField(fieldId, section, key, value);
    } else if (typeof value === 'number') {
      html += `
        <input type="number" id="${fieldId}" data-section="${section}" data-key="${key}"
          value="${value}" onchange="markSettingsModified()">
      `;
    } else if (key.includes('password') || key.includes('api_key') || key.includes('api_token') || key.includes('secret')) {
      html += `
        <input type="password" id="${fieldId}" data-section="${section}" data-key="${key}"
          value="${escapeHtml(value)}" onchange="markSettingsModified()" autocomplete="off">
      `;
    } else if (key.includes('path') || key.includes('url') || key.includes('directory')) {
      html += `
        <input type="text" id="${fieldId}" data-section="${section}" data-key="${key}"
          value="${escapeHtml(value)}" onchange="markSettingsModified()"
          placeholder="${getPlaceholder(key)}" style="font-family: var(--font-mono);">
      `;
    } else if (key === 'default_mode') {
      html += `
        <select id="${fieldId}" data-section="${section}" data-key="${key}" onchange="markSettingsModified()">
          <option value="read_only" ${value === 'read_only' ? 'selected' : ''}>read_only - Nur Lesen</option>
          <option value="write_with_confirm" ${value === 'write_with_confirm' ? 'selected' : ''}>write_with_confirm - Mit Bestätigung</option>
          <option value="autonomous" ${value === 'autonomous' ? 'selected' : ''}>autonomous - Autonom</option>
        </select>
      `;
    } else if (key === 'driver' && section === 'database') {
      html += `
        <select id="${fieldId}" data-section="${section}" data-key="${key}" onchange="markSettingsModified()">
          <option value="jaydebeapi" ${value === 'jaydebeapi' ? 'selected' : ''}>jaydebeapi - JDBC (empfohlen)</option>
          <option value="ibm_db" ${value === 'ibm_db' ? 'selected' : ''}>ibm_db - Native Driver</option>
        </select>
      `;
    } else {
      html += `
        <input type="text" id="${fieldId}" data-section="${section}" data-key="${key}"
          value="${escapeHtml(value)}" onchange="markSettingsModified()">
      `;
    }

    html += `</div>`;
  }

  return html;
}

function renderArrayField(fieldId, section, key, values) {
  let html = `<div class="settings-array" id="${fieldId}-container">`;

  values.forEach((val, idx) => {
    html += `
      <div class="settings-array-item">
        <input type="text" value="${escapeHtml(val)}"
          data-section="${section}" data-key="${key}" data-index="${idx}"
          onchange="markSettingsModified()">
        <button onclick="removeArrayItem('${fieldId}', ${idx})">✕</button>
      </div>
    `;
  });

  html += `
    <button class="settings-array-add" onclick="addArrayItem('${fieldId}', '${section}', '${key}')">
      + Hinzufügen
    </button>
  </div>`;

  return html;
}

function renderDictField(fieldId, section, key, dictValue) {
  // Dict/Object als Key-Value-Paare bearbeitbar machen
  let html = `<div class="settings-dict" id="${fieldId}-container">`;

  const entries = Object.entries(dictValue || {});

  if (entries.length === 0) {
    html += `<div class="settings-dict-empty" style="color: var(--text-secondary); font-size: 0.9em; padding: 8px 0;">Keine Einträge</div>`;
  }

  entries.forEach(([k, v], idx) => {
    const displayValue = typeof v === 'number' ? v : escapeHtml(String(v));
    const inputType = typeof v === 'number' ? 'number' : 'text';
    html += `
      <div class="settings-dict-item" style="display: flex; gap: 8px; margin-bottom: 6px;">
        <input type="text" value="${escapeHtml(k)}" placeholder="Schlüssel"
          data-section="${section}" data-key="${key}" data-dict-key="${idx}"
          onchange="markSettingsModified()" style="flex: 1; font-family: var(--font-mono);">
        <input type="${inputType}" value="${displayValue}" placeholder="Wert"
          data-section="${section}" data-key="${key}" data-dict-value="${idx}"
          onchange="markSettingsModified()" style="flex: 1;">
        <button onclick="removeDictItem('${fieldId}', '${escapeHtml(k)}')" style="padding: 4px 8px;">✕</button>
      </div>
    `;
  });

  html += `
    <button class="settings-array-add" onclick="addDictItem('${fieldId}', '${section}', '${key}')" style="margin-top: 4px;">
      + Eintrag hinzufügen
    </button>
  </div>`;

  return html;
}

function addDictItem(fieldId, section, key) {
  const container = document.getElementById(fieldId + '-container');
  // Entferne "Keine Einträge" Hinweis falls vorhanden
  const emptyHint = container.querySelector('.settings-dict-empty');
  if (emptyHint) emptyHint.remove();

  const idx = container.querySelectorAll('.settings-dict-item').length;

  const newItem = document.createElement('div');
  newItem.className = 'settings-dict-item';
  newItem.style.cssText = 'display: flex; gap: 8px; margin-bottom: 6px;';
  newItem.innerHTML = `
    <input type="text" value="" placeholder="Schlüssel"
      data-section="${section}" data-key="${key}" data-dict-key="${idx}"
      onchange="markSettingsModified()" style="flex: 1; font-family: var(--font-mono);">
    <input type="text" value="" placeholder="Wert"
      data-section="${section}" data-key="${key}" data-dict-value="${idx}"
      onchange="markSettingsModified()" style="flex: 1;">
    <button onclick="this.parentElement.remove(); markSettingsModified();" style="padding: 4px 8px;">✕</button>
  `;

  container.insertBefore(newItem, container.querySelector('.settings-array-add'));
  markSettingsModified();
}

function removeDictItem(fieldId, keyToRemove) {
  const container = document.getElementById(fieldId + '-container');
  const items = container.querySelectorAll('.settings-dict-item');
  for (const item of items) {
    const keyInput = item.querySelector('[data-dict-key]');
    if (keyInput && keyInput.value === keyToRemove) {
      item.remove();
      markSettingsModified();
      break;
    }
  }
}

function addArrayItem(fieldId, section, key) {
  const container = document.getElementById(fieldId + '-container');
  const items = container.querySelectorAll('.settings-array-item');
  const newIdx = items.length;

  const newItem = document.createElement('div');
  newItem.className = 'settings-array-item';
  newItem.innerHTML = `
    <input type="text" value=""
      data-section="${section}" data-key="${key}" data-index="${newIdx}"
      onchange="markSettingsModified()">
    <button onclick="this.parentElement.remove(); markSettingsModified();">✕</button>
  `;

  container.insertBefore(newItem, container.querySelector('.settings-array-add'));
  markSettingsModified();
}

function removeArrayItem(fieldId, idx) {
  const container = document.getElementById(fieldId + '-container');
  const items = container.querySelectorAll('.settings-array-item');
  if (items[idx]) {
    items[idx].remove();
    markSettingsModified();
  }
}

async function renderTaskAgentsSection() {
  const form = document.getElementById('settings-form');
  const cfg = settingsState.settings.task_agents || {};
  const models = settingsState.settings.models || [];
  const llmCfg = settingsState.settings.llm || {};

  // Agent-Typen mit Beschreibungen
  const agentTypes = [
    { key: 'research_model', label: 'Research Agent', desc: 'Sucht und sammelt Informationen', fallback: llmCfg.tool_model || llmCfg.default_model },
    { key: 'code_model', label: 'Code Agent', desc: 'Schreibt und bearbeitet Code', fallback: llmCfg.default_model },
    { key: 'analyst_model', label: 'Analyst Agent', desc: 'Analysiert und reviewed Code', fallback: llmCfg.analysis_model || llmCfg.default_model },
    { key: 'devops_model', label: 'DevOps Agent', desc: 'CI/CD, Docker, Deployment', fallback: llmCfg.tool_model || llmCfg.default_model },
    { key: 'docs_model', label: 'Docs Agent', desc: 'Erstellt Dokumentation', fallback: llmCfg.tool_model || llmCfg.default_model },
    { key: 'debug_model', label: 'Debug Agent', desc: 'Debuggt und testet Code (lokal/remote)', fallback: llmCfg.analysis_model || llmCfg.default_model },
  ];

  let html = `
    <div class="settings-section">
      <h3 class="settings-section-title">TASK-DECOMPOSITION AGENT SYSTEM</h3>
      <p class="settings-section-desc">
        Zerlegt komplexe Anfragen in spezialisierte Tasks, die von dedizierten Agenten
        mit eigenen Models und System-Prompts ausgefuehrt werden.
      </p>
    </div>

    <!-- Master Switch -->
    <div class="settings-field" style="background: var(--bg-secondary); padding: 12px; border-radius: 6px; margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 12px;">
        <label class="toggle-switch">
          <input type="checkbox" id="task-agents-enabled"
            data-section="task_agents" data-key="enabled"
            ${cfg.enabled ? 'checked' : ''}
            onchange="markSettingsModified()">
          <span class="toggle-slider"></span>
        </label>
        <div>
          <strong>Task-Decomposition aktivieren</strong>
          <div style="font-size: 0.85em; color: var(--text-secondary);">
            Komplexe Anfragen werden automatisch in spezialisierte Tasks zerlegt
          </div>
        </div>
      </div>
    </div>

    <!-- Agent Models -->
    <div class="settings-section">
      <h4 style="margin: 0 0 12px; color: var(--accent);">Agent-Modelle</h4>
      <p style="font-size: 0.85em; color: var(--text-secondary); margin-bottom: 12px;">
        Jeder Agent-Typ kann ein eigenes LLM-Modell verwenden. Leeres Feld = Fallback-Modell.
      </p>
    </div>
  `;

  // Agent-Modell-Selects
  for (const agent of agentTypes) {
    const currentVal = cfg[agent.key] || '';
    html += `
      <div class="settings-field" style="margin-bottom: 12px;">
        <label for="ta-${agent.key}">
          <strong>${escapeHtml(agent.label)}</strong>
          <span style="color: var(--text-secondary); font-size: 0.85em;"> - ${escapeHtml(agent.desc)}</span>
        </label>
        <select id="ta-${agent.key}" data-section="task_agents" data-key="${agent.key}"
          onchange="markSettingsModified()" style="width: 100%; margin-top: 4px;">
          <option value="">Standard (${escapeHtml(agent.fallback || 'default_model')})</option>
          ${models.map(m => `<option value="${escapeHtml(m.id)}" ${currentVal === m.id ? 'selected' : ''}>${escapeHtml(m.display_name || m.id)}</option>`).join('')}
        </select>
      </div>
    `;
  }

  // Fallback Model
  html += `
    <div class="settings-field" style="margin-bottom: 16px; padding-top: 8px; border-top: 1px solid var(--border);">
      <label for="ta-fallback_model">
        <strong>Fallback-Modell</strong>
        <span style="color: var(--text-secondary); font-size: 0.85em;"> - Wenn Agent-Modell nicht verfuegbar</span>
      </label>
      <select id="ta-fallback_model" data-section="task_agents" data-key="fallback_model"
        onchange="markSettingsModified()" style="width: 100%; margin-top: 4px;">
        <option value="">Standard (${escapeHtml(llmCfg.default_model || '')})</option>
        ${models.map(m => `<option value="${escapeHtml(m.id)}" ${cfg.fallback_model === m.id ? 'selected' : ''}>${escapeHtml(m.display_name || m.id)}</option>`).join('')}
      </select>
    </div>
  `;

  // Execution Settings
  html += `
    <div class="settings-section" style="margin-top: 20px;">
      <h4 style="margin: 0 0 12px; color: var(--accent);">Ausfuehrung</h4>
    </div>

    <div class="settings-field">
      <label for="ta-max_parallel_tasks">Max. parallele Tasks</label>
      <input type="number" id="ta-max_parallel_tasks" min="1" max="10"
        value="${cfg.max_parallel_tasks || 3}"
        data-section="task_agents" data-key="max_parallel_tasks"
        onchange="markSettingsModified()" style="width: 100px;">
      <span style="color: var(--text-secondary); font-size: 0.85em; margin-left: 8px;">
        Unabhaengige Tasks werden parallel ausgefuehrt
      </span>
    </div>

    <div class="settings-field">
      <label for="ta-task_timeout_seconds">Task-Timeout (Sekunden)</label>
      <input type="number" id="ta-task_timeout_seconds" min="30" max="600"
        value="${cfg.task_timeout_seconds || 120}"
        data-section="task_agents" data-key="task_timeout_seconds"
        onchange="markSettingsModified()" style="width: 100px;">
    </div>

    <div class="settings-field">
      <label for="ta-max_retries_per_task">Max. Retries pro Task</label>
      <input type="number" id="ta-max_retries_per_task" min="0" max="5"
        value="${cfg.max_retries_per_task || 3}"
        data-section="task_agents" data-key="max_retries_per_task"
        onchange="markSettingsModified()" style="width: 100px;">
    </div>

    <!-- Phase Synthesis -->
    <div class="settings-section" style="margin-top: 20px;">
      <h4 style="margin: 0 0 12px; color: var(--accent);">Phasen-Synthese</h4>
    </div>

    <div class="settings-field" style="display: flex; align-items: center; gap: 12px;">
      <label class="toggle-switch">
        <input type="checkbox" id="ta-enable_phase_synthesis"
          data-section="task_agents" data-key="enable_phase_synthesis"
          ${cfg.enable_phase_synthesis !== false ? 'checked' : ''}
          onchange="markSettingsModified()">
        <span class="toggle-slider"></span>
      </label>
      <div>
        <strong>Zwischen-Synthese aktivieren</strong>
        <div style="font-size: 0.85em; color: var(--text-secondary);">
          Bei Phasenwechsel (z.B. Research -> Code) werden Ergebnisse zusammengefasst
        </div>
      </div>
    </div>

    <div class="settings-field">
      <label for="ta-synthesis_max_tokens">Max. Tokens fuer Synthese</label>
      <input type="number" id="ta-synthesis_max_tokens" min="100" max="2000"
        value="${cfg.synthesis_max_tokens || 500}"
        data-section="task_agents" data-key="synthesis_max_tokens"
        onchange="markSettingsModified()" style="width: 100px;">
    </div>

    <!-- Planning -->
    <div class="settings-section" style="margin-top: 20px;">
      <h4 style="margin: 0 0 12px; color: var(--accent);">Planung</h4>
    </div>

    <div class="settings-field">
      <label for="ta-min_tasks_for_decomposition">Min. Tasks fuer Zerlegung</label>
      <input type="number" id="ta-min_tasks_for_decomposition" min="1" max="5"
        value="${cfg.min_tasks_for_decomposition || 2}"
        data-section="task_agents" data-key="min_tasks_for_decomposition"
        onchange="markSettingsModified()" style="width: 100px;">
      <span style="color: var(--text-secondary); font-size: 0.85em; margin-left: 8px;">
        Unter diesem Threshold wird direkt verarbeitet
      </span>
    </div>

    <div class="settings-field">
      <label for="ta-planning_model">Planning-Modell</label>
      <select id="ta-planning_model" data-section="task_agents" data-key="planning_model"
        onchange="markSettingsModified()" style="width: 100%;">
        <option value="">Standard (${escapeHtml(llmCfg.analysis_model || llmCfg.default_model || '')})</option>
        ${models.map(m => `<option value="${escapeHtml(m.id)}" ${cfg.planning_model === m.id ? 'selected' : ''}>${escapeHtml(m.display_name || m.id)}</option>`).join('')}
      </select>
    </div>
  `;

  form.innerHTML = html;
}

function renderModelsSection() {
  const models = settingsState.settings.models || [];
  const defaultModel = settingsState.settings.llm?.default_model || '';

  let html = `
    <div class="settings-section">
      <h3 class="settings-section-title">MODELLE</h3>
      <p class="settings-section-desc">Verfügbare LLM-Modelle für die Auswahl im Header</p>
    </div>
    <div class="models-list">
  `;

  models.forEach((model, idx) => {
    const isDefault = model.id === defaultModel;
    html += `
      <div class="model-item" data-model-idx="${idx}">
        <input type="text" value="${escapeHtml(model.id)}" placeholder="model-id"
          data-field="id" onchange="markSettingsModified()">
        <input type="text" value="${escapeHtml(model.display_name)}" placeholder="Anzeigename"
          data-field="display_name" onchange="markSettingsModified()">
        ${isDefault ? '<span style="color: var(--success);">Standard</span>' : ''}
        <button class="model-delete" onclick="deleteModel(${idx})">✕</button>
      </div>
    `;
  });

  html += `
    </div>
    <div class="add-model-form">
      <input type="text" id="new-model-id" placeholder="Modell-ID (z.B. gpt-4)">
      <input type="text" id="new-model-name" placeholder="Anzeigename">
      <button onclick="addNewModel()">+ Hinzufügen</button>
    </div>
  `;

  document.getElementById('settings-form').innerHTML = html;
}

function addNewModel() {
  const idInput = document.getElementById('new-model-id');
  const nameInput = document.getElementById('new-model-name');

  if (!idInput.value.trim()) {
    updateSettingsStatus('Modell-ID erforderlich', 'error');
    return;
  }

  const newModel = {
    id: idInput.value.trim(),
    display_name: nameInput.value.trim() || idInput.value.trim()
  };

  settingsState.settings.models.push(newModel);
  idInput.value = '';
  nameInput.value = '';
  renderModelsSection();
  markSettingsModified();
}

function deleteModel(idx) {
  settingsState.settings.models.splice(idx, 1);
  renderModelsSection();
  markSettingsModified();
}

// ── Sub-Agenten Section ──

const ALL_SUB_AGENTS = [
  { id: 'code_explorer',    label: 'Code Explorer',      desc: 'Java/Python Quellcode, Klassen, Methoden' },
  { id: 'wiki_agent',       label: 'Wiki Agent',          desc: 'Confluence-Wiki, Architektur, Dokumentation' },
  { id: 'jira_agent',       label: 'Jira Agent',          desc: 'Tickets, Bugs, User Stories' },
  { id: 'database_agent',   label: 'Database Agent',      desc: 'DB2-Tabellen, SQL-Schema' },
  { id: 'knowledge_agent',  label: 'Knowledge Agent',     desc: 'Handbuch, PDFs, Skills' },
  { id: 'datasource_agent', label: 'Datenquellen-Agent',  desc: 'Jenkins, GitHub, interne REST-APIs (ds_*)' },
];

function renderSubAgentsSection() {
  const form = document.getElementById('settings-form');
  const cfg = settingsState.settings.sub_agents || {};
  const activeAgents = cfg.agents || [];
  const availModels = settingsState.settings.models || [];
  const defaultModel = settingsState.settings.llm?.tool_model || settingsState.settings.llm?.default_model || '';

  const modelOptions = availModels.map(m =>
    `<option value="${escapeHtml(m.id)}" ${(cfg.routing_model || '') === m.id ? 'selected' : ''}>${escapeHtml(m.display_name || m.id)}</option>`
  ).join('');

  const agentRows = ALL_SUB_AGENTS.map(a => {
    const checked = activeAgents.includes(a.id);
    return `
      <label class="subagent-setting-row ${checked ? 'active' : ''}">
        <input type="checkbox" class="sa-agent-cb" data-agent="${a.id}" ${checked ? 'checked' : ''}
               onchange="markSettingsModified()">
        <div class="sa-agent-info">
          <span class="sa-agent-name">${escapeHtml(a.label)}</span>
          <span class="sa-agent-desc">${escapeHtml(a.desc)}</span>
        </div>
      </label>`;
  }).join('');

  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">SUB-AGENTEN</h3>
      <p class="settings-section-desc">
        Spezialisierte Agenten durchsuchen Datenquellen <strong>parallel</strong>, bevor der Haupt-Agent antwortet.
        Das Routing-Modell entscheidet welche Agenten für eine Anfrage relevant sind.
      </p>
    </div>

    <div class="settings-field">
      <label>
        <input type="checkbox" id="sa-enabled" ${cfg.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        Sub-Agenten aktiviert
      </label>
    </div>

    <div class="settings-field">
      <label for="sa-routing-model">Routing-Modell</label>
      <select id="sa-routing-model" onchange="markSettingsModified()">
        <option value="">Standard (${escapeHtml(defaultModel)})</option>
        ${modelOptions}
      </select>
      <small style="color:var(--text-muted)">Wird verwendet um zu entscheiden welche Sub-Agenten aktiviert werden.</small>
    </div>

    <div class="settings-field">
      <label for="sa-timeout">Timeout pro Agent (Sekunden)</label>
      <input type="number" id="sa-timeout" value="${cfg.timeout_seconds ?? 30}" min="5" max="300" onchange="markSettingsModified()">
    </div>

    <div class="settings-field">
      <label for="sa-max-iter">Max. Tool-Calls pro Agent</label>
      <input type="number" id="sa-max-iter" value="${cfg.max_iterations ?? 5}" min="1" max="20" onchange="markSettingsModified()">
    </div>

    <div class="settings-field">
      <label for="sa-min-len">Minimale Anfrage-Länge (Zeichen)</label>
      <input type="number" id="sa-min-len" value="${cfg.min_query_length ?? 15}" min="1" max="200" onchange="markSettingsModified()">
      <small style="color:var(--text-muted)">Kürzere Anfragen überspringen die Sub-Agent-Phase.</small>
    </div>

    <div class="settings-section" style="margin-top:16px">
      <h4 style="margin-bottom:8px;color:var(--text-muted)">Aktive Agenten</h4>
      <div class="sa-agents-list">${agentRows}</div>
    </div>
  `;
}

function collectSubAgentsValues() {
  const enabled = document.getElementById('sa-enabled')?.checked ?? true;
  const routing_model = document.getElementById('sa-routing-model')?.value || '';
  const timeout_seconds = parseInt(document.getElementById('sa-timeout')?.value || '30');
  const max_iterations = parseInt(document.getElementById('sa-max-iter')?.value || '5');
  const min_query_length = parseInt(document.getElementById('sa-min-len')?.value || '15');
  const agents = [...document.querySelectorAll('.sa-agent-cb:checked')].map(cb => cb.dataset.agent);
  return { enabled, routing_model, timeout_seconds, max_iterations, min_query_length, agents };
}

// ── Repos Section (Java / Python) ──

async function renderReposSection(lang) {
  const label = lang === 'java' ? 'Java' : 'Python';
  const values = settingsState.settings[lang] || {};

  // Repos frisch vom Server laden
  let repos = [];
  let activeRepo = values.active_repo || '';
  try {
    const res = await fetch(`/api/settings/repos/${lang}`);
    if (res.ok) {
      const d = await res.json();
      repos = d.repos || [];
      activeRepo = d.active_repo || '';
    }
  } catch (e) { /* ignore */ }

  let html = `
    <div class="settings-section">
      <h3 class="settings-section-title">${label.toUpperCase()} REPOSITORIES</h3>
      <p class="settings-section-desc">${settingsState.descriptions[lang]}</p>
    </div>

    <div class="settings-section">
      <h4 style="margin-bottom:8px;color:var(--text-muted)">Konfigurierte Repositories</h4>
      <div class="repos-list" id="${lang}-repos-list">
  `;

  if (repos.length === 0) {
    html += `<div style="color:var(--text-muted);font-size:0.85rem;padding:8px 0;">Noch keine Repositories konfiguriert.</div>`;
  } else {
    repos.forEach(r => {
      const isActive = r.name === activeRepo;
      html += `
        <div class="repo-item ${isActive ? 'repo-item-active' : ''}" data-name="${escapeHtml(r.name)}">
          <div class="repo-item-info">
            <span class="repo-item-name">${escapeHtml(r.name)}</span>
            ${isActive ? '<span class="repo-active-badge">Aktiv</span>' : ''}
            <span class="repo-item-path">${escapeHtml(r.path)}</span>
          </div>
          <div class="repo-item-actions">
            ${!isActive ? `<button class="btn btn-secondary btn-sm" onclick="setActiveRepoSettings('${lang}', '${escapeHtml(r.name).replace(/'/g, "\\'")}')">Aktivieren</button>` : ''}
            <button class="btn btn-danger btn-sm" onclick="deleteRepoSettings('${lang}', '${escapeHtml(r.name).replace(/'/g, "\\'")}')">✕</button>
          </div>
        </div>
      `;
    });
  }

  html += `
      </div>
    </div>

    <div class="settings-section">
      <h4 style="margin-bottom:8px;color:var(--text-muted)">Repository hinzufügen</h4>
      <div class="add-repo-form">
        <input type="text" id="${lang}-new-repo-name" placeholder="Name (z.B. MeinProjekt)" style="margin-bottom:6px">
        <input type="text" id="${lang}-new-repo-path" placeholder="Pfad (z.B. /opt/projekte/repo oder //server/share/repo)" style="font-family:var(--font-mono);margin-bottom:6px">
        <button class="btn btn-primary" onclick="addRepoSettings('${lang}')">+ Hinzufügen</button>
        <span id="${lang}-repo-msg" class="test-result" style="margin-left:8px"></span>
      </div>
    </div>

    <div class="settings-section">
      <h4 style="margin-bottom:8px;color:var(--text-muted)">Sonstige Einstellungen</h4>
  `;

  // Generic fields für exclude_dirs, max_file_size_kb
  const otherValues = {};
  for (const [k, v] of Object.entries(values)) {
    if (!['repos', 'active_repo', 'repo_path'].includes(k)) {
      otherValues[k] = v;
    }
  }
  html += renderSettingsFields(lang, otherValues);
  html += `</div>`;

  document.getElementById('settings-form').innerHTML = html;
}

async function setActiveRepoSettings(lang, name) {
  try {
    const res = await fetch(`/api/settings/repos/${lang}/active?name=${encodeURIComponent(name)}`, { method: 'PUT' });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Fehler');
    updateSettingsStatus(`${name} aktiviert`, 'success');
    renderReposSection(lang);
  } catch (e) {
    updateSettingsStatus('Fehler: ' + e.message, 'error');
  }
}

async function addRepoSettings(lang) {
  const nameEl = document.getElementById(`${lang}-new-repo-name`);
  const pathEl = document.getElementById(`${lang}-new-repo-path`);
  const msgEl = document.getElementById(`${lang}-repo-msg`);

  const name = nameEl.value.trim();
  const path = pathEl.value.trim();
  if (!name || !path) {
    msgEl.textContent = 'Name und Pfad erforderlich';
    msgEl.className = 'test-result error';
    return;
  }

  msgEl.textContent = '⏳ Wird hinzugefügt...';
  msgEl.className = 'test-result testing';

  try {
    const res = await fetch(`/api/settings/repos/${lang}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, path })
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Fehler');

    nameEl.value = '';
    pathEl.value = '';
    msgEl.textContent = '✓ Hinzugefügt';
    msgEl.className = 'test-result success';
    // Jetzt in config.yaml speichern
    await fetch('/api/settings/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backup: true }) });
    updateSettingsStatus('Repository hinzugefügt und gespeichert', 'success');
    renderReposSection(lang);
  } catch (e) {
    msgEl.textContent = '✗ ' + e.message;
    msgEl.className = 'test-result error';
  }
}

async function deleteRepoSettings(lang, name) {
  if (!confirm(`Repository "${name}" wirklich entfernen?`)) return;
  try {
    const encodedName = encodeURIComponent(name);
    const res = await fetch(`/api/settings/repos/${lang}/${encodedName}`, { method: 'DELETE' });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Fehler');
    await fetch('/api/settings/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backup: true }) });
    updateSettingsStatus('Repository entfernt und gespeichert', 'success');
    renderReposSection(lang);
  } catch (e) {
    updateSettingsStatus('Fehler: ' + e.message, 'error');
  }
}

function markSettingsModified() {
  settingsState.modified = true;
  updateSettingsStatus('Ungespeicherte Änderungen', '');
}

function formatFieldLabel(key) {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

function getPlaceholder(key) {
  const placeholders = {
    'base_url': 'http://localhost:8080/v1',
    'repo_path': '/pfad/zum/repository',
    'path': '//server/share/ordner',
    'directory': './verzeichnis'
  };

  for (const [k, v] of Object.entries(placeholders)) {
    if (key.includes(k)) return v;
  }
  return '';
}

function collectSectionValues(section) {
  if (section === 'models') {
    const models = [];
    document.querySelectorAll('.model-item').forEach(item => {
      const id = item.querySelector('[data-field="id"]').value;
      const displayName = item.querySelector('[data-field="display_name"]').value;
      if (id) {
        models.push({ id, display_name: displayName || id });
      }
    });
    return models;
  }

  const values = {};
  const fields = document.querySelectorAll(`[data-section="${section}"]`);

  // Zuerst Dict-Felder sammeln (haben data-dict-key oder data-dict-value)
  const dictFields = {};
  fields.forEach(field => {
    const key = field.dataset.key;
    const dictKeyIdx = field.dataset.dictKey;
    const dictValIdx = field.dataset.dictValue;

    if (dictKeyIdx !== undefined) {
      // Dict key input
      if (!dictFields[key]) dictFields[key] = {};
      if (!dictFields[key][dictKeyIdx]) dictFields[key][dictKeyIdx] = {};
      dictFields[key][dictKeyIdx].key = field.value.trim();
    } else if (dictValIdx !== undefined) {
      // Dict value input
      if (!dictFields[key]) dictFields[key] = {};
      if (!dictFields[key][dictValIdx]) dictFields[key][dictValIdx] = {};
      const rawVal = field.value.trim();
      // Parse number if field type is number
      dictFields[key][dictValIdx].value = field.type === 'number' ? (parseFloat(rawVal) || 0) : rawVal;
    }
  });

  // Dict-Felder in values umwandeln
  for (const [key, entries] of Object.entries(dictFields)) {
    values[key] = {};
    for (const entry of Object.values(entries)) {
      if (entry.key) {
        values[key][entry.key] = entry.value;
      }
    }
  }

  // Normale Felder verarbeiten (ohne Dict-Felder)
  fields.forEach(field => {
    const key = field.dataset.key;
    const idx = field.dataset.index;

    // Skip Dict fields (already processed)
    if (field.dataset.dictKey !== undefined || field.dataset.dictValue !== undefined) {
      return;
    }

    if (idx !== undefined) {
      // Array field
      if (!values[key]) values[key] = [];
      if (field.value.trim()) {
        values[key][parseInt(idx)] = field.value.trim();
      }
    } else if (field.type === 'checkbox') {
      values[key] = field.checked;
    } else if (field.type === 'number') {
      values[key] = parseFloat(field.value) || 0;
    } else {
      // Don't send masked passwords unless changed
      if (field.value !== '********') {
        values[key] = field.value;
      }
    }
  });

  // Clean up arrays (remove empty slots)
  for (const key of Object.keys(values)) {
    if (Array.isArray(values[key])) {
      values[key] = values[key].filter(v => v !== undefined && v !== '');
    }
  }

  return values;
}

async function saveCurrentSection() {
  const section = settingsState.currentSection;

  // Web-Suche hat eigene Felder (nur timeout - Proxy ist global)
  if (section === 'search') {
    const config = {
      timeout_seconds: parseInt(document.getElementById('search-timeout')?.value) || 30,
    };
    try {
      const res = await fetch('/api/search/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      updateSettingsStatus('Web-Suche-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // WLP hat eigene Felder (enabled, java_home)
  if (section === 'wlp') {
    const config = {
      enabled: document.getElementById('wlp-enabled')?.checked || false,
      java_home: document.getElementById('wlp-java-home')?.value || '',
    };
    try {
      const res = await fetch('/api/settings/section/wlp', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      updateSettingsStatus('WLP-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // Maven hat eigene Felder
  if (section === 'maven') {
    const config = {
      java_home: document.getElementById('maven-java-home')?.value || '',
      mvn_executable: document.getElementById('maven-mvn-exec')?.value || 'mvn',
      settings_file: document.getElementById('maven-settings-file')?.value || '',
      local_repo: document.getElementById('maven-local-repo')?.value || '',
    };
    try {
      const res = await fetch('/api/settings/section/maven', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      updateSettingsStatus('Maven-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // Datenquellen haben eigene Speicher-Buttons (kein generischer Save)
  if (section === 'data_sources') {
    updateSettingsStatus('Datenquellen werden direkt über die Formular-Buttons gespeichert', 'success');
    return;
  }

  // Sub-Agenten haben eigene Felder
  if (section === 'sub_agents') {
    const values = collectSubAgentsValues();
    try {
      const res = await fetch('/api/settings/section/sub_agents', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      settingsState.settings.sub_agents = data.values;
      updateSettingsStatus('Sub-Agenten-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // Jenkins hat eigene Felder (job_paths Array)
  if (section === 'jenkins') {
    const values = collectJenkinsSettings();
    try {
      const res = await fetch('/api/settings/section/jenkins', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      settingsState.settings.jenkins = data.values;
      updateSettingsStatus('Jenkins-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // GitHub hat eigene Felder
  if (section === 'github') {
    const values = collectGitHubSettings();
    try {
      const res = await fetch('/api/settings/section/github', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      settingsState.settings.github = data.values;
      updateSettingsStatus('GitHub-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // Internal Fetch hat eigene Felder
  if (section === 'internal_fetch') {
    const values = collectInternalFetchSettings();
    try {
      const res = await fetch('/api/settings/section/internal_fetch', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      settingsState.settings.internal_fetch = data.values;
      updateSettingsStatus('Internal-Fetch-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // Docker Sandbox hat eigene Felder
  if (section === 'docker_sandbox') {
    const values = collectDockerSandboxSettings();
    try {
      const res = await fetch('/api/docker-sandbox/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      settingsState.settings.docker_sandbox = data.config;
      updateSettingsStatus('Container-Sandbox-Einstellungen angewendet', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  const values = collectSectionValues(section);

  try {
    const res = await fetch(`/api/settings/section/${section}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(values)
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || 'Fehler');
    }

    settingsState.settings[section] = data.values;
    updateSettingsStatus('Angewendet (nur im Speicher)', 'success');

    // Modell-Dropdown aktualisieren wenn LLM oder Models geändert
    if (section === 'llm' || section === 'models') {
      await loadModels();
    }

  } catch (err) {
    updateSettingsStatus('Fehler: ' + err.message, 'error');
  }
}

async function saveSettingsToFile() {
  // Erst aktuelle Section speichern
  await saveCurrentSection();

  try {
    const res = await fetch('/api/settings/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ backup: true })
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || 'Fehler');
    }

    settingsState.modified = false;
    updateSettingsStatus('Gespeichert in config.yaml', 'success');

  } catch (err) {
    updateSettingsStatus('Speichern fehlgeschlagen: ' + err.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Datenquellen-Verwaltung
// ══════════════════════════════════════════════════════════════════════════════

let _dsState = {
  sources: [],
  editingId: null,   // null = neue Quelle, string = ID der bearbeiteten
  suggestion: null,  // KI-Vorschlag (zwischengespeichert)
};

async function renderDataSourcesSection() {
  const form = document.getElementById('settings-form');
  form.innerHTML = `<div class="settings-section">
    <h3 class="settings-section-title">DATENQUELLEN</h3>
    <p class="settings-section-desc">
      Interne HTTP-Systeme einbinden (Jenkins, GitHub, Log-APIs, Testsysteme, …).
      Der Agent bekommt für jede Quelle ein eigenes Tool, das per KI automatisch
      beschrieben werden kann.
    </p>
    <button class="btn btn-primary" onclick="dsShowForm(null)" style="margin-bottom:16px">
      + Neue Datenquelle
    </button>
    <div id="ds-list"></div>
    <div id="ds-form-container"></div>
  </div>`;
  await dsLoadList();
}

async function dsLoadList() {
  try {
    const res = await fetch('/api/datasources');
    const data = await res.json();
    _dsState.sources = data.sources || [];
    dsRenderList();
  } catch (e) {
    document.getElementById('ds-list').innerHTML =
      `<p style="color:var(--error)">Fehler beim Laden: ${e.message}</p>`;
  }
}

function dsRenderList() {
  const el = document.getElementById('ds-list');
  if (!_dsState.sources.length) {
    el.innerHTML = `<p style="color:var(--text-muted);font-style:italic">
      Noch keine Datenquellen konfiguriert.</p>`;
    return;
  }
  el.innerHTML = _dsState.sources.map(s => `
    <div class="ds-item" id="ds-item-${s.id}">
      <div class="ds-item-header">
        <div class="ds-item-info">
          <span class="ds-item-name">${escapeHtml(s.name)}</span>
          <span class="ds-item-url">${escapeHtml(s.base_url)}</span>
          ${s.explored ? '<span class="ds-badge explored">KI ✓</span>' : '<span class="ds-badge">Neu</span>'}
          ${s.auth?.type !== 'none' ? `<span class="ds-badge auth">${escapeHtml(s.auth.type)}</span>` : ''}
          ${!s.verify_ssl ? '<span class="ds-badge nossl">SSL off</span>' : ''}
        </div>
        <div class="ds-item-actions">
          <button class="btn btn-xs" onclick="dsTest('${s.id}')" title="Verbindung testen">🔌 Test</button>
          <button class="btn btn-xs btn-secondary" onclick="dsExplore('${s.id}')" title="KI erkundet die API">🤖 Erkunden</button>
          <button class="btn btn-xs btn-secondary" onclick="dsShowForm('${s.id}')">✏ Bearbeiten</button>
          <button class="btn btn-xs btn-danger" onclick="dsDelete('${s.id}', '${escapeHtml(s.name)}')">✕</button>
        </div>
      </div>
      ${s.description ? `<div class="ds-item-desc">${escapeHtml(s.description)}</div>` : ''}
      ${s.tool_description ? `<div class="ds-item-tool-desc">${escapeHtml(s.tool_description)}</div>` : ''}
      <div id="ds-test-result-${s.id}" class="ds-test-result"></div>
      <div id="ds-explore-result-${s.id}"></div>
    </div>
  `).join('');
}

function dsShowForm(id) {
  _dsState.editingId = id;
  _dsState.suggestion = null;
  const source = id ? _dsState.sources.find(s => s.id === id) : null;
  const s = source || {};
  const auth = s.auth || {};

  document.getElementById('ds-form-container').innerHTML = `
    <div class="ds-form" id="ds-edit-form">
      <h4 style="margin-bottom:12px">${id ? 'Datenquelle bearbeiten' : 'Neue Datenquelle'}</h4>

      <div class="settings-field">
        <label>Name *</label>
        <input type="text" id="ds-name" placeholder="z.B. Jenkins-CI, GitHub-Internal, Log-Server"
          value="${escapeHtml(s.name || '')}">
      </div>
      <div class="settings-field">
        <label>Beschreibung</label>
        <input type="text" id="ds-desc" placeholder="Was ist diese Datenquelle? Wofür wird sie verwendet?"
          value="${escapeHtml(s.description || '')}">
      </div>
      <div class="settings-field">
        <label>Basis-URL *</label>
        <input type="text" id="ds-url" placeholder="http://jenkins.intern:8080"
          value="${escapeHtml(s.base_url || '')}">
      </div>

      <div class="settings-field">
        <label class="checkbox-label">
          <input type="checkbox" id="ds-ssl" ${s.verify_ssl === false ? '' : 'checked'}>
          SSL-Verifizierung aktiviert (deaktivieren für selbstsignierte Zertifikate)
        </label>
      </div>

      <div class="settings-field">
        <label>Authentifizierung</label>
        <select id="ds-auth-type" onchange="dsUpdateAuthFields()">
          <option value="none" ${auth.type === 'none' || !auth.type ? 'selected' : ''}>Keine</option>
          <option value="basic" ${auth.type === 'basic' ? 'selected' : ''}>Basic Auth (Benutzer + Passwort)</option>
          <option value="bearer" ${auth.type === 'bearer' ? 'selected' : ''}>Bearer Token</option>
          <option value="api_key" ${auth.type === 'api_key' ? 'selected' : ''}>API-Key Header</option>
        </select>
      </div>
      <div id="ds-auth-fields"></div>

      <details style="margin-bottom:12px">
        <summary style="cursor:pointer;color:var(--text-muted);font-size:13px">
          Tool-Definition (manuell oder per KI-Erkundung befüllen)
        </summary>
        <div style="margin-top:10px">
          <div class="settings-field">
            <label>Tool-Beschreibung (für KI-Agenten)</label>
            <textarea id="ds-tool-desc" rows="3" placeholder="Was liefert dieses Tool? Welche Daten kann der Agent damit abrufen?"
              style="width:100%;resize:vertical">${escapeHtml(s.tool_description || '')}</textarea>
          </div>
          <div class="settings-field">
            <label>Verwendungszweck</label>
            <textarea id="ds-tool-usage" rows="2" placeholder="Wann soll der Agent dieses Tool verwenden? Beispiel-Anfragen?"
              style="width:100%;resize:vertical">${escapeHtml(s.tool_usage || '')}</textarea>
          </div>
          <div class="settings-field">
            <label>Standard-Endpunkt</label>
            <input type="text" id="ds-endpoint" placeholder="/api/json oder /api/v1/builds"
              value="${escapeHtml(s.endpoint_path || '')}">
          </div>
          <div class="settings-field">
            <label>HTTP-Methode</label>
            <select id="ds-method">
              <option value="GET" ${(s.method || 'GET') === 'GET' ? 'selected' : ''}>GET</option>
              <option value="POST" ${s.method === 'POST' ? 'selected' : ''}>POST</option>
              <option value="PUT" ${s.method === 'PUT' ? 'selected' : ''}>PUT</option>
            </select>
          </div>

          <div class="settings-field">
            <label>Tool-Parameter</label>
            <div id="ds-params-list">
              ${(s.parameters || []).map((p, i) => dsRenderParamRow(i, p)).join('')}
            </div>
            <button class="btn btn-xs btn-secondary" onclick="dsAddParam()" style="margin-top:6px">
              + Parameter hinzufügen
            </button>
          </div>
        </div>
      </details>

      <div id="ds-suggestion-box"></div>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
        <button class="btn btn-primary" onclick="dsSave()">
          ${id ? 'Speichern' : 'Anlegen'}
        </button>
        <button class="btn btn-secondary" onclick="dsCancelForm()">Abbrechen</button>
        <button class="btn btn-secondary" onclick="dsTestForm()" title="Verbindung mit den aktuellen Eingaben testen">
          🔌 Verbindung testen
        </button>
      </div>
      <div id="ds-form-test-result" class="ds-test-result" style="margin-top:8px"></div>
    </div>
  `;

  dsUpdateAuthFields();
  // Befüllung der Auth-Felder mit gespeicherten Werten
  const t = auth.type || 'none';
  if (t === 'basic') {
    document.getElementById('ds-auth-username').value = auth.username || '';
    document.getElementById('ds-auth-password').value = auth.password ? '***' : '';
  } else if (t === 'bearer') {
    document.getElementById('ds-auth-bearer').value = auth.bearer_token ? '***' : '';
  } else if (t === 'api_key') {
    document.getElementById('ds-auth-key-header').value = auth.api_key_header || 'X-API-Key';
    document.getElementById('ds-auth-key-value').value = auth.api_key_value ? '***' : '';
  }

  document.getElementById('ds-edit-form').scrollIntoView({ behavior: 'smooth' });
}

function dsRenderParamRow(index, p = {}) {
  return `<div class="ds-param-row" id="ds-param-${index}">
    <input type="text" placeholder="Name" value="${escapeHtml(p.name || '')}" data-param="${index}" data-field="name" style="width:120px">
    <select data-param="${index}" data-field="type">
      <option value="string" ${p.type === 'string' || !p.type ? 'selected' : ''}>string</option>
      <option value="number" ${p.type === 'number' ? 'selected' : ''}>number</option>
      <option value="boolean" ${p.type === 'boolean' ? 'selected' : ''}>boolean</option>
    </select>
    <input type="text" placeholder="Beschreibung" value="${escapeHtml(p.description || '')}" data-param="${index}" data-field="description" style="flex:1">
    <select data-param="${index}" data-field="location" title="Wo wird der Parameter übergeben?">
      <option value="query" ${p.location === 'query' || !p.location ? 'selected' : ''}>Query</option>
      <option value="body" ${p.location === 'body' ? 'selected' : ''}>Body</option>
      <option value="path" ${p.location === 'path' ? 'selected' : ''}>Path</option>
      <option value="header" ${p.location === 'header' ? 'selected' : ''}>Header</option>
    </select>
    <label class="checkbox-label" style="white-space:nowrap;font-size:12px">
      <input type="checkbox" data-param="${index}" data-field="required" ${p.required ? 'checked' : ''}> Pflicht
    </label>
    <button class="btn btn-xs btn-danger" onclick="dsRemoveParam(${index})">✕</button>
  </div>`;
}

let _dsParamCount = 0;
function dsAddParam() {
  const list = document.getElementById('ds-params-list');
  const idx = Date.now();
  list.insertAdjacentHTML('beforeend', dsRenderParamRow(idx));
  _dsParamCount++;
}

function dsRemoveParam(index) {
  const row = document.getElementById(`ds-param-${index}`);
  if (row) row.remove();
}

function dsCollectParams() {
  const rows = document.querySelectorAll('.ds-param-row');
  return Array.from(rows).map(row => {
    const idx = row.id.replace('ds-param-', '');
    const get = (field) => {
      const el = row.querySelector(`[data-param="${idx}"][data-field="${field}"]`);
      if (!el) return '';
      if (el.type === 'checkbox') return el.checked;
      return el.value;
    };
    return {
      name: get('name'),
      type: get('type') || 'string',
      description: get('description'),
      required: get('required'),
      location: get('location') || 'query',
    };
  }).filter(p => p.name);
}

function dsUpdateAuthFields() {
  const type = document.getElementById('ds-auth-type')?.value;
  const container = document.getElementById('ds-auth-fields');
  if (!container) return;
  if (type === 'basic') {
    container.innerHTML = `
      <div class="settings-field" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div>
          <label style="font-size:12px">Benutzername</label>
          <input type="text" id="ds-auth-username" placeholder="user">
        </div>
        <div>
          <label style="font-size:12px">Passwort</label>
          <input type="password" id="ds-auth-password" placeholder="••••••">
        </div>
      </div>`;
  } else if (type === 'bearer') {
    container.innerHTML = `
      <div class="settings-field">
        <label style="font-size:12px">Bearer Token</label>
        <input type="password" id="ds-auth-bearer" placeholder="eyJhbGci...">
      </div>`;
  } else if (type === 'api_key') {
    container.innerHTML = `
      <div class="settings-field" style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div>
          <label style="font-size:12px">Header-Name</label>
          <input type="text" id="ds-auth-key-header" value="X-API-Key">
        </div>
        <div>
          <label style="font-size:12px">API-Key</label>
          <input type="password" id="ds-auth-key-value" placeholder="••••••">
        </div>
      </div>`;
  } else {
    container.innerHTML = '';
  }
}

function dsCollectAuth() {
  const type = document.getElementById('ds-auth-type')?.value || 'none';
  const auth = { type };
  if (type === 'basic') {
    auth.username = document.getElementById('ds-auth-username')?.value || '';
    auth.password = document.getElementById('ds-auth-password')?.value || '';
  } else if (type === 'bearer') {
    auth.bearer_token = document.getElementById('ds-auth-bearer')?.value || '';
  } else if (type === 'api_key') {
    auth.api_key_header = document.getElementById('ds-auth-key-header')?.value || 'X-API-Key';
    auth.api_key_value = document.getElementById('ds-auth-key-value')?.value || '';
  }
  return auth;
}

function dsCollectFormData() {
  return {
    name: document.getElementById('ds-name')?.value?.trim() || '',
    description: document.getElementById('ds-desc')?.value?.trim() || '',
    base_url: document.getElementById('ds-url')?.value?.trim() || '',
    verify_ssl: document.getElementById('ds-ssl')?.checked ?? true,
    auth: dsCollectAuth(),
    tool_description: document.getElementById('ds-tool-desc')?.value?.trim() || '',
    tool_usage: document.getElementById('ds-tool-usage')?.value?.trim() || '',
    endpoint_path: document.getElementById('ds-endpoint')?.value?.trim() || '',
    method: document.getElementById('ds-method')?.value || 'GET',
    parameters: dsCollectParams(),
  };
}

async function dsSave() {
  const data = dsCollectFormData();
  if (!data.name) { updateSettingsStatus('Name ist erforderlich', 'error'); return; }
  if (!data.base_url) { updateSettingsStatus('Basis-URL ist erforderlich', 'error'); return; }

  try {
    let res;
    if (_dsState.editingId) {
      res = await fetch(`/api/datasources/${_dsState.editingId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
    } else {
      res = await fetch('/api/datasources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
    }
    const result = await res.json();
    if (!res.ok) throw new Error(result.detail || 'Fehler');

    updateSettingsStatus(result.message || 'Gespeichert', 'success');
    dsCancelForm();
    await dsLoadList();
  } catch (e) {
    updateSettingsStatus('Fehler: ' + e.message, 'error');
  }
}

function dsCancelForm() {
  document.getElementById('ds-form-container').innerHTML = '';
  _dsState.editingId = null;
  _dsState.suggestion = null;
}

async function dsDelete(id, name) {
  if (!confirm(`Datenquelle "${name}" wirklich löschen?`)) return;
  try {
    const res = await fetch(`/api/datasources/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Fehler');
    updateSettingsStatus(data.message, 'success');
    await dsLoadList();
  } catch (e) {
    updateSettingsStatus('Fehler: ' + e.message, 'error');
  }
}

async function dsTest(id) {
  const el = document.getElementById(`ds-test-result-${id}`);
  el.textContent = '⏳ Teste Verbindung...';
  el.className = 'ds-test-result testing';
  try {
    const res = await fetch(`/api/datasources/${id}/test`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      el.textContent = `✓ ${data.message} — ${data.preview?.substring(0, 120) || ''}`;
      el.className = 'ds-test-result success';
    } else {
      el.textContent = `✗ ${data.error}`;
      el.className = 'ds-test-result error';
    }
  } catch (e) {
    el.textContent = `✗ ${e.message}`;
    el.className = 'ds-test-result error';
  }
}

async function dsTestForm() {
  const el = document.getElementById('ds-form-test-result');
  // Temp-Quelle aus Formular speichern (oder bestehende ID nutzen)
  if (_dsState.editingId) {
    el.textContent = '⏳ Speichere und teste...';
    el.className = 'ds-test-result testing';
    await dsSave();
    if (_dsState.editingId) await dsTest(_dsState.editingId);
  } else {
    el.textContent = '⚠ Bitte erst "Anlegen" um die Verbindung zu testen.';
    el.className = 'ds-test-result';
  }
}

async function dsExplore(id) {
  const resultEl = document.getElementById(`ds-explore-result-${id}`);
  resultEl.innerHTML = `<div class="ds-explore-loading">🤖 KI erkundet die API… <span class="spinner-small"></span></div>`;

  try {
    const res = await fetch(`/api/datasources/${id}/explore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json();

    if (!data.success) {
      resultEl.innerHTML = `<div class="ds-explore-error">✗ ${escapeHtml(data.error)}</div>`;
      return;
    }

    const s = data.suggestion;
    resultEl.innerHTML = `
      <div class="ds-suggestion">
        <div class="ds-suggestion-header">
          <strong>🤖 KI-Vorschlag</strong>
          <span style="color:var(--text-muted);font-size:12px">Bitte prüfen und übernehmen</span>
        </div>
        <div class="ds-suggestion-field"><label>Tool-Beschreibung:</label>
          <span>${escapeHtml(s.tool_description || '')}</span></div>
        <div class="ds-suggestion-field"><label>Verwendungszweck:</label>
          <span>${escapeHtml(s.tool_usage || '')}</span></div>
        <div class="ds-suggestion-field"><label>Endpunkt:</label>
          <span>${escapeHtml(s.endpoint_path || '/')}</span>
          <span class="ds-badge">${escapeHtml(s.method || 'GET')}</span></div>
        ${s.suggested_endpoints?.length ? `
          <div class="ds-suggestion-field"><label>Weitere Endpunkte:</label>
            <ul style="margin:4px 0 0 16px;font-size:12px">
              ${s.suggested_endpoints.map(e =>
                `<li><code>${escapeHtml(e.path)}</code> – ${escapeHtml(e.description)}</li>`
              ).join('')}
            </ul>
          </div>` : ''}
        ${s.parameters?.length ? `
          <div class="ds-suggestion-field"><label>Parameter (${s.parameters.length}):</label>
            <div style="font-size:12px;margin-top:4px">
              ${s.parameters.map(p =>
                `<code>${escapeHtml(p.name)}</code> (${escapeHtml(p.type)}, ${escapeHtml(p.location || 'query')}${p.required ? ', Pflicht' : ''}) – ${escapeHtml(p.description)}`
              ).join('<br>')}
            </div>
          </div>` : ''}
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="btn btn-primary btn-xs" onclick="dsApplySuggestion('${id}', this)">
            ✓ Vorschlag übernehmen
          </button>
          <button class="btn btn-secondary btn-xs" onclick="this.closest('.ds-suggestion').remove()">
            Verwerfen
          </button>
        </div>
      </div>
    `;
    // Vorschlag für Apply speichern
    resultEl.dataset.suggestion = JSON.stringify(s);
  } catch (e) {
    resultEl.innerHTML = `<div class="ds-explore-error">✗ ${e.message}</div>`;
  }
}

async function dsApplySuggestion(id, btn) {
  const resultEl = btn.closest('[id^="ds-explore-result-"]');
  const suggestion = JSON.parse(resultEl?.dataset.suggestion || 'null');
  if (!suggestion) return;

  try {
    const res = await fetch(`/api/datasources/${id}/apply-suggestion`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ suggestion }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Fehler');

    updateSettingsStatus('KI-Vorschlag übernommen ✓', 'success');
    resultEl.innerHTML = '';
    await dsLoadList();
  } catch (e) {
    updateSettingsStatus('Fehler: ' + e.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// MQ Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderMQSection() {
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">MQ SERIES</h3>
      <p class="settings-section-desc">MQ-Queues per HTTP abrufen und Nachrichten einspielen. Jede Queue kann Service-Zuordnung, feste Header und ein Body-Template haben.</p>
    </div>
    <div id="mq-queues-list"><div class="spinner-inline"></div></div>
    <div class="settings-add-form" id="mq-add-form">
      <h4>Queue hinzufügen</h4>
      <div class="settings-field"><label>Name</label><input id="mq-new-name" type="text" class="settings-input" placeholder="z.B. Order-Queue"></div>
      <div class="settings-field"><label>URL</label><input id="mq-new-url" type="text" class="settings-input" placeholder="http://mq-server/api/queues/ORDER_QUEUE"></div>
      <div class="settings-field"><label>Methode</label>
        <select id="mq-new-method" class="settings-input">
          <option value="GET">GET (Lesen)</option>
          <option value="POST">POST (Einspielen)</option>
          <option value="PUT">PUT (Einspielen)</option>
        </select>
      </div>
      <div class="settings-field"><label>Rolle</label>
        <select id="mq-new-role" class="settings-input">
          <option value="read">read – Lesen</option>
          <option value="trigger">trigger – Auslösen</option>
          <option value="both">both – Lesen + Auslösen</option>
        </select>
      </div>
      <div class="settings-field"><label>Service (zugehöriger Service)</label><input id="mq-new-service" type="text" class="settings-input" placeholder="z.B. OrderService"></div>
      <div class="settings-field"><label>Beschreibung</label><input id="mq-new-desc" type="text" class="settings-input" placeholder="Was diese Queue triggert oder liest"></div>
      <div class="settings-field"><label>Body-Template ({{key}} als Platzhalter)</label><textarea id="mq-new-body" class="settings-input" rows="3" placeholder='{"orderId": "{{orderId}}"}'></textarea></div>
      <div class="settings-field"><label>Header (JSON)</label><textarea id="mq-new-headers" class="settings-input" rows="2" placeholder='{"Authorization": "Bearer token"}'></textarea></div>
      <button class="btn btn-primary" onclick="mqAddQueue()">+ Hinzufügen</button>
    </div>
  `;
  await mqLoadQueues();
}

async function mqLoadQueues() {
  const res = await fetch('/api/mq/queues');
  const data = await res.json();
  const list = document.getElementById('mq-queues-list');
  if (!list) return;
  if (!data.queues || !data.queues.length) {
    list.innerHTML = '<p class="empty-hint">Noch keine Queues konfiguriert.</p>';
    return;
  }
  list.innerHTML = data.queues.map(q => `
    <div class="ds-item">
      <div class="ds-item-header">
        <div>
          <span class="ds-item-name">${escapeHtml(q.name)}</span>
          <span class="ds-item-badge">${q.role}</span>
          <span class="ds-item-badge badge-info">${q.method}</span>
        </div>
        <div class="ds-item-actions">
          <button class="btn btn-xs btn-secondary" onclick="mqTestQueue('${q.id}')">Test</button>
          <button class="btn btn-xs btn-danger" onclick="mqDeleteQueue('${q.id}')">&#128465;</button>
        </div>
      </div>
      <div class="ds-item-detail">
        <span class="ds-detail-label">Service:</span> ${escapeHtml(q.service || '-')}
        &nbsp;|&nbsp; <span class="ds-detail-label">URL:</span> <code>${escapeHtml(q.url)}</code>
      </div>
      ${q.description ? `<div class="ds-item-desc">${escapeHtml(q.description)}</div>` : ''}
      ${q.body_template ? `<div class="ds-item-desc"><small>Template: <code>${escapeHtml(q.body_template.substring(0,80))}</code></small></div>` : ''}
    </div>
  `).join('');
}

async function mqAddQueue() {
  let headers = {};
  try { headers = JSON.parse(document.getElementById('mq-new-headers').value || '{}'); } catch (e) {}
  const body = {
    name: document.getElementById('mq-new-name').value,
    url: document.getElementById('mq-new-url').value,
    method: document.getElementById('mq-new-method').value,
    role: document.getElementById('mq-new-role').value,
    service: document.getElementById('mq-new-service').value,
    description: document.getElementById('mq-new-desc').value,
    body_template: document.getElementById('mq-new-body').value,
    headers,
  };
  const res = await fetch('/api/mq/queues', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (!res.ok) { updateSettingsStatus('Fehler: ' + (await res.json()).detail, 'error'); return; }
  updateSettingsStatus('Queue hinzugefügt ✓', 'success');
  ['mq-new-name','mq-new-url','mq-new-service','mq-new-desc','mq-new-body','mq-new-headers'].forEach(id => document.getElementById(id).value = '');
  await mqLoadQueues();
}

async function mqDeleteQueue(id) {
  if (!confirm('Queue löschen?')) return;
  await fetch(`/api/mq/queues/${id}`, { method: 'DELETE' });
  await mqLoadQueues();
}

async function mqTestQueue(id) {
  updateSettingsStatus('⏳ Teste Queue...', '');
  const res = await fetch(`/api/mq/queues/${id}/test`, { method: 'POST' });
  const data = await res.json();
  if (data.success) updateSettingsStatus(`✓ HTTP ${data.status_code} – ${data.body_preview?.substring(0,80)}`, 'success');
  else updateSettingsStatus(`✗ ${data.error || 'Fehler'}`, 'error');
}

// ══════════════════════════════════════════════════════════════════════════════
// Test-Tool Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderTestToolSection() {
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">TEST-TOOL</h3>
      <p class="settings-section-desc">SOAP-basierte Service-Aufrufe mit XML-Templates und automatischem Session-Management. Jedes Institut hat eigene Credentials.</p>
    </div>

    <div class="settings-subsection">
      <h4>Endpoints & Login</h4>
      <div id="soap-config-status" class="config-status-box" style="margin-bottom:12px;padding:8px;border-radius:4px;font-size:12px;background:var(--bg-tertiary)">
        <span class="spinner-inline"></span> Prüfe Konfiguration...
      </div>
      <div class="settings-field">
        <label>Service-URL:</label>
        <input id="soap-service-url" type="text" class="settings-input" placeholder="https://example.com/soap/services">
      </div>
      <div class="settings-field">
        <label>Login-URL:</label>
        <input id="soap-login-url" type="text" class="settings-input" placeholder="https://example.com/soap/auth">
        <small style="color:var(--text-muted);display:block;margin-top:4px">Wird für automatisches Session-Management verwendet</small>
      </div>
      <div class="settings-field">
        <label>Login-Template:</label>
        <select id="soap-login-template" class="settings-input">
          <option value="">Lade Templates...</option>
        </select>
        <small style="color:var(--text-muted);display:block;margin-top:4px">XML-Template für SOAP-Login (global für alle Institute)</small>
      </div>
      <div class="settings-field">
        <label>Session-Token XPath:</label>
        <input id="soap-session-xpath" type="text" class="settings-input" placeholder="//SessionToken/text()">
        <small style="color:var(--text-muted);display:block;margin-top:4px">XPath zum Extrahieren des Session-Tokens aus der Login-Response</small>
      </div>
      <div class="settings-field">
        <label style="display:flex;align-items:center;gap:8px">
          <input id="soap-verify-ssl" type="checkbox" checked>
          SSL-Zertifikate verifizieren
        </label>
      </div>
      <button class="btn btn-primary" onclick="soapSaveConfig()">Konfiguration speichern</button>
    </div>

    <div class="settings-subsection" style="margin-top:16px">
      <h4>Institute</h4>
      <p class="settings-hint">Jedes Institut hat eigene Zugangsdaten. Passwörter können Umgebungsvariablen referenzieren: <code>{{env:VAR_NAME}}</code></p>
      <div id="soap-institute-list"><div class="spinner-inline"></div></div>
      <div class="settings-add-form">
        <h5 style="margin:0 0 8px">Institut hinzufügen</h5>
        <div class="settings-field-row">
          <input id="soap-inst-nr" type="text" class="settings-input" placeholder="Institut-Nr (z.B. 001)" style="max-width:120px">
          <input id="soap-inst-name" type="text" class="settings-input" placeholder="Name (z.B. Hauptfiliale)">
        </div>
        <div class="settings-field-row" style="margin-top:4px">
          <input id="soap-inst-user" type="text" class="settings-input" placeholder="Benutzername">
          <input id="soap-inst-pass" type="password" class="settings-input" placeholder="Passwort oder {{env:VAR}}">
        </div>
        <button class="btn btn-primary" onclick="soapAddInstitut()">+ Institut</button>
      </div>
    </div>

    <div class="settings-subsection" style="margin-top:16px">
      <h4>Sessions</h4>
      <p class="settings-hint">Aktive Session-Tokens. Sessions werden automatisch beim ersten Aufruf erstellt.</p>
      <div id="soap-sessions-list"><div class="spinner-inline"></div></div>
    </div>

    <div class="settings-subsection" style="margin-top:16px">
      <h4>Services & Templates</h4>
      <p class="settings-hint">SOAP-Services mit XML-Templates.</p>
      <div id="soap-services-list"><div class="spinner-inline"></div></div>
      <div class="settings-add-form">
        <h5 style="margin:0 0 8px">Service hinzufügen</h5>
        <div class="settings-field-row">
          <input id="soap-svc-name" type="text" class="settings-input" placeholder="Service-Name (z.B. Kundenverwaltung)">
          <input id="soap-svc-desc" type="text" class="settings-input" placeholder="Beschreibung">
        </div>
        <button class="btn btn-primary" onclick="soapAddService()">+ Service</button>
      </div>
    </div>

    <div id="soap-template-modal" class="modal" style="display:none">
      <div class="modal-content" style="max-width:800px">
        <div class="modal-header">
          <h3 id="soap-template-title">Template bearbeiten</h3>
          <button class="modal-close" onclick="soapCloseTemplateModal()">&times;</button>
        </div>
        <div class="modal-body">
          <div class="settings-field">
            <label>XML-Template:</label>
            <textarea id="soap-template-content" class="settings-input" rows="20" style="font-family:monospace;font-size:12px" placeholder="<?xml version='1.0'?>
<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>
  <soap:Header>
    <AuthHeader>
      <SessionToken>{{session_token}}</SessionToken>
    </AuthHeader>
  </soap:Header>
  <soap:Body>
    <!-- Operation content -->
  </soap:Body>
</soap:Envelope>"></textarea>
          </div>
          <p class="settings-hint">Platzhalter: <code>{{session_token}}</code>, <code>{{institut}}</code>, <code>{{param_name}}</code>, <code>{{param:default}}</code></p>
          <div id="soap-template-params" style="margin-top:8px"></div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" onclick="soapCloseTemplateModal()">Abbrechen</button>
          <button class="btn btn-primary" onclick="soapSaveTemplate()">Speichern</button>
        </div>
      </div>
    </div>
  `;
  await soapLoadAll();
}

async function ttLoadAll() {
  const [stRes, svRes, wlpRes] = await Promise.all([
    fetch('/api/testtool/stages'),
    fetch('/api/testtool/services'),
    fetch('/api/testtool/local-wlp'),
  ]);
  const stData = await stRes.json();
  const svData = await svRes.json();
  const wlpData = wlpRes.ok ? await wlpRes.json() : { local_wlp_url: '' };

  // Lokaler WLP-URL
  const wlpSection = document.getElementById('tt-local-wlp-section');
  if (wlpSection) {
    wlpSection.innerHTML = `
      <div class="settings-field-row">
        <input id="tt-local-wlp-url" type="text" class="settings-input" placeholder="http://localhost:9080" value="${escapeHtml(wlpData.local_wlp_url || '')}">
        <button class="btn btn-primary btn-sm" onclick="ttSaveLocalWLP()">Speichern</button>
        ${wlpData.local_wlp_url ? '<button class="btn btn-secondary btn-sm" onclick="ttClearLocalWLP()">&#10006; Löschen</button>' : ''}
      </div>
      ${wlpData.local_wlp_url ? `<p style="margin:4px 0 0;font-size:12px;color:var(--success)">&#10003; Aktiv: <code>${escapeHtml(wlpData.local_wlp_url)}</code></p>` : '<p style="margin:4px 0 0;font-size:12px;color:var(--text-muted)">Nicht konfiguriert</p>'}
    `;
  }

  // Stages
  const stList = document.getElementById('tt-stages-list');
  if (stList) {
    stList.innerHTML = !stData.stages?.length ? '<p class="empty-hint">Keine Stages konfiguriert.</p>' :
      stData.stages.map(s => `
        <div class="ds-item">
          <div class="ds-item-header">
            <span class="ds-item-name">${escapeHtml(s.name)}</span>
            ${s.id === stData.active_stage ? '<span class="badge badge-success">aktiv</span>' : ''}
            <div class="ds-item-actions">
              <button class="btn btn-xs btn-secondary" onclick="ttSetActiveStage('${s.id}')">Aktivieren</button>
              <button class="btn btn-xs btn-danger" onclick="ttDeleteStage('${s.id}')">&#128465;</button>
            </div>
          </div>
          <div class="ds-item-detail">${s.urls.map(u => `<code>${escapeHtml(u.url)}</code> ${escapeHtml(u.description||'')}`).join(' | ')}</div>
        </div>
      `).join('');
  }

  // Active Stage Select
  const sel = document.getElementById('tt-active-stage-select');
  if (sel) {
    sel.innerHTML = stData.stages.map(s => `<option value="${s.id}" ${s.id===stData.active_stage?'selected':''}>${escapeHtml(s.name)}</option>`).join('');
  }

  // Services
  const svList = document.getElementById('tt-services-list');
  if (svList) {
    svList.innerHTML = !svData.services?.length ? '<p class="empty-hint">Keine Services konfiguriert.</p>' :
      svData.services.map(s => `
        <div class="ds-item">
          <div class="ds-item-header">
            <span class="ds-item-name">${escapeHtml(s.name)}</span>
            <span class="ds-item-badge">${s.method}</span>
            ${s.local_script ? '<span class="ds-item-badge badge-info">lokal</span>' : ''}
            <button class="btn btn-xs btn-danger" onclick="ttDeleteService('${s.id}')">&#128465;</button>
          </div>
          <div class="ds-item-detail"><code>${escapeHtml(s.endpoint)}</code> ${s.description ? '– ' + escapeHtml(s.description) : ''}</div>
          ${s.parameters?.length ? `<div class="ds-item-desc"><small>Params: ${s.parameters.map(p=>`${p.name}(${p.type}${p.required?'*':''})`).join(', ')}</small></div>` : ''}
        </div>
      `).join('');
  }
}

// Aktuell ausgewählte Stage-ID für URL-Zuweisung
let _ttSelectedStageId = null;

async function ttAddStage() {
  const name = document.getElementById('tt-new-stage-name').value.trim();
  if (!name) return;
  const res = await fetch('/api/testtool/stages', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name, urls: [] }) });
  if (!res.ok) { updateSettingsStatus('Fehler', 'error'); return; }
  const data = await res.json();
  _ttSelectedStageId = data.added.id;
  document.getElementById('tt-new-stage-name').value = '';
  updateSettingsStatus('Stage hinzugefügt ✓', 'success');
  await ttLoadAll();
}

async function ttAddUrlToStage() {
  const stageId = _ttSelectedStageId || document.getElementById('tt-active-stage-select')?.value;
  if (!stageId) { updateSettingsStatus('Bitte zuerst eine Stage auswählen oder erstellen', 'error'); return; }
  const url = document.getElementById('tt-new-url').value.trim();
  const desc = document.getElementById('tt-new-url-desc').value.trim();
  if (!url) return;
  const stage = (await (await fetch('/api/testtool/stages')).json()).stages.find(s => s.id === stageId);
  if (!stage) return;
  const newUrls = [...stage.urls, { url, description: desc }];
  await fetch(`/api/testtool/stages/${stageId}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: stage.name, urls: newUrls }) });
  document.getElementById('tt-new-url').value = '';
  document.getElementById('tt-new-url-desc').value = '';
  await ttLoadAll();
}

async function ttSetActiveStage(id) {
  await fetch(`/api/testtool/stages/active?stage_id=${encodeURIComponent(id)}`, { method: 'PUT' });
  updateSettingsStatus('Aktive Stage gesetzt ✓', 'success');
  await ttLoadAll();
}

async function ttDeleteStage(id) {
  if (!confirm('Stage löschen?')) return;
  await fetch(`/api/testtool/stages/${id}`, { method: 'DELETE' });
  await ttLoadAll();
}

async function ttSaveLocalWLP() {
  const url = document.getElementById('tt-local-wlp-url')?.value.trim();
  if (!url) { ttClearLocalWLP(); return; }
  await fetch('/api/testtool/local-wlp', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  updateSettingsStatus('Lokaler WLP gespeichert ✓', 'success');
  await ttLoadAll();
}

async function ttClearLocalWLP() {
  await fetch('/api/testtool/local-wlp', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: '' }),
  });
  updateSettingsStatus('Lokaler WLP entfernt', 'info');
  await ttLoadAll();
}

async function ttAddService() {
  const paramLines = document.getElementById('tt-svc-params').value.trim().split('\n').filter(Boolean);
  const parameters = paramLines.map(line => {
    const [name, type, req] = line.split(',').map(s => s.trim());
    return { name: name||'', type: type||'string', required: req === 'true', description: '', location: 'body', default: '' };
  });
  const body = {
    name: document.getElementById('tt-svc-name').value,
    endpoint: document.getElementById('tt-svc-endpoint').value,
    method: document.getElementById('tt-svc-method').value,
    description: document.getElementById('tt-svc-desc').value,
    local_script: document.getElementById('tt-svc-script').value,
    parameters,
  };
  const res = await fetch('/api/testtool/services', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (!res.ok) { updateSettingsStatus('Fehler', 'error'); return; }
  updateSettingsStatus('Service hinzugefügt ✓', 'success');
  ['tt-svc-name','tt-svc-endpoint','tt-svc-desc','tt-svc-script','tt-svc-params'].forEach(id => document.getElementById(id).value = '');
  await ttLoadAll();
}

async function ttDeleteService(id) {
  if (!confirm('Service löschen?')) return;
  await fetch(`/api/testtool/services/${id}`, { method: 'DELETE' });
  await ttLoadAll();
}

// ══════════════════════════════════════════════════════════════════════════════
// SOAP Helper Functions (used by Test-Tool)
// ══════════════════════════════════════════════════════════════════════════════

async function soapLoadAll() {
  let configData = null;
  let instituteData = null;
  let servicesData = null;

  // Config und Templates parallel laden
  try {
    const [cfgRes, tplRes] = await Promise.all([
      fetch('/api/testtool/config'),
      fetch('/api/testtool/templates'),
    ]);

    if (cfgRes.ok) {
      configData = await cfgRes.json();
      const urlEl = document.getElementById('soap-service-url');
      const loginEl = document.getElementById('soap-login-url');
      const sslEl = document.getElementById('soap-verify-ssl');
      const xpathEl = document.getElementById('soap-session-xpath');
      if (urlEl) urlEl.value = configData.service_url || '';
      if (loginEl) loginEl.value = configData.login_url || '';
      if (sslEl) sslEl.checked = configData.verify_ssl !== false;
      if (xpathEl) xpathEl.value = configData.session_token_xpath || '//SessionToken/text()';
    }

    // Templates-Dropdown befüllen
    if (tplRes.ok) {
      const tplData = await tplRes.json();
      const tplSelect = document.getElementById('soap-login-template');
      if (tplSelect && tplData.templates) {
        tplSelect.innerHTML = tplData.templates.map(t =>
          `<option value="${escapeHtml(t.name)}" ${t.name === configData?.login_template ? 'selected' : ''}>${escapeHtml(t.name)}</option>`
        ).join('');
        if (!tplData.templates.length) {
          tplSelect.innerHTML = '<option value="">Keine Templates gefunden</option>';
        }
      }
    }
  } catch (e) { log.error('SOAP config load error:', e); }

  // Institute laden
  instituteData = await soapLoadInstitute();

  // Sessions laden
  await soapLoadSessions();

  // Services laden
  servicesData = await soapLoadServices();

  // Konfigurations-Status aktualisieren
  soapUpdateConfigStatus(configData, instituteData, servicesData);
}

function soapUpdateConfigStatus(config, institutes, services) {
  const statusEl = document.getElementById('soap-config-status');
  if (!statusEl) return;

  const issues = [];
  const ok = [];

  // Login-URL prüfen
  if (!config?.login_url) {
    issues.push('Login-URL nicht konfiguriert');
  } else {
    ok.push('Login-URL');
  }

  // Login-Template prüfen
  if (!config?.login_template) {
    issues.push('Login-Template nicht ausgewählt');
  } else {
    ok.push(`Template: ${config.login_template}`);
  }

  // Institute prüfen
  const instCount = institutes?.institute?.length || 0;
  const enabledInst = institutes?.institute?.filter(i => i.enabled)?.length || 0;
  if (instCount === 0) {
    issues.push('Keine Institute konfiguriert');
  } else if (enabledInst === 0) {
    issues.push('Kein Institut aktiviert');
  } else {
    ok.push(`${enabledInst} Institut(e)`);
  }

  if (issues.length === 0) {
    statusEl.innerHTML = `<span style="color:var(--success)">&#10003; Login bereit:</span> ${ok.join(', ')}`;
    statusEl.style.borderLeft = '3px solid var(--success)';
  } else {
    statusEl.innerHTML = `<span style="color:var(--warning)">&#9888; Login nicht möglich:</span><ul style="margin:4px 0 0 16px;padding:0">${issues.map(i => `<li>${escapeHtml(i)}</li>`).join('')}</ul>`;
    statusEl.style.borderLeft = '3px solid var(--warning)';
  }
}

async function soapSaveConfig() {
  const body = {
    service_url: document.getElementById('soap-service-url').value.trim(),
    login_url: document.getElementById('soap-login-url').value.trim(),
    verify_ssl: document.getElementById('soap-verify-ssl').checked,
    login_template: document.getElementById('soap-login-template').value,
    session_token_xpath: document.getElementById('soap-session-xpath').value.trim() || '//SessionToken/text()',
  };
  const res = await fetch('/api/testtool/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (res.ok) {
    updateSettingsStatus('Konfiguration gespeichert ✓', 'success');
    // Status aktualisieren
    await soapLoadAll();
  } else {
    updateSettingsStatus('Fehler beim Speichern', 'error');
  }
}

async function soapLoadInstitute() {
  const list = document.getElementById('soap-institute-list');
  let data = null;

  try {
    const res = await fetch('/api/testtool/institute');
    data = await res.json();

    if (!list) return data;

    if (!data.institute?.length) {
      list.innerHTML = '<p class="empty-hint">Keine Institute konfiguriert.</p>';
      return data;
    }

    list.innerHTML = data.institute.map(inst => `
      <div class="ds-item" data-institut="${escapeHtml(inst.institut_nr)}">
        <div class="ds-item-header">
          <span class="ds-item-name">${inst.enabled ? '🏢' : '⏸️'} ${escapeHtml(inst.name || inst.institut_nr)}</span>
          <span class="ds-item-badge">${escapeHtml(inst.institut_nr)}</span>
          <div class="ds-item-actions">
            <button class="btn btn-xs btn-primary" onclick="soapTestLogin('${escapeHtml(inst.institut_nr)}')" title="Login testen" ${!inst.enabled ? 'disabled' : ''}>🔑 Login</button>
            <button class="btn btn-xs btn-secondary" onclick="soapEditInstitut('${escapeHtml(inst.institut_nr)}')" title="Bearbeiten">✏️</button>
            <button class="btn btn-xs btn-danger" onclick="soapDeleteInstitut('${escapeHtml(inst.institut_nr)}')" title="Löschen">🗑️</button>
          </div>
        </div>
        <div class="ds-item-details">
          <span class="ds-detail-label">User:</span> ${escapeHtml(inst.user)}
          <span class="ds-detail-label" style="margin-left:12px">Passwort:</span> ${inst.password ? '••••••••' : '(leer)'}
        </div>
        <div id="soap-login-status-${escapeHtml(inst.institut_nr)}" class="ds-item-status" style="margin-top:4px;font-size:11px"></div>
      </div>
    `).join('');
  } catch (e) {
    if (list) list.innerHTML = '<p class="error-hint">Fehler beim Laden der Institute</p>';
  }
  return data;
}

async function soapAddInstitut() {
  const nr = document.getElementById('soap-inst-nr').value.trim();
  const name = document.getElementById('soap-inst-name').value.trim();
  const user = document.getElementById('soap-inst-user').value.trim();
  const pass = document.getElementById('soap-inst-pass').value;

  if (!nr) {
    updateSettingsStatus('Institut-Nr ist erforderlich', 'error');
    return;
  }

  const res = await fetch('/api/testtool/institute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      institut_nr: nr,
      name: name || nr,
      user: user,
      password: pass,
      enabled: true
    })
  });

  if (res.ok) {
    updateSettingsStatus('Institut hinzugefügt ✓', 'success');
    ['soap-inst-nr', 'soap-inst-name', 'soap-inst-user', 'soap-inst-pass'].forEach(id => {
      document.getElementById(id).value = '';
    });
    await soapLoadInstitute();
    await soapLoadSessions();
  } else {
    const err = await res.json().catch(() => ({}));
    updateSettingsStatus(err.detail || 'Fehler', 'error');
  }
}

async function soapEditInstitut(nr) {
  const name = prompt('Neuer Name (leer = behalten):');
  if (name === null) return;

  const user = prompt('Neuer Benutzername (leer = behalten):');
  if (user === null) return;

  const pass = prompt('Neues Passwort (leer = behalten):');
  if (pass === null) return;

  // Aktuelle Daten laden
  const res = await fetch('/api/testtool/institute');
  const data = await res.json();
  const inst = data.institute?.find(i => i.institut_nr === nr);
  if (!inst) return;

  await fetch(`/api/testtool/institute/${encodeURIComponent(nr)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      institut_nr: nr,
      name: name || inst.name,
      user: user || inst.user,
      password: pass || '********',
      enabled: inst.enabled
    })
  });
  await soapLoadInstitute();
}

async function soapDeleteInstitut(nr) {
  if (!confirm(`Institut ${nr} wirklich löschen?`)) return;
  await fetch(`/api/testtool/institute/${encodeURIComponent(nr)}`, { method: 'DELETE' });
  updateSettingsStatus('Institut gelöscht', 'success');
  await soapLoadInstitute();
  await soapLoadSessions();
}

async function soapTestLogin(institutNr) {
  const statusEl = document.getElementById(`soap-login-status-${institutNr}`);
  if (statusEl) {
    statusEl.innerHTML = '<span style="color:var(--text-muted)">⏳ Login wird durchgeführt...</span>';
  }

  try {
    const res = await fetch(`/api/testtool/session/${encodeURIComponent(institutNr)}/login`, {
      method: 'POST'
    });
    const data = await res.json();

    if (res.ok && data.success) {
      if (statusEl) {
        statusEl.innerHTML = `<span style="color:var(--success)">✓ Login erfolgreich!</span>
          <span style="margin-left:8px;color:var(--text-muted)">Token: ${escapeHtml(data.token_preview || '...')}</span>`;
      }
      updateSettingsStatus(`Login erfolgreich für Institut ${institutNr}`, 'success');
      await soapLoadSessions();
    } else {
      const errorMsg = data.detail || data.error || 'Login fehlgeschlagen';
      if (statusEl) {
        statusEl.innerHTML = `<span style="color:var(--error)">✗ ${escapeHtml(errorMsg)}</span>`;
      }
      updateSettingsStatus(`Login fehlgeschlagen: ${errorMsg}`, 'error');
    }
  } catch (e) {
    if (statusEl) {
      statusEl.innerHTML = `<span style="color:var(--error)">✗ Verbindungsfehler: ${escapeHtml(e.message)}</span>`;
    }
    updateSettingsStatus(`Verbindungsfehler: ${e.message}`, 'error');
  }
}

async function soapLoadSessions() {
  const list = document.getElementById('soap-sessions-list');
  if (!list) return;

  try {
    const res = await fetch('/api/testtool/sessions');
    const data = await res.json();

    const sessions = Object.entries(data.sessions || {});
    if (!sessions.length) {
      list.innerHTML = '<p class="empty-hint">Keine aktiven Sessions.</p>';
      return;
    }

    list.innerHTML = sessions.map(([nr, s]) => `
      <div class="ds-item">
        <div class="ds-item-header">
          <span class="ds-item-name">${s.has_token && !s.is_expired ? '🟢' : '🔴'} Institut ${escapeHtml(nr)}</span>
          <div class="ds-item-actions">
            <button class="btn btn-xs btn-secondary" onclick="soapRefreshSession('${escapeHtml(nr)}')" title="Neu einloggen">🔄</button>
            <button class="btn btn-xs btn-danger" onclick="soapDeleteSession('${escapeHtml(nr)}')" title="Session löschen">🗑️</button>
          </div>
        </div>
        <div class="ds-item-details">
          <span class="ds-detail-label">User:</span> ${escapeHtml(s.user || '-')}
          <span class="ds-detail-label" style="margin-left:12px">Status:</span>
          ${s.has_token ? (s.is_expired ? 'Abgelaufen' : 'Aktiv') : 'Kein Token'}
          ${s.expires_at ? `<span class="ds-detail-label" style="margin-left:12px">Läuft ab:</span> ${new Date(s.expires_at).toLocaleString()}` : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = '<p class="empty-hint">Sessions nicht verfügbar</p>';
  }
}

async function soapRefreshSession(nr) {
  updateSettingsStatus('Login wird durchgeführt...', 'info');
  try {
    const res = await fetch(`/api/testtool/session/${encodeURIComponent(nr)}/login`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.success) {
      updateSettingsStatus(`Login erfolgreich für Institut ${nr} ✓`, 'success');
    } else {
      // Show actual error from backend
      const errorMsg = data.detail || data.error || 'Login fehlgeschlagen';
      updateSettingsStatus(`Login fehlgeschlagen: ${errorMsg}`, 'error');
    }
    await soapLoadSessions();
  } catch (e) {
    updateSettingsStatus(`Verbindungsfehler: ${e.message}`, 'error');
  }
}

async function soapDeleteSession(nr) {
  await fetch(`/api/testtool/session/${encodeURIComponent(nr)}`, { method: 'DELETE' });
  updateSettingsStatus('Session gelöscht', 'success');
  await soapLoadSessions();
}

async function soapLoadServices() {
  const list = document.getElementById('soap-services-list');
  let data = null;

  try {
    const res = await fetch('/api/testtool/services');
    data = await res.json();

    if (!list) return data;

    if (!data.services?.length) {
      list.innerHTML = '<p class="empty-hint">Keine Services konfiguriert.</p>';
      return data;
    }

    list.innerHTML = data.services.map(svc => `
      <div class="ds-item" data-svc-id="${svc.id}">
        <div class="ds-item-header">
          <span class="ds-item-name">${svc.enabled ? '📡' : '⏸️'} ${escapeHtml(svc.name)}</span>
          <div style="display:flex;gap:4px;align-items:center">
            <span class="ds-item-badge">${svc.operation_count} Ops</span>
            <button class="btn btn-xs btn-secondary" onclick="soapToggleAddOp('${svc.id}')" title="Operation hinzufügen">+Op</button>
            <button class="btn btn-xs btn-danger" onclick="soapDeleteService('${svc.id}')" title="Service löschen">&#128465;</button>
          </div>
        </div>
        <div class="ds-item-details">${escapeHtml(svc.description || 'Keine Beschreibung')}</div>
        <div id="soap-add-op-${svc.id}" class="settings-add-form" style="display:none;margin:8px 0">
          <div class="settings-field-row">
            <input id="soap-op-name-${svc.id}" type="text" class="settings-input" placeholder="Operation-Name">
            <input id="soap-op-desc-${svc.id}" type="text" class="settings-input" placeholder="Beschreibung">
            <button class="btn btn-primary btn-sm" onclick="soapAddOperation('${svc.id}')">Hinzufügen</button>
          </div>
        </div>
        ${svc.operations?.length ? `
          <div class="soap-operations-list">
            ${svc.operations.map(op => `
              <div class="soap-op-item" style="display:flex;justify-content:space-between;align-items:center">
                <div>
                  <span class="soap-op-name">▸ ${escapeHtml(op.name)}</span>
                  <span class="soap-op-desc">${escapeHtml(op.description || '')}</span>
                </div>
                <div style="display:flex;gap:4px">
                  <button class="btn btn-xs btn-secondary" onclick="soapEditTemplate('${svc.id}','${op.id}','${escapeHtml(op.name)}')" title="Template bearbeiten">XML</button>
                  <button class="btn btn-xs btn-danger" onclick="soapDeleteOperation('${svc.id}','${op.id}')" title="Löschen">&#128465;</button>
                </div>
              </div>
            `).join('')}
          </div>
        ` : '<div class="empty-hint" style="padding:4px 8px;font-size:12px">Keine Operationen</div>'}
      </div>
    `).join('');
  } catch (e) {
    if (list) list.innerHTML = '<p class="error-hint">Fehler beim Laden der Services</p>';
  }
  return data;
}

async function soapAddService() {
  const name = document.getElementById('soap-svc-name').value.trim();
  const desc = document.getElementById('soap-svc-desc').value.trim();
  if (!name) { updateSettingsStatus('Service-Name erforderlich', 'error'); return; }

  const res = await fetch('/api/testtool/services', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: desc, operations: [] })
  });

  if (res.ok) {
    document.getElementById('soap-svc-name').value = '';
    document.getElementById('soap-svc-desc').value = '';
    updateSettingsStatus('Service hinzugefügt', 'success');
    await soapLoadServices();
  } else {
    const err = await res.json();
    updateSettingsStatus(err.detail || 'Fehler', 'error');
  }
}

async function soapDeleteService(svcId) {
  if (!confirm('Service wirklich löschen?')) return;
  const res = await fetch(`/api/testtool/services/${svcId}`, { method: 'DELETE' });
  if (res.ok) {
    updateSettingsStatus('Service gelöscht', 'success');
    await soapLoadServices();
  }
}

function soapToggleAddOp(svcId) {
  const el = document.getElementById(`soap-add-op-${svcId}`);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function soapAddOperation(svcId) {
  const name = document.getElementById(`soap-op-name-${svcId}`).value.trim();
  const desc = document.getElementById(`soap-op-desc-${svcId}`).value.trim();
  if (!name) { updateSettingsStatus('Operation-Name erforderlich', 'error'); return; }

  const res = await fetch(`/api/testtool/services/${svcId}/operations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: desc })
  });

  if (res.ok) {
    updateSettingsStatus('Operation hinzugefügt', 'success');
    await soapLoadServices();
  } else {
    const err = await res.json();
    updateSettingsStatus(err.detail || 'Fehler', 'error');
  }
}

async function soapDeleteOperation(svcId, opId) {
  if (!confirm('Operation wirklich löschen?')) return;
  const res = await fetch(`/api/testtool/services/${svcId}/operations/${opId}`, { method: 'DELETE' });
  if (res.ok) {
    updateSettingsStatus('Operation gelöscht', 'success');
    await soapLoadServices();
  }
}

let _currentTemplateEdit = { svcId: '', opId: '' };

async function soapEditTemplate(svcId, opId, opName) {
  _currentTemplateEdit = { svcId, opId };
  document.getElementById('soap-template-title').textContent = `Template: ${opName}`;

  // Template laden
  try {
    const res = await fetch(`/api/testtool/templates/${svcId}/${opId}`);
    if (res.ok) {
      const data = await res.json();
      document.getElementById('soap-template-content').value = data.content || '';
      const paramsDiv = document.getElementById('soap-template-params');
      if (data.parameters?.length) {
        paramsDiv.innerHTML = '<strong>Erkannte Parameter:</strong> ' + data.parameters.map(p =>
          `<code>${p.name}${p.required ? '*' : ''}</code>`
        ).join(', ');
      } else {
        paramsDiv.innerHTML = '';
      }
    } else {
      // Template existiert noch nicht
      document.getElementById('soap-template-content').value = `<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Header>
    <AuthHeader>
      <SessionToken>{{session_token}}</SessionToken>
    </AuthHeader>
  </soap:Header>
  <soap:Body>
    <!-- TODO: ${opName} Request Body -->
  </soap:Body>
</soap:Envelope>`;
      document.getElementById('soap-template-params').innerHTML = '<em>Neues Template</em>';
    }
  } catch (e) {
    document.getElementById('soap-template-content').value = '';
    document.getElementById('soap-template-params').innerHTML = '<span class="error-hint">Fehler beim Laden</span>';
  }

  document.getElementById('soap-template-modal').style.display = 'flex';
}

function soapCloseTemplateModal() {
  document.getElementById('soap-template-modal').style.display = 'none';
  _currentTemplateEdit = { svcId: '', opId: '' };
}

async function soapSaveTemplate() {
  const { svcId, opId } = _currentTemplateEdit;
  const content = document.getElementById('soap-template-content').value;

  if (!content.trim()) {
    updateSettingsStatus('Template darf nicht leer sein', 'error');
    return;
  }

  const res = await fetch(`/api/testtool/templates/${svcId}/${opId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  });

  if (res.ok) {
    updateSettingsStatus('Template gespeichert', 'success');
    soapCloseTemplateModal();
  } else {
    const err = await res.json();
    updateSettingsStatus(err.detail || 'Fehler beim Speichern', 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Log-Server Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderLogServersSection() {
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">LOG-SERVER</h3>
      <p class="settings-section-desc">URLs zum Log-Download je Stage und Server. Der Agent nutzt Zeitstempel-Abgleich um den richtigen Server für einen Testzeitpunkt zu finden.</p>
    </div>
    <div id="ls-stages-list"><div class="spinner-inline"></div></div>
    <div class="settings-add-form">
      <h4>Stage hinzufügen</h4>
      <div class="settings-field-row">
        <input id="ls-new-stage" type="text" class="settings-input" placeholder="Stage-Name (z.B. Production)">
        <button class="btn btn-primary btn-sm" onclick="lsAddStage()">+ Stage</button>
      </div>
    </div>
    <div class="settings-add-form" style="margin-top:12px">
      <h4>Server zu Stage hinzufügen</h4>
      <div class="settings-field">
        <label>Stage:</label>
        <select id="ls-target-stage" class="settings-input" style="max-width:200px"></select>
      </div>
      <div class="settings-field-row">
        <input id="ls-new-srv-name" type="text" class="settings-input" placeholder="Server-Name">
        <input id="ls-new-srv-url" type="text" class="settings-input" placeholder="Log-Download-URL">
        <button class="btn btn-primary btn-sm" onclick="lsAddServer()">+ Server</button>
      </div>
      <div class="settings-field"><label>Beschreibung:</label><input id="ls-new-srv-desc" type="text" class="settings-input" placeholder="Optionale Beschreibung"></div>
    </div>
  `;
  await lsLoadAll();
}

async function lsLoadAll() {
  const res = await fetch('/api/log-servers/stages');
  const data = await res.json();
  const list = document.getElementById('ls-stages-list');
  if (!list) return;
  list.innerHTML = !data.stages?.length ? '<p class="empty-hint">Keine Stages konfiguriert.</p>' :
    data.stages.map(stage => `
      <div class="ds-item">
        <div class="ds-item-header">
          <span class="ds-item-name">&#128218; ${escapeHtml(stage.name)}</span>
          <button class="btn btn-xs btn-danger" onclick="lsDeleteStage('${stage.id}')">&#128465;</button>
        </div>
        ${stage.servers.map(srv => `
          <div class="ls-server-row">
            <span class="ds-detail-label">${escapeHtml(srv.name)}</span>
            <code>${escapeHtml(srv.url)}</code>
            ${srv.description ? `<small>${escapeHtml(srv.description)}</small>` : ''}
            <button class="btn btn-xs btn-danger" onclick="lsDeleteServer('${stage.id}','${srv.id}')">&#128465;</button>
          </div>
        `).join('')}
        ${!stage.servers.length ? '<div class="ls-server-row empty-hint">Keine Server</div>' : ''}
      </div>
    `).join('');

  // Stage-Select für Server-Zuweisung befüllen
  const sel = document.getElementById('ls-target-stage');
  if (sel) sel.innerHTML = data.stages.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
}

async function lsAddStage() {
  const name = document.getElementById('ls-new-stage').value.trim();
  if (!name) return;
  await fetch('/api/log-servers/stages', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name }) });
  document.getElementById('ls-new-stage').value = '';
  await lsLoadAll();
}

async function lsDeleteStage(id) {
  if (!confirm('Stage löschen?')) return;
  await fetch(`/api/log-servers/stages/${id}`, { method: 'DELETE' });
  await lsLoadAll();
}

async function lsAddServer() {
  const stageId = document.getElementById('ls-target-stage')?.value;
  if (!stageId) return;
  const body = {
    name: document.getElementById('ls-new-srv-name').value,
    url: document.getElementById('ls-new-srv-url').value,
    description: document.getElementById('ls-new-srv-desc').value,
  };
  const res = await fetch(`/api/log-servers/stages/${stageId}/servers`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (!res.ok) { updateSettingsStatus('Fehler', 'error'); return; }
  ['ls-new-srv-name','ls-new-srv-url','ls-new-srv-desc'].forEach(id => document.getElementById(id).value = '');
  await lsLoadAll();
}

async function lsDeleteServer(stageId, serverId) {
  if (!confirm('Server löschen?')) return;
  await fetch(`/api/log-servers/stages/${stageId}/servers/${serverId}`, { method: 'DELETE' });
  await lsLoadAll();
}

// ══════════════════════════════════════════════════════════════════════════════
// WLP Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderWLPSection() {
  // JAVA_HOME aus Settings laden
  let wlpConfig = settingsState.settings?.wlp || {};
  try {
    const res = await fetch('/api/settings/section/wlp');
    if (res.ok) wlpConfig = (await res.json()).values || wlpConfig;
  } catch (_) {}

  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">WLP SERVER</h3>
      <p class="settings-section-desc">WebSphere Liberty Profile Server starten, server.xml prüfen und Artefakt validieren. Start wird per SSE-Stream überwacht.</p>
    </div>

    <!-- Aktivierung -->
    <div class="settings-field">
      <label for="wlp-enabled">Aktiviert</label>
      <label class="checkbox-label">
        <input type="checkbox" id="wlp-enabled" ${wlpConfig.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        ${wlpConfig.enabled ? 'Aktiviert - WLP-Tools sind für den Agent verfügbar' : 'Deaktiviert - WLP-Tools werden nicht geladen'}
      </label>
      <span style="font-size:11px;color:var(--text-muted)">Aktiviere dies um die WLP-Agent-Tools (Server starten, stoppen, Konfiguration prüfen) zu nutzen.</span>
    </div>

    <!-- Java-Konfiguration -->
    <div class="settings-subsection">
      <h4>&#9749; Java-Konfiguration</h4>
      <div class="settings-field">
        <label>JAVA_HOME (für WLP)</label>
        <input type="text" id="wlp-java-home" class="settings-input"
               placeholder="z.B. C:\\Program Files\\Java\\jdk-17 oder /usr/lib/jvm/java-17"
               value="${escapeHtml(wlpConfig.java_home || '')}"
               onchange="markSettingsModified()">
        <span style="font-size:11px;color:var(--text-muted)">Leer lassen für System-Default. Wird beim Server-Start als JAVA_HOME gesetzt.</span>
      </div>
    </div>

    <!-- Import aus WLP-Installation -->
    <div class="settings-subsection" style="background: var(--accent-bg); border-color: var(--accent);">
      <h4>&#128229; Server aus WLP-Installation importieren</h4>
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:8px">Scannt einen WLP-Ordner nach vorhandenen Servern (usr/servers/*) inkl. jvm.options.</p>
      <div class="settings-field-row">
        <input id="wlp-discover-path" type="text" class="settings-input" placeholder="WLP-Pfad (z.B. C:\\wlp oder /opt/ibm/wlp)">
        <button class="btn btn-primary" onclick="wlpDiscoverServers()">&#128269; Suchen</button>
      </div>
      <div id="wlp-discover-results" style="margin-top:10px"></div>
    </div>

    <div id="wlp-list"><div class="spinner-inline"></div></div>
    <div class="settings-add-form">
      <h4>Server manuell hinzufügen</h4>
      <div class="settings-field"><label>Name</label><input id="wlp-new-name" type="text" class="settings-input" placeholder="z.B. Lokaler Dev-Server"></div>
      <div class="settings-field"><label>WLP-Pfad (Verzeichnis, z.B. /opt/ibm/wlp)</label><input id="wlp-new-path" type="text" class="settings-input" placeholder="/opt/ibm/wlp"></div>
      <div class="settings-field"><label>Server-Name (in usr/servers/)</label><input id="wlp-new-srvname" type="text" class="settings-input" placeholder="defaultServer" value="defaultServer"></div>
      <div class="settings-field"><label>Beschreibung</label><input id="wlp-new-desc" type="text" class="settings-input"></div>
      <div class="settings-field"><label>Extra JVM-Args</label><input id="wlp-new-jvm" type="text" class="settings-input" placeholder="-Xmx512m"></div>
      <div class="settings-field"><label>Repo-Pfad (für Artefakt-Suche, optional)</label><input id="wlp-repo-path" type="text" class="settings-input" placeholder="Standardmäßig aktives Java-Repo"></div>
      <button class="btn btn-primary" onclick="wlpAddServer()">+ Hinzufügen</button>
    </div>
  `;
  await wlpLoadList();
}

// ── WLP Discovery & Import ────────────────────────────────────────────────────
async function wlpDiscoverServers() {
  const path = document.getElementById('wlp-discover-path').value.trim();
  const container = document.getElementById('wlp-discover-results');
  container.innerHTML = '<span class="spinner-inline"></span> Suche Server...';

  const url = path ? `/api/wlp/discover?path=${encodeURIComponent(path)}` : '/api/wlp/discover';
  const res = await fetch(url);
  const data = await res.json();

  if (!data.found?.length) {
    container.innerHTML = `<p class="empty-hint">${escapeHtml(data.message || 'Keine Server gefunden')}</p>`;
    if (data.hint) container.innerHTML += `<p style="font-size:11px;color:var(--text-muted)">${escapeHtml(data.hint)}</p>`;
    return;
  }

  container.innerHTML = `
    <p style="font-size:12px;margin-bottom:8px">${data.found.length} Server gefunden:</p>
    ${data.found.map((s, i) => `
      <div class="ds-item" style="padding:8px;margin-bottom:6px">
        <label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer">
          <input type="checkbox" class="wlp-import-check" data-idx="${i}" ${s.already_imported ? 'disabled' : 'checked'}>
          <div>
            <strong>${escapeHtml(s.server_name)}</strong>
            ${s.already_imported ? '<span class="badge">bereits importiert</span>' : ''}
            <div style="font-size:11px;color:var(--text-muted)">${escapeHtml(s.wlp_path)}</div>
            ${s.features?.length ? `<div style="font-size:11px;color:var(--text-secondary)">Features: ${escapeHtml(s.features.slice(0,5).join(', '))}${s.features.length > 5 ? '...' : ''}</div>` : ''}
            ${s.has_jvm_options ? `<div style="font-size:11px;color:var(--accent)">JVM: ${escapeHtml(s.jvm_options.substring(0,80))}${s.jvm_options.length > 80 ? '...' : ''}</div>` : ''}
          </div>
        </label>
      </div>
    `).join('')}
    <button class="btn btn-success" onclick="wlpImportSelected()">&#128229; Ausgewählte importieren</button>
  `;
  // Speichere Daten für Import
  window._wlpDiscoveredServers = data.found;
}

async function wlpImportSelected() {
  const servers = window._wlpDiscoveredServers || [];
  const toImport = [];
  document.querySelectorAll('.wlp-import-check:checked').forEach(cb => {
    const idx = parseInt(cb.dataset.idx);
    if (servers[idx] && !servers[idx].already_imported) {
      toImport.push(servers[idx]);
    }
  });
  if (!toImport.length) { updateSettingsStatus('Keine Server ausgewählt', 'error'); return; }

  try {
    const res = await fetch('/api/wlp/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ servers: toImport })
    });
    const data = await res.json();

    if (data.errors?.length) {
      log.warn('WLP Import Fehler:', data.errors);
      const errMsg = data.errors.map(e => `${e.server_name}: ${e.error}`).join(', ');
      if (data.imported_count > 0) {
        updateSettingsStatus(`${data.imported_count} importiert, Fehler: ${errMsg}`, 'warning');
      } else {
        updateSettingsStatus(`Import fehlgeschlagen: ${errMsg}`, 'error');
      }
    } else {
      updateSettingsStatus(`${data.imported_count} Server importiert ✓`, 'success');
    }
    document.getElementById('wlp-discover-results').innerHTML = '';
    await wlpLoadList();
  } catch (e) {
    log.error('WLP Import Fehler:', e);
    updateSettingsStatus('Import fehlgeschlagen: ' + e.message, 'error');
  }
}

async function wlpLoadList() {
  const res = await fetch('/api/wlp/servers');
  const data = await res.json();
  const list = document.getElementById('wlp-list');
  if (!list) return;
  list.innerHTML = !data.servers?.length ? '<p class="empty-hint">Keine WLP-Server konfiguriert.</p>' :
    data.servers.map(s => `
      <div class="ds-item">
        <div class="ds-item-header">
          <div>
            <span class="ds-item-name">${escapeHtml(s.name)}</span>
            ${data.running?.includes(s.id) ? '<span class="badge badge-success">läuft</span>' : '<span class="badge">gestoppt</span>'}
          </div>
          <div class="ds-item-actions">
            <button class="btn btn-xs btn-secondary" onclick="wlpValidate('${s.id}')">&#10003; Prüfen</button>
            <button class="btn btn-xs btn-primary" onclick="wlpStart('${s.id}')">&#9654; Start</button>
            <button class="btn btn-xs btn-warning" onclick="wlpStop('${s.id}')">&#9646;&#9646; Stop</button>
            <button class="btn btn-xs btn-danger" onclick="wlpDeleteServer('${s.id}')">&#128465;</button>
          </div>
        </div>
        <div class="ds-item-detail">
          <span class="ds-detail-label">WLP:</span> <code>${escapeHtml(s.wlp_path)}</code>
          &nbsp;|&nbsp; <span class="ds-detail-label">Server:</span> <code>${escapeHtml(s.server_name)}</code>
        </div>
        ${s.description ? `<div class="ds-item-desc">${escapeHtml(s.description)}</div>` : ''}
      </div>
    `).join('');
}

async function wlpAddServer() {
  const body = {
    name: document.getElementById('wlp-new-name').value,
    wlp_path: document.getElementById('wlp-new-path').value,
    server_name: document.getElementById('wlp-new-srvname').value || 'defaultServer',
    description: document.getElementById('wlp-new-desc').value,
    extra_jvm_args: document.getElementById('wlp-new-jvm').value,
  };
  const repoPath = document.getElementById('wlp-repo-path').value;
  if (repoPath) {
    await fetch('/api/settings/section/wlp', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ repo_path: repoPath }) });
  }
  const res = await fetch('/api/wlp/servers', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (!res.ok) { updateSettingsStatus('Fehler', 'error'); return; }
  updateSettingsStatus('Server hinzugefügt ✓', 'success');
  await wlpLoadList();
}

async function wlpDeleteServer(id) {
  if (!confirm('Server entfernen?')) return;
  await fetch(`/api/wlp/servers/${id}`, { method: 'DELETE' });
  await wlpLoadList();
}

async function wlpValidate(id) {
  updateSettingsStatus('⏳ Prüfe server.xml...', '');
  const res = await fetch(`/api/wlp/servers/${id}/validate`, { method: 'POST' });
  const data = await res.json();
  if (!data.valid) { updateSettingsStatus(`✗ ${data.error}`, 'error'); return; }
  const ok = data.all_artifacts_present;
  const appInfo = data.applications.map(a => `${a.name||a.tag} (${a.location||'?'})`).join(', ');
  const artOk = data.built_artifact ? `Artefakt: ${data.built_artifact.path} (${data.built_artifact.size_kb}KB)` : 'Kein gebautes Artefakt gefunden';
  updateSettingsStatus(
    `${ok ? '✓' : '⚠'} Apps: ${appInfo} | ${artOk}`,
    ok ? 'success' : 'warning'
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Maven Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderMavenSection() {
  // Maven-Config aus Settings laden
  let mavenConfig = settingsState.settings?.maven || {};
  try {
    const res = await fetch('/api/settings/section/maven');
    if (res.ok) mavenConfig = (await res.json()).values || mavenConfig;
  } catch (_) {}

  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">MAVEN BUILD</h3>
      <p class="settings-section-desc">Maven-Builds definieren und per Klick ausführen. Build-Ausgabe wird per SSE live gestreamt.</p>
    </div>

    <!-- Java & Maven Konfiguration -->
    <div class="settings-subsection">
      <h4>&#9749; Java & Maven Konfiguration</h4>
      <div class="settings-field">
        <label>JAVA_HOME</label>
        <input type="text" id="maven-java-home" class="settings-input"
               placeholder="z.B. C:\\Program Files\\Java\\jdk-17"
               value="${escapeHtml(mavenConfig.java_home || '')}"
               onchange="markSettingsModified()">
        <span style="font-size:11px;color:var(--text-muted)">Leer = System-Default</span>
      </div>
      <div class="settings-field">
        <label>Maven Executable</label>
        <input type="text" id="maven-mvn-exec" class="settings-input"
               placeholder="mvn (oder z.B. C:\\maven\\bin\\mvn.cmd)"
               value="${escapeHtml(mavenConfig.mvn_executable || 'mvn')}"
               onchange="markSettingsModified()">
      </div>
      <div class="settings-field">
        <label>Maven Settings (settings.xml)</label>
        <input type="text" id="maven-settings-file" class="settings-input"
               placeholder="z.B. C:\\Users\\user\\.m2\\settings.xml (leer = Default)"
               value="${escapeHtml(mavenConfig.settings_file || '')}"
               onchange="markSettingsModified()">
      </div>
      <div class="settings-field">
        <label>Lokales Repository</label>
        <input type="text" id="maven-local-repo" class="settings-input"
               placeholder="z.B. C:\\Users\\user\\.m2\\repository (leer = Default)"
               value="${escapeHtml(mavenConfig.local_repo || '')}"
               onchange="markSettingsModified()">
      </div>
    </div>

    <!-- Import aus Repository -->
    <div class="settings-subsection" style="background: var(--accent-bg); border-color: var(--accent);">
      <h4>&#128229; Builds aus Repository importieren</h4>
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:8px">Findet pom.xml Dateien und IntelliJ Maven Run Configurations im aktiven Java-Repository.</p>
      <button class="btn btn-primary" onclick="mvnDiscoverProjects()">&#128269; Projekte suchen</button>
      <div id="mvn-discover-results" style="margin-top:10px"></div>
    </div>
    <div id="mvn-builds-list"><div class="spinner-inline"></div></div>
    <div class="settings-add-form">
      <h4>Build manuell hinzufügen</h4>
      <div class="settings-field-row">
        <input id="mvn-new-name" type="text" class="settings-input" placeholder="Name (z.B. OrderService Build)">
        <input id="mvn-new-desc" type="text" class="settings-input" placeholder="Beschreibung">
      </div>
      <div class="settings-field"><label>pom.xml Pfad</label><input id="mvn-new-pom" type="text" class="settings-input" placeholder="/pfad/zum/pom.xml"></div>
      <div class="settings-field-row">
        <input id="mvn-new-goals" type="text" class="settings-input" placeholder="Goals (z.B. clean install)" value="clean install">
        <input id="mvn-new-profiles" type="text" class="settings-input" placeholder="Profile (kommasepariert)">
      </div>
      <div class="settings-field-row">
        <input id="mvn-new-jvm" type="text" class="settings-input" placeholder="JVM-Args (z.B. -Xmx512m)">
        <label class="checkbox-label" style="align-self:center">
          <input type="checkbox" id="mvn-new-skip-tests"> Tests überspringen
        </label>
      </div>
      <div class="settings-actions-section" style="margin-top:8px">
        <button class="btn btn-secondary" onclick="mvnDetectPoms()">&#128269; pom.xml erkennen</button>
        <button class="btn btn-primary" onclick="mvnAddBuild()">+ Build hinzufügen</button>
      </div>
      <div id="mvn-pom-detect-result" style="margin-top:8px"></div>
    </div>
  `;
  await mvnLoadBuilds();
}

// ── Maven Discovery & Import ──────────────────────────────────────────────────
async function mvnDiscoverProjects() {
  const container = document.getElementById('mvn-discover-results');
  container.innerHTML = '<span class="spinner-inline"></span> Suche Maven-Projekte...';

  const res = await fetch('/api/maven/discover');
  const data = await res.json();

  const poms = data.pom_projects || [];
  const intellij = data.intellij_configs || [];

  if (!poms.length && !intellij.length) {
    container.innerHTML = `<p class="empty-hint">${escapeHtml(data.message || 'Keine Maven-Projekte gefunden')}</p>`;
    return;
  }

  let html = '';

  // IntelliJ Configs zuerst (diese haben schon Goals etc.)
  if (intellij.length) {
    html += `<p style="font-size:12px;font-weight:600;margin-bottom:6px">IntelliJ Run Configurations (${intellij.length}):</p>`;
    intellij.forEach((c, i) => {
      html += `
        <div class="ds-item" style="padding:8px;margin-bottom:6px">
          <label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer">
            <input type="checkbox" class="mvn-import-intellij" data-idx="${i}" ${c.already_imported ? 'disabled' : 'checked'}>
            <div>
              <strong>${escapeHtml(c.name)}</strong>
              <span class="badge badge-info">IntelliJ</span>
              ${c.already_imported ? '<span class="badge">bereits importiert</span>' : ''}
              <div style="font-size:11px;color:var(--text-muted)">${escapeHtml(c.pom_path || '?')}</div>
              <div style="font-size:11px;color:var(--accent)">Goals: ${escapeHtml(c.goals)} ${c.profiles?.length ? `| Profile: ${c.profiles.join(',')}` : ''}</div>
            </div>
          </label>
        </div>
      `;
    });
  }

  // pom.xml Projekte
  if (poms.length) {
    html += `<p style="font-size:12px;font-weight:600;margin:10px 0 6px">pom.xml Projekte (${poms.length}):</p>`;
    poms.forEach((p, i) => {
      html += `
        <div class="ds-item" style="padding:8px;margin-bottom:6px">
          <label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer">
            <input type="checkbox" class="mvn-import-pom" data-idx="${i}" ${p.already_imported ? 'disabled' : 'checked'}>
            <div>
              <strong>${escapeHtml(p.name || p.artifact_id)}</strong>
              ${p.is_multi_module ? '<span class="badge">multi-module</span>' : ''}
              ${p.already_imported ? '<span class="badge">bereits importiert</span>' : ''}
              <div style="font-size:11px;color:var(--text-muted)">${escapeHtml(p.relative_path)}</div>
              <div style="font-size:11px;color:var(--text-secondary)">${escapeHtml(p.group_id)}:${escapeHtml(p.artifact_id)} (${p.packaging})</div>
            </div>
          </label>
        </div>
      `;
    });
  }

  html += `<button class="btn btn-success" onclick="mvnImportSelected()">&#128229; Ausgewählte importieren</button>`;
  container.innerHTML = html;

  // Speichere Daten für Import
  window._mvnDiscoveredPoms = poms;
  window._mvnDiscoveredIntelliJ = intellij;
}

async function mvnImportSelected() {
  const poms = window._mvnDiscoveredPoms || [];
  const intellij = window._mvnDiscoveredIntelliJ || [];
  const toImport = [];

  // IntelliJ Configs
  document.querySelectorAll('.mvn-import-intellij:checked').forEach(cb => {
    const idx = parseInt(cb.dataset.idx);
    const c = intellij[idx];
    if (c && !c.already_imported) {
      toImport.push({
        name: c.name,
        pom_path: c.pom_path,
        goals: c.goals || 'clean install',
        profiles: c.profiles || [],
        skip_tests: c.skip_tests || false,
        jvm_args: c.jvm_args || '',
        description: `Importiert aus IntelliJ`
      });
    }
  });

  // pom.xml Projekte
  document.querySelectorAll('.mvn-import-pom:checked').forEach(cb => {
    const idx = parseInt(cb.dataset.idx);
    const p = poms[idx];
    if (p && !p.already_imported) {
      toImport.push({
        name: p.name || p.artifact_id,
        pom_path: p.pom_path,
        goals: p.suggested_goals || 'clean install',
        profiles: [],
        skip_tests: false,
        jvm_args: '',
        description: `${p.group_id}:${p.artifact_id}`
      });
    }
  });

  if (!toImport.length) { updateSettingsStatus('Keine Builds ausgewählt', 'error'); return; }

  try {
    const res = await fetch('/api/maven/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ builds: toImport })
    });
    const data = await res.json();

    if (data.errors?.length) {
      log.warn('Maven Import Fehler:', data.errors);
      const errMsg = data.errors.map(e => `${e.name}: ${e.error}`).join(', ');
      if (data.imported_count > 0) {
        updateSettingsStatus(`${data.imported_count} importiert, Fehler: ${errMsg}`, 'warning');
      } else {
        updateSettingsStatus(`Import fehlgeschlagen: ${errMsg}`, 'error');
      }
    } else {
      updateSettingsStatus(`${data.imported_count} Builds importiert ✓`, 'success');
    }
    document.getElementById('mvn-discover-results').innerHTML = '';
    await mvnLoadBuilds();
  } catch (e) {
    log.error('Maven Import Fehler:', e);
    updateSettingsStatus('Import fehlgeschlagen: ' + e.message, 'error');
  }
}

async function mvnLoadBuilds() {
  const res = await fetch('/api/maven/builds');
  const data = await res.json();

  const list = document.getElementById('mvn-builds-list');
  if (!list) return;
  list.innerHTML = !data.builds?.length ? '<p class="empty-hint">Keine Builds konfiguriert.</p>' :
    data.builds.map(b => `
      <div class="ds-item">
        <div class="ds-item-header">
          <div>
            <span class="ds-item-name">${escapeHtml(b.name)}</span>
            ${data.running?.includes(b.id) ? '<span class="badge badge-success">läuft</span>' : ''}
          </div>
          <div class="ds-item-actions">
            <button class="btn btn-xs btn-primary" onclick="mvnRunBuild('${b.id}')">&#9654; Build</button>
            <button class="btn btn-xs btn-warning" onclick="mvnStopBuild('${b.id}')">&#9646;&#9646;</button>
            <button class="btn btn-xs btn-danger" onclick="mvnDeleteBuild('${b.id}')">&#128465;</button>
          </div>
        </div>
        <div class="ds-item-detail">
          <code>${escapeHtml(b.pom_path)}</code> | <span class="ds-detail-label">Goals:</span> <code>${escapeHtml(b.goals)}</code>
          ${b.profiles?.length ? `| Profile: ${b.profiles.join(',')}` : ''}
          ${b.skip_tests ? '| <span class="badge">skip-tests</span>' : ''}
        </div>
        ${b.description ? `<div class="ds-item-desc">${escapeHtml(b.description)}</div>` : ''}
      </div>
    `).join('');
}

async function mvnDetectPoms() {
  const res = await fetch('/api/maven/detect');
  const data = await res.json();
  const el = document.getElementById('mvn-pom-detect-result');
  if (!el) return;
  if (!data.found?.length) { el.innerHTML = '<p class="empty-hint">Keine pom.xml im aktiven Repo gefunden.</p>'; return; }
  el.innerHTML = data.found.map(p => `
    <div class="ls-server-row">
      <code>${escapeHtml(p.relative)}</code>
      <button class="btn btn-xs btn-secondary" onclick="document.getElementById('mvn-new-pom').value='${escapeHtml(p.path)}'">Übernehmen</button>
    </div>
  `).join('');
}

async function mvnAddBuild() {
  const profiles = (document.getElementById('mvn-new-profiles').value || '').split(',').map(s=>s.trim()).filter(Boolean);
  const body = {
    name: document.getElementById('mvn-new-name').value,
    description: document.getElementById('mvn-new-desc').value,
    pom_path: document.getElementById('mvn-new-pom').value,
    goals: document.getElementById('mvn-new-goals').value || 'clean install',
    profiles,
    skip_tests: document.getElementById('mvn-new-skip-tests').checked,
    jvm_args: document.getElementById('mvn-new-jvm').value,
  };
  const res = await fetch('/api/maven/builds', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (!res.ok) { updateSettingsStatus('Fehler', 'error'); return; }
  updateSettingsStatus('Build hinzugefügt ✓', 'success');
  await mvnLoadBuilds();
}

async function mvnDeleteBuild(id) {
  if (!confirm('Build löschen?')) return;
  await fetch(`/api/maven/builds/${id}`, { method: 'DELETE' });
  await mvnLoadBuilds();
}

// ══════════════════════════════════════════════════════════════════════════════
// Operative Panels (rechte Sidebar)
// ══════════════════════════════════════════════════════════════════════════════

// ── MQ Panel ──────────────────────────────────────────────────────────────────

async function loadMQPanel() {
  const content = document.getElementById('mq-panel-content');
  if (!content) return;
  try {
    const res = await fetch('/api/mq/queues');
    const data = await res.json();
    if (!data.queues?.length) {
      content.innerHTML = '<div class="empty-state"><span>&#128233;</span><p>Keine Queues konfiguriert</p></div>';
      return;
    }
    content.innerHTML = data.queues.map(q => `
      <div class="tool-card">
        <div class="tool-card-header">
          <span class="tool-card-name">${escapeHtml(q.name)}</span>
          <span class="tool-card-badge">${q.role}</span>
        </div>
        <div class="tool-card-desc">${escapeHtml(q.service || q.description || '')}</div>
        <div class="tool-card-actions">
          ${q.method === 'GET' || q.role !== 'trigger' ? `<button class="btn btn-xs btn-secondary" onclick="mqPanelGet('${q.id}','${escapeHtml(q.name)}')">&#128229; Lesen</button>` : ''}
          ${q.method !== 'GET' || q.role !== 'read' ? `<button class="btn btn-xs btn-primary" onclick="mqPanelPutDialog('${q.id}','${escapeHtml(q.name)}','${escapeHtml(q.body_template||'')}')">&#128228; Einspielen</button>` : ''}
        </div>
        <div id="mq-result-${q.id}" class="tool-result" style="display:none"></div>
      </div>
    `).join('');
  } catch (e) {
    content.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

async function mqPanelGet(queueId, queueName) {
  const resultEl = document.getElementById(`mq-result-${queueId}`);
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<span class="spinner-inline"></span> Lese...';
  try {
    const res = await fetch(`/api/mq/queues/${queueId}/get`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    const data = await res.json();
    resultEl.innerHTML = `<span class="${data.ok ? 'ok' : 'err'}">HTTP ${data.status_code}</span><pre>${escapeHtml(JSON.stringify(data.body, null, 2).substring(0,500))}</pre>`;
  } catch (e) {
    resultEl.innerHTML = `<span class="err">Fehler: ${e.message}</span>`;
  }
}

function mqPanelPutDialog(queueId, queueName, bodyTemplate) {
  const userBody = prompt(`Nachricht für Queue "${queueName}" einspielen:\n${bodyTemplate ? 'Template: ' + bodyTemplate.substring(0,100) : ''}`, bodyTemplate || '{}');
  if (userBody === null) return;
  mqPanelPut(queueId, userBody);
}

async function mqPanelPut(queueId, body) {
  const resultEl = document.getElementById(`mq-result-${queueId}`);
  if (!resultEl) return;
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<span class="spinner-inline"></span> Sende...';
  try {
    const res = await fetch(`/api/mq/queues/${queueId}/put`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ body }),
    });
    const data = await res.json();
    resultEl.innerHTML = `<span class="${data.ok ? 'ok' : 'err'}">HTTP ${data.status_code}</span><pre>${escapeHtml(JSON.stringify(data.body, null, 2).substring(0,300))}</pre>`;
  } catch (e) {
    resultEl.innerHTML = `<span class="err">Fehler: ${e.message}</span>`;
  }
}

// ── Test-Tool Panel (SOAP) ────────────────────────────────────────────────────

let _ttCurrentInstitut = null;

async function loadTestToolPanel() {
  try {
    const [instRes, svcRes, sessRes, cfgRes] = await Promise.all([
      fetch('/api/testtool/institute'),
      fetch('/api/testtool/services'),
      fetch('/api/testtool/sessions'),
      fetch('/api/testtool/config'),
    ]);
    const instData = await instRes.json();
    const svcData = await svcRes.json();
    const sessData = sessRes.ok ? await sessRes.json() : { sessions: {} };
    const cfgData = cfgRes.ok ? await cfgRes.json() : {};

    // Institut-Dropdown
    const instSelect = document.getElementById('testtool-institut-select');
    if (instSelect) {
      const institutes = (instData.institute || []).filter(i => i.enabled);
      if (institutes.length === 0) {
        instSelect.innerHTML = '<option value="">Keine Institute konfiguriert</option>';
        _ttCurrentInstitut = null;
      } else {
        instSelect.innerHTML = institutes.map(i =>
          `<option value="${i.institut_nr}">${escapeHtml(i.name || i.institut_nr)}</option>`
        ).join('');
        _ttCurrentInstitut = institutes[0]?.institut_nr || null;
      }
    }

    // Session-Status aktualisieren (mit Config-Info)
    updateInstitutSessionStatus(sessData.sessions || {}, cfgData);

    // Services anzeigen
    const content = document.getElementById('testtool-services-content');
    if (!content) return;

    const services = svcData.services || [];
    if (services.length === 0) {
      content.innerHTML = '<div class="empty-state"><span>&#128296;</span><p>Konfiguriere Services in den Einstellungen</p></div>';
      return;
    }

    content.innerHTML = services.map(svc => {
      const operations = svc.operations || [];
      return `
        <div class="tool-card">
          <div class="tool-card-header">
            <span class="tool-card-name">${escapeHtml(svc.name)}</span>
            <span class="tool-card-badge">SOAP</span>
          </div>
          ${svc.description ? `<div class="tool-card-desc">${escapeHtml(svc.description)}</div>` : ''}
          ${operations.length ? operations.map(op => `
            <div class="tool-operation" style="margin:6px 0;padding:6px;background:var(--bg-tertiary);border-radius:4px">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span style="font-size:12px;font-weight:500">${escapeHtml(op.name)}</span>
                <button class="btn btn-xs btn-primary" onclick="ttSoapExecute('${svc.id}','${op.id}')">&#9654;</button>
              </div>
              ${op.parameters?.length ? `
                <div class="tool-params" style="margin-top:6px">
                  ${op.parameters.map(p => `
                    <div class="param-row">
                      <label class="param-label" style="font-size:11px">${escapeHtml(p.name)}${p.required?'*':''}</label>
                      <input type="text" class="param-input" id="p-${svc.id}-${op.id}-${p.name}"
                             placeholder="${escapeHtml(p.default_value || p.type || '')}"
                             data-svc="${svc.id}" data-op="${op.id}" data-param="${p.name}"
                             style="font-size:11px;padding:4px">
                    </div>
                  `).join('')}
                </div>
              ` : ''}
            </div>
          `).join('') : '<div style="font-size:11px;color:var(--text-muted);padding:4px">Keine Operationen</div>'}
        </div>
      `;
    }).join('');
  } catch (e) {
    const content = document.getElementById('testtool-services-content');
    if (content) content.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

function updateInstitutSessionStatus(sessions, config = null) {
  const statusDiv = document.getElementById('testtool-session-status');
  if (!statusDiv) return;

  if (!_ttCurrentInstitut) {
    statusDiv.innerHTML = '<span class="badge">Kein Institut</span>';
    return;
  }

  // Prüfe ob Login möglich ist
  if (config && !config.login_url) {
    statusDiv.innerHTML = `<span class="badge badge-error">&#9888; Login-URL fehlt</span>
      <small style="display:block;margin-top:4px;color:var(--text-muted)">Konfiguriere die Login-URL in Einstellungen > Test-Tool</small>`;
    return;
  }

  const session = sessions[_ttCurrentInstitut];
  if (session?.has_token && !session.is_expired) {
    statusDiv.innerHTML = `<span class="badge badge-success">&#10003; Session aktiv</span>
      <small style="display:block;margin-top:4px;color:var(--text-muted)">User: ${escapeHtml(session.user || '-')}</small>`;
  } else if (session?.has_token && session.is_expired) {
    statusDiv.innerHTML = `<span class="badge badge-warning">&#9888; Session abgelaufen</span>
      <button class="btn btn-xs btn-secondary" style="margin-left:8px" onclick="ttSoapLogin()">Erneuern</button>`;
  } else {
    statusDiv.innerHTML = `<span class="badge badge-warning">&#9888; Keine Session</span>
      <button class="btn btn-xs btn-secondary" style="margin-left:8px" onclick="ttSoapLogin()">Login</button>`;
  }
}

async function onInstitutChange(institutNr) {
  _ttCurrentInstitut = institutNr;
  try {
    const [sessRes, cfgRes] = await Promise.all([
      fetch('/api/testtool/sessions'),
      fetch('/api/testtool/config'),
    ]);
    const sessData = await sessRes.json();
    const cfgData = cfgRes.ok ? await cfgRes.json() : {};
    updateInstitutSessionStatus(sessData.sessions || {}, cfgData);
  } catch (e) {
    updateInstitutSessionStatus({});
  }
}

async function ttSoapLogin() {
  if (!_ttCurrentInstitut) return;
  const statusDiv = document.getElementById('testtool-session-status');
  if (statusDiv) statusDiv.innerHTML = '<span class="badge">&#8987; Login...</span>';
  try {
    const res = await fetch(`/api/testtool/session/${_ttCurrentInstitut}/login`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      statusDiv.innerHTML = '<span class="badge badge-success">&#10003; Session aktiv</span>';
    } else {
      // Handle both error formats: {error: "..."} and {detail: "..."} (FastAPI HTTPException)
      const errorMsg = data.detail || data.error || 'Login fehlgeschlagen';
      statusDiv.innerHTML = `<span class="badge badge-error">&#10007; ${escapeHtml(errorMsg)}</span>
        <button class="btn btn-xs btn-secondary" style="margin-left:8px" onclick="ttSoapLogin()">Retry</button>`;
    }
  } catch (e) {
    statusDiv.innerHTML = `<span class="badge badge-error">&#10007; ${escapeHtml(e.message)}</span>
      <button class="btn btn-xs btn-secondary" style="margin-left:8px" onclick="ttSoapLogin()">Retry</button>`;
  }
}

function _collectSoapParams(svcId, opId) {
  const params = {};
  document.querySelectorAll(`[data-svc="${svcId}"][data-op="${opId}"]`).forEach(el => {
    if (el.value) params[el.dataset.param] = el.value;
  });
  return params;
}

async function ttSoapExecute(svcId, opId) {
  const params = _collectSoapParams(svcId, opId);
  const resultArea = document.getElementById('testtool-result-area');
  const resultPre = document.getElementById('testtool-result-pre');
  const badge = document.getElementById('testtool-status-badge');

  resultArea.style.display = 'block';
  resultPre.textContent = '⏳ SOAP-Aufruf...';
  badge.textContent = '';
  badge.className = 'badge';

  try {
    const res = await fetch(`/api/testtool/execute/${svcId}/${opId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        institut_nr: _ttCurrentInstitut,
        params: params
      }),
    });
    const data = await res.json();

    if (data.success) {
      badge.textContent = `HTTP ${data.status_code || 200}`;
      badge.className = 'badge badge-success';
      // Zeige Response-Data
      const dataStr = typeof data.data === 'string' ? data.data : JSON.stringify(data.data, null, 2);
      resultPre.textContent = dataStr.substring(0, 5000);
      if (data.elapsed_ms) {
        resultPre.textContent += `\n\n[${data.elapsed_ms}ms | Institut: ${data.institut_nr}]`;
      }
    } else {
      badge.textContent = data.status_code ? `HTTP ${data.status_code}` : 'Fehler';
      badge.className = 'badge badge-error';
      let errText = data.error || data.fault_message || 'Unbekannter Fehler';
      if (data.fault_code) errText = `[${data.fault_code}] ${errText}`;
      resultPre.textContent = errText;
      if (data.response_xml) {
        resultPre.textContent += '\n\n--- Response XML ---\n' + data.response_xml;
      }
    }
  } catch (e) {
    badge.textContent = 'Fehler';
    badge.className = 'badge badge-error';
    resultPre.textContent = `Fehler: ${e.message}`;
  }
}

// ── WLP Panel ─────────────────────────────────────────────────────────────────

// Polling für WLP-Status (aktualisiert Panel automatisch wenn Server gestartet wird)
let _wlpPollInterval = null;
let _wlpLastRunningState = null;

function startWLPPolling() {
  if (_wlpPollInterval) return; // Bereits aktiv
  log.info('[WLP] Starting status polling');

  _wlpPollInterval = setInterval(async () => {
    // Nur pollen wenn WLP-Panel sichtbar ist
    const panel = document.getElementById('wlp-panel');
    if (!panel || !panel.classList.contains('active')) {
      return;
    }

    try {
      const res = await fetch('/api/wlp/servers');
      const data = await res.json();
      const currentRunning = JSON.stringify(data.running || []);

      // Nur aktualisieren wenn sich Status geändert hat
      if (_wlpLastRunningState !== currentRunning) {
        log.info('[WLP] Status changed, refreshing panel');
        _wlpLastRunningState = currentRunning;
        await loadWLPPanel();
      }
    } catch (e) {
      log.warn('[WLP] Poll error:', e);
    }
  }, 3000); // Alle 3 Sekunden prüfen
}

function stopWLPPolling() {
  if (_wlpPollInterval) {
    clearInterval(_wlpPollInterval);
    _wlpPollInterval = null;
    log.info('[WLP] Stopped status polling');
  }
}

// Polling starten wenn App lädt (stoppt automatisch wenn Panel nicht sichtbar)
document.addEventListener('DOMContentLoaded', () => {
  startWLPPolling();
});

async function loadWLPPanel() {
  const content = document.getElementById('wlp-servers-content');
  if (!content) return;
  try {
    const res = await fetch('/api/wlp/servers');
    const data = await res.json();
    if (!data.servers?.length) {
      content.innerHTML = '<div class="empty-state"><span>&#9881;</span><p>Keine WLP-Server konfiguriert</p></div>';
      return;
    }
    content.innerHTML = data.servers.map(s => `
      <div class="tool-card">
        <div class="tool-card-header">
          <span class="tool-card-name">${escapeHtml(s.name)}</span>
          ${data.running?.includes(s.id) ? '<span class="badge badge-success">läuft</span>' : '<span class="badge">gestoppt</span>'}
        </div>
        <div class="tool-card-desc"><code>${escapeHtml(s.server_name)}</code> in <code>${escapeHtml(s.wlp_path)}</code></div>
        <div class="tool-card-actions">
          <button class="btn btn-xs btn-secondary" onclick="wlpPanelValidate('${s.id}')">&#10003; Prüfen</button>
          <button class="btn btn-xs btn-primary" onclick="wlpPanelStart('${s.id}')">&#9654; Start</button>
          <button class="btn btn-xs btn-warning" onclick="wlpPanelStop('${s.id}')">&#9646;&#9646; Stop</button>
          <button class="btn btn-xs btn-secondary" onclick="wlpPanelLogs('${s.id}')">&#128196; Logs</button>
        </div>
        <div id="wlp-card-result-${s.id}" class="tool-result" style="display:none"></div>
      </div>
    `).join('');
  } catch (e) {
    content.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

async function wlpPanelValidate(id) {
  const el = document.getElementById(`wlp-card-result-${id}`);
  el.style.display = 'block';
  el.innerHTML = '<span class="spinner-inline"></span> Prüfe server.xml...';
  const res = await fetch(`/api/wlp/servers/${id}/validate`, { method: 'POST' });
  const data = await res.json();
  if (!data.valid) { el.innerHTML = `<span class="err">✗ ${escapeHtml(data.error)}</span>`; return; }
  const ok = data.all_artifacts_present;
  el.innerHTML = `
    <span class="${ok ? 'ok' : 'warn'}">${ok ? '✓' : '⚠'} server.xml valide</span>
    ${data.applications.map(a => `<div><code>${escapeHtml(a.name||a.tag)}</code> → <code>${escapeHtml(a.artifact_path||'?')}</code> ${a.artifact_exists ? '✓' : '<span class="err">fehlt!</span>'}</div>`).join('')}
    ${data.built_artifact ? `<div class="ok">Gebaut: <code>${escapeHtml(data.built_artifact.path)}</code> (${data.built_artifact.size_kb}KB)</div>` : '<div class="warn">Kein gebautes Artefakt im Repo</div>'}
  `;
}

function wlpPanelStart(id) {
  log.info('[WLP] wlpPanelStart called with id:', id);
  const logArea = document.getElementById('wlp-log-area');
  const logOutput = document.getElementById('wlp-log-output');
  if (!logArea || !logOutput) {
    log.error('[WLP] Log elements not found!', { logArea, logOutput });
    return;
  }
  logArea.style.display = 'block';
  logOutput.textContent = '⏳ Starte Server...\n';
  _streamWLPServer(id, 'start', logOutput);
}

async function wlpPanelStop(id) {
  const res = await fetch(`/api/wlp/servers/${id}/stop`, { method: 'POST' });
  const data = await res.json();
  const logOutput = document.getElementById('wlp-log-output');
  if (logOutput) logOutput.textContent += `\n[Stop: ${data.success ? 'OK' : data.error}]\n${data.output||''}`;
  loadWLPPanel();
}

async function wlpPanelLogs(id) {
  const logArea = document.getElementById('wlp-log-area');
  const logOutput = document.getElementById('wlp-log-output');
  logArea.style.display = 'block';
  logOutput.textContent = '⏳ Lade Logs...';
  const res = await fetch(`/api/wlp/servers/${id}/logs?lines=200`);
  const data = await res.json();
  logOutput.textContent = data.found ? data.lines.join('\n') : `Keine messages.log gefunden: ${data.log_path}`;
  logOutput.scrollTop = logOutput.scrollHeight;
}

// Alias-Funktionen für WLP (Button-Callbacks in wlpLoadList)
function wlpStart(id) {
  log.info('[WLP] wlpStart called from Settings, id:', id);
  switchRightPanel('wlp-panel');
  loadWLPPanel(); // Panel-Inhalt aktualisieren
  wlpPanelStart(id);
}

async function wlpStop(id) {
  log.info('[WLP] wlpStop called from Settings, id:', id);
  await wlpPanelStop(id);
}

async function _streamWLPServer(id, action, outputEl) {
  log.info('[WLP] _streamWLPServer called:', { id, action });
  try {
    const res = await fetch(`/api/wlp/servers/${id}/${action}`, { method: 'POST' });
    log.info('[WLP] Fetch response:', res.status, res.statusText);

    // Fehlerprüfung
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errData = await res.json();
        errorMsg = errData.detail || errorMsg;
      } catch (_) {}
      outputEl.textContent += `\n❌ Fehler: ${errorMsg}`;
      log.error('[WLP] API Error:', errorMsg);
      return;
    }

    if (!res.body) {
      outputEl.textContent += '\n❌ Keine Streaming-Antwort vom Server';
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(line.slice(5).trim());
          log.info('[WLP] Event:', ev.type);
          if (ev.type === 'start') {
            const span = document.createElement('div');
            span.className = 'log-info';
            span.textContent = `$ ${ev.cmd} (PID: ${ev.pid})`;
            outputEl.appendChild(span);
          } else if (ev.type === 'output') {
            const span = document.createElement('div');
            span.className = ev.is_error ? 'log-error' : (ev.is_ready ? 'log-ready' : '');
            span.textContent = ev.line;
            outputEl.appendChild(span);
            outputEl.scrollTop = outputEl.scrollHeight;
          } else if (ev.type === 'ready') {
            const span = document.createElement('div');
            span.className = 'log-ready';
            span.textContent = '✓ Server bereit!';
            outputEl.appendChild(span);
          } else if (ev.type === 'warning') {
            const span = document.createElement('div');
            span.className = 'log-warn';
            span.textContent = '⚠ ' + ev.message;
            outputEl.appendChild(span);
          } else if (ev.type === 'error') {
            const span = document.createElement('div');
            span.className = 'log-error';
            span.textContent = '❌ ' + ev.message;
            outputEl.appendChild(span);
            // Traceback anzeigen wenn vorhanden
            if (ev.traceback) {
              const tbDiv = document.createElement('pre');
              tbDiv.className = 'log-traceback';
              tbDiv.style.cssText = 'font-size:11px;color:#ff6b6b;margin:4px 0;white-space:pre-wrap;';
              tbDiv.textContent = ev.traceback;
              outputEl.appendChild(tbDiv);
            }
          } else if (ev.type === 'done') {
            const span = document.createElement('div');
            span.className = ev.exit_code === 0 ? 'log-ready' : 'log-error';
            span.textContent = `[Exit: ${ev.exit_code}]`;
            outputEl.appendChild(span);
            loadWLPPanel();
          }
        } catch (parseErr) {
          log.warn('[WLP] Parse error:', parseErr, line);
        }
      }
    }
  } catch (e) {
    log.error('[WLP] Stream error:', e);
    outputEl.textContent += `\n❌ Fehler: ${e.message}`;
  }
}

// ── Maven Panel ───────────────────────────────────────────────────────────────

async function loadMavenPanel() {
  const content = document.getElementById('maven-builds-content');
  if (!content) return;
  try {
    const res = await fetch('/api/maven/builds');
    const data = await res.json();
    if (!data.builds?.length) {
      content.innerHTML = '<div class="empty-state"><span>&#128736;</span><p>Keine Builds konfiguriert</p></div>';
      return;
    }
    content.innerHTML = data.builds.map(b => `
      <div class="tool-card">
        <div class="tool-card-header">
          <span class="tool-card-name">${escapeHtml(b.name)}</span>
          ${data.running?.includes(b.id) ? '<span class="badge badge-success">läuft</span>' : ''}
        </div>
        <div class="tool-card-desc"><code>${escapeHtml(b.goals)}</code>${b.description ? ' – ' + escapeHtml(b.description) : ''}</div>
        <div class="tool-card-actions">
          <button class="btn btn-xs btn-primary" onclick="mvnPanelRun('${b.id}')">&#9654; Build</button>
          <button class="btn btn-xs btn-warning" onclick="mvnStopBuild('${b.id}')">&#9646;&#9646; Stop</button>
        </div>
        <div id="mvn-card-result-${b.id}" class="tool-result" style="display:none"></div>
      </div>
    `).join('');
  } catch (e) {
    content.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

function mvnPanelRun(buildId) {
  log.info('[Maven] mvnPanelRun called with buildId:', buildId);
  const logArea = document.getElementById('maven-log-area');
  const logOutput = document.getElementById('maven-log-output');
  if (!logArea || !logOutput) {
    log.error('[Maven] Log elements not found!', { logArea, logOutput });
    return;
  }
  logArea.style.display = 'block';
  logOutput.textContent = '⏳ Starte Maven Build...\n';
  _streamMavenBuild(buildId, logOutput);
}

async function mvnStopBuild(buildId) {
  const res = await fetch(`/api/maven/builds/${buildId}/stop`, { method: 'POST' });
  const data = await res.json();
  const logOutput = document.getElementById('maven-log-output');
  if (logOutput) logOutput.textContent += `\n[${data.message}]`;
  loadMavenPanel();
}

async function mvnRunBuild(buildId) {
  log.info('[Maven] mvnRunBuild called from Settings, buildId:', buildId);
  switchRightPanel('maven-panel');
  loadMavenPanel(); // Panel-Inhalt aktualisieren
  mvnPanelRun(buildId);
}

async function _streamMavenBuild(buildId, outputEl) {
  log.info('[Maven] _streamMavenBuild called:', buildId);
  try {
    const res = await fetch(`/api/maven/builds/${buildId}/run`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    log.info('[Maven] Fetch response:', res.status, res.statusText);

    // Fehlerprüfung
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errData = await res.json();
        errorMsg = errData.detail || errorMsg;
      } catch (_) {}
      outputEl.textContent += `\n❌ Fehler: ${errorMsg}`;
      log.error('[Maven] API Error:', errorMsg);
      return;
    }

    if (!res.body) {
      outputEl.textContent += '\n❌ Keine Streaming-Antwort vom Server';
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(line.slice(5).trim());
          log.info('[Maven] Event:', ev.type);
          if (ev.type === 'output') {
            const div = document.createElement('div');
            div.className = ev.is_error ? 'log-error' : (ev.is_success ? 'log-ready' : (ev.is_warning ? 'log-warn' : ''));
            div.textContent = ev.line;
            outputEl.appendChild(div);
            outputEl.scrollTop = outputEl.scrollHeight;
          } else if (ev.type === 'done') {
            const div = document.createElement('div');
            div.className = ev.success ? 'log-ready' : 'log-error';
            div.textContent = ev.success ? '✓ BUILD SUCCESS' : `✗ BUILD FAILURE (Exit: ${ev.exit_code})`;
            outputEl.appendChild(div);
            loadMavenPanel();
          } else if (ev.type === 'start') {
            const div = document.createElement('div');
            div.className = 'log-info';
            div.textContent = `$ ${ev.cmd}`;
            outputEl.appendChild(div);
          } else if (ev.type === 'error') {
            const div = document.createElement('div');
            div.className = 'log-error';
            div.textContent = '❌ ' + ev.message;
            outputEl.appendChild(div);
            // Traceback anzeigen wenn vorhanden
            if (ev.traceback) {
              const tbDiv = document.createElement('pre');
              tbDiv.className = 'log-traceback';
              tbDiv.style.cssText = 'font-size:11px;color:#ff6b6b;margin:4px 0;white-space:pre-wrap;';
              tbDiv.textContent = ev.traceback;
              outputEl.appendChild(tbDiv);
            }
          }
        } catch (parseErr) {
          log.warn('[Maven] Parse error:', parseErr, line);
        }
      }
    }
  } catch (e) {
    log.error('[Maven] Stream error:', e);
    outputEl.textContent += `\n❌ Fehler: ${e.message}`;
  }
}

// ── Auto-load operative panels when tab switches ──────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Panel-Tab-Klick-Handler für operative Panels
  document.querySelectorAll('[data-panel]').forEach(tab => {
    const panel = tab.getAttribute('data-panel');
    if (['mq-panel','testtool-panel','wlp-panel','maven-panel','websearch-panel'].includes(panel)) {
      tab.addEventListener('click', () => {
        if (panel === 'mq-panel') loadMQPanel();
        else if (panel === 'testtool-panel') loadTestToolPanel();
        else if (panel === 'wlp-panel') loadWLPPanel();
        else if (panel === 'maven-panel') loadMavenPanel();
        else if (panel === 'websearch-panel') loadSearchPanel();
      });
    }
  });

  // Web-Such-Polling starten (alle 4 Sekunden auf ausstehende Anfragen prüfen)
  startSearchPolling();
});

// ══════════════════════════════════════════════════════════════════════════════
// Web-Suche
// ══════════════════════════════════════════════════════════════════════════════

let _searchPollInterval = null;
let _searchConfirmInProgress = {};  // Track ongoing confirms to prevent double-clicks

function startSearchPolling() {
  if (_searchPollInterval) return;
  // Schnelleres Polling (2s statt 4s) für bessere Responsivität
  _searchPollInterval = setInterval(_pollPendingSearches, 2000);
  _pollPendingSearches(); // Sofort einmal prüfen
}

async function _pollPendingSearches() {
  try {
    const res = await fetch('/api/search/pending');
    if (!res.ok) return;
    const data = await res.json();
    const pending = data.pending || [];
    if (pending.length > 0) {
      log.info('[search] Pending searches:', pending.map(p => ({ id: p.id, status: p.status, query: p.query?.substring(0, 30) })));
    }

    // Badge in Sidebar-Tab aktualisieren
    const badge = document.getElementById('search-pending-badge');
    if (badge) {
      badge.textContent = pending.length;
      badge.style.display = pending.length ? 'inline' : 'none';
    }

    // Badge in "Bestätigung"-Tab
    const confirmBadge = document.getElementById('pending-count');
    if (confirmBadge) {
      const hasPlan = document.getElementById('pending-confirmation')?.style.display !== 'none';
      const total = (hasPlan ? 1 : 0) + pending.length;
      confirmBadge.textContent = total;
      confirmBadge.style.display = total ? 'inline' : 'none';
    }

    // Ausstehende Suchen in confirm-panel rendern
    const searchesDiv = document.getElementById('pending-searches');
    const searchesList = document.getElementById('pending-searches-list');
    const noConfirm = document.getElementById('no-confirmation');

    if (!searchesDiv || !searchesList) return;

    if (pending.length === 0) {
      searchesDiv.style.display = 'none';
      if (noConfirm && document.getElementById('pending-confirmation')?.style.display === 'none') {
        noConfirm.style.display = 'flex';
      }
      return;
    }

    if (noConfirm) noConfirm.style.display = 'none';
    searchesDiv.style.display = 'block';

    // Auto-Switch zu Confirm-Panel wenn Suchen ausstehen
    const confirmPanel = document.getElementById('confirm-panel');
    if (confirmPanel && !confirmPanel.classList.contains('active')) {
      // Zum Confirm-Panel wechseln um Buttons anzuzeigen
      // FIX: Nur rechte Sidebar betreffen, nicht die linke (Chat-Liste)
      document.querySelectorAll('#sidebar-right .sidebar-panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('#sidebar-right .sidebar-tabs-compact button').forEach(b => b.classList.remove('active'));
      confirmPanel.classList.add('active');
      const confirmTab = document.querySelector('#sidebar-right [data-panel="confirm-panel"]');
      if (confirmTab) confirmTab.classList.add('active');
    }
    searchesList.innerHTML = pending.map(item => {
      const isExecuting = item.status === 'executing';
      return `
      <div class="search-confirm-card" id="sc-${item.id}">
        <div style="font-size:12px;font-weight:600;margin-bottom:4px">&#128269; Agent möchte suchen:</div>
        <div class="sc-query">${escapeHtml(item.query)}</div>
        ${item.reason ? `<div class="sc-reason">Grund: ${escapeHtml(item.reason)}</div>` : ''}
        <div class="sc-actions">
          ${isExecuting
            ? '<div class="spinner-inline"></div> <span style="color:var(--text-secondary)">Suche läuft...</span>'
            : `<button class="btn btn-xs btn-success" onclick="searchConfirm('${item.id}')">&#10003; Bestätigen</button>
               <button class="btn btn-xs btn-danger" onclick="searchReject('${item.id}')">&#10005; Ablehnen</button>`
          }
        </div>
      </div>
    `;
    }).join('');
  } catch (e) {
    // Kein Netz oder Server nicht erreichbar – still ignorieren
  }
}

async function searchConfirm(searchId) {
  // Verhindere Doppelklicks
  if (_searchConfirmInProgress[searchId]) {
    log.info('[search] Confirm already in progress for', searchId);
    return;
  }
  _searchConfirmInProgress[searchId] = true;

  const card = document.getElementById(`sc-${searchId}`);
  if (card) card.innerHTML = '<div class="spinner-inline"></div> Suche wird ausgeführt...';

  try {
    const res = await fetch(`/api/search/confirm/${searchId}`, { method: 'POST' });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    log.info('[search] Confirm response:', data);
    // Kurz warten damit der Agent-Poll die Änderung sieht
    await new Promise(r => setTimeout(r, 300));
    await _pollPendingSearches();
    loadSearchPanel(); // History aktualisieren
  } catch (e) {
    log.error('[search] Confirm error:', e);
    if (card) card.innerHTML = `<span class="badge badge-error">Fehler: ${e.message}</span>`;
  } finally {
    delete _searchConfirmInProgress[searchId];
  }
}

async function searchReject(searchId) {
  await fetch(`/api/search/cancel/${searchId}`, { method: 'DELETE' });
  await _pollPendingSearches();
}

async function searchToggle(enabled) {
  await fetch('/api/search/toggle', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  const txt = document.getElementById('search-status-text');
  if (txt) {
    txt.textContent = enabled
      ? 'Aktiviert – Agent kann Internet-Suchen anfragen (Bestätigung erforderlich)'
      : 'Deaktiviert – Agent kann keine Internet-Suchen durchführen';
    txt.style.color = enabled ? 'var(--success)' : 'var(--text-muted)';
  }
}

async function loadSearchPanel() {
  try {
    const [statusRes, histRes] = await Promise.all([
      fetch('/api/search/status'),
      fetch('/api/search/history'),
    ]);
    const status = await statusRes.json();
    const hist = await histRes.json();

    // Toggle-Zustand setzen
    const toggle = document.getElementById('search-enabled-toggle');
    if (toggle) toggle.checked = status.enabled;
    const txt = document.getElementById('search-status-text');
    if (txt) {
      txt.textContent = status.enabled
        ? 'Aktiviert – Agent kann Internet-Suchen anfragen (Bestätigung erforderlich)'
        : 'Deaktiviert – Agent kann keine Internet-Suchen durchführen';
      txt.style.color = status.enabled ? 'var(--success)' : 'var(--text-muted)';
    }

    // History rendern
    const content = document.getElementById('search-history-content');
    if (!content) return;
    const history = hist.history || [];
    if (!history.length) {
      content.innerHTML = '<div class="empty-state"><span>&#128269;</span><p>Noch keine Suchanfragen</p></div>';
      return;
    }
    content.innerHTML = history.map(item => `
      <div style="margin-bottom:12px">
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">
          ${item.executed_at ? new Date(item.executed_at).toLocaleTimeString('de-DE') : ''} –
          <span style="font-family:var(--font-mono)">${escapeHtml(item.query)}</span>
        </div>
        ${(item.results || []).map(r => `
          <div class="search-result-card">
            <div class="sr-title">${escapeHtml(r.title)}</div>
            ${r.snippet ? `<div class="sr-snippet">${escapeHtml(r.snippet.substring(0, 180))}</div>` : ''}
            ${r.url ? `<div class="sr-url"><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.url.substring(0, 60))}...</a></div>` : ''}
          </div>
        `).join('')}
      </div>
    `).join('<hr style="border-color:var(--border);margin:8px 0">');
  } catch (e) {
    const content = document.getElementById('search-history-content');
    if (content) content.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

// ── GitHub Settings Section ──────────────────────────────────────────────────────

async function renderGitHubSection() {
  const cfg = settingsState.settings.github || {};

  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">GITHUB ENTERPRISE</h3>
      <p class="settings-section-desc">
        GitHub Enterprise Server für Repository-, PR- und Issue-Abfragen.
      </p>
    </div>

    <div class="settings-field">
      <label for="github-enabled">Aktiviert</label>
      <label class="checkbox-label">
        <input type="checkbox" id="github-enabled" ${cfg.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.enabled ? 'Aktiviert' : 'Deaktiviert'}
      </label>
    </div>

    <div class="settings-field">
      <label for="github-base-url">Server URL</label>
      <input type="text" id="github-base-url" value="${escapeHtml(cfg.base_url || '')}"
        placeholder="https://github.intern.example.com" onchange="markSettingsModified()" style="font-family:var(--font-mono)">
      <small style="color:var(--text-muted)">API-URL wird automatisch als /api/v3 angehängt</small>
    </div>

    <div class="settings-field">
      <label for="github-token">Personal Access Token</label>
      <input type="password" id="github-token" value="${escapeHtml(cfg.token || '')}"
        placeholder="ghp_xxxxxxxxxxxx" onchange="markSettingsModified()" autocomplete="off">
    </div>

    <div class="settings-field">
      <label for="github-verify-ssl">SSL-Zertifikat prüfen</label>
      <label class="checkbox-label">
        <input type="checkbox" id="github-verify-ssl" ${cfg.verify_ssl ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.verify_ssl ? 'Ja' : 'Nein (für Self-Signed Certs)'}
      </label>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">STANDARD-WERTE</h3>
      <p class="settings-section-desc">
        Diese Werte werden verwendet, wenn der Agent keine expliziten Angaben macht.
      </p>
    </div>

    <div class="settings-field">
      <label for="github-default-org">Standard-Organisation</label>
      <input type="text" id="github-default-org" value="${escapeHtml(cfg.default_org || '')}"
        placeholder="z.B. IT-Networks" onchange="markSettingsModified()">
      <small style="color:var(--text-muted)">Wird für github_list_repos verwendet</small>
    </div>

    <div class="settings-field">
      <label for="github-default-repo">Standard-Repository</label>
      <input type="text" id="github-default-repo" value="${escapeHtml(cfg.default_repo || '')}"
        placeholder="z.B. IT-Networks/AI-Assist" onchange="markSettingsModified()" style="font-family:var(--font-mono)">
      <small style="color:var(--text-muted)">Format: org/repo - für PRs, Issues, Branches</small>
    </div>

    <div class="settings-field">
      <label for="github-timeout">Timeout (Sekunden)</label>
      <input type="number" id="github-timeout" value="${cfg.timeout_seconds || 30}"
        min="5" max="120" onchange="markSettingsModified()">
    </div>

    <div class="settings-field">
      <label for="github-max-items">Max. Einträge pro Liste</label>
      <input type="number" id="github-max-items" value="${cfg.max_items || 50}"
        min="10" max="100" onchange="markSettingsModified()">
    </div>

    <div class="settings-actions-section" style="margin-top:20px">
      <button class="btn btn-secondary" onclick="githubTestConnection()">
        🔌 Verbindung testen
      </button>
      <span id="github-test-result" class="test-result"></span>
    </div>
  `;
}

async function githubTestConnection() {
  const resultEl = document.getElementById('github-test-result');
  resultEl.textContent = '⏳ Teste Verbindung...';
  resultEl.className = 'test-result testing';

  // Erst die aktuellen Werte speichern
  const cfg = {
    enabled: document.getElementById('github-enabled').checked,
    base_url: document.getElementById('github-base-url').value.trim(),
    token: document.getElementById('github-token').value,
    verify_ssl: document.getElementById('github-verify-ssl').checked,
    default_org: document.getElementById('github-default-org').value.trim(),
    default_repo: document.getElementById('github-default-repo').value.trim(),
    timeout_seconds: parseInt(document.getElementById('github-timeout').value) || 30,
    max_items: parseInt(document.getElementById('github-max-items').value) || 50,
  };

  if (!cfg.base_url) {
    resultEl.textContent = '✗ Server URL fehlt';
    resultEl.className = 'test-result error';
    return;
  }

  try {
    // Temporär speichern für den Test
    await fetch('/api/settings/section/github', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });

    const res = await fetch('/api/github/test', { method: 'POST' });
    const data = await res.json();

    if (data.success) {
      let msg = `✓ ${data.message}`;
      if (data.org?.status === 'ok') {
        msg += ` | Org: ${data.org.name} (${data.org.public_repos} Repos)`;
      } else if (data.org?.status === 'error') {
        msg += ` | ⚠ Org-Fehler: ${data.org.error}`;
      }
      resultEl.textContent = msg;
      resultEl.className = 'test-result success';
    } else {
      resultEl.textContent = `✗ ${data.error || 'Verbindung fehlgeschlagen'}`;
      resultEl.className = 'test-result error';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

function collectGitHubSettings() {
  return {
    enabled: document.getElementById('github-enabled')?.checked || false,
    base_url: document.getElementById('github-base-url')?.value?.trim() || '',
    token: document.getElementById('github-token')?.value || '',
    verify_ssl: document.getElementById('github-verify-ssl')?.checked || false,
    default_org: document.getElementById('github-default-org')?.value?.trim() || '',
    default_repo: document.getElementById('github-default-repo')?.value?.trim() || '',
    timeout_seconds: parseInt(document.getElementById('github-timeout')?.value) || 30,
    max_items: parseInt(document.getElementById('github-max-items')?.value) || 50,
  };
}

// ── Internal Fetch Settings Section ─────────────────────────────────────────────

async function renderInternalFetchSection() {
  const cfg = settingsState.settings.internal_fetch || {};
  const baseUrls = (cfg.base_urls || []).join('\n');

  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">INTERNAL FETCH</h3>
      <p class="settings-section-desc">
        Tool zum Abrufen interner/Intranet-URLs. URLs werden gegen die Base URLs validiert (Sicherheit).
      </p>
    </div>

    <div class="settings-field">
      <label for="if-enabled">Aktiviert</label>
      <label class="checkbox-label">
        <input type="checkbox" id="if-enabled" ${cfg.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.enabled ? 'Aktiviert' : 'Deaktiviert'}
      </label>
    </div>

    <div class="settings-field">
      <label for="if-base-urls">Erlaubte Base URLs (optional)</label>
      <textarea id="if-base-urls" rows="3" onchange="markSettingsModified()"
        placeholder="Leer lassen = alle URLs erlaubt"
        style="font-family:var(--font-mono);font-size:13px">${escapeHtml(baseUrls)}</textarea>
      <small style="color:var(--text-muted)">Optional: Eine URL pro Zeile. Leer = alle URLs erlaubt.</small>
    </div>

    <div class="settings-field">
      <label for="if-verify-ssl">SSL-Zertifikat pruefen</label>
      <label class="checkbox-label">
        <input type="checkbox" id="if-verify-ssl" ${cfg.verify_ssl !== false ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.verify_ssl !== false ? 'Ja' : 'Nein (fuer Self-Signed Certs)'}
      </label>
    </div>

    <div class="settings-field">
      <label for="if-timeout">Timeout (Sekunden)</label>
      <input type="number" id="if-timeout" value="${cfg.timeout_seconds || 30}"
        min="5" max="120" onchange="markSettingsModified()">
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">AUTHENTIFIZIERUNG</h3>
      <p class="settings-section-desc">
        Optional: Authentifizierung fuer alle Requests.
      </p>
    </div>

    <div class="settings-field">
      <label for="if-auth-type">Auth-Typ</label>
      <select id="if-auth-type" onchange="internalFetchAuthTypeChanged(); markSettingsModified()">
        <option value="none" ${cfg.auth_type === 'none' || !cfg.auth_type ? 'selected' : ''}>Keine</option>
        <option value="basic" ${cfg.auth_type === 'basic' ? 'selected' : ''}>Basic Auth</option>
        <option value="bearer" ${cfg.auth_type === 'bearer' ? 'selected' : ''}>Bearer Token</option>
      </select>
    </div>

    <div id="if-auth-basic" style="display:${cfg.auth_type === 'basic' ? 'block' : 'none'}">
      <div class="settings-field">
        <label for="if-auth-username">Benutzername</label>
        <input type="text" id="if-auth-username" value="${escapeHtml(cfg.auth_username || '')}"
          placeholder="username" onchange="markSettingsModified()">
      </div>
      <div class="settings-field">
        <label for="if-auth-password">Passwort</label>
        <input type="password" id="if-auth-password" value="${escapeHtml(cfg.auth_password || '')}"
          placeholder="password" onchange="markSettingsModified()" autocomplete="off">
      </div>
    </div>

    <div id="if-auth-bearer" style="display:${cfg.auth_type === 'bearer' ? 'block' : 'none'}">
      <div class="settings-field">
        <label for="if-auth-token">Bearer Token</label>
        <input type="password" id="if-auth-token" value="${escapeHtml(cfg.auth_token || '')}"
          placeholder="eyJhbGc..." onchange="markSettingsModified()" autocomplete="off">
      </div>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">PROXY</h3>
      <p class="settings-section-desc">
        Optional: Proxy fuer interne Requests.
      </p>
    </div>

    <div class="settings-field">
      <label for="if-proxy-url">Proxy URL</label>
      <input type="text" id="if-proxy-url" value="${escapeHtml(cfg.proxy_url || '')}"
        placeholder="http://proxy.intern:8080" onchange="markSettingsModified()" style="font-family:var(--font-mono)">
    </div>

    <div class="settings-actions-section" style="margin-top:20px">
      <button class="btn btn-secondary" onclick="internalFetchTestConnection()">
        🔌 Verbindung testen
      </button>
      <span id="if-test-result" class="test-result"></span>
    </div>
  `;
}

function internalFetchAuthTypeChanged() {
  const authType = document.getElementById('if-auth-type').value;
  document.getElementById('if-auth-basic').style.display = authType === 'basic' ? 'block' : 'none';
  document.getElementById('if-auth-bearer').style.display = authType === 'bearer' ? 'block' : 'none';
}

async function internalFetchTestConnection() {
  const resultEl = document.getElementById('if-test-result');
  resultEl.textContent = '⏳ Teste...';
  resultEl.className = 'test-result testing';

  // Erst die aktuellen Werte speichern
  const cfg = collectInternalFetchSettings();

  try {
    // Temporaer speichern fuer den Test
    await fetch('/api/settings/section/internal_fetch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });

    const res = await fetch('/api/internal-fetch/test', { method: 'POST' });
    const data = await res.json();

    if (data.success) {
      resultEl.textContent = `✓ ${data.message}`;
      resultEl.className = 'test-result success';
    } else {
      resultEl.textContent = `✗ ${data.error || 'Verbindung fehlgeschlagen'}`;
      resultEl.className = 'test-result error';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

function collectInternalFetchSettings() {
  const baseUrlsText = document.getElementById('if-base-urls')?.value || '';
  const baseUrls = baseUrlsText.split('\n').map(u => u.trim()).filter(u => u.length > 0);

  return {
    enabled: document.getElementById('if-enabled')?.checked || false,
    base_urls: baseUrls,
    verify_ssl: document.getElementById('if-verify-ssl')?.checked || false,
    timeout_seconds: parseInt(document.getElementById('if-timeout')?.value) || 30,
    auth_type: document.getElementById('if-auth-type')?.value || 'none',
    auth_username: document.getElementById('if-auth-username')?.value?.trim() || '',
    auth_password: document.getElementById('if-auth-password')?.value || '',
    auth_token: document.getElementById('if-auth-token')?.value || '',
    proxy_url: document.getElementById('if-proxy-url')?.value?.trim() || '',
  };
}

// ── WSL Podman Sandbox Settings Section ───────────────────────────────────────

async function renderDockerSandboxSection() {
  // Runtime-Info und Config laden
  let runtimeInfo = { available: false, runtime: 'none', version: null };
  let cfg = {};

  try {
    const [runtimeRes, configRes] = await Promise.all([
      fetch('/api/docker-sandbox/runtime'),
      fetch('/api/docker-sandbox/config')
    ]);
    if (runtimeRes.ok) runtimeInfo = await runtimeRes.json();
    if (configRes.ok) cfg = await configRes.json();
  } catch (e) {
    log.error('Docker Sandbox config load error:', e);
  }

  // In Settings-State speichern
  settingsState.settings.docker_sandbox = cfg;

  const packages = (cfg.preinstalled_packages || []).join('\n');
  const runtimeBadge = runtimeInfo.available
    ? `<span class="badge badge-success">WSL Podman ${runtimeInfo.version || ''}</span>`
    : `<span class="badge badge-error">Nicht verfuegbar</span>`;

  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">WSL PODMAN SANDBOX</h3>
      <p class="settings-section-desc">
        Sichere Python-Code-Ausfuehrung in isolierten Containern via Podman in WSL2 Ubuntu.
        Die AI kann hier Code ausfuehren ohne das Host-System zu gefaehrden.
      </p>
      <p class="settings-section-desc">
        <strong>Runtime:</strong> ${runtimeBadge}
      </p>
    </div>

    <div class="settings-field">
      <label for="ds-enabled">Aktiviert</label>
      <label class="checkbox-label">
        <input type="checkbox" id="ds-enabled" ${cfg.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.enabled ? 'Aktiviert' : 'Deaktiviert'}
      </label>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">WSL KONFIGURATION</h3>
      <p class="settings-section-desc">
        Podman wird in der WSL2 Ubuntu Distribution ausgefuehrt.
      </p>
    </div>

    <div class="settings-field">
      <label for="ds-wsl-distro">WSL Distribution</label>
      <input type="text" id="ds-wsl-distro" value="${escapeHtml(cfg.wsl_integration?.distro_name || 'Ubuntu')}"
        placeholder="Ubuntu" onchange="markSettingsModified()"
        style="font-family:var(--font-mono);font-size:13px">
      <small style="color:var(--text-muted)">Name der WSL-Distribution (wsl -l zeigt alle)</small>
    </div>

    <div class="settings-field">
      <label for="ds-wsl-podman-path">Podman Pfad in WSL</label>
      <input type="text" id="ds-wsl-podman-path" value="${escapeHtml(cfg.wsl_integration?.podman_path_in_wsl || '/usr/bin/podman')}"
        placeholder="/usr/bin/podman" onchange="markSettingsModified()"
        style="font-family:var(--font-mono);font-size:13px">
    </div>

    <div class="settings-field">
      <label for="ds-wsl-image-path">Interner Image-Pfad (optional)</label>
      <input type="text" id="ds-wsl-image-path" value="${escapeHtml(cfg.wsl_integration?.internal_image_path || '')}"
        placeholder="/mnt/images oder registry.intern:5000" onchange="markSettingsModified()"
        style="font-family:var(--font-mono);font-size:13px">
      <small style="color:var(--text-muted)">Lokaler Pfad oder interne Registry fuer Images (air-gapped)</small>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">CONTAINER IMAGE</h3>
    </div>

    <div class="settings-field">
      <label for="ds-image">Base Image</label>
      <input type="text" id="ds-image" value="${escapeHtml(cfg.image || 'python:3.11-slim')}"
        placeholder="python:3.11-slim" onchange="markSettingsModified()" style="font-family:var(--font-mono)">
    </div>

    <div class="settings-field">
      <label for="ds-custom-image">Custom Image (optional)</label>
      <input type="text" id="ds-custom-image" value="${escapeHtml(cfg.custom_image || '')}"
        placeholder="my-sandbox:latest" onchange="markSettingsModified()"
        style="font-family:var(--font-mono)">
      <small style="color:var(--text-muted)">Eigenes Image mit vorinstallierten Paketen</small>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">RESSOURCEN-LIMITS</h3>
    </div>

    <div class="settings-field">
      <label for="ds-memory">Memory Limit</label>
      <input type="text" id="ds-memory" value="${escapeHtml(cfg.memory_limit || '512m')}"
        placeholder="512m" onchange="markSettingsModified()" style="width:100px">
      <small style="color:var(--text-muted)">z.B. 256m, 512m, 1g</small>
    </div>

    <div class="settings-field">
      <label for="ds-cpu">CPU Limit</label>
      <input type="number" id="ds-cpu" value="${cfg.cpu_limit || 1.0}"
        min="0.1" max="4" step="0.1" onchange="markSettingsModified()" style="width:100px">
      <small style="color:var(--text-muted)">Anzahl CPU-Cores</small>
    </div>

    <div class="settings-field">
      <label for="ds-timeout">Timeout (Sekunden)</label>
      <input type="number" id="ds-timeout" value="${cfg.timeout_seconds || 60}"
        min="5" max="300" onchange="markSettingsModified()" style="width:100px">
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">FEATURES</h3>
    </div>

    <div class="settings-field">
      <label for="ds-network">Netzwerkzugriff</label>
      <label class="checkbox-label">
        <input type="checkbox" id="ds-network" ${cfg.network_enabled !== false ? 'checked' : ''} onchange="markSettingsModified()">
        Aktiviert (fuer HTTP-Requests)
      </label>
    </div>

    <div class="settings-field">
      <label for="ds-sessions">Sessions</label>
      <label class="checkbox-label">
        <input type="checkbox" id="ds-sessions" ${cfg.session_enabled !== false ? 'checked' : ''} onchange="markSettingsModified()">
        Aktiviert (Variablen bleiben erhalten)
      </label>
    </div>

    <div class="settings-field">
      <label for="ds-max-sessions">Max Sessions</label>
      <input type="number" id="ds-max-sessions" value="${cfg.max_sessions || 5}"
        min="1" max="20" onchange="markSettingsModified()" style="width:100px">
    </div>

    <div class="settings-field">
      <label for="ds-upload">Datei-Upload</label>
      <label class="checkbox-label">
        <input type="checkbox" id="ds-upload" ${cfg.file_upload_enabled !== false ? 'checked' : ''} onchange="markSettingsModified()">
        Aktiviert
      </label>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">PYTHON-PAKETE</h3>
      <p class="settings-section-desc">
        Diese Pakete werden automatisch installiert (bei Standard-Image).
      </p>
    </div>

    <div class="settings-field">
      <label for="ds-packages">Vorinstallierte Pakete</label>
      <textarea id="ds-packages" rows="6" onchange="markSettingsModified()"
        placeholder="requests&#10;pandas&#10;numpy"
        style="font-family:var(--font-mono);font-size:13px">${escapeHtml(packages)}</textarea>
      <small style="color:var(--text-muted)">Ein Paket pro Zeile</small>
    </div>

    <div class="settings-actions-section" style="margin-top:20px">
      <button class="btn btn-secondary" onclick="dockerSandboxTestConnection()">
        🔌 Verbindung testen
      </button>
      <span id="ds-test-result" class="test-result"></span>
    </div>
  `;
}

async function dockerSandboxTestConnection() {
  const resultEl = document.getElementById('ds-test-result');
  resultEl.textContent = '⏳ Teste...';
  resultEl.className = 'test-result testing';

  // Erst die aktuellen Werte speichern
  const cfg = collectDockerSandboxSettings();

  try {
    // Temporaer speichern fuer den Test
    await fetch('/api/docker-sandbox/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });

    const res = await fetch('/api/docker-sandbox/test', { method: 'POST' });
    const data = await res.json();

    if (data.status === 'ok') {
      resultEl.textContent = `✓ ${data.python_version} (${data.execution_time}s)`;
      resultEl.className = 'test-result success';
    } else {
      resultEl.textContent = `✗ ${data.error || 'Container-Test fehlgeschlagen'}`;
      resultEl.className = 'test-result error';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

function collectDockerSandboxSettings() {
  const packagesText = document.getElementById('ds-packages')?.value || '';
  const packages = packagesText.split('\n').map(p => p.trim()).filter(p => p.length > 0);

  return {
    enabled: document.getElementById('ds-enabled')?.checked || false,
    image: document.getElementById('ds-image')?.value?.trim() || 'python:3.11-slim',
    custom_image: document.getElementById('ds-custom-image')?.value?.trim() || '',
    memory_limit: document.getElementById('ds-memory')?.value?.trim() || '512m',
    cpu_limit: parseFloat(document.getElementById('ds-cpu')?.value) || 1.0,
    timeout_seconds: parseInt(document.getElementById('ds-timeout')?.value) || 60,
    network_enabled: document.getElementById('ds-network')?.checked || false,
    session_enabled: document.getElementById('ds-sessions')?.checked || false,
    max_sessions: parseInt(document.getElementById('ds-max-sessions')?.value) || 5,
    file_upload_enabled: document.getElementById('ds-upload')?.checked || false,
    preinstalled_packages: packages,
    // WSL Podman settings
    wsl_integration: {
      distro_name: document.getElementById('ds-wsl-distro')?.value?.trim() || 'Ubuntu',
      podman_path_in_wsl: document.getElementById('ds-wsl-podman-path')?.value?.trim() || '/usr/bin/podman',
      internal_image_path: document.getElementById('ds-wsl-image-path')?.value?.trim() || '',
    },
  };
}

// ── Jenkins Settings Section ────────────────────────────────────────────────────

async function renderJenkinsSection() {
  const cfg = settingsState.settings.jenkins || {};
  const jobPaths = cfg.job_paths || [];

  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">JENKINS CI/CD</h3>
      <p class="settings-section-desc">
        Jenkins CI/CD Server Konfiguration für Build-Status und Job-Ausführung.
      </p>
    </div>

    <div class="settings-field">
      <label for="jenkins-enabled">Aktiviert</label>
      <label class="checkbox-label">
        <input type="checkbox" id="jenkins-enabled" ${cfg.enabled ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.enabled ? 'Aktiviert' : 'Deaktiviert'}
      </label>
    </div>

    <div class="settings-field">
      <label for="jenkins-base-url">Base URL</label>
      <input type="text" id="jenkins-base-url" value="${escapeHtml(cfg.base_url || '')}"
        placeholder="http://jenkins.intern:8080" onchange="markSettingsModified()" style="font-family:var(--font-mono)">
    </div>

    <div class="settings-field">
      <label for="jenkins-username">Benutzername</label>
      <input type="text" id="jenkins-username" value="${escapeHtml(cfg.username || '')}"
        placeholder="jenkins-user" onchange="markSettingsModified()">
    </div>

    <div class="settings-field">
      <label for="jenkins-token">API Token</label>
      <input type="password" id="jenkins-token" value="${escapeHtml(cfg.api_token || '')}"
        placeholder="Jenkins API Token" onchange="markSettingsModified()" autocomplete="off">
    </div>

    <div class="settings-field">
      <label for="jenkins-verify-ssl">SSL-Zertifikat prüfen</label>
      <label class="checkbox-label">
        <input type="checkbox" id="jenkins-verify-ssl" ${cfg.verify_ssl ? 'checked' : ''} onchange="markSettingsModified()">
        ${cfg.verify_ssl ? 'Ja' : 'Nein (für Self-Signed Certs)'}
      </label>
    </div>

    <div class="settings-section" style="margin-top:20px">
      <h3 class="settings-section-title">JOB-PFADE</h3>
      <p class="settings-section-desc">
        Jenkins Job-Ordner-Pfade (z.B. job/Verbund/job/OSPE). Der Agent sucht Jobs in diesen Pfaden.
      </p>
    </div>

    <div id="jenkins-paths-list">
      ${jobPaths.length ? jobPaths.map((p, i) => `
        <div class="ds-item" style="margin-bottom:8px">
          <div class="ds-item-header">
            <div>
              <span class="ds-item-name">${escapeHtml(p.name)}</span>
              ${cfg.default_job_path === p.name ? '<span class="badge badge-success">Standard</span>' : ''}
            </div>
            <div class="ds-item-actions">
              <button class="btn btn-xs btn-secondary" onclick="jenkinsSetDefaultPath('${escapeHtml(p.name)}')">&#9733; Standard</button>
              <button class="btn btn-xs btn-danger" onclick="jenkinsRemovePath(${i})">&#128465;</button>
            </div>
          </div>
          <div class="ds-item-detail">
            <code>${escapeHtml(p.path)}</code>
          </div>
        </div>
      `).join('') : '<p class="empty-hint">Keine Job-Pfade konfiguriert.</p>'}
    </div>

    <div class="ds-add-form" style="margin-top:12px">
      <h4>Neuen Pfad hinzufügen</h4>
      <div class="settings-field">
        <label for="jenkins-new-name">Name</label>
        <input type="text" id="jenkins-new-name" placeholder="z.B. OSPE, PKP">
      </div>
      <div class="settings-field">
        <label for="jenkins-new-path">Pfad</label>
        <input type="text" id="jenkins-new-path" placeholder="job/Verbund/job/OSPE" style="font-family:var(--font-mono)">
      </div>
      <button class="btn btn-primary" onclick="jenkinsAddPath()">+ Pfad hinzufügen</button>
    </div>

    <div class="settings-field" style="margin-top:20px">
      <label for="jenkins-job-filter">Job-Filter (Optional)</label>
      <input type="text" id="jenkins-job-filter" value="${escapeHtml(cfg.job_filter || '')}"
        placeholder="Prefix-Filter, z.B. MyProject-" onchange="markSettingsModified()">
    </div>

    <div class="settings-field">
      <label for="jenkins-timeout">Timeout (Sekunden)</label>
      <input type="number" id="jenkins-timeout" value="${cfg.timeout_seconds || 30}"
        min="5" max="300" onchange="markSettingsModified()">
    </div>

    <div class="settings-field">
      <label for="jenkins-confirm-build">Build-Bestätigung erforderlich</label>
      <label class="checkbox-label">
        <input type="checkbox" id="jenkins-confirm-build" ${cfg.require_build_confirmation !== false ? 'checked' : ''} onchange="markSettingsModified()">
        Builds müssen bestätigt werden
      </label>
    </div>

    <div class="settings-actions-section" style="margin-top:20px">
      <button class="btn btn-secondary" onclick="jenkinsTestConnection()">
        🔌 Verbindung testen
      </button>
      <span id="jenkins-test-result" class="test-result"></span>
    </div>
  `;
}

function jenkinsAddPath() {
  const name = document.getElementById('jenkins-new-name').value.trim();
  const path = document.getElementById('jenkins-new-path').value.trim();

  if (!name || !path) {
    updateSettingsStatus('Name und Pfad erforderlich', 'error');
    return;
  }

  if (!settingsState.settings.jenkins) {
    settingsState.settings.jenkins = { job_paths: [] };
  }
  if (!settingsState.settings.jenkins.job_paths) {
    settingsState.settings.jenkins.job_paths = [];
  }

  // Prüfen ob Name schon existiert
  if (settingsState.settings.jenkins.job_paths.some(p => p.name === name)) {
    updateSettingsStatus('Name bereits vorhanden', 'error');
    return;
  }

  settingsState.settings.jenkins.job_paths.push({ name, path });
  markSettingsModified();
  renderJenkinsSection();
  updateSettingsStatus('Pfad hinzugefügt', 'success');
}

function jenkinsRemovePath(idx) {
  if (!settingsState.settings.jenkins?.job_paths) return;
  settingsState.settings.jenkins.job_paths.splice(idx, 1);
  markSettingsModified();
  renderJenkinsSection();
}

function jenkinsSetDefaultPath(name) {
  if (!settingsState.settings.jenkins) {
    settingsState.settings.jenkins = {};
  }
  settingsState.settings.jenkins.default_job_path = name;
  markSettingsModified();
  renderJenkinsSection();
  updateSettingsStatus(`Standard-Pfad: ${name}`, 'success');
}

async function jenkinsTestConnection() {
  const resultEl = document.getElementById('jenkins-test-result');
  resultEl.textContent = '⏳ Teste Verbindung...';
  resultEl.className = 'test-result testing';

  // Erst die aktuellen Werte speichern
  const cfg = {
    enabled: document.getElementById('jenkins-enabled').checked,
    base_url: document.getElementById('jenkins-base-url').value.trim(),
    username: document.getElementById('jenkins-username').value.trim(),
    api_token: document.getElementById('jenkins-token').value,
    verify_ssl: document.getElementById('jenkins-verify-ssl').checked,
    job_paths: settingsState.settings.jenkins?.job_paths || [],
    default_job_path: settingsState.settings.jenkins?.default_job_path || '',
    job_filter: document.getElementById('jenkins-job-filter').value.trim(),
    timeout_seconds: parseInt(document.getElementById('jenkins-timeout').value) || 30,
    require_build_confirmation: document.getElementById('jenkins-confirm-build').checked,
  };

  if (!cfg.base_url) {
    resultEl.textContent = '✗ Base URL fehlt';
    resultEl.className = 'test-result error';
    return;
  }

  try {
    // Temporär speichern für den Test
    await fetch('/api/settings/section/jenkins', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });

    const res = await fetch('/api/jenkins/test', { method: 'POST' });
    const data = await res.json();

    if (data.success) {
      resultEl.textContent = `✓ ${data.message || 'Verbindung erfolgreich'}`;
      resultEl.className = 'test-result success';
    } else {
      resultEl.textContent = `✗ ${data.error || 'Verbindung fehlgeschlagen'}`;
      resultEl.className = 'test-result error';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

// Beim Speichern die Jenkins-Felder sammeln
function collectJenkinsSettings() {
  return {
    enabled: document.getElementById('jenkins-enabled')?.checked || false,
    base_url: document.getElementById('jenkins-base-url')?.value?.trim() || '',
    username: document.getElementById('jenkins-username')?.value?.trim() || '',
    api_token: document.getElementById('jenkins-token')?.value || '',
    verify_ssl: document.getElementById('jenkins-verify-ssl')?.checked || false,
    job_paths: settingsState.settings.jenkins?.job_paths || [],
    default_job_path: settingsState.settings.jenkins?.default_job_path || '',
    job_filter: document.getElementById('jenkins-job-filter')?.value?.trim() || '',
    timeout_seconds: parseInt(document.getElementById('jenkins-timeout')?.value) || 30,
    require_build_confirmation: document.getElementById('jenkins-confirm-build')?.checked !== false,
  };
}

// ── Search Settings Section ────────────────────────────────────────────────────

async function renderSearchSettingsSection() {
  const form = document.getElementById('settings-form');
  try {
    // Status und Proxy-Config parallel laden
    const [statusRes, configRes] = await Promise.all([
      fetch('/api/search/status'),
      fetch('/api/search/config')
    ]);
    const data = await statusRes.json();
    const config = await configRes.json();

    form.innerHTML = `
      <div class="settings-section">
        <h3 class="settings-section-title">WEB-SUCHE</h3>
        <p class="settings-section-desc">
          Der Agent kann Internet-Recherchen durchführen (z.B. Fehlercodes nachschlagen).
          Jede Anfrage muss vom Nutzer einzeln bestätigt werden.
          Interne IPs, Hostnamen und Dateipfade werden automatisch geblockt.
        </p>
      </div>
      <div class="settings-subsection">
        <h4>Status</h4>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
          <label class="toggle-switch">
            <input type="checkbox" id="search-settings-toggle" ${data.enabled ? 'checked' : ''} onchange="searchSettingsToggle(this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <span id="search-settings-status" style="font-size:13px;color:${data.enabled ? 'var(--success)' : 'var(--text-muted)'}">
            ${data.enabled ? '&#10003; Aktiviert' : 'Deaktiviert'}
          </span>
        </div>
        <p style="font-size:12px;color:var(--text-muted)">
          Der Agent kann die Suche auch selbst ein-/ausschalten wenn du schreibst:<br>
          <code>"Websuche einschalten"</code> oder <code>"Suche ausschalten"</code>
        </p>
      </div>

      <div class="settings-subsection" style="margin-top:16px">
        <h4>Verbindungseinstellungen</h4>
        <div class="settings-field">
          <label>Timeout (Sekunden)</label>
          <input type="number" id="search-timeout" class="settings-input" style="width:100px"
                 min="5" max="120"
                 value="${config.timeout_seconds || 30}"
                 onchange="markSettingsModified()">
        </div>
        <div class="settings-field" style="margin-top:12px;padding:12px;background:var(--surface);border-radius:6px">
          <p style="font-size:12px;color:var(--text-secondary);margin:0">
            <strong>Proxy:</strong> ${data.proxy_configured ? '✓ Konfiguriert' : 'Nicht konfiguriert'}
            ${data.proxy_url ? ` (${escapeHtml(data.proxy_url)})` : ''}
          </p>
          <p style="font-size:11px;color:var(--text-muted);margin:4px 0 0 0">
            Proxy-Einstellungen werden global unter <a href="#" onclick="showSettingsSection('proxy'); return false;" style="color:var(--accent)">Settings &gt; Proxy</a> konfiguriert.
          </p>
        </div>
        <div class="settings-actions-section" style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
          <button class="btn btn-secondary" onclick="testSearchConnection()">
            🔍 Suche testen
          </button>
          <span id="search-test-result" class="test-result"></span>
        </div>
        <div id="search-test-details" style="display:none;margin-top:12px;padding:12px;background:var(--surface);border-radius:6px;font-size:12px;font-family:var(--font-mono)"></div>
      </div>

      <div class="settings-subsection" style="margin-top:16px">
        <h4>Sicherheitsregeln</h4>
        <ul style="font-size:12px;color:var(--text-secondary);padding-left:16px;margin:0">
          <li>Interne IP-Adressen (10.x, 192.168.x, 172.16-31.x) werden geblockt</li>
          <li>Interne Hostnamen (.local, .intern, .corp, .lan) werden geblockt</li>
          <li>Lokale Dateipfade werden geblockt</li>
          <li>Jede Suche erscheint im "Bestätigung"-Panel zur Freigabe</li>
          <li>Abgelehnte Suchen werden nicht ausgeführt</li>
        </ul>
      </div>
      <div class="settings-subsection" style="margin-top:16px">
        <h4>Verlauf (letzte Suchen)</h4>
        <div id="search-settings-history"><div class="spinner-inline"></div></div>
      </div>
    `;
    // History laden
    const histRes = await fetch('/api/search/history');
    const histData = await histRes.json();
    const histEl = document.getElementById('search-settings-history');
    if (!histEl) return;
    const history = histData.history || [];
    if (!history.length) {
      histEl.innerHTML = '<p class="empty-hint">Noch keine Suchanfragen.</p>';
      return;
    }
    histEl.innerHTML = history.slice(0, 10).map(item => `
      <div class="ds-item">
        <div class="ds-item-header">
          <span class="ds-item-name">${escapeHtml(item.query)}</span>
          <span class="badge ${item.status === 'done' ? 'badge-success' : 'badge-error'}">${item.status}</span>
        </div>
        <div class="ds-item-detail">
          ${item.reason ? escapeHtml(item.reason) + ' – ' : ''}
          ${item.results?.length || 0} Ergebnis(se)
          ${item.executed_at ? '– ' + new Date(item.executed_at).toLocaleString('de-DE') : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    form.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

async function testSearchConnection() {
  const resultEl = document.getElementById('search-test-result');
  const detailsEl = document.getElementById('search-test-details');

  resultEl.textContent = '⏳ Teste Suche...';
  resultEl.className = 'test-result testing';
  detailsEl.style.display = 'none';

  // Erst aktuelle Config speichern
  await saveSearchProxyConfig();

  try {
    const res = await fetch('/api/search/test', { method: 'POST' });
    const data = await res.json();

    if (data.success && data.debug?.search_results?.length > 0) {
      const results = data.debug.search_results;
      const hasRealResults = results.some(r => r.url && !r.title.includes('Fehler') && !r.title.includes('Keine'));

      if (hasRealResults) {
        resultEl.textContent = `✓ ${results.length} Ergebnisse gefunden`;
        resultEl.className = 'test-result success';
      } else {
        resultEl.textContent = '⚠ Verbindung OK, aber keine Suchergebnisse';
        resultEl.className = 'test-result warning';
      }
    } else if (data.success) {
      resultEl.textContent = '⚠ Verbindung OK, aber Parsing fehlgeschlagen';
      resultEl.className = 'test-result warning';
    } else {
      resultEl.textContent = `✗ ${data.error || 'Unbekannter Fehler'}`;
      resultEl.className = 'test-result error';
    }

    // Debug-Details anzeigen
    if (data.debug) {
      const d = data.debug;
      let html = `<strong>Debug-Info:</strong><br>`;
      html += `Proxy: ${d.proxy_url}<br>`;
      html += `SSL-Verify: ${d.verify_ssl}<br>`;
      html += `HTTP-Status: ${d.http_status || 'N/A'}<br>`;
      html += `Response-Länge: ${d.response_length || 0} Zeichen<br>`;

      if (d.regex_matches) {
        html += `<br><strong>Regex-Matches:</strong><br>`;
        html += `Titel gefunden: ${d.regex_matches.titles_found}<br>`;
        html += `Snippets gefunden: ${d.regex_matches.snippets_found}<br>`;
        html += `URLs gefunden: ${d.regex_matches.urls_found}<br>`;
        if (d.regex_matches.first_title) {
          html += `Erster Titel: ${escapeHtml(d.regex_matches.first_title)}<br>`;
        }
      }

      if (d.bot_block_indicators?.possible_block) {
        html += `<br><strong style="color:var(--warning)">⚠ Mögliche Bot-Blockierung erkannt!</strong><br>`;
        html += `Robot: ${d.bot_block_indicators.robot}, Captcha: ${d.bot_block_indicators.captcha}<br>`;
      }

      if (d.response_preview) {
        html += `<br><strong>Response-Vorschau:</strong><br>`;
        html += `<pre style="max-height:200px;overflow:auto;white-space:pre-wrap;font-size:10px;background:var(--bg);padding:8px;border-radius:4px">${escapeHtml(d.response_preview)}</pre>`;
      }

      detailsEl.innerHTML = html;
      detailsEl.style.display = 'block';
    }
  } catch (e) {
    resultEl.textContent = `✗ Fehler: ${e.message}`;
    resultEl.className = 'test-result error';
  }
}

async function searchSettingsToggle(enabled) {
  await fetch('/api/search/toggle', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  const el = document.getElementById('search-settings-status');
  if (el) {
    el.textContent = enabled ? '✓ Aktiviert' : 'Deaktiviert';
    el.style.color = enabled ? 'var(--success)' : 'var(--text-muted)';
  }
  updateSettingsStatus(enabled ? 'Web-Suche aktiviert ✓' : 'Web-Suche deaktiviert', 'success');
  // Auch Search-Panel-Toggle synchronisieren
  const panelToggle = document.getElementById('search-enabled-toggle');
  if (panelToggle) panelToggle.checked = enabled;
  const panelTxt = document.getElementById('search-status-text');
  if (panelTxt) {
    panelTxt.textContent = enabled
      ? 'Aktiviert – Agent kann Internet-Suchen anfragen (Bestätigung erforderlich)'
      : 'Deaktiviert – Agent kann keine Internet-Suchen durchführen';
    panelTxt.style.color = enabled ? 'var(--success)' : 'var(--text-muted)';
  }
}

async function saveSearchProxyConfig() {
  // Nur timeout_seconds speichern - Proxy wird global konfiguriert
  const config = {
    timeout_seconds: parseInt(document.getElementById('search-timeout')?.value) || 30,
  };

  log.info('[Search] Saving config:', config);

  try {
    // 1. Config im Speicher aktualisieren
    const res = await fetch('/api/search/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    const data = await res.json();

    if (!res.ok) {
      statusEl.textContent = '✗ Fehler: ' + (data.detail || 'Unbekannt');
      statusEl.style.color = 'var(--error)';
      return;
    }

    // 2. Einstellungen in Datei persistieren
    const saveRes = await fetch('/api/settings/save', { method: 'POST' });
    if (saveRes.ok) {
      statusEl.textContent = '✓ Gespeichert & persistiert';
      statusEl.style.color = 'var(--success)';
      updateSettingsStatus('Proxy-Einstellungen dauerhaft gespeichert ✓', 'success');
    } else {
      statusEl.textContent = '✓ Gespeichert (nicht persistiert)';
      statusEl.style.color = 'var(--warning)';
      updateSettingsStatus('Im Speicher aktualisiert, aber nicht in Datei gespeichert', 'warning');
    }
  } catch (e) {
    log.error('[Search] Save error:', e);
    statusEl.textContent = '✗ Fehler: ' + e.message;
    statusEl.style.color = 'var(--error)';
  }

  // Status nach 3s ausblenden
  setTimeout(() => {
    if (statusEl) statusEl.textContent = '';
  }, 3000);
}

// Keyboard shortcut to close modals (Escape key)
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    // Prioritätsreihenfolge der Modals
    const modals = [
      { id: 'healing-modal', close: closeHealingModal },
      { id: 'update-modal', close: closeUpdateModal },
      { id: 'tokens-modal', close: closeTokensModal },
      { id: 'arena-modal', close: closeArenaModal },
      { id: 'pattern-modal', close: closePatternModal },
      { id: 'dashboard-modal', close: closeDashboard },
      { id: 'settings-modal', close: closeSettings },
    ];
    for (const { id, close } of modals) {
      const modal = document.getElementById(id);
      if (modal && (modal.style.display === 'flex' || modal.style.display === 'block')) {
        close();
        return;
      }
    }
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// Template System - Prompt Templates für schnellen Zugriff
// ══════════════════════════════════════════════════════════════════════════════

let _templates = [];
let _currentTemplate = null;
let _templateExpanded = false;

/**
 * Lädt Templates vom Backend und rendert die Template-Bar
 */
async function loadTemplates() {
  try {
    const res = await fetch('/api/settings/templates');
    if (!res.ok) {
      log.warn('[Templates] Failed to load:', res.status);
      return;
    }
    const data = await res.json();

    // Prüfen ob Templates aktiviert sind und im Header angezeigt werden sollen
    if (!data.enabled || !data.show_in_chat_header) {
      document.getElementById('template-bar').style.display = 'none';
      return;
    }

    // Templates aus der Response extrahieren
    _templates = data.templates || [];
    log.info('[Templates] Loaded', _templates.length, 'templates');
    renderTemplateBar();
  } catch (e) {
    log.error('[Templates] Load error:', e);
  }
}

/**
 * Rendert die Template-Chips in der Template-Bar
 */
// Icon-Mapping für Templates
const TEMPLATE_ICONS = {
  search: '🔍',
  book: '📖',
  database: '🗃️',
  code: '💻',
  bug: '🐛',
  analyze: '📊',
  document: '📄',
  api: '🔌',
  test: '🧪',
  config: '⚙️',
  default: '📋'
};

function getTemplateIcon(iconName) {
  return TEMPLATE_ICONS[iconName] || TEMPLATE_ICONS.default;
}

function renderTemplateBar() {
  const container = document.getElementById('template-chips');
  if (!container) return;

  if (!_templates || _templates.length === 0) {
    container.innerHTML = '<span style="font-size:0.75rem;color:var(--text-muted)">Keine Templates konfiguriert</span>';
    return;
  }

  container.innerHTML = _templates.map(t => `
    <button class="template-chip"
            data-category="${t.category || 'general'}"
            data-id="${t.id}"
            onclick="selectTemplate('${t.id}')"
            title="${escapeHtml(t.description || t.name)}">
      <span class="template-chip-icon">${getTemplateIcon(t.icon)}</span>
      <span class="template-chip-name">${escapeHtml(t.name)}</span>
    </button>
  `).join('');
}

/**
 * Template auswählen und ggf. Placeholder-Modal öffnen
 */
function selectTemplate(templateId) {
  const template = _templates.find(t => t.id === templateId);
  if (!template) {
    log.warn('[Templates] Template not found:', templateId);
    return;
  }

  _currentTemplate = template;

  // Wenn Placeholders vorhanden, Modal öffnen
  if (template.placeholders && template.placeholders.length > 0) {
    openTemplateModal(template);
  } else {
    // Direkt in Input einfügen
    applyTemplateToInput(template.prompt);
  }
}

/**
 * Öffnet das Modal für Placeholder-Eingabe
 */
function openTemplateModal(template) {
  const modal = document.getElementById('template-modal');
  const title = document.getElementById('template-modal-title');
  const form = document.getElementById('template-placeholder-form');
  const preview = document.getElementById('template-preview');

  title.textContent = template.name;

  // Placeholder-Felder generieren
  form.innerHTML = template.placeholders.map(ph => `
    <div class="template-placeholder-field">
      <label class="template-placeholder-label">${ph}</label>
      <input type="text" 
             class="template-placeholder-input" 
             data-placeholder="${ph}"
             placeholder="Wert für ${ph} eingeben..."
             oninput="updateTemplatePreview()">
    </div>
  `).join('');

  // Initial-Vorschau
  preview.textContent = template.prompt;

  modal.classList.add('active');

  // Ersten Input fokussieren
  const firstInput = form.querySelector('input');
  if (firstInput) firstInput.focus();
}

/**
 * Schließt das Template-Modal
 */
function closeTemplateModal() {
  const modal = document.getElementById('template-modal');
  modal.classList.remove('active');
  _currentTemplate = null;
}

/**
 * Aktualisiert die Vorschau im Modal mit eingegebenen Werten
 */
function updateTemplatePreview() {
  if (!_currentTemplate) return;

  const preview = document.getElementById('template-preview');
  const inputs = document.querySelectorAll('#template-placeholder-form input');

  let prompt = _currentTemplate.prompt;
  inputs.forEach(input => {
    const ph = input.dataset.placeholder;
    const value = input.value || `{{${ph}}}`;
    prompt = prompt.replace(new RegExp(`\{\{${ph}\}\}`, 'g'), value);
  });

  preview.textContent = prompt;
}

/**
 * Wendet das Template mit Placeholder-Werten an
 */
function applyTemplate() {
  if (!_currentTemplate) return;

  const inputs = document.querySelectorAll('#template-placeholder-form input');

  let prompt = _currentTemplate.prompt;
  inputs.forEach(input => {
    const ph = input.dataset.placeholder;
    const value = input.value || '';
    prompt = prompt.replace(new RegExp(`\{\{${ph}\}\}`, 'g'), value);
  });

  applyTemplateToInput(prompt);
  closeTemplateModal();
}

/**
 * Fügt den Prompt in das Chat-Input-Feld ein
 */
function applyTemplateToInput(prompt) {
  const input = document.getElementById('message-input');
  if (!input) return;

  // Bestehenden Text ersetzen oder anhängen
  if (input.value.trim() === '') {
    input.value = prompt;
  } else {
    input.value = prompt + '\n\n' + input.value;
  }

  input.focus();
  // Cursor ans Ende
  input.selectionStart = input.selectionEnd = input.value.length;

  // Auto-resize wenn vorhanden
  if (typeof autoResizeTextarea === 'function') {
    autoResizeTextarea(input);
  }
}

/**
 * Togglet die erweiterte Ansicht der Template-Bar
 */
function toggleTemplateExpand() {
  const bar = document.getElementById('template-bar');
  const icon = document.getElementById('template-toggle-icon');

  _templateExpanded = !_templateExpanded;

  if (_templateExpanded) {
    bar.classList.add('expanded');
    bar.classList.remove('collapsed');
    icon.textContent = '▲';
  } else {
    bar.classList.remove('expanded');
    bar.classList.add('collapsed');
    icon.textContent = '▼';
  }
}

// Templates beim Start laden
document.addEventListener('DOMContentLoaded', () => {
  loadTemplates();
});

// Template-Modal mit Escape schließen
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('template-modal');
    if (modal && modal.classList.contains('active')) {
      closeTemplateModal();
    }
  }
});

// ══════════════════════════════════════════════════════════════════════════════
//   MCP Activity Panel - Akkordeon-basierte Multi-MCP Darstellung
// ══════════════════════════════════════════════════════════════════════════════

// MCP-Typen mit Icons und Farben
const MCP_TYPES = {
  sequential_thinking: { icon: '🧠', label: 'Sequential Thinking', color: 'thinking' },
  thinking: { icon: '🧠', label: 'Sequential Thinking', color: 'thinking' },  // ThinkingEngine wrapper
  brainstorm: { icon: '💡', label: 'Brainstorm', color: 'brainstorm' },
  design: { icon: '📐', label: 'Design', color: 'design' },
  implement: { icon: '⚙️', label: 'Implement', color: 'implement' },
  analyze: { icon: '🔍', label: 'Analyze', color: 'analyze' },
  research: { icon: '🌐', label: 'Research', color: 'research' },
  // Fallback für unbekannte MCP-Typen
  default: { icon: '⚡', label: 'MCP Tool', color: 'thinking' }
};

// Max. Anzahl Sessions im Panel
const MAX_MCP_SESSIONS = 10;

/**
 * Ermittelt MCP-Typ-Info aus Event-Daten oder Tool-Name.
 */
function getMcpTypeInfo(data) {
  const toolName = data.tool_name || data.capability || 'default';
  return MCP_TYPES[toolName] || MCP_TYPES.default;
}

/**
 * Generiert eine eindeutige Session-ID.
 */
function generateMcpSessionId() {
  return 'mcp_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

/**
 * Erstellt eine neue MCP-Session im Panel (Akkordeon-Item).
 * @param {Object} data - MCP_START Event-Daten
 * @param {Object} chat - Chat-Objekt
 */
function showThinkingPanel(data, chat) {
  const sessionsContainer = document.getElementById('mcp-sessions');
  const emptyState = document.getElementById('mcp-empty-state');
  const isActive = chat.id === chatManager.activeId;

  // Session-ID generieren
  const sessionId = data.session_id || generateMcpSessionId();
  const typeInfo = getMcpTypeInfo(data);

  // MCP-Sessions-State im Chat initialisieren
  if (!chat.mcpSessions) {
    chat.mcpSessions = {};
  }

  // Session-State speichern (mit v2 Features)
  chat.mcpSessions[sessionId] = {
    id: sessionId,
    toolName: data.tool_name || 'sequential_thinking',
    typeInfo,
    query: data.query || '',
    status: 'running',
    steps: [],
    startTime: Date.now(),
    maxSteps: data.estimated_steps || data.max_steps || 10,
    currentStep: 0,
    // v2: Tree View Features
    branches: {},           // branch_id -> { info, steps[] }
    activeBranch: null,     // Currently active branch
    assumptions: {},        // assumption_id -> { text, confidence, critical, status }
    revisionCount: 0,
    riskScore: 0
  };

  if (isActive && sessionsContainer) {
    // Empty-State ausblenden
    if (emptyState) emptyState.style.display = 'none';

    // Panel aktivieren
    switchRightPanel('mcp-panel');

    // Prüfen ob Session bereits existiert (re-start)
    let sessionEl = document.getElementById(`mcp-session-${sessionId}`);
    if (!sessionEl) {
      // Neue Session erstellen
      sessionEl = document.createElement('div');
      sessionEl.id = `mcp-session-${sessionId}`;
      sessionEl.className = 'mcp-session active expanded';
      sessionEl.innerHTML = `
        <div class="mcp-session-header" onclick="toggleMcpSession('${sessionId}')">
          <span class="mcp-session-arrow">▶</span>
          <span class="mcp-session-icon ${typeInfo.color}">${typeInfo.icon}</span>
          <span class="mcp-session-name">${typeInfo.label}</span>
          <span class="mcp-session-status running"></span>
        </div>
        <div class="mcp-session-progress">
          <div class="mcp-session-progress-bar" id="mcp-progress-${sessionId}"></div>
        </div>
        <div class="mcp-session-content">
          <div class="mcp-session-query">
            <span class="mcp-session-query-label">Query:</span>
            <span class="mcp-session-query-text">${escapeHtml(data.query || '-')}</span>
          </div>
          <div class="mcp-steps" id="mcp-steps-${sessionId}"></div>
        </div>
      `;

      // Am Anfang einfügen (neueste oben)
      sessionsContainer.insertBefore(sessionEl, sessionsContainer.firstChild);

      // Alte Sessions entfernen wenn zu viele
      const allSessions = sessionsContainer.querySelectorAll('.mcp-session');
      if (allSessions.length > MAX_MCP_SESSIONS) {
        allSessions[allSessions.length - 1].remove();
      }
    }
  }

  // Badge aktualisieren
  updateMcpBadge(chat);
}

/**
 * Fügt einen Step zu einer MCP-Session hinzu.
 * @param {Object} data - MCP_STEP Event-Daten
 * @param {Object} chat - Chat-Objekt
 */
function addThinkingStep(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId || !chat.mcpSessions?.[sessionId]) return;

  const session = chat.mcpSessions[sessionId];
  const step = {
    number: data.step_number || (session.steps.length + 1),
    type: data.step_type || 'analysis',
    title: data.title || `Schritt ${data.step_number}`,
    content: data.content || '',
    confidence: data.confidence || 0,
    timestamp: Date.now(),
    // v2: Extended step properties
    isRevision: data.is_revision || false,
    revisesStep: data.revises_step || null,
    revisionReason: data.revision_reason || null,
    branchId: data.branch_id || null,
    branchFromStep: data.branch_from_step || null,
    assumptions: data.assumptions || [],
    toolRecommendations: data.tool_recommendations || []
  };

  session.steps.push(step);
  session.currentStep = step.number;

  // v2: Track revision count
  if (step.isRevision) {
    session.revisionCount = (session.revisionCount || 0) + 1;
  }

  // Dynamische Tiefensteuerung: max_steps aktualisieren wenn vom Backend angepasst
  if (data.total_steps && data.total_steps > (session.maxSteps || 0)) {
    const oldMax = session.maxSteps || session.initialMaxSteps || 10;
    session.maxSteps = data.total_steps;
    session.stepsAdded = (session.stepsAdded || 0) + (data.total_steps - oldMax);
  }
  if (data.steps_added !== undefined) {
    session.stepsAdded = data.steps_added;
  }

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  const stepsContainer = document.getElementById(`mcp-steps-${sessionId}`);
  if (!stepsContainer) return;

  // Step-Element erstellen mit v2 Features
  const stepEl = document.createElement('div');

  // Build CSS classes based on step properties
  let stepClasses = 'mcp-step';
  if (step.isRevision) stepClasses += ' revision';
  if (step.branchId) stepClasses += ' branch-step';
  if (step.type === 'conclusion') stepClasses += ' conclusion';
  stepEl.className = stepClasses;
  stepEl.dataset.step = step.number;
  if (step.branchId) stepEl.dataset.branch = step.branchId;

  const typeIcons = {
    analysis: '🔍', hypothesis: '💡', verification: '✓', conclusion: '🎯',
    refinement: '🔄', exploration: '🗺️', evaluation: '⚖️', synthesis: '🧩',
    planning: '📋', decision: '⚖️', revision: '🔄', branch_start: '🌿', branch_merge: '🔀'
  };

  // v2: Build revision indicator
  let revisionHtml = '';
  if (step.isRevision && step.revisesStep) {
    revisionHtml = `<a class="mcp-revision-link" href="#" onclick="scrollToStep('${sessionId}', ${step.revisesStep}); return false;">↩ korrigiert #${step.revisesStep}</a>`;
  }

  // v2: Build branch indicator
  let branchHtml = '';
  if (step.branchId && step.type === 'branch_start') {
    branchHtml = `<span class="mcp-branch-badge">${step.branchId}</span>`;
  }

  // v2: Build assumption warnings
  let assumptionHtml = '';
  if (step.assumptions && step.assumptions.length > 0) {
    const assumption = session.assumptions?.[step.assumptions[0]];
    if (assumption?.critical) {
      assumptionHtml = `
        <div class="mcp-assumption-warning" title="${escapeHtml(assumption.text)}">
          ⚠️ <span class="assumption-text">${escapeHtml(assumption.text.substring(0, 30))}...</span>
          <span class="assumption-confidence">(${Math.round(assumption.confidence * 100)}%)</span>
        </div>`;
    }
  }

  // v2: Build tool recommendations
  let toolRecHtml = '';
  if (step.toolRecommendations && step.toolRecommendations.length > 0) {
    const chips = step.toolRecommendations.map(r =>
      `<span class="mcp-tool-chip" data-tool="${r.tool_name}" onclick="insertToolCall('${r.tool_name}')" title="${escapeHtml(r.reason)}">
        💡 ${r.tool_name} <span class="confidence">${Math.round(r.confidence * 100)}%</span>
      </span>`
    ).join('');
    toolRecHtml = `<div class="mcp-tool-recommendations">${chips}</div>`;
  }

  // Zeige "needs more" Indikator wenn LLM mehr Schritte anfordert
  const needsMoreIndicator = data.needs_more_steps ? '<span class="mcp-needs-more"></span>' : '';

  // Tree-view connector
  const connector = step.type === 'conclusion' ? '└──' : '├──';

  stepEl.innerHTML = `
    <span class="mcp-step-connector">${connector}</span>
    <span class="mcp-step-number">${step.number}${needsMoreIndicator}</span>
    ${branchHtml}
    <div class="mcp-step-content">
      <span class="mcp-step-title">
        ${escapeHtml(step.title)}
        <span class="mcp-step-type">${typeIcons[step.type] || '📝'} ${step.type}</span>
        ${revisionHtml}
      </span>
      <div class="mcp-step-text">${escapeHtml(step.content?.substring(0, 200) || '')}</div>
      ${assumptionHtml}
      ${toolRecHtml}
    </div>
  `;

  // v2: Mark revised steps as superseded
  if (step.isRevision && step.revisesStep) {
    const revisedStepEl = stepsContainer.querySelector(`[data-step="${step.revisesStep}"]`);
    if (revisedStepEl && !revisedStepEl.classList.contains('superseded')) {
      revisedStepEl.classList.add('superseded');
      const titleEl = revisedStepEl.querySelector('.mcp-step-title');
      if (titleEl && !titleEl.querySelector('.mcp-superseded-badge')) {
        const badge = document.createElement('span');
        badge.className = 'mcp-superseded-badge';
        badge.textContent = `→#${step.number}`;
        badge.title = `Revidiert in Schritt ${step.number}`;
        titleEl.appendChild(badge);
      }
    }
  }

  stepsContainer.appendChild(stepEl);
  stepEl.scrollIntoView({ behavior: 'smooth', block: 'end' });

  // Progress aktualisieren
  const progressBar = document.getElementById(`mcp-progress-${sessionId}`);
  if (progressBar && session.maxSteps) {
    const percent = Math.min(100, Math.round((step.number / session.maxSteps) * 100));
    progressBar.style.width = `${percent}%`;
  }

  // Session-Name aktualisieren mit Steps-Added Info
  if (session.stepsAdded > 0) {
    const sessionEl = document.getElementById(`mcp-session-${sessionId}`);
    if (sessionEl) {
      const nameEl = sessionEl.querySelector('.mcp-session-name');
      if (nameEl && !nameEl.dataset.updated) {
        nameEl.innerHTML = `${session.typeInfo?.label || 'MCP'} <span class="mcp-steps-added">(+${session.stepsAdded})</span>`;
        nameEl.dataset.updated = 'true';
      }
    }
  }
}

/**
 * Aktualisiert den Fortschritt einer MCP-Session.
 * @param {Object} data - MCP_PROGRESS Event-Daten
 * @param {Object} chat - Chat-Objekt
 */
function updateThinkingProgress(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId) return;

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  const progressBar = document.getElementById(`mcp-progress-${sessionId}`);
  if (progressBar && data.progress_percent !== undefined) {
    progressBar.style.width = `${data.progress_percent}%`;
  }
}

/**
 * Markiert eine MCP-Session als abgeschlossen.
 * @param {Object} data - MCP_COMPLETE Event-Daten
 * @param {Object} chat - Chat-Objekt
 */
function completeThinking(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId || !chat.mcpSessions?.[sessionId]) return;

  const session = chat.mcpSessions[sessionId];
  session.status = 'complete';
  session.endTime = Date.now();
  session.conclusion = data.final_conclusion || data.conclusion || '';

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) {
    updateMcpBadge(chat);
    return;
  }

  const sessionEl = document.getElementById(`mcp-session-${sessionId}`);
  if (sessionEl) {
    sessionEl.classList.remove('active');

    // Status-Badge aktualisieren
    const statusEl = sessionEl.querySelector('.mcp-session-status');
    if (statusEl) {
      statusEl.className = 'mcp-session-status complete';
      statusEl.textContent = '✓';
    }

    // Progress auf 100%
    const progressBar = document.getElementById(`mcp-progress-${sessionId}`);
    if (progressBar) {
      progressBar.style.width = '100%';
    }

    // Conclusion hinzufügen wenn vorhanden
    if (session.conclusion) {
      const stepsContainer = document.getElementById(`mcp-steps-${sessionId}`);
      if (stepsContainer) {
        const conclusionEl = document.createElement('div');
        conclusionEl.className = 'mcp-step';
        conclusionEl.style.background = 'var(--success-bg)';
        conclusionEl.innerHTML = `
          <span class="mcp-step-number">🎯</span>
          <div class="mcp-step-content">
            <span class="mcp-step-title">Fazit</span>
            <div class="mcp-step-text">${escapeHtml(session.conclusion.substring(0, 300))}</div>
          </div>
        `;
        stepsContainer.appendChild(conclusionEl);
      }
    }

    // Nach kurzer Zeit einklappen
    setTimeout(() => {
      sessionEl.classList.remove('expanded');
    }, 2000);
  }

  updateMcpBadge(chat);
}

/**
 * Zeigt einen Fehler in einer MCP-Session an.
 * @param {Object} data - MCP_ERROR Event-Daten
 * @param {Object} chat - Chat-Objekt
 */
function showThinkingError(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (sessionId && chat.mcpSessions?.[sessionId]) {
    chat.mcpSessions[sessionId].status = 'error';
    chat.mcpSessions[sessionId].error = data.error;
  }

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) {
    updateMcpBadge(chat);
    return;
  }

  if (sessionId) {
    const sessionEl = document.getElementById(`mcp-session-${sessionId}`);
    if (sessionEl) {
      sessionEl.classList.remove('active');

      const statusEl = sessionEl.querySelector('.mcp-session-status');
      if (statusEl) {
        statusEl.className = 'mcp-session-status error';
        statusEl.textContent = '✗';
      }

      const stepsContainer = document.getElementById(`mcp-steps-${sessionId}`);
      if (stepsContainer && data.error) {
        const errorEl = document.createElement('div');
        errorEl.className = 'mcp-step';
        errorEl.style.background = 'var(--danger-bg)';
        errorEl.innerHTML = `
          <span class="mcp-step-number">⚠️</span>
          <div class="mcp-step-content">
            <span class="mcp-step-title" style="color: var(--danger)">Fehler</span>
            <div class="mcp-step-text">${escapeHtml(data.error)}</div>
          </div>
        `;
        stepsContainer.appendChild(errorEl);
      }
    }
  }

  updateMcpBadge(chat);
}

/**
 * Klappt eine MCP-Session ein/aus.
 * @param {string} sessionId - Session-ID
 */
function toggleMcpSession(sessionId) {
  const sessionEl = document.getElementById(`mcp-session-${sessionId}`);
  if (sessionEl) {
    sessionEl.classList.toggle('expanded');
  }
}

/**
 * Löscht alle MCP-Sessions aus dem Panel.
 */
function clearMcpSessions() {
  const chat = chatManager.getActive();
  if (chat) {
    chat.mcpSessions = {};
  }

  const sessionsContainer = document.getElementById('mcp-sessions');
  const emptyState = document.getElementById('mcp-empty-state');

  if (sessionsContainer) {
    // Alle Session-Elemente entfernen, aber Empty-State behalten
    const sessions = sessionsContainer.querySelectorAll('.mcp-session');
    sessions.forEach(s => s.remove());
  }

  if (emptyState) {
    emptyState.style.display = 'block';
  }

  updateMcpBadge(chat);
}

/**
 * Aktualisiert das MCP-Badge im Tab.
 * @param {Object} chat - Chat-Objekt
 */
function updateMcpBadge(chat) {
  const badge = document.getElementById('mcp-badge');
  if (!badge) return;

  const sessions = chat?.mcpSessions || {};
  const activeSessions = Object.values(sessions).filter(s => s.status === 'running');
  const count = activeSessions.length;

  if (count > 0) {
    badge.style.display = 'inline-block';
    badge.textContent = count.toString();
    badge.classList.add('pulse');
  } else {
    badge.style.display = 'none';
    badge.classList.remove('pulse');
  }
}

// Backward compatibility alias
function updateThinkingBadge(active, text = null) {
  const chat = chatManager.getActive();
  updateMcpBadge(chat);
}

// ══════════════════════════════════════════════════════════════════════════════
//   v2: Extended MCP Event Handlers
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Handles branch start event.
 * @param {Object} data - Branch start event data
 * @param {Object} chat - Chat object
 */
function handleMcpBranchStart(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId || !chat.mcpSessions?.[sessionId]) return;

  const session = chat.mcpSessions[sessionId];
  const branchId = data.branch_id;

  // Store branch info
  if (!session.branches) session.branches = {};
  session.branches[branchId] = {
    id: branchId,
    fromStep: data.from_step,
    description: data.description,
    status: 'active',
    steps: []
  };
  session.activeBranch = branchId;

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  // Add visual branch indicator to the steps container
  const stepsContainer = document.getElementById(`mcp-steps-${sessionId}`);
  if (!stepsContainer) return;

  const branchEl = document.createElement('div');
  branchEl.className = 'mcp-branch-marker';
  branchEl.id = `mcp-branch-${sessionId}-${branchId}`;
  branchEl.innerHTML = `
    <div class="mcp-branch-header" onclick="toggleBranch('${sessionId}', '${branchId}')">
      <span class="mcp-branch-connector">├─ Branch:</span>
      <span class="mcp-branch-name">${escapeHtml(branchId)}</span>
      <span class="mcp-branch-status active">⟳</span>
      <span class="mcp-branch-desc">${escapeHtml(data.description || '')}</span>
    </div>
    <div class="mcp-branch-content expanded" id="mcp-branch-content-${sessionId}-${branchId}"></div>
  `;
  stepsContainer.appendChild(branchEl);
}

/**
 * Handles branch end event (merge or abandon).
 * @param {Object} data - Branch end event data
 * @param {Object} chat - Chat object
 */
function handleMcpBranchEnd(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId || !chat.mcpSessions?.[sessionId]) return;

  const session = chat.mcpSessions[sessionId];
  const branchId = data.branch_id;

  // Update branch status
  if (session.branches?.[branchId]) {
    session.branches[branchId].status = data.status; // 'merged' or 'abandoned'
    session.branches[branchId].summary = data.summary;
  }

  if (session.activeBranch === branchId) {
    session.activeBranch = null;
  }

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  // Update visual indicator
  const branchEl = document.getElementById(`mcp-branch-${sessionId}-${branchId}`);
  if (branchEl) {
    const statusEl = branchEl.querySelector('.mcp-branch-status');
    if (statusEl) {
      statusEl.className = `mcp-branch-status ${data.status}`;
      statusEl.textContent = data.status === 'merged' ? '✓' : '✗';
    }
  }
}

/**
 * Handles assumption created event.
 * @param {Object} data - Assumption event data
 * @param {Object} chat - Chat object
 */
function handleMcpAssumption(data, chat) {
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId || !chat.mcpSessions?.[sessionId]) return;

  const session = chat.mcpSessions[sessionId];
  const assumption = data.assumption;

  if (!assumption?.id) return;

  // Store assumption
  if (!session.assumptions) session.assumptions = {};
  session.assumptions[assumption.id] = {
    id: assumption.id,
    text: assumption.text,
    confidence: assumption.confidence,
    critical: assumption.critical,
    status: assumption.status || 'unverified',
    riskScore: assumption.risk_score || 0
  };

  // Update session risk score
  const criticalAssumptions = Object.values(session.assumptions).filter(a => a.critical);
  if (criticalAssumptions.length > 0) {
    session.riskScore = criticalAssumptions.reduce((sum, a) => sum + (a.riskScore || 0), 0) / criticalAssumptions.length;
  }

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  // Update session header with risk indicator if high risk
  if (session.riskScore > 0.5) {
    const sessionEl = document.getElementById(`mcp-session-${sessionId}`);
    if (sessionEl && !sessionEl.querySelector('.mcp-risk-badge')) {
      const nameEl = sessionEl.querySelector('.mcp-session-name');
      if (nameEl) {
        const badge = document.createElement('span');
        badge.className = 'mcp-risk-badge';
        badge.title = 'Hohe Risiko-Annahmen';
        badge.textContent = '⚠️';
        nameEl.appendChild(badge);
      }
    }
  }
}

/**
 * Handles tool recommendation event.
 * @param {Object} data - Tool recommendation event data
 * @param {Object} chat - Chat object
 */
function handleMcpToolRec(data, chat) {
  // Tool recommendations are usually included in step events
  // This handler is for standalone recommendations
  const sessionId = data.session_id || Object.keys(chat.mcpSessions || {}).pop();
  if (!sessionId || !chat.mcpSessions?.[sessionId]) return;

  const stepNumber = data.step_number;
  const recommendations = data.recommendations || [];

  const isActive = chat.id === chatManager.activeId;
  if (!isActive || recommendations.length === 0) return;

  // Find the step and add recommendations
  const stepsContainer = document.getElementById(`mcp-steps-${sessionId}`);
  if (!stepsContainer) return;

  const stepEl = stepsContainer.querySelector(`[data-step="${stepNumber}"]`);
  if (!stepEl) return;

  // Check if recommendations already exist
  if (stepEl.querySelector('.mcp-tool-recommendations')) return;

  const chips = recommendations.map(r =>
    `<span class="mcp-tool-chip" data-tool="${r.tool_name}" onclick="insertToolCall('${r.tool_name}')" title="${escapeHtml(r.reason || '')}">
      💡 ${r.tool_name} <span class="confidence">${Math.round((r.confidence || 0) * 100)}%</span>
    </span>`
  ).join('');

  const recDiv = document.createElement('div');
  recDiv.className = 'mcp-tool-recommendations';
  recDiv.innerHTML = chips;
  stepEl.querySelector('.mcp-step-content')?.appendChild(recDiv);
}

/**
 * Scrolls to a specific step in the MCP session.
 * @param {string} sessionId - Session ID
 * @param {number} stepNumber - Step number to scroll to
 */
function scrollToStep(sessionId, stepNumber) {
  const stepsContainer = document.getElementById(`mcp-steps-${sessionId}`);
  if (!stepsContainer) return;

  const stepEl = stepsContainer.querySelector(`[data-step="${stepNumber}"]`);
  if (stepEl) {
    stepEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    stepEl.classList.add('highlighted');
    setTimeout(() => stepEl.classList.remove('highlighted'), TIMING.HIGHLIGHT_DURATION);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Task Progress Panel - Inline UI for Task Decomposition
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Creates and shows the task progress panel when a plan is created.
 * @param {Object} data - Plan created event data
 * @param {Object} chat - Chat object
 * @param {HTMLElement} bubble - Message bubble element
 */
function handleTaskPlanCreated(data, chat, bubble) {
  // Store task plan in chat
  chat.taskPlan = {
    taskCount: data.task_count,
    originalQuery: data.original_query,
    tasks: new Map(data.tasks.map(t => [t.id, {
      id: t.id,
      type: t.type,
      description: t.description,
      dependsOn: t.depends_on,
      status: 'pending',
      result: null,
      resultPreview: null
    }])),
    startedAt: new Date()
  };

  // Find or create the panel in the bubble
  let panel = bubble.querySelector('.task-progress-panel');
  if (!panel) {
    panel = createTaskProgressPanel(chat.taskPlan);
    bubble.appendChild(panel);
  }

  console.log(`[TaskProgress] Plan created with ${data.task_count} tasks`);
}

/**
 * Creates the task progress panel HTML.
 * @param {Object} plan - Task plan object
 * @returns {HTMLElement} Panel element
 */
function createTaskProgressPanel(plan) {
  const panel = document.createElement('div');
  panel.className = 'task-progress-panel';

  const header = document.createElement('div');
  header.className = 'task-panel-header';
  header.innerHTML = `
    <div class="task-panel-title">
      <span class="task-panel-icon">📋</span>
      <span>Ausführungsplan</span>
      <span class="task-count-badge">${plan.taskCount} Tasks</span>
    </div>
    <div class="task-overall-progress">
      <div class="task-progress-bar">
        <div class="task-progress-fill" style="width: 0%"></div>
      </div>
      <span class="task-progress-text">0%</span>
    </div>
  `;

  const taskList = document.createElement('div');
  taskList.className = 'task-list';

  plan.tasks.forEach((task, id) => {
    const taskEl = createTaskItem(task);
    taskList.appendChild(taskEl);
  });

  panel.appendChild(header);
  panel.appendChild(taskList);

  return panel;
}

/**
 * Creates a single task item element.
 * @param {Object} task - Task object
 * @returns {HTMLElement} Task item element
 */
function createTaskItem(task) {
  const el = document.createElement('div');
  el.className = 'task-item';
  el.dataset.taskId = task.id;

  const statusIcon = getTaskStatusIcon(task.status);
  const typeLabel = getTaskTypeLabel(task.type);
  const dependsText = task.dependsOn.length > 0
    ? `<span class="task-depends">Wartet auf: ${task.dependsOn.join(', ')}</span>`
    : '';

  el.innerHTML = `
    <div class="task-item-header">
      <span class="task-status-icon">${statusIcon}</span>
      <span class="task-id">${task.id}</span>
      <span class="task-type-badge task-type-${task.type}">${typeLabel}</span>
      <span class="task-status-label">${getTaskStatusLabel(task.status)}</span>
    </div>
    <div class="task-description">${escapeHtml(task.description)}</div>
    ${dependsText}
    <div class="task-result-preview" style="display: none;"></div>
  `;

  return el;
}

/**
 * Updates task status when a task starts.
 * @param {Object} data - Task started event data
 * @param {Object} chat - Chat object
 */
function handleTaskStarted(data, chat) {
  if (!chat.taskPlan) return;

  const task = chat.taskPlan.tasks.get(data.task_id);
  if (task) {
    task.status = 'running';
    task.startedAt = new Date();
  }

  updateTaskItemUI(chat, data.task_id, 'running');
  updateOverallProgress(chat);

  console.log(`[TaskProgress] Task ${data.task_id} started`);
}

/**
 * Updates task status when a task completes.
 * @param {Object} data - Task completed event data
 * @param {Object} chat - Chat object
 */
function handleTaskCompleted(data, chat) {
  if (!chat.taskPlan) return;

  const task = chat.taskPlan.tasks.get(data.task_id);
  if (task) {
    task.status = 'completed';
    task.resultPreview = data.result_preview;
    task.hasFullResult = data.has_full_result;
    task.completedAt = new Date();
  }

  updateTaskItemUI(chat, data.task_id, 'completed', data.result_preview);
  updateOverallProgress(chat);

  console.log(`[TaskProgress] Task ${data.task_id} completed`);
}

/**
 * Updates task status when a task fails.
 * @param {Object} data - Task failed event data
 * @param {Object} chat - Chat object
 */
function handleTaskFailed(data, chat) {
  if (!chat.taskPlan) return;

  const task = chat.taskPlan.tasks.get(data.task_id);
  if (task) {
    task.status = 'failed';
    task.error = data.error;
  }

  updateTaskItemUI(chat, data.task_id, 'failed', data.error);
  updateOverallProgress(chat);

  console.log(`[TaskProgress] Task ${data.task_id} failed: ${data.error}`);
}

/**
 * Handles execution complete event.
 * @param {Object} data - Execution complete event data
 * @param {Object} chat - Chat object
 */
function handleTaskExecutionComplete(data, chat) {
  if (!chat.taskPlan) return;

  chat.taskPlan.completedAt = new Date();
  chat.taskPlan.success = data.success;

  // Update panel header to show completion
  const panel = document.querySelector(`[data-chat-id="${chat.id}"] .task-progress-panel`);
  if (panel) {
    const header = panel.querySelector('.task-panel-title');
    if (header) {
      const icon = data.success ? '✅' : '⚠️';
      const text = data.success ? 'Abgeschlossen' : 'Teilweise fehlgeschlagen';
      header.querySelector('.task-panel-icon').textContent = icon;
      header.innerHTML = header.innerHTML.replace('Ausführungsplan', text);
    }
  }

  updateOverallProgress(chat);
  console.log(`[TaskProgress] Execution complete, success: ${data.success}`);
}

/**
 * Updates the UI for a specific task item.
 * @param {Object} chat - Chat object
 * @param {string} taskId - Task ID
 * @param {string} status - New status
 * @param {string} [resultPreview] - Optional result preview text
 */
function updateTaskItemUI(chat, taskId, status, resultPreview) {
  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  const taskEl = document.querySelector(`.task-item[data-task-id="${taskId}"]`);
  if (!taskEl) return;

  // Update status icon
  const iconEl = taskEl.querySelector('.task-status-icon');
  if (iconEl) {
    iconEl.textContent = getTaskStatusIcon(status);
  }

  // Update status label
  const labelEl = taskEl.querySelector('.task-status-label');
  if (labelEl) {
    labelEl.textContent = getTaskStatusLabel(status);
    labelEl.className = `task-status-label task-status-${status}`;
  }

  // Update class for styling
  taskEl.className = `task-item task-${status}`;

  // Show result preview if available
  if (resultPreview) {
    const previewEl = taskEl.querySelector('.task-result-preview');
    if (previewEl) {
      previewEl.textContent = status === 'failed' ? `Fehler: ${resultPreview}` : resultPreview;
      previewEl.style.display = 'block';
      previewEl.className = `task-result-preview ${status === 'failed' ? 'task-error' : ''}`;
    }
  }
}

/**
 * Updates the overall progress bar.
 * @param {Object} chat - Chat object
 */
function updateOverallProgress(chat) {
  if (!chat.taskPlan) return;

  const isActive = chat.id === chatManager.activeId;
  if (!isActive) return;

  const tasks = Array.from(chat.taskPlan.tasks.values());
  const completed = tasks.filter(t => t.status === 'completed').length;
  const failed = tasks.filter(t => t.status === 'failed').length;
  const total = tasks.length;
  const percent = Math.round(((completed + failed) / total) * 100);

  const progressFill = document.querySelector('.task-progress-fill');
  const progressText = document.querySelector('.task-progress-text');

  if (progressFill) {
    progressFill.style.width = `${percent}%`;
    progressFill.className = `task-progress-fill ${failed > 0 ? 'has-failures' : ''}`;
  }

  if (progressText) {
    progressText.textContent = `${completed}/${total}`;
  }
}

/**
 * Returns the icon for a task status.
 * @param {string} status - Task status
 * @returns {string} Icon character
 */
function getTaskStatusIcon(status) {
  const icons = {
    'pending': '⏳',
    'running': '🔄',
    'completed': '✅',
    'failed': '❌'
  };
  return icons[status] || '○';
}

/**
 * Returns the label for a task status.
 * @param {string} status - Task status
 * @returns {string} Status label
 */
function getTaskStatusLabel(status) {
  const labels = {
    'pending': 'Wartend',
    'running': 'Läuft...',
    'completed': 'Fertig',
    'failed': 'Fehler'
  };
  return labels[status] || status;
}

/**
 * Returns the label for a task type.
 * @param {string} type - Task type
 * @returns {string} Type label
 */
function getTaskTypeLabel(type) {
  const labels = {
    'research': '🔍 Recherche',
    'code': '💻 Code',
    'analyst': '📊 Analyse',
    'devops': '🔧 DevOps',
    'docs': '📝 Doku',
    'debug': '🐛 Debug'
  };
  return labels[type] || type;
}

/**
 * Toggles a branch's visibility.
 * @param {string} sessionId - Session ID
 * @param {string} branchId - Branch ID
 */
function toggleBranch(sessionId, branchId) {
  const contentEl = document.getElementById(`mcp-branch-content-${sessionId}-${branchId}`);
  if (contentEl) {
    contentEl.classList.toggle('expanded');
    contentEl.classList.toggle('collapsed');
  }
}

/**
 * Inserts a tool call into the chat input.
 * @param {string} toolName - Name of the tool to insert
 */
function insertToolCall(toolName) {
  const input = document.getElementById('message-input');
  if (!input) return;

  const toolCommands = {
    'read_file': '/read ',
    'glob_search': '/search ',
    'grep_search': '/grep ',
    'edit_file': '/edit ',
    'write_file': '/write ',
    'run_tests': '/test',
    'git_operation': '/git ',
    'http_request': '/fetch '
  };

  const command = toolCommands[toolName] || `Use tool: ${toolName}`;
  input.value = command;
  input.focus();
}

// formatDuration and escapeHtml defined earlier in the file

// ══════════════════════════════════════════════════════════════════════════════
// Update Service Functions
// ══════════════════════════════════════════════════════════════════════════════

let updateState = {
  checking: false,
  installing: false,
  downloadUrl: null,
  latestVersion: null,
};

async function initUpdateButton() {
  try {
    const response = await fetch('/api/update/config');
    if (response.ok) {
      const config = await response.json();
      const btn = document.getElementById('update-btn');
      if (btn && config.enabled) {
        btn.style.display = 'inline-flex';
        // Check on start if configured
        if (config.check_on_start) {
          await refreshUpdateStatus();
        }
        // Periodisch alle 5 Minuten prüfen
        setInterval(refreshUpdateStatus, 5 * 60 * 1000);
      }
    }
  } catch (e) {
    log.warn('[update] Init failed:', e);
  }
}

async function refreshUpdateStatus() {
  try {
    // Cache-Busting mit Timestamp
    const check = await fetch(`/api/update/check?_t=${Date.now()}`);
    if (check.ok) {
      const result = await check.json();
      const btn = document.getElementById('update-btn');
      if (btn) {
        if (result.available) {
          btn.classList.add('update-available');
          btn.title = `Update verfügbar: v${result.latest_version}`;
        } else {
          btn.classList.remove('update-available');
          btn.title = 'Nach Updates suchen';
        }
      }
      // Cache für Modal aktualisieren
      updateState.latestVersion = result.latest_version;
    }
  } catch (e) {
    log.debug('[update] Refresh failed:', e);
  }
}

async function checkForUpdates() {
  const modal = document.getElementById('update-modal');
  modal.style.display = 'block';
  focusTrap.activate(modal);

  // Reset state
  document.getElementById('update-checking').style.display = 'block';
  document.getElementById('update-info').style.display = 'none';
  document.getElementById('update-progress').style.display = 'none';
  document.getElementById('update-complete').style.display = 'none';
  document.getElementById('update-actions-check').style.display = 'flex';
  document.getElementById('update-actions-complete').style.display = 'none';

  updateState.checking = true;

  try {
    // Cache-Busting mit Timestamp für frische Daten
    const response = await fetch(`/api/update/check?_t=${Date.now()}`);
    const result = await response.json();

    document.getElementById('update-checking').style.display = 'none';
    document.getElementById('update-info').style.display = 'block';

    document.getElementById('update-current-version').textContent = result.current_version || '-';
    document.getElementById('update-latest-version').textContent = result.latest_version || '-';

    if (result.error) {
      document.getElementById('update-error').style.display = 'block';
      document.getElementById('update-error').textContent = result.error;
      document.getElementById('update-available').style.display = 'none';
      document.getElementById('update-current').style.display = 'none';
    } else if (result.available) {
      document.getElementById('update-available').style.display = 'block';
      document.getElementById('update-current').style.display = 'none';
      document.getElementById('update-error').style.display = 'none';
      document.getElementById('update-install-btn').style.display = 'inline-flex';

      if (result.release_notes) {
        document.getElementById('update-release-notes').innerHTML =
          `<h4>Release Notes:</h4><div>${marked.parse(result.release_notes)}</div>`;
      } else {
        document.getElementById('update-release-notes').innerHTML = '';
      }

      updateState.downloadUrl = result.download_url;
      updateState.latestVersion = result.latest_version;
    } else {
      document.getElementById('update-available').style.display = 'none';
      document.getElementById('update-current').style.display = 'block';
      document.getElementById('update-error').style.display = 'none';
      document.getElementById('update-install-btn').style.display = 'none';
    }
  } catch (e) {
    document.getElementById('update-checking').style.display = 'none';
    document.getElementById('update-info').style.display = 'block';
    document.getElementById('update-error').style.display = 'block';
    document.getElementById('update-error').textContent = `Fehler: ${e.message}`;
  } finally {
    updateState.checking = false;
  }
}

async function installUpdate() {
  if (updateState.installing) return;
  updateState.installing = true;

  document.getElementById('update-info').style.display = 'none';
  document.getElementById('update-progress').style.display = 'block';
  document.getElementById('update-install-btn').style.display = 'none';

  try {
    const response = await fetch('/api/update/install/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        download_url: updateState.downloadUrl,
        create_backup: true,
      }),
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value);
      const lines = text.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));

            if (data.success !== undefined) {
              // Final result
              if (data.success) {
                document.getElementById('update-progress').style.display = 'none';
                document.getElementById('update-complete').style.display = 'block';
                document.getElementById('update-complete-message').textContent =
                  `${data.files_updated?.length || 0} Dateien wurden aktualisiert.`;
                document.getElementById('update-actions-check').style.display = 'none';
                document.getElementById('update-actions-complete').style.display = 'flex';

                // Update button status
                const btn = document.getElementById('update-btn');
                if (btn) {
                  btn.classList.remove('update-available');
                  btn.classList.add('update-installed');
                }
              } else {
                document.getElementById('update-progress').style.display = 'none';
                document.getElementById('update-info').style.display = 'block';
                document.getElementById('update-error').style.display = 'block';
                document.getElementById('update-error').textContent = data.error || 'Update fehlgeschlagen';
              }
            } else {
              // Progress update
              document.getElementById('update-progress-stage').textContent = stageLabel(data.stage);
              document.getElementById('update-progress-fill').style.width = `${data.percent}%`;
              document.getElementById('update-progress-message').textContent = data.message;
            }
          } catch (e) {
            // Ignore parse errors
          }
        }
      }
    }
  } catch (e) {
    document.getElementById('update-progress').style.display = 'none';
    document.getElementById('update-info').style.display = 'block';
    document.getElementById('update-error').style.display = 'block';
    document.getElementById('update-error').textContent = `Fehler: ${e.message}`;
  } finally {
    updateState.installing = false;
  }
}

function stageLabel(stage) {
  const labels = {
    prepare: 'Vorbereitung',
    download: 'Download',
    analyze: 'Analyse',
    backup: 'Backup erstellen',
    install: 'Installation',
    complete: 'Abgeschlossen',
  };
  return labels[stage] || stage;
}

async function restartServer() {
  try {
    showToast('Server wird neu gestartet...', 'info');
    await fetch('/api/update/restart', { method: 'POST' });
    // Server wird sich beenden - Modal schließen
    closeUpdateModal();
    // Warte und versuche neu zu verbinden
    setTimeout(() => {
      showToast('Versuche erneut zu verbinden...', 'info');
      setTimeout(() => window.location.reload(), 3000);
    }, 2000);
  } catch (e) {
    // Erwarteter Fehler wenn Server sich beendet
    setTimeout(() => window.location.reload(), 3000);
  }
}

function closeUpdateModal() {
  focusTrap.deactivate();
  document.getElementById('update-modal').style.display = 'none';
}

// ══════════════════════════════════════════════════════════════════════════════
// Proxy Settings Section (Global Proxy Configuration)
// ══════════════════════════════════════════════════════════════════════════════

function renderProxySection() {
  const container = document.getElementById('settings-form');

  container.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">PROXY-KONFIGURATION</h3>
      <p class="settings-section-desc">
        Globale Proxy-Einstellungen für alle externen HTTP-Verbindungen.
        Wird von Web-Suche, Update-Service, Internal-Fetch und anderen Services verwendet.
      </p>

      <div id="proxy-settings-loading" style="text-align:center; padding:20px;">
        <span class="spinner"></span> Lade Konfiguration...
      </div>
      <div id="proxy-settings-form" style="display:none;"></div>
    </div>
  `;

  loadProxySettings();
}

async function loadProxySettings() {
  try {
    const response = await fetch('/api/settings');
    const data = await response.json();
    const config = data.settings?.proxy || {};

    document.getElementById('proxy-settings-loading').style.display = 'none';
    document.getElementById('proxy-settings-form').style.display = 'block';

    document.getElementById('proxy-settings-form').innerHTML = `
      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="proxy-enabled" ${config.enabled ? 'checked' : ''}>
          Proxy aktivieren
        </label>
        <small class="settings-hint">Aktiviert den Proxy für alle externen Verbindungen</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">Proxy-URL</label>
        <input type="text" id="proxy-url" class="settings-input"
          value="${escapeHtml(config.url || '')}"
          placeholder="http://proxy.intern:8080">
        <small class="settings-hint">Format: http://host:port oder https://host:port</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">Benutzername (optional)</label>
        <input type="text" id="proxy-username" class="settings-input"
          value="${config.username === '********' ? '********' : escapeHtml(config.username || '')}"
          placeholder="proxy-user">
      </div>

      <div class="settings-group">
        <label class="settings-label">Passwort (optional)</label>
        <input type="password" id="proxy-password" class="settings-input"
          value="${config.password === '********' ? '********' : ''}"
          placeholder="********">
      </div>

      <div class="settings-group">
        <label class="settings-label">No-Proxy (Ausnahmen)</label>
        <input type="text" id="proxy-no-proxy" class="settings-input"
          value="${escapeHtml(config.no_proxy || '')}"
          placeholder="localhost,127.0.0.1,.intern">
        <small class="settings-hint">Kommagetrennte Liste von Hosts ohne Proxy</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="proxy-verify-ssl" ${config.verify_ssl !== false ? 'checked' : ''}>
          SSL-Zertifikate prüfen
        </label>
        <small class="settings-hint">Deaktivieren für Corporate Proxies mit selbstsignierten Zertifikaten</small>
      </div>

      <div class="settings-actions" style="margin-top: 20px;">
        <button class="btn btn-primary" onclick="saveProxySettings()">Speichern</button>
        <button class="btn btn-secondary" onclick="testProxyConnection()">Verbindung testen</button>
      </div>

      <div id="proxy-test-result" style="margin-top: 15px;"></div>
    `;
  } catch (e) {
    document.getElementById('proxy-settings-loading').style.display = 'none';
    document.getElementById('proxy-settings-form').style.display = 'block';
    document.getElementById('proxy-settings-form').innerHTML = `
      <div class="settings-error">Fehler beim Laden: ${escapeHtml(e.message)}</div>
    `;
  }
}

async function saveProxySettings() {
  const data = {
    enabled: document.getElementById('proxy-enabled').checked,
    url: document.getElementById('proxy-url').value,
    username: document.getElementById('proxy-username').value,
    password: document.getElementById('proxy-password').value,
    no_proxy: document.getElementById('proxy-no-proxy').value,
    verify_ssl: document.getElementById('proxy-verify-ssl').checked,
  };

  // Maskierte Werte nicht überschreiben
  if (data.username === '********') delete data.username;
  if (data.password === '********' || data.password === '') delete data.password;

  try {
    // Section im URL-Pfad, nicht im Body
    const response = await fetch('/api/settings/section/proxy', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    if (response.ok) {
      showToast('Proxy-Einstellungen gespeichert', 'success');
    } else {
      const error = await response.json();
      showToast(`Fehler: ${error.detail || 'Speichern fehlgeschlagen'}`, 'error');
    }
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

async function testProxyConnection() {
  const resultEl = document.getElementById('proxy-test-result');
  resultEl.innerHTML = '<span class="spinner"></span> Teste Proxy-Verbindung...';

  try {
    const response = await fetch('/api/search/test-proxy', { method: 'POST' });
    const result = await response.json();

    if (result.success) {
      resultEl.innerHTML = `<div class="settings-success">✓ Proxy-Verbindung erfolgreich</div>`;
    } else {
      resultEl.innerHTML = `<div class="settings-error">✗ ${escapeHtml(result.error || 'Verbindung fehlgeschlagen')}</div>`;
    }
  } catch (e) {
    resultEl.innerHTML = `<div class="settings-error">✗ ${escapeHtml(e.message)}</div>`;
  }
}

function renderUpdateSection() {
  const container = document.getElementById('settings-form');

  container.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">APP-UPDATES</h3>
      <p class="settings-section-desc">
        GitHub-basierte Updates für AI-Assist. Lädt neue Versionen herunter und installiert sie automatisch.
        Konfigurationsdateien und Daten werden nicht überschrieben.
      </p>

      <div id="update-settings-loading" style="text-align:center; padding:20px;">
        <span class="spinner"></span> Lade Konfiguration...
      </div>
      <div id="update-settings-form" style="display:none;"></div>
    </div>
  `;

  loadUpdateSettings();
}

async function loadUpdateSettings() {
  try {
    const response = await fetch('/api/update/config');
    const config = await response.json();

    document.getElementById('update-settings-loading').style.display = 'none';
    document.getElementById('update-settings-form').style.display = 'block';

    document.getElementById('update-settings-form').innerHTML = `
      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="update-enabled" ${config.enabled ? 'checked' : ''}>
          Update-Service aktivieren
        </label>
      </div>

      <div class="settings-group">
        <label class="settings-label">GitHub Repository URL</label>
        <input type="text" id="update-repo-url" class="settings-input"
          value="${escapeHtml(config.repo_url)}"
          placeholder="https://github.com/user/ai-assist-releases">
        <small class="settings-hint">URL des GitHub-Repositories mit den Releases</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">Branch (optional)</label>
        <input type="text" id="update-branch" class="settings-input"
          value="${escapeHtml(config.branch || '')}"
          placeholder="main">
        <small class="settings-hint">Leer = Releases/Tags verwenden, "main" = immer neuesten Branch-Code laden</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">GitHub Token (optional, für private Repos)</label>
        <input type="password" id="update-github-token" class="settings-input"
          value="${config.has_token ? '***' : ''}"
          placeholder="ghp_xxxxxxxxxxxx">
        <small class="settings-hint">Personal Access Token mit 'repo' Berechtigung</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="update-use-proxy" ${config.use_proxy ? 'checked' : ''}>
          Globalen Proxy verwenden
        </label>
        <small class="settings-hint">
          ${config.proxy_configured
            ? `Proxy konfiguriert: ${escapeHtml(config.proxy_url)}`
            : 'Kein Proxy konfiguriert - <a href="#" onclick="showSettingsSection(\'proxy\'); return false;">Proxy einrichten</a>'}
        </small>
      </div>

      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="update-verify-ssl" ${config.verify_ssl ? 'checked' : ''}>
          SSL-Zertifikate prüfen
        </label>
      </div>

      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="update-check-on-start" ${config.check_on_start ? 'checked' : ''}>
          Beim Start nach Updates suchen
        </label>
      </div>

      <div class="settings-actions" style="margin-top: 20px;">
        <button class="btn btn-primary" onclick="saveUpdateSettings()">Speichern</button>
        <button class="btn btn-secondary" onclick="checkForUpdates()">Jetzt prüfen</button>
      </div>

      <hr style="margin: 30px 0;">

      <h4>Whitelist (wird aktualisiert)</h4>
      <div class="settings-code-block">
        ${config.include_patterns?.map(p => `<div>${escapeHtml(p)}</div>`).join('') || '-'}
      </div>

      <h4 style="margin-top: 20px;">Blacklist (wird NICHT überschrieben)</h4>
      <div class="settings-code-block">
        ${config.exclude_patterns?.map(p => `<div>${escapeHtml(p)}</div>`).join('') || '-'}
      </div>
    `;
  } catch (e) {
    document.getElementById('update-settings-loading').style.display = 'none';
    document.getElementById('update-settings-form').style.display = 'block';
    document.getElementById('update-settings-form').innerHTML = `
      <div class="settings-error">Fehler beim Laden: ${escapeHtml(e.message)}</div>
    `;
  }
}

async function saveUpdateSettings() {
  const data = {
    enabled: document.getElementById('update-enabled').checked,
    repo_url: document.getElementById('update-repo-url').value,
    branch: document.getElementById('update-branch').value,
    github_token: document.getElementById('update-github-token').value,
    use_proxy: document.getElementById('update-use-proxy').checked,
    verify_ssl: document.getElementById('update-verify-ssl').checked,
    check_on_start: document.getElementById('update-check-on-start').checked,
  };

  try {
    const response = await fetch('/api/update/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    if (response.ok) {
      showToast('Update-Einstellungen gespeichert', 'success');
      // Update button visibility
      const btn = document.getElementById('update-btn');
      if (btn) {
        btn.style.display = data.enabled ? 'inline-flex' : 'none';
      }
    } else {
      const error = await response.json();
      showToast(`Fehler: ${error.detail || 'Speichern fehlgeschlagen'}`, 'error');
    }
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

// Initialize update button on page load
document.addEventListener('DOMContentLoaded', () => {
  initUpdateButton();
});

// ══════════════════════════════════════════════════════════════════════════════
// Arena Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderArenaSettingsSection() {
  const container = document.getElementById('settings-form');

  container.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">ARENA MODE</h3>
      <p class="settings-section-desc">
        Vergleiche zwei LLM-Modelle blind gegeneinander. Die Antworten werden anonymisiert angezeigt
        und du bewertest, welche besser ist. Ideal zum Finden des besten Modells für deine Aufgaben.
      </p>

      <div id="arena-settings-loading" style="text-align:center; padding:20px;">
        <span class="spinner"></span> Lade Konfiguration...
      </div>
      <div id="arena-settings-form" style="display:none;"></div>
    </div>
  `;

  await loadArenaSettings();
}

async function loadArenaSettings() {
  try {
    // Load available models - API returns {models: [...], default: "..."}
    const modelsRes = await fetch('/api/models');
    const modelsData = modelsRes.ok ? await modelsRes.json() : { models: [] };
    const models = modelsData.models || [];

    // Load arena config
    const configRes = await fetch('/api/arena/config');
    const config = configRes.ok ? await configRes.json() : {
      enabled: false,
      modelA: '',
      modelB: '',
      autoArena: false,
      sampleRate: 1.0,
      eloKFactor: 32
    };

    document.getElementById('arena-settings-loading').style.display = 'none';
    document.getElementById('arena-settings-form').style.display = 'block';

    const modelOptions = models.map(m =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.display_name || m.id)}</option>`
    ).join('');

    document.getElementById('arena-settings-form').innerHTML = `
      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="arena-enabled" ${config.enabled ? 'checked' : ''}>
          Arena Mode aktivieren
        </label>
      </div>

      <div class="settings-group">
        <label class="settings-label">Modell A</label>
        <select id="arena-model-a" class="settings-select">
          <option value="">-- Modell wählen --</option>
          ${modelOptions}
        </select>
      </div>

      <div class="settings-group">
        <label class="settings-label">Modell B</label>
        <select id="arena-model-b" class="settings-select">
          <option value="">-- Modell wählen --</option>
          ${modelOptions}
        </select>
      </div>

      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="arena-auto" ${config.autoArena ? 'checked' : ''}>
          Auto-Arena (automatisch Matches starten)
        </label>
        <small class="settings-hint">Bei aktiviert wird bei jeder Anfrage automatisch ein Arena-Match gestartet</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">Sample Rate: <span id="arena-sample-value">${Math.round(config.sampleRate * 100)}%</span></label>
        <input type="range" id="arena-sample-rate" min="0" max="100" value="${Math.round(config.sampleRate * 100)}"
          oninput="document.getElementById('arena-sample-value').textContent = this.value + '%'">
        <small class="settings-hint">Prozentsatz der Anfragen die als Arena-Matches laufen</small>
      </div>

      <div class="settings-group">
        <label class="settings-label">ELO K-Faktor</label>
        <input type="number" id="arena-elo-k" class="settings-input" value="${config.eloKFactor}" min="1" max="64">
        <small class="settings-hint">Höhere Werte = schnellere Rating-Änderungen (Standard: 32)</small>
      </div>

      <div class="settings-actions" style="margin-top: 20px;">
        <button class="btn btn-primary" onclick="saveArenaSettings()">Speichern</button>
        <button class="btn btn-secondary" onclick="openArenaModal()">Arena öffnen</button>
      </div>
    `;

    // Set selected models
    if (config.modelA) {
      document.getElementById('arena-model-a').value = config.modelA;
    }
    if (config.modelB) {
      document.getElementById('arena-model-b').value = config.modelB;
    }

  } catch (e) {
    document.getElementById('arena-settings-loading').style.display = 'none';
    document.getElementById('arena-settings-form').style.display = 'block';
    document.getElementById('arena-settings-form').innerHTML = `
      <div class="settings-error">Fehler beim Laden: ${escapeHtml(e.message)}</div>
    `;
  }
}

async function saveArenaSettings() {
  const data = {
    enabled: document.getElementById('arena-enabled').checked,
    modelA: document.getElementById('arena-model-a').value,
    modelB: document.getElementById('arena-model-b').value,
    autoArena: document.getElementById('arena-auto').checked,
    sampleRate: parseInt(document.getElementById('arena-sample-rate').value) / 100,
    eloKFactor: parseInt(document.getElementById('arena-elo-k').value) || 32,
  };

  try {
    const response = await fetch('/api/arena/config', {
      method: 'PUT',  // Backend expects PUT, not POST
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    if (response.ok) {
      showToast('Arena-Einstellungen gespeichert', 'success');
    } else {
      const error = await response.json();
      showToast(`Fehler: ${error.detail || 'Speichern fehlgeschlagen'}`, 'error');
    }
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Analytics Settings Section
// ══════════════════════════════════════════════════════════════════════════════

async function renderAnalyticsSettingsSection() {
  const container = document.getElementById('settings-form');

  container.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">ANALYTICS</h3>
      <p class="settings-section-desc">
        Erfasst anonymisierte Nutzungsdaten zur Verbesserung der Anwendung.
        Daten werden lokal gespeichert und nicht an externe Server gesendet.
      </p>

      <div id="analytics-settings-loading" style="text-align:center; padding:20px;">
        <span class="spinner"></span> Lade Status...
      </div>
      <div id="analytics-settings-form" style="display:none;"></div>
    </div>
  `;

  await loadAnalyticsSettings();
}

async function loadAnalyticsSettings() {
  try {
    const response = await fetch('/api/analytics/status');
    const status = response.ok ? await response.json() : {
      enabled: false,
      storage_path: '',
      retention_days: 30,
      log_level: 'info',
      anonymization_enabled: true
    };

    document.getElementById('analytics-settings-loading').style.display = 'none';
    document.getElementById('analytics-settings-form').style.display = 'block';

    document.getElementById('analytics-settings-form').innerHTML = `
      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" id="analytics-enabled" ${status.enabled ? 'checked' : ''}
            onchange="toggleAnalytics(this.checked)">
          Analytics aktivieren
        </label>
      </div>

      <div class="settings-group">
        <label class="settings-label">Speicherpfad</label>
        <input type="text" class="settings-input" value="${escapeHtml(status.storage_path)}" disabled>
      </div>

      <div class="settings-group">
        <label class="settings-label">Aufbewahrung (Tage)</label>
        <input type="number" class="settings-input" value="${status.retention_days}" disabled>
      </div>

      <div class="settings-group">
        <label class="settings-label">
          <input type="checkbox" ${status.anonymization_enabled ? 'checked' : ''} disabled>
          Anonymisierung aktiviert
        </label>
        <small class="settings-hint">Sensible Daten werden automatisch entfernt</small>
      </div>

      <div class="settings-actions" style="margin-top: 20px;">
        <button class="btn btn-secondary" onclick="openDashboard()">Dashboard öffnen</button>
      </div>

      <hr style="margin: 30px 0;">

      <h4>Daten-Management</h4>
      <div class="settings-actions" style="margin-top: 12px; gap: 8px;">
        <button class="btn btn-secondary" onclick="exportAnalytics()">Daten exportieren</button>
        <button class="btn btn-danger" onclick="cleanupAnalytics()">Alte Daten löschen</button>
      </div>
    `;

  } catch (e) {
    document.getElementById('analytics-settings-loading').style.display = 'none';
    document.getElementById('analytics-settings-form').style.display = 'block';
    document.getElementById('analytics-settings-form').innerHTML = `
      <div class="settings-error">Fehler beim Laden: ${escapeHtml(e.message)}</div>
    `;
  }
}

async function toggleAnalytics(enabled) {
  try {
    const response = await fetch('/api/analytics/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });

    if (response.ok) {
      showToast(enabled ? 'Analytics aktiviert' : 'Analytics deaktiviert', 'success');
    } else {
      showToast('Fehler beim Umschalten', 'error');
    }
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

async function exportAnalytics() {
  try {
    const response = await fetch('/api/analytics/export?format=json');
    if (response.ok) {
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `analytics_export_${new Date().toISOString().split('T')[0]}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showToast('Export gestartet', 'success');
    } else {
      showToast('Export fehlgeschlagen', 'error');
    }
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

async function cleanupAnalytics() {
  if (!confirm('Alte Analytics-Daten wirklich löschen?')) return;

  try {
    const response = await fetch('/api/analytics/maintenance/cleanup', {
      method: 'POST',
    });

    if (response.ok) {
      const result = await response.json();
      showToast(`${result.deleted_count || 0} alte Einträge gelöscht`, 'success');
    } else {
      showToast('Cleanup fehlgeschlagen', 'error');
    }
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'error');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Task Progress Panel - Live-Fortschrittsanzeige für Agent-Tasks
// ══════════════════════════════════════════════════════════════════════════════

const taskProgressPanel = {
  container: null,
  tasks: new Map(),
  expanded: new Set(),
  eventSource: null,
  sessionId: null,

  /**
   * Initialisiert das Panel
   */
  init(containerId = 'task-progress-container') {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      // Container dynamisch erstellen wenn nicht vorhanden
      this.container = document.createElement('div');
      this.container.id = containerId;
      this.container.className = 'task-progress-container';
      // Nach dem Input-Bereich einfügen
      const inputArea = document.getElementById('input-area');
      if (inputArea) {
        inputArea.parentNode.insertBefore(this.container, inputArea);
      }
    }
  },

  /**
   * Startet SSE-Stream für eine Session
   */
  connect(sessionId) {
    if (this.eventSource) {
      this.eventSource.close();
    }

    this.sessionId = sessionId;
    this.eventSource = new EventSource(`/api/tasks/${sessionId}/stream`);

    this.eventSource.addEventListener('task_snapshot', (e) => {
      const task = JSON.parse(e.data);
      this.tasks.set(task.task_id, task);
      this.render();
    });

    this.eventSource.addEventListener('task_started', (e) => {
      const data = JSON.parse(e.data);
      this.tasks.set(data.task_id, data);
      this.render();
    });

    this.eventSource.addEventListener('step_started', (e) => {
      const data = JSON.parse(e.data);
      this._updateTask(data.task_id, task => {
        if (task.steps && task.steps[data.step_index]) {
          task.steps[data.step_index] = data.step;
        }
        task.current_step = data.step.name;
        task.current_step_index = data.step_index;
      });
    });

    this.eventSource.addEventListener('step_progress', (e) => {
      const data = JSON.parse(e.data);
      this._updateTask(data.task_id, task => {
        if (task.steps && task.steps[data.step_index]) {
          task.steps[data.step_index].progress = data.progress;
          task.steps[data.step_index].details = data.details;
        }
        task.progress_percent = data.total_progress;
      });
    });

    this.eventSource.addEventListener('step_completed', (e) => {
      const data = JSON.parse(e.data);
      this._updateTask(data.task_id, task => {
        if (task.steps && task.steps[data.step_index]) {
          task.steps[data.step_index] = data.step;
        }
        task.completed_steps = (task.completed_steps || 0) + 1;
        task.progress_percent = data.total_progress;
        task.estimated_remaining_seconds = data.estimated_remaining;
      });
    });

    this.eventSource.addEventListener('step_failed', (e) => {
      const data = JSON.parse(e.data);
      this._updateTask(data.task_id, task => {
        if (task.steps && task.steps[data.step_index]) {
          task.steps[data.step_index] = data.step;
        }
      });
    });

    this.eventSource.addEventListener('task_artifact', (e) => {
      const data = JSON.parse(e.data);
      this._updateTask(data.task_id, task => {
        if (!task.artifacts) task.artifacts = [];
        task.artifacts.push(data.artifact);
      });
    });

    this.eventSource.addEventListener('task_completed', (e) => {
      const data = JSON.parse(e.data);
      this.tasks.set(data.task_id, data);
      this.render();
      // Nach 5 Sekunden ausblenden
      setTimeout(() => {
        this.tasks.delete(data.task_id);
        this.render();
      }, 5000);
    });

    this.eventSource.addEventListener('task_failed', (e) => {
      const data = JSON.parse(e.data);
      this.tasks.set(data.task_id, data);
      this.render();
    });

    this.eventSource.addEventListener('task_cancelled', (e) => {
      const data = JSON.parse(e.data);
      this.tasks.delete(data.task_id);
      this.render();
    });

    this.eventSource.onerror = () => {
      // Nur reconnecten wenn dies noch die aktive Session ist
      if (this.sessionId === sessionId) {
        log.warn('[TaskProgress] SSE connection error, reconnecting...');
        setTimeout(() => {
          // Nochmal prüfen vor reconnect
          if (this.sessionId === sessionId) {
            this.connect(sessionId);
          }
        }, 3000);
      }
    };
  },

  /**
   * Trennt die SSE-Verbindung
   */
  disconnect() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.tasks.clear();
    this.render();
  },

  /**
   * Aktualisiert einen Task
   */
  _updateTask(taskId, updater) {
    const task = this.tasks.get(taskId);
    if (task) {
      updater(task);
      this.render();
    }
  },

  /**
   * Rendert alle aktiven Tasks
   */
  render() {
    if (!this.container) return;

    if (this.tasks.size === 0) {
      this.container.innerHTML = '';
      this.container.style.display = 'none';
      return;
    }

    this.container.style.display = 'block';

    const tasksHtml = Array.from(this.tasks.values())
      .map(task => this._renderTask(task))
      .join('');

    this.container.innerHTML = tasksHtml;
  },

  /**
   * Rendert einen einzelnen Task
   */
  _renderTask(task) {
    const statusClass = task.status || 'running';
    const isExpanded = this.expanded.has(task.task_id);

    const stepsHtml = (task.steps || []).map((step, i) => this._renderStep(step, i)).join('');

    const artifactsHtml = (task.artifacts && task.artifacts.length > 0) ? `
      <div class="task-artifacts">
        <button class="task-artifacts-toggle" onclick="taskProgressPanel.toggleArtifacts('${task.task_id}')">
          ${isExpanded ? '&#9660;' : '&#9658;'} Zwischenergebnisse (${task.artifacts.length})
        </button>
        ${isExpanded ? `
          <div class="task-artifacts-list">
            ${task.artifacts.map(a => this._renderArtifact(a)).join('')}
          </div>
        ` : ''}
      </div>
    ` : '';

    return `
      <div class="task-progress-card ${statusClass}" data-task-id="${task.task_id}">
        <div class="task-header">
          <span class="task-title">${escapeHtml(task.title)}</span>
          <span class="task-time">${this._formatRemaining(task.estimated_remaining_seconds)}</span>
          ${task.status === 'running' ? `
            <button class="task-cancel" onclick="taskProgressPanel.cancel('${task.task_id}')" title="Abbrechen">
              &#10005;
            </button>
          ` : ''}
        </div>

        <div class="task-progress-bar">
          <div class="task-progress-fill" style="width: ${task.progress_percent || 0}%"></div>
          <span class="task-progress-text">${Math.round(task.progress_percent || 0)}%</span>
        </div>

        <div class="task-steps">
          ${stepsHtml}
        </div>

        ${artifactsHtml}

        ${task.error ? `<div class="task-error">${escapeHtml(task.error)}</div>` : ''}
      </div>
    `;
  },

  /**
   * Rendert einen Schritt
   */
  _renderStep(step, index) {
    const icons = {
      pending: '&#9675;',   // ○
      running: '&#10227;',  // ⟳
      completed: '&#10003;', // ✓
      failed: '&#10007;',   // ✗
      skipped: '&#9676;',   // ◌
    };

    const icon = icons[step.status] || icons.pending;
    const statusClass = step.status || 'pending';

    return `
      <div class="task-step ${statusClass}">
        <span class="step-icon">${icon}</span>
        <span class="step-name">${escapeHtml(step.name)}</span>
        ${step.details ? `<span class="step-details">${escapeHtml(step.details)}</span>` : ''}
        ${step.status === 'running' && step.progress > 0 && step.progress < 1 ? `
          <span class="step-progress">${Math.round(step.progress * 100)}%</span>
        ` : ''}
      </div>
    `;
  },

  /**
   * Rendert ein Artifact
   */
  _renderArtifact(artifact) {
    return `
      <div class="task-artifact">
        <span class="artifact-type">${escapeHtml(artifact.type)}</span>
        <span class="artifact-content">${escapeHtml(artifact.summary || '')}</span>
      </div>
    `;
  },

  /**
   * Formatiert verbleibende Zeit
   */
  _formatRemaining(seconds) {
    if (!seconds || seconds <= 0) return '';
    if (seconds < 60) return `~${seconds}s`;
    return `~${Math.round(seconds / 60)} min`;
  },

  /**
   * Toggled Artifacts-Ansicht
   */
  toggleArtifacts(taskId) {
    if (this.expanded.has(taskId)) {
      this.expanded.delete(taskId);
    } else {
      this.expanded.add(taskId);
    }
    this.render();
  },

  /**
   * Bricht einen Task ab
   */
  async cancel(taskId) {
    try {
      const response = await fetch(`/api/tasks/${this.sessionId}/${taskId}/cancel`, {
        method: 'POST',
      });

      if (response.ok) {
        showToast('Task abgebrochen', 'info');
      } else {
        const error = await response.json();
        showToast(error.message || 'Abbruch fehlgeschlagen', 'error');
      }
    } catch (e) {
      showToast(`Fehler: ${e.message}`, 'error');
    }
  },
};

// Initialisiere Task-Panel beim Laden
document.addEventListener('DOMContentLoaded', () => {
  taskProgressPanel.init();
});
