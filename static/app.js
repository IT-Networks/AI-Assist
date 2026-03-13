// ══════════════════════════════════════════════════════════════════════════════
// AI Code Assistant - Frontend Application
// Agent-basierte Architektur mit Tool-Calling und Bestätigungs-Workflow
// ══════════════════════════════════════════════════════════════════════════════

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

  // Load data
  await Promise.all([
    loadModels(),
    loadSkills(),
    loadJavaIndexStatus(),
    loadPythonIndexStatus(),
    loadHandbookStatus(),
    scanExistingPdfs(),
  ]);

  // Gespeicherte Chats laden oder neuen Chat erstellen
  await loadPersistedChats();
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

/**
 * Programmtisch zu einem Panel in der rechten Sidebar wechseln.
 * @param {string} panelId - Die ID des Panels (z.B. 'mcp-panel', 'tools-panel')
 */
function switchRightPanel(panelId) {
  const sidebar = document.getElementById('sidebar-right');
  if (!sidebar) return;

  // Alle Tabs und Panels deaktivieren
  sidebar.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
  sidebar.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));

  // Ziel-Tab und Panel aktivieren
  const tab = sidebar.querySelector(`.sidebar-tab[data-panel="${panelId}"]`);
  const panel = document.getElementById(panelId);

  if (tab) tab.classList.add('active');
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
    console.error('Failed to load persisted chats:', e);
    await createNewChat();
  }
}

async function createNewChat() {
  try {
    const sessionId = await createAgentSession();
    const chat = chatManager.createChat(sessionId);
    // switchToChat übernimmt Pane-Swap, State-Restore und UI-Updates
    await switchToChat(chat.id);
    console.log('New chat created:', chat.id, 'session:', sessionId);
  } catch (e) {
    console.error('Failed to create new chat:', e);
  }
}

async function switchToChat(chatId) {
  if (chatId === chatManager.activeId) return;

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
    incomingChat.needsRestore = false;
    // Context bar first, then messages
    incomingChat.pane.innerHTML = _contextBarHTML();
    try {
      const res = await fetch(`/api/agent/session/${incomingChat.sessionId}/history`);
      if (res.ok) {
        const data = await res.json();
        const { messages, mode } = data;
        for (const msg of messages) {
          if (msg.role === 'user' || msg.role === 'assistant') {
            appendMessageToPane(incomingChat.pane, msg.role, msg.content);
          }
        }
        // Mode vom Server synchronisieren
        if (mode) {
          console.log(`[switchToChat] Restored mode from server: ${mode}`);
          syncModeUI(mode);
        }
      }
    } catch (e) {
      incomingChat.pane.innerHTML = _contextBarHTML() + welcomeHTML();
      console.error('Failed to restore chat history:', e);
    }
  } else {
    // Bestehender Chat - Mode aus Chat-Objekt oder Server laden
    if (incomingChat.mode) {
      console.log(`[switchToChat] Using cached mode: ${incomingChat.mode}`);
      syncModeUI(incomingChat.mode);
    } else {
      // Fallback: Mode vom Server laden
      try {
        const res = await fetch(`/api/agent/mode/${incomingChat.sessionId}`);
        if (res.ok) {
          const { mode } = await res.json();
          console.log(`[switchToChat] Fetched mode from server: ${mode}`);
          syncModeUI(mode);
        }
      } catch (e) {
        console.debug('Could not fetch mode:', e);
      }
    }
  }

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

function renderChatList() {
  const listEl = document.getElementById('chat-list');
  if (!listEl) return;

  if (chatManager.chats.length === 0) {
    listEl.innerHTML = '<div class="chat-list-empty">Keine Chats</div>';
    return;
  }

  // Newest first
  const sorted = [...chatManager.chats].reverse();
  listEl.innerHTML = '';

  sorted.forEach(chat => {
    const isActive = chat.id === chatManager.activeId;
    const item = document.createElement('div');
    item.className = 'chat-item' + (isActive ? ' active' : '');
    item.dataset.chatId = chat.id;

    item.innerHTML = `
      <span class="chat-item-icon">💬</span>
      <span class="chat-item-title" title="${escapeHtml(chat.title)}">${escapeHtml(chat.title)}</span>
      <button class="chat-item-rename" title="Umbenennen">✏</button>
      <button class="chat-item-delete" title="Chat löschen">✕</button>`;

    item.querySelector('.chat-item-title').addEventListener('click', () => switchToChat(chat.id));
    item.querySelector('.chat-item-icon').addEventListener('click', () => switchToChat(chat.id));
    item.querySelector('.chat-item-rename').addEventListener('click', (e) => {
      e.stopPropagation();
      startInlineRename(chat.id, item);
    });
    item.querySelector('.chat-item-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      deleteChat(chat.id);
    });

    listEl.appendChild(item);
  });
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

function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
    console.error('No session ID available for mode change');
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
    console.log(`[setAgentMode] Mode changed from ${prevMode} to ${data.mode} for session ${sessionId}`);
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
    console.log(`[Mode] Radio synced to: ${mode}`);
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
      console.log(`[Model] Changed from ${prevModel} to ${sel.value}, mode remains: ${state.mode}`);
      // Mode UI-Sync sicherstellen (defensiv - sollte nicht nötig sein)
      syncModeRadioButtons(state.mode);
    });
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

