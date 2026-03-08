from contextlib import asynccontextmanager
from typing import Any, Dict
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.api.routes import chat, java, logs, pdf, confluence, models, python_routes, handbook, skills, agent, settings, database, datasources, mq, testtool, log_servers, wlp, maven, search


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: optionaler automatischer Index-Build
    from app.core.config import settings
    import asyncio

    # Java Index
    if settings.index.auto_build_on_start and settings.java.get_active_path():
        try:
            from app.services.java_reader import JavaReader
            from app.services.java_indexer import get_java_indexer
            reader = JavaReader(settings.java.get_active_path())
            indexer = get_java_indexer()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: indexer.build(settings.java.get_active_path(), reader, force=False)
            )
            print(f"[startup] Java-Index aufgebaut: {settings.java.get_active_path()}")
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
        from app.agent.datasource_tools import register_datasource_tools
        registry = get_tool_registry()
        orchestrator = get_agent_orchestrator()
        tools_count = len(registry.tools)
        write_tools = sum(1 for t in registry.tools.values() if t.is_write_operation)
        print(f"[startup] Agent initialisiert: {tools_count} Tools ({write_tools} Schreib-Ops)")
        if settings.file_operations.enabled:
            print(f"[startup] File-Ops aktiviert: Modus={settings.file_operations.default_mode}")
        # Datenquellen-Tools registrieren
        ds_count = register_datasource_tools(registry)
        if ds_count:
            print(f"[startup] Datenquellen-Tools registriert: {ds_count}")

        # MQ-Tools registrieren
        try:
            from app.agent.mq_tools import register_mq_tools
            mq_count = register_mq_tools(registry)
            if mq_count:
                print(f"[startup] MQ-Tools registriert: {mq_count}")
        except Exception as e:
            print(f"[startup] MQ-Tools-Registrierung fehlgeschlagen: {e}")

        # TestTool-Tools registrieren
        try:
            from app.agent.testtool_tools import register_testtool_tools
            tt_count = register_testtool_tools(registry)
            if tt_count:
                print(f"[startup] TestTool-Tools registriert: {tt_count}")
        except Exception as e:
            print(f"[startup] TestTool-Tools-Registrierung fehlgeschlagen: {e}")

        # WLP-Tools registrieren
        try:
            from app.agent.wlp_tools import register_wlp_tools
            wlp_count = register_wlp_tools(registry)
            if wlp_count:
                print(f"[startup] WLP-Tools registriert: {wlp_count}")
        except Exception as e:
            print(f"[startup] WLP-Tools-Registrierung fehlgeschlagen: {e}")

        # Maven-Tools registrieren
        try:
            from app.agent.maven_tools import register_maven_tools
            mvn_count = register_maven_tools(registry)
            if mvn_count:
                print(f"[startup] Maven-Tools registriert: {mvn_count}")
        except Exception as e:
            print(f"[startup] Maven-Tools-Registrierung fehlgeschlagen: {e}")

        # Log-Tools registrieren (log_find_server, log_read_window, log_read_ffdc)
        try:
            from app.agent.log_tools import register_log_tools
            log_count = register_log_tools(registry)
            if log_count:
                print(f"[startup] Log-Tools registriert: {log_count}")
        except Exception as e:
            print(f"[startup] Log-Tools-Registrierung fehlgeschlagen: {e}")

        # Web-Such-Tools registrieren (web_search, web_search_toggle)
        try:
            from app.agent.search_tools import register_search_tools
            search_count = register_search_tools(registry)
            if search_count:
                print(f"[startup] Such-Tools registriert: {search_count}")
        except Exception as e:
            print(f"[startup] Such-Tools-Registrierung fehlgeschlagen: {e}")
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
app.include_router(settings.router)
app.include_router(database.router)
app.include_router(datasources.router)
app.include_router(mq.router)
app.include_router(testtool.router)
app.include_router(log_servers.router)
app.include_router(wlp.router)
app.include_router(maven.router)
app.include_router(search.router)


