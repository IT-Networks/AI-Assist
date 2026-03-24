"""
Code Services - Code-Analyse und -Lesen.

Dieses Paket gruppiert Services für Code-Verarbeitung:
- JavaReader / PythonReader
- CodeSearch (ripgrep-basiert)
- PomParser (Maven)
- FileManager

Verwendung:
    from app.services.code import JavaReader

    reader = JavaReader("/path/to/repo")
    content = reader.read_file("src/Main.java")
"""

from app.services.java_reader import (
    JavaReader,
)

from app.services.python_reader import (
    PythonReader,
)

from app.services.code_search import (
    CodeSearchEngine,
    get_code_search_engine,
)

from app.services.pom_parser import (
    PomParser,
)

from app.services.file_manager import (
    FileManager,
    get_file_manager,
)

__all__ = [
    # Readers
    "JavaReader",
    "PythonReader",
    # Search
    "CodeSearchEngine",
    "get_code_search_engine",
    # Parsers
    "PomParser",
    # File Management
    "FileManager",
    "get_file_manager",
]
