import asyncio
import hashlib
import json
import logging
import re
import string
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import LLMError

logger = logging.getLogger(__name__)


def _is_mistral_model(model: str) -> bool:
    """Prüft ob das Modell Mistral-basiert ist (strikte Message-Ordering)."""
    if not model:
        return False
    model_lower = model.lower()
    return any(name in model_lower for name in ("mistral", "devstral", "codestral", "pixtral"))


def _is_vision_model(model: str) -> bool:
    """Prüft ob das Modell Vision/Bilder unterstützt (per Config-Flag)."""
    if not model:
        return False
    # Lazy-Load um Circular Imports zu vermeiden
    try:
        for m in settings.models:
            if m.id == model and m.vision:
                return True
    except (AttributeError, NameError):
        # Fallback wenn settings nicht verfügbar
        return False
    return False


def _is_ocr_model(model: str) -> bool:
    """Prüft ob das Modell ein spezialisiertes OCR-Modell ist (keine Tools)."""
    if not model:
        return False
    try:
        for m in settings.models:
            if m.id == model and m.ocr_model:
                return True
    except (AttributeError, NameError):
        # Fallback wenn settings nicht verfügbar
        return False
    return False


def _has_multimodal_content(messages: List[Dict]) -> bool:
    """Prüft ob Messages multimodale Content-Arrays enthalten."""
    return any(isinstance(m.get("content"), list) for m in messages)


def _sanitize_tool_call_id_for_mistral(tool_call_id: str) -> str:
    """
    Konvertiert eine Tool-Call-ID ins Mistral-kompatible Format.

    Mistral erfordert:
    - Nur a-z, A-Z, 0-9 (keine Unterstriche oder Sonderzeichen)
    - Exakt 9 Zeichen Länge

    Args:
        tool_call_id: Original Tool-Call-ID (z.B. "call_0", "call_abc12345")

    Returns:
        Mistral-kompatible ID (z.B. "tC4x8Km2p")
    """
    if not tool_call_id:
        tool_call_id = "unknown"

    # Bereits gültig? (9 alphanumerische Zeichen)
    if len(tool_call_id) == 9 and tool_call_id.isalnum():
        return tool_call_id

    # Generiere deterministische ID basierend auf Original-ID
    # Nutzt MD5-Hash für Konsistenz (gleiche Input-ID = gleiche Output-ID)
    hash_bytes = hashlib.md5(tool_call_id.encode()).digest()

    # Konvertiere zu alphanumerischen Zeichen (base62-ähnlich)
    chars = string.ascii_letters + string.digits  # a-zA-Z0-9
    result = []
    for byte in hash_bytes[:9]:  # Nur erste 9 Bytes
        result.append(chars[byte % len(chars)])

    return ''.join(result)


