from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.api.routes import chat, java, logs, pdf, confluence, models

app = FastAPI(
    title="Java AI Code Assistant",
    description="Lokaler AI-Assistent für Java-Entwicklung mit WLP-Log-, PDF- und Confluence-Unterstützung",
    version="1.0.0",
)

# API routes
app.include_router(chat.router)
app.include_router(java.router)
app.include_router(logs.router)
app.include_router(pdf.router)
app.include_router(confluence.router)
app.include_router(models.router)

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
