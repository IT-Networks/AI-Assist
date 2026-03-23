"""
Handbook API Routes - Endpunkte für Handbuch-Suche und -Verwaltung.

Optimiert für große Handbücher (100.000+ Dateien):
- SSE-Streaming für Index-Progress
- Abbruch-Möglichkeit
- Polling-Endpoint für Progress
"""

import asyncio
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.handbook_indexer import get_handbook_indexer


def get_settings():
    """Holt immer die aktuellen Settings (nach UI-Änderungen)."""
    from app.core.config import settings
    return settings


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


class CheckpointInfo(BaseModel):
    phase: str
    batch_index: int
    files_scanned: int
    files_processed: int
    created_at: str
    updated_at: str
    handbook_path: str


class HandbookIndexStatus(BaseModel):
    indexed: bool
    indexed_pages: int
    services_count: int
    fields_count: int
    last_build: Optional[str] = None
    handbook_path: Optional[str] = None
    db_size_kb: float
    # Build-Status für Fortsetzung
    build_status: str = "none"  # none, complete, incomplete, cancelled, in_progress
    total_files_expected: int = 0
    files_processed: int = 0
    # Checkpoint für Resume
    has_checkpoint: bool = False
    checkpoint_info: Optional[CheckpointInfo] = None


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
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    stats = indexer.get_stats()
    return HandbookIndexStatus(**stats)


@router.post("/index/build")
async def build_index(
    force: bool = Query(False, description="Alle Dateien neu indexieren"),
    resume: bool = Query(False, description="Unterbrochene Indexierung fortsetzen"),
    stream: bool = Query(True, description="Progress als SSE streamen")
):
    """
    Baut den Handbuch-Index auf oder aktualisiert ihn.

    Bei großen Handbüchern (100.000+ Dateien) wird Progress gestreamt.

    - force=false: Nur geänderte Dateien neu indexieren (schneller)
    - force=true: Alle Dateien neu indexieren
    - resume=true: Setzt unterbrochene Indexierung fort (nutzt Checkpoint)
    - stream=true: Progress als Server-Sent Events streamen
    """
    # Einmalig aktuelle Config holen
    hb_config = get_settings().handbook

    if not hb_config.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    if not hb_config.path:
        raise HTTPException(status_code=400, detail="Kein Handbuch-Pfad konfiguriert")

    handbook_path = Path(hb_config.path)
    if not handbook_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Handbuch-Pfad existiert nicht: {hb_config.path}"
        )

    indexer = get_handbook_indexer()

    # Struktur-Einstellungen aus Config
    structure_mode = getattr(hb_config, 'structure_mode', 'auto')
    known_tab_suffixes = getattr(hb_config, 'known_tab_suffixes', None)
    functions_subdir = hb_config.functions_subdir
    fields_subdir = hb_config.fields_subdir
    exclude_patterns = hb_config.exclude_patterns
    parallel_workers = getattr(hb_config, 'parallel_workers', 8)

    if stream:
        # SSE Streaming Response
        async def progress_generator():
            try:
                # Resume oder normaler Build
                if resume and indexer.has_checkpoint():
                    generator = indexer.resume_build(
                        functions_subdir=functions_subdir,
                        fields_subdir=fields_subdir,
                        exclude_patterns=exclude_patterns,
                        structure_mode=structure_mode,
                        known_tab_suffixes=known_tab_suffixes,
                        parallel_workers=parallel_workers
                    )
                else:
                    generator = indexer.build_with_progress(
                        handbook_path=str(handbook_path),
                        functions_subdir=functions_subdir,
                        fields_subdir=fields_subdir,
                        exclude_patterns=exclude_patterns,
                        force=force,
                        structure_mode=structure_mode,
                        known_tab_suffixes=known_tab_suffixes,
                        parallel_workers=parallel_workers
                    )

                for progress in generator:
                    event_data = json.dumps(progress.to_dict(), ensure_ascii=False)
                    yield f"data: {event_data}\n\n"

                    # Kurze Pause um nicht zu viele Events zu senden
                    await asyncio.sleep(0.05)

            except Exception as e:
                error_data = json.dumps({
                    "phase": "error",
                    "message": str(e)
                })
                yield f"data: {error_data}\n\n"

        return StreamingResponse(
            progress_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # Nginx buffering deaktivieren
            }
        )
    else:
        # Synchroner Aufruf (Legacy)
        result = indexer.build(
            handbook_path=str(handbook_path),
            functions_subdir=functions_subdir,
            fields_subdir=fields_subdir,
            exclude_patterns=exclude_patterns,
            force=force
        )
        return HandbookBuildResult(**result)


