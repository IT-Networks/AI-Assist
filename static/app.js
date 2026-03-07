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
    pane.innerHTML = welcomeHTML();
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
    chat.context = JSON.parse(JSON.stringify(state.context));
    chat.pendingConfirmation = state.pendingConfirmation;
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

  // Create initial chat
  await createNewChat();
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
  const skillIds = state.activeSkills.join(',');
  const res = await fetch(`/api/agent/session/new?mode=${state.mode}${skillIds ? '&skill_ids=' + skillIds : ''}`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`Session creation failed: ${res.status}`);
  const data = await res.json();
  return data.session_id;
}

// ── MultiChat Functions ──
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
  state.context = JSON.parse(JSON.stringify(incomingChat.context));

  // Pane des eingehenden Chats in den DOM hängen (inklusive aller laufenden DOM-Updates)
  messagesContainer.appendChild(incomingChat.pane);

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
    if (newTitle) chat.title = newTitle;
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
  renderChatList();
}

function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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

async function sendMessage() {
  const input = document.getElementById('message-input');
  const text = input.value.trim();
  if (!text) return;

  const activeChat = chatManager.getActive();
  // Verhindere Doppel-Senden wenn dieser Chat bereits streamt
  if (activeChat?.streamingState || _chatAbortController) return;

  input.value = '';
  input.style.height = 'auto';
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

async function sendAgentChat(message, abortSignal, chat) {
  const payload = {
    message,
    session_id: chat.sessionId,
    model: state.currentModel,
    skill_ids: state.activeSkills.length > 0 ? state.activeSkills : null,
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

    case 'error':
      appendMessageToPane(chat.pane, 'error', data.error || 'Unbekannter Fehler');
      break;

    case 'usage':
      displayTokenUsage(data, chat);
      break;

    case 'done':
      if (data.usage) displayTokenUsage(data.usage, chat);
      break;
  }
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
    uploads: 'Upload-Verzeichnis und Limits'
  }
};

async function openSettings() {
  const modal = document.getElementById('settings-modal');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

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

  fields.forEach(field => {
    const key = field.dataset.key;
    const idx = field.dataset.index;

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

  // Datenquellen haben eigene Speicher-Buttons (kein generischer Save)
  if (section === 'data_sources') {
    updateSettingsStatus('Datenquellen werden direkt über die Formular-Buttons gespeichert', 'success');
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

// Keyboard shortcut to close modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('settings-modal');
    if (modal.style.display === 'flex') {
      closeSettings();
    }
  }
});
