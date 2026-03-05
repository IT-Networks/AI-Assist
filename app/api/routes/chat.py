import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest, ChatResponse
from app.core import context_manager
from app.core.context_manager import ContextAttachment
from app.core.exceptions import LLMError, JavaReaderError
from app.services.llm_client import llm_client

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Lazy imports to avoid circular dependencies at startup
def _get_stores():
    from app.api.routes.logs import _log_store
    from app.api.routes.pdf import _pdf_store
    return _log_store, _pdf_store


async def _collect_attachments(sources, session_id: str):
    attachments = []

    if not sources:
        return attachments

    # Java files
    if sources.java_files:
        try:
            from app.services.java_reader import JavaReader
            from app.core.config import settings
            reader = JavaReader(settings.java.repo_path)
            for rel_path in sources.java_files[:5]:  # limit to 5 files
                try:
                    content = reader.read_file(rel_path)
                    attachments.append(ContextAttachment(
                        label=f"DATEI: {rel_path}",
                        content=content,
                        priority=1,
                    ))
                except Exception:
                    pass
        except Exception:
            pass

    # POM
    if sources.include_pom:
        try:
            from app.services.pom_parser import PomParser
            from app.services.java_reader import JavaReader
            from app.core.config import settings
            reader = JavaReader(settings.java.repo_path)
            pom_files = reader.get_pom_files()
            if pom_files:
                parser = PomParser()
                pom_data = parser.parse(pom_files[0])
                attachments.append(ContextAttachment(
                    label="POM ABHÄNGIGKEITEN",
                    content=parser.format_for_context(pom_data),
                    priority=2,
                ))
        except Exception:
            pass

    # Logs
    if sources.log_id:
        try:
            _log_store, _ = _get_stores()
            if sources.log_id in _log_store:
                log_data = _log_store[sources.log_id]
                attachments.append(ContextAttachment(
                    label=f"SERVER LOG - {log_data['filename']}",
                    content=log_data["summary"],
                    priority=3,
                ))
        except Exception:
            pass

    # PDFs
    for pdf_id in sources.pdf_ids[:3]:
        try:
            _, _pdf_store = _get_stores()
            if pdf_id in _pdf_store:
                pdf_data = _pdf_store[pdf_id]
                attachments.append(ContextAttachment(
                    label=f"PDF: {pdf_data['filename']}",
                    content=pdf_data["text"],
                    priority=4,
                ))
        except Exception:
            pass

    # Confluence pages
    for page_id in sources.confluence_page_ids[:3]:
        try:
            from app.services.confluence_client import ConfluenceClient
            client = ConfluenceClient()
            page = await client.get_page_by_id(page_id)
            attachments.append(ContextAttachment(
                label=f"CONFLUENCE: {page.get('title', page_id)}",
                content=page.get("content", ""),
                priority=5,
            ))
        except Exception:
            pass

    return attachments


@router.post("", response_model=ChatResponse)
async def chat_non_stream(request: ChatRequest):
    """Non-streaming chat endpoint."""
    if request.stream:
        raise HTTPException(status_code=400, detail="Use stream=false for this endpoint or accept text/event-stream")

    attachments = await _collect_attachments(request.context_sources, request.session_id)
    messages = context_manager.build_messages(
        session_id=request.session_id,
        user_message=request.message,
        attachments=attachments,
    )

    try:
        response_text = await llm_client.chat(messages=messages, model=request.model)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))

    context_manager.add_message(request.session_id, "user", request.message)
    context_manager.add_message(request.session_id, "assistant", response_text)

    return ChatResponse(session_id=request.session_id, response=response_text)


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint – returns text/event-stream."""
    attachments = await _collect_attachments(request.context_sources, request.session_id)
    messages = context_manager.build_messages(
        session_id=request.session_id,
        user_message=request.message,
        attachments=attachments,
    )

    async def generate():
        full_response = []
        try:
            async for token in llm_client.chat_stream(messages=messages, model=request.model):
                full_response.append(token)
                data = json.dumps({"token": token, "done": False})
                yield f"data: {data}\n\n"

            complete = "".join(full_response)
            context_manager.add_message(request.session_id, "user", request.message)
            context_manager.add_message(request.session_id, "assistant", complete)

            data = json.dumps({"token": "", "done": True, "full_response": complete})
            yield f"data: {data}\n\n"

        except LLMError as e:
            error_data = json.dumps({"error": str(e), "done": True})
            yield f"data: {error_data}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.delete("/{session_id}")
async def clear_session(session_id: str):
    context_manager.clear_session(session_id)
    return {"message": f"Session {session_id} gelöscht"}


@router.get("/{session_id}/history")
async def get_history(session_id: str):
    return {"session_id": session_id, "history": context_manager.get_history(session_id)}
