"""
Update Service - GitHub-basierte App-Updates.

Ermöglicht das Herunterladen und Installieren von Updates aus einem
GitHub-Repository via ZIP-Download mit Whitelist-Filterung.

Features:
- Version-Check via GitHub API (Releases oder Tags)
- ZIP-Download mit Proxy-Support
- Whitelist-basierte Extraktion (nur Code, keine Configs)
- Backup vor Update
- Server-Restart nach Update
"""

import asyncio
import fnmatch
import io
import logging
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from app.core.config import settings, build_proxy_url

logger = logging.getLogger(__name__)

# Aktuelle Version (aus main.py oder VERSION-Datei)
VERSION_FILE = Path(__file__).parent.parent.parent / "VERSION"
CURRENT_VERSION = "2.0.0"  # Fallback


def get_current_version() -> str:
    """Liest die aktuelle Version aus VERSION-Datei oder Fallback."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return CURRENT_VERSION


def parse_repo_url(repo_url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parst GitHub-Repository-URL in Owner und Repo-Name.

    Args:
        repo_url: z.B. "https://github.com/user/repo" oder "user/repo"

    Returns:
        (owner, repo) oder (None, None) bei Fehler
    """
    # Normalisiere URL
    url = repo_url.strip().rstrip("/")

    # Entferne Schema
    if url.startswith("https://"):
        url = url[8:]
    elif url.startswith("http://"):
        url = url[7:]

    # Entferne github.com prefix
    if url.startswith("github.com/"):
        url = url[11:]

    # Splitte in owner/repo
    parts = url.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]

    return None, None


def matches_pattern(path: str, patterns: List[str]) -> bool:
    """Prüft ob ein Pfad zu einem der Glob-Patterns passt."""
    # Normalisiere Pfad (forward slashes)
    path = path.replace("\\", "/")

    for pattern in patterns:
        pattern = pattern.replace("\\", "/")

        # ** Pattern für rekursive Matches
        if "**" in pattern:
            # Konvertiere ** zu regex
            regex_pattern = pattern.replace("**", ".*").replace("*", "[^/]*")
            if re.match(regex_pattern, path):
                return True
        elif fnmatch.fnmatch(path, pattern):
            return True

    return False


def should_extract_file(relative_path: str) -> bool:
    """
    Prüft ob eine Datei extrahiert werden soll.

    Args:
        relative_path: Pfad relativ zum Repo-Root

    Returns:
        True wenn Datei extrahiert werden soll
    """
    config = settings.update

    # Erst Blacklist prüfen (hat Priorität)
    if matches_pattern(relative_path, config.exclude_patterns):
        return False

    # Dann Whitelist prüfen
    return matches_pattern(relative_path, config.include_patterns)


