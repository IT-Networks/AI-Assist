from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.schemas import ConfluenceSearchResult, ConfluencePageResponse
from app.core.exceptions import ConfluenceError
from app.services.confluence_client import ConfluenceClient

router = APIRouter(prefix="/api/confluence", tags=["confluence"])


class PageByUrlRequest(BaseModel):
    url: str


def _client() -> ConfluenceClient:
    return ConfluenceClient()


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
