"""
Pattern Learning API - Phase 5 Error Pattern Learning.

Ermoeglicht:
- Pattern-Liste abrufen
- Pattern-Vorschlaege fuer Fehler
- Neue Patterns lernen
- User-Feedback aufzeichnen
- Pattern-Export/Import
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.pattern_learner import get_pattern_learner, ErrorPattern

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class PatternResponse(BaseModel):
    """Response fuer ein einzelnes Pattern."""
    id: str
    error_type: str
    confidence: float
    solution_description: str
    solution_steps: List[str]
    solution_code: Optional[str]
    times_seen: int
    times_solved: int
    times_accepted: int
    times_rejected: int
    acceptance_rate: float
    avg_rating: float
    context_keywords: List[str]
    file_patterns: List[str]
    tools_used: List[str]
    created_at: str
    updated_at: str


class PatternSuggestRequest(BaseModel):
    """Request fuer Pattern-Vorschlag."""
    errorType: str
    stackTrace: str = ""
    fileContext: str = ""


class PatternSuggestResponse(BaseModel):
    """Response fuer Pattern-Vorschlag."""
    pattern: Optional[PatternResponse]
    confidence: float
    alternatives: List[PatternResponse]


class PatternLearnRequest(BaseModel):
    """Request zum Lernen eines neuen Patterns."""
    errorType: str
    errorText: str
    stackTrace: str = ""
    solutionDescription: str
    solutionSteps: List[str] = Field(default_factory=list)
    solutionCode: Optional[str] = None
    toolsUsed: List[str] = Field(default_factory=list)
    filesChanged: List[str] = Field(default_factory=list)
    codeContext: str = ""


class PatternLearnResponse(BaseModel):
    """Response nach Pattern-Learning."""
    patternId: str
    isNew: bool
    confidence: float


class PatternFeedbackRequest(BaseModel):
    """Request fuer Pattern-Feedback."""
    accepted: bool
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = None


class PatternFeedbackResponse(BaseModel):
    """Response nach Feedback."""
    success: bool
    newConfidence: float


def _pattern_to_response(pattern: ErrorPattern) -> PatternResponse:
    """Konvertiert ErrorPattern zu API Response."""
    return PatternResponse(
        id=pattern.id,
        error_type=pattern.error_type,
        confidence=pattern.confidence,
        solution_description=pattern.solution_description,
        solution_steps=pattern.solution_steps,
        solution_code=pattern.solution_code,
        times_seen=pattern.times_seen,
        times_solved=pattern.times_solved,
        times_accepted=pattern.times_accepted,
        times_rejected=pattern.times_rejected,
        acceptance_rate=pattern.acceptance_rate,
        avg_rating=pattern.avg_rating,
        context_keywords=pattern.context_keywords,
        file_patterns=pattern.file_patterns,
        tools_used=pattern.tools_used,
        created_at=pattern.created_at.isoformat(),
        updated_at=pattern.updated_at.isoformat(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=List[PatternResponse])
async def list_patterns(
    minConfidence: float = 0.0,
    errorType: Optional[str] = None,
    limit: int = 50
):
    """
    Listet alle Error Patterns.

    Args:
        minConfidence: Minimale Confidence (default: 0.0)
        errorType: Filter nach Error-Typ (optional)
        limit: Maximale Anzahl (default: 50)
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit muss zwischen 1 und 500 sein")

    learner = get_pattern_learner()
    patterns = learner.list_patterns(
        min_confidence=minConfidence,
        error_type=errorType,
        limit=limit
    )

    return [_pattern_to_response(p) for p in patterns]


@router.get("/{pattern_id}", response_model=PatternResponse)
async def get_pattern(pattern_id: str):
    """Gibt ein einzelnes Pattern zurueck."""
    learner = get_pattern_learner()
    pattern = learner.get_pattern_by_id(pattern_id)

    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern nicht gefunden")

    return _pattern_to_response(pattern)


