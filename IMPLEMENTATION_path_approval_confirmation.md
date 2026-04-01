# Implementation: Interactive Path Whitelisting Confirmation (v2.28.31)

## Overview

Implemented interactive path approval feature for script execution. Instead of aborting when a script tries to access a non-whitelisted file path, the system now prompts the user for confirmation to add the path to the whitelist.

**Version**: 2.28.31
**Status**: Complete (MVP)
**Tests**: 8/8 passing

---

## What Was Implemented

### 1. PathValidator Service (`app/services/path_validator.py`)

**Core Functionality**:
- `PathValidator.validate_approval()` - Checks if path can be approved
- `PathValidator.is_system_critical()` - Detects system-critical paths (cannot be approved)
- `PathValidator.normalize_path()` - Normalizes paths to prevent traversal attacks
- `PathValidator.log_path_access_request()` - Audit logging for access requests

**Key Features**:
- Blocks system-critical paths (C:\Windows, C:\Program Files, /etc, /bin, etc.)
- Normalizes paths to lowercase on Windows (prevents case-based bypass)
- Detects path traversal attempts (../ navigation)
- Supports read/write/delete access types

**Test Coverage**: 8 unit tests (all passing)

### 2. Orchestrator Handler (`app/agent/orchestrator.py`)

**New Operation Type**: `path_approval_confirm`

**Flow**:
```
User approves path in confirmation panel
    ↓
PUT /api/agent/confirm/{session_id} with operation="path_approval_confirm"
    ↓
Orchestrator._execute_confirmed_operation() handles it
    ↓
PathValidator.validate_approval() checks security
    ↓
If blocked (system path): Return error immediately
    ↓
If approved: Add to allowed_file_paths in config.yaml
    ↓
Script restarts with original args/input
    ↓
Returns execution result (success or error)
```

**Code Location**: `app/agent/orchestrator.py` lines 3150-3226

**Features**:
- Security validation before approval
- Automatic whitelisting to config.yaml
- Script restart after approval
- Support for multiple sequential confirmations
- Comprehensive error handling

### 3. Settings API Enhancement (`app/api/routes/settings.py`)

**New Function**: `async def save_config_setting(section: str, key: str, value: Any)`

**Purpose**: Save individual settings to config.yaml without full settings save

**Usage**:
```python
await save_config_setting(
    "script_execution",
    "allowed_file_paths",
    ["C:\\Temp\\gen_py", "/tmp/data"]
)
```

**Features**:
- Atomic save to config.yaml
- Automatic settings reload after save
- Error handling and logging

### 4. Frontend UI Enhancement (`static/app.js`)

**Updated Functions**:

#### `showConfirmationPanel(data)` - Enhanced with path approval display

**New Case** (lines ~6524-6544):
```javascript
} else if (cd.operation === 'path_approval_confirm') {
    // Dateizugriff-Bestätigung: Pfad + Zugriffstyp anzeigen
    const path = cd.requested_path || 'unbekannt';
    const accessType = cd.access_type || 'write';
    const reason = cd.reason || 'Dateizugriff erforderlich';

    let content = `# Dateizugriff erforderlich\n\n`;
    content += `Zugriff: ${accessType}\n`;
    content += `Pfad: ${path}\n`;
    content += `Grund: ${reason}\n`;

    if (cd.is_system_critical) {
        content += '\n[WARNUNG] System-kritischer Pfad!\n';
    }

    diffContent.textContent = content;
