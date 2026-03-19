# Tool-Optimierung Architektur-Design v1.0

> Design-Dokument: Intelligente Tool-Nutzung & Analyse-Optimierung
> Erstellt: 2026-03-19
> Status: DESIGN PHASE

---

## 1. Executive Summary

Dieses Design adressiert vier Kernprobleme:
1. **Endlosschleifen**: Agent dreht sich bei Problemen im Kreis
2. **Informationsauswertung**: Tool-Ergebnisse werden nicht sinnvoll bewertet
3. **Halluzinationen**: Aussagen ohne Quellenreferenz
4. **Confluence-Integration**: Suboptimale Nutzung der Wiki-Inhalte

### Design-Entscheidungen (aus Requirements)
- Max. 3 gleiche Tool-Calls → Stuck-Detection
- Relevanz-Score < 0.3 → Ergebnis verwerfen
- `analysis_model` für Tool-Result-Zusammenfassungen
- Max. 9 Confluence-Seiten pro Query

---

## 2. Komponenten-Architektur

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATOR                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ Tool Budget  │  │ Tool Cache   │  │ Analytics    │              │
│  │ (existiert)  │  │ (existiert)  │  │ (existiert)  │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│         │                │                   │                      │
│  ┌──────┴────────────────┴───────────────────┴──────┐              │
│  │                                                    │              │
│  │         ┌─────────────────────────────┐           │              │
│  │         │    ToolProgressTracker      │  ◄── NEU  │              │
│  │         │    (Stuck-Detection)        │           │              │
│  │         └─────────────────────────────┘           │              │
│  │                      │                             │              │
│  │         ┌─────────────────────────────┐           │              │
│  │         │    ResultValidator          │  ◄── NEU  │              │
│  │         │    (Relevanz + Quellen)     │           │              │
│  │         └─────────────────────────────┘           │              │
│  │                      │                             │              │
│  │         ┌─────────────────────────────┐           │              │
│  │         │    ResultSummarizer         │  ◄── NEU  │              │
│  │         │    (LLM-basiert)            │           │              │
│  │         └─────────────────────────────┘           │              │
│  │                                                    │              │
│  └────────────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         SUB-AGENTS                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ WikiAgent    │  │ CodeExplorer │  │ JiraAgent    │              │
│  │ (enhanced)   │  │              │  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│         │                │                   │                      │
│  ┌──────┴────────────────┴───────────────────┴──────┐              │
│  │         ┌─────────────────────────────┐           │              │
│  │         │  SubAgentCoordinator        │  ◄── NEU  │              │
│  │         │  (Dedup + Ranking)          │           │              │
│  │         └─────────────────────────────┘           │              │
│  └────────────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Neue Komponenten

### 3.1 ToolProgressTracker

**Datei**: `app/agent/tool_progress.py`

**Zweck**: Erkennt Endlosschleifen und "Stuck"-Situationen

