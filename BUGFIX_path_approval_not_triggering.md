# BUG ANALYSIS: Path Approval Confirmation Not Triggering (v2.28.31)

## Problem Statement

User reports:
- **Actual Behavior**: Script runs without confirmation, paths are accessed without user approval
- **Expected Behavior**: When script requests non-whitelisted path, show confirmation panel
- **Additional Issue**: Missing imports also don't trigger confirmation

## Root Cause Analysis

### Issue 1: Empty Whitelist Disables ALL Validation

**File**: `app/services/script_manager.py:736-782`

```python
def _create_wrapper(self, code: str, args: Dict[str, Any], allowed_paths: List[str] = None) -> str:
    """Erstellt Wrapper-Code mit injizierten Argumenten und Sicherheits-Guard."""
    args_json = json.dumps(args, ensure_ascii=False)
    paths_json = json.dumps(allowed_paths or [], ensure_ascii=False)

    # PROBLEM: if allowed_paths: is False when allowed_paths=[]
    if allowed_paths:  # ← EMPTY LIST IS FALSY!
        wrapper = f'''... with _safe_open() guard ...'''
    else:  # ← TAKES THIS PATH!
        wrapper = f'''... no guard at all ...'''
        return wrapper
```

**Current config.yaml**:
```yaml
allowed_file_paths: []  # EMPTY - no file access configured
```

**What happens**:
1. `allowed_paths = self.config.allowed_file_paths` → `[]`
2. `if allowed_paths:` → `False` (empty list is falsy in Python)
3. Script wrapper created **WITHOUT** the `_safe_open()` guard
4. Script runs with **ZERO file access restrictions**
5. Script can write anywhere

**Result**: ✗ No validation occurs, no PermissionError thrown, no confirmation triggered

---

### Issue 2: No Runtime Error Detection

**File**: `app/services/script_manager.py:784-931`

Even if PermissionError was thrown by the wrapper, the subprocess error handling doesn't:
1. Extract the blocked path from error message
2. Create a pending_confirmation
3. Signal that user approval is needed

**Current behavior** (line 915-922):
```python
except PermissionError as e:
    logger.error(f"Keine Berechtigung zum Ausführen von Script: {script_path}")
    return ExecutionResult(
        success=False,
        stdout='',
        stderr='',
        error=f"Keine Berechtigung zum Ausführen des Scripts (PermissionError)"
    )
```

**Missing**:
- Parse error message to extract requested path
- Create `pending_confirmation` with path approval data
- Return signal to orchestrator that confirmation needed

---

### Issue 3: No Missing Import Detection

**File**: `app/services/script_manager.py` - NO import validation at runtime

Scripts can `import win32com` even if not in `allowed_imports`.

**Why**:
- Static validation happens BEFORE execution (script_tools.py)
- But dynamic imports can happen at runtime
- No wrapper to catch ImportError for non-whitelisted modules

---

## Missing Implementations from Design

### Planned MVP (from IMPLEMENTATION doc)
```
## 5. File-Write Wrapper Approach (in design doc)

"### File-Write Wrapper Approach":
- Pro: Covers all File-Writes
- Con: Complex injection, hard to test
- Alt: Restrict to specific operations for MVP
```

**Status**: ✗ NOT IMPLEMENTED

**What was planned**:
```python
# In script wrapper
def _validated_open(path, mode='r', *args, **kwargs):
    if write_mode:
        abs_path = os.path.abspath(path)
        # Send validation request to parent via JSON message
        validation_msg = {
            "_type": "path_validation_request",
            "requested_path": abs_path,
            "access_type": "write"
        }
        print(json.dumps(validation_msg), file=sys.stderr)
        # Read response from parent
        # If blocked: pause script, trigger confirmation
        # If approved: continue
```

**What actually exists**:
- Wrapper throws PermissionError
- Parent catches it as generic error
- No communication channel to parent about which path failed
- No pause/resume mechanism

---

## Design vs Implementation Mismatch

### From IMPLEMENTATION_path_approval_confirmation.md:

**Claimed**:
```
### When Confirmation Triggered

**Current Implementation (MVP)**:
- Manual confirmation when user approves in panel
- Via PUT /api/agent/confirm endpoint
- Operation type: `path_approval_confirm`

**Future Enhancement (Phase 2)**:
- Runtime file-write wrapper injection
- Automatic detection during execution
- Script pause/resume capability
```

