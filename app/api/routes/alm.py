"""
HP ALM/Quality Center API Routes.

Routes:
  GET    /api/alm/status            - Prueft ob ALM konfiguriert und aktiviert ist
  POST   /api/alm/test-connection   - Testet die Verbindung zu ALM
  GET    /api/alm/folders           - Listet Test-Plan Folder auf
  GET    /api/alm/tests             - Sucht Testfaelle
  GET    /api/alm/tests/{test_id}   - Laedt einen Testfall mit Details
  GET    /api/alm/test-sets         - Listet Test-Sets auf
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import ALMError

router = APIRouter(prefix="/api/alm", tags=["alm"])


class ALMStatusResponse(BaseModel):
    """Response fuer ALM Status."""
    enabled: bool
    configured: bool
    base_url: str = ""
    domain: str = ""
    project: str = ""


class ALMConnectionTestResponse(BaseModel):
    """Response fuer Verbindungstest."""
    success: bool
    user: str = ""
    domain: str = ""
    project: str = ""
    error: str = ""


class ALMFolderResponse(BaseModel):
    """Response fuer einen Folder."""
    id: int
    name: str
    parent_id: int
    path: str = ""


class ALMTestResponse(BaseModel):
    """Response fuer einen Testfall."""
    id: int
    name: str
    description: str = ""
    folder_id: int = 0
    folder_path: str = ""
    test_type: str = "MANUAL"
    status: str = ""
    owner: str = ""
    steps: List[Dict[str, Any]] = []


class ALMTestSetResponse(BaseModel):
    """Response fuer ein Test-Set."""
    id: int
    name: str
    folder_id: int = 0
    status: str = ""
    description: str = ""
    user_01: str = "Nur Intern"  # Custom Field: Anzeige (Extern, Nur Intern, Sparkasse)


@router.get("/status", response_model=ALMStatusResponse)
async def get_alm_status() -> ALMStatusResponse:
    """
    Prueft ob ALM konfiguriert und aktiviert ist.

    Returns:
        Status-Informationen zu ALM
    """
    configured = bool(
        settings.alm.base_url and
        settings.alm.domain and
        settings.alm.project
    )

    return ALMStatusResponse(
        enabled=settings.alm.enabled,
        configured=configured,
        base_url=settings.alm.base_url if configured else "",
        domain=settings.alm.domain if configured else "",
        project=settings.alm.project if configured else "",
    )


@router.post("/test-connection", response_model=ALMConnectionTestResponse)
async def test_connection() -> ALMConnectionTestResponse:
    """
    Testet die Verbindung zu HP ALM.

    Prueft:
    - Credentials sind korrekt
    - Session kann erstellt werden
    - Domain/Project sind erreichbar

    Returns:
        Verbindungsstatus und User-Info
    """
    if not settings.alm.enabled:
        return ALMConnectionTestResponse(
            success=False,
            error="ALM ist nicht aktiviert (alm.enabled=false)"
        )

    if not settings.alm.base_url:
        return ALMConnectionTestResponse(
            success=False,
            error="ALM Base URL nicht konfiguriert"
        )

    from app.services.alm_client import get_alm_client

    try:
        client = get_alm_client()
        result = await client.test_connection()

        if result["success"]:
            return ALMConnectionTestResponse(
                success=True,
                user=result.get("user", ""),
                domain=result.get("domain", ""),
                project=result.get("project", ""),
            )
        else:
            return ALMConnectionTestResponse(
                success=False,
                error=result.get("error", "Unbekannter Fehler"),
            )

    except Exception as e:
        return ALMConnectionTestResponse(
            success=False,
            error=str(e),
        )


@router.get("/folders", response_model=List[ALMFolderResponse])
async def list_folders(
    parent_id: int = Query(0, description="Parent-Folder-ID (0 = Root)")
) -> List[ALMFolderResponse]:
    """
    Listet Test-Plan Folder auf.

    Args:
        parent_id: Parent-Folder-ID (0 fuer Root-Folder)

    Returns:
        Liste von Folders
    """
    if not settings.alm.enabled:
        raise HTTPException(status_code=400, detail="ALM ist nicht aktiviert")

    from app.services.alm_client import get_alm_client

    try:
        client = get_alm_client()
        folders = await client.list_folders(parent_id)

        return [
            ALMFolderResponse(
                id=f.id,
                name=f.name,
                parent_id=f.parent_id,
                path=f.path,
            )
            for f in folders
        ]

    except ALMError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tests", response_model=List[ALMTestResponse])
async def search_tests(
    query: str = Query("", description="Suchbegriff im Testfall-Namen"),
    folder_id: Optional[int] = Query(None, description="Nur in diesem Folder suchen"),
    limit: int = Query(50, description="Max. Anzahl Ergebnisse"),
) -> List[ALMTestResponse]:
    """
    Sucht Testfaelle im Test Plan.

    Args:
        query: Suchbegriff
        folder_id: Optional Folder-Filter
        limit: Max. Ergebnisse

    Returns:
        Liste von Testfaellen (ohne Steps)
    """
    if not settings.alm.enabled:
        raise HTTPException(status_code=400, detail="ALM ist nicht aktiviert")

    from app.services.alm_client import get_alm_client

    try:
        client = get_alm_client()
        tests = await client.search_tests(query=query, folder_id=folder_id, limit=limit)

        return [
            ALMTestResponse(
                id=t.id,
                name=t.name,
                description=t.description,
                folder_id=t.folder_id,
                folder_path=t.folder_path,
                test_type=t.test_type,
                status=t.status,
                owner=t.owner,
            )
            for t in tests
        ]

    except ALMError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tests/{test_id}", response_model=ALMTestResponse)
async def get_test(
    test_id: int,
    include_steps: bool = Query(True, description="Test-Schritte laden"),
) -> ALMTestResponse:
    """
    Laedt einen Testfall mit Details.

    Args:
        test_id: Test-ID
        include_steps: Auch Design-Steps laden

    Returns:
        Testfall mit optionalen Steps
    """
    if not settings.alm.enabled:
        raise HTTPException(status_code=400, detail="ALM ist nicht aktiviert")

    from app.services.alm_client import get_alm_client

    try:
        client = get_alm_client()
        test = await client.get_test(test_id, include_steps=include_steps)

        return ALMTestResponse(
            id=test.id,
            name=test.name,
            description=test.description,
            folder_id=test.folder_id,
            folder_path=test.folder_path,
            test_type=test.test_type,
            status=test.status,
            owner=test.owner,
            steps=[
                {
                    "id": s.id,
                    "order": s.step_order,
                    "name": s.name,
                    "description": s.description,
                    "expected_result": s.expected_result,
                }
                for s in test.steps
            ],
        )

    except ALMError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/test-sets", response_model=List[ALMTestSetResponse])
async def list_test_sets(
    folder_id: Optional[int] = Query(None, description="Nur in diesem Test Lab Folder"),
) -> List[ALMTestSetResponse]:
    """
    Listet Test-Sets aus dem Test Lab auf.

    Args:
        folder_id: Optional Folder-Filter

    Returns:
        Liste von Test-Sets
    """
    if not settings.alm.enabled:
        raise HTTPException(status_code=400, detail="ALM ist nicht aktiviert")

    from app.services.alm_client import get_alm_client

    try:
        client = get_alm_client()
        test_sets = await client.list_test_sets(folder_id)

        return [
            ALMTestSetResponse(
                id=ts.id,
                name=ts.name,
                folder_id=ts.folder_id,
                status=ts.status,
                description=ts.description,
                user_01=ts.user_01,
            )
            for ts in test_sets
        ]

    except ALMError as e:
        raise HTTPException(status_code=400, detail=str(e))
