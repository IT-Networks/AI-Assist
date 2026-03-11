# MCP Progress Updates - Design Document

## Übersicht

System zur Echtzeit-Visualisierung von MCP-Verarbeitungsschritten im Chat.

### Ziele
- **Transparenz**: Benutzer sieht was MCP gerade macht
- **Feedback**: Regelmäßige Updates während langer Operationen
- **Debugging**: Nachvollziehbarkeit bei Problemen
- **UX**: Besseres Verständnis der KI-Denkprozesse

---

## Aktuelle SSE Event-Typen

```python
class AgentEventType(str, Enum):
    TOKEN = "token"                    # Streaming-Token
    TOOL_START = "tool_start"          # Tool wird ausgeführt
    TOOL_RESULT = "tool_result"        # Tool-Ergebnis
    CONFIRM_REQUIRED = "confirm_required"
    ERROR = "error"
    USAGE = "usage"
    COMPACTION = "compaction"
    DONE = "done"
    # Sub-Agent Events
    SUBAGENT_START = "subagent_start"
    SUBAGENT_ROUTING = "subagent_routing"
    SUBAGENT_DONE = "subagent_done"
    SUBAGENT_ERROR = "subagent_error"
    # Planning Events
    PLAN_READY = "plan_ready"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"
    QUESTION = "question"
```

---

## Neue Event-Typen für MCP

### Erweiterung von AgentEventType

```python
class AgentEventType(str, Enum):
    # ... bestehende Events ...

    # MCP Progress Events (NEU)
    MCP_START = "mcp_start"            # MCP-Tool startet
    MCP_STEP = "mcp_step"              # Einzelner Denkschritt
    MCP_PROGRESS = "mcp_progress"      # Fortschritts-Update (%)
    MCP_COMPLETE = "mcp_complete"      # MCP-Tool fertig
    MCP_ERROR = "mcp_error"            # MCP-Fehler
```

### Event-Datenstrukturen

```python
@dataclass
class MCPStartEvent:
    """Event wenn MCP-Tool startet."""
    tool_name: str               # z.B. "sequential_thinking", "brainstorm"
    session_id: str              # Thinking-Session ID
    query: str                   # Die Aufgabenstellung
    estimated_steps: int         # Geschätzte Anzahl Schritte (optional)

@dataclass
class MCPStepEvent:
    """Event für jeden einzelnen Denkschritt."""
    tool_name: str
    session_id: str
    step_number: int
    step_type: str               # z.B. "analysis", "hypothesis", "verification"
    title: str                   # Kurze Beschreibung des Schritts
    content: str                 # Detaillierter Inhalt (optional gekürzt)
    confidence: float            # 0.0 - 1.0
    is_final: bool               # Letzter Schritt?

@dataclass
class MCPProgressEvent:
    """Fortschritts-Update für lange Operationen."""
    tool_name: str
    session_id: str
    progress_percent: int        # 0-100
    current_phase: str           # z.B. "Analyse", "Planung", "Validierung"
    message: str                 # Status-Nachricht

@dataclass
class MCPCompleteEvent:
    """Event wenn MCP-Tool fertig ist."""
    tool_name: str
    session_id: str
    total_steps: int
    final_conclusion: str        # Zusammenfassung
    duration_ms: int
```

---

## Architektur

