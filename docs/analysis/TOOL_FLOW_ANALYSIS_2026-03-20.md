# Tool-Flow Analyse - Optimierungspotenziale

**Datum**: 2026-03-20
**Analysiert von**: Architect Persona
**Datenquelle**: `chains.jsonl` (Produktionsdaten vom 2026-03-19)

---

## Executive Summary

Die Analyse von 37 Produktions-Chains zeigt erhebliches Optimierungspotenzial in drei Bereichen:

| Problem | Impact | Lösung | Einsparung |
|---------|--------|--------|------------|
| Redundante Tool-Calls | 30-70% der Calls unnötig | Smart-Deduplication | ~50% weniger Calls |
| Falsche Tool-Auswahl | 15-20% Fehler-Calls | Context-aware Selection | ~15% weniger Fehler |
| Fehlende Parallelisierung | Sequentielle Wartezeit | Async Batch-Calls | ~40% schneller |

---

## 1. Detaillierte Findings aus chains.jsonl

### 1.1 Redundante Tool-Aufrufe (KRITISCH)

**Chain c_60b19986** (21 Iterationen, resolved):
```
Step 7-14:  8x github_search_code → sequentiell statt parallel
Step 20:    github_search_code erneut (gleiche Daten)
```
**Problem**: 7 redundante Calls, ~4000ms verschwendet

**Chain c_689ced15** (30 Iterationen, TIMEOUT):
```
Step 1-8:   8x search_confluence + read_confluence_page abwechselnd
Step 9-24:  16x search_code mit minimalen Results (14-17 Tokens)
Step 26:    read_confluence_page mit 83.873 Tokens (!) → Überlastung
```
**Problem**: Endlos-Suche ohne Fortschritt, dann massive Page-Ladung

**Chain c_bed51ca1** (30 Iterationen, TIMEOUT):
```
Step 5-9:   5x list_database_tables (exakt gleiche Ergebnisse!)
Step 15:    search_pdf mit 40.137ms (!) → blockiert alles
```
**Problem**: Gleicher Call 5x wiederholt, langsamer PDF-Call blockiert

**Chain c_84453c99** (27 Iterationen, TIMEOUT):
```
Step 1-27:  16x search_confluence, 4x search_code gemischt
```
**Problem**: Sucht endlos in Confluence ohne Early-Exit

### 1.2 Falsche Tool-Auswahl

**Pattern: GitHub vs. Lokal**
```
Chain c_1d2a3b3b Step 3:  read_file → ERROR → dann github_get_file → SUCCESS
Chain c_d9abe5b2 Step 2:  read_file → ERROR → dann github_get_file → SUCCESS
Chain c_d6ac024a Step 2:  read_file → ERROR → dann github_get_file → SUCCESS
```
**Root Cause**: Bei GitHub-PRs wird fälschlich `read_file` (lokal) statt `github_get_file` gewählt

**Pattern: Kaputte Tools werden trotzdem aufgerufen**
```
search_handbook: 6x aufgerufen, 6x ERROR (100% Fehlerrate!)
analyze_java_class: 2x aufgerufen, 2x ERROR
```
**Root Cause**: Keine Blacklist für konsistent fehlerhafte Tools

### 1.3 Erfolgreiche Early-Termination (Positiv-Beispiele)

```
Chain c_0118e790: 1 Tool-Call (github_pr_diff) → resolved in 6.704ms
Chain c_ee9d14a2: 1 Tool-Call (github_pr_diff) → resolved in 7.226ms
Chain c_265041dd: 1 Tool-Call (github_pr_diff) → resolved in 9.452ms
```
**Erkenntnis**: Bei wiederholter gleicher Anfrage lernt das System, früher abzubrechen

### 1.4 Parallelisierungs-Potenzial

**Aktuelle sequentielle Muster:**
```
search_confluence → read_confluence_page  (könnte parallel mit anderen Suchen)
github_search_code → github_get_file      (mehrere Files parallel ladbar)
search_code → search_code → search_code   (alle 3 parallel möglich)
```

---

## 2. Architektur-Schwachstellen

### 2.1 Orchestrator: Keine parallele Tool-Ausführung

**Aktuell** (`orchestrator.py`):
```python
for tc in tool_calls[:self.max_tool_calls_per_iter]:
    result = await self.tools.execute(tool_name, **args)  # Sequentiell!
```

**Verbesserung**:
```python
# Parallel wenn keine Abhängigkeiten
independent_calls = [tc for tc in tool_calls if not has_write_dependency(tc)]
results = await asyncio.gather(*[
    self.tools.execute(tc.name, **tc.args) for tc in independent_calls
])
```

### 2.2 ToolProgressTracker: Stuck-Detection zu spät

**Aktuell**: Erkennt Stuck erst nach 3 identischen Calls oder 5 Iterationen ohne Fortschritt

**Problem**: Bei 30 Iterationen werden ~27 unnötige Calls gemacht

**Verbesserung**:
- "No Progress" Threshold: 5 → 3
- Pattern-basierte Early-Exit: Wenn 3x search ohne neue Erkenntnisse → STOP
- Tool-Blacklist wenn Fehlerrate > 80%

### 2.3 Fehlende Intent-basierte Tool-Auswahl

