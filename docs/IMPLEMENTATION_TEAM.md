# Implementation Team

Multi-Agent-Team für End-to-End Feature-Implementation mit User-Approval,
atomarem Rollback und Projekt-Ausführung direkt aus dem Chat.

**Status**: v2.37.30 | Tests: 78 passed | Produktiv

---

## Überblick

Das **Implementation-Team** zerlegt eine Feature-Anforderung in Teilaufgaben,
lässt sie von spezialisierten Agenten parallel bearbeiten, und liefert am Ende
ein reviewed Ergebnis – alles mit **User-Kontrolle** an zwei Punkten:

```
User-Prompt
  ↓ Coordinator-LLM zerlegt in Tasks
[Plan-Approval-Modal] ← User: Start / Abbrechen
  ↓
Parallel: db → backend → frontend → tests → reviewer
  ↓
[Verification-Modal] ← User: Merge / Request-Changes / Discard
  ↓ bei Discard/Changes: atomarer Rollback via ChangeTracker
```

---

## Agenten

Definition in `config.yaml` → `multi_agent.teams[implementation-team]`:

| Agent | Rolle | Haupt-Tools |
|-------|-------|-------------|
| `database-engineer` | SQL-Schema, Migrations | `write_file`, `edit_file` |
| `backend-engineer` | Python/FastAPI Logik | `write_file`, `edit_file`, `read_file` |
| `frontend-engineer` | React/TypeScript UI | `write_file`, `edit_file`, `read_file` |
| `test-engineer` | pytest + jest/vitest Tests | `write_file`, `read_file`, `run_pytest`, `run_npm_tests` |
| `implementation-reviewer` | Code-Review + Test-Run | `read_file`, `run_pytest`, `run_npm_tests` |

Abhängigkeitsreihenfolge (erzwungen im Coordinator-Prompt):
```
database → backend → frontend → tests → reviewer
```

---

## Tools (v2.37.29 – v2.37.30)

### Test-Execution (kein User-Confirm)
- **`run_pytest(path, test_path, pattern, coverage)`** – pytest-Subprocess mit
  JSON-Report-Parsing (pytest-json-report) und Text-Fallback.
- **`run_npm_tests(path, framework, coverage)`** – npm/jest/vitest Subprocess.
  `framework="auto"` detektiert aus `package.json`.

### Command-Execution (User-Confirm obligatorisch)
- **`run_workspace_command(path, command, timeout_seconds?)`** – führt beliebiges
  Binary aus der Whitelist aus. 49 Default-Binaries (python, node, npm, npx,
  pytest, pip, uv, poetry, cargo, go, mvn, gradle, tsc, jest, vitest, java,
  make, cmake + `.exe`/`.cmd`-Varianten). `command` als Liste, kein `shell=True`.

### Write-Tools (Auto-Confirm im Impl-Team)
- **`write_file(path, content)`** – im Impl-Team bypass von `allowed_paths` und
  `allowed_extensions` (Plan-Approval deckt Scope).
- **`edit_file(path, old, new, replace_all)`** – String-Ersetzung.
- **`create_directory(path)`** – ohne Preview.

---

## User-Flow

### 1. Feature anfordern
Prompt:
```
Implementiere ein JWT-Auth-Feature mit Tests in C:/Users/marku/myproject
```

### 2. Plan-Modal erscheint
- Zeigt Task-Zerlegung, Agenten, geschätzte Dauer.
- Buttons: `Start Implementation` / `Abbrechen`.

### 3. Agenten arbeiten
- Backend schreibt `app/auth.py`, `app/models.py`.
- Frontend schreibt `src/LoginForm.tsx`.
- Tests schreibt `tests/test_auth.py`.
- Reviewer ruft `run_pytest` auf und erhält echte Zahlen.

### 4. Verification-Modal erscheint
- **Echte Metriken**: `"3 Backend-Tests geschrieben, pytest: 28 passed/0 failed, 91% Coverage"`.
- Liste aller geänderten Dateien.
- Buttons: `Zu Git Mergen` / `Änderungen Nötig` / `Alles Verwerfen`.

