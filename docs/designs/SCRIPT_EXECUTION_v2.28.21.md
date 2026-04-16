# Architecture Design: Script Execution Flow mit Confirmation & Progress Visibility

**Version**: 2.28.21
**Date**: 2026-04-01
**Focus**: Two-Phase Confirmation + Real-Time Progress Visibility

---

## 1. Overview

Sichere, transparente Python-Script-Ausführung mit:
- **Phase 1**: Zwei-Schritt-Bestätigung (pip install → Script-Code)
- **Phase 2**: Real-Time Progress Visibility (pip packages, Script execution, stdout/stderr)
- **Phase 3**: Error Context (welche Phase scheiterte?)

```
User: "Execute docx2pdf script"
       ↓
[PHASE 1] Confirmation
  - Show pip commands to install
  - Show script code
  - User clicks "Confirm"
       ↓
[PHASE 2] Execution mit Progress
  - pip install: "Installing docx2pdf..."
  - pip install: "Installing python-pptx..."
  - pip install: "✓ Installation complete"
  - Script: "Executing script..."
  - Script: stdout/stderr in Echtzeit
  - Script: "✓ Complete in 1234ms"
       ↓
[PHASE 3] Result
  - Output anzeigen
  - Oder Error mit Kontext
```

---

## 2. Current Implementation Status

### ✅ ALREADY IMPLEMENTED
| Feature | Location | Status |
|---------|----------|--------|
| **Two-Phase Confirmation** | `app/agent/script_tools.py:126-193` | ✅ Code exists |
| **requires_confirmation flag** | Line 171, 191 | ✅ Returns True |
| **confirmation_data structure** | Line 155-172, 176-186 | ✅ Properly formed |
| **Confirmation Endpoint** | `/api/agent/confirm/{session_id}` | ✅ Exists |
| **sqlite3.Row fix** | `app/services/script_manager.py:330,364` | ✅ v2.28.21 FIXED |

### ❌ NOT IMPLEMENTED
| Feature | Location | Why Missing |
|---------|----------|-------------|
| **Progress Events** | ScriptExecutor | No SSE event callbacks |
| **Confirmation Panel Display** | Frontend `app.js` | Might not show confirmation |
| **pip Progress** | `ScriptExecutor._install_requirements()` | Silent execution |
| **Script stdout/stderr Real-Time** | `ScriptExecutor._run_local()` | Not streamed |

---

## 3. Complete Architecture

### 3.1 System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                      FRONTEND (Chat UI)                          │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐ │
│  │ Message Input        │  │ Confirmation Panel (Hidden)      │ │
│  │ - "execute script X" │  │ - pip commands preview           │ │
│  └──────────┬───────────┘  │ - script code preview            │ │
│             │              │ - Buttons: Confirm / Cancel      │ │
│             │              └──────────────────────────────────┘ │
│             │                                                   │
│  ┌──────────▼──────────┐  ┌──────────────────────────────────┐ │
│  │ SSE Event Listener  │  │ Message Bubble (Progress)        │ │
│  │ - progress events   │  │ - Installing: docx2pdf...        │ │
│  │ - stream stdout     │  │ - Installing: python-pptx...     │ │
│  │ - stream stderr     │  │ - [stdout line 1]                │ │
│  │ - done event        │  │ - [stdout line 2]                │ │
│  └─────────────────────┘  └──────────────────────────────────┘ │
└──────────────────┬─────────────────────────────────────────────┘
                   │
                   │ WebSocket/SSE
                   │
┌──────────────────▼─────────────────────────────────────────────┐
│                    BACKEND (FastAPI)                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ Agent Chat Endpoint (/api/agent/chat)                    │ │
│  │ - Receives: "execute script XYZ"                          │ │
│  │ - Calls: handle_execute_script()                          │ │
│  │ - Returns: ToolResult(requires_confirmation=True)         │ │
│  └────────────┬─────────────────────────────────────────────┘ │
│               │                                                 │
│  ┌────────────▼─────────────────────────────────────────────┐ │
│  │ Confirmation Endpoint (/api/agent/confirm/{session_id}) │ │
│  │ - Receives: confirmation_data + user approval            │ │
│  │ - Operation: "pip_install_confirm" OR "execute_script"   │ │
│  │ - Calls: Orchestrator._execute_confirmed_operation()     │ │
│  └────────────┬─────────────────────────────────────────────┘ │
│               │                                                 │
│  ┌────────────▼─────────────────────────────────────────────┐ │
│  │ Script Manager (ScriptManager)                           │ │
│  │ ┌────────────────────────────────────────────────────┐   │ │
│  │ │ install_requirements(reqs: List[str])              │   │ │
│  │ │ - Calls: ScriptExecutor._install_requirements()    │   │ │
│  │ │ - Sends SSE events for progress                    │   │ │
│  │ │ - Returns: error string OR None                    │   │ │
│  │ └─────────────┬────────────────────────────────────┘   │ │
│  │ ┌─────────────▼────────────────────────────────────┐   │ │
│  │ │ execute(script_id)                               │   │ │
│  │ │ - Calls: ScriptExecutor.run(script)              │   │ │
│  │ │ - Sends SSE events for progress                  │   │ │
│  │ │ - Returns: ExecutionResult with stdout/stderr    │   │ │
│  │ └────────────────────────────────────────────────────┘   │ │
│  └──────────────────────────────────────────────────────────┘ │
│               │                                                 │
│  ┌────────────▼─────────────────────────────────────────────┐ │
│  │ Script Executor (ScriptExecutor)                         │ │
│  │ ┌────────────────────────────────────────────────────┐   │ │
│  │ │ _install_requirements()                            │   │ │
│  │ │ - FOR each package:                                │   │ │
│  │ │   - Send: SSE "Installing: package_name"          │   │ │
│  │ │   - pip install via subprocess                     │   │ │
│  │ │ - Send: SSE "Installation complete"               │   │ │
│  │ └────────────────────────────────────────────────────┘   │ │
│  │ ┌────────────────────────────────────────────────────┐   │ │
│  │ │ _run_local()                                       │   │ │
│  │ │ - Send: SSE "Executing script..."                 │   │ │
│  │ │ - FOR each stdout line:                            │   │ │
│  │ │   - Send: SSE "script_stdout" + line               │   │ │
│  │ │ - FOR each stderr line:                            │   │ │
│  │ │   - Send: SSE "script_stderr" + line               │   │ │
│  │ │ - Send: SSE "Script complete" + stats              │   │ │
│  │ └────────────────────────────────────────────────────┘   │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Flow Diagrams

