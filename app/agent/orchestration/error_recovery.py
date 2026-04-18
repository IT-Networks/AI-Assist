"""
Error Recovery System for Tool Calling.

Provides pattern-based error analysis and actionable recovery hints for failed tool calls.
Matches error messages to known patterns and suggests fixes + alternative tools.

Usage:
    hint = get_recovery_hint("read_file", "No such file or directory", available_tools)
    # → "Datei nicht gefunden. Überprüfe den Pfad mit list_files()..."
"""

import re
from typing import Dict, List, Optional, Any


# Error pattern database mapping common errors to recovery guidance
ERROR_RECOVERY_MAP: Dict[str, Dict[str, Any]] = {
    # ─── Missing argument patterns ───────────────────────────────────
    "missing required argument 'path'": {
        "hint": "Parameter 'path' fehlt. Gib den vollständigen Dateipfad an.",
        "example": "read_file(path='/src/main/java/MyClass.java')",
        "alternatives": ["list_files", "search_code", "glob_files"],
        "priority": 100,
    },
    "missing required argument 'query'": {
        "hint": "Parameter 'query' fehlt. Gib einen Suchbegriff an.",
        "example": "search_code(query='MySearchTerm')",
        "alternatives": ["combined_search", "grep_code"],
        "priority": 100,
    },
    "missing required argument 'content'": {
        "hint": "Parameter 'content' fehlt. Gib den Dateiinhalt an.",
        "example": "write_file(path='/path/to/file.py', content='...')",
        "alternatives": ["edit_file", "batch_write_files"],
        "priority": 100,
    },
    "missing required argument": {
        "hint": "Ein erforderlicher Parameter fehlt. Überprüfe die Tool-Signatur.",
        "alternatives": [],
        "priority": 80,
    },

    # ─── File not found / path errors ────────────────────────────────
    "no such file or directory": {
        "hint": "Datei existiert nicht oder Pfad ist falsch. Überprüfe den Dateipfad.",
        "solution": [
            "1. Verwende list_files(dir='/path/') um Verzeichnis zu durchsuchen",
            "2. Oder search_code(query='filename') um Datei zu finden",
            "3. Dann erneut mit korrektem Pfad aufrufen",
        ],
        "alternatives": ["list_files", "glob_files", "search_code"],
        "priority": 100,
    },
    "file not found": {
        "hint": "Datei nicht gefunden. Prüfe den Dateipfad.",
        "alternatives": ["list_files", "glob_files", "search_code"],
        "priority": 95,
    },
    "cannot find file": {
        "hint": "Datei konnte nicht gefunden werden. Überprüfe den Pfad.",
        "alternatives": ["list_files", "search_code"],
        "priority": 90,
    },

    # ─── Permission / mode errors ────────────────────────────────────
    "permission denied": {
        "hint": "Schreibzugriff verweigert. Möglicherweise bist du im READ-ONLY Modus oder der Pfad ist schreibgeschützt.",
        "solution": [
            "1. Prüfe AgentMode: ist dieser READ-ONLY?",
            "2. Prüfe Dateiberechtigungen: chmod +w /path/to/file",
            "3. Versuche in einem beschreibbaren Verzeichnis zu schreiben",
        ],
        "alternatives": ["read_file", "search_code"],
        "priority": 95,
    },
    "access denied": {
        "hint": "Zugriff verweigert. Möglicherweise Berechtigungsproblem.",
        "alternatives": [],
        "priority": 85,
    },
    "insufficient permissions": {
        "hint": "Unzureichende Berechtigungen. Prüfe Dateizugriff.",
        "alternatives": [],
        "priority": 85,
    },

    # ─── Tool not available ──────────────────────────────────────────
    "tool not available": {
        "hint": "Tool nicht verfügbar im aktuellen Modus. Überprüfe verfügbare Tools.",
        "alternatives": [],
        "priority": 90,
    },
    "tool does not exist": {
        "hint": "Das Tool existiert nicht. Überprüfe den Tool-Namen.",
        "alternatives": [],
        "priority": 90,
    },

    # ─── Connection / network errors ─────────────────────────────────
    "connection refused": {
        "hint": "Service nicht erreichbar (Connection refused). Prüfe ob der Service läuft.",
        "solution": [
            "1. Starten Sie den Service",
            "2. Prüfen Sie Host/Port Konfiguration",
            "3. Versuchen Sie später erneut",
        ],
        "alternatives": [],
        "priority": 85,
    },
    "connection timeout": {
        "hint": "Verbindungs-Timeout. Service antwortet nicht rechtzeitig.",
        "solution": [
            "1. Prüfe Netzwerk-Konnektivität",
            "2. Versuche mit kleineren/spezifischeren Queries",
            "3. Versuche später erneut",
        ],
        "alternatives": ["search_code", "grep_code"],  # smaller/faster alternatives
        "priority": 85,
    },
    "timeout": {
        "hint": "Zeitüberschreitung. Operation hat zu lange gedauert.",
        "solution": [
            "1. Versuche einen kleineren oder spezifischeren Query",
            "2. Teile die Anfrage in mehrere kleinere auf",
            "3. Versuche später erneut",
        ],
        "alternatives": ["search_code", "grep_code"],  # faster alternatives
        "priority": 85,
    },

    # ─── JSON / format errors ────────────────────────────────────────
    "json decode error": {
        "hint": "Ungültiges JSON-Format. Überprüfe die Argument-Struktur.",
        "example": '[TOOL_CALLS][{"name": "tool_name", "arguments": {"key": "value"}}]',
        "solution": [
            "1. Überprüfe JSON-Syntax (alle Klammern korrekt?)",
            "2. Überprüfe Anführungszeichen (einfach vs. doppelt)",
            "3. Nutze ein JSON-Validator Tool",
        ],
        "alternatives": [],
        "priority": 95,
    },
    "invalid json": {
        "hint": "JSON-Format ungültig. Überprüfe Klammern und Anführungszeichen.",
        "alternatives": [],
        "priority": 90,
    },
    "json error": {
        "hint": "Fehler beim JSON-Parsing. Format ungültig.",
        "alternatives": [],
        "priority": 85,
    },

    # ─── Type / argument errors ──────────────────────────────────────
    "type error": {
        "hint": "Typ-Fehler: Ein Argument hat den falschen Typ. Überprüfe Parameter-Typen.",
        "solution": [
            "1. Überprüfe erwartet vs. erhaltener Typ",
            "2. Wandle den Typ um (z.B. String → Integer)",
            "3. Konsultiere Tool-Dokumentation",
        ],
        "alternatives": [],
        "priority": 85,
    },
    "value error": {
        "hint": "Wert-Fehler: Ein Parameter hat einen ungültigen Wert.",
        "solution": [
            "1. Überprüfe gültige Wertebereiche",
            "2. Überprüfe enum-Werte (falls vorhanden)",
            "3. Konsultiere Tool-Dokumentation",
        ],
        "alternatives": [],
        "priority": 80,
    },

    # ─── Generic fallback patterns ───────────────────────────────────
    "error": {
        "hint": "Ein Fehler ist aufgetreten. Überprüfe die Fehlermeldung.",
        "solution": [
            "1. Lese die Fehlermeldung sorgfältig",
            "2. Überprüfe die Parameter und Argumente",
            "3. Versuche das Tool mit anderen Parametern",
        ],
        "alternatives": [],
        "priority": 10,  # Very low priority - only match as fallback
    },
}

