"""
Arena Mode API - Model comparison with blind voting.

Features:
- Create and manage arena matches
- Blind A/B voting
- Model statistics and leaderboards
- Configuration (enable/disable, model selection)
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.arena_mode import (
    get_arena_mode_service,
    ArenaConfig,
    Vote,
    MatchStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arena", tags=["arena"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class ConfigRequest(BaseModel):
    """Arena configuration request."""
    enabled: bool = Field(default=False)
    modelA: str = Field(default="", max_length=100)
    modelB: str = Field(default="", max_length=100)
    autoArena: bool = Field(default=False)
    sampleRate: float = Field(default=1.0, ge=0.0, le=1.0)
    eloKFactor: int = Field(default=32, ge=1, le=64)


class ConfigResponse(BaseModel):
    """Arena configuration response."""
    enabled: bool
    modelA: str
    modelB: str
    autoArena: bool
    sampleRate: float
    eloKFactor: int


class StartMatchRequest(BaseModel):
    """Start a new arena match."""
    prompt: str = Field(min_length=1, max_length=10000)
    sessionId: str = Field(min_length=1, max_length=100)
    context: Optional[str] = Field(default=None, max_length=50000)
    modelA: Optional[str] = Field(default=None, max_length=100)
    modelB: Optional[str] = Field(default=None, max_length=100)


class SetResponseRequest(BaseModel):
    """Set a model's response in a match."""
    model: str = Field(min_length=1, max_length=100)
    response: str = Field(min_length=1, max_length=100000)
    latencyMs: int = Field(ge=0)
    tokens: int = Field(ge=0)


class VoteRequest(BaseModel):
    """Vote on a match."""
    vote: str = Field(pattern="^(A|B|tie)$")
    feedback: Optional[str] = Field(default=None, max_length=1000)


class MatchResponse(BaseModel):
    """Arena match response."""
    id: str
    timestamp: int
    sessionId: str
    prompt: str
    context: Optional[str]
    modelA: str
    modelB: str
    responseA: str
    responseB: str
    latencyA: int
    latencyB: int
    tokensA: int
    tokensB: int
    status: str
    vote: Optional[str]
    votedAt: Optional[int]
    feedback: Optional[str]


class ModelStatsResponse(BaseModel):
    """Model statistics response."""
    model: str
    wins: int
    losses: int
    ties: int
    totalMatches: int
    winRate: float
    eloRating: float
    avgLatency: float
    avgTokens: float
    vsStats: Dict[str, Dict[str, int]]


class OverallStatsResponse(BaseModel):
    """Overall arena statistics response."""
    totalMatches: int
    modelCount: int
    votesA: int
    votesB: int
    votesTie: int
    pendingVotes: int
    configEnabled: bool
    configModelA: str
    configModelB: str


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/config", response_model=ConfigResponse)
async def get_config():
    """
    Get arena mode configuration.

    Returns:
        Current configuration (enabled, models, settings)
    """
    service = get_arena_mode_service()
    config = service.get_config()

    return ConfigResponse(
        enabled=config.enabled,
        modelA=config.model_a,
        modelB=config.model_b,
        autoArena=config.auto_arena,
        sampleRate=config.sample_rate,
        eloKFactor=config.elo_k_factor,
    )


@router.put("/config", response_model=ConfigResponse)
async def set_config(request: ConfigRequest):
    """
    Update arena mode configuration.

    Enable/disable arena mode and configure comparison models.

    Args:
        request: Configuration settings

    Returns:
        Updated configuration
    """
    service = get_arena_mode_service()

    config = ArenaConfig(
        enabled=request.enabled,
        model_a=request.modelA,
        model_b=request.modelB,
        auto_arena=request.autoArena,
        sample_rate=request.sampleRate,
        elo_k_factor=request.eloKFactor,
    )

    result = service.set_config(config)

    return ConfigResponse(
        enabled=result.enabled,
        modelA=result.model_a,
        modelB=result.model_b,
        autoArena=result.auto_arena,
        sampleRate=result.sample_rate,
        eloKFactor=result.elo_k_factor,
    )


@router.get("/enabled")
async def check_enabled():
    """
    Quick check if arena mode is enabled and configured.

    Returns:
        enabled: True if arena mode can be used
    """
    service = get_arena_mode_service()
    return {"enabled": service.is_enabled()}


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Endpoints (MUST be before /{match_id} to avoid route conflict)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=OverallStatsResponse)
async def get_stats():
    """
    Get overall arena statistics.

    Returns:
        Total matches, vote distribution, configuration status
    """
    service = get_arena_mode_service()
    stats = service.get_overall_stats()
    return OverallStatsResponse(**stats)


