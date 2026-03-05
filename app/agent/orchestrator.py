"""
Agent Orchestrator - Koordiniert den Agent-Loop mit Tool-Calls.

Der Orchestrator:
1. Nimmt User-Nachrichten entgegen
2. Baut den Kontext aus aktiven Skills
3. Ruft das LLM mit Tool-Definitionen auf
4. Führt Tool-Calls aus
5. Bei Schreib-Ops: Wartet auf User-Bestätigung
6. Wiederholt bis fertig oder max_iterations erreicht
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
import asyncio

from app.agent.tools import ToolRegistry, ToolResult, get_tool_registry
from app.core.config import settings
from app.services.llm_client import SYSTEM_PROMPT


class AgentMode(str, Enum):
    """Betriebsmodus des Agents."""
    READ_ONLY = "read_only"           # Nur Lese-Operationen
    WRITE_WITH_CONFIRM = "write_with_confirm"  # Schreiben mit Bestätigung
    AUTONOMOUS = "autonomous"          # Schreiben ohne Bestätigung (gefährlich)


class AgentEventType(str, Enum):
    """Typen von Agent-Events."""
    TOKEN = "token"                    # Streaming-Token
    TOOL_START = "tool_start"          # Tool wird ausgeführt
    TOOL_RESULT = "tool_result"        # Tool-Ergebnis
    CONFIRM_REQUIRED = "confirm_required"  # User-Bestätigung benötigt
    CONFIRMED = "confirmed"            # User hat bestätigt
    CANCELLED = "cancelled"            # User hat abgelehnt
    ERROR = "error"                    # Fehler
    DONE = "done"                      # Fertig


@dataclass
class AgentEvent:
    """Ein Event das vom Agent emittiert wird."""
    type: AgentEventType
    data: Any = None

    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "data": self.data
        }


@dataclass
class ToolCall:
    """Ein Tool-Call vom LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]
    result: Optional[ToolResult] = None
    confirmed: Optional[bool] = None


@dataclass
class AgentState:
    """Zustand einer Agent-Session."""
    session_id: str
    mode: AgentMode = AgentMode.READ_ONLY
    active_skill_ids: Set[str] = field(default_factory=set)
    pending_confirmation: Optional[ToolCall] = None
    tool_calls_history: List[ToolCall] = field(default_factory=list)
    context_items: List[str] = field(default_factory=list)
    # Konversations-Historie für Multi-Turn Chats
    messages_history: List[Dict[str, str]] = field(default_factory=list)
    max_history_messages: int = 20  # Letzte N Nachrichten behalten


