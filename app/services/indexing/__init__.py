"""
Indexing Services - FTS5-basierte Volltextsuche.

Dieses Paket gruppiert alle Index-Services für Code und Dokumente.

Verwendung:
    from app.services.indexing import get_java_indexer, get_python_indexer

    indexer = get_java_indexer()
    results = indexer.search("OrderService", top_k=5)
"""

# Re-exports von existierenden Modulen (Backwards-Kompatibilität)
from app.services.java_indexer import (
    JavaIndexer,
    get_java_indexer,
)

from app.services.python_indexer import (
    PythonIndexer,
    get_python_indexer,
)

from app.services.pdf_indexer import (
    PDFIndexer,
    get_pdf_indexer,
)

from app.services.handbook_indexer import (
    HandbookIndexer,
    get_handbook_indexer,
)

__all__ = [
    # Java
    "JavaIndexer",
    "get_java_indexer",
    # Python
    "PythonIndexer",
    "get_python_indexer",
    # PDF
    "PDFIndexer",
    "get_pdf_indexer",
    # Handbook
    "HandbookIndexer",
    "get_handbook_indexer",
]
