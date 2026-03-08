import asyncio
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel

from app.core.config import settings
from app.api.schemas import UploadResponse, PDFInfoResponse
from app.services.pdf_reader import PDFReader
from app.services.pdf_indexer import get_pdf_indexer
from app.core.exceptions import PDFReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pdf", tags=["pdf"])

# LRU-Cache mit TTL: max 100 PDFs, 2 Stunden TTL (verhindert Memory-Leak)
_pdf_store: TTLCache = TTLCache(maxsize=100, ttl=7200)
_reader = PDFReader()


class PDFListItem(BaseModel):
    id: str
    filename: str
    page_count: int
    char_count: int
    indexed: bool


class ScanResult(BaseModel):
    scanned: int
    loaded: int
    errors: int
    pdfs: List[PDFListItem]


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

    # Async File I/O: Blockierendes Schreiben in ThreadPool auslagern
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, filepath.write_bytes, content_bytes)

    try:
        # PDF-Parsing ist CPU-intensiv → in ThreadPool auslagern
        filepath_str = str(filepath)
        meta = await loop.run_in_executor(None, _reader.get_metadata, filepath_str)
        # Pre-extract full text (up to 50 pages) for context
        text = await loop.run_in_executor(
            None, lambda: _reader.extract_text(filepath_str, max_pages=50)
        )
    except PDFReadError as e:
        await loop.run_in_executor(None, lambda: filepath.unlink(missing_ok=True))
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


@router.get("", response_model=List[PDFListItem])
async def list_pdfs():
    """Listet alle geladenen PDFs auf."""
    indexer = get_pdf_indexer()
    result = []
    for pdf_id, data in _pdf_store.items():
        result.append(PDFListItem(
            id=pdf_id,
            filename=data.get("filename", ""),
            page_count=data.get("page_count", 0),
            char_count=len(data.get("text", "")),
            indexed=indexer.has_pdf(pdf_id) if indexer else False
        ))
    return result


@router.post("/scan", response_model=ScanResult)
async def scan_uploads():
    """
    Scannt den Upload-Ordner nach PDFs und lädt diese in den Store.
    Nützlich nach Server-Neustart um vorhandene PDFs wieder verfügbar zu machen.
    """
    upload_dir = Path(settings.uploads.directory)
    if not upload_dir.exists():
        return ScanResult(scanned=0, loaded=0, errors=0, pdfs=[])

    scanned = 0
    loaded = 0
    errors = 0
    indexer = get_pdf_indexer()

    for pdf_file in upload_dir.glob("*.pdf"):
        scanned += 1

        # ID aus Dateiname extrahieren (Format: {id}_{originalname}.pdf)
        filename = pdf_file.name
        if "_" in filename:
            pdf_id = filename.split("_")[0]
            original_name = "_".join(filename.split("_")[1:])
        else:
            pdf_id = filename[:8]
            original_name = filename

        # Bereits geladen?
        if pdf_id in _pdf_store:
            continue

        try:
            meta = _reader.get_metadata(str(pdf_file))
            text = _reader.extract_text(str(pdf_file), max_pages=50)

            _pdf_store[pdf_id] = {
                "filename": original_name,
                "filepath": str(pdf_file),
                "page_count": meta["page_count"],
                "text": text,
                "meta": meta,
            }

            # Index aufbauen
            try:
                indexer.index_pdf(pdf_id, text)
            except Exception as e:
                logger.debug(f"PDF-Indexierung fehlgeschlagen ({pdf_id}): {e}")

            loaded += 1
            logger.info(f"PDF geladen: {pdf_id} ({original_name})")

        except Exception as e:
            errors += 1
            logger.warning(f"PDF konnte nicht geladen werden ({filename}): {e}")

    # Ergebnis-Liste erstellen
    pdfs = []
    for pdf_id, data in _pdf_store.items():
        pdfs.append(PDFListItem(
            id=pdf_id,
            filename=data.get("filename", ""),
            page_count=data.get("page_count", 0),
            char_count=len(data.get("text", "")),
            indexed=indexer.has_pdf(pdf_id) if indexer else False
        ))

    return ScanResult(scanned=scanned, loaded=loaded, errors=errors, pdfs=pdfs)
