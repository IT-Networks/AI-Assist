"""
Workspace Events - Helper functions for building workspace event payloads.

Provides:
- Code change event data building
- SQL result event data building
- Diff generation utilities
- Language detection from file extensions
"""

import difflib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tools import ToolResult


# File extension to language mapping
EXT_TO_LANGUAGE = {
    ".py": "python", ".java": "java", ".js": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".sql": "sql", ".html": "html", ".css": "css",
    ".json": "json", ".xml": "xml", ".yaml": "yaml",
    ".yml": "yaml", ".md": "markdown", ".sh": "bash",
    ".go": "go", ".rs": "rust", ".cpp": "cpp", ".c": "c",
    ".h": "c", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby",
    ".php": "php", ".swift": "swift", ".kt": "kotlin"
}


def detect_language(file_path: str) -> str:
    """
    Detect programming language from file extension.

    Args:
        file_path: Path to the file

    Returns:
        Language identifier (e.g., "python", "javascript")
    """
    path_obj = Path(file_path)
    return EXT_TO_LANGUAGE.get(path_obj.suffix.lower(), "text")


def generate_diff(
    original_content: str,
    modified_content: str,
    file_path: str,
    is_new: bool = False
) -> str:
    """
    Generate unified diff between original and modified content.

    Args:
        original_content: Original file content
        modified_content: Modified file content
        file_path: Path to the file (for diff header)
        is_new: True if this is a new file

    Returns:
        Unified diff string
    """
    if is_new:
        diff = f"--- /dev/null\n+++ b/{file_path}\n@@ -0,0 +1,{len(modified_content.splitlines())} @@\n"
        for line in modified_content.splitlines():
            diff += f"+{line}\n"
        return diff

    diff_lines = difflib.unified_diff(
        original_content.splitlines(keepends=True),
        modified_content.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm=""
    )
    return "".join(diff_lines)


def build_code_change_event(
    file_path: str,
    original_content: str,
    modified_content: str,
    tool_call: str,
    description: str = "",
    is_new: bool = False
) -> Dict[str, Any]:
    """
    Build workspace code change event payload.

    Args:
        file_path: Path to the file
        original_content: Original file content
        modified_content: Modified file content
        tool_call: Name of the tool (write_file, edit_file, etc.)
        description: Description of the change
        is_new: True if this is a new file

    Returns:
        Event data dictionary matching CodeChange interface
    """
    path_obj = Path(file_path)
    timestamp = int(time.time() * 1000)

    return {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "filePath": str(file_path),
        "fileName": path_obj.name,
        "language": detect_language(file_path),
        "originalContent": original_content,
        "modifiedContent": modified_content,
        "diff": generate_diff(original_content, modified_content, file_path, is_new),
        "toolCall": tool_call,
        "description": description,
        "status": "applied",
        "appliedAt": timestamp,
        "isNew": is_new
    }


def build_sql_result_event(
    query: str,
    database: str,
    schema: Optional[str],
    columns: List[str],
    rows: List[List[Any]],
    row_count: int,
    execution_time_ms: int,
    truncated: bool = False,
    error: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build workspace SQL result event payload.

    Args:
        query: SQL query that was executed
        database: Database name
        schema: Schema name
        columns: List of column names
        rows: List of row data
        row_count: Total row count
        execution_time_ms: Query execution time in milliseconds
        truncated: True if results were truncated
        error: Error message if query failed

    Returns:
        Event data dictionary matching SQLResult interface
    """
    # Build column definitions
    column_defs = []
    for col_name in columns:
        column_defs.append({
            "name": col_name,
            "type": "VARCHAR",  # Basic type info
            "nullable": True,
            "visible": True
        })

    return {
        "id": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "query": query,
        "database": database,
        "schema": schema,
        "columns": column_defs,
        "rows": rows,
        "rowCount": row_count,
        "executionTimeMs": execution_time_ms,
        "toolCall": "query_database",
        "truncated": truncated,
        "error": error
    }


def format_sql_result_for_agent(
    columns: List[str],
    rows: List[List[Any]],
    row_count: int,
    truncated: bool,
    max_rows: int
) -> str:
    """
    Format SQL result for agent response.

    Args:
        columns: Column names
        rows: Row data
        row_count: Total row count
        truncated: True if results were truncated
        max_rows: Maximum rows setting

    Returns:
        Formatted string for agent
    """
    output = "=== Query-Ergebnis ===\n"
    output += f"Zeilen: {row_count}"
    if truncated:
        output += f" (begrenzt auf {max_rows})"
    output += "\n\n"

    if columns and rows:
        output += " | ".join(columns) + "\n"
        output += "-" * (len(" | ".join(columns))) + "\n"
        for row in rows:
            output += " | ".join(str(v) if v is not None else "NULL" for v in row) + "\n"

    return output
