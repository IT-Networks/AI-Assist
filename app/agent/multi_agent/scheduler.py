"""
TaskScheduler – Topologische Sortierung und Scheduling-Strategien fuer Team-Tasks.

Strategien:
- dependency-first: Tasks die die meisten anderen blockieren zuerst (kritischer Pfad)
- capability-match: Tasks dem Agent mit bestem Keyword-Overlap zuweisen
"""

import logging
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set

# Pre-compiled regex (vermeidet Recompilation pro Agent/Task)
_RE_WORDS = re.compile(r'\w{3,}')

from app.agent.multi_agent.models import TeamAgentConfig, TeamTask

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Sortiert und priorisiert Tasks basierend auf Strategie."""

    def __init__(self, strategy: str = "dependency-first"):
        self._strategy = strategy

    def schedule(
        self,
        tasks: List[TeamTask],
        agents: List[TeamAgentConfig],
    ) -> List[TeamTask]:
        """
        Sortiert Tasks topologisch und weist fehlende Assignees zu.

        Returns:
            Sortierte Task-Liste (ready-to-execute Reihenfolge)
        """
        # Validierung
        errors = self.validate_dependencies(tasks)
        if errors:
            logger.warning(f"[Scheduler] Dependency-Fehler: {errors}")

        # 1. Topologische Sortierung
        ordered = self._topological_sort(tasks)

        # 2. Strategie anwenden
        if self._strategy == "dependency-first":
            ordered = self._sort_by_critical_path(ordered, tasks)

        # 3. Fehlende Assignees zuweisen
        agent_names = [a.name for a in agents]
        for task in ordered:
            if not task.assignee or task.assignee not in agent_names:
                task.assignee = self._best_agent_for_task(task, agents)

        # 4. Blocked-Status setzen fuer Tasks mit unerfuellten Dependencies
        task_ids = {t.id for t in ordered}
        for task in ordered:
            unresolved = [d for d in task.depends_on if d not in task_ids]
            if unresolved:
                task.status = "blocked"
                logger.warning(f"[Scheduler] Task '{task.title}' blocked: unbekannte Dependencies {unresolved}")

        return ordered

    def _topological_sort(self, tasks: List[TeamTask]) -> List[TeamTask]:
        """Kahn's Algorithm fuer DAG-Sortierung."""
        task_map = {t.id: t for t in tasks}
        in_degree: Dict[str, int] = {t.id: 0 for t in tasks}
        adjacency: Dict[str, List[str]] = defaultdict(list)

        for task in tasks:
            for dep in task.depends_on:
                if dep in task_map:
                    adjacency[dep].append(task.id)
                    in_degree[task.id] += 1

        # Starte mit Tasks ohne Dependencies
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        result: List[TeamTask] = []

        while queue:
            tid = queue.popleft()
            result.append(task_map[tid])
            for neighbor in adjacency[tid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Nicht sortierte Tasks (Zyklen) am Ende anfuegen
        sorted_ids = {t.id for t in result}
        for task in tasks:
            if task.id not in sorted_ids:
                logger.warning(f"[Scheduler] Task '{task.title}' in Zyklus — wird trotzdem ausgefuehrt")
                result.append(task)

        return result

    def _sort_by_critical_path(
        self,
        ordered: List[TeamTask],
        all_tasks: List[TeamTask],
    ) -> List[TeamTask]:
        """
        Priorisiert Tasks die die meisten anderen transitiv blockieren.

        Performance: Reverse-topologische Berechnung in O(V+E) statt O(V*(V+E)).
        Nutzt Bottom-Up-Zaehlung: Blattnoten haben 0 Abhaengige,
        jeder Elternknoten hat Summe(Kinder) + direkte Kinder.
        """
        adjacency: Dict[str, List[str]] = defaultdict(list)
        for task in all_tasks:
            for dep in task.depends_on:
                adjacency[dep].append(task.id)

        # Bottom-Up: Blocker-Count via memoized DFS (O(V+E) gesamt)
        blocker_count: Dict[str, int] = {}
        visited_memo: Dict[str, int] = {}

        def count_descendants(tid: str) -> int:
            if tid in visited_memo:
                return visited_memo[tid]
            children = adjacency.get(tid, [])
            total = len(children)
            for child in children:
                total += count_descendants(child)
            visited_memo[tid] = total
            return total

        for task in all_tasks:
            blocker_count[task.id] = count_descendants(task.id)

        # Sortiere: Mehr Blocker → hoehere Prioritaet (innerhalb jeder Tiefe)
        return sorted(ordered, key=lambda t: -blocker_count.get(t.id, 0))

    def _best_agent_for_task(
        self,
        task: TeamTask,
        agents: List[TeamAgentConfig],
    ) -> str:
        """Capability-Match: Keyword-Overlap zwischen Task und Agent."""
        if not agents:
            return ""

        task_words = set(_RE_WORDS.findall(f"{task.title} {task.description}".lower()))

        best_agent = agents[0].name
        best_score = -1

        for agent in agents:
            agent_words = set(_RE_WORDS.findall(
                f"{agent.name} {agent.system_prompt}".lower()
            ))
            overlap = len(task_words & agent_words)
            if overlap > best_score:
                best_score = overlap
                best_agent = agent.name

        return best_agent

    @staticmethod
    def validate_dependencies(tasks: List[TeamTask]) -> List[str]:
        """Prueft auf ungueltige Dependencies und Zyklen."""
        errors: List[str] = []
        task_ids = {t.id for t in tasks}

        # Unbekannte References
        for task in tasks:
            for dep in task.depends_on:
                if dep not in task_ids:
                    errors.append(f"Task '{task.title}' referenziert unbekannte Dependency '{dep}'")

        # Self-Dependencies
        for task in tasks:
            if task.id in task.depends_on:
                errors.append(f"Task '{task.title}' hat Self-Dependency")

        # Zyklus-Erkennung (DFS)
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        adj: Dict[str, List[str]] = defaultdict(list)
        for task in tasks:
            for dep in task.depends_on:
                adj[dep].append(task.id)

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for task in tasks:
            if task.id not in visited:
                if has_cycle(task.id):
                    errors.append("Zyklische Dependency erkannt im Task-DAG")
                    break

        return errors
