"""
File Search API - Unified file search across all repositories.

Routes:
  GET  /api/files/search    - Fuzzy search for files by name
  GET  /api/files/list      - List all indexed files (for caching)
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.core.config import settings

router = APIRouter(prefix="/api/files", tags=["files"])

# ── File Cache ────────────────────────────────────────────────────────────────

_file_cache: Dict[str, Dict[str, Any]] = {
    "java": {"files": [], "timestamp": 0},
    "python": {"files": [], "timestamp": 0},
}
_CACHE_TTL = 5 * 60  # 5 minutes in seconds


def _is_cache_valid(repo_type: str) -> bool:
    """Check if cache is still valid."""
    cache = _file_cache.get(repo_type)
    if not cache or not cache["files"]:
        return False
    age = datetime.now().timestamp() - cache["timestamp"]
    return age < _CACHE_TTL


def _refresh_cache(repo_type: str) -> List[Dict[str, Any]]:
    """Refresh file cache for a repository type."""
    files = []

    if repo_type == "java":
        repo_path = settings.java.get_active_path() if hasattr(settings.java, 'get_active_path') else None
        if repo_path and os.path.isdir(repo_path):
            files = _scan_directory(repo_path, "java", [".java", ".xml", ".properties", ".yaml", ".yml"])

    elif repo_type == "python":
        repo_path = settings.python.get_active_path() if hasattr(settings.python, 'get_active_path') else None
        if repo_path and os.path.isdir(repo_path):
            files = _scan_directory(repo_path, "python", [".py", ".yaml", ".yml", ".json", ".toml"])

    _file_cache[repo_type] = {
        "files": files,
        "timestamp": datetime.now().timestamp(),
    }

    return files


def _scan_directory(
    base_path: str,
    repo_type: str,
    extensions: List[str],
    max_files: int = 10000
) -> List[Dict[str, Any]]:
    """Scan directory for files with given extensions."""
    files = []
    base = Path(base_path)

    # Directories to skip
    skip_dirs = {
        ".git", ".svn", "node_modules", "__pycache__", ".pytest_cache",
        "target", "build", "dist", ".idea", ".vscode", "venv", ".venv",
        "env", ".env", "htmlcov", ".tox", ".mypy_cache"
    }

    try:
        for root, dirs, filenames in os.walk(base):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]

            for filename in filenames:
                if len(files) >= max_files:
                    return files

                # Check extension
                if not any(filename.endswith(ext) for ext in extensions):
                    continue

                filepath = Path(root) / filename
                rel_path = filepath.relative_to(base)

                files.append({
                    "name": filename,
                    "path": str(rel_path).replace("\\", "/"),
                    "type": repo_type,
                    "repo": _get_repo_name(repo_type),
                })
    except Exception as e:
        print(f"[files] Error scanning {base_path}: {e}")

    return files


def _get_repo_name(repo_type: str) -> str:
    """Get human-readable repo name."""
    if repo_type == "java":
        path = settings.java.get_active_path() if hasattr(settings.java, 'get_active_path') else ""
        return Path(path).name if path else "Java"
    elif repo_type == "python":
        path = settings.python.get_active_path() if hasattr(settings.python, 'get_active_path') else ""
        return Path(path).name if path else "Python"
    return repo_type


def _get_cached_files(repo_type: str) -> List[Dict[str, Any]]:
    """Get files from cache, refreshing if necessary."""
    if not _is_cache_valid(repo_type):
        return _refresh_cache(repo_type)
    return _file_cache[repo_type]["files"]


def _fuzzy_match(filename: str, query: str) -> float:
    """
    Calculate fuzzy match score between filename and query.
    Returns score between 0.0 (no match) and 1.0 (perfect match).
    """
    if not query:
        return 0.0

    name_lower = filename.lower()
    query_lower = query.lower()

    # Exact match
    if name_lower == query_lower:
        return 1.0

    # Starts with query (high priority)
    if name_lower.startswith(query_lower):
        return 0.95 - (len(query_lower) / len(name_lower)) * 0.05

    # Contains query as substring
    if query_lower in name_lower:
        # Earlier position = higher score
        pos = name_lower.index(query_lower)
        return 0.85 - (pos / len(name_lower)) * 0.1

    # Character-by-character fuzzy match (for typos)
    qi = 0
    matched_chars = 0
    consecutive_bonus = 0
    last_match_pos = -2

    for i, char in enumerate(name_lower):
        if qi < len(query_lower) and char == query_lower[qi]:
            matched_chars += 1
            # Bonus for consecutive matches
            if i == last_match_pos + 1:
                consecutive_bonus += 0.1
            last_match_pos = i
            qi += 1

    if qi == len(query_lower):
        # All query chars found in order
        base_score = matched_chars / len(name_lower)
        return min(0.7, base_score + consecutive_bonus)

    return 0.0


# ── API Routes ────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_files(
    q: str = Query(..., min_length=1, description="Search query"),
    repo: str = Query("all", description="Filter by repo type: java, python, all"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
) -> Dict[str, Any]:
    """
    Fuzzy search for files by name across repositories.

    Returns files sorted by match score (best matches first).
    """
    results = []

    # Determine which repos to search
    repo_types = []
    if repo in ("all", "java"):
        repo_types.append("java")
    if repo in ("all", "python"):
        repo_types.append("python")

    # Search each repo
    for repo_type in repo_types:
        files = _get_cached_files(repo_type)

        for file in files:
            score = _fuzzy_match(file["name"], q)
            if score > 0.1:  # Minimum threshold
                results.append({
                    **file,
                    "score": round(score, 3),
                })

    # Sort by score (descending) and limit
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    return {
        "results": results,
        "total": len(results),
        "query": q,
        "repo_filter": repo,
    }


@router.get("/list")
async def list_files(
    repo: str = Query("all", description="Filter by repo type: java, python, all"),
) -> Dict[str, Any]:
    """
    List all indexed files (for client-side caching).
    """
    files = []

    if repo in ("all", "java"):
        files.extend(_get_cached_files("java"))
    if repo in ("all", "python"):
        files.extend(_get_cached_files("python"))

    return {
        "files": files,
        "total": len(files),
        "cache_ttl": _CACHE_TTL,
    }


@router.post("/refresh")
async def refresh_cache(
    repo: str = Query("all", description="Repo to refresh: java, python, all"),
) -> Dict[str, Any]:
    """
    Force refresh the file cache.
    """
    refreshed = []

    if repo in ("all", "java"):
        files = _refresh_cache("java")
        refreshed.append({"type": "java", "count": len(files)})

    if repo in ("all", "python"):
        files = _refresh_cache("python")
        refreshed.append({"type": "python", "count": len(files)})

    return {
        "refreshed": refreshed,
        "cache_ttl": _CACHE_TTL,
    }
