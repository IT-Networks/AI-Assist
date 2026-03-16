"""
Skills API Routes - Endpunkte für Skill-Verwaltung und -Aktivierung.
"""

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from app.core.config import settings
from app.models.skill import (
    SkillSummary,
    SkillDetail,
    SkillCreateRequest,
    SkillFromPDFRequest,
    SkillActivateRequest,
    SkillSearchResult,
    SkillType,
    ActivationMode,
)
from app.services.skill_manager import get_skill_manager


router = APIRouter(prefix="/api/skills", tags=["skills"])


# ══════════════════════════════════════════════════════════════════════════════
# Response Models
# ══════════════════════════════════════════════════════════════════════════════

class SkillStatsResponse(BaseModel):
    total_skills: int
    skills_with_knowledge: int
    skills_with_prompt: int
    total_knowledge_chunks: int
    active_sessions: int


class SkillCreatedResponse(BaseModel):
    id: str
    name: str
    message: str


class SkillKnowledgeSearchResponse(BaseModel):
    query: str
    results: List[SkillSearchResult]


class ActiveSkillsResponse(BaseModel):
    session_id: str
    active_skill_ids: List[str]
    combined_prompt: str
    knowledge_context: str


# ══════════════════════════════════════════════════════════════════════════════
# CRUD Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=List[SkillSummary])
async def list_skills(
    session_id: Optional[str] = Query(None, description="Session-ID für Aktivierungsstatus")
):
    """Listet alle verfügbaren Skills auf."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    return manager.list_skills(session_id=session_id)


@router.get("/stats", response_model=SkillStatsResponse)
async def get_stats():
    """Gibt Statistiken über Skills zurück."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    stats = manager.get_stats()
    return SkillStatsResponse(**stats)


@router.get("/{skill_id}", response_model=SkillDetail)
async def get_skill(
    skill_id: str,
    session_id: Optional[str] = Query(None, description="Session-ID für Aktivierungsstatus")
):
    """Gibt detaillierte Informationen zu einem Skill zurück."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    skill = manager.get_skill_detail(skill_id, session_id=session_id)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' nicht gefunden")

    return skill


@router.post("", response_model=SkillCreatedResponse)
async def create_skill(request: SkillCreateRequest):
    """Erstellt einen neuen Skill."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()

    try:
        skill = manager.create_skill(
            name=request.name,
            description=request.description,
            skill_type=request.type,
            activation_mode=request.activation_mode,
            trigger_words=request.trigger_words,
            system_prompt=request.system_prompt,
            tags=request.tags
        )

        return SkillCreatedResponse(
            id=skill.id,
            name=skill.name,
            message=f"Skill '{skill.name}' erfolgreich erstellt"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{skill_id}", response_model=SkillDetail)
async def update_skill(
    skill_id: str,
    request: SkillCreateRequest
):
    """Aktualisiert einen Skill."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()

    updates = {
        "name": request.name,
        "description": request.description,
        "type": request.type,
        "system_prompt": request.system_prompt,
    }
    # activation als nested update
    skill = manager.get_skill(skill_id)
    if skill:
        skill.activation.mode = request.activation_mode
        skill.activation.trigger_words = request.trigger_words
        skill.metadata.tags = request.tags

    updated = manager.update_skill(skill_id, **updates)

    if not updated:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' nicht gefunden")

    return manager.get_skill_detail(skill_id)


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    """Löscht einen Skill."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    success = manager.delete_skill(skill_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' nicht gefunden")

    return {"message": f"Skill '{skill_id}' gelöscht"}


