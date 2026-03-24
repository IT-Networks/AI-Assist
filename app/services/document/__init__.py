"""
Document Services - Dokument-Verarbeitung.

Dieses Paket gruppiert Services für Dokument-Handling:
- PdfReader
- LogParser
- OutputFormatter

Verwendung:
    from app.services.document import PdfReader

    reader = PdfReader()
    text = reader.extract_text("/path/to/doc.pdf")
"""

from app.services.pdf_reader import (
    PDFReader,
)

from app.services.log_parser import (
    WLPLogParser,
    LogEntry,
    ParsedLog,
)

from app.services.output_formatter import (
    OutputFormatter,
    get_output_formatter,
)

__all__ = [
    # PDF
    "PDFReader",
    # Logs
    "WLPLogParser",
    "LogEntry",
    "ParsedLog",
    # Formatting
    "OutputFormatter",
    "get_output_formatter",
]