@router.get("/leaderboard", response_model=List[ModelStatsResponse])
async def get_leaderboard(limit: int = Query(default=10, ge=1, le=50)):
    """
    Get model leaderboard sorted by ELO rating.

    Args:
        limit: Max models to return

    Returns:
        Models ranked by ELO
    """
    service = get_arena_mode_service()
    stats = service.get_leaderboard(limit=limit)
    return [ModelStatsResponse(**s.to_dict()) for s in stats]


@router.get("/history", response_model=List[MatchResponse])
async def get_history(
    sessionId: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, pattern="^(pending|ready|voted|skipped)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Get match history.

    Args:
        sessionId: Filter by session
        status: Filter by status
        limit: Max results

    Returns:
        List of matches
    """
    service = get_arena_mode_service()

    status_enum = MatchStatus(status) if status else None

    matches = service.get_matches(
        session_id=sessionId,
        status=status_enum,
        limit=limit,
    )

    return [MatchResponse(**m.to_dict(reveal_models=True)) for m in matches]


@router.get("/models/{model}/stats", response_model=ModelStatsResponse)
async def get_model_stats(model: str):
    """
    Get statistics for a specific model.

    Args:
        model: Model name

    Returns:
        Model statistics
    """
    service = get_arena_mode_service()
    stats = service.get_model_stats(model)

    if not stats:
        raise HTTPException(status_code=404, detail=f"No stats for model {model}")

    return ModelStatsResponse(**stats[0].to_dict())


# ═══════════════════════════════════════════════════════════════════════════════
# Match Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/start", response_model=MatchResponse)
async def start_match(request: StartMatchRequest):
    """
    Start a new arena match.

    Creates a match ready to receive responses from both models.
    Models are assigned randomly to A/B for blind comparison.

    Args:
        request: Match parameters (prompt, session, optional models)

    Returns:
        New match with hidden model identities
    """
    service = get_arena_mode_service()

    if not service.is_enabled():
        raise HTTPException(
            status_code=400,
            detail="Arena mode is not enabled. Configure it first."
        )

    match = service.create_match(
        prompt=request.prompt,
        session_id=request.sessionId,
        context=request.context,
        model_a=request.modelA,
        model_b=request.modelB,
    )

    return MatchResponse(**match.to_dict())


@router.get("/session/{session_id}/pending", response_model=Optional[MatchResponse])
async def get_pending_match(session_id: str):
    """
    Get any pending match for a session that needs voting.

    Args:
        session_id: Chat session ID

    Returns:
        Pending match or null
    """
    service = get_arena_mode_service()
    match = service.get_pending_match(session_id)

    if not match:
        return None

    return MatchResponse(**match.to_dict())


# ═══════════════════════════════════════════════════════════════════════════════
# Single Match Endpoints (MUST be after specific routes)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/match/{match_id}", response_model=MatchResponse)
async def get_match(match_id: str, reveal: bool = Query(default=False)):
    """
    Get a match by ID.

    Args:
        match_id: Match ID
        reveal: Force reveal model identities (default: only after voting)

    Returns:
        Match details
    """
    service = get_arena_mode_service()
    match = service.get_match(match_id)

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    return MatchResponse(**match.to_dict(reveal_models=reveal))


@router.post("/match/{match_id}/response", response_model=MatchResponse)
async def set_response(match_id: str, request: SetResponseRequest):
    """
    Set a model's response in a match.

    Call this twice (once for each model) to complete a match.

    Args:
        match_id: Match ID
        request: Response data (model, response, latency, tokens)

    Returns:
        Updated match
    """
    service = get_arena_mode_service()

    match = service.set_response(
        match_id=match_id,
        model=request.model,
        response=request.response,
        latency_ms=request.latencyMs,
        tokens=request.tokens,
    )

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    return MatchResponse(**match.to_dict())


@router.post("/match/{match_id}/vote", response_model=MatchResponse)
async def vote(match_id: str, request: VoteRequest):
    """
    Submit a vote for a match.

    After voting, model identities are revealed.

    Args:
        match_id: Match ID
        request: Vote (A, B, or tie) and optional feedback

    Returns:
        Match with revealed models
    """
    service = get_arena_mode_service()

    match = service.vote(
        match_id=match_id,
        vote=Vote(request.vote),
        feedback=request.feedback,
    )

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found or not ready for voting")

    return MatchResponse(**match.to_dict(reveal_models=True))


@router.post("/match/{match_id}/skip", response_model=MatchResponse)
async def skip_match(match_id: str):
    """
    Skip voting on a match.

    Args:
        match_id: Match ID

    Returns:
        Skipped match
    """
    service = get_arena_mode_service()
    match = service.skip_match(match_id)

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    return MatchResponse(**match.to_dict(reveal_models=True))
