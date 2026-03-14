"""
Analytics API - Endpunkte fuer das Analytics-System.

Ermoeglicht:
- Status abrufen (aktiv/inaktiv)
- Analytics ein-/ausschalten
- Zusammenfassung abrufen
- Pattern-Analyse (Tool-Sequenzen, Loops)
- Modell-Vergleich
- Claude-lesbaren Report generieren
"""

import logging
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
