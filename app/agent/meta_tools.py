"""
Meta-Tools - Kombinierte Tools für effizientere Operationen.

Diese Tools reduzieren die Anzahl der Tool-Aufrufe indem sie
mehrere Operationen parallel ausführen.

Haupttools:
- combined_search: Durchsucht mehrere Quellen parallel
- batch_read_files: Liest mehrere Dateien in einem Aufruf
- batch_write_files: Schreibt mehrere Dateien in einem Aufruf (EINE Bestätigung)
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


async def combined_search(
    query: str,
    sources: str = "code,handbook",
    max_per_source: int = 5,
    include_content: bool = False,
    language: str = "all",
    **kwargs
) -> ToolResult:
    """
    Durchsucht mehrere Quellen parallel und kombiniert die Ergebnisse.

    Ersetzt: search_code + search_handbook + search_skills
    Ein Tool-Aufruf statt 3!

    Args:
        query: Suchbegriff
        sources: Komma-getrennte Liste (code, handbook, skills)
        max_per_source: Max Ergebnisse pro Quelle
        include_content: Bei Code-Suche Dateien direkt mitlesen
        language: Sprache für Code-Suche (all, java, python)
    """
    from app.agent.tools import search_code, search_handbook, search_skills

    if not query or not query.strip():
        return ToolResult(
            success=False,
            error="Query darf nicht leer sein"
        )

    query = query.strip()
    source_list = [s.strip().lower() for s in sources.split(",") if s.strip()]

    if not source_list:
        source_list = ["code", "handbook"]

    # Validiere Quellen
    valid_sources = {"code", "handbook", "skills"}
    invalid = set(source_list) - valid_sources
    if invalid:
        return ToolResult(
            success=False,
            error=f"Ungültige Quellen: {', '.join(invalid)}. Erlaubt: code, handbook, skills"
        )

    tasks = []
    source_names = []

    # Parallele Tasks erstellen
    if "code" in source_list:
        tasks.append(_safe_search(
            search_code,
            query=query,
            language=language,
            max_results=max_per_source,
            read_files=include_content
        ))
        source_names.append("Code")

    if "handbook" in source_list:
        tasks.append(_safe_search(
            search_handbook,
            query=query,
            top_k=max_per_source
        ))
        source_names.append("Handbuch")

    if "skills" in source_list:
        tasks.append(_safe_search(
            search_skills,
            query=query,
            top_k=max_per_source
        ))
        source_names.append("Skills")

    if not tasks:
        return ToolResult(
            success=False,
            error="Keine Quellen zum Durchsuchen angegeben"
        )

    logger.debug(f"[combined_search] Starte parallele Suche in {len(tasks)} Quellen für: {query[:50]}...")

    # Parallel ausführen mit Timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=60.0  # 60 Sekunden Gesamt-Timeout
        )
    except asyncio.TimeoutError:
        return ToolResult(
            success=False,
            error="Timeout bei paralleler Suche (60s)"
        )

    # Ergebnisse kombinieren
    output = f"=== Kombinierte Suche: '{query}' ===\n"
    output += f"Quellen: {', '.join(source_names)}\n"
    output += f"Max pro Quelle: {max_per_source}\n\n"

    found_any = False
    error_count = 0

    for i, result in enumerate(results):
        source_name = source_names[i]
        output += f"━━━ {source_name} ━━━\n"

        if isinstance(result, Exception):
            output += f"  ⚠️ Fehler: {result}\n\n"
            error_count += 1
        elif isinstance(result, ToolResult):
            if result.success and result.data:
                # Daten etwas einrücken für bessere Lesbarkeit
                data_str = str(result.data)
                # Kürzen wenn zu lang
                if len(data_str) > 3000:
                    data_str = data_str[:3000] + f"\n\n... [+{len(data_str) - 3000} Zeichen gekürzt]"
                output += f"{data_str}\n\n"
                found_any = True
            elif result.error:
                output += f"  ⚠️ {result.error}\n\n"
                error_count += 1
            else:
                output += "  Keine Treffer\n\n"
        else:
            output += f"  ⚠️ Unerwartetes Ergebnis: {type(result)}\n\n"
            error_count += 1

    # Zusammenfassung
    output += "━━━━━━━━━━━━━━━━━━━━━━━\n"
    if found_any:
        output += f"✓ Ergebnisse in {len(source_names) - error_count}/{len(source_names)} Quellen gefunden"
    else:
        output += f"✗ Keine Treffer in {len(source_names)} Quellen"

    return ToolResult(
        success=found_any or error_count < len(source_names),
        data=output
    )


async def _safe_search(search_func, **kwargs) -> ToolResult:
    """Wrapper für sichere Ausführung von Such-Funktionen."""
    try:
        return await search_func(**kwargs)
    except Exception as e:
        logger.warning(f"[combined_search] Fehler in {search_func.__name__}: {e}")
        return ToolResult(success=False, error=str(e))


async def batch_read_files(
    paths: str,
    show_line_numbers: bool = True,
    max_lines_per_file: int = 100,
    encoding: str = "utf-8",
    **kwargs
) -> ToolResult:
    """
    Liest mehrere Dateien in einem Aufruf.

    Ersetzt: read_file × N

    Args:
        paths: Komma-getrennte Dateipfade (max 10)
        show_line_numbers: Zeilennummern anzeigen
        max_lines_per_file: Max Zeilen pro Datei (0 = unbegrenzt)
        encoding: Datei-Encoding
    """
    from app.agent.tools import read_file

    if not paths or not paths.strip():
        return ToolResult(
            success=False,
            error="Keine Pfade angegeben. Beispiel: paths='file1.java, file2.java'"
        )

    # Pfade parsen - unterstützt Komma und Semikolon als Trenner
    path_list = []
    for sep in [";", ","]:
        if sep in paths:
            path_list = [p.strip() for p in paths.split(sep) if p.strip()]
            break

    if not path_list:
        # Einzelner Pfad ohne Trenner
        path_list = [paths.strip()]

    # Limit prüfen
    max_files = 10
    if len(path_list) > max_files:
        return ToolResult(
            success=False,
            error=f"Maximal {max_files} Dateien pro Aufruf (angefordert: {len(path_list)}). "
                  f"Teile die Anfrage auf oder nutze search_code für gezielte Suche."
        )

    logger.debug(f"[batch_read_files] Lese {len(path_list)} Dateien...")

    # Parallele Tasks erstellen
    tasks = [
        _safe_read_file(
            read_file,
            path=p,
            limit=max_lines_per_file if max_lines_per_file > 0 else 0,
            show_line_numbers=show_line_numbers,
            encoding=encoding
        )
        for p in path_list
    ]

    # Parallel ausführen
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=30.0  # 30 Sekunden Timeout
        )
    except asyncio.TimeoutError:
        return ToolResult(
            success=False,
            error="Timeout beim Lesen der Dateien (30s)"
        )

    # Ergebnisse kombinieren
    output = f"=== Batch-Lesen: {len(path_list)} Dateien ===\n\n"
    success_count = 0
    total_lines = 0

    for path, result in zip(path_list, results):
        if isinstance(result, Exception):
            output += f"━━━ {path} ━━━\n"
            output += f"⚠️ FEHLER: {result}\n\n"
        elif isinstance(result, ToolResult):
            if result.success and result.data:
                output += f"{result.data}\n\n"
                success_count += 1
                # Zeilen zählen
                total_lines += result.data.count("\n")
            else:
                output += f"━━━ {path} ━━━\n"
                output += f"⚠️ {result.error or 'Unbekannter Fehler'}\n\n"
        else:
            output += f"━━━ {path} ━━━\n"
            output += f"⚠️ Unerwarteter Typ: {type(result)}\n\n"

    # Zusammenfassung
    output += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    output += f"Gelesen: {success_count}/{len(path_list)} Dateien"
    if success_count > 0:
        output += f" (~{total_lines} Zeilen gesamt)"

    return ToolResult(
        success=success_count > 0,
        data=output
    )


async def _safe_read_file(read_func, **kwargs) -> ToolResult:
    """Wrapper für sichere Ausführung von read_file."""
    try:
        return await read_func(**kwargs)
    except Exception as e:
        logger.warning(f"[batch_read_files] Fehler beim Lesen: {e}")
        return ToolResult(success=False, error=str(e))


async def batch_write_files(
    files: str,
    **kwargs
) -> ToolResult:
    """
    Schreibt mehrere Dateien in einem Aufruf mit EINER Bestätigung.

    Ersetzt: write_file × N (mit N einzelnen Bestätigungen)

    Args:
        files: JSON-Array von Objekten mit 'path' und 'content'.
               Format: [{"path": "src/file1.py", "content": "..."}, ...]
               ODER: Komma-getrennte Pfade wenn alle leer erstellt werden sollen.
    """
    from app.services.file_manager import get_file_manager

    if not files or not files.strip():
        return ToolResult(
            success=False,
            error="Keine Dateien angegeben. Format: [{\"path\": \"...\", \"content\": \"...\"}]"
        )

    # Versuche JSON zu parsen (mit Fallbacks für verschiedene Formate)
    file_list: List[Dict[str, str]] = []
    files_clean = files.strip()

    # Mehrere Parse-Versuche mit zunehmender Bereinigung
    parse_attempts = [
        files_clean,  # Original
        files_clean.replace('\\"', '"'),  # Escaped quotes
        files_clean.replace("'", '"'),  # Single quotes zu double quotes
    ]

    parsed = None
    last_error = None

    for attempt in parse_attempts:
        try:
            parsed = json.loads(attempt)
            break
        except json.JSONDecodeError as e:
            last_error = e
            continue

    if parsed is None:
        # Letzter Versuch: Suche nach JSON-Array im String
        json_match = re.search(r'\[.*\]', files_clean, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

    if parsed is None:
        return ToolResult(
            success=False,
            error=f"Ungültiges JSON: {last_error}. Format: [{{\"path\": \"...\", \"content\": \"...\"}}]"
        )

    if isinstance(parsed, list):
        file_list = parsed
    elif isinstance(parsed, dict) and "path" in parsed:
        # Einzelnes Objekt statt Array
        file_list = [parsed]
    else:
        return ToolResult(
            success=False,
            error="files muss ein JSON-Array sein: [{\"path\": \"...\", \"content\": \"...\"}]"
        )

    # Validierung
    if not file_list:
        return ToolResult(success=False, error="Leere Datei-Liste")

    max_files = 20
    if len(file_list) > max_files:
        return ToolResult(
            success=False,
            error=f"Maximal {max_files} Dateien pro Batch (angefordert: {len(file_list)})"
        )

    # Struktur validieren
    for i, f in enumerate(file_list):
        if not isinstance(f, dict):
            return ToolResult(success=False, error=f"Element {i} ist kein Objekt")
        if "path" not in f:
            return ToolResult(success=False, error=f"Element {i} fehlt 'path'")
        if "content" not in f:
            return ToolResult(success=False, error=f"Element {i} fehlt 'content'")

    logger.info(f"[batch_write_files] Bereite {len(file_list)} Dateien vor...")

    # Previews generieren
    manager = get_file_manager()
    previews = []
    combined_diff = []
    errors = []

    for file_spec in file_list:
        path = file_spec["path"]
        content = file_spec["content"]
        try:
            preview = await manager.write_file(path, content)
            previews.append({
                "path": path,
                "content": content,
                "is_new": preview.is_new,
                "diff": preview.diff
            })
            # Kombiniertes Diff für Übersicht
            status = "NEU" if preview.is_new else "ÄNDERUNG"
            combined_diff.append(f"━━━ [{status}] {path} ━━━")
            if preview.diff:
                combined_diff.append(preview.diff)
            else:
                combined_diff.append(f"(Neue Datei mit {len(content)} Zeichen)")
            combined_diff.append("")
        except Exception as e:
            errors.append(f"{path}: {e}")
            logger.warning(f"[batch_write_files] Fehler bei {path}: {e}")

    if errors and not previews:
        return ToolResult(
            success=False,
            error=f"Alle Dateien fehlgeschlagen:\n" + "\n".join(errors)
        )

    # Summary
    new_count = sum(1 for p in previews if p["is_new"])
    update_count = len(previews) - new_count

    summary = f"=== Batch-Write: {len(previews)} Dateien ===\n"
    summary += f"Neu: {new_count} | Updates: {update_count}\n"
    if errors:
        summary += f"Fehler: {len(errors)}\n"
    summary += "\n" + "\n".join(combined_diff)

    return ToolResult(
        success=True,
        requires_confirmation=True,
        data=summary,
        confirmation_data={
            "operation": "batch_write_files",
            "files": previews,
            "file_count": len(previews),
            "new_count": new_count,
            "update_count": update_count,
            "errors": errors
        }
    )


def register_meta_tools(registry: ToolRegistry) -> int:
    """
    Registriert alle Meta-Tools im Tool-Registry.

    Meta-Tools sind kombinierte Tools die mehrere Operationen
    in einem Aufruf ausführen und so Tool-Aufrufe sparen.

    Returns:
        Anzahl der registrierten Tools
    """
    count = 0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Combined Search - Parallele Suche in mehreren Quellen
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    registry.register(Tool(
        name="combined_search",
        description=(
            "🔍 EFFIZIENZ-TOOL: Durchsucht MEHRERE Quellen PARALLEL in EINEM Aufruf!\n"
            "Ersetzt: search_code + search_handbook + search_skills\n\n"
            "Nutze sources='code,handbook,skills' um alle zu durchsuchen.\n"
            "Mit include_content=true werden gefundene Code-Dateien direkt mitgelesen.\n\n"
            "Beispiel: combined_search(query='getUserById', sources='code,handbook', include_content=true)"
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="query",
                type="string",
                description="Suchbegriff (z.B. Methodenname, Fehlermeldung, Konzept)",
                required=True
            ),
            ToolParameter(
                name="sources",
                type="string",
                description=(
                    "Komma-getrennte Quellen: code, handbook, skills\n"
                    "Default: 'code,handbook'\n"
                    "Für vollständige Suche: 'code,handbook,skills'"
                ),
                required=False,
                default="code,handbook"
            ),
            ToolParameter(
                name="max_per_source",
                type="integer",
                description="Max Ergebnisse pro Quelle (default: 5, max: 20)",
                required=False,
                default=5
            ),
            ToolParameter(
                name="include_content",
                type="boolean",
                description=(
                    "Bei Code-Suche: Gefundene Dateien direkt mitlesen?\n"
                    "Spart zusätzliche read_file Aufrufe! (default: false)"
                ),
                required=False,
                default=False
            ),
            ToolParameter(
                name="language",
                type="string",
                description="Sprache für Code-Suche: all, java, python (default: all)",
                required=False,
                default="all",
                enum=["all", "java", "python", "sql"]
            )
        ],
        handler=combined_search
    ))
    count += 1
    logger.info("[meta_tools] combined_search registriert")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Batch Read Files - Mehrere Dateien parallel lesen
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    registry.register(Tool(
        name="batch_read_files",
        description=(
            "📁 EFFIZIENZ-TOOL: Liest MEHRERE Dateien in EINEM Aufruf!\n"
            "Ersetzt: read_file × N\n\n"
            "Pfade komma-getrennt angeben (max 10 Dateien).\n"
            "Ideal für: Stacktrace-Analyse, Code-Review, Vergleiche.\n\n"
            "Beispiel: batch_read_files(paths='UserService.java, UserRepository.java, User.java')"
        ),
        category=ToolCategory.FILE,
        parameters=[
            ToolParameter(
                name="paths",
                type="string",
                description=(
                    "Komma- oder semikolon-getrennte Dateipfade.\n"
                    "Max 10 Dateien pro Aufruf.\n"
                    "Beispiel: 'src/User.java, src/UserService.java'"
                ),
                required=True
            ),
            ToolParameter(
                name="show_line_numbers",
                type="boolean",
                description="Zeilennummern anzeigen (default: true)",
                required=False,
                default=True
            ),
            ToolParameter(
                name="max_lines_per_file",
                type="integer",
                description=(
                    "Max Zeilen pro Datei (default: 100, 0 = unbegrenzt).\n"
                    "Für große Dateien empfohlen!"
                ),
                required=False,
                default=100
            ),
            ToolParameter(
                name="encoding",
                type="string",
                description="Datei-Encoding (default: utf-8)",
                required=False,
                default="utf-8"
            )
        ],
        handler=batch_read_files
    ))
    count += 1
    logger.info("[meta_tools] batch_read_files registriert")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Batch Write Files - Mehrere Dateien mit EINER Bestätigung schreiben
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    registry.register(Tool(
        name="batch_write_files",
        description=(
            "📝 EFFIZIENZ-TOOL: Schreibt MEHRERE Dateien mit EINER Bestätigung!\n"
            "Ersetzt: write_file × N (ohne N einzelne Bestätigungen)\n\n"
            "WICHTIG: Nutze dieses Tool wenn du mehrere Dateien erstellen/ändern musst!\n"
            "Der User bestätigt EINMAL für alle Dateien.\n\n"
            "Format: JSON-Array mit path und content pro Datei.\n"
            "Beispiel: batch_write_files(files='[{\"path\": \"src/User.java\", \"content\": \"...\"}]')"
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="files",
                type="string",
                description=(
                    "JSON-Array von Dateien. Format:\n"
                    "[{\"path\": \"pfad/datei.ext\", \"content\": \"Inhalt...\"}, ...]\n\n"
                    "Max 20 Dateien pro Batch.\n"
                    "Beispiel: '[{\"path\": \"src/A.java\", \"content\": \"class A {}\"}, "
                    "{\"path\": \"src/B.java\", \"content\": \"class B {}\"}]'"
                ),
                required=True
            )
        ],
        handler=batch_write_files
    ))
    count += 1
    logger.info("[meta_tools] batch_write_files registriert")

    return count
