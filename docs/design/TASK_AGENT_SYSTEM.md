# Task-Decomposition Agent System - Design Document

**Version:** 1.0
**Datum:** 2026-03-15
**Status:** Draft

---

## 1. Executive Summary

Dieses Design beschreibt ein neues Agent-System, das User-Anfragen intelligent in
spezialisierte Tasks zerlegt und diese mit optimalen Modellen und System-Prompts
ausfuehrt.

### Kernprinzipien

| Prinzip | Beschreibung |
|---------|--------------|
| **Minimale Zerlegung** | Nur zerlegen wenn noetig, 1 Task wenn ausreichend |
| **Spezialisierung** | Jeder Agent-Typ hat eigenes Model + System-Prompt |
| **Intelligente Abhaengigkeiten** | Warten wenn noetig, parallelisieren wenn moeglich |
| **Kontext-Weitergabe** | Nachfolgende Agenten bekommen vorherige Ergebnisse |
| **Graceful Degradation** | Retry -> anderer Ansatz -> User-Eingriff |

---

## 2. Architektur-Uebersicht

```
                                USER PROMPT
                                     |
                                     v
+===========================================================================+
|                           TASK PLANNER                                     |
|  Model: analysis_model (gptoss120b)                                       |
|  Aufgabe: Analysiert Anfrage, zerlegt in Tasks, definiert Abhaengigkeiten |
+---------------------------------------------------------------------------+
|  Output: TaskPlan {                                                        |
|    needs_clarification: bool,                                             |
|    clarification_questions: List[str],                                    |
|    tasks: List[Task]                                                      |
|  }                                                                         |
+===========================================================================+
                                     |
                    +----------------+----------------+
                    |                                 |
                    v                                 v
        [needs_clarification?]              [tasks vorhanden]
                    |                                 |
                    v                                 v
        Fragen an User senden              TASK EXECUTOR
        Auf Antwort warten                       |
        Erneut planen                            v
                                    +========================+
                                    |    EXECUTION LOOP      |
                                    |------------------------|
                                    | 1. Ready Tasks finden  |
                                    | 2. Parallel ausfuehren |
                                    | 3. Results speichern   |
                                    | 4. Abhaengige freigeben|
                                    | 5. Repeat bis leer     |
                                    +========================+
                                             |
                    +------------------------+------------------------+
                    |                        |                        |
                    v                        v                        v
          +----------------+       +----------------+       +----------------+
          | RESEARCH AGENT |       |   CODE AGENT   |       | ANALYST AGENT  |
          |----------------|       |----------------|       |----------------|
          | Model: qwen-7b |       | Model: code-   |       | Model: gptoss  |
          | Prompt: Recher-|       |        llama   |       | Prompt: Review |
          |         cheur  |       | Prompt: Coder  |       |         Bugs   |
          | Tools: search, |       | Tools: write,  |       | Tools: read,   |
          |        read    |       |        edit    |       |        grep    |
          +----------------+       +----------------+       +----------------+
                    |                        |                        |
                    +------------------------+------------------------+
                                             |
                                             v
                              +============================+
                              |   PHASE SYNTHESIZER        |
                              |----------------------------|
                              | Bei Phasenwechsel:         |
                              | Research -> Code           |
                              | Komprimiert Ergebnisse     |
                              | Model: analysis_model      |
                              +============================+
                                             |
                                             v
                              +============================+
                              |   FINAL SYNTHESIZER        |
                              |----------------------------|
                              | Alle Ergebnisse zusammen   |
                              | Kohaerente User-Antwort    |
                              | Model: analysis_model      |
                              +============================+
                                             |
                                             v
                                      USER RESPONSE
```

---

## 3. Datenmodelle

### 3.1 Task

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

class TaskType(str, Enum):
    RESEARCH = "research"      # Informationen suchen/lesen
    CODE = "code"              # Code schreiben/editieren
    ANALYST = "analyst"        # Code analysieren/reviewen
    DEVOPS = "devops"          # CI/CD, Deployment
    DOCUMENTATION = "docs"     # Dokumentation erstellen

