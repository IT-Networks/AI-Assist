"""
Agent-Tools für Remote-Log-Server-Zugriff (ospe_ope.log).
NICHT für lokale WLP-Server-Logs – dafür gibt es wlp_read_log / wlp_read_ffdc.
Wird beim Startup registriert wenn log_servers aktiviert ist.

Architektur: Fetch-once → Grep-local
  - log_fetch_stage: Download + lokales Speichern → Summary + Dateipfade
  - log_grep: Regex-Suche auf lokalen Dateien mit Kontext-Zeilen
"""

import asyncio
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

# Cache-Konfiguration
_CACHE_DIR = Path("data/log_cache")
_CACHE_TTL_SECONDS = 600       # 10 Minuten
_CACHE_CLEANUP_SECONDS = 1800  # 30 Minuten – alte Caches entfernen

# Grep-Defaults
_DEFAULT_CONTEXT_LINES = 3
_DEFAULT_MAX_MATCHES = 50

# Regex für Log-Level-Erkennung – ALLE Levels, nicht nur Fehler
_LOG_LEVEL_RE = re.compile(
    r"\b(FATAL|ERROR|WARN(?:ING)?|SEVERE|EXCEPTION|INFO|DEBUG|TRACE|AUDIT|CONFIG)\b",
    re.IGNORECASE,
)
_ERROR_LEVELS = {"FATAL", "ERROR", "SEVERE", "EXCEPTION"}
_TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})"
)


def _extract_log_summary(content: str, server_name: str) -> Dict[str, Any]:
    """Extrahiert eine Zusammenfassung ALLER Log-Levels aus dem Content."""
    lines = content.splitlines()
    level_counts: Counter = Counter()
    notable_entries: List[Dict[str, str]] = []

    for line in lines:
        m = _LOG_LEVEL_RE.search(line)
        if not m:
            continue
        level = m.group(1).upper()
        if level == "WARNING":
            level = "WARN"
        level_counts[level] += 1

        # ERROR/FATAL/SEVERE/EXCEPTION als notable entries (max 50)
        if level in _ERROR_LEVELS and len(notable_entries) < 50:
            ts_match = _TIMESTAMP_RE.search(line)
            timestamp = ts_match.group(1) if ts_match else ""
            msg = line.strip()[:200]
            notable_entries.append({"timestamp": timestamp, "server": server_name, "level": level, "message": msg})

    return {
        "level_counts": dict(level_counts),
        "total_lines": len(lines),
        "total_errors": sum(v for k, v in level_counts.items() if k in _ERROR_LEVELS),
        "total_warnings": level_counts.get("WARN", 0),
        "total_info": level_counts.get("INFO", 0),
        "total_debug": level_counts.get("DEBUG", 0),
        "notable_entries": notable_entries,
    }


def _read_file_sync(path: str) -> str:
    """Synchrones Datei-Lesen für run_in_executor."""
    with open(path, "r", errors="replace") as f:
        return f.read()


# ── Cache-Helpers ────────────────────────────────────────────────────────────

def _cache_dir_for_stage(stage_id: str) -> Path:
    return _CACHE_DIR / stage_id


def _is_cache_valid(stage_id: str) -> bool:
    meta_path = _cache_dir_for_stage(stage_id) / "_meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        return (datetime.now() - fetched_at).total_seconds() < _CACHE_TTL_SECONDS
    except Exception:
        return False


