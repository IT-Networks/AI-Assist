"""
Task Integration - Verbindet das Task-Decomposition-System mit dem Orchestrator.

Dieses Modul bietet:
1. Entry-Point fuer Task-basierte Verarbeitung
2. Event-Bridge fuer Task-Events -> Agent-Events
3. Kontext-Uebergabe zwischen Tasks
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from app.agent.constants import should_skip_decomposition
from app.agent.task_models import Task, TaskPlan, TaskStatus, TaskExecutionResult
from app.agent.task_planner import get_task_planner
from app.agent.task_executor import get_task_executor
from app.core.config import settings

logger = logging.getLogger(__name__)


class TaskEventType:
    """Event-Typen fuer Task-System."""
    PLAN_CREATED = "task_plan_created"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_RETRY = "task_retry"
    PHASE_SYNTHESIS = "phase_synthesis"
    EXECUTION_COMPLETE = "task_execution_complete"
    CLARIFICATION_NEEDED = "task_clarification_needed"


async def should_use_task_decomposition(user_message: str) -> bool:
    """
    Entscheidet ob Task-Decomposition verwendet werden soll.

    Kriterien:
    - Feature ist enabled
    - Message ist lang genug (nicht triviale Anfragen)
    - Message enthaelt keine Skip-Marker (z.B. [CONTINUE])

    Args:
        user_message: Die User-Nachricht

    Returns:
        True wenn Task-Decomposition verwendet werden soll
    """
    if not settings.task_agents.enabled:
        return False

    # Skip-Marker pruefen (zentralisierte Konstanten)
    if should_skip_decomposition(user_message):
        return False

    # Mindestlaenge pruefen (triviale Anfragen nicht zerlegen)
    min_length = 30
    if len(user_message.strip()) < min_length:
        return False

    return True


async def process_with_tasks(
    user_message: str,
    session_id: str,
    context: Optional[str] = None,
    event_callback: Optional[Callable] = None,
    project_id: Optional[str] = None,
    project_path: Optional[str] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Verarbeitet eine User-Nachricht mit dem Task-Decomposition-System.

    Dieser Generator yieldet Events die vom Orchestrator weiterverarbeitet
    werden koennen.

    Args:
        user_message: Die User-Anfrage
        session_id: Session-ID fuer Tracking
        context: Optionaler Kontext (z.B. vorherige Konversation)
        event_callback: Optional Callback fuer Events
        project_id: Projekt-ID fuer Analytics/Learning
        project_path: Projekt-Pfad fuer Memory-Speicherung

    Yields:
        Event-Dictionaries fuer den Orchestrator
    """
    planner = get_task_planner()
    executor = get_task_executor()

    # 1. Planung - Event SOFORT senden bevor LLM blockiert
    logger.info(f"[TaskIntegration] Planning for query: {user_message[:80]}...")

    # Progress-Event für UI (zeigt "Plane Tasks...")
    yield {
        "type": "planning_start",
        "data": {"message": "Analysiere Anfrage und erstelle Aufgabenplan..."}
    }

    try:
        plan = await planner.plan(user_message, context)
    except Exception as e:
        logger.error(f"[TaskIntegration] Planning failed: {e}")
        yield {
            "type": "error",
            "data": {"message": f"Task planning failed: {e}"}
        }
        return

    # 2. Klaerungsfragen?
    if plan.needs_clarification:
        yield {
            "type": TaskEventType.CLARIFICATION_NEEDED,
            "data": {
                "questions": plan.clarification_questions,
                "original_query": plan.original_query
            }
        }
        return

    # 3. Plan-Event senden (mit vollständigen Task-Infos für UI)
    yield {
        "type": TaskEventType.PLAN_CREATED,
        "data": {
            "task_count": len(plan.tasks),
            "original_query": plan.original_query[:200] if plan.original_query else "",
            "tasks": [
                {
                    "id": t.id,
                    "type": t.type.value,
                    "description": t.description,
                    "depends_on": t.depends_on,
                    "status": "pending"
                }
                for t in plan.tasks
            ]
        }
    }

    # 4. Zu wenige Tasks? -> Normale Verarbeitung empfehlen
    min_tasks = settings.task_agents.min_tasks_for_decomposition
    if len(plan.tasks) < min_tasks:
        logger.debug(
            f"[TaskIntegration] Only {len(plan.tasks)} task(s), "
            f"below threshold ({min_tasks}). Recommending direct processing."
        )
        yield {
            "type": "use_direct_processing",
            "data": {
                "reason": f"Task count ({len(plan.tasks)}) below decomposition threshold",
                "single_task_description": plan.tasks[0].description if plan.tasks else user_message
            }
        }
        return

    # 5. Task-Execution
    def task_event_handler(event_type: str, event_data: Dict[str, Any]):
        """Synchroner Callback fuer Task-Events."""
        logger.debug(f"[TaskIntegration] Task event: {event_type}")
        # Events werden asynchron verarbeitet
        return {"type": event_type, "data": event_data}

    logger.info(f"[TaskIntegration] Executing {len(plan.tasks)} tasks...")

    # Task-Events als Generator streamen
    async for event in _execute_tasks_with_events(
        executor, plan, task_event_handler,
        project_id=project_id,
        session_id=session_id,
        project_path=project_path
    ):
        yield event