```python
@dataclass
class ToolCallSignature:
    """Signatur eines Tool-Calls für Vergleiche."""
    tool_name: str
    args_hash: str  # MD5 der sortierten Args
    result_hash: str  # MD5 der ersten 500 Zeichen des Results
    timestamp: float

@dataclass
class ProgressState:
    """Fortschritts-Status für eine Session."""
    call_signatures: List[ToolCallSignature] = field(default_factory=list)
    knowledge_gained: Set[str] = field(default_factory=set)  # Unique Findings
    stuck_counter: int = 0
    last_progress_iteration: int = 0

class ToolProgressTracker:
    """
    Trackt Tool-Aufrufe und erkennt Stuck-Situationen.

    Stuck-Detection Logik:
    1. Gleiche Tool + Args + Result 3x → STUCK
    2. 5 Iterationen ohne neues Wissen → STUCK
    3. Zyklische Pattern-Erkennung (A→B→A→B)
    """

    STUCK_THRESHOLD = 3  # Max gleiche Calls
    NO_PROGRESS_THRESHOLD = 5  # Max Iterationen ohne neues Wissen

    def record_call(
        self,
        tool_name: str,
        args: Dict,
        result: ToolResult,
        iteration: int
    ) -> StuckDetectionResult:
        """
        Zeichnet einen Tool-Call auf und prüft auf Stuck.

        Returns:
            StuckDetectionResult mit:
            - is_stuck: bool
            - reason: str (warum stuck)
            - suggestion: str (was tun)
        """

    def extract_knowledge(self, result: ToolResult) -> Set[str]:
        """
        Extrahiert "Wissen" aus einem Tool-Ergebnis.

        Für search_code: gefundene Dateipfade
        Für confluence: Seiten-IDs
        Für read_file: Funktions-/Klassennamen
        """

    def get_stuck_hint(self) -> str:
        """
        Generiert einen Hinweis für den System-Prompt wenn stuck.

        Beispiel:
        "⚠️ LOOP ERKANNT: Du hast search_code('getUserById') 3x
        mit gleichem Ergebnis aufgerufen. Versuche:
        1. Andere Suchbegriffe
        2. read_file für bereits gefundene Dateien
        3. Fasse zusammen was du bisher weißt"
        """
```

**Integration in Orchestrator** (orchestrator.py:2627):

```python
# Nach Tool-Ausführung
result = await self.tools.execute(tool_call.name, **tool_call.arguments)

# NEU: Progress-Tracking
stuck_result = self._progress_tracker.record_call(
    tool_call.name,
    tool_call.arguments,
    result,
    iteration
)

if stuck_result.is_stuck:
    # Stuck-Hinweis in Messages injizieren
    messages.append({
        "role": "system",
        "content": stuck_result.get_hint()
    })
    # Event für Frontend
    yield AgentEvent(AgentEventType.STUCK_DETECTED, {
        "reason": stuck_result.reason,
        "suggestion": stuck_result.suggestion
    })
```

---

### 3.2 ResultValidator

**Datei**: `app/agent/result_validator.py`

**Zweck**: Bewertet Relevanz von Tool-Ergebnissen und erzwingt Quellenangaben

