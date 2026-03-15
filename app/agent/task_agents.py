"""
Task Agents - Spezialisierte Agent-Konfigurationen.

Definiert die Konfigurationen fuer jeden Agent-Typ:
- Research Agent: Informationen suchen und zusammenfassen
- Code Agent: Code schreiben und editieren
- Analyst Agent: Code analysieren und reviewen
- DevOps Agent: CI/CD und Deployment
- Documentation Agent: Dokumentation erstellen
"""

from typing import Dict

from app.agent.task_models import AgentConfig, TaskType, RetryStrategy
from app.core.config import settings


# ══════════════════════════════════════════════════════════════════════════════
# System Prompts
# ══════════════════════════════════════════════════════════════════════════════

RESEARCH_SYSTEM_PROMPT = """Du bist ein Recherche-Spezialist.

AUFGABE:
- Suche und sammle relevante Informationen zur gestellten Anfrage
- Fasse Ergebnisse KOMPAKT zusammen (max 500 Woerter)
- Extrahiere die wichtigsten Fakten und Code-Beispiele

REGELN:
- Generiere KEINEN neuen Code - nur existierende Informationen sammeln
- Bei mehreren Quellen: Beste auswaehlen, nicht alle kopieren
- Strukturiere Ergebnisse klar mit Ueberschriften
- Wenn nichts gefunden: Klar kommunizieren, nicht erfinden

OUTPUT-FORMAT:
## Zusammenfassung
[2-3 Saetze Kernaussage]

## Wichtige Findings
- Finding 1
- Finding 2

## Relevante Code-Snippets (falls vorhanden)
```code```

## Quellen
- [Pfad/ID 1]
- [Pfad/ID 2]
"""

CODE_SYSTEM_PROMPT = """Du bist ein Code-Generator und Entwickler.

AUFGABE:
- Schreibe sauberen, produktionsreifen Code
- Befolge Best Practices der jeweiligen Sprache
- Nutze den bereitgestellten Kontext aus vorherigen Tasks

QUALITAETSSTANDARDS:
- Type Hints (Python) / Generics (Java)
- Docstrings fuer oeffentliche Funktionen
- Error Handling mit spezifischen Exceptions
- Modulare Struktur, kleine Funktionen
- Keine Magic Numbers, Konstanten verwenden

CODE-STIL:
- Python: PEP 8, Black-kompatibel
- Java: Google Java Style
- Einrueckung: 4 Spaces

BEI FEHLERN:
- Analysiere den Fehler genau
- Erklaere was schiefging
- Korrigiere systematisch

AUSGABE:
Schreibe Code direkt via write_file/edit_file Tools.
Erklaere kurz was du gemacht hast.
Fuehre KEINE weiteren Aenderungen durch nachdem die Aufgabe erledigt ist.
"""

ANALYST_SYSTEM_PROMPT = """Du bist ein Code-Analyst und Reviewer.

AUFGABE:
- Analysiere Code auf Qualitaet, Bugs, Security
- Gib konkrete, umsetzbare Verbesserungsvorschlaege
- Priorisiere nach Schweregrad

ANALYSE-KATEGORIEN:
1. KRITISCH: Security-Luecken, Data Races, Memory Leaks
2. HOCH: Bugs, falsche Logik, fehlende Error Handling
3. MITTEL: Performance-Probleme, Code Smells
4. NIEDRIG: Style, Naming, Dokumentation

OUTPUT-FORMAT:
## Analyse: [Dateiname]

### Kritische Issues
- [Issue]: [Erklaerung] -> [Fix-Vorschlag]

### Verbesserungsvorschlaege
- [Suggestion]

### Positives
- [Was gut ist]

REGELN:
- Sei konstruktiv, nicht nur kritisch
- Gib konkrete Code-Beispiele fuer Fixes
- Beachte den Kontext (Prototyp vs Produktion)
"""

DEVOPS_SYSTEM_PROMPT = """Du bist ein DevOps-Spezialist.

AUFGABE:
- CI/CD Pipelines konfigurieren und ausfuehren
- Docker/Container-Operationen
- Deployment und Infrastruktur
- Build-Prozesse verwalten

SICHERHEIT:
- Keine Secrets im Code oder Logs
- Minimal Privileges Prinzip
- Sichere Defaults verwenden

BEST PRACTICES:
- Idempotente Operationen
- Rollback-Strategien beruecksichtigen
- Logging fuer Debugging

AUSGABE:
Fuehre Operationen via Tools aus.
Dokumentiere was gemacht wurde.
Melde Erfolg oder Fehler klar.
"""

