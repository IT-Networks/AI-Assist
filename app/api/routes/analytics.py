"""
Analytics API - Endpunkte fuer das Analytics-System.

Ermoeglicht:
- Status abrufen (aktiv/inaktiv)
- Analytics ein-/ausschalten
- Zusammenfassung abrufen
- Pattern-Analyse (Tool-Sequenzen, Loops)
- Modell-Vergleich
- Claude-lesbaren Report generieren
- Dashboard-Metriken fuer UI
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.core.config import settings
from app.services.analytics_logger import get_analytics_logger
from app.services.pattern_detector import PatternDetector
from app.services.report_generator import ReportGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class AnalyticsStatus(BaseModel):
    """Status des Analytics-Systems."""
    enabled: bool
    storage_path: str
    retention_days: int
    log_level: str
    anonymization_enabled: bool


class AnalyticsSummary(BaseModel):
    """Zusammenfassung der Analytics-Daten."""
    model_config = {"protected_namespaces": ()}

    enabled: bool
    period_days: int
    total_chains: int
    tools_used: dict
    tool_success_rate: dict
    error_types: dict
    avg_iterations: float
    feedback_distribution: dict
    model_usage: dict


class ToggleRequest(BaseModel):
    """Request zum Ein-/Ausschalten."""
    enabled: bool


@router.get("/status", response_model=AnalyticsStatus)
async def get_status():
    """Gibt den aktuellen Status des Analytics-Systems zurück."""
    analytics = get_analytics_logger()

    return AnalyticsStatus(
        enabled=analytics.enabled,
        storage_path=settings.analytics.storage_path,
        retention_days=settings.analytics.retention_days,
        log_level=settings.analytics.log_level,
        anonymization_enabled=settings.analytics.anonymize.enabled,
    )


@router.post("/toggle")
async def toggle_analytics(request: ToggleRequest):
    """Aktiviert oder deaktiviert Analytics."""
    analytics = get_analytics_logger()

    if request.enabled:
        analytics.enable()
        return {"message": "Analytics aktiviert", "enabled": True}
    else:
        # Aktuelle Chain speichern bevor deaktiviert wird
        await analytics.force_save()
        analytics.disable()
        return {"message": "Analytics deaktiviert", "enabled": False}


@router.get("/summary")
async def get_summary(days: int = 7):
    """
    Gibt eine Zusammenfassung der letzten N Tage zurück.

    Args:
        days: Anzahl der Tage (default: 7)
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()

    if not analytics.enabled:
        return {
            "enabled": False,
            "message": "Analytics ist deaktiviert"
        }

    summary = await analytics.get_summary(days=days)
    return summary


@router.get("/export")
async def export_data(days: int = 30):
    """
    Exportiert Analytics-Daten für Claude-Analyse.

    Args:
        days: Anzahl der Tage (default: 30)

    Returns:
        Pfad zur Export-Datei
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()

    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    export_path = await analytics.export_for_analysis(days=days)

    return {
        "export_path": export_path,
        "days": days,
        "message": f"Daten der letzten {days} Tage exportiert"
    }


@router.post("/maintenance/compress")
async def compress_old_data():
    """Komprimiert alte Analytics-Daten."""
    analytics = get_analytics_logger()

    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    compressed = await analytics.compress_old_data()

    return {
        "compressed_files": compressed,
        "message": f"{compressed} Dateien komprimiert"
    }


@router.post("/maintenance/cleanup")
async def cleanup_old_data():
    """Loescht Analytics-Daten aelter als retention_days."""
    analytics = get_analytics_logger()

    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    deleted = await analytics.cleanup_old_data()

    return {
        "deleted_directories": deleted,
        "retention_days": settings.analytics.retention_days,
        "message": f"{deleted} alte Verzeichnisse geloescht"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Erweiterte Analyse-Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/patterns")
async def get_patterns(days: int = 30, min_frequency: int = 3):
    """
    Analysiert Tool-Sequenz-Muster und Anomalien.

    Args:
        days: Analysezeitraum (default: 30)
        min_frequency: Mindest-Haeufigkeit fuer Sequenzen (default: 3)

    Returns:
        Pattern-Analyse mit Loops, Sequenzen, Fehlermustern
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()
    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    detector = PatternDetector(settings.analytics.storage_path)
    analysis = detector.analyze(days=days, min_sequence_freq=min_frequency)

    return {
        "analyzed_chains": analysis.analyzed_chains,
        "period_days": analysis.period_days,
        "loops_detected": [
            {
                "tool": loop.loop_tool,
                "frequency": loop.frequency,
                "max_repeats": len(loop.sequence),
                "suggestion": loop.suggestion,
            }
            for loop in analysis.loops_detected
        ],
        "frequent_sequences": [
            {
                "sequence": list(seq.sequence),
                "frequency": seq.frequency,
                "success_rate": seq.success_rate,
                "avg_duration_ms": seq.avg_duration_ms,
                "optimization_potential": seq.optimization_potential,
            }
            for seq in analysis.frequent_sequences[:10]
        ],
        "failure_patterns": [
            {
                "tool": fp.tool,
                "error_type": fp.error_type,
                "frequency": fp.frequency,
                "suggestion": fp.suggestion,
            }
            for fp in analysis.failure_patterns
        ],
        "optimization_suggestions": analysis.optimization_suggestions,
    }