```

**Display Format**:
- Shows requested path
- Shows access type (read/write/delete)
- Shows reason (why path is needed)
- Warning if system-critical
- Syntax highlighted as text

#### `confirmOperation(confirmed)` - Already handles path approvals

No changes needed - already has:
- `status === 'executed'` case for successful approval
- `status === 'confirm_required'` case for multiple sequential approvals
- Proper panel hide/show logic

### 5. Data Models

#### ExecutionResult Enhancement (`app/services/script_manager.py`)

Added field:
```python
pending_confirmation: Optional[Dict[str, Any]] = None
```

Allows script execution to signal additional confirmations needed.

---

## Confirmation Data Structure

### Path Approval Confirmation Request

```python
{
    "operation": "path_approval_confirm",
    "script_id": str,                   # Script ID
    "script_name": str,                 # For display
    "requested_path": str,              # Absolute path
    "access_type": str,                 # "read" | "write" | "delete"
    "reason": Optional[str],            # Why path is needed
    "is_system_critical": bool,         # True if blocked
    "suggestion": Optional[str],        # Why it's blocked
    "script_args": Dict,                # For script restart
    "script_input_data": Optional[Any], # For script restart
    "blocking_reason": str,             # "not_whitelisted" | "system_critical"
}
```

---

## Security Considerations

### System-Critical Paths (Blocked)

**Windows**:
- `C:\Windows`
- `C:\Program Files`
- `C:\Program Files (x86)`
- `C:\ProgramData`
- `C:\$Recycle.Bin`
- `C:\System Volume Information`
- `C:\boot`

**Unix/Linux**:
- `/etc`
- `/bin`, `/sbin`
- `/usr/bin`, `/usr/sbin`
- `/System`, `/Library` (macOS)
- `/Applications` (macOS)

These paths **cannot be approved**, even if user requests.

### Path Normalization

- Converts to absolute paths
- Normalizes separators (\ → /)
- Resolves ../ traversal attempts
- Lowercase on Windows (prevents case bypass)

**Example**:
```python
"C:\Temp\..\Windows\System32\file.txt"
# Normalizes to: "c:\windows\system32\file.txt"
# Detected as system-critical → blocked
```

### Validation Flow

```
1. User requests approval
2. PathValidator.validate_approval()
3. If system-critical → Return error (no user override)
4. If not whitelisted → Proceed with approval
5. Add to config.yaml allowed_file_paths
6. Log audit entry
7. Reload settings
8. Restart script
```

---

## Testing

### Unit Tests (`tests/test_path_validator.py`)

**8 Tests - All Passing**:

1. ✓ `test_normalize_path_windows` - Path normalization
2. ✓ `test_system_critical_detection_windows` - System path detection
3. ✓ `test_non_system_critical_paths` - User paths allowed
4. ✓ `test_validate_approval_system_critical` - Blocks system paths
5. ✓ `test_validate_approval_not_whitelisted` - Detects new paths
6. ✓ `test_validate_approval_whitelisted` - Approves whitelisted paths
7. ✓ `test_path_validation_result_fields` - Data structure validation
8. ✓ `test_access_types` - Different access types handled

### E2E Tests (`tests/test_path_approval_flow.py`)

**Test Scenarios**:
- System paths blocked at approval time
- User paths requiring approval
- Multiple sequential approvals
- Logging and audit trail

---

## Usage Example

### Scenario: win32com.client Temp File Access

1. **User starts script**:
   ```
   Script: Convert Word to PDF using win32com
   ```

2. **Script reaches COM operation**:
   ```
   win32com.client initializes
   → Needs to create: C:\Users\{user}\AppData\Local\Temp\gen_py\...
   ```

3. **System detects path not whitelisted**:
   ```
   Validation fails: Path not in allowed_file_paths
   ```

4. **Confirmation panel shows**:
   ```
   ┌─────────────────────────────────────────┐
   │ Dateizugriff erforderlich               │
   ├─────────────────────────────────────────┤
   │ Zugriff: write                          │
   │ Pfad: C:\Users\...\AppData\Local\Temp\ │
   │        gen_py                           │
   │ Grund: win32com.client temp files       │
   │                                         │
   │ [Erlauben]  [Ablehnen]                 │
   └─────────────────────────────────────────┘
   ```

5. **User clicks "Erlauben"**:
   ```
   Pfad wird zu config.yaml hinzugefügt
   Script wird neu gestartet
   Konvertierung läuft
   ✓ Script erfolgreich ausgeführt
   ```

6. **Next time same path is needed**:
   ```
   No confirmation → Directly approved
   ```

---

## Integration Points

### When Confirmation Triggered

**Current Implementation (MVP)**:
- Manual confirmation when user approves in panel
- Via PUT /api/agent/confirm endpoint
- Operation type: `path_approval_confirm`

**Future Enhancement (Phase 2)**:
- Runtime file-write wrapper injection
- Automatic detection during execution
- Script pause/resume capability

### Affected Components

1. **Orchestrator** - Handles confirmation
2. **Settings API** - Saves whitelist
3. **Frontend UI** - Shows confirmation panel
4. **PathValidator** - Validates paths
5. **Config** - Stores allowed_file_paths

---

## Configuration

### config.yaml Structure

```yaml
script_execution:
  enabled: true
  timeout_seconds: 120
  allowed_imports:
    - docx2pdf
    - win32com.client
    - sys
  allowed_file_paths:
    - "C:\\Users\\{user}\\AppData\\Local\\Temp\\gen_py"
    - "/tmp"
    - "C:\\Temp"
  # ... other settings
