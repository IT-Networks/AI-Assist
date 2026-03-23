"""
Script Tools - Tools für Python-Script-Generierung und -Ausführung.

Ermöglicht dem AI-Agent:
- Python-Scripte zu generieren und zu speichern
- Scripte sicher auszuführen (nach User-Bestätigung)
- Verfügbare Scripte aufzulisten
"""

import logging
from typing import Any, Dict, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolRegistry, ToolResult
from app.services.script_manager import (
    ExecutionResult,
    ScriptManager,
    ScriptNotFoundError,
    ScriptSecurityError,
    ValidationResult,
    get_script_manager,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tool Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def handle_generate_script(
    code: str,
    name: str,
    description: str,
    parameters: Dict[str, str] = None,
    **kwargs
) -> ToolResult:
    """
    Handler für generate_python_script Tool.

    Validiert und speichert ein Python-Script.
    Gibt das Script zur Bestätigung an den User zurück.
    """
    try:
        manager = get_script_manager()

        # Validieren und speichern
        script, validation = await manager.generate_and_save(
            code=code,
            name=name,
            description=description,
            parameters=parameters
        )

        # Warnungen formatieren
        warnings_text = ""
        if validation.warnings:
            warnings_text = "\n⚠️ Warnungen:\n" + "\n".join(f"  - {w}" for w in validation.warnings)

        result_data = {
            "script_id": script.id,
            "name": script.name,
            "description": script.description,
            "file_path": script.file_path,
            "code": code,
            "imports_used": validation.imports_used,
            "warnings": validation.warnings
        }

        return ToolResult(
            success=True,
            data=f"""Script '{name}' erfolgreich erstellt und gespeichert.

📝 Script-ID: {script.id}
📁 Pfad: {script.file_path}
📦 Verwendete Imports: {', '.join(validation.imports_used) if validation.imports_used else 'keine'}
{warnings_text}

Das Script ist bereit zur Ausführung. Verwende `execute_python_script` mit script_id="{script.id}" um es auszuführen.""",
            requires_confirmation=False,  # Speichern braucht keine Bestätigung
            confirmation_data=result_data
        )

    except ScriptSecurityError as e:
        return ToolResult(
            success=False,
            error=f"Script-Validierung fehlgeschlagen:\n" + "\n".join(f"❌ {err}" for err in e.errors)
        )
    except Exception as e:
        logger.error(f"Script-Generierung fehlgeschlagen: {e}")
        return ToolResult(success=False, error=str(e))


async def handle_execute_script(
    script_id: str,
    args: Dict[str, Any] = None,
    input_data: str = None,
    **kwargs
) -> ToolResult:
    """
    Handler für execute_python_script Tool.

    Führt ein gespeichertes Script aus.
    Erfordert User-Bestätigung.
    """
    try:
        manager = get_script_manager()

        # Script laden für Preview
        script = manager.get_script(script_id)
        if not script:
            return ToolResult(
                success=False,
                error=f"Script '{script_id}' nicht gefunden. Verwende list_python_scripts um verfügbare Scripte anzuzeigen."
            )

        # Bestätigungsdaten vorbereiten
        confirmation_data = {
            "operation": "execute_script",
            "script_id": script_id,
            "script_name": script.name,
            "script_description": script.description,
            "code": script.code,
            "args": args or {},
            "input_data": input_data,
            "file_path": script.file_path
        }

        return ToolResult(
            success=True,
            data=f"Script '{script.name}' bereit zur Ausführung. Warte auf Bestätigung.",
            requires_confirmation=True,
            confirmation_data=confirmation_data
        )

    except Exception as e:
        logger.error(f"Script-Ausführung fehlgeschlagen: {e}")
        return ToolResult(success=False, error=str(e))


async def execute_script_after_confirmation(
    script_id: str,
    args: Dict[str, Any] = None,
    input_data: str = None
) -> ToolResult:
    """
    Führt ein Script nach User-Bestätigung aus.

    Diese Funktion wird vom Orchestrator aufgerufen, nachdem
    der User die Ausführung bestätigt hat.
    """
    try:
        manager = get_script_manager()
        result = await manager.execute(script_id, args, input_data)

        if result.success:
            output_text = f"""✅ Script erfolgreich ausgeführt in {result.execution_time_ms}ms

📤 Output:
{result.stdout if result.stdout else '(keine Ausgabe)'}"""

            if result.stderr:
                output_text += f"\n\n⚠️ Stderr:\n{result.stderr}"

            return ToolResult(success=True, data=output_text)
        else:
            return ToolResult(
                success=False,
                error=f"Script-Ausführung fehlgeschlagen:\n{result.error or result.stderr}"
            )

    except ScriptNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Script-Ausführung fehlgeschlagen: {e}")
        return ToolResult(success=False, error=str(e))


async def handle_list_scripts(
    filter: str = None,
    **kwargs
) -> ToolResult:
    """
    Handler für list_python_scripts Tool.

    Listet alle verfügbaren Scripte auf.
    """
    try:
        manager = get_script_manager()
        scripts = manager.list_scripts(filter)

        if not scripts:
            return ToolResult(
                success=True,
                data="Keine Scripte gefunden." + (f" (Filter: '{filter}')" if filter else "")
            )

        # Formatierte Liste
        lines = ["📜 Verfügbare Python-Scripte:\n"]
        for s in scripts:
            exec_info = f", {s.execution_count}x ausgeführt" if s.execution_count else ""
            lines.append(f"  • [{s.id}] {s.name}{exec_info}")
            if s.description:
                lines.append(f"    {s.description[:80]}{'...' if len(s.description) > 80 else ''}")

        lines.append(f"\n{len(scripts)} Script(s) gefunden.")

        return ToolResult(success=True, data="\n".join(lines))

    except Exception as e:
        logger.error(f"Script-Liste fehlgeschlagen: {e}")
        return ToolResult(success=False, error=str(e))


async def handle_validate_script(
    code: str,
    **kwargs
) -> ToolResult:
    """
    Handler für validate_python_script Tool.

    Validiert Code ohne zu speichern.
    """
    try:
        manager = get_script_manager()
        validation = manager.validate_code(code)

        if validation.is_safe:
            result = "✅ Script-Validierung erfolgreich.\n"
            if validation.imports_used:
                result += f"\n📦 Imports: {', '.join(validation.imports_used)}"
            if validation.warnings:
                result += "\n\n⚠️ Warnungen:\n" + "\n".join(f"  - {w}" for w in validation.warnings)
            return ToolResult(success=True, data=result)
        else:
            return ToolResult(
                success=False,
                error="❌ Script-Validierung fehlgeschlagen:\n" +
                      "\n".join(f"  - {e}" for e in validation.errors)
            )

    except Exception as e:
        logger.error(f"Script-Validierung fehlgeschlagen: {e}")
        return ToolResult(success=False, error=str(e))


async def handle_delete_script(
    script_id: str,
    **kwargs
) -> ToolResult:
    """
    Handler für delete_python_script Tool.

    Löscht ein gespeichertes Script.
    """
    try:
        manager = get_script_manager()
        if manager.delete_script(script_id):
            return ToolResult(success=True, data=f"Script '{script_id}' gelöscht.")
        else:
            return ToolResult(success=False, error=f"Script '{script_id}' nicht gefunden.")

    except Exception as e:
        logger.error(f"Script-Löschung fehlgeschlagen: {e}")
        return ToolResult(success=False, error=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Tool Definitions
# ══════════════════════════════════════════════════════════════════════════════

generate_script_tool = Tool(
    name="generate_python_script",
    description="""Erstellt und speichert ein Python-Script für komplexe Aufgaben.

WANN VERWENDEN:
✅ Komplexe Datentransformationen (CSV→JSON, XML-Parsing)
✅ Batch-Operationen auf vielen Dateien
✅ Mathematische/statistische Berechnungen
✅ Datenanalyse mit pandas/numpy
✅ Wiederverwendbare Automatisierungen

WANN NICHT VERWENDEN:
❌ Einfaches Datei-Lesen → read_file
❌ Einfache Textsuche → grep/search_code
❌ Shell-Befehle → execute_command
❌ Einmalige einfache Operationen

Das Script wird validiert und sicher gespeichert.
Gefährliche Imports (subprocess, os.system, etc.) sind nicht erlaubt.

Verfügbare Imports: json, csv, pathlib, re, datetime, collections,
itertools, functools, math, statistics, pandas, numpy, yaml

Das Script kann über SCRIPT_ARGS auf übergebene Argumente zugreifen.""",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter(
            name="code",
            type="string",
            description="Python-Quellcode des Scripts",
            required=True
        ),
        ToolParameter(
            name="name",
            type="string",
            description="Kurzer Name für das Script (z.B. 'csv_to_json_converter')",
            required=True
        ),
        ToolParameter(
            name="description",
            type="string",
            description="Beschreibung was das Script macht",
            required=True
        ),
        ToolParameter(
            name="parameters",
            type="object",
            description="Parameter-Definitionen als {name: beschreibung} für Dokumentation",
            required=False
        ),
    ],
    is_write_operation=False,  # Speichern ist sicher, keine Bestätigung nötig
    handler=handle_generate_script
)

execute_script_tool = Tool(
    name="execute_python_script",
    description="""Führt ein gespeichertes Python-Script aus.

Das Script wird in einer isolierten Umgebung ausgeführt.
ERFORDERT USER-BESTÄTIGUNG vor der Ausführung.

Argumente werden dem Script als SCRIPT_ARGS Dictionary zur Verfügung gestellt.
Beispiel im Script: input_file = SCRIPT_ARGS.get('input_file', 'default.csv')""",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter(
            name="script_id",
            type="string",
            description="ID des Scripts (aus generate_python_script oder list_python_scripts)",
            required=True
        ),
        ToolParameter(
            name="args",
            type="object",
            description="Argumente für das Script als {key: value}",
            required=False
        ),
        ToolParameter(
            name="input_data",
            type="string",
            description="Optionale Eingabedaten (wird an stdin übergeben)",
            required=False
        ),
    ],
    is_write_operation=True,  # Ausführung erfordert Bestätigung
    handler=handle_execute_script
)

