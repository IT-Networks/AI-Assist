# Konsolidierungs-Plan: Lokale Verarbeitungs-Tools

**Datum:** 2026-04-23
**Version:** AI-Assist v2.40.1 → v2.40.2 (Phase 1 ausgeliefert) → Ziel v2.41.0
**Scope:** **Nur lokale Verarbeitung** (File I/O, Exec, generische Suche, generischer Fetch, Python-Scripts, Container-Exec). Domain-Tools (git, github, alm, jenkins, maven, wlp, iq, servicenow, confluence, jira, email, webex, mq, log, knowledge, junit, compile, api/soap, test SOAP, graph) bleiben **unverändert**.
**Ziel:** Angleichung an das Muster moderner Agenten (Claude Code, OpenCode) mit orthogonalem Core-Toolset.

**Status 2026-04-23:** Phase 1 umgesetzt — Alias-Mechanismus in `ToolRegistry`, Telemetrie-Feld `called_as`, Schema-Snapshot-Skript (`scripts/snapshot_tool_schemas.py`), 15 neue Tests (`tests/test_tool_aliases.py`). Registry-Inventur per Snapshot: **108 Tools** (die früher genannten 185 kamen aus einer fehlerhaften Regex, die Parameter-Namen mitgezählt hat — die **Konsolidierungs-Relationen** im Plan bleiben unverändert gültig).

---

## 0 Zusammenfassung der Konsolidierung

| Kategorie | Heute | Ziel | Reduktion |
|---|---:|---:|---:|
| File I/O | 9 Tools | 5 Tools | −4 |
| Generische Suche | 6 Tools | 2 Tools | −4 |
| Shell-Exec | 6 Tools | 1 Tool | −5 |
| Container-Exec | 7 Tools | 1 Tool (mit Sessions) | −6 |
| Python-Script-Lifecycle | 6 Tools | 1 Tool | −5 |
| Generischer Fetch | 2 Tools | 1 Tool | −1 |
| PDF-Read (lokal) | 2 Tools | in `read` | −2 |
| Batch-Wrapper | 2 Tools | entfallen | −2 |
| **Summe lokal** | **40** | **11** | **−29** |

*Podman-Image-Tools (3) fallen weg — durch `bash` abgedeckt; Docker-Image-Verwaltung ist keine tägliche LLM-Operation.*

Dead-Code-Sichtung: **`my_tool` ist KEIN Dead-Code** — es ist ein Docstring-Beispiel in `ToolDefinition` (`tools.py:178`). Bleibt. Keine echten Dead-Tools gefunden; tatsächliche „Redundanz" kommt aus Feature-Splitting, nicht aus toten Definitionen.

---

## 1 Ziel-Toolset (lokaler Core, 11 Tools)

Anlehnung an Claude Code + OpenCode: ein Tool pro Operation, Dispatch über Parameter.

### 1.1 File I/O (5)

| Tool | Parameter | Ersetzt | Kommentar |
|---|---|---|---|
| `read` | `path`, `offset?`, `limit?`, `pages?` (PDF), `encoding?` | `read_file`, `read_pdf_pages`, `read_sqlj_file`, `get_pdf_info`, `batch_read_files` | Content-Type-Dispatch anhand Extension; bei Batch: LLM ruft parallel |
| `write` | `path`, `content` | `write_file`, `create_directory`, `batch_write_files` | Erzeugt Parent-Dirs implizit; `content=""` + Pfad endet auf `/` ⇒ nur Dir erzeugen |
| `edit` | `path`, `old_string`, `new_string`, `replace_all?` | `edit_file` | Umbenannt, Signatur unverändert |
| `ls` | `path`, `recursive?`, `glob?` | `list_files` | Umbenannt |
| `glob` | `pattern`, `path?` | `glob_files` | Umbenannt |

**Anmerkungen zu `read`:**
- `.pdf` ⇒ intern `read_pdf_pages` (mit `pages` Param, Fallback: alle Seiten).
- `.ipynb` ⇒ strukturiert (Zellen + Outputs).
- `.sqlj` ⇒ identisch zu `.java`-Read, aber mit SQL-Block-Highlighting im Output (wie `read_sqlj_file` heute).
- Keine `batch`-Variante — Tool-Calls parallelisieren ist Aufgabe des LLM/Harness.

### 1.2 Generische Suche (2)

