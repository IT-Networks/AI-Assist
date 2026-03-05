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


async def _collect_attachments(sources, session_id: str, user_message: str = ""):
    attachments = []

    if not sources:
        return attachments

    # Python files – manuell gewählt oder per Auto-Index-Suche
    python_paths = list(sources.python_files)
    if sources.auto_python_search and not python_paths:
        try:
            from app.services.python_indexer import get_python_indexer
            from app.core.config import settings as _s
            py_indexer = get_python_indexer()
            if py_indexer.is_built():
                py_results = py_indexer.search(user_message, top_k=_s.index.max_search_results)
                python_paths = [r["file_path"] for r in py_results]
        except Exception:
            pass

    if python_paths:
        try:
            from app.services.python_reader import PythonReader
            from app.core.config import settings as _ps
            py_reader = PythonReader(
                _ps.python.repo_path,
                exclude_dirs=_ps.python.exclude_dirs,
                max_file_size_kb=_ps.python.max_file_size_kb,
            )
            for rel_path in python_paths[:5]:
                try:
                    content = py_reader.read_file(rel_path)
                    attachments.append(ContextAttachment(
                        label=f"PYTHON-DATEI: {rel_path}",
                        content=content,
                        priority=1,
                    ))
                except Exception:
                    pass
        except Exception:
            pass

    # Java files – manuell gewählt oder per Auto-Index-Suche
    java_paths = list(sources.java_files)
    if sources.auto_java_search and not java_paths:
        # FTS-Index nach zur Frage passenden Dateien durchsuchen
        try:
            from app.services.java_indexer import get_java_indexer
            from app.core.config import settings as _s
            indexer = get_java_indexer()
            if indexer.is_built():
                results = indexer.search(user_message, top_k=_s.index.max_search_results)
                java_paths = [r["file_path"] for r in results]
        except Exception:
            pass

    if java_paths:
        try:
            from app.services.java_reader import JavaReader
            from app.core.config import settings
            reader = JavaReader(settings.java.get_active_path())
            for rel_path in java_paths[:5]:  # limit to 5 files
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
            reader = JavaReader(settings.java.get_active_path())
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

    # PDFs – relevante Seiten per Index, sonst Volltext
    for pdf_id in sources.pdf_ids[:3]:
        try:
            _, _pdf_store = _get_stores()
            if pdf_id not in _pdf_store:
                continue
            pdf_data = _pdf_store[pdf_id]
            content = ""
            # Versuche Index-basierte Suche (nur relevante Seiten)
            if user_message:
                try:
                    from app.services.pdf_indexer import get_pdf_indexer
                    from app.core.config import settings as _s
                    pdf_idx = get_pdf_indexer()
                    if pdf_idx.has_pdf(pdf_id):
                        content = pdf_idx.search(
                            pdf_id, user_message, top_k=_s.index.max_search_results
                        )
                except Exception:
                    pass
            # Fallback: gesamter Text (kleine PDFs oder kein Index)
            if not content:
                content = pdf_data["text"]
            attachments.append(ContextAttachment(
                label=f"PDF: {pdf_data['filename']}",
                content=content,
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

    # Handbuch-Seiten – manuell gewählt oder per Auto-Index-Suche
    handbook_paths = list(sources.handbook_pages) if hasattr(sources, 'handbook_pages') else []
    auto_handbook = getattr(sources, 'auto_handbook_search', False)
    handbook_service = getattr(sources, 'handbook_service_filter', None)

    if auto_handbook and not handbook_paths:
        try:
            from app.services.handbook_indexer import get_handbook_indexer
            from app.core.config import settings as _hs
            if _hs.handbook.enabled:
                hb_indexer = get_handbook_indexer()
                if hb_indexer.is_built():
                    hb_results = hb_indexer.search(
                        query=user_message,
                        service_filter=handbook_service,
                        top_k=_hs.index.max_search_results
                    )
                    handbook_paths = [r["file_path"] for r in hb_results]
        except Exception:
            pass

    if handbook_paths:
        try:
            from app.services.handbook_indexer import get_handbook_indexer
            from app.core.config import settings as _hs
            if _hs.handbook.enabled:
                hb_indexer = get_handbook_indexer()
                for hb_path in handbook_paths[:5]:
                    try:
                        content = hb_indexer.get_page_content(hb_path)
                        if content:
                            attachments.append(ContextAttachment(
                                label=f"HANDBUCH: {hb_path}",
                                content=content,
                                priority=2,  # Hohe Priorität für Handbuch
                            ))
                    except Exception:
                        pass
        except Exception:
            pass

    # Skill-Wissen – automatisch in aktiven Skills suchen
    skill_ids = getattr(sources, 'active_skill_ids', [])
    auto_skill = getattr(sources, 'auto_skill_knowledge', True)

    if skill_ids and auto_skill and user_message:
        try:
            from app.services.skill_manager import get_skill_manager
            from app.core.config import settings as _sk
            if _sk.skills.enabled:
                skill_mgr = get_skill_manager()
                # Skills aktivieren
                for sid in skill_ids:
                    skill_mgr.activate_skill(session_id, sid)
                # Wissen suchen
                knowledge = skill_mgr.get_knowledge_context(
                    session_id, user_message, top_k=_sk.index.max_search_results
                )
                if knowledge:
                    attachments.append(ContextAttachment(
                        label="SKILL-WISSEN",
                        content=knowledge,
                        priority=1,  # Höchste Priorität
                    ))
        except Exception:
            pass

    return attachments


def _get_skill_system_prompt(session_id: str, skill_ids: list) -> str:
    """Holt den kombinierten System-Prompt aus aktiven Skills."""
    try:
        from app.services.skill_manager import get_skill_manager
        from app.core.config import settings
        if not settings.skills.enabled:
            return ""
        skill_mgr = get_skill_manager()
        # Skills aktivieren falls noch nicht
        for sid in skill_ids:
            skill_mgr.activate_skill(session_id, sid)
        return skill_mgr.build_system_prompt(session_id)
    except Exception:
        return ""


@router.post("", response_model=ChatResponse)
async def chat_non_stream(request: ChatRequest):
    """Non-streaming chat endpoint."""
    if request.stream:
        raise HTTPException(status_code=400, detail="Use stream=false for this endpoint or accept text/event-stream")

    attachments = await _collect_attachments(request.context_sources, request.session_id, request.message)

    # Skill-System-Prompt hinzufügen
    skill_ids = []
    if request.context_sources:
        skill_ids = getattr(request.context_sources, 'active_skill_ids', [])
    skill_prompt = _get_skill_system_prompt(request.session_id, skill_ids)

    messages = context_manager.build_messages(
        session_id=request.session_id,
        user_message=request.message,
        attachments=attachments,
        additional_system_prompt=skill_prompt,
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
    attachments = await _collect_attachments(request.context_sources, request.session_id, request.message)

    # Skill-System-Prompt hinzufügen
    skill_ids = []
    if request.context_sources:
        skill_ids = getattr(request.context_sources, 'active_skill_ids', [])
    skill_prompt = _get_skill_system_prompt(request.session_id, skill_ids)

    messages = context_manager.build_messages(
        session_id=request.session_id,
        user_message=request.message,
        attachments=attachments,
        additional_system_prompt=skill_prompt,
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
