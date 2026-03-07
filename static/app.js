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
  state.context = JSON.parse(JSON.stringify(incomingChat.context));

  // Nachrichten-Historie vom Server laden wenn Chat vom Disk wiederhergestellt wird
  if (incomingChat.needsRestore) {
    incomingChat.needsRestore = false;
    incomingChat.pane.innerHTML = '';
    try {
      const res = await fetch(`/api/agent/session/${incomingChat.sessionId}/history`);
      if (res.ok) {
        const { messages } = await res.json();
        for (const msg of messages) {
          if (msg.role === 'user' || msg.role === 'assistant') {
            appendMessageToPane(incomingChat.pane, msg.role, msg.content);
          }
        }
      }
    } catch (e) {
      incomingChat.pane.innerHTML = welcomeHTML();
      console.error('Failed to restore chat history:', e);
    }
  }

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
    case 'plan_then_execute':
      indicator.classList.add('mode-plan');
      indicator.innerHTML = '<span class="mode-icon">&#128203;</span><span class="mode-text">Plan &amp; Ausführen</span>';
      if (welcomeMode) welcomeMode.textContent = 'Plan & Ausführen';
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
  `;
  await ttLoadAll();
}

async function ttLoadAll() {
  const [stRes, svRes] = await Promise.all([
    fetch('/api/testtool/stages'),
    fetch('/api/testtool/services'),
  ]);
  const stData = await stRes.json();
  const svData = await svRes.json();

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
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">WLP SERVER</h3>
      <p class="settings-section-desc">WebSphere Liberty Profile Server starten, server.xml prüfen und Artefakt validieren. Start wird per SSE-Stream überwacht.</p>
    </div>
    <div id="wlp-list"><div class="spinner-inline"></div></div>
    <div class="settings-add-form">
      <h4>Server hinzufügen</h4>
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
  const form = document.getElementById('settings-form');
  form.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-title">MAVEN BUILD</h3>
      <p class="settings-section-desc">Maven-Builds definieren und per Klick ausführen. Build-Ausgabe wird per SSE live gestreamt.</p>
    </div>
    <div class="settings-field">
      <label>mvn-Executable</label>
      <input id="mvn-exec" type="text" class="settings-input" placeholder="mvn" value="">
    </div>
    <div id="mvn-builds-list"><div class="spinner-inline"></div></div>
    <div class="settings-add-form">
      <h4>Build hinzufügen</h4>
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

async function mvnLoadBuilds() {
  const res = await fetch('/api/maven/builds');
  const data = await res.json();

  const execInput = document.getElementById('mvn-exec');
  if (execInput) execInput.value = data.mvn_executable || 'mvn';

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
  // Executable speichern
  const exec = document.getElementById('mvn-exec').value;
  if (exec) await fetch('/api/settings/section/maven', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ mvn_executable: exec }) });
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
    const [stRes, svRes] = await Promise.all([fetch('/api/testtool/stages'), fetch('/api/testtool/services')]);
    const stData = await stRes.json();
    const svData = await svRes.json();

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
  const stageUrl = document.getElementById('testtool-url-select')?.value || '';
  const params = _collectParams(svcId);
  const resultArea = document.getElementById('testtool-result-area');
  const resultPre = document.getElementById('testtool-result-pre');
  const badge = document.getElementById('testtool-status-badge');

  resultArea.style.display = 'block';
  resultPre.textContent = '⏳ Ausführung...';
  badge.textContent = '';

  try {
    const res = await fetch(`/api/testtool/execute/${svcId}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ params, stage_url: stageUrl }),
    });
    const data = await res.json();
    badge.textContent = `HTTP ${data.status_code}`;
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
  const logArea = document.getElementById('wlp-log-area');
  const logOutput = document.getElementById('wlp-log-output');
  logArea.style.display = 'block';
  logOutput.textContent = '';
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

async function _streamWLPServer(id, action, outputEl) {
  try {
    const res = await fetch(`/api/wlp/servers/${id}/${action}`, { method: 'POST' });
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
          if (ev.type === 'output') {
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
          } else if (ev.type === 'done') {
            outputEl.textContent += `\n[Exit: ${ev.exit_code}]`;
            loadWLPPanel();
          }
        } catch (_) {}
      }
    }
  } catch (e) {
    outputEl.textContent += `\nFehler: ${e.message}`;
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
  const logArea = document.getElementById('maven-log-area');
  const logOutput = document.getElementById('maven-log-output');
  logArea.style.display = 'block';
  logOutput.textContent = '';
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
  switchRightPanel('maven-panel');
  mvnPanelRun(buildId);
}

async function _streamMavenBuild(buildId, outputEl) {
  try {
    const res = await fetch(`/api/maven/builds/${buildId}/run`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
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
            outputEl.textContent += `$ ${ev.cmd}\n`;
          }
        } catch (_) {}
      }
    }
  } catch (e) {
    outputEl.textContent += `\nFehler: ${e.message}`;
  }
}

// ── Auto-load operative panels when tab switches ──────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Panel-Tab-Klick-Handler für operative Panels
  document.querySelectorAll('[data-panel]').forEach(tab => {
    const panel = tab.getAttribute('data-panel');
    if (['mq-panel','testtool-panel','wlp-panel','maven-panel'].includes(panel)) {
      tab.addEventListener('click', () => {
        if (panel === 'mq-panel') loadMQPanel();
        else if (panel === 'testtool-panel') loadTestToolPanel();
        else if (panel === 'wlp-panel') loadWLPPanel();
        else if (panel === 'maven-panel') loadMavenPanel();
      });
    }
  });
});

// Keyboard shortcut to close modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('settings-modal');
    if (modal.style.display === 'flex') {
      closeSettings();
    }
  }
});
