# Bug Fix: Missing Output Callbacks in Script Execution (v2.28.28+)

## Problem

**Symptom:** All scripts show "(no output)" even when they produce output

**Root Cause:** Output callbacks are not passed through the confirmation flow

```
execute_python_script()
  ↓
handle_execute_script() creates confirmation_data
  ↓ ❌ _output_callbacks NOT included
confirmation_data sent to user for approval
  ↓
User confirms
  ↓
_execute_confirmed_operation() receives confirmation_data
  ↓ ❌ _output_callbacks is empty/missing
execute_script_after_confirmation() called with on_output_chunk=None
  ↓ ❌ No output events emitted
manager.execute() runs with on_output_chunk=None
  ↓ ❌ Output is collected in ExecutionResult
Result shown to user: "(no output)"
```

## Code Analysis

### File: app/agent/script_tools.py, handle_execute_script()

**Current Code (BROKEN):**
```python
confirmation_data = {
    "operation": "execute_script",
    "script_id": script_id,
    "script_name": script.name,
    "script_description": script.description,
    "code": script.code,
    "args": args or {},
    "input_data": input_data,
    "file_path": script.file_path,
    "allowed_file_paths": manager.config.allowed_file_paths,
    # ❌ MISSING: "_output_callbacks"
}
```

### File: app/agent/orchestrator.py, _execute_confirmed_operation()

**Code that expects the callbacks:**
```python
output_callbacks = confirmation_data.get("_output_callbacks", {})  # ← Returns {}
result = await execute_script_after_confirmation(
    script_id, args, input_data,
    on_output_chunk=output_callbacks.get("on_output_chunk")  # ← None!
)
```

## Impact

- **Scripts with output:** Show "(no output)" instead of actual output
- **Scripts without output:** Show "(no output)" correctly (expected)
- **User confusion:** Can't see what the script did
- **Silent operations:** Harder to debug (docx2pdf, file operations)

## Solution

The `_output_callbacks` dict must be included in `confirmation_data`.

However, there's a challenge: **Callbacks cannot be serialized.**

The callbacks are Python functions, which can't be:
- Passed through JSON APIs
- Stored in confirmation_data for the user to see
- Serialized for persistence

### Options

**Option 1: Store callback in Agent state (not confirmation_data)**
- Agent keeps a registry of output callbacks
- confirmation_data includes a reference ID
- Orchestrator looks up the callback from agent registry
- **Pro:** Clean, doesn't expose callbacks to user
- **Con:** More complex state management

**Option 2: Don't pass callbacks through confirmation flow**
- Scripts aren't truly interactive anyway (they run after confirmation)
- Output is captured in ExecutionResult
- ExecutionResult.stdout already contains the full output
- Just display ExecutionResult.stdout instead of relying on callbacks
- **Pro:** Simpler, output is already captured
- **Con:** Output not streamed to user in real-time

**Option 3: Use event-based streaming**
- Callbacks emit events to Agent event bus
- Agent aggregates events and displays them
- **Pro:** Matches current architecture (AgentEvent streaming)
- **Con:** More complex implementation

## Recommendation

**Use Option 2 (Recommended):**

The output is ALREADY captured in `ExecutionResult.stdout`. We don't need to pass callbacks through confirmation.

**Why this works:**
1. `manager.execute()` collects stdout/stderr in ExecutionResult
2. ExecutionResult is returned to execute_script_after_confirmation()
3. execute_script_after_confirmation() displays ExecutionResult.stdout
4. User sees the output ✅

The issue is just that ExecutionResult might be displayed incorrectly if it doesn't reach the user.

## Implementation

Check if:
1. ExecutionResult is correctly created with stdout
2. execute_script_after_confirmation() correctly formats the result
3. ToolResult is correctly returned with the output text

## Tests

Created:
- `test_output_capture.py` - Scripts with output
- `test_silent_script.py` - Scripts without output
- `test_execute_flow.py` - Full execution flow (pending)
