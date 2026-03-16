"""
File Search API - Unified file search across all repositories.

Routes:
  GET  /api/files/search    - Fuzzy search for files by name
  GET  /api/files/list      - List all indexed files (for caching)
  GET  /api/files/repos/{lang} - List repos with file counts
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
# Cache structure: { "java": { "repo_name": {"files": [], "timestamp": 0} } }

_file_cache: Dict[str, Dict[str, Dict[str, Any]]] = {
    "java": {},
    "python": {},
}
_CACHE_TTL = 5 * 60  # 5 minutes in seconds


def _get_repos(lang: str) -> List[Dict[str, str]]:
    """Get all repos for a language type."""
    repos = []
    config = getattr(settings, lang, None)
    if not config:
        return repos

    # Get from repos list
    if hasattr(config, 'repos') and config.repos:
        for repo in config.repos:
            if os.path.isdir(repo.path):
                repos.append({"name": repo.name, "path": repo.path})

    # Fallback to repo_path if no repos defined
    if not repos and hasattr(config, 'repo_path') and config.repo_path:
        if os.path.isdir(config.repo_path):
            name = Path(config.repo_path).name
            repos.append({"name": name, "path": config.repo_path})

    return repos


def _is_cache_valid(lang: str, repo_name: str) -> bool:
    """Check if cache is still valid for a specific repo."""
    cache = _file_cache.get(lang, {}).get(repo_name)
    if not cache or not cache.get("files"):
        return False
    age = datetime.now().timestamp() - cache.get("timestamp", 0)
    return age < _CACHE_TTL


def _refresh_repo_cache(lang: str, repo_name: str, repo_path: str) -> List[Dict[str, Any]]:
    """Refresh file cache for a specific repository."""
    extensions = {
        "java": [".java", ".xml", ".properties", ".yaml", ".yml"],
        "python": [".py", ".yaml", ".yml", ".json", ".toml"],
    }

    files = _scan_directory(repo_path, lang, repo_name, extensions.get(lang, []))

    if lang not in _file_cache:
        _file_cache[lang] = {}

    _file_cache[lang][repo_name] = {
        "files": files,
        "timestamp": datetime.now().timestamp(),
    }

    return files


def _get_all_files(lang: str, repo_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all files for a language, optionally filtered by repo name."""
    all_files = []
    repos = _get_repos(lang)

    for repo in repos:
        # Skip if filtering by repo_name and doesn't match
        if repo_name and repo["name"] != repo_name:
            continue

        # Check cache or refresh
        if not _is_cache_valid(lang, repo["name"]):
            files = _refresh_repo_cache(lang, repo["name"], repo["path"])
        else:
            files = _file_cache[lang][repo["name"]]["files"]

        all_files.extend(files)

    return all_files


def _scan_directory(
    base_path: str,
    lang: str,
    repo_name: str,
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
                    "type": lang,
                    "repo": repo_name,
                })
    except Exception as e:
        print(f"[files] Error scanning {base_path}: {e}")

    return files


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

@router.get("/repos/{lang}")
async def get_repos(lang: str) -> Dict[str, Any]:
    """
    List all repositories for a language with file counts.
    """
    if lang not in ("java", "python"):
        return {"repos": [], "total": 0}

    repos = _get_repos(lang)
    result = []

    for repo in repos:
        # Get file count (uses cache if valid)
        files = _get_all_files(lang, repo["name"])
        result.append({
            "name": repo["name"],
            "path": repo["path"],
            "file_count": len(files),
        })

    return {
        "repos": result,
        "total": len(result),
    }


@router.get("/search")
async def search_files(
    q: str = Query(..., min_length=1, description="Search query"),
    lang: str = Query("all", description="Filter by language: java, python, all"),
    repo_name: Optional[str] = Query(None, description="Filter by specific repo name"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
) -> Dict[str, Any]:
    """
    Fuzzy search for files by name across repositories.

    Returns files sorted by match score (best matches first).
    """
    results = []

    # Determine which languages to search
    languages = []
    if lang in ("all", "java"):
        languages.append("java")
    if lang in ("all", "python"):
        languages.append("python")

    # Search each language
    for language in languages:
        files = _get_all_files(language, repo_name)

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
        "lang_filter": lang,
        "repo_filter": repo_name,
    }


@router.get("/list")
async def list_files(
    lang: str = Query("all", description="Filter by language: java, python, all"),
    repo_name: Optional[str] = Query(None, description="Filter by specific repo name"),
) -> Dict[str, Any]:
    """
    List all indexed files (for client-side caching).
    """
    files = []

    if lang in ("all", "java"):
        files.extend(_get_all_files("java", repo_name))
    if lang in ("all", "python"):
        files.extend(_get_all_files("python", repo_name))

    return {
        "files": files,
        "total": len(files),
        "cache_ttl": _CACHE_TTL,
    }


@router.post("/refresh")
async def refresh_cache_endpoint(
    lang: str = Query("all", description="Language to refresh: java, python, all"),
) -> Dict[str, Any]:
    """
    Force refresh the file cache for all repos.
    """
    refreshed = []

    languages = []
    if lang in ("all", "java"):
        languages.append("java")
    if lang in ("all", "python"):
        languages.append("python")

    for language in languages:
        repos = _get_repos(language)
        total_files = 0
        for repo in repos:
            files = _refresh_repo_cache(language, repo["name"], repo["path"])
            total_files += len(files)
        refreshed.append({"type": language, "count": total_files})

    return {
        "refreshed": refreshed,
        "cache_ttl": _CACHE_TTL,
    }
