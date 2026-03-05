from pathlib import Path
from typing import Optional

import pdfplumber

from app.core.exceptions import PDFReadError


class PDFReader:
    def extract_text(self, file_path: str, max_pages: Optional[int] = None) -> str:
        """Extract text from all (or first max_pages) pages of a PDF."""
        path = Path(file_path)
        if not path.exists():
            raise PDFReadError(f"PDF nicht gefunden: {file_path}")

        parts = []
        try:
            with pdfplumber.open(path) as pdf:
                pages = pdf.pages
                if max_pages:
                    pages = pages[:max_pages]
                for i, page in enumerate(pages, start=1):
                    text = page.extract_text()
                    if text and text.strip():
                        parts.append(f"--- Seite {i} ---\n{text.strip()}")
                    else:
                        parts.append(f"--- Seite {i} --- [Kein extrahierbarer Text]")
        except Exception as e:
            raise PDFReadError(f"PDF-Extraktion fehlgeschlagen: {e}")

        return "\n\n".join(parts)

    def extract_pages(self, file_path: str, start: int, end: int) -> str:
        """Extract text from a page range (1-indexed, inclusive)."""
        path = Path(file_path)
        if not path.exists():
            raise PDFReadError(f"PDF nicht gefunden: {file_path}")

        parts = []
        try:
            with pdfplumber.open(path) as pdf:
                for i in range(start - 1, min(end, len(pdf.pages))):
                    page = pdf.pages[i]
                    text = page.extract_text()
                    if text and text.strip():
                        parts.append(f"--- Seite {i + 1} ---\n{text.strip()}")
                    else:
                        parts.append(f"--- Seite {i + 1} --- [Kein extrahierbarer Text]")
        except Exception as e:
            raise PDFReadError(f"PDF-Extraktion fehlgeschlagen: {e}")

        return "\n\n".join(parts)

    def get_page_count(self, file_path: str) -> int:
        path = Path(file_path)
        if not path.exists():
            raise PDFReadError(f"PDF nicht gefunden: {file_path}")
        try:
            with pdfplumber.open(path) as pdf:
                return len(pdf.pages)
        except Exception as e:
            raise PDFReadError(f"PDF konnte nicht geöffnet werden: {e}")

    def get_metadata(self, file_path: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            raise PDFReadError(f"PDF nicht gefunden: {file_path}")
        try:
            with pdfplumber.open(path) as pdf:
                meta = pdf.metadata or {}
                return {
                    "page_count": len(pdf.pages),
                    "title": meta.get("Title", ""),
                    "author": meta.get("Author", ""),
                    "subject": meta.get("Subject", ""),
                    "creator": meta.get("Creator", ""),
                }
        except Exception as e:
            raise PDFReadError(f"PDF-Metadaten konnten nicht gelesen werden: {e}")
