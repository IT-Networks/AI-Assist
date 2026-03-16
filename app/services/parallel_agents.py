"""
Parallel Agents Service - Multi-Agent Task Execution in Git Worktrees.

Features:
- Agent pool management with configurable max agents
- Git worktree isolation for parallel work
- Task queue with dependency handling
- Automatic and manual merge support
- Conflict detection and resolution
"""

import asyncio
import logging
import os
import shutil
import sqlite3
import subprocess
import uuid

from app.utils.json_utils import json_loads, json_dumps
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
import threading

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════

class TaskType(str, Enum):
    """Types of agent tasks."""
    IMPLEMENT = "implement"
    REFACTOR = "refactor"
    TEST = "test"
    DOCUMENT = "document"
    REVIEW = "review"
    FIX = "fix"
    CUSTOM = "custom"


class TaskStatus(str, Enum):
    """Task status values."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class AgentStatus(str, Enum):
    """Agent instance status."""
    IDLE = "idle"
    WORKING = "working"
    MERGING = "merging"
    ERROR = "error"


class MergeStatus(str, Enum):
    """Merge status values."""
    PENDING = "pending"
    MERGED = "merged"
    CONFLICT = "conflict"
    SKIPPED = "skipped"


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Column Constants (Performance: avoid SELECT *)
# ═══════════════════════════════════════════════════════════════════════════════

_CONFIG_COLUMNS = """id, enabled, max_agents, worktree_base_path, auto_merge,
    cleanup_on_complete, task_timeout_seconds, repo_path"""

_TASK_COLUMNS = """id, task_type, description, instructions, target_files, target_directories,
    depends_on, status, progress, created_at, started_at, completed_at, agent_id,
    worktree_path, branch_name, result_json"""


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ParallelAgentConfig:
    """Configuration for parallel agents."""
    enabled: bool = True
    max_agents: int = 4
    worktree_base_path: str = ".ai-worktrees"
    auto_merge: bool = True
    cleanup_on_complete: bool = True
    task_timeout_seconds: int = 600  # 10 minutes
    repo_path: str = "."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "maxAgents": self.max_agents,
            "worktreeBasePath": self.worktree_base_path,
            "autoMerge": self.auto_merge,
            "cleanupOnComplete": self.cleanup_on_complete,
            "taskTimeoutSeconds": self.task_timeout_seconds,
            "repoPath": self.repo_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParallelAgentConfig":
        return cls(
            enabled=data.get("enabled", True),
            max_agents=data.get("maxAgents", 4),
            worktree_base_path=data.get("worktreeBasePath", ".ai-worktrees"),
            auto_merge=data.get("autoMerge", True),
            cleanup_on_complete=data.get("cleanupOnComplete", True),
            task_timeout_seconds=data.get("taskTimeoutSeconds", 600),
            repo_path=data.get("repoPath", "."),
        )


@dataclass
class FileChange:
    """Record of a file change."""
    file_path: str
    change_type: str  # added, modified, deleted
    additions: int = 0
    deletions: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CommitInfo:
    """Information about a commit."""
    sha: str
    message: str
    author: str
    timestamp: int
    files_changed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConflictInfo:
    """Information about a merge conflict."""
    file_path: str
    our_changes: str
    their_changes: str
    suggested_resolution: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filePath": self.file_path,
            "ourChanges": self.our_changes,
            "theirChanges": self.their_changes,
            "suggestedResolution": self.suggested_resolution,
        }


@dataclass
class AgentTaskResult:
    """Result of a completed agent task."""
    success: bool
    changed_files: List[FileChange] = field(default_factory=list)
    commits: List[CommitInfo] = field(default_factory=list)
    summary: str = ""
    merge_status: MergeStatus = MergeStatus.PENDING
    conflicts: List[ConflictInfo] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "changedFiles": [f.to_dict() for f in self.changed_files],
            "commits": [c.to_dict() for c in self.commits],
            "summary": self.summary,
            "mergeStatus": self.merge_status.value,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "errorMessage": self.error_message,
        }


@dataclass
class AgentTask:
    """A task to be executed by an agent."""
    id: str
    task_type: TaskType
    description: str
    created_at: int

    # Scope
    target_files: List[str] = field(default_factory=list)
    target_directories: List[str] = field(default_factory=list)
    instructions: str = ""

    # Dependencies
    depends_on: List[str] = field(default_factory=list)
    blocked_by: List[str] = field(default_factory=list)

    # Status
    status: TaskStatus = TaskStatus.QUEUED
    progress: int = 0
    started_at: Optional[int] = None
    completed_at: Optional[int] = None

    # Assignment
    agent_id: Optional[str] = None
    worktree_path: Optional[str] = None
    branch_name: Optional[str] = None

    # Results
    result: Optional[AgentTaskResult] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.task_type.value,
            "description": self.description,
            "createdAt": self.created_at,
            "targetFiles": self.target_files,
            "targetDirectories": self.target_directories,
            "instructions": self.instructions,
            "dependsOn": self.depends_on,
            "blockedBy": self.blocked_by,
            "status": self.status.value,
            "progress": self.progress,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "agentId": self.agent_id,
            "worktreePath": self.worktree_path,
            "branchName": self.branch_name,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass
class AgentInstance:
    """A running agent instance."""
    id: str
    task_id: Optional[str] = None
    worktree_path: Optional[str] = None
    branch_name: Optional[str] = None
    status: AgentStatus = AgentStatus.IDLE
    started_at: Optional[int] = None

    # Metrics
    tokens_used: int = 0
    tool_calls: int = 0
    current_step: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "taskId": self.task_id,
            "worktreePath": self.worktree_path,
            "branchName": self.branch_name,
            "status": self.status.value,
            "startedAt": self.started_at,
            "tokensUsed": self.tokens_used,
            "toolCalls": self.tool_calls,
            "currentStep": self.current_step,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Git Worktree Manager
# ═══════════════════════════════════════════════════════════════════════════════

class GitWorktreeManager:
    """Manages Git worktrees for parallel agent execution."""

    def __init__(self, repo_path: str, worktree_base: str):
        self.repo_path = Path(repo_path).resolve()
        self.worktree_base = self.repo_path / worktree_base
        self.worktree_base.mkdir(parents=True, exist_ok=True)

    def _run_git(self, args: List[str], cwd: Path = None) -> Tuple[bool, str]:
        """Run a git command and return (success, output)."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd or self.repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "Git command timed out"
        except Exception as e:
            return False, str(e)

    def create_worktree(self, task_id: str) -> Tuple[bool, str, str]:
        """
        Create a worktree for a task.

        Returns:
            Tuple of (success, worktree_path, branch_name)
        """
        branch_name = f"ai-task-{task_id[:8]}"
        worktree_path = self.worktree_base / f"wt-{task_id[:8]}"

        # Check if worktree already exists
        if worktree_path.exists():
            return True, str(worktree_path), branch_name

        # Get current branch
        success, current_branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if not success:
            current_branch = "main"
        current_branch = current_branch.strip()

        # Create worktree with new branch
        success, output = self._run_git([
            "worktree", "add", "-b", branch_name,
            str(worktree_path), current_branch
        ])

        if not success:
            # Try without -b if branch exists
            success, output = self._run_git([
                "worktree", "add", str(worktree_path), branch_name
            ])

        if success:
            logger.info(f"Created worktree at {worktree_path} on branch {branch_name}")
            return True, str(worktree_path), branch_name
        else:
            logger.error(f"Failed to create worktree: {output}")
            return False, output, ""

    def remove_worktree(self, worktree_path: str, force: bool = False) -> bool:
        """Remove a worktree."""
        path = Path(worktree_path)
        if not path.exists():
            return True

        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))

        success, output = self._run_git(args)

        if success:
            logger.info(f"Removed worktree at {worktree_path}")
        else:
            # Try force removal
            if not force:
                return self.remove_worktree(worktree_path, force=True)
            logger.error(f"Failed to remove worktree: {output}")

        return success

    def delete_branch(self, branch_name: str, force: bool = False) -> bool:
        """Delete a branch."""
        args = ["branch", "-d" if not force else "-D", branch_name]
        success, output = self._run_git(args)

        if success:
            logger.info(f"Deleted branch {branch_name}")
        else:
            if not force:
                return self.delete_branch(branch_name, force=True)
            logger.warning(f"Failed to delete branch: {output}")

        return success

    def get_changed_files(self, worktree_path: str) -> List[FileChange]:
        """Get list of changed files in a worktree."""
        path = Path(worktree_path)
        if not path.exists():
            return []

        # Get status
        success, output = self._run_git(["status", "--porcelain"], cwd=path)
        if not success:
            return []

        changes = []
        for line in output.strip().split("\n"):
            if not line:
                continue

            status = line[:2].strip()
            file_path = line[3:].strip()

            if status in ("A", "??"):
                change_type = "added"
            elif status == "D":
                change_type = "deleted"
            else:
                change_type = "modified"

            changes.append(FileChange(
                file_path=file_path,
                change_type=change_type,
            ))

        return changes

    def commit_changes(
        self,
        worktree_path: str,
        message: str,
        author: str = "AI-Assist <ai@assist.local>"
    ) -> Optional[CommitInfo]:
        """Commit all changes in a worktree."""
        path = Path(worktree_path)
        if not path.exists():
            return None

        # Stage all changes
        success, _ = self._run_git(["add", "-A"], cwd=path)
        if not success:
            return None

        # Check if there are changes to commit
        success, status = self._run_git(["status", "--porcelain"], cwd=path)
        if not status.strip():
            return None  # Nothing to commit

        # Commit
        success, output = self._run_git([
            "commit", "-m", message,
            "--author", author
        ], cwd=path)

        if not success:
            logger.error(f"Failed to commit: {output}")
            return None

        # Get commit info
        success, sha = self._run_git(["rev-parse", "HEAD"], cwd=path)
        if not success:
            return None

        return CommitInfo(
            sha=sha.strip()[:8],
            message=message,
            author=author,
            timestamp=int(datetime.now().timestamp() * 1000),
        )

    def merge_branch(
        self,
        branch_name: str,
        message: str = None
    ) -> Tuple[MergeStatus, List[ConflictInfo]]:
        """
        Merge a branch into the current branch.

        Returns:
            Tuple of (merge_status, conflicts)
        """
        if not message:
            message = f"AI: Merge {branch_name}"

        # Attempt merge
        success, output = self._run_git([
            "merge", branch_name, "--no-ff", "-m", message
        ])

        if success:
            return MergeStatus.MERGED, []

        # Check for conflicts
        if "CONFLICT" in output or "Automatic merge failed" in output:
            conflicts = self._get_conflicts()
            return MergeStatus.CONFLICT, conflicts

        return MergeStatus.PENDING, []

    def _get_conflicts(self) -> List[ConflictInfo]:
        """Get list of conflicting files."""
        success, output = self._run_git(["diff", "--name-only", "--diff-filter=U"])
        if not success:
            return []

        conflicts = []
        for file_path in output.strip().split("\n"):
            if not file_path:
                continue

            # Read conflict markers
            file_full_path = self.repo_path / file_path
            if file_full_path.exists():
                try:
                    content = file_full_path.read_text()
                    conflicts.append(ConflictInfo(
                        file_path=file_path,
                        our_changes="See file for details",
                        their_changes="See file for details",
                    ))
                except Exception:
                    pass

        return conflicts

    def abort_merge(self) -> bool:
        """Abort an in-progress merge."""
        success, _ = self._run_git(["merge", "--abort"])
        return success

    def list_worktrees(self) -> List[str]:
        """List all active worktrees."""
        success, output = self._run_git(["worktree", "list", "--porcelain"])
        if not success:
            return []

        worktrees = []
        for line in output.split("\n"):
            if line.startswith("worktree "):
                path = line[9:].strip()
                if str(self.worktree_base) in path:
                    worktrees.append(path)

        return worktrees


