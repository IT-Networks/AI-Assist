"""
Tests fuer das Multi-Agent Team System.

Testet: Models, MessageBus, TaskScheduler, AgentPool, Config, Events.
"""

import asyncio
import pytest

from app.agent.multi_agent.models import (
    AgentMessage, TeamAgentConfig, TeamConfig, TeamRunResult, TeamTask,
)
from app.agent.multi_agent.message_bus import MessageBus
from app.agent.multi_agent.scheduler import TaskScheduler


# ══════════════════════════════════════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamTask:
    def test_is_ready_no_deps(self):
        task = TeamTask(id="t1", title="A")
        assert task.is_ready(set()) is True

    def test_is_ready_deps_satisfied(self):
        task = TeamTask(id="t2", title="B", depends_on=["t1"])
        assert task.is_ready({"t1"}) is True

    def test_is_ready_deps_not_satisfied(self):
        task = TeamTask(id="t2", title="B", depends_on=["t1"])
        assert task.is_ready(set()) is False

    def test_is_ready_wrong_status(self):
        task = TeamTask(id="t1", title="A", status="completed")
        assert task.is_ready(set()) is False


class TestTeamConfig:
    def test_get_agent(self):
        config = TeamConfig(name="test", agents=[
            TeamAgentConfig(name="a"), TeamAgentConfig(name="b")
        ])
        assert config.get_agent("a").name == "a"
        assert config.get_agent("z") is None

    def test_agent_names(self):
        config = TeamConfig(name="test", agents=[
            TeamAgentConfig(name="x"), TeamAgentConfig(name="y")
        ])
        assert config.agent_names() == ["x", "y"]


# ══════════════════════════════════════════════════════════════════════════════
# MessageBus
# ══════════════════════════════════════════════════════════════════════════════

class TestMessageBus:
    def test_send_and_get(self):
        bus = MessageBus()
        bus.send("agent_a", "agent_b", "Hello")
        msgs = bus.get_for("agent_b")
        assert len(msgs) == 1
        assert msgs[0].content == "Hello"

    def test_broadcast(self):
        bus = MessageBus()
        bus.broadcast("agent_a", "Broadcast msg")
        msgs_b = bus.get_for("agent_b")
        msgs_a = bus.get_for("agent_a")  # Sender soll eigenen Broadcast nicht sehen
        assert len(msgs_b) == 1
        assert len(msgs_a) == 0

    def test_get_conversation(self):
        bus = MessageBus()
        bus.send("a", "b", "msg1")
        bus.send("b", "a", "msg2")
        bus.send("c", "a", "msg3")
        conv = bus.get_conversation("a", "b")
        assert len(conv) == 2

    def test_clear(self):
        bus = MessageBus()
        bus.send("a", "b", "test")
        bus.clear()
        assert bus.count == 0

    def test_count(self):
        bus = MessageBus()
        bus.send("a", "b", "1")
        bus.send("a", "b", "2")
        assert bus.count == 2