list_scripts_tool = Tool(
    name="list_python_scripts",
    description="Listet alle verfügbaren Python-Scripte auf.",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter(
            name="filter",
            type="string",
            description="Optional: Filtertext für Name/Beschreibung",
            required=False
        ),
    ],
    is_write_operation=False,
    handler=handle_list_scripts
)

validate_script_tool = Tool(
    name="validate_python_script",
    description="""Validiert Python-Code ohne zu speichern.

Prüft:
- Syntax-Korrektheit
- Erlaubte Imports
- Gefährliche Patterns

Nützlich um Code vor dem Speichern zu testen.""",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter(
            name="code",
            type="string",
            description="Python-Code zur Validierung",
            required=True
        ),
    ],
    is_write_operation=False,
    handler=handle_validate_script
)

delete_script_tool = Tool(
    name="delete_python_script",
    description="Löscht ein gespeichertes Python-Script.",
    category=ToolCategory.ANALYSIS,
    parameters=[
        ToolParameter(
            name="script_id",
            type="string",
            description="ID des zu löschenden Scripts",
            required=True
        ),
    ],
    is_write_operation=True,
    handler=handle_delete_script
)


# ══════════════════════════════════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════════════════════════════════

def register_script_tools(registry: ToolRegistry):
    """Registriert alle Script-Tools."""
    from app.core.config import settings

    if not settings.script_execution.enabled:
        logger.info("Script-Execution deaktiviert - Tools nicht registriert")
        return

    registry.register(generate_script_tool)
    registry.register(execute_script_tool)
    registry.register(list_scripts_tool)
    registry.register(validate_script_tool)
    registry.register(delete_script_tool)

    logger.info("Script-Tools registriert (5 Tools)")
