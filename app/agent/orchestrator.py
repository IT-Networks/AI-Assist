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

from app.agent.entity_tracker import EntityTracker
from app.agent.tools import ToolRegistry, ToolResult, get_tool_registry
from app.core.config import settings
from app.mcp.tool_bridge import get_tool_bridge, MCPToolBridge
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
    PLAN_THEN_EXECUTE = "plan_then_execute"    # Erst planen, dann mit Bestätigung ausführen
    DEBUG = "debug"                    # Fehler-Analyse: Rückfragen + Tools zum Nachstellen


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
    # Sub-Agent Events
    SUBAGENT_START = "subagent_start"      # Sub-Agent-Phase beginnt (Routing läuft)
    SUBAGENT_ROUTING = "subagent_routing"  # Routing fertig – ausgewählte Agenten bekannt
    SUBAGENT_DONE = "subagent_done"        # Ein Sub-Agent hat Ergebnis geliefert
    SUBAGENT_ERROR = "subagent_error"      # Sub-Agent fehlgeschlagen
    # Planning Events
    PLAN_READY = "plan_ready"              # Plan erstellt – wartet auf User-Genehmigung
    PLAN_APPROVED = "plan_approved"        # Plan genehmigt, Ausführung startet
    PLAN_REJECTED = "plan_rejected"        # Plan abgelehnt
    # Debug-Modus Events
    QUESTION = "question"                  # Agent stellt Rückfrage mit Vorschlägen


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
    compaction_attempted_while_full: bool = False  # Verhindert Spam wenn Komprimierung nicht hilft
    # Loop-Prävention: Zählt wie oft eine Datei pro Request gelesen wurde (max 2x erlaubt)
    read_files_this_request: Dict[str, int] = field(default_factory=dict)
    # Abbruch-Flag für laufende Anfragen
    cancelled: bool = False
    # Entity Tracker: Verfolgt gefundene Entitäten und ihre Quellen (Java ↔ Handbuch ↔ PDF)
    entity_tracker: EntityTracker = field(default_factory=EntityTracker)
    # Chat-Titel (wird aus erster User-Nachricht abgeleitet oder manuell gesetzt)
    title: str = ""
    # Planungsphase (PLAN_THEN_EXECUTE-Modus)
    pending_plan: Optional[str] = None    # Erstellter Plan, wartet auf Genehmigung
    plan_approved: bool = False           # True wenn User den Plan genehmigt hat


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

    # Debug: Wenn kein Tool-Call erkannt wurde, hilfreiche Info loggen
    if content and len(content) > 20:
        # Prüfe auf mögliche Tool-Call-Patterns die nicht gematcht wurden
        potential_patterns = [
            (r'\[TOOL', '[TOOL...'),
            (r'<tool', '<tool...'),
            (r'<function', '<function...'),
            (r'"name"\s*:', '"name":'),
            (r'"tool"\s*:', '"tool":'),
        ]
        found_hints = []
        for pattern, hint in potential_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                found_hints.append(hint)
        if found_hints:
            print(f"[agent] Text-Parser: Keine Tool-Calls erkannt, aber Hinweise gefunden: {found_hints}")
            print(f"[agent] Content-Anfang (100 chars): {content[:100]!r}")

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
        # MCP Tool Bridge (für Sequential Thinking und externe MCP-Server)
        self._mcp_bridge: Optional[MCPToolBridge] = None

    def _get_state(self, session_id: str) -> AgentState:
        """Holt oder erstellt den State für eine Session. Stellt bei Bedarf vom Disk wieder her."""
        if session_id not in self._states:
            state = AgentState(session_id=session_id)
            # Gespeicherten Chat vom Disk laden (Server-Neustart)
            try:
                from app.services.chat_store import load_chat
                saved = load_chat(session_id)
                if saved:
                    state.messages_history = saved.get("messages_history", [])
                    state.title = saved.get("title", "")
                    try:
                        state.mode = AgentMode(saved.get("mode", "read_only"))
                    except ValueError:
                        pass
            except Exception:
                pass
            self._states[session_id] = state
        return self._states[session_id]

    def set_mode(self, session_id: str, mode: AgentMode) -> None:
        """Setzt den Modus für eine Session."""
        state = self._get_state(session_id)
        state.mode = mode

    def set_active_skills(self, session_id: str, skill_ids: List[str]) -> None:
        """Setzt die aktiven Skills für eine Session."""
        state = self._get_state(session_id)
        state.active_skill_ids = set(skill_ids)

    async def _run_sub_agents_phase(
        self,
        user_message: str,
        model: Optional[str],
        messages: List[Dict],
        budget,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Führt die parallele Sub-Agent-Erkundungsphase aus.

        Sub-Agenten durchsuchen ihre spezialisierten Datenquellen parallel
        und liefern komprimierte Zusammenfassungen zurück.
        Diese werden als System-Message in den Main-Context injiziert,
        bevor der Main-Agent-Loop startet.

        Yieldet SUBAGENT_START / SUBAGENT_DONE / SUBAGENT_ERROR Events.
        """
        from app.agent.sub_agents import get_sub_agent_dispatcher
        from app.agent.sub_agent import format_sub_agent_results
        from app.services.llm_client import llm_client
        from app.utils.token_counter import estimate_tokens

        try:
            dispatcher = get_sub_agent_dispatcher()
        except Exception as e:
            print(f"[sub_agents] Dispatcher nicht verfügbar: {e}")
            return

        routing_model = (
            settings.sub_agents.routing_model
            or settings.llm.tool_model
            or settings.llm.default_model
        )

        # Phase 1: Routing läuft – noch keine Agenten bekannt
        yield AgentEvent(AgentEventType.SUBAGENT_START, {
            "message": "Intent-Routing läuft...",
            "routing_model": routing_model,
        })

        # Routing: welche Agenten werden benötigt?
        try:
            selected_agents = await dispatcher.classify_intent(user_message, llm_client)
        except Exception as e:
            print(f"[sub_agents] Routing fehlgeschlagen: {e}")
            yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {"error": f"Routing: {e}"})
            return

        # Phase 2: Routing fertig – ausgewählte Agenten bekannt
        yield AgentEvent(AgentEventType.SUBAGENT_ROUTING, {
            "agents": selected_agents,
            "routing_model": routing_model,
        })

        if not selected_agents:
            print("[sub_agents] Keine relevanten Agenten ermittelt – überspringe Phase")
            return

        # Phase 3: Ausgewählte Agenten parallel ausführen
        try:
            results = await dispatcher.dispatch_selected(
                query=user_message,
                agents=selected_agents,
                llm_client=llm_client,
                tool_registry=self.tools,
            )
        except Exception as e:
            print(f"[sub_agents] Dispatch fehlgeschlagen: {e}")
            yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {"error": str(e)})
            return

        # Events für jeden Sub-Agenten emittieren
        for result in results:
            if result.success:
                yield AgentEvent(AgentEventType.SUBAGENT_DONE, {
                    "agent": result.agent_name,
                    "findings_count": len(result.key_findings),
                    "sources_count": len(result.sources),
                    "duration_ms": result.duration_ms,
                })
            else:
                yield AgentEvent(AgentEventType.SUBAGENT_ERROR, {
                    "agent": result.agent_name,
                    "error": result.error,
                })

        # Ergebnisse als System-Message in den Haupt-Kontext injizieren
        context_block = format_sub_agent_results(results)
        if context_block:
            context_tokens = estimate_tokens(context_block)
            # Nur injizieren wenn Token-Budget ausreicht
            if budget.can_add("context", context_tokens):
                # Nach dem ersten System-Message (Haupt-Prompt) einfügen
                insert_pos = 1
                messages.insert(insert_pos, {
                    "role": "system",
                    "content": context_block,
                })
                budget.add("context", context_tokens)
                print(f"[sub_agents] Context injiziert: {context_tokens} Tokens")
            else:
                print(f"[sub_agents] Context zu groß ({context_tokens} Tokens), übersprungen")

    async def process(
        self,
        session_id: str,
        user_message: str,
        model: Optional[str] = None,
        context_selection: Optional[Any] = None,
    ) -> AsyncGenerator[AgentEvent, Optional[bool]]:
        """
        Verarbeitet eine User-Nachricht im Agent-Loop.

        Yieldet AgentEvents für das Frontend.
        Bei CONFIRM_REQUIRED wartet der Generator auf eine Antwort via send().

        Args:
            session_id: Session-ID
            user_message: Nachricht des Users
            model: Optional: LLM-Modell
            context_selection: Optional: Manuell ausgewählte Kontext-Elemente (ContextSelection)

        Yields:
            AgentEvent für jeden Schritt
        """
        from app.services.llm_client import llm_client
        from app.services.skill_manager import get_skill_manager
        import re

        state = self._get_state(session_id)
        # Gelesene Dateien für diese Anfrage zurücksetzen (Loop-Prävention)
        state.read_files_this_request = {}
        # Abbruch-Flag zurücksetzen
        state.cancelled = False

        # ── MCP Force-Capability Detection ────────────────────────────────────
        # Format: [MCP:capability_name] actual query
        forced_capability = None
        mcp_match = re.match(r'^\[MCP:(\w+)\]\s*(.+)$', user_message, re.DOTALL)
        if mcp_match:
            forced_capability = mcp_match.group(1)
            user_message = mcp_match.group(2).strip()
            print(f"[agent] Forced MCP capability: {forced_capability}")

        # ── Continue-Handling (nach Bestätigung) ───────────────────────────────
        # [CONTINUE] wird nach Schreibbestätigung gesendet um weitere Tools auszuführen
        is_continue = user_message.strip() == "[CONTINUE]"
        if is_continue:
            print("[agent] Continue nach Bestätigung erkannt")
            # Ersetze durch System-Hinweis statt User-Message
            user_message = (
                "Die letzte Datei-Operation wurde bestätigt und ausgeführt. "
                "Setze die Arbeit fort und führe die verbleibenden Schritte aus."
            )
        # ─────────────────────────────────────────────────────────────────────

        # Schreib-Ops: In Planungsphase nur erlaubt wenn Plan bereits genehmigt
        if state.mode == AgentMode.PLAN_THEN_EXECUTE:
            include_write_ops = state.plan_approved
        elif state.mode in (AgentMode.READ_ONLY, AgentMode.DEBUG):
            include_write_ops = False
        else:
            include_write_ops = True

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
                # Skill erfordert Planungsphase → Modus automatisch setzen
                if (
                    skill_mgr.requires_plan(session_id)
                    and state.mode not in (AgentMode.PLAN_THEN_EXECUTE,)
                    and not state.plan_approved
                ):
                    state.mode = AgentMode.PLAN_THEN_EXECUTE
                    include_write_ops = False
                    print(f"[agent] Skill erfordert Planungsphase → Modus auf PLAN_THEN_EXECUTE gesetzt")
            except Exception:
                pass

        # Tool-Definitionen
        tool_schemas = self.tools.get_openai_schemas(include_write_ops=include_write_ops)

        # MCP-Tools hinzufügen (Sequential Thinking, etc.)
        if settings.mcp.sequential_thinking_enabled or settings.mcp.enabled:
            try:
                if self._mcp_bridge is None:
                    self._mcp_bridge = get_tool_bridge()
                mcp_tools = self._mcp_bridge.get_tool_definitions()
                tool_schemas.extend(mcp_tools)
                if mcp_tools:
                    print(f"[agent] MCP tools added: {len(mcp_tools)}")
            except Exception as e:
                print(f"[agent] MCP bridge initialization failed: {e}")

        # Agent-Instruktionen
        agent_instructions = self._build_agent_instructions(state.mode, state.plan_approved)
        system_prompt += f"\n\n{agent_instructions}"

        # Planungsphase: Plan als Kontext injizieren wenn genehmigt
        if state.mode == AgentMode.PLAN_THEN_EXECUTE and state.plan_approved and state.pending_plan:
            system_prompt += f"\n\n## Genehmigter Ausführungsplan\n\n{state.pending_plan}\n\nFühre diesen Plan jetzt Schritt für Schritt aus."

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

        # Entity-Kontext: Bekannte Entitäten aus dieser Session (Java ↔ Handbuch ↔ PDF)
        entity_hint = state.entity_tracker.get_context_hint()
        if entity_hint:
            messages.append({"role": "system", "content": entity_hint})
            budget.set("memory", budget.used_memory + estimate_tokens(entity_hint))

        # Manuell vom Nutzer ausgewählte Kontext-Elemente (Explorer-Chips)
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
                    "Der Nutzer hat folgende Elemente explizit als Kontext ausgewählt. "
                    "Beziehe dich bevorzugt auf diese Quellen:\n"
                    + "\n".join(f"- {p}" for p in hint_parts)
                )
                messages.append({"role": "system", "content": ctx_msg})
                budget.set("memory", budget.used_memory + estimate_tokens(ctx_msg))
                print(f"[agent] Nutzer-Kontext injiziert: {hint_parts}")

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

        # Auto-Titel aus erster User-Nachricht ableiten
        if not state.title:
            state.title = user_message[:60] + ("…" if len(user_message) > 60 else "")

        # === COMPACTION CHECK ===
        # Wenn Budget zu voll (>80%), Konversation zusammenfassen.
        # Logik: Nur versuchen wenn noch nicht bei diesem Füllstand versucht wurde.
        # Nach erfolgreicher Komprimierung sinkt Budget unter 80% → Flag wird zurückgesetzt.
        if budget.needs_compaction():
            # Nur versuchen wenn nicht bereits bei vollem Budget versucht
            if not state.compaction_attempted_while_full:
                state.compaction_attempted_while_full = True
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

                        # Budget aktualisieren
                        budget.set("conversation", estimate_messages_tokens([
                            m for m in messages if m.get("role") not in ("system",)
                        ]))

                        # Nur Event emittieren wenn tatsächlich Tokens gespart wurden
                        if savings > 100:
                            yield AgentEvent(AgentEventType.COMPACTION, {
                                "savings": savings,
                                "old_tokens": old_tokens,
                                "new_tokens": new_tokens,
                                "compaction_count": state.compaction_count
                            })

                        # Wenn Budget jetzt unter Threshold, Flag zurücksetzen
                        if not budget.needs_compaction():
                            state.compaction_attempted_while_full = False
                except Exception as e:
                    print(f"[agent] Compaction failed: {e}")
        else:
            # Budget unter 80% → bereit für nächste Komprimierung wenn nötig
            state.compaction_attempted_while_full = False

        # === SUB-AGENT PHASE ===
        # Spezialisierte Sub-Agenten erkunden Datenquellen parallel,
        # bevor der Main-Agent seinen Loop startet.
        if (
            settings.sub_agents.enabled
            and len(user_message) >= settings.sub_agents.min_query_length
            and not forced_capability  # Skip sub-agents when forcing capability
        ):
            async for event in self._run_sub_agents_phase(
                user_message, model, messages, budget
            ):
                yield event

        # === FORCED CAPABILITY EXECUTION ===
        # Wenn User /brainstorm, /design etc. nutzt, direkt ausführen
        if forced_capability:
            print(f"[agent] Executing forced capability: {forced_capability}")

            if self._mcp_bridge is None:
                self._mcp_bridge = get_tool_bridge()

            # TOOL_START Event
            yield AgentEvent(AgentEventType.TOOL_START, {
                "id": f"forced_{forced_capability}",
                "name": forced_capability,
                "arguments": {"query": user_message},
                "model": "MCP"
            })

            try:
                # Capability direkt ausführen
                mcp_result = await self._mcp_bridge.call_tool(
                    forced_capability,
                    {"query": user_message, "context": None}
                )

                # Ergebnis formatieren
                if mcp_result.get("success"):
                    output = mcp_result.get("formatted_output") or mcp_result.get("result") or str(mcp_result)

                    # TOOL_RESULT Event
                    yield AgentEvent(AgentEventType.TOOL_RESULT, {
                        "id": f"forced_{forced_capability}",
                        "name": forced_capability,
                        "success": True,
                        "data": output[:500] if len(output) > 500 else output
                    })

                    # Streaming-Antwort mit formatiertem Output
                    for chunk in output.split('\n'):
                        yield AgentEvent(AgentEventType.TOKEN, chunk + '\n')

                    # Handoff-Vorschlag wenn vorhanden
                    next_cap = mcp_result.get("next_capability")
                    if next_cap:
                        yield AgentEvent(AgentEventType.TOKEN, f"\n\n---\n➡️ **Nächster Schritt:** `/{next_cap}` für die Weiterführung\n")

                else:
                    error_msg = mcp_result.get("error", "Unbekannter Fehler")
                    yield AgentEvent(AgentEventType.TOOL_RESULT, {
                        "id": f"forced_{forced_capability}",
                        "name": forced_capability,
                        "success": False,
                        "data": error_msg
                    })
                    yield AgentEvent(AgentEventType.ERROR, {"error": f"Capability {forced_capability} fehlgeschlagen: {error_msg}"})

            except Exception as e:
                yield AgentEvent(AgentEventType.ERROR, {"error": f"Capability-Ausführung fehlgeschlagen: {str(e)}"})

            yield AgentEvent(AgentEventType.DONE, {})
            return  # Beende hier - kein normaler Agent-Loop

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

                    # === PLANUNGSPHASE: Plan extrahieren ===
                    # In der Planungsphase (vor Genehmigung) kein Streaming –
                    # vollständige Antwort holen und [PLAN]...[/PLAN]-Block extrahieren.
                    if state.mode == AgentMode.PLAN_THEN_EXECUTE and not state.plan_approved:
                        plan_response = existing_content
                        # Nur neue Requests zählen – usage wurde im Loop bereits akkumuliert.
                        # Wenn existing_content leer ist, muss ein neuer LLM-Call gemacht werden.
                        extra_plan_usage: Optional[TokenUsage] = None
                        if not plan_response:
                            plan_resp = await self._call_llm_with_tools(
                                messages, [], None, is_tool_phase=False
                            )
                            plan_response = plan_resp.get("content", "")
                            extra_plan_usage = plan_resp.get("usage")  # Noch nicht gezählt

                        # Token-Nutzung für neuen Call akkumulieren (nur wenn neu)
                        if extra_plan_usage and isinstance(extra_plan_usage, TokenUsage):
                            request_prompt_tokens += extra_plan_usage.prompt_tokens
                            request_completion_tokens += extra_plan_usage.completion_tokens
                            last_finish_reason = extra_plan_usage.finish_reason
                            last_model = extra_plan_usage.model

                        state.total_prompt_tokens += request_prompt_tokens
                        state.total_completion_tokens += request_completion_tokens

                        usage_data = {
                            "prompt_tokens": request_prompt_tokens,
                            "completion_tokens": request_completion_tokens,
                            "total_tokens": request_prompt_tokens + request_completion_tokens,
                            "finish_reason": last_finish_reason,
                            "model": last_model,
                            "truncated": last_finish_reason == "length",
                            "max_tokens": settings.llm.max_tokens,
                            "session_total_prompt": state.total_prompt_tokens,
                            "session_total_completion": state.total_completion_tokens,
                            "budget": budget.get_status() if budget else None,
                            "compaction_count": state.compaction_count,
                        }

                        plan_match = re.search(r'\[PLAN\](.*?)\[/PLAN\]', plan_response, re.DOTALL)
                        if plan_match:
                            plan_text = plan_match.group(1).strip()
                            state.pending_plan = plan_text

                            # In Konversations-Historie speichern
                            state.messages_history.append({
                                "role": "assistant",
                                "content": plan_response
                            })
                            try:
                                from app.services.chat_store import save_chat
                                save_chat(
                                    session_id=session_id,
                                    title=state.title or "Chat",
                                    messages_history=state.messages_history,
                                    mode=state.mode.value,
                                )
                            except Exception:
                                pass

                            yield AgentEvent(AgentEventType.PLAN_READY, {
                                "plan": plan_text,
                                "full_response": plan_response,
                            })
                            yield AgentEvent(AgentEventType.USAGE, usage_data)
                            yield AgentEvent(AgentEventType.DONE, {
                                "response": plan_response,
                                "is_plan": True,
                                "tool_calls_count": len(state.tool_calls_history),
                                "usage": usage_data,
                            })
                            return
                        # Kein [PLAN]-Block → als normale Antwort ausgeben (Fallback)
                        if plan_response:
                            yield AgentEvent(AgentEventType.TOKEN, plan_response)
                        state.messages_history.append({
                            "role": "assistant",
                            "content": plan_response
                        })
                        yield AgentEvent(AgentEventType.USAGE, usage_data)
                        yield AgentEvent(AgentEventType.DONE, {
                            "response": plan_response,
                            "tool_calls_count": len(state.tool_calls_history),
                            "usage": usage_data,
                        })
                        return

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
                        # Chat auf Disk persistieren
                        try:
                            from app.services.chat_store import save_chat
                            save_chat(
                                session_id=session_id,
                                title=state.title or "Chat",
                                messages_history=state.messages_history,
                                mode=state.mode.value,
                            )
                        except Exception:
                            pass

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

                    # Pro-Tool Modell ermitteln (Priorität: pro-Tool > tool_model > default)
                    tool_specific_model = settings.llm.tool_models.get(tool_call.name, "")
                    effective_model = tool_specific_model or settings.llm.tool_model or settings.llm.default_model

                    yield AgentEvent(AgentEventType.TOOL_START, {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "model": effective_model
                    })

                    # ── suggest_answers: Display-only Tool im Debug-Modus ────
                    if tool_call.name == "suggest_answers":
                        question = tool_call.arguments.get("question", "")
                        options = tool_call.arguments.get("options", [])
                        yield AgentEvent(AgentEventType.QUESTION, {
                            "question": question,
                            "options": options,
                        })
                        result = ToolResult(
                            success=True,
                            data={"status": "options_presented_to_user", "count": len(options)}
                        )
                        tool_call.result = result
                        has_used_tools = True
                        current_tool_calls_for_messages.append(tc)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result.to_context()
                        })
                        state.tool_calls_history.append(tool_call)
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "result": result.to_context()[:500],
                            "success": True,
                        })
                        continue  # Nächsten Tool-Call verarbeiten
                    # ────────────────────────────────────────────────────────

                    # Tool ausführen - MCP-Tools speziell behandeln
                    # Capabilities: brainstorm, design, implement, analyze, capability_handoff
                    MCP_CAPABILITY_TOOLS = {
                        "sequential_thinking", "seq_think",
                        "brainstorm", "design", "implement", "analyze",
                        "capability_handoff"
                    }
                    if tool_call.name.startswith("mcp_") or tool_call.name in MCP_CAPABILITY_TOOLS:
                        # MCP-Tool über Bridge ausführen
                        if self._mcp_bridge is None:
                            self._mcp_bridge = get_tool_bridge()
                        mcp_result = await self._mcp_bridge.call_tool(
                            tool_call.name,
                            tool_call.arguments
                        )
                        result = ToolResult(
                            success=mcp_result.get("success", False),
                            data=mcp_result.get("result") or mcp_result.get("formatted_output") or mcp_result,
                            error=mcp_result.get("error")
                        )
                    else:
                        # Standard-Tool über ToolRegistry ausführen
                        result = await self.tools.execute(
                            tool_call.name,
                            **tool_call.arguments
                        )
                    tool_call.result = result
                    has_used_tools = True
                    current_tool_calls_for_messages.append(tc)

                    # Entity Tracker: Entitäten aus Tool-Result extrahieren
                    if result.success:
                        _source_path = (
                            tool_call.arguments.get("path", "")
                            or tool_call.arguments.get("query", "")
                        )
                        state.entity_tracker.extract_from_tool_result(
                            tool_name=tool_call.name,
                            result_text=result.to_context(),
                            source_path=_source_path
                        )

                    # Bestätigung benötigt?
                    if result.requires_confirmation and state.mode == AgentMode.WRITE_WITH_CONFIRM:
                        state.pending_confirmation = tool_call

                        # Tool-Card auf "Wartet auf Bestätigung" setzen
                        yield AgentEvent(AgentEventType.TOOL_RESULT, {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "success": True,
                            "data": f"⏳ Wartet auf Bestätigung: {result.confirmation_data.get('path', '')}"
                        })

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
                                    "message": f"✓ Datei geschrieben: {result.confirmation_data.get('path', '')}"
                                })
                                # Tool-Result aktualisieren auf Erfolg
                                result = ToolResult(
                                    success=True,
                                    data=f"Datei erfolgreich geschrieben: {result.confirmation_data.get('path', '')}"
                                )
                                tool_call.result = result
                                state.context_items.append(
                                    f"[{tool_call.name}] Ausgeführt: {result.confirmation_data.get('path', '')}"
                                )
                                # WICHTIG: Finales TOOL_RESULT senden um UI zu aktualisieren
                                yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                    "id": tool_call.id,
                                    "name": tool_call.name,
                                    "success": True,
                                    "data": f"✓ Datei geschrieben: {result.confirmation_data.get('path', '')}"
                                })
                            else:
                                yield AgentEvent(AgentEventType.ERROR, {
                                    "id": tool_call.id,
                                    "error": exec_result.error
                                })
                                # Tool-Result aktualisieren auf Fehler
                                result = ToolResult(
                                    success=False,
                                    error=exec_result.error
                                )
                                tool_call.result = result
                                # Finales TOOL_RESULT für UI
                                yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                    "id": tool_call.id,
                                    "name": tool_call.name,
                                    "success": False,
                                    "data": f"❌ Fehler: {exec_result.error}"
                                })
                        else:
                            yield AgentEvent(AgentEventType.CANCELLED, {
                                "id": tool_call.id,
                                "message": "Operation abgebrochen"
                            })
                            # Bei Ablehnung: Tool als abgebrochen markieren
                            result = ToolResult(
                                success=False,
                                data="Operation vom Benutzer abgebrochen"
                            )
                            tool_call.result = result
                            # Finales TOOL_RESULT für UI
                            yield AgentEvent(AgentEventType.TOOL_RESULT, {
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "success": False,
                                "data": "⚠️ Operation abgebrochen"
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

                # Proaktive Cross-Source-Anreicherung:
                # Wenn search_code eine Java-Klasse findet, automatisch search_handbook aufrufen
                # und umgekehrt — nur wenn die Quelle noch nicht bekannt ist.
                if current_tool_calls_for_messages:
                    last_tc = state.tool_calls_history[-1] if state.tool_calls_history else None
                    if last_tc and last_tc.result and last_tc.result.success:
                        enrichments = await self._enrich_from_entity_tracker(
                            state, last_tc.name, last_tc.result.to_context()
                        )
                        if enrichments:
                            messages.append({
                                "role": "system",
                                "content": "=== AUTO-QUERVERWEISE ===\n" + "\n\n".join(enrichments)
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

        # Phase-spezifische Temperature: Tool-Phase deterministisch, Analyse-Phase konfigurierbar
        is_tool_phase = bool(tools)
        if is_tool_phase:
            effective_temperature = settings.llm.tool_temperature
        else:
            cfg_analysis_temp = settings.llm.analysis_temperature
            effective_temperature = cfg_analysis_temp if cfg_analysis_temp >= 0 else settings.llm.temperature

        payload = {
            "model": selected_model,
            "messages": messages,
            "temperature": effective_temperature,
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

    async def _enrich_from_entity_tracker(
        self,
        state: AgentState,
        last_tool_name: str,
        result_text: str
    ) -> List[str]:
        """
        Proaktive Cross-Source-Anreicherung nach einem Tool-Result.

        Wenn search_code eine Java-Klasse findet → search_handbook (wenn noch kein Eintrag)
        Wenn search_handbook einen Service findet → search_code (wenn noch kein Code)

        Gibt Anreicherungs-Texte zurück (werden als AUTO-QUERVERWEISE eingefügt).
        """
        # Nur für Code/Handbuch-Suchergebnisse relevant
        if last_tool_name not in ("search_code", "read_file", "search_handbook", "get_service_info"):
            return []

        candidates = state.entity_tracker.get_entities_for_enrichment(last_tool_name, result_text)
        if not candidates:
            return []

        enrichments = []
        for entity in candidates[:2]:  # Max 2 Entitäten pro Iteration
            try:
                if last_tool_name in ("search_code", "read_file") and "handbuch" not in entity.sources:
                    # Java-Klasse gefunden → Handbuch-Eintrag suchen
                    hw_result = await self.tools.execute("search_handbook", query=entity.name, top_k=1)
                    if hw_result.success and hw_result.data and len(hw_result.data) > 50:
                        entity.sources["handbuch"] = entity.name
                        enrichments.append(
                            f"[Handbuch-Querverweis für {entity.name}]\n{hw_result.data[:1500]}"
                        )

                elif last_tool_name in ("search_handbook", "get_service_info") and "java" not in entity.sources:
                    # Handbuch-Service gefunden → Java-Code suchen
                    java_result = await self.tools.execute("search_code", query=entity.name, top_k=1)
                    if java_result.success and java_result.data and len(java_result.data) > 50:
                        entity.sources["java"] = entity.name
                        enrichments.append(
                            f"[Java-Querverweis für {entity.name}]\n{java_result.data[:1500]}"
                        )
            except Exception as e:
                print(f"[entity_enrichment] Fehler bei Anreicherung für {entity.name}: {e}")

        return enrichments

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

    def _build_agent_instructions(self, mode: AgentMode, plan_approved: bool = False) -> str:
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

        # Git-Tools sind immer verfügbar (Git ist vorausgesetzt)
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

1. **Verstehen**: Was genau wird gefragt? Welche Information fehlt mir?
2. **Planen**: Welche Tools brauche ich in welcher Reihenfolge?
3. **Ausführen**: Tools einzeln aufrufen und Ergebnis abwarten, bevor das nächste Tool gerufen wird.
4. **Antworten**: Erst wenn alle nötigen Infos vorliegen, die Antwort formulieren.

Verwende die passenden Tools um Informationen zu sammeln, bevor du antwortest.
- Bei Code-Fragen: Suche zuerst nach relevantem Code. Bei komplexen Klassen nutze trace_java_references.
- Bei Handbuch-Fragen: Suche zuerst im Handbuch.
- Bei PDF-Dokumenten: Durchsuche sie mit search_pdf.
- Bei Datenbank-Fragen: Nutze list_database_tables und describe_database_table um die Struktur zu verstehen.
- Lese jede Datei nur EINMAL - der Inhalt bleibt im Kontext verfügbar.

### Beispiel-Abläufe:

**Beispiel 1** — Benutzer: "Was macht die Klasse PaymentService?"
→ Plane: Ich brauche den Java-Code → search_code, dann ggf. read_file.
→ Tool: search_code(query="PaymentService", language="java", top_k=3)
→ Ergebnis zeigt Pfad: src/payment/PaymentService.java
→ Tool: read_file(path="src/payment/PaymentService.java")
→ Antwort: "PaymentService ist zuständig für..."

**Beispiel 2** — Benutzer: "Erstelle einen JUnit-Test für OrderValidator"
→ Plane: Erst Quellcode lesen, dann Test generieren.
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

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file

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
- write_file: Erstelle oder überschreibe eine DATEI (benötigt Bestätigung) - NUR für Dateien mit Endung!
- edit_file: Bearbeite eine Datei (benötigt Bestätigung)
- create_directory: Erstelle einen ORDNER - NUR für Verzeichnisse ohne Dateiendung!

**WICHTIG - VOLLSTÄNDIGE PLAN-AUSFÜHRUNG:**
Du MUSST den gesamten Plan abarbeiten und ALLE Dateien erstellen/ändern, nicht nur eine!
- Führe JEDEN Schritt des Plans aus, einen nach dem anderen
- Nach jeder bestätigten Datei-Operation: Fahre SOFORT mit dem nächsten Schritt fort
- Höre NICHT nach der ersten Datei auf - arbeite den kompletten Plan ab
- Erst wenn ALLE Schritte erledigt sind, gib eine Zusammenfassung

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file
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
- write_file: Erstelle oder überschreibe eine DATEI - NUR für Dateien mit Endung!
- edit_file: Bearbeite eine Datei
- create_directory: Erstelle einen ORDNER - NUR für Verzeichnisse!

**ORDNER vs DATEI:**
- Pfad OHNE Dateiendung (z.B. `src/components`) → verwende create_directory
- Pfad MIT Dateiendung (z.B. `src/app.py`) → verwende write_file

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
        """Löscht den State einer Session (Speicher + Disk)."""
        self._states.pop(session_id, None)
        try:
            from app.services.chat_store import delete_chat
            delete_chat(session_id)
        except Exception:
            pass


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
