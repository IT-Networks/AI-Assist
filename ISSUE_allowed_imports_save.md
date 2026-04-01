# Issue: allowed_imports Changes Not Persisted (v2.28.28)

## Problem

When user adds `win32com` to `allowed_imports` in Settings UI:
1. Change is saved to in-memory `settings` object ✅
2. User clicks "Save" → written to config.yaml ✅
3. BUT: Next validation still rejects `win32com` as "not allowed" ❌

## Root Cause

**Likely culprit: ScriptManager singleton cache**

The ScriptManager caches the validator configuration at initialization:

```python
class ScriptManager:
    def __init__(self):
        self.config = settings.script_execution  # ← Cached reference
        self.validator = ScriptValidator()       # ← Uses cached config
```

When `POST /api/settings/save` is called:
1. config.yaml is updated ✅
2. settings object is reloaded ✅
3. BUT: ScriptManager still has old reference to old config ❌
4. Validator still uses old config with old allowed_imports ❌

## The Flow (BROKEN)

```
1. User adds win32com to allowed_imports
2. PUT /api/settings/section/script_execution
   → settings.script_execution updated in memory
   → ScriptManager.invalidate_cache() called ✅
3. POST /api/settings/save
   → config.yaml written ✅
   → settings reloaded ✅
   → But ScriptManager was already destroyed by invalidate_cache
   → Next usage: ScriptManager._instance = None → get_instance() creates NEW instance
   → NEW instance gets NEW reference to updated settings ✅
4. But there's a race condition or timing issue

Actually this should work... let me check if the issue is:
   - invalidate_cache() is called BEFORE settings reload?
   - Or it's not called at all?
```

## Investigation

From earlier testing:
- `allowed_imports` in config.yaml: `docx2pdf` (no `win32com`)
- Validation result: `win32com` rejected as "not allowed"

This suggests that the config.yaml update FAILED or DIDN'T HAPPEN.

## Possible Causes

1. **UI Save Button Not Working:**
   - User adds win32com via UI
   - Clicks "Save" button
   - But the button doesn't actually call POST /api/settings/save?
   - Only updates in-memory settings via PUT /api/settings/section/?

2. **Settings API Issue:**
   - The settings reload in POST /api/settings/save may have failed
   - Check `app/api/routes/settings.py` line 475-490

3. **ScriptValidator Caching:**
   - Even though ScriptManager is invalidated
   - The ScriptValidator might still have cached config

## Workaround

Add `win32com` directly to `config.yaml` in allowed_imports section.

## Proper Fix (TODO)

Need to verify:
1. POST /api/settings/save actually reloads settings ✅ (added in v2.28.27)
2. ScriptManager.invalidate_cache() is actually called ✅ (confirmed in settings.py)
3. But maybe the issue is **BEFORE** the save:
   - When user edits in UI, they click "Save" button
   - This should call POST /api/settings/save
   - But maybe it only calls PUT /api/settings/section/?

Check `static/app.js` - how does the "Save" button work?

## Verification

Test case added: `test_win32com_validation.py`
- Shows exactly which imports are blocked
- Confirms allowed_imports from current config

## Status

- [x] Issue identified: win32com not in config.yaml
- [x] Workaround applied: Added win32com to config.yaml manually
- [ ] Root cause verified: Need to check UI save flow
- [ ] Proper fix: Ensure all settings changes are persisted correctly