| Tool | Parameter | Ersetzt | Kommentar |
|---|---|---|---|
| `grep` | `pattern`, `path?`, `glob?`, `output_mode?` (`content`/`files`/`count`), `-i?`, `-n?`, `-A/-B/-C?`, `multiline?` | `grep_content`, `combined_search` | Reine Regex-Suche über Dateien |
| `search` | `query`, `scope?` (`code`/`handbook`/`skills`/`pdf`/`all`), `limit?` | `search_code`, `search_handbook`, `search_skills`, `search_pdf` | Semantische Suche über Indizes; Scope per Enum |

**`search_pdf` Edge-Case:** heute zieht es Pages aus indizierten PDFs. Bleibt via `search(scope="pdf")`.

**`combined_search` wird nicht direkt ersetzt** — die Kombinationslogik war ein Anti-Pattern; parallele Tool-Calls sind effizienter.

**Nicht angefasst:** `knowledge_tools.py::search_knowledge`, `log_tools.py::log_grep`, `github_tools.py::github_search_code` — **Domain**, bleiben.

### 1.3 Exec (3)

| Tool | Parameter | Ersetzt | Kommentar |
|---|---|---|---|
| `bash` | `command`, `sandbox?` (`workspace`/`local`/`container`), `session_id?`, `cwd?`, `timeout?`, `run_in_background?` | `run_workspace_command`, `shell_execute`, `shell_execute_local`, `shell_list_executions`, `run_pytest`, `run_npm_tests`, `docker_session_execute`, `podman_*` | Eine Exec-Engine. `sandbox=container` + `session_id=null` ⇒ neuer One-Shot-Container; mit `session_id` ⇒ persistent |
| `bash_sessions` | `action` (`list`/`close`/`upload`), `session_id?`, `file?` | `shell_list_executions`, `docker_session_list`, `docker_session_close`, `docker_upload_file` | Lifecycle-Hilfsmittel; kleines Tool für Session-Verwaltung |
| `exec_python` | `code`, `requirements?` (list), `args?`, `stdin?`, `sandbox?` (`container`/`local`), `session_id?` | `generate_python_script`, `execute_python_script`, `validate_python_script`, `list_python_scripts`, `delete_python_script`, `generate_and_execute_python_script`, `docker_execute_python` | Ein Shot: Code rein, Output raus. Keine Persistenz (s.u.) |

**Wichtige Design-Entscheidung `exec_python`:**
Die heutigen 6 Script-Lifecycle-Tools (`generate_/execute_/validate_/list_/delete_python_script` + `generate_and_execute_python_script`) behandeln Scripts als persistente Artefakte. Das war bei älteren LLMs ohne Parallelität sinnvoll; heute ist der saubere Ansatz:

- **Ephemeral by default**: LLM übergibt Code, bekommt Output. Kein Speichern.
- **Wenn ein Name oder Namensraum nötig ist** (z. B. Skill-System), übernimmt das eine **separate Skill/Snippet-Registry** — aber das ist kein LLM-Tool, sondern ein UI-Feature.
- Validierung (`validate_python_script`) wird Teil des Exec-Pipelines: vor Ausführung syntaxcheck, Fehler sofort zurück — eingebettet statt eigener Tool-Call.

Falls „named scripts" *wirklich* gebraucht werden (ProjectHub-Integration?): **Vor Phase 4 entscheiden**. Falls ja: 1 zusätzliches Tool `exec_python(script_name="foo")` das nach `~/.claude-scripts/foo.py` auflöst — immer noch nur 1 Tool, keine 6.

**Nicht angefasst:** `run_team` (`team_tools.py`) — eigenständiger Sub-Agent-Entry, anderer Layer.

### 1.4 Generischer Fetch (1)

| Tool | Parameter | Ersetzt | Kommentar |
|---|---|---|---|
| `web_fetch` | `url`, `extract_mode?` (`markdown`/`text`/`html`), `max_length?`, `headers?`, `verify_ssl?` | `fetch_webpage`, `http_request` | Generischer HTTP-GET mit Extraction |

**Nicht angefasst:** `internal_fetch`, `internal_fetch_section`, `internal_search` — sind Intranet/Auth-spezifisch ⇒ **Domain** (bleiben).

---

## 2 Vollständiges Before/After-Mapping