// Aktiver AbortController für laufende Anfragen
let _chatAbortController = null;

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
  console.log('[cmd] Befehl erkannt:', { original: text, normalized: raw });

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
    console.log('[cmd] Modus-Befehl erkannt:', { modePrefix, modeKey });
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
    console.log('[cmd] MCP Capability:', { capability: cap.name, query: capQuery });

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
  console.log('[cmd] Unbekannter Befehl:', { raw, modePrefix });
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
    console.log('[sendChatInternal] Bereits am Streamen, überspringe');
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

function switchRightPanel(panelId) {
  const sidebar = document.getElementById('sidebar-right');
  sidebar.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
  sidebar.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));

  sidebar.querySelector(`[data-panel="${panelId}"]`).classList.add('active');
  document.getElementById(panelId).classList.add('active');
}

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

async function loadRepoSelector(lang) {
  const selectorDiv = document.getElementById(`${lang}-repo-selector`);
  const select = document.getElementById(`${lang}-repo-select`);
  if (!selectorDiv || !select) return;

  try {
    const res = await fetch(`/api/settings/repos/${lang}`);
    if (!res.ok) { selectorDiv.style.display = 'none'; return; }
    const d = await res.json();
    const repos = d.repos || [];
    const activeRepo = d.active_repo || '';

    if (repos.length < 2) {
      // Nur einen Repo → kein Selector nötig
      selectorDiv.style.display = 'none';
      return;
    }

    select.innerHTML = repos.map(r =>
      `<option value="${escapeHtml(r.name)}" ${r.name === activeRepo ? 'selected' : ''}>${escapeHtml(r.name)}</option>`
    ).join('');
    selectorDiv.style.display = 'flex';
  } catch {
    selectorDiv.style.display = 'none';
  }
}

async function setActiveRepo(lang, name) {
  try {
    const res = await fetch(`/api/settings/repos/${lang}/active?name=${encodeURIComponent(name)}`, { method: 'PUT' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      appendMessage('system', `Fehler beim Wechsel des Repositories: ${d.detail || res.statusText}`);
      // Selector zurücksetzen
      loadRepoSelector(lang);
      return;
    }
    // Speichern
    await fetch('/api/settings/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backup: false }) });
    appendMessage('system', `${lang === 'java' ? 'Java' : 'Python'}-Repository gewechselt zu: ${name}`);
    // Index-Status neu laden (zeigt Dateizahl des neuen Repos)
    if (lang === 'java') loadJavaIndexStatus();
    else loadPythonIndexStatus();
  } catch (e) {
    appendMessage('system', `Fehler: ${e.message}`);
    loadRepoSelector(lang);
  }
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
  loadRepoSelector('java');
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
  loadRepoSelector('python');
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
            console.warn('Handbook progress parse error:', e);
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
  renderContextChips();
}

// ── PDF Management ──
async function scanExistingPdfs() {
  try {
    const res = await fetch('/api/pdf/scan', { method: 'POST' });
    if (!res.ok) return;

    const data = await res.json();
    if (data.pdfs && data.pdfs.length > 0) {
      // PDFs in Context laden
      state.context.pdfIds = data.pdfs.map(pdf => ({
        id: pdf.id,
        label: pdf.filename
      }));
      renderPdfList();
      console.log(`${data.loaded} PDFs aus Upload-Ordner geladen`);
    }
  } catch (e) {
    console.debug('PDF-Scan fehlgeschlagen:', e);
  }
}

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

  renderContextChips();
}

// ── Utilities ──
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
    agent_tools: 'Agent-Tools mit Modell-Zuweisungen',
    jira: 'Jira-Anbindung für Issue-Suche',
    context: 'Kontext-Limits für LLM',
    uploads: 'Upload-Verzeichnis und Limits',
    jenkins: 'Jenkins CI/CD Server (intern gehostet)',
    github: 'GitHub Enterprise Server (intern gehostet)',
    internal_fetch: 'Intranet-URLs abrufen (HTTP Fetch)',
    docker_sandbox: 'Container-Sandbox (Docker/Podman)',
    data_sources: 'Interne HTTP-Systeme (Jenkins, GitHub, APIs)',
    mq: 'IBM MQ Series Messaging',
    test_tool: 'Test-Automatisierung',
    soap_tool: 'SOAP Services (Multi-Institut)',
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
      console.warn('Could not restore settings category state:', e);
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
}