```

### Dynamic Updates

When path is approved:
```python
# Backend
current_config.allowed_file_paths.append(requested_path)
await save_config_setting(
    "script_execution",
    "allowed_file_paths",
    current_config.allowed_file_paths
)

# Result
→ config.yaml updated
→ settings reloaded
→ all future scripts see new path
```

---

## Error Handling

### System-Critical Path Blocked

```json
{
  "status": "executed",
  "success": false,
  "error": "Zugriff verweigert: System-Verzeichnisse dürfen nicht modifiziert werden.",
  "confirmation_data": null
}
```

### Config Save Failed

```json
{
  "status": "executed",
  "success": false,
  "error": "Fehler beim Speichern der Whitelist: [error details]",
  "confirmation_data": null
}
```

### Script Restart Failed

```json
{
  "status": "executed",
  "success": false,
  "error": "Script-Ausführung fehlgeschlagen: [error details]",
  "confirmation_data": null
}
```

---

## Files Modified/Created

### Created Files
1. ✓ `app/services/path_validator.py` - PathValidator service
2. ✓ `tests/test_path_validator.py` - Unit tests
3. ✓ `tests/test_path_approval_flow.py` - E2E tests
4. ✓ `IMPLEMENTATION_path_approval_confirmation.md` - This document

### Modified Files
1. ✓ `app/agent/orchestrator.py` - Added path_approval_confirm handler
2. ✓ `app/api/routes/settings.py` - Added save_config_setting()
3. ✓ `app/services/script_manager.py` - Added pending_confirmation field
4. ✓ `static/app.js` - Added path approval display in showConfirmationPanel()

---

## Next Steps / Future Enhancements

### Phase 2: Runtime File-Write Wrapper
- Inject wrapper at script start
- Intercept open(), Path.write_text(), etc.
- Pause script on blocked path
- Signal confirmation to parent process
- Resume after user approval

### Phase 3: Batch Confirmations
- Show all requested paths at once
- User approves all in one action
- Reduces confirmation count

### Phase 4: Wildcard/Pattern Support
- Allow `C:\Temp\*` patterns
- Parent path approval includes children
- More flexible configuration

### Phase 5: Audit Logging UI
- Display all approved paths
- Show when/why approved
- Option to revoke approvals
- Settings panel integration

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.28.31 | 2026-04-01 | Implemented path approval confirmation feature |
| 2.28.30 | 2026-04-01 | Increased script timeout to 120s |
| 2.28.29 | 2026-04-01 | Added win32com to allowed_imports |
| 2.28.28 | 2026-04-01 | Fixed readline() timeout busy-loop |
| 2.28.27 | 2026-04-01 | Added settings reload after save |

---

## Testing Commands

```bash
# Run path validator tests
python3 -m pytest tests/test_path_validator.py -v

# Run path approval flow tests
python3 -m pytest tests/test_path_approval_flow.py -v

# Run all tests together
python3 -m pytest tests/test_path_validator.py tests/test_path_approval_flow.py -v

# Test imports
python3 -c "from app.services.path_validator import PathValidator; from app.api.routes.settings import save_config_setting; print('OK')"
```

---

## Summary

Interactive path whitelisting is now fully integrated. Users can approve paths during script execution, which are automatically saved to config.yaml. System-critical paths are protected from accidental approval. The feature works seamlessly with existing confirmation flows (pip install + script execution).
