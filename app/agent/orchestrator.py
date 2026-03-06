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

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from app.agent.tools import ToolRegistry, ToolResult, get_tool_registry
from app.core.config import settings
from app.core.token_budget import TokenBudget, create_budget_from_config
from app.core.conversation_summarizer import get_summarizer
from app.services.llm_client import SYSTEM_PROMPT, _get_http_client, _RETRY_DELAYS, _is_retryable
from app.services.memory_store import get_memory_store
from app.utils.token_counter import estimate_tokens, estimate_messages_tokens


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
    USAGE = "usage"                    # Token-Nutzung
    COMPACTION = "compaction"          # Context wurde komprimiert
    DONE = "done"                      # Fertig


@dataclass
class TokenUsage:
    """Token-Nutzung einer LLM-Anfrage."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""  # stop, length, tool_calls, etc.
    model: str = ""
    truncated: bool = False  # True wenn wegen max_tokens abgebrochen


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
    max_history_messages: int = 50  # Erhöht - Summarizer kümmert sich um Kompression
    # Token-Tracking für aktuelle Anfrage
    current_usage: Optional[TokenUsage] = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    # Token Budget Management
    token_budget: Optional[TokenBudget] = None
    # Compaction Stats
    compaction_count: int = 0
    last_compaction_savings: int = 0
    # Loop-Prävention: Zählt wie oft eine Datei pro Request gelesen wurde (max 2x erlaubt)
    read_files_this_request: Dict[str, int] = field(default_factory=dict)
    # Abbruch-Flag für laufende Anfragen
    cancelled: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# Text-basierter Tool-Call-Parser (Fallback für Modelle ohne natives Tool-Calling)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_text_tool_calls(content: str, available_tools: List[Dict]) -> List[Dict]:
    """
    Parst Tool-Calls aus dem Text-Content von Modellen, die kein natives
    Tool-Calling unterstützen (z.B. Mistral, Qwen, OpenHermes).

    Unterstützte Formate:
    1. Mistral: [TOOL_CALLS] [{"name": "func", "arguments": {...}}]
    2. XML:     <tool_call>{"name": "func", "arguments": {...}}</tool_call>
    3. OpenHermes: <functioncall>{"name": "func", "arguments": {...}}</functioncall>
    4. JSON-Block: ```json\n{"tool": "func", ...}\n```
    """
    if not content:
        return []

    tool_names = {t["function"]["name"] for t in available_tools} if available_tools else set()
    parsed_calls = []

    # Format 1a: Mistral 678B Compact Format
    # [TOOL_CALLS]funcname{"arg": "val"}  (kein Leerzeichen, kein JSON-Array)
    mistral_compact_matches = re.findall(
        r'\[TOOL_CALLS\](\w+)(\{.*?\}|\[.*?\])',
        content,
        re.DOTALL
    )
    if mistral_compact_matches:
        for name, args_str in mistral_compact_matches:
            if not tool_names or name in tool_names:
                try:
                    args = json.loads(args_str)
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args_str
                        }
                    })
                except json.JSONDecodeError:
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": name, "arguments": args_str}
                    })
        if parsed_calls:
            print(f"[agent] Mistral 678B Compact Format erkannt: {len(parsed_calls)} calls")
            return parsed_calls

    # Format 1b: Mistral Standard Format
    # [TOOL_CALLS] [{"name": "...", "arguments": {...}}]
    mistral_match = re.search(
        r'\[TOOL_CALLS\]\s*(\[.*?\])',
        content,
        re.DOTALL
    )
    if mistral_match:
        try:
            calls = json.loads(mistral_match.group(1))
            if isinstance(calls, list):
                for call in calls:
                    name = call.get("name") or call.get("function")
                    args = call.get("arguments") or call.get("parameters") or {}
                    if name and (not tool_names or name in tool_names):
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
            if parsed_calls:
                print(f"[agent] Mistral Standard Format erkannt: {len(parsed_calls)} calls")
                return parsed_calls
        except (json.JSONDecodeError, KeyError):
            pass

    # Format 2: XML <tool_call> oder <functioncall>
    xml_patterns = [
        r'<tool_call>(.*?)</tool_call>',
        r'<functioncall>(.*?)</functioncall>',
        r'<function_calls>(.*?)</function_calls>',
        r'<invoke>(.*?)</invoke>',
    ]
    for pattern in xml_patterns:
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            try:
                call = json.loads(match.strip())
                name = call.get("name") or call.get("function") or call.get("tool_name")
                args = call.get("arguments") or call.get("parameters") or call.get("kwargs") or {}
                if name and (not tool_names or name in tool_names):
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args
                        }
                    })
            except (json.JSONDecodeError, KeyError):
                continue
    if parsed_calls:
        print(f"[agent] XML Tool-Call Format erkannt: {len(parsed_calls)} calls")
        return parsed_calls

    # Format 3: JSON-Codeblock mit Tool-Call Struktur
    json_blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n```', content, re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            # Prüfe ob es ein Tool-Call ist
            name = None
            args = {}
            if isinstance(data, dict):
                if "name" in data and ("arguments" in data or "parameters" in data):
                    name = data["name"]
                    args = data.get("arguments") or data.get("parameters") or {}
                elif "tool" in data:
                    name = data["tool"]
                    args = data.get("input") or data.get("arguments") or {}
            if name and (not tool_names or name in tool_names):
                parsed_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else args
                    }
                })
        except (json.JSONDecodeError, KeyError):
            continue
    if parsed_calls:
        print(f"[agent] JSON-Block Tool-Call Format erkannt: {len(parsed_calls)} calls")
        return parsed_calls

    # Format 4: Inline JSON mit bekanntem Tool-Namen
    # Suche nach {"name": "known_tool", ...} direkt im Text
    if tool_names:
        inline_matches = re.findall(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*\}', content)
        for match_name in inline_matches:
            if match_name in tool_names:
                # Versuche den vollständigen JSON-Block zu extrahieren
                pattern = r'\{[^{}]*"name"\s*:\s*"' + re.escape(match_name) + r'"[^{}]*\}'
                full_matches = re.findall(pattern, content, re.DOTALL)
                for fm in full_matches:
                    try:
                        call = json.loads(fm)
                        args = call.get("arguments") or call.get("parameters") or {}
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": match_name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
                    except (json.JSONDecodeError, KeyError):
                        continue
        if parsed_calls:
            print(f"[agent] Inline JSON Tool-Call Format erkannt: {len(parsed_calls)} calls")
            return parsed_calls

    return []


class AgentOrchestrator:
    """
    Koordiniert den Agent-Loop ähnlich wie Claude Code.
    """

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        max_iterations: int = 30,  # Erhöht von 10 auf 30 für komplexe Aufgaben
        max_tool_calls_per_iteration: int = 10  # Erhöht von 5 auf 10
    ):
        self.tools = tool_registry or get_tool_registry()
        self.max_iterations = max_iterations
        self.max_tool_calls_per_iter = max_tool_calls_per_iteration
        self._states: Dict[str, AgentState] = {}
        # Token Management Components
        self.memory_store = get_memory_store()
        self.summarizer = get_summarizer()

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
        # Gelesene Dateien für diese Anfrage zurücksetzen (Loop-Prävention)
        state.read_files_this_request = {}
        # Abbruch-Flag zurücksetzen
        state.cancelled = False
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

        # Token Budget initialisieren
        state.token_budget = create_budget_from_config()
        budget = state.token_budget

        # Messages aufbauen
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        budget.set("system", estimate_tokens(system_prompt))

        # Memory Context laden (relevante Fakten aus vorherigen Sessions)
        try:
            memory_context = await self.memory_store.get_context_injection(
                session_id, user_message, max_tokens=budget.memory_limit
            )
            if memory_context:
                messages.append({"role": "system", "content": memory_context})
                budget.set("memory", estimate_tokens(memory_context))
        except Exception as e:
            print(f"[agent] Memory loading failed: {e}")

        # Konversations-Historie hinzufügen (für Multi-Turn)
        # context_items werden NICHT als separate System-Message eingefügt,
        # da Tool-Results bereits korrekt als role="tool" Messages im Verlauf erscheinen.
        # Das verhindert Dopplung und reduziert LLM-Loop-Verhalten.
        history_tokens = 0
        for hist_msg in state.messages_history[-state.max_history_messages:]:
            messages.append(hist_msg)
            history_tokens += estimate_tokens(hist_msg.get("content", ""))
        budget.set("conversation", history_tokens)

        # Aktuelle User-Nachricht hinzufügen
        messages.append({"role": "user", "content": user_message})

        # User-Nachricht in Historie speichern
        state.messages_history.append({"role": "user", "content": user_message})

        # === COMPACTION CHECK ===
        # Wenn Budget zu voll, Konversation zusammenfassen
        if budget.needs_compaction():
            try:
                savings_estimate = self.summarizer.estimate_savings(messages)
                if savings_estimate.get("would_summarize"):
                    old_tokens = estimate_messages_tokens(messages)
                    messages = await self.summarizer.summarize_if_needed(
                        messages,
                        target_tokens=budget.available_total
                    )
                    new_tokens = estimate_messages_tokens(messages)
                    savings = old_tokens - new_tokens

                    state.compaction_count += 1
                    state.last_compaction_savings = savings

                    yield AgentEvent(AgentEventType.COMPACTION, {
                        "savings": savings,
                        "old_tokens": old_tokens,
                        "new_tokens": new_tokens,
                        "compaction_count": state.compaction_count
                    })

                    # Budget aktualisieren
                    budget.set("conversation", estimate_messages_tokens([
                        m for m in messages if m.get("role") not in ("system",)
                    ]))
            except Exception as e:
                print(f"[agent] Compaction failed: {e}")

        # Agent-Loop
        has_used_tools = False
        # Token-Tracking für diese Anfrage zurücksetzen
        state.current_usage = TokenUsage()
        request_prompt_tokens = 0
        request_completion_tokens = 0
        last_finish_reason = ""
        last_model = ""

        # Debug: Tool-Schemas Anzahl loggen
        print(f"[agent] Starting with {len(tool_schemas)} tools, mode={state.mode.value}")

        for iteration in range(self.max_iterations):
            try:
                # Abbruch prüfen
                if state.cancelled:
                    yield AgentEvent(AgentEventType.CANCELLED, {"message": "Anfrage wurde abgebrochen"})
                    return

                # LLM aufrufen (nicht-streamend für Tool-Calls)
                print(f"[agent] Iteration {iteration + 1}: Calling LLM with {len(tool_schemas)} tools")
                response = await self._call_llm_with_tools(
                    messages, tool_schemas, model, is_tool_phase=True
                )
                tool_calls = response.get("tool_calls", [])
                content = response.get("content", "")
                finish_reason = response.get("finish_reason", "")
                native_tools = response.get("native_tools", True)

                print(
                    f"[agent] LLM response: finish_reason={finish_reason!r}, "
                    f"tool_calls={len(tool_calls)}, content_len={len(content or '')}"
                )

                # Token-Nutzung akkumulieren
                usage = response.get("usage")
                if usage:
                    request_prompt_tokens += usage.prompt_tokens
                    request_completion_tokens += usage.completion_tokens
                    last_finish_reason = usage.finish_reason
                    last_model = usage.model

                # Fallback: Text-Tool-Call-Parser für Modelle ohne natives Tool-Calling
                # (z.B. Mistral-678B gibt Tool-Calls manchmal als Text zurück)
                if not tool_calls and content:
                    text_tool_calls = _parse_text_tool_calls(content, tool_schemas)
                    if text_tool_calls:
                        print(f"[agent] Text-Parser erkannte {len(text_tool_calls)} Tool-Calls im Content")
                        tool_calls = text_tool_calls
                        native_tools = False  # Text-geparst, kein natives tool_calls-Format
                        finish_reason = "tool_calls"

                # Tool-Calls verarbeiten
                if not tool_calls:
                    # Keine weiteren Tool-Calls -> Finale Antwort generieren
                    assistant_response = ""
                    final_usage = None

                    # Entscheiden ob wir streamen oder die vorhandene Antwort nutzen
                    should_stream = settings.llm.streaming
                    existing_content = content

                    # Wenn ein separates Analyse-Modell konfiguriert ist, muss neu angefragt werden
                    needs_new_request = (has_used_tools and settings.llm.analysis_model and not model)

                    if needs_new_request:
                        # Neue Anfrage mit Analyse-Modell
                        if should_stream:
                            stream_result = await self._stream_final_response_with_usage(messages)
                            async for token in stream_result["tokens"]:
                                assistant_response += token
                                yield AgentEvent(AgentEventType.TOKEN, token)
                            final_usage = stream_result["usage"].get("usage")
                        else:
                            analysis_response = await self._call_llm_with_tools(
                                messages, [], None, is_tool_phase=False
                            )
                            assistant_response = analysis_response.get("content", "")
                            final_usage = analysis_response.get("usage")
                            if assistant_response:
                                yield AgentEvent(AgentEventType.TOKEN, assistant_response)
                    elif should_stream and not existing_content:
                        # Keine Antwort vorhanden -> Stream anfordern
                        stream_result = await self._stream_final_response_with_usage(messages, model)
                        async for token in stream_result["tokens"]:
                            assistant_response += token
                            yield AgentEvent(AgentEventType.TOKEN, token)
                        final_usage = stream_result["usage"].get("usage")
                    else:
                        # Vorhandene Antwort nutzen (LLM hat direkt geantwortet ohne Tools)
                        assistant_response = existing_content
                        final_usage = usage
                        if assistant_response:
                            yield AgentEvent(AgentEventType.TOKEN, assistant_response)

                    # Finale Token-Nutzung akkumulieren
                    if final_usage and isinstance(final_usage, TokenUsage):
                        request_prompt_tokens += final_usage.prompt_tokens
                        request_completion_tokens += final_usage.completion_tokens
                        last_finish_reason = final_usage.finish_reason
                        last_model = final_usage.model

                    # Gesamte Token-Nutzung aktualisieren
                    state.total_prompt_tokens += request_prompt_tokens
                    state.total_completion_tokens += request_completion_tokens

                    # Assistant-Antwort in Historie speichern
                    if assistant_response:
                        state.messages_history.append({
                            "role": "assistant",
                            "content": assistant_response
                        })

                    # Token-Nutzung als Event senden
                    usage_data = {
                        "prompt_tokens": request_prompt_tokens,
                        "completion_tokens": request_completion_tokens,
                        "total_tokens": request_prompt_tokens + request_completion_tokens,
                        "finish_reason": last_finish_reason,
                        "model": last_model,
                        "truncated": last_finish_reason == "length",
                        "max_tokens": settings.llm.max_tokens,
                        # Session-Gesamtwerte
                        "session_total_prompt": state.total_prompt_tokens,
                        "session_total_completion": state.total_completion_tokens,
                        # Budget-Status für Context-Management
                        "budget": budget.get_status() if budget else None,
                        "compaction_count": state.compaction_count
                    }
                    yield AgentEvent(AgentEventType.USAGE, usage_data)

                    yield AgentEvent(AgentEventType.DONE, {
                        "response": assistant_response,
                        "tool_calls_count": len(state.tool_calls_history),
                        "usage": usage_data
                    })
                    return

                # Tools ausführen
                current_tool_calls_for_messages = []
                for tc in tool_calls[:self.max_tool_calls_per_iter]:
                    raw_args = tc["function"]["arguments"]
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            print(f"[agent] Malformed tool arguments JSON: {e} — raw: {raw_args[:100]}")
                            parsed_args = {}
                    else:
                        parsed_args = raw_args

                    tool_call = ToolCall(
                        id=tc.get("id", f"call_{len(state.tool_calls_history)}"),
                        name=tc["function"]["name"],
                        arguments=parsed_args
                    )

                    # Loop-Prävention: read_file max 2x pro Datei erlauben
                    if tool_call.name == "read_file":
                        file_path = tool_call.arguments.get("path", "")
                        read_count = state.read_files_this_request.get(file_path, 0)
                        if read_count >= 2:
                            print(f"[agent] Loop-Prävention: {file_path} wurde bereits {read_count}x gelesen, überspringe")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": f"[HINWEIS] Die Datei '{file_path}' wurde bereits {read_count}x gelesen. Bitte nutze den bereits erhaltenen Inhalt aus dem Kontext weiter oder verwende search_code für gezielte Suchen."
                            })
                            current_tool_calls_for_messages.append(tc)
                            continue
                        state.read_files_this_request[file_path] = read_count + 1

                    # Pro-Tool Modell ermitteln
                    tool_specific_model = settings.llm.tool_models.get(tool_call.name, "")
                    effective_model = tool_specific_model or last_model or settings.llm.tool_model or settings.llm.default_model

                    yield AgentEvent(AgentEventType.TOOL_START, {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "model": effective_model
                    })

                    # Tool ausführen
                    result = await self.tools.execute(
                        tool_call.name,
                        **tool_call.arguments
                    )
                    tool_call.result = result
                    has_used_tools = True
                    current_tool_calls_for_messages.append(tc)

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
                            "data": result.to_context()[:2000]  # Truncate für Frontend
                        })

                    state.tool_calls_history.append(tool_call)

                # Messages für nächste Iteration aktualisieren
                def _truncate_result(raw: str, max_chars: int = 20000) -> str:
                    if len(raw) > max_chars:
                        return raw[:max_chars] + f"\n\n[HINWEIS: Inhalt bei {max_chars} Zeichen abgeschnitten. Gesamtlänge: {len(raw)} Zeichen. Nutze read_file mit offset-Parameter für weitere Abschnitte.]"
                    return raw

                if native_tools:
                    # OpenAI-kompatibles Format: role="assistant" mit tool_calls + role="tool" Ergebnisse
                    # content muss None (nicht "") sein wenn tool_calls vorhanden - Mistral/OpenAI-Anforderung
                    messages.append({
                        "role": "assistant",
                        "content": content if content else None,
                        "tool_calls": current_tool_calls_for_messages
                    })
                    for tc in current_tool_calls_for_messages:
                        tc_id = tc.get("id")
                        tool_call_obj = next(
                            (t for t in state.tool_calls_history if t.id == tc_id),
                            None
                        )
                        if tool_call_obj and tool_call_obj.result:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": _truncate_result(tool_call_obj.result.to_context())
                            })
                else:
                    # Text-basiertes Format (Mistral-Compact, Qwen etc.):
                    # Kein tool_calls-Feld im assistant-Message, Ergebnisse als user-Message
                    messages.append({
                        "role": "assistant",
                        "content": content or ""
                    })
                    results_parts = []
                    for tc in current_tool_calls_for_messages:
                        tc_id = tc.get("id")
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        tool_call_obj = next(
                            (t for t in state.tool_calls_history if t.id == tc_id),
                            None
                        )
                        if tool_call_obj and tool_call_obj.result:
                            result_text = _truncate_result(tool_call_obj.result.to_context())
                            results_parts.append(f"### Tool-Ergebnis: {tool_name}\n{result_text}")
                    if results_parts:
                        messages.append({
                            "role": "user",
                            "content": "Tool-Ergebnisse:\n\n" + "\n\n---\n\n".join(results_parts)
                        })

            except Exception as e:
                yield AgentEvent(AgentEventType.ERROR, {"error": str(e)})
                return

        # Max iterations erreicht
        yield AgentEvent(AgentEventType.DONE, {
            "response": "Maximale Iterationen erreicht.",
            "tool_calls_count": len(state.tool_calls_history)
        })

    async def _stream_final_response(
        self,
        messages: List[Dict],
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Streamt die finale Antwort Token für Token.
        Verwendet das analysis_model wenn konfiguriert.
        """
        result = await self._stream_final_response_with_usage(messages, model)
        async for token in result["tokens"]:
            yield token

    async def _stream_final_response_with_usage(
        self,
        messages: List[Dict],
        model: Optional[str] = None
    ) -> Dict:
        """
        Streamt die finale Antwort und tracked Token-Nutzung.
        Mit Retry-Logik für Verbindungsabbrüche.

        Returns:
            Dict mit "tokens" (AsyncGenerator) und "usage" (TokenUsage nach Abschluss)
        """
        # Modell-Auswahl: User-Auswahl > Phase-spezifisch > Default
        if model:
            # User hat explizit ein Modell ausgewählt - das hat Vorrang
            selected_model = model
            print(f"[stream] Using user-selected model: {selected_model}")
        elif settings.llm.analysis_model:
            selected_model = settings.llm.analysis_model
            print(f"[stream] Using analysis_model: {selected_model}")
        else:
            selected_model = settings.llm.default_model
            print(f"[stream] Using default_model: {selected_model}")

        base_url = settings.llm.base_url.rstrip("/")

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        payload = {
            "model": selected_model,
            "messages": messages,
            "temperature": settings.llm.temperature,
            "max_tokens": settings.llm.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True}
        }

        # Container für Usage (wird während Streaming gefüllt)
        usage_container = {"usage": None, "finish_reason": ""}

        # Prompt-Tokens schätzen (ca. 4 Zeichen pro Token)
        prompt_text = "".join(m.get("content", "") or "" for m in messages)
        estimated_prompt_tokens = len(prompt_text) // 4

        async def token_generator():
            completion_tokens = 0
            completion_chars = 0
            last_exc = None
            for attempt, delay in enumerate([0] + _RETRY_DELAYS):
                if delay:
                    print(f"[agent] Stream Retry {attempt} nach {delay}s")
                    await asyncio.sleep(delay)
                try:
                    client = _get_http_client()
                    async with client.stream(
                        "POST",
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json=payload
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                import json as json_module
                                chunk = json_module.loads(raw)

                                # Check for usage in final chunk (OpenAI stream_options)
                                if "usage" in chunk and chunk["usage"]:
                                    usage_data = chunk["usage"]
                                    usage_container["usage"] = TokenUsage(
                                        prompt_tokens=usage_data.get("prompt_tokens", 0),
                                        completion_tokens=usage_data.get("completion_tokens", 0),
                                        total_tokens=usage_data.get("total_tokens", 0),
                                        finish_reason=usage_container["finish_reason"],
                                        model=selected_model,
                                        truncated=(usage_container["finish_reason"] == "length")
                                    )

                                choices = chunk.get("choices", [])
                                if choices:
                                    choice = choices[0]
                                    delta = choice.get("delta", {})
                                    token = delta.get("content", "")

                                    # finish_reason extrahieren
                                    if choice.get("finish_reason"):
                                        usage_container["finish_reason"] = choice["finish_reason"]

                                    if token:
                                        completion_chars += len(token)
                                        completion_tokens = completion_chars // 4  # ~4 chars per token
                                        yield token

                            except (ValueError, KeyError, IndexError):
                                continue
                    return  # Erfolgreich abgeschlossen
                except Exception as e:
                    last_exc = e
                    if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                        print(f"[agent] Stream unterbrochen: {e}, Retry {attempt + 1}")
                        continue
                    print(f"[agent] Stream Fehler (kein Retry): {e}")
                    break

            # Fallback: Wenn kein Usage vom Server, schätzen wir basierend auf Zeichenzahl
            if not usage_container["usage"]:
                usage_container["usage"] = TokenUsage(
                    prompt_tokens=estimated_prompt_tokens,  # Geschätzt aus Prompt-Länge
                    completion_tokens=completion_tokens,
                    total_tokens=estimated_prompt_tokens + completion_tokens,
                    finish_reason=usage_container["finish_reason"],
                    model=selected_model,
                    truncated=(usage_container["finish_reason"] == "length")
                )

        return {
            "tokens": token_generator(),
            "usage": usage_container  # Wird nach Streaming gefüllt
        }

    async def _call_llm_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        model: Optional[str] = None,
        is_tool_phase: bool = True
    ) -> Dict:
        """
        Ruft das LLM mit Tool-Definitionen auf.
        Mit Retry-Logik für Verbindungsabbrüche und 5xx Fehler.

        Args:
            messages: Chat-Nachrichten
            tools: Tool-Definitionen (leer für finale Antwort)
            model: Explizites Modell (überschreibt automatische Auswahl)
            is_tool_phase: True = Tool-Phase (schnelles Modell), False = Analyse-Phase (großes Modell)
        """

        # Modell-Auswahl: Pro-Tool > Phase-spezifisch > Explizit (Header-Dropdown) > Default
        selected_model = None

        # 1. Pro-Tool Modell prüfen
        if is_tool_phase and settings.llm.tool_models:
            for msg in reversed(messages):
                if msg.get("role") == "tool":
                    tool_call_id = msg.get("tool_call_id", "")
                    for prev_msg in messages:
                        for tc in prev_msg.get("tool_calls", []):
                            if tc.get("id") == tool_call_id:
                                tool_name = tc["function"]["name"]
                                if tool_name in settings.llm.tool_models:
                                    selected_model = settings.llm.tool_models[tool_name]
                                    break
                        if selected_model:
                            break
                    if selected_model:
                        break

        if not selected_model:
            # Priorität: User-Auswahl > Phase-spezifisch > Default
            if model:
                # User hat explizit ein Modell ausgewählt - das hat Vorrang
                selected_model = model
                print(f"[model] Using user-selected model: {selected_model}")
            elif is_tool_phase and tools and settings.llm.tool_model:
                selected_model = settings.llm.tool_model
                print(f"[model] Using tool_model: {selected_model}")
            elif not is_tool_phase and settings.llm.analysis_model:
                selected_model = settings.llm.analysis_model
                print(f"[model] Using analysis_model: {selected_model}")
            else:
                selected_model = settings.llm.default_model
                print(f"[model] Using default_model: {selected_model}")

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

        last_exc = None
        data = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                print(f"[agent] LLM Retry {attempt} nach {delay}s (Modell: {selected_model})")
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                break  # Erfolg
            except Exception as e:
                last_exc = e
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    print(f"[agent] LLM Fehler (Retry {attempt + 1}): {e}")
                    continue
                # Nicht wiederholbarer Fehler
                raise

        if data is None:
            raise last_exc or RuntimeError("LLM Aufruf fehlgeschlagen")

        # Debug: Rohe Antwort prüfen
        if "choices" not in data or not data["choices"]:
            print(f"[agent] WARNING: No choices in LLM response: {list(data.keys())}")
            return {"content": "", "tool_calls": [], "usage": TokenUsage(), "finish_reason": "error"}

        choice = data["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")
        tool_calls_in_msg = message.get("tool_calls", [])
        content = message.get("content", "")

        # Debug für Modelle mit Text-basierten Tool-Calls
        if finish_reason == "tool_calls" and not tool_calls_in_msg:
            print(
                f"[agent] WARNING: finish_reason='tool_calls' aber keine tool_calls im message-Objekt!\n"
                f"  Modell: {selected_model}\n"
                f"  Content (erste 500 Zeichen): {(content or '')[:500]}"
            )

        # Token-Nutzung extrahieren
        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            finish_reason=finish_reason,
            model=selected_model,
            truncated=(finish_reason == "length")
        )

        return {
            "content": content,
            "tool_calls": tool_calls_in_msg,
            "native_tools": bool(tool_calls_in_msg),  # True = LLM hat nativ tool_calls geliefert
            "usage": usage,
            "finish_reason": finish_reason
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

        elif operation == "query_database":
            from app.agent.tools import execute_confirmed_query
            query = confirmation_data.get("query")
            max_rows = confirmation_data.get("max_rows", 100)
            return await execute_confirmed_query(query, max_rows)

        else:
            return ToolResult(success=False, error=f"Unbekannte Operation: {operation}")

    def _build_agent_instructions(self, mode: AgentMode) -> str:
        """Baut die Agent-Instruktionen für den System-Prompt."""
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
### Anweisungen:

Verwende die passenden Tools um Informationen zu sammeln, bevor du antwortest.
- Bei Code-Fragen: Suche zuerst nach relevantem Code. Bei komplexen Klassen nutze trace_java_references.
- Bei Handbuch-Fragen: Suche zuerst im Handbuch.
- Bei PDF-Dokumenten: Durchsuche sie mit search_pdf.
- Bei Datenbank-Fragen: Nutze list_database_tables und describe_database_table um die Struktur zu verstehen.
- Lese jede Datei nur EINMAL - der Inhalt bleibt im Kontext verfügbar.
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
- write_file: Erstelle oder überschreibe eine Datei (benötigt Bestätigung)
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)

Der User muss Datei-Operationen bestätigen bevor sie ausgeführt werden.
Datenbank-Abfragen (SELECT) sind ohne Bestätigung erlaubt.
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

    def cancel_request(self, session_id: str) -> None:
        """Bricht die laufende Anfrage einer Session ab."""
        state = self._get_state(session_id)
        state.cancelled = True
        print(f"[agent] Anfrage für Session {session_id} abgebrochen")

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