class TaskStatus(str, Enum):
    PENDING = "pending"        # Wartet auf Ausfuehrung
    BLOCKED = "blocked"        # Wartet auf Abhaengigkeiten
    RUNNING = "running"        # Wird ausgefuehrt
    COMPLETED = "completed"    # Erfolgreich abgeschlossen
    FAILED = "failed"          # Fehlgeschlagen
    RETRY = "retry"            # Wird mit anderem Ansatz wiederholt

@dataclass
class Task:
    id: str                              # Eindeutige ID (z.B. "T1", "T2")
    type: TaskType                       # Agent-Typ
    description: str                     # Was soll gemacht werden
    depends_on: List[str] = field(default_factory=list)  # Task-IDs
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None         # Ergebnis nach Completion
    error: Optional[str] = None          # Fehler falls fehlgeschlagen
    retry_count: int = 0                 # Anzahl Retries
    context_from: List[str] = field(default_factory=list)  # Kontext-Injection

    @property
    def is_ready(self) -> bool:
        """Task ist bereit wenn alle Abhaengigkeiten completed sind."""
        return self.status == TaskStatus.PENDING and not self.depends_on
```

### 3.2 TaskPlan

```python
@dataclass
class TaskPlan:
    needs_clarification: bool = False
    clarification_questions: List[str] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)
    original_query: str = ""

    def get_ready_tasks(self, completed_ids: Set[str]) -> List[Task]:
        """Gibt alle Tasks zurueck die ausgefuehrt werden koennen."""
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            # Alle Abhaengigkeiten muessen completed sein
            if all(dep in completed_ids for dep in task.depends_on):
                ready.append(task)
        return ready

    def get_tasks_by_phase(self) -> Dict[TaskType, List[Task]]:
        """Gruppiert Tasks nach Typ fuer Phasen-Synthese."""
        phases = {}
        for task in self.tasks:
            if task.type not in phases:
                phases[task.type] = []
            phases[task.type].append(task)
        return phases
```

### 3.3 AgentConfig

```python
@dataclass
class AgentConfig:
    type: TaskType
    model: str                           # Primaeres Model
    fallback_model: str                  # Fallback wenn primaer nicht verfuegbar
    system_prompt: str                   # Spezialisierter Prompt
    tools: List[str]                     # Erlaubte Tool-Namen
    max_iterations: int = 5              # Max Tool-Loops
    temperature: float = 0.2             # LLM Temperature
    retry_strategy: str = "rephrase"     # Strategie bei Fehler
    max_retries: int = 3                 # Max Retry-Versuche
