# Implementation: Runtime Path Approval Detection (v2.28.33)

## Overview

**COMPLETE RUNTIME PATH APPROVAL FEATURE** is now fully implemented and tested.

When a script tries to write to a non-whitelisted file path:
1. ✅ **Detection**: Wrapper blocks write with PermissionError
2. ✅ **Extraction**: Parent process parses error message for blocked path
3. ✅ **Confirmation**: User is asked via frontend panel
4. ✅ **Approval**: User can approve and path gets whitelisted
5. ✅ **Restart**: Script automatically restarts with approved path
6. ✅ **Success**: Script continues and succeeds

## Complete Flow

```
Script writes to /tmp/output.txt
  ↓
Wrapper _safe_open() checks allowed_file_paths
  ↓
/tmp/output.txt NOT in allowed_file_paths: []
  ↓
PermissionError thrown:
  "File write blocked: '/tmp/output.txt' not in allowed_file_paths. Allowed: []"
  ↓
ScriptExecutor._run_local() catches it
  ↓
_extract_blocked_path() parses stderr
  ↓
Returns: "/tmp/output.txt"
  ↓
Creates pending_confirmation:
  {
    "operation": "path_approval_confirm",
    "requested_path": "/tmp/output.txt",
    "access_type": "write",
    "reason": "Script versucht Datei zu schreiben"
  }
  ↓
ExecutionResult includes pending_confirmation
  ↓
execute_script_after_confirmation() detects it
  ↓
Returns ToolResult with:
  requires_confirmation=True
  confirmation_data={...}
  ↓
Frontend receives confirmation request
  ↓
Confirmation panel shows:
  "Dateizugriff blockiert: /tmp/output.txt"
  "Erlauben" / "Ablehnen" Buttons
  ↓
User clicks "Erlauben"
  ↓
Frontend sends PUT /api/agent/confirm with:
  operation="path_approval_confirm"
  requested_path="/tmp/output.txt"
  ↓
Orchestrator._execute_confirmed_operation() handles it
  ↓
PathValidator.validate_approval() checks:
  - Not system-critical? ✓
  - User allowed? ✓
  ↓
Path added to config.yaml:
  allowed_file_paths:
    - "/tmp/output.txt"
  ↓
Settings reloaded in memory
  ↓
Script restarts with same args/input
  ↓
NOW: /tmp/output.txt IS in allowed_file_paths
  ↓
Wrapper allows write → ✅ Success
```

## Implementation Details

### 1. Path Extraction (`ScriptExecutor._extract_blocked_path()`)

**Location**: `app/services/script_manager.py:795-823`

**Regex Pattern**:
```python
pattern = r"File write blocked:\s*(['\"])([^'\"]+)\1"
```

**Matches**:
- `File write blocked: '/home/user/file.txt'`
- `File write blocked: "C:\Users\test\Temp"`
- Any path with single or double quotes

**Returns**: Extracted path or None if not found

### 2. Error Detection (`ScriptExecutor._run_local()`)

**Location**: `app/services/script_manager.py:908-927`

**Process**:
1. After script execution completes
2. Check if return code != 0 (failed)
3. Call `_extract_blocked_path(stderr, stdout)`
4. If blocked path found:
   - Create `pending_confirmation` dict
   - Set error message
   - Return ExecutionResult with pending_confirmation

### 3. Confirmation Triggering (`execute_script_after_confirmation()`)

**Location**: `app/agent/script_tools.py:228-251`

**Process**:
1. Receives ExecutionResult from script manager
2. Checks: `if result.pending_confirmation:`
3. Builds confirmation_data:
   - operation: "path_approval_confirm"
   - script_id, script_name
   - requested_path (from pending_confirmation)
   - access_type: "write"
   - reason: "Script versucht Datei zu schreiben"
4. Returns ToolResult with:
   - requires_confirmation=True
   - confirmation_data=confirmation_data

### 4. User Confirmation (`Frontend`)

**Location**: `static/app.js` (unchanged, already handles this)

**Shows**:
```
┌────────────────────────────────┐
│ Dateizugriff erforderlich       │
├────────────────────────────────┤
│ Zugriff: write                 │
│ Pfad: /tmp/output.txt          │
│ Grund: Script versucht ...     │
│                                │
│ [Erlauben]  [Ablehnen]        │
└────────────────────────────────┘
```

### 5. Path Whitelisting (`Orchestrator`)

**Location**: `app/agent/orchestrator.py:3150-3226` (v2.28.31)

**Process**:
1. User clicks "Erlauben"
2. Frontend sends PUT /api/agent/confirm
3. Orchestrator._execute_confirmed_operation() with operation="path_approval_confirm"
4. Validate path (not system-critical)
5. Add to config.yaml allowed_file_paths
6. Reload settings
7. Restart script
8. Return success

## Test Coverage

### Unit Tests (13 new tests in `test_path_approval_runtime.py`)

✅ All passing:
- `test_extract_blocked_path_unix` - Unix paths
- `test_extract_blocked_path_windows` - Windows paths
- `test_extract_blocked_path_not_found` - No false positives
- `test_execution_result_with_pending_confirmation` - Data structure
- `test_wrapper_creates_guard_with_empty_list` - Guard creation
- `test_wrapper_error_message_includes_path` - Error message format
- `test_pending_confirmation_structure` - Confirmation data
- `test_confirmation_data_construction` - Frontend data
- `test_tool_result_with_confirmation` - ToolResult format
- `test_path_approval_flow_scenario` - Complete flow
- `test_multiple_path_extractions` - Multiple blocked paths
- `test_path_with_spaces_extraction` - Paths with spaces
- `test_path_with_special_chars_extraction` - Special characters

