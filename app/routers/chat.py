from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import settings
from app.llm import (
    LlmError,
    LlmModelMissingError,
    LlmUnavailableError,
    stream_chat,
)
from app.schemas import ChatRequest

router = APIRouter()


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@router.post("/chat", response_model=None)
async def chat(body: ChatRequest) -> Response:
    try:
        chunks = await stream_chat(body.messages)
    except LlmUnavailableError:
        return _error(
            503,
            f"LLM 서버({settings.llm_base_url})에 연결할 수 없습니다. "
            "Ollama가 실행 중인지 확인하세요 (ollama serve).",
        )
    except LlmModelMissingError:
        return _error(
            502,
            f'모델 "{settings.llm_model}"을 찾을 수 없습니다. '
            f"ollama pull {settings.llm_model} 으로 받아주세요.",
        )
    except LlmError as exc:
        return _error(502, exc.detail)

    return StreamingResponse(chunks, media_type="text/plain; charset=utf-8")
