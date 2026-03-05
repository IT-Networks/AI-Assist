"""
Agent API Routes - Endpunkte für Agent-Chat mit Tool-Calling.

Features:
- Agent-Chat mit Tool-Ausführung
- Schreib-Operationen mit Bestätigung
- Modus-Wechsel (read_only, write_with_confirm, autonomous)
- Session-Verwaltung
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uuid

from app.core.config import settings


router = APIRouter(prefix="/api/agent", tags=["agent"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class AgentChatRequest(BaseModel):
    """Anfrage für Agent-Chat."""
    message: str = Field(..., min_length=1, description="User-Nachricht")
    session_id: Optional[str] = Field(None, description="Session-ID (neu wenn leer)")
    model: Optional[str] = Field(None, description="LLM-Modell")
    skill_ids: Optional[List[str]] = Field(None, description="Skill-IDs zum Aktivieren")


class AgentModeRequest(BaseModel):
    """Anfrage zum Ändern des Agent-Modus."""
    mode: str = Field(..., description="Neuer Modus: read_only, write_with_confirm, autonomous")


class AgentConfirmRequest(BaseModel):
    """Anfrage zur Bestätigung einer Schreib-Operation."""
    confirmed: bool = Field(..., description="True = ausführen, False = abbrechen")


class AgentModeResponse(BaseModel):
    """Antwort mit aktuellem Agent-Modus."""
    session_id: str
    mode: str
    available_modes: List[str]


class AgentSessionResponse(BaseModel):
    """Antwort mit Session-Details."""
    session_id: str
    mode: str
    active_skills: List[str]
    tool_calls_count: int
    pending_confirmation: Optional[Dict[str, Any]]


class AgentEventData(BaseModel):
    """Server-Sent Event Daten."""
    type: str
    data: Any


# ══════════════════════════════════════════════════════════════════════════════
# SSE Streaming Chat
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
async def agent_chat(request: AgentChatRequest):
    """
    Agent-Chat mit Tool-Calling als Server-Sent Events.

    Der Agent kann Tools aufrufen um Informationen zu sammeln oder
    Dateien zu bearbeiten. Bei Schreib-Operationen wird eine Bestätigung
    verlangt (je nach Modus).

    Event-Typen:
    - token: Streaming-Token vom LLM
    - tool_start: Tool-Aufruf gestartet
    - tool_result: Tool-Ergebnis
    - confirm_required: Bestätigung für Schreib-Op benötigt
    - error: Fehler
    - done: Fertig

    Returns:
        SSE-Stream mit AgentEvents
    """
    from app.agent.orchestrator import get_agent_orchestrator, AgentEventType

    orchestrator = get_agent_orchestrator()

    # Session erstellen falls nötig
    session_id = request.session_id or str(uuid.uuid4())

    # Skills aktivieren falls angegeben
    if request.skill_ids:
        orchestrator.set_active_skills(session_id, request.skill_ids)

    async def event_generator():
        """Generiert SSE-Events aus dem Agent-Loop."""
        try:
            gen = orchestrator.process(
                session_id=session_id,
                user_message=request.message,
                model=request.model
            )

            async for event in gen:
                event_data = {
                    "type": event.type.value,
                    "session_id": session_id,
                    "data": event.data
                }
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

                # Bei CONFIRM_REQUIRED pausieren wir hier
                # Das Frontend muss dann /confirm aufrufen
                if event.type == AgentEventType.CONFIRM_REQUIRED:
                    # Wir brechen hier ab und warten auf /confirm
                    yield f"data: {json.dumps({'type': 'waiting_for_confirmation', 'session_id': session_id}, ensure_ascii=False)}\n\n"
                    return

        except Exception as e:
            error_event = {
                "type": "error",
                "session_id": session_id,
                "data": {"error": str(e)}
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": session_id
        }
    )


@router.post("/chat/sync")
async def agent_chat_sync(request: AgentChatRequest) -> Dict[str, Any]:
    """
    Agent-Chat ohne Streaming (synchron).

    Sammelt alle Events und gibt sie als Liste zurück.
    Nützlich für programmatische Aufrufe.

    Returns:
        Dict mit session_id, events, final_response
    """
    from app.agent.orchestrator import get_agent_orchestrator, AgentEventType

    orchestrator = get_agent_orchestrator()
    session_id = request.session_id or str(uuid.uuid4())

    if request.skill_ids:
        orchestrator.set_active_skills(session_id, request.skill_ids)

    events = []
    final_response = ""
    pending_confirmation = None

    try:
        gen = orchestrator.process(
            session_id=session_id,
            user_message=request.message,
            model=request.model
        )

        async for event in gen:
            event_dict = event.to_dict()
            events.append(event_dict)

            if event.type == AgentEventType.TOKEN:
                final_response += event.data if isinstance(event.data, str) else ""
            elif event.type == AgentEventType.CONFIRM_REQUIRED:
                pending_confirmation = event.data
                break
            elif event.type == AgentEventType.DONE:
                if isinstance(event.data, dict):
                    final_response = event.data.get("response", final_response)

    except Exception as e:
        events.append({"type": "error", "data": {"error": str(e)}})

    return {
        "session_id": session_id,
        "events": events,
        "response": final_response,
        "pending_confirmation": pending_confirmation
    }


# ══════════════════════════════════════════════════════════════════════════════
# Confirmation Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/confirm/{session_id}")
async def confirm_operation(
    session_id: str,
    request: AgentConfirmRequest
) -> Dict[str, Any]:
    """
    Bestätigt oder lehnt eine ausstehende Schreib-Operation ab.

    Wird nach einem confirm_required Event aufgerufen.

    Args:
        session_id: Session-ID
        request: Bestätigung (True/False)

    Returns:
        Status der Operation
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if not state.pending_confirmation:
        raise HTTPException(
            status_code=400,
            detail="Keine ausstehende Bestätigung für diese Session"
        )

    tool_call = state.pending_confirmation

    if request.confirmed:
        # Operation ausführen
        try:
            result = await orchestrator._execute_confirmed_operation(
                tool_call.result.confirmation_data
            )
            state.pending_confirmation = None

            if result.success:
                return {
                    "status": "executed",
                    "message": f"Operation '{tool_call.name}' ausgeführt",
                    "data": result.data
                }
            else:
                return {
                    "status": "error",
                    "message": f"Operation fehlgeschlagen: {result.error}"
                }
        except Exception as e:
            state.pending_confirmation = None
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Operation abbrechen
        state.pending_confirmation = None
        return {
            "status": "cancelled",
            "message": f"Operation '{tool_call.name}' abgebrochen"
        }