@router.post("/suggest", response_model=PatternSuggestResponse)
async def suggest_pattern(request: PatternSuggestRequest):
    """
    Schlaegt ein Pattern fuer einen Fehler vor.

    Sucht nach exaktem Hash-Match oder aehnlichen Patterns
    basierend auf Keywords und Error-Typ.
    """
    learner = get_pattern_learner()

    error_text = f"{request.errorType}: {request.stackTrace}"
    pattern, confidence, alternatives = learner.suggest_pattern(
        error_text=error_text,
        stack_trace=request.stackTrace,
        file_context=request.fileContext
    )

    return PatternSuggestResponse(
        pattern=_pattern_to_response(pattern) if pattern else None,
        confidence=confidence,
        alternatives=[
            _pattern_to_response(alt) for alt, _ in alternatives[:3]
        ]
    )


@router.post("/learn", response_model=PatternLearnResponse)
async def learn_pattern(request: PatternLearnRequest):
    """
    Lernt ein neues Pattern oder aktualisiert ein bestehendes.

    Extrahiert automatisch:
    - Error-Typ aus dem Fehlertext
    - Keywords fuer Similarity-Matching
    - File-Patterns aus geaenderten Dateien
    """
    learner = get_pattern_learner()

    pattern, is_new = learner.learn_pattern(
        error_text=f"{request.errorType}: {request.errorText}",
        stack_trace=request.stackTrace,
        solution_description=request.solutionDescription,
        solution_steps=request.solutionSteps,
        solution_code=request.solutionCode,
        tools_used=request.toolsUsed,
        files_changed=request.filesChanged,
        code_context=request.codeContext
    )

    return PatternLearnResponse(
        patternId=pattern.id,
        isNew=is_new,
        confidence=pattern.confidence
    )


@router.post("/{pattern_id}/feedback", response_model=PatternFeedbackResponse)
async def record_feedback(pattern_id: str, request: PatternFeedbackRequest):
    """
    Zeichnet User-Feedback fuer ein Pattern auf.

    Updates:
    - times_accepted oder times_rejected
    - user_ratings (falls rating angegeben)
    - confidence (wird neu berechnet)
    """
    learner = get_pattern_learner()

    pattern = learner.record_feedback(
        pattern_id=pattern_id,
        accepted=request.accepted,
        rating=request.rating,
        comment=request.comment
    )

    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern nicht gefunden")

    return PatternFeedbackResponse(
        success=True,
        newConfidence=pattern.confidence
    )


@router.delete("/{pattern_id}")
async def delete_pattern(pattern_id: str):
    """Loescht ein Pattern."""
    learner = get_pattern_learner()

    success = learner.delete_pattern(pattern_id)

    if not success:
        raise HTTPException(status_code=404, detail="Pattern nicht gefunden")

    return {"success": True, "message": "Pattern geloescht"}


@router.post("/cleanup")
async def cleanup_patterns(max_age_days: int = 90):
    """
    Bereinigt alte Patterns mit niedriger Confidence.

    Args:
        max_age_days: Maximales Alter in Tagen (default: 90)
    """
    if max_age_days < 1 or max_age_days > 365:
        raise HTTPException(status_code=400, detail="max_age_days muss zwischen 1 und 365 sein")

    learner = get_pattern_learner()
    deleted = learner.cleanup_old_patterns(max_age_days)

    return {
        "deleted": deleted,
        "message": f"{deleted} alte Patterns geloescht"
    }


@router.get("/export/json")
async def export_patterns():
    """Exportiert alle Patterns als JSON."""
    import tempfile
    from fastapi.responses import FileResponse

    learner = get_pattern_learner()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        count = learner.export_patterns(f.name)

    return FileResponse(
        f.name,
        media_type="application/json",
        filename=f"error_patterns_{count}.json"
    )


@router.post("/import/json")
async def import_patterns_endpoint():
    """
    Import-Endpoint fuer Patterns.
    (Vereinfachte Implementierung - in Production mit File Upload)
    """
    return {
        "message": "Pattern-Import wird ueber CLI unterstuetzt",
        "command": "python -m app.services.pattern_learner import <file.json>"
    }
