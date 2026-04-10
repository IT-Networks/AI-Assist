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
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator
import uuid

from app.core.config import settings


router = APIRouter(prefix="/api/agent", tags=["agent"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class ContextSelection(BaseModel):
    """Vom Nutzer manuell ausgewählte Kontext-Elemente."""
    java_files: List[str] = Field(default_factory=list, description="Ausgewählte Java-Dateipfade")
    python_files: List[str] = Field(default_factory=list, description="Ausgewählte Python-Dateipfade")
    pdf_ids: List[str] = Field(default_factory=list, description="Ausgewählte PDF-IDs")
    handbook_services: List[str] = Field(default_factory=list, description="Ausgewählte Handbuch-Service-IDs")


ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/mp3", "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/flac", "audio/x-flac"}

class ChatAttachment(BaseModel):
    """Ein Bild- oder Audio-Anhang (Base64-kodiert)."""
    type: Literal["image", "audio"] = Field(..., description="Medientyp")
    mime: str = Field(..., description="MIME-Type, z.B. image/png, audio/webm")
    data: str = Field(..., description="Base64-kodierte Datei-Daten")
    name: Optional[str] = Field(None, max_length=255, description="Original-Dateiname")

    @validator("mime")
    def validate_mime(cls, v, values):
        allowed = ALLOWED_IMAGE_MIMES | ALLOWED_AUDIO_MIMES
        if v not in allowed:
            raise ValueError(f"MIME-Type '{v}' nicht erlaubt. Erlaubt: {sorted(allowed)}")
        return v

    @validator("data")
    def validate_size(cls, v, values):
        size_bytes = len(v) * 3 / 4
        is_audio = values.get("type") == "audio"
        limit = 25 * 1024 * 1024 if is_audio else 10 * 1024 * 1024
        limit_mb = 25 if is_audio else 10
        if size_bytes > limit:
            raise ValueError(f"Datei zu groß: {size_bytes / 1024 / 1024:.1f}MB (max {limit_mb}MB)")
        return v


class AgentChatRequest(BaseModel):
    """Anfrage für Agent-Chat."""
    message: str = Field("", max_length=100000, description="User-Nachricht (leer erlaubt bei Attachments)")
    session_id: Optional[str] = Field(None, max_length=100, description="Session-ID (neu wenn leer)")
    model: Optional[str] = Field(None, max_length=100, description="LLM-Modell")
    skill_ids: Optional[List[str]] = Field(None, max_length=20, description="Skill-IDs zum Aktivieren (max 20)")
    context: Optional[ContextSelection] = Field(None, description="Manuell ausgewählte Kontext-Elemente")
    attachments: Optional[List[ChatAttachment]] = Field(None, max_length=5, description="Bild-/Audio-Anhänge (max 5)")


class AgentModeRequest(BaseModel):
    """Anfrage zum Ändern des Agent-Modus."""
    mode: str = Field(..., description="Neuer Modus: read_only, write_with_confirm, autonomous, plan_then_execute")


class AgentConfirmRequest(BaseModel):
    """Anfrage zur Bestätigung einer Schreib-Operation."""
    confirmed: bool = Field(..., description="True = ausführen, False = abbrechen")


class QuestionResponseRequest(BaseModel):
    """Anfrage zur Beantwortung einer Frage vom suggest_answers Tool."""
    tool_id: str = Field(..., description="ID des suggest_answers Tool-Calls")
    answer: Optional[str] = Field(None, description="Benutzer-Antwort (freier Text oder Option-Label)")


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
async def agent_chat(request: AgentChatRequest, http_request: Request):
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
                model=request.model,
                context_selection=request.context,
                attachments=[a.dict() for a in request.attachments] if request.attachments else None,
            )

            async for event in gen:
                # Client-Disconnect erkennen und Anfrage abbrechen
                if await http_request.is_disconnected():
                    orchestrator.cancel_request(session_id)
                    await gen.aclose()
                    return

                event_data = {
                    "type": event.type.value,
                    "session_id": session_id,
                    "data": event.data
                }
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

                # Bei CONFIRM_REQUIRED pausieren wir hier
                # Das Frontend muss dann /confirm aufrufen
                if event.type == AgentEventType.CONFIRM_REQUIRED:
                    yield f"data: {json.dumps({'type': 'waiting_for_confirmation', 'session_id': session_id}, ensure_ascii=False)}\n\n"
                    return

                # PLAN_READY: Stream NICHT schließen – nachfolgende USAGE/DONE Events
                # werden noch benötigt um die Status-Bar zu finalisieren.

        except asyncio.CancelledError:
            orchestrator.cancel_request(session_id)
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
            model=request.model,
            context_selection=request.context,
            attachments=[a.dict() for a in request.attachments] if request.attachments else None,
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
    Aktualisiert die Message-Historie damit das LLM bei der nächsten
    Anfrage weiß, dass die Operation ausgeführt wurde.

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
    confirmation_data = tool_call.result.confirmation_data if tool_call.result else {}
    file_path = confirmation_data.get("path", "unbekannt")

    if request.confirmed:
        # Operation ausführen
        try:
            # Für pip_install_confirm: Create callback functions for progress events
            operation = confirmation_data.get("operation")
            if operation == "pip_install_confirm":
                from app.agent.orchestration.types import AgentEventType

                async def on_pip_start(requirements):
                    """Emit event when pip install starts."""
                    await orchestrator._event_bridge.emit(
                        AgentEventType.MCP_START.value,
                        {
                            "type": "pip_install_start",
                            "message": f"Installiere {len(requirements)} Python-Paket(e)...",
                            "total": len(requirements)
                        }
                    )

                async def on_pip_installing(pkg):
                    """Emit event when installing a package."""
                    await orchestrator._event_bridge.emit(
                        AgentEventType.MCP_PROGRESS.value,
                        {
                            "type": "pip_installing",
                            "package": pkg,
                            "message": f"↓ Installiere: {pkg}"
                        }
                    )

                async def on_pip_installed(pkg, success, error):
                    """Emit event when package installation completes."""
                    if success:
                        await orchestrator._event_bridge.emit(
                            AgentEventType.MCP_PROGRESS.value,
                            {
                                "type": "pip_installed",
                                "package": pkg,
                                "success": True,
                                "message": f"✓ {pkg} installiert"
                            }
                        )
                    else:
                        await orchestrator._event_bridge.emit(
                            AgentEventType.MCP_PROGRESS.value,
                            {
                                "type": "pip_installed",
                                "package": pkg,
                                "success": False,
                                "error": error,
                                "message": f"✗ {pkg} fehlgeschlagen: {error}"
                            }
                        )

                async def on_pip_complete(success, total_ms):
                    """Emit event when all pip installs complete."""
                    await orchestrator._event_bridge.emit(
                        AgentEventType.MCP_COMPLETE.value,
                        {
                            "type": "pip_install_complete",
                            "success": success,
                            "duration_ms": total_ms,
                            "message": f"✓ Pip-Installation abgeschlossen ({total_ms}ms)" if success else f"✗ Pip-Installation fehlgeschlagen"
                        }
                    )

                # Add callbacks to confirmation_data for use in orchestrator
                confirmation_data["_pip_callbacks"] = {
                    "on_pip_start": on_pip_start,
                    "on_pip_installing": on_pip_installing,
                    "on_pip_installed": on_pip_installed,
                    "on_pip_complete": on_pip_complete
                }

            # PHASE 2: For execute_script, create output streaming callbacks
            elif operation == "execute_script":
                from app.agent.orchestration.types import AgentEventType

                async def on_output_chunk(stream_type: str, chunk: str):
                    """Emit event when script outputs data."""
                    await orchestrator._event_bridge.emit(
                        AgentEventType.MCP_PROGRESS.value,
                        {
                            "type": "script_output",
                            "stream_type": stream_type,
                            "chunk": chunk,
                            "message": f"{chunk}"
                        }
                    )

                # Add callbacks to confirmation_data for use in orchestrator
                confirmation_data["_output_callbacks"] = {
                    "on_output_chunk": on_output_chunk
                }

            result = await orchestrator._execute_confirmed_operation(confirmation_data)

            # Phase-2: Wenn requires_confirmation=True → weitere Bestätigung nötig
            if result.requires_confirmation:
                # Aktualisiere pending_confirmation mit neuen Daten
                tool_call.result.confirmation_data = result.confirmation_data
                # pending_confirmation NICHT löschen
                return {
                    "status": "confirm_required",
                    "name": f"Script '{result.confirmation_data.get('script_name', '')}' ausführen",
                    "confirmation_data": result.confirmation_data,
                    "message": result.data,
                    "continue": False  # Panel bleibt offen
                }

            # Message-Historie aktualisieren damit LLM weiß was passiert ist
            if result.success:
                result_text = f"✓ Datei erfolgreich geschrieben: {file_path}"
            else:
                result_text = f"✗ Fehler beim Schreiben: {result.error}"

            # Tool-Result zur Message-Historie hinzufügen
            state.messages_history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text
            })

            # Tool-Call als abgeschlossen markieren
            tool_call.result.data = result_text
            tool_call.result.requires_confirmation = False
            tool_call.confirmed = True
            state.tool_calls_history.append(tool_call)

            state.pending_confirmation = None

            if result.success:
                return {
                    "status": "executed",
                    "message": f"Operation '{tool_call.name}' ausgeführt",
                    "data": result.data,
                    "continue": True  # Signal ans Frontend: weitere Anfrage starten
                }
            else:
                return {
                    "status": "error",
                    "message": f"Operation fehlgeschlagen: {result.error}",
                    "continue": True
                }
        except Exception as e:
            state.pending_confirmation = None
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Operation abbrechen - auch dies in History dokumentieren
        result_text = f"⚠️ Operation abgebrochen: {file_path}"
        state.messages_history.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result_text
        })

        tool_call.result.data = result_text
        tool_call.confirmed = False
        state.tool_calls_history.append(tool_call)

        state.pending_confirmation = None
        return {
            "status": "cancelled",
            "message": f"Operation '{tool_call.name}' abgebrochen",
            "continue": True  # Auch bei Abbruch kann weitergemacht werden
        }