class AgentOrchestrator:
    """
    Koordiniert den Agent-Loop ähnlich wie Claude Code.
    """

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        max_iterations: int = 10,
        max_tool_calls_per_iteration: int = 5
    ):
        self.tools = tool_registry or get_tool_registry()
        self.max_iterations = max_iterations
        self.max_tool_calls_per_iter = max_tool_calls_per_iteration
        self._states: Dict[str, AgentState] = {}

    def _get_state(self, session_id: str) -> AgentState:
        """Holt oder erstellt den State für eine Session."""
        if session_id not in self._states:
            self._states[session_id] = AgentState(session_id=session_id)
        return self._states[session_id]

    def set_mode(self, session_id: str, mode: AgentMode) -> None:
        """Setzt den Modus für eine Session."""
        state = self._get_state(session_id)
        state.mode = mode

    def set_active_skills(self, session_id: str, skill_ids: List[str]) -> None:
        """Setzt die aktiven Skills für eine Session."""
        state = self._get_state(session_id)
        state.active_skill_ids = set(skill_ids)

    async def process(
        self,
        session_id: str,
        user_message: str,
        model: Optional[str] = None
    ) -> AsyncGenerator[AgentEvent, Optional[bool]]:
        """
        Verarbeitet eine User-Nachricht im Agent-Loop.

        Yieldet AgentEvents für das Frontend.
        Bei CONFIRM_REQUIRED wartet der Generator auf eine Antwort via send().

        Args:
            session_id: Session-ID
            user_message: Nachricht des Users
            model: Optional: LLM-Modell

        Yields:
            AgentEvent für jeden Schritt
        """
        from app.services.llm_client import llm_client
        from app.services.skill_manager import get_skill_manager

        state = self._get_state(session_id)
        include_write_ops = state.mode != AgentMode.READ_ONLY

        # System-Prompt bauen
        system_prompt = SYSTEM_PROMPT

        # Skill-Prompts hinzufügen
        if state.active_skill_ids:
            try:
                skill_mgr = get_skill_manager()
                for sid in state.active_skill_ids:
                    skill_mgr.activate_skill(session_id, sid)
                skill_prompt = skill_mgr.build_system_prompt(session_id)
                if skill_prompt:
                    system_prompt += f"\n\n{skill_prompt}"
            except Exception:
                pass

        # Tool-Definitionen
        tool_schemas = self.tools.get_openai_schemas(include_write_ops=include_write_ops)

        # Agent-Instruktionen
        agent_instructions = self._build_agent_instructions(state.mode)
        system_prompt += f"\n\n{agent_instructions}"

        # Messages aufbauen
        messages = [
            {"role": "system", "content": system_prompt}
        ]

        # Bisherige Kontext-Items hinzufügen
        if state.context_items:
            context_block = "\n\n".join(state.context_items[-10:])  # Letzte 10
            messages.append({
                "role": "system",
                "content": f"=== KONTEXT AUS VORHERIGEN TOOL-AUFRUFEN ===\n{context_block}"
            })

        # Konversations-Historie hinzufügen (für Multi-Turn)
        for hist_msg in state.messages_history[-state.max_history_messages:]:
            messages.append(hist_msg)

        # Aktuelle User-Nachricht hinzufügen
        messages.append({"role": "user", "content": user_message})

        # User-Nachricht in Historie speichern
        state.messages_history.append({"role": "user", "content": user_message})

        # Agent-Loop
        has_used_tools = False

        for iteration in range(self.max_iterations):
            try:
                # LLM aufrufen (nicht-streamend für Tool-Calls)
                # Tool-Phase: Schnelles Modell für Suche/Tool-Aufrufe
                response = await self._call_llm_with_tools(
                    messages, tool_schemas, model, is_tool_phase=True
                )

                # Antwort-Text yielden
                if response.get("content"):
                    yield AgentEvent(AgentEventType.TOKEN, response["content"])

                # Tool-Calls verarbeiten
                tool_calls = response.get("tool_calls", [])
                if not tool_calls:
                    # Keine weiteren Tool-Calls -> Analyse-Phase
                    assistant_response = response.get("content", "")

                    # Wenn Tools verwendet wurden, finale Analyse mit großem Modell
                    if has_used_tools and settings.llm.analysis_model and not model:
                        # Letzte Antwort verwerfen, neu generieren mit Analyse-Modell
                        analysis_response = await self._call_llm_with_tools(
                            messages, [], None, is_tool_phase=False
                        )
                        assistant_response = analysis_response.get("content", assistant_response)

                    # Assistant-Antwort in Historie speichern
                    if assistant_response:
                        state.messages_history.append({
                            "role": "assistant",
                            "content": assistant_response
                        })

                    yield AgentEvent(AgentEventType.DONE, {
                        "response": assistant_response,
                        "tool_calls_count": len(state.tool_calls_history)
                    })
                    return

                # Tools ausführen
                for tc in tool_calls[:self.max_tool_calls_per_iter]:
                    tool_call = ToolCall(
                        id=tc.get("id", f"call_{len(state.tool_calls_history)}"),
                        name=tc["function"]["name"],
                        arguments=json.loads(tc["function"]["arguments"])
                    )

                    yield AgentEvent(AgentEventType.TOOL_START, {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments
                    })

                    # Tool ausführen
                    result = await self.tools.execute(
                        tool_call.name,
                        **tool_call.arguments
                    )
                    tool_call.result = result
                    has_used_tools = True

                    # Bestätigung benötigt?
                    if result.requires_confirmation and state.mode == AgentMode.WRITE_WITH_CONFIRM:
                        state.pending_confirmation = tool_call

                        yield AgentEvent(AgentEventType.CONFIRM_REQUIRED, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "confirmation_data": result.confirmation_data
                        })

                        # Warten auf Bestätigung
                        confirmed = yield
                        tool_call.confirmed = confirmed

                        if confirmed:
                            # Operation ausführen
                            exec_result = await self._execute_confirmed_operation(
                                result.confirmation_data
                            )
                            if exec_result.success:
                                yield AgentEvent(AgentEventType.CONFIRMED, {
                                    "id": tool_call.id,
                                    "message": "Operation ausgeführt"
                                })
                                state.context_items.append(
                                    f"[{tool_call.name}] Ausgeführt: {result.confirmation_data.get('path', '')}"
                                )
                            else:
                                yield AgentEvent(AgentEventType.ERROR, {
                                    "id": tool_call.id,
                                    "error": exec_result.error
                                })
                        else:
                            yield AgentEvent(AgentEventType.CANCELLED, {
                                "id": tool_call.id,
                                "message": "Operation abgebrochen"
                            })

                        state.pending_confirmation = None

                    else:
                        # Normales Tool-Ergebnis
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "success": result.success,
                            "data": result.to_context()[:2000]  # Truncate
                        })

                        # Kontext speichern
                        if result.success:
                            state.context_items.append(result.to_context())

                    state.tool_calls_history.append(tool_call)

                # Messages für nächste Iteration aktualisieren
                messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": tool_calls
                })

                for tc in tool_calls:
                    tool_call = next(
                        (t for t in state.tool_calls_history if t.id == tc.get("id")),
                        None
                    )
                    if tool_call and tool_call.result:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": tool_call.result.to_context()[:5000]
                        })

            except Exception as e:
                yield AgentEvent(AgentEventType.ERROR, {"error": str(e)})
                return

        # Max iterations erreicht
        yield AgentEvent(AgentEventType.DONE, {
            "response": "Maximale Iterationen erreicht.",
            "tool_calls_count": len(state.tool_calls_history)
        })

    async def _call_llm_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        model: Optional[str] = None,
        is_tool_phase: bool = True
    ) -> Dict:
        """
        Ruft das LLM mit Tool-Definitionen auf.

        Args:
            messages: Chat-Nachrichten
            tools: Tool-Definitionen (leer für finale Antwort)
            model: Explizites Modell (überschreibt automatische Auswahl)
            is_tool_phase: True = Tool-Phase (schnelles Modell), False = Analyse-Phase (großes Modell)
        """
        import httpx

        # Modell-Auswahl: Explizit > Phase-spezifisch > Default
        if model:
            selected_model = model
        elif is_tool_phase and tools and settings.llm.tool_model:
            selected_model = settings.llm.tool_model
        elif not is_tool_phase and settings.llm.analysis_model:
            selected_model = settings.llm.analysis_model
        else:
            selected_model = settings.llm.default_model

        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        payload = {
            "model": selected_model,
            "messages": messages,
            "temperature": settings.llm.temperature,
            "max_tokens": settings.llm.max_tokens,
        }

        # Tools nur hinzufügen wenn vorhanden
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(
            timeout=settings.llm.timeout_seconds,
            verify=settings.llm.verify_ssl
        ) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        message = choice.get("message", {})

        return {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", [])
        }

    async def _execute_confirmed_operation(self, confirmation_data: Dict) -> ToolResult:
        """Führt eine bestätigte Operation aus."""
        from app.services.file_manager import get_file_manager

        operation = confirmation_data.get("operation")
        path = confirmation_data.get("path")

        if operation == "write_file":
            content = confirmation_data.get("content")
            manager = get_file_manager()
            success = await manager.execute_write(path, content)
            return ToolResult(success=success, data=f"Datei geschrieben: {path}")

        elif operation == "edit_file":
            old_string = confirmation_data.get("old_string")
            new_string = confirmation_data.get("new_string")
            manager = get_file_manager()
            success = await manager.execute_edit(path, old_string, new_string)
            return ToolResult(success=success, data=f"Datei bearbeitet: {path}")

        else:
            return ToolResult(success=False, error=f"Unbekannte Operation: {operation}")

    def _build_agent_instructions(self, mode: AgentMode) -> str:
        """Baut die Agent-Instruktionen für den System-Prompt."""
        base = """
## Agent-Anweisungen

Du bist ein intelligenter Assistent mit Zugriff auf Tools.

Verfügbare Tools:
- search_code: Durchsuche Java/Python Code nach relevanten Dateien
- search_handbook: Durchsuche das Handbuch nach Service-Dokumentation
- search_skills: Durchsuche die Wissensbasen der aktiven Skills
- read_file: Lese den Inhalt einer Datei
- list_files: Liste Dateien in einem Verzeichnis auf
- get_service_info: Hole Service-Details aus dem Handbuch

Verwende Tools um Informationen zu sammeln, bevor du antwortest.
Bei Code-Fragen: Suche zuerst nach relevantem Code.
Bei Handbuch-Fragen: Suche zuerst im Handbuch.
"""

        if mode == AgentMode.READ_ONLY:
            return base + """
MODUS: Nur Lesen
Du kannst keine Dateien schreiben oder bearbeiten.
Gib Code-Vorschläge als Markdown-Codeblöcke aus.
"""

        elif mode == AgentMode.WRITE_WITH_CONFIRM:
            return base + """
MODUS: Schreiben mit Bestätigung
Zusätzliche Tools:
- write_file: Erstelle oder überschreibe eine Datei (benötigt Bestätigung)
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)

Der User muss Schreib-Operationen bestätigen bevor sie ausgeführt werden.
"""

        else:  # AUTONOMOUS
            return base + """
MODUS: Autonom
Zusätzliche Tools:
- write_file: Erstelle oder überschreibe eine Datei
- edit_file: Bearbeite eine Datei

Du kannst Dateien ohne Bestätigung schreiben/bearbeiten.
Sei vorsichtig und mache nur notwendige Änderungen.
"""

    def confirm_operation(self, session_id: str, confirmed: bool) -> bool:
        """
        Bestätigt oder lehnt eine ausstehende Operation ab.

        Args:
            session_id: Session-ID
            confirmed: True wenn bestätigt, False wenn abgelehnt

        Returns:
            True wenn eine Operation wartete
        """
        state = self._get_state(session_id)
        if state.pending_confirmation:
            state.pending_confirmation.confirmed = confirmed
            return True
        return False

    def clear_session(self, session_id: str) -> None:
        """Löscht den State einer Session."""
        self._states.pop(session_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_orchestrator: Optional[AgentOrchestrator] = None


def get_agent_orchestrator() -> AgentOrchestrator:
    """Gibt die Singleton-Instanz des Agent Orchestrators zurück."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator()
    return _orchestrator