```
┌────────────────────────────────────────────────────────────────┐
│                        Frontend (Chat)                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │           MCP Progress Display Component                  │  │
│  │  ┌─────────────────────────────────────────────────────┐ │  │
│  │  │ 🧠 Sequential Thinking                              │ │  │
│  │  │ ──────────────────────────────────────────          │ │  │
│  │  │ ✓ Schritt 1: Analyse - Problem identifiziert       │ │  │
│  │  │ ✓ Schritt 2: Hypothese - Mögliche Ursachen         │ │  │
│  │  │ ⟳ Schritt 3: Verifikation - Prüfung läuft...       │ │  │
│  │  │                                        [3/5 Steps]  │ │  │
│  │  └─────────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
                              │ SSE Stream
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                    /api/agent/chat (SSE)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │    event: mcp_start                                       │  │
│  │    data: {"tool": "sequential_thinking", "session": "x"}  │  │
│  │                                                           │  │
│  │    event: mcp_step                                        │  │
│  │    data: {"step": 1, "type": "analysis", "title": "..."}  │  │
│  │                                                           │  │
│  │    event: mcp_step                                        │  │
│  │    data: {"step": 2, "type": "hypothesis", "title": "..."}│  │
│  │                                                           │  │
│  │    event: mcp_complete                                    │  │
│  │    data: {"total_steps": 5, "conclusion": "..."}          │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
                              ▲
                              │ Events
┌────────────────────────────────────────────────────────────────┐
│                       Orchestrator                              │
│  ┌────────────────────────┐    ┌────────────────────────────┐  │
│  │   MCPToolBridge        │───>│   SequentialThinking       │  │
│  │   (event_callback)     │    │   (emits ThinkingSteps)    │  │
│  └────────────────────────┘    └────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

---

## Implementierung

### 1. Event-Callback in SequentialThinking

```python
# app/mcp/sequential_thinking.py

class SequentialThinking:
    def __init__(
        self,
        llm_callback: Optional[Callable] = None,
        event_callback: Optional[Callable] = None  # NEU
    ):
        self.llm_callback = llm_callback
        self.event_callback = event_callback  # Callback für Progress-Events

    async def _emit_event(self, event_type: str, data: Dict) -> None:
        """Sendet ein Progress-Event."""
        if self.event_callback:
            await self.event_callback(event_type, data)

    async def think(
        self,
        query: str,
        context: str = "",
        max_steps: int = 10,
        thinking_type: ThinkingType = ThinkingType.ANALYSIS
    ) -> ThinkingSession:
        """Führt strukturiertes Denken durch mit Progress-Updates."""

        session = ThinkingSession(
            session_id=str(uuid.uuid4()),
            query=query
        )

        # Start-Event
        await self._emit_event("mcp_start", {
            "tool_name": "sequential_thinking",
            "session_id": session.session_id,
            "query": query[:200],
            "estimated_steps": max_steps
        })

        try:
            for step_num in range(1, max_steps + 1):
                # ... Denkschritt ausführen ...
                step = await self._execute_step(session, step_num, ...)

                # Step-Event
                await self._emit_event("mcp_step", {
                    "tool_name": "sequential_thinking",
                    "session_id": session.session_id,
                    "step_number": step.step_number,
                    "step_type": step.type.value,
                    "title": step.title,
                    "content": step.content[:300],  # Gekürzt
                    "confidence": step.confidence,
                    "is_final": step.type == ThinkingType.CONCLUSION
                })

                if step.type == ThinkingType.CONCLUSION:
                    break

            # Complete-Event
            await self._emit_event("mcp_complete", {
                "tool_name": "sequential_thinking",
                "session_id": session.session_id,
                "total_steps": len(session.steps),
                "final_conclusion": session.final_conclusion or "",
                "duration_ms": self._calculate_duration(session)
            })

        except Exception as e:
            await self._emit_event("mcp_error", {
                "tool_name": "sequential_thinking",
                "session_id": session.session_id,
                "error": str(e)
            })
            raise

        return session
```

### 2. Integration in MCPToolBridge

```python
# app/mcp/tool_bridge.py

class MCPToolBridge:
    def __init__(
        self,
        llm_callback: Optional[Callable] = None,
        event_callback: Optional[Callable] = None  # NEU
    ):
        self.llm_callback = llm_callback
        self.event_callback = event_callback

        # Sequential Thinking mit Event-Callback
        self.sequential_thinking = get_sequential_thinking(
            llm_callback,
            event_callback=event_callback  # Weitergeben
        )
```

### 3. Integration im Orchestrator

```python
# app/agent/orchestrator.py