# ══════════════════════════════════════════════════════════════════════════════
# Question Response Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/question-response/{session_id}")
async def answer_question(
    session_id: str,
    request: QuestionResponseRequest
) -> Dict[str, Any]:
    """
    Beantwortet eine Frage vom suggest_answers Tool.

    Wird nach einem QUESTION Event aufgerufen wenn der Benutzer
    eine der angebotenen Optionen auswählt oder eine Antwort eingibt.

    Args:
        session_id: Session-ID
        request: tool_id und Benutzer-Antwort

    Returns:
        Status und Signal zum Fortfahren mit dem Chat
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if not state.pending_question:
        raise HTTPException(
            status_code=400,
            detail="Keine ausstehende Frage für diese Session"
        )

    tool_call = state.pending_question

    # Validiere dass die tool_id passt
    if tool_call.id != request.tool_id:
        raise HTTPException(
            status_code=400,
            detail=f"Tool-ID passt nicht: erwartet {tool_call.id}, erhalten {request.tool_id}"
        )

    # Benutzer-Antwort zur Message-Historie hinzufügen
    # Format: "User selected: <option-label>" oder "User answered: <freetext>"
    user_message = f"User selected: {request.answer}" if request.answer else "User skipped question"

    state.messages_history.append({
        "role": "user",
        "content": user_message
    })

    # Tool-Call als beantwortet markieren
    tool_call.confirmed = True
    state.tool_calls_history.append(tool_call)

    # Ausstehende Frage löschen
    state.pending_question = None

    return {
        "status": "answered",
        "message": f"Frage beantwortet: {request.answer}",
        "continue": True  # Signal ans Frontend: weitere Anfrage starten
    }


# ══════════════════════════════════════════════════════════════════════════════
# Plan Approval Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/plan/{session_id}/approve")
async def approve_plan(session_id: str) -> Dict[str, Any]:
    """
    Genehmigt den ausstehenden Plan einer Session.

    Nach der Genehmigung kann der Agent mit der Ausführung beginnen.
    Das Frontend startet dazu eine neue Chat-Anfrage.

    Args:
        session_id: Session-ID

    Returns:
        Status und Plan-Text
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if not state.pending_plan:
        raise HTTPException(
            status_code=400,
            detail="Kein ausstehender Plan für diese Session"
        )

    state.plan_approved = True

    return {
        "status": "approved",
        "message": "Plan genehmigt. Starte eine neue Chat-Anfrage um die Ausführung zu beginnen.",
        "plan": state.pending_plan,
    }


