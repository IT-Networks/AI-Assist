# Enhancement Confirmation UI Component Design

## Overview

This component displays collected MCP context to users and allows them to confirm or reject the enhancement before task decomposition proceeds.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Enhancement Flow                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Backend (Orchestrator)              Frontend (app.js)           │
│  ────────────────────               ────────────────────         │
│                                                                  │
│  ENHANCEMENT_START ──────────────→ showEnhancementProgress()     │
│       │                                    │                     │
│       │ (MCP Context Collection)           │ (Show spinner)      │
│       ▼                                    ▼                     │
│  ENHANCEMENT_COMPLETE ───────────→ showEnhancementConfirmation() │
│       │                                    │                     │
│       │ (Yield + wait for send())          │ (Display context)   │
│       │                                    ▼                     │
│  CONFIRM_REQUIRED ───────────────→ User sees panel               │
│       │                                    │                     │
│       │                            ┌───────┴───────┐             │
│       │                            │               │             │
│       │                      [Confirm]        [Reject]           │
│       │                            │               │             │
│       ▼                            ▼               ▼             │
│  generator.send(true/false) ←── confirmEnhancement(true/false)   │
│       │                                                          │
│       ▼                                                          │
│  ENHANCEMENT_CONFIRMED/REJECTED ─→ hideEnhancementPanel()        │
│       │                                                          │
│       ▼                                                          │
│  Continue to Task Decomposition                                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Component Structure

### HTML Structure (in index.html)

```html
<!-- Enhancement Confirmation Panel (inside #confirm-panel) -->
<div id="pending-enhancement" style="display:none">
  <!-- Header with enhancement type -->
  <div class="confirm-header enhancement-header">
    <span class="confirm-icon enhancement-icon" id="enhancement-type-icon">🔍</span>
    <span class="confirm-title" id="enhancement-type-label">Kontext-Sammlung</span>
    <span class="enhancement-badge" id="enhancement-source-count">3 Quellen</span>
  </div>

  <!-- Query Preview -->
  <div class="enhancement-query">
    <label>Anfrage:</label>
    <span id="enhancement-query-text">...</span>
  </div>

  <!-- Context Items List (collapsible) -->
  <div class="enhancement-context-list" id="enhancement-context-list">
    <!-- Dynamically populated -->
  </div>

  <!-- Summary -->
  <div class="enhancement-summary" id="enhancement-summary">
    <!-- Summary text from EnrichedPrompt -->
  </div>

  <!-- Actions -->
  <div class="confirm-actions enhancement-actions">
    <button class="btn btn-success" onclick="confirmEnhancement(true)">
      ✓ Mit Kontext fortfahren
    </button>
    <button class="btn btn-secondary" onclick="confirmEnhancement(false)">
      ✗ Ohne Kontext fortfahren
    </button>
    <button class="btn btn-link" onclick="toggleEnhancementDetails()">
      Details anzeigen
    </button>
  </div>
</div>

<!-- Enhancement Progress (shown during collection) -->
<div id="enhancement-progress" style="display:none">
  <div class="confirm-header">
    <span class="confirm-icon spinner">⟳</span>
    <span class="confirm-title">Sammle Kontext...</span>
  </div>
  <div class="enhancement-progress-details">
    <span id="enhancement-progress-type">research</span>
    <div class="progress-bar">
      <div class="progress-fill" id="enhancement-progress-fill"></div>
    </div>
  </div>
</div>
```

### Context Item Template

```html
<div class="enhancement-context-item" data-source="wiki">
  <div class="context-item-header">
    <span class="context-source-badge source-wiki">Wiki</span>
    <span class="context-item-title">API Documentation</span>
    <span class="context-relevance">90%</span>
  </div>
  <div class="context-item-content">
    Content preview...
  </div>
  <div class="context-item-meta">
    <a href="..." target="_blank">Öffnen</a>
  </div>
</div>
```

## CSS Additions (style.css)