function closeSettings() {
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

async function loadSettings() {
  showSettingsLoading(true);
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    settingsState.settings = data.settings;
    renderSettingsSection();
  } catch (err) {
    console.error('Settings load error:', err);
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
    setTimeout(() => { el.textContent = ''; el.className = 'settings-status'; }, 3000);
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

  if (section === 'soap_tool') {
    renderSoapToolSection();
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

  const values = settingsState.settings[section];
  const desc = settingsState.descriptions[section] || '';

  if (section === 'models') {
    renderModelsSection();
    return;
  }

  if (section === 'agent_tools') {
    renderAgentToolsSection();
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

async function renderAgentToolsSection() {
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">AGENT TOOLS</h3>
      <p class="settings-section-desc">Übersicht aller Agent-Tools. Pro Tool kann ein eigenes LLM-Modell zugewiesen werden. Leeres Feld = Standard-Modell (tool_model oder default_model).</p>
    </div>
    <div id="agent-tools-loading" style="text-align:center; padding:20px;">
      <span class="spinner"></span> Lade Tools...
    </div>
  `;

  try {
    const [toolsRes, dsRes] = await Promise.all([
      fetch('/api/settings/agent-tools'),
      fetch('/api/datasources'),
    ]);
    const data = await toolsRes.json();
    const dsData = dsRes.ok ? await dsRes.json() : { sources: [] };

    // Build lookup: tool-name → datasource id
    const dsById = {};
    for (const src of (dsData.sources || [])) {
      // replicate _slugify + get_datasource_tool_name logic
      const slug = src.name.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '').slice(0, 40) || 'unnamed';
      dsById[`ds_${slug}`] = src.id;
    }

    const tools = data.tools || [];
    const availableModels = data.available_models || [];
    const defaultModel = data.default_model || '';
    const globalToolModel = data.tool_model || '';

    // Kategorien gruppieren
    const categories = {};
    for (const tool of tools) {
      const cat = tool.category || 'other';
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(tool);
    }

    const categoryLabels = {
      search: 'Suche',
      file: 'Dateien',
      knowledge: 'Wissen',
      analysis: 'Analyse',
      other: 'Sonstige'
    };

    let html = `
      <div class="settings-section">
        <h3 class="settings-section-title">AGENT TOOLS</h3>
        <p class="settings-section-desc">
          Pro Tool kann ein eigenes LLM-Modell zugewiesen werden.<br>
          <small>Standard-Modell: <strong>${escapeHtml(globalToolModel || defaultModel)}</strong></small>
        </p>
        <div style="margin-top: 12px;">
          <input type="text" id="agent-tools-search" placeholder="Tools durchsuchen..."
            oninput="filterAgentTools(this.value)"
            style="width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px;">
        </div>
      </div>
    `;

    for (const [cat, catTools] of Object.entries(categories)) {
      html += `<div class="settings-section" style="margin-top:12px;">
        <h4 style="margin:0 0 8px; color: var(--accent);">${categoryLabels[cat] || cat}</h4>
      </div>`;

      for (const tool of catTools) {
        const fieldId = `tool-model-${tool.name}`;
        const currentModel = tool.model || '';
        const writeIcon = tool.is_write_operation ? ' &#9888;' : '';
        const isDsTool = tool.name.startsWith('ds_') && dsById[tool.name];
        const dsEditBtn = isDsTool
          ? `<button class="btn btn-xs btn-secondary" onclick="openDatasourceEdit('${dsById[tool.name]}')" title="Datenquelle bearbeiten">✏ Datenquelle</button>`
          : '';

        html += `
          <div class="settings-field" style="border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-bottom: 8px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
              <label for="${fieldId}" style="font-weight:600;">${escapeHtml(tool.name)}${writeIcon}</label>
              ${dsEditBtn}
            </div>
            <div style="font-size:0.85em; color: var(--text-secondary); margin-bottom:6px;">
              ${escapeHtml(tool.description)}
            </div>
            <select id="${fieldId}" data-tool-name="${tool.name}" onchange="markSettingsModified()" style="width:100%;">
              <option value="">Standard (${escapeHtml(globalToolModel || defaultModel)})</option>
              ${availableModels.map(m => `<option value="${escapeHtml(m.id)}" ${currentModel === m.id ? 'selected' : ''}>${escapeHtml(m.display_name || m.id)}</option>`).join('')}
            </select>
          </div>
        `;
      }
    }

    form.innerHTML = html;
  } catch (err) {
    form.innerHTML = `<p style="color:var(--error);">Fehler beim Laden der Tools: ${escapeHtml(err.message)}</p>`;
  }
}

function openDatasourceEdit(sourceId) {
  // Switch to data_sources settings section and open edit form
  document.querySelectorAll('.settings-nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.section === 'data_sources');
  });
  settingsState.currentSection = 'data_sources';
  renderDataSourcesSection().then(() => {
    dsShowForm(sourceId);
    // Scroll form into view
    setTimeout(() => {
      document.getElementById('ds-edit-form')?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
  });
}

async function saveAgentToolModels() {
  const toolModels = {};
  document.querySelectorAll('[data-tool-name]').forEach(select => {
    const toolName = select.dataset.toolName;
    const modelId = select.value;
    if (modelId) {
      toolModels[toolName] = modelId;
    }
  });

  const res = await fetch('/api/settings/agent-tools/models', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toolModels)
  });

  if (!res.ok) {
    throw new Error('Fehler beim Speichern der Tool-Modelle');
  }

  return toolModels;
}

function filterAgentTools(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll('#settings-form .settings-field').forEach(field => {
    // Finde tool name im label
    const label = field.querySelector('label');
    if (!label) return;
    const toolName = label.textContent.toLowerCase();
    const desc = field.querySelector('[style*="text-secondary"]')?.textContent?.toLowerCase() || '';
    const matches = !q || toolName.includes(q) || desc.includes(q);
    field.style.display = matches ? '' : 'none';
  });
  // Kategorie-Header verstecken wenn alle Tools darin versteckt
  document.querySelectorAll('#settings-form .settings-section h4').forEach(h4 => {
    const section = h4.closest('.settings-section');
    if (!section) return;
    let next = section.nextElementSibling;
    let anyVisible = false;
    while (next && !next.querySelector('h4')) {
      if (next.classList.contains('settings-field') && next.style.display !== 'none') {
        anyVisible = true;
        break;
      }
      next = next.nextElementSibling;
    }
    section.style.display = anyVisible || !q ? '' : 'none';
  });
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
    // Sidebar-Selektor aktualisieren
    loadRepoSelector(lang);
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
    loadRepoSelector(lang);
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
    loadRepoSelector(lang);
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

  // Agent Tools hat eigenen Save-Endpunkt
  if (section === 'agent_tools') {
    try {
      await saveAgentToolModels();
      updateSettingsStatus('Tool-Modelle angewendet (nur im Speicher)', 'success');
    } catch (err) {
      updateSettingsStatus('Fehler: ' + err.message, 'error');
    }
    return;
  }

  // Web-Suche hat eigene Felder
  if (section === 'search') {
    const config = {
      proxy_url: document.getElementById('search-proxy-url')?.value || '',
      proxy_username: document.getElementById('search-proxy-user')?.value || '',
      proxy_password: document.getElementById('search-proxy-pass')?.value || '',
      no_proxy: document.getElementById('search-no-proxy')?.value || '',
      timeout_seconds: parseInt(document.getElementById('search-timeout')?.value) || 30,
      verify_ssl: document.getElementById('search-verify-ssl')?.checked ?? true,
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
      <p class="settings-section-desc">Services per HTTP aufrufen, Parameter übergeben und Ergebnisse lesen. Lokale Services aus dem Repo können ebenfalls ausgeführt werden.</p>
    </div>
    <div class="settings-subsection">
      <h4>Stages (Deployment-Umgebungen)</h4>
      <div id="tt-stages-list"><div class="spinner-inline"></div></div>
      <div class="settings-add-form">
        <div class="settings-field-row">
          <input id="tt-new-stage-name" type="text" class="settings-input" placeholder="Stage-Name (z.B. Dev)">
          <button class="btn btn-primary btn-sm" onclick="ttAddStage()">+ Stage</button>
        </div>
        <div class="settings-field-row" style="margin-top:4px">
          <input id="tt-new-url" type="text" class="settings-input" placeholder="URL (z.B. http://dev:8080)">
          <input id="tt-new-url-desc" type="text" class="settings-input" placeholder="Beschreibung" style="max-width:180px">
          <button class="btn btn-secondary btn-sm" onclick="ttAddUrlToStage()">+ URL</button>
        </div>
        <div class="settings-field" style="margin-top:4px">
          <label>Aktive Stage:</label>
          <select id="tt-active-stage-select" onchange="ttSetActiveStage(this.value)" class="settings-input" style="max-width:200px"></select>
        </div>
      </div>
    </div>
    <div class="settings-subsection" style="margin-top:16px">
      <h4>Services</h4>
      <div id="tt-services-list"><div class="spinner-inline"></div></div>
      <div class="settings-add-form">
        <h5 style="margin:0 0 8px">Service hinzufügen</h5>
        <div class="settings-field-row">
          <input id="tt-svc-name" type="text" class="settings-input" placeholder="Name">
          <input id="tt-svc-endpoint" type="text" class="settings-input" placeholder="Endpoint (z.B. /api/orders)">
          <select id="tt-svc-method" class="settings-input" style="max-width:90px">
            <option>POST</option><option>GET</option><option>PUT</option><option>DELETE</option><option>PATCH</option>
          </select>
        </div>
        <div class="settings-field-row" style="margin-top:4px">
          <input id="tt-svc-desc" type="text" class="settings-input" placeholder="Beschreibung">
          <input id="tt-svc-script" type="text" class="settings-input" placeholder="Lokales Skript (optional, relativ zum Repo)">
        </div>
        <div class="settings-field" style="margin-top:4px">
          <label>Parameter (Name,Typ,Required je Zeile: <code>customerId,string,true</code>):</label>
          <textarea id="tt-svc-params" class="settings-input" rows="3" placeholder="customerId,string,true&#10;amount,number,false"></textarea>
        </div>
        <button class="btn btn-primary" onclick="ttAddService()">+ Service</button>
      </div>
    </div>
    <div class="settings-subsection" style="margin-top:16px">
      <h4>Lokaler WLP-Server</h4>
      <p class="settings-section-desc">Testaufrufe direkt an einen lokalen WLP-Server weiterleiten. Wenn gesetzt, wird <code>use_local_wlp=true</code> genutzt statt der Stage-URL.</p>
      <div id="tt-local-wlp-section"><div class="spinner-inline"></div></div>
    </div>
  `;
  await ttLoadAll();
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
// SOAP Tool Settings Section (Multi-Institut)
// ══════════════════════════════════════════════════════════════════════════════

async function renderSoapToolSection() {
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">SOAP SERVICES (Multi-Institut)</h3>
      <p class="settings-section-desc">SOAP-basierte Service-Aufrufe mit automatischem Session-Management. Jedes Institut hat eigene Credentials und Session.</p>
    </div>

    <div class="settings-subsection">
      <h4>Endpoints</h4>
      <div class="settings-field">
        <label>Service-URL:</label>
        <input id="soap-service-url" type="text" class="settings-input" placeholder="https://example.com/soap/services">
      </div>
      <div class="settings-field">
        <label>Login-URL:</label>
        <input id="soap-login-url" type="text" class="settings-input" placeholder="https://example.com/soap/auth">
      </div>
      <div class="settings-field">
        <label style="display:flex;align-items:center;gap:8px">
          <input id="soap-verify-ssl" type="checkbox" checked>
          SSL-Zertifikate verifizieren
        </label>
      </div>
      <button class="btn btn-primary" onclick="soapSaveConfig()">Endpoints speichern</button>
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
      <h4>Services</h4>
      <p class="settings-hint">SOAP-Services mit XML-Templates. Templates können Platzhalter enthalten.</p>
      <div id="soap-services-list"><div class="spinner-inline"></div></div>
    </div>
  `;
  await soapLoadAll();
}

async function soapLoadAll() {
  // Config laden
  try {
    const cfgRes = await fetch('/api/soap/config');
    if (cfgRes.ok) {
      const cfg = await cfgRes.json();
      const urlEl = document.getElementById('soap-service-url');
      const loginEl = document.getElementById('soap-login-url');
      const sslEl = document.getElementById('soap-verify-ssl');
      if (urlEl) urlEl.value = cfg.service_url || '';
      if (loginEl) loginEl.value = cfg.login_url || '';
      if (sslEl) sslEl.checked = cfg.verify_ssl !== false;
    }
  } catch (e) { console.error('SOAP config load error:', e); }

  // Institute laden
  await soapLoadInstitute();

  // Sessions laden
  await soapLoadSessions();

  // Services laden
  await soapLoadServices();
}

async function soapSaveConfig() {
  const body = {
    service_url: document.getElementById('soap-service-url').value.trim(),
    login_url: document.getElementById('soap-login-url').value.trim(),
    verify_ssl: document.getElementById('soap-verify-ssl').checked,
  };
  const res = await fetch('/api/soap/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (res.ok) {
    updateSettingsStatus('Endpoints gespeichert ✓', 'success');
  } else {
    updateSettingsStatus('Fehler beim Speichern', 'error');
  }
}

async function soapLoadInstitute() {
  const list = document.getElementById('soap-institute-list');
  if (!list) return;

  try {
    const res = await fetch('/api/soap/institute');
    const data = await res.json();

    if (!data.institute?.length) {
      list.innerHTML = '<p class="empty-hint">Keine Institute konfiguriert.</p>';
      return;
    }

    list.innerHTML = data.institute.map(inst => `
      <div class="ds-item" data-institut="${escapeHtml(inst.institut_nr)}">
        <div class="ds-item-header">
          <span class="ds-item-name">${inst.enabled ? '🏢' : '⏸️'} ${escapeHtml(inst.name || inst.institut_nr)}</span>
          <span class="ds-item-badge">${escapeHtml(inst.institut_nr)}</span>
          <div class="ds-item-actions">
            <button class="btn btn-xs btn-secondary" onclick="soapEditInstitut('${escapeHtml(inst.institut_nr)}')" title="Bearbeiten">✏️</button>
            <button class="btn btn-xs btn-danger" onclick="soapDeleteInstitut('${escapeHtml(inst.institut_nr)}')" title="Löschen">🗑️</button>
          </div>
        </div>
        <div class="ds-item-details">
          <span class="ds-detail-label">User:</span> ${escapeHtml(inst.user)}
          <span class="ds-detail-label" style="margin-left:12px">Passwort:</span> ${inst.password ? '••••••••' : '(leer)'}
        </div>
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = '<p class="error-hint">Fehler beim Laden der Institute</p>';
  }
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

  const res = await fetch('/api/soap/institute', {
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
  const res = await fetch('/api/soap/institute');
  const data = await res.json();
  const inst = data.institute?.find(i => i.institut_nr === nr);
  if (!inst) return;

  await fetch(`/api/soap/institute/${encodeURIComponent(nr)}`, {
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
  await fetch(`/api/soap/institute/${encodeURIComponent(nr)}`, { method: 'DELETE' });
  updateSettingsStatus('Institut gelöscht', 'success');
  await soapLoadInstitute();
  await soapLoadSessions();
}

async function soapLoadSessions() {
  const list = document.getElementById('soap-sessions-list');
  if (!list) return;

  try {
    const res = await fetch('/api/soap/sessions');
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
  try {
    const res = await fetch(`/api/soap/session/${encodeURIComponent(nr)}/login`, { method: 'POST' });
    if (res.ok) {
      updateSettingsStatus('Login erfolgreich ✓', 'success');
    } else {
      const err = await res.json().catch(() => ({}));
      updateSettingsStatus(err.detail || 'Login fehlgeschlagen', 'error');
    }
    await soapLoadSessions();
  } catch (e) {
    updateSettingsStatus('Verbindungsfehler', 'error');
  }
}

async function soapDeleteSession(nr) {
  await fetch(`/api/soap/session/${encodeURIComponent(nr)}`, { method: 'DELETE' });
  updateSettingsStatus('Session gelöscht', 'success');
  await soapLoadSessions();
}

async function soapLoadServices() {
  const list = document.getElementById('soap-services-list');
  if (!list) return;

  try {
    const res = await fetch('/api/soap/services');
    const data = await res.json();

    if (!data.services?.length) {
      list.innerHTML = '<p class="empty-hint">Keine Services konfiguriert. Services werden über YAML-Config oder durch die KI angelegt.</p>';
      return;
    }

    list.innerHTML = data.services.map(svc => `
      <div class="ds-item">
        <div class="ds-item-header">
          <span class="ds-item-name">${svc.enabled ? '📡' : '⏸️'} ${escapeHtml(svc.name)}</span>
          <span class="ds-item-badge">${svc.operation_count} Operationen</span>
        </div>
        <div class="ds-item-details">
          ${escapeHtml(svc.description || 'Keine Beschreibung')}
        </div>
        ${svc.operations?.length ? `
          <div class="soap-operations-list">
            ${svc.operations.map(op => `
              <div class="soap-op-item">
                <span class="soap-op-name">▸ ${escapeHtml(op.name)}</span>
                <span class="soap-op-desc">${escapeHtml(op.description || '')}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = '<p class="error-hint">Fehler beim Laden der Services</p>';
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
      console.warn('WLP Import Fehler:', data.errors);
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
    console.error('WLP Import Fehler:', e);
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
      console.warn('Maven Import Fehler:', data.errors);
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
    console.error('Maven Import Fehler:', e);
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

// ── Test-Tool Panel ───────────────────────────────────────────────────────────

async function loadTestToolPanel() {
  try {
    const [stRes, svRes, wlpRes] = await Promise.all([
      fetch('/api/testtool/stages'),
      fetch('/api/testtool/services'),
      fetch('/api/testtool/local-wlp'),
    ]);
    const stData = await stRes.json();
    const svData = await svRes.json();
    const wlpData = wlpRes.ok ? await wlpRes.json() : { local_wlp_url: '' };

    // Lokaler WLP Status
    const wlpStatus = document.getElementById('testtool-local-wlp-status');
    if (wlpStatus) {
      if (wlpData.local_wlp_url) {
        wlpStatus.innerHTML = `<span class="badge badge-success">Lokal WLP: ${escapeHtml(wlpData.local_wlp_url)}</span>`;
        wlpStatus.style.display = 'block';
      } else {
        wlpStatus.style.display = 'none';
      }
    }

    const stageSelect = document.getElementById('testtool-stage-select');
    const urlSelect = document.getElementById('testtool-url-select');
    if (stageSelect) {
      stageSelect.innerHTML = stData.stages?.map(s =>
        `<option value="${s.id}" ${s.id===stData.active_stage?'selected':''}>${escapeHtml(s.name)}</option>`
      ).join('') || '<option>Keine Stages</option>';
      // URLs für aktive Stage laden
      const active = stData.stages?.find(s => s.id === stData.active_stage);
      if (active && urlSelect) {
        urlSelect.innerHTML = active.urls.map(u => `<option value="${u.url}">${escapeHtml(u.url)} ${u.description?'('+escapeHtml(u.description)+')':''}</option>`).join('');
      }
    }

    const content = document.getElementById('testtool-services-content');
    if (!content) return;
    if (!svData.services?.length) {
      content.innerHTML = '<div class="empty-state"><span>&#128296;</span><p>Keine Services konfiguriert</p></div>';
      return;
    }
    content.innerHTML = svData.services.map(s => `
      <div class="tool-card">
        <div class="tool-card-header">
          <span class="tool-card-name">${escapeHtml(s.name)}</span>
          <span class="tool-card-badge">${s.method}</span>
          ${s.has_local ? '<span class="tool-card-badge badge-info">lokal</span>' : ''}
        </div>
        ${s.description ? `<div class="tool-card-desc">${escapeHtml(s.description)}</div>` : ''}
        ${s.parameters?.length ? `
          <div class="tool-params" id="params-${s.id}">
            ${s.parameters.map(p => `
              <div class="param-row">
                <label class="param-label">${escapeHtml(p.name)}${p.required?'*':''}</label>
                <input type="text" class="param-input" id="p-${s.id}-${p.name}" placeholder="${escapeHtml(p.type)}" data-svc="${s.id}" data-param="${p.name}">
              </div>
            `).join('')}
          </div>
        ` : ''}
        <div class="tool-card-actions">
          <button class="btn btn-xs btn-primary" onclick="ttPanelExecute('${s.id}')">&#9654; Ausführen</button>
          ${s.has_local ? `<button class="btn btn-xs btn-secondary" onclick="ttPanelLocal('${s.id}')">&#128196; Lokal</button>` : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    const content = document.getElementById('testtool-services-content');
    if (content) content.innerHTML = `<p class="error-hint">Fehler: ${e.message}</p>`;
  }
}

function ttToggleLocalWlp(checked) {
  const stageSection = document.getElementById('testtool-stage-section');
  if (stageSection) stageSection.style.display = checked ? 'none' : 'block';
}

function onTestStageChange(stageId) {
  fetch('/api/testtool/stages').then(r => r.json()).then(data => {
    const stage = data.stages?.find(s => s.id === stageId);
    const sel = document.getElementById('testtool-url-select');
    if (sel && stage) {
      sel.innerHTML = stage.urls.map(u => `<option value="${u.url}">${escapeHtml(u.url)}</option>`).join('');
    }
  });
}

function _collectParams(svcId) {
  const params = {};
  document.querySelectorAll(`[data-svc="${svcId}"]`).forEach(el => {
    if (el.value) params[el.dataset.param] = el.value;
  });
  return params;
}

async function ttPanelExecute(svcId) {
  const useLocalWlp = document.getElementById('testtool-use-local-wlp')?.checked || false;
  const stageUrl = useLocalWlp ? '' : (document.getElementById('testtool-url-select')?.value || '');
  const params = _collectParams(svcId);
  const resultArea = document.getElementById('testtool-result-area');
  const resultPre = document.getElementById('testtool-result-pre');
  const badge = document.getElementById('testtool-status-badge');

  resultArea.style.display = 'block';
  resultPre.textContent = useLocalWlp ? '⏳ Ausführung (lokaler WLP)...' : '⏳ Ausführung...';
  badge.textContent = '';

  try {
    const res = await fetch(`/api/testtool/execute/${svcId}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ params, stage_url: stageUrl || undefined, use_local_wlp: useLocalWlp }),
    });
    const data = await res.json();
    badge.textContent = `HTTP ${data.status_code}${data.via_local_wlp ? ' [lokal]' : ''}`;
    badge.className = `badge ${data.success ? 'badge-success' : 'badge-error'}`;
    resultPre.textContent = JSON.stringify(data.response, null, 2).substring(0, 3000);
    if (data.elapsed_ms) resultPre.textContent += `\n\n[${data.elapsed_ms}ms | ${data.url}]`;
  } catch (e) {
    resultPre.textContent = `Fehler: ${e.message}`;
  }
}

async function ttPanelLocal(svcId) {
  const params = _collectParams(svcId);
  const resultArea = document.getElementById('testtool-result-area');
  const resultPre = document.getElementById('testtool-result-pre');
  resultArea.style.display = 'block';
  resultPre.textContent = '⏳ Lokale Ausführung...';

  try {
    const res = await fetch(`/api/testtool/local/${svcId}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ params }),
    });
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
          if (ev.type === 'output') resultPre.textContent += ev.line + '\n';
          else if (ev.type === 'done') resultPre.textContent += `\n[Exit: ${ev.exit_code}]`;
          else if (ev.type === 'error') resultPre.textContent += `\nFehler: ${ev.message}`;
        } catch (_) {}
      }
    }
  } catch (e) {
    resultPre.textContent += `\nFehler: ${e.message}`;
  }
}

// ── WLP Panel ─────────────────────────────────────────────────────────────────

// Polling für WLP-Status (aktualisiert Panel automatisch wenn Server gestartet wird)
let _wlpPollInterval = null;
let _wlpLastRunningState = null;

function startWLPPolling() {
  if (_wlpPollInterval) return; // Bereits aktiv
  console.log('[WLP] Starting status polling');

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
        console.log('[WLP] Status changed, refreshing panel');
        _wlpLastRunningState = currentRunning;
        await loadWLPPanel();
      }
    } catch (e) {
      console.warn('[WLP] Poll error:', e);
    }
  }, 3000); // Alle 3 Sekunden prüfen
}

function stopWLPPolling() {
  if (_wlpPollInterval) {
    clearInterval(_wlpPollInterval);
    _wlpPollInterval = null;
    console.log('[WLP] Stopped status polling');
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
  console.log('[WLP] wlpPanelStart called with id:', id);
  const logArea = document.getElementById('wlp-log-area');
  const logOutput = document.getElementById('wlp-log-output');
  if (!logArea || !logOutput) {
    console.error('[WLP] Log elements not found!', { logArea, logOutput });
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
  console.log('[WLP] wlpStart called from Settings, id:', id);
  switchRightPanel('wlp-panel');
  loadWLPPanel(); // Panel-Inhalt aktualisieren
  wlpPanelStart(id);
}

async function wlpStop(id) {
  console.log('[WLP] wlpStop called from Settings, id:', id);
  await wlpPanelStop(id);
}

async function _streamWLPServer(id, action, outputEl) {
  console.log('[WLP] _streamWLPServer called:', { id, action });
  try {
    const res = await fetch(`/api/wlp/servers/${id}/${action}`, { method: 'POST' });
    console.log('[WLP] Fetch response:', res.status, res.statusText);

    // Fehlerprüfung
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errData = await res.json();
        errorMsg = errData.detail || errorMsg;
      } catch (_) {}
      outputEl.textContent += `\n❌ Fehler: ${errorMsg}`;
      console.error('[WLP] API Error:', errorMsg);
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
          console.log('[WLP] Event:', ev.type);
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
          console.warn('[WLP] Parse error:', parseErr, line);
        }
      }
    }
  } catch (e) {
    console.error('[WLP] Stream error:', e);
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
  console.log('[Maven] mvnPanelRun called with buildId:', buildId);
  const logArea = document.getElementById('maven-log-area');
  const logOutput = document.getElementById('maven-log-output');
  if (!logArea || !logOutput) {
    console.error('[Maven] Log elements not found!', { logArea, logOutput });
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
  console.log('[Maven] mvnRunBuild called from Settings, buildId:', buildId);
  switchRightPanel('maven-panel');
  loadMavenPanel(); // Panel-Inhalt aktualisieren
  mvnPanelRun(buildId);
}

async function _streamMavenBuild(buildId, outputEl) {
  console.log('[Maven] _streamMavenBuild called:', buildId);
  try {
    const res = await fetch(`/api/maven/builds/${buildId}/run`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    console.log('[Maven] Fetch response:', res.status, res.statusText);

    // Fehlerprüfung
    if (!res.ok) {
      let errorMsg = `HTTP ${res.status}: ${res.statusText}`;
      try {
        const errData = await res.json();
        errorMsg = errData.detail || errorMsg;
      } catch (_) {}
      outputEl.textContent += `\n❌ Fehler: ${errorMsg}`;
      console.error('[Maven] API Error:', errorMsg);
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
          console.log('[Maven] Event:', ev.type);
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
          console.warn('[Maven] Parse error:', parseErr, line);
        }
      }
    }
  } catch (e) {
    console.error('[Maven] Stream error:', e);
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
      console.log('[search] Pending searches:', pending.map(p => ({ id: p.id, status: p.status, query: p.query?.substring(0, 30) })));
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
    console.log('[search] Confirm already in progress for', searchId);
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
    console.log('[search] Confirm response:', data);
    // Kurz warten damit der Agent-Poll die Änderung sieht
    await new Promise(r => setTimeout(r, 300));
    await _pollPendingSearches();
    loadSearchPanel(); // History aktualisieren
  } catch (e) {
    console.error('[search] Confirm error:', e);
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
    console.error('Docker Sandbox config load error:', e);
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
        <h4>Proxy-Konfiguration</h4>
        <p style="font-size:12px;color:var(--text-muted);margin-bottom:12px">
          Für Netzwerke mit Proxy-Server. Leer lassen für direkten Internetzugang.
        </p>
        <div class="settings-field">
          <label>Proxy-URL</label>
          <input type="text" id="search-proxy-url" class="settings-input"
                 placeholder="http://proxy.example.com:8080"
                 value="${escapeHtml(config.proxy_url || '')}"
                 onchange="markSettingsModified()">
        </div>
        <div class="settings-field-row" style="display:flex;gap:12px">
          <div class="settings-field" style="flex:1">
            <label>Benutzername</label>
            <input type="text" id="search-proxy-user" class="settings-input"
                   placeholder="(optional)"
                   value="${escapeHtml(config.proxy_username || '')}"
                   onchange="markSettingsModified()">
          </div>
          <div class="settings-field" style="flex:1">
            <label>Passwort</label>
            <input type="password" id="search-proxy-pass" class="settings-input"
                   placeholder="(optional)"
                   value="${config.proxy_password || ''}"
                   onchange="markSettingsModified()">
          </div>
        </div>
        <div class="settings-field">
          <label>No-Proxy (Ausnahmen)</label>
          <input type="text" id="search-no-proxy" class="settings-input"
                 placeholder="localhost,.intern,.local"
                 value="${escapeHtml(config.no_proxy || '')}"
                 onchange="markSettingsModified()">
          <span style="font-size:11px;color:var(--text-muted)">Kommagetrennte Liste von Hosts ohne Proxy</span>
        </div>
        <div class="settings-field">
          <label>Timeout (Sekunden)</label>
          <input type="number" id="search-timeout" class="settings-input" style="width:100px"
                 min="5" max="120"
                 value="${config.timeout_seconds || 30}"
                 onchange="markSettingsModified()">
        </div>
        <div class="settings-field" style="margin-top:12px">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="search-verify-ssl" ${config.verify_ssl !== false ? 'checked' : ''}
                   onchange="markSettingsModified()">
            <span>SSL-Zertifikate verifizieren</span>
          </label>
          <span style="font-size:11px;color:var(--text-muted);display:block;margin-top:4px">
            Deaktivieren für selbstsignierte Zertifikate (z.B. interne Proxys)
          </span>
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
  const statusEl = document.getElementById('search-proxy-status');
  statusEl.textContent = 'Speichere...';
  statusEl.style.color = 'var(--text-muted)';

  const config = {
    proxy_url: document.getElementById('search-proxy-url')?.value || '',
    proxy_username: document.getElementById('search-proxy-user')?.value || '',
    proxy_password: document.getElementById('search-proxy-pass')?.value || '',
    no_proxy: document.getElementById('search-no-proxy')?.value || '',
    timeout_seconds: parseInt(document.getElementById('search-timeout')?.value) || 30,
    verify_ssl: document.getElementById('search-verify-ssl')?.checked ?? true,
  };

  console.log('[Search] Saving config:', { ...config, proxy_password: '***' });

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
    console.error('[Search] Save error:', e);
    statusEl.textContent = '✗ Fehler: ' + e.message;
    statusEl.style.color = 'var(--error)';
  }

  // Status nach 3s ausblenden
  setTimeout(() => {
    if (statusEl) statusEl.textContent = '';
  }, 3000);
}

// Keyboard shortcut to close modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('settings-modal');
    if (modal.style.display === 'flex') {
      closeSettings();
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
      console.warn('[Templates] Failed to load:', res.status);
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
    console.log('[Templates] Loaded', _templates.length, 'templates');
    renderTemplateBar();
  } catch (e) {
    console.error('[Templates] Load error:', e);
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
    console.warn('[Templates] Template not found:', templateId);
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
    setTimeout(() => stepEl.classList.remove('highlighted'), 2000);
  }
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

/**
 * Formatiert eine Dauer in ms zu lesbarem Format.
 * @param {number} ms - Dauer in Millisekunden
 * @returns {string} Formatierte Dauer
 */
function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const mins = Math.floor(ms / 60000);
  const secs = Math.round((ms % 60000) / 1000);
  return `${mins}:${secs.toString().padStart(2, '0')}min`;
}

/**
 * Escapes HTML characters to prevent XSS.
 * @param {string} str - Input string
 * @returns {string} Escaped string
 */
function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
