"""MessageBus – In-Memory Nachrichten-System zwischen Team-Agenten."""

import logging
from typing import List

from app.agent.multi_agent.models import AgentMessage

logger = logging.getLogger(__name__)


class MessageBus:
    """
    In-Memory Message-Passing zwischen Team-Agenten.

    Lebt nur innerhalb eines Team-Runs. Keine Persistenz.
    """

    def __init__(self):
        self._messages: List[AgentMessage] = []

    def send(self, from_agent: str, to_agent: str, content: str) -> AgentMessage:
        """Sendet eine Nachricht an einen bestimmten Agenten."""
        msg = AgentMessage(from_agent=from_agent, to_agent=to_agent, content=content)
        self._messages.append(msg)
        logger.debug(f"[MessageBus] {from_agent} → {to_agent}: {content[:80]}...")
        return msg

    def broadcast(self, from_agent: str, content: str) -> AgentMessage:
        """Sendet eine Nachricht an alle Agenten."""
        return self.send(from_agent, "*", content)

    def get_for(self, agent_name: str) -> List[AgentMessage]:
        """Holt alle Nachrichten fuer einen Agenten (direkt + Broadcast)."""
        return [
            m for m in self._messages
            if m.to_agent == agent_name or (m.to_agent == "*" and m.from_agent != agent_name)
        ]

    def get_conversation(self, agent1: str, agent2: str) -> List[AgentMessage]:
        """Holt Konversation zwischen zwei Agenten."""
        return [
            m for m in self._messages
            if (m.from_agent == agent1 and m.to_agent == agent2)
            or (m.from_agent == agent2 and m.to_agent == agent1)
        ]

    def get_all(self) -> List[AgentMessage]:
        """Gibt alle Nachrichten zurueck."""
        return list(self._messages)

    def clear(self):
        """Loescht alle Nachrichten."""
        self._messages.clear()

    @property
    def count(self) -> int:
        return len(self._messages)