# ══════════════════════════════════════════════════════════════════════════════
# Mode Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/mode/{session_id}", response_model=AgentModeResponse)
async def get_mode(session_id: str) -> AgentModeResponse:
    """Gibt den aktuellen Agent-Modus einer Session zurück."""
    from app.agent.orchestrator import get_agent_orchestrator, AgentMode

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    return AgentModeResponse(
        session_id=session_id,
        mode=state.mode.value,
        available_modes=[m.value for m in AgentMode]
    )


@router.put("/mode/{session_id}", response_model=AgentModeResponse)
async def set_mode(
    session_id: str,
    request: AgentModeRequest
) -> AgentModeResponse:
    """
    Setzt den Agent-Modus für eine Session.

    Modi:
    - read_only: Nur Lese-Operationen erlaubt
    - write_with_confirm: Schreiben mit Bestätigung
    - autonomous: Schreiben ohne Bestätigung (Vorsicht!)
    """
    from app.agent.orchestrator import get_agent_orchestrator, AgentMode

    # Modus validieren
    try:
        mode = AgentMode(request.mode)
    except ValueError:
        valid_modes = [m.value for m in AgentMode]
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger Modus: {request.mode}. Erlaubt: {valid_modes}"
        )

    # File-Operations muss aktiviert sein für Schreibmodi
    if mode != AgentMode.READ_ONLY and not settings.file_operations.enabled:
        raise HTTPException(
            status_code=400,
            detail="Datei-Operationen sind nicht aktiviert. Setze file_operations.enabled=true in config.yaml"
        )

    orchestrator = get_agent_orchestrator()
    orchestrator.set_mode(session_id, mode)

    return AgentModeResponse(
        session_id=session_id,
        mode=mode.value,
        available_modes=[m.value for m in AgentMode]
    )