```

---

## 4. Agent-Konfigurationen

### 4.1 Research Agent

```python
RESEARCH_AGENT = AgentConfig(
    type=TaskType.RESEARCH,
    model="qwen-7b",
    fallback_model="default_model",
    system_prompt="""Du bist ein Recherche-Spezialist.

AUFGABE:
- Suche und sammle relevante Informationen
- Fasse Ergebnisse KOMPAKT zusammen (max 500 Woerter)
- Extrahiere die wichtigsten Fakten und Code-Beispiele

REGELN:
- Generiere KEINEN neuen Code
- Nur existierende Informationen sammeln
- Bei mehreren Quellen: Beste auswaehlen, nicht alle kopieren
- Strukturiere Ergebnisse klar mit Ueberschriften

OUTPUT-FORMAT:
## Zusammenfassung
[2-3 Saetze Kernaussage]

## Wichtige Findings
- Finding 1
- Finding 2

## Relevante Code-Snippets (falls vorhanden)
```code```

## Quellen
- [Pfad/ID 1]
- [Pfad/ID 2]
""",
    tools=[
        "search_code", "read_file", "grep_content",
        "search_confluence", "read_confluence_page",
        "search_handbook", "search_skills"
    ],
    max_iterations=5,
    temperature=0.1,
    retry_strategy="broaden_query"
)
```

### 4.2 Code Agent

```python
CODE_AGENT = AgentConfig(
    type=TaskType.CODE,
    model="codellama-34b",
    fallback_model="default_model",
    system_prompt="""Du bist ein Code-Generator und Entwickler.

AUFGABE:
- Schreibe sauberen, produktionsreifen Code
- Befolge Best Practices der jeweiligen Sprache
- Nutze den bereitgestellten Kontext aus vorherigen Tasks

QUALITAETSSTANDARDS:
- Type Hints (Python) / Generics (Java)
- Docstrings fuer oeffentliche Funktionen
- Error Handling mit spezifischen Exceptions
- Modulare Struktur, kleine Funktionen
- Keine Magic Numbers, Konstanten verwenden

CODE-STIL:
- Python: PEP 8, Black-kompatibel
- Java: Google Java Style
- Einrueckung: 4 Spaces

BEI FEHLERN:
- Analysiere den Fehler genau
- Erklaere was schiefging
- Korrigiere systematisch

AUSGABE:
Schreibe Code direkt via write_file/edit_file Tools.
Erklaere kurz was du gemacht hast.
""",
    tools=[
        "write_file", "edit_file", "read_file",
        "create_directory", "delete_file"
    ],
    max_iterations=8,
    temperature=0.2,
    retry_strategy="alternative_approach"
)
```

### 4.3 Analyst Agent

```python
ANALYST_AGENT = AgentConfig(
    type=TaskType.ANALYST,
    model="gptoss120b",
    fallback_model="default_model",
    system_prompt="""Du bist ein Code-Analyst und Reviewer.

AUFGABE:
- Analysiere Code auf Qualitaet, Bugs, Security
- Gib konkrete, umsetzbare Verbesserungsvorschlaege
- Priorisiere nach Schweregrad

ANALYSE-KATEGORIEN:
1. KRITISCH: Security-Luecken, Data Races, Memory Leaks
2. HOCH: Bugs, falsche Logik, fehlende Error Handling
3. MITTEL: Performance-Probleme, Code Smells
4. NIEDRIG: Style, Naming, Dokumentation

OUTPUT-FORMAT:
## Analyse: [Dateiname]

### Kritische Issues
- [Issue]: [Erklaerung] -> [Fix-Vorschlag]

### Verbesserungsvorschlaege
- [Suggestion]

### Positives
- [Was gut ist]
""",
    tools=[
        "read_file", "grep_content", "search_code"
    ],
    max_iterations=3,
    temperature=0.1,
    retry_strategy="different_perspective"
)
```

### 4.4 DevOps Agent

```python
DEVOPS_AGENT = AgentConfig(
    type=TaskType.DEVOPS,
    model="qwen-7b",
    fallback_model="default_model",
    system_prompt="""Du bist ein DevOps-Spezialist.

AUFGABE:
- CI/CD Pipelines konfigurieren
- Docker/Container-Operationen
- Deployment und Infrastruktur

SICHERHEIT:
- Keine Secrets im Code
- Minimal Privileges
- Sichere Defaults

AUSGABE:
Fuehre Operationen via Tools aus.
Dokumentiere was gemacht wurde.
""",
    tools=[
        "jenkins_build", "jenkins_status",
        "docker_build", "docker_run", "docker_logs",
        "shell_command"
    ],
    max_iterations=5,
    temperature=0.0,
    retry_strategy="check_prerequisites"
)
```

---

## 5. Komponenten-Design

### 5.1 TaskPlanner

```python
class TaskPlanner:
    """
    Analysiert User-Anfragen und zerlegt sie in ausfuehrbare Tasks.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.model = settings.llm.analysis_model or settings.llm.default_model

    async def plan(self, user_query: str, context: Optional[str] = None) -> TaskPlan:
        """
        Erstellt einen Ausfuehrungsplan fuer die User-Anfrage.

        Args:
            user_query: Die Anfrage des Users
            context: Optionaler Kontext (z.B. aus vorherigen Interaktionen)

        Returns:
            TaskPlan mit Tasks oder Klaerungsfragen
        """
        system_prompt = self._build_planner_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ]

        if context:
            messages.insert(1, {"role": "system", "content": f"Kontext:\n{context}"})

        response = await self.llm.chat_with_tools(
            messages=messages,
            model=self.model,
            temperature=0.1,
            max_tokens=2048
        )

        return self._parse_plan(response.content, user_query)

    def _build_planner_prompt(self) -> str:
        return """Du bist ein Task-Planner fuer ein KI-Assistenzsystem.

AUFGABE:
Analysiere die User-Anfrage und erstelle einen Ausfuehrungsplan.

REGELN:
1. Zerlege NUR wenn noetig - eine Task reicht oft aus
2. Definiere klare Abhaengigkeiten zwischen Tasks
3. Stelle Klaerungsfragen wenn die Anfrage unklar ist
4. Research-Tasks nur wenn explizit gefragt oder zwingend noetig

