"""Datenmodelle fuer das Multi-Agent Team System."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TeamAgentConfig:
    """Definition eines Agenten innerhalb eines Teams."""
    name: str
    model: str = ""                    # Leer = default_model
    system_prompt: str = ""
    tools: List[str] = field(default_factory=list)
    max_turns: int = 15


@dataclass
class TeamConfig:
    """Definition eines Teams (geladen aus config.yaml)."""
    name: str
    description: str = ""
    agents: List[TeamAgentConfig] = field(default_factory=list)
    strategy: str = "dependency-first"
    max_parallel: int = 3

    def get_agent(self, name: str) -> Optional[TeamAgentConfig]:
        return next((a for a in self.agents if a.name == name), None)

    def agent_names(self) -> List[str]:
        return [a.name for a in self.agents]


@dataclass
class TeamTask:
    """Ein Task innerhalb eines Team-Runs."""
    id: str = field(default_factory=lambda: f"t-{uuid.uuid4().hex[:6]}")
    title: str = ""
    description: str = ""
    assignee: str = ""                 # Agent-Name
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"            # pending | in_progress | completed | failed | blocked
    result: str = ""
    error: str = ""

    def is_ready(self, completed_ids: set) -> bool:
        """Prueft ob alle Dependencies erfuellt sind."""
        if self.status != "pending":
            return False
        return all(dep in completed_ids for dep in self.depends_on)


@dataclass
class AgentMessage:
    """Nachricht zwischen Agenten."""
    from_agent: str
    to_agent: str                      # "*" fuer Broadcast
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class TeamRunResult:
    """Ergebnis eines Team-Runs."""
    team_name: str
    goal: str
    tasks: List[TeamTask] = field(default_factory=list)
    messages: List[AgentMessage] = field(default_factory=list)
    final_summary: str = ""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    duration_seconds: float = 0
    # Token-Tracking (ueber alle Agents aggregiert)
    total_tokens: int = 0
    total_llm_calls: int = 0
