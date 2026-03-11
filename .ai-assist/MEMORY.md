# AI-Assist - Gelerntes Wissen

> Dynamisch gepflegt - wird während der Nutzung mit Erkenntnissen gefüllt.
> Max. 200 Zeilen werden in den Kontext geladen.

## [decision] 2026-03-11
**Shell-Execution-Ansatz:** Container-First gewählt.
- Befehle werden erst im Docker-Container getestet
- Lokale Ausführung nur mit expliziter User-Bestätigung
- Git-Befehle komplett blockiert (nutze git_* Tools)

## [decision] 2026-03-11
**Context-System:** 3-Schichten-Modell implementiert.
- Global: ~/.ai-assist/global/ (user-weit)
- Project: .ai-assist/ (projekt-spezifisch)
- Session: In-Memory (temporär)

## [pattern] 2026-03-11
**Tool-Registrierung:** Neue Tools in `app/agent/tools.py` registrieren.
```python
registry.register(ToolDefinition(
    name="tool_name",
    description="...",
    parameters={...},
    handler=handler_func,
    is_write_op=False
))
```

## [solution] 2026-03-11
**ModuleNotFoundError lxml:** Bei Import-Fehlern `pip install -r requirements.txt` ausführen.
Betrifft: app/services/pom_parser.py

## [warning] 2026-03-11
**SOAP-Parser Bug:** Test `test_parse_fault_response` schlägt fehl.
Vorbestehender Bug in app/utils/soap_utils.py - Fault-Parsing unvollständig.
