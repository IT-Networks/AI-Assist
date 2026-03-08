import uuid
from pathlib import Path

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, UploadFile, File

from app.core.config import settings
from app.api.schemas import UploadResponse, LogSummaryResponse
from app.services.log_parser import WLPLogParser

router = APIRouter(prefix="/api/logs", tags=["logs"])

# LRU-Cache mit TTL: max 100 Logs, 1 Stunde TTL (verhindert Memory-Leak)
_log_store: TTLCache = TTLCache(maxsize=100, ttl=3600)
_parser = WLPLogParser()


@router.post("/upload", response_model=UploadResponse)
async def upload_log(file: UploadFile = File(...)):
    """Upload a WLP server log file for analysis."""
    max_bytes = settings.uploads.max_file_size_mb * 1024 * 1024
    content_bytes = await file.read()

    if len(content_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Datei zu groß (max {settings.uploads.max_file_size_mb}MB)")

    try:
        content = content_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Log-Datei konnte nicht gelesen werden: {e}")

    log_id = str(uuid.uuid4())[:8]
    parsed = _parser.parse(content)
    summary = _parser.format_for_context(parsed)

    _log_store[log_id] = {
        "filename": file.filename,
        "content": content,
        "summary": summary,
        "parsed": parsed,
    }

    return UploadResponse(
        id=log_id,
        filename=file.filename,
        size_bytes=len(content_bytes),
        message=f"Log analysiert: {parsed.error_count} Fehler, {parsed.warning_count} Warnungen",
    )


@router.get("/{log_id}/errors", response_model=LogSummaryResponse)
async def get_log_errors(log_id: str):
    """Return structured list of errors and warnings from an uploaded log."""
    if log_id not in _log_store:
        raise HTTPException(status_code=404, detail=f"Log-ID nicht gefunden: {log_id}")

    log_data = _log_store[log_id]
    parsed = log_data["parsed"]
    errors = _parser.get_errors(parsed)

    return LogSummaryResponse(
        log_id=log_id,
        total_lines=parsed.total_lines,
        error_count=parsed.error_count,
        warning_count=parsed.warning_count,
        errors=errors,
    )


@router.get("/{log_id}/summary")
async def get_log_summary(log_id: str):
    """Return the formatted log summary for use as LLM context."""
    if log_id not in _log_store:
        raise HTTPException(status_code=404, detail=f"Log-ID nicht gefunden: {log_id}")

    log_data = _log_store[log_id]
    return {"log_id": log_id, "filename": log_data["filename"], "summary": log_data["summary"]}


@router.delete("/{log_id}")
async def delete_log(log_id: str):
    if log_id not in _log_store:
        raise HTTPException(status_code=404, detail=f"Log-ID nicht gefunden: {log_id}")
    del _log_store[log_id]
    return {"message": f"Log {log_id} gelöscht"}