```python
@dataclass
class ValidationResult:
    """Ergebnis der Validierung."""
    relevance_score: float  # 0.0 - 1.0
    should_use: bool  # score >= 0.3
    source_metadata: SourceMetadata
    summary: Optional[str]  # Gekürzt wenn > 2000 tokens

@dataclass
class SourceMetadata:
    """Strukturierte Quelleninformation."""
    source_type: str  # "confluence", "code", "handbook", etc.
    source_id: str  # Page-ID, Dateipfad, etc.
    source_title: str  # Lesbare Bezeichnung
    source_url: Optional[str]
    excerpt_start: int  # Zeile/Position
    excerpt_end: int

class ResultValidator:
    """
    Validiert und bewertet Tool-Ergebnisse.

    Relevanz-Scoring:
    - TF-IDF Match zwischen Query und Result
    - Keyword-Overlap
    - Strukturelle Qualität (Hat Headers, Code-Blöcke etc.)
    """

    RELEVANCE_THRESHOLD = 0.3
    MAX_TOKENS_BEFORE_SUMMARY = 2000

    def __init__(self, llm_client, analysis_model: str):
        self.llm = llm_client
        self.model = analysis_model

    def validate(
        self,
        tool_name: str,
        query: str,
        result: ToolResult
    ) -> ValidationResult:
        """
        Validiert ein Tool-Ergebnis.

        1. Berechnet Relevanz-Score via TF-IDF
        2. Extrahiert Source-Metadata
        3. Kürzt wenn nötig via LLM-Summary
        """

    def _calculate_relevance(self, query: str, content: str) -> float:
        """
        TF-IDF basiertes Relevanz-Scoring.

        Kein LLM-Call - rein algorithmus-basiert für Performance.
        """
        # Tokenize
        query_tokens = set(self._tokenize(query))
        content_tokens = self._tokenize(content)

        # TF: Wie oft erscheinen Query-Tokens im Content?
        tf_score = sum(1 for t in content_tokens if t in query_tokens)
        tf_normalized = tf_score / max(len(content_tokens), 1)

        # IDF: Bonus für seltene Tokens
        # (vereinfacht: Query-Tokens die im Content vorkommen)
        idf_score = len(query_tokens & set(content_tokens)) / len(query_tokens)

        return min(1.0, (tf_normalized + idf_score) / 2 * 1.5)

    def _extract_source_metadata(
        self,
        tool_name: str,
        result: ToolResult
    ) -> SourceMetadata:
        """
        Extrahiert strukturierte Quelleninformationen.

        Für search_confluence: Page-ID, Titel, URL
        Für read_file: Pfad, Zeilennummern
        Für search_code: Dateipfad, Match-Zeilen
        """

    async def _summarize_if_needed(
        self,
        content: str,
        query: str
    ) -> Optional[str]:
        """
        LLM-basierte Zusammenfassung für große Ergebnisse.

        Verwendet analysis_model für Konsistenz.
        Prompt fokussiert auf Query-relevante Informationen.
        """
        tokens = estimate_tokens(content)
        if tokens <= self.MAX_TOKENS_BEFORE_SUMMARY:
            return None

        prompt = f"""Fasse folgendes Tool-Ergebnis zusammen.
Fokus auf Informationen relevant für: {query}

INHALT:
{content[:8000]}

ZUSAMMENFASSUNG (max 500 Wörter, behalte wichtige Details):"""

        response = await self.llm.chat_simple(
            prompt,
            model=self.model,
            max_tokens=800
        )
        return response
```

**Integration** (orchestrator.py nach Tool-Result):

```python
# Nach Tool-Ausführung
result = await self.tools.execute(tool_call.name, **tool_call.arguments)

# NEU: Validierung
validation = await self._result_validator.validate(
    tool_call.name,
    user_message,  # Original Query
    result
)

if not validation.should_use:
    logger.debug(f"[agent] Ergebnis verworfen (score={validation.relevance_score})")
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": f"[NIEDRIGE RELEVANZ - Score: {validation.relevance_score:.2f}] "
                   f"Ergebnis scheint nicht relevant. Versuche andere Suchbegriffe."
    })
    continue

# Gekürzte Version verwenden wenn vorhanden
content_to_inject = validation.summary or result.to_context()

# Source-Metadata hinzufügen
source_info = validation.source_metadata
content_with_source = f"""[QUELLE: {source_info.source_type} | {source_info.source_title}]
{source_info.source_url or source_info.source_id}

{content_to_inject}"""

messages.append({
    "role": "tool",
    "tool_call_id": tool_call.id,
    "content": content_with_source
})
```

---

### 3.3 ResultSummarizer (Erweiterung von ConversationSummarizer)

**Datei**: `app/core/conversation_summarizer.py` (erweitern)

```python
class ConversationSummarizer:
    # ... bestehender Code ...

    # NEU: Tool-Result Summarization
    TOOL_RESULT_PROMPT = """Fasse dieses Tool-Ergebnis zusammen.

KONTEXT: Der User fragt nach "{query}"
TOOL: {tool_name}

ERGEBNIS:
{content}

ZUSAMMENFASSUNG (max 300 Wörter):
1. Relevante Informationen für die Frage
2. Wichtige Details (Zahlen, Namen, Pfade)
3. Was NICHT gefunden wurde (falls relevant)

Format: Strukturierte Bullet-Points mit Quellenreferenzen."""

    async def summarize_tool_result(
        self,
        tool_name: str,
        result_content: str,
        query: str,
        max_tokens: int = 500
    ) -> str:
        """
        Fasst ein Tool-Ergebnis zusammen.

        Verwendet analysis_model für Konsistenz mit dem Haupt-Analyse-Flow.
        """
        model = settings.llm.analysis_model or settings.llm.default_model

        prompt = self.TOOL_RESULT_PROMPT.format(
            query=query,
            tool_name=tool_name,
            content=result_content[:6000]  # Limit für Prompt
        )

        return await self._call_llm_simple(prompt, model, max_tokens)
```