### Integration Tests (28 total passing)

Includes tests from:
- `test_path_validator.py` (8 tests) - Path validation logic
- `test_path_approval_flow.py` (7 tests) - Scenario-based tests
- `test_path_approval_runtime.py` (13 tests) - Runtime detection

## Example Scenarios

### Scenario 1: win32com.client Temp Files

**Initial State**:
```yaml
allowed_file_paths: []  # No paths whitelisted
```

**User starts word-to-pdf conversion**:
```python
script = """
import win32com.client
word = win32com.client.Dispatch('Word.Application')
# ... conversion happens ...
# win32com needs to create files in C:\Users\...\AppData\Local\Temp\gen_py
```

**What happens**:
1. Script runs
2. win32com tries to create temp file
3. Wrapper blocks: "File write blocked: 'C:\Users\...\Temp\gen_py\...'"
4. Extraction gets: `C:\Users\...\AppData\Local\Temp\gen_py`
5. Frontend shows confirmation
6. User clicks "Erlauben"
7. Path added to config.yaml
8. Script restarts
9. ✅ Conversion succeeds

### Scenario 2: Multiple Sequential Paths

**Script needs multiple output paths**:
```python
open('C:\output\report.pdf', 'w')  # ← First blocked
open('C:\output\data.csv', 'w')     # ← Would block if first allowed
```

**What happens**:
1. First write blocked → "File write blocked: 'C:\output\report.pdf'"
2. User approves → added to whitelist
3. Script restarts
4. First write succeeds
5. Second write blocked → "File write blocked: 'C:\output\data.csv'"
6. User approves → added to whitelist
7. Script restarts
8. ✅ Both writes succeed

### Scenario 3: System Path Protection

**Script tries to write to system directory**:
```python
open('C:\Windows\test.txt', 'w')
```

**What happens**:
1. Wrapper blocks: "File write blocked: 'C:\Windows\test.txt'"
2. Path extracted: 'C:\Windows\test.txt'
3. Orchestrator detects it's system-critical
4. Validates with PathValidator.is_system_critical()
5. Returns error: "Zugriff verweigert: System-Verzeichnisse dürfen nicht modifiziert werden"
6. ✅ System path protected, script fails with clear message

## Configuration

### Before (v2.28.32)

```yaml
script_execution:
  allowed_file_paths: []  # Empty = blocks ALL writes
```

**Result**:
- All scripts blocked from writing
- No way to run scripts that need file output

### After (v2.28.33)

```yaml
script_execution:
  allowed_file_paths: []  # Still blocks initially
```

**But now**:
- User can approve paths at runtime
- Paths are automatically added
- Script restarts and succeeds
- Next time same path: no confirmation needed

**Example after user approvals**:
```yaml
script_execution:
  allowed_file_paths:
    - "/tmp/output.txt"
    - "/home/user/Documents/reports"
    - "C:\\Users\\...\\AppData\\Local\\Temp\\gen_py"
```

## Files Modified

| File | Changes |
|------|---------|
| `app/services/script_manager.py` | +32 lines: `_extract_blocked_path()`, error detection in `_run_local()` |
| `app/agent/script_tools.py` | +24 lines: pending_confirmation detection in `execute_script_after_confirmation()` |
| `tests/test_path_approval_runtime.py` | +256 lines: 13 new comprehensive tests |
| `VERSION` | 2.28.32 → 2.28.33 |

## Error Messages

### User-Friendly

**When path blocked**:
```
Dateizugriff blockiert: /tmp/output.txt
Bitte bestätigen Sie, um Zugriff freizugeben.
```

**When system path attempted**:
```
Zugriff verweigert: System-Verzeichnisse dürfen nicht modifiziert werden.
```

### Technical (Logs)

```
[INFO] Path approval requested: /tmp/output.txt
[WARNING] File write blocked: '/tmp/output.txt' not in allowed_file_paths
[INFO] Path whitelisted: /tmp/output.txt
```

## Security

✅ **Protected**:
- System directories cannot be whitelisted
- Paths are normalized (prevents ../../../Windows traversal)
- Case-insensitive on Windows (prevents case-based bypass)
- Only user-approved paths are added

✅ **Validated**:
- PathValidator.validate_approval() checks each path
- is_system_critical() blocks protected paths
- Error messages don't expose sensitive info

## Verification

```bash
# Run all tests
python3 -m pytest tests/test_path_validator.py tests/test_path_approval_flow.py tests/test_path_approval_runtime.py -v

# Result: 28/28 passing
```

## Known Limitations (Phase 2+)

1. **Only write access**: Read/delete not yet implemented
2. **Single path per error**: Multiple simultaneous writes only first detected
3. **No batch approval**: User must approve each path individually
4. **No wildcards**: Exact paths only, no patterns like `/tmp/*`

## Next Steps

1. **Import Validation**: Similar flow for missing imports
2. **Batch Approval**: Ask for multiple paths at once
3. **Wildcard Support**: Allow parent directory patterns
4. **Audit Logging**: Track all approvals in UI

## Summary

The path approval feature is **FULLY OPERATIONAL**:
- ✅ Automatic detection of blocked paths
- ✅ User-friendly confirmation interface
- ✅ Automatic path whitelisting
- ✅ Script restart and resume
- ✅ System protection
- ✅ Comprehensive test coverage
- ✅ Clear error messages

**Status**: Production Ready (MVP complete)