# Tool-specific error patterns (exact tool name matching)
TOOL_SPECIFIC_ERRORS: Dict[str, Dict[str, Any]] = {
    "read_file": {
        "file_too_large": {
            "hint": "Datei zu groß zum Lesen. Datei überschreitet Größenlimit.",
            "solution": [
                "1. Versuche batch_read_files() mit mehreren kleineren Dateien",
                "2. Oder search_code() um relevante Teile zu finden",
                "3. Lese nur einen Teil der Datei",
            ],
            "alternatives": ["batch_read_files", "search_code", "grep_code"],
        },
        "encoding_error": {
            "hint": "Encoding-Fehler: Datei nutzt nicht-unterstütztes Encoding.",
            "solution": [
                "1. Überprüfe Datei-Encoding (UTF-8, ASCII, etc.)",
                "2. Versuche mit anderem Encoding zu lesen",
                "3. Konvertiere Datei zu UTF-8",
            ],
            "alternatives": [],
        },
    },
    "search_code": {
        "no_results": {
            "hint": "Keine Ergebnisse gefunden. Suchbegriff passt auf keine Dateien.",
            "solution": [
                "1. Versuche ein generischeres Suchbegriff",
                "2. Überprüfe ob der Code im aktuellen Workspace ist",
                "3. Nutze grep_code() statt search_code()",
            ],
            "alternatives": ["combined_search", "grep_code", "glob_files"],
        },
        "too_many_results": {
            "hint": "Zu viele Ergebnisse. Suchbegriff ist zu allgemein.",
            "solution": [
                "1. Verfeinere den Suchbegriff (spezifischer)",
                "2. Füge Datei-Patterns hinzu (z.B. '*.py')",
                "3. Nutze Regex-Pattern statt einfacher Suche",
            ],
            "alternatives": ["combined_search", "grep_code"],
        },
    },
    "write_file": {
        "already_exists": {
            "hint": "Datei existiert bereits. Verwende edit_file() zum Ändern oder gib anderen Namen an.",
            "solution": [
                "1. Nutze edit_file() um existierende Datei zu ändern",
                "2. Oder erstelle neue Datei mit anderem Namen",
                "3. Oder lösche alte Datei erst",
            ],
            "alternatives": ["edit_file", "batch_write_files"],
        },
        "directory_not_exist": {
            "hint": "Zielverzeichnis existiert nicht. Erstelle Verzeichnis erst.",
            "solution": [
                "1. Verwende mkdir um Verzeichnis zu erstellen",
                "2. Oder wähle existierendes Verzeichnis",
                "3. Überprüfe Dateipfad",
            ],
            "alternatives": ["list_files"],
        },
    },
}