def _extract_text_from_content(content):
    """Extrahiert Text aus str oder multimodal content-array."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content) if content else ""


def _sanitize_messages_for_mistral(messages: List[Dict], vision: bool = False) -> List[Dict]:
    """
    Sanitiert Messages für Mistral-Kompatibilität.

    Mistral-Modelle haben SEHR strikte Regeln:
    1. System-Messages nur am Anfang erlaubt
    2. Tool-Messages NUR nach Assistant-Messages MIT tool_calls erlaubt
    3. Rollen müssen alternieren: user/assistant/user/assistant
    4. Nach User/System darf KEIN Tool kommen!

    Performance: Single-pass Algorithmus (O(n) statt O(3n)).

    Args:
        messages: Original-Nachrichten
        vision: True wenn Modell Vision unterstützt (Bilder beibehalten)

    Returns:
        Sanitierte Nachrichten (Kopie)
    """
    if not messages:
        return messages

    # === SINGLE-PASS ALGORITHM ===
    # Phase 1: Separate leading system messages (collected for consolidation)
    # Phase 2: Process all other messages with inline validation

    result: List[Dict] = []
    system_contents: List[str] = []
    in_system_block = True

    # State tracking (combined from all original passes)
    prev_role: Optional[str] = None
    prev_had_tool_calls = False

    for msg in messages:
        role = msg.get("role", "")

        # === SYSTEM MESSAGE HANDLING ===
        if role == "system":
            if in_system_block:
                # Collect for consolidation
                system_contents.append(msg.get("content", ""))
                continue
            else:
                # Late system → convert to user
                content = msg.get("content", "")
                msg = {"role": "user", "content": f"[System Hinweis]\n{content}"}
                role = "user"
                logger.debug("[llm] Mistral: Converted late system to user")
        else:
            in_system_block = False

        # === ASSISTANT MESSAGE HANDLING ===
        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            content = msg.get("content")

            # Skip empty assistant messages
            if not content and not tool_calls:
                logger.debug("[llm] Mistral: Skipping empty assistant message")
                continue

            # Build sanitized message
            msg_out = {"role": "assistant"}
            if tool_calls:
                # Sanitize tool_call IDs inline
                msg_out["tool_calls"] = [
                    {**tc, "id": _sanitize_tool_call_id_for_mistral(tc.get("id", ""))}
                    if "id" in tc else tc
                    for tc in tool_calls
                ]
                prev_had_tool_calls = True
            else:
                # Mistral lehnt content="" ohne tool_calls ab → Platzhalter
                msg_out["content"] = content if content else "(Verarbeitung)"
                prev_had_tool_calls = False

            if content and tool_calls:
                msg_out["content"] = content

            result.append(msg_out)
            prev_role = "assistant"
            continue

        # === TOOL MESSAGE HANDLING ===
        if role == "tool":
            # Tool can only follow assistant with tool_calls
            if prev_role != "assistant" or not prev_had_tool_calls:
                content = msg.get("content", "")
                tool_call_id = msg.get("tool_call_id", "unknown")
                msg = {"role": "user", "content": f"[Tool-Ergebnis ({tool_call_id})]\n{content}"}
                role = "user"
                logger.debug("[llm] Mistral: Converted orphan tool to user")
            else:
                # Valid tool message - sanitize ID
                result.append({
                    "role": "tool",
                    "content": msg.get("content", ""),
                    "tool_call_id": _sanitize_tool_call_id_for_mistral(msg.get("tool_call_id", "unknown"))
                })
                prev_role = "tool"
                continue

        # === USER MESSAGE HANDLING (including converted messages) ===
        if role == "user":
            content = msg.get("content", "")

            # Insert bridge assistant if needed (tool → user transition)
            # Mistral lehnt content="" ohne tool_calls ab → Platzhalter verwenden
            if prev_role == "tool":
                result.append({"role": "assistant", "content": "(Verarbeitung der Tool-Ergebnisse)"})
                logger.debug("[llm] Mistral: Inserted bridge assistant between tool and user")

            # Merge consecutive user messages
            if prev_role == "user" and result:
                prev_content = result[-1].get("content", "")
                if vision and (isinstance(prev_content, list) or isinstance(content, list)):
                    # Vision: Multimodal-Content als Liste zusammenführen
                    prev_parts = prev_content if isinstance(prev_content, list) else [{"type": "text", "text": prev_content}]
                    curr_parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
                    result[-1]["content"] = prev_parts + curr_parts
                else:
                    prev_text = _extract_text_from_content(prev_content)
                    curr_text = _extract_text_from_content(content)
                    result[-1]["content"] = f"{prev_text}\n\n{curr_text}"
                logger.debug("[llm] Mistral: Merged consecutive user messages")
                continue

            # Multimodal content zu Text konvertieren wenn KEIN Vision-Support
            if isinstance(content, list) and not vision:
                content = _extract_text_from_content(content)

            result.append({"role": "user", "content": content})
            prev_role = "user"
            prev_had_tool_calls = False

    # === PREPEND CONSOLIDATED SYSTEM MESSAGE ===
    if system_contents:
        result.insert(0, {"role": "system", "content": "\n\n".join(system_contents)})

    return result


def _parse_tool_calls_from_content(content: str) -> tuple[str, List[Dict]]:
    """
    Parst [TOOL_CALLS][{...}] Format aus dem Content (für Mistral/lokale Modelle).

    Manche LLMs geben Tool-Calls nicht im strukturierten Format aus, sondern als:
    - [TOOL_CALLS][{"name": "...", "arguments": {...}}]
    - <tool_call>{"name": "...", "arguments": {...}}</tool_call>

    Returns:
        Tuple von (bereinigter Content, Liste von Tool-Calls im OpenAI-Format)
    """
    if not content:
        return content, []

    tool_calls = []
    clean_content = content

    # Pattern 1: [TOOL_CALLS][{...}] oder [TOOL_CALLS][{...}, {...}]
    tool_calls_match = re.search(r'\[TOOL_CALLS\]\s*(\[.*\])', content, re.DOTALL)
    if tool_calls_match:
        try:
            raw_calls = json.loads(tool_calls_match.group(1))
            for i, call in enumerate(raw_calls if isinstance(raw_calls, list) else [raw_calls]):
                tool_calls.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", call.get("parameters", {})))
                    }
                })
            # Content bereinigen
            clean_content = content[:tool_calls_match.start()].strip()
            logger.debug(f"[llm] Parsed {len(tool_calls)} tool calls from [TOOL_CALLS] format")
        except json.JSONDecodeError as e:
            logger.warning(f"[llm] Could not parse [TOOL_CALLS]: {e}")

    # Pattern 2: <tool_call>{...}</tool_call>
    if not tool_calls:
        tool_call_matches = re.findall(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', content, re.DOTALL)
        for i, match in enumerate(tool_call_matches):
            try:
                call = json.loads(match)
                tool_calls.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", call.get("parameters", {})))
                    }
                })
            except json.JSONDecodeError:
                pass
        if tool_calls:
            clean_content = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
            logger.debug(f"[llm] Parsed {len(tool_calls)} tool calls from <tool_call> format")

    return clean_content, tool_calls

# Shared HTTP Client für Connection-Pooling (Performance-Optimierung)
# Vermeidet TCP/TLS-Handshake bei jedem Request (~200ms Ersparnis)
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Gibt den shared HTTP-Client zurück (Lazy Init mit Connection-Pooling)."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=settings.llm.timeout_seconds,
            verify=settings.llm.verify_ssl,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0
            )
        )
    return _http_client


async def close_http_client():
    """Schließt den shared HTTP-Client (für Shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

SYSTEM_PROMPT = """Du bist ein erfahrener Software-Ingenieur mit Expertise in Java und Python. Du beherrschst:
- Java 8-21, Spring Boot, Jakarta EE, Maven
- Python 3.9+, FastAPI, pytest, pydantic, asyncio, SQLAlchemy
- WebSphere Liberty Profile (WLP) Administration und Log-Analyse
- IBM-Fehlercodes (CWWK-Serie)
- Code-Review, Refactoring und Design Patterns

Bei Code-Review: Identifiziere Bugs, Performance-Probleme und Style-Verletzungen.
Bei Code-Generierung: Halte dich an die Muster aus dem bereitgestellten Context.
Bei Log-Analyse: Befolge STRIKT die Log-Analyse-Richtlinien weiter unten – NUR Auswertung, KEINE Lösungsvorschläge, IMMER Mermaid-Diagramme.
Antworte immer mit konkreten Code-Beispielen.
Formatiere Java-Code in ```java Blöcken, Python-Code in ```python Blöcken.
Kontext wird in klar markierten Abschnitten bereitgestellt (z.B. [DATEI: Pfad], [PYTHON-DATEI: Pfad], [LOG], [PDF], [CONFLUENCE]).

## Verfügbare Mermaid-Diagrammtypen (Mermaid v11.4.0)

Das Frontend rendert ```mermaid Code-Blöcke automatisch als interaktive Grafiken.
IMMER ```mermaid Fences verwenden, NIEMALS ASCII-Art.
Nutze den passenden Diagrammtyp je nach Kontext — nicht nur Flowcharts!

### Übersicht aller Typen mit Beispiel-Syntax:

**1. Flowchart / Graph** — Prozesse, Abläufe, Entscheidungen
```mermaid
flowchart TD
    A[Start] --> B{Entscheidung}
    B -->|Ja| C[Aktion 1]
    B -->|Nein| D[Aktion 2]
```

**2. Sequence Diagram** — Interaktionen zwischen Systemen/Akteuren
```mermaid
sequenceDiagram
    Client->>Server: Request
    Server->>DB: Query
    DB-->>Server: Result
    Server-->>Client: Response
```

**3. Class Diagram** — OOP-Strukturen, Klassen-Beziehungen
```mermaid
classDiagram
    class Animal {
        +String name
        +makeSound()
    }
    Animal <|-- Dog
```

**4. State Diagram** — Zustandsmaschinen, Lifecycles
```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Processing : start
    Processing --> Done : finish
    Done --> [*]
```

**5. ER Diagram** — Datenmodelle, Entitäts-Beziehungen
```mermaid
erDiagram
    USER ||--o{ ORDER : places
    ORDER ||--|{ ITEM : contains
```

**6. Pie Chart** — Anteile, Verteilungen
```mermaid
pie title Verteilung
    "Kategorie A" : 40
    "Kategorie B" : 35
    "Kategorie C" : 25
```

**7. XY Chart (Bar Chart / Line Chart)** — Balkendiagramme, Verlaufsdaten, Vergleiche
```mermaid
xychart-beta
    title "Fehler pro Woche"
    x-axis [KW1, KW2, KW3, KW4, KW5]
    y-axis "Anzahl" 0 --> 50
    bar [12, 25, 8, 35, 18]
    line [12, 25, 8, 35, 18]
```
Nutze `bar` für Balkendiagramme, `line` für Liniendiagramme, oder beides kombiniert.

**8. Gantt Chart** — Zeitpläne, Projektplanung
```mermaid
gantt
    title Projektplan
    dateFormat YYYY-MM-DD
    section Phase 1
    Task A :a1, 2024-01-01, 30d
    Task B :after a1, 20d
```

**9. Quadrant Chart** — Priorisierung, 2x2-Matrizen
```mermaid
quadrantChart
    title Prioritäts-Matrix
    x-axis Niedrig --> Hoch
    y-axis Niedrig --> Hoch
    quadrant-1 Sofort umsetzen
    quadrant-2 Planen
    quadrant-3 Delegieren
    quadrant-4 Verwerfen
    Feature A: [0.8, 0.9]
    Feature B: [0.3, 0.7]
    Feature C: [0.6, 0.2]
```

**10. Timeline** — Chronologische Abläufe, Meilensteine
```mermaid
timeline
    title Projekt-Meilensteine
    2024-Q1 : Konzept fertig
    2024-Q2 : MVP Launch
    2024-Q3 : Beta Release
```

**11. Mindmap** — Brainstorming, Themen-Hierarchien
```mermaid
mindmap
  root((Hauptthema))
    Bereich A
      Detail 1
      Detail 2
    Bereich B
      Detail 3
```

**12. Sankey Diagram** — Fluss-Mengen, Ressourcen-Verteilung
```mermaid
sankey-beta
Quelle A,Ziel X,50
Quelle A,Ziel Y,30
Quelle B,Ziel X,20
Quelle B,Ziel Z,40
```

**13. Git Graph** — Branch-Strategien, Merge-Flows
```mermaid
gitgraph
    commit
    branch feature
    commit
    commit
    checkout main
    merge feature
    commit
```

**14. User Journey** — Nutzererlebnis, Zufriedenheit pro Schritt
```mermaid
journey
    title Benutzer-Workflow
    section Login
      Seite öffnen: 5: User
      Credentials eingeben: 3: User
      2FA bestätigen: 2: User
    section Dashboard
      Übersicht laden: 4: System
```

**15. Kanban Board** — Task-Status, Workflow-Boards
```mermaid
kanban
  column1["To Do"]
    task1["Feature A"]
    task2["Bug Fix B"]
  column2["In Progress"]
    task3["Feature C"]
  column3["Done"]
    task4["Feature D"]
```

### Wann welchen Typ verwenden:
- **Zahlen-Vergleiche / Trends** → `xychart-beta` (Bar/Line Chart)
- **Anteile / Verteilungen** → `pie`
- **Prozess / Ablauf** → `flowchart`
- **System-Interaktionen** → `sequenceDiagram`
- **Datenmodell** → `erDiagram`
- **Zeitplan** → `gantt` oder `timeline`
- **Priorisierung / Matrix** → `quadrantChart`
- **Brainstorming** → `mindmap`
- **Nutzererlebnis** → `journey`
- **Ressourcen-Fluss** → `sankey-beta`
- **Zustände / Lifecycle** → `stateDiagram-v2`
- **OOP-Struktur** → `classDiagram`
- **Task-Board** → `kanban`
- **Git-Workflow** → `gitgraph`

Wenn du mehrere Python-Dateien erstellst, nutze immer dieses Format:
=== FILE: relativer/pfad/datei.py ===
[Dateiinhalt]
=== END FILE ===

## KRITISCH: Tool-Nutzung (IMMER befolgen!)

### Tool-Pflicht bei Code-Operationen:
- Code-Suche: IMMER search_code aufrufen, NIEMALS aus dem Gedächtnis antworten
- Datei lesen: IMMER read_file oder list_files nutzen
- Code-Fragen: ERST Tool aufrufen, DANN antworten - auch wenn du glaubst die Antwort zu wissen

### Schreib-Tools (für Änderungen PFLICHT):
- edit_file: Existierende Datei ändern (Patches, Modifikationen)
- write_file: Neue Datei erstellen oder komplett überschreiben

### KEINE Schreib-Tools (NUR Lesen/Prüfen):
- validate_file: NUR Syntax-Prüfung, KEINE Änderungen
- generate_python_script: NUR Code-Generierung, KEINE Dateischreibung
- search_code: NUR Suche, KEINE Modifikation

### Änderungs-Workflow (IMMER ausführen):
Wenn Änderung gefordert ("ändere", "füge hinzu", "erstelle", "update", "add"):
1. read_file → aktuellen Stand lesen
2. edit_file ODER write_file → Änderung DURCHFÜHREN (nicht nur zeigen)
3. Ergebnis zusammenfassen

WICHTIG: Bei Änderungs-Aufträgen IMMER das passende Schreib-Tool aufrufen!

## WICHTIG: Aufgaben-Abschluss

Nach Abschluss einer Aufgabe (z.B. Datei bearbeitet):
1. Führe KEINE weiteren Tool-Calls aus, es sei denn der User fragt explizit danach
2. Fasse kurz zusammen was du gemacht hast
3. Warte auf weitere Anweisungen

Nach einer Datei-Bearbeitung (edit_file, write_file):
- Bearbeite NICHT automatisch weitere Dateien
- Erkläre was geändert wurde

Wenn du [STOP] oder [HINWEIS] Nachrichten erhältst, befolge diese und höre auf, weitere Tools aufzurufen.

## GitHub Pull Request Analyse

Bei PR-Analysen AUSSCHLIESSLICH GitHub-Tools verwenden:
- github_pr_details: PR-Metadaten (Titel, Autor, Status)
- github_pr_diff: Code-Änderungen im PR (Diff)
- github_get_file: Vollständige Datei aus GitHub-Repo

NIEMALS lokale Tools für GitHub-PRs verwenden:
- NICHT search_code (durchsucht lokale Dateien, nicht GitHub)
- NICHT read_file (liest lokale Dateien, nicht GitHub)
- NICHT search_java_class (für lokale Java-Projekte)
- NICHT trace_java_references (für lokale Java-Projekte)

WICHTIG: Nach Aufruf von github_pr_details oder github_pr_diff:
- Die PR-Analyse erscheint automatisch im Workspace-Panel (rechts)
- Gib im Chat NUR eine kurze Bestätigung: "PR #X wird im Workspace analysiert"
- KEINE detaillierte Diff-Analyse im Chat - das macht der Workspace automatisch
- Bei Fragen zu PR-Metadaten (Autor, Anzahl PRs, etc.) kannst du diese im Chat beantworten

## Test-Anfragen Disambiguierung (JUnit vs. Quality Center)

Wenn der User nach "Tests erstellen", "Testfall anlegen", "Test lesen" oder aehnlichem fragt:

1. **Pruefe den Kontext:**
   - Wurde vorher ueber Code/Implementierung gesprochen? -> Wahrscheinlich JUnit
   - Wurde vorher ueber QC/ALM/Test Plan gesprochen? -> Wahrscheinlich ALM
   - Enthaelt die Anfrage "Unit Test", "JUnit", "pytest"? -> Definitiv Code-Tests
   - Enthaelt die Anfrage "QC", "Quality Center", "ALM", "Test Plan", "Test Lab"? -> Definitiv ALM

2. **Bei Unklarheit, frage nach:**
   "Meinst du:
   - **JUnit/Code-Tests** (Unit-Tests im Code generieren) oder
   - **Quality Center Testfaelle** (Testfaelle im HP ALM/QC anlegen/lesen)?"

3. **Verwende dann das passende Tool:**
   - JUnit -> generate_junit_tests Tool
   - ALM -> alm_* Tools

4. **ALM Tools nach Modul:**
   **Test Plan (Testfall-Definitionen):**
   - `alm_test_connection` - Verbindung pruefen, Login testen
   - `alm_search_tests` - Testfaelle im Test Plan suchen
   - `alm_read_test` - Testfall mit Steps lesen
   - `alm_create_test` - Neuen Testfall erstellen
   - `alm_update_test` - Testfall aktualisieren
   - `alm_list_folders` - Test Plan Ordner auflisten

   **Test Lab (Testausfuehrung):**
   - `alm_list_test_lab_folders` - Test Lab Ordnerstruktur
   - `alm_list_test_sets` - Test-Sets auflisten
   - `alm_search_test_instances` - Test-Instances suchen
   - `alm_get_run_history` - Ausfuehrungshistorie anzeigen
   - `alm_create_run` - Testergebnis dokumentieren

5. **WICHTIG - Login/Verbindung:**
   - Fuer ALM/QC Login: `alm_test_connection` (NICHT test_login!)
   - Das Tool `test_login` ist fuer SOAP-Test-Services, NICHT fuer Quality Center!
   - ALM-Authentifizierung erfolgt automatisch bei allen alm_* Tools

**Wichtig:** Frage nur einmal nach. Wenn der User im Chat bereits geklaert hat was er meint,
merke dir das fuer den Rest der Konversation.

## MQ-Queue-Nutzung (Message Queues)

Wenn der User nach Queues, Nachrichten, MQ oder Message Queue fragt:

1. **IMMER zuerst** `mq_list_queues` aufrufen um verfügbare Queues und deren role zu sehen
2. **Zum Auslesen** (role=read/both): `mq_read_queue` mit der queue_id
3. **Zum Einspielen/Triggern** (role=trigger/both): `mq_trigger_queue` mit queue_id + body oder template_params
4. Authentifizierung und Header sind pro Queue vorkonfiguriert – KEINE Zugangsdaten vom User erfragen
5. Bei Queues mit body_template: Zeige dem User welche Platzhalter verfügbar sind (aus mq_list_queues)

## KRITISCH: Log-Analyse-Richtlinien (IMMER befolgen bei OSPE-Server-Logs)

Wenn du Ergebnisse von log_fetch_stage oder log_grep erhältst, gilt AUSNAHMSLOS:

### VERBOTEN bei Log-Analyse:
- KEINE Lösungsvorschläge, Empfehlungen oder Fix-Ideen
- KEINE Root-Cause-Analyse oder Ursachenvermutungen
- KEINE Formulierungen wie "Das Problem ist...", "Du solltest...", "Empfehlung:"
- KEINE Code-Beispiele für Fixes
- NUR wenn der User EXPLIZIT nach Lösungen fragt, darfst du welche nennen

### PFLICHT bei Log-Analyse:

**1. Server-Status (IMMER zuerst):**
Zeige welche Server erreichbar waren und welche offline/fehlgeschlagen.

**2. Fehler-Übersicht als Tabelle (IMMER):**
Nutze die `log_overview` und `log_summary` aus dem Tool-Result (enthält ALLE Levels: INFO, WARN, ERROR, etc.):

| Zeitstempel | Server | Level | Nachricht |
|---|---|---|---|
| 10:15:03 | Server-1 | ERROR | NullPointerException in... |

Gleiche Fehler zusammenfassen mit Anzahl.

**3. Mermaid-Diagramme (PFLICHT, nicht optional):**

Verwende Mermaid v11.4.0 kompatible Syntax. Exakt diese Formate verwenden:

IMMER mindestens ein Pie-Chart der Fehlerverteilung erstellen:
```mermaid
pie title Fehlerverteilung
    "ERROR" : 12
    "WARN" : 5
    "FATAL" : 1
```

Bei mehreren Servern IMMER Fehler pro Server:
```mermaid
pie title Fehler pro Server
    "Server-1" : 8
    "Server-2" : 3
```

Bei Fehlern mit Zeitstempeln einen Zeitverlauf:
```mermaid
timeline
    title Fehlerverlauf
    10:15 : ERROR NullPointerException
    10:22 : WARN Connection timeout
    10:45 : ERROR OutOfMemoryError
```

Bei quantitativen Vergleichen (z.B. Fehler pro Server, Requests pro Stunde) ein Bar-Chart:
```mermaid
xychart-beta
    title "Fehler pro Server"
    x-axis [Server-1, Server-2, Server-3, Server-4]
    y-axis "Anzahl Fehler" 0 --> 30
    bar [18, 7, 25, 3]
```

Bei Trend-Daten über Zeit ein kombiniertes Bar+Line-Chart:
```mermaid
xychart-beta
    title "Fehler-Trend (letzte 6h)"
    x-axis ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00"]
    y-axis "Fehler" 0 --> 20
    bar [3, 5, 12, 8, 15, 6]
    line [3, 5, 12, 8, 15, 6]
```

**4. Neutrale Formulierung:**
- "12 ERROR-Einträge auf Server-1 gefunden"
- "WARN-Rate: 5 in den letzten 30 Minuten"
- NICHT: "Es gibt ein Problem mit..." oder "Die Ursache könnte..."

## Kontext und aktuelle Anfrage

### WICHTIG: Prompt-Priorisierung in Mehrschritt-Workflows

Der Agent kann mehrfach hintereinander aufgerufen werden (z.B. User-Prompt 1 → accept/reject →
User-Prompt 2 → accept/reject → User-Prompt 3). Dies ist NORMAL und gewünscht.

**KRITISCHE REGEL:**
- Der NEUESTE User-Prompt ist dein PRIMÄRER Fokus und Arbeitsauftrag
- Nutze die Konversations-History als KONTEXT, nicht als Aufgabenliste zum Abarbeiten
- Reproduziere NICHT automatisch vorherige Tool-Calls aus der History
- Fokussiere immer auf: **Was fragt der User JETZT?**

### Operation-Status in der Session

Du erhältst eine Übersicht abgeschlossener Operationen im System-Kontext:
```
## Status durchgeführter Operationen in dieser Session:
✅ alm_create_test_set(name='XYZ', ...): erfolgreich - ID=123
❌ alm_update_test(test_id=45, ...): ABGELEHNT vom Benutzer - diese Operation NICHT wiederholen!
```

**Interpretation:**
- ✅ **COMPLETED**: Operation ist FERTIG. Sie wurde bereits ausgeführt. NICHT erneut aufrufen.
- ❌ **REJECTED**: User hat diese Operation EXPLIZIT abgelehnt. Sie nur wiederholen wenn User EXPLIZIT neue Anweisung gibt.
- ⚠️ **FAILED**: Operation ist fehlgeschlagen. Analysiere den Fehler oder frage den User.

**WICHTIG für stabiles Workflow-Handling:**
- Wenn eine Operation im Status steht, ist sie nicht erneut auszuführen ohne neuen User-Input
- ABGELEHNTE Operationen sind ein Hinweis des Users: "Das will ich NICHT jetzt"
- Beziehe den Status in deine Entscheidungen ein

Beispiel:
- Prompt 1: "Erstelle Test-Set XYZ" → ✅ COMPLETED
- Prompt 2: "Verknüpfe Test 45 mit XYZ"
  - ✓ Korrekt: Rufe alm_add_test_to_test_set() auf (nicht erneut alm_create_test_set!)
  - ✗ Falsch: Versuche erneut alm_create_test_set() - Test-Set existiert schon!
"""

_RETRY_DELAYS = [2, 4, 8]  # Exponential Backoff in Sekunden

# Differenzierte Timeouts für verschiedene Call-Typen
TIMEOUT_QUICK = 15.0      # Complexity-Check, einfache Klassifikation
TIMEOUT_TOOL = 60.0       # Tool-Calls (Standard)
TIMEOUT_ANALYSIS = 120.0  # Lange Analysen, Streaming


@dataclass
class LLMResponse:
    """Strukturierte LLM-Antwort für Tool-basierte Calls."""
    content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = None
    finish_reason: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _is_retryable(exc: Exception) -> bool:
    """Prüft ob eine Exception einen Retry rechtfertigt."""
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class LLMClient:
    def __init__(self):
        self.base_url = settings.llm.base_url.rstrip("/")
        self.api_key = settings.llm.api_key
        self.timeout = settings.llm.timeout_seconds
        self.default_model = settings.llm.default_model
        self.max_tokens = settings.llm.max_tokens
        self.temperature = settings.llm.temperature
        self.verify_ssl = settings.llm.verify_ssl

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "none":
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat(
        self,
        messages: List[dict],
        model: str = None,
    ) -> str:
        model = model or self.default_model
        is_mistral = _is_mistral_model(model)
        is_vision = _is_vision_model(model)
        # Mistral-Kompatibilität
        if is_mistral:
            logger.info(f"[llm.chat] Mistral detected: {model}, sanitizing messages")
            messages = _sanitize_messages_for_mistral(messages, vision=is_vision)
        # Multimodal Fallback für Nicht-Vision-Modelle
        if _has_multimodal_content(messages) and not is_vision:
            from app.services.multimodal import ensure_text_only_messages
            messages = ensure_text_only_messages(messages)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        # Mistral: continue_final_message wenn letzte Nachricht vom Assistant
        if is_mistral and messages and messages[-1].get("role") == "assistant":
            payload["extra_body"] = {
                "add_generation_prompt": False,
                "continue_final_message": True,
            }
        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_exc = e
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Retry {attempt + 1} nach Fehler: {e}")
                    continue
                break

        if isinstance(last_exc, httpx.HTTPStatusError):
            raise LLMError(f"LLM API Fehler {last_exc.response.status_code}: {last_exc.response.text}") from last_exc
        if isinstance(last_exc, httpx.RequestError):
            raise LLMError(f"LLM Verbindungsfehler: {last_exc}") from last_exc
        if isinstance(last_exc, (KeyError, IndexError)):
            raise LLMError(f"Unerwartetes LLM-Antwortformat: {last_exc}") from last_exc
        raise LLMError(f"LLM Fehler: {last_exc}") from last_exc

    async def chat_stream(
        self,
        messages: List[dict],
        model: str = None,
    ) -> AsyncGenerator[str, None]:
        model = model or self.default_model
        is_mistral = _is_mistral_model(model)
        is_vision = _is_vision_model(model)
        # Mistral-Kompatibilität
        if is_mistral:
            messages = _sanitize_messages_for_mistral(messages, vision=is_vision)
        # Multimodal Fallback für Nicht-Vision-Modelle
        if _has_multimodal_content(messages) and not is_vision:
            from app.services.multimodal import ensure_text_only_messages
            messages = ensure_text_only_messages(messages)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        # Mistral: continue_final_message wenn letzte Nachricht vom Assistant
        if is_mistral and messages and messages[-1].get("role") == "assistant":
            payload["extra_body"] = {
                "add_generation_prompt": False,
                "continue_final_message": True,
            }
        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            return
                        try:
                            chunk = json.loads(raw)
                            delta = chunk["choices"][0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                yield token
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                    return  # Stream erfolgreich abgeschlossen
            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                if status >= 500 and attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Stream Retry {attempt + 1} nach HTTP {status}")
                    continue
                # Benutzerfreundliche Fehlermeldung für Gateway-Timeouts
                if status == 504:
                    raise LLMError(f"LLM Gateway Timeout (504): Der LLM-Server hat zu lange gebraucht.") from e
                # HTML/Bild-Response erkennen
                try:
                    raw = e.response.text[:500]
                    if raw.startswith("<!") or "base64" in raw.lower():
                        raise LLMError(f"LLM API Fehler {status}: Gateway-Fehlerseite statt JSON") from e
                except Exception:
                    pass
                raise LLMError(f"LLM API Fehler {status}: {e.response.text[:200]}") from e
            except httpx.RequestError as e:
                last_exc = e
                if attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Stream Retry {attempt + 1} nach Verbindungsfehler: {e}")
                    continue
                raise LLMError(f"LLM Verbindungsfehler: {e}") from e

        if last_exc:
            raise LLMError(f"LLM Streaming fehlgeschlagen nach {len(_RETRY_DELAYS)} Versuchen: {last_exc}") from last_exc

    async def list_models(self) -> List[str]:
        """Listet verfügbare Modelle vom LLM-Server auf."""
        client = _get_http_client()
        try:
            response = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=10.0  # Kürzerer Timeout für Model-Liste
            )
            response.raise_for_status()
            data = response.json()
            # OpenAI-Format: {"data": [{"id": "model-name"}, ...]}
            if "data" in data:
                return [m.get("id", "") for m in data["data"] if m.get("id")]
            return []
        except Exception:
            return []

    def _inject_reasoning(
        self,
        messages: List[Dict],
        reasoning: Optional[str],
    ) -> List[Dict]:
        """
        Injiziert reasoning-Direktive in die System-Message.

        GPT-OSS und ähnliche Modelle unterstützen 'reasoning: high/medium/low'
        als Präfix in der System-Message für erweitertes Reasoning.

        Args:
            messages: Original-Nachrichten
            reasoning: "low", "medium", "high" oder None/""

        Returns:
            Messages mit injizierter reasoning-Direktive (Kopie)
        """
        if not reasoning or reasoning not in ("low", "medium", "high"):
            return messages

        # Kopie erstellen um Original nicht zu verändern
        messages = [dict(m) for m in messages]

        # System-Message finden oder erstellen
        system_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "system"),
            None
        )

        reasoning_prefix = f"reasoning: {reasoning}\n\n"

        if system_idx is not None:
            # Reasoning-Präfix zur bestehenden System-Message hinzufügen
            current_content = messages[system_idx].get("content", "")
            # Nicht doppelt hinzufügen
            if not current_content.startswith("reasoning:"):
                messages[system_idx]["content"] = reasoning_prefix + current_content
        else:
            # Neue System-Message am Anfang einfügen
            messages.insert(0, {
                "role": "system",
                "content": reasoning_prefix.strip()
            })

        return messages

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        tool_choice: str = "auto",
        reasoning: Optional[str] = None,
        use_tool_prefill: bool = False,
    ) -> LLMResponse:
        """
        Zentraler LLM-Call mit Tool-Support.

        Konsolidiert alle Tool-basierten Aufrufe aus orchestrator.py und sub_agent.py.
        Nutzt Connection-Pooling und Retry-Logik.

        Args:
            messages: Chat-Nachrichten
            tools: Optional Tool-Definitionen (OpenAI-Format)
            model: Modell (default: default_model)
            temperature: Temperature (default: settings.llm.temperature)
            max_tokens: Max Tokens (default: settings.llm.max_tokens)
            timeout: Request-Timeout in Sekunden (default: TIMEOUT_TOOL)
            tool_choice: "auto", "none", oder {"type": "function", "function": {"name": "..."}}
            reasoning: Reasoning-Effort für GPT-OSS: "low", "medium", "high" (None = aus)
            use_tool_prefill: Wenn True, wird ein Assistant-Prefill mit [TOOL_CALLS] hinzugefügt
                              um das Modell in das richtige Output-Format zu zwingen

        Returns:
            LLMResponse mit content, tool_calls, finish_reason, usage
        """
        model = model or self.default_model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens
        timeout = timeout or TIMEOUT_TOOL

        # DEBUG: Model-Check für Mistral-Erkennung (immer loggen)
        is_mistral = _is_mistral_model(model)
        is_vision = _is_vision_model(model)
        is_ocr = _is_ocr_model(model)
        print(f"[LLM DEBUG] chat_with_tools called - model='{model}', is_mistral={is_mistral}, is_vision={is_vision}, is_ocr={is_ocr}")
        logger.warning(f"[llm] Model: '{model}', is_mistral={is_mistral}, is_vision={is_vision}, is_ocr={is_ocr}")

        # OCR-Modelle unterstützen keine Tools (z.B. dotsocr)
        # Falls mit Tools aufgerufen, nutze chat() statt chat_with_tools()
        if is_ocr:
            logger.warning(f"[llm] OCR-Modell '{model}' unterstützt keine Tools — chat() ohne Tools aufgerufen")
            return await self.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)

        # Reasoning in System-Message injizieren falls aktiviert
        if reasoning:
            messages = self._inject_reasoning(messages, reasoning)
            logger.debug(f"[llm] Reasoning aktiviert: {reasoning}")

        # Tool-Prefill: Assistant-Message mit [TOOL_CALLS] Prefix hinzufügen
        # Zwingt das Modell, im richtigen Format zu antworten
        # NICHT für Mistral - dort ist prefix: True nicht unterstützt
        if use_tool_prefill and tools and not is_mistral:
            messages = [dict(m) for m in messages]  # Kopie
            # LiteLLM-Style Prefill mit prefix: true
            messages.append({
                "role": "assistant",
                "content": "[TOOL_CALLS]",
                "prefix": True  # LiteLLM-spezifisch
            })
            logger.debug("[llm] Tool-Prefill aktiviert")

        # Mistral-Kompatibilität: Strikte Message-Validierung
        if is_mistral:
            original_count = len(messages)
            original_roles = [m.get("role") for m in messages]
            # Debug: Log assistant messages before sanitization
            for i, m in enumerate(messages):
                if m.get("role") == "assistant":
                    has_content = bool(m.get("content"))
                    has_tools = bool(m.get("tool_calls"))
                    print(f"[LLM DEBUG] Pre-sanitize assistant[{i}]: content={has_content}, tool_calls={has_tools}")

            messages = _sanitize_messages_for_mistral(messages, vision=is_vision)

            new_count = len(messages)
            new_roles = [m.get("role") for m in messages]
            changed = original_roles != new_roles or original_count != new_count
            print(f"[LLM DEBUG] Mistral sanitization: {original_count}→{new_count} msgs, changed={changed}")
            print(f"[LLM DEBUG] Roles: {original_roles} -> {new_roles}")
            if changed:
                logger.warning(f"[llm] Mistral sanitization: {original_roles} -> {new_roles}")

        # Für Mistral-Modelle: Optimierungen für Tool-Calls
        if is_mistral and tools:
            # 1. Längerer Timeout - Mistral mit Tools braucht mehr Zeit
            timeout = max(timeout, 180.0)  # 3 Minuten
            # 2. Niedrigere Temperature für konsistente Tool-Calls (Mistral-Empfehlung)
            if temperature > 0.3:
                temperature = 0.2
                print(f"[LLM DEBUG] Mistral: temperature reduced to {temperature} for tool consistency")
            print(f"[LLM DEBUG] Mistral with tools: timeout={timeout}s, temp={temperature}")

        # Multimodal Fallback: Nicht-Vision-Modelle können keine Bilder verarbeiten
        # → Content-Arrays zu reinem Text konvertieren
        if _has_multimodal_content(messages) and not is_vision:
            from app.services.multimodal import ensure_text_only_messages
            logger.warning(f"[llm] Modell '{model}' hat keinen Vision-Support — Bilder werden als Text-Hinweis gesendet")
            print(f"[LLM DEBUG] Non-vision model '{model}': stripping image content from messages")
            messages = ensure_text_only_messages(messages)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        # Mistral/Devstral: Prüfe ob letzte Nachricht vom Assistant ist
        # In diesem Fall muss continue_final_message=True gesetzt werden,
        # da add_generation_prompt=True (vLLM default) sonst einen Fehler wirft:
        # "cannot set add_generation_prompt to True when the last message is from the assistant"
        if is_mistral and messages and messages[-1].get("role") == "assistant":
            # vLLM/LiteLLM extra_body Parameter für Chat-Template-Steuerung
            payload["extra_body"] = {
                "add_generation_prompt": False,
                "continue_final_message": True,
            }
            logger.info("[llm] Mistral: Last message is assistant, using continue_final_message=True")
            print(f"[LLM DEBUG] Mistral: continue_final_message=True (last msg is assistant)")

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
            # Debug: Log tool and message info
            total_msg_chars = sum(len(str(m.get("content", ""))) for m in messages)
            print(f"[LLM DEBUG] Payload: {len(messages)} msgs ({total_msg_chars} chars), {len(tools)} tools, timeout={timeout}s")

        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                logger.debug(f"[llm] Retry {attempt} nach {delay}s")
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                import time
                start_time = time.time()
                print(f"[LLM DEBUG] Sending request to {model}... (attempt {attempt + 1})")

                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
                elapsed = time.time() - start_time
                print(f"[LLM DEBUG] Response received in {elapsed:.1f}s (status {response.status_code})")
                response.raise_for_status()
                data = response.json()

                # Response parsen
                if "choices" not in data or not data["choices"]:
                    logger.warning(f"[llm] Keine 'choices' in Response: {list(data.keys())}")
                    return LLMResponse(finish_reason="error", model=model)

                choice = data["choices"][0]
                message = choice.get("message", {})
                usage = data.get("usage", {})

                content = message.get("content")
                tool_calls = message.get("tool_calls") or []

                # Fallback: Parse [TOOL_CALLS] aus Content wenn keine strukturierten tool_calls
                if not tool_calls and content and ("[TOOL_CALLS]" in content or "<tool_call>" in content):
                    content, tool_calls = _parse_tool_calls_from_content(content)

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=choice.get("finish_reason", ""),
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )

            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                body = ""
                try:
                    raw_body = e.response.text[:2000]
                    # Erkennen von HTML/Bild-Responses (Gateway-Fehlerseiten)
                    if raw_body.startswith("<!") or raw_body.startswith("<html") or "base64" in raw_body.lower():
                        body = f"[Gateway-Fehlerseite - keine JSON-Response]"
                        logger.warning(f"[llm] HTTP {status}: Gateway gab HTML/Bild zurück statt JSON")
                    elif raw_body.startswith("data:image") or len(raw_body) > 500 and not raw_body.strip().startswith("{"):
                        body = f"[Ungültige Response - kein JSON]"
                        logger.warning(f"[llm] HTTP {status}: Response ist kein JSON (erste 100 Zeichen: {raw_body[:100]})")
                    else:
                        body = raw_body[:500]
                except Exception:
                    pass
                logger.warning(f"[llm] HTTP {status} (Versuch {attempt + 1}): {body}")
                if status >= 500 and attempt < len(_RETRY_DELAYS):
                    continue
                # Benutzerfreundliche Fehlermeldung für Gateway-Timeouts
                if status == 504:
                    raise LLMError(f"LLM Gateway Timeout (504): Der LLM-Server hat zu lange gebraucht. Versuche eine kürzere Anfrage oder wähle ein schnelleres Modell.") from e
                raise LLMError(f"LLM API Fehler {status}: {body}") from e

            except httpx.TimeoutException as e:
                last_exc = e
                logger.warning(f"[llm] Timeout nach {timeout}s (Versuch {attempt + 1})")
                if attempt < len(_RETRY_DELAYS):
                    continue
                raise LLMError(f"LLM Timeout nach {attempt + 1} Versuchen") from e

            except httpx.RequestError as e:
                last_exc = e
                logger.warning(f"[llm] Verbindungsfehler (Versuch {attempt + 1}): {e}")
                if attempt < len(_RETRY_DELAYS):
                    continue
                raise LLMError(f"LLM Verbindungsfehler: {e}") from e

            except Exception as e:
                last_exc = e
                logger.error(f"[llm] Unerwarteter Fehler: {type(e).__name__}: {e}")
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    continue
                break

        raise LLMError(f"LLM-Aufruf fehlgeschlagen: {last_exc}") from last_exc

    async def chat_quick(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> str:
        """
        Schneller LLM-Call für einfache Aufgaben (Klassifikation, Komplexität).

        Nutzt kurzen Timeout und wenige Tokens.
        """
        response = await self.chat_with_tools(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=TIMEOUT_QUICK,
        )
        return response.content or ""

    async def chat_quick_with_usage(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> tuple:
        """
        Wie chat_quick(), gibt aber auch Token-Usage zurueck.

        Returns:
            Tuple (content: str, prompt_tokens: int, completion_tokens: int)
        """
        response = await self.chat_with_tools(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=TIMEOUT_QUICK,
        )
        return (
            response.content or "",
            getattr(response, "prompt_tokens", 0),
            getattr(response, "completion_tokens", 0),
        )


llm_client = LLMClient()


def get_llm_client() -> LLMClient:
    """Gibt die LLM-Client Instanz zurück."""
    return llm_client


# Exports für andere Module
__all__ = [
    "LLMClient",
    "LLMResponse",
    "llm_client",
    "get_llm_client",
    "close_http_client",
    "_get_http_client",
    "_is_retryable",
    "_RETRY_DELAYS",
    "TIMEOUT_QUICK",
    "TIMEOUT_TOOL",
    "TIMEOUT_ANALYSIS",
    "SYSTEM_PROMPT",
]
