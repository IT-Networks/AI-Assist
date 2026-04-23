# AI-Assist Tool-Inventar: Vergleich & Konsolidierungs-Empfehlung

**Datum:** 2026-04-22
**Version:** AI-Assist v2.40.1
**Scope:** Sollten die 185 Basis-Tools auf einen schmalen Kern (read, write, grep, exec …) reduziert werden?
**Confidence:** Hoch für I/O- und Exec-Konsolidierung; Mittel für Domain-Tool-Strategie

---

## TL;DR

**Ja — aber differenziert.** Die AI-Assist-Tool-Landschaft (185 Tools / 27 Dateien) ist gegenüber modernen Referenz-Systemen (Claude Code ~30 Tools, OpenCode 12 Tools, Cursor ~6) massiv überdimensioniert. **Aggressive Konsolidierung lohnt bei File-I/O, Exec und generischer Suche**; domänen­spezifische Enterprise-Tools (ALM, ServiceNow, Jira, IQ, MQ, Webex, WLP …) sollten **nicht ersatzlos gestrichen**, sondern architektonisch in drei Schichten organisiert werden: Unified Core → Domain Packs → MCP-Adapter.

**Erwartete Wins:** ~20–30 k Token weniger im System-Prompt, höhere Tool-Select-Accuracy, weniger Redundanz (`read_file` vs. `batch_read_files` vs. `read_confluence_page` …), weniger Wartungsaufwand.

---

## 1 Aktueller Zustand AI-Assist (Inventar)

**185 eindeutige Tools in 27 Modulen** (Stand v2.40.1, automatisch extrahiert aus `app/agent/*_tools.py`).

### 1.1 Kategorisierung

| Kategorie | Beispiel-Tools | Anzahl | Bewertung |
|---|---|---:|---|
| **File I/O (Core)** | `read_file`, `write_file`, `edit_file`, `list_files`, `glob_files`, `create_directory`, `batch_read_files`, `batch_write_files` | 8 | Granularität zu hoch |
| **Search (Code+Content)** | `search_code`, `grep_content`, `combined_search`, `search_handbook`, `search_skills`, `search_pdf`, `graph_search` | 7 | Überlappend |
| **Execution** | `run_workspace_command`, `shell_execute`, `shell_execute_local`, `shell_list_executions`, `execute_python_script`, `generate_and_execute_python_script`, `validate_python_script`, `docker_execute_python`, `docker_session_*` (5), `podman_*` (3), `run_pytest`, `run_npm_tests`, `run_team` | 20 | Stark zersplittert |
| **Git/GitHub** | `git_status/diff/log/blame/show_commit/…` (7), `github_*` (11) | 18 | OK, getrennt nach lokal/remote |
| **PDF / Doc** | `search_pdf`, `get_pdf_info`, `read_pdf_pages`, `read_sqlj_file` | 4 | Legitim |
| **Database** | `query_database`, `list_database_tables`, `describe_database_table`, `execute_confirmed_query` | 4 | OK |
| **Web / HTTP** | `fetch_webpage`, `internal_fetch`, `internal_fetch_section`, `internal_search`, `http_request`, `web_search` | 6 | Überlappend |
| **Enterprise: ALM** | `alm_*` | 20 | CRUD-Explosion |
| **Enterprise: ServiceNow** | `search_servicenow_*`, `query_servicenow_*` | 6 | OK |
| **Enterprise: Confluence/Jira** | `search_confluence`, `read_confluence_page`, `list_confluence_pdfs`, `read_confluence_pdf`, `search_jira`, `read_jira_issue` | 6 | OK |
| **Enterprise: Jenkins/Maven/WLP/IQ** | `jenkins_*` (5), `maven_*` (4), `wlp_*` (13), `iq_*` (7) | 29 | WLP zu fein |
| **Enterprise: Messaging/Mail/Webex** | `mq_*` (3), `email_*` (11), `webex_*` (9) | 23 | Mail zu fein |
| **Tests / Compile / JUnit** | `junit_*` (4), `compile_files`, `validate_file`, `run_pytest`, `run_npm_tests` | 7 | OK |
| **SOAP/API** | `wsdl_info`, `soap_request`, `rest_api`, `test_*` (6 für spezifische SOAP-Services) | 10 | Mix aus generisch und speziell |
| **Graph (Code-KG)** | `graph_impact/context/find_path/search/dependents` | 5 | Domain-legitim |
| **Script-Gen/Exec** | `generate_python_script`, `execute_python_script`, `list_python_scripts`, `validate_python_script`, `delete_python_script`, `generate_and_execute_python_script` | 6 | Zu viele CRUD-Operationen |
| **Meta / Misc** | `combined_search`, `batch_read_files`, `batch_write_files`, `suggest_answers`, `run_team`, `my_tool` | 6 | `my_tool` = Dead Code? |

