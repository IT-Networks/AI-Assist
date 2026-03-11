"""
Transcript Logger - Protokolliert Session-Verläufe für spätere Suche.

Format: JSONL (wie Claude Code)
- Ein JSON-Objekt pro Zeile
- Effizient für Append und Line-by-Line Lesen
- Durchsuchbar mit einfachen Tools

Inspiriert von Claude Code's Session Transcript System.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiofiles
    HAS_AIOFILES = True
except ImportError:
    HAS_AIOFILES = False

from app.core.config import settings


@dataclass
class TranscriptEntry:
    """Ein Eintrag im Session-Transcript."""
    timestamp: str
    type: str           # 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'event'
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.timestamp,
            "type": self.type,
            "content": self.content,
            **self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptEntry":
        ts = data.pop("ts", "")
        entry_type = data.pop("type", "unknown")
        content = data.pop("content", "")
        return cls(
            timestamp=ts,
            type=entry_type,
            content=content,
            metadata=data
        )


@dataclass
class SearchResult:
    """Ergebnis einer Transcript-Suche."""
    session_id: str
    entry: TranscriptEntry
    line_number: int
    match_score: float = 1.0


class TranscriptLogger:
    """
    Loggt Session-Verläufe für spätere Suche.

    Speicherort: {data_dir}/transcripts/{project_id}/{session_id}.jsonl

    Features:
    - JSONL-Format für effizientes Append
    - Durchsuchbar nach Schlüsselwörtern
    - Session-übergreifende Suche
    - Automatische Bereinigung alter Logs
    """

    # Max Zeichen pro Content-Feld (für Speichereffizienz)
    MAX_CONTENT_LENGTH = 2000

    # Max Alter für Transcripts (Tage)
    MAX_AGE_DAYS = 90

    def __init__(
        self,
        base_dir: Optional[str] = None,
        project_id: Optional[str] = None
    ):
        """
        Initialisiert den TranscriptLogger.

        Args:
            base_dir: Basis-Verzeichnis für Transcripts
            project_id: Projekt-ID (optional, kann pro Operation gesetzt werden)
        """
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(settings.index.directory) / "transcripts"

        self.project_id = project_id
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_dir(self, project_id: Optional[str] = None) -> Path:
        """Gibt das Log-Verzeichnis für ein Projekt zurück."""
        pid = project_id or self.project_id or "default"
        log_dir = self.base_dir / pid
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _get_log_file(
        self,
        session_id: str,
        project_id: Optional[str] = None
    ) -> Path:
        """Gibt den Pfad zur Log-Datei zurück."""
        return self._get_log_dir(project_id) / f"{session_id}.jsonl"

    # ══════════════════════════════════════════════════════════════════════════
    # Logging Operations
    # ══════════════════════════════════════════════════════════════════════════

    async def log_message(
        self,
        session_id: str,
        entry: TranscriptEntry,
        project_id: Optional[str] = None
    ) -> None:
        """
        Loggt eine Nachricht ins Transcript.

        Args:
            session_id: Session-ID
            entry: Der zu loggende Eintrag
            project_id: Projekt-ID (optional)
        """
        log_file = self._get_log_file(session_id, project_id)

        # Content kürzen wenn nötig
        content = entry.content
        if len(content) > self.MAX_CONTENT_LENGTH:
            content = content[:self.MAX_CONTENT_LENGTH] + "... [truncated]"

        record = {
            "ts": entry.timestamp,
            "type": entry.type,
            "content": content,
            **entry.metadata
        }

        line = json.dumps(record, ensure_ascii=False) + "\n"

        if HAS_AIOFILES:
            async with aiofiles.open(log_file, "a", encoding="utf-8") as f:
                await f.write(line)
        else:
            # Fallback: synchrones Schreiben
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)

    async def log_user_message(
        self,
        session_id: str,
        content: str,
        project_id: Optional[str] = None
    ) -> None:
        """Convenience: Loggt eine User-Nachricht."""
        await self.log_message(
            session_id=session_id,
            entry=TranscriptEntry(
                timestamp=datetime.utcnow().isoformat(),
                type="user",
                content=content
            ),
            project_id=project_id
        )

    async def log_assistant_message(
        self,
        session_id: str,
        content: str,
        project_id: Optional[str] = None,
        tool_count: int = 0
    ) -> None:
        """Convenience: Loggt eine Assistant-Nachricht."""
        await self.log_message(
            session_id=session_id,
            entry=TranscriptEntry(
                timestamp=datetime.utcnow().isoformat(),
                type="assistant",
                content=content,
                metadata={"tool_count": tool_count} if tool_count else {}
            ),
            project_id=project_id
        )

    async def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        project_id: Optional[str] = None
    ) -> None:
        """Convenience: Loggt einen Tool-Aufruf."""
        # Arguments kürzen für Logging
        args_str = json.dumps(arguments, ensure_ascii=False)
        if len(args_str) > 500:
            args_str = args_str[:500] + "..."

        await self.log_message(
            session_id=session_id,
            entry=TranscriptEntry(
                timestamp=datetime.utcnow().isoformat(),
                type="tool_call",
                content=f"{tool_name}: {args_str}",
                metadata={"tool": tool_name}
            ),
            project_id=project_id
        )

    async def log_tool_result(
        self,
        session_id: str,
        tool_name: str,
        result: str,
        success: bool = True,
        project_id: Optional[str] = None
    ) -> None:
        """Convenience: Loggt ein Tool-Ergebnis."""
        await self.log_message(
            session_id=session_id,
            entry=TranscriptEntry(
                timestamp=datetime.utcnow().isoformat(),
                type="tool_result",
                content=result,
                metadata={"tool": tool_name, "success": success}
            ),
            project_id=project_id
        )

    async def log_event(
        self,
        session_id: str,
        event_type: str,
        data: Any,
        project_id: Optional[str] = None
    ) -> None:
        """Loggt ein generisches Event."""
        content = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)

        await self.log_message(
            session_id=session_id,
            entry=TranscriptEntry(
                timestamp=datetime.utcnow().isoformat(),
                type="event",
                content=content,
                metadata={"event": event_type}
            ),
            project_id=project_id
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Search Operations
    # ══════════════════════════════════════════════════════════════════════════

    async def search_session(
        self,
        session_id: str,
        query: str,
        project_id: Optional[str] = None,
        entry_types: Optional[List[str]] = None,
        limit: int = 20
    ) -> List[SearchResult]:
        """
        Durchsucht ein einzelnes Session-Transcript.

        Args:
            session_id: Session-ID
            query: Suchbegriff
            project_id: Projekt-ID
            entry_types: Filter für Entry-Typen
            limit: Max. Ergebnisse
        """
        log_file = self._get_log_file(session_id, project_id)

        if not log_file.exists():
            return []

        results: List[SearchResult] = []
        query_lower = query.lower()

        try:
            if HAS_AIOFILES:
                async with aiofiles.open(log_file, "r", encoding="utf-8") as f:
                    line_num = 0
                    async for line in f:
                        line_num += 1
                        await self._process_search_line(
                            line, line_num, session_id, query_lower,
                            entry_types, results, limit
                        )
                        if len(results) >= limit:
                            break
            else:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        await self._process_search_line(
                            line, line_num, session_id, query_lower,
                            entry_types, results, limit
                        )
                        if len(results) >= limit:
                            break
        except Exception as e:
            print(f"[TranscriptLogger] Fehler beim Suchen in {log_file}: {e}")

        return results

    async def _process_search_line(
        self,
        line: str,
        line_num: int,
        session_id: str,
        query_lower: str,
        entry_types: Optional[List[str]],
        results: List[SearchResult],
        limit: int
    ) -> None:
        """Verarbeitet eine Zeile für die Suche."""
        try:
            record = json.loads(line.strip())

            # Typ-Filter
            if entry_types and record.get("type") not in entry_types:
                return

            # Suche im Content
            content = record.get("content", "").lower()
            if query_lower in content:
                entry = TranscriptEntry.from_dict(record.copy())
                results.append(SearchResult(
                    session_id=session_id,
                    entry=entry,
                    line_number=line_num
                ))
        except json.JSONDecodeError:
            pass

    async def search_project(
        self,
        query: str,
        project_id: Optional[str] = None,
        entry_types: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[SearchResult]:
        """
        Durchsucht alle Transcripts eines Projekts.

        Args:
            query: Suchbegriff
            project_id: Projekt-ID
            entry_types: Filter für Entry-Typen
            limit: Max. Ergebnisse
        """
        log_dir = self._get_log_dir(project_id)
        results: List[SearchResult] = []

        # Alle .jsonl Dateien durchsuchen (neueste zuerst)
        log_files = sorted(
            log_dir.glob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )

        for log_file in log_files:
            if len(results) >= limit:
                break

            session_id = log_file.stem
            session_results = await self.search_session(
                session_id=session_id,
                query=query,
                project_id=project_id,
                entry_types=entry_types,
                limit=limit - len(results)
            )
            results.extend(session_results)

        return results

    # ══════════════════════════════════════════════════════════════════════════
    # Session Management
    # ══════════════════════════════════════════════════════════════════════════

    async def get_session_summary(
        self,
        session_id: str,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Gibt eine Zusammenfassung einer Session zurück."""
        log_file = self._get_log_file(session_id, project_id)

        if not log_file.exists():
            return {"exists": False}

        stats = {
            "exists": True,
            "session_id": session_id,
            "file_size": log_file.stat().st_size,
            "modified": datetime.fromtimestamp(log_file.stat().st_mtime).isoformat(),
            "entry_counts": {},
            "first_entry": None,
            "last_entry": None
        }

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if lines:
                # Erste und letzte Zeile parsen
                try:
                    first = json.loads(lines[0])
                    stats["first_entry"] = first.get("ts")
                except json.JSONDecodeError:
                    pass

                try:
                    last = json.loads(lines[-1])
                    stats["last_entry"] = last.get("ts")
                except json.JSONDecodeError:
                    pass

                # Entry-Typen zählen
                for line in lines:
                    try:
                        record = json.loads(line)
                        entry_type = record.get("type", "unknown")
                        stats["entry_counts"][entry_type] = \
                            stats["entry_counts"].get(entry_type, 0) + 1
                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            stats["error"] = str(e)

        return stats

    async def list_sessions(
        self,
        project_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Listet alle Sessions eines Projekts."""
        log_dir = self._get_log_dir(project_id)
        sessions = []

        # Sortiert nach Änderungsdatum (neueste zuerst)
        log_files = sorted(
            log_dir.glob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )

        for log_file in log_files[:limit]:
            stats = log_file.stat()
            sessions.append({
                "session_id": log_file.stem,
                "file_size": stats.st_size,
                "modified": datetime.fromtimestamp(stats.st_mtime).isoformat()
            })

        return sessions

    async def delete_session(
        self,
        session_id: str,
        project_id: Optional[str] = None
    ) -> bool:
        """Löscht ein Session-Transcript."""
        log_file = self._get_log_file(session_id, project_id)

        if log_file.exists():
            log_file.unlink()
            return True
        return False

    async def cleanup_old_transcripts(
        self,
        max_age_days: Optional[int] = None,
        project_id: Optional[str] = None
    ) -> int:
        """
        Löscht alte Transcripts.

        Returns:
            Anzahl gelöschter Dateien
        """
        max_age = max_age_days or self.MAX_AGE_DAYS
        cutoff = datetime.now().timestamp() - (max_age * 24 * 60 * 60)

        log_dir = self._get_log_dir(project_id)
        deleted = 0

        for log_file in log_dir.glob("*.jsonl"):
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                deleted += 1

        return deleted


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_transcript_logger: Optional[TranscriptLogger] = None


def get_transcript_logger(project_id: Optional[str] = None) -> TranscriptLogger:
    """Gibt Singleton-Instanz zurück."""
    global _transcript_logger
    if _transcript_logger is None:
        _transcript_logger = TranscriptLogger()
    if project_id:
        _transcript_logger.project_id = project_id
    return _transcript_logger
