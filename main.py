from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.api.routes import chat, java, logs, pdf, confluence, models, python_routes, handbook, skills, agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: optionaler automatischer Index-Build
    from app.core.config import settings
    import asyncio

    # Java Index
    if settings.index.auto_build_on_start and settings.java.repo_path:
        try:
            from app.services.java_reader import JavaReader
            from app.services.java_indexer import get_java_indexer
            reader = JavaReader(settings.java.repo_path)
            indexer = get_java_indexer()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: indexer.build(settings.java.repo_path, reader, force=False)
            )
            print(f"[startup] Java-Index aufgebaut: {settings.java.repo_path}")
        except Exception as e:
            print(f"[startup] Java-Index-Build fehlgeschlagen: {e}")

    # Handbook Index
    if settings.handbook.enabled and settings.handbook.index_on_start and settings.handbook.path:
        try:
            from app.services.handbook_indexer import get_handbook_indexer
            indexer = get_handbook_indexer()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: indexer.build(
                    handbook_path=settings.handbook.path,
                    functions_subdir=settings.handbook.functions_subdir,
                    fields_subdir=settings.handbook.fields_subdir,
                    exclude_patterns=settings.handbook.exclude_patterns,
                    force=False
                )
            )
            print(f"[startup] Handbuch-Index aufgebaut: {settings.handbook.path}")
        except Exception as e:
            print(f"[startup] Handbuch-Index-Build fehlgeschlagen: {e}")

    # Skills laden
    if settings.skills.enabled:
        try:
            from app.services.skill_manager import get_skill_manager
            manager = get_skill_manager()
            stats = manager.get_stats()
            print(f"[startup] Skills geladen: {stats['total_skills']} Skills, {stats['total_knowledge_chunks']} Wissens-Chunks")
        except Exception as e:
            print(f"[startup] Skills-Laden fehlgeschlagen: {e}")

    # Agent Orchestrator und Tools initialisieren
    try:
        from app.agent import get_tool_registry, get_agent_orchestrator
        registry = get_tool_registry()
        orchestrator = get_agent_orchestrator()
        tools_count = len(registry.tools)
        write_tools = sum(1 for t in registry.tools.values() if t.is_write_operation)
        print(f"[startup] Agent initialisiert: {tools_count} Tools ({write_tools} Schreib-Ops)")
        if settings.file_operations.enabled:
            print(f"[startup] File-Ops aktiviert: Modus={settings.file_operations.default_mode}")
    except Exception as e:
        print(f"[startup] Agent-Initialisierung fehlgeschlagen: {e}")

    yield


app = FastAPI(
    title="AI Code Assistant",
    description="Lokaler AI-Assistent für Java/Python-Entwicklung mit Handbuch-, WLP-Log-, PDF- und Confluence-Unterstützung",
    version="2.0.0",
    lifespan=lifespan,
)

# API routes
app.include_router(chat.router)
app.include_router(java.router)
app.include_router(logs.router)
app.include_router(pdf.router)
app.include_router(confluence.router)
app.include_router(models.router)
app.include_router(python_routes.router)
app.include_router(handbook.router)
app.include_router(skills.router)
app.include_router(agent.router)

# Static files (frontend)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    from app.core.config import settings

    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.reload,
    )