# ══════════════════════════════════════════════════════════════════════════════
# Health Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/health", tags=["system"])
async def health_check() -> Dict[str, Any]:
    """
    Health-Check für alle Subsysteme.

    Prüft den Status von:
    - LLM-Verbindung
    - Skills-Service
    - Handbuch-Index
    - Java-Index
    - Python-Repository
    - Agent/Tools

    Returns:
        Dict mit Status aller Subsysteme und Gesamtstatus
    """
    from app.core.config import settings

    subsystems = {}
    overall_healthy = True

    # LLM Connection
    try:
        from app.services.llm_client import get_llm_client
        client = get_llm_client()
        llm_models = await client.list_models()
        subsystems["llm"] = {
            "status": "healthy",
            "base_url": settings.llm.base_url,
            "models_available": len(llm_models),
            "default_model": settings.llm.default_model
        }
    except Exception as e:
        subsystems["llm"] = {
            "status": "unhealthy",
            "error": str(e),
            "base_url": settings.llm.base_url
        }
        overall_healthy = False

    # Skills Service
    try:
        if settings.skills.enabled:
            from app.services.skill_manager import get_skill_manager
            manager = get_skill_manager()
            stats = manager.get_stats()
            subsystems["skills"] = {
                "status": "healthy",
                "enabled": True,
                "total_skills": stats.get("total_skills", 0),
                "knowledge_chunks": stats.get("total_knowledge_chunks", 0)
            }
        else:
            subsystems["skills"] = {
                "status": "disabled",
                "enabled": False
            }
    except Exception as e:
        subsystems["skills"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        overall_healthy = False

    # Handbook Index
    try:
        if settings.handbook.enabled:
            from app.services.handbook_indexer import get_handbook_indexer
            indexer = get_handbook_indexer()
            stats = indexer.get_stats()
            subsystems["handbook"] = {
                "status": "healthy" if stats.get("indexed", False) else "not_indexed",
                "enabled": True,
                "path": settings.handbook.path,
                "services_count": stats.get("services_count", 0),
                "fields_count": stats.get("fields_count", 0)
            }
        else:
            subsystems["handbook"] = {
                "status": "disabled",
                "enabled": False
            }
    except Exception as e:
        subsystems["handbook"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        overall_healthy = False

    # Java Index
    try:
        if settings.java.get_active_path():
            from app.services.java_indexer import get_java_indexer
            indexer = get_java_indexer()
            stats = indexer.get_stats()
            subsystems["java"] = {
                "status": "healthy" if stats.get("indexed", False) else "not_indexed",
                "repo_path": settings.java.get_active_path(),
                "classes_count": stats.get("classes_count", 0)
            }
        else:
            subsystems["java"] = {
                "status": "not_configured",
                "repo_path": None
            }
    except Exception as e:
        subsystems["java"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        overall_healthy = False

    # Python Repository
    try:
        if settings.python.get_active_path():
            repo_path = Path(settings.python.get_active_path())
            subsystems["python"] = {
                "status": "healthy" if repo_path.exists() else "path_not_found",
                "repo_path": settings.python.get_active_path(),
                "exists": repo_path.exists()
            }
        else:
            subsystems["python"] = {
                "status": "not_configured",
                "repo_path": None
            }
    except Exception as e:
        subsystems["python"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        overall_healthy = False

    # Agent & Tools
    try:
        from app.agent import get_tool_registry
        registry = get_tool_registry()
        tools = registry.tools
        write_tools = sum(1 for t in tools.values() if t.is_write_operation)
        subsystems["agent"] = {
            "status": "healthy",
            "tools_count": len(tools),
            "write_tools": write_tools,
            "read_tools": len(tools) - write_tools,
            "file_operations_enabled": settings.file_operations.enabled
        }
    except Exception as e:
        subsystems["agent"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        overall_healthy = False

    return {
        "status": "healthy" if overall_healthy else "degraded",
        "version": "2.0.0",
        "subsystems": subsystems
    }


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
