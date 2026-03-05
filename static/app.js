// ══════════════════════════════════════════════════════════════════════════════
// AI Code Assistant - Frontend Application
// Agent-basierte Architektur mit Tool-Calling und Bestätigungs-Workflow
// ══════════════════════════════════════════════════════════════════════════════

// ── State ──
const state = {
  sessionId: crypto.randomUUID(),
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
};

// ── Initialization ──
document.addEventListener('DOMContentLoaded', async () => {
  marked.setOptions({ breaks: true, gfm: true });

  // Initialize UI
  setupSidebarTabs();
  setupModeSwitch();
  setupInputHandlers();

  // Load data
  await Promise.all([
    loadModels(),
    loadSkills(),
    loadJavaIndexStatus(),
    loadPythonIndexStatus(),
    loadHandbookStatus(),
  ]);

  // Create new agent session
  await createAgentSession();
});

// ── UI Setup ──
function setupSidebarTabs() {
  document.querySelectorAll('.sidebar-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const panelId = tab.dataset.panel;
      const sidebar = tab.closest('.sidebar');

      sidebar.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
      sidebar.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));

      tab.classList.add('active');
      document.getElementById(panelId).classList.add('active');
    });
  });
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
      e.preventDefault();
      sendMessage();
    }
  });

  input.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 150) + 'px';
  });
}

// ── Agent Session Management ──
async function createAgentSession() {
  try {
    const skillIds = state.activeSkills.join(',');
    const res = await fetch(`/api/agent/session/new?mode=${state.mode}${skillIds ? '&skill_ids=' + skillIds : ''}`, {
      method: 'POST'
    });
    const data = await res.json();
    state.sessionId = data.session_id;
    console.log('Agent session created:', state.sessionId);
  } catch (e) {
    console.error('Failed to create agent session:', e);
  }
}

