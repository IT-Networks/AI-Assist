"""
Parallel Agents API - Endpoints for multi-agent task execution.

Features:
- Task creation and management
- Agent pool status
- Merge and conflict resolution
- Statistics
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.parallel_agents import (
    get_parallel_orchestrator,
    ParallelAgentConfig,
    TaskType,
    TaskStatus,
    MergeStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class ConfigRequest(BaseModel):
    """Parallel agents configuration request."""
    enabled: bool = Field(default=True)
    maxAgents: int = Field(default=4, ge=1, le=16)
    worktreeBasePath: str = Field(default=".ai-worktrees")
    autoMerge: bool = Field(default=True)
    cleanupOnComplete: bool = Field(default=True)
    taskTimeoutSeconds: int = Field(default=600, ge=60, le=3600)
    repoPath: str = Field(default=".")


class ConfigResponse(BaseModel):
    """Parallel agents configuration response."""
    enabled: bool
    maxAgents: int
    worktreeBasePath: str
    autoMerge: bool
    cleanupOnComplete: bool
    taskTimeoutSeconds: int
    repoPath: str


class CreateTaskRequest(BaseModel):
    """Create task request."""
    type: str = Field(pattern="^(implement|refactor|test|document|review|fix|custom)$")
    description: str = Field(min_length=1, max_length=500)
    instructions: str = Field(default="", max_length=5000)
    targetFiles: List[str] = Field(default_factory=list)
    targetDirectories: List[str] = Field(default_factory=list)
    dependsOn: List[str] = Field(default_factory=list)


class FileChangeResponse(BaseModel):
    """File change response."""
    filePath: str
    changeType: str
    additions: int
    deletions: int


class CommitInfoResponse(BaseModel):
    """Commit info response."""
    sha: str
    message: str
    author: str
    timestamp: int
    filesChanged: int


class ConflictInfoResponse(BaseModel):
    """Conflict info response."""
    filePath: str
    ourChanges: str
    theirChanges: str
    suggestedResolution: Optional[str]


class TaskResultResponse(BaseModel):
    """Task result response."""
    success: bool
    changedFiles: List[FileChangeResponse]
    commits: List[CommitInfoResponse]
    summary: str
    mergeStatus: str
    conflicts: List[ConflictInfoResponse]
    errorMessage: Optional[str]


class TaskResponse(BaseModel):
    """Task response."""
    id: str
    type: str
    description: str
    createdAt: int
    targetFiles: List[str]
    targetDirectories: List[str]
    instructions: str
    dependsOn: List[str]
    blockedBy: List[str]
    status: str
    progress: int
    startedAt: Optional[int]
    completedAt: Optional[int]
    agentId: Optional[str]
    worktreePath: Optional[str]
    branchName: Optional[str]
    result: Optional[TaskResultResponse]


class AgentResponse(BaseModel):
    """Agent instance response."""
    id: str
    taskId: Optional[str]
    worktreePath: Optional[str]
    branchName: Optional[str]
    status: str
    startedAt: Optional[int]
    tokensUsed: int
    toolCalls: int
    currentStep: str


class PoolStatusResponse(BaseModel):
    """Agent pool status response."""
    maxAgents: int
    activeAgents: int
    idleAgents: int
    totalAgents: int
    queuedTasks: int
    runningTasks: int
    completedTasks: int
    agents: List[AgentResponse]


class StatsResponse(BaseModel):
    """Statistics response."""
    totalTasks: int
    byStatus: Dict[str, int]
    byType: Dict[str, int]
    completedCount: int
    successfulCount: int
    successRate: float
    activeWorktrees: int


class ResolveConflictRequest(BaseModel):
    """Resolve conflict request."""
    filePath: str
    resolution: str


class UpdateProgressRequest(BaseModel):
    """Update task progress request."""
    progress: int = Field(ge=0, le=100)
    currentStep: str = Field(default="")


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/config", response_model=ConfigResponse)
async def get_config():
    """
    Get parallel agents configuration.

    Returns:
        Current configuration settings
    """
    orchestrator = get_parallel_orchestrator()
    config = orchestrator.get_config()

    return ConfigResponse(
        enabled=config.enabled,
        maxAgents=config.max_agents,
        worktreeBasePath=config.worktree_base_path,
        autoMerge=config.auto_merge,
        cleanupOnComplete=config.cleanup_on_complete,
        taskTimeoutSeconds=config.task_timeout_seconds,
        repoPath=config.repo_path,
    )


@router.put("/config", response_model=ConfigResponse)
async def set_config(request: ConfigRequest):
    """
    Update parallel agents configuration.

    Args:
        request: New configuration settings

    Returns:
        Updated configuration
    """
    orchestrator = get_parallel_orchestrator()

    config = ParallelAgentConfig(
        enabled=request.enabled,
        max_agents=request.maxAgents,
        worktree_base_path=request.worktreeBasePath,
        auto_merge=request.autoMerge,
        cleanup_on_complete=request.cleanupOnComplete,
        task_timeout_seconds=request.taskTimeoutSeconds,
        repo_path=request.repoPath,
    )

    result = orchestrator.set_config(config)

    return ConfigResponse(
        enabled=result.enabled,
        maxAgents=result.max_agents,
        worktreeBasePath=result.worktree_base_path,
        autoMerge=result.auto_merge,
        cleanupOnComplete=result.cleanup_on_complete,
        taskTimeoutSeconds=result.task_timeout_seconds,
        repoPath=result.repo_path,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Task Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/tasks", response_model=List[TaskResponse])
async def get_tasks(
    status: Optional[str] = Query(
        default=None,
        pattern="^(queued|running|completed|failed|cancelled|blocked)$"
    ),
    limit: int = Query(default=50, ge=1, le=200)
):
    """
    Get tasks with optional filters.

    Args:
        status: Filter by status
        limit: Maximum number of results

    Returns:
        List of tasks
    """
    orchestrator = get_parallel_orchestrator()

    status_enum = TaskStatus(status) if status else None
    tasks = orchestrator.get_tasks(status=status_enum, limit=limit)

    return [_task_to_response(t) for t in tasks]


@router.post("/tasks", response_model=TaskResponse)
async def create_task(request: CreateTaskRequest):
    """
    Create a new task.

    Args:
        request: Task details

    Returns:
        Created task
    """
    orchestrator = get_parallel_orchestrator()

    task = orchestrator.create_task(
        task_type=TaskType(request.type),
        description=request.description,
        instructions=request.instructions,
        target_files=request.targetFiles,
        target_directories=request.targetDirectories,
        depends_on=request.dependsOn,
    )

    return _task_to_response(task)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """
    Get a specific task.

    Args:
        task_id: Task ID

    Returns:
        Task details
    """
    orchestrator = get_parallel_orchestrator()
    task = orchestrator.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return _task_to_response(task)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """
    Cancel and delete a task.

    Args:
        task_id: Task ID

    Returns:
        Success message
    """
    orchestrator = get_parallel_orchestrator()
    success = orchestrator.cancel_task(task_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or already completed")

    return {"success": True, "message": "Task cancelled", "taskId": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """
    Cancel a running or queued task.

    Args:
        task_id: Task ID

    Returns:
        Success message
    """
    orchestrator = get_parallel_orchestrator()
    success = orchestrator.cancel_task(task_id)

    if not success:
        raise HTTPException(status_code=400, detail=f"Task {task_id} cannot be cancelled")

    return {"success": True, "message": "Task cancelled", "taskId": task_id}


@router.post("/tasks/{task_id}/progress")
async def update_progress(task_id: str, request: UpdateProgressRequest):
    """
    Update task progress.

    Args:
        task_id: Task ID
        request: Progress update

    Returns:
        Success message
    """
    orchestrator = get_parallel_orchestrator()
    orchestrator.update_task_progress(task_id, request.progress, request.currentStep)

    return {"success": True, "progress": request.progress}


@router.post("/tasks/{task_id}/start")
async def start_task(task_id: str):
    """
    Manually start a queued task (if agent available).

    Args:
        task_id: Task ID

    Returns:
        Started task or error
    """
    orchestrator = get_parallel_orchestrator()
    task = orchestrator.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.status != TaskStatus.QUEUED:
        raise HTTPException(status_code=400, detail=f"Task is not queued (status: {task.status.value})")

    started = await orchestrator.start_next_task()

    if not started or started.id != task_id:
        raise HTTPException(status_code=400, detail="No available agent or task blocked")

    return _task_to_response(started)


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    success: bool = Query(default=True),
    summary: str = Query(default=""),
    errorMessage: Optional[str] = Query(default=None)
):
    """
    Mark a task as completed.

    Args:
        task_id: Task ID
        success: Whether task succeeded
        summary: Completion summary
        errorMessage: Error message if failed

    Returns:
        Completed task
    """
    orchestrator = get_parallel_orchestrator()
    task = await orchestrator.complete_task(task_id, success, summary, errorMessage)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return _task_to_response(task)


# ═══════════════════════════════════════════════════════════════════════════════
# Pool Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/pool", response_model=PoolStatusResponse)
async def get_pool_status():
    """
    Get agent pool status.

    Returns:
        Pool status with active agents and queue info
    """
    orchestrator = get_parallel_orchestrator()
    status = orchestrator.get_agent_pool_status()

    return PoolStatusResponse(
        maxAgents=status["maxAgents"],
        activeAgents=status["activeAgents"],
        idleAgents=status["idleAgents"],
        totalAgents=status["totalAgents"],
        queuedTasks=status["queuedTasks"],
        runningTasks=status["runningTasks"],
        completedTasks=status["completedTasks"],
        agents=[
            AgentResponse(
                id=a["id"],
                taskId=a["taskId"],
                worktreePath=a["worktreePath"],
                branchName=a["branchName"],
                status=a["status"],
                startedAt=a["startedAt"],
                tokensUsed=a["tokensUsed"],
                toolCalls=a["toolCalls"],
                currentStep=a["currentStep"],
            )
            for a in status["agents"]
        ]
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Merge Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/merge/{task_id}")
async def merge_task(task_id: str):
    """
    Manually merge a completed task.

    Args:
        task_id: Task ID

    Returns:
        Merge result
    """
    orchestrator = get_parallel_orchestrator()
    status, conflicts = await orchestrator.merge_task(task_id)

    return {
        "taskId": task_id,
        "mergeStatus": status.value,
        "conflicts": [c.to_dict() for c in conflicts],
        "success": status == MergeStatus.MERGED,
    }


@router.get("/conflicts/{task_id}", response_model=List[ConflictInfoResponse])
async def get_conflicts(task_id: str):
    """
    Get conflicts for a task.

    Args:
        task_id: Task ID

    Returns:
        List of conflicts
    """
    orchestrator = get_parallel_orchestrator()
    task = orchestrator.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if not task.result or not task.result.conflicts:
        return []

    return [
        ConflictInfoResponse(
            filePath=c.file_path,
            ourChanges=c.our_changes,
            theirChanges=c.their_changes,
            suggestedResolution=c.suggested_resolution,
        )
        for c in task.result.conflicts
    ]


@router.post("/conflicts/{task_id}/resolve")
async def resolve_conflict(task_id: str, request: ResolveConflictRequest):
    """
    Resolve a merge conflict.

    Args:
        task_id: Task ID
        request: Resolution details

    Returns:
        Success message
    """
    orchestrator = get_parallel_orchestrator()
    success = await orchestrator.resolve_conflict(
        task_id,
        request.filePath,
        request.resolution
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to resolve conflict")

    return {"success": True, "message": "Conflict resolved", "filePath": request.filePath}


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    Get parallel agents statistics.

    Returns:
        Statistics
    """
    orchestrator = get_parallel_orchestrator()
    stats = orchestrator.get_stats()

    return StatsResponse(
        totalTasks=stats["totalTasks"],
        byStatus=stats["byStatus"],
        byType=stats["byType"],
        completedCount=stats["completedCount"],
        successfulCount=stats["successfulCount"],
        successRate=stats["successRate"],
        activeWorktrees=stats["activeWorktrees"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _task_to_response(task) -> TaskResponse:
    """Convert AgentTask to response model."""
    result_response = None
    if task.result:
        result_response = TaskResultResponse(
            success=task.result.success,
            changedFiles=[
                FileChangeResponse(
                    filePath=f.file_path,
                    changeType=f.change_type,
                    additions=f.additions,
                    deletions=f.deletions,
                )
                for f in task.result.changed_files
            ],
            commits=[
                CommitInfoResponse(
                    sha=c.sha,
                    message=c.message,
                    author=c.author,
                    timestamp=c.timestamp,
                    filesChanged=c.files_changed,
                )
                for c in task.result.commits
            ],
            summary=task.result.summary,
            mergeStatus=task.result.merge_status.value,
            conflicts=[
                ConflictInfoResponse(
                    filePath=c.file_path,
                    ourChanges=c.our_changes,
                    theirChanges=c.their_changes,
                    suggestedResolution=c.suggested_resolution,
                )
                for c in task.result.conflicts
            ],
            errorMessage=task.result.error_message,
        )

    return TaskResponse(
        id=task.id,
        type=task.task_type.value,
        description=task.description,
        createdAt=task.created_at,
        targetFiles=task.target_files,
        targetDirectories=task.target_directories,
        instructions=task.instructions,
        dependsOn=task.depends_on,
        blockedBy=task.blocked_by,
        status=task.status.value,
        progress=task.progress,
        startedAt=task.started_at,
        completedAt=task.completed_at,
        agentId=task.agent_id,
        worktreePath=task.worktree_path,
        branchName=task.branch_name,
        result=result_response,
    )
