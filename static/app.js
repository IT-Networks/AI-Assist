// ── State ──
const state = {
  sessionId: crypto.randomUUID(),
  currentModel: null,
  streaming: true,
  context: {
    javaFiles: [],   // [{path, label}]
    includePom: false,
    logId: null,
    logLabel: null,
    pdfIds: [],      // [{id, label}]
    confluenceIds: [], // [{id, label}]
  },
};

// ── Init ──
document.addEventListener("DOMContentLoaded", async () => {
  marked.setOptions({ breaks: true, gfm: true });
  await loadModels();
  await loadIndexStatus();
  document.getElementById("stream-cb").addEventListener("change", e => {
    state.streaming = e.target.checked;
  });
});

// ── Models ──
async function loadModels() {
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    const sel = document.getElementById("model-select");
    sel.innerHTML = "";
    data.models.forEach(m => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.display_name;
      if (m.id === data.default) opt.selected = true;
      sel.appendChild(opt);
    });
    state.currentModel = sel.value;
    sel.addEventListener("change", () => { state.currentModel = sel.value; });
  } catch (e) {
    appendMessage("system", "Modelle konnten nicht geladen werden: " + e.message);
  }
}

// ── Session ──
document.getElementById("clear-btn").addEventListener("click", async () => {
  if (!confirm("Session löschen?")) return;
  await fetch(`/api/chat/${state.sessionId}`, { method: "DELETE" });
  state.sessionId = crypto.randomUUID();
  state.context = { javaFiles: [], includePom: false, logId: null, logLabel: null, pdfIds: [], confluenceIds: [] };
  document.getElementById("messages").innerHTML = "";
  renderContextChips();
  appendMessage("system", "Neue Session gestartet.");
});

// ── Chat ──
function handleInputKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

async function sendMessage() {
  const input = document.getElementById("message-input");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  input.style.height = "auto";
  appendMessage("user", text);

  const sendBtn = document.getElementById("send-btn");
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<span class="spinner"></span>';

  const autoSearch = document.getElementById("auto-search-cb")?.checked || false;
  const contextSources = {
    java_files: state.context.javaFiles.map(f => f.path),
    include_pom: state.context.includePom,
    auto_java_search: autoSearch,
    log_id: state.context.logId || null,
    pdf_ids: state.context.pdfIds.map(p => p.id),
    confluence_page_ids: state.context.confluenceIds.map(c => c.id),
  };

  const payload = {
    session_id: state.sessionId,
    message: text,
    model: state.currentModel,
    stream: state.streaming,
    context_sources: contextSources,
  };

  try {
    if (state.streaming) {
      await sendStreaming(payload);
    } else {
      await sendNonStreaming(payload);
    }
  } catch (e) {
    appendMessage("error", "Fehler: " + e.message);
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "Senden";
  }
}

async function sendNonStreaming(payload) {
  payload.stream = false;
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  const data = await res.json();
  appendMessage("assistant", data.response);
}

async function sendStreaming(payload) {
  const endpoint = "/api/chat/stream";
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }

  const msgDiv = appendMessage("assistant", "");
  const bubble = msgDiv.querySelector(".message-bubble");
  let fullText = "";

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      try {
        const obj = JSON.parse(line.slice(5).trim());
        if (obj.error) throw new Error(obj.error);
        if (obj.token) {
          fullText += obj.token;
          bubble.innerHTML = marked.parse(fullText);
          applyHighlight(bubble);
          scrollToBottom();
        }
      } catch (e) {
        // ignore parse errors for partial chunks
      }
    }
  }
  bubble.innerHTML = marked.parse(fullText);
  applyHighlight(bubble);
  scrollToBottom();
}

// ── Messages ──
function appendMessage(role, text) {
  const messages = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";

  if (role === "assistant") {
    bubble.innerHTML = marked.parse(text);
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
  el.querySelectorAll("pre code").forEach(block => {
    hljs.highlightElement(block);
  });
}

function scrollToBottom() {
  const m = document.getElementById("messages");
  m.scrollTop = m.scrollHeight;
}

// ── Sidebar toggles ──
function toggleSection(id) {
  const body = document.getElementById(id);
  const section = id.replace("-body", "");
  const arrow = document.getElementById(section + "-arrow");
  const isOpen = body.classList.toggle("open");
  if (arrow) arrow.textContent = isOpen ? "▼" : "▶";
}

// ── Java file tree ──
async function loadFileTree() {
  const container = document.getElementById("file-tree");
  container.innerHTML = '<span style="color:var(--text-muted);font-size:0.8rem">Lade...</span>';
  try {
    const res = await fetch("/api/java/tree");
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      container.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.detail}</span>`;
      return;
    }
    const tree = await res.json();
    container.innerHTML = "";
    container.appendChild(renderTreeNode(tree, true));
  } catch (e) {
    container.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.message}</span>`;
  }
}