```css
/* Enhancement Confirmation Styles */
.enhancement-header {
  background: linear-gradient(135deg, #1a365d 0%, #2d3748 100%);
  border-bottom: 2px solid #4299e1;
}

.enhancement-icon {
  font-size: 1.2em;
}

.enhancement-badge {
  background: #4299e1;
  color: white;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 0.75em;
  margin-left: auto;
}

.enhancement-query {
  padding: 12px;
  background: rgba(66, 153, 225, 0.1);
  border-radius: 4px;
  margin: 12px;
}

.enhancement-query label {
  color: #a0aec0;
  font-size: 0.8em;
  display: block;
  margin-bottom: 4px;
}

.enhancement-query span {
  color: #e2e8f0;
  font-style: italic;
}

.enhancement-context-list {
  max-height: 300px;
  overflow-y: auto;
  margin: 0 12px;
}

.enhancement-context-item {
  background: #1e1e1e;
  border: 1px solid #333;
  border-radius: 6px;
  margin-bottom: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.enhancement-context-item:hover {
  border-color: #4299e1;
}

.context-item-header {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #252525;
  gap: 8px;
}

.context-source-badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.7em;
  text-transform: uppercase;
  font-weight: 600;
}

/* Source-specific colors */
.source-wiki { background: #805ad5; color: white; }
.source-code { background: #38a169; color: white; }
.source-web { background: #dd6b20; color: white; }
.source-handbook { background: #3182ce; color: white; }
.source-memory { background: #d53f8c; color: white; }
.source-sequential { background: #319795; color: white; }
.source-hypothesis { background: #d69e2e; color: black; }

.context-item-title {
  flex: 1;
  color: #e2e8f0;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.context-relevance {
  color: #68d391;
  font-size: 0.8em;
}

.context-item-content {
  padding: 12px;
  color: #a0aec0;
  font-size: 0.9em;
  line-height: 1.5;
  max-height: 80px;
  overflow: hidden;
}

.context-item-meta {
  padding: 8px 12px;
  border-top: 1px solid #333;
  font-size: 0.8em;
}

.context-item-meta a {
  color: #63b3ed;
}

.enhancement-summary {
  padding: 12px;
  margin: 12px;
  background: rgba(72, 187, 120, 0.1);
  border-left: 3px solid #48bb78;
  border-radius: 0 4px 4px 0;
  color: #a0aec0;
  font-size: 0.9em;
}

.enhancement-actions {
  flex-wrap: wrap;
}

.enhancement-actions .btn-secondary {
  background: #4a5568;
  border-color: #4a5568;
}

.enhancement-actions .btn-link {
  background: none;
  border: none;
  color: #63b3ed;
  padding: 8px;
}

/* Progress Animation */
.spinner {
  animation: spin 1s linear infinite;
  display: inline-block;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.progress-bar {
  height: 4px;
  background: #333;
  border-radius: 2px;
  margin-top: 8px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #4299e1, #63b3ed);
  width: 0%;
  animation: progress-pulse 2s ease-in-out infinite;
}

@keyframes progress-pulse {
  0%, 100% { width: 30%; }
  50% { width: 70%; }
}
```

## JavaScript Functions (app.js)

