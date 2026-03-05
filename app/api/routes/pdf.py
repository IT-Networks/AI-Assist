import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from typing import Optional

from app.core.config import settings
from app.api.schemas import UploadResponse, PDFInfoResponse
from app.services.pdf_reader import PDFReader
from app.services.pdf_indexer import get_pdf_indexer
from app.core.exceptions import PDFReadError

router = APIRouter(prefix="/api/pdf", tags=["pdf"])

# In-memory store: pdf_id -> {filename, filepath, page_count, text}
_pdf_store: dict = {}
_reader = PDFReader()


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF file. Returns a pdf_id for subsequent text extraction."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien erlaubt")

    max_bytes = settings.uploads.max_file_size_mb * 1024 * 1024
    content_bytes = await file.read()

    if len(content_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Datei zu groß (max {settings.uploads.max_file_size_mb}MB)")

    pdf_id = str(uuid.uuid4())[:8]
    upload_dir = Path(settings.uploads.directory)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filepath = upload_dir / f"{pdf_id}_{file.filename}"

    filepath.write_bytes(content_bytes)

    try:
        meta = _reader.get_metadata(str(filepath))
        # Pre-extract full text (up to 50 pages) for context
        text = _reader.extract_text(str(filepath), max_pages=50)
    except PDFReadError as e:
        filepath.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))

    _pdf_store[pdf_id] = {
        "filename": file.filename,
        "filepath": str(filepath),
        "page_count": meta["page_count"],
        "text": text,
        "meta": meta,
    }

    # Index für smarte Kontext-Suche aufbauen
    try:
        indexer = get_pdf_indexer()
        indexed_pages = indexer.index_pdf(pdf_id, text)
    except Exception:
        indexed_pages = 0

    return UploadResponse(
        id=pdf_id,
        filename=file.filename,
        size_bytes=len(content_bytes),
        message=f"PDF geladen: {meta['page_count']} Seiten, {indexed_pages} Seiten indexiert",
    )


@router.get("/{pdf_id}/info", response_model=PDFInfoResponse)
async def get_pdf_info(pdf_id: str):
    if pdf_id not in _pdf_store:
        raise HTTPException(status_code=404, detail=f"PDF-ID nicht gefunden: {pdf_id}")
    d = _pdf_store[pdf_id]
    return PDFInfoResponse(
        pdf_id=pdf_id,
        filename=d["filename"],
        page_count=d["page_count"],
        char_count=len(d["text"]),
    )


@router.get("/{pdf_id}/text")
async def get_pdf_text(
    pdf_id: str,
    page_start: Optional[int] = Query(None, ge=1),
    page_end: Optional[int] = Query(None, ge=1),
):
    """Return extracted text from the uploaded PDF (optionally limited to a page range)."""
    if pdf_id not in _pdf_store:
        raise HTTPException(status_code=404, detail=f"PDF-ID nicht gefunden: {pdf_id}")

    d = _pdf_store[pdf_id]

    if page_start or page_end:
        start = page_start or 1
        end = page_end or d["page_count"]
        try:
            text = _reader.extract_pages(d["filepath"], start, end)
        except PDFReadError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        text = d["text"]

    return {"pdf_id": pdf_id, "filename": d["filename"], "text": text}


@router.delete("/{pdf_id}")
async def delete_pdf(pdf_id: str):
    if pdf_id not in _pdf_store:
        raise HTTPException(status_code=404, detail=f"PDF-ID nicht gefunden: {pdf_id}")
    d = _pdf_store.pop(pdf_id)
    Path(d["filepath"]).unlink(missing_ok=True)
    try:
        get_pdf_indexer().remove_pdf(pdf_id)
    except Exception:
        pass
    return {"message": f"PDF {pdf_id} gelöscht"}