**Reality**:
- ✗ No automatic detection during execution
- ✗ No script pause/resume
- ✗ No communication from script to parent about blocked paths
- ✗ Path approval only works if user manually triggers it (which doesn't happen)

---

## Current Flow vs Intended Flow

### Current Flow (BROKEN)
```
User starts script
  ↓
Script wrapper created with allowed_paths=[]
  ↓
if []: False  ← GUARD NOT CREATED
  ↓
Script runs WITHOUT validation
  ↓
Script writes to any path
  ↓
No error, no confirmation, no blocking
```

### Intended Flow (NOT IMPLEMENTED)
```
User starts script
  ↓
Script wrapper created with allowed_paths and _safe_open()
  ↓
if allowed_paths: True  ← GUARD CREATED
  ↓
Script tries to write to non-whitelisted path
  ↓
_safe_open() throws PermissionError
  ↓
Error message sent to parent via JSON/stderr
  ↓
Parent detects "File write blocked" in output
  ↓
Parent creates pending_confirmation
  ↓
Frontend shows confirmation panel
  ↓
User approves → path added to whitelist
  ↓
Script restarted
  ↓
Now writes succeed
```

---

## Issues Summary

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| **1** | Empty `allowed_file_paths` disables ALL validation | 🔴 CRITICAL | Not Fixed |
| **2** | No runtime detection of blocked paths | 🔴 CRITICAL | Not Implemented |
| **3** | No communication from subprocess to parent | 🟠 HIGH | Not Implemented |
| **4** | No missing import validation at runtime | 🟠 HIGH | Not Implemented |
| **5** | Path approval only manual, never auto-triggered | 🟠 HIGH | By Design (Gap) |

---

## Fixes Required

### FIX 1: Change Wrapper Logic (SIMPLE)

**File**: `app/services/script_manager.py:742`

**Current**:
```python
if allowed_paths:  # Falsy for []
```

**Change to**:
```python
if allowed_paths is not None:  # Explicit None check
```

**BUT**: Also need to configure `allowed_file_paths` properly:
- Empty = NO file access
- OR add some default paths like `/tmp`, `C:\Temp`

### FIX 2: Implement Runtime Error Detection (COMPLEX)

Need to:
1. Monitor subprocess stderr for "File write blocked" messages
2. Parse requested path from error
3. Create pending_confirmation in ExecutionResult
4. Return to orchestrator
5. Orchestrator creates confirmation panel
6. Frontend shows it
7. User approves
8. Script restarts

**This requires**:
- Modify _run_local() to parse stderr for path errors
- Add pending_confirmation field handling
- Modify orchestrator to handle path_approval from script errors
- Test full round-trip

### FIX 3: Implement Missing Import Detection (MEDIUM)

Add wrapper around `__import__` or use importlib hook:
```python
_original_import = __builtins__.__import__
def _safe_import(name, *args, **kwargs):
    if name not in ALLOWED_IMPORTS and not name.startswith('_'):
        raise ImportError(f"Import blocked: {name}")
    return _original_import(name, *args, **kwargs)
__builtins__.__import__ = _safe_import
```

---

## Immediate Actions Needed

### Option A: Disable Path Approval Feature (QUICK)
```python
# In script_manager.py _create_wrapper()
# Remove the guard entirely for now
# Until proper implementation is ready
wrapper = f'''# === UNSAFE WRAPPER ===
{code}
'''
```
⚠️ **Risk**: Scripts have unrestricted file access

### Option B: Properly Implement Path Approval (RIGHT WAY)
1. Initialize `allowed_file_paths` with sensible defaults (temp dir, outputs dir)
2. Implement runtime error detection
3. Handle pending confirmations properly
4. Test end-to-end

### Option C: Strict Mode (TEMPORARY)
```python
if not allowed_paths:
    # No paths configured = NO file access at all
    wrapper = '''
import io
import os
class BlockedOpen:
    def __init__(self, *args, **kwargs):
        raise PermissionError("File access disabled: No paths whitelisted")
os.open = BlockedOpen()
'''
```

---

## Recommendation

**DO NOT USE** path approval feature in current state (v2.28.31).

**Options**:
1. **Revert** to v2.28.30 (before path approval was added)
2. **Or**: Configure allowed_file_paths with actual paths:
   ```yaml
   allowed_file_paths:
     - "/tmp"
     - "C:\\Temp"
     - "/home/{user}/documents"
   ```
3. **Or**: Properly implement the missing runtime detection

The feature was **designed but not fully implemented**. The confirmation panel exists, but the trigger mechanism (runtime path validation) was never built.

---

## Testing

### Test 1: Verify Wrapper is Created with Guard
```python
script_manager = get_script_manager()
script_manager.config.allowed_file_paths = ["/tmp"]
wrapper = script_manager.executor._create_wrapper(
    "print('test')",
    {},
    allowed_paths=["/tmp"]
)
assert "_safe_open" in wrapper
assert "ALLOWED_FILE_PATHS" in wrapper
```

### Test 2: Verify Empty List Disables Guard (BUG)
```python
wrapper = script_manager.executor._create_wrapper(
    "print('test')",
    {},
    allowed_paths=[]
)
assert "_safe_open" not in wrapper  # ← BUG: Guard not created!
```

### Test 3: Verify Script Can Write Anywhere (BUG)
```python
result = await script_manager.execute(
    script_id,
    args={},
    input_data=None
)
# Script writes to C:\Windows\test.txt
assert result.success  # ← Should fail but doesn't
```

---

## Files to Modify

1. **app/services/script_manager.py**
   - Line 742: Fix `if allowed_paths:` check
   - Line 784-931: Add path validation error detection
   - Implement pending_confirmation handling

2. **app/services/path_validator.py**
   - (Already correct, just not used during execution)

3. **app/agent/orchestrator.py**
   - (Already has path_approval_confirm handler)

4. **static/app.js**
   - (Already has UI, just never triggered)

---

## Summary

The path approval feature was **partially implemented**:
- ✓ Designed
- ✓ Frontend UI built
- ✓ PathValidator service created
- ✓ Confirmation handler in orchestrator
- ✗ **Runtime detection NOT implemented**
- ✗ **Error parsing NOT implemented**
- ✗ **Trigger mechanism MISSING**

Result: Feature appears to exist but is non-functional. Scripts run without any file access restrictions, and user is never prompted for confirmation.
