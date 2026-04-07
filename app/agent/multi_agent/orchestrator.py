"""
MultiAgentOrchestrator – Koordiniert ein Team aus Agenten fuer eine komplexe Aufgabe.

Pipeline:
1. Goal → TaskPlan (Coordinator LLM-Call)
2. TaskPlan → Scheduled Tasks (Topologische Sortierung)
3. Tasks → Parallele Ausfuehrung (AgentPool)
4. Ergebnisse → Synthesis (LLM-Zusammenfassung)
"""

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Dict, List, Optional

from app.agent.multi_agent.agent_pool import AgentPool
from app.agent.multi_agent.message_bus import MessageBus
from app.agent.multi_agent.models import (
    TeamConfig,
    TeamRunResult,
    TeamTask,
)
from app.agent.multi_agent.scheduler import TaskScheduler
from app.agent.multi_agent.team_agent import TeamAgent
from app.core.config import settings
from app.services.llm_client import llm_client as default_llm_client

logger = logging.getLogger(__name__)


class MultiAgentOrchestrator:
    """Koordiniert ein Team aus Agenten fuer eine komplexe Aufgabe."""

    def __init__(
        self,
        team_config: TeamConfig,
        on_progress: Optional[Callable[[Dict], Awaitable[None]]] = None,
    ):
        self._team = team_config
        self._on_progress = on_progress
        self._message_bus = MessageBus()
        self._shared_context: Dict[str, str] = {}  # task_id → result
        self._pool = AgentPool(max_concurrent=team_config.max_parallel)
        self._scheduler = TaskScheduler(strategy=team_config.strategy)
        self._model = settings.multi_agent.coordinator_model or settings.llm.default_model
        self._timeout = settings.multi_agent.task_timeout_seconds

        # Agents erstellen und im Pool registrieren
        for agent_config in team_config.agents:
            agent = TeamAgent(agent_config, self._message_bus)
            self._pool.register(agent)

    async def run(self, goal: str) -> TeamRunResult:
        """Fuehrt den kompletten Team-Run aus."""
        start = time.time()
        logger.info(f"[MultiAgent] Team '{self._team.name}' startet: {goal[:100]}")

        # Phase 1: Coordinator zerlegt Ziel in Tasks
        await self._emit({"phase": "planning", "message": "Zerlege Aufgabe in Tasks..."})
        tasks = await self._decompose_goal(goal)

        if not tasks:
            return TeamRunResult(
                team_name=self._team.name,
                goal=goal,
                final_summary="Konnte die Aufgabe nicht in Tasks zerlegen.",
                duration_seconds=time.time() - start,
            )

        logger.info(f"[MultiAgent] {len(tasks)} Tasks erstellt")
        await self._emit({
            "phase": "planned",
            "tasks": len(tasks),
            "agents": self._team.agent_names(),
        })

        # Phase 2: Scheduling
        ordered = self._scheduler.schedule(tasks, self._team.agents)

        # Phase 3: Execution Loop
        await self._execute_loop(ordered)

        # Phase 4: Synthesis
        await self._emit({"phase": "synthesizing", "message": "Fasse Ergebnisse zusammen..."})
        summary = await self._synthesize_results(goal, tasks)

        # Statistiken
        completed = sum(1 for t in tasks if t.status == "completed")
        failed = sum(1 for t in tasks if t.status == "failed")
        duration = time.time() - start

        logger.info(f"[MultiAgent] Fertig: {completed}/{len(tasks)} Tasks in {duration:.1f}s")

        await self._emit({
            "phase": "complete",
            "completed": completed,
            "failed": failed,
            "total": len(tasks),
            "duration": round(duration, 1),
        })

        return TeamRunResult(
            team_name=self._team.name,
            goal=goal,
            tasks=tasks,
            messages=self._message_bus.get_all(),
            final_summary=summary,
            total_tasks=len(tasks),
            completed_tasks=completed,
            failed_tasks=failed,
            duration_seconds=duration,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 1: Goal Decomposition
    # ══════════════════════════════════════════════════════════════════════════

    async def _decompose_goal(self, goal: str) -> List[TeamTask]:
        """Coordinator: LLM zerlegt Ziel in Tasks mit Agent-Zuweisungen."""
        agents_desc = "\n".join([
            f"- {a.name}: {a.system_prompt or 'Allgemeiner Agent'} (Tools: {', '.join(a.tools) or 'keine'})"
            for a in self._team.agents
        ])

        prompt = (
            f"Du bist ein Task-Koordinator. Zerlege das folgende Ziel in konkrete Aufgaben "
            f"und weise sie den verfuegbaren Agenten zu.\n\n"
            f"ZIEL: {goal}\n\n"
            f"VERFUEGBARE AGENTEN:\n{agents_desc}\n\n"
            f"REGELN:\n"
            f"- Erstelle 2-8 Tasks (nicht mehr)\n"
            f"- Jeder Task muss einem Agenten zugewiesen sein (assignee)\n"
            f"- Tasks koennen von anderen Tasks abhaengen (dependsOn: [task-id])\n"
            f"- IDs muessen mit 't' beginnen: t1, t2, t3, ...\n"
            f"- Unabhaengige Tasks koennen parallel laufen\n"
            f"- Der letzte Task sollte die Ergebnisse zusammenfassen\n\n"
            f"Antworte NUR mit JSON-Array:\n"
            f'[{{"id":"t1","title":"...","description":"...","assignee":"agent_name","dependsOn":[]}},'
            f'{{"id":"t2","title":"...","description":"...","assignee":"agent_name","dependsOn":["t1"]}}]'
        )

        try:
            text = await default_llm_client.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.1,
                max_tokens=2048,
            )
            return self._parse_tasks(text)
        except Exception as e:
            logger.error(f"[MultiAgent] Coordinator fehlgeschlagen: {e}")
            return []

    def _parse_tasks(self, text: str) -> List[TeamTask]:
        """Parst Task-JSON aus LLM-Antwort."""
        # JSON-Array extrahieren
        try:
            # Versuche direktes Parsing
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                raw_tasks = json.loads(text[start:end])
            else:
                # Versuche aus Code-Block
                import re
                match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
                if match:
                    raw_tasks = json.loads(match.group(1))
                else:
                    logger.warning("[MultiAgent] Kein JSON-Array in Coordinator-Antwort gefunden")
                    return []
        except json.JSONDecodeError as e:
            logger.warning(f"[MultiAgent] JSON-Parse-Fehler: {e}")
            return []

        if not isinstance(raw_tasks, list):
            return []

        # Zu TeamTask-Objekten konvertieren
        tasks: List[TeamTask] = []
        valid_agents = set(self._team.agent_names())

        for raw in raw_tasks:
            if not isinstance(raw, dict):
                continue

            task_id = raw.get("id", f"t{len(tasks) + 1}")
            assignee = raw.get("assignee", "")

            # Assignee validieren
            if assignee not in valid_agents:
                assignee = self._team.agents[0].name if self._team.agents else ""

            depends_on = raw.get("dependsOn", raw.get("depends_on", []))
            if not isinstance(depends_on, list):
                depends_on = []

            tasks.append(TeamTask(
                id=task_id,
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                assignee=assignee,
                depends_on=depends_on,
            ))

        return tasks

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 3: Execution Loop
    # ══════════════════════════════════════════════════════════════════════════

    async def _execute_loop(self, tasks: List[TeamTask]):
        """
        Execution Loop:
        while pending tasks exist:
          1. Finde ready Tasks (dependencies satisfied)
          2. Dispatche an AgentPool (parallel)
          3. Ergebnisse → SharedContext
          4. Unblock abhaengige Tasks
        """
        task_map = {t.id: t for t in tasks}
        completed_ids: set = set()
        max_rounds = 20  # Sicherheits-Limit

        for round_num in range(max_rounds):
            # Ready Tasks finden
            ready = [t for t in tasks if t.is_ready(completed_ids)]
            if not ready:
                # Pruefen ob noch pending Tasks da sind (blockiert?)
                pending = [t for t in tasks if t.status in ("pending", "blocked")]
                if not pending:
                    break  # Alle fertig
                logger.warning(f"[MultiAgent] Keine ready Tasks, aber {len(pending)} pending/blocked")
                break

            # Batch-Assignments erstellen
            assignments = []
            for task in ready:
                task.status = "in_progress"
                # Context aus Dependencies zusammenbauen
                dep_context = self._build_dependency_context(task)

                await self._emit({
                    "phase": "executing",
                    "task": task.title,
                    "agent": task.assignee,
                    "round": round_num + 1,
                    "completed": len(completed_ids),
                    "total": len(tasks),
                })

                assignments.append((task.assignee, task, dep_context))

            # Parallel ausfuehren
            results = await self._pool.execute_batch(assignments, timeout=self._timeout)

            # Ergebnisse verarbeiten
            for task in ready:
                result = results.get(task.id, "")

                if result.startswith("FEHLER:"):
                    task.status = "failed"
                    task.error = result
                    logger.warning(f"[MultiAgent] Task '{task.title}' fehlgeschlagen: {result}")
                    # Cascade: Abhaengige Tasks blockieren
                    self._cascade_failure(task.id, tasks)
                    await self._emit({
                        "phase": "task_failed",
                        "task": task.title,
                        "agent": task.assignee,
                        "error": result,
                    })
                else:
                    task.status = "completed"
                    task.result = result
                    completed_ids.add(task.id)
                    self._shared_context[task.id] = result
                    await self._emit({
                        "phase": "task_completed",
                        "task": task.title,
                        "agent": task.assignee,
                        "completed": len(completed_ids),
                        "total": len(tasks),
                    })

    def _build_dependency_context(self, task: TeamTask) -> str:
        """Baut Context aus Dependency-Ergebnissen."""
        parts = []
        for dep_id in task.depends_on:
            if dep_id in self._shared_context:
                parts.append(f"=== Ergebnis von {dep_id} ===\n{self._shared_context[dep_id][:2000]}")
        return "\n\n".join(parts)

    def _cascade_failure(self, failed_id: str, tasks: List[TeamTask]):
        """
        Markiert alle transitiv abhaengigen Tasks als failed.

        Performance: O(V+E) via Reverse-Adjacency-Map statt O(n^2) Loop.
        """
        # Reverse-Adjacency: dep_id → [tasks die davon abhaengen]
        from collections import defaultdict, deque
        reverse_adj: Dict[str, List[TeamTask]] = defaultdict(list)
        for task in tasks:
            for dep in task.depends_on:
                reverse_adj[dep].append(task)

        # BFS ueber Reverse-Adjacency
        affected: set = set()
        queue = deque([failed_id])
        while queue:
            fid = queue.popleft()
            for task in reverse_adj.get(fid, []):
                if task.id not in affected and task.status in ("pending", "blocked"):
                    task.status = "failed"
                    task.error = f"Abhaengiger Task {fid} fehlgeschlagen"
                    affected.add(task.id)
                    queue.append(task.id)

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 4: Synthesis
    # ══════════════════════════════════════════════════════════════════════════

    async def _synthesize_results(self, goal: str, tasks: List[TeamTask]) -> str:
        """LLM fasst alle Task-Ergebnisse zusammen."""
        results_text = []
        for task in tasks:
            status_icon = "OK" if task.status == "completed" else "FEHLER"
            results_text.append(f"[{status_icon}] {task.title} ({task.assignee}):\n{task.result or task.error}")

        prompt = (
            f"Fasse die Ergebnisse dieses Team-Runs zusammen.\n\n"
            f"URSPRUENGLICHES ZIEL: {goal}\n\n"
            f"TASK-ERGEBNISSE:\n" + "\n\n---\n\n".join(results_text) + "\n\n"
            f"Erstelle eine strukturierte Zusammenfassung mit:\n"
            f"1. Erreichte Ergebnisse\n"
            f"2. Offene Punkte oder Fehler\n"
            f"3. Empfehlungen\n"
        )

        try:
            return await default_llm_client.chat_quick(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.2,
                max_tokens=2048,
            )
        except Exception as e:
            logger.error(f"[MultiAgent] Synthesis fehlgeschlagen: {e}")
            # Fallback: Einfache Auflistung
            return "\n\n".join(results_text)

    # ══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════════

    async def _emit(self, data: Dict):
        """Emittiert ein Progress-Event."""
        if self._on_progress:
            try:
                await self._on_progress(data)
            except Exception as e:
                logger.debug(f"[MultiAgent] Progress-Callback Fehler: {e}")