### 5. Bei Discard
- **Atomarer Rollback** via ChangeTracker:
  - Neue Dateien → gelöscht.
  - Geänderte Dateien → aus SHA256-Backup wiederhergestellt.

---

## Chat-Interaktionen (außerhalb des Teams)

Nach v2.37.30 kann der normale Chat-Agent auch:

| User sagt | LLM-Tool | Confirm? |
|-----------|----------|----------|
| "Teste das Projekt in C:/myapp" | `run_pytest(path="C:/myapp")` | Nein |
| "Starte main.py in C:/myapp" | `run_workspace_command(path, ["python","main.py"])` | **Ja** |
| "npm run dev" | `run_workspace_command(path, ["npm","run","dev"])` | **Ja** |
| "Baue das Maven-Projekt" | `run_workspace_command(path, ["mvn","package"])` | **Ja** |

---

## Architektur

```
User-Chat
    ↓
app/agent/orchestrator.py (main chat agent)
    ├─ LLM erkennt "implementiere..." → ruft run_team
    └─ LLM erkennt "starte..." → ruft run_workspace_command
                 ↓
app/agent/multi_agent/team_tools.py → run_team
                 ↓
app/agent/multi_agent/orchestrator.py
    ├─ _extract_workspace_path(goal)
    ├─ _decompose_goal() mit impl_team_rules
    ├─ ApprovalManager.request_plan_approval() ← Plan-Modal
    ├─ AgentPool.dispatch Tasks
    │   ├─ TeamAgent (erbt SubAgent)
    │   ├─ auto_confirm_writes=True
    │   └─ _direct_file_op() bypass FileManager
    ├─ ChangeTracker.track_create/modify
    └─ ApprovalManager.request_verification_approval() ← Verification-Modal
```

**Kernkomponenten**:
- `app/agent/multi_agent/orchestrator.py` – Team-Coordinator mit Plan/Verification
- `app/agent/multi_agent/team_agent.py` – TeamAgent erbt SubAgent
- `app/agent/sub_agent.py` – LLM-Loop, Write-Mode vs. Read-Mode System-Prompts
- `app/agent/multi_agent/change_tracker.py` – SHA256-Backup, atomarer Rollback
- `app/agent/multi_agent/approval_manager.py` – 3-Stage Workflow, globale Registry
- `app/services/test_runner.py` – pytest/jest/vitest Subprocess-Helper
- `app/services/command_runner.py` – Generic Binary-Whitelist Runner
- `app/agent/test_exec_tools.py` – run_pytest, run_npm_tests Tool-Handler
- `app/agent/command_tools.py` – run_workspace_command mit Two-Phase-Confirm

---

## Sicherheits-Layers

| Schutz | Wo | Aktiv wann |
|--------|-----|-----------|
| Plan-Approval-Modal | ApprovalManager | Vor jeder Impl-Team-Ausführung |
| Verification-Approval-Modal | ApprovalManager | Vor Finalisierung |
| Atomarer Rollback | ChangeTracker | Bei Discard/Changes-Request |
| Binary-Whitelist | command_runner | `run_workspace_command` |
| Command-Confirm | Two-Phase-Pattern | `run_workspace_command` |
| No-Shell | `create_subprocess_exec` | Alle Subprocess-Tools |
| Timeout | `asyncio.wait_for` | Alle Subprocess-Tools |
| stdout-Cap | `_truncate(5000)` | Context-Protection |
| FileManager-Schutz | `allowed_paths/extensions` | Nur für **normale** Chat-Writes, Team bypasst |
| Workspace-Validation | `Path.resolve() + is_dir()` | Alle Workspace-Tools |

---

## Konfiguration

**`config.yaml`** relevante Sections:
```yaml
multi_agent:
  enabled: true
  teams:
    - name: implementation-team
      strategy: dependency-first
      max_parallel: 4
      agents: [...]   # 5 Agenten

test_exec:
  enabled: true
  timeout_seconds: 120

command_exec:
  enabled: true
  timeout_seconds: 120
  allowed_binaries: []   # [] = Default-Whitelist
```

