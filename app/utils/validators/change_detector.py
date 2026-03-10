"""
Change Detector - Erkennt geänderte Dateien.

Modi:
1. Git-basiert (Standard): Unstaged, Staged, Untracked
2. Timestamp-basiert (Fallback): Kürzlich geänderte Dateien
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ChangedFile:
    """Eine geänderte Datei."""
    path: str
    status: str  # modified, added, deleted, untracked, renamed
    staged: bool = False


class GitChangeDetector:
    """Git-basierte Änderungserkennung."""

    def __init__(self, repo_path: str):
        """
        Args:
            repo_path: Pfad zum Git-Repository
        """
        self.repo_path = Path(repo_path)

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        """Führt einen Git-Befehl aus."""
        cmd = ["git", "-C", str(self.repo_path)] + list(args)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            return (
                process.returncode or 0,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except Exception as e:
            return (-1, "", str(e))

    async def is_git_repo(self) -> bool:
        """Prüft ob der Pfad ein Git-Repository ist."""
        code, _, _ = await self._run_git("rev-parse", "--git-dir")
        return code == 0

    async def get_changed_files(
        self,
        include_untracked: bool = True,
        include_staged: bool = True,
        extensions: Optional[List[str]] = None,
    ) -> List[ChangedFile]:
        """
        Gibt alle geänderten Dateien zurück.

        Args:
            include_untracked: Auch untracked Dateien einschließen
            include_staged: Auch gestaged Dateien einschließen
            extensions: Nur diese Dateiendungen (z.B. [".py", ".java"])

        Returns:
            Liste von ChangedFile Objekten
        """
        if not await self.is_git_repo():
            logger.warning(f"{self.repo_path} is not a git repository")
            return []

        changed: List[ChangedFile] = []
        seen: Set[str] = set()

        # 1. Staged Changes (git diff --cached)
        if include_staged:
            code, stdout, _ = await self._run_git(
                "diff", "--cached", "--name-status"
            )
            if code == 0:
                for line in stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        status_char = parts[0][0]
                        file_path = parts[-1]

                        if self._matches_extensions(file_path, extensions):
                            status = self._parse_status(status_char)
                            full_path = str(self.repo_path / file_path)
                            if full_path not in seen:
                                changed.append(ChangedFile(
                                    path=full_path,
                                    status=status,
                                    staged=True,
                                ))
                                seen.add(full_path)

        # 2. Unstaged Changes (git diff)
        code, stdout, _ = await self._run_git("diff", "--name-status")
        if code == 0:
            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    status_char = parts[0][0]
                    file_path = parts[-1]

                    if self._matches_extensions(file_path, extensions):
                        status = self._parse_status(status_char)
                        full_path = str(self.repo_path / file_path)
                        if full_path not in seen:
                            changed.append(ChangedFile(
                                path=full_path,
                                status=status,
                                staged=False,
                            ))
                            seen.add(full_path)

        # 3. Untracked Files (git ls-files --others --exclude-standard)
        if include_untracked:
            code, stdout, _ = await self._run_git(
                "ls-files", "--others", "--exclude-standard"
            )
            if code == 0:
                for line in stdout.strip().split("\n"):
                    if not line:
                        continue
                    file_path = line.strip()

                    if self._matches_extensions(file_path, extensions):
                        full_path = str(self.repo_path / file_path)
                        if full_path not in seen:
                            changed.append(ChangedFile(
                                path=full_path,
                                status="untracked",
                                staged=False,
                            ))
                            seen.add(full_path)

        return changed

    async def get_files_since_commit(
        self,
        commit: str = "HEAD~1",
        extensions: Optional[List[str]] = None,
    ) -> List[ChangedFile]:
        """
        Gibt alle geänderten Dateien seit einem Commit zurück.

        Args:
            commit: Commit-Referenz (z.B. "HEAD~1", "abc123", "main")
            extensions: Nur diese Dateiendungen

        Returns:
            Liste von ChangedFile Objekten
        """
        if not await self.is_git_repo():
            return []

        code, stdout, _ = await self._run_git(
            "diff", "--name-status", commit, "HEAD"
        )

        if code != 0:
            return []

        changed: List[ChangedFile] = []

        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                status_char = parts[0][0]
                file_path = parts[-1]

                if self._matches_extensions(file_path, extensions):
                    status = self._parse_status(status_char)
                    full_path = str(self.repo_path / file_path)
                    changed.append(ChangedFile(
                        path=full_path,
                        status=status,
                        staged=False,
                    ))

        return changed

    def _parse_status(self, status_char: str) -> str:
        """Konvertiert Git Status-Zeichen zu lesbarem Status."""
        status_map = {
            "M": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "U": "unmerged",
            "?": "untracked",
        }
        return status_map.get(status_char, "modified")

    def _matches_extensions(
        self,
        file_path: str,
        extensions: Optional[List[str]]
    ) -> bool:
        """Prüft ob Datei den gewünschten Endungen entspricht."""
        if not extensions:
            return True
        ext = Path(file_path).suffix.lower()
        return ext in [e.lower() for e in extensions]


class TimestampChangeDetector:
    """Timestamp-basierte Änderungserkennung (Fallback für Nicht-Git-Repos)."""

    def __init__(self, repo_path: str):
        """
        Args:
            repo_path: Pfad zum Verzeichnis
        """
        self.repo_path = Path(repo_path)

    async def get_changed_files(
        self,
        since_minutes: int = 60,
        extensions: Optional[List[str]] = None,
        exclude_dirs: Optional[List[str]] = None,
    ) -> List[ChangedFile]:
        """
        Gibt kürzlich geänderte Dateien zurück.

        Args:
            since_minutes: Dateien geändert in den letzten N Minuten
            extensions: Nur diese Dateiendungen
            exclude_dirs: Diese Verzeichnisse überspringen

        Returns:
            Liste von ChangedFile Objekten
        """
        if not self.repo_path.exists():
            return []

        exclude_dirs = exclude_dirs or [
            ".git", "node_modules", "__pycache__", "target",
            ".idea", ".vscode", "build", "dist", ".gradle",
        ]

        cutoff_time = time.time() - (since_minutes * 60)
        changed: List[ChangedFile] = []

        def should_exclude(path: Path) -> bool:
            for part in path.parts:
                if part in exclude_dirs:
                    return True
            return False

        # Rekursiv durchsuchen
        for file_path in self.repo_path.rglob("*"):
            if not file_path.is_file():
                continue

            if should_exclude(file_path):
                continue

            # Extension prüfen
            if extensions:
                ext = file_path.suffix.lower()
                if ext not in [e.lower() for e in extensions]:
                    continue

            # Änderungszeit prüfen
            try:
                mtime = file_path.stat().st_mtime
                if mtime >= cutoff_time:
                    changed.append(ChangedFile(
                        path=str(file_path),
                        status="modified",
                        staged=False,
                    ))
            except OSError:
                continue

        return changed


async def get_changed_files(
    repo_path: str,
    use_git: bool = True,
    fallback_minutes: int = 60,
    extensions: Optional[List[str]] = None,
) -> List[ChangedFile]:
    """
    Convenience-Funktion: Gibt geänderte Dateien zurück.

    Versucht zuerst Git, dann Fallback auf Timestamp.

    Args:
        repo_path: Pfad zum Repository/Verzeichnis
        use_git: Git verwenden wenn möglich
        fallback_minutes: Bei Timestamp: Minuten zurück
        extensions: Nur diese Dateiendungen

    Returns:
        Liste von ChangedFile Objekten
    """
    if use_git:
        git_detector = GitChangeDetector(repo_path)
        if await git_detector.is_git_repo():
            return await git_detector.get_changed_files(extensions=extensions)

    # Fallback
    ts_detector = TimestampChangeDetector(repo_path)
    return await ts_detector.get_changed_files(
        since_minutes=fallback_minutes,
        extensions=extensions,
    )
