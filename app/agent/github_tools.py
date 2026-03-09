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
from typing import Any, Dict, List, Optional

import httpx

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


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


def register_github_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    count = 0

    # ── github_list_repos ──────────────────────────────────────────────────────
    async def github_list_repos(**kwargs: Any) -> ToolResult:
        """Listet Repositories einer Organisation auf."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        org: str = kwargs.get("org", "").strip() or settings.github.default_org
        if not org:
            return ToolResult(success=False, error="org ist erforderlich (oder default_org in Konfiguration setzen)")

        api_url = settings.github.get_api_url()
        if not api_url:
            return ToolResult(success=False, error="GitHub API-URL ist nicht konfiguriert")

        result = await _github_request(
            method="GET",
            url=f"{api_url}/orgs/{org}/repos",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": settings.github.max_items, "sort": "updated"},
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
                "repos": repos,
            },
        )

    registry.register(Tool(
        name="github_list_repos",
        description=(
            "Listet alle Repositories einer GitHub-Organisation auf. "
            "Zeigt Name, Beschreibung, Sichtbarkeit und Anzahl offener Issues. "
            "Verwende dies um einen Überblick über Projekte zu bekommen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="org",
                type="string",
                description="GitHub-Organisation (leer = Standard-Organisation aus Konfiguration)",
                required=False,
            ),
        ],
        handler=github_list_repos,
    ))
    count += 1

    # ── github_list_prs ────────────────────────────────────────────────────────
    async def github_list_prs(**kwargs: Any) -> ToolResult:
        """Listet Pull Requests eines Repositories auf."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = kwargs.get("repo", "").strip() or settings.github.default_repo
        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich (Format: owner/repo)")

        state: str = kwargs.get("state", settings.github.pr_state_filter)

        api_url = settings.github.get_api_url()
        if not api_url:
            return ToolResult(success=False, error="GitHub API-URL ist nicht konfiguriert")

        result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/pulls",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"state": state, "per_page": settings.github.max_items, "sort": "updated"},
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
                "pull_requests": prs,
            },
        )

    registry.register(Tool(
        name="github_list_prs",
        description=(
            "Listet Pull Requests eines GitHub-Repositories auf. "
            "Zeigt Nummer, Titel, Autor, Status und Branches. "
            "Verwende dies um offene PRs zu sehen oder den Review-Status zu prüfen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository im Format 'owner/repo' (leer = Standard-Repository)",
                required=False,
            ),
            ToolParameter(
                name="state",
                type="string",
                description="Filter: 'open', 'closed', oder 'all' (Standard: open)",
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

        repo: str = kwargs.get("repo", "").strip() or settings.github.default_repo
        pr_number: int = int(kwargs.get("pr_number", 0))

        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich")
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
                description="Repository im Format 'owner/repo'",
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
        """Listet Issues eines Repositories auf."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = kwargs.get("repo", "").strip() or settings.github.default_repo
        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich")

        state: str = kwargs.get("state", settings.github.issue_state_filter)
        labels: str = kwargs.get("labels", "")

        api_url = settings.github.get_api_url()

        params = {
            "state": state,
            "per_page": settings.github.max_items,
            "sort": "updated",
        }
        if labels:
            params["labels"] = labels

        result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/issues",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params=params,
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
                "issues": issues,
            },
        )

    registry.register(Tool(
        name="github_list_issues",
        description=(
            "Listet Issues eines GitHub-Repositories auf (ohne Pull Requests). "
            "Zeigt Nummer, Titel, Labels und Kommentar-Anzahl. "
            "Verwende dies um offene Bugs oder Feature-Requests zu sehen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository im Format 'owner/repo'",
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
        ],
        handler=github_list_issues,
    ))
    count += 1

    # ── github_issue_details ───────────────────────────────────────────────────
    async def github_issue_details(**kwargs: Any) -> ToolResult:
        """Holt Details eines Issues."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = kwargs.get("repo", "").strip() or settings.github.default_repo
        issue_number: int = int(kwargs.get("issue_number", 0))

        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich")
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
            "Verwende dies um den Kontext eines Bugs oder Features zu verstehen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository im Format 'owner/repo'",
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
        """Listet Branches eines Repositories auf."""
        if not settings.github.enabled:
            return ToolResult(success=False, error="GitHub ist nicht aktiviert")

        repo: str = kwargs.get("repo", "").strip() or settings.github.default_repo
        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich")

        api_url = settings.github.get_api_url()

        result = await _github_request(
            method="GET",
            url=f"{api_url}/repos/{repo}/branches",
            token=settings.github.token,
            verify_ssl=settings.github.verify_ssl,
            timeout=settings.github.timeout_seconds,
            params={"per_page": settings.github.max_items},
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
                "branches": branches,
            },
        )

    registry.register(Tool(
        name="github_list_branches",
        description=(
            "Listet alle Branches eines GitHub-Repositories auf. "
            "Zeigt Name und ob der Branch geschützt ist. "
            "Verwende dies um verfügbare Branches zu sehen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository im Format 'owner/repo'",
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

        repo: str = kwargs.get("repo", "").strip() or settings.github.default_repo
        branch: str = kwargs.get("branch", "").strip()
        limit: int = min(int(kwargs.get("limit", 10)), 50)

        if not repo:
            return ToolResult(success=False, error="repo ist erforderlich")

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
            "Verwende dies um die Commit-Historie zu prüfen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="repo",
                type="string",
                description="Repository im Format 'owner/repo'",
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