---

## Frontend

`static/app.js` + `static/style.css`:
- `approvalState` – State-Manager für Plan/Verification-Modals
- `openPlanApprovalModal()` / `openVerificationModal()`
- `approveImplementationPlan()` / `rejectImplementationPlan()` (umbenannt
  wegen Namens-Kollision mit bestehendem `approvePlan(card, chat)`)
- POST `/api/agent/approval-response` zur Backend-Rückmeldung
- Dark-Theme-Variablen (`--bg`, `--text`, `--surface`, `--border`)

---

## Change-Tracker & Rollback

Jede Write-Operation im Impl-Team wird getrackt:
```python
self._change_tracker.track_create(path, agent=self.name)
# oder
self._change_tracker.track_modify(path, agent=self.name)  # mit SHA256-Backup
```

Bei Rollback:
- **CREATE** → Datei wird gelöscht.
- **MODIFY** → Original aus `backups/` wiederhergestellt (SHA256-verifiziert).

Manifest unter `backups/feat_<id>/manifest.json` enthält:
- Feature-ID, Timestamp, User-Request
- Alle Änderungen mit Agent-Name, Operation-Typ, Hash
- Test-Results, Rollback-Status

---

## Version-History Highlights

| Version | Fix |
|---------|-----|
| v2.37.13 | Initial Implementation-Team mit 3-Stage-Approval |
| v2.37.18 | Namens-Kollision approvePlan behoben |
| v2.37.19 | Write-Tools im Agent-Schema sichtbar (include_write_ops) |
| v2.37.20 | gpt-oss-120b (mit Strichen), Team-Agents nutzen default_model |
| v2.37.22 | Auto-Confirm für Team-Writes (Dateien werden tatsächlich geschrieben) |
| v2.37.24 | `_direct_file_op` bypasst allowed_paths/extensions |
| v2.37.25 | Getrennte System-Prompts: Write-Agent vs. Such-Agent |
| v2.37.27 | max_tokens=8192 für Write-Mode (keine Truncation mehr) |
| v2.37.28 | Test-Engineer + Reviewer system_prompts bereinigt |
| v2.37.29 | **`run_pytest` + `run_npm_tests`** Tools |
| v2.37.30 | **`run_workspace_command`** Tool für Projekt-Ausführung |

---

## Testen des Flows

```bash
# 1. Server starten
cd AI-Assist
uvicorn main:app

# 2. Im Browser Chat öffnen (http://localhost:8000)

# 3. Prompt eingeben:
"Implementiere ein einfaches FastAPI Hello-World Feature mit Tests
 in C:/Users/marku/Documents/hello-test"

# 4. Plan-Modal → Start klicken
# 5. Agenten laufen → Dateien entstehen in C:/Users/marku/Documents/hello-test
# 6. Verification-Modal → Merge klicken
# 7. Im Chat: "Starte main.py in C:/Users/marku/Documents/hello-test"
# 8. Confirm-Modal → Bestätigen → Ausgabe im Chat sichtbar
```

---

## Fehlerdiagnose

Server-Log Patterns:
```
[MultiAgent] Workspace-Pfad aus Goal: C:/Users/marku/hello-test
[sub_agent:backend-engineer] TOOL_CALL write_file path='.../main.py' content_len=845
[sub_agent:backend-engineer] DIRECT write_file ...main.py (845 chars, new=True)
[sub_agent:backend-engineer] RESPONSE TRUNCATED (finish_reason=length) ...  ← content zu groß
[sub_agent:backend-engineer] TOOL_ARGS PARSE-FEHLER ...                      ← JSON truncated
[test_runner] pytest fertig in 1823ms: 28 passed, 0 failed, exit=0
[command_runner] Fertig in 340ms, exit=0
```

---

## Nächste Schritte (optional)

- [ ] Streaming-Output für langlaufende `run_workspace_command`-Aufrufe
- [ ] Per-Projekt Binary-Whitelist-Overrides
- [ ] Verification-Modal: reviewer-Output parsen → test_results-Dict
- [ ] Docker-Sandbox-Option für `run_workspace_command`