---

### 3.4 SubAgentCoordinator

**Datei**: `app/agent/sub_agent_coordinator.py`

**Zweck**: Deduplizierung und Ranking von Sub-Agent-Ergebnissen

```python
@dataclass
class RankedFinding:
    """Ein geranktes Finding mit Quelleninformation."""
    content: str
    source_agent: str
    source_id: str
    relevance_score: float
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None

class SubAgentCoordinator:
    """
    Koordiniert Sub-Agent-Ergebnisse.

    Features:
    1. Deduplizierung ähnlicher Findings
    2. Relevanz-Ranking
    3. Cross-Agent-Synthese
    4. Early-Exit bei hoher Konfidenz
    """

    SIMILARITY_THRESHOLD = 0.85  # Für Duplikat-Erkennung
    HIGH_CONFIDENCE_THRESHOLD = 0.9  # Für Early-Exit

    def __init__(self):
        self._findings: List[RankedFinding] = []

    async def process_results(
        self,
        results: List[SubAgentResult],
        query: str
    ) -> CoordinatedResult:
        """
        Verarbeitet Sub-Agent-Ergebnisse.

        1. Extrahiert alle Findings
        2. Berechnet Relevanz-Scores
        3. Erkennt Duplikate via Similarity
        4. Rankt nach Relevanz
        5. Erstellt synthetisierte Zusammenfassung
        """

    def _detect_duplicates(
        self,
        findings: List[RankedFinding]
    ) -> List[RankedFinding]:
        """
        Erkennt semantisch ähnliche Findings.

        Verwendet Jaccard-Similarity auf Token-Ebene.
        Schneller als Embedding-basierte Methoden.
        """

    def _create_synthesis(
        self,
        ranked_findings: List[RankedFinding],
        query: str
    ) -> str:
        """
        Erstellt eine synthetisierte Zusammenfassung.

        Format:
        ## Ergebnisse zu "{query}"

        ### Aus Code (3 Treffer)
        - [Datei.java:45] Klasse UserService implements...

        ### Aus Confluence (2 Treffer)
        - [Seite: API Docs] Beschreibt getUserById Endpunkt...

        ### Aus Jira (1 Treffer)
        - [PROJ-123] Bug: getUserById wirft NPE bei...
        """
```

**Integration in Orchestrator** (_run_sub_agents_phase):

```python
async def _run_sub_agents_phase(self, ...):
    # ... bestehender Code bis results = await dispatcher.dispatch_selected(...) ...

    # NEU: Koordination statt direkter Injektion
    coordinator = SubAgentCoordinator()
    coordinated = await coordinator.process_results(results, user_message)

    # Deduplizierte, gerankte Ergebnisse
    context_block = coordinated.to_context_block()

    # Statistiken für Frontend
    yield AgentEvent(AgentEventType.SUBAGENT_SYNTHESIS, {
        "total_findings": coordinated.total_findings,
        "unique_findings": coordinated.unique_findings,
        "duplicates_removed": coordinated.duplicates_removed,
        "top_source": coordinated.top_source
    })
```

---

### 3.5 Enhanced WikiAgent

**Datei**: `app/agent/sub_agents/wiki_agent.py` (erweitern)