```javascript
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
  progress.style.display = 'block';

  document.getElementById('enhancement-progress-type').textContent =
    ENHANCEMENT_LABELS[data.detection_type] || data.detection_type;
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
    confirmation_message: data.confirmation_message
  };

  if (isActive) {
    showEnhancementConfirmation(data);
  }
}

/**
 * Show the enhancement confirmation panel with context items
 */
function showEnhancementConfirmation(data) {
  // Hide progress
  document.getElementById('enhancement-progress').style.display = 'none';
  document.getElementById('no-confirmation').style.display = 'none';
  document.getElementById('pending-confirmation').style.display = 'none';

  // Show enhancement panel
  const panel = document.getElementById('pending-enhancement');
  panel.style.display = 'block';

  // Set type icon and label
  const type = data.detection_type || 'research';
  document.getElementById('enhancement-type-icon').textContent = ENHANCEMENT_ICONS[type];
  document.getElementById('enhancement-type-label').textContent = ENHANCEMENT_LABELS[type];

  // Set source count
  document.getElementById('enhancement-source-count').textContent =
    `${data.context_count} Kontext-Elemente`;

  // Set query preview
  const chat = chatManager.getActive();
  if (chat?.pendingEnhancement?.query_preview) {
    document.getElementById('enhancement-query-text').textContent =
      chat.pendingEnhancement.query_preview + '...';
  }

  // Set summary
  document.getElementById('enhancement-summary').innerHTML =
    marked.parse(data.summary || 'Kontext wurde gesammelt.');

  // Show pending badge
  document.getElementById('pending-count').style.display = 'inline';
}

/**
 * Handle CONFIRM_REQUIRED for enhancement type
 */
function handleEnhancementConfirmRequired(data, chat) {
  // Already handled in handleEnhancementComplete
  // This is the signal that we're waiting for user input
  console.log('[enhancement] Waiting for user confirmation');
}

/**
 * Confirm or reject the enhancement context
 */
async function confirmEnhancement(confirmed) {
  const chat = chatManager.getActive();
  if (!chat?.pendingEnhancement) return;

  try {
    const res = await fetch(`/api/agent/confirm/${chat.sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirmed,
        type: 'enhancement'
      })
    });

    if (!res.ok) {
      console.error('[enhancement] Confirmation failed:', await res.text());
    }

    // Clear pending state
    chat.pendingEnhancement = null;
    hideEnhancementPanel();

  } catch (err) {
    console.error('[enhancement] Confirmation error:', err);
    appendMessageToPane(chat.pane, 'error', `Enhancement-Bestätigung fehlgeschlagen: ${err.message}`);
  }
}

/**
 * Hide the enhancement confirmation panel
 */
function hideEnhancementPanel() {
  document.getElementById('enhancement-progress').style.display = 'none';
  document.getElementById('pending-enhancement').style.display = 'none';
  document.getElementById('no-confirmation').style.display = 'block';
  document.getElementById('pending-count').style.display = 'none';
}

/**
 * Handle ENHANCEMENT_CONFIRMED event
 */
function handleEnhancementConfirmed(data, chat) {
  const isActive = chat.id === chatManager.activeId;

  chat.pendingEnhancement = null;

  if (isActive) {
    hideEnhancementPanel();
    appendMessageToPane(chat.pane, 'system',
      `✓ Kontext bestätigt (${data.context_length} Zeichen)`);
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
    appendMessageToPane(chat.pane, 'system',
      '⚠ Ohne Kontext fortfahren...');
  }
}

/**
 * Toggle detailed context view
 */
function toggleEnhancementDetails() {
  const list = document.getElementById('enhancement-context-list');
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

  try {
    const res = await fetch(`/api/agent/enhancement/${chat.sessionId}`);
    if (!res.ok) throw new Error('Failed to load details');

    const data = await res.json();
    renderContextItems(data.context_items || []);

  } catch (err) {
    console.error('[enhancement] Failed to load details:', err);
  }
}

/**
 * Render context items in the list
 */
function renderContextItems(items) {
  const list = document.getElementById('enhancement-context-list');
  list.innerHTML = '';

  items.forEach(item => {
    const sourceClass = getSourceClass(item.source);
    const relevancePercent = Math.round((item.relevance || 0.5) * 100);

    const el = document.createElement('div');
    el.className = 'enhancement-context-item';
    el.innerHTML = `
      <div class="context-item-header">
        <span class="context-source-badge ${sourceClass}">${item.source}</span>
        <span class="context-item-title">${escapeHtml(item.title)}</span>
        <span class="context-relevance">${relevancePercent}%</span>
      </div>
      <div class="context-item-content">
        ${escapeHtml(item.content?.substring(0, 200) || '')}...
      </div>
      ${item.url || item.file_path ? `
        <div class="context-item-meta">
          ${item.url ? `<a href="${item.url}" target="_blank">Öffnen</a>` : ''}
          ${item.file_path ? `<span>${item.file_path}</span>` : ''}
        </div>
      ` : ''}
    `;

    list.appendChild(el);
  });
}

