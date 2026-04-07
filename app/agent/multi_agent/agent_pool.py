"""
AgentPool – Semaphore-basierte parallele Agent-Ausfuehrung.

Verwaltet TeamAgents und begrenzt die gleichzeitige Ausfuehrung
via asyncio.Semaphore (analog zu open-multi-agent AgentPool).
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from app.agent.multi_agent.models import TeamTask
from app.agent.multi_agent.team_agent import TeamAgent

logger = logging.getLogger(__name__)


class AgentPool:
    """
    Pool fuer parallele Agent-Ausfuehrung mit Concurrency-Limit.

    Usage:
        pool = AgentPool(max_concurrent=3)
        pool.register(agent1)
        pool.register(agent2)
        results = await pool.execute_batch([
            ("agent1", task1, "context1"),
            ("agent2", task2, "context2"),
        ])
    """

    def __init__(self, max_concurrent: int = 3):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._agents: Dict[str, TeamAgent] = {}
        self._running: Dict[str, str] = {}  # agent_name → task_id
        self._completed: int = 0
        self._failed: int = 0

    def register(self, agent: TeamAgent):
        """Registriert einen Agent im Pool."""
        self._agents[agent.name] = agent

    def get(self, name: str) -> Optional[TeamAgent]:
        return self._agents.get(name)

    async def execute(
        self,
        agent_name: str,
        task: TeamTask,
        context: str = "",
        timeout: float = 120.0,
        original_goal: str = "",
    ) -> str:
        """
        Fuehrt einen Task mit Semaphore-Guard aus.

        Args:
            agent_name: Name des ausfuehrenden Agents
            task: Der Task
            context: SharedContext-String
            timeout: Max. Ausfuehrungszeit in Sekunden
            original_goal: Urspruengliche Nutzer-Frage

        Returns:
            Ergebnis-String oder Fehler-Nachricht
        """
        agent = self._agents.get(agent_name)
        if not agent:
            return f"FEHLER: Agent '{agent_name}' nicht im Pool registriert"

        async with self._semaphore:
            self._running[agent_name] = task.id
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    agent.run_task(task, context, original_goal=original_goal),
                    timeout=timeout,
                )
                self._completed += 1
                duration = time.time() - start
                logger.info(f"[AgentPool] {agent_name} fertig: '{task.title}' in {duration:.1f}s")
                return result
            except asyncio.TimeoutError:
                self._failed += 1
                logger.warning(f"[AgentPool] {agent_name} Timeout nach {timeout}s fuer '{task.title}'")
                return f"FEHLER: Timeout nach {timeout}s"
            except Exception as e:
                self._failed += 1
                logger.error(f"[AgentPool] {agent_name} Fehler bei '{task.title}': {e}", exc_info=True)
                return f"FEHLER: {type(e).__name__}: {e}"
            finally:
                self._running.pop(agent_name, None)

    async def execute_batch(
        self,
        assignments: List[Tuple[str, TeamTask, str]],
        timeout: float = 120.0,
        original_goal: str = "",
    ) -> Dict[str, str]:
        """
        Fuehrt mehrere Tasks parallel aus (bis Semaphore-Limit).

        Args:
            assignments: Liste von (agent_name, task, context)
            timeout: Max. Ausfuehrungszeit pro Task
            original_goal: Urspruengliche Nutzer-Frage

        Returns:
            Dict task_id → Ergebnis-String
        """
        tasks = [
            self.execute(agent_name, task, context, timeout, original_goal=original_goal)
            for agent_name, task, context in assignments
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        result_map: Dict[str, str] = {}
        for (agent_name, task, _), result in zip(assignments, results):
            if isinstance(result, Exception):
                result_map[task.id] = f"FEHLER: {result}"
            else:
                result_map[task.id] = result

        return result_map

    def get_status(self) -> Dict:
        return {
            "total": len(self._agents),
            "running": len(self._running),
            "idle": len(self._agents) - len(self._running),
            "completed": self._completed,
            "failed": self._failed,
        }