# ═══════════════════════════════════════════════════════════════════════════════
# Parallel Agent Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class ParallelAgentOrchestrator:
    """
    Orchestrates multiple AI agents working in parallel.

    Features:
    - Agent pool management
    - Task queue with dependencies
    - Git worktree isolation
    - Result merging
    """

    def __init__(self, db_path: str = "./data/parallel_agents.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

        self._config: Optional[ParallelAgentConfig] = None
        self._agents: Dict[str, AgentInstance] = {}
        self._tasks: Dict[str, AgentTask] = {}
        self._worktree_manager: Optional[GitWorktreeManager] = None
        self._lock = threading.Lock()
        self._task_executor: Optional[Callable] = None

    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Config table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 1,
                max_agents INTEGER DEFAULT 4,
                worktree_base_path TEXT DEFAULT '.ai-worktrees',
                auto_merge INTEGER DEFAULT 1,
                cleanup_on_complete INTEGER DEFAULT 1,
                task_timeout_seconds INTEGER DEFAULT 600,
                repo_path TEXT DEFAULT '.'
            )
        """)

        # Tasks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                description TEXT NOT NULL,
                instructions TEXT,
                target_files TEXT,
                target_directories TEXT,
                depends_on TEXT,
                status TEXT DEFAULT 'queued',
                progress INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                agent_id TEXT,
                worktree_path TEXT,
                branch_name TEXT,
                result_json TEXT
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_status ON agent_tasks(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_created ON agent_tasks(created_at)")

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def _get_worktree_manager(self) -> GitWorktreeManager:
        """Get or create worktree manager."""
        if not self._worktree_manager:
            config = self.get_config()
            self._worktree_manager = GitWorktreeManager(
                config.repo_path,
                config.worktree_base_path
            )
        return self._worktree_manager

    # ═══════════════════════════════════════════════════════════════════════════
    # Configuration
    # ═══════════════════════════════════════════════════════════════════════════

    def get_config(self) -> ParallelAgentConfig:
        """Get current configuration."""
        if self._config:
            return self._config

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"SELECT {_CONFIG_COLUMNS} FROM agent_config WHERE id = 1")
        row = cursor.fetchone()
        conn.close()

        if row:
            self._config = ParallelAgentConfig(
                enabled=bool(row[1]),
                max_agents=row[2],
                worktree_base_path=row[3],
                auto_merge=bool(row[4]),
                cleanup_on_complete=bool(row[5]),
                task_timeout_seconds=row[6],
                repo_path=row[7],
            )
        else:
            self._config = ParallelAgentConfig()
            self.set_config(self._config)

        return self._config

    def set_config(self, config: ParallelAgentConfig) -> ParallelAgentConfig:
        """Set configuration."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO agent_config
            (id, enabled, max_agents, worktree_base_path, auto_merge,
             cleanup_on_complete, task_timeout_seconds, repo_path)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        """, (
            1 if config.enabled else 0,
            config.max_agents,
            config.worktree_base_path,
            1 if config.auto_merge else 0,
            1 if config.cleanup_on_complete else 0,
            config.task_timeout_seconds,
            config.repo_path,
        ))

        conn.commit()
        conn.close()

        self._config = config
        self._worktree_manager = None  # Reset to use new config
        return config

    # ═══════════════════════════════════════════════════════════════════════════
    # Task Management
    # ═══════════════════════════════════════════════════════════════════════════

    def create_task(
        self,
        task_type: TaskType,
        description: str,
        instructions: str = "",
        target_files: List[str] = None,
        target_directories: List[str] = None,
        depends_on: List[str] = None,
    ) -> AgentTask:
        """Create a new task."""
        task = AgentTask(
            id=str(uuid.uuid4()),
            task_type=task_type,
            description=description,
            instructions=instructions,
            target_files=target_files or [],
            target_directories=target_directories or [],
            depends_on=depends_on or [],
            created_at=int(datetime.now().timestamp() * 1000),
            status=TaskStatus.QUEUED,
        )

        # Check dependencies
        self._update_blocked_status(task)

        # Save to database
        self._save_task(task)

        # Add to in-memory cache
        self._tasks[task.id] = task

        logger.info(f"Created task {task.id}: {description}")
        return task

    def _update_blocked_status(self, task: AgentTask):
        """Update blocked_by based on depends_on."""
        task.blocked_by = []

        for dep_id in task.depends_on:
            dep_task = self.get_task(dep_id)
            if dep_task and dep_task.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                task.blocked_by.append(dep_id)

        if task.blocked_by and task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.BLOCKED

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Get a task by ID."""
        if task_id in self._tasks:
            return self._tasks[task_id]

        return self._load_task(task_id)

    def get_tasks(
        self,
        status: TaskStatus = None,
        limit: int = 50
    ) -> List[AgentTask]:
        """Get tasks with optional filter."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT id FROM agent_tasks WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        tasks = []
        for row in rows:
            task = self.get_task(row[0])
            if task:
                tasks.append(task)

        return tasks

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a task."""
        task = self.get_task(task_id)
        if not task:
            return False

        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return False

        task.status = TaskStatus.CANCELLED
        task.completed_at = int(datetime.now().timestamp() * 1000)

        # Clean up worktree if running
        if task.worktree_path:
            manager = self._get_worktree_manager()
            manager.remove_worktree(task.worktree_path, force=True)
            if task.branch_name:
                manager.delete_branch(task.branch_name, force=True)

        # Release agent
        if task.agent_id and task.agent_id in self._agents:
            agent = self._agents[task.agent_id]
            agent.task_id = None
            agent.status = AgentStatus.IDLE

        self._save_task(task)

        # Unblock dependent tasks
        self._unblock_dependents(task_id)

        logger.info(f"Cancelled task {task_id}")
        return True

    def _unblock_dependents(self, completed_task_id: str):
        """Unblock tasks that depend on a completed/cancelled task."""
        for task in self._tasks.values():
            if completed_task_id in task.blocked_by:
                task.blocked_by.remove(completed_task_id)
                if not task.blocked_by and task.status == TaskStatus.BLOCKED:
                    task.status = TaskStatus.QUEUED
                    self._save_task(task)

    def _save_task(self, task: AgentTask):
        """Save task to database."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO agent_tasks
            (id, task_type, description, instructions, target_files, target_directories,
             depends_on, status, progress, created_at, started_at, completed_at,
             agent_id, worktree_path, branch_name, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.id,
            task.task_type.value,
            task.description,
            task.instructions,
            json_dumps(task.target_files),
            json_dumps(task.target_directories),
            json_dumps(task.depends_on),
            task.status.value,
            task.progress,
            task.created_at,
            task.started_at,
            task.completed_at,
            task.agent_id,
            task.worktree_path,
            task.branch_name,
            json_dumps(task.result.to_dict()) if task.result else None,
        ))

        conn.commit()
        conn.close()

        self._tasks[task.id] = task

    def _load_task(self, task_id: str) -> Optional[AgentTask]:
        """Load task from database."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(f"SELECT {_TASK_COLUMNS} FROM agent_tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        result_data = json_loads(row[15]) if row[15] else None
        result = None
        if result_data:
            result = AgentTaskResult(
                success=result_data.get("success", False),
                summary=result_data.get("summary", ""),
                merge_status=MergeStatus(result_data.get("mergeStatus", "pending")),
                error_message=result_data.get("errorMessage"),
            )

        task = AgentTask(
            id=row[0],
            task_type=TaskType(row[1]),
            description=row[2],
            instructions=row[3] or "",
            target_files=json_loads(row[4]) if row[4] else [],
            target_directories=json_loads(row[5]) if row[5] else [],
            depends_on=json_loads(row[6]) if row[6] else [],
            status=TaskStatus(row[7]),
            progress=row[8],
            created_at=row[9],
            started_at=row[10],
            completed_at=row[11],
            agent_id=row[12],
            worktree_path=row[13],
            branch_name=row[14],
            result=result,
        )

        self._tasks[task.id] = task
        return task

    # ═══════════════════════════════════════════════════════════════════════════
    # Agent Pool Management
    # ═══════════════════════════════════════════════════════════════════════════

    def get_agent_pool_status(self) -> Dict[str, Any]:
        """Get current agent pool status."""
        config = self.get_config()

        active_agents = [a for a in self._agents.values() if a.status == AgentStatus.WORKING]
        idle_agents = [a for a in self._agents.values() if a.status == AgentStatus.IDLE]

        queued_tasks = self.get_tasks(status=TaskStatus.QUEUED)
        running_tasks = self.get_tasks(status=TaskStatus.RUNNING)
        completed_tasks = self.get_tasks(status=TaskStatus.COMPLETED)

        return {
            "maxAgents": config.max_agents,
            "activeAgents": len(active_agents),
            "idleAgents": len(idle_agents),
            "totalAgents": len(self._agents),
            "queuedTasks": len(queued_tasks),
            "runningTasks": len(running_tasks),
            "completedTasks": len(completed_tasks),
            "agents": [a.to_dict() for a in self._agents.values()],
        }

    def _get_or_create_agent(self) -> Optional[AgentInstance]:
        """Get an idle agent or create a new one if under limit."""
        config = self.get_config()

        # Look for idle agent
        for agent in self._agents.values():
            if agent.status == AgentStatus.IDLE:
                return agent

        # Create new if under limit
        if len(self._agents) < config.max_agents:
            agent = AgentInstance(
                id=str(uuid.uuid4()),
                status=AgentStatus.IDLE,
            )
            self._agents[agent.id] = agent
            return agent

        return None

    async def start_next_task(self) -> Optional[AgentTask]:
        """Start the next queued task if an agent is available."""
        config = self.get_config()
        if not config.enabled:
            return None

        with self._lock:
            # Get next queued task
            queued = self.get_tasks(status=TaskStatus.QUEUED, limit=1)
            if not queued:
                return None

            task = queued[0]

            # Check if blocked
            self._update_blocked_status(task)
            if task.status == TaskStatus.BLOCKED:
                return None

            # Get available agent
            agent = self._get_or_create_agent()
            if not agent:
                return None

            # Create worktree
            manager = self._get_worktree_manager()
            success, worktree_path, branch_name = manager.create_worktree(task.id)

            if not success:
                logger.error(f"Failed to create worktree for task {task.id}")
                return None

            # Assign task to agent
            agent.task_id = task.id
            agent.worktree_path = worktree_path
            agent.branch_name = branch_name
            agent.status = AgentStatus.WORKING
            agent.started_at = int(datetime.now().timestamp() * 1000)
            agent.tokens_used = 0
            agent.tool_calls = 0

            # Update task
            task.agent_id = agent.id
            task.worktree_path = worktree_path
            task.branch_name = branch_name
            task.status = TaskStatus.RUNNING
            task.started_at = agent.started_at

            self._save_task(task)

            logger.info(f"Started task {task.id} on agent {agent.id}")
            return task

    async def complete_task(
        self,
        task_id: str,
        success: bool,
        summary: str = "",
        error_message: str = None
    ) -> Optional[AgentTask]:
        """Mark a task as completed."""
        task = self.get_task(task_id)
        if not task:
            return None

        config = self.get_config()
        manager = self._get_worktree_manager()

        # Get changed files and commits
        changed_files = []
        commits = []

        if task.worktree_path:
            changed_files = manager.get_changed_files(task.worktree_path)

            # Commit any uncommitted changes
            if changed_files and success:
                commit = manager.commit_changes(
                    task.worktree_path,
                    f"AI: {task.description}"
                )
                if commit:
                    commits.append(commit)

        # Determine merge status
        merge_status = MergeStatus.PENDING
        conflicts = []

        if success and task.branch_name and config.auto_merge:
            merge_status, conflicts = manager.merge_branch(
                task.branch_name,
                f"AI: {task.description}"
            )

        # Create result
        task.result = AgentTaskResult(
            success=success,
            changed_files=changed_files,
            commits=commits,
            summary=summary,
            merge_status=merge_status,
            conflicts=conflicts,
            error_message=error_message,
        )

        # Update task status
        task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        task.completed_at = int(datetime.now().timestamp() * 1000)

        # Cleanup
        if config.cleanup_on_complete and merge_status == MergeStatus.MERGED:
            if task.worktree_path:
                manager.remove_worktree(task.worktree_path)
            if task.branch_name:
                manager.delete_branch(task.branch_name)

        # Release agent
        if task.agent_id and task.agent_id in self._agents:
            agent = self._agents[task.agent_id]
            agent.task_id = None
            agent.status = AgentStatus.IDLE

        self._save_task(task)

        # Unblock dependents
        self._unblock_dependents(task_id)

        logger.info(f"Completed task {task_id} with status {task.status.value}")
        return task

    # ═══════════════════════════════════════════════════════════════════════════
    # Merge Management
    # ═══════════════════════════════════════════════════════════════════════════

    async def merge_task(self, task_id: str) -> Tuple[MergeStatus, List[ConflictInfo]]:
        """Manually merge a completed task."""
        task = self.get_task(task_id)
        if not task or not task.branch_name:
            return MergeStatus.SKIPPED, []

        if task.result and task.result.merge_status == MergeStatus.MERGED:
            return MergeStatus.MERGED, []

        manager = self._get_worktree_manager()
        status, conflicts = manager.merge_branch(
            task.branch_name,
            f"AI: {task.description}"
        )

        # Update task result
        if task.result:
            task.result.merge_status = status
            task.result.conflicts = conflicts
            self._save_task(task)

        # Cleanup if merged
        config = self.get_config()
        if status == MergeStatus.MERGED and config.cleanup_on_complete:
            if task.worktree_path:
                manager.remove_worktree(task.worktree_path)
            manager.delete_branch(task.branch_name)

        return status, conflicts

    async def resolve_conflict(
        self,
        task_id: str,
        file_path: str,
        resolution: str
    ) -> bool:
        """Resolve a merge conflict."""
        task = self.get_task(task_id)
        if not task:
            return False

        manager = self._get_worktree_manager()

        # Write resolution
        full_path = manager.repo_path / file_path
        try:
            full_path.write_text(resolution)

            # Stage the resolution
            success, _ = manager._run_git(["add", file_path])
            if not success:
                return False

            # Check if all conflicts resolved
            success, output = manager._run_git(["diff", "--name-only", "--diff-filter=U"])
            if success and not output.strip():
                # All resolved, complete the merge
                success, _ = manager._run_git(["commit", "--no-edit"])
                if success and task.result:
                    task.result.merge_status = MergeStatus.MERGED
                    task.result.conflicts = []
                    self._save_task(task)

            return True
        except Exception as e:
            logger.error(f"Failed to resolve conflict: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════════════════
    # Statistics
    # ═══════════════════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Get parallel agents statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Total tasks
        cursor.execute("SELECT COUNT(*) FROM agent_tasks")
        total = cursor.fetchone()[0]

        # By status
        cursor.execute("SELECT status, COUNT(*) FROM agent_tasks GROUP BY status")
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

        # By type
        cursor.execute("SELECT task_type, COUNT(*) FROM agent_tasks GROUP BY task_type")
        by_type = {row[0]: row[1] for row in cursor.fetchall()}

        # Success rate
        cursor.execute("""
            SELECT COUNT(*) FROM agent_tasks
            WHERE status = 'completed' AND result_json LIKE '%"success": true%'
        """)
        successful = cursor.fetchone()[0]

        completed = by_status.get("completed", 0)

        conn.close()

        return {
            "totalTasks": total,
            "byStatus": by_status,
            "byType": by_type,
            "completedCount": completed,
            "successfulCount": successful,
            "successRate": round(successful / max(completed, 1) * 100, 1),
            "activeWorktrees": len(self._get_worktree_manager().list_worktrees()),
        }

    def update_task_progress(self, task_id: str, progress: int, current_step: str = ""):
        """Update task progress."""
        task = self.get_task(task_id)
        if task:
            task.progress = min(100, max(0, progress))
            self._save_task(task)

            if task.agent_id and task.agent_id in self._agents:
                self._agents[task.agent_id].current_step = current_step


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_parallel_orchestrator: Optional[ParallelAgentOrchestrator] = None


def get_parallel_orchestrator() -> ParallelAgentOrchestrator:
    """Get the singleton ParallelAgentOrchestrator instance."""
    global _parallel_orchestrator
    if _parallel_orchestrator is None:
        _parallel_orchestrator = ParallelAgentOrchestrator()
    return _parallel_orchestrator