class Orchestrator:
    async def _setup_mcp_bridge(self) -> None:
        """Initialisiert MCP Bridge mit Event-Callback."""
        self.tool_bridge = MCPToolBridge(
            llm_callback=self._llm_callback,
            event_callback=self._handle_mcp_event  # NEU
        )

    async def _handle_mcp_event(self, event_type: str, data: Dict) -> None:
        """Verarbeitet MCP-Events und leitet sie weiter."""
        # Mapping zu AgentEventType
        event_mapping = {
            "mcp_start": AgentEventType.MCP_START,
            "mcp_step": AgentEventType.MCP_STEP,
            "mcp_progress": AgentEventType.MCP_PROGRESS,
            "mcp_complete": AgentEventType.MCP_COMPLETE,
            "mcp_error": AgentEventType.MCP_ERROR,
        }

        agent_event_type = event_mapping.get(event_type)
        if agent_event_type:
            yield AgentEvent(type=agent_event_type, data=data)
```

### 4. SSE Stream in Agent-Route

```python
# app/api/routes/agent.py

async def chat_stream(request: ChatRequest):
    async for event in orchestrator.run(user_message, session_id):
        event_data = {
            "type": event.type.value,
            "session_id": session_id,
            "data": event.data
        }

        # MCP-Events speziell formatieren für bessere Frontend-Darstellung
        if event.type in [
            AgentEventType.MCP_START,
            AgentEventType.MCP_STEP,
            AgentEventType.MCP_PROGRESS,
            AgentEventType.MCP_COMPLETE,
        ]:
            event_data["category"] = "mcp"
            event_data["displayable"] = True  # Im Chat anzeigen

        yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
```

---

## Frontend-Integration

### Event-Handler im Chat

```typescript
// Beispiel: Frontend Event-Handler

interface MCPStepEvent {
  tool_name: string;
  session_id: string;
  step_number: number;
  step_type: string;
  title: string;
  content: string;
  confidence: number;
  is_final: boolean;
}

class MCPProgressHandler {
  private activeSession: string | null = null;
  private steps: MCPStepEvent[] = [];

  handleEvent(event: SSEEvent): void {
    switch (event.type) {
      case 'mcp_start':
        this.activeSession = event.data.session_id;
        this.steps = [];
        this.showProgressIndicator(event.data.tool_name);
        break;

      case 'mcp_step':
        this.steps.push(event.data);
        this.updateProgressDisplay(event.data);
        break;

      case 'mcp_complete':
        this.hideProgressIndicator();
        this.showSummary(event.data);
        this.activeSession = null;
        break;
    }
  }

  private updateProgressDisplay(step: MCPStepEvent): void {
    // Zeigt den aktuellen Schritt im Chat an
    const stepIcon = this.getStepIcon(step.step_type);
    const display = `
      <div class="mcp-step">
        ${stepIcon} <strong>${step.title}</strong>
        <div class="step-content">${step.content}</div>
        <span class="confidence">${Math.round(step.confidence * 100)}%</span>
      </div>
    `;
    this.appendToChat(display);
  }

  private getStepIcon(type: string): string {
    const icons: Record<string, string> = {
      'analysis': '🔍',
      'hypothesis': '💡',
      'verification': '✓',
      'planning': '📋',
      'decision': '⚖️',
      'revision': '🔄',
      'conclusion': '✅'
    };
    return icons[type] || '•';
  }
}
```

### Chat-Anzeige (Beispiel)

```
┌─────────────────────────────────────────────────────────────┐
│ User: Analysiere warum der Jenkins-Build fehlschlägt       │
├─────────────────────────────────────────────────────────────┤
│ 🧠 Sequential Thinking aktiv...                             │
│                                                             │
│ 🔍 Analyse: Build-Log untersuchen                          │
│    → Fehler in Test-Phase identifiziert                    │
│    Konfidenz: 85%                                          │
│                                                             │
│ 💡 Hypothese: Test-Dependency fehlt                        │
│    → Mock-Library nicht im Classpath                       │
│    Konfidenz: 72%                                          │
│                                                             │
│ ✓ Verifikation: pom.xml prüfen                             │
│    → mockito-core fehlt in dependencies                    │
│    Konfidenz: 95%                                          │
│                                                             │
│ ✅ Fazit: mockito-core zur pom.xml hinzufügen              │
│    [Abgeschlossen in 3.2s - 4 Schritte]                    │
├─────────────────────────────────────────────────────────────┤
│ Assistent: Der Build schlägt fehl weil die Test-Dependency │
│ mockito-core fehlt. Füge folgendes zur pom.xml hinzu: ...  │
└─────────────────────────────────────────────────────────────┘
```

---

## Konfiguration

```yaml
# config.yaml
mcp:
  progress_updates:
    enabled: true
    show_steps: true           # Einzelne Schritte anzeigen
    show_confidence: true      # Konfidenz-Werte anzeigen
    max_content_length: 300    # Max. Zeichen pro Schritt
    collapse_after: 3          # Nach X Schritten einklappen