async function setAgentMode(mode) {
  try {
    const res = await fetch(`/api/agent/mode/${state.sessionId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });

    if (!res.ok) {
      const err = await res.json();
      appendMessage('error', `Modus-Wechsel fehlgeschlagen: ${err.detail}`);
      // Reset radio to current mode
      document.querySelector(`input[name="agent-mode"][value="${state.mode}"]`).checked = true;
      return;
    }

    const data = await res.json();
    state.mode = data.mode;
    updateModeIndicator();
  } catch (e) {
    appendMessage('error', 'Modus-Wechsel fehlgeschlagen: ' + e.message);
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
  }
}

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
    sel.addEventListener('change', () => { state.currentModel = sel.value; });
  } catch (e) {
    console.error('Failed to load models:', e);
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
    console.error('Failed to toggle skill:', e);
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
async function sendMessage() {
  const input = document.getElementById('message-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.style.height = 'auto';
  appendMessage('user', text);

  const sendBtn = document.getElementById('send-btn');
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<span class="spinner"></span>';

  try {
    await sendAgentChat(text);
  } catch (e) {
    appendMessage('error', 'Fehler: ' + e.message);
  } finally {
    sendBtn.disabled = false;
    sendBtn.innerHTML = '<span class="send-icon">&#10148;</span>';
  }
}

async function sendAgentChat(message) {
  const payload = {
    message,
    session_id: state.sessionId,
    model: state.currentModel,
    skill_ids: state.activeSkills.length > 0 ? state.activeSkills : null,
  };

  const res = await fetch('/api/agent/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }

  // Process SSE stream
  const msgDiv = appendMessage('assistant', '');
  const bubble = msgDiv.querySelector('.message-bubble');
  let fullText = '';
  let currentToolCard = null;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data:')) continue;

      try {
        const event = JSON.parse(line.slice(5).trim());
        await processAgentEvent(event, bubble, msgDiv);

        if (event.type === 'token' && event.data) {
          fullText += event.data;
          bubble.innerHTML = marked.parse(fullText);
          applyHighlight(bubble);
          scrollToBottom();
        }
      } catch (e) {
        // Ignore parse errors for partial chunks
      }
    }
  }

  // Final render
  if (fullText) {
    bubble.innerHTML = marked.parse(fullText);
    applyHighlight(bubble);
  }
  scrollToBottom();
}

async function processAgentEvent(event, bubble, msgDiv) {
  const { type, data, session_id } = event;

  switch (type) {
    case 'tool_start':
      // Add tool card to message
      const toolCard = createToolCard(data.name, data.arguments, 'running');
      bubble.appendChild(toolCard);
      addToolToHistory(data.id, data.name, data.arguments, 'running');
      scrollToBottom();
      break;

    case 'tool_result':
      // Update tool card
      updateToolCard(data.id, data.success ? 'success' : 'error', data.data);
      updateToolHistory(data.id, data.success ? 'success' : 'error', data.data);
      break;

    case 'confirm_required':
      // Show confirmation panel
      showConfirmationPanel(data);
      state.pendingConfirmation = data;
      // Switch to confirm tab
      switchRightPanel('confirm-panel');
      break;

    case 'waiting_for_confirmation':
      appendMessage('system', 'Warte auf Bestätigung für Schreib-Operation...');
      break;

    case 'confirmed':
      hideConfirmationPanel();
      appendMessage('system', `✓ ${data.message}`);
      break;

    case 'cancelled':
      hideConfirmationPanel();
      appendMessage('system', `✗ ${data.message}`);
      break;

    case 'error':
      appendMessage('error', data.error || 'Unbekannter Fehler');
      break;

    case 'done':
      // Chat complete
      break;
  }
}

function createToolCard(toolName, args, status) {
  const card = document.createElement('div');
  card.className = 'tool-call-card';
  card.dataset.toolId = toolName + Date.now();

  const statusClass = status === 'running' ? 'running' : (status === 'success' ? 'success' : 'error');
  const statusText = status === 'running' ? 'Läuft...' : (status === 'success' ? 'Fertig' : 'Fehler');

  card.innerHTML = `
    <div class="tool-call-header">
      <span class="tool-call-icon">&#128295;</span>
      <span class="tool-call-name">${escapeHtml(toolName)}</span>
      <span class="tool-call-status ${statusClass}">${statusText}</span>
    </div>
    <div class="tool-call-body">
      <div class="tool-call-args">${escapeHtml(JSON.stringify(args, null, 2))}</div>
      <div class="tool-call-result"></div>
    </div>
  `;

  return card;
}

function updateToolCard(toolId, status, result) {
  // Find by tool name (simplified)
  const cards = document.querySelectorAll('.tool-call-card');
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

// ── Tool History ──
function addToolToHistory(id, name, args, status) {
  state.toolHistory.unshift({ id, name, args, status, result: null });
  renderToolHistory();
}

function updateToolHistory(id, status, result) {
  const tool = state.toolHistory.find(t => t.name === id || state.toolHistory[0]?.name === id);
  if (tool || state.toolHistory.length > 0) {
    const target = tool || state.toolHistory[0];
    target.status = status;
    target.result = result;
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
  } catch (e) {
    appendMessage('error', 'Bestätigung fehlgeschlagen: ' + e.message);
  }
}

function switchRightPanel(panelId) {
  const sidebar = document.getElementById('sidebar-right');
  sidebar.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
  sidebar.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));

  sidebar.querySelector(`[data-panel="${panelId}"]`).classList.add('active');
  document.getElementById(panelId).classList.add('active');
}

// ── Messages ──
function appendMessage(role, text) {
  const messages = document.getElementById('messages');
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
  messages.appendChild(div);
  scrollToBottom();
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
  if (!confirm('Session löschen und neu starten?')) return;

  // Clear old session
  await fetch(`/api/agent/session/${state.sessionId}`, { method: 'DELETE' }).catch(() => {});

  // Reset state
  state.sessionId = crypto.randomUUID();
  state.toolHistory = [];
  state.pendingConfirmation = null;
  state.context = { javaFiles: [], pythonFiles: [], pdfIds: [], handbookServices: [] };

  // Reset UI
  document.getElementById('messages').innerHTML = '';
  renderToolHistory();
  renderContextChips();
  hideConfirmationPanel();

  // Create new session
  await createAgentSession();

  appendMessage('system', 'Neue Session gestartet.');
});

// ── Explorer Sections ──
function toggleExplorerSection(section) {
  const content = document.getElementById(`${section}-content`);
  const arrow = document.getElementById(`${section}-arrow`);

  content.classList.toggle('open');
  arrow.style.transform = content.classList.contains('open') ? 'rotate(90deg)' : '';
}

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

  renderContextChips();
}

// ── Handbook ──
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
    if (d.is_built) {
      el.innerHTML = `<span class="status-icon">&#128214;</span><span>${d.service_count} Services indexiert</span>`;
      el.classList.add('success');
      await loadHandbookServices();
    } else {
      el.innerHTML = '<span class="status-icon">&#128214;</span><span>Index nicht aufgebaut</span>';
    }
  } catch {
    el.innerHTML = '<span class="status-icon">&#128214;</span><span>Handbuch nicht verfügbar</span>';
    el.classList.add('error');
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
      <div class="service-item" onclick="addServiceToContext('${s.service_id}', '${escapeHtml(s.service_name)}')">
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
      <div class="search-result" onclick="addServiceToContext('${r.service_id}', '${escapeHtml(r.title)}')">
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
  renderContextChips();
}

// ── PDF Upload ──
async function uploadPDF() {
  const fileInput = document.getElementById('pdf-file-input');
  const file = fileInput.files[0];
  if (!file) return;

  const container = document.getElementById('pdf-list');
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
    state.context.pdfIds.push({ id: data.id, label: file.name });
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
  container.innerHTML = state.context.pdfIds.map(pdf => `
    <div class="item-list-item">
      <span class="item-icon">&#128196;</span>
      <span class="item-name">${escapeHtml(pdf.label)}</span>
      <span class="item-remove" onclick="removePdf('${pdf.id}')">&times;</span>
    </div>
  `).join('');
}

function removePdf(id) {
  state.context.pdfIds = state.context.pdfIds.filter(p => p.id !== id);
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
    const data = await res.json();

    const results = data.matches || data.results || data || [];

    if (!results || results.length === 0) {
      container.innerHTML = '<span style="color:var(--text-muted)">Keine Ergebnisse</span>';
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

  renderContextChips();
}

// ── Utilities ──
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