def get_recovery_hint(
    tool_name: str,
    error: str,
    available_tools: Optional[List[str]] = None,
) -> str:
    """
    Get actionable recovery hint for a failed tool call.

    Analyzes error message and matches against known patterns. Returns
    specific recovery guidance including solutions and alternative tools.

    Args:
        tool_name: Name of the tool that failed (e.g., "read_file")
        error: Error message from tool execution
        available_tools: List of currently available tool names (optional)

    Returns:
        Formatted string with recovery guidance, or empty string if no match
    """
    error_lower = error.lower()

    # Check tool-specific errors first
    if tool_name in TOOL_SPECIFIC_ERRORS:
        for error_pattern, error_info in TOOL_SPECIFIC_ERRORS[tool_name].items():
            if error_pattern in error_lower:
                return _format_recovery_message(error_info, available_tools)

    # Check general error patterns (sorted by priority)
    best_match = None
    best_priority = -1

    for pattern, recovery_info in ERROR_RECOVERY_MAP.items():
        priority = recovery_info.get("priority", 50)
        # Case-insensitive pattern matching
        if pattern.lower() in error_lower and priority > best_priority:
            best_match = recovery_info
            best_priority = priority

    if best_match:
        return _format_recovery_message(best_match, available_tools)

    # Fallback: no specific match found
    return ""


def _format_recovery_message(
    recovery_info: Dict[str, Any],
    available_tools: Optional[List[str]] = None,
) -> str:
    """
    Format recovery information into user-facing message.

    Args:
        recovery_info: Dictionary with hint, example, solution, alternatives
        available_tools: List of currently available tools

    Returns:
        Formatted markdown-style message
    """
    parts = []

    # Main hint
    if "hint" in recovery_info:
        parts.append(f"**Recovery:** {recovery_info['hint']}")

    # Concrete solution steps
    if "solution" in recovery_info:
        parts.append("**Konkrete Lösung:**")
        for step in recovery_info["solution"]:
            parts.append(f"  {step}")

    # Example (if provided)
    if "example" in recovery_info:
        parts.append(f"**Beispiel:** `{recovery_info['example']}`")

    # Alternative tools
    alternatives = recovery_info.get("alternatives", [])
    if alternatives:
        # Filter to only available tools if provided
        if available_tools:
            alternatives = [t for t in alternatives if t in available_tools]

        if alternatives:
            parts.append(f"**Verfügbare Alternativen:** {', '.join(alternatives)}")

    return "\n\n".join(parts) if parts else ""


def has_recovery_hint(error: str) -> bool:
    """
    Check if error message matches any known recovery pattern.

    Args:
        error: Error message

    Returns:
        True if a recovery pattern matches, False otherwise
    """
    error_lower = error.lower()

    # Check tool-specific patterns
    for tool_errors in TOOL_SPECIFIC_ERRORS.values():
        for pattern in tool_errors.keys():
            if pattern in error_lower:
                return True

    # Check general patterns
    for pattern in ERROR_RECOVERY_MAP.keys():
        if pattern.lower() in error_lower:
            return True

    return False


def get_alternative_tools(
    tool_name: str,
    available_tools: Optional[List[str]] = None,
) -> List[str]:
    """
    Get alternative tools for a failed tool.

    Useful when a tool consistently fails and we want to suggest alternatives.

    Args:
        tool_name: Name of the tool that failed
        available_tools: List of currently available tools (optional)

    Returns:
        List of tool names that could be alternatives
    """
    alternatives = set()

    # Collect from tool-specific alternatives
    if tool_name in TOOL_SPECIFIC_ERRORS:
        for error_info in TOOL_SPECIFIC_ERRORS[tool_name].values():
            alternatives.update(error_info.get("alternatives", []))

    # Filter to available tools if provided
    if available_tools:
        alternatives = {t for t in alternatives if t in available_tools}

    return sorted(list(alternatives))
