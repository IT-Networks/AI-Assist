from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import Optional

from app.core.config import settings
from app.core.exceptions import JavaReaderError, PathTraversalError
from app.services.java_reader import JavaReader
from app.services.java_indexer import get_java_indexer
from app.services.pom_parser import PomParser

router = APIRouter(prefix="/api/java", tags=["java"])


def _get_reader() -> JavaReader:
    if not settings.java.get_active_path():
        raise HTTPException(status_code=503, detail="Java-Repository-Pfad nicht konfiguriert (java.repo_path in config.yaml)")
    return JavaReader(settings.java.get_active_path())


@router.get("/tree")
async def get_file_tree():
    """Return the nested directory/file tree of the Java repository."""
    try:
        reader = _get_reader()
        return reader.get_file_tree()
    except JavaReaderError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/file")
async def get_file(path: str = Query(..., description="Relativer Pfad zur Datei im Repo")):
    """Return the raw content of a Java source file."""
    try:
        reader = _get_reader()
        content = reader.read_file(path)
        return {"path": path, "content": content}
    except PathTraversalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except JavaReaderError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/summary")
async def get_file_summary(path: str = Query(..., description="Relativer Pfad zur .java Datei")):
    """Return an AST-based summary (package, class, method signatures) of a Java file."""
    try:
        reader = _get_reader()
        return reader.summarize_file(path)
    except PathTraversalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except JavaReaderError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/search")
async def search_class(q: str = Query(..., description="Klassen- oder Interface-Name")):
    """Find all Java files containing a class/interface matching the query."""
    try:
        reader = _get_reader()
        matches = reader.search_class(q)
        return {"query": q, "matches": matches, "count": len(matches)}
    except JavaReaderError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pom")
async def get_pom_info():
    """List all POM files in the repo with parsed dependency information."""
    try:
        reader = _get_reader()
        pom_files = reader.get_pom_files()
        if not pom_files:
            return {"poms": []}

        parser = PomParser()
        results = []
        for pom_path in pom_files:
            try:
                pom_data = parser.parse(pom_path)
                results.append(pom_data)
            except Exception as e:
                results.append({"path": pom_path, "error": str(e)})
        return {"poms": results}
    except JavaReaderError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Index-Endpunkte ──────────────────────────────────────────────────────────

def _run_index_build(force: bool) -> dict:
    reader = _get_reader()
    indexer = get_java_indexer()
    return indexer.build(settings.java.get_active_path(), reader, force=force)


@router.post("/index/build")
async def build_index(
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="true = alle Dateien neu indexieren"),
    background: bool = Query(False, description="true = asynchron im Hintergrund starten"),
):
    """
    Baut den FTS5-Suchindex für das Java-Repository auf.
    Nur geänderte Dateien werden neu indexiert (inkrementell).
    """
    if not settings.java.get_active_path():
        raise HTTPException(status_code=503, detail="Java-Repository-Pfad nicht konfiguriert")

    if background:
        background_tasks.add_task(_run_index_build, force)
        return {"message": "Index-Build im Hintergrund gestartet"}

    try:
        result = _run_index_build(force)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Index-Build fehlgeschlagen: {e}")


@router.get("/index/status")
async def get_index_status():
    """Gibt den aktuellen Status des Java-Suchindex zurück."""
    indexer = get_java_indexer()
    return indexer.get_stats()


@router.get("/index/search")
async def search_index(
    q: str = Query(..., description="Volltext-Suchbegriff"),
    top_k: int = Query(None, ge=1, le=20, description="Anzahl Treffer"),
):
    """Sucht im FTS5-Index nach relevanten Java-Dateien."""
    indexer = get_java_indexer()
    if not indexer.is_built():
        raise HTTPException(
            status_code=404,
            detail="Kein Index vorhanden. Bitte zuerst POST /api/java/index/build aufrufen.",
        )
    k = top_k or settings.index.max_search_results
    results = indexer.search(q, top_k=k)
    return {"query": q, "results": results, "count": len(results)}


@router.delete("/index")
async def delete_index():
    """Löscht den Java-Suchindex vollständig."""
    indexer = get_java_indexer()
    indexer.clear()
    return {"message": "Index gelöscht"}
