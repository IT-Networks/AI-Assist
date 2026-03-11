# Design: Loop-Prävention, Kontext-Anzeige und Summarizer-Aktivierung

## Problem-Analyse

### 1. Loop-Problem bei Datei-Bearbeitung

**Symptom**: Nach einer Datei-Bearbeitung versucht die KI wiederholt, weitere Dateien zu bearbeiten.

**Ursache**:
- Loop-Prävention existiert nur für `read_file` (max 2x pro Datei)
- Für `edit_file` und `write_file` fehlt die Prävention
- `max_iterations = 30` erlaubt sehr viele Durchläufe
- Kein klares "Task Complete" Signal für das LLM

**Code-Stelle**: `orchestrator.py:1186-1198` (nur read_file)

### 2. Fehlende Kontext-Anzeige

**Symptom**: User sieht nicht, wie viel Kontext noch verfügbar ist.

**Claude Code Referenz**:
- Zeigt "Context: X/Y tokens" in der Statusleiste
- Warnung bei >80% Auslastung
- Sichtbare Komprimierung mit Animation

### 3. Summarizer nicht sichtbar aktiv

**Symptom**: Summarizer läuft nicht oder User sieht es nicht.

**Ursache**:
- Summarizer wird nur in `_call_llm_with_tools` aufgerufen (Zeile 1670-1682)
- Nur bei Streaming-Response aktiviert, nicht im Tool-Loop
- Kein Event wird emittiert wenn Komprimierung stattfindet
- `COMPACTION` Event existiert aber wird nie genutzt

---

## Lösungs-Design

### 1. Loop-Prävention erweitern

```python
# In AgentState hinzufügen:
@dataclass
class AgentState:
    # ... existing fields ...
    edit_files_this_request: Dict[str, int] = field(default_factory=dict)
    write_files_this_request: Dict[str, int] = field(default_factory=dict)
    task_completed: bool = False  # LLM kann Task als erledigt markieren
```

**Neue Loop-Checks**:

```python
# Für edit_file (max 2x pro Datei)
if tool_call.name == "edit_file":
    file_path = tool_call.arguments.get("path", "")
    edit_count = state.edit_files_this_request.get(file_path, 0)
    if edit_count >= 2:
        # Skip mit Hinweis
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": f"[HINWEIS] Die Datei '{file_path}' wurde bereits {edit_count}x bearbeitet. "
                       "Bitte prüfe ob die bisherigen Änderungen korrekt sind."
        })
        continue
    state.edit_files_this_request[file_path] = edit_count + 1

# Für write_file (max 1x pro Datei ohne Bestätigung)
if tool_call.name == "write_file":
    file_path = tool_call.arguments.get("path", "")
    write_count = state.write_files_this_request.get(file_path, 0)
    if write_count >= 1 and state.mode != AgentMode.WRITE_WITH_CONFIRM:
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": f"[HINWEIS] Die Datei '{file_path}' wurde bereits geschrieben. "
                       "Weitere Schreibvorgänge erfordern explizite Bestätigung."
        })
        continue
    state.write_files_this_request[file_path] = write_count + 1
```

**System-Prompt Erweiterung** (in `SYSTEM_PROMPT`):

```
## WICHTIG: Aufgaben-Abschluss

Wenn du eine Aufgabe abgeschlossen hast:
1. Führe KEINE weiteren Tool-Calls aus, es sei denn der User fragt explizit danach
2. Fasse kurz zusammen was du gemacht hast
3. Frage ob weitere Änderungen gewünscht sind

Nach einer Datei-Bearbeitung:
- Bearbeite NICHT automatisch weitere Dateien
- Erkläre was geändert wurde
- Warte auf weitere Anweisungen vom User
```

### 2. Kontext-Anzeige implementieren

**Backend**: Neues Event mit Kontext-Info

```python
# Nach jedem LLM-Call: Kontext-Status senden
class AgentEventType(str, Enum):
    # ... existing ...
    CONTEXT_STATUS = "context_status"  # Kontext-Auslastung
```

**Kontext-Event senden**:

```python
# In run() Loop nach jedem Tool-Call:
context_tokens = estimate_messages_tokens(messages)
model_limit = settings.llm.llm_context_limits.get(model, settings.llm.default_context_limit)
yield AgentEvent(AgentEventType.CONTEXT_STATUS, {
    "current_tokens": context_tokens,
    "limit_tokens": model_limit,
    "percent": round(context_tokens / model_limit * 100, 1),
    "warning": context_tokens > model_limit * 0.8,
    "compaction_pending": context_tokens > model_limit * 0.9
})
```

**Frontend**: Status-Anzeige in Chat-Header