### 4.1 Two-Phase Confirmation Flow

```
┌────────────────────────────────────────────────────────────────────┐
│ PHASE 1: CONFIRMATION                                              │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  Frontend                        Backend                           │
│  ┌──────────┐                   ┌──────────────────────────┐      │
│  │ User     │                   │ Agent receives message   │      │
│  │ "execute │                   │ "execute script xyz"     │      │
│  │  script  │────SSE stream────▶│                          │      │
│  │  xyz"    │                   │ Calls:                   │      │
│  └──────────┘                   │ handle_execute_script()  │      │
│       △                         │ (script has requirements)│      │
│       │                         │                          │      │
│       │ SSE event:             │ Returns:                 │      │
│       │ "confirm_required"      │ requires_confirmation    │      │
│       │ + confirmation_data     │ = True                   │      │
│       │ (code + pip commands)   │ confirmation_data = {    │      │
│       │                         │   operation: "pip_...",  │      │
│       │                         │   requirements: [...],   │      │
│       │                         │   code: "..."            │      │
│       └─────────────────────────│ }                        │      │
│                                 └──────────────────────────┘      │
│                                                                    │
│  ┌──────────────────────┐                                         │
│  │ Confirmation Panel   │                                         │
│  │ shows:               │                                         │
│  │ - pip commands       │                                         │
│  │ - code preview       │                                         │
│  │ [Confirm] [Cancel]   │                                         │
│  └─────────┬────────────┘                                         │
│            │                                                      │
│            │ User clicks "Confirm"                                │
│            └──────────▶ POST /api/agent/confirm/{sid}             │
│                        + confirmation_data                        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ PHASE 2: PIP INSTALL                                               │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  /confirm Endpoint receives confirmation_data                     │
│    │                                                              │
│    ├─▶ operation == "pip_install_confirm"                        │
│    │    └─▶ manager.install_requirements(["docx2pdf", ...])      │
│    │        │                                                    │
│    │        ├─▶ SSE Event: "pip_start"                           │
│    │        │   {packages: ["docx2pdf", "python-pptx"]}         │
│    │        │                                                    │
│    │        ├─▶ FOR docx2pdf:                                    │
│    │        │   ├─ SSE: "pip_installing"                        │
│    │        │   │  {package: "docx2pdf"}                        │
│    │        │   ├─ subprocess: pip install docx2pdf             │
│    │        │   └─ SSE: "pip_installed"                         │
│    │        │      {package: "docx2pdf", success: true}         │
│    │        │                                                   │
│    │        └─▶ SSE Event: "pip_complete"                       │
│    │            {status: "success", total_ms: 5432}             │
│    │                                                             │
│    └─▶ Return ToolResult(requires_confirmation=True)            │
│        WITH new confirmation_data for Phase 3:                   │
│        operation: "execute_script"                               │
│                                                                  │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ PHASE 3: SCRIPT EXECUTION                                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  Frontend displays new Confirmation Panel                         │
│  (now showing script code, file access warnings)                  │
│                                                                   │
│  User clicks "Confirm"                                            │
│  POST /api/agent/confirm/{sid} again with:                        │
│    operation: "execute_script"                                    │
│                                                                   │
│  /confirm receives second confirmation:                           │
│    │                                                              │
│    └─▶ manager.execute(script_id)                                │
│        │                                                          │
│        └─▶ ScriptExecutor.run(script)                            │
│            │                                                      │
│            ├─ SSE: "script_start"                                │
│            │  {script_id, name: "docx_converter"}                │
│            │                                                      │
│            ├─ subprocess.Popen(python script.py)                 │
│            │                                                      │
│            ├─ WHILE stdout/stderr available:                     │
│            │  ├─ SSE: "script_stdout"                            │
│            │  │  {line: "Processing file..."}                   │
│            │  └─ SSE: "script_stderr"                            │
│            │     {line: "Warning: ..."}                         │
│            │                                                      │
│            └─ SSE: "script_complete"                             │
│               {status: "success", exit_code: 0, ms: 1234}        │
│                                                                   │
│  Frontend:                                                        │
│    - Displays progress in message bubble                          │
│    - Streams stdout/stderr in real-time                          │
│    - Final result display                                         │
│                                                                   │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5. SSE Event Specification

### Event Types

| Event | Sender | Data | Frontend Action |
|-------|--------|------|-----------------|
| **confirm_required** | handle_execute_script() | confirmation_data | Show Confirmation Panel |
| **pip_start** | install_requirements() | {packages: [...]} | Display: "Installing packages..." |
| **pip_installing** | _install_requirements() | {package: "name"} | Update: "Installing: name..." |
| **pip_installed** | _install_requirements() | {package, success, error?} | Update: "✓ Installed" or "✗ Failed" |
| **pip_complete** | install_requirements() | {total_ms, status} | Display: "Installation complete" |
| **script_start** | ScriptExecutor.run() | {script_id, name} | Display: "Executing script..." |
| **script_stdout** | _run_local() | {line: "..."} | Append to output bubble |
| **script_stderr** | _run_local() | {line: "..."} | Append with ⚠️ prefix |
| **script_complete** | ScriptExecutor.run() | {exit_code, ms, status} | Display: "✓ Complete in Xms" |
| **error** | Any | {phase, message, context} | Display error panel |

---

## 6. Implementation Roadmap

### Must Implement

| Priority | Task | Location | Complexity |
|----------|------|----------|-----------|
| 🔴 **1** | Add SSE progress callback to ScriptExecutor | `app/services/script_manager.py:464-535` | Medium |
| 🔴 **2** | Send "pip_installing" events per package | `app/services/script_manager.py:536-611` | Small |
| 🔴 **3** | Stream stdout/stderr in real-time | `app/services/script_manager.py:615-700` | Medium |
| 🔴 **4** | Verify confirmation_data flows to frontend | `app/api/routes/agent.py:233-300` | Small |
| 🟡 **5** | Update frontend to show progress events | `static/app.js` | Medium |

### Should Implement

| Priority | Task | Location | Complexity |
|----------|------|----------|-----------|
| 🟡 **6** | Error context (which phase failed?) | `app/services/script_manager.py` | Small |
| 🟡 **7** | Timeout handling with progress | `ScriptExecutor` | Medium |

---

## 7. Frontend Integration Points

### app.js Changes Needed

```javascript
// 1. Listen for confirmation events (already done, verify!)
if (data.status === 'confirm_required') {
  showConfirmationPanel(data);
}

