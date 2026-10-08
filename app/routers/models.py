"""GET /api/models — 화면의 모델 선택 드롭다운에 보여줄 목록."""

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.config import settings
from app.llm import list_installed_models
from app.schemas import ModelOption, ModelsResponse

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    # Ollama가 꺼져 있으면 LlmUnavailableError가 터지고, main.py의 처리기가 503으로 바꾼다
    installed = await list_installed_models()
    return ModelsResponse(
        default=settings.llm_model,
        # 목록은 서버의 허용 목록 그대로. 설치 여부만 표시해서, 프론트가 "설치 필요"로 흐리게 보여줄 수 있게 한다
        models=[
            ModelOption(id=model, installed=model in installed)
            for model in settings.allowed_models
        ],
    )