### 1.2 Beobachtete Redundanzen

| Redundanz | Tools | Beobachtung |
|---|---|---|
| Shell-Ausführung | `run_workspace_command`, `shell_execute`, `shell_execute_local`, `shell_list_executions` | 4 Tools für im Kern dieselbe Operation mit unterschiedlichen Sandboxes |
| Python-Script-Lifecycle | `generate_python_script`, `execute_python_script`, `validate_python_script`, `list_python_scripts`, `delete_python_script`, `generate_and_execute_python_script` | CRUD-artig — LLM könnte mit 1–2 Tools auskommen |
| Grep/Search | `grep_content`, `combined_search`, `search_code`, `log_grep`, `github_search_code` | Fünf Grep-Varianten, je nach Scope |
| Fetch | `internal_fetch`, `internal_fetch_section`, `http_request`, `fetch_webpage` | Wesentlich ein HTTP-GET mit Post-Processing-Flags |
| Read | `read_file`, `batch_read_files`, `read_pdf_pages`, `read_confluence_page`, `read_jira_issue`, `read_sqlj_file`, `read_confluence_pdf` | Sieben Lese-Pfade |
| Batch-Wrapper | `batch_read_files`, `batch_write_files` | Moderne LLMs rufen Tools parallel — Batch-Wrapper meist überflüssig |

---

## 2 Referenz-Systeme

### 2.1 Claude Code (Anthropic, 2026)

**~30 eingebaute Tools**, stark kategorisiert und orthogonal:

```
File:      Read, Write, Edit, NotebookEdit
Search:    Grep, Glob
Exec:      Bash, PowerShell, Monitor (background + streaming)
Web:       WebFetch, WebSearch
Task/Plan: TaskCreate, TaskGet, TaskList, TaskUpdate, TodoWrite,
           EnterPlanMode, ExitPlanMode
Agents:    Agent, SendMessage, TeamCreate, TeamDelete
Schedule:  CronCreate, CronDelete, CronList
Worktree:  EnterWorktree, ExitWorktree
LSP/MCP:   LSP, ListMcpResourcesTool, ReadMcpResourceTool
Meta:      Skill, ToolSearch, AskUserQuestion
```

Kern-Prinzipien:
- **Orthogonalität**: Keine zwei Tools decken dieselbe Operation ab.
- **Domain-Tools → MCP-Server**: Jira, Confluence, ServiceNow etc. werden nicht eingebaut, sondern als externe MCP-Server angebunden.
- **ToolSearch**: Selten benötigte Tools sind „deferred“ — nur Name ist sichtbar, Schema wird bei Bedarf nachgeladen. (Genau der Mechanismus, den diese Session gerade nutzt.)

[Quelle: code.claude.com/docs/en/tools-reference]

### 2.2 OpenCode (sst)

**12 Tools, bewusst minimalistisch:**

```
File:   read, write, edit, list
Search: grep, glob
Exec:   bash (persistent shell + WASM-Parser zur Pfad-Inspektion)
Net:    webfetch (HTML→Markdown via Turndown)
Tasks:  todowrite, todoread, task (sub-agent)
```

[Quelle: deepwiki.com/sst/opencode/5.3-built-in-tools-reference]

### 2.3 Cursor Agent

- `list_dir`, `codebase_search` (semantisch), `grep`, Terminal-Exec
- Git über Bash, keine eigenen Git-Tools
- **Semantische Suche** statt Dutzender spezialisierter Search-Tools