```

---

## Betroffene Dateien

### Zu ändern:
- `app/agent/orchestrator.py` - Neue Event-Typen + MCP-Event-Handler
- `app/mcp/sequential_thinking.py` - Event-Callback Integration
- `app/mcp/tool_bridge.py` - Event-Callback weitergeben
- `app/api/routes/agent.py` - MCP-Events im SSE-Stream
- `app/core/config.py` - Progress-Update Konfiguration

### Neu zu erstellen:
- `app/mcp/events.py` - Event-Datenklassen (optional, kann auch in orchestrator.py)

---

## Implementierungsplan

### Phase 1: Backend Events (1-2h)
1. Event-Typen in `orchestrator.py` hinzufügen
2. Event-Callback in `SequentialThinking` implementieren
3. `MCPToolBridge` erweitern

### Phase 2: SSE Integration (30min)
1. Events im Agent-Router verarbeiten
2. SSE-Format für MCP-Events definieren

### Phase 3: Frontend (Optional, 1-2h)
1. Event-Handler implementieren
2. Progress-Display Component erstellen
3. Styling für Denkschritte

---

## Beispiel SSE-Output

```
event: mcp_start
data: {"type":"mcp_start","session_id":"sess_1","data":{"tool_name":"sequential_thinking","query":"Analysiere Build-Fehler","estimated_steps":5}}

event: mcp_step
data: {"type":"mcp_step","session_id":"sess_1","data":{"step_number":1,"step_type":"analysis","title":"Build-Log analysieren","content":"Untersuche Jenkins-Konsole...","confidence":0.8}}

event: mcp_step
data: {"type":"mcp_step","session_id":"sess_1","data":{"step_number":2,"step_type":"hypothesis","title":"Mögliche Ursache","content":"Test-Framework-Problem...","confidence":0.7}}

event: mcp_complete
data: {"type":"mcp_complete","session_id":"sess_1","data":{"total_steps":4,"final_conclusion":"Dependency hinzufügen","duration_ms":3200}}
```

---

## Implementierungsstatus

**Status: Teilweise implementiert**

### Erledigte Schritte:
1. [x] Event-Typen zu `AgentEventType` hinzufügen (MCP_START, MCP_STEP, MCP_PROGRESS, MCP_COMPLETE, MCP_ERROR)
2. [x] Event-Callback in `SequentialThinking` implementieren (`_emit_event`, async Methoden)
3. [x] `get_sequential_thinking()` mit event_callback Parameter erweitern

### Nächste Schritte:
4. [ ] `MCPToolBridge` mit Event-Callback erweitern
5. [ ] SSE-Stream für MCP-Events anpassen
6. [ ] Frontend Progress-Display (optional)

---

## Zusätzliche Verbesserung: Workflow-Tracker für Implement

**Problem:** MCP Implement versuchte wiederholt dieselben Verzeichnisse anzulegen.

**Lösung:** `FileWorkflowTracker` Klasse in `implement.py`:
- Trackt erstellte Verzeichnisse und Dateien
- Verhindert redundante Operationen
- Gruppiert Dateien für Batch-Verarbeitung
- Sortiert Verzeichnisse nach Tiefe (Parents zuerst)
