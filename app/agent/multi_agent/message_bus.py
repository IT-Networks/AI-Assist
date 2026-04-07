"""MessageBus – In-Memory Nachrichten-System zwischen Team-Agenten."""

import logging
from typing import Dict, List

from app.agent.multi_agent.models import AgentMessage

logger = logging.getLogger(__name__)


class MessageBus:
    """
    In-Memory Message-Passing zwischen Team-Agenten.

    Lebt nur innerhalb eines Team-Runs. Keine Persistenz.
    Performance: Index nach Empfaenger fuer O(1) Lookup statt O(n) Scan.
    """

    def __init__(self):
        self._messages: List[AgentMessage] = []
        self._index: Dict[str, List[int]] = {}  # agent_name → [message indices]
        self._broadcasts: List[int] = []  # Indices von Broadcast-Nachrichten

    def send(self, from_agent: str, to_agent: str, content: str) -> AgentMessage:
        """Sendet eine Nachricht an einen bestimmten Agenten."""
        msg = AgentMessage(from_agent=from_agent, to_agent=to_agent, content=content)
        idx = len(self._messages)
        self._messages.append(msg)

        # Index aktualisieren
        if to_agent == "*":
            self._broadcasts.append(idx)
        else:
            if to_agent not in self._index:
                self._index[to_agent] = []
            self._index[to_agent].append(idx)

        logger.debug(f"[MessageBus] {from_agent} -> {to_agent}: {content[:80]}...")
        return msg

    def broadcast(self, from_agent: str, content: str) -> AgentMessage:
        """Sendet eine Nachricht an alle Agenten."""
        return self.send(from_agent, "*", content)

    def get_for(self, agent_name: str) -> List[AgentMessage]:
        """Holt alle Nachrichten fuer einen Agenten (direkt + Broadcast). O(k) statt O(n)."""
        result = []
        # Direkte Nachrichten (via Index)
        for idx in self._index.get(agent_name, []):
            result.append(self._messages[idx])
        # Broadcasts (ohne eigene)
        for idx in self._broadcasts:
            msg = self._messages[idx]
            if msg.from_agent != agent_name:
                result.append(msg)
        result.sort(key=lambda m: m.timestamp)
        return result

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
        """Loescht alle Nachrichten und den Index."""
        self._messages.clear()
        self._index.clear()
        self._broadcasts.clear()

    @property
    def count(self) -> int:
        return len(self._messages)
