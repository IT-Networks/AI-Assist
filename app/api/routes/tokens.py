"""
Token API - Endpoints for Token/Credit Usage Tracking.

Features:
- Current usage with period filter (day, week, month)
- Detailed breakdown by model/request type
- Budget configuration and limits
- Export functionality (JSON/CSV)
- Budget alerts management
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.token_tracker import (
    get_token_tracker,
    BudgetConfig,
    TokenUsage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class TokenBreakdownResponse(BaseModel):
    """Token breakdown response."""
    requests: int
    inputTokens: int
    outputTokens: int
    totalTokens: int
    costUsd: float


class HourlyUsageResponse(BaseModel):
    """Hourly usage response."""
    hour: str
    tokens: int
    requests: int


class UsageSummaryResponse(BaseModel):
    """Usage summary response."""
    period: str
    startDate: str
    endDate: str
    totalRequests: int
    totalTokens: int
    inputTokens: int
    outputTokens: int
    estimatedCostUsd: float
    byModel: Dict[str, TokenBreakdownResponse]
    byRequestType: Dict[str, TokenBreakdownResponse]
    byHour: List[HourlyUsageResponse]
    budgetLimit: Optional[float] = None
    budgetUsed: float = 0.0
    budgetRemaining: Optional[float] = None


class TokenUsageResponse(BaseModel):
    """Single token usage record response."""
    id: str
    timestamp: int
    sessionId: str
    userId: str
    requestType: str
    model: str
    inputTokens: int
    outputTokens: int
    totalTokens: int
    costUsd: float
    toolName: Optional[str] = None
    chainId: Optional[str] = None


class BudgetConfigRequest(BaseModel):
    """Budget configuration request."""
    enabled: bool = Field(default=False, description="Enable budget tracking")
    limitTokens: Optional[int] = Field(default=None, description="Monthly token limit")
    limitUsd: Optional[float] = Field(default=None, description="Monthly cost limit in USD")
    alertThreshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Alert threshold (0.0-1.0)")
    alertEmail: Optional[str] = Field(default=None, description="Email for budget alerts")


class BudgetConfigResponse(BaseModel):
    """Budget configuration response."""
    enabled: bool
    limitTokens: Optional[int]
    limitUsd: Optional[float]
    alertThreshold: float
    alertEmail: Optional[str]


class BudgetAlertResponse(BaseModel):
    """Budget alert response."""
    id: str
    timestamp: int
    alertType: str
    currentUsage: float
    limit: float
    message: str
    acknowledged: bool


class LogUsageRequest(BaseModel):
    """Request to log token usage."""
    sessionId: str
    model: str
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    requestType: str = "chat"
    userId: str = "default"
    toolName: Optional[str] = None
    chainId: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Usage Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    period: str = Query(default="day", pattern="^(day|week|month)$", description="Time period")
):
    """
    Get token usage summary for a period.

    Args:
        period: Time period - 'day', 'week', or 'month'

    Returns:
        Usage summary with breakdowns by model, request type, and hour
    """
    tracker = get_token_tracker()
    summary = tracker.get_usage_summary(period)

    return UsageSummaryResponse(
        period=summary.period,
        startDate=summary.start_date,
        endDate=summary.end_date,
        totalRequests=summary.total_requests,
        totalTokens=summary.total_tokens,
        inputTokens=summary.input_tokens,
        outputTokens=summary.output_tokens,
        estimatedCostUsd=round(summary.estimated_cost_usd, 4),
        byModel={
            k: TokenBreakdownResponse(
                requests=v.requests,
                inputTokens=v.input_tokens,
                outputTokens=v.output_tokens,
                totalTokens=v.total_tokens,
                costUsd=round(v.cost_usd, 4)
            )
            for k, v in summary.by_model.items()
        },
        byRequestType={
            k: TokenBreakdownResponse(
                requests=v.requests,
                inputTokens=v.input_tokens,
                outputTokens=v.output_tokens,
                totalTokens=v.total_tokens,
                costUsd=round(v.cost_usd, 4)
            )
            for k, v in summary.by_request_type.items()
        },
        byHour=[
            HourlyUsageResponse(hour=h.hour, tokens=h.tokens, requests=h.requests)
            for h in summary.by_hour
        ],
        budgetLimit=summary.budget_limit,
        budgetUsed=round(summary.budget_used, 4),
        budgetRemaining=round(summary.budget_remaining, 4) if summary.budget_remaining else None
    )


@router.get("/breakdown")
async def get_usage_breakdown(
    period: str = Query(default="month", pattern="^(day|week|month)$"),
    groupBy: str = Query(default="model", pattern="^(model|requestType|session)$")
):
    """
    Get detailed usage breakdown.

    Args:
        period: Time period for breakdown
        groupBy: Grouping dimension - 'model', 'requestType', or 'session'

    Returns:
        Detailed breakdown grouped by specified dimension
    """
    tracker = get_token_tracker()
    summary = tracker.get_usage_summary(period)

    if groupBy == "model":
        breakdown = {
            k: {
                "requests": v.requests,
                "inputTokens": v.input_tokens,
                "outputTokens": v.output_tokens,
                "totalTokens": v.total_tokens,
                "costUsd": round(v.cost_usd, 4),
                "percentage": round(v.total_tokens / max(summary.total_tokens, 1) * 100, 1)
            }
            for k, v in summary.by_model.items()
        }
    elif groupBy == "requestType":
        breakdown = {
            k: {
                "requests": v.requests,
                "inputTokens": v.input_tokens,
                "outputTokens": v.output_tokens,
                "totalTokens": v.total_tokens,
                "costUsd": round(v.cost_usd, 4),
                "percentage": round(v.total_tokens / max(summary.total_tokens, 1) * 100, 1)
            }
            for k, v in summary.by_request_type.items()
        }
    else:
        # Session breakdown - get recent records grouped by session
        recent = tracker.get_recent_usage(limit=1000)
        session_map: Dict[str, Dict[str, Any]] = {}

        for usage in recent:
            if usage.session_id not in session_map:
                session_map[usage.session_id] = {
                    "requests": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                    "costUsd": 0.0
                }
            session_map[usage.session_id]["requests"] += 1
            session_map[usage.session_id]["inputTokens"] += usage.input_tokens
            session_map[usage.session_id]["outputTokens"] += usage.output_tokens
            session_map[usage.session_id]["totalTokens"] += usage.total_tokens
            session_map[usage.session_id]["costUsd"] += usage.cost_usd

        # Round costs
        for sid in session_map:
            session_map[sid]["costUsd"] = round(session_map[sid]["costUsd"], 4)

        breakdown = session_map

    return {
        "period": period,
        "groupBy": groupBy,
        "breakdown": breakdown,
        "totalTokens": summary.total_tokens,
        "estimatedCostUsd": round(summary.estimated_cost_usd, 4)
    }


@router.get("/recent", response_model=List[TokenUsageResponse])
async def get_recent_usage(
    limit: int = Query(default=50, ge=1, le=500, description="Number of records to return")
):
    """
    Get recent token usage records.

    Args:
        limit: Maximum number of records (1-500)

    Returns:
        List of recent usage records
    """
    tracker = get_token_tracker()
    records = tracker.get_recent_usage(limit=limit)

    return [
        TokenUsageResponse(
            id=r.id,
            timestamp=r.timestamp,
            sessionId=r.session_id,
            userId=r.user_id,
            requestType=r.request_type,
            model=r.model,
            inputTokens=r.input_tokens,
            outputTokens=r.output_tokens,
            totalTokens=r.total_tokens,
            costUsd=round(r.cost_usd, 6),
            toolName=r.tool_name,
            chainId=r.chain_id
        )
        for r in records
    ]


@router.get("/session/{session_id}", response_model=List[TokenUsageResponse])
async def get_session_usage(session_id: str):
    """
    Get token usage for a specific session.

    Args:
        session_id: Session ID to query

    Returns:
        List of usage records for the session
    """
    tracker = get_token_tracker()
    records = tracker.get_usage_by_session(session_id)

    return [
        TokenUsageResponse(
            id=r.id,
            timestamp=r.timestamp,
            sessionId=r.session_id,
            userId=r.user_id,
            requestType=r.request_type,
            model=r.model,
            inputTokens=r.input_tokens,
            outputTokens=r.output_tokens,
            totalTokens=r.total_tokens,
            costUsd=round(r.cost_usd, 6),
            toolName=r.tool_name,
            chainId=r.chain_id
        )
        for r in records
    ]


@router.post("/log", response_model=TokenUsageResponse)
async def log_token_usage(request: LogUsageRequest):
    """
    Log a new token usage record.

    Args:
        request: Usage data to log

    Returns:
        Created usage record
    """
    tracker = get_token_tracker()

    usage = tracker.log_usage(
        session_id=request.sessionId,
        model=request.model,
        input_tokens=request.inputTokens,
        output_tokens=request.outputTokens,
        request_type=request.requestType,
        user_id=request.userId,
        tool_name=request.toolName,
        chain_id=request.chainId
    )

    return TokenUsageResponse(
        id=usage.id,
        timestamp=usage.timestamp,
        sessionId=usage.session_id,
        userId=usage.user_id,
        requestType=usage.request_type,
        model=usage.model,
        inputTokens=usage.input_tokens,
        outputTokens=usage.output_tokens,
        totalTokens=usage.total_tokens,
        costUsd=round(usage.cost_usd, 6),
        toolName=usage.tool_name,
        chainId=usage.chain_id
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Budget Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/budget", response_model=Optional[BudgetConfigResponse])
async def get_budget_config():
    """
    Get current budget configuration.

    Returns:
        Budget configuration or null if not set
    """
    tracker = get_token_tracker()
    config = tracker.get_budget_config()

    if not config:
        return None

    return BudgetConfigResponse(
        enabled=config.enabled,
        limitTokens=config.limit_tokens,
        limitUsd=config.limit_usd,
        alertThreshold=config.alert_threshold,
        alertEmail=config.alert_email
    )


@router.put("/budget", response_model=BudgetConfigResponse)
async def set_budget_config(request: BudgetConfigRequest):
    """
    Set budget configuration.

    Args:
        request: Budget configuration to set

    Returns:
        Updated budget configuration
    """
    tracker = get_token_tracker()

    config = BudgetConfig(
        enabled=request.enabled,
        limit_tokens=request.limitTokens,
        limit_usd=request.limitUsd,
        alert_threshold=request.alertThreshold,
        alert_email=request.alertEmail
    )

    result = tracker.set_budget_config(config)

    return BudgetConfigResponse(
        enabled=result.enabled,
        limitTokens=result.limit_tokens,
        limitUsd=result.limit_usd,
        alertThreshold=result.alert_threshold,
        alertEmail=result.alert_email
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/alerts", response_model=List[BudgetAlertResponse])
async def get_budget_alerts(
    includeAcknowledged: bool = Query(default=False, description="Include acknowledged alerts")
):
    """
    Get budget alerts.

    Args:
        includeAcknowledged: Whether to include acknowledged alerts

    Returns:
        List of budget alerts
    """
    tracker = get_token_tracker()
    alerts = tracker.get_alerts(include_acknowledged=includeAcknowledged)

    return [
        BudgetAlertResponse(
            id=a.id,
            timestamp=a.timestamp,
            alertType=a.alert_type,
            currentUsage=a.current_usage,
            limit=a.limit,
            message=a.message,
            acknowledged=a.acknowledged
        )
        for a in alerts
    ]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """
    Acknowledge a budget alert.

    Args:
        alert_id: ID of the alert to acknowledge

    Returns:
        Success message
    """
    tracker = get_token_tracker()
    success = tracker.acknowledge_alert(alert_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")

    return {"message": "Alert acknowledged", "alertId": alert_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Export Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/export")
async def export_usage(
    format: str = Query(default="json", pattern="^(json|csv)$", description="Export format"),
    period: str = Query(default="month", pattern="^(day|week|month)$", description="Time period")
):
    """
    Export token usage data.

    Args:
        format: Export format - 'json' or 'csv'
        period: Time period for export

    Returns:
        Usage data in requested format
    """
    tracker = get_token_tracker()
    content, filename = tracker.export_usage(format=format, period=period)

    if format == "json":
        media_type = "application/json"
    else:
        media_type = "text/csv"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_token_stats():
    """
    Get overall token usage statistics.

    Returns:
        Statistics including totals, averages, and trends
    """
    tracker = get_token_tracker()

    # Get summaries for different periods
    day_summary = tracker.get_usage_summary("day")
    week_summary = tracker.get_usage_summary("week")
    month_summary = tracker.get_usage_summary("month")

    # Calculate averages
    avg_tokens_per_request = (
        month_summary.total_tokens / max(month_summary.total_requests, 1)
    )
    avg_cost_per_request = (
        month_summary.estimated_cost_usd / max(month_summary.total_requests, 1)
    )

    # Get top models
    top_models = sorted(
        month_summary.by_model.items(),
        key=lambda x: x[1].total_tokens,
        reverse=True
    )[:5]

    return {
        "today": {
            "requests": day_summary.total_requests,
            "tokens": day_summary.total_tokens,
            "costUsd": round(day_summary.estimated_cost_usd, 4)
        },
        "thisWeek": {
            "requests": week_summary.total_requests,
            "tokens": week_summary.total_tokens,
            "costUsd": round(week_summary.estimated_cost_usd, 4)
        },
        "thisMonth": {
            "requests": month_summary.total_requests,
            "tokens": month_summary.total_tokens,
            "costUsd": round(month_summary.estimated_cost_usd, 4)
        },
        "averages": {
            "tokensPerRequest": round(avg_tokens_per_request, 0),
            "costPerRequest": round(avg_cost_per_request, 6),
            "inputRatio": round(
                month_summary.input_tokens / max(month_summary.total_tokens, 1) * 100, 1
            ),
            "outputRatio": round(
                month_summary.output_tokens / max(month_summary.total_tokens, 1) * 100, 1
            )
        },
        "topModels": [
            {
                "model": name,
                "tokens": breakdown.total_tokens,
                "requests": breakdown.requests,
                "costUsd": round(breakdown.cost_usd, 4)
            }
            for name, breakdown in top_models
        ]
    }
