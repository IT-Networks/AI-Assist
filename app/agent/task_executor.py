"""
Task Executor - Fuehrt Tasks mit Abhaengigkeits-Management aus.

Der TaskExecutor:
1. Verwaltet die Task-Queue und Abhaengigkeiten
2. Parallelisiert unabhaengige Tasks
3. Fuehrt Zwischen-Synthese bei Phasenwechsel durch
4. Implementiert Retry-Logik mit verschiedenen Strategien
5. Erstellt finale Synthese aller Ergebnisse
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set

from app.agent.task_models import (
    Task, TaskPlan, TaskType, TaskStatus, TaskExecutionResult,
    AgentConfig, RetryStrategy
)
from app.agent.task_agents import get_agent_config
from app.agent.tools import ToolRegistry, get_tool_registry
from app.core.config import settings
from app.services.llm_client import LLMClient, llm_client as default_llm_client
from app.services.auto_learner import AutoLearner, get_auto_learner
from app.services.analytics_logger import AnalyticsLogger, get_analytics_logger

logger = logging.getLogger(__name__)

# Timeout für einzelne Tool-Aufrufe in Sekunden
# Research-Tools (Confluence, GitHub, etc.) können lange dauern
TOOL_EXECUTION_TIMEOUT = 90.0


class TaskExecutor:
    """
    Fuehrt Tasks aus dem TaskPlan aus.
    Verwaltet Abhaengigkeiten, Parallelisierung und Retries.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        tool_registry: Optional[ToolRegistry] = None,
        max_parallel: int = 3,
        auto_learner: Optional[AutoLearner] = None,
        analytics: Optional[AnalyticsLogger] = None
    ):
        """
        Initialisiert den TaskExecutor.

        Args:
            llm_client: LLM-Client fuer Agent-Calls
            tool_registry: Registry mit verfuegbaren Tools
            max_parallel: Maximale Anzahl paralleler Tasks
            auto_learner: AutoLearner fuer Lern-Extraktion aus Responses
            analytics: AnalyticsLogger fuer Metriken
        """
        self.llm = llm_client or default_llm_client
        self.tools = tool_registry or get_tool_registry()
        self.max_parallel = max_parallel
        self.auto_learner = auto_learner or get_auto_learner()
        self.analytics = analytics or get_analytics_logger()

        # Execution State
        self.results: Dict[str, str] = {}
        self.completed: Set[str] = set()
        self.current_phase: Optional[TaskType] = None

        # Session/Project Context (set via execute)
        self._project_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._project_path: Optional[str] = None

    async def execute(
        self,
        plan: TaskPlan,
        event_callback: Optional[Callable] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None
    ) -> TaskExecutionResult:
        """
        Fuehrt alle Tasks im Plan aus.

        Args:
            plan: Der auszufuehrende TaskPlan
            event_callback: Optional async Callback fuer Progress-Events
                           Signature: async def callback(event_type: str, data: dict)
            project_id: Projekt-ID fuer Analytics/Learning
            session_id: Session-ID fuer Analytics/Learning
            project_path: Projekt-Pfad fuer Memory-Speicherung

        Returns:
            TaskExecutionResult mit allen Ergebnissen
        """
        start_time = time.time()
        self.results = {}
        self.completed = set()
        self.current_phase = None
        phase_results: List[str] = []

        # Context speichern
        self._project_id = project_id
        self._session_id = session_id
        self._project_path = project_path

        logger.info(f"[TaskExecutor] Starting execution of {len(plan.tasks)} tasks")

        while True:
            # Ready Tasks finden
            ready = plan.get_ready_tasks(self.completed)

            if not ready:
                # Pruefen ob noch Tasks pending sind
                pending = [t for t in plan.tasks if t.status == TaskStatus.PENDING]
                if pending:
                    # Deadlock: Tasks warten auf nicht-existierende Abhaengigkeiten
                    logger.error(
                        f"[TaskExecutor] Deadlock! Pending tasks: "
                        f"{[t.id for t in pending]}"
                    )
                    for t in pending:
                        t.status = TaskStatus.FAILED
                        t.error = "Deadlock: Abhaengigkeiten nicht erfuellbar"
                break

            # Phasenwechsel erkennen
            new_phase = ready[0].type
            if self.current_phase and new_phase != self.current_phase:
                # Zwischen-Synthese durchfuehren
                if phase_results:
                    logger.info(
                        f"[TaskExecutor] Phase change: {self.current_phase.value} -> "
                        f"{new_phase.value}, synthesizing {len(phase_results)} results"
                    )
                    synthesis = await self._synthesize_phase(
                        self.current_phase, phase_results
                    )
                    phase_key = f"PHASE_{self.current_phase.value}"
                    self.results[phase_key] = synthesis

                    # Synthese als Kontext fuer naechste Tasks
                    for task in ready:
                        if phase_key not in task.context_from:
                            task.context_from.append(phase_key)

                phase_results = []

            self.current_phase = new_phase

            # Event: Tasks starten (einzeln für bessere UI-Updates)
            if event_callback:
                for task in ready:
                    await event_callback("task_started", {
                        "task_id": task.id,
                        "type": task.type.value,
                        "description": task.description,
                        "depends_on": task.depends_on,
                        "phase": new_phase.value
                    })

            # Tasks parallel ausfuehren (bis zu max_parallel)
            batches = [
                ready[i:i + self.max_parallel]
                for i in range(0, len(ready), self.max_parallel)
            ]

            for batch in batches:
                # Tasks als RUNNING markieren
                for task in batch:
                    task.status = TaskStatus.RUNNING

                # Parallel ausfuehren
                results = await asyncio.gather(*[
                    self._execute_single_task(task, event_callback)
                    for task in batch
                ], return_exceptions=True)

                # Ergebnisse verarbeiten
                for task, result in zip(batch, results):
                    if isinstance(result, Exception):
                        task.status = TaskStatus.FAILED
                        task.error = str(result)
                        logger.error(f"[TaskExecutor] Task {task.id} failed: {result}")

                        if event_callback:
                            await event_callback("task_failed", {
                                "task_id": task.id,
                                "type": task.type.value,
                                "description": task.description,
                                "error": str(result)[:200],
                                "retry_count": task.retry_count
                            })
                    else:
                        task.status = TaskStatus.COMPLETED
                        task.result = result
                        self.results[task.id] = result
                        self.completed.add(task.id)
                        phase_results.append(result)

                        logger.info(f"[TaskExecutor] Task {task.id} completed")

                        # Auto-Learning: Analysiere Agent-Response
                        if self.auto_learner and result:
                            try:
                                candidates = await self.auto_learner.analyze_assistant_response(
                                    response=result,
                                    user_message=task.description,
                                    project_id=self._project_id
                                )
                                if candidates:
                                    await self.auto_learner.save_candidates(
                                        candidates=candidates,
                                        project_id=self._project_id,
                                        session_id=self._session_id,
                                        project_path=self._project_path
                                    )
                                    logger.debug(
                                        f"[TaskExecutor] Auto-learned {len(candidates)} items "
                                        f"from task {task.id}"
                                    )
                            except Exception as e:
                                logger.debug(f"[TaskExecutor] Auto-learning failed: {e}")

                        if event_callback:
                            # Kurze Vorschau des Ergebnisses für UI
                            result_preview = ""
                            if result:
                                # Erste Zeile oder erste 150 Zeichen
                                first_line = result.split('\n')[0][:150]
                                result_preview = first_line + ("..." if len(result) > 150 else "")

                            await event_callback("task_completed", {
                                "task_id": task.id,
                                "type": task.type.value,
                                "description": task.description,
                                "result_preview": result_preview,
                                "result_length": len(result) if result else 0,
                                "has_full_result": bool(result)
                            })

        # Finale Synthese - Event senden damit UI nicht in Timeout-Loop läuft
        if event_callback:
            await event_callback("synthesis_started", {
                "total_tasks": len(plan.tasks),
                "completed_tasks": len(self.completed),
                "results_count": len(self.results)
            })

        final_response = await self._final_synthesis(plan)

        duration_ms = int((time.time() - start_time) * 1000)

        return TaskExecutionResult(
            success=plan.is_successful,
            results=self.results,
            final_response=final_response,
            failed_tasks=[t.id for t in plan.tasks if t.status == TaskStatus.FAILED],
            total_duration_ms=duration_ms
        )

    async def _execute_single_task(
        self,
        task: Task,
        event_callback: Optional[Callable] = None
    ) -> str:
        """
        Fuehrt einen einzelnen Task mit dem passenden Agent aus.

        Args:
            task: Der auszufuehrende Task
            event_callback: Optional Callback fuer Events

        Returns:
            Task-Ergebnis als String

        Raises:
            RuntimeError: Wenn Task nach max_retries fehlschlaegt
        """
        agent_config = get_agent_config(task.type)

        # Kontext aus Abhaengigkeiten und Phasen sammeln
        context = self._build_task_context(task)

        # Agent mit Retry-Logik ausfuehren
        return await self._run_agent_with_retry(
            agent_config, task, context, event_callback
        )

    async def _run_agent_with_retry(
        self,
        config: AgentConfig,
        task: Task,
        context: str,
        event_callback: Optional[Callable] = None
    ) -> str:
        """
        Fuehrt Agent aus mit Retry-Strategie.

        Args:
            config: Agent-Konfiguration
            task: Der Task
            context: Kontext aus Abhaengigkeiten
            event_callback: Optional Callback

        Returns:
            Agent-Ergebnis

        Raises:
            RuntimeError: Nach max_retries Fehlversuchen
        """
        last_error: Optional[Exception] = None
        current_description = task.description

        for attempt in range(config.max_retries):
            try:
                # Model auswaehlen (mit Fallback)
                model = await self._select_model(config)

                logger.debug(
                    f"[TaskExecutor] Running task {task.id} (attempt {attempt + 1}) "
                    f"with model {model}"
                )

                result = await self._run_agent(
                    config, model, current_description, context
                )

                return result

            except Exception as e:
                last_error = e
                task.retry_count = attempt + 1
                task.status = TaskStatus.RETRY

                logger.warning(
                    f"[TaskExecutor] Task {task.id} attempt {attempt + 1} failed: {e}"
                )

                if event_callback:
                    await event_callback("task_retry", {
                        "task_id": task.id,
                        "attempt": attempt + 1,
                        "max_retries": config.max_retries,
                        "error": str(e)
                    })

                # Retry-Strategie anwenden (ausser beim letzten Versuch)
                if attempt < config.max_retries - 1:
                    current_description = self._apply_retry_strategy(
                        config.retry_strategy,
                        task.description,
                        str(e)
                    )
                    # Kurze Pause vor Retry
                    await asyncio.sleep(1.0)

        # Alle Retries fehlgeschlagen
        raise RuntimeError(
            f"Task {task.id} failed after {config.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    async def _run_agent(
        self,
        config: AgentConfig,
        model: str,
        description: str,
        context: str
    ) -> str:
        """
        Fuehrt den Agent-Loop fuer eine Task aus.

        Args:
            config: Agent-Konfiguration
            model: Zu verwendendes Model
            description: Task-Beschreibung
            context: Kontext aus Abhaengigkeiten

        Returns:
            Agent-Ergebnis
        """
        # Messages aufbauen
        messages = [
            {"role": "system", "content": config.system_prompt}
        ]

        if context:
            messages.append({
                "role": "system",
                "content": f"KONTEXT AUS VORHERIGEN TASKS:\n{context}"
            })

        messages.append({
            "role": "user",
            "content": description
        })

        # Tool-Schemas fuer erlaubte Tools
        all_schemas = self.tools.get_openai_schemas(include_write_ops=True)
        tool_schemas = [
            schema for schema in all_schemas
            if schema["function"]["name"] in config.tools
        ]

        collected_output: List[str] = []

        # Agent-Loop
        for iteration in range(config.max_iterations):
            logger.debug(
                f"[TaskExecutor] Agent iteration {iteration + 1}/{config.max_iterations}"
            )

            response = await self.llm.chat_with_tools(
                messages=messages,
                tools=tool_schemas if tool_schemas else None,
                model=model,
                temperature=config.temperature,
                max_tokens=4096
            )

            content = response.content
            tool_calls = response.tool_calls

            # Content sammeln
            if content:
                collected_output.append(content)

            # Keine Tool-Calls -> fertig
            if not tool_calls:
                break

            # Tool-Calls ausfuehren
            # Assistant-Message mit Tool-Calls hinzufuegen
            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_msg["content"] = content
            assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                tool_args_str = func.get("arguments", "{}")

                try:
                    tool_args = json.loads(tool_args_str) if tool_args_str else {}
                except json.JSONDecodeError:
                    tool_args = {}

                # Tool ausfuehren mit Timeout
                tool_success = False
                if tool_name in config.tools:
                    try:
                        # Timeout für Tool-Ausführung (verhindert Hänger bei langsamen APIs)
                        tool_result = await asyncio.wait_for(
                            self.tools.execute(tool_name, tool_args),
                            timeout=TOOL_EXECUTION_TIMEOUT
                        )
                        result_str = tool_result.to_context()
                        tool_success = True
                    except asyncio.TimeoutError:
                        result_str = f"[Timeout] Tool '{tool_name}' hat nach {TOOL_EXECUTION_TIMEOUT}s nicht geantwortet"
                        logger.warning(f"[TaskExecutor] Tool timeout: {tool_name}")
                    except Exception as e:
                        result_str = f"[Tool-Fehler] {e}"
                else:
                    result_str = f"[Fehler] Tool '{tool_name}' nicht erlaubt fuer diesen Agent"

                # Tool-Usage tracken (fuer Pattern-Erkennung)
                if self.auto_learner and tool_success:
                    try:
                        pattern = await self.auto_learner.track_tool_usage(
                            tool_name=tool_name,
                            arguments=tool_args,
                            project_id=self._project_id
                        )
                        if pattern:
                            # Pattern erkannt (3x verwendet)
                            await self.auto_learner.save_candidates(
                                candidates=[pattern],
                                project_id=self._project_id,
                                session_id=self._session_id
                            )
                            logger.debug(f"[TaskExecutor] Tool pattern detected: {tool_name}")
                    except Exception as e:
                        logger.debug(f"[TaskExecutor] Tool tracking failed: {e}")

                # Tool-Result als Message
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_str
                })

        # Ergebnis zusammenfassen
        if collected_output:
            return "\n\n".join(collected_output)
        else:
            return "[Keine Ausgabe vom Agent]"

    async def _select_model(self, config: AgentConfig) -> str:
        """
        Waehlt Model mit Fallback-Logik.

        Args:
            config: Agent-Konfiguration

        Returns:
            Ausgewaehltes Model-ID
        """
        try:
            models = await self.llm.list_models()
            model_ids = [m for m in models if m]

            if config.model in model_ids:
                return config.model
            elif config.fallback_model in model_ids:
                logger.info(
                    f"[TaskExecutor] Primary model {config.model} not available, "
                    f"using fallback: {config.fallback_model}"
                )
                return config.fallback_model
            else:
                logger.warning(
                    f"[TaskExecutor] Neither primary nor fallback model available, "
                    f"using default"
                )
                return settings.llm.default_model

        except Exception as e:
            logger.warning(f"[TaskExecutor] Model listing failed: {e}")
            return config.fallback_model or settings.llm.default_model

    def _build_task_context(self, task: Task) -> str:
        """
        Baut Kontext aus Abhaengigkeiten und Phasen-Synthesen.

        Args:
            task: Der Task fuer den Kontext gebaut wird

        Returns:
            Zusammengesetzter Kontext-String
        """
        context_parts = []

        # Ergebnisse von direkten Abhaengigkeiten
        for dep_id in task.depends_on:
            if dep_id in self.results:
                context_parts.append(
                    f"## Ergebnis von Task {dep_id}:\n{self.results[dep_id]}"
                )

        # Kontext von Phasen-Synthesen
        for ctx_ref in task.context_from:
            if ctx_ref in self.results:
                context_parts.append(
                    f"## {ctx_ref}:\n{self.results[ctx_ref]}"
                )

        return "\n\n---\n\n".join(context_parts)

    def _apply_retry_strategy(
        self,
        strategy: RetryStrategy,
        description: str,
        error: str
    ) -> str:
        """
        Wendet Retry-Strategie auf die Task-Beschreibung an.

        Args:
            strategy: Die anzuwendende Strategie
            description: Urspruengliche Beschreibung
            error: Fehlermeldung vom vorherigen Versuch

        Returns:
            Modifizierte Beschreibung
        """
        if strategy == RetryStrategy.BROADEN_QUERY:
            return (
                f"{description}\n\n"
                f"HINWEIS: Der vorherige Versuch hat nicht genug Ergebnisse geliefert. "
                f"Versuche eine breitere Suche mit alternativen Begriffen."
            )

        elif strategy == RetryStrategy.ALTERNATIVE_APPROACH:
            return (
                f"{description}\n\n"
                f"FEHLER IM VORHERIGEN VERSUCH: {error}\n\n"
                f"Versuche einen anderen Ansatz. Analysiere den Fehler und "
                f"waehle eine alternative Implementierungsstrategie."
            )

        elif strategy == RetryStrategy.DIFFERENT_PERSPECTIVE:
            return (
                f"{description}\n\n"
                f"Der vorherige Analyseversucht war nicht erfolgreich: {error}\n\n"
                f"Analysiere aus einer anderen Perspektive. "
                f"Fokussiere auf andere Aspekte des Codes."
            )

        elif strategy == RetryStrategy.CHECK_PREREQUISITES:
            return (
                f"{description}\n\n"
                f"VORHERIGER FEHLER: {error}\n\n"
                f"Pruefe zuerst ob alle Voraussetzungen erfuellt sind "
                f"(Dienste laufen, Berechtigungen vorhanden, Konfiguration korrekt)."
            )

        elif strategy == RetryStrategy.ISOLATE_AND_TEST:
            return (
                f"{description}\n\n"
                f"VORHERIGER DEBUG-VERSUCH FEHLGESCHLAGEN: {error}\n\n"
                f"DEBUGGING-STRATEGIE:\n"
                f"1. Isoliere das Problem auf eine kleinere Einheit\n"
                f"2. Erstelle einen minimalen Testfall zum Reproduzieren\n"
                f"3. Teste Hypothesen schrittweise (eine Variable pro Versuch)\n"
                f"4. Nutze verfuegbare Test-Tools um Verhalten zu verifizieren\n"
                f"5. Dokumentiere Erkenntnisse fuer die naechste Iteration"
            )

        else:  # REPHRASE
            return (
                f"Aufgabe (umformuliert nach Fehler):\n"
                f"{description}\n\n"
                f"Fehler beim vorherigen Versuch: {error}"
            )

    async def _synthesize_phase(
        self,
        phase: TaskType,
        results: List[str]
    ) -> str:
        """
        Synthetisiert Ergebnisse einer Phase.

        Args:
            phase: Der Phasen-Typ
            results: Liste der Task-Ergebnisse dieser Phase

        Returns:
            Komprimierte Zusammenfassung
        """
        if not results:
            return ""

        if len(results) == 1:
            # Nur ein Ergebnis -> minimal komprimieren
            return self._truncate(results[0], max_tokens=1500)

        combined = "\n\n---\n\n".join(results)

        prompt = f"""Fasse die folgenden {phase.value}-Ergebnisse zusammen.

ERGEBNISSE:
{combined}

REGELN:
- Maximal 1000 Woerter
- Behalte alle wichtigen Informationen
- Strukturiere klar mit Ueberschriften
- Bei Code: Wichtigste Snippets behalten
- Bei Recherche: Kernfakten extrahieren

AUSGABE-FORMAT:
## Zusammenfassung {phase.value.title()}-Phase
[Kompakte Zusammenfassung]

## Wichtigste Ergebnisse
- [Punkt 1]
- [Punkt 2]

## Fuer naechste Phase relevant
[Was die naechsten Tasks wissen muessen]
"""

        try:
            response = await self.llm.chat_with_tools(
                messages=[
                    {"role": "system", "content": "Du fasst Ergebnisse praezise zusammen."},
                    {"role": "user", "content": prompt}
                ],
                model=settings.llm.analysis_model or settings.llm.default_model,
                temperature=0.1,
                max_tokens=2048
            )

            return response.content or self._truncate(combined, max_tokens=1500)

        except Exception as e:
            logger.warning(f"[TaskExecutor] Phase synthesis failed: {e}")
            return self._truncate(combined, max_tokens=1500)

    async def _final_synthesis(self, plan: TaskPlan) -> str:
        """
        Erstellt die finale Synthese aller Task-Ergebnisse.

        Args:
            plan: Der ausgefuehrte TaskPlan

        Returns:
            Finale kohaerente Antwort
        """
        # Nur erfolgreiche Tasks beruecksichtigen
        successful_results = [
            f"## Task {t.id} ({t.type.value})\n{t.result}"
            for t in plan.tasks
            if t.status == TaskStatus.COMPLETED and t.result
        ]

        if not successful_results:
            failed = [t.id for t in plan.tasks if t.status == TaskStatus.FAILED]
            if failed:
                return f"Alle Tasks sind fehlgeschlagen: {', '.join(failed)}"
            return "Keine Ergebnisse verfuegbar."

        if len(successful_results) == 1:
            # Nur ein Ergebnis -> direkt zurueckgeben
            return plan.tasks[0].result or ""

        combined = "\n\n---\n\n".join(successful_results)

        prompt = f"""Erstelle eine kohaerente Antwort aus den folgenden Task-Ergebnissen.

URSPRUENGLICHE ANFRAGE:
{plan.original_query}

TASK-ERGEBNISSE:
{combined}

REGELN:
- Beantworte die urspruengliche Anfrage vollstaendig
- Integriere alle relevanten Ergebnisse
- Strukturiere die Antwort logisch
- Keine Wiederholungen
- Bei Code: Zeige den finalen Code, nicht alle Zwischenschritte
"""

        try:
            response = await self.llm.chat_with_tools(
                messages=[
                    {"role": "system", "content": "Du erstellst kohaerente Zusammenfassungen."},
                    {"role": "user", "content": prompt}
                ],
                model=settings.llm.analysis_model or settings.llm.default_model,
                temperature=0.2,
                max_tokens=4096
            )

            return response.content or combined

        except Exception as e:
            logger.warning(f"[TaskExecutor] Final synthesis failed: {e}")
            return combined

    def _truncate(self, text: str, max_tokens: int) -> str:
        """
        Kuerzt Text auf max_tokens.

        Args:
            text: Zu kuerzender Text
            max_tokens: Maximale Token-Anzahl (geschaetzt)

        Returns:
            Gekuerzter Text
        """
        # Grobe Schaetzung: 1 Token ~ 4 Zeichen
        max_chars = max_tokens * 4

        if len(text) <= max_chars:
            return text

        return text[:max_chars] + "\n\n[... gekuerzt ...]"


# Singleton
_task_executor: Optional[TaskExecutor] = None


def get_task_executor() -> TaskExecutor:
    """
    Gibt die TaskExecutor-Instanz zurueck (Singleton).

    Returns:
        TaskExecutor-Instanz
    """
    global _task_executor
    if _task_executor is None:
        _task_executor = TaskExecutor()
    return _task_executor