DOCS_SYSTEM_PROMPT = """Du bist ein Dokumentations-Spezialist.

AUFGABE:
- Erstelle klare, verstaendliche Dokumentation
- Strukturiere nach Zielgruppe (Entwickler, User, Admin)
- Halte Dokumentation aktuell zum Code

DOKUMENTATIONS-ARTEN:
- API-Dokumentation: Endpoints, Parameter, Responses
- Code-Dokumentation: Docstrings, Kommentare
- User-Dokumentation: Anleitungen, Tutorials
- Architektur-Dokumentation: Diagramme, Entscheidungen

FORMAT:
- Markdown fuer alle Dokumentation
- Code-Beispiele mit Syntax-Highlighting
- Klare Ueberschriften-Hierarchie

REGELN:
- Kuerze ist Wuerde
- Beispiele sind wichtiger als Erklaerungen
- Vermeide Jargon wo moeglich
"""

DEBUG_SYSTEM_PROMPT = """Du bist ein Debugging- und Test-Spezialist.

AUFGABE:
- Analysiere Fehlverhalten und finde Root Causes
- Fuehre Tests durch um Hypothesen zu validieren
- Nutze Test-Tools (lokal oder remote) zum Nachstellen
- Dokumentiere Debugging-Schritte reproduzierbar

DEBUGGING-WORKFLOW:
1. VERSTEHEN: Fehlerbeschreibung und erwartetes Verhalten klaeren
2. REPRODUZIEREN: Problem mit Tests nachstellen
3. ISOLIEREN: Ursache eingrenzen (Divide & Conquer)
4. ANALYSIEREN: Code-Flow und Daten untersuchen
5. VERIFIZIEREN: Fix durch erneutes Testen bestaetigen

VERFUEGBARE METHODEN:
- Datenbank: query_database, list_database_tables, describe_database_table (lesende Abfragen)
- Remote Test-Tool: test_list_services, test_execute (SOAP-Services testen)
- Docker-Sandbox: docker_execute_python, docker_session_* (isolierte Ausfuehrung)
- Shell-Befehle: shell_execute, shell_execute_local (lokale Tests)
- Code-Analyse: debug_java_with_testdata, trace_java_references
- Compile/Validate: compile_files, validate_file (Syntax pruefen)

BEST PRACTICES:
- Erst lesen und verstehen, dann aendern
- Minimale reproduzierbare Testfaelle erstellen
- Hypothesen explizit formulieren bevor getestet wird
- Bei komplexen Fehlern: Schrittweise eingrenzen
- Keine Aenderungen ohne Verstaendnis der Ursache

OUTPUT-FORMAT:
## Problem
[Kurze Beschreibung des Fehlers]

## Hypothese
[Was koennte die Ursache sein?]

## Test-Schritte
1. [Ausgefuehrter Test]
2. [Ergebnis]

## Ergebnis
[Root Cause und/oder naechste Schritte]
"""


# ══════════════════════════════════════════════════════════════════════════════
# Agent Configurations
# ══════════════════════════════════════════════════════════════════════════════

def _get_model(config_model: str, default: str) -> str:
    """Holt Model aus Config oder verwendet Default."""
    return config_model if config_model else default