/**
 * Get CSS class for source type
 */
function getSourceClass(source) {
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
    'research_report': 'source-wiki',
    'sequential_analysis': 'source-sequential',
    'sequential_conclusion': 'source-sequential',
    'brainstorm_exploration': 'source-hypothesis',
    'code_analysis': 'source-code',
    'analysis_insight': 'source-code'
  };
  return mapping[source] || 'source-web';
}
```

## Event Integration (SSE Handler)

Add to the SSE event switch in `handleSSEEvent()`:

```javascript
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
```

## Backend API Endpoint

Add endpoint for fetching enhancement details:

```python
# app/api/routes/agent.py

@router.get("/enhancement/{session_id}")
async def get_enhancement_details(session_id: str):
    """Get current enhancement context details."""
    from app.agent.prompt_enhancer import get_prompt_enhancer

    enhancer = get_prompt_enhancer()

    # Get from cache if available
    # This requires storing the EnrichedPrompt in session state
    orchestrator = get_orchestrator()
    state = orchestrator._get_state(session_id)

    if hasattr(state, 'pending_enrichment') and state.pending_enrichment:
        enriched = state.pending_enrichment
        return {
            "context_items": [item.to_dict() for item in enriched.context_items],
            "summary": enriched.summary,
            "enhancement_type": enriched.enhancement_type.value,
            "total_length": enriched.total_context_length
        }

    return {"context_items": [], "summary": "", "enhancement_type": "none"}
```

## State Management Updates

### Chat Object Extension

```javascript
// In chatManager.createChat():
const chat = {
  // ... existing fields
  pendingEnhancement: null,  // NEW: Track enhancement state
};
```

### State Persistence

```javascript
// In chatManager.saveActiveState():
chat.pendingEnhancement = state.pendingEnhancement;
```

## Interaction Flows

### Flow 1: User Query with Enhancement

```
1. User types: "Implementiere wie im Wiki beschrieben"
2. Backend detects: enhancement_type = RESEARCH
3. Frontend shows: Progress indicator with "Recherche-Kontext"
4. Backend collects: Wiki, Code, Handbook results
5. Frontend shows: Confirmation panel with context items
6. User clicks: "Mit Kontext fortfahren"
7. Backend receives: confirmed = true
8. Backend proceeds: Task decomposition with enriched context
```

### Flow 2: User Rejects Enhancement

```
1. User types: "Debug warum der Test fehlschlägt"
2. Backend detects: enhancement_type = SEQUENTIAL
3. Frontend shows: Progress indicator
4. Backend collects: Sequential thinking results
5. Frontend shows: Confirmation panel
6. User clicks: "Ohne Kontext fortfahren"
7. Backend receives: confirmed = false
8. Backend proceeds: Task decomposition without context
```

### Flow 3: No Enhancement Needed

```
1. User types: "Schreibe Hello World"
2. Backend detects: enhancement_type = NONE
3. No events sent
4. Backend proceeds: Direct processing
```

## Testing Checklist

- [ ] Progress indicator shows during collection
- [ ] Correct icon/label for each enhancement type
- [ ] Context items render correctly
- [ ] Source badges have correct colors
- [ ] Confirm button sends true to backend
- [ ] Reject button sends false to backend
- [ ] Panel hides after confirmation
- [ ] Multi-chat state isolation works
- [ ] Keyboard shortcut (Escape) closes panel
- [ ] Details toggle loads full context

## Future Enhancements

1. **Edit Context**: Allow users to remove specific items before confirming
2. **Add Context**: Let users manually add files/URLs to context
3. **Context History**: Show previous enhancements in chat history
4. **Auto-confirm Setting**: Option to skip confirmation for trusted queries