| Heutiges Tool | Datei:Zeile | Zielort | Typ |
|---|---|---|---|
| `read_file` | `tools.py:600` | `read` | Signatur angepasst |
| `read_pdf_pages` | `tools.py:1509` | `read` (pages=) | Handler intern |
| `read_sqlj_file` | `tools.py:1894` | `read` (ext=.sqlj) | Handler intern |
| `get_pdf_info` | `tools.py:1481` | `read` (meta=true) oder bleibt als Hilfstool? | **Entscheidung nötig** |
| `batch_read_files` | `meta_tools.py` | **entfällt** | Parallel-Calls |
| `write_file` | `tools.py:1042` | `write` | Signatur identisch |
| `create_directory` | `tools.py:1139` | `write` (content="", path endet auf /) | Handler intern |
| `batch_write_files` | `meta_tools.py` | **entfällt** | Parallel-Calls |
| `edit_file` | `tools.py:1197` | `edit` | Rename |
| `list_files` | `tools.py:791` | `ls` | Rename |
| `glob_files` | `tools.py:877` | `glob` | Rename |
| `grep_content` | `tools.py:934` | `grep` | Rename |
| `combined_search` | `meta_tools.py:517` | **entfällt** | Parallel-Calls |
| `search_code` | `tools.py:2157` | `search(scope="code")` | Handler intern |
| `search_handbook` | `tools.py:2181` | `search(scope="handbook")` | Handler intern |
| `search_skills` | `tools.py:2197` | `search(scope="skills")` | Handler intern |
| `search_pdf` | `tools.py:2377` | `search(scope="pdf")` | Handler intern |
| `run_workspace_command` | `command_tools.py:278` | `bash(sandbox="workspace")` | Default-Mode |
| `shell_execute` | `shell_tools.py:893` | `bash(sandbox="container")` | Handler-Pfad |
| `shell_execute_local` | `shell_tools.py:941` | `bash(sandbox="local")` | Handler-Pfad |
| `shell_list_executions` | `shell_tools.py:974` | `bash_sessions(action="list")` | Lifecycle |
| `run_pytest` | `test_exec_tools.py` | `bash` (pytest CLI) | LLM ruft direkt |
| `run_npm_tests` | `test_exec_tools.py` | `bash` (npm/yarn CLI) | LLM ruft direkt |
| `docker_execute_python` | `docker_tools.py` | `exec_python(sandbox="container")` | Default |
| `docker_session_create` | `docker_tools.py` | `bash(sandbox="container", new_session=true)` | Return `session_id` |
| `docker_session_execute` | `docker_tools.py` | `bash(sandbox="container", session_id=…)` | Wiederverwendung |
| `docker_session_list` | `docker_tools.py` | `bash_sessions(action="list")` | Lifecycle |
| `docker_session_close` | `docker_tools.py` | `bash_sessions(action="close", session_id=…)` | Lifecycle |
| `docker_upload_file` | `docker_tools.py` | `bash_sessions(action="upload", session_id=…, file=…)` | Lifecycle |
| `docker_list_packages` | `docker_tools.py` | `bash(command="pip list", sandbox="container")` | LLM ruft direkt |
| `podman_build_image` | `docker_tools.py` | `bash("podman build …")` | LLM ruft direkt |
| `podman_list_images` | `docker_tools.py` | `bash("podman images")` | LLM ruft direkt |
| `podman_remove_image` | `docker_tools.py` | `bash("podman rmi …")` | LLM ruft direkt |
| `generate_python_script` | `script_tools.py` | `exec_python` oder **Skill-Registry** (UI-Feature) | Eingeschmolzen |
| `execute_python_script` | `script_tools.py` | `exec_python(script_name=…)` falls Skill-Registry | Eingeschmolzen |
| `validate_python_script` | `script_tools.py` | `exec_python`-interner Syntax-Check vor Ausführung | Eingeschmolzen |
| `list_python_scripts` | `script_tools.py` | **UI**, nicht LLM-Tool | Raus aus Registry |
| `delete_python_script` | `script_tools.py` | **UI**, nicht LLM-Tool | Raus aus Registry |
| `generate_and_execute_python_script` | `script_tools.py` | `exec_python` | Eingeschmolzen |
| `fetch_webpage` | `internal_fetch_tools.py` | `web_fetch` | Rename |
| `http_request` | `internal_fetch_tools.py` | `web_fetch` (method="POST" etc. optional) | Erweitern |
| `get_project_paths` | `tools.py:355` | **bleibt** | Config-Lookup, zu klein für Dispatch |
| `suggest_answers` | `tools.py:3043` | **bleibt** | UI-Interaktion |

**Beibehalten (lokal, aber Domain/speziell):**
- `trace_java_references`, `debug_java_with_testdata` (Java-Analyse)
- `graph_*` (lokale Code-KG)
- `compile_files`, `validate_file` (Build-spezifisch)
- `junit_*` (Test-Gen)
- `knowledge_tools.py` (Confluence/Index-Cache)
- `team_tools.py::run_team` (Multi-Agent)