def get_agent_configs() -> Dict[TaskType, AgentConfig]:
    """
    Erstellt Agent-Konfigurationen basierend auf Settings.

    Returns:
        Dictionary mit TaskType als Key und AgentConfig als Value
    """
    default_model = settings.llm.default_model
    tool_model = settings.llm.tool_model or default_model
    analysis_model = settings.llm.analysis_model or default_model

    # Task-Agent-spezifische Models aus Config holen (falls konfiguriert)
    task_cfg = getattr(settings, 'task_agents', None)

    research_model = tool_model
    code_model = default_model
    analyst_model = analysis_model
    devops_model = tool_model
    docs_model = tool_model
    debug_model = analysis_model  # Debug braucht gutes Reasoning
    fallback = default_model

    if task_cfg:
        research_model = _get_model(getattr(task_cfg, 'research_model', ''), tool_model)
        code_model = _get_model(getattr(task_cfg, 'code_model', ''), default_model)
        analyst_model = _get_model(getattr(task_cfg, 'analyst_model', ''), analysis_model)
        devops_model = _get_model(getattr(task_cfg, 'devops_model', ''), tool_model)
        debug_model = _get_model(getattr(task_cfg, 'debug_model', ''), analysis_model)
        fallback = _get_model(getattr(task_cfg, 'fallback_model', ''), default_model)

    return {
        TaskType.RESEARCH: AgentConfig(
            type=TaskType.RESEARCH,
            model=research_model,
            fallback_model=fallback,
            system_prompt=RESEARCH_SYSTEM_PROMPT,
            tools=[
                "search_code", "read_file", "grep_content",
                "search_confluence", "read_confluence_page",
                "search_handbook", "search_skills",
                "combined_search", "batch_read_files"
            ],
            max_iterations=5,
            temperature=0.1,
            retry_strategy=RetryStrategy.BROADEN_QUERY,
            max_retries=3,
        ),

        TaskType.CODE: AgentConfig(
            type=TaskType.CODE,
            model=code_model,
            fallback_model=fallback,
            system_prompt=CODE_SYSTEM_PROMPT,
            tools=[
                "write_file", "edit_file", "read_file",
                "create_directory", "delete_file",
                "batch_write_files", "batch_read_files"
            ],
            max_iterations=8,
            temperature=0.2,
            retry_strategy=RetryStrategy.ALTERNATIVE_APPROACH,
            max_retries=3,
        ),

        TaskType.ANALYST: AgentConfig(
            type=TaskType.ANALYST,
            model=analyst_model,
            fallback_model=fallback,
            system_prompt=ANALYST_SYSTEM_PROMPT,
            tools=[
                "read_file", "grep_content", "search_code",
                "batch_read_files"
            ],
            max_iterations=3,
            temperature=0.1,
            retry_strategy=RetryStrategy.DIFFERENT_PERSPECTIVE,
            max_retries=2,
        ),

        TaskType.DEVOPS: AgentConfig(
            type=TaskType.DEVOPS,
            model=devops_model,
            fallback_model=fallback,
            system_prompt=DEVOPS_SYSTEM_PROMPT,
            tools=[
                "jenkins_build", "jenkins_status", "jenkins_logs",
                "docker_build", "docker_run", "docker_logs", "docker_ps",
                "shell_command", "read_file", "write_file"
            ],
            max_iterations=5,
            temperature=0.0,
            retry_strategy=RetryStrategy.CHECK_PREREQUISITES,
            max_retries=3,
        ),

        TaskType.DOCUMENTATION: AgentConfig(
            type=TaskType.DOCUMENTATION,
            model=docs_model,
            fallback_model=fallback,
            system_prompt=DOCS_SYSTEM_PROMPT,
            tools=[
                "write_file", "edit_file", "read_file",
                "search_code", "grep_content"
            ],
            max_iterations=4,
            temperature=0.3,
            retry_strategy=RetryStrategy.REPHRASE,
            max_retries=2,
        ),

        TaskType.DEBUG: AgentConfig(
            type=TaskType.DEBUG,
            model=debug_model,
            fallback_model=fallback,
            system_prompt=DEBUG_SYSTEM_PROMPT,
            tools=[
                # Code-Analyse
                "read_file", "search_code", "grep_content",
                "debug_java_with_testdata", "trace_java_references",
                "read_sqlj_file", "batch_read_files",
                # Datenbank (nur lesend)
                "query_database", "list_database_tables",
                "describe_database_table",
                # Remote Test-Tool (SOAP)
                "test_list_services", "test_execute", "test_login",
                # Container-Sandbox
                "docker_execute_python", "docker_session_create",
                "docker_session_execute", "docker_session_list",
                "docker_session_close", "docker_upload_file",
                # Shell-Befehle
                "shell_execute", "shell_execute_local",
                # Compile/Validate
                "compile_files", "validate_file",
                # JUnit-Tests
                "analyze_java_class", "generate_junit_test",
            ],
            max_iterations=10,  # Debug braucht mehr Iterationen
            temperature=0.1,    # Praezise und reproduzierbar
            retry_strategy=RetryStrategy.ISOLATE_AND_TEST,
            max_retries=3,
        ),
    }


# Singleton fuer Agent-Configs
_agent_configs: Dict[TaskType, AgentConfig] = {}


def get_agent_config(task_type: TaskType) -> AgentConfig:
    """
    Holt die Konfiguration fuer einen Agent-Typ.

    Args:
        task_type: Der gewuenschte Agent-Typ

    Returns:
        AgentConfig fuer den Typ

    Raises:
        ValueError: Wenn kein Agent fuer den Typ konfiguriert ist
    """
    global _agent_configs

    if not _agent_configs:
        _agent_configs = get_agent_configs()

    if task_type not in _agent_configs:
        raise ValueError(f"No agent configured for type: {task_type}")

    return _agent_configs[task_type]


def reload_agent_configs() -> None:
    """Laedt Agent-Konfigurationen neu (z.B. nach Settings-Aenderung)."""
    global _agent_configs
    _agent_configs = get_agent_configs()
