"""
Designs API Routes - Persistente Speicherung von Design-Outputs.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.design_persistence import (
    get_design_persistence,
    DesignType,
    DesignStatus,
    DesignSummary,
    SavedDesign,
)


router = APIRouter(prefix="/api/designs", tags=["designs"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class SaveDesignRequest(BaseModel):
    """Request zum Speichern eines Designs."""
    type: str = Field(..., description="Typ: brainstorm oder design")
    title: str = Field(..., min_length=1, description="Titel des Designs")
    content: str = Field(..., min_length=1, description="Markdown-Inhalt")
    tags: List[str] = Field(default=[], description="Tags für Kategorisierung")
    sources: List[str] = Field(default=[], description="Verwendete Quellen")
    command: Optional[str] = Field(None, description="Ursprünglicher MCP-Command")


class SaveDesignResponse(BaseModel):
    """Response nach Speichern."""
    id: str
    file_path: str
    title: str
    created: str
    message: str


class DesignListResponse(BaseModel):
    """Response für Design-Liste."""
    designs: List[DesignSummary]
    total: int


class DesignDetailResponse(BaseModel):
    """Response für Design-Details."""
    id: str
    type: str
    title: str
    status: str
    created: str
    updated: Optional[str]
    tags: List[str]
    sources: List[str]
    content: str
    file_path: str
    implementation_refs: List[dict]


class UpdateStatusRequest(BaseModel):
    """Request zum Aktualisieren des Status."""
    status: str = Field(..., description="Neuer Status: draft, approved, implemented, archived")


class LinkImplementationRequest(BaseModel):
    """Request zum Verknüpfen von Implementations-Dateien."""
    files: List[str] = Field(..., description="Liste der implementierten Dateien")
    commit: Optional[str] = Field(None, description="Commit-Hash")


class StatsResponse(BaseModel):
    """Response für Statistiken."""
    total: int
    by_type: dict
    by_status: dict
    last_updated: Optional[str]


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/save", response_model=SaveDesignResponse)
async def save_design(request: SaveDesignRequest):
    """
    Speichert ein Design als MD-Datei.

    Das Design wird mit YAML-Frontmatter und Implementation-Tracking
    als Markdown-Datei im designs-Verzeichnis gespeichert.
    """
    # Typ validieren
    try:
        design_type = DesignType(request.type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger Typ: {request.type}. Erlaubt: brainstorm, design"
        )

    persistence = get_design_persistence()

    saved = persistence.save(
        design_type=design_type,
        title=request.title,
        content=request.content,
        tags=request.tags,
        sources=request.sources,
        command=request.command
    )

    return SaveDesignResponse(
        id=saved.metadata.id,
        file_path=saved.file_path,
        title=saved.metadata.title,
        created=saved.metadata.created.isoformat(),
        message=f"Design gespeichert: {saved.file_path}"
    )


@router.get("", response_model=DesignListResponse)
async def list_designs(
    type: Optional[str] = Query(None, description="Filter nach Typ"),
    status: Optional[str] = Query(None, description="Filter nach Status"),
    tag: Optional[str] = Query(None, description="Filter nach Tag"),
    limit: int = Query(50, ge=1, le=100, description="Maximale Anzahl")
):
    """
    Listet gespeicherte Designs.

    Unterstützt Filter nach Typ, Status und Tags.
    """
    persistence = get_design_persistence()

    # Filter konvertieren
    design_type = DesignType(type) if type else None
    design_status = DesignStatus(status) if status else None

    designs = persistence.list_designs(
        design_type=design_type,
        status=design_status,
        tag=tag,
        limit=limit
    )

    return DesignListResponse(
        designs=designs,
        total=len(designs)
    )


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    Gibt Statistiken über gespeicherte Designs zurück.
    """
    persistence = get_design_persistence()
    stats = persistence.get_stats()

    return StatsResponse(**stats)


@router.get("/{design_id}", response_model=DesignDetailResponse)
async def get_design(design_id: str):
    """
    Lädt ein Design anhand seiner ID.

    Gibt den vollständigen Inhalt inkl. Metadaten zurück.
    """
    persistence = get_design_persistence()
    saved = persistence.get_design(design_id)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail=f"Design nicht gefunden: {design_id}"
        )

    return DesignDetailResponse(
        id=saved.metadata.id,
        type=saved.metadata.type.value,
        title=saved.metadata.title,
        status=saved.metadata.status.value,
        created=saved.metadata.created.isoformat(),
        updated=saved.metadata.updated.isoformat() if saved.metadata.updated else None,
        tags=saved.metadata.tags,
        sources=saved.metadata.sources,
        content=saved.content,
        file_path=saved.file_path,
        implementation_refs=[
            {
                "file_path": ref.file_path,
                "status": ref.status,
                "commit": ref.commit
            }
            for ref in saved.metadata.implementation_refs
        ]
    )


@router.put("/{design_id}/status")
async def update_status(design_id: str, request: UpdateStatusRequest):
    """
    Aktualisiert den Status eines Designs.

    Status-Werte: draft, approved, implemented, archived
    """
    try:
        status = DesignStatus(request.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger Status: {request.status}. "
                   f"Erlaubt: {[s.value for s in DesignStatus]}"
        )

    persistence = get_design_persistence()
    success = persistence.update_status(design_id, status)

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Design nicht gefunden: {design_id}"
        )

    return {
        "id": design_id,
        "status": status.value,
        "message": f"Status aktualisiert auf: {status.value}"
    }


@router.post("/{design_id}/link")
async def link_implementation(design_id: str, request: LinkImplementationRequest):
    """
    Verknüpft Implementations-Dateien mit einem Design.

    Aktualisiert die Implementation-Tracking-Tabelle in der MD-Datei.
    """
    if not request.files:
        raise HTTPException(
            status_code=400,
            detail="Mindestens eine Datei erforderlich"
        )

    persistence = get_design_persistence()
    success = persistence.link_implementation(
        design_id=design_id,
        files=request.files,
        commit=request.commit
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Design nicht gefunden: {design_id}"
        )

    return {
        "id": design_id,
        "linked_files": len(request.files),
        "message": f"{len(request.files)} Datei(en) verknüpft"
    }


@router.get("/{design_id}/content")
async def get_design_content(design_id: str):
    """
    Gibt nur den Markdown-Inhalt eines Designs zurück.

    Nützlich für direkte Einbindung in andere Contexts.
    """
    persistence = get_design_persistence()
    saved = persistence.get_design(design_id)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail=f"Design nicht gefunden: {design_id}"
        )

    return {
        "id": design_id,
        "title": saved.metadata.title,
        "content": saved.content
    }
