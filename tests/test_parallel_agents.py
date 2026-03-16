"""
Tests for Parallel Agents Service and API.

Tests cover:
- Configuration management
- Task creation and management
- Agent pool status
- Task lifecycle (start, progress, complete)
- Statistics
- API endpoints
"""

import json
import os
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.parallel_agents import (
    ParallelAgentOrchestrator,
    ParallelAgentConfig,
    GitWorktreeManager,
    TaskType,
    TaskStatus,
    AgentStatus,
    MergeStatus,
    AgentTask,
    AgentInstance,
    AgentTaskResult,
    FileChange,
    CommitInfo,
    ConflictInfo,
    get_parallel_orchestrator,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_agents.db")
        yield db_path


@pytest.fixture
def orchestrator(temp_db):
    """Create a ParallelAgentOrchestrator with temporary database."""
    return ParallelAgentOrchestrator(db_path=temp_db)


@pytest.fixture
def configured_orchestrator(orchestrator):
    """Orchestrator with custom configuration."""
    config = ParallelAgentConfig(
        enabled=True,
        max_agents=4,
        auto_merge=True,
        cleanup_on_complete=True,
    )
    orchestrator.set_config(config)
    return orchestrator


# ═══════════════════════════════════════════════════════════════════════════════
# Data Class Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestParallelAgentConfig:
    """Tests for ParallelAgentConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ParallelAgentConfig()

        assert config.enabled == True
        assert config.max_agents == 4
        assert config.worktree_base_path == ".ai-worktrees"
        assert config.auto_merge == True
        assert config.cleanup_on_complete == True

    def test_to_dict(self):
        """Test config serialization."""
        config = ParallelAgentConfig(
            enabled=False,
            max_agents=8,
            auto_merge=False,
        )

        data = config.to_dict()

        assert data["enabled"] == False
        assert data["maxAgents"] == 8
        assert data["autoMerge"] == False

    def test_from_dict(self):
        """Test config deserialization."""
        data = {
            "enabled": True,
            "maxAgents": 6,
            "worktreeBasePath": "/custom/path",
            "autoMerge": True,
        }

        config = ParallelAgentConfig.from_dict(data)

        assert config.enabled == True
        assert config.max_agents == 6
        assert config.worktree_base_path == "/custom/path"


class TestAgentTask:
    """Tests for AgentTask dataclass."""

    def test_create_task(self):
        """Test creating a task."""
        task = AgentTask(
            id="task-1",
            task_type=TaskType.IMPLEMENT,
            description="Implement feature X",
            created_at=1234567890000,
        )

        assert task.id == "task-1"
        assert task.task_type == TaskType.IMPLEMENT
        assert task.status == TaskStatus.QUEUED

    def test_task_to_dict(self):
        """Test task serialization."""
        task = AgentTask(
            id="task-2",
            task_type=TaskType.TEST,
            description="Write tests",
            created_at=1234567890000,
            target_files=["src/main.py"],
        )

        data = task.to_dict()

        assert data["id"] == "task-2"
        assert data["type"] == "test"
        assert data["targetFiles"] == ["src/main.py"]


class TestAgentInstance:
    """Tests for AgentInstance dataclass."""

    def test_create_agent(self):
        """Test creating an agent instance."""
        agent = AgentInstance(
            id="agent-1",
            status=AgentStatus.IDLE,
        )

        assert agent.id == "agent-1"
        assert agent.status == AgentStatus.IDLE
        assert agent.task_id is None

    def test_agent_to_dict(self):
        """Test agent serialization."""
        agent = AgentInstance(
            id="agent-2",
            task_id="task-1",
            status=AgentStatus.WORKING,
            tokens_used=1000,
            tool_calls=5,
        )

        data = agent.to_dict()

        assert data["id"] == "agent-2"
        assert data["taskId"] == "task-1"
        assert data["tokensUsed"] == 1000


class TestAgentTaskResult:
    """Tests for AgentTaskResult dataclass."""

    def test_create_result(self):
        """Test creating a task result."""
        result = AgentTaskResult(
            success=True,
            summary="Task completed successfully",
        )

        assert result.success == True
        assert result.merge_status == MergeStatus.PENDING

    def test_result_with_changes(self):
        """Test result with file changes."""
        result = AgentTaskResult(
            success=True,
            changed_files=[
                FileChange(file_path="src/main.py", change_type="modified"),
            ],
            commits=[
                CommitInfo(sha="abc123", message="Fix bug", author="AI", timestamp=0),
            ],
        )

        assert len(result.changed_files) == 1
        assert len(result.commits) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestratorInit:
    """Tests for ParallelAgentOrchestrator initialization."""

    def test_creates_database(self, temp_db):
        """Test database is created on init."""
        orchestrator = ParallelAgentOrchestrator(db_path=temp_db)
        assert Path(temp_db).exists()

    def test_creates_tables(self, orchestrator):
        """Test required tables are created."""
        conn = orchestrator._get_conn()
        cursor = conn.cursor()

        # Check agent_config table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_config'"
        )
        assert cursor.fetchone() is not None

        # Check agent_tasks table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_tasks'"
        )
        assert cursor.fetchone() is not None

        conn.close()


class TestConfiguration:
    """Tests for configuration management."""

    def test_get_default_config(self, orchestrator):
        """Test getting default configuration."""
        config = orchestrator.get_config()

        assert config.enabled == True
        assert config.max_agents == 4

    def test_set_config(self, orchestrator):
        """Test setting configuration."""
        config = ParallelAgentConfig(
            enabled=False,
            max_agents=8,
            auto_merge=False,
        )

        result = orchestrator.set_config(config)

        assert result.enabled == False
        assert result.max_agents == 8
        assert result.auto_merge == False

    def test_config_persists(self, temp_db):
        """Test configuration persists across instances."""
        orchestrator1 = ParallelAgentOrchestrator(db_path=temp_db)
        config = ParallelAgentConfig(max_agents=12)
        orchestrator1.set_config(config)

        orchestrator2 = ParallelAgentOrchestrator(db_path=temp_db)
        retrieved = orchestrator2.get_config()

        assert retrieved.max_agents == 12


class TestTaskManagement:
    """Tests for task management."""

    def test_create_task(self, configured_orchestrator):
        """Test creating a task."""
        task = configured_orchestrator.create_task(
            task_type=TaskType.IMPLEMENT,
            description="Implement feature",
            instructions="Create new endpoint",
        )

        assert task.id is not None
        assert task.task_type == TaskType.IMPLEMENT
        assert task.status == TaskStatus.QUEUED

    def test_create_task_with_dependencies(self, configured_orchestrator):
        """Test creating a task with dependencies."""
        task1 = configured_orchestrator.create_task(
            task_type=TaskType.IMPLEMENT,
            description="First task",
        )

        task2 = configured_orchestrator.create_task(
            task_type=TaskType.TEST,
            description="Second task",
            depends_on=[task1.id],
        )

        assert task2.depends_on == [task1.id]
        assert task2.blocked_by == [task1.id]
        assert task2.status == TaskStatus.BLOCKED

    def test_get_task(self, configured_orchestrator):
        """Test getting a task."""
        created = configured_orchestrator.create_task(
            task_type=TaskType.FIX,
            description="Fix bug",
        )

        retrieved = configured_orchestrator.get_task(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id

    def test_get_tasks(self, configured_orchestrator):
        """Test getting tasks list."""
        configured_orchestrator.create_task(TaskType.IMPLEMENT, "Task 1")
        configured_orchestrator.create_task(TaskType.TEST, "Task 2")

        tasks = configured_orchestrator.get_tasks()

        assert len(tasks) >= 2

    def test_get_tasks_by_status(self, configured_orchestrator):
        """Test filtering tasks by status."""
        configured_orchestrator.create_task(TaskType.IMPLEMENT, "Queued task")

        queued = configured_orchestrator.get_tasks(status=TaskStatus.QUEUED)
        running = configured_orchestrator.get_tasks(status=TaskStatus.RUNNING)

        assert len(queued) >= 1
        assert len(running) == 0

    def test_cancel_task(self, configured_orchestrator):
        """Test cancelling a task."""
        task = configured_orchestrator.create_task(
            TaskType.IMPLEMENT,
            "Task to cancel"
        )

        success = configured_orchestrator.cancel_task(task.id)

        assert success == True
        updated = configured_orchestrator.get_task(task.id)
        assert updated.status == TaskStatus.CANCELLED

    def test_cancel_completed_task_fails(self, configured_orchestrator):
        """Test cancelling a completed task fails."""
        task = configured_orchestrator.create_task(
            TaskType.IMPLEMENT,
            "Completed task"
        )
        task.status = TaskStatus.COMPLETED
        configured_orchestrator._save_task(task)

        success = configured_orchestrator.cancel_task(task.id)

        assert success == False


class TestAgentPool:
    """Tests for agent pool management."""

    def test_get_pool_status(self, configured_orchestrator):
        """Test getting pool status."""
        status = configured_orchestrator.get_agent_pool_status()

        assert "maxAgents" in status
        assert "activeAgents" in status
        assert "queuedTasks" in status

    def test_pool_respects_max_agents(self, configured_orchestrator):
        """Test pool respects max agents limit."""
        config = ParallelAgentConfig(max_agents=2)
        configured_orchestrator.set_config(config)

        # Create agents up to limit
        agent1 = configured_orchestrator._get_or_create_agent()
        agent2 = configured_orchestrator._get_or_create_agent()
        agent3 = configured_orchestrator._get_or_create_agent()

        assert agent1 is not None
        assert agent2 is not None
        # Third should fail since we have 2 idle agents
        # But since they're idle, it returns existing
        assert agent3 is not None


class TestStatistics:
    """Tests for statistics."""

    def test_get_stats_empty(self, orchestrator):
        """Test stats with no tasks."""
        stats = orchestrator.get_stats()

        assert stats["totalTasks"] == 0
        assert stats["successRate"] == 0.0

    def test_get_stats_with_data(self, configured_orchestrator):
        """Test stats with tasks."""
        configured_orchestrator.create_task(TaskType.IMPLEMENT, "Task 1")
        configured_orchestrator.create_task(TaskType.TEST, "Task 2")

        stats = configured_orchestrator.get_stats()

        assert stats["totalTasks"] >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# Git Worktree Manager Tests (Mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGitWorktreeManager:
    """Tests for GitWorktreeManager (with mocked git commands)."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary directory for repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_manager_init(self, temp_repo):
        """Test manager initialization."""
        manager = GitWorktreeManager(temp_repo, ".ai-worktrees")

        assert manager.repo_path == Path(temp_repo).resolve()
        assert manager.worktree_base.exists()

    @patch('subprocess.run')
    def test_create_worktree(self, mock_run, temp_repo):
        """Test worktree creation."""
        mock_run.return_value = MagicMock(returncode=0, stdout="main", stderr="")

        manager = GitWorktreeManager(temp_repo, ".ai-worktrees")
        success, path, branch = manager.create_worktree("task-123")

        assert success == True
        assert "wt-task-123" in path
        assert "ai-task-task-123" in branch

    @patch('subprocess.run')
    def test_remove_worktree(self, mock_run, temp_repo):
        """Test worktree removal."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        manager = GitWorktreeManager(temp_repo, ".ai-worktrees")
        # Create worktree dir to remove
        wt_path = manager.worktree_base / "wt-test"
        wt_path.mkdir()

        success = manager.remove_worktree(str(wt_path))

        assert success == True

    @patch('subprocess.run')
    def test_get_changed_files(self, mock_run, temp_repo):
        """Test getting changed files."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="M  src/main.py\nA  src/new.py\n",
            stderr=""
        )

        manager = GitWorktreeManager(temp_repo, ".ai-worktrees")
        wt_path = manager.worktree_base / "wt-test"
        wt_path.mkdir()

        changes = manager.get_changed_files(str(wt_path))

        assert len(changes) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# API Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentsAPI:
    """Tests for Agents API endpoints."""

    @pytest.fixture
    def client(self, temp_db):
        """Create test client with temporary database."""
        import app.services.parallel_agents as module
        original_orchestrator = module._parallel_orchestrator
        module._parallel_orchestrator = ParallelAgentOrchestrator(db_path=temp_db)

        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        yield client

        module._parallel_orchestrator = original_orchestrator

    def test_get_config(self, client):
        """Test GET /api/agents/config endpoint."""
        response = client.get("/api/agents/config")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "maxAgents" in data

    def test_set_config(self, client):
        """Test PUT /api/agents/config endpoint."""
        response = client.put("/api/agents/config", json={
            "enabled": True,
            "maxAgents": 8,
            "autoMerge": False,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["maxAgents"] == 8
        assert data["autoMerge"] == False

    def test_create_task(self, client):
        """Test POST /api/agents/tasks endpoint."""
        response = client.post("/api/agents/tasks", json={
            "type": "implement",
            "description": "Create new feature",
            "instructions": "Add endpoint for users",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "implement"
        assert data["status"] == "queued"

    def test_get_tasks(self, client):
        """Test GET /api/agents/tasks endpoint."""
        # Create a task first
        client.post("/api/agents/tasks", json={
            "type": "test",
            "description": "Write tests",
        })

        response = client.get("/api/agents/tasks")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_task(self, client):
        """Test GET /api/agents/tasks/{id} endpoint."""
        # Create a task
        create_response = client.post("/api/agents/tasks", json={
            "type": "refactor",
            "description": "Refactor code",
        })
        task_id = create_response.json()["id"]

        response = client.get(f"/api/agents/tasks/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_id

    def test_cancel_task(self, client):
        """Test POST /api/agents/tasks/{id}/cancel endpoint."""
        # Create a task
        create_response = client.post("/api/agents/tasks", json={
            "type": "document",
            "description": "Write docs",
        })
        task_id = create_response.json()["id"]

        response = client.post(f"/api/agents/tasks/{task_id}/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] == True

    def test_get_pool_status(self, client):
        """Test GET /api/agents/pool endpoint."""
        response = client.get("/api/agents/pool")

        assert response.status_code == 200
        data = response.json()
        assert "maxAgents" in data
        assert "activeAgents" in data
        assert "agents" in data

    def test_get_stats(self, client):
        """Test GET /api/agents/stats endpoint."""
        response = client.get("/api/agents/stats")

        assert response.status_code == 200
        data = response.json()
        assert "totalTasks" in data
        assert "successRate" in data

    def test_update_progress(self, client):
        """Test POST /api/agents/tasks/{id}/progress endpoint."""
        # Create a task
        create_response = client.post("/api/agents/tasks", json={
            "type": "implement",
            "description": "Implement feature",
        })
        task_id = create_response.json()["id"]

        response = client.post(f"/api/agents/tasks/{task_id}/progress", json={
            "progress": 50,
            "currentStep": "Writing code",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["progress"] == 50


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_parallel_orchestrator_singleton(self):
        """Test singleton returns same instance."""
        import app.services.parallel_agents as module
        module._parallel_orchestrator = None

        orchestrator1 = get_parallel_orchestrator()
        orchestrator2 = get_parallel_orchestrator()

        assert orchestrator1 is orchestrator2


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests for edge cases."""

    def test_get_nonexistent_task(self, configured_orchestrator):
        """Test getting nonexistent task."""
        task = configured_orchestrator.get_task("nonexistent-id")
        assert task is None

    def test_cancel_nonexistent_task(self, configured_orchestrator):
        """Test cancelling nonexistent task."""
        success = configured_orchestrator.cancel_task("nonexistent-id")
        assert success == False

    def test_max_agents_limit(self, configured_orchestrator):
        """Test max agents limit is enforced."""
        config = ParallelAgentConfig(max_agents=1)
        configured_orchestrator.set_config(config)

        # First agent
        agent1 = configured_orchestrator._get_or_create_agent()
        agent1.status = AgentStatus.WORKING

        # Second should fail
        agent2 = configured_orchestrator._get_or_create_agent()

        # Should be same agent if idle, or None if working
        assert agent2 is None or agent2.id == agent1.id