```javascript
// In handleAgentEvent():
case 'context_status':
    updateContextIndicator(data);
    break;

function updateContextIndicator(data) {
    const indicator = document.getElementById('context-indicator');
    if (!indicator) return;

    const percent = data.percent;
    const color = percent > 90 ? 'var(--danger)' :
                  percent > 80 ? 'var(--warning)' :
                  'var(--text-secondary)';

    indicator.innerHTML = `
        <span style="color: ${color}">
            ${data.current_tokens.toLocaleString()} / ${data.limit_tokens.toLocaleString()} tokens
            (${percent}%)
        </span>
    `;

    if (data.compaction_pending) {
        indicator.innerHTML += ' <span style="color: var(--warning)">⚠ Komprimierung bald</span>';
    }
}
```

**HTML**: Indikator im Chat-Header

```html
<div class="chat-header">
    <!-- ... existing elements ... -->
    <div id="context-indicator" class="context-indicator"></div>
</div>
```

### 3. Summarizer aktivieren und sichtbar machen

**Problem**: Summarizer wird nur bei finalem Streaming aufgerufen.

**Lösung**: Auch im Tool-Loop aktivieren + Event emittieren.

```python
# In run() vor LLM-Call (ca. Zeile 942):
async def run(self, ...):
    # ... setup code ...

    for iteration in range(self.max_iterations):
        # Context-Check VOR jedem LLM-Call
        context_tokens = estimate_messages_tokens(messages)
        model_limit = settings.llm.llm_context_limits.get(
            model or settings.llm.default_model,
            settings.llm.default_context_limit
        )

        # Kontext-Status Event
        yield AgentEvent(AgentEventType.CONTEXT_STATUS, {
            "current_tokens": context_tokens,
            "limit_tokens": model_limit,
            "percent": round(context_tokens / model_limit * 100, 1),
            "iteration": iteration + 1,
            "max_iterations": self.max_iterations
        })

        # Summarizer bei 75% Auslastung aktivieren
        if context_tokens > model_limit * 0.75:
            old_tokens = context_tokens
            summarized = await self.summarizer.summarize_if_needed(
                messages,
                target_tokens=int(model_limit * 0.6)
            )
            if summarized:
                messages = summarized
                new_tokens = estimate_messages_tokens(messages)
                state.compaction_count += 1

                # COMPACTION Event für UI
                yield AgentEvent(AgentEventType.COMPACTION, {
                    "old_tokens": old_tokens,
                    "new_tokens": new_tokens,
                    "saved_tokens": old_tokens - new_tokens,
                    "compaction_count": state.compaction_count
                })
```

**Frontend**: Komprimierungs-Anzeige

```javascript
case 'compaction':
    showCompactionNotification(data);
    updateContextIndicator({
        current_tokens: data.new_tokens,
        limit_tokens: data.limit_tokens || state.contextLimit,
        percent: (data.new_tokens / (data.limit_tokens || state.contextLimit)) * 100
    });
    break;

function showCompactionNotification(data) {
    const saved = data.saved_tokens.toLocaleString();
    const msg = `Kontext komprimiert: ${saved} Tokens eingespart`;

    // Toast-Notification oder Status-Update
    updateChatStatus(msg, 'info');

    // Optional: Animation im Context-Indicator
    const indicator = document.getElementById('context-indicator');
    indicator.classList.add('compacting');
    setTimeout(() => indicator.classList.remove('compacting'), 1000);
}
```

**CSS Animation**:

```css
.context-indicator.compacting {
    animation: pulse 0.5s ease-in-out 2;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
```

---

## Implementation Plan

### Phase 1: Loop-Prävention (Kritisch)
1. `AgentState` erweitern mit `edit_files_this_request`, `write_files_this_request`
2. Loop-Checks für edit_file und write_file hinzufügen
3. System-Prompt mit "Task Complete" Instruktionen erweitern
4. Testen mit Edit-Szenario

### Phase 2: Kontext-Anzeige
1. `CONTEXT_STATUS` Event hinzufügen
2. Event nach jedem Tool-Call emittieren
3. Frontend: Context-Indicator im Chat-Header
4. Styling für Warning-States

### Phase 3: Summarizer-Aktivierung
1. Summarizer-Check in Haupt-Loop verschieben
2. `COMPACTION` Event korrekt emittieren
3. Frontend: Komprimierungs-Notification
4. Animation für visuelles Feedback

---

## Dateien zu ändern

| Datei | Änderungen |
|-------|------------|
| `app/agent/orchestrator.py` | Loop-Prävention, Context-Events, Summarizer-Aufruf |
| `app/services/llm_client.py` | System-Prompt erweitern |
| `static/app.js` | Event-Handler, Context-Indicator |
| `static/index.html` | Context-Indicator Element |
| `static/style.css` | Styling für Indicator und Animation |

---

## Risiken und Mitigationen

1. **Summarizer-Performance**: LLM-Call für Summary kann langsam sein
   - Mitigation: Nur bei >75% Auslastung, kleines Modell nutzen

2. **False Positives bei Loop-Detection**: Legitime Mehrfach-Edits blockiert
   - Mitigation: Limit auf 2 statt 1, Hinweis statt harter Block

3. **Context-Indicator Overhead**: Zusätzliche Events könnten UI verlangsamen
   - Mitigation: Throttling, nur bei Änderung senden
