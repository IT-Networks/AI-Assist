from fastapi import APIRouter

from app.api.schemas import ModelInfo, ModelsResponse
from app.core.config import settings

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=ModelsResponse)
async def list_models():
    return ModelsResponse(
        models=[ModelInfo(id=m.id, display_name=m.display_name) for m in settings.models],
        default=settings.llm.default_model,
    )
