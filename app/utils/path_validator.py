"""
Path-Validierung zur Verhinderung von Path Traversal-Angriffen.

Verwendet zur Absicherung von Benutzer-Pfad-Eingaben in:
- maven.py (pom_path)
- testtool.py (local_script)
- wlp.py (wlp_path, server_name)
"""

import os
import re
from pathlib import Path
from typing import Optional, Tuple


def validate_path_within_base(
    user_path: str,
    base_path: str,
    allow_absolute: bool = False
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validiert, dass ein Pfad innerhalb eines Basis-Verzeichnisses liegt.

    Args:
        user_path: Der vom Benutzer eingegebene Pfad
        base_path: Das erlaubte Basis-Verzeichnis
        allow_absolute: Ob absolute Pfade erlaubt sind

    Returns:
        Tuple aus (is_valid, resolved_path, error_message)
    """
    if not user_path or not user_path.strip():
        return False, None, "Pfad darf nicht leer sein"

    if not base_path or not base_path.strip():
        return False, None, "Basis-Verzeichnis nicht konfiguriert"

    user_path = user_path.strip()

    # Null-Byte-Injektion verhindern
    if "\x00" in user_path or "\n" in user_path or "\r" in user_path:
        return False, None, "Ungültiger Pfad: enthält Steuerzeichen"

    # Verdächtige Muster prüfen
    suspicious_patterns = [
        r"\.\.",           # Parent directory traversal
        r"^/",             # Unix absolute path (wenn nicht erlaubt)
        r"^[a-zA-Z]:\\",   # Windows absolute path (wenn nicht erlaubt)
        r"~",              # Home directory expansion
        r"\$\{",           # Variable expansion
        r"%[a-zA-Z]+%",    # Windows environment variables
    ]

    if not allow_absolute:
        for pattern in suspicious_patterns:
            if re.search(pattern, user_path):
                return False, None, f"Ungültiger Pfad: verdächtiges Muster '{pattern}'"

    try:
        base = Path(base_path).resolve()
        if not base.exists():
            return False, None, f"Basis-Verzeichnis existiert nicht: {base_path}"

        # Pfad relativ zum Basis-Verzeichnis auflösen
        if allow_absolute and os.path.isabs(user_path):
            resolved = Path(user_path).resolve()
        else:
            resolved = (base / user_path).resolve()

        # Prüfen ob der aufgelöste Pfad innerhalb der Basis liegt
        try:
            resolved.relative_to(base)
        except ValueError:
            return False, None, f"Pfad liegt außerhalb des erlaubten Verzeichnisses"

        return True, str(resolved), None

    except Exception as e:
        return False, None, f"Pfad-Validierung fehlgeschlagen: {e}"


def validate_identifier(
    value: str,
    max_length: int = 64,
    allow_dots: bool = False,
    allow_hyphens: bool = True,
    allow_underscores: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Validiert einen Identifier (z.B. server_name, build_id).

    Args:
        value: Der zu prüfende Wert
        max_length: Maximale Länge
        allow_dots: Ob Punkte erlaubt sind
        allow_hyphens: Ob Bindestriche erlaubt sind
        allow_underscores: Ob Unterstriche erlaubt sind

    Returns:
        Tuple aus (is_valid, error_message)
    """
    if not value or not value.strip():
        return False, "Identifier darf nicht leer sein"

    value = value.strip()

    if len(value) > max_length:
        return False, f"Identifier zu lang (max {max_length} Zeichen)"

    # Basis-Pattern: alphanumerisch
    allowed_chars = r"a-zA-Z0-9"
    if allow_dots:
        allowed_chars += r"\."
    if allow_hyphens:
        allowed_chars += r"\-"
    if allow_underscores:
        allowed_chars += r"_"

    pattern = f"^[{allowed_chars}]+$"
    if not re.match(pattern, value):
        return False, f"Identifier enthält ungültige Zeichen (erlaubt: {allowed_chars})"

    # Zusätzliche Sicherheit: keine versteckten Dateien
    if value.startswith("."):
        return False, "Identifier darf nicht mit Punkt beginnen"

    return True, None


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Bereinigt einen Dateinamen von unsicheren Zeichen.

    Args:
        filename: Der zu bereinigende Dateiname
        max_length: Maximale Länge

    Returns:
        Bereinigter Dateiname
    """
    if not filename:
        return "unnamed"

    # Entferne Pfad-Komponenten
    filename = os.path.basename(filename)

    # Ersetze unsichere Zeichen
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)

    # Keine versteckten Dateien
    filename = filename.lstrip('.')

    # Länge begrenzen
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext

    return filename or "unnamed"