### 2.4 Aider

Tool-agnostisch: zwei Modi (Architect/Editor) statt Tool-Zoo; Git ist die API. Kein direkter Vergleich zur Tool-Granularität, aber Beleg dafür, dass *keine* Tools nötig sind, wenn Ein-/Ausgabe-Format stark strukturiert ist.

---

## 3 Vergleichsmatrix

| Dimension | AI-Assist | Claude Code | OpenCode | Cursor |
|---|---:|---:|---:|---:|
| **Tool-Anzahl** | 185 | ~30 | 12 | ~6 |
| **Read-Varianten** | 7 | 1 | 1 | 1 |
| **Exec-Varianten** | 20 | 2 (Bash + PowerShell + Monitor) | 1 | 1 |
| **Grep-Varianten** | 5 | 1 | 1 | 1 + semantisch |
| **Fetch-Varianten** | 4 | 1 | 1 | 0 (via bash) |
| **Domain-Tools eingebaut?** | Ja (100+) | Nein (MCP) | Nein | Nein |
| **Lazy-Loading?** | Nein | Ja (ToolSearch) | Nein | Nein |
| **Schema-Budget im Prompt** | hoch (geschätzt 30–50 k Tokens) | mittel (<10 k Core) | niedrig | niedrig |

---

## 4 Pro/Contra Konsolidierung

### 4.1 Pro Vereinheitlichung

1. **Token-Budget.** 185 Tool-Schemas im System-Prompt kosten grob 30–50 k Tokens pro Request (abhängig von Beschreibungen + Parameter-Schemas). Das ist pro-Call-Kosten *und* Cache-Druck.
2. **Decision-Quality.** LLMs wählen bei >50 Tools messbar schlechter. Claude Code bringt ToolSearch nicht aus ästhetischen Gründen.
3. **Wartungsaufwand.** 27 Tool-Dateien mit teils überlappendem Verhalten = viele Stellen für denselben Bugfix.
4. **Testbarkeit.** Weniger Tools = weniger Integrationstests. Heute hängt jedes Tool am System (DB, Confluence, Jira …).
5. **Konzeptuelle Klarheit.** Jemand, der ein neues LLM anschließt, versteht `read/write/grep/bash` sofort. `alm_create_test_lab_folder` — nicht.

### 4.2 Contra (warum nicht alles zu 8 Tools kollabieren)

1. **Enterprise-Tools tragen Domain-Wissen.** `alm_search_tests` liefert strukturiert (ID, Owner, Status, Folder) — ein LLM mit rohem `http_request` müsste das pro Call neu extrahieren. Kostet Tokens und ist unzuverlässig.
2. **Session-Management.** `test_login`, `alm_switch_project`, `wlp_server_status` verwalten Zustand, den ein raw-HTTP-Ansatz ins LLM-Gedächtnis verlagern würde.
3. **Bestehende Skills/Workflows.** Der ProjectHub UI und Multi-Agent-Teams referenzieren konkrete Tool-Namen; jede Umbenennung ist eine Breaking Change.
4. **Spezifische Reasoning-Tools** wie `trace_java_references`, `debug_java_with_testdata`, `graph_impact` sind *keine* Wrapper — sie sind eigenständige Analyse-Primitive.

---

## 5 Empfehlung: 3-Schichten-Architektur

### Schicht 1 — **Unified Core (10–12 Tools, immer geladen)**

Direkt an Claude Code / OpenCode angelehnt:

| Tool | Ersetzt | Parameter-Hinweis |
|---|---|---|
| `read` | `read_file`, `read_pdf_pages`, `read_sqlj_file`, `batch_read_files` | `path`, optional `pages`/`offset+limit`, dispatcht auf PDF/Binary automatisch |
| `write` | `write_file`, `create_directory`, `batch_write_files` | Erzeugt Parent-Dirs implizit |
| `edit` | `edit_file` | Behalten wie ist |
| `glob` | `glob_files` | Behalten |
| `grep` | `grep_content`, `combined_search`, `search_code`, `log_grep`, `github_search_code` | `pattern`, optional `scope=fs/log/github` |
| `bash` | `run_workspace_command`, `shell_execute`, `shell_execute_local`, `run_pytest`, `run_npm_tests` | Eine sandbox-konfigurierbare Exec-Engine |
| `web_fetch` | `fetch_webpage`, `internal_fetch`, `internal_fetch_section`, `http_request` | Dispatcht auf intern/extern über URL |
| `web_search` | `web_search` | Behalten |
| `task` / `run_team` | `run_team`, sub-agent Aufrufe | Einheitlicher Sub-Agent-Entry |
| `suggest_answers` | Behalten | UI-Interaktion, legitim singular |
| `tool_search` / Registry-Lookup | *neu* | Lazy-Loader für Schicht 2 |

**Erwartet:** 185 → ~11 Core-Tools. Alles I/O + Exec-Redundanz weg.

### Schicht 2 — **Domain Packs (lazy-loaded, namespaced)**

Gruppiert nach Geschäftsdomäne, geladen wenn Prompt Keywords matched **oder** explizit:

```
code/     : trace_java_references, debug_java_with_testdata, graph_*, read_sqlj_file
git/      : git_status, git_diff, git_log, git_blame, …   (7)
github/   : github_list_prs, github_pr_diff, …            (11)
db/       : query_database, list_database_tables, …      (4)
build/    : maven_*, jenkins_*, wlp_* (konsolidiert), compile_files, junit_*
enterprise/ alm_* (konsolidiert 20→~6), servicenow_*, iq_*
docs/     : confluence_*, jira_*, knowledge_*
comm/     : email_*, webex_*, mq_*
soap/     : wsdl_info, soap_request, rest_api, test_*
script/   : generate_and_execute_python_script (ein Tool statt 6)
```

**Konsolidierungs-Kandidaten innerhalb der Packs:**

| Pack | Heute | Ziel |
|---|---:|---:|
| `alm/` | 20 CRUD-Tools | 6–8 (`alm_search`, `alm_read`, `alm_write`, `alm_list_folders`, `alm_context`) |
| `wlp/` | 13 | 5–6 (Server-Lifecycle + Config + Logs + Deploy zusammenfassen) |
| `email/` | 11 | 5–6 |
| `script/` | 6 | 1–2 (nur `exec_python` + `list_snippets`) |

**Mechanismus:** Tool-Registry markiert Packs als `deferred`. LLM sieht im System-Prompt nur die Pack-Namen + 1-Liner; volles Schema wird via `tool_search` nachgeladen — identisch zu dem, was diese Session gerade erlebt.

### Schicht 3 — **MCP-Adapter (mittelfristig)**

Wirklich externe Systeme (ServiceNow, Jenkins, IQ, Webex, HP ALM, SAP) sollten zu **MCP-Servern** werden:
- Prozess-Isolation (Crash in ServiceNow-Adapter killt nicht den Agent)
- Config pro Server statt pro Tool
- Wiederverwendbar von anderen Agents/Claude Code etc.
- AI-Assist bleibt schlank, Domain-Code verlässt das Repo

**Nicht sofort** — nur für Systeme, wo ein MCP-Refactor durch andere Motivationen (Wiederverwendung, Isolation) sowieso gerechtfertigt ist.

---

## 6 Migrationspfad (inkrementell, risikoarm)

| Phase | Scope | Aufwand | Risiko |
|---|---|---|---|
| **P0 — Toter Code** | `my_tool` entfernen, nicht genutzte Tools identifizieren (Usage-Telemetrie ≥30 Tage) | 1 h | null |
| **P1 — Shell-Konsolidierung** | `shell_execute`, `shell_execute_local`, `run_workspace_command`, `run_pytest`, `run_npm_tests` → `bash` mit `sandbox=` Flag | 1–2 Tage | mittel (Breaking für bestehende Prompts) |
| **P2 — Read/Write-Konsolidierung** | `read_file` + `batch_read_files` + `read_pdf_pages` + `read_sqlj_file` → `read` mit Content-Type-Dispatch | 1 Tag | niedrig |
| **P3 — Grep/Fetch-Konsolidierung** | Siehe Schicht 1 | 1 Tag | niedrig |
| **P4 — Script-Tool-Kollaps** | 6 Script-Tools → 2 (exec + list) | 0.5 Tage | niedrig |
| **P5 — Domain-Pack-Registry** | Lazy-Load-Mechanismus einbauen (Keyword-Trigger + explicit load) | 3–5 Tage | mittel (Architektur) |
| **P6 — ALM/WLP/Email-Kollaps** | 20 ALM-Tools → 6–8, 13 WLP → 6, 11 Email → 6 | 2–3 Tage je Pack | mittel |
| **P7 — MCP-Extraktion** | Wenn es sich lohnt: ServiceNow/IQ/Jenkins als MCP-Server | ≥1 Woche je System | hoch |

