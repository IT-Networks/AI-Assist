"""
Script API Routes - REST-Endpoints für Python-Script-Management.

Endpunkte für:
- Script-Liste abrufen
- Script-Details anzeigen
- Script löschen
- Script manuell ausführen
- Statistiken abrufen
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.config import settings
from app.services.script_manager import (
    ExecutionResult,
    Script,
    ScriptNotFoundError,
    ScriptSecurityError,
    get_script_manager,
)

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


# ══════════════════════════════════════════════════════════════════════════════
# Response Models
# ══════════════════════════════════════════════════════════════════════════════

class ScriptSummary(BaseModel):
    """Kurze Script-Info für Listen."""
    id: str
    name: str
    description: str
    created_at: str
    last_executed: Optional[str] = None
    execution_count: int = 0


class ScriptDetail(BaseModel):
    """Vollständige Script-Info."""
    id: str
    name: str
    description: str
    code: str
    created_at: str
    last_executed: Optional[str] = None
    execution_count: int = 0
    parameters: dict = {}
    tags: list = []
    file_path: Optional[str] = None


class ScriptExecutionResponse(BaseModel):
    """Ergebnis einer Script-Ausführung."""
    success: bool
    stdout: str
    stderr: str
    execution_time_ms: int
    error: Optional[str] = None


class ScriptStatsResponse(BaseModel):
    """Script-Statistiken."""
    enabled: bool
    script_count: int
    total_executions: int
    total_size_kb: float
    scripts_directory: str
    require_confirmation: bool


class ValidationResponse(BaseModel):
    """Ergebnis einer Script-Validierung."""
    is_safe: bool
    errors: List[str]
    warnings: List[str]
    imports_used: List[str]


# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_model=List[ScriptSummary])
async def list_scripts(
    filter: Optional[str] = Query(None, description="Filtertext für Name/Beschreibung"),
    limit: int = Query(50, ge=1, le=200, description="Max. Anzahl Ergebnisse")
):
    """Listet alle verfügbaren Scripte."""
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()
    scripts = manager.list_scripts(filter)[:limit]

    return [
        ScriptSummary(
            id=s.id,
            name=s.name,
            description=s.description,
            created_at=s.created_at.isoformat(),
            last_executed=s.last_executed.isoformat() if s.last_executed else None,
            execution_count=s.execution_count
        )
        for s in scripts
    ]


@router.get("/stats", response_model=ScriptStatsResponse)
async def get_stats():
    """Gibt Script-Statistiken zurück."""
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()
    stats = manager.get_stats()

    return ScriptStatsResponse(**stats)


@router.get("/{script_id}", response_model=ScriptDetail)
async def get_script(script_id: str):
    """Gibt Details zu einem Script zurück."""
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()
    script = manager.get_script(script_id)

    if not script:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' nicht gefunden")

    return ScriptDetail(
        id=script.id,
        name=script.name,
        description=script.description,
        code=script.code,
        created_at=script.created_at.isoformat(),
        last_executed=script.last_executed.isoformat() if script.last_executed else None,
        execution_count=script.execution_count,
        parameters=script.parameters,
        tags=script.tags,
        file_path=script.file_path
    )


@router.delete("/{script_id}")
async def delete_script(script_id: str):
    """Löscht ein Script."""
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()
    if not manager.delete_script(script_id):
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' nicht gefunden")

    return {"message": f"Script '{script_id}' gelöscht"}


@router.post("/{script_id}/execute", response_model=ScriptExecutionResponse)
async def execute_script(
    script_id: str,
    args: dict = None,
    input_data: str = None
):
    """
    Führt ein Script manuell aus.

    Achtung: Dies umgeht die normale Bestätigung!
    Nur für manuelle Tests gedacht.
    """
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()

    try:
        result = await manager.execute(script_id, args, input_data)

        return ScriptExecutionResponse(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            execution_time_ms=result.execution_time_ms,
            error=result.error
        )

    except ScriptNotFoundError:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' nicht gefunden")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate", response_model=ValidationResponse)
async def validate_script(code: str):
    """
    Validiert Python-Code ohne zu speichern.

    Prüft Syntax, Imports und gefährliche Patterns.
    """
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()
    validation = manager.validate_code(code)

    return ValidationResponse(
        is_safe=validation.is_safe,
        errors=validation.errors,
        warnings=validation.warnings,
        imports_used=validation.imports_used
    )


@router.post("/cleanup")
async def cleanup_old_scripts():
    """
    Löscht alte, ungenutzte Scripte.

    Löscht Scripte die älter als cleanup_days sind und nie ausgeführt wurden.
    """
    if not settings.script_execution.enabled:
        raise HTTPException(status_code=404, detail="Script-Execution ist nicht aktiviert")

    manager = get_script_manager()
    deleted = manager.cleanup()

    return {"deleted_count": deleted, "message": f"{deleted} alte Script(s) gelöscht"}


@router.get("/config/allowed-imports")
async def get_allowed_imports():
    """Gibt die Liste erlaubter Imports zurück."""
    return {
        "allowed_imports": settings.script_execution.allowed_imports,
        "blocked_patterns": settings.script_execution.blocked_patterns
    }
