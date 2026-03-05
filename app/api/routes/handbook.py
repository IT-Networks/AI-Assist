"""
Handbook API Routes - Endpunkte für Handbuch-Suche und -Verwaltung.
"""

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from app.core.config import settings
from app.services.handbook_indexer import get_handbook_indexer


router = APIRouter(prefix="/api/handbook", tags=["handbook"])


# ══════════════════════════════════════════════════════════════════════════════
# Response Models
# ══════════════════════════════════════════════════════════════════════════════

class HandbookSearchResult(BaseModel):
    file_path: str
    service_name: str
    tab_name: str
    title: str
    snippet: str
    rank: float


class HandbookServiceSummary(BaseModel):
    service_id: str
    service_name: str
    description: Optional[str] = None


class HandbookServiceDetail(BaseModel):
    service_id: str
    service_name: str
    description: Optional[str] = None
    tabs: List[dict] = []
    input_fields: List[dict] = []
    output_fields: List[dict] = []
    call_variants: List[dict] = []


class HandbookIndexStatus(BaseModel):
    is_built: bool
    indexed_pages: int
    services: int
    fields: int
    last_build: Optional[str] = None
    handbook_path: Optional[str] = None
    db_size_kb: float


class HandbookBuildResult(BaseModel):
    indexed: int
    skipped: int
    services: int
    fields: int
    errors: int
    duration_s: float


class HandbookPageContent(BaseModel):
    file_path: str
    content: str


# ══════════════════════════════════════════════════════════════════════════════
# Index Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/status", response_model=HandbookIndexStatus)
async def get_index_status():
    """Gibt den Status des Handbuch-Index zurück."""
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    stats = indexer.get_stats()
    return HandbookIndexStatus(**stats)


@router.post("/index/build", response_model=HandbookBuildResult)
async def build_index(
    force: bool = Query(False, description="Alle Dateien neu indexieren"),
    background: bool = Query(False, description="Im Hintergrund ausführen"),
    background_tasks: BackgroundTasks = None
):
    """
    Baut den Handbuch-Index auf oder aktualisiert ihn.

    - force=false: Nur geänderte Dateien neu indexieren (schneller)
    - force=true: Alle Dateien neu indexieren
    - background=true: Im Hintergrund ausführen, sofort zurückkehren
    """
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    if not settings.handbook.path:
        raise HTTPException(status_code=400, detail="Kein Handbuch-Pfad konfiguriert")

    handbook_path = Path(settings.handbook.path)
    if not handbook_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Handbuch-Pfad existiert nicht: {settings.handbook.path}"
        )

    indexer = get_handbook_indexer()

    def do_build():
        return indexer.build(
            handbook_path=str(handbook_path),
            functions_subdir=settings.handbook.functions_subdir,
            fields_subdir=settings.handbook.fields_subdir,
            exclude_patterns=settings.handbook.exclude_patterns,
            force=force
        )

    if background and background_tasks:
        background_tasks.add_task(do_build)
        return HandbookBuildResult(
            indexed=0, skipped=0, services=0, fields=0, errors=0, duration_s=0
        )

    result = do_build()
    return HandbookBuildResult(**result)


@router.delete("/index")
async def delete_index():
    """Löscht den Handbuch-Index."""
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    indexer.clear()
    return {"message": "Handbuch-Index gelöscht"}


# ══════════════════════════════════════════════════════════════════════════════
# Search
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search", response_model=List[HandbookSearchResult])
async def search_handbook(
    q: str = Query(..., min_length=1, description="Suchbegriff"),
    service: Optional[str] = Query(None, description="Nur in diesem Service suchen"),
    tab: Optional[str] = Query(None, description="Nur in diesem Tab suchen"),
    top_k: int = Query(5, ge=1, le=50, description="Maximale Anzahl Ergebnisse")
):
    """
    Durchsucht das Handbuch nach dem angegebenen Begriff.

    Unterstützt Volltextsuche mit Porter-Stemming (z.B. "Bestellung" findet auch "Bestellungen").
    """
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()

    if not indexer.is_built():
        raise HTTPException(
            status_code=400,
            detail="Handbuch-Index wurde noch nicht aufgebaut. Bitte erst POST /api/handbook/index/build aufrufen."
        )

    results = indexer.search(
        query=q,
        service_filter=service,
        tab_filter=tab,
        top_k=top_k
    )

    return [HandbookSearchResult(**r) for r in results]


# ══════════════════════════════════════════════════════════════════════════════
# Services
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/services", response_model=List[HandbookServiceSummary])
async def list_services():
    """Listet alle im Handbuch dokumentierten Services auf."""
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()

    if not indexer.is_built():
        raise HTTPException(status_code=400, detail="Handbuch-Index wurde noch nicht aufgebaut")

    services = indexer.list_services()
    return [HandbookServiceSummary(**s) for s in services]


@router.get("/services/{service_id}", response_model=HandbookServiceDetail)
async def get_service(service_id: str):
    """Gibt detaillierte Informationen zu einem Service zurück."""
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    service = indexer.get_service_info(service_id)

    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    return HandbookServiceDetail(**service)


# ══════════════════════════════════════════════════════════════════════════════
# Pages
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/page", response_model=HandbookPageContent)
async def get_page_content(
    path: str = Query(..., description="Relativer Pfad zur Handbuch-Seite")
):
    """Lädt den Textinhalt einer Handbuch-Seite."""
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    content = indexer.get_page_content(path)

    if content is None:
        raise HTTPException(status_code=404, detail=f"Seite '{path}' nicht gefunden")

    return HandbookPageContent(file_path=path, content=content)


# ══════════════════════════════════════════════════════════════════════════════
# Fields
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/fields/{field_id}")
async def get_field(field_id: str):
    """Gibt Informationen zu einem Feld zurück."""
    if not settings.handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    field_info = indexer.get_field_info(field_id)

    if not field_info:
        raise HTTPException(status_code=404, detail=f"Feld '{field_id}' nicht gefunden")

    return field_info