@router.post("/plan/{session_id}/reject")
async def reject_plan(session_id: str) -> Dict[str, Any]:
    """
    Lehnt den ausstehenden Plan einer Session ab.

    Der Plan wird verworfen und die Session bleibt im Planungsmodus.

    Args:
        session_id: Session-ID

    Returns:
        Status
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    state.pending_plan = None
    state.plan_approved = False

    return {
        "status": "rejected",
        "message": "Plan abgelehnt. Du kannst eine neue Anfrage stellen um einen neuen Plan zu erstellen.",
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

    # File-Operations muss aktiviert sein für Schreibmodi (nicht für read_only, plan, debug)
    write_modes = {AgentMode.WRITE_WITH_CONFIRM, AgentMode.AUTONOMOUS}
    if mode in write_modes and not settings.file_operations.enabled:
        raise HTTPException(
            status_code=400,
            detail="Datei-Operationen sind nicht aktiviert. Setze file_operations.enabled=true in config.yaml"
        )

    orchestrator = get_agent_orchestrator()
    orchestrator.set_mode(session_id, mode)

    # Mode-Änderung persistieren
    from app.services.chat_store import load_chat, save_chat
    existing = load_chat(session_id)
    if existing:
        save_chat(
            session_id,
            existing.get("title", "Chat"),
            existing.get("messages_history", []),
            mode.value
        )

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


@router.post("/cancel/{session_id}")
async def cancel_request(session_id: str) -> Dict[str, str]:
    """Bricht die laufende Anfrage einer Session ab."""
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    orchestrator.cancel_request(session_id)

    return {"message": f"Anfrage für Session '{session_id}' abgebrochen"}


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

    # Initiales Disk-Record anlegen (damit Neustart die Session kennt)
    try:
        from app.services.chat_store import save_chat
        save_chat(session_id, "Neuer Chat", [], state.mode.value)
    except Exception:
        pass

    return AgentSessionResponse(
        session_id=session_id,
        mode=state.mode.value,
        active_skills=list(state.active_skill_ids),
        tool_calls_count=0,
        pending_confirmation=None
    )


@router.get("/sessions")
async def list_sessions() -> Dict[str, Any]:
    """Listet alle aktiven Agent-Sessions auf."""
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    sessions = []
    for session_id, session_state in orchestrator._sessions.items():
        first_msg = ""
        if session_state.messages_history:
            for msg in session_state.messages_history:
                if msg.get("role") == "user":
                    first_msg = msg.get("content", "")[:80]
                    break
        sessions.append({
            "session_id": session_id,
            "mode": session_state.mode.value,
            "message_count": len(session_state.messages_history),
            "tool_calls_count": len(session_state.tool_calls_history),
            "first_message": first_msg,
        })
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/chats")
async def list_persisted_chats() -> Dict[str, Any]:
    """Listet alle persistierten Chats (aus Disk) auf."""
    from app.services.chat_store import list_chats
    return {"chats": list_chats()}


@router.get("/session/{session_id}/history")
async def get_session_history(session_id: str) -> Dict[str, Any]:
    """Gibt die Nachrichten-Historie einer Session zurück (aus Speicher oder Disk)."""
    from app.agent.orchestrator import get_agent_orchestrator
    from app.services.chat_store import load_chat

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    # Fallback: Falls State leer ist, nochmal direkt von Disk laden
    # (schützt gegen Race-Conditions beim Startup)
    if not state.messages_history:
        saved = load_chat(session_id)
        if saved and saved.get("messages_history"):
            state.messages_history = saved["messages_history"]
            state.title = saved.get("title", state.title)
            try:
                from app.agent.orchestrator import AgentMode
                state.mode = AgentMode(saved.get("mode", "read_only"))
            except (ValueError, ImportError):
                pass

    return {
        "session_id": session_id,
        "messages": state.messages_history,
        "title": state.title,
        "mode": state.mode.value,  # Mode für UI-Synchronisation
    }


class UpdateTitleRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


@router.patch("/session/{session_id}/title")
async def update_session_title(session_id: str, body: UpdateTitleRequest) -> Dict[str, str]:
    """Aktualisiert den Titel einer Chat-Session."""
    from app.agent.orchestrator import get_agent_orchestrator
    from app.services.chat_store import update_title, load_chat, save_chat
    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)
    state.title = body.title
    # Auf Disk aktualisieren
    if not update_title(session_id, body.title):
        # Datei existiert noch nicht → vollständig speichern
        save_chat(session_id, body.title, state.messages_history, state.mode.value)
    return {"title": body.title}


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


# ══════════════════════════════════════════════════════════════════════════════
# Memory Management Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class MemoryRequest(BaseModel):
    """Request zum Speichern eines Memory-Eintrags."""
    category: str = Field(..., description="Kategorie: fact, entity, preference, decision")
    key: str = Field(..., max_length=200, description="Schlüssel/Titel")
    value: str = Field(..., max_length=2000, description="Inhalt")
    importance: float = Field(0.5, ge=0.0, le=1.0, description="Wichtigkeit 0-1")


@router.get("/memory/{session_id}")
async def get_memories(
    session_id: str,
    category: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200)
) -> Dict[str, Any]:
    """
    Holt alle Memories einer Session.

    Optional nach Kategorie filtern.
    """
    from app.services.memory_store import get_memory_store
    store = get_memory_store()

    if category:
        memories = await store.get_by_category(session_id, category, limit)
    else:
        memories = await store.get_all(session_id, limit)

    stats = await store.get_stats(session_id)

    return {
        "session_id": session_id,
        "memories": [
            {
                "id": m.id,
                "category": m.category,
                "key": m.key,
                "value": m.value,
                "importance": m.importance,
                "access_count": m.access_count,
                "created_at": m.created_at
            }
            for m in memories
        ],
        "stats": stats
    }


@router.post("/memory/{session_id}")
async def add_memory(session_id: str, request: MemoryRequest) -> Dict[str, Any]:
    """
    Speichert einen neuen Memory-Eintrag.

    Bei gleichem (session_id, category, key) wird aktualisiert.
    """
    from app.services.memory_store import get_memory_store
    store = get_memory_store()

    memory_id = await store.remember(
        session_id=session_id,
        category=request.category,
        key=request.key,
        value=request.value,
        importance=request.importance
    )

    return {
        "success": True,
        "id": memory_id,
        "message": f"Memory '{request.key}' gespeichert"
    }


@router.delete("/memory/{session_id}/{memory_id}")
async def delete_memory(session_id: str, memory_id: str) -> Dict[str, Any]:
    """Löscht einen einzelnen Memory-Eintrag."""
    from app.services.memory_store import get_memory_store
    store = get_memory_store()

    success = await store.forget(memory_id)

    if success:
        return {"success": True, "message": "Memory gelöscht"}
    else:
        raise HTTPException(status_code=404, detail="Memory nicht gefunden")


@router.delete("/memory/{session_id}")
async def clear_memories(session_id: str) -> Dict[str, Any]:
    """Löscht alle Memories einer Session."""
    from app.services.memory_store import get_memory_store
    store = get_memory_store()

    count = await store.forget_session(session_id)

    return {
        "success": True,
        "deleted_count": count,
        "message": f"{count} Memories gelöscht"
    }


@router.get("/memory/{session_id}/search")
async def search_memories(
    session_id: str,
    query: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50)
) -> Dict[str, Any]:
    """
    Durchsucht Memories nach relevanten Einträgen.

    Verwendet FTS5-Volltextsuche.
    """
    from app.services.memory_store import get_memory_store
    store = get_memory_store()

    memories = await store.recall(session_id, query, limit)

    return {
        "session_id": session_id,
        "query": query,
        "results": [
            {
                "id": m.id,
                "category": m.category,
                "key": m.key,
                "value": m.value,
                "importance": m.importance
            }
            for m in memories
        ],
        "count": len(memories)
    }


@router.get("/budget/{session_id}")
async def get_token_budget(session_id: str) -> Dict[str, Any]:
    """
    Gibt den aktuellen Token-Budget-Status zurück.

    Nützlich für UI-Anzeige und Debugging.
    """
    from app.agent.orchestrator import get_agent_orchestrator
    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if state.token_budget:
        return {
            "session_id": session_id,
            "budget": state.token_budget.get_status(),
            "compaction_count": state.compaction_count,
            "last_savings": state.last_compaction_savings
        }
    else:
        return {
            "session_id": session_id,
            "budget": None,
            "message": "Kein aktives Budget (Session noch nicht gestartet)"
        }


@router.get("/cache/stats")
async def get_cache_stats() -> Dict[str, Any]:
    """
    Gibt LLM-Cache-Statistiken zurück.

    Returns:
        - enabled: Ob Caching aktiv ist
        - type: Cache-Typ (local/redis)
        - hits: Anzahl Cache-Treffer
        - misses: Anzahl Cache-Misses
        - hit_rate: Trefferquote in %
        - size: Anzahl Cache-Einträge
        - evictions: Anzahl entfernter Einträge
    """
    from app.services.llm_cache import get_cache_stats
    return get_cache_stats()


@router.post("/cache/clear")
async def clear_cache() -> Dict[str, str]:
    """
    Leert den LLM-Response-Cache.
    """
    from app.services.llm_cache import get_cache_manager
    cache = await get_cache_manager()
    await cache.clear()
    return {"status": "ok", "message": "Cache cleared"}


# ══════════════════════════════════════════════════════════════════════════════
# Enhancement Confirmation Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class EnhancementConfirmRequest(BaseModel):
    """Anfrage zur Bestätigung einer Enhancement-Operation."""
    confirmed: bool = Field(..., description="True = Kontext verwenden, False = ohne Kontext fortfahren")


@router.get("/enhancement/{session_id}")
async def get_enhancement_details(session_id: str) -> Dict[str, Any]:
    """
    Holt Details des ausstehenden Enhancement-Kontexts.

    Wird vom Frontend aufgerufen um die vollständigen Context-Items
    für die Bestätigungsanzeige zu laden.

    Args:
        session_id: Session-ID

    Returns:
        Enhancement-Details mit Context-Items
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if not state.pending_enhancement:
        return {
            "session_id": session_id,
            "has_enhancement": False,
            "message": "Kein ausstehendes Enhancement für diese Session"
        }

    enriched = state.pending_enhancement

    return {
        "session_id": session_id,
        "has_enhancement": True,
        "enhancement": {
            "original_query": enriched.original_query,
            "enhancement_type": enriched.enhancement_type.value,
            "context_sources": enriched.context_sources,
            "summary": enriched.summary,
            "context_items": [
                {
                    "source": item.source,
                    "title": item.title,
                    "content": item.content,
                    "content_preview": item.content[:300] + "..." if len(item.content) > 300 else item.content,
                    "relevance": item.relevance,
                    "file_path": item.file_path,
                    "url": item.url
                }
                for item in enriched.context_items
            ],
            "confirmation_message": enriched.get_confirmation_message()
        }
    }