def _read_cached_meta(stage_id: str) -> Optional[Dict]:
    meta_path = _cache_dir_for_stage(stage_id) / "_meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(stage_id: str, stage_name: str, server_results: List[Dict]) -> None:
    """Schreibt Logs + Meta in den Cache."""
    cache_dir = _cache_dir_for_stage(stage_id)
    cache_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "fetched_at": datetime.now().isoformat(),
        "stage_name": stage_name,
        "servers": {},
    }

    for sr in server_results:
        sid = sr["server_id"]
        if sr["success"]:
            # Log-Datei schreiben
            log_path = cache_dir / f"{sid}.log"
            log_path.write_text(sr["_content"], encoding="utf-8")
            meta["servers"][sid] = {
                "server_name": sr["server"],
                "success": True,
                "total_lines": sr["total_lines"],
                "log_summary": sr["log_summary"],
            }
        else:
            meta["servers"][sid] = {
                "server_name": sr["server"],
                "success": False,
                "error": sr.get("error", "Unbekannter Fehler"),
            }

    (cache_dir / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _cleanup_old_caches() -> None:
    """Entfernt Caches älter als _CACHE_CLEANUP_SECONDS."""
    if not _CACHE_DIR.exists():
        return
    for stage_dir in _CACHE_DIR.iterdir():
        if not stage_dir.is_dir():
            continue
        meta_path = stage_dir / "_meta.json"
        if not meta_path.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            age = (datetime.now() - datetime.fromisoformat(data["fetched_at"])).total_seconds()
            if age > _CACHE_CLEANUP_SECONDS:
                shutil.rmtree(stage_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)


# ── Grep-Helper ──────────────────────────────────────────────────────────────

# Stacktrace-Continuation: Zeilen die zu einem Stacktrace gehören
_STACKTRACE_CONT_RE = re.compile(
    r"^\s+(at |\.\.\.|\d+ more|Caused by:|Suppressed:)"
    r"|^(java|javax|org|com|net|io|sun|jdk)\.\S+Exception"
    r"|^(java|javax|org|com|net|io|sun|jdk)\.\S+Error"
    r"|^\s*\.\.\. \d+ more",
    re.IGNORECASE,
)


def _is_log_entry_start(line: str) -> bool:
    """Prüft ob eine Zeile der Beginn eines neuen Log-Eintrags ist (hat Timestamp + Level)."""
    return bool(_TIMESTAMP_RE.match(line) and _LOG_LEVEL_RE.search(line))


def _find_block_start(lines: List[str], idx: int, fallback_context: int) -> int:
    """Findet den Anfang des Log-Eintrags/Stacktrace-Blocks rückwärts."""
    start = idx
    for i in range(idx - 1, max(-1, idx - 200), -1):
        if _is_log_entry_start(lines[i]):
            start = i
            break
        if _STACKTRACE_CONT_RE.match(lines[i]):
            continue
        # Zeile gehört nicht zum Stacktrace – vielleicht Teil der Error-Message
        # Weiter rückwärts suchen bis zum Log-Entry-Start
        continue
    else:
        # Kein Log-Entry-Start gefunden → Fallback auf context
        start = max(0, idx - fallback_context)
    return start


def _find_block_end(lines: List[str], idx: int, fallback_context: int) -> int:
    """Findet das Ende des Stacktrace-Blocks vorwärts."""
    end = idx + 1
    for i in range(idx + 1, min(len(lines), idx + 500)):
        if _is_log_entry_start(lines[i]):
            # Neuer Log-Eintrag → Stacktrace ist zuende
            end = i
            break
        if _STACKTRACE_CONT_RE.match(lines[i]):
            end = i + 1
            continue
        # Leere Zeile oder sonstige Zeile nach dem Stacktrace
        # Noch 1-2 Zeilen tolerieren (leere Zeilen zwischen Caused-by)
        if lines[i].strip() == "":
            end = i + 1
            continue
        # Nicht-Stacktrace-Zeile → Ende
        end = i
        break
    else:
        end = min(len(lines), idx + fallback_context + 1)
    return end


def _grep_with_context(
    lines: List[str],
    pattern: re.Pattern,
    context: int = _DEFAULT_CONTEXT_LINES,
    max_matches: int = _DEFAULT_MAX_MATCHES,
) -> List[Dict]:
    """
    Grep mit Stacktrace-aware Kontext.

    Wenn der Match in einem Stacktrace liegt, wird der komplette Block
    zurückgegeben (vom Log-Entry-Start bis zum Ende des Stacktrace).
    Sonst: normaler Kontext (N Zeilen vor/nach, wie grep -C).
    """
    match_indices = []
    for i, line in enumerate(lines):
        if pattern.search(line):
            match_indices.append(i)
            if len(match_indices) >= max_matches:
                break

    # Deduplizierung: überlappende Blöcke zusammenführen
    blocks = []
    covered_until = -1

    for idx in match_indices:
        if idx <= covered_until:
            # Bereits in einem vorherigen Block enthalten
            continue

        # Prüfen ob Match in/nahe einem Stacktrace liegt
        in_stacktrace = bool(
            _STACKTRACE_CONT_RE.match(lines[idx])
            or (idx + 1 < len(lines) and _STACKTRACE_CONT_RE.match(lines[idx + 1]))
            or any(
                _STACKTRACE_CONT_RE.match(lines[j])
                for j in range(idx + 1, min(len(lines), idx + 4))
            )
        )

        if in_stacktrace:
            start = _find_block_start(lines, idx, context)
            end = _find_block_end(lines, idx, context)
        else:
            start = max(0, idx - context)
            end = min(len(lines), idx + context + 1)

        covered_until = end - 1
        block_lines = lines[start:end]

        blocks.append({
            "line_number": idx + 1,
            "block_start": start + 1,
            "block_end": end,
            "match_line": lines[idx],
            "is_stacktrace": in_stacktrace,
            "block": "\n".join(block_lines),
        })
    return blocks


def register_log_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    if not settings.log_servers.enabled:
        return 0

    count = 0

    # ── log_list_stages ───────────────────────────────────────────────────────
    async def log_list_stages(**kwargs: Any) -> ToolResult:
        stages = [
            {
                "id": s.id,
                "name": s.name,
                "server_count": len(s.servers),
                "servers": [{"id": srv.id, "name": srv.name, "url": srv.url} for srv in s.servers],
            }
            for s in settings.log_servers.stages
        ]
        return ToolResult(success=True, data={"stages": stages})

    registry.register(Tool(
        name="log_list_stages",
        description=(
            "Listet alle konfigurierten Remote-Log-Server-Stages auf (OSPE-Server, NICHT lokale WLP-Server). "
            "Gibt Stage-IDs und Server-IDs zurück. Diese IDs werden für log_fetch_stage und "
            "log_grep benötigt. Authentifizierung erfolgt automatisch – keine Zugangsdaten nötig."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=log_list_stages,
    ))
    count += 1

    # ── log_fetch_stage ─────────────────────────────────────────────────────
    async def log_fetch_stage(**kwargs: Any) -> ToolResult:
        from app.api.routes.log_servers import _fetch_server_logs, _get_credentials

        stage_id: str = kwargs.get("stage_id", "")
        server_id: str = kwargs.get("server_id", "")
        force: bool = kwargs.get("force", False)

        stage = next((s for s in settings.log_servers.stages if s.id == stage_id), None)
        if not stage:
            return ToolResult(success=False, error=f"Stage '{stage_id}' nicht gefunden")
        if not stage.servers:
            return ToolResult(success=False, error="Stage hat keine Server konfiguriert")

        # Cache prüfen (wenn nicht force)
        if not force and _is_cache_valid(stage_id):
            meta = _read_cached_meta(stage_id)
            if meta:
                # Wenn server_id angegeben: prüfen ob dieser Server im Cache ist
                if server_id and server_id not in meta.get("servers", {}):
                    pass  # Cache hat den Server nicht → neu fetchen
                else:
                    # Cache-Hit: Summary aus Meta zurückgeben
                    total_level_counts: Counter = Counter()
                    all_notable: list = []
                    servers_info = []
                    for sid, sdata in meta["servers"].items():
                        if server_id and sid != server_id:
                            continue
                        servers_info.append({
                            "server_id": sid,
                            "server": sdata.get("server_name", sid),
                            "success": sdata.get("success", False),
                            "total_lines": sdata.get("total_lines", 0),
                            "error": sdata.get("error"),
                        })
                        if sdata.get("success"):
                            summary = sdata.get("log_summary", {})
                            for level, cnt in summary.get("level_counts", {}).items():
                                total_level_counts[level] += cnt
                            all_notable.extend(summary.get("notable_entries", []))

                    successful = [s for s in servers_info if s["success"]]
                    return ToolResult(
                        success=bool(successful),
                        data={
                            "stage": meta.get("stage_name", stage.name),
                            "cached": True,
                            "cached_at": meta.get("fetched_at", ""),
                            "servers_total": len(servers_info),
                            "servers_successful": len(successful),
                            "log_overview": {
                                "level_counts": dict(total_level_counts),
                                "total_errors": sum(v for k, v in total_level_counts.items() if k in _ERROR_LEVELS),
                                "total_warnings": total_level_counts.get("WARN", 0),
                                "total_info": total_level_counts.get("INFO", 0),
                                "total_debug": total_level_counts.get("DEBUG", 0),
                                "notable_entries": all_notable[:100],
                            },
                            "servers": servers_info,
                            "hint": "Logs gecacht. Nutze log_grep zum Durchsuchen.",
                        },
                        error=None if successful else "Kein Server im Cache erfolgreich",
                    )

        # Alte Caches aufräumen
        _cleanup_old_caches()

        # Credentials einmal vorab laden
        try:
            creds = _get_credentials()
        except ValueError as e:
            return ToolResult(success=False, error=f"Credentials: {e}")

        # Server-Auswahl
        if server_id:
            servers_to_try = [s for s in stage.servers if s.id == server_id]
            if not servers_to_try:
                return ToolResult(success=False, error=f"Server '{server_id}' nicht in Stage '{stage.name}' gefunden")
        else:
            servers_to_try = stage.servers

        tail = settings.log_servers.default_tail

        # Alle Server parallel abfragen
        fetch_results = await asyncio.gather(
            *[_fetch_server_logs(s, tail, credentials=creds) for s in servers_to_try],
            return_exceptions=True,
        )

        results = []
        for server, fetch_result in zip(servers_to_try, fetch_results):
            if isinstance(fetch_result, BaseException):
                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": False,
                    "error": f"{type(fetch_result).__name__}: {fetch_result}",
                })
                continue

            if not fetch_result.success:
                results.append({
                    "server_id": server.id,
                    "server": server.name,
                    "success": False,
                    "error": fetch_result.error,
                })
                continue

            all_lines = fetch_result.content.splitlines()
            total_lines = len(all_lines)

            # Analyse auf VOLLEM Content
            log_summary = _extract_log_summary(fetch_result.content, server.name)

            results.append({
                "server_id": server.id,
                "server": server.name,
                "success": True,
                "total_lines": total_lines,
                "log_summary": log_summary,
                "_content": fetch_result.content,  # Nur für Cache, nicht im Response
            })

        successful = [r for r in results if r["success"]]
        all_errors = [r["error"] for r in results if not r["success"]]

        # Cache schreiben
        if successful:
            _write_cache(stage_id, stage.name, results)

        # Gesamt-Übersicht
        total_level_counts = Counter()
        all_notable = []
        for r in successful:
            summary = r.get("log_summary", {})
            for level, cnt in summary.get("level_counts", {}).items():
                total_level_counts[level] += cnt
            all_notable.extend(summary.get("notable_entries", []))

        # _content aus Response entfernen
        servers_info = []
        for r in results:
            info = {k: v for k, v in r.items() if k != "_content"}
            servers_info.append(info)

        return ToolResult(
            success=bool(successful),
            data={
                "stage": stage.name,
                "cached": False,
                "servers_total": len(servers_to_try),
                "servers_successful": len(successful),
                "log_overview": {
                    "level_counts": dict(total_level_counts),
                    "total_errors": sum(v for k, v in total_level_counts.items() if k in _ERROR_LEVELS),
                    "total_warnings": total_level_counts.get("WARN", 0),
                    "total_info": total_level_counts.get("INFO", 0),
                    "total_debug": total_level_counts.get("DEBUG", 0),
                    "notable_entries": all_notable[:100],
                },
                "servers": servers_info,
                "hint": "Logs heruntergeladen. Nutze log_grep zum Durchsuchen.",
            },
            error=None if successful else f"Kein Server erreichbar: {'; '.join(all_errors)}",
        )

    registry.register(Tool(
        name="log_fetch_stage",
        description=(
            "Lädt ospe_ope.log von Remote-OSPE-Servern herunter und speichert sie lokal (NICHT lokale WLP-Server). "
            "Authentifizierung erfolgt vollautomatisch – KEINE Zugangsdaten nötig. "
            "Gibt eine Log-Zusammenfassung zurück (Error-Counts, Notable Entries) – KEINEN Log-Content. "
            "Die Logs können danach beliebig oft mit log_grep durchsucht werden, ohne erneuten Download. "
            "Überspringt den Download wenn gecachte Logs < 10 Min alt (force=true erzwingt Neuladen). "
            "Voraussetzung: log_list_stages aufrufen um stage_id zu erhalten."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage (aus log_list_stages)", required=True),
            ToolParameter(name="server_id", type="string", description="Nur einen Server laden (optional)", required=False),
            ToolParameter(name="force", type="boolean", description="Cache ignorieren, neu downloaden (Default: false)", required=False),
        ],
        handler=log_fetch_stage,
    ))
    count += 1

    # ── log_grep ─────────────────────────────────────────────────────────────
    async def log_grep(**kwargs: Any) -> ToolResult:
        from app.api.routes.log_servers import _filter_lines_to_window

        stage_id: str = kwargs.get("stage_id", "")
        pattern_str: str = kwargs.get("pattern", "")
        server_id: str = kwargs.get("server_id", "")
        time_start: str = kwargs.get("time_start", "")
        time_end: str = kwargs.get("time_end", "")

        try:
            context_lines = int(kwargs.get("context_lines", _DEFAULT_CONTEXT_LINES))
            context_lines = max(0, min(context_lines, 20))
        except (ValueError, TypeError):
            context_lines = _DEFAULT_CONTEXT_LINES

        try:
            max_matches = int(kwargs.get("max_matches", _DEFAULT_MAX_MATCHES))
            max_matches = max(1, min(max_matches, 200))
        except (ValueError, TypeError):
            max_matches = _DEFAULT_MAX_MATCHES

        if not pattern_str:
            return ToolResult(success=False, error="pattern ist erforderlich")

        # Cache prüfen
        meta = _read_cached_meta(stage_id)
        if not meta:
            return ToolResult(
                success=False,
                error=f"Keine gecachten Logs für Stage '{stage_id}'. Erst log_fetch_stage aufrufen.",
            )

        # Regex kompilieren
        try:
            pattern = re.compile(pattern_str, re.IGNORECASE)
        except re.error as e:
            return ToolResult(success=False, error=f"Ungültiges Regex-Pattern: {e}")

        # Zeitfenster parsen (optional)
        t_start = t_end = None
        if time_start and time_end:
            try:
                t_start = datetime.fromisoformat(time_start.replace("Z", "+00:00")).replace(tzinfo=None)
                t_end = datetime.fromisoformat(time_end.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                return ToolResult(success=False, error="Ungültiges Zeitformat – ISO-8601 erwartet")

        # Lokale Log-Dateien durchsuchen
        cache_dir = _cache_dir_for_stage(stage_id)
        results = []
        total_matches = 0

        servers_to_search = meta.get("servers", {})
        if server_id:
            if server_id not in servers_to_search:
                return ToolResult(success=False, error=f"Server '{server_id}' nicht im Cache. Verfügbar: {list(servers_to_search.keys())}")
            servers_to_search = {server_id: servers_to_search[server_id]}

        loop = asyncio.get_event_loop()
        for sid, sdata in servers_to_search.items():
            if not sdata.get("success"):
                results.append({
                    "server_id": sid,
                    "server": sdata.get("server_name", sid),
                    "matches": 0,
                    "skipped": True,
                    "reason": sdata.get("error", "Fetch war nicht erfolgreich"),
                })
                continue

            log_path = cache_dir / f"{sid}.log"
            if not log_path.exists():
                results.append({
                    "server_id": sid,
                    "server": sdata.get("server_name", sid),
                    "matches": 0,
                    "skipped": True,
                    "reason": "Log-Datei nicht im Cache gefunden",
                })
                continue

            # Datei lesen (async via executor)
            content = await loop.run_in_executor(None, _read_file_sync, str(log_path))
            lines = content.splitlines()

            # Zeitfenster-Filter (optional)
            if t_start and t_end:
                lines = _filter_lines_to_window(lines, t_start, t_end)

            # Grep mit Kontext
            match_blocks = _grep_with_context(lines, pattern, context_lines, max_matches)
            match_count = len(match_blocks)
            total_matches += match_count

            results.append({
                "server_id": sid,
                "server": sdata.get("server_name", sid),
                "total_lines": sdata.get("total_lines", len(lines)),
                "matches": match_count,
                "truncated": match_count >= max_matches,
                "match_blocks": match_blocks,
            })

        return ToolResult(
            success=True,  # Immer True wenn Cache vorhanden – auch bei 0 Matches
            data={
                "stage": meta.get("stage_name", stage_id),
                "pattern": pattern_str,
                "total_matches": total_matches,
                "servers_searched": len(results),
                "results": results,
            },
        )

    registry.register(Tool(
        name="log_grep",
        description=(
            "Durchsucht zuvor mit log_fetch_stage heruntergeladene OSPE-Logs per Regex (NICHT lokale WLP-Server). "
            "Arbeitet auf lokalen Dateien – kein Netzwerk, beliebig oft aufrufbar. "
            "Stacktrace-aware: erkennt Java-Stacktraces automatisch und gibt den KOMPLETTEN "
            "Block zurück (inkl. Caused-by-Ketten), nicht nur einzelne Zeilen. "
            "Gibt Treffer mit Kontext-Zeilen zurück (wie grep -C). Case-insensitive. "
            "Unterstützt Regex-Patterns (z.B. 'Exception.*timeout|Connection refused'). "
            "Voraussetzung: log_fetch_stage muss vorher aufgerufen worden sein."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="stage_id", type="string", description="ID der Stage (Logs müssen vorher mit log_fetch_stage geladen sein)", required=True),
            ToolParameter(name="pattern", type="string", description="Regex-Suchpattern (case-insensitive)", required=True),
            ToolParameter(name="server_id", type="string", description="Nur einen Server durchsuchen (optional)", required=False),
            ToolParameter(name="context_lines", type="integer", description="Zeilen vor/nach jedem Treffer, wie grep -C (Default: 3)", required=False),
            ToolParameter(name="time_start", type="string", description="Zeitfenster-Beginn ISO-8601 (optional)", required=False),
            ToolParameter(name="time_end", type="string", description="Zeitfenster-Ende ISO-8601 (optional)", required=False),
            ToolParameter(name="max_matches", type="integer", description="Max Treffer pro Server (Default: 50)", required=False),
        ],
        handler=log_grep,
    ))
    count += 1

    # ── log_read_ffdc ─────────────────────────────────────────────────────────
    async def log_read_ffdc(**kwargs: Any) -> ToolResult:
        """Liest FFDC-Logs eines lokalen WLP-Servers (First Failure Data Capture)."""
        from pathlib import Path
        import logging
        logger = logging.getLogger(__name__)

        server_id: str = kwargs.get("server_id", "")

        try:
            max_files_raw = int(kwargs.get("max_files", 3))
            max_files: int = max(1, min(max_files_raw, 20))
        except (ValueError, TypeError):
            max_files = 3

        srv = next((s for s in settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"WLP-Server '{server_id}' nicht gefunden")

        ffdc_dir = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "logs" / "ffdc"
        if not ffdc_dir.exists():
            return ToolResult(success=False, error=f"FFDC-Verzeichnis nicht gefunden: {ffdc_dir}")

        ffdc_files = sorted(ffdc_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not ffdc_files:
            ffdc_files = sorted(ffdc_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not ffdc_files:
            return ToolResult(success=True, data={"found": False, "message": "Keine FFDC-Dateien gefunden"})

        results = []
        loop = asyncio.get_event_loop()
        for fpath in ffdc_files[:max_files]:
            try:
                content = await loop.run_in_executor(None, _read_file_sync, str(fpath))
                results.append({
                    "file": fpath.name,
                    "path": str(fpath),
                    "size_kb": round(fpath.stat().st_size / 1024, 1),
                    "content": content[:8000] if len(content) > 8000 else content,
                    "truncated": len(content) > 8000,
                })
            except Exception as e:
                logger.warning(f"FFDC-Datei lesen fehlgeschlagen ({fpath.name}): {e}")
                results.append({"file": fpath.name, "error": str(e)})

        return ToolResult(success=True, data={
            "ffdc_dir": str(ffdc_dir),
            "total_files": len(ffdc_files),
            "files_read": results,
        })

    registry.register(Tool(
        name="log_read_ffdc",
        description=(
            "Liest FFDC-Logs (First Failure Data Capture) eines LOKALEN WLP-Servers. "
            "NICHT für Remote-OSPE-Server – dafür log_fetch_stage / log_grep nutzen. "
            "FFDC-Logs enthalten detaillierte Stack-Traces bei unerwarteten Fehlern. "
            "Nutze dies wenn messages.log nur 'FFDC' erwähnt aber keine Details zeigt."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="server_id", type="string", description="ID des lokalen WLP-Servers (aus wlp_list_servers, NICHT aus log_list_stages)", required=True),
            ToolParameter(name="max_files", type="integer", description="Maximale Anzahl FFDC-Dateien (Standard: 3)", required=False),
        ],
        handler=log_read_ffdc,
    ))
    count += 1

    return count