# ══════════════════════════════════════════════════════════════════════════════
# Activation Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{skill_id}/activate")
async def activate_skill(
    skill_id: str,
    session_id: str = Query(..., description="Session-ID")
):
    """Aktiviert einen Skill für eine Session."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    success = manager.activate_skill(session_id, skill_id)

    if not success:
        # Prüfen ob Skill existiert
        if not manager.get_skill(skill_id):
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' nicht gefunden")
        raise HTTPException(
            status_code=400,
            detail=f"Maximale Anzahl aktiver Skills ({settings.skills.max_active_skills}) erreicht"
        )

    return {"message": f"Skill '{skill_id}' aktiviert", "session_id": session_id}


@router.post("/{skill_id}/deactivate")
async def deactivate_skill(
    skill_id: str,
    session_id: str = Query(..., description="Session-ID")
):
    """Deaktiviert einen Skill für eine Session."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    manager.deactivate_skill(session_id, skill_id)

    return {"message": f"Skill '{skill_id}' deaktiviert", "session_id": session_id}


@router.post("/activate-batch")
async def activate_skills_batch(request: SkillActivateRequest, session_id: str = Query(...)):
    """Aktiviert oder deaktiviert mehrere Skills auf einmal."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()

    if request.activate:
        # Alle aktiven Skills ersetzen
        manager.set_active_skills(session_id, request.skill_ids)
    else:
        for skill_id in request.skill_ids:
            manager.deactivate_skill(session_id, skill_id)

    return {
        "message": f"Skills {'aktiviert' if request.activate else 'deaktiviert'}",
        "session_id": session_id,
        "skill_ids": request.skill_ids
    }


@router.get("/session/{session_id}/active", response_model=ActiveSkillsResponse)
async def get_active_skills(
    session_id: str,
    query: Optional[str] = Query(None, description="Suchbegriff für Wissenskontext")
):
    """Gibt die aktiven Skills einer Session zurück."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    active_ids = list(manager.get_active_skill_ids(session_id))
    combined_prompt = manager.build_system_prompt(session_id)

    knowledge_context = ""
    if query:
        knowledge_context = manager.get_knowledge_context(session_id, query)

    return ActiveSkillsResponse(
        session_id=session_id,
        active_skill_ids=active_ids,
        combined_prompt=combined_prompt,
        knowledge_context=knowledge_context
    )


# ══════════════════════════════════════════════════════════════════════════════
# Knowledge Search
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search/knowledge", response_model=SkillKnowledgeSearchResponse)
async def search_knowledge(
    q: str = Query(..., min_length=1, description="Suchbegriff"),
    skill_ids: Optional[str] = Query(None, description="Komma-getrennte Skill-IDs"),
    top_k: int = Query(5, ge=1, le=50)
):
    """
    Durchsucht die Wissensbasen von Skills.

    Wenn skill_ids angegeben, wird nur in diesen Skills gesucht.
    Sonst wird in allen Skills gesucht.
    """
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()

    ids = skill_ids.split(",") if skill_ids else None
    results = manager.search_knowledge(q, skill_ids=ids, top_k=top_k)

    return SkillKnowledgeSearchResponse(query=q, results=results)


