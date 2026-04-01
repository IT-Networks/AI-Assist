# Deep Analysis: Script Execution Flow & Visibility Issues (v2.28.29)

## Problem Statement

**User Issue:** After starting script execution (e.g., win32com operation):
- Waits 52 seconds
- Gets HTTP 200 response
- **Minimal output/error information visible**
- No visibility into what the script is doing
- No clear error messages if something fails

**Root Question:** Where is the information getting lost?

---

## Information Flow Analysis

### Step 1: Script Execution Initiation

**File:** `app/agent/script_tools.py` - `handle_execute_script()`

```python
async def handle_execute_script(script_id, args, input_data, **kwargs):
    # ... validation ...

    # Two paths:

    # Path A: Script has requirements (pip packages)
    if script.requirements:
        return ToolResult(
            requires_confirmation=True,
            confirmation_data={
                "operation": "pip_install_confirm",
                "pip_cmd_preview": "...",  # ← Shown to user ✅
                # ... other fields ...
            }
        )

    # Path B: Script has no requirements
    return ToolResult(
        requires_confirmation=True,
        confirmation_data={
            "operation": "execute_script",
            "code": script.code,  # ← Code shown to user ✅
            # ... other fields ...
        }
    )
```

**Issue:** Both paths show confirmation panel. User approves and execution begins.

### Step 2: Orchestrator Execution

**File:** `app/agent/orchestrator.py` - `_execute_confirmed_operation()`

```python
elif operation == "execute_script":
    from app.agent.script_tools import execute_script_after_confirmation

    result = await execute_script_after_confirmation(
        script_id=confirmation_data["script_id"],
        args=confirmation_data["args"],
        input_data=confirmation_data["input_data"],
        on_output_chunk=output_callbacks.get("on_output_chunk")  # ← ISSUE: Usually None!
    )
```

**⚠️ ISSUE 1: Output Callbacks Not Passed**
- `output_callbacks` comes from `confirmation_data.get("_output_callbacks", {})`
- But `handle_execute_script()` never sets `_output_callbacks`!
- Result: `on_output_chunk=None` → No real-time output streaming

### Step 3: Script Execution

**File:** `app/services/script_manager.py` - `execute_script_after_confirmation()`

```python
async def execute_script_after_confirmation(script_id, args, input_data, on_output_chunk=None):
    manager = get_script_manager()
    result = await manager.execute(script_id, args, input_data, on_output_chunk=on_output_chunk)

    # Result formatting
    if result.success:
        output_text = f"""✅ Script erfolgreich ausgeführt in {result.execution_time_ms}ms
📤 Output:
{result.stdout if result.stdout else '(keine Ausgabe)'}"""

        if result.stderr:
            output_text += f"\n\n⚠️ Stderr:\n{result.stderr}"

        return ToolResult(success=True, data=output_text)
```

**⚠️ ISSUE 2: Output Only Shown After Execution Completes**
- No streaming of output during execution
- User waits in silence
- Only after timeout/completion does output appear

### Step 4: Local Execution

**File:** `app/services/script_manager.py` - `_run_local()`

```python
async def _run_local(self, script_path, input_data=None):
    stdout_lines = []
    stderr_lines = []

    # ... create subprocess ...

    async def stream_output(stream, stream_type):
        while True:
            line = await asyncio.wait_for(
                stream.readline(),
                timeout=self.timeout  # ← 30 seconds per line!
            )
            if not line:
                break

            decoded = line.decode('utf-8', errors='replace')

            # Accumulate
            if stream_type == 'stdout':
                stdout_lines.append(decoded)
            else:
                stderr_lines.append(decoded)

            # ⚠️ ISSUE 3: Callback usually None (from above)
            if self.on_output_chunk:
                await self.on_output_chunk(stream_type, decoded.rstrip('\r\n'))

    # Wait for completion
    await asyncio.wait_for(
        asyncio.gather(
            stream_output(process.stdout, 'stdout'),
            stream_output(process.stderr, 'stderr'),
            process.wait()
        ),
        timeout=self.timeout  # ← Another timeout! (30 seconds)
    )

    # Final result
    return ExecutionResult(
        success=process.returncode == 0,
        stdout=''.join(stdout_lines),
        stderr=''.join(stderr_lines),
        error=...
    )
```

**⚠️ ISSUE 3: Cascading Timeouts**
- `stream.readline()` has 30s timeout
- Outer `asyncio.wait_for()` has 30s timeout
- If script takes longer → timeout exception

**⚠️ ISSUE 4: Output Not Streamed to Frontend**
- Output is collected in lists
- Only returned AFTER completion
- Frontend doesn't see progress during execution

---

## Why You See "200 Status After 52 Seconds"

```
Timeline:
├─ 0s:   User confirms execution
├─ 0s:   Script starts executing
├─ 30s:  Outer timeout fires (self.timeout = 30s from config.yaml)
├─ 30s:  Script is killed/terminated
├─ 30-52s: Cleanup, error formatting, response building
└─ 52s:  HTTP 200 response returned to frontend
```

