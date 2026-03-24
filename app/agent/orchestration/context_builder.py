"""
Context Builder - Builds context for LLM requests.

Handles:
- MCP prompt enhancement
- Project context loading
- Memory context injection
- Entity context from trackers
- Tool budget hints
- Manual context selection
- Conversation history management
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.agent.orchestration.types import AgentEvent, AgentEventType, AgentMode, AgentState
from app.core.config import settings
from app.utils.token_counter import estimate_tokens

logger = logging.getLogger(__name__)


async def run_prompt_enhancement(
    user_message: str,
    state: AgentState,
    session_id: str,
    event_callback: callable,
    event_bridge: Any,
    task_tracker: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """
    Run MCP prompt enhancement to collect context.

    Yields AgentEvents during the enhancement process.
    The enriched context is stored in state.confirmed_enhancement_context
    for retrieval by the caller.

    Args:
        user_message: The user's message
        state: Current agent state (will be modified with enhancement results)
        session_id: Session identifier
        event_callback: Callback for MCP events
        event_bridge: Event bridge for live streaming
        task_tracker: Task tracker for progress

    Yields:
        AgentEvent objects during enhancement
    """

    try:
        from app.agent.prompt_enhancer import (
            get_prompt_enhancer,
            EnhancementType,
            ConfirmationStatus,
            ContextItem
        )
        from app.services.task_tracker import TaskArtifact

        enhancer = get_prompt_enhancer(event_callback=event_callback)

        # Check if enhancement makes sense
        if not enhancer.detector.should_enhance(user_message):
            return

        logger.info("[context_builder] Starting MCP prompt enhancement")

        # Create enhancement task
        enhancement_task_id = task_tracker.create_task(
            title="Kontext-Sammlung",
            steps=["Anfrage analysieren", "Quellen durchsuchen", "Kontext aufbereiten"],
            metadata={"type": "enhancement", "query_preview": user_message[:100]}
        )
        await task_tracker.start_task(enhancement_task_id)
        await task_tracker.start_step(enhancement_task_id, 0, "Analysiere Anfrage...")

        # Enhancement-Start Event
        yield AgentEvent(AgentEventType.ENHANCEMENT_START, {
            "query_preview": user_message[:100],
            "detection_type": enhancer.detector.detect(user_message).value
        })

        # Subscribe to event bridge for live streaming
        mcp_queue = event_bridge.subscribe()

        try:
            # Step 1 complete, start step 2
            await task_tracker.complete_step(enhancement_task_id, 0)
            await task_tracker.start_step(enhancement_task_id, 1, "Durchsuche Quellen...")

            # Run enhancement in separate task for live streaming
            enhance_task = asyncio.create_task(enhancer.enhance(user_message))

            # Stream events while enhancement runs
            while not enhance_task.done():
                async for mcp_event in _drain_events_from_queue(mcp_queue):
                    yield mcp_event
                await asyncio.sleep(0.05)

            enriched = await enhance_task

            # Step 2 complete, start step 3
            await task_tracker.complete_step(enhancement_task_id, 1)
            await task_tracker.start_step(enhancement_task_id, 2, "Bereite Kontext auf...")

        finally:
            event_bridge.unsubscribe(mcp_queue)

        if enriched.context_items:
            # Store enhancement in state
            state.pending_enhancement = enriched

            # Check confirmation mode
            confirm_mode = settings.task_agents.enhancement_confirm_mode
            always_confirm_types = settings.task_agents.enhancement_always_confirm
            enhancement_type = enriched.enhancement_type.value

            needs_confirmation = False
            if confirm_mode == "all":
                needs_confirmation = True
            elif confirm_mode == "write_only":
                needs_confirmation = False
            elif enhancement_type in always_confirm_types:
                needs_confirmation = True

            if needs_confirmation:
                # Emit enhancement complete event for confirmation
                yield AgentEvent(AgentEventType.ENHANCEMENT_COMPLETE, {
                    "context_count": len(enriched.context_items),
                    "sources": enriched.context_sources,
                    "summary": enriched.summary,
                    "confirmation_message": enriched.get_confirmation_message(),
                    "context_items": [
                        {
                            "source": item.source,
                            "title": item.title,
                            "content_preview": item.content[:200] + "..." if len(item.content) > 200 else item.content,
                            "relevance": item.relevance,
                            "file_path": item.file_path,
                            "url": item.url
                        }
                        for item in enriched.context_items
                    ]
                })

                # Request confirmation
                yield AgentEvent(AgentEventType.CONFIRM_REQUIRED, {
                    "type": "enhancement",
                    "message": enriched.get_confirmation_message()
                })

                # Wait for confirmation (via generator send)
                user_confirmed = yield
                if user_confirmed is None:
                    user_confirmed = True

                if user_confirmed:
                    enriched = enhancer.confirm(enriched, True)
                    state.confirmed_enhancement_context = enriched.get_context_for_planner()
                    state.pending_enhancement = None
                    yield AgentEvent(AgentEventType.ENHANCEMENT_CONFIRMED, {
                        "context_length": len(state.confirmed_enhancement_context)
                    })
                    logger.info(f"[context_builder] Enhancement confirmed, context: {len(state.confirmed_enhancement_context)} chars")
                else:
                    enriched = enhancer.confirm(enriched, False)
                    state.pending_enhancement = None
                    state.confirmed_enhancement_context = None
                    yield AgentEvent(AgentEventType.ENHANCEMENT_REJECTED, {})
                    logger.info("[context_builder] Enhancement rejected by user")
            else:
                # Auto-confirm
                enriched = enhancer.confirm(enriched, True)
                state.confirmed_enhancement_context = enriched.get_context_for_planner()
                state.pending_enhancement = None
                logger.info(f"[context_builder] Enhancement auto-confirmed, context: {len(state.confirmed_enhancement_context)} chars")

            # Complete enhancement task
            await task_tracker.complete_step(enhancement_task_id, 2, artifacts=[
                TaskArtifact(
                    id=f"ctx_{enhancement_task_id[:8]}",
                    type="context",
                    summary=f"{len(enriched.context_items)} Kontext-Elemente gesammelt",
                    data={"context_count": len(enriched.context_items), "sources": enriched.context_sources}
                )
            ])
            await task_tracker.complete_task(enhancement_task_id)

        elif enriched.cache_hit:
            state.confirmed_enhancement_context = enriched.get_context_for_planner()
            logger.debug("[context_builder] Using cached enhancement context")
            await task_tracker.skip_step(enhancement_task_id, 2, "Cache-Treffer")
            await task_tracker.complete_task(enhancement_task_id)
        else:
            await task_tracker.skip_step(enhancement_task_id, 2, "Kein Kontext gefunden")
            await task_tracker.complete_task(enhancement_task_id)

    except ImportError as e:
        logger.debug(f"[context_builder] Prompt enhancement not available: {e}")
    except Exception as e:
        logger.warning(f"[context_builder] Prompt enhancement failed: {e}")
        try:
            if 'enhancement_task_id' in locals():
                await task_tracker.fail_task(enhancement_task_id, str(e))
        except Exception:
            pass
        yield AgentEvent(AgentEventType.MCP_ERROR, {
            "mode": "enhancement",
            "error": str(e),
            "message": "Kontext-Sammlung fehlgeschlagen - fahre ohne Kontext fort"
        })


async def build_messages_context(
    state: AgentState,
    user_message: str,
    system_prompt: str,
    budget: Any,
    context_manager: Any,
    memory_store: Any,
    context_selection: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Build the messages list with all context.

    Args:
        state: Agent state
        user_message: User's message
        system_prompt: Base system prompt
        budget: Token budget
        context_manager: Context manager for project context
        memory_store: Memory store for memory context
        context_selection: Optional manual context selection

    Returns:
        List of message dicts for LLM
    """
    messages = [{"role": "system", "content": system_prompt}]
    budget.set("system", estimate_tokens(system_prompt))

    # Project context (static, from PROJECT_CONTEXT.md)
    try:
        project_context = await context_manager.build_full_context(
            project_path=state.project_path,
            project_id=state.project_id,
            max_tokens=min(500, budget.memory_limit // 3)
        )
        if project_context:
            messages.append({"role": "system", "content": project_context})
            budget.set("memory", estimate_tokens(project_context))
    except Exception as e:
        logger.debug(f"[context_builder] Project context loading failed: {e}")

    # Memory context (dynamic, multi-scope)
    try:
        memory_context = await memory_store.get_context_injection(
            current_message=user_message,
            project_id=state.project_id,
            session_id=state.session_id,
            scopes=["global", "project", "session"],
            max_tokens=budget.memory_limit - budget.used_memory
        )
        if memory_context:
            messages.append({"role": "system", "content": memory_context})
            budget.set("memory", budget.used_memory + estimate_tokens(memory_context))
    except Exception as e:
        logger.debug(f"[context_builder] Memory loading failed: {e}")

    # Entity context from tracker
    if state.entity_tracker:
        entity_hint = state.entity_tracker.get_context_hint()
        if entity_hint:
            messages.append({"role": "system", "content": entity_hint})
            budget.set("memory", budget.used_memory + estimate_tokens(entity_hint))

    # Tool budget hint
    if state.tool_budget:
        budget_hint = state.tool_budget.get_budget_hint()
        if budget_hint:
            messages.append({"role": "system", "content": budget_hint})
            logger.debug(f"[context_builder] Budget hint injected (Level: {state.tool_budget.level.value})")

    # Manual context selection (Explorer chips)
    if context_selection:
        hint_parts = []
        if getattr(context_selection, "java_files", None):
            paths = ", ".join(context_selection.java_files[:20])
            hint_parts.append(f"Java-Dateien: {paths}")
        if getattr(context_selection, "python_files", None):
            paths = ", ".join(context_selection.python_files[:20])
            hint_parts.append(f"Python-Dateien: {paths}")
        if getattr(context_selection, "pdf_ids", None):
            ids = ", ".join(context_selection.pdf_ids[:10])
            hint_parts.append(f"PDF-Dokumente (IDs): {ids}")
        if getattr(context_selection, "handbook_services", None):
            ids = ", ".join(context_selection.handbook_services[:10])
            hint_parts.append(f"Handbuch-Services: {ids}")

        if hint_parts:
            ctx_msg = (
                "Der Nutzer hat folgende Elemente explizit als Kontext ausgewaehlt. "
                "Beziehe dich bevorzugt auf diese Quellen:\n"
                + "\n".join(f"- {p}" for p in hint_parts)
            )
            messages.append({"role": "system", "content": ctx_msg})
            budget.set("memory", budget.used_memory + estimate_tokens(ctx_msg))
            logger.debug(f"[context_builder] User context injected: {hint_parts}")

    # Conversation history
    history_tokens = 0
    history_count = len(state.messages_history)
    history_to_add = state.messages_history[-state.max_history_messages:]
    logger.info(f"[context_builder] Conversation history: {history_count} total, adding {len(history_to_add)} messages")

    for hist_msg in history_to_add:
        messages.append(hist_msg)
        history_tokens += estimate_tokens(hist_msg.get("content", ""))
    budget.set("conversation", history_tokens)

    # Current user message
    messages.append({"role": "user", "content": user_message})

    return messages


async def _drain_events_from_queue(
    queue: asyncio.Queue,
    timeout: float = 0.01
) -> AsyncGenerator[AgentEvent, None]:
    """Drain events from a queue with timeout."""
    while True:
        try:
            event_data = queue.get_nowait()
            event_type_str = event_data.get("type", "")
            try:
                event_type = AgentEventType(event_type_str)
                yield AgentEvent(event_type, event_data.get("data", {}))
            except ValueError:
                logger.debug(f"[context_builder] Unknown event type: {event_type_str}")
        except asyncio.QueueEmpty:
            break


def extract_conversation_context(
    messages: List[Dict],
    max_messages: int = 4
) -> Optional[str]:
    """
    Extract a short context from recent conversation messages.

    Provides sub-agents with conversation context so follow-up
    questions like "from my initial question" can be understood.

    Args:
        messages: The full message list
        max_messages: Max number of user/assistant messages to extract

    Returns:
        Context string or None if no relevant messages
    """
    # Only extract user/assistant messages (no system prompts)
    relevant = [
        m for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    if len(relevant) <= 1:
        return None  # No previous conversation

    # Take last N messages (without current user message, that's the query)
    recent = relevant[-(max_messages + 1):-1] if len(relevant) > max_messages else relevant[:-1]

    if not recent:
        return None

    # Format as compact context string
    context_parts = []
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = str(msg.get("content", ""))[:300]  # Truncate
        if len(str(msg.get("content", ""))) > 300:
            content += "..."
        context_parts.append(f"{role}: {content}")

    return "\n\n".join(context_parts)


def build_agent_instructions(mode: AgentMode, plan_approved: bool = False) -> str:
    """
    Build agent instructions for the system prompt.

    Creates mode-specific instructions including available tools
    and behavioral guidelines based on the current agent mode.

    Args:
        mode: Current agent mode (READ_ONLY, WRITE_WITH_CONFIRM, etc.)
        plan_approved: Whether a plan has been approved (for PLAN_THEN_EXECUTE mode)

    Returns:
        Formatted instruction string for the system prompt
    """
    db_available = settings.database.enabled
    handbook_available = settings.handbook.enabled

    base = """
## Agent-Anweisungen

Du bist ein intelligenter Assistent mit Zugriff auf Tools.

### Verfügbare Tools:

**Code-Suche:**
- search_code: Durchsuche Java/Python/SQL Code nach relevanten Dateien
- read_file: Lese den Inhalt einer Datei
- list_files: Liste Dateien in einem Verzeichnis auf
- trace_java_references: Verfolge Java-Klassenhierarchien (Interfaces, Parent-Klassen)
"""

    if handbook_available:
        base += """
**Handbuch:**
- search_handbook: Durchsuche das Handbuch nach Service-Dokumentation
- get_service_info: Hole Service-Details aus dem Handbuch
"""

    base += """
**Wissen & Dokumente:**
- search_skills: Durchsuche die Wissensbasen der aktiven Skills
- search_pdf: Durchsuche hochgeladene PDF-Dokumente
"""

    if settings.internal_fetch.enabled:
        base += """
**HTTP/URL-Abruf (Internal Fetch):**
- internal_fetch: Ruft eine URL ab und gibt den Inhalt zurück (HTML, JSON, Text)
- internal_search: Ruft eine URL ab und durchsucht den Inhalt nach einem Pattern
- http_request: Führt HTTP-Requests aus (wie curl) - GET, POST, PUT, DELETE, PATCH mit Body und Headers

Nutze diese Tools um:
- Interne/Intranet-Seiten abzurufen
- REST-APIs aufzurufen (GET, POST, etc.)
- Webseiten zu durchsuchen
- Daten von URLs zu holen
"""

    if settings.github.enabled:
        base += """
**GitHub (Code-Suche & Repository):**
- github_search_code: Durchsucht Code in ALLEN Repos nach Beispielen, Patterns, Funktionen
- github_list_repos: Listet Repositories einer Organisation
- github_list_prs: Pull Requests eines Repos auflisten
- github_pr_diff: Code-Änderungen eines PRs anzeigen
- github_get_file: Dateiinhalt von GitHub holen (aus Branch/Commit)
- github_recent_commits: Letzte Commits eines Branches

Nutze github_search_code wenn der User nach:
- Code-Beispielen sucht ("wie wird X implementiert", "Beispiele für Y")
- Patterns oder Best Practices sucht
- Wissen will wo eine Funktion/Klasse verwendet wird
- Nach ähnlichen Implementierungen sucht
"""

    # Git tools are always available (Git is required)
    base += """
**Git (Lokales Repository):**
- git_status: Zeigt geänderte/ungetrackte Dateien, aktueller Branch
- git_diff: Zeigt Code-Änderungen (Working Dir, Staged, zwischen Commits)
- git_log: Commit-Historie anzeigen (mit Filter nach Autor, Datei, Zeit)
- git_branch_list: Alle Branches auflisten
- git_blame: Wer hat welche Zeile geändert?
- git_show_commit: Vollständige Commit-Details mit Diff

WICHTIG: git_* Tools sind für LOKALE Repos. Für REMOTE GitHub: github_* Tools verwenden.
Nutze git_status/git_diff wenn der User wissen will was sich geändert hat.
Nutze git_blame um herauszufinden wer Code geschrieben hat.
"""

    if settings.docker_sandbox.enabled:
        base += """
**Docker Sandbox (Sichere Code-Ausführung):**
- docker_execute_python: Führt Python-Code in isoliertem Container aus (stateless)
- docker_session_create: Erstellt persistente Session (Variablen bleiben erhalten)
- docker_session_execute: Führt Code in Session aus (mit persistenten Variablen)
- docker_session_list: Listet aktive Sessions
- docker_session_close: Schließt eine Session
- docker_upload_file: Lädt Datei in Session hoch (Base64)
- docker_list_packages: Zeigt verfügbare Python-Pakete

WANN NUTZEN:
- Benutzer bittet um Code-Ausführung: "encodiere in Base64", "berechne SHA256 Hash"
- Datenverarbeitung: JSON parsen, CSV verarbeiten, Regex testen
- Mathematische Berechnungen
- Testen von Code-Snippets
- Daten transformieren oder konvertieren

BEISPIELE:
- "Encodiere 'Hello' in Base64" → docker_execute_python(code="import base64; print(base64.b64encode(b'Hello').decode())")
- "Berechne SHA256 von 'password'" → docker_execute_python(code="import hashlib; print(hashlib.sha256(b'password').hexdigest())")

FÜR MEHRERE OPERATIONEN: Erstelle eine Session mit docker_session_create, dann docker_session_execute für jeden Schritt.
Variablen bleiben zwischen Aufrufen erhalten!
"""

    if db_available:
        base += f"""
**Datenbank (DB2):**
Die DB2-Datenbank ist aktiviert und verbunden ({settings.database.host}:{settings.database.port}/{settings.database.database}).
- query_database: Führe eine SELECT-Abfrage aus (nur SELECT erlaubt, readonly)
- list_database_tables: Liste alle Tabellen im Schema auf
- describe_database_table: Zeige Spalten, Typen und Constraints einer Tabelle

WICHTIG: Nutze query_database um Daten abzufragen. Beispiel: query_database(query="SELECT * FROM tabelle FETCH FIRST 10 ROWS ONLY")
"""

    base += """
### Tool-Aufrufe

**WICHTIG - Tool-Aufruf-Format:**
Wenn du ein Tool aufrufen willst, formatiere es EXAKT so:

```
[TOOL_CALLS][{"name": "tool_name", "arguments": {"param1": "value1", "param2": "value2"}}]
```

Beispiele:
- `[TOOL_CALLS][{"name": "search_code", "arguments": {"query": "PaymentService", "language": "java"}}]`
- `[TOOL_CALLS][{"name": "read_file", "arguments": {"path": "src/Main.java"}}]`
- `[TOOL_CALLS][{"name": "http_request", "arguments": {"url": "https://api.example.com", "method": "POST", "body": "{\"key\": \"value\"}"}}]`

Rufe immer nur EIN Tool pro Nachricht auf. Warte auf das Ergebnis bevor du das nächste Tool aufrufst.

### Vorgehensweise bei jeder Anfrage:

1. **Verstehen**: Was genau wird gefragt? Hat der User bereits Pfade/Dateien genannt?
2. **Planen**: Welche Tools brauche ich in welcher Reihenfolge?
3. **Ausführen**: Tools einzeln aufrufen und Ergebnis abwarten, bevor das nächste Tool gerufen wird.
4. **Antworten**: Erst wenn alle nötigen Infos vorliegen, die Antwort formulieren.

### WICHTIG: Direkte Pfade vs. Suche

**Wenn der User explizite Pfade/Ordner/Dateien nennt:**
- Verwende list_files und read_file DIREKT mit den genannten Pfaden
- KEINE search_code aufrufen - der User kennt bereits die Dateien!
- Beispiel: "Lies die Dateien in /app/models" → list_files("/app/models"), dann read_file für jede

**Nur wenn der User KEINE Pfade nennt (vage Anfragen):**
- Bei Code-Fragen ohne Pfad: search_code um relevanten Code zu finden
- Bei Handbuch-Fragen: search_handbook
- Bei PDF-Dokumenten: search_pdf

**Immer beachten:**
- Bei komplexen Klassen: trace_java_references für Hierarchien
- Lese jede Datei nur EINMAL - der Inhalt bleibt im Kontext verfügbar

### Beispiel-Abläufe:

**Beispiel 1** — Benutzer: "Lies und bearbeite die Dateien im Ordner /src/models"
→ User hat Pfad genannt → KEIN search_code!
→ Tool: list_files(path="/src/models")
→ Tool: read_file(path="/src/models/user.py"), read_file(path="/src/models/order.py"), etc.
→ Dann bearbeiten gemäß Anfrage

**Beispiel 2** — Benutzer: "Was macht die Klasse PaymentService?"
→ Kein Pfad genannt → search_code ist hier korrekt
→ Tool: search_code(query="PaymentService", language="java", top_k=3)
→ Ergebnis zeigt Pfad: src/payment/PaymentService.java
→ Tool: read_file(path="src/payment/PaymentService.java")
→ Antwort: "PaymentService ist zuständig für..."

**Beispiel 3** — Benutzer: "Erstelle einen JUnit-Test für OrderValidator"
→ Kein Pfad genannt → search_code um die Klasse zu finden
→ Tool: search_code(query="OrderValidator", language="java")
→ Tool: read_file(path="src/order/OrderValidator.java")
→ Antwort mit vollständigem JUnit-5-Test in ```java Block.
"""

    if mode == AgentMode.READ_ONLY:
        return base + """
MODUS: Nur Lesen
Du kannst keine Dateien schreiben oder bearbeiten.
Gib Code-Vorschläge als Markdown-Codeblöcke aus.
Datenbank-Abfragen (SELECT) sind erlaubt.
"""

    elif mode == AgentMode.WRITE_WITH_CONFIRM:
        return base + """
MODUS: Schreiben mit Bestätigung
Zusätzliche Tools:
- write_file: Erstelle oder überschreibe eine DATEI (benötigt Bestätigung) - NUR für Dateien mit Endung!
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)
- create_directory: Erstelle einen ORDNER (benötigt Bestätigung) - NUR für Verzeichnisse!
- batch_write_files: WICHTIG! Schreibt MEHRERE Dateien mit EINER Bestätigung. Nutze wenn du 2+ Dateien erstellen musst!

**MEHRERE DATEIEN ERSTELLEN:**
Wenn du mehrere Dateien erstellen musst (z.B. bei einem Design-Konzept), nutze IMMER batch_write_files!
Format: batch_write_files(files='[{"path": "src/A.java", "content": "..."}, {"path": "src/B.java", "content": "..."}]')
→ User bestätigt EINMAL für alle Dateien

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file oder batch_write_files

Der User muss Datei-Operationen bestätigen bevor sie ausgeführt werden.
Datenbank-Abfragen (SELECT) sind ohne Bestätigung erlaubt.
"""

    elif mode == AgentMode.PLAN_THEN_EXECUTE and not plan_approved:
        return base + """
MODUS: Planungsphase
Du befindest dich in der Planungsphase. Datei-Änderungen sind noch NICHT erlaubt.

**Deine Aufgabe:**
1. Nutze Read-Tools (search_code, read_file, etc.) um den relevanten Code zu analysieren.
2. Erstelle einen strukturierten Implementierungsplan.
3. Schreibe deinen fertigen Plan EXAKT in folgendem Format:

[PLAN]
**Aufgabe:** <Kurzbeschreibung der Aufgabe>

**Analysierte Dateien:**
- `<Dateipfad>`: <Was wurde darin gefunden>

**Implementierungsschritte:**
1. **`<Dateipfad>`** – <Was wird geändert und warum>
2. ...

**Erwartete Auswirkungen:**
- <Auswirkung 1>
[/PLAN]

Schreibe NUR den [PLAN]-Block als deine finale Antwort. Führe keine Datei-Änderungen durch.
"""

    elif mode == AgentMode.PLAN_THEN_EXECUTE and plan_approved:
        return base + """
MODUS: Ausführungsphase (Plan genehmigt)
Der User hat deinen Plan genehmigt.
Zusätzliche Tools:
- write_file: Erstelle eine einzelne DATEI (benötigt Bestätigung)
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)
- create_directory: Erstelle einen ORDNER
- batch_write_files: BEVORZUGT! Schreibt MEHRERE Dateien mit EINER Bestätigung!

**WICHTIG - NUTZE BATCH_WRITE_FILES FÜR MEHRERE DATEIEN:**
Wenn dein Plan mehrere Dateien erstellt/ändert, nutze batch_write_files um sie ALLE auf einmal zu schreiben!
→ Statt 5x write_file (5 Bestätigungen) → 1x batch_write_files (1 Bestätigung)
Format: batch_write_files(files='[{"path": "...", "content": "..."}, ...]')

**VOLLSTÄNDIGE PLAN-AUSFÜHRUNG:**
Du MUSST ALLE Dateien des Plans erstellen - nicht nach der ersten aufhören!
- Sammle ALLE zu erstellenden Dateien
- Nutze batch_write_files um sie in EINEM Aufruf zu schreiben
- User bestätigt einmal, alle Dateien werden erstellt

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende batch_write_files (oder write_file für einzelne)
"""

    elif mode == AgentMode.DEBUG:
        return base + """
MODUS: Debug & Fehleranalyse
Du hilfst beim systematischen Verstehen und Lösen von Fehlern. Keine Datei-Änderungen erlaubt.

**Dein Vorgehen:**
1. **Verstehen**: Stelle gezielte Rückfragen bevor du analysierst. Nutze das Tool `suggest_answers` um dem User Antwort-Optionen anzubieten.
2. **Nachstellen**: Nutze Log-Tools, Code-Suche und Datenbank-Abfragen um den Fehler zu reproduzieren.
3. **Analysieren**: Suche nach Root-Cause im Code, Konfiguration und Logs.
4. **Lösungsvorschlag**: Erkläre die Ursache und schlage Korrekturen als Codeblöcke vor (keine Datei-Schreiboperationen).

**Rückfragen mit suggest_answers:**
Wenn du mehr Kontext brauchst, rufe `suggest_answers` auf BEVOR du mit der Analyse beginnst:
- Formuliere eine klare Frage
- Gib 3-5 konkrete Antwort-Optionen vor
- Der User kann eine Option wählen oder frei antworten

**Typische Rückfragen:**
- Wann tritt der Fehler auf? (immer / sporadisch / nach bestimmten Aktionen)
- In welcher Umgebung? (dev / test / prod)
- Gibt es eine Fehlermeldung/Exception? (ja, welche / nein, nur falsches Verhalten)
- Ist das Verhalten neu? (seit letztem Deployment / schon immer / nach Konfigurationsänderung)

Verfügbare Diagnose-Tools: search_code, read_file, search_handbook, Log-Tools, Datenbank-Abfragen (SELECT).
"""

    else:  # AUTONOMOUS
        return base + """
MODUS: Autonom
Zusätzliche Tools:
- write_file: Erstelle eine einzelne DATEI
- edit_file: Bearbeite eine Datei
- create_directory: Erstelle einen ORDNER
- batch_write_files: Schreibt mehrere Dateien in einem Aufruf (effizienter!)

**MEHRERE DATEIEN:**
Bei mehreren Dateien nutze batch_write_files für bessere Performance.
Format: batch_write_files(files='[{"path": "...", "content": "..."}, ...]')

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file oder batch_write_files

Du kannst Dateien ohne Bestätigung schreiben/bearbeiten.
Sei vorsichtig und mache nur notwendige Änderungen.
"""
