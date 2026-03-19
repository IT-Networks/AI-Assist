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
      <div class="kg-search">
        <input type="text" id="kg-search-input" placeholder="Suche Klasse/Interface..." />
        <button id="kg-search-btn" title="Suchen">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/>
            <path d="M21 21l-4.35-4.35"/>
          </svg>
        </button>
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
      </div>
      <div class="kg-info" id="kg-info">
        <span id="kg-node-count">0 Nodes</span>
        <span id="kg-edge-count">0 Edges</span>
      </div>
    `;
    this.container.insertBefore(controls, this.container.firstChild);

    // Event Listeners
    document.getElementById('kg-search-btn')?.addEventListener('click', () => this._handleSearch());
    document.getElementById('kg-search-input')?.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') this._handleSearch();
    });
    document.getElementById('kg-zoom-in')?.addEventListener('click', () => this._zoomIn());
    document.getElementById('kg-zoom-out')?.addEventListener('click', () => this._zoomOut());
    document.getElementById('kg-reset')?.addEventListener('click', () => this._resetView());
    document.getElementById('kg-depth')?.addEventListener('change', (e) => {
      this.currentDepth = parseInt(e.target.value);
      if (this.currentCenter) {
        this.loadSubgraph(this.currentCenter, this.currentDepth);
      }
    });
  }

  async _handleSearch() {
    const input = document.getElementById('kg-search-input');
    const query = input?.value?.trim();
    if (!query) return;

    try {
      const response = await fetch(`/api/graph/search?q=${encodeURIComponent(query)}&limit=20`);
      if (!response.ok) throw new Error('Search failed');

      const results = await response.json();
      if (results.length === 0) {
        this._showMessage('Keine Ergebnisse gefunden');
        return;
      }

      // Erstes Ergebnis als Center laden
      await this.loadSubgraph(results[0].id, this.currentDepth);
    } catch (e) {
      console.error('[KnowledgeGraph] Search error:', e);
      this._showMessage('Suche fehlgeschlagen');
    }
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
    this.currentCenter = centerId;
    this.currentDepth = depth;

    try {
      const response = await fetch(`/api/graph/subgraph?center=${encodeURIComponent(centerId)}&depth=${depth}`);
      if (!response.ok) throw new Error('Failed to load subgraph');

      const data = await response.json();
      this.nodes = data.nodes;
      this.edges = data.edges;

      this._updateInfo();
      this.render();
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
    const nodeCount = document.getElementById('kg-node-count');
    const edgeCount = document.getElementById('kg-edge-count');
    if (nodeCount) nodeCount.textContent = `${this.nodes.length} Nodes`;
    if (edgeCount) edgeCount.textContent = `${this.edges.length} Edges`;
  }

  render() {
    // Clear existing
    const graphContainer = this.container.querySelector('.kg-graph');
    if (graphContainer) graphContainer.remove();

    const wrapper = document.createElement('div');
    wrapper.className = 'kg-graph';
    this.container.appendChild(wrapper);

    const width = wrapper.offsetWidth || 800;
    const height = wrapper.offsetHeight || 500;

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

    // Create node id map for edge linking
    const nodeMap = new Map(this.nodes.map(n => [n.id, n]));

    // Filter edges to only include those with valid nodes
    const validEdges = this.edges.filter(e =>
      nodeMap.has(e.from_id) && nodeMap.has(e.to_id)
    ).map(e => ({
      ...e,
      source: e.from_id,
      target: e.to_id
    }));

    // Force simulation
    this.simulation = d3.forceSimulation(this.nodes)
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

    // Nodes
    const node = this.g.append('g')
      .attr('class', 'kg-nodes')
      .selectAll('g')
      .data(this.nodes)
      .join('g')
      .attr('class', d => `kg-node kg-node-${d.type}`)
      .call(this._drag(this.simulation))
      .on('click', (e, d) => this._selectNode(d, e))
      .on('dblclick', (e, d) => this._expandNode(d));

    // Node circles
    node.append('circle')
      .attr('r', d => this._getNodeRadius(d))
      .attr('fill', d => this._getNodeColor(d))
      .attr('stroke', d => d.id === this.currentCenter ? '#fff' : 'none')
      .attr('stroke-width', d => d.id === this.currentCenter ? 3 : 0);

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
    this.g.selectAll('.kg-node circle')
      .attr('stroke', d => d.id === node.id ? '#fff' : (d.id === this.currentCenter ? '#fff' : 'none'))
      .attr('stroke-width', d => d.id === node.id ? 2 : (d.id === this.currentCenter ? 3 : 0));

    // Dispatch event for external handlers
    this.container.dispatchEvent(new CustomEvent('nodeSelected', {
      detail: node
    }));

    // Show node details
    this._showNodeDetails(node);
  }

  _showNodeDetails(node) {
    let details = this.container.querySelector('.kg-details');
    if (!details) {
      details = document.createElement('div');
      details.className = 'kg-details';
      this.container.appendChild(details);
    }

    details.innerHTML = `
      <div class="kg-details-header">
        <span class="kg-details-type kg-type-${node.type}">${node.type}</span>
        <span class="kg-details-name">${node.name}</span>
        <button class="kg-details-close" onclick="this.parentElement.parentElement.remove()">x</button>
      </div>
      <div class="kg-details-body">
        <div class="kg-details-row">
          <span class="kg-details-label">ID:</span>
          <span class="kg-details-value">${node.id}</span>
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
        <button onclick="window.knowledgeGraph?.loadSubgraph('${node.id}', ${this.currentDepth})">
          Als Center laden
        </button>
      </div>
    `;
  }

  async _expandNode(node) {
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
    this.g.attr('transform', event.transform);
  }

  _zoomIn() {
    this.svg.transition().duration(300).call(
      this.zoom.scaleBy, 1.3
    );
  }

  _zoomOut() {
    this.svg.transition().duration(300).call(
      this.zoom.scaleBy, 0.7
    );
  }

  _resetView() {
    this.svg.transition().duration(500).call(
      this.zoom.transform,
      d3.zoomIdentity
    );
  }

  destroy() {
    if (this.simulation) {
      this.simulation.stop();
    }
    this.container.innerHTML = '';
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
