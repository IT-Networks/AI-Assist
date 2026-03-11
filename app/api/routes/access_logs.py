"""
API-Routen für External Access Logs.

Ermöglicht Abfrage und Analyse aller externen HTTP-Zugriffe.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.config import settings
from app.services.external_access_logger import (
    ExternalAccessEntry,
    get_access_logger,
)

router = APIRouter(prefix="/api/access-logs", tags=["Access Logs"])


class AccessLogEntry(BaseModel):
    """API Response Model für einen Access-Log-Eintrag."""
    id: str
    timestamp: str
    session_id: str
    tool_name: str
    client_type: str
    method: str
    url: str
    host: str
    status_code: int
    success: bool
    response_size: int
    duration_ms: int
    error_message: Optional[str] = None
    content_type: Optional[str] = None


class AccessLogsResponse(BaseModel):
    """API Response für Access-Log-Abfrage."""
    entries: List[AccessLogEntry]
    total: int
    filtered: int


class AccessLogStatistics(BaseModel):
    """API Response für Access-Log-Statistiken."""
    total_requests: int
    success_count: int
    error_count: int
    success_rate: float
    by_tool: dict
    by_host: dict
    by_client: dict
    avg_duration_ms: float
    total_bytes: int
    start_date: str
    end_date: str


class AccessLoggingStatus(BaseModel):
    """Status des Access-Logging-Systems."""
    enabled: bool
    log_directory: str
    max_age_days: int
    log_files: List[dict]


@router.get("/", response_model=AccessLogsResponse)
async def get_access_logs(
    start_date: Optional[str] = Query(None, description="Start-Datum (ISO-8601)"),
    end_date: Optional[str] = Query(None, description="End-Datum (ISO-8601)"),
    host: Optional[str] = Query(None, description="Filter nach Host"),
    tool: Optional[str] = Query(None, description="Filter nach Tool-Name"),
    client: Optional[str] = Query(None, description="Filter nach Client-Typ"),
    success_only: bool = Query(False, description="Nur erfolgreiche Requests"),
    errors_only: bool = Query(False, description="Nur fehlgeschlagene Requests"),
    limit: int = Query(100, ge=1, le=1000, description="Max. Ergebnisse"),
):
    """
    Gibt Access-Log-Einträge zurück.

    Unterstützt Filter nach Zeitraum, Host, Tool und Erfolgs-Status.
    """
    logger = get_access_logger()

    # Daten parsen
    start = None
    end = None
    if start_date:
        try:
            start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            start = datetime.utcnow() - timedelta(days=7)
    if end_date:
        try:
            end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            end = datetime.utcnow()

    entries = await logger.search_logs(
        start_date=start,
        end_date=end,
        host=host,
        tool_name=tool,
        client_type=client,
        success_only=success_only,
        errors_only=errors_only,
        limit=limit,
    )

    # Zu API Response konvertieren
    api_entries = [
        AccessLogEntry(
            id=e.id,
            timestamp=e.timestamp,
            session_id=e.session_id,
            tool_name=e.tool_name,
            client_type=e.client_type,
            method=e.method,
            url=e.url,
            host=e.host,
            status_code=e.status_code,
            success=e.success,
            response_size=e.response_size,
            duration_ms=e.duration_ms,
            error_message=e.error_message,
            content_type=e.content_type,
        )
        for e in entries
    ]

    return AccessLogsResponse(
        entries=api_entries,
        total=len(api_entries),
        filtered=len(api_entries),
    )


@router.get("/statistics", response_model=AccessLogStatistics)
async def get_access_statistics(
    start_date: Optional[str] = Query(None, description="Start-Datum (ISO-8601)"),
    end_date: Optional[str] = Query(None, description="End-Datum (ISO-8601)"),
):
    """
    Gibt Zugriffsstatistiken für den angegebenen Zeitraum zurück.

    Enthält Requests pro Tool/Host, Erfolgsrate und Durchschnitts-Antwortzeit.
    """
    logger = get_access_logger()

    # Daten parsen
    start = None
    end = None
    if start_date:
        try:
            start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            start = datetime.utcnow() - timedelta(days=7)
    if end_date:
        try:
            end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            end = datetime.utcnow()

    stats = await logger.get_statistics(start_date=start, end_date=end)

    return AccessLogStatistics(**stats)


@router.get("/status", response_model=AccessLoggingStatus)
async def get_access_logging_status():
    """
    Gibt den Status des Access-Logging-Systems zurück.

    Zeigt Konfiguration und vorhandene Log-Dateien.
    """
    logger = get_access_logger()

    return AccessLoggingStatus(
        enabled=settings.access_logging.enabled,
        log_directory=str(logger.base_dir),
        max_age_days=settings.access_logging.max_age_days,
        log_files=logger.get_log_files()[:30],  # Letzte 30 Tage
    )


@router.post("/cleanup")
async def cleanup_old_logs(
    max_age_days: Optional[int] = Query(None, description="Max. Alter in Tagen"),
):
    """
    Löscht alte Access-Log-Dateien.

    Standardmäßig werden Logs älter als 90 Tage gelöscht.
    """
    logger = get_access_logger()
    deleted = await logger.cleanup_old_logs(max_age_days)

    return {
        "success": True,
        "deleted_files": deleted,
        "message": f"{deleted} Log-Datei(en) gelöscht",
    }
