"""
External Access Logger - Protokolliert alle KI-Zugriffe auf externe Systeme.

Features:
- Automatisches Logging aller HTTP-Requests zu externen Systemen
- JSONL-Format für effizientes Append und Suche
- Privacy-bewusst: Keine Auth-Header, keine Bodies, URL-Sanitization
- Statistik-Funktionen für Audit und Debugging

Inspiriert von TranscriptLogger für konsistentes Logging-Pattern.
"""

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    import aiofiles
    HAS_AIOFILES = True
except ImportError:
    HAS_AIOFILES = False

from app.core.config import settings


@dataclass
class ExternalAccessEntry:
    """Ein Eintrag im External Access Log."""
    id: str
    timestamp: str
    session_id: str
    tool_name: str              # z.B. "github_list_repos", "internal_fetch"
    client_type: str            # "github", "jenkins", "mq", "testtool", "internal", "api"
    method: str                 # GET, POST, PUT, DELETE, PATCH
    url: str                    # Sanitized URL (ohne Query-Parameter)
    host: str                   # Extrahierter Hostname
    status_code: int            # HTTP Status (0 bei Connection Error)
    success: bool               # True wenn 2xx
    response_size: int          # Bytes
    duration_ms: int            # Antwortzeit in Millisekunden
    error_message: Optional[str] = None
    content_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary für JSON-Serialisierung."""
        return {
            "id": self.id,
            "ts": self.timestamp,
            "session": self.session_id,
            "tool": self.tool_name,
            "client": self.client_type,
            "method": self.method,
            "url": self.url,
            "host": self.host,
            "status": self.status_code,
            "success": self.success,
            "size": self.response_size,
            "duration_ms": self.duration_ms,
            "error": self.error_message,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExternalAccessEntry":
        """Erstellt Entry aus Dictionary."""
        return cls(
            id=data.get("id", ""),
            timestamp=data.get("ts", ""),
            session_id=data.get("session", ""),
            tool_name=data.get("tool", ""),
            client_type=data.get("client", ""),
            method=data.get("method", ""),
            url=data.get("url", ""),
            host=data.get("host", ""),
            status_code=data.get("status", 0),
            success=data.get("success", False),
            response_size=data.get("size", 0),
            duration_ms=data.get("duration_ms", 0),
            error_message=data.get("error"),
            content_type=data.get("content_type"),
        )


def sanitize_url(url: str) -> str:
    """
    Entfernt sensible Daten aus URL für Privacy.

    - Entfernt Query-Parameter (können API-Keys enthalten)
    - Maskiert Passwörter in Basic-Auth URLs (user:pass@host → user:****@host)
    """
    try:
        parsed = urlparse(url)

        # Maskiere Passwort in Basic-Auth URL (user:pass@host)
        netloc = parsed.netloc
        if '@' in netloc and ':' in netloc.split('@')[0]:
            # Format: user:password@host
            auth_part, host_part = netloc.rsplit('@', 1)
            if ':' in auth_part:
                user, _ = auth_part.split(':', 1)
                netloc = f"{user}:****@{host_part}"

        # Nur Schema, Host und Pfad behalten (keine Query-Parameter)
        return f"{parsed.scheme}://{netloc}{parsed.path}"

    except Exception:
        # Fallback: Sensitive Patterns maskieren
        import re
        sensitive_patterns = [
            r'password[=:][^&\s]*',
            r'passwd[=:][^&\s]*',
            r'pwd[=:][^&\s]*',
            r'token[=:][^&\s]*',
            r'secret[=:][^&\s]*',
        ]
        result = url
        for pattern in sensitive_patterns:
            result = re.sub(pattern, '****', result, flags=re.IGNORECASE)
        return result


def extract_host(url: str) -> str:
    """Extrahiert Hostname aus URL."""
    try:
        parsed = urlparse(url)
        return parsed.netloc or ""
    except Exception:
        return ""


class ExternalAccessLogger:
    """
    Loggt externe Zugriffe für Audit und Debugging.

    Speicherort: {data_dir}/access_logs/{date}.jsonl

    Features:
    - JSONL-Format für effizientes Append
    - Tägliche Log-Rotation
    - Durchsuchbar nach verschiedenen Kriterien
    - Statistik-Funktionen
    """

    # Max Alter für Logs (Tage)
    MAX_AGE_DAYS = 90

    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialisiert den ExternalAccessLogger.

        Args:
            base_dir: Basis-Verzeichnis für Access-Logs
        """
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(settings.index.directory) / "access_logs"

        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file(self, date: Optional[datetime] = None) -> Path:
        """Gibt den Pfad zur Log-Datei für ein Datum zurück."""
        if date is None:
            date = datetime.utcnow()
        filename = date.strftime("%Y-%m-%d") + ".jsonl"
        return self.base_dir / filename

    # ══════════════════════════════════════════════════════════════════════════
    # Logging Operations
    # ══════════════════════════════════════════════════════════════════════════

    async def log_access(self, entry: ExternalAccessEntry) -> None:
        """
        Loggt einen externen Zugriff.

        Args:
            entry: Der zu loggende Eintrag
        """
        log_file = self._get_log_file()
        record = entry.to_dict()

        # Entferne None-Werte für kompaktere Logs
        record = {k: v for k, v in record.items() if v is not None}

        line = json.dumps(record, ensure_ascii=False) + "\n"

        if HAS_AIOFILES:
            async with aiofiles.open(log_file, "a", encoding="utf-8") as f:
                await f.write(line)
        else:
            # Fallback: synchrones Schreiben
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)

    def log_access_sync(self, entry: ExternalAccessEntry) -> None:
        """
        Synchrone Version von log_access für nicht-async Kontexte.
        """
        log_file = self._get_log_file()
        record = entry.to_dict()
        record = {k: v for k, v in record.items() if v is not None}
        line = json.dumps(record, ensure_ascii=False) + "\n"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

    def create_entry(
        self,
        session_id: str,
        tool_name: str,
        client_type: str,
        method: str,
        url: str,
        status_code: int,
        success: bool,
        response_size: int,
        duration_ms: int,
        error_message: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> ExternalAccessEntry:
        """
        Factory-Methode zum Erstellen eines Log-Eintrags.

        Kümmert sich um URL-Sanitization und Host-Extraktion.
        """
        return ExternalAccessEntry(
            id=str(uuid.uuid4())[:8],
            timestamp=datetime.utcnow().isoformat() + "Z",
            session_id=session_id or "unknown",
            tool_name=tool_name,
            client_type=client_type,
            method=method.upper(),
            url=sanitize_url(url),
            host=extract_host(url),
            status_code=status_code,
            success=success,
            response_size=response_size,
            duration_ms=duration_ms,
            error_message=error_message,
            content_type=content_type,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Search Operations
    # ══════════════════════════════════════════════════════════════════════════

    async def search_logs(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        host: Optional[str] = None,
        tool_name: Optional[str] = None,
        client_type: Optional[str] = None,
        success_only: bool = False,
        errors_only: bool = False,
        limit: int = 100,
    ) -> List[ExternalAccessEntry]:
        """
        Durchsucht Access-Logs nach Kriterien.

        Args:
            start_date: Startdatum (inklusiv)
            end_date: Enddatum (inklusiv)
            host: Filter nach Host
            tool_name: Filter nach Tool-Name
            client_type: Filter nach Client-Typ
            success_only: Nur erfolgreiche Requests
            errors_only: Nur fehlgeschlagene Requests
            limit: Max. Ergebnisse

        Returns:
            Liste von ExternalAccessEntry
        """
        if end_date is None:
            end_date = datetime.utcnow()
        if start_date is None:
            start_date = end_date - timedelta(days=7)

        results: List[ExternalAccessEntry] = []

        # Iteriere über alle Tage im Bereich
        current_date = start_date
        while current_date <= end_date and len(results) < limit:
            log_file = self._get_log_file(current_date)

            if log_file.exists():
                entries = await self._search_file(
                    log_file,
                    host=host,
                    tool_name=tool_name,
                    client_type=client_type,
                    success_only=success_only,
                    errors_only=errors_only,
                    limit=limit - len(results),
                )
                results.extend(entries)

            current_date += timedelta(days=1)

        return results

    async def _search_file(
        self,
        log_file: Path,
        host: Optional[str] = None,
        tool_name: Optional[str] = None,
        client_type: Optional[str] = None,
        success_only: bool = False,
        errors_only: bool = False,
        limit: int = 100,
    ) -> List[ExternalAccessEntry]:
        """Durchsucht eine einzelne Log-Datei."""
        results: List[ExternalAccessEntry] = []

        try:
            if HAS_AIOFILES:
                async with aiofiles.open(log_file, "r", encoding="utf-8") as f:
                    async for line in f:
                        entry = self._process_line(
                            line, host, tool_name, client_type,
                            success_only, errors_only
                        )
                        if entry:
                            results.append(entry)
                            if len(results) >= limit:
                                break
            else:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        entry = self._process_line(
                            line, host, tool_name, client_type,
                            success_only, errors_only
                        )
                        if entry:
                            results.append(entry)
                            if len(results) >= limit:
                                break
        except Exception as e:
            print(f"[ExternalAccessLogger] Fehler beim Lesen von {log_file}: {e}")

        return results

    def _process_line(
        self,
        line: str,
        host: Optional[str],
        tool_name: Optional[str],
        client_type: Optional[str],
        success_only: bool,
        errors_only: bool,
    ) -> Optional[ExternalAccessEntry]:
        """Verarbeitet eine Zeile und prüft Filter."""
        try:
            data = json.loads(line.strip())

            # Filter anwenden
            if host and data.get("host", "").lower() != host.lower():
                return None
            if tool_name and tool_name.lower() not in data.get("tool", "").lower():
                return None
            if client_type and data.get("client") != client_type:
                return None
            if success_only and not data.get("success"):
                return None
            if errors_only and data.get("success"):
                return None

            return ExternalAccessEntry.from_dict(data)

        except json.JSONDecodeError:
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # Statistics
    # ══════════════════════════════════════════════════════════════════════════

    async def get_statistics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Gibt Zugriffsstatistiken zurück.

        Returns:
            Dict mit:
            - total_requests: Gesamtzahl Requests
            - success_count: Erfolgreiche Requests
            - error_count: Fehlgeschlagene Requests
            - success_rate: Erfolgsrate in Prozent
            - by_tool: Requests pro Tool
            - by_host: Requests pro Host
            - by_client: Requests pro Client-Typ
            - avg_duration_ms: Durchschnittliche Antwortzeit
            - total_bytes: Gesamt-Datenmenge
        """
        if end_date is None:
            end_date = datetime.utcnow()
        if start_date is None:
            start_date = end_date - timedelta(days=7)

        stats = {
            "total_requests": 0,
            "success_count": 0,
            "error_count": 0,
            "success_rate": 0.0,
            "by_tool": {},
            "by_host": {},
            "by_client": {},
            "avg_duration_ms": 0.0,
            "total_bytes": 0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }

        total_duration = 0

        # Iteriere über alle Tage im Bereich
        current_date = start_date
        while current_date <= end_date:
            log_file = self._get_log_file(current_date)

            if log_file.exists():
                await self._process_stats_file(log_file, stats)
                total_duration += stats.get("_total_duration", 0)

            current_date += timedelta(days=1)

        # Berechne Durchschnitte
        if stats["total_requests"] > 0:
            stats["success_rate"] = round(
                (stats["success_count"] / stats["total_requests"]) * 100, 1
            )
            stats["avg_duration_ms"] = round(
                total_duration / stats["total_requests"], 1
            )

        # Entferne temporäre Felder
        stats.pop("_total_duration", None)

        return stats

    async def _process_stats_file(
        self,
        log_file: Path,
        stats: Dict[str, Any]
    ) -> None:
        """Verarbeitet eine Log-Datei für Statistiken."""
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())

                        stats["total_requests"] += 1

                        if data.get("success"):
                            stats["success_count"] += 1
                        else:
                            stats["error_count"] += 1

                        # By Tool
                        tool = data.get("tool", "unknown")
                        stats["by_tool"][tool] = stats["by_tool"].get(tool, 0) + 1

                        # By Host
                        host = data.get("host", "unknown")
                        stats["by_host"][host] = stats["by_host"].get(host, 0) + 1

                        # By Client
                        client = data.get("client", "unknown")
                        stats["by_client"][client] = stats["by_client"].get(client, 0) + 1

                        # Duration & Size
                        stats["_total_duration"] = stats.get("_total_duration", 0) + data.get("duration_ms", 0)
                        stats["total_bytes"] += data.get("size", 0)

                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            print(f"[ExternalAccessLogger] Fehler bei Statistik für {log_file}: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Maintenance
    # ══════════════════════════════════════════════════════════════════════════

    async def cleanup_old_logs(
        self,
        max_age_days: Optional[int] = None
    ) -> int:
        """
        Löscht alte Log-Dateien.

        Args:
            max_age_days: Max. Alter in Tagen (default: MAX_AGE_DAYS)

        Returns:
            Anzahl gelöschter Dateien
        """
        max_age = max_age_days or self.MAX_AGE_DAYS
        cutoff = datetime.now().timestamp() - (max_age * 24 * 60 * 60)

        deleted = 0
        for log_file in self.base_dir.glob("*.jsonl"):
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                deleted += 1

        return deleted

    def get_log_files(self) -> List[Dict[str, Any]]:
        """Listet alle Log-Dateien mit Metadaten."""
        files = []
        for log_file in sorted(self.base_dir.glob("*.jsonl"), reverse=True):
            stats = log_file.stat()
            files.append({
                "date": log_file.stem,
                "file_size": stats.st_size,
                "modified": datetime.fromtimestamp(stats.st_mtime).isoformat(),
            })
        return files


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_access_logger: Optional[ExternalAccessLogger] = None


def get_access_logger() -> ExternalAccessLogger:
    """Gibt Singleton-Instanz zurück."""
    global _access_logger
    if _access_logger is None:
        _access_logger = ExternalAccessLogger()
    return _access_logger