class UpdateService:
    """Service für GitHub-basierte App-Updates."""

    def __init__(self):
        self.app_root = Path(__file__).parent.parent.parent
        self.backup_dir = self.app_root / "backups" / "updates"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _get_http_client(self) -> httpx.AsyncClient:
        """Erstellt HTTP-Client mit Proxy-Support."""
        config = settings.update

        # Proxy aus search-Config wenn aktiviert
        proxy_url = None
        if config.use_proxy and settings.search.proxy_url:
            proxy_url = build_proxy_url(
                settings.search.proxy_url,
                settings.search.proxy_username,
                settings.search.proxy_password
            )

        # Headers
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-Assist-Update-Service/1.0",
        }
        if config.github_token:
            headers["Authorization"] = f"token {config.github_token}"

        return httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds),
            verify=config.verify_ssl,
            proxy=proxy_url,
            headers=headers,
            follow_redirects=True,
        )

    async def check_for_updates(self) -> Dict:
        """
        Prüft auf verfügbare Updates.

        Returns:
            Dict mit: available, current_version, latest_version, release_notes, download_url
        """
        config = settings.update

        if not config.enabled or not config.repo_url:
            return {
                "available": False,
                "error": "Update-Service nicht konfiguriert",
            }

        owner, repo = parse_repo_url(config.repo_url)
        if not owner or not repo:
            return {
                "available": False,
                "error": f"Ungültige Repository-URL: {config.repo_url}",
            }

        current = get_current_version()

        try:
            async with self._get_http_client() as client:
                # Versuche zuerst Releases API
                response = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
                )

                if response.status_code == 200:
                    release = response.json()
                    latest = release.get("tag_name", "").lstrip("v")

                    # Vergleiche Versionen (einfacher String-Vergleich)
                    available = latest > current if latest else False

                    return {
                        "available": available,
                        "current_version": current,
                        "latest_version": latest,
                        "release_notes": release.get("body", ""),
                        "download_url": release.get("zipball_url", ""),
                        "html_url": release.get("html_url", ""),
                        "published_at": release.get("published_at", ""),
                    }

                elif response.status_code == 404:
                    # Keine Releases - versuche Tags
                    tags_response = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}/tags"
                    )

                    if tags_response.status_code == 200:
                        tags = tags_response.json()
                        if tags:
                            latest_tag = tags[0]
                            latest = latest_tag.get("name", "").lstrip("v")
                            available = latest > current if latest else False

                            return {
                                "available": available,
                                "current_version": current,
                                "latest_version": latest,
                                "release_notes": "",
                                "download_url": f"https://api.github.com/repos/{owner}/{repo}/zipball/{latest_tag.get('name')}",
                            }

                    # Kein Release, kein Tag - Main Branch
                    return {
                        "available": False,
                        "current_version": current,
                        "latest_version": current,
                        "message": "Keine Releases oder Tags gefunden. Verwende main-Branch.",
                        "download_url": f"https://api.github.com/repos/{owner}/{repo}/zipball/main",
                    }

                else:
                    return {
                        "available": False,
                        "error": f"GitHub API Fehler: {response.status_code}",
                    }

        except httpx.TimeoutException:
            return {
                "available": False,
                "error": "Timeout bei der Verbindung zu GitHub",
            }
        except Exception as e:
            logger.exception("[update] Check failed")
            return {
                "available": False,
                "error": str(e),
            }

    async def download_and_install(
        self,
        download_url: Optional[str] = None,
        create_backup: bool = True,
        progress_callback: Optional[callable] = None,
    ) -> Dict:
        """
        Lädt Update herunter und installiert es.

        Args:
            download_url: ZIP-Download-URL (None = latest)
            create_backup: Backup vor Update erstellen
            progress_callback: Async-Callback für Fortschritt (stage, percent, message)

        Returns:
            Dict mit: success, message, files_updated, backup_path
        """
        config = settings.update

        if not config.enabled:
            return {"success": False, "error": "Update-Service nicht aktiviert"}

        async def report_progress(stage: str, percent: int, message: str):
            if progress_callback:
                await progress_callback(stage, percent, message)
            logger.info(f"[update] {stage}: {percent}% - {message}")

        try:
            # 1. Download-URL ermitteln
            await report_progress("prepare", 0, "Ermittle Download-URL...")

            if not download_url:
                check = await self.check_for_updates()
                if "error" in check:
                    return {"success": False, "error": check["error"]}
                download_url = check.get("download_url")

            if not download_url:
                return {"success": False, "error": "Keine Download-URL verfügbar"}

            # 2. ZIP herunterladen
            await report_progress("download", 10, "Lade Update herunter...")

            async with self._get_http_client() as client:
                response = await client.get(download_url)

                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Download fehlgeschlagen: HTTP {response.status_code}",
                    }

                zip_data = response.content

            await report_progress("download", 50, f"Download abgeschlossen ({len(zip_data) // 1024} KB)")

            # 3. ZIP öffnen und analysieren
            await report_progress("analyze", 55, "Analysiere Update-Paket...")

            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, "r") as zf:
                # GitHub ZIP hat einen Root-Ordner (owner-repo-sha/)
                all_files = zf.namelist()
                if not all_files:
                    return {"success": False, "error": "Leeres ZIP-Archiv"}

                # Root-Ordner ermitteln
                root_prefix = all_files[0].split("/")[0] + "/"

                # Dateien filtern
                files_to_extract = []
                for zip_path in all_files:
                    if zip_path.endswith("/"):
                        continue  # Skip directories

                    # Pfad ohne Root-Ordner
                    relative_path = zip_path[len(root_prefix):]
                    if not relative_path:
                        continue

                    if should_extract_file(relative_path):
                        files_to_extract.append((zip_path, relative_path))

                if not files_to_extract:
                    return {
                        "success": False,
                        "error": "Keine aktualisierbaren Dateien gefunden",
                    }

                await report_progress("analyze", 60, f"{len(files_to_extract)} Dateien zu aktualisieren")

                # 4. Backup erstellen
                backup_path = None
                if create_backup:
                    await report_progress("backup", 65, "Erstelle Backup...")

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.backup_dir / f"backup_{timestamp}"
                    backup_path.mkdir(parents=True, exist_ok=True)

                    for _, relative_path in files_to_extract:
                        source = self.app_root / relative_path
                        if source.exists():
                            dest = backup_path / relative_path
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, dest)

                    await report_progress("backup", 75, f"Backup erstellt: {backup_path.name}")

                # 5. Dateien extrahieren
                await report_progress("install", 80, "Installiere Update...")

                updated_files = []
                for i, (zip_path, relative_path) in enumerate(files_to_extract):
                    dest = self.app_root / relative_path
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    with zf.open(zip_path) as source:
                        with open(dest, "wb") as target:
                            target.write(source.read())

                    updated_files.append(relative_path)

                    # Fortschritt
                    progress = 80 + int(15 * (i + 1) / len(files_to_extract))
                    if i % 10 == 0:
                        await report_progress("install", progress, f"{i + 1}/{len(files_to_extract)} Dateien...")

            await report_progress("complete", 100, f"Update abgeschlossen: {len(updated_files)} Dateien aktualisiert")

            return {
                "success": True,
                "message": f"{len(updated_files)} Dateien aktualisiert",
                "files_updated": updated_files,
                "backup_path": str(backup_path) if backup_path else None,
                "restart_required": True,
            }

        except zipfile.BadZipFile:
            return {"success": False, "error": "Ungültiges ZIP-Archiv"}
        except Exception as e:
            logger.exception("[update] Installation failed")
            return {"success": False, "error": str(e)}

    async def restore_backup(self, backup_name: str) -> Dict:
        """
        Stellt ein Backup wieder her.

        Args:
            backup_name: Name des Backup-Ordners

        Returns:
            Dict mit: success, message, files_restored
        """
        backup_path = self.backup_dir / backup_name

        if not backup_path.exists():
            return {"success": False, "error": f"Backup nicht gefunden: {backup_name}"}

        try:
            restored_files = []

            for backup_file in backup_path.rglob("*"):
                if backup_file.is_file():
                    relative = backup_file.relative_to(backup_path)
                    dest = self.app_root / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, dest)
                    restored_files.append(str(relative))

            return {
                "success": True,
                "message": f"{len(restored_files)} Dateien wiederhergestellt",
                "files_restored": restored_files,
                "restart_required": True,
            }

        except Exception as e:
            logger.exception("[update] Restore failed")
            return {"success": False, "error": str(e)}

    def list_backups(self) -> List[Dict]:
        """Listet alle verfügbaren Backups."""
        backups = []

        for backup_dir in sorted(self.backup_dir.iterdir(), reverse=True):
            if backup_dir.is_dir() and backup_dir.name.startswith("backup_"):
                file_count = sum(1 for _ in backup_dir.rglob("*") if _.is_file())
                backups.append({
                    "name": backup_dir.name,
                    "created": backup_dir.stat().st_mtime,
                    "file_count": file_count,
                })

        return backups

    def request_restart(self) -> None:
        """
        Fordert einen Server-Neustart an.

        Nutzt os.execv() für einen sauberen In-Place-Restart.
        """
        logger.info("[update] Server-Neustart angefordert...")

        # In-Place Restart via os.execv()
        # Startet denselben Python-Prozess mit denselben Argumenten neu
        python = sys.executable
        os.execv(python, [python] + sys.argv)


# Singleton
_update_service: Optional[UpdateService] = None


def get_update_service() -> UpdateService:
    """Gibt die Singleton-Instanz zurück."""
    global _update_service
    if _update_service is None:
        _update_service = UpdateService()
    return _update_service