**Aktuell**: LLM wählt Tools frei → oft falsche Wahl

**Verbesserung**: Intent-Router vor Tool-Auswahl:
```python
class IntentRouter:
    def select_tools(self, query: str, context: dict) -> List[str]:
        if "github" in context.get("active_repos", []):
            return ["github_*"]  # Nur GitHub-Tools
        if context.get("is_pr_analysis"):
            return ["github_pr_diff", "github_get_file"]  # Kein read_file!
```

---

## 3. Konkrete Optimierungsvorschläge

### 3.1 Kurzfristig (1-2 Tage)

#### A) Tool-Blacklist für fehlerhafte Tools
```python
# In tool_progress.py
TOOL_BLACKLIST = {
    "search_handbook": "consistently_failing",  # 100% Fehlerrate
}

def should_skip_tool(self, tool_name: str) -> bool:
    return tool_name in self.TOOL_BLACKLIST
```

#### B) Aggressivere Stuck-Detection
```python
# In tool_progress.py
STUCK_THRESHOLD = 2  # War: 3
NO_PROGRESS_THRESHOLD = 3  # War: 5
EMPTY_STREAK_THRESHOLD = 2  # War: 3
```

#### C) Context-aware Tool Selection für PRs
```python
# In orchestrator.py
def _select_pr_tools(self, tool_calls: List[ToolCall]) -> List[ToolCall]:
    """Ersetzt lokale Tools durch GitHub-Tools bei PR-Context."""
    pr_context = self._detect_pr_context()
    if pr_context:
        return [
            self._map_to_github_tool(tc) if tc.name in ("read_file",) else tc
            for tc in tool_calls
        ]
    return tool_calls
```

### 3.2 Mittelfristig (1-2 Wochen)

#### D) Parallele Tool-Ausführung
```python
# In orchestrator.py
async def _execute_tools_parallel(self, tool_calls: List[ToolCall]) -> List[ToolResult]:
    # Gruppiere nach Abhängigkeiten
    read_only = [tc for tc in tool_calls if tc.name.startswith(("search_", "read_", "get_", "list_"))]
    write_ops = [tc for tc in tool_calls if tc not in read_only]

    # Read-only parallel, Write-ops sequentiell
    read_results = await asyncio.gather(*[
        self._execute_single_tool(tc) for tc in read_only
    ])

    write_results = []
    for tc in write_ops:
        write_results.append(await self._execute_single_tool(tc))

    return read_results + write_results
```

#### E) Intelligente Result-Deduplication
```python
# In tool_progress.py
def is_redundant_call(self, tool_name: str, args: Dict) -> bool:
    """Prüft ob dieser Call redundante Daten liefern würde."""
    signature = self._hash_args(args)

    # Bereits mit gleichen Args aufgerufen?
    if signature in self._seen_signatures:
        return True

    # Ähnliche Suche bereits durchgeführt?
    if tool_name.startswith("search_"):
        query = args.get("query", "")
        for prev_query in self._seen_queries:
            if self._jaccard_similarity(query, prev_query) > 0.8:
                return True

    return False
```

### 3.3 Langfristig (Sprint)

#### F) Intent-basierte Orchestrierung
```
User Query → Intent Classifier → Tool Selector → Parallel Executor → Result Aggregator
                   ↓
           [pr_analysis, code_search, documentation, ...]
                   ↓
           Dedizierte Tool-Sets pro Intent
```

#### G) Learning-basierte Optimierung
- Erfolgreiche Chains analysieren (1 Tool → resolved)
- Patterns extrahieren: "Bei PR-Anfragen: github_pr_diff first"
- Auto-Tuning der Thresholds basierend auf Feedback

---

## 4. Erwartete Verbesserungen

| Metrik | Aktuell | Nach Optimierung |
|--------|---------|------------------|
| Durchschn. Iterationen | 12.3 | ~6 |
| Timeout-Rate | 16% (6/37) | <5% |
| Redundante Calls | ~40% | <10% |
| Durchschn. Duration | 65s | ~35s |
| Token-Verbrauch | 100% | ~60% |

---

## 5. Implementierungs-Reihenfolge

1. **Tool-Blacklist** (30 Min) - Sofortige Verbesserung
2. **Aggressivere Stuck-Detection** (1h) - Schneller Timeout-Fix
3. **PR-Context Tool-Mapping** (2h) - Eliminiert read_file Fehler
4. **Parallele Read-Only Tools** (4h) - 40% Speedup
5. **Result-Deduplication** (4h) - 50% weniger redundante Calls

---

## Anhang: Analysierte Chains

| Chain ID | Iterations | Duration | Status | Hauptproblem |
|----------|------------|----------|--------|--------------|
| c_689ced15 | 30 | 144s | timeout | Endlos-Confluence-Suche |
| c_84453c99 | 27 | 121s | timeout | 16x search_confluence |
| c_bed51ca1 | 30 | 173s | timeout | 5x redundant list_database |
| c_60b19986 | 21 | 88s | resolved | 8x sequentiell github_search |
| c_0118e790 | 1 | 6s | resolved | ✓ Optimal |
| c_ee9d14a2 | 1 | 7s | resolved | ✓ Optimal |
