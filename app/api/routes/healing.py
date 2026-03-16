"""
Self-Healing API - Endpoints for Self-Healing Code functionality.

Features:
- Configuration management
- Healing attempts listing
- Manual fix application
- Fix dismissal
- Statistics
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.self_healing import (
    get_self_healing_engine,
    SelfHealingConfig,
    AutoApplyLevel,
    HealingStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/healing", tags=["healing"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class ConfigRequest(BaseModel):
    """Self-Healing configuration request."""
    enabled: bool = Field(default=True, description="Enable self-healing")
    autoApplyLevel: str = Field(default="safe", pattern="^(none|safe|all)$")
    maxRetries: int = Field(default=3, ge=1, le=10)
    retryDelayMs: int = Field(default=1000, ge=100, le=60000)
    excludedTools: List[str] = Field(default_factory=list)
    minConfidenceForAuto: float = Field(default=0.8, ge=0.0, le=1.0)
    learnFromSuccess: bool = Field(default=True)


class ConfigResponse(BaseModel):
    """Self-Healing configuration response."""
    enabled: bool
    autoApplyLevel: str
    maxRetries: int
    retryDelayMs: int
    excludedTools: List[str]
    minConfidenceForAuto: float
    learnFromSuccess: bool


class CodeChangeResponse(BaseModel):
    """Code change response."""
    filePath: str
    lineNumber: Optional[int]
    oldContent: Optional[str]
    newContent: Optional[str]
    description: str


class SuggestedFixResponse(BaseModel):
    """Suggested fix response."""
    id: str
    type: str
    description: str
    changes: List[CodeChangeResponse]
    command: Optional[str]
    confidence: float
    safeToAutoApply: bool
    patternId: Optional[str]
    patternName: Optional[str]


class ToolErrorResponse(BaseModel):
    """Tool error response."""
    tool: str
    errorType: str
    errorMessage: str
    stackTrace: str
    context: Dict[str, Any]


class HealingAttemptResponse(BaseModel):
    """Healing attempt response."""
    id: str
    timestamp: int
    sessionId: str
    chainId: Optional[str]
    originalError: Optional[ToolErrorResponse]
    patternId: Optional[str]
    patternName: Optional[str]
    suggestedFix: Optional[SuggestedFixResponse]
    status: str
    applied: bool
    success: bool
    retryCount: int
    resultMessage: Optional[str]


class StatsResponse(BaseModel):
    """Healing statistics response."""
    totalAttempts: int
    byStatus: Dict[str, int]
    appliedCount: int
    successCount: int
    successRate: float
    topToolsWithErrors: Dict[str, int]


class AnalyzeErrorRequest(BaseModel):
    """Request to analyze an error."""
    toolName: str
    error: str
    toolArgs: Optional[Dict[str, Any]] = None
    sessionId: str = "default"
    chainId: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/config", response_model=ConfigResponse)
async def get_config():
    """
    Get current self-healing configuration.

    Returns:
        Current configuration settings
    """
    engine = get_self_healing_engine()
    config = engine.get_config()

    return ConfigResponse(
        enabled=config.enabled,
        autoApplyLevel=config.auto_apply_level.value,
        maxRetries=config.max_retries,
        retryDelayMs=config.retry_delay_ms,
        excludedTools=config.excluded_tools,
        minConfidenceForAuto=config.min_confidence_for_auto,
        learnFromSuccess=config.learn_from_success,
    )


@router.put("/config", response_model=ConfigResponse)
async def set_config(request: ConfigRequest):
    """
    Update self-healing configuration.

    Args:
        request: New configuration settings

    Returns:
        Updated configuration
    """
    engine = get_self_healing_engine()

    config = SelfHealingConfig(
        enabled=request.enabled,
        auto_apply_level=AutoApplyLevel(request.autoApplyLevel),
        max_retries=request.maxRetries,
        retry_delay_ms=request.retryDelayMs,
        excluded_tools=request.excludedTools,
        min_confidence_for_auto=request.minConfidenceForAuto,
        learn_from_success=request.learnFromSuccess,
    )

    result = engine.set_config(config)

    return ConfigResponse(
        enabled=result.enabled,
        autoApplyLevel=result.auto_apply_level.value,
        maxRetries=result.max_retries,
        retryDelayMs=result.retry_delay_ms,
        excludedTools=result.excluded_tools,
        minConfidenceForAuto=result.min_confidence_for_auto,
        learnFromSuccess=result.learn_from_success,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Attempt Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/attempts", response_model=List[HealingAttemptResponse])
async def get_attempts(
    sessionId: Optional[str] = Query(default=None, description="Filter by session"),
    status: Optional[str] = Query(default=None, pattern="^(pending|applied|success|failed|dismissed)$"),
    limit: int = Query(default=50, ge=1, le=200)
):
    """
    Get healing attempts with optional filters.

    Args:
        sessionId: Filter by session ID
        status: Filter by status
        limit: Maximum number of results

    Returns:
        List of healing attempts
    """
    engine = get_self_healing_engine()

    status_enum = HealingStatus(status) if status else None
    attempts = engine.get_attempts(
        session_id=sessionId,
        status=status_enum,
        limit=limit
    )

    return [_attempt_to_response(a) for a in attempts]


@router.get("/attempts/pending", response_model=List[HealingAttemptResponse])
async def get_pending_attempts(
    sessionId: Optional[str] = Query(default=None, description="Filter by session")
):
    """
    Get pending healing attempts that need user action.

    Args:
        sessionId: Filter by session ID

    Returns:
        List of pending healing attempts
    """
    engine = get_self_healing_engine()
    attempts = engine.get_pending_attempts(session_id=sessionId)

    return [_attempt_to_response(a) for a in attempts]


@router.get("/attempts/{attempt_id}", response_model=HealingAttemptResponse)
async def get_attempt(attempt_id: str):
    """
    Get a specific healing attempt.

    Args:
        attempt_id: ID of the attempt

    Returns:
        Healing attempt details
    """
    engine = get_self_healing_engine()
    attempts = engine.get_attempts(limit=1)

    # Load specific attempt
    attempt = engine._load_attempt(attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail=f"Attempt {attempt_id} not found")

    return _attempt_to_response(attempt)


# ═══════════════════════════════════════════════════════════════════════════════
# Action Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/analyze")
async def analyze_error(request: AnalyzeErrorRequest):
    """
    Analyze an error and generate a fix suggestion.

    Args:
        request: Error details

    Returns:
        Healing attempt with suggested fix, or null if no fix found
    """
    engine = get_self_healing_engine()

    attempt = engine.analyze_error(
        tool_name=request.toolName,
        error=request.error,
        tool_args=request.toolArgs,
        session_id=request.sessionId,
        chain_id=request.chainId,
    )

    if not attempt:
        return {"found": False, "message": "No fix suggestion found"}

    return {
        "found": True,
        "attempt": _attempt_to_response(attempt),
        "shouldAutoApply": engine.should_auto_apply(attempt),
    }


@router.post("/apply/{attempt_id}")
async def apply_fix(attempt_id: str):
    """
    Apply a suggested fix.

    Args:
        attempt_id: ID of the healing attempt

    Returns:
        Result of applying the fix
    """
    engine = get_self_healing_engine()

    # Note: In a real implementation, we'd pass an executor function
    # that can actually execute tools. For now, we just mark it as applied.
    success, message = engine.apply_fix(attempt_id)

    return {
        "success": success,
        "message": message,
        "attemptId": attempt_id,
    }


@router.post("/dismiss/{attempt_id}")
async def dismiss_fix(attempt_id: str):
    """
    Dismiss a suggested fix.

    Args:
        attempt_id: ID of the healing attempt

    Returns:
        Success message
    """
    engine = get_self_healing_engine()
    success = engine.dismiss_fix(attempt_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Attempt {attempt_id} not found")

    return {
        "success": True,
        "message": "Fix dismissed",
        "attemptId": attempt_id,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    Get self-healing statistics.

    Returns:
        Statistics about healing attempts
    """
    engine = get_self_healing_engine()
    stats = engine.get_stats()

    return StatsResponse(
        totalAttempts=stats["totalAttempts"],
        byStatus=stats["byStatus"],
        appliedCount=stats["appliedCount"],
        successCount=stats["successCount"],
        successRate=stats["successRate"],
        topToolsWithErrors=stats["topToolsWithErrors"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _attempt_to_response(attempt) -> HealingAttemptResponse:
    """Convert HealingAttempt to response model."""
    error_response = None
    if attempt.original_error:
        error_response = ToolErrorResponse(
            tool=attempt.original_error.tool,
            errorType=attempt.original_error.error_type,
            errorMessage=attempt.original_error.error_message,
            stackTrace=attempt.original_error.stack_trace,
            context={
                "filePath": attempt.original_error.file_path,
                "lineNumber": attempt.original_error.line_number,
                "codeSnippet": attempt.original_error.code_snippet,
            }
        )

    fix_response = None
    if attempt.suggested_fix:
        fix = attempt.suggested_fix
        fix_response = SuggestedFixResponse(
            id=fix.id,
            type=fix.fix_type.value,
            description=fix.description,
            changes=[
                CodeChangeResponse(
                    filePath=c.file_path,
                    lineNumber=c.line_number,
                    oldContent=c.old_content,
                    newContent=c.new_content,
                    description=c.description,
                )
                for c in fix.changes
            ],
            command=fix.command,
            confidence=fix.confidence,
            safeToAutoApply=fix.safe_to_auto_apply,
            patternId=fix.pattern_id,
            patternName=fix.pattern_name,
        )

    return HealingAttemptResponse(
        id=attempt.id,
        timestamp=attempt.timestamp,
        sessionId=attempt.session_id,
        chainId=attempt.chain_id,
        originalError=error_response,
        patternId=attempt.pattern_id,
        patternName=attempt.pattern_name,
        suggestedFix=fix_response,
        status=attempt.status.value,
        applied=attempt.applied,
        success=attempt.success,
        retryCount=attempt.retry_count,
        resultMessage=attempt.result_message,
    )
