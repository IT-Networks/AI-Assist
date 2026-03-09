from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.schemas import ConfluenceSearchResult, ConfluencePageResponse
from app.core.config import settings
from app.core.exceptions import ConfluenceError
from app.services.confluence_client import ConfluenceClient

router = APIRouter(prefix="/api/confluence", tags=["confluence"])


class PageByUrlRequest(BaseModel):
    url: str


def _client() -> ConfluenceClient:
    return ConfluenceClient()


@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """
    Testet die Confluence-Verbindung und erkennt den korrekten API-Pfad.
    """
    if not settings.confluence.base_url:
        return {"success": False, "error": "Base URL nicht konfiguriert"}

    try:
        client = _client()
        # Erkennt automatisch den API-Pfad
        api_path = await client._detect_api_path()

        # Test-Suche durchführen
        results = await client.search(query="test", limit=1)

        return {
            "success": True,
            "message": f"Verbindung erfolgreich",
            "api_path": api_path or "(root)",
            "detected_url": client._api_url("/content"),
            "test_search_works": True,
        }
    except ConfluenceError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Unerwarteter Fehler: {e}"}


@router.get("/search", response_model=List[ConfluenceSearchResult])
async def search_confluence(
    q: str = Query(..., description="Suchbegriff (Volltext)"),
    space: Optional[str] = Query(None, description="Space-Key (z.B. DEV)"),
    type: str = Query("page", description="page oder blogpost"),
    limit: int = Query(20, ge=1, le=50),
    ancestor_id: Optional[str] = Query(None, description="Nur Unterseiten dieser Seiten-ID"),
    labels: Optional[str] = Query(None, description="Kommagetrennte Labels (z.B. backend,api)"),
):
    """Full-text search in Confluence using CQL."""
    label_list = [l.strip() for l in labels.split(",")] if labels else None
    try:
        client = _client()
        results = await client.search(
            query=q,
            space_key=space,
            content_type=type,
            limit=limit,
            ancestor_id=ancestor_id,
            labels=label_list,
        )
        return [ConfluenceSearchResult(**r) for r in results]
    except ConfluenceError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/page/{page_id}", response_model=ConfluencePageResponse)
async def get_page(page_id: str):
    """Fetch and extract text from a Confluence page by its ID."""
    try:
        client = _client()
        page = await client.get_page_by_id(page_id)
        return ConfluencePageResponse(**page)
    except ConfluenceError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/page-by-url", response_model=ConfluencePageResponse)
async def get_page_by_url(request: PageByUrlRequest):
    """Fetch a Confluence page by its full URL."""
    try:
        client = _client()
        page = await client.get_page_by_url(request.url)
        return ConfluencePageResponse(**page)
    except ConfluenceError as e:
        raise HTTPException(status_code=502, detail=str(e))