function renderTreeNode(node, isRoot = false) {
  if (node.type === "file") {
    const div = document.createElement("div");
    div.className = "tree-file";
    div.dataset.path = node.path;
    div.title = node.path;
    div.innerHTML = `<span>📄</span><span>${node.name}</span><span style="color:var(--text-muted);font-size:0.7rem;margin-left:auto">${node.size_kb}KB</span>`;
    div.addEventListener("click", () => addJavaFileToContext(node.path, node.name, div));
    return div;
  }

  const wrapper = document.createElement("div");
  wrapper.className = "tree-node";

  if (!isRoot) {
    const label = document.createElement("div");
    label.className = "tree-dir";
    label.innerHTML = `<div class="tree-dir-label"><span>📁</span><span>${node.name}</span></div>`;
    const children = document.createElement("div");
    children.className = "tree-children";
    label.addEventListener("click", () => children.classList.toggle("open"));
    wrapper.appendChild(label);

    (node.children || []).forEach(child => children.appendChild(renderTreeNode(child)));
    wrapper.appendChild(children);
  } else {
    (node.children || []).forEach(child => wrapper.appendChild(renderTreeNode(child)));
  }

  return wrapper;
}

function addJavaFileToContext(path, name, el) {
  if (state.context.javaFiles.find(f => f.path === path)) return;
  state.context.javaFiles.push({ path, label: name });
  el.classList.add("selected");
  renderContextChips();
}

async function searchClass() {
  const q = document.getElementById("java-search-input").value.trim();
  if (!q) return;
  const container = document.getElementById("class-search-results");
  container.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Suche...</span>';
  try {
    const res = await fetch(`/api/java/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    if (!data.matches || data.matches.length === 0) {
      container.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Keine Treffer</span>';
      return;
    }
    container.innerHTML = "";
    data.matches.forEach(path => {
      const name = path.split("/").pop();
      const div = document.createElement("div");
      div.className = "search-result";
      div.innerHTML = `<div class="sr-title">📄 ${name}</div><div class="sr-space">${path}</div>`;
      div.addEventListener("click", () => {
        addJavaFileToContext(path, name, div);
        div.style.borderColor = "var(--green)";
      });
      container.appendChild(div);
    });
  } catch (e) {
    container.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.message}</span>`;
  }
}

// ── Log upload ──
async function uploadLog() {
  const fileInput = document.getElementById("log-file-input");
  const file = fileInput.files[0];
  if (!file) return;

  const status = document.getElementById("log-status");
  status.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Lade hoch...</span>';

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/logs/upload", { method: "POST", body: formData });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      status.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.detail}</span>`;
      return;
    }
    const data = await res.json();
    state.context.logId = data.id;
    state.context.logLabel = file.name;
    status.innerHTML = `<span style="color:var(--green);font-size:0.78rem">✓ ${data.message}</span>`;
    renderContextChips();
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.message}</span>`;
  }
  fileInput.value = "";
}

// ── PDF upload ──
async function uploadPDF() {
  const fileInput = document.getElementById("pdf-file-input");
  const file = fileInput.files[0];
  if (!file) return;

  const status = document.getElementById("pdf-status");
  status.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Lade hoch...</span>';

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/pdf/upload", { method: "POST", body: formData });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      status.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.detail}</span>`;
      return;
    }
    const data = await res.json();
    state.context.pdfIds.push({ id: data.id, label: file.name });
    status.innerHTML = `<span style="color:var(--green);font-size:0.78rem">✓ ${data.message}</span>`;
    renderContextChips();
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.message}</span>`;
  }
  fileInput.value = "";
}

// ── Confluence search ──
async function searchConfluence() {
  const q = document.getElementById("conf-search-q").value.trim();
  const space = document.getElementById("conf-search-space").value.trim();
  const labels = document.getElementById("conf-search-labels").value.trim();
  if (!q) return;

  const container = document.getElementById("conf-search-results");
  container.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Suche...</span>';

  let url = `/api/confluence/search?q=${encodeURIComponent(q)}`;
  if (space) url += `&space=${encodeURIComponent(space)}`;
  if (labels) url += `&labels=${encodeURIComponent(labels)}`;

  try {
    const res = await fetch(url);
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      container.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.detail}</span>`;
      return;
    }
    const results = await res.json();
    container.innerHTML = "";
    if (results.length === 0) {
      container.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Keine Ergebnisse</span>';
      return;
    }
    results.forEach(r => {
      const div = document.createElement("div");
      div.className = "search-result";
      div.innerHTML = `
        <div class="sr-title">${escapeHtml(r.title)}</div>
        <div class="sr-space">${escapeHtml(r.space)} · ID: ${r.id}</div>
        <div class="sr-excerpt">${escapeHtml(r.excerpt || "").slice(0, 120)}...</div>
      `;
      div.addEventListener("click", () => addConfluencePageToContext(r.id, r.title, div));
      container.appendChild(div);
    });
  } catch (e) {
    container.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.message}</span>`;
  }
}

async function loadConfluencePage() {
  const pageId = document.getElementById("conf-page-id").value.trim();
  if (!pageId) return;
  const status = document.getElementById("conf-status");
  status.innerHTML = '<span style="color:var(--text-muted);font-size:0.78rem">Lade...</span>';
  try {
    const res = await fetch(`/api/confluence/page/${pageId}`);
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      status.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.detail}</span>`;
      return;
    }
    const page = await res.json();
    addConfluencePageToContext(page.id, page.title, null);
    status.innerHTML = `<span style="color:var(--green);font-size:0.78rem">✓ "${escapeHtml(page.title)}" geladen</span>`;
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red);font-size:0.78rem">${e.message}</span>`;
  }
}

