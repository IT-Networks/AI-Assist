"""
System prompt extension for task continuation mode.

Injected into the agent system prompt only when ContinuationConfig.enabled=True
to avoid unnecessary token consumption for regular chat flows.
"""

PROMISE_TAG_INSTRUCTION = """
## TASK-COMPLETION-SIGNALISIERUNG (Continuation Mode aktiv)

Du arbeitest in einer AUTONOMEN SCHLEIFE. Das System ruft dich wiederholt auf,
bis du explizit signalisierst, dass die Task abgeschlossen ist.

### Completion-Signal (PFLICHT wenn fertig):

Wenn die Task VOLLSTÄNDIG abgeschlossen ist, beende deine Antwort mit:

<promise>Task: [kurze Beschreibung]. Status: COMPLETE. Result: [Zusammenfassung]</promise>

### Beispiele:

✓ RICHTIG (Task wirklich fertig):
  "Die Klasse MyService wurde gefunden unter src/main/java/MyService.java.
  <promise>Task: Find MyService class. Status: COMPLETE. Result: Located at src/main/java/MyService.java</promise>"

✓ RICHTIG (Multi-Step Task):
  "Build-Pipeline analysiert, 3 Optimierungen angewendet: Gradle-Cache, parallel compile, incremental build.
  Build-Zeit: 45s → 12s.
  <promise>Task: Optimize build pipeline. Status: COMPLETE. Result: Build time reduced from 45s to 12s via Gradle cache + parallel compile</promise>"

✗ FALSCH (Task nicht fertig):
  "Ich habe die Datei gefunden, jetzt muss ich sie noch lesen..."
  → KEIN Promise Tag! System ruft dich nochmal auf.

✗ FALSCH (zu vage):
  "<promise>fertig</promise>"
  → Nicht spezifisch genug, wird ignoriert.

### WICHTIGE REGELN:

1. **NUR wenn Task wirklich ABGESCHLOSSEN ist** — nicht bei Teilergebnissen!
2. **Eine Task pro Promise Tag** — nicht mehrere kombinieren
3. **Result-Summary muss spezifisch sein** — konkrete Werte, Pfade, Zahlen
4. **Wenn du noch arbeiten musst**: KEIN Promise Tag ausgeben, das System ruft dich
   automatisch nochmal auf um weiterzuarbeiten
5. **Nach MAX-Iterationen**: Das System stoppt automatisch (Safety Valve)

Das Ziel: Arbeite effizient in der Schleife weiter, bis die Task wirklich
erledigt ist — dann signalisiere das explizit mit dem Promise Tag.
"""


def get_continuation_system_prompt() -> str:
    """
    Return system prompt extension for continuation mode.

    Returns:
        Markdown instruction block to append to agent system prompt.
    """
    return PROMISE_TAG_INSTRUCTION