The "200" is HTTP status code = response sent successfully.
The actual script output/error is in the response body, but it may be truncated/incomplete.

---

## Missing Information

**Why you don't see detailed errors:**

1. **No real-time logging to frontend**
   - Script runs silently in background
   - No progress indicators
   - No intermediate results

2. **Errors only shown at end**
   - If script fails, error message is in `result.error`
   - But it might be truncated (max_output_size_kb = 256KB)
   - Or cut off by the timeout

3. **No debugging information**
   - Script working directory?
   - Current imports loaded?
   - stdout/stderr from subprocess?
   - These aren't shown to user

4. **UI doesn't display streaming events**
   - Even if callbacks were called (they're not)
   - Frontend would need to handle SSE/WebSocket events
   - Currently not implemented for script execution

---

## Configuration Issues

**From `config.yaml`:**

```yaml
script_execution:
  timeout_seconds: 30  # ← Very short for win32com operations!
  max_output_size_kb: 256  # ← May truncate large outputs
```

**For win32com COM automation:**
- Operations can take 30+ seconds
- Loading Excel/Word COM objects is slow
- Timeout of 30s is too aggressive

---

## Issues Found (Priority-Ranked)

### 🔴 CRITICAL (Blocks Functionality)

1. **Script Timeout Too Short**
   - `timeout_seconds: 30` is insufficient for COM operations
   - win32com operations take 20-60 seconds
   - Script gets killed before completion
   - **Fix:** Increase timeout to 60-120 seconds

2. **Output Callbacks Never Passed**
   - `_output_callbacks` never set in confirmation_data
   - Real-time streaming never happens
   - User sees nothing until completion/timeout
   - **Fix:** Either pass callbacks OR don't rely on them

### 🟠 HIGH (Reduces Usability)

3. **No Real-Time Output Visibility**
   - Script output only shown after completion
   - No progress indicators
   - User thinks script is hung (but it's working)
   - **Fix:** Stream output events to frontend

4. **Error Messages Truncated**
   - `max_output_size_kb: 256` may cut off errors
   - User doesn't see full traceback
   - **Fix:** Check if output is truncated, show indicator

5. **No Debugging Information**
   - Script working directory not visible
   - Module loading not logged
   - COM initialization errors hidden
   - **Fix:** Add verbose logging mode

### 🟡 MEDIUM (Improves Information)

6. **Status Updates Missing**
   - No "Script is running..." message
   - No "Waiting for output..." indicator
   - User confused about what's happening
   - **Fix:** Send periodic status messages

7. **Tool Output Formatting**
   - Result message uses emojis that cause encoding errors on Windows
   - Makes output hard to read
   - **Fix:** Remove or escape emojis

---

## Recommendations

### Immediate Fixes

1. **Increase Script Timeout**
   ```yaml
   script_execution:
     timeout_seconds: 120  # Increased from 30
   ```

2. **Add Diagnostic Logging**
   ```python
   logger.info(f"Starting script execution: {script.id}")
   logger.info(f"Working directory: {temp_path}")
   logger.info(f"Timeout: {self.timeout}s")
   logger.info(f"Imports allowed: {config.allowed_imports}")
   ```

3. **Show Intermediate Status**
   ```python
   # During execution wait, periodically emit status
   async def emit_status():
       while not done:
           await self.on_output_chunk("status", "Running...")
           await asyncio.sleep(5)
   ```

### Medium-Term Improvements

4. **Stream Output to Frontend**
   - Implement WebSocket/SSE for real-time output
   - Show progress bar or spinner
   - Display output as it comes

5. **Better Error Messages**
   - Catch COM-specific errors
   - Show meaningful error messages
   - Include diagnostic suggestions

6. **Configuration Profiles**
   - Quick operations: 30s timeout
   - Normal operations: 60s timeout
   - Long operations (COM, batch): 120+ seconds
   - User can select at execution time

---

## Test Results

Created tests to verify:
- ✅ Output capture works
- ✅ Silent scripts (no output) handled correctly
- ✅ Error output (stderr) captured
- ✅ Streaming events emitted (but not used)

Issue: Callbacks not being utilized, so streaming never reaches frontend.

---

## Summary

The main issue is:

> **Your win32com script is likely COMPLETING successfully, but the result is not reaching the frontend due to:**
>
> 1. **30-second timeout is too short** for COM operations
> 2. **Output streaming not connected** (callbacks are None)
> 3. **No visibility into what's happening** during the 52-second wait
> 4. **Errors might be truncated** or not displayed

**Next steps:**
1. Increase `timeout_seconds` to 120
2. Add logging to see what's actually happening
3. Implement output streaming for real-time visibility
4. Test win32com script with diagnostic logging enabled