function addConfluencePageToContext(id, title, el) {
  if (state.context.confluenceIds.find(c => c.id === id)) return;
  state.context.confluenceIds.push({ id, label: title });
  if (el) el.style.borderColor = "var(--green)";
  renderContextChips();
}

// ── Context chips ──
function renderContextChips() {
  const container = document.getElementById("context-chips");
  container.innerHTML = "";

  const all = [
    ...state.context.javaFiles.map(f => ({ key: "java:" + f.path, label: f.label, type: "Java", remove: () => { state.context.javaFiles = state.context.javaFiles.filter(x => x.path !== f.path); } })),
    ...(state.context.logId ? [{ key: "log", label: state.context.logLabel, type: "Log", remove: () => { state.context.logId = null; state.context.logLabel = null; } }] : []),
    ...state.context.pdfIds.map(p => ({ key: "pdf:" + p.id, label: p.label, type: "PDF", remove: () => { state.context.pdfIds = state.context.pdfIds.filter(x => x.id !== p.id); } })),
    ...state.context.confluenceIds.map(c => ({ key: "conf:" + c.id, label: c.label, type: "Confluence", remove: () => { state.context.confluenceIds = state.context.confluenceIds.filter(x => x.id !== c.id); } })),
  ];

  if (all.length === 0) {
    container.innerHTML = '<span style="font-size:0.78rem;color:var(--text-muted)">Kein Kontext ausgewählt</span>';
    return;
  }

  all.forEach(item => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerHTML = `
      <span class="chip-label" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</span>
      <span class="chip-type">${item.type}</span>
      <span class="chip-remove" title="Entfernen">×</span>
    `;
    chip.querySelector(".chip-remove").addEventListener("click", () => {
      item.remove();
      renderContextChips();
    });
    container.appendChild(chip);
  });
}

// ── Utilities ──
function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── Java Index ──
async function loadIndexStatus() {
  const el = document.getElementById("index-status");
  if (!el) return;
  try {
    const res = await fetch("/api/java/index/status");
    if (!res.ok) { el.textContent = "Status nicht verfügbar"; return; }
    const d = await res.json();
    if (d.is_built) {
      el.textContent = `${d.indexed_files} Dateien indexiert · ${d.last_build}`;
      el.style.color = "var(--green)";
    } else {
      el.textContent = "Kein Index – bitte aufbauen";
      el.style.color = "var(--text-muted)";
    }
  } catch {
    el.textContent = "Status nicht verfügbar";
  }
}

async function buildJavaIndex() {
  const el = document.getElementById("index-status");
  if (el) { el.textContent = "Index wird aufgebaut..."; el.style.color = "var(--yellow)"; }
  try {
    const res = await fetch("/api/java/index/build?background=false", { method: "POST" });
    const d = await res.json();
    if (!res.ok) {
      if (el) { el.textContent = d.detail || "Fehler"; el.style.color = "var(--red)"; }
      return;
    }
    appendMessage("system",
      `Index aufgebaut: ${d.indexed} Dateien indexiert, ${d.skipped} unverändert, ${d.stale_removed} veraltet entfernt (${d.duration_s}s)`
    );
    await loadIndexStatus();
  } catch (e) {
    if (el) { el.textContent = e.message; el.style.color = "var(--red)"; }
  }
}

async function deleteJavaIndex() {
  if (!confirm("Java-Index wirklich löschen?")) return;
  await fetch("/api/java/index", { method: "DELETE" });
  await loadIndexStatus();
  appendMessage("system", "Java-Index gelöscht.");
}

// Auto-resize textarea
document.getElementById("message-input").addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 150) + "px";
});
