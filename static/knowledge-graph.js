/**
 * Knowledge Graph Viewer
 *
 * Interaktive D3.js-basierte Visualisierung des Code-Graphs.
 */

class KnowledgeGraphViewer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      console.error('[KnowledgeGraph] Container not found:', containerId);
      return;
    }

    this.svg = null;
    this.g = null;
    this.simulation = null;
    this.nodes = [];
    this.edges = [];
    this.selectedNode = null;
    this.layout = 'force'; // force, tree, radial
    this.zoom = null;
    this.currentCenter = null;
    this.currentDepth = 2;
    this.hideAccessors = true;  // Getter/Setter standardmäßig ausblenden

    // Node colors by type
    this.colors = {
      'class': '#6366f1',
      'interface': '#8b5cf6',
      'method': '#22c55e',
      'field': '#f59e0b',
      'table': '#ef4444',
      'file': '#64748b',
      'package': '#0ea5e9',
      'enum': '#ec4899',
      'annotation': '#14b8a6'
    };

    // Node sizes by type
    this.sizes = {
      'class': 14,
      'interface': 12,
      'method': 7,
      'field': 6,
      'table': 12,
      'package': 16,
      'enum': 10,
      'annotation': 8
    };

    this._initControls();
  }

  _initControls() {
    // Control bar erstellen
    const controls = document.createElement('div');
    controls.className = 'kg-controls';
    controls.innerHTML = `
      <div class="kg-graph-selector">
        <select id="kg-graph-select" title="Graph auswählen">
          <option value="">-- Graph wählen --</option>
        </select>
        <button id="kg-edit-graph" title="Graph umbenennen">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <button id="kg-new-graph" title="Neuen Graph erstellen">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14"/>
          </svg>
        </button>
        <button id="kg-delete-graph" title="Graph löschen" class="kg-btn-danger">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
          </svg>
        </button>
      </div>

      <!-- Graph Modal -->
      <div id="kg-graph-modal" class="kg-modal" style="display:none;">
        <div class="kg-modal-content">
          <div class="kg-modal-header">
            <h3 id="kg-modal-title">Neuer Graph</h3>
            <button class="kg-modal-close" id="kg-modal-close">&times;</button>
          </div>
          <div class="kg-modal-body">
            <div class="kg-form-group" id="kg-modal-existing-group">
              <label for="kg-modal-existing">Vorhandene Repos:</label>
              <select id="kg-modal-existing">
                <option value="">-- Neues Repo eingeben --</option>
              </select>
              <small>Wähle ein bestehendes Repo oder gib einen neuen Pfad ein</small>
            </div>
            <div class="kg-form-group">
              <label for="kg-modal-path">Repository / Pfad:</label>
              <input type="text" id="kg-modal-path" placeholder="C:/projects/my-repo oder /home/user/project" />
              <small>Der Pfad zum Quellcode der indexiert werden soll</small>
            </div>
            <div class="kg-form-group">
              <label for="kg-modal-name">Graph-Name:</label>
              <input type="text" id="kg-modal-name" placeholder="Wird aus Pfad abgeleitet..." />
              <small>Name zur Identifizierung des Graphen</small>
            </div>
            <div class="kg-form-group">
              <label for="kg-modal-language">Sprache:</label>
              <select id="kg-modal-language">
                <option value="java">Java</option>
                <option value="python">Python</option>
                <option value="typescript">TypeScript/JavaScript</option>
              </select>
            </div>
          </div>
          <div class="kg-modal-footer">
            <button id="kg-modal-cancel" class="kg-btn-secondary">Abbrechen</button>
            <button id="kg-modal-submit" class="kg-btn-primary">Erstellen & Indexieren</button>
          </div>
        </div>
      </div>
      <div class="kg-search">
        <input type="text" id="kg-search-input" placeholder="Suche Klasse/Interface..." autocomplete="off" />
        <button id="kg-search-btn" title="Suchen">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/>
            <path d="M21 21l-4.35-4.35"/>
          </svg>
        </button>
        <div id="kg-search-results" class="kg-search-results"></div>
      </div>
      <div class="kg-actions">
        <button id="kg-zoom-in" title="Zoom In">+</button>
        <button id="kg-zoom-out" title="Zoom Out">-</button>
        <button id="kg-reset" title="Reset View">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
            <path d="M3 3v5h5"/>
          </svg>
        </button>
        <select id="kg-depth" title="Graph-Tiefe">
          <option value="1">Tiefe: 1</option>
          <option value="2" selected>Tiefe: 2</option>
          <option value="3">Tiefe: 3</option>
        </select>
        <label class="kg-filter-label" title="Getter/Setter/Is-Methoden ausblenden">
          <input type="checkbox" id="kg-hide-accessors" checked />
          <span>Accessors ausblenden</span>
        </label>
      </div>
      <div class="kg-info" id="kg-info">
        <span id="kg-node-count">0 Nodes</span>
        <span id="kg-edge-count">0 Edges</span>
      </div>
    `;
    this.container.insertBefore(controls, this.container.firstChild);

    // Graph list laden
    this._loadGraphList();

    // Search debounce
    this._searchTimeout = null;
    this._selectedResultIndex = -1;

    // Event Listeners
    const searchInput = document.getElementById('kg-search-input');
    searchInput?.addEventListener('input', (e) => this._handleSearchInput(e.target.value));
    searchInput?.addEventListener('keydown', (e) => this._handleSearchKeydown(e));
    searchInput?.addEventListener('blur', () => {
      // Delay to allow click on results
      setTimeout(() => this._hideSearchResults(), 200);
    });

    document.getElementById('kg-search-btn')?.addEventListener('click', () => this._handleSearch());
    document.getElementById('kg-zoom-in')?.addEventListener('click', () => this._zoomIn());
    document.getElementById('kg-zoom-out')?.addEventListener('click', () => this._zoomOut());
    document.getElementById('kg-reset')?.addEventListener('click', () => this._resetView());
    document.getElementById('kg-depth')?.addEventListener('change', (e) => {
      this.currentDepth = parseInt(e.target.value);
      if (this.currentCenter) {
        this.loadSubgraph(this.currentCenter, this.currentDepth);
      }
    });

    document.getElementById('kg-hide-accessors')?.addEventListener('change', (e) => {
      this.hideAccessors = e.target.checked;
      if (this.nodes.length > 0) {
        this.render();  // Re-render mit neuem Filter
      }
    });

    // Graph selector events
    document.getElementById('kg-graph-select')?.addEventListener('change', (e) => {
      const graphId = e.target.value;
      if (graphId) {
        this._switchGraph(graphId);
      }
    });

    document.getElementById('kg-edit-graph')?.addEventListener('click', () => {
      this._showEditGraphDialog();
    });

    document.getElementById('kg-new-graph')?.addEventListener('click', () => {
      this._showNewGraphModal();
    });

    document.getElementById('kg-delete-graph')?.addEventListener('click', () => {
      this._deleteCurrentGraph();
    });

    // Modal events
    document.getElementById('kg-modal-close')?.addEventListener('click', () => {
      this._hideModal();
    });

    document.getElementById('kg-modal-cancel')?.addEventListener('click', () => {
      this._hideModal();
    });

    document.getElementById('kg-modal-submit')?.addEventListener('click', () => {
      this._handleModalSubmit();
    });

    // Path input -> auto-derive name
    document.getElementById('kg-modal-path')?.addEventListener('input', (e) => {
      const path = e.target.value;
      const nameInput = document.getElementById('kg-modal-name');
      // Nur automatisch setzen wenn Name leer oder auto-generiert
      if (nameInput && (!nameInput.value || nameInput.dataset.autoGenerated === 'true')) {
        const derivedName = this._deriveNameFromPath(path);
        if (derivedName) {
          nameInput.value = derivedName;
          nameInput.dataset.autoGenerated = 'true';
        }
      }
    });

    document.getElementById('kg-modal-name')?.addEventListener('input', (e) => {
      // Wenn User manuell tippt, nicht mehr auto-generieren
      if (e.target.value) {
        e.target.dataset.autoGenerated = 'false';
      }
    });

    // Existing repo dropdown -> fill path and name
    document.getElementById('kg-modal-existing')?.addEventListener('change', (e) => {
      const selected = e.target.options[e.target.selectedIndex];
      const path = selected?.dataset?.path || '';
      const name = selected?.dataset?.name || '';

      const pathInput = document.getElementById('kg-modal-path');
      const nameInput = document.getElementById('kg-modal-name');

      if (path && pathInput) {
        pathInput.value = path;
      }
      if (name && nameInput) {
        nameInput.value = name + ' (Kopie)';
        nameInput.dataset.autoGenerated = 'true';
      }
    });

    // Modal schließen bei Klick außerhalb
    document.getElementById('kg-graph-modal')?.addEventListener('click', (e) => {
      if (e.target.id === 'kg-graph-modal') {
        this._hideModal();
      }
    });
  }

  _deriveNameFromPath(path) {
    if (!path) return '';
    // Normalisiere Pfad
    const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '');
    const parts = normalized.split('/');
    // Letzter Ordner-Name
    const lastPart = parts[parts.length - 1];
    if (lastPart) {
      // Kapitalisiere ersten Buchstaben
      return lastPart.charAt(0).toUpperCase() + lastPart.slice(1);
    }
    return '';
  }

  async _loadGraphList() {
    try {
      const [graphsRes, activeRes] = await Promise.all([
        fetch('/api/graph/graphs'),
        fetch('/api/graph/graphs/active')
      ]);

      if (!graphsRes.ok || !activeRes.ok) return;

      const graphs = await graphsRes.json();
      const activeData = await activeRes.json();
      this.activeGraphId = activeData.active?.id || null;

      const select = document.getElementById('kg-graph-select');
      if (!select) return;

      select.innerHTML = graphs.length === 0
        ? '<option value="">-- Kein Graph --</option>'
        : graphs.map(g => `
            <option value="${g.id}" ${g.id === this.activeGraphId ? 'selected' : ''}>
              ${g.name} (${g.node_count} Nodes)
            </option>
          `).join('');

      // Delete-Button nur aktiv wenn Graphs vorhanden
      const deleteBtn = document.getElementById('kg-delete-graph');
      if (deleteBtn) {
        deleteBtn.disabled = graphs.length === 0;
      }

    } catch (e) {
      console.error('[KnowledgeGraph] Failed to load graph list:', e);
    }
  }

  async _switchGraph(graphId) {
    try {
      const response = await fetch(`/api/graph/graphs/${graphId}/activate`, {
        method: 'POST'
      });

      if (!response.ok) throw new Error('Switch failed');

      this.activeGraphId = graphId;
      this._showMessage('Graph gewechselt');

      // Reset view
      this.nodes = [];
      this.edges = [];
      this.currentCenter = null;

      // Graph-Container leeren
      const graphContainer = this.container?.querySelector('.kg-graph');
      if (graphContainer) graphContainer.remove();

      // Stats neu laden
      const stats = await this.loadStats();
      if (stats) {
        this._updateInfoFiltered(stats.total_nodes, stats.total_edges);
      }

    } catch (e) {
      console.error('[KnowledgeGraph] Switch failed:', e);
      this._showMessage('Wechsel fehlgeschlagen');
    }
  }

  async _showNewGraphModal() {
    this._modalMode = 'create';
    const modal = document.getElementById('kg-graph-modal');
    const title = document.getElementById('kg-modal-title');
    const pathInput = document.getElementById('kg-modal-path');
    const nameInput = document.getElementById('kg-modal-name');
    const submitBtn = document.getElementById('kg-modal-submit');
    const langGroup = document.getElementById('kg-modal-language')?.parentElement;
    const existingGroup = document.getElementById('kg-modal-existing-group');
    const existingSelect = document.getElementById('kg-modal-existing');

    if (title) title.textContent = 'Neuer Graph';
    if (pathInput) {
      pathInput.value = '';
      pathInput.disabled = false;
      pathInput.placeholder = 'C:/projects/my-repo oder /home/user/project';
    }
    if (nameInput) {
      nameInput.value = '';
      nameInput.dataset.autoGenerated = 'true';
    }
    if (submitBtn) submitBtn.textContent = 'Erstellen & Indexieren';
    if (langGroup) langGroup.style.display = '';
    if (existingGroup) existingGroup.style.display = '';
    // Pfad-Gruppe sichtbar im Create-Modus
    if (pathInput?.parentElement) pathInput.parentElement.style.display = '';

    // Repos aus Settings laden (Java + Python)
    if (existingSelect) {
      existingSelect.innerHTML = '<option value="">-- Pfad manuell eingeben --</option>';
      try {
        // Beide Sprachen abfragen
        const [javaRes, pythonRes] = await Promise.all([
          fetch('/api/settings/repos/java'),
          fetch('/api/settings/repos/python')
        ]);

        const allRepos = [];

        if (javaRes.ok) {
          const javaData = await javaRes.json();
          if (javaData.repos) {
            for (const r of javaData.repos) {
              allRepos.push({ ...r, language: 'java' });
            }
          }
        }

        if (pythonRes.ok) {
          const pythonData = await pythonRes.json();
          if (pythonData.repos) {
            for (const r of pythonData.repos) {
              allRepos.push({ ...r, language: 'python' });
            }
          }
        }

        console.log('[KnowledgeGraph] Loaded repos from settings:', allRepos.length);

        for (const repo of allRepos) {
          if (repo.path) {
            const option = document.createElement('option');
            option.value = repo.path;
            option.textContent = `${repo.name} (${repo.language})`;
            option.dataset.path = repo.path;
            option.dataset.name = repo.name;
            option.dataset.language = repo.language;
            existingSelect.appendChild(option);
          }
        }

        // Falls keine Repos konfiguriert
        if (allRepos.length === 0) {
          const emptyOption = document.createElement('option');
          emptyOption.disabled = true;
          emptyOption.textContent = '-- Keine Repos in Einstellungen --';
          existingSelect.appendChild(emptyOption);
        }
      } catch (e) {
        console.error('[KnowledgeGraph] Failed to load repos from settings:', e);
      }
    }

    if (modal) modal.style.display = 'flex';

    // Focus auf Pfad-Input
    setTimeout(() => pathInput?.focus(), 100);
  }

  _showEditGraphDialog() {
    const select = document.getElementById('kg-graph-select');
    const graphId = select?.value;

    if (!graphId) {
      this._showMessage('Kein Graph ausgewählt');
      return;
    }

    const selectedOption = select.options[select.selectedIndex];
    const currentName = selectedOption?.textContent?.replace(/\s*\(\d+ Nodes\)$/, '').trim() || '';

    this._modalMode = 'edit';
    this._editingGraphId = graphId;

    const modal = document.getElementById('kg-graph-modal');
    const title = document.getElementById('kg-modal-title');
    const pathInput = document.getElementById('kg-modal-path');
    const nameInput = document.getElementById('kg-modal-name');
    const submitBtn = document.getElementById('kg-modal-submit');
    const langGroup = document.getElementById('kg-modal-language')?.parentElement;
    const existingGroup = document.getElementById('kg-modal-existing-group');

    if (title) title.textContent = 'Graph umbenennen';
    if (pathInput) {
      pathInput.value = '';
      pathInput.disabled = true;
      pathInput.placeholder = '(Pfad kann nicht geändert werden)';
    }
    if (nameInput) {
      nameInput.value = currentName;
      nameInput.dataset.autoGenerated = 'false';
    }
    if (submitBtn) submitBtn.textContent = 'Speichern';
    if (existingGroup) existingGroup.style.display = 'none';
    if (langGroup) langGroup.style.display = 'none';
    // Pfad-Gruppe auch ausblenden im Edit-Modus
    if (pathInput?.parentElement) pathInput.parentElement.style.display = 'none';

    if (modal) modal.style.display = 'flex';

    // Focus auf Name-Input
    setTimeout(() => nameInput?.focus(), 100);
  }

  _hideModal() {
    const modal = document.getElementById('kg-graph-modal');
    if (modal) modal.style.display = 'none';
    this._modalMode = null;
    this._editingGraphId = null;
  }

  async _handleModalSubmit() {
    if (this._modalMode === 'create') {
      await this._createNewGraph();
    } else if (this._modalMode === 'edit') {
      await this._renameGraph();
    }
  }

  async _createNewGraph() {
    const pathInput = document.getElementById('kg-modal-path');
    const nameInput = document.getElementById('kg-modal-name');
    const langSelect = document.getElementById('kg-modal-language');

    const path = pathInput?.value?.trim() || '';
    const name = nameInput?.value?.trim() || this._deriveNameFromPath(path) || 'Neuer Graph';
    const language = langSelect?.value || 'java';

    if (!path) {
      this._showMessage('Bitte Pfad angeben');
      pathInput?.focus();
      return;
    }

    this._hideModal();
    this._showMessage('Graph wird erstellt...');

    try {
      // 1. Graph erstellen
      const createResponse = await fetch('/api/graph/graphs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, path })
      });

      if (!createResponse.ok) throw new Error('Create failed');

      const graph = await createResponse.json();
      this._showMessage(`Graph "${graph.name}" erstellt, indexiere...`);

      // 2. Zum neuen Graph wechseln
      await this._switchGraph(graph.id);

      // 3. Indexierung starten
      const indexResponse = await fetch(`/api/graph/index?path=${encodeURIComponent(path)}&language=${language}&clear=true`, {
        method: 'POST'
      });

      if (indexResponse.ok) {
        const result = await indexResponse.json();
        this._showMessage(`Indexiert: ${result.nodes_added} Nodes, ${result.edges_added} Edges`);
        await this._loadGraphList();
      } else {
        this._showMessage('Indexierung fehlgeschlagen');
      }

    } catch (e) {
      console.error('[KnowledgeGraph] Create failed:', e);
      this._showMessage('Erstellen fehlgeschlagen');
    }
  }

  async _renameGraph() {
    const nameInput = document.getElementById('kg-modal-name');
    const newName = nameInput?.value?.trim();

    if (!newName) {
      this._showMessage('Bitte Namen angeben');
      nameInput?.focus();
      return;
    }

    if (!this._editingGraphId) {
      this._hideModal();
      return;
    }

    this._hideModal();

    try {
      const response = await fetch(`/api/graph/graphs/${this._editingGraphId}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName })
      });

      if (!response.ok) throw new Error('Rename failed');

      this._showMessage(`Graph umbenannt zu "${newName}"`);
      await this._loadGraphList();

    } catch (e) {
      console.error('[KnowledgeGraph] Rename failed:', e);
      this._showMessage('Umbenennen fehlgeschlagen');
    }
  }

  async _deleteCurrentGraph() {
    const select = document.getElementById('kg-graph-select');
    const graphId = select?.value;

    if (!graphId) {
      this._showMessage('Kein Graph ausgewählt');
      return;
    }

    const selectedOption = select.options[select.selectedIndex];
    const graphName = selectedOption?.textContent || graphId;

    if (!confirm(`Graph "${graphName}" wirklich löschen?\n\nDiese Aktion kann nicht rückgängig gemacht werden!`)) {
      return;
    }

    try {
      const response = await fetch(`/api/graph/graphs/${graphId}`, {
        method: 'DELETE'
      });

      if (!response.ok) throw new Error('Delete failed');

      this._showMessage('Graph gelöscht');

      // Reset view
      this.nodes = [];
      this.edges = [];
      this.currentCenter = null;

      const graphContainer = this.container?.querySelector('.kg-graph');
      if (graphContainer) graphContainer.remove();

      // Graph-Liste neu laden
      await this._loadGraphList();

    } catch (e) {
      console.error('[KnowledgeGraph] Delete failed:', e);
      this._showMessage('Löschen fehlgeschlagen');
    }
  }

  /**
   * Prüft ob ein Node ein Accessor (Getter/Setter/Is) ist.
   */
  _isAccessorMethod(node) {
    if (node.type !== 'method') return false;
    const name = node.name || '';
    // Java-Style: getXxx, setXxx, isXxx
    // Auch: hashCode, equals, toString (Standard-Methoden)
    return /^(get|set|is)[A-Z]/.test(name) ||
           /^(hashCode|equals|toString|clone|compareTo)$/.test(name);
  }

  /**
   * Filtert Nodes basierend auf aktuellen Einstellungen.
   */
  _filterNodes(nodes) {
    if (!this.hideAccessors) return nodes;
    return nodes.filter(n => !this._isAccessorMethod(n));
  }

  /**
   * Filtert Edges basierend auf gefilterten Nodes.
   */
  _filterEdges(edges, nodeIds) {
    return edges.filter(e => nodeIds.has(e.from_id) && nodeIds.has(e.to_id));
  }

  _handleSearchInput(query) {
    clearTimeout(this._searchTimeout);

    if (!query || query.length < 2) {
      this._hideSearchResults();
      return;
    }

    // Debounce search
    this._searchTimeout = setTimeout(() => this._performSearch(query), 300);
  }

  async _performSearch(query) {
    try {
      const response = await fetch(`/api/graph/search?q=${encodeURIComponent(query)}&limit=20`);
      if (!response.ok) throw new Error('Search failed');

      const results = await response.json();
      this._showSearchResults(results, query);
    } catch (e) {
      console.error('[KnowledgeGraph] Search error:', e);
      this._showSearchResults([], query);
    }
  }

  _showSearchResults(results, query) {
    const container = document.getElementById('kg-search-results');
    if (!container) return;

    this._selectedResultIndex = -1;
    this._searchResults = results;

    if (results.length === 0) {
      container.innerHTML = `
        <div class="kg-search-no-results">
          Keine Ergebnisse für "<strong>${query}</strong>"
          <div class="kg-search-hint">
            Prüfe ob das Workspace indexiert ist (Einstellungen → Graph indexieren)
          </div>
        </div>
      `;
      container.style.display = 'block';
      return;
    }

    container.innerHTML = results.map((node, index) => {
      // Name highlighting
      const name = this._highlightMatch(node.name, query);
      const typeIcon = this._getTypeIcon(node.type);
      const typeClass = `kg-type-${node.type}`;

      return `
        <div class="kg-search-result" data-index="${index}" data-id="${node.id}">
          <span class="kg-search-result-icon ${typeClass}">${typeIcon}</span>
          <div class="kg-search-result-info">
            <div class="kg-search-result-header">
              <span class="kg-search-result-name">${name}</span>
              <span class="kg-search-result-type">${node.type}</span>
            </div>
            ${node.file_path ? `<span class="kg-search-result-path">${this._shortenPath(node.file_path)}</span>` : ''}
          </div>
        </div>
      `;
    }).join('');

    // Click handlers
    container.querySelectorAll('.kg-search-result').forEach(el => {
      el.addEventListener('click', () => {
        const nodeId = el.dataset.id;
        this._selectSearchResult(nodeId);
      });
    });

    container.style.display = 'block';
  }

  _highlightMatch(text, query) {
    const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark>$1</mark>');
  }

  _getTypeIcon(type) {
    const icons = {
      'class': 'C',
      'interface': 'I',
      'method': 'm',
      'field': 'f',
      'table': 'T',
      'file': '📄',
      'package': '📦',
      'enum': 'E',
      'annotation': '@'
    };
    return icons[type] || '○';
  }

  _shortenPath(path) {
    if (!path) return '';
    const parts = path.replace(/\\/g, '/').split('/');
    if (parts.length > 3) {
      return '.../' + parts.slice(-2).join('/');
    }
    return path;
  }

  _handleSearchKeydown(e) {
    const container = document.getElementById('kg-search-results');
    if (!container || container.style.display !== 'block') {
      if (e.key === 'Enter') this._handleSearch();
      return;
    }

    const results = container.querySelectorAll('.kg-search-result');

    switch(e.key) {
      case 'ArrowDown':
        e.preventDefault();
        this._selectedResultIndex = Math.min(this._selectedResultIndex + 1, results.length - 1);
        this._updateResultSelection(results);
        break;
      case 'ArrowUp':
        e.preventDefault();
        this._selectedResultIndex = Math.max(this._selectedResultIndex - 1, 0);
        this._updateResultSelection(results);
        break;
      case 'Enter':
        e.preventDefault();
        if (this._selectedResultIndex >= 0 && this._searchResults) {
          this._selectSearchResult(this._searchResults[this._selectedResultIndex].id);
        }
        break;
      case 'Escape':
        this._hideSearchResults();
        break;
    }
  }

  _updateResultSelection(results) {
    results.forEach((el, i) => {
      el.classList.toggle('selected', i === this._selectedResultIndex);
      if (i === this._selectedResultIndex) {
        el.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  _selectSearchResult(nodeId) {
    this._hideSearchResults();
    document.getElementById('kg-search-input').value = '';
    this.loadSubgraph(nodeId, this.currentDepth);
  }

  _hideSearchResults() {
    const container = document.getElementById('kg-search-results');
    if (container) {
      container.style.display = 'none';
      container.innerHTML = '';
    }
    this._searchResults = null;
    this._selectedResultIndex = -1;
  }

  async _handleSearch() {
    const input = document.getElementById('kg-search-input');
    const query = input?.value?.trim();
    if (!query) return;

    // If search results are visible and one is selected, use it
    if (this._selectedResultIndex >= 0 && this._searchResults) {
      this._selectSearchResult(this._searchResults[this._selectedResultIndex].id);
      return;
    }

    // Otherwise perform search and show results
    await this._performSearch(query);
  }

  _showMessage(msg) {
    const info = document.getElementById('kg-info');
    if (info) {
      const original = info.innerHTML;
      info.innerHTML = `<span class="kg-message">${msg}</span>`;
      setTimeout(() => { info.innerHTML = original; }, 3000);
    }
  }

  async loadSubgraph(centerId, depth = 2) {
    console.log('[KnowledgeGraph] loadSubgraph called:', centerId, 'depth:', depth);

    this.currentCenter = centerId;
    this.currentDepth = depth;

    try {
      const url = `/api/graph/subgraph?center=${encodeURIComponent(centerId)}&depth=${depth}`;
      console.log('[KnowledgeGraph] Fetching:', url);

      const response = await fetch(url);
      if (!response.ok) {
        const errorText = await response.text();
        console.error('[KnowledgeGraph] API error:', response.status, errorText);
        throw new Error(`Failed to load subgraph: ${response.status}`);
      }

      const data = await response.json();
      console.log('[KnowledgeGraph] Loaded:', data.nodes?.length, 'nodes,', data.edges?.length, 'edges');

      this.nodes = data.nodes || [];
      this.edges = data.edges || [];

      if (this.nodes.length === 0) {
        this._showMessage(`Keine Daten für "${centerId}" gefunden`);
        return;
      }

      this._updateInfo();
      this.render();
      this._showMessage(`Center: ${centerId.split('.').pop()}`);
    } catch (e) {
      console.error('[KnowledgeGraph] Load error:', e);
      this._showMessage('Laden fehlgeschlagen');
    }
  }

  async loadStats() {
    try {
      const response = await fetch('/api/graph/stats');
      if (!response.ok) return null;
      return await response.json();
    } catch (e) {
      console.error('[KnowledgeGraph] Stats error:', e);
      return null;
    }
  }

  _updateInfo() {
    const filteredNodes = this._filterNodes(this.nodes);
    const nodeIds = new Set(filteredNodes.map(n => n.id));
    const filteredEdges = this._filterEdges(this.edges, nodeIds);
    this._updateInfoFiltered(filteredNodes.length, filteredEdges.length);
  }

  _updateInfoFiltered(nodeCount, edgeCount) {
    const nodeEl = document.getElementById('kg-node-count');
    const edgeEl = document.getElementById('kg-edge-count');
    if (nodeEl) nodeEl.textContent = `${nodeCount} Nodes`;
    if (edgeEl) edgeEl.textContent = `${edgeCount} Edges`;
  }

  render() {
    if (!this.container) return;

    // Check D3 availability
    if (typeof d3 === 'undefined') {
      console.error('[KnowledgeGraph] D3.js not loaded');
      return;
    }

    // Check if we have nodes
    if (!this.nodes || this.nodes.length === 0) {
      console.warn('[KnowledgeGraph] No nodes to render');
      return;
    }

    // Filter nodes and edges based on settings
    const filteredNodes = this._filterNodes(this.nodes);
    const nodeIds = new Set(filteredNodes.map(n => n.id));
    const filteredEdges = this._filterEdges(this.edges, nodeIds);

    console.log('[KnowledgeGraph] Rendering', filteredNodes.length, '/', this.nodes.length, 'nodes,',
                filteredEdges.length, '/', this.edges.length, 'edges (filtered)');

    // Update info with filtered counts
    this._updateInfoFiltered(filteredNodes.length, filteredEdges.length);

    // Check if any nodes remain after filtering
    if (filteredNodes.length === 0) {
      console.warn('[KnowledgeGraph] All nodes filtered out');
      this._showMessage('Alle Nodes gefiltert - deaktiviere "Accessors ausblenden"');
      return;
    }

    // Hide empty state
    const emptyState = document.getElementById('kg-empty-state');
    if (emptyState) emptyState.style.display = 'none';

    // Clear existing
    const graphContainer = this.container.querySelector('.kg-graph');
    if (graphContainer) graphContainer.remove();

    const wrapper = document.createElement('div');
    wrapper.className = 'kg-graph';
    this.container.appendChild(wrapper);

    // Force layout calculation and use explicit dimensions
    const rect = wrapper.getBoundingClientRect();
    const width = rect.width > 100 ? rect.width : 800;
    const height = rect.height > 100 ? rect.height : 500;
    console.log('[KnowledgeGraph] Graph dimensions:', width, 'x', height);

    // Create SVG
    this.svg = d3.select(wrapper)
      .append('svg')
      .attr('width', '100%')
      .attr('height', '100%')
      .attr('viewBox', `0 0 ${width} ${height}`);

    // Zoom behavior
    this.zoom = d3.zoom()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => this._handleZoom(event));

    this.svg.call(this.zoom);

    // Main group for transformations
    this.g = this.svg.append('g');

    // Arrow marker for directed edges
    this.svg.append('defs').append('marker')
      .attr('id', 'arrowhead')
      .attr('viewBox', '-0 -5 10 10')
      .attr('refX', 20)
      .attr('refY', 0)
      .attr('orient', 'auto')
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .append('path')
      .attr('d', 'M 0,-5 L 10,0 L 0,5')
      .attr('fill', 'var(--border-color, #475569)');

    // Create node id map for edge linking (use filtered nodes)
    const nodeMap = new Map(filteredNodes.map(n => [n.id, n]));

    // Filter edges to only include those with valid nodes
    const validEdges = filteredEdges.filter(e =>
      nodeMap.has(e.from_id) && nodeMap.has(e.to_id)
    ).map(e => ({
      ...e,
      source: e.from_id,
      target: e.to_id
    }));

    // Force simulation with filtered nodes
    this.simulation = d3.forceSimulation(filteredNodes)
      .force('link', d3.forceLink(validEdges)
        .id(d => d.id)
        .distance(100))
      .force('charge', d3.forceManyBody().strength(-400))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => this._getNodeRadius(d) + 10));

    // Edges
    const link = this.g.append('g')
      .attr('class', 'kg-links')
      .selectAll('line')
      .data(validEdges)
      .join('line')
      .attr('class', d => `kg-link kg-link-${d.type}`)
      .attr('stroke-width', d => Math.max(1, Math.sqrt(d.weight || 1)))
      .attr('marker-end', 'url(#arrowhead)');

    // Edge labels (on hover)
    const linkLabel = this.g.append('g')
      .attr('class', 'kg-link-labels')
      .selectAll('text')
      .data(validEdges)
      .join('text')
      .attr('class', 'kg-link-label')
      .text(d => d.type)
      .style('opacity', 0);

    // Nodes (use filtered)
    const node = this.g.append('g')
      .attr('class', 'kg-nodes')
      .selectAll('g')
      .data(filteredNodes)
      .join('g')
      .attr('class', d => `kg-node kg-node-${d.type}`)
      .call(this._drag(this.simulation))
      .on('click', (e, d) => this._selectNode(d, e))
      .on('dblclick', (e, d) => this._expandNode(d));

    // Invisible hit area (larger than visible circle for easier clicking)
    node.append('circle')
      .attr('class', 'kg-node-hitarea')
      .attr('r', d => this._getNodeRadius(d) + 8)
      .attr('fill', 'transparent')
      .attr('stroke', 'none');

    // Node circles (visible)
    node.append('circle')
      .attr('class', 'kg-node-circle')
      .attr('r', d => this._getNodeRadius(d))
      .attr('fill', d => this._getNodeColor(d))
      .attr('stroke', d => d.id === this.currentCenter ? '#fff' : 'none')
      .attr('stroke-width', d => d.id === this.currentCenter ? 3 : 0)
      .style('pointer-events', 'none');  // Let hitarea handle events

    // Node labels
    node.append('text')
      .attr('dx', d => this._getNodeRadius(d) + 4)
      .attr('dy', 4)
      .attr('class', 'kg-node-label')
      .text(d => d.name);

    // Simulation tick
    this.simulation.on('tick', () => {
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);

      linkLabel
        .attr('x', d => (d.source.x + d.target.x) / 2)
        .attr('y', d => (d.source.y + d.target.y) / 2);

      node.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    // Hover effects for edges
    link
      .on('mouseenter', function(e, d) {
        d3.select(this).classed('kg-link-hover', true);
        linkLabel.filter(l => l === d).style('opacity', 1);
      })
      .on('mouseleave', function(e, d) {
        d3.select(this).classed('kg-link-hover', false);
        linkLabel.filter(l => l === d).style('opacity', 0);
      });
  }

  _getNodeColor(node) {
    return this.colors[node.type] || '#6b7280';
  }

  _getNodeRadius(node) {
    return this.sizes[node.type] || 10;
  }

  _selectNode(node, event) {
    event?.stopPropagation();

    this.selectedNode = node;

    // Visual feedback
    if (this.g) {
      this.g.selectAll('.kg-node-circle')
        .attr('stroke', d => d.id === node.id ? '#fff' : (d.id === this.currentCenter ? '#fff' : 'none'))
        .attr('stroke-width', d => d.id === node.id ? 2 : (d.id === this.currentCenter ? 3 : 0));
    }

    // Dispatch event for external handlers
    this.container.dispatchEvent(new CustomEvent('nodeSelected', {
      detail: node
    }));

    // Show node details
    this._showNodeDetails(node);
  }

  _showNodeDetails(node) {
    if (!this.container) return;

    let details = this.container.querySelector('.kg-details');
    if (!details) {
      details = document.createElement('div');
      details.className = 'kg-details';
      this.container.appendChild(details);
    }

    // Escape node.id für sichere HTML-Darstellung
    const escapedId = node.id.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    details.innerHTML = `
      <div class="kg-details-header">
        <span class="kg-details-type kg-type-${node.type}">${node.type}</span>
        <span class="kg-details-name">${node.name}</span>
        <button class="kg-details-close" data-action="close">×</button>
      </div>
      <div class="kg-details-body">
        <div class="kg-details-row">
          <span class="kg-details-label">ID:</span>
          <span class="kg-details-value">${escapedId}</span>
        </div>
        ${node.file_path ? `
        <div class="kg-details-row">
          <span class="kg-details-label">File:</span>
          <span class="kg-details-value">${node.file_path}:${node.line_number || ''}</span>
        </div>
        ` : ''}
        ${Object.keys(node.metadata || {}).length > 0 ? `
        <div class="kg-details-row">
          <span class="kg-details-label">Metadata:</span>
          <pre class="kg-details-meta">${JSON.stringify(node.metadata, null, 2)}</pre>
        </div>
        ` : ''}
      </div>
      <div class="kg-details-actions">
        <button data-action="load-center">Als Center laden</button>
      </div>
    `;

    // Event Listeners (sicherer als inline onclick)
    details.querySelector('[data-action="close"]')?.addEventListener('click', () => {
      details.remove();
    });

    details.querySelector('[data-action="load-center"]')?.addEventListener('click', () => {
      console.log('[KnowledgeGraph] Loading as center:', node.id);
      this.loadSubgraph(node.id, this.currentDepth);
      details.remove();  // Panel schließen nach Laden
    });
  }

  async _expandNode(node) {
    console.log('[KnowledgeGraph] Doppelklick - Expanding node:', node.id, node.name);
    await this.loadSubgraph(node.id, this.currentDepth);
  }

  _drag(simulation) {
    return d3.drag()
      .on('start', (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
  }

  _handleZoom(event) {
    if (!this.g) return;
    this.g.attr('transform', event.transform);
  }

  _zoomIn() {
    if (!this.svg || !this.zoom) return;
    this.svg.transition().duration(300).call(
      this.zoom.scaleBy, 1.3
    );
  }

  _zoomOut() {
    if (!this.svg || !this.zoom) return;
    this.svg.transition().duration(300).call(
      this.zoom.scaleBy, 0.7
    );
  }

  _resetView() {
    if (!this.svg || !this.zoom) return;
    this.svg.transition().duration(500).call(
      this.zoom.transform,
      d3.zoomIdentity
    );
  }

  destroy() {
    if (this.simulation) {
      this.simulation.stop();
    }
    if (this.container) {
      this.container.innerHTML = '';
    }
  }
}

// Global instance
window.knowledgeGraph = null;

// Initialize when DOM ready
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('knowledge-graph-container');
  if (container) {
    window.knowledgeGraph = new KnowledgeGraphViewer('knowledge-graph-container');
  }
});
