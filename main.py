from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.api.routes import chat, java, logs, pdf, confluence, models, python_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: optionaler automatischer Index-Build
    from app.core.config import settings
    if settings.index.auto_build_on_start and settings.java.repo_path:
        try:
            from app.services.java_reader import JavaReader
            from app.services.java_indexer import get_java_indexer
            import asyncio
            reader = JavaReader(settings.java.repo_path)
            indexer = get_java_indexer()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: indexer.build(settings.java.repo_path, reader, force=False)
            )
            print(f"[startup] Java-Index aufgebaut: {settings.java.repo_path}")
        except Exception as e:
            print(f"[startup] Index-Build fehlgeschlagen: {e}")
    yield


app = FastAPI(
    title="Java AI Code Assistant",
    description="Lokaler AI-Assistent für Java-Entwicklung mit WLP-Log-, PDF- und Confluence-Unterstützung",
    version="1.0.0",
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