---

## 3 Migrations-Plan (5 Phasen)

### Phase 1 — Foundation (0.5 Tag, null Risiko)

**Ziel:** Infrastruktur für Alias-Layer und Telemetrie.

1. **Tool-Call-Telemetrie einbauen** in `tool_executor.py`:
   - Log-Event pro Tool-Aufruf → `chats/tool_usage.jsonl`: `{ts, tool_name, session_id, duration_ms, success}`.
   - Läuft 2 Wochen im Hintergrund mit; liefert harte Daten: „Welche der 40 Tools wird tatsächlich aufgerufen?"

2. **Alias-Mechanismus** in `ToolRegistry`:
   ```python
   def register_alias(self, old_name: str, new_name: str,
                      deprecation_msg: str | None = None): ...
   ```
   Alter Name routet zum neuen Handler, Deprecation-Log beim Aufruf.

3. **Schema-Snapshot** vor jeder Phase als Regressions-Baseline (Eval-Golden).

**Kein Breaking, keine Tool-Änderung.** Nur Tooling.

### Phase 2 — File I/O + Search zusammenlegen (1 Tag)

**Scope:** `read`, `write`, `edit`, `ls`, `glob`, `grep`, `search`.

1. Neue Handler in `tools.py` implementieren (interner Dispatch auf heutige Helper).
2. Alte Tools als Alias registrieren (`read_file → read` usw.).
3. System-Prompt-Template aktualisieren (nur neue Namen beschreiben).
4. `batch_read_files` / `batch_write_files` / `combined_search` deprecaten (nicht entfernen), Deprecation-Warning.

**Test:**
- Regressions-Eval auf 50 letzten Chats; Erfolgsrate muss ≥ heute bleiben.
- `pytest tests/` grün.

**Rollback:** Alias-Layer zurücknehmen, alte Handler bleiben.

### Phase 3 — Exec zusammenlegen (1.5 Tage, mittleres Risiko)

**Scope:** `bash`, `bash_sessions`, `exec_python`.

1. Neuer `bash`-Handler im `command_tools.py` oder neuer `exec_tools.py`:
   - `sandbox="workspace"` ⇒ heutiger `run_workspace_command` Pfad.
   - `sandbox="local"` ⇒ heutiger `shell_execute_local` Pfad.
   - `sandbox="container"` ⇒ heutiger `shell_execute`/`docker_session_execute` Pfad.
   - Session-Handling: `new_session=true` → Session-ID zurück; `session_id=X` → weiterverwenden.
2. `bash_sessions` als eigenes kleines Tool (nicht in `bash` überladen).
3. `exec_python` implementieren:
   - Wenn `session_id` leer: One-Shot-Container + Code.
   - Wenn `session_id` gesetzt: Session-Exec.
   - Syntax-Validierung eingebaut (ersetzt `validate_python_script`).
4. Aliasse für alle alten Namen.
5. `run_pytest` / `run_npm_tests` / `docker_list_packages` / `podman_*`: **NICHT** als Alias, sondern im System-Prompt als „benutze `bash('pytest …')`" beschreiben; altes Tool deprecaten.

**Test:**
- Regressions-Eval speziell auf Exec-Chats.
- Streaming/Cancel-Funktionalität aus v2.37.31-36 muss in `bash` erhalten bleiben — **nicht** neu implementieren, denselben Streaming-Path wiederverwenden.

**Risiko:** Streaming-Pfad (run_workspace_command) ist erst kürzlich stabilisiert. In Phase 3 **nur wrappen, nichts am Streaming-Code ändern**.

### Phase 4 — Fetch + PDF-Meta (0.5 Tag)

**Scope:** `web_fetch`, Entscheidung zu `get_pdf_info`.

1. `web_fetch` als dünner Wrapper um heutiges `fetch_webpage`; Methoden-Parameter für `http_request`-Fall.
2. `get_pdf_info`: Zwei Optionen:
   - **A (empfohlen):** Bleibt als Tool — PDF-Metadaten-Abfrage ist spezifisch genug, um nicht in `read` zu verstecken.
   - **B:** `read(path="…", meta=true)` — dispatched zu Meta-Handler.
   - Entscheidung: Telemetrie aus Phase 1 heranziehen. Wenn `get_pdf_info` <5× pro Woche aufgerufen wird: A ist ehrlicher (sichtbar klein).