async def _execute_tasks_with_events(
    executor,
    plan: TaskPlan,
    event_handler: Callable,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project_path: Optional[str] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fuehrt Tasks aus und yieldet Events.

    Args:
        executor: TaskExecutor-Instanz
        plan: TaskPlan mit Tasks
        event_handler: Event-Callback
        project_id: Projekt-ID fuer Analytics/Learning
        session_id: Session-ID fuer Analytics/Learning
        project_path: Projekt-Pfad fuer Memory-Speicherung

    Yields:
        Event-Dictionaries
    """
    # Event-Queue fuer asynchrone Events
    event_queue: asyncio.Queue = asyncio.Queue()

    async def async_event_callback(event_type: str, event_data: Dict[str, Any]):
        """Async Callback der Events in Queue pusht."""
        await event_queue.put({"type": event_type, "data": event_data})

    # Execution in Background-Task starten
    execution_task = asyncio.create_task(
        executor.execute(
            plan, async_event_callback,
            project_id=project_id,
            session_id=session_id,
            project_path=project_path
        )
    )

    # Events aus Queue yielden bis Execution fertig
    result: Optional[TaskExecutionResult] = None

    synthesis_started = False

    while not execution_task.done():
        try:
            # Längerer Timeout während Synthese (LLM-Call), kürzerer während Tasks
            timeout = 2.0 if synthesis_started else 0.5
            event = await asyncio.wait_for(event_queue.get(), timeout=timeout)
            yield event

            # Track synthesis phase to use longer timeout
            if event.get("type") == "synthesis_started":
                synthesis_started = True

        except asyncio.TimeoutError:
            # Während Synthese: Weniger häufige Polls, keine Warn-Logs
            if not synthesis_started:
                continue
            # In Synthese-Phase: Warten ist normal, kein Fehler
            continue
        except Exception as e:
            logger.error(f"[TaskIntegration] Event processing error: {e}")
            continue

    # Restliche Events aus Queue
    while not event_queue.empty():
        try:
            event = event_queue.get_nowait()
            yield event
        except Exception:
            break

    # Execution-Result holen
    try:
        result = execution_task.result()
    except Exception as e:
        logger.error(f"[TaskIntegration] Execution failed: {e}")
        yield {
            "type": "error",
            "data": {"message": f"Task execution failed: {e}"}
        }
        return

    # Final-Event
    yield {
        "type": TaskEventType.EXECUTION_COMPLETE,
        "data": {
            "success": result.success,
            "results": result.results,
            "final_response": result.final_response,
            "failed_tasks": result.failed_tasks,
            "duration_ms": result.total_duration_ms
        }
    }


def format_task_response_for_user(result: Dict[str, Any]) -> str:
    """
    Formatiert das Task-Execution-Result fuer die User-Anzeige.

    Args:
        result: Das execution_complete Event-Data

    Returns:
        Formatierter String fuer den User
    """
    if not result.get("success"):
        failed = result.get("failed_tasks", [])
        return f"Einige Tasks sind fehlgeschlagen: {', '.join(failed)}\n\n{result.get('final_response', '')}"

    return result.get("final_response", "Alle Tasks wurden erfolgreich ausgefuehrt.")


def format_clarification_questions(questions: List[str]) -> str:
    """
    Formatiert Klaerungsfragen fuer die User-Anzeige.

    Args:
        questions: Liste der Fragen

    Returns:
        Formatierter String
    """
    if not questions:
        return "Es werden zusaetzliche Informationen benoetigt."

    formatted = "Bevor ich fortfahre, benoetigen ich einige Klaerungen:\n\n"
    for i, q in enumerate(questions, 1):
        formatted += f"{i}. {q}\n"

    return formatted


# ══════════════════════════════════════════════════════════════════════════════
# Singleton Access
# ══════════════════════════════════════════════════════════════════════════════

_task_integration_enabled: Optional[bool] = None


def is_task_integration_enabled() -> bool:
    """
    Prueft ob Task-Integration enabled ist (cached).

    Returns:
        True wenn enabled
    """
    global _task_integration_enabled

    if _task_integration_enabled is None:
        _task_integration_enabled = settings.task_agents.enabled

    return _task_integration_enabled


def reload_task_integration_config() -> None:
    """Laedt Task-Integration-Config neu."""
    global _task_integration_enabled
    _task_integration_enabled = None