@router.get("/index/progress")
async def get_build_progress():
    """
    Gibt den aktuellen Indexierungs-Progress zurück (für Polling).

    Nützlich wenn SSE nicht verwendet wird.
    """
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    progress = indexer.get_current_progress()

    if progress is None:
        return {"status": "idle", "message": "Keine Indexierung aktiv"}

    return progress


@router.post("/index/cancel")
async def cancel_build():
    """
    Bricht die laufende Indexierung ab.

    Der Abbruch erfolgt nach dem aktuellen Batch.
    """
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    indexer.cancel_build()

    return {"message": "Abbruch angefordert", "status": "cancelling"}


@router.delete("/index")
async def delete_index():
    """Löscht den Handbuch-Index."""
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    indexer.clear()
    return {"message": "Handbuch-Index gelöscht"}


@router.delete("/index/checkpoint")
async def delete_checkpoint():
    """Löscht nur den Checkpoint (für Neustart ohne Index zu löschen)."""
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    indexer._clear_checkpoint()
    return {"message": "Checkpoint gelöscht"}


# ══════════════════════════════════════════════════════════════════════════════
# Search
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search", response_model=List[HandbookSearchResult])
async def search_handbook(
    q: str = Query(..., min_length=1, max_length=500, description="Suchbegriff"),
    service: Optional[str] = Query(None, max_length=200, description="Nur in diesem Service suchen"),
    tab: Optional[str] = Query(None, max_length=100, description="Nur in diesem Tab suchen"),
    top_k: int = Query(5, ge=1, le=50, description="Maximale Anzahl Ergebnisse")
):
    """
    Durchsucht das Handbuch nach dem angegebenen Begriff.

    Unterstützt Volltextsuche mit Porter-Stemming (z.B. "Bestellung" findet auch "Bestellungen").
    """
    if not get_settings().handbook.enabled:
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
async def list_services(
    limit: int = Query(100, ge=1, le=1000, description="Max. Anzahl"),
    offset: int = Query(0, ge=0, description="Offset für Pagination")
):
    """Listet alle im Handbuch dokumentierten Services auf (mit Pagination)."""
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()

    if not indexer.is_built():
        raise HTTPException(status_code=400, detail="Handbuch-Index wurde noch nicht aufgebaut")

    services = indexer.list_services(limit=limit, offset=offset)
    return [HandbookServiceSummary(**s) for s in services]


@router.get("/services/{service_id}", response_model=HandbookServiceDetail)
async def get_service(service_id: str):
    """Gibt detaillierte Informationen zu einem Service zurück."""
    if not get_settings().handbook.enabled:
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
    path: str = Query(..., max_length=500, description="Relativer Pfad zur Handbuch-Seite")
):
    """Lädt den Textinhalt einer Handbuch-Seite."""
    if not get_settings().handbook.enabled:
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
    if not get_settings().handbook.enabled:
        raise HTTPException(status_code=404, detail="Handbuch-Feature ist nicht aktiviert")

    indexer = get_handbook_indexer()
    field_info = indexer.get_field_info(field_id)

    if not field_info:
        raise HTTPException(status_code=404, detail=f"Feld '{field_id}' nicht gefunden")

    return field_info