```python
class WikiAgent(SubAgent):
    name = "wiki_agent"
    display_name = "Wiki-Agent"

    # NEU: Intelligentere Beschreibung
    description = """Du durchsuchst Confluence-Wiki-Seiten.

STRATEGIE:
1. ZUERST search_confluence mit präzisen Keywords
2. Bewerte Ergebnisse nach Relevanz (Titel-Match > Excerpt-Match)
3. Lies NUR die Top-3 relevantesten Seiten vollständig
4. Bei jedem read_confluence_page: Extrahiere KONKRETE Fakten

WICHTIG:
- Zitiere immer [Seiten-ID: Titel] bei Fakten
- Lies NICHT mehr als 9 Seiten (Budget-Limit)
- Bei <3 relevanten Treffern: Melde "wenig gefunden" statt weitersuchen
"""

    # NEU: Relevanz-basierte Seiten-Auswahl
    async def _rank_search_results(
        self,
        results: List[Dict],
        query: str
    ) -> List[Dict]:
        """
        Rankt Confluence-Suchergebnisse nach Relevanz.

        Scoring:
        - Titel enthält Query-Term: +0.5
        - Excerpt enthält Query-Term: +0.3
        - Space ist relevant (konfiguriert): +0.2
        """
        scored = []
        query_terms = set(query.lower().split())

        for result in results:
            score = 0.0
            title_lower = result.get("title", "").lower()
            excerpt_lower = result.get("excerpt", "").lower()

            # Titel-Match
            title_matches = sum(1 for t in query_terms if t in title_lower)
            score += 0.5 * (title_matches / len(query_terms))

            # Excerpt-Match
            excerpt_matches = sum(1 for t in query_terms if t in excerpt_lower)
            score += 0.3 * (excerpt_matches / len(query_terms))

            scored.append({**result, "_relevance": score})

        return sorted(scored, key=lambda x: x["_relevance"], reverse=True)
```

---

## 4. Datenfluss-Diagramm

```
User Query
    │
    ▼
┌─────────────────────────────────────────────────────┐
│                  ORCHESTRATOR                        │
│                                                      │
│  1. Sub-Agents parallel starten                      │
│     ├── WikiAgent ──────────┐                        │
│     ├── CodeExplorer ───────┼──► SubAgentCoordinator │
│     └── JiraAgent ──────────┘         │              │
│                                       ▼              │
│  2. Koordinierte Ergebnisse ◄─────────┘              │
│     (dedupliziert, gerankt)                          │
│                                                      │
│  3. Main Agent Loop                                  │
│     ┌─────────────────────────────────────┐         │
│     │  Tool Call                          │         │
│     │      │                              │         │
│     │      ▼                              │         │
│     │  ToolProgressTracker                │         │
│     │      │ (Stuck-Detection)            │         │
│     │      ▼                              │         │
│     │  Tool Execute                       │         │
│     │      │                              │         │
│     │      ▼                              │         │
│     │  ResultValidator                    │         │
│     │      │ (Relevanz + Sources)         │         │
│     │      ▼                              │         │
│     │  ResultSummarizer (wenn > 2000 tok) │         │
│     │      │                              │         │
│     │      ▼                              │         │
│     │  Context Injection mit Quellenref   │         │
│     └─────────────────────────────────────┘         │
│                                                      │
│  4. Final Response mit Quellenangaben               │
└─────────────────────────────────────────────────────┘
    │
    ▼
Response mit [QUELLE: ...] Referenzen
```

---

## 5. Neue Events (AgentEventType)

```python
class AgentEventType(str, Enum):
    # ... bestehende Events ...

    # NEU: Progress & Stuck
    STUCK_DETECTED = "stuck_detected"  # Agent dreht sich im Kreis
    PROGRESS_UPDATE = "progress_update"  # Neues Wissen gewonnen

    # NEU: Validation
    RESULT_VALIDATED = "result_validated"  # Ergebnis bewertet
    RESULT_DISCARDED = "result_discarded"  # Ergebnis verworfen (low relevance)

    # NEU: Sub-Agent Coordination
    SUBAGENT_SYNTHESIS = "subagent_synthesis"  # Koordinierte Ergebnisse
```