TASK-TYPEN:
- research: Informationen suchen (Code, Wiki, Docs)
- code: Code schreiben oder editieren
- analyst: Code analysieren/reviewen
- devops: CI/CD, Deployment, Docker
- docs: Dokumentation erstellen

OUTPUT (JSON):
{
  "needs_clarification": false,
  "clarification_questions": [],
  "tasks": [
    {
      "id": "T1",
      "type": "code",
      "description": "Tetris-Spiel in Python implementieren",
      "depends_on": []
    }
  ]
}

BEISPIEL - Einfache Anfrage:
User: "Schreibe eine Fibonacci-Funktion in Python"
-> 1 Task (code), keine Zerlegung noetig

BEISPIEL - Komplexe Anfrage mit Abhaengigkeit:
User: "Implementiere das Design aus unserem Wiki"
-> T1: research (Wiki durchsuchen)
-> T2: code (implementieren), depends_on: ["T1"]

BEISPIEL - Klaerung noetig:
User: "Baue eine App"
-> needs_clarification: true
-> questions: ["Welche Art App?", "Welche Sprache?"]
"""

    def _parse_plan(self, content: str, original_query: str) -> TaskPlan:
        """Parst die LLM-Antwort in einen TaskPlan."""
        try:
            # JSON aus Response extrahieren
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())

                tasks = []
                for t in data.get("tasks", []):
                    tasks.append(Task(
                        id=t["id"],
                        type=TaskType(t["type"]),
                        description=t["description"],
                        depends_on=t.get("depends_on", [])
                    ))

                return TaskPlan(
                    needs_clarification=data.get("needs_clarification", False),
                    clarification_questions=data.get("clarification_questions", []),
                    tasks=tasks,
                    original_query=original_query
                )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"[TaskPlanner] Parse error: {e}")

        # Fallback: Einzelne Code-Task
        return TaskPlan(
            tasks=[Task(
                id="T1",
                type=TaskType.CODE,
                description=original_query
            )],
            original_query=original_query
        )
```

### 5.2 TaskExecutor

```python
class TaskExecutor:
    """
    Fuehrt Tasks aus dem TaskPlan aus.
    Verwaltet Abhaengigkeiten, Parallelisierung und Retries.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        agents: Dict[TaskType, AgentConfig]
    ):
        self.llm = llm_client
        self.tools = tool_registry
        self.agents = agents
        self.results: Dict[str, str] = {}  # task_id -> result
        self.completed: Set[str] = set()

    async def execute(
        self,
        plan: TaskPlan,
        event_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Fuehrt alle Tasks im Plan aus.

        Args:
            plan: Der auszufuehrende TaskPlan
            event_callback: Optional Callback fuer Progress-Events

        Returns:
            Dict mit allen Task-Ergebnissen und Metadaten
        """
        self.results = {}
        self.completed = set()

        # Phasen tracken fuer Zwischen-Synthese
        current_phase: Optional[TaskType] = None
        phase_results: List[str] = []

        while True:
            # Ready Tasks finden
            ready = plan.get_ready_tasks(self.completed)

            if not ready:
                # Keine ready Tasks mehr
                pending = [t for t in plan.tasks if t.status == TaskStatus.PENDING]
                if pending:
                    # Es gibt noch pending Tasks aber keine ready -> Deadlock
                    logger.error("[TaskExecutor] Deadlock detected!")
                    break
                else:
                    # Alle Tasks abgeschlossen
                    break

            # Phasenwechsel erkennen (z.B. Research -> Code)
            new_phase = ready[0].type
            if current_phase and new_phase != current_phase:
                # Zwischen-Synthese durchfuehren
                if phase_results:
                    synthesis = await self._synthesize_phase(
                        current_phase, phase_results
                    )
                    # Synthese als Kontext fuer naechste Phase
                    for task in ready:
                        task.context_from.append(f"PHASE_{current_phase.value}")
                    self.results[f"PHASE_{current_phase.value}"] = synthesis
                phase_results = []

            current_phase = new_phase

            # Tasks parallel ausfuehren
            if event_callback:
                await event_callback("tasks_started", {
                    "tasks": [t.id for t in ready]
                })

            results = await asyncio.gather(*[
                self._execute_single_task(task)
                for task in ready
            ], return_exceptions=True)

            # Ergebnisse verarbeiten
            for task, result in zip(ready, results):
                if isinstance(result, Exception):
                    task.status = TaskStatus.FAILED
                    task.error = str(result)
                    logger.error(f"[TaskExecutor] Task {task.id} failed: {result}")
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
                    self.results[task.id] = result
                    self.completed.add(task.id)
                    phase_results.append(result)

                    if event_callback:
                        await event_callback("task_completed", {
                            "task_id": task.id,
                            "type": task.type.value
                        })

            # Update Abhaengigkeiten
            self._update_dependencies(plan)

        # Finale Synthese
        final = await self._final_synthesis(plan, self.results)

        return {
            "success": all(t.status == TaskStatus.COMPLETED for t in plan.tasks),
            "results": self.results,
            "final_response": final,
            "failed_tasks": [t.id for t in plan.tasks if t.status == TaskStatus.FAILED]
        }

    async def _execute_single_task(self, task: Task) -> str:
        """Fuehrt einen einzelnen Task mit dem passenden Agent aus."""
        agent_config = self.agents.get(task.type)
        if not agent_config:
            raise ValueError(f"No agent configured for type: {task.type}")

        # Kontext aus Abhaengigkeiten sammeln
        context = self._build_task_context(task)

        # Agent ausfuehren mit Retry-Logik
        return await self._run_agent_with_retry(
            agent_config, task.description, context, task
        )

    async def _run_agent_with_retry(
        self,
        config: AgentConfig,
        description: str,
        context: str,
        task: Task
    ) -> str:
        """Fuehrt Agent aus mit Retry-Strategie."""
        last_error = None

        for attempt in range(config.max_retries):
            try:
                # Model auswaehlen (mit Fallback)
                model = await self._select_model(config)

                result = await self._run_agent(
                    config, model, description, context, attempt
                )

                return result

            except Exception as e:
                last_error = e
                task.retry_count = attempt + 1
                task.status = TaskStatus.RETRY

                logger.warning(
                    f"[TaskExecutor] Task {task.id} attempt {attempt + 1} failed: {e}"
                )

                # Retry-Strategie anwenden
                if attempt < config.max_retries - 1:
                    description = self._apply_retry_strategy(
                        config.retry_strategy, description, str(e)
                    )

        # Alle Retries fehlgeschlagen
        raise RuntimeError(
            f"Task failed after {config.max_retries} attempts: {last_error}"
        )

    async def _select_model(self, config: AgentConfig) -> str:
        """Waehlt Model mit Fallback-Logik."""
        # Pruefen ob primaeres Model verfuegbar
        try:
            models = await self.llm.list_models()
            if config.model in models:
                return config.model
            elif config.fallback_model in models:
                logger.info(f"[TaskExecutor] Using fallback model: {config.fallback_model}")
                return config.fallback_model
            else:
                return settings.llm.default_model
        except Exception:
            return config.fallback_model or settings.llm.default_model

    def _build_task_context(self, task: Task) -> str:
        """Baut Kontext aus Abhaengigkeiten und vorherigen Phasen."""
        context_parts = []

        # Ergebnisse von Abhaengigkeiten
        for dep_id in task.depends_on:
            if dep_id in self.results:
                context_parts.append(f"## Ergebnis von {dep_id}:\n{self.results[dep_id]}")

        # Kontext von Phasen-Synthesen
        for ctx_ref in task.context_from:
            if ctx_ref in self.results:
                context_parts.append(f"## {ctx_ref}:\n{self.results[ctx_ref]}")

        return "\n\n".join(context_parts)

    def _apply_retry_strategy(
        self,
        strategy: str,
        description: str,
        error: str
    ) -> str:
        """Wendet Retry-Strategie an."""
        if strategy == "broaden_query":
            return f"{description}\n\nHINWEIS: Vorheriger Versuch fehlgeschlagen. Versuche breitere Suche."
        elif strategy == "alternative_approach":
            return f"{description}\n\nFEHLER: {error}\n\nVersuche einen anderen Ansatz."
        elif strategy == "different_perspective":
            return f"{description}\n\nAnalysiere aus anderer Perspektive. Vorheriger Fehler: {error}"
        else:
            return f"{description}\n\nRetry nach Fehler: {error}"
```

### 5.3 PhaseSynthesizer

```python
class PhaseSynthesizer:
    """
    Komprimiert Ergebnisse einer Phase fuer die naechste.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.model = settings.llm.analysis_model or settings.llm.default_model

    async def synthesize(
        self,
        phase: TaskType,
        results: List[str],
        max_tokens: int = 1000
    ) -> str:
        """
        Fasst Phase-Ergebnisse zusammen.

        Args:
            phase: Der Phasen-Typ (research, code, etc.)
            results: Liste der Task-Ergebnisse dieser Phase
            max_tokens: Maximale Laenge der Synthese

        Returns:
            Komprimierte Zusammenfassung
        """
        if not results:
            return ""

        if len(results) == 1:
            # Nur ein Ergebnis -> minimale Komprimierung
            return self._truncate(results[0], max_tokens)

        combined = "\n\n---\n\n".join(results)

        prompt = f"""Fasse die folgenden {phase.value}-Ergebnisse zusammen.

ERGEBNISSE:
{combined}

REGELN:
- Maximal {max_tokens} Tokens
- Behalte alle wichtigen Informationen
- Strukturiere klar mit Ueberschriften
- Bei Code: Wichtigste Snippets behalten
"""

        response = await self.llm.chat_with_tools(
            messages=[
                {"role": "system", "content": "Du bist ein Zusammenfassungs-Spezialist."},
                {"role": "user", "content": prompt}
            ],
            model=self.model,
            temperature=0.1,
            max_tokens=max_tokens
        )

        return response.content or self._truncate(combined, max_tokens)

    def _truncate(self, text: str, max_tokens: int) -> str:
        """Kuerzt Text auf max_tokens."""
        # Grobe Schaetzung: 1 Token ~ 4 Zeichen
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n\n[... gekuerzt ...]"
```

---

## 6. Konfigurations-Erweiterung

### 6.1 Settings-Erweiterung (config.py)

```python
class TaskAgentConfig(BaseModel):
    """Konfiguration fuer das Task-Agent-System."""

    enabled: bool = True

    # Modell-Zuweisungen pro Agent-Typ
    research_model: str = ""      # Leer = tool_model
    code_model: str = ""          # Leer = default_model
    analyst_model: str = ""       # Leer = analysis_model
    devops_model: str = ""        # Leer = tool_model

    # Fallback-Modell wenn spezifisches nicht verfuegbar
    fallback_model: str = ""      # Leer = default_model

    # Execution Settings
    max_parallel_tasks: int = 3   # Max parallele Task-Ausfuehrungen
    max_retries: int = 3          # Max Retries pro Task
    phase_synthesis: bool = True  # Zwischen-Synthese aktivieren

    # Task Planning
    auto_decompose: bool = True   # Automatische Task-Zerlegung
    min_tasks_for_parallel: int = 2  # Ab wann parallelisieren
```

### 6.2 Settings-Integration

```python
class Settings(BaseModel):
    # ... existing fields ...

    task_agents: TaskAgentConfig = Field(default_factory=TaskAgentConfig)
```

---

## 7. Integration in Orchestrator

### 7.1 Neuer Einstiegspunkt

```python
# In orchestrator.py

async def process_with_task_agents(
    self,
    session_id: str,
    user_message: str,
    model: Optional[str] = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Verarbeitet Anfrage mit Task-Agent-System.
    """
    if not settings.task_agents.enabled:
        # Fallback auf alten Flow
        async for event in self.process(session_id, user_message, model):
            yield event
        return

    state = self._get_state(session_id)

    # 1. Task Planning
    planner = TaskPlanner(central_llm_client)
    plan = await planner.plan(user_message)

    yield AgentEvent(AgentEventType.PLAN_CREATED, {
        "tasks": [{"id": t.id, "type": t.type.value, "description": t.description}
                  for t in plan.tasks],
        "needs_clarification": plan.needs_clarification
    })

    # 2. Klaerungsfragen?
    if plan.needs_clarification:
        yield AgentEvent(AgentEventType.CLARIFICATION_NEEDED, {
            "questions": plan.clarification_questions
        })
        return

    # 3. Task Execution
    executor = TaskExecutor(
        llm_client=central_llm_client,
        tool_registry=self.tools,
        agents=AGENT_CONFIGS
    )

    async def event_callback(event_type: str, data: dict):
        yield AgentEvent(AgentEventType.TASK_PROGRESS, {
            "event": event_type,
            **data
        })

    result = await executor.execute(plan, event_callback)

    # 4. Finale Antwort
    if result["success"]:
        yield AgentEvent(AgentEventType.TOKEN, result["final_response"])
    else:
        yield AgentEvent(AgentEventType.ERROR, {
            "failed_tasks": result["failed_tasks"],
            "message": "Einige Tasks sind fehlgeschlagen."
        })

    yield AgentEvent(AgentEventType.DONE, {})
```

---

## 8. Sequenzdiagramm

```
User          Orchestrator     TaskPlanner     TaskExecutor     Agents
  |                |                |                |             |
  |--- Request --->|                |                |             |
  |                |--- plan() ---->|                |             |
  |                |                |--- LLM call -->|             |
  |                |<-- TaskPlan ---|                |             |
  |                |                                 |             |
  |<- PLAN_CREATED-|                                 |             |
  |                |                                 |             |
  |                |---------- execute() ----------->|             |
  |                |                                 |             |
  |                |                                 |-- T1 (research)
  |<- TASK_PROGRESS|                                 |<--- result -|
  |                |                                 |             |
  |                |                                 |-- Synthesize|
  |                |                                 |             |
  |                |                                 |-- T2 (code) |
  |<- TASK_PROGRESS|                                 |<--- result -|
  |                |                                 |             |
  |                |<-------- final_response --------|             |
  |<---- TOKEN ----|                                 |             |
  |<---- DONE -----|                                 |             |
```

---

## 9. Retry-Flow

```
                    +------------------+
                    |   Execute Task   |
                    +------------------+
                            |
                            v
                    +------------------+
                    |   Agent Loop     |
                    |  (max_iterations)|
                    +------------------+
                            |
               +------------+------------+
               |                         |
               v                         v
        [SUCCESS]                   [FAILURE]
               |                         |
               v                         v
        Return Result           +------------------+
                                | retry_count < 3? |
                                +------------------+
                                    |         |
                                   YES        NO
                                    |         |
                                    v         v
                            Apply Strategy   +------------------+
                            - broaden_query  | USER INTERVENTION|
                            - alt_approach   | "Task X failed"  |
                            - diff_perspec.  | [retry][skip][?] |
                                    |        +------------------+
                                    v
                            +------------------+
                            |   Retry Task     |
                            +------------------+
```

---

## 10. Migrations-Plan

### Phase 1: Grundstruktur (Woche 1)
- [ ] Datenmodelle erstellen (Task, TaskPlan, AgentConfig)
- [ ] TaskPlanner implementieren
- [ ] Unit Tests fuer Planner

### Phase 2: Execution (Woche 2)
- [ ] TaskExecutor implementieren
- [ ] Agent-Konfigurationen definieren
- [ ] Retry-Logik einbauen

### Phase 3: Integration (Woche 3)
- [ ] PhaseSynthesizer implementieren
- [ ] Settings erweitern
- [ ] Orchestrator-Integration

### Phase 4: Testing (Woche 4)
- [ ] End-to-End Tests
- [ ] Performance-Tests
- [ ] Edge Cases

---

## 11. Offene Entscheidungen

| # | Entscheidung | Optionen | Empfehlung |
|---|--------------|----------|------------|
| 1 | Planner-Model | analysis_model vs tool_model | analysis_model (Qualitaet wichtiger) |
| 2 | Max parallele Tasks | 2-5 | 3 (Balance Geschwindigkeit/Ressourcen) |
| 3 | Synthese-Trigger | Jeder Phasenwechsel vs nur Research->Code | Nur bei Typwechsel |

---

## 12. Naechste Schritte

Nach Freigabe dieses Designs:

1. **`/sc:implement`** - Implementierung der Kernkomponenten
2. **`/sc:test`** - Test-Suite erstellen
3. **Integration** - Schrittweise Aktivierung im Orchestrator

---

*Design erstellt: 2026-03-15*
*Autor: AI-Assist Design System*
