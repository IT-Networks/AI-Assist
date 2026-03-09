"""
Agent-Tools für lokale Git-Operationen.

Tools:
- git_status: Zeigt geänderte/ungetrackte Dateien
- git_diff: Zeigt Änderungen im Working Directory oder zwischen Commits
- git_log: Commit-Historie anzeigen
- git_branch_list: Branches auflisten, aktueller Branch
- git_blame: Wer hat welche Zeile geändert?
- git_stash_list: Stash-Einträge anzeigen

WICHTIG für Agent:
- Diese Tools sind für LOKALE Git-Repos
- Für REMOTE GitHub: github_* Tools verwenden
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


async def _run_git_command(
    args: List[str],
    cwd: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Führt einen Git-Befehl aus."""
    cmd = ["git"] + args

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

        if proc.returncode != 0:
            return {
                "success": False,
                "error": stderr_str or f"Git-Befehl fehlgeschlagen (exit code {proc.returncode})",
                "stdout": stdout_str,
            }

        return {
            "success": True,
            "stdout": stdout_str,
            "stderr": stderr_str,
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": f"Timeout nach {timeout} Sekunden"}
    except FileNotFoundError:
        return {"success": False, "error": "Git ist nicht installiert oder nicht im PATH"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _resolve_repo_path(path: Optional[str] = None) -> Optional[str]:
    """Löst den Repository-Pfad auf."""
    from app.core.config import settings

    if path:
        p = Path(path)
        if p.exists():
            return str(p)
        return None

    # Fallback auf konfigurierte Repos
    if settings.java.get_active_path():
        return settings.java.get_active_path()
    if settings.python.get_active_path():
        return settings.python.get_active_path()

    return None


def register_git_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    count = 0

    # ── git_status ──────────────────────────────────────────────────────────────
    async def git_status(**kwargs: Any) -> ToolResult:
        """Zeigt den Git-Status (geänderte, ungetrackte Dateien)."""
        repo_path = _resolve_repo_path(kwargs.get("path"))

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        # Git status mit porcelain für maschinenlesbares Format
        result = await _run_git_command(
            ["status", "--porcelain=v1", "-b"],
            cwd=repo_path,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        lines = result["stdout"].strip().split("\n") if result["stdout"].strip() else []

        # Erste Zeile ist Branch-Info
        branch_info = ""
        if lines and lines[0].startswith("##"):
            branch_info = lines[0][3:]  # "## branch...tracking"
            lines = lines[1:]

        # Dateien kategorisieren
        staged = []
        modified = []
        untracked = []

        for line in lines:
            if len(line) < 3:
                continue
            status = line[:2]
            filename = line[3:]

            if status[0] in "MADRC":  # Staged changes
                staged.append({"status": status[0], "file": filename})
            if status[1] == "M":  # Modified but not staged
                modified.append(filename)
            elif status == "??":  # Untracked
                untracked.append(filename)

        # Auch normale Status-Ausgabe für Kontext
        readable_result = await _run_git_command(
            ["status", "--short"],
            cwd=repo_path,
        )

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "branch": branch_info,
                "staged_count": len(staged),
                "staged": staged[:20],  # Max 20
                "modified_count": len(modified),
                "modified": modified[:20],
                "untracked_count": len(untracked),
                "untracked": untracked[:20],
                "summary": readable_result.get("stdout", "")[:2000],
            },
        )

    registry.register(Tool(
        name="git_status",
        description=(
            "Zeigt den Git-Status des lokalen Repositories: "
            "Geänderte Dateien, gestaged Änderungen, ungetrackte Dateien, aktueller Branch. "
            "Verwende dies um den aktuellen Zustand des Repos zu verstehen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Java/Python Repo)",
                required=False,
            ),
        ],
        handler=git_status,
    ))
    count += 1

    # ── git_diff ────────────────────────────────────────────────────────────────
    async def git_diff(**kwargs: Any) -> ToolResult:
        """Zeigt Git-Diff (Änderungen im Code)."""
        repo_path = _resolve_repo_path(kwargs.get("path"))
        file_filter: str = kwargs.get("file", "").strip()
        staged: bool = kwargs.get("staged", False)
        commit1: str = kwargs.get("commit1", "").strip()
        commit2: str = kwargs.get("commit2", "").strip()
        context_lines: int = int(kwargs.get("context_lines", 3))

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        # Git diff Befehl aufbauen
        args = ["diff", f"-U{context_lines}", "--stat"]

        if staged:
            args.append("--cached")
        elif commit1 and commit2:
            args.extend([commit1, commit2])
        elif commit1:
            args.append(commit1)

        if file_filter:
            args.extend(["--", file_filter])

        # Erst Statistik holen
        stat_result = await _run_git_command(args, cwd=repo_path)

        # Dann den eigentlichen Diff
        args_diff = ["diff", f"-U{context_lines}"]
        if staged:
            args_diff.append("--cached")
        elif commit1 and commit2:
            args_diff.extend([commit1, commit2])
        elif commit1:
            args_diff.append(commit1)
        if file_filter:
            args_diff.extend(["--", file_filter])

        diff_result = await _run_git_command(args_diff, cwd=repo_path)

        if not diff_result["success"]:
            return ToolResult(success=False, error=diff_result["error"])

        diff_content = diff_result["stdout"]
        truncated = False

        # Diff auf max 30000 Zeichen begrenzen
        if len(diff_content) > 30000:
            diff_content = diff_content[:30000]
            truncated = True

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "mode": "staged" if staged else ("commits" if commit1 else "working_directory"),
                "file_filter": file_filter or "(alle)",
                "stats": stat_result.get("stdout", "")[:1000],
                "diff": diff_content,
                "truncated": truncated,
            },
        )

    registry.register(Tool(
        name="git_diff",
        description=(
            "Zeigt Git-Diff: Code-Änderungen zwischen Working Directory, Staging, oder Commits. "
            "Kann auf bestimmte Dateien gefiltert werden. "
            "Zeigt hinzugefügte/entfernte Zeilen mit Kontext."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Repo)",
                required=False,
            ),
            ToolParameter(
                name="file",
                type="string",
                description="Auf Datei/Pfad filtern (z.B. 'src/Main.java' oder '*.java')",
                required=False,
            ),
            ToolParameter(
                name="staged",
                type="boolean",
                description="Nur gestaged Änderungen zeigen (Standard: false)",
                required=False,
            ),
            ToolParameter(
                name="commit1",
                type="string",
                description="Erster Commit/Branch zum Vergleichen",
                required=False,
            ),
            ToolParameter(
                name="commit2",
                type="string",
                description="Zweiter Commit/Branch (wenn leer: Vergleich mit Working Dir)",
                required=False,
            ),
            ToolParameter(
                name="context_lines",
                type="integer",
                description="Kontext-Zeilen um Änderungen (Standard: 3)",
                required=False,
            ),
        ],
        handler=git_diff,
    ))
    count += 1

    # ── git_log ─────────────────────────────────────────────────────────────────
    async def git_log(**kwargs: Any) -> ToolResult:
        """Zeigt Git-Commit-Historie."""
        repo_path = _resolve_repo_path(kwargs.get("path"))
        limit: int = min(int(kwargs.get("limit", 20)), 100)
        branch: str = kwargs.get("branch", "").strip()
        author: str = kwargs.get("author", "").strip()
        since: str = kwargs.get("since", "").strip()
        file_filter: str = kwargs.get("file", "").strip()
        oneline: bool = kwargs.get("oneline", False)

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        # Git log Befehl aufbauen
        if oneline:
            format_str = "%h|%s|%an|%ar"
        else:
            format_str = "%H%n%h%n%s%n%b%n%an%n%ae%n%ar%n%ad%n---COMMIT_END---"

        args = ["log", f"-{limit}", f"--format={format_str}"]

        if branch:
            args.append(branch)
        if author:
            args.append(f"--author={author}")
        if since:
            args.append(f"--since={since}")
        if file_filter:
            args.extend(["--", file_filter])

        result = await _run_git_command(args, cwd=repo_path)

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        commits = []

        if oneline:
            for line in result["stdout"].strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 3)
                if len(parts) >= 4:
                    commits.append({
                        "sha": parts[0],
                        "message": parts[1],
                        "author": parts[2],
                        "date": parts[3],
                    })
        else:
            raw_commits = result["stdout"].split("---COMMIT_END---")
            for raw in raw_commits:
                lines = raw.strip().split("\n")
                if len(lines) >= 7:
                    # Body kann mehrere Zeilen haben
                    body_lines = lines[3:-4] if len(lines) > 7 else []
                    commits.append({
                        "sha": lines[0],
                        "sha_short": lines[1],
                        "subject": lines[2],
                        "body": "\n".join(body_lines).strip()[:500],
                        "author": lines[-4],
                        "email": lines[-3],
                        "date_relative": lines[-2],
                        "date": lines[-1],
                    })

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "branch": branch or "(current)",
                "commit_count": len(commits),
                "commits": commits,
            },
        )

    registry.register(Tool(
        name="git_log",
        description=(
            "Zeigt die Git-Commit-Historie: Commits, Autoren, Zeitpunkte. "
            "Kann nach Branch, Autor, Zeitraum und Datei gefiltert werden. "
            "Verwende dies um die Entwicklungsgeschichte zu verstehen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Repo)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Anzahl Commits (Standard: 20, max: 100)",
                required=False,
            ),
            ToolParameter(
                name="branch",
                type="string",
                description="Branch-Name (leer = aktueller Branch)",
                required=False,
            ),
            ToolParameter(
                name="author",
                type="string",
                description="Nach Autor filtern",
                required=False,
            ),
            ToolParameter(
                name="since",
                type="string",
                description="Seit wann: '1 week ago', '2023-01-01', etc.",
                required=False,
            ),
            ToolParameter(
                name="file",
                type="string",
                description="Nur Commits die diese Datei betreffen",
                required=False,
            ),
            ToolParameter(
                name="oneline",
                type="boolean",
                description="Kompakte Ausgabe (Standard: false)",
                required=False,
            ),
        ],
        handler=git_log,
    ))
    count += 1

    # ── git_branch_list ─────────────────────────────────────────────────────────
    async def git_branch_list(**kwargs: Any) -> ToolResult:
        """Listet alle Git-Branches auf."""
        repo_path = _resolve_repo_path(kwargs.get("path"))
        include_remote: bool = kwargs.get("include_remote", False)

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        # Aktuellen Branch holen
        current_result = await _run_git_command(
            ["branch", "--show-current"],
            cwd=repo_path,
        )
        current_branch = current_result.get("stdout", "").strip() if current_result["success"] else ""

        # Alle Branches holen
        args = ["branch", "-v"]
        if include_remote:
            args.append("-a")

        result = await _run_git_command(args, cwd=repo_path)

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        branches = []
        for line in result["stdout"].strip().split("\n"):
            if not line.strip():
                continue
            is_current = line.startswith("*")
            # Parse: "* branch_name commit_sha commit_message" oder "  branch_name ..."
            parts = line[2:].split(None, 2)
            if parts:
                branch_name = parts[0]
                commit_sha = parts[1] if len(parts) > 1 else ""
                commit_msg = parts[2] if len(parts) > 2 else ""

                branches.append({
                    "name": branch_name,
                    "is_current": is_current,
                    "sha": commit_sha[:7] if commit_sha else "",
                    "last_commit": commit_msg[:100] if commit_msg else "",
                })

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "current_branch": current_branch,
                "branch_count": len(branches),
                "branches": branches,
            },
        )

    registry.register(Tool(
        name="git_branch_list",
        description=(
            "Listet alle Git-Branches auf: Name, letzter Commit, aktueller Branch. "
            "Optional auch Remote-Branches einschließen."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Repo)",
                required=False,
            ),
            ToolParameter(
                name="include_remote",
                type="boolean",
                description="Auch Remote-Branches zeigen (Standard: false)",
                required=False,
            ),
        ],
        handler=git_branch_list,
    ))
    count += 1

    # ── git_blame ───────────────────────────────────────────────────────────────
    async def git_blame(**kwargs: Any) -> ToolResult:
        """Zeigt wer welche Zeile geändert hat (git blame)."""
        repo_path = _resolve_repo_path(kwargs.get("path"))
        file: str = kwargs.get("file", "").strip()
        start_line: int = int(kwargs.get("start_line", 0))
        end_line: int = int(kwargs.get("end_line", 0))

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        if not file:
            return ToolResult(success=False, error="file ist erforderlich")

        # Git blame Befehl
        args = ["blame", "--porcelain"]

        if start_line > 0 and end_line > 0:
            args.extend(["-L", f"{start_line},{end_line}"])
        elif start_line > 0:
            args.extend(["-L", f"{start_line},+50"])  # 50 Zeilen ab start

        args.append(file)

        result = await _run_git_command(args, cwd=repo_path)

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        # Porcelain-Format parsen
        lines = result["stdout"].split("\n")
        blame_entries = []
        current_entry = {}

        for line in lines:
            if not line:
                continue

            # Neue Zeile beginnt mit SHA
            if len(line) >= 40 and all(c in "0123456789abcdef" for c in line[:40]):
                if current_entry:
                    blame_entries.append(current_entry)
                parts = line.split()
                current_entry = {
                    "sha": parts[0][:7],
                    "original_line": int(parts[1]) if len(parts) > 1 else 0,
                    "final_line": int(parts[2]) if len(parts) > 2 else 0,
                }
            elif line.startswith("author "):
                current_entry["author"] = line[7:]
            elif line.startswith("author-time "):
                import datetime
                ts = int(line[12:])
                current_entry["date"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            elif line.startswith("summary "):
                current_entry["summary"] = line[8:][:80]
            elif line.startswith("\t"):
                current_entry["code"] = line[1:][:200]

        if current_entry:
            blame_entries.append(current_entry)

        # Gruppieren nach Autor für Zusammenfassung
        author_stats = {}
        for entry in blame_entries:
            author = entry.get("author", "Unknown")
            author_stats[author] = author_stats.get(author, 0) + 1

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "file": file,
                "line_range": f"{start_line}-{end_line}" if start_line and end_line else "all",
                "total_lines": len(blame_entries),
                "author_stats": author_stats,
                "blame": blame_entries[:100],  # Max 100 Zeilen
            },
        )

    registry.register(Tool(
        name="git_blame",
        description=(
            "Zeigt wer welche Zeile einer Datei zuletzt geändert hat (git blame). "
            "Hilfreich um Verantwortliche für Code zu finden. "
            "Kann auf Zeilenbereich eingeschränkt werden."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Repo)",
                required=False,
            ),
            ToolParameter(
                name="file",
                type="string",
                description="Dateipfad relativ zum Repo (z.B. 'src/Main.java')",
                required=True,
            ),
            ToolParameter(
                name="start_line",
                type="integer",
                description="Start-Zeile (optional)",
                required=False,
            ),
            ToolParameter(
                name="end_line",
                type="integer",
                description="End-Zeile (optional)",
                required=False,
            ),
        ],
        handler=git_blame,
    ))
    count += 1

    # ── git_stash_list ──────────────────────────────────────────────────────────
    async def git_stash_list(**kwargs: Any) -> ToolResult:
        """Zeigt gespeicherte Stashes."""
        repo_path = _resolve_repo_path(kwargs.get("path"))

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        result = await _run_git_command(
            ["stash", "list", "--format=%gd|%gs|%ci"],
            cwd=repo_path,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        stashes = []
        for line in result["stdout"].strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) >= 2:
                stashes.append({
                    "ref": parts[0],
                    "message": parts[1],
                    "date": parts[2] if len(parts) > 2 else "",
                })

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "stash_count": len(stashes),
                "stashes": stashes,
            },
        )

    registry.register(Tool(
        name="git_stash_list",
        description=(
            "Zeigt alle gespeicherten Git-Stashes. "
            "Stashes sind zwischengespeicherte Änderungen die später wiederhergestellt werden können."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Repo)",
                required=False,
            ),
        ],
        handler=git_stash_list,
    ))
    count += 1

    # ── git_show_commit ─────────────────────────────────────────────────────────
    async def git_show_commit(**kwargs: Any) -> ToolResult:
        """Zeigt Details eines Commits mit Diff."""
        repo_path = _resolve_repo_path(kwargs.get("path"))
        commit: str = kwargs.get("commit", "").strip()

        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Repository-Pfad angegeben und kein Standard-Repo konfiguriert"
            )

        if not commit:
            return ToolResult(success=False, error="commit ist erforderlich (SHA oder HEAD~n)")

        # Commit-Info holen
        result = await _run_git_command(
            ["show", "--stat", "--format=fuller", commit],
            cwd=repo_path,
        )

        if not result["success"]:
            return ToolResult(success=False, error=result["error"])

        output = result["stdout"]

        # Auf max 20000 Zeichen begrenzen
        truncated = False
        if len(output) > 20000:
            output = output[:20000]
            truncated = True

        return ToolResult(
            success=True,
            data={
                "repo_path": repo_path,
                "commit": commit,
                "details": output,
                "truncated": truncated,
            },
        )

    registry.register(Tool(
        name="git_show_commit",
        description=(
            "Zeigt vollständige Details eines Commits: Metadaten, geänderte Dateien, Diff. "
            "Verwende Commit-SHA oder Referenzen wie HEAD, HEAD~1, branch_name."
        ),
        category=ToolCategory.DEVOPS,
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Repository-Pfad (leer = aktives Repo)",
                required=False,
            ),
            ToolParameter(
                name="commit",
                type="string",
                description="Commit-SHA, HEAD, HEAD~1, Branch-Name, etc.",
                required=True,
            ),
        ],
        handler=git_show_commit,
    ))
    count += 1

    return count