---

## 6. Konfiguration (config.yaml)

```yaml
tool_optimization:
  # Stuck-Detection
  stuck_threshold: 3  # Max gleiche Tool-Calls
  no_progress_threshold: 5  # Max Iterationen ohne Wissen

  # Relevanz-Validierung
  relevance_threshold: 0.3  # Min Score für Ergebnis-Nutzung
  max_tokens_before_summary: 2000  # Ab wann LLM-Summary

  # Sub-Agent Koordination
  similarity_threshold: 0.85  # Für Duplikat-Erkennung
  max_confluence_pages: 9  # Max Seiten pro WikiAgent

  # Modelle
  summarization_model: null  # null = analysis_model verwenden
```

---

## 7. Implementierungs-Reihenfolge

### Phase 1: Stuck-Detection (Prio: HOCH)
1. `ToolProgressTracker` implementieren
2. Integration in Orchestrator Loop
3. Frontend-Event für STUCK_DETECTED

### Phase 2: Result-Validierung (Prio: HOCH)
1. `ResultValidator` mit TF-IDF Scoring
2. Source-Metadata Extraktion
3. Integration in Tool-Result-Handling

### Phase 3: Result-Summarization (Prio: MITTEL)
1. Erweiterung ConversationSummarizer
2. Async LLM-Call mit analysis_model
3. Token-basierte Entscheidung

### Phase 4: Sub-Agent Koordination (Prio: MITTEL)
1. `SubAgentCoordinator` implementieren
2. Duplikat-Erkennung via Similarity
3. Synthese-Generierung

### Phase 5: WikiAgent Enhancement (Prio: NIEDRIG)
1. Relevanz-Ranking für Suchergebnisse
2. Angepasste System-Prompts
3. Budget-bewusste Strategie

---

## 8. Testplan

### Unit Tests
- `test_tool_progress.py`: Stuck-Detection Szenarien
- `test_result_validator.py`: TF-IDF Scoring, Source-Extraction
- `test_sub_agent_coordinator.py`: Duplikat-Erkennung, Ranking

### Integration Tests
- `test_orchestrator_stuck.py`: Vollständiger Loop mit Stuck-Erkennung
- `test_confluence_optimization.py`: WikiAgent mit Ranking

### Manuelle Tests
- Confluence-Query mit vielen Ergebnissen → Ranking prüfen
- Wiederholte Suche → Stuck-Detection prüfen
- Lange Tool-Ergebnisse → Summary-Qualität prüfen

---

## 9. Metriken & Monitoring

### Neue Analytics-Felder
```python
# In analytics_logger.py
await self._analytics.log_optimization_metrics({
    "stuck_detections": count,
    "results_discarded": count,
    "results_summarized": count,
    "duplicates_removed": count,
    "avg_relevance_score": float,
})
```

### Dashboard-Indikatoren
- Stuck-Rate pro Session
- Durchschnittlicher Relevanz-Score
- Summary-Ratio (wie oft wird gekürzt)
- Duplikat-Rate bei Sub-Agents

---

## 10. Risiken & Mitigationen

| Risiko | Wahrscheinlichkeit | Auswirkung | Mitigation |
|--------|-------------------|------------|------------|
| TF-IDF zu simpel für Relevanz | Mittel | Falsche Verwerfung | Threshold anpassbar, Fallback auf alles durchlassen |
| LLM-Summary zu langsam | Niedrig | Latenz | Async, nur bei >2000 Tokens |
| Stuck-Detection false positives | Mittel | Vorzeitiger Abbruch | 3 Calls Threshold, Args-Hash prüfen |
| Duplikat-Erkennung ungenau | Niedrig | Relevante Infos fehlen | Similarity bei 0.85 konservativ |

---

**Nächster Schritt**: `/sc:implement --phase 1` für Stuck-Detection oder `/sc:implement --full` für komplette Implementierung.
