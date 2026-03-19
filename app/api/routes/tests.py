"""
JUnit Test Execution API Routes.

Endpoints für Test-Ausführung, Ergebnis-Anzeige und Fix-Generierung.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.test_execution import (
    get_test_execution_service,
    get_test_fix_generator,
    TestExecutionService,
    TestFixGenerator,
    TestCase,
    TestStatus,
    TestRun,
    TestFix
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tests", tags=["tests"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class TestRunRequest(BaseModel):
    target: str  # Class or package name
    session_id: str = "default"
    with_coverage: bool = True
    test_method: Optional[str] = None


class FixGenerateRequest(BaseModel):
    test_class: str
    test_method: str
    failure_message: str
    stack_trace: Optional[str] = None
    failure_type: Optional[str] = None


class FixValidateRequest(BaseModel):
    fix_id: str
    file_path: str
    original_code: str
    fixed_code: str
    test_class: str
    test_method: str


class FixApplyRequest(BaseModel):
    file_path: str
    original_code: str
    fixed_code: str


class TestRunResponse(BaseModel):
    id: str
    target: str
    status: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    skipped_tests: int
    duration_seconds: float
    coverage_percent: Optional[float] = None


class TestSuiteResponse(BaseModel):
    name: str
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_seconds: float
    tests: List[Dict[str, Any]]


class FixResponse(BaseModel):
    id: str
    test_class: str
    test_method: str
    fix_type: str
    description: str
    confidence: float
    file_path: str
    diff: str
    validated: bool = False
    validation_passed: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/run")
async def run_tests(request: TestRunRequest):
    """
    Startet Test-Lauf mit SSE Streaming.

    Streamt Events:
    - started: Test-Lauf gestartet
    - output: Build-Output Zeile
    - test_started: Test gestartet
    - test_finished: Test abgeschlossen
    - suite_finished: Suite abgeschlossen
    - finished: Alle Tests abgeschlossen
    - error: Fehler aufgetreten
    """
    service = get_test_execution_service()

    async def event_generator():
        try:
            async for event in service.run_tests(
                target=request.target,
                session_id=request.session_id,
                with_coverage=request.with_coverage,
                test_method=request.test_method
            ):
                # Format as SSE
                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error(f"[TestAPI] Error in test execution: {e}")
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/runs/{session_id}")
async def get_test_runs(session_id: str) -> List[TestRunResponse]:
    """Alle Test-Läufe einer Session."""
    service = get_test_execution_service()
    runs = service.get_session_runs(session_id)

    return [
        TestRunResponse(
            id=run.id,
            target=run.target,
            status=run.status.value,
            total_tests=run.total_tests,
            passed_tests=run.passed_tests,
            failed_tests=run.failed_tests,
            error_tests=run.error_tests,
            skipped_tests=run.skipped_tests,
            duration_seconds=run.duration_seconds,
            coverage_percent=run.coverage_percent
        )
        for run in runs
    ]


@router.get("/runs/{session_id}/{run_id}")
async def get_test_run(session_id: str, run_id: str) -> Dict[str, Any]:
    """Details eines Test-Laufs."""
    service = get_test_execution_service()
    run = service.get_test_run(run_id)

    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Test run not found: {run_id}")

    return run.to_dict()


@router.get("/runs/{session_id}/{run_id}/failures")
async def get_test_failures(session_id: str, run_id: str) -> List[Dict[str, Any]]:
    """Gibt alle fehlgeschlagenen Tests eines Laufs zurück."""
    service = get_test_execution_service()
    run = service.get_test_run(run_id)

    if not run or run.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Test run not found: {run_id}")

    failures = []
    for suite in run.suites:
        for test in suite.tests:
            if test.status in (TestStatus.FAILED, TestStatus.ERROR):
                failures.append(test.to_dict())

    return failures


@router.post("/fix/generate")
async def generate_fix(request: FixGenerateRequest) -> FixResponse:
    """
    Generiert Fix für fehlgeschlagenen Test.

    Verwendet Pattern-Matching und LLM für Fix-Generierung.
    """
    generator = get_test_fix_generator()

    # Create TestCase from request
    test_case = TestCase(
        name=request.test_method,
        class_name=request.test_class,
        status=TestStatus.FAILED,
        failure_message=request.failure_message,
        failure_type=request.failure_type,
        stack_trace=request.stack_trace
    )

    fix = await generator.generate_fix(test_case)

    if not fix:
        raise HTTPException(
            status_code=422,
            detail="Konnte keinen Fix generieren. Prüfe die Fehlermeldung und Stack-Trace."
        )

    return FixResponse(
        id=fix.id,
        test_class=fix.test_class,
        test_method=fix.test_method,
        fix_type=fix.fix_type,
        description=fix.description,
        confidence=fix.confidence,
        file_path=fix.file_path,
        diff=fix.diff,
        validated=fix.validated,
        validation_passed=fix.validation_passed
    )


@router.post("/fix/validate")
async def validate_fix(request: FixValidateRequest) -> FixResponse:
    """
    Validiert Fix durch erneuten Test-Lauf.

    Wendet Fix temporär an, führt Test aus und stellt Original wieder her.
    """
    service = get_test_execution_service()
    generator = get_test_fix_generator()

    fix = TestFix(
        id=request.fix_id,
        test_class=request.test_class,
        test_method=request.test_method,
        fix_type="validation",
        description="Validation in progress",
        confidence=1.0,
        file_path=request.file_path,
        original_code=request.original_code,
        fixed_code=request.fixed_code,
        diff=""
    )

    validated_fix = await generator.validate_fix(fix, service)

    return FixResponse(
        id=validated_fix.id,
        test_class=validated_fix.test_class,
        test_method=validated_fix.test_method,
        fix_type=validated_fix.fix_type,
        description=validated_fix.validation_output or validated_fix.description,
        confidence=validated_fix.confidence,
        file_path=validated_fix.file_path,
        diff=validated_fix.diff,
        validated=validated_fix.validated,
        validation_passed=validated_fix.validation_passed
    )


@router.post("/fix/apply")
async def apply_fix(request: FixApplyRequest) -> Dict[str, Any]:
    """
    Wendet Fix an (schreibt Datei).

    WARNUNG: Überschreibt die Originaldatei!
    """
    from pathlib import Path

    file_path = Path(request.file_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {request.file_path}")

    try:
        # Read current content
        current_content = file_path.read_text(encoding='utf-8')

        # Verify original code exists
        if request.original_code and request.original_code not in current_content:
            raise HTTPException(
                status_code=409,
                detail="Original-Code nicht gefunden - Datei wurde möglicherweise geändert"
            )

        # Apply fix
        if request.original_code:
            new_content = current_content.replace(request.original_code, request.fixed_code, 1)
        else:
            new_content = request.fixed_code

        # Write back
        file_path.write_text(new_content, encoding='utf-8')

        return {
            "status": "applied",
            "file_path": str(file_path),
            "message": "Fix erfolgreich angewendet"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Anwenden des Fixes: {str(e)}")


@router.get("/status")
async def get_test_status() -> Dict[str, Any]:
    """
    Status des Test-Systems.

    Gibt Konfiguration und Verfügbarkeit zurück.
    """
    from app.core.config import settings

    java_path = settings.java.get_active_path()
    service = get_test_execution_service()

    return {
        "available": bool(java_path),
        "java_path": java_path or None,
        "build_tool": service.build_tool if java_path else None,
        "maven_available": bool(java_path and (Path(java_path) / "pom.xml").exists()) if java_path else False,
        "gradle_available": bool(java_path and (
            (Path(java_path) / "build.gradle").exists() or
            (Path(java_path) / "build.gradle.kts").exists()
        )) if java_path else False
    }