### Phase 5 — Dekommissionieren (1 Tag, braucht Kalender-Zeit)

Erst nach **30 Tagen Koexistenz** der neuen + deprecated Tools:

1. Telemetrie-Check: Werden deprecated Tools noch aufgerufen? (LLM sollte längst nur neue Namen kennen.)
2. Aliasse entfernen, alte Handler löschen.
3. Dead Code aufräumen: Gelöschte Tool-Registrierungen in `create_default_registry()` (`tools.py:3083`).
4. `meta_tools.py` reduziert sich deutlich — prüfen, ob ganze Datei entfallen kann.
5. Version-Bump: v2.41.0 oder v3.0.0 (Semver: Breaking Change im Tool-Interface → Major? Intern-API → Minor).

---

## 4 Risiken und Abfedern

| Risiko | Eintritt | Gegenmaßnahme |
|---|---|---|
| Bestehende Chats/Prompts verwenden alte Namen | sehr hoch | Alias-Layer über 30 Tage; Deprecation-Warnings ins Tool-Result einspeisen |
| LLM wählt nach Umbenennung falsch | mittel | Regressions-Eval auf 50 Chats pro Phase; vor Rollout Prompt-Tuning |
| Script-Registry geht verloren | mittel | Vor Phase 3 klären: gibt es persistente Named-Scripts, die UI/User nutzen? Falls ja: Skill-Registry-Refactor zuerst |
| Streaming-Regression in `bash` | mittel | Phase 3 **wrappt** nur, ändert den Streaming-Code nicht |
| Docker-Session-User verlieren offene Sessions | niedrig | Bei Alias: Session-IDs bleiben kompatibel |
| `search` mit `scope=all` macht Dinge doppelt | niedrig | Kein `scope=all`-Default; explizit scope oder erster Hit |

---

## 5 Erwartete Effekte

### Quantitativ

- **Tool-Schema im System-Prompt:** 40 lokal + 140 Domain → **11 lokal + 140 Domain**. Schätzung: **−8 bis −15 k Tokens** pro Request (lokale Tools sind die parameter-reichsten).
- **Tool-Dateien lokal:** 6 Module (`tools.py`, `shell_tools.py`, `command_tools.py`, `docker_tools.py`, `meta_tools.py`, `script_tools.py`, `test_exec_tools.py`, `internal_fetch_tools.py`) → konsolidiert in 3–4.
- **LOC-Reduktion:** ~1500–2000 Zeilen (Duplicate-Logik + Alt-Handler).

### Qualitativ

- **Tool-Select-Accuracy:** LLM wählt aus 11 statt 40 lokalen Tools — messbar besser.
- **Onboarding:** „Lies die Datei" = `read`, ohne Entscheidung zwischen 7 Varianten.
- **Claude-Code-/OpenCode-Kompatibilität:** Der neue Core ist 1:1 abbildbar; falls später ein MCP-Server von AI-Assist gebaut werden soll, sind die Namen „muttersprachlich".

### Was sich nicht ändert

- Domain-Tools (~140) komplett unverändert.
- Feature-Funktionalität: keine Capability entfällt.
- Sub-Agent- und Multi-Agent-Flows.

---

## 6 Offene Entscheidungen vor Start

1. **Named Python Scripts behalten?** — Wenn ja, welches UI-Feature hängt daran? (→ bestimmt Design von `exec_python`.)
2. **`get_pdf_info` eigenständig oder in `read`?** — Telemetrie-Frage.
3. **Versionierung:** v2.41.0 (interne Refactor-Version) oder v3.0.0 (Breaking-Tool-API)?
4. **Rollout-Fenster:** Parallel-Phase wie lange? 30 Tage (Vorschlag) vs. 14 Tage?
5. **Alias-Format im Deprecation-Log:** Nur Log, oder auch in Tool-Result für LLM sichtbar? (Sichtbar macht LLM lernfähig, kostet aber Tokens.)

---

## 7 Empfohlene Reihenfolge für heute

1. Telemetrie + Alias-Infrastruktur (Phase 1) — **morgen starten, 2 Wochen laufen lassen**.
2. Mit den Daten: Entscheidungen aus §6 treffen.
3. Phase 2 starten, wenn Datenlage klar.

**Nicht heute beginnen:** Phase 2 ohne Phase-1-Telemetrie. Die Telemetrie-Daten entscheiden, welche Tools tatsächlich gebraucht werden — ohne sie ist jede Konsolidierung Bauchgefühl.