# ══════════════════════════════════════════════════════════════════════════════
# PDF to Skill
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/from-pdf", response_model=SkillCreatedResponse)
async def create_skill_from_pdf(request: SkillFromPDFRequest):
    """
    Erstellt einen neuen Skill aus einer hochgeladenen PDF.

    Die PDF muss vorher über /api/pdf/upload hochgeladen worden sein.
    """
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    # PDF aus Store holen
    from app.api.routes.pdf import _pdf_store
    if request.pdf_id not in _pdf_store:
        raise HTTPException(status_code=404, detail=f"PDF '{request.pdf_id}' nicht gefunden")

    pdf_data = _pdf_store[request.pdf_id]
    pdf_path = pdf_data.get("path")

    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=400, detail="PDF-Datei nicht mehr verfügbar")

    manager = get_skill_manager()

    try:
        skill = await manager.create_skill_from_pdf(
            pdf_path=pdf_path,
            name=request.name,
            description=request.description,
            trigger_words=request.trigger_words,
            system_prompt=request.system_prompt,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
            selected_pages=request.selected_pages
        )

        return SkillCreatedResponse(
            id=skill.id,
            name=skill.name,
            message=f"Skill '{skill.name}' aus PDF erstellt"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/from-pdf-upload", response_model=SkillCreatedResponse)
async def create_skill_from_pdf_upload(
    file: UploadFile = File(...),
    name: str = Query(..., description="Name des Skills"),
    description: str = Query("", description="Beschreibung"),
    system_prompt: str = Query(
        "Beantworte Fragen basierend auf dem folgenden Dokument.",
        description="System-Prompt"
    ),
    trigger_words: str = Query("", description="Komma-getrennte Trigger-Wörter"),
    chunk_size: int = Query(1000, ge=100, le=5000),
):
    """
    Lädt eine PDF hoch und erstellt direkt einen Skill daraus.

    Kombiniert Upload und Skill-Erstellung in einem Schritt.
    """
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien erlaubt")

    # PDF temporär speichern
    uploads_dir = Path(settings.uploads.directory)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    import uuid
    temp_path = uploads_dir / f"skill_upload_{uuid.uuid4().hex}.pdf"

    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        manager = get_skill_manager()
        triggers = [t.strip() for t in trigger_words.split(",") if t.strip()]

        skill = await manager.create_skill_from_pdf(
            pdf_path=str(temp_path),
            name=name,
            description=description,
            trigger_words=triggers,
            system_prompt=system_prompt,
            chunk_size=chunk_size
        )

        return SkillCreatedResponse(
            id=skill.id,
            name=skill.name,
            message=f"Skill '{skill.name}' aus PDF '{file.filename}' erstellt"
        )
    finally:
        # Temporäre Datei löschen (Kopie ist in skills/data/)
        if temp_path.exists():
            temp_path.unlink()


# ══════════════════════════════════════════════════════════════════════════════
# Reindex
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{skill_id}/reindex")
async def reindex_skill(skill_id: str):
    """Indexiert die Wissensquellen eines Skills neu."""
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()

    if not manager.get_skill(skill_id):
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' nicht gefunden")

    chunks = manager.reindex_skill(skill_id)

    return {
        "message": f"Skill '{skill_id}' neu indexiert",
        "chunks_indexed": chunks
    }


# ══════════════════════════════════════════════════════════════════════════════
# MCP Command Integration (NEU)
# ══════════════════════════════════════════════════════════════════════════════

class CommandSkillsResponse(BaseModel):
    """Response für Skills eines MCP-Commands."""
    command: str
    skills: List[str]
    combined_system_prompt: str
    research_config: Optional[dict] = None
    output_config: Optional[dict] = None


class CommandTriggersResponse(BaseModel):
    """Übersicht welche Commands welche Skills triggern."""
    triggers: dict  # {"brainstorm": ["skill-1"], "design": ["skill-2"]}


# WICHTIG: Diese Route MUSS vor /for-command/{command} stehen,
# da sonst "command-triggers" als {command} gematcht wird!
@router.get("/command-triggers", response_model=CommandTriggersResponse)
async def list_command_triggers():
    """
    Gibt eine Übersicht welche MCP-Commands welche Skills triggern.

    Nützlich für UI um zu zeigen welche Commands erweitert werden.
    """
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    triggers = manager.list_command_triggers()

    return CommandTriggersResponse(triggers=triggers)


@router.get("/for-command/{command}", response_model=CommandSkillsResponse)
async def get_skills_for_command(
    command: str,
    session_id: Optional[str] = Query(None, description="Session-ID für Aktivierungsstatus")
):
    """
    Gibt alle Skills zurück, die für ein MCP-Command konfiguriert sind.

    MCP-Commands wie /brainstorm oder /design können Skills mit
    `activation.trigger_commands` automatisch aktivieren.
    """
    if not settings.skills.enabled:
        raise HTTPException(status_code=404, detail="Skill-Feature ist nicht aktiviert")

    manager = get_skill_manager()
    config = manager.get_command_skills_config(command)

    return CommandSkillsResponse(
        command=command,
        skills=config["skills"],
        combined_system_prompt=config["combined_system_prompt"],
        research_config=config["research"],
        output_config=config["output"]
    )