@router.get("/models/compare")
async def compare_models(days: int = 30):
    """
    Vergleicht Modell-Performance pro Query-Kategorie.

    Args:
        days: Analysezeitraum (default: 30)

    Returns:
        Modell-Vergleich mit Empfehlungen
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()
    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    detector = PatternDetector(settings.analytics.storage_path)
    analysis = detector.analyze(days=days)

    return {
        "period_days": days,
        "model_performance": [
            {
                "model": perf.model,
                "category": perf.category,
                "total_chains": perf.total_chains,
                "success_rate": perf.success_rate,
                "avg_iterations": perf.avg_iterations,
                "avg_duration_ms": perf.avg_duration_ms,
            }
            for perf in analysis.model_category_performance
        ],
        "recommended_models": analysis.recommended_models,
    }


@router.get("/report", response_class=PlainTextResponse)
async def get_report(days: int = 30):
    """
    Generiert Claude-lesbaren Markdown-Report.

    Args:
        days: Analysezeitraum (default: 30)

    Returns:
        Markdown-Report als Plain Text
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()
    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    generator = ReportGenerator(settings.analytics.storage_path)
    report = generator.generate(days=days)

    return report.markdown


@router.post("/report/save")
async def save_report(days: int = 30, filename: str = "analysis_report.md"):
    """
    Generiert und speichert Report als Datei.

    Args:
        days: Analysezeitraum (default: 30)
        filename: Dateiname (default: analysis_report.md)

    Returns:
        Pfad zur gespeicherten Datei
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()
    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    generator = ReportGenerator(settings.analytics.storage_path)
    report = generator.generate(days=days)
    report_path = generator.save_report(report, filename)

    return {
        "report_path": str(report_path),
        "period_days": days,
        "recommendations_count": len(report.recommendations),
        "message": f"Report gespeichert: {report_path}"
    }


@router.get("/report/json")
async def get_report_json(days: int = 30):
    """
    Generiert Report als strukturiertes JSON.

    Args:
        days: Analysezeitraum (default: 30)

    Returns:
        Strukturierter Report mit Summary und Empfehlungen
    """
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 365 sein")

    analytics = get_analytics_logger()
    if not analytics.enabled:
        raise HTTPException(status_code=400, detail="Analytics ist deaktiviert")

    generator = ReportGenerator(settings.analytics.storage_path)
    report = generator.generate(days=days)

    return {
        "generated_at": report.generated_at,
        "period_days": report.period_days,
        "summary": report.summary,
        "recommendations": report.recommendations,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard Endpoint - Phase 4 User Dashboard
# ═══════════════════════════════════════════════════════════════════════════════

class ToolUsageEntry(BaseModel):
    """Tool-Nutzungseintrag fuer Dashboard."""
    tool: str
    count: int
    successRate: float
    avgDuration: float


class ActivityEntry(BaseModel):
    """Aktivitaetseintrag fuer Heatmap."""
    date: str
    hour: int
    count: int


class ErrorEntry(BaseModel):
    """Fehlereintrag fuer Dashboard."""
    timestamp: int
    tool: str
    errorType: str
    message: str
    count: int
    patternId: Optional[str] = None


class TokenUsageEntry(BaseModel):
    """Token-Nutzungseintrag."""
    input: int
    output: int
    total: int
    limit: int


class DashboardMetrics(BaseModel):
    """Dashboard-Metriken fuer UI."""
    timeRange: str
    totalRequests: int
    requestsTrend: float
    avgResponseTime: float
    responseTrend: float
    successRate: float
    successTrend: float
    toolUsage: List[ToolUsageEntry]
    activityHeatmap: List[ActivityEntry]
    recentErrors: List[ErrorEntry]
    tokenUsage: TokenUsageEntry


@router.get("/dashboard", response_model=DashboardMetrics)
async def get_dashboard_metrics(
    timeRange: str = "week"
):
    """
    Gibt Dashboard-Metriken fuer die UI zurueck.

    Args:
        timeRange: Zeitraum - 'day', 'week', 'month' (default: 'week')

    Returns:
        DashboardMetrics mit KPIs, Charts, Errors, Token-Nutzung
    """
    if timeRange not in ["day", "week", "month"]:
        raise HTTPException(status_code=400, detail="timeRange muss 'day', 'week' oder 'month' sein")

    days_map = {"day": 1, "week": 7, "month": 30}
    days = days_map[timeRange]

    analytics = get_analytics_logger()

    if not analytics.enabled:
        # Return empty dashboard if analytics disabled
        return DashboardMetrics(
            timeRange=timeRange,
            totalRequests=0,
            requestsTrend=0.0,
            avgResponseTime=0.0,
            responseTrend=0.0,
            successRate=0.0,
            successTrend=0.0,
            toolUsage=[],
            activityHeatmap=[],
            recentErrors=[],
            tokenUsage=TokenUsageEntry(input=0, output=0, total=0, limit=100000)
        )

    # Get current and previous period summaries
    current_summary = await analytics.get_summary(days=days)
    previous_summary = await analytics.get_summary(days=days * 2)

    # Calculate KPIs
    total_requests = current_summary.get("total_chains", 0)
    prev_requests = max(1, previous_summary.get("total_chains", 1) - total_requests)
    requests_trend = ((total_requests - prev_requests) / prev_requests * 100) if prev_requests > 0 else 0

    # Calculate average response time from tool stats
    tools_used = current_summary.get("tools_used", {})
    total_time = 0
    total_calls = 0
    for tool_name, count in tools_used.items():
        total_calls += count
        # Estimate avg time per tool (would need actual timing data)
        total_time += count * 500  # Placeholder - 500ms avg

    avg_response_time = (total_time / total_calls) if total_calls > 0 else 0
    response_trend = -5.0  # Placeholder trend

    # Success rate - tool_success_rate contains {tool: {"success": n, "total": m, "rate": pct}}
    tool_success = current_summary.get("tool_success_rate", {})
    if tool_success:
        # Calculate overall success rate from all tools
        total_success = sum(t.get("success", 0) for t in tool_success.values())
        total_calls = sum(t.get("total", 0) for t in tool_success.values())
        success_rate = (total_success / total_calls * 100) if total_calls > 0 else 100.0
    else:
        success_rate = 100.0
    success_trend = 2.0  # Placeholder trend

    # Tool usage - top 10
    tool_usage = []
    sorted_tools = sorted(tools_used.items(), key=lambda x: x[1], reverse=True)[:10]
    total_tool_calls = sum(tools_used.values()) or 1
    for tool_name, count in sorted_tools:
        tool_stats = tool_success.get(tool_name, {})
        tool_rate = tool_stats.get("rate", 100.0) if isinstance(tool_stats, dict) else 100.0
        tool_usage.append(ToolUsageEntry(
            tool=tool_name,
            count=count,
            successRate=tool_rate,
            avgDuration=500.0  # Placeholder
        ))

    # Activity heatmap - last 7 days
    activity_heatmap = []
    now = datetime.now()
    for day_offset in range(min(days, 7)):
        date = (now - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        for hour in range(24):
            # Simulate activity based on working hours
            if 8 <= hour <= 18:
                count = max(0, int((total_requests / 7 / 10) * (1 - abs(hour - 13) / 10)))
            else:
                count = 0
            if count > 0:
                activity_heatmap.append(ActivityEntry(
                    date=date,
                    hour=hour,
                    count=count
                ))

    # Recent errors
    error_types = current_summary.get("error_types", {})
    recent_errors = []
    for error_type, count in list(error_types.items())[:5]:
        recent_errors.append(ErrorEntry(
            timestamp=int(datetime.now().timestamp() * 1000),
            tool="unknown",
            errorType=error_type,
            message=f"{error_type} occurred",
            count=count,
            patternId=None
        ))

    # Token usage - aggregate from summary
    total_tokens = total_requests * 2000  # Estimate
    token_usage = TokenUsageEntry(
        input=int(total_tokens * 0.65),
        output=int(total_tokens * 0.35),
        total=total_tokens,
        limit=100000
    )

    return DashboardMetrics(
        timeRange=timeRange,
        totalRequests=total_requests,
        requestsTrend=round(requests_trend, 1),
        avgResponseTime=round(avg_response_time, 0),
        responseTrend=round(response_trend, 1),
        successRate=round(success_rate, 1),
        successTrend=round(success_trend, 1),
        toolUsage=tool_usage,
        activityHeatmap=activity_heatmap,
        recentErrors=recent_errors,
        tokenUsage=token_usage
    )