# ══════════════════════════════════════════════════════════════════════════════
# Session Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/session/{session_id}", response_model=AgentSessionResponse)
async def get_session(session_id: str) -> AgentSessionResponse:
    """Gibt Details einer Agent-Session zurück."""
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    pending = None
    if state.pending_confirmation:
        tc = state.pending_confirmation
        pending = {
            "id": tc.id,
            "name": tc.name,
            "arguments": tc.arguments,
            "confirmation_data": tc.result.confirmation_data if tc.result else None
        }

    return AgentSessionResponse(
        session_id=session_id,
        mode=state.mode.value,
        active_skills=list(state.active_skill_ids),
        tool_calls_count=len(state.tool_calls_history),
        pending_confirmation=pending
    )


@router.delete("/session/{session_id}")
async def clear_session(session_id: str) -> Dict[str, str]:
    """Löscht eine Agent-Session."""
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    orchestrator.clear_session(session_id)

    return {"message": f"Session '{session_id}' gelöscht"}


@router.post("/session/new")
async def create_session(
    mode: Optional[str] = Query("read_only", description="Initialer Modus"),
    skill_ids: Optional[str] = Query(None, description="Komma-getrennte Skill-IDs")
) -> AgentSessionResponse:
    """
    Erstellt eine neue Agent-Session.

    Args:
        mode: Initialer Modus (read_only, write_with_confirm, autonomous)
        skill_ids: Skill-IDs zum Aktivieren

    Returns:
        Neue Session-Details
    """
    from app.agent.orchestrator import get_agent_orchestrator, AgentMode

    session_id = str(uuid.uuid4())
    orchestrator = get_agent_orchestrator()

    # Modus setzen
    try:
        agent_mode = AgentMode(mode)
        orchestrator.set_mode(session_id, agent_mode)
    except ValueError:
        pass  # Default bleibt read_only

    # Skills aktivieren
    if skill_ids:
        ids = [s.strip() for s in skill_ids.split(",") if s.strip()]
        orchestrator.set_active_skills(session_id, ids)

    state = orchestrator._get_state(session_id)

    return AgentSessionResponse(
        session_id=session_id,
        mode=state.mode.value,
        active_skills=list(state.active_skill_ids),
        tool_calls_count=0,
        pending_confirmation=None
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tool Information
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/tools")
async def list_tools(
    include_write_ops: bool = Query(False, description="Schreib-Tools einbeziehen")
) -> Dict[str, Any]:
    """
    Listet alle verfügbaren Agent-Tools auf.

    Args:
        include_write_ops: Ob Schreib-Operationen einbezogen werden sollen

    Returns:
        Dict mit Tool-Liste und OpenAI-Schemas
    """
    from app.agent.tools import get_tool_registry

    registry = get_tool_registry()

    tools_info = []
    for tool in registry.tools.values():
        if not include_write_ops and tool.is_write_operation:
            continue

        tools_info.append({
            "name": tool.name,
            "description": tool.description,
            "category": tool.category.value,
            "is_write_operation": tool.is_write_operation,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required
                }
                for p in tool.parameters
            ]
        })

    return {
        "tools": tools_info,
        "count": len(tools_info),
        "openai_schemas": registry.get_openai_schemas(include_write_ops=include_write_ops)
    }


# ══════════════════════════════════════════════════════════════════════════════
# Direct Tool Execution (für Testing)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/tools/{tool_name}/execute")
async def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Führt ein Tool direkt aus (für Testing).

    Schreib-Operationen geben nur einen Preview zurück,
    keine tatsächliche Ausführung.

    Args:
        tool_name: Name des Tools
        arguments: Tool-Argumente

    Returns:
        Tool-Ergebnis
    """
    from app.agent.tools import get_tool_registry

    registry = get_tool_registry()

    if tool_name not in registry.tools:
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_name}' nicht gefunden"
        )

    tool = registry.tools[tool_name]

    if tool.is_write_operation:
        # Nur Preview für Schreib-Ops
        result = await registry.execute(tool_name, **arguments)
        return {
            "tool": tool_name,
            "is_preview": True,
            "requires_confirmation": result.requires_confirmation,
            "success": result.success,
            "data": result.data,
            "confirmation_data": result.confirmation_data,
            "error": result.error
        }
    else:
        result = await registry.execute(tool_name, **arguments)
        return {
            "tool": tool_name,
            "success": result.success,
            "data": result.data,
            "error": result.error
        }