// 2. Listen for progress events
if (data.type === 'pip_installing') {
  appendMessage('system', `📦 Installing: ${data.package}...`);
}

if (data.type === 'pip_installed') {
  appendMessage('system', `✓ Installed: ${data.package}`);
}

if (data.type === 'script_stdout') {
  appendMessage('system', data.line);
}

if (data.type === 'script_stderr') {
  appendMessage('system', `⚠️ ${data.line}`);
}

if (data.type === 'script_complete') {
  appendMessage('system', `✓ Complete in ${data.ms}ms`);
}
```

---

## 8. Next Steps

1. **Verify**: Check if confirmation_data actually reaches frontend
   ```bash
   → Inspect Network Tab when executing script
   → Check if SSE event "confirm_required" arrives
   ```

2. **Implement**: Add progress event callbacks to ScriptExecutor
   ```python
   # Pass callback to ScriptExecutor
   executor.run(script, callbacks={
     'on_pip_start': lambda pkgs: send_sse_event(...),
     'on_pip_installing': lambda pkg: send_sse_event(...),
     ...
   })
   ```

3. **Frontend**: Update app.js to display progress events
   ```javascript
   // Add handlers for all new SSE event types
   ```

4. **Test**: Execute docx2pdf script and verify:
   - [ ] Confirmation panel shows code + pip commands
   - [ ] User sees "Installing: docx2pdf..."
   - [ ] User sees real-time stdout/stderr
   - [ ] Final result displayed

---

## 9. Success Criteria

✅ **Phase 1 (Confirmation)**
- [ ] User sees code before execution
- [ ] User sees pip commands before execution
- [ ] Confirmation can be accepted/rejected

✅ **Phase 2 (Progress)**
- [ ] User sees "Installing: [package]" for each pip package
- [ ] User sees script execution starting
- [ ] User sees stdout/stderr in real-time
- [ ] User sees completion with execution time

✅ **Phase 3 (Reliability)**
- [ ] No sqlite3.Row errors (✓ v2.28.21)
- [ ] Clear error messages with context
- [ ] Timeout handling with user feedback

---

**Status**: Ready for implementation → `/sc:implement`