@router.post("/enhancement/{session_id}/confirm")
async def confirm_enhancement(
    session_id: str,
    request: EnhancementConfirmRequest
) -> Dict[str, Any]:
    """
    Bestätigt oder lehnt einen Enhancement-Kontext ab.

    Nach der Bestätigung setzt der Agent die Verarbeitung fort.

    Args:
        session_id: Session-ID
        request: Bestätigung (True/False)

    Returns:
        Status der Operation
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if not state.pending_enhancement:
        raise HTTPException(
            status_code=400,
            detail="Kein ausstehendes Enhancement für diese Session"
        )

    enriched = state.pending_enhancement

    if request.confirmed:
        # Kontext wird verwendet
        from app.agent.prompt_enhancer import get_prompt_enhancer
        enhancer = get_prompt_enhancer()
        enriched = enhancer.confirm(enriched, True)
        context = enriched.get_context_for_planner()

        # Store confirmed context for task processing when [CONTINUE_ENHANCED] arrives
        state.confirmed_enhancement_context = context
        state.enhancement_original_query = enriched.original_query
        state.pending_enhancement = None

        return {
            "status": "confirmed",
            "message": "Enhancement-Kontext bestätigt",
            "context_length": len(context),
            "continue": True
        }
    else:
        # Kontext wird abgelehnt
        state.confirmed_enhancement_context = None
        state.enhancement_original_query = enriched.original_query
        state.pending_enhancement = None

        return {
            "status": "rejected",
            "message": "Enhancement-Kontext abgelehnt, fahre ohne Kontext fort",
            "continue": True
        }


# ══════════════════════════════════════════════════════════════════════════════
# Web Fallback Confirmation Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class WebFallbackConfirmRequest(BaseModel):
    """Anfrage zur Bestätigung der Web-Fallback-Suche."""
    confirmed: bool = Field(..., description="True = Mit Web-Suche, False = Ohne")


@router.post("/web-fallback/{session_id}/confirm")
async def confirm_web_fallback(
    session_id: str,
    request: WebFallbackConfirmRequest
) -> Dict[str, Any]:
    """
    Bestätigt oder lehnt die Web-Fallback-Suche ab.

    Wird aufgerufen, wenn interne Quellen keine Ergebnisse geliefert haben
    und der User entscheiden muss, ob im Web gesucht werden soll.

    Args:
        session_id: Session-ID
        request: Bestätigung (True = Mit Web, False = Ohne Web)

    Returns:
        Status und ob die Suche fortgesetzt werden soll
    """
    from app.agent.orchestrator import get_agent_orchestrator

    orchestrator = get_agent_orchestrator()
    state = orchestrator._get_state(session_id)

    if request.confirmed:
        # Web-Suche wurde genehmigt
        state.web_fallback_approved = True
        return {
            "status": "confirmed",
            "message": "Web-Suche genehmigt mit bereinigter Query",
            "continue": True,
            "retry_with_web": True
        }
    else:
        # Web-Suche wurde abgelehnt
        state.web_fallback_approved = False
        return {
            "status": "rejected",
            "message": "Web-Suche abgelehnt, fahre ohne Web-Ergebnisse fort",
            "continue": True,
            "retry_with_web": False
        }


# ══════════════════════════════════════════════════════════════════════════════
# Workspace Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class WorkspaceCodeApplyRequest(BaseModel):
    """Anfrage zum Anwenden einer Code-Änderung."""
    filePath: str = Field(..., description="Pfad zur Datei")
    content: str = Field(..., description="Neuer Dateiinhalt")


@router.post("/workspace/code/apply/{change_id}")
async def apply_workspace_code_change(
    change_id: str,
    request: WorkspaceCodeApplyRequest
) -> Dict[str, Any]:
    """
    Wendet eine Code-Änderung aus dem Workspace an.

    Args:
        change_id: ID der Code-Änderung
        request: Datei-Pfad und neuer Inhalt

    Returns:
        Status der Operation
    """
    import os
    from pathlib import Path

    # Security: Validate path
    file_path = request.filePath

    # Check if file operations are enabled
    if not settings.file_operations.enabled:
        raise HTTPException(
            status_code=403,
            detail="Datei-Operationen sind deaktiviert"
        )

    # Check allowed paths
    if settings.file_operations.allowed_paths:
        path_allowed = False
        for allowed in settings.file_operations.allowed_paths:
            try:
                if Path(file_path).resolve().is_relative_to(Path(allowed).resolve()):
                    path_allowed = True
                    break
            except ValueError:
                continue

        if not path_allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Pfad nicht in erlaubten Verzeichnissen: {file_path}"
            )

    try:
        # Write the file
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(request.content)

        return {
            "success": True,
            "message": f"Datei gespeichert: {file_path}",
            "filePath": file_path,
            "changeId": change_id
        }

    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=f"Keine Schreibrechte für: {file_path}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Fehler beim Speichern: {str(e)}"
        )