**Breaking-Change-Strategie:** Pro Phase Alias-Layer (alter Tool-Name → neuer Handler) für 1–2 Versionen, dann Deprecation-Warnung, dann Entfernung. LLM-seitig: Prompt-Update + Eval-Run.

---

## 7 Konkrete Quick-Wins (diese Woche)

Ohne Architekturänderung sofort machbar:

1. **`my_tool` entfernen** aus `tools.py` (Dead Code, Z. 3083).
2. **`batch_read_files` / `batch_write_files` deprecaten** — moderne LLMs rufen `read`/`write` parallel. Ein Eval-Run auf bestehenden Chats bestätigt, ob noch verwendet.
3. **Shell-Tool Dedup**: `shell_execute_local` vs. `run_workspace_command` auf dieselbe Sandbox-Backend-Methode routen (keine Tool-Entfernung, nur Code-Kollaps).
4. **Script-Tools konsolidieren**: `generate_python_script` + `execute_python_script` → nur noch `generate_and_execute_python_script` (existiert schon); andere deprecaten.
5. **Tool-Counts loggen**: Pro Session zählen, welche Tools wirklich aufgerufen werden — nach 30 Tagen harte Daten für Phase-1-Entscheidungen.

---

## 8 Antwort auf die Ausgangsfrage

> „Sollte ich diese vereinheitlichen — bspw. nur read, write, grep, exec?"

**Für die Core-Tools: Ja, unbedingt.** Reduziere I/O + Exec + generische Suche von heute ~40 auf 8–10 orthogonale Tools. Das ist eine reine Qualitäts- und Kostenverbesserung.

**Für Domain-Tools: Nein, nicht ersatzlos** — aber **ja zu Lazy-Loading und Namespace-Gruppierung.** ALM, Jira, ServiceNow etc. tragen echtes Domain-Wissen, das ein LLM nicht aus rohem `http_request` rekonstruieren sollte. Stattdessen: Packs bilden, pro Pack konsolidieren (20 → 6), deferred laden.

**Die ehrliche Erkenntnis aus dem Vergleich:** Claude Code und OpenCode haben *nicht* weniger Fähigkeiten — sie haben die Fähigkeiten ausgelagert (MCP bei Claude Code, komplett weggelassen bei OpenCode). AI-Assist ist ein Agent *mit Batterien*, das ist Feature, nicht Bug. Aber die Batterien gehören besser organisiert, sonst zahlt man für jedes Feature bei jedem Request.

---

## Quellen

- [Claude Code Tools Reference](https://code.claude.com/docs/en/tools-reference)
- [OpenCode Built-in Tools Reference (DeepWiki)](https://deepwiki.com/sst/opencode/5.3-built-in-tools-reference)
- [OpenCode GitHub (sst/opencode)](https://github.com/sst/opencode)
- [Aider Architect/Editor Approach](https://aider.chat/2024/09/26/architect.html)
- [Cursor Agent Tools](https://cursor.com/docs/agent/tools)
- [Claude Code Tools — CallSphere Deep Dive](https://callsphere.tech/blog/claude-code-tool-system-explained)
- [Claude Code Tools Internal Implementation (Gist)](https://gist.github.com/bgauryy/0cdb9aa337d01ae5bd0c803943aa36bd)
- Lokale Quelle: `app/agent/*_tools.py` (automatische Extraktion, Stand 2026-04-22)
