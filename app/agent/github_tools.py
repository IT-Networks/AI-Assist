"""
Agent-Tools für GitHub Enterprise Server (intern gehostet).

Tools:
- github_list_repos: Repositories einer Organisation auflisten
- github_list_prs: Pull Requests eines Repos auflisten
- github_pr_details: Details eines Pull Requests
- github_list_issues: Issues eines Repos auflisten
- github_issue_details: Details eines Issues
- github_list_branches: Branches eines Repos auflisten
- github_recent_commits: Letzte Commits eines Branches
"""

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


def _parse_link_header(link_header: str) -> Dict[str, str]:
    """Parst den GitHub Link-Header für Pagination."""
    links = {}
    if not link_header:
        return links

    for part in link_header.split(","):
        match = re.match(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if match:
            links[match.group(2)] = match.group(1)
    return links


async def _github_request(
    method: str,
    url: str,
    token: str,
    verify_ssl: bool = False,
    timeout: int = 30,
    params: Optional[dict] = None,
) -> Dict[str, Any]:
    """Führt einen GitHub API Request aus."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
    }

    async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout) as client:
        try:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.json().get("message", "")
            except Exception:
                error_body = e.response.text[:200]
            return {"success": False, "error": f"HTTP {e.response.status_code}: {error_body}"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Verbindungsfehler: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


async def _github_paginated_request(
    url: str,
    token: str,
    verify_ssl: bool = False,
    timeout: int = 30,
    params: Optional[dict] = None,
    max_items: int = 0,
) -> Dict[str, Any]:
    """
    Führt paginierte GitHub API Requests aus.

    Args:
        max_items: Maximale Anzahl Items (0 = alle)
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
    }

    all_data: List[Any] = []
    current_url = url
    page_count = 0
    max_pages = 100  # Sicherheitslimit

    async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout) as client:
        while current_url and page_count < max_pages:
            try:
                response = await client.get(
                    current_url,
                    headers=headers,
                    params=params if page_count == 0 else None,
                )
                response.raise_for_status()

                data = response.json()
                if isinstance(data, list):
                    all_data.extend(data)
                else:
                    all_data.append(data)

                page_count += 1

                # Prüfen ob max_items erreicht
                if max_items > 0 and len(all_data) >= max_items:
                    all_data = all_data[:max_items]
                    break

                # Nächste Seite aus Link-Header
                link_header = response.headers.get("Link", "")
                links = _parse_link_header(link_header)
                current_url = links.get("next")

            except httpx.HTTPStatusError as e:
                error_body = ""
                try:
                    error_body = e.response.json().get("message", "")
                except Exception:
                    error_body = e.response.text[:200]
                return {"success": False, "error": f"HTTP {e.response.status_code}: {error_body}"}
            except httpx.RequestError as e:
                return {"success": False, "error": f"Verbindungsfehler: {e}"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    return {
        "success": True,
        "data": all_data,
        "pages_fetched": page_count,
    }


def register_github_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    count = 0

    # ── github_list_repos ──────────────────────────────────────────────────────
    async def github_list_repos(**kwargs: Any) -> ToolResult:
        """Listet Repositories einer Organisation auf (mit Pagination)."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        org: str = kwargs.get("org", "").strip() or settings.github.default_org
        max_repos: int = int(kwargs.get("max_repos", 0))  # 0 = alle

        if not org:
            return ToolResult(success=False, error="org ist erforderlich (oder default_org in Konfiguration setzen)")

        api_url = settings.github.get_api_url()
        if not api_url:
            return ToolResult(success=False, error="GitHub API-URL ist nicht konfiguriert")

        # Paginierte Abfrage - holt alle Seiten
        result = await _github_paginated_request(
            url=f"{api_url}/orgs/{org}/repos",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": 100, "sort": "updated"},  # Max pro Seite
            max_items=max_repos,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        repos = []
        for repo in result["data"]:
            repos.append({
                "name": repo.get("name"),
                "full_name": repo.get("full_name"),
                "description": repo.get("description"),
                "private": repo.get("private"),
                "default_branch": repo.get("default_branch"),
                "open_issues_count": repo.get("open_issues_count"),
                "updated_at": repo.get("updated_at"),
            })

        return ToolResult(
            success=True,
            data={
                "org": org,
                "repo_count": len(repos),
                "pages_fetched": result.get("pages_fetched", 1),
                "repos": repos,
            },
        )

    registry.register(Tool(
        name="github_list_repos",
        description=(
            "Listet alle Repositories einer GitHub-Organisation auf (mit automatischer Pagination). "
            "Zeigt Name, Beschreibung, Sichtbarkeit und Anzahl offener Issues. "
            "Holt automatisch alle Seiten - nicht auf 50/100 begrenzt. "
            "Verwende max_repos um die Anzahl zu limitieren."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="org",
                type="string",
                description="GitHub-Organisation (leer = Standard-Organisation aus Konfiguration)",
                required=False,
            ),
            ToolParameter(
                name="max_repos",
                type="integer",
                description="Maximale Anzahl Repos (0 = alle, Standard: 0)",
                required=False,
            ),
        ],
        handler=github_list_repos,
    ))
    count += 1

    # ── Hilfsfunktion: Repo-Name zu vollständigem Pfad ──────────────────────────
    def _resolve_repo(repo_input: str) -> str:
        """
        Löst Repo-Namen auf: Wenn kein '/' enthalten, wird default_org vorangestellt.
        Beispiel: 'AI-Assist' → 'IT-Networks/AI-Assist'
        """
        repo = repo_input.strip()
        if not repo:
            return settings.github.default_repo
        if "/" not in repo and settings.github.default_org:
            return f"{settings.github.default_org}/{repo}"
        return repo

    # ── github_list_prs ────────────────────────────────────────────────────────
    async def github_list_prs(**kwargs: Any) -> ToolResult:
        """Listet Pull Requests eines Repositories auf (mit Pagination, neueste zuerst)."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = _resolve_repo(kwargs.get("repo", ""))
        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (Format: owner/repo oder nur repo-name wenn default_org gesetzt)")

        state: str = kwargs.get("state", settings.github.pr_state_filter)
        max_prs: int = int(kwargs.get("max_prs", 0))  # 0 = alle

        api_url = settings.github.get_api_url()
        if not api_url:
            return ToolResult(success=False, error="GitHub API-URL ist nicht konfiguriert")

        # Paginierte Abfrage mit direction=desc für neueste zuerst
        result = await _github_paginated_request(
            url=f"{api_url}/repos/{repo}/pulls",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"state": state, "per_page": 100, "sort": "updated", "direction": "desc"},
            max_items=max_prs,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        prs = []
        for pr in result["data"]:
            prs.append({
                "number": pr.get("number"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "user": pr.get("user", {}).get("login"),
                "created_at": pr.get("created_at"),
                "updated_at": pr.get("updated_at"),
                "head_branch": pr.get("head", {}).get("ref"),
                "base_branch": pr.get("base", {}).get("ref"),
                "draft": pr.get("draft"),
                "mergeable_state": pr.get("mergeable_state"),
            })

        return ToolResult(
            success=True,
            data={
                "repo": repo,
                "state_filter": state,
                "pr_count": len(prs),
                "pages_fetched": result.get("pages_fetched", 1),
                "pull_requests": prs,
            },
        )

    registry.register(Tool(
        name="github_list_prs",
        description=(
            "Listet Pull Requests eines GitHub-Repositories auf (neueste zuerst, mit Pagination). "
            "Zeigt Nummer, Titel, Autor, Status und Branches. "
            "Repo kann als 'owner/repo' oder nur 'repo-name' angegeben werden (default_org wird verwendet)."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository: 'owner/repo' oder nur 'repo-name' (dann wird default_org verwendet)",
                required=False,
            ),
            ToolParameter(
                name="state",
                type="string",
                description="Filter: 'open', 'closed', oder 'all' (Standard: open)",
                required=False,
            ),
            ToolParameter(
                name="max_prs",
                type="integer",
                description="Maximale Anzahl PRs (0 = alle, Standard: 0)",
                required=False,
            ),
        ],
        handler=github_list_prs,
    ))
    count += 1

    # ── github_pr_details ──────────────────────────────────────────────────────
    async def github_pr_details(**kwargs: Any) -> ToolResult:
        """Holt Details eines Pull Requests inkl. Reviews und Kommentare."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = _resolve_repo(kwargs.get("repo", ""))
        pr_number: int = int(kwargs.get("pr_number", 0))

        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (oder default_repo/default_org konfigurieren)")
        if not pr_number:
            return ToolResult(success=False, error="pr_number ist erforderlich")

        api_url = settings.github.get_api_url()

        # PR-Details holen
        pr_result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/pulls/{pr_number}",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
        )

        if not pr_result["success"]:
            return ToolResult(success=False, error=pr_result["error"])

        pr = pr_result["data"]

        # Reviews holen
        reviews_result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/pulls/{pr_number}/reviews",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
        )

        reviews = []
        if reviews_result["success"]:
            for review in reviews_result["data"]:
                reviews.append({
                    "user": review.get("user", {}).get("login"),
                    "state": review.get("state"),
                    "submitted_at": review.get("submitted_at"),
                })

        # Kommentare holen (nur Anzahl)
        comments_result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/pulls/{pr_number}/comments",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": 1},
        )

        return ToolResult(
            success=True,
            data={
                "number": pr.get("number"),
                "title": pr.get("title"),
                "body": (pr.get("body") or "")[:2000],  # Truncate
                "state": pr.get("state"),
                "user": pr.get("user", {}).get("login"),
                "created_at": pr.get("created_at"),
                "updated_at": pr.get("updated_at"),
                "merged": pr.get("merged"),
                "mergeable": pr.get("mergeable"),
                "mergeable_state": pr.get("mergeable_state"),
                "head_branch": pr.get("head", {}).get("ref"),
                "base_branch": pr.get("base", {}).get("ref"),
                "additions": pr.get("additions"),
                "deletions": pr.get("deletions"),
                "changed_files": pr.get("changed_files"),
                "reviews": reviews,
                "comment_count": pr.get("comments", 0) + pr.get("review_comments", 0),
            },
        )

    registry.register(Tool(
        name="github_pr_details",
        description=(
            "Holt detaillierte Informationen zu einem Pull Request: "
            "Beschreibung, Status, Änderungsstatistik, Reviews und Mergability. "
            "Verwende dies um einen PR im Detail zu analysieren."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository: 'owner/repo' oder nur 'repo-name' (dann wird default_org verwendet)",
                required=False,
            ),
            ToolParameter(
                name="pr_number",
                type="integer",
                description="Pull Request Nummer",
                required=True,
            ),
        ],
        handler=github_pr_details,
    ))
    count += 1

    # ── github_list_issues ─────────────────────────────────────────────────────
    async def github_list_issues(**kwargs: Any) -> ToolResult:
        """Listet Issues eines Repositories auf (mit Pagination, neueste zuerst)."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = _resolve_repo(kwargs.get("repo", ""))
        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (oder default_repo/default_org konfigurieren)")

        state: str = kwargs.get("state", settings.github.issue_state_filter)
        labels: str = kwargs.get("labels", "")
        max_issues: int = int(kwargs.get("max_issues", 0))  # 0 = alle

        api_url = settings.github.get_api_url()

        params = {
            "state": state,
            "per_page": 100,
            "sort": "updated",
            "direction": "desc",
        }
        if labels:
            params["labels"] = labels

        result = await _github_paginated_request(
            url=f"{api_url}/repos/{repo}/issues",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params=params,
            max_items=max_issues,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        issues = []
        for issue in result["data"]:
            # PRs werden auch als Issues zurückgegeben, filtern
            if "pull_request" in issue:
                continue
            issues.append({
                "number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "user": issue.get("user", {}).get("login"),
                "labels": [l.get("name") for l in issue.get("labels", [])],
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
                "comments": issue.get("comments"),
            })

        return ToolResult(
            success=True,
            data={
                "repo": repo,
                "state_filter": state,
                "issue_count": len(issues),
                "pages_fetched": result.get("pages_fetched", 1),
                "issues": issues,
            },
        )

    registry.register(Tool(
        name="github_list_issues",
        description=(
            "Listet Issues eines GitHub-Repositories auf (neueste zuerst, mit Pagination). "
            "Zeigt Nummer, Titel, Labels und Kommentar-Anzahl. "
            "Repo kann als 'owner/repo' oder nur 'repo-name' angegeben werden."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository: 'owner/repo' oder nur 'repo-name' (dann wird default_org verwendet)",
                required=False,
            ),
            ToolParameter(
                name="state",
                type="string",
                description="Filter: 'open', 'closed', oder 'all' (Standard: open)",
                required=False,
            ),
            ToolParameter(
                name="labels",
                type="string",
                description="Komma-separierte Labels zum Filtern",
                required=False,
            ),
            ToolParameter(
                name="max_issues",
                type="integer",
                description="Maximale Anzahl Issues (0 = alle, Standard: 0)",
                required=False,
            ),
        ],
        handler=github_list_issues,
    ))
    count += 1

    # ── github_issue_details ───────────────────────────────────────────────────
    async def github_issue_details(**kwargs: Any) -> ToolResult:
        """Holt Details eines Issues."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = _resolve_repo(kwargs.get("repo", ""))
        issue_number: int = int(kwargs.get("issue_number", 0))

        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (oder default_repo/default_org konfigurieren)")
        if not issue_number:
            return ToolResult(success=False, error="issue_number ist erforderlich")

        api_url = settings.github.get_api_url()

        result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/issues/{issue_number}",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        issue = result["data"]

        # Kommentare holen
        comments_result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/issues/{issue_number}/comments",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": 10},
        )

        comments = []
        if comments_result["success"]:
            for comment in comments_result["data"]:
                comments.append({
                    "user": comment.get("user", {}).get("login"),
                    "body": (comment.get("body") or "")[:500],
                    "created_at": comment.get("created_at"),
                })

        return ToolResult(
            success=True,
            data={
                "number": issue.get("number"),
                "title": issue.get("title"),
                "body": (issue.get("body") or "")[:3000],
                "state": issue.get("state"),
                "user": issue.get("user", {}).get("login"),
                "labels": [l.get("name") for l in issue.get("labels", [])],
                "assignees": [a.get("login") for a in issue.get("assignees", [])],
                "milestone": issue.get("milestone", {}).get("title") if issue.get("milestone") else None,
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
                "closed_at": issue.get("closed_at"),
                "recent_comments": comments,
            },
        )

    registry.register(Tool(
        name="github_issue_details",
        description=(
            "Holt detaillierte Informationen zu einem GitHub Issue: "
            "Beschreibung, Labels, Assignees, Milestone und letzte Kommentare. "
            "Repo kann als 'owner/repo' oder nur 'repo-name' angegeben werden."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository: 'owner/repo' oder nur 'repo-name' (dann wird default_org verwendet)",
                required=False,
            ),
            ToolParameter(
                name="issue_number",
                type="integer",
                description="Issue-Nummer",
                required=True,
            ),
        ],
        handler=github_issue_details,
    ))
    count += 1

    # ── github_list_branches ───────────────────────────────────────────────────
    async def github_list_branches(**kwargs: Any) -> ToolResult:
        """Listet Branches eines Repositories auf (mit Pagination)."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = _resolve_repo(kwargs.get("repo", ""))
        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (oder default_repo/default_org konfigurieren)")

        api_url = settings.github.get_api_url()

        result = await _github_paginated_request(
            url=f"{api_url}/repos/{repo}/branches",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": 100},
            max_items=0,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        branches = []
        for branch in result["data"]:
            branches.append({
                "name": branch.get("name"),
                "protected": branch.get("protected"),
            })

        return ToolResult(
            success=True,
            data={
                "repo": repo,
                "branch_count": len(branches),
                "pages_fetched": result.get("pages_fetched", 1),
                "branches": branches,
            },
        )

    registry.register(Tool(
        name="github_list_branches",
        description=(
            "Listet alle Branches eines GitHub-Repositories auf (mit Pagination). "
            "Zeigt Name und ob der Branch geschützt ist. "
            "Repo kann als 'owner/repo' oder nur 'repo-name' angegeben werden."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository: 'owner/repo' oder nur 'repo-name' (dann wird default_org verwendet)",
                required=False,
            ),
        ],
        handler=github_list_branches,
    ))
    count += 1

    # ── github_recent_commits ──────────────────────────────────────────────────
    async def github_recent_commits(**kwargs: Any) -> ToolResult:
        """Holt die letzten Commits eines Branches."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = _resolve_repo(kwargs.get("repo", ""))
        branch: str = kwargs.get("branch", "").strip()
        limit: int = min(int(kwargs.get("limit", 10)), 50)

        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (oder default_repo/default_org konfigurieren)")

        api_url = settings.github.get_api_url()

        params = {"per_page": limit}
        if branch:
            params["sha"] = branch

        result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/commits",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params=params,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        commits = []
        for commit in result["data"]:
            commit_data = commit.get("commit", {})
            commits.append({
                "sha": commit.get("sha", "")[:7],
                "message": commit_data.get("message", "").split("\n")[0],  # Erste Zeile
                "author": commit_data.get("author", {}).get("name"),
                "date": commit_data.get("author", {}).get("date"),
            })

        return ToolResult(
            success=True,
            data={
                "repo": repo,
                "branch": branch or "(default)",
                "commit_count": len(commits),
                "commits": commits,
            },
        )

    registry.register(Tool(
        name="github_recent_commits",
        description=(
            "Holt die letzten Commits eines GitHub-Repositories. "
            "Zeigt SHA (gekürzt), Commit-Message und Autor. "
            "Repo kann als 'owner/repo' oder nur 'repo-name' angegeben werden."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository: 'owner/repo' oder nur 'repo-name' (dann wird default_org verwendet)",
                required=False,
            ),
            ToolParameter(
                name="branch",
                type="string",
                description="Branch-Name (leer = Default-Branch)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Anzahl Commits (1-50, Standard: 10)",
                required=False,
            ),
        ],
        handler=github_recent_commits,
    ))
    count += 1

    return count
