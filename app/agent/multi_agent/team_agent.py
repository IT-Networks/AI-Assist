"""
TeamAgent – Agent innerhalb eines Teams.

Erbt von SubAgent: Mini-LLM-Loop, Tool-Whitelist, Content-Extraktion.
Erweiterungen: Team-Context (SharedContext + MessageBus), Task-spezifischer Prompt.
"""

import logging
from typing import Optional

from app.agent.sub_agent import SubAgent
from app.agent.multi_agent.message_bus import MessageBus
from app.agent.multi_agent.models import TeamAgentConfig, TeamTask
from app.core.config import settings

logger = logging.getLogger(__name__)


class TeamAgent(SubAgent):
    """
    Agent innerhalb eines Teams.

    Wird vom AgentPool ausgefuehrt. Bekommt pro Task:
    - Die Aufgabenbeschreibung
    - Ergebnisse von Dependency-Tasks (SharedContext)
    - Nachrichten von anderen Agenten (MessageBus)
    """

    def __init__(self, config: TeamAgentConfig, message_bus: Optional[MessageBus] = None):
        super().__init__()
        self.name = config.name
        self.display_name = config.name.replace("_", " ").title()
        self.description = config.system_prompt or f"Team-Agent: {config.name}"
        self.allowed_tools = list(config.tools)
        self.max_iterations = config.max_turns
        self._message_bus = message_bus
        # TeamAgents brauchen das groessere Modell fuer Tool-Calls (nicht das kleine tool_model)
        self._model = config.model or settings.llm.default_model
        # Token-Tracking: Letzte Run-Tokens (wird nach jedem run_task aktualisiert)
        self.last_token_usage: int = 0
        # Diagramm: Letztes vom Agent generiertes Mermaid-Diagramm
        self.last_diagram: str = ""
        self.last_diagram_title: str = ""

    async def run_task(
        self,
        task: TeamTask,
        shared_context: str = "",
        original_goal: str = "",
    ) -> str:
        """
        Fuehrt einen Task aus mit Team-Kontext.

        Args:
            task: Der auszufuehrende Task
            shared_context: Ergebnisse von Dependency-Tasks
            original_goal: Die urspruengliche Nutzer-Frage (fuer Kontext)

        Returns:
            Ergebnis-Text (summary)
        """
        from app.agent import get_tool_registry
        from app.services.llm_client import llm_client as default_llm_client

        # Kontext zusammenbauen — Original-Frage IMMER mitgeben
        context_parts = []
        if original_goal:
            context_parts.append(
                f"URSPRUENGLICHE NUTZER-FRAGE: {original_goal}\n"
                f"Dein Task ist ein Teil dieser Gesamtaufgabe. "
                f"Deine Ergebnisse muessen zur Beantwortung DIESER Frage beitragen."
            )
        context_parts.append(f"DEINE ROLLE: {self.description}")

        if shared_context:
            context_parts.append(f"\nKONTEXT VON VORHERIGEN TASKS:\n{shared_context}")

        # Nachrichten an diesen Agent
        if self._message_bus:
            messages = self._message_bus.get_for(self.name)
            if messages:
                msg_text = "\n".join(f"[{m.from_agent}]: {m.content}" for m in messages[-5:])
                context_parts.append(f"\nNACHRICHTEN VON ANDEREN AGENTEN:\n{msg_text}")

        context = "\n".join(context_parts)

        logger.info(f"[TeamAgent:{self.name}] Starte Task '{task.title}' (Tools: {self.allowed_tools})")

        result = await self.run(
            query=f"Aufgabe: {task.title}\n\n{task.description}",
            llm_client=default_llm_client,
            tool_registry=get_tool_registry(),
            conversation_context=context,
        )

        # Token-Usage + Diagramm speichern
        self.last_token_usage = result.token_usage
        self.last_diagram = result.diagram or ""
        self.last_diagram_title = result.diagram_title or ""

        # Ergebnis auswerten — auch "unvollstaendige" Ergebnisse als Erfolg behandeln
        # Der SubAgent gibt success=False wenn kein JSON-Finish kam, aber der Agent
        # hat trotzdem gearbeitet (Tool-Calls gemacht, Ergebnisse erhalten).
        # In dem Fall nutzen wir den summary/key_findings als Ergebnis.
        summary = result.summary or ""
        if not summary and result.key_findings:
            summary = "\n".join(f"- {f}" for f in result.key_findings)

        if result.success or summary:
            logger.info(f"[TeamAgent:{self.name}] Task '{task.title}' abgeschlossen "
                       f"(success={result.success}, {len(result.key_findings)} Findings, {len(summary)} chars)")
            if self._message_bus and summary:
                self._message_bus.broadcast(
                    self.name,
                    f"Ergebnis von '{task.title}': {summary[:300]}"
                )
            return summary if summary else "(Agent hat gearbeitet aber kein Ergebnis formuliert)"
        else:
            error_msg = result.error or "Agent konnte keine Ergebnisse liefern (max_iterations erreicht)"
            logger.warning(f"[TeamAgent:{self.name}] Task '{task.title}' fehlgeschlagen: {error_msg}")
            return f"FEHLER: {error_msg}"