# ══════════════════════════════════════════════════════════════════════════════
# TaskScheduler
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskScheduler:
    def _make_tasks(self):
        return [
            TeamTask(id="t1", title="Analyse", assignee="analyst"),
            TeamTask(id="t2", title="Security", assignee="security"),
            TeamTask(id="t3", title="Review", assignee="reviewer", depends_on=["t1", "t2"]),
        ]

    def _make_agents(self):
        return [
            TeamAgentConfig(name="analyst", system_prompt="Analysiert Code"),
            TeamAgentConfig(name="security", system_prompt="Prueft Sicherheit"),
            TeamAgentConfig(name="reviewer", system_prompt="Reviewt Ergebnisse"),
        ]

    def test_topological_sort_basic(self):
        scheduler = TaskScheduler()
        tasks = self._make_tasks()
        ordered = scheduler._topological_sort(tasks)
        ids = [t.id for t in ordered]
        # t3 muss nach t1 und t2 kommen
        assert ids.index("t3") > ids.index("t1")
        assert ids.index("t3") > ids.index("t2")

    def test_topological_sort_no_deps(self):
        scheduler = TaskScheduler()
        tasks = [TeamTask(id="a", title="A"), TeamTask(id="b", title="B")]
        ordered = scheduler._topological_sort(tasks)
        assert len(ordered) == 2

    def test_schedule_assigns_agents(self):
        scheduler = TaskScheduler()
        tasks = [TeamTask(id="t1", title="Analysiere Code")]
        agents = self._make_agents()
        ordered = scheduler.schedule(tasks, agents)
        assert ordered[0].assignee in [a.name for a in agents]

    def test_validate_no_errors(self):
        errors = TaskScheduler.validate_dependencies(self._make_tasks())
        assert errors == []

    def test_validate_self_dependency(self):
        tasks = [TeamTask(id="t1", title="A", depends_on=["t1"])]
        errors = TaskScheduler.validate_dependencies(tasks)
        assert any("Self-Dependency" in e for e in errors)

    def test_validate_unknown_dependency(self):
        tasks = [TeamTask(id="t1", title="A", depends_on=["nonexistent"])]
        errors = TaskScheduler.validate_dependencies(tasks)
        assert any("unbekannte" in e for e in errors)

    def test_validate_cycle(self):
        tasks = [
            TeamTask(id="t1", title="A", depends_on=["t2"]),
            TeamTask(id="t2", title="B", depends_on=["t1"]),
        ]
        errors = TaskScheduler.validate_dependencies(tasks)
        assert any("Zyklisch" in e for e in errors)

    def test_capability_match(self):
        scheduler = TaskScheduler()
        task = TeamTask(id="t1", title="Security Analyse durchfuehren")
        agents = self._make_agents()
        best = scheduler._best_agent_for_task(task, agents)
        assert best == "security"  # Keyword-Overlap "Sicherheit" vs "Security"

    def test_critical_path_ordering(self):
        scheduler = TaskScheduler(strategy="dependency-first")
        tasks = [
            TeamTask(id="t1", title="Root"),
            TeamTask(id="t2", title="Branch A", depends_on=["t1"]),
            TeamTask(id="t3", title="Branch B", depends_on=["t1"]),
            TeamTask(id="t4", title="Leaf", depends_on=["t2", "t3"]),
        ]
        agents = [TeamAgentConfig(name="a")]
        ordered = scheduler.schedule(tasks, agents)
        ids = [t.id for t in ordered]
        # t1 blockiert am meisten (t2, t3, t4 transitiv) → sollte zuerst kommen
        assert ids[0] == "t1"


# ══════════════════════════════════════════════════════════════════════════════
# AgentPool
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentPool:
    def test_get_status_empty(self):
        from app.agent.multi_agent.agent_pool import AgentPool
        pool = AgentPool(max_concurrent=2)
        status = pool.get_status()
        assert status["total"] == 0
        assert status["running"] == 0

    def test_register(self):
        from app.agent.multi_agent.agent_pool import AgentPool
        from app.agent.multi_agent.team_agent import TeamAgent
        pool = AgentPool()
        agent = TeamAgent(TeamAgentConfig(name="test_agent", tools=[]))
        pool.register(agent)
        assert pool.get("test_agent") is not None
        assert pool.get("nonexistent") is None


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiAgentConfig:
    def test_config_loaded(self):
        from app.core.config import settings
        assert hasattr(settings, "multi_agent")
        assert settings.multi_agent.enabled is False
        assert settings.multi_agent.max_concurrent_agents == 3
        assert settings.multi_agent.default_strategy == "dependency-first"

    def test_teams_default_empty(self):
        from app.core.config import settings
        assert isinstance(settings.multi_agent.teams, list)


# ══════════════════════════════════════════════════════════════════════════════
# Events
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamEvents:
    def test_team_events_in_mapping(self):
        from app.agent.orchestration.types import AgentEventType, MCP_EVENT_TYPE_MAPPING
        team_events = [e for e in AgentEventType if e.value.startswith("team_")]
        assert len(team_events) >= 7  # started, planned, executing, completed, failed, synthesizing, complete

    def test_team_started_mapping(self):
        from app.agent.orchestration.types import AgentEventType, MCP_EVENT_TYPE_MAPPING
        assert MCP_EVENT_TYPE_MAPPING["team_started"] == AgentEventType.TEAM_STARTED

    def test_team_complete_mapping(self):
        from app.agent.orchestration.types import AgentEventType, MCP_EVENT_TYPE_MAPPING
        assert MCP_EVENT_TYPE_MAPPING["team_complete"] == AgentEventType.TEAM_COMPLETE
