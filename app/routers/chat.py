from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import settings
from app.nvidia import (
    NvidiaApiKeyMissingError,
    NvidiaAuthError,
    NvidiaError,
    NvidiaModelMissingError,
    NvidiaRateLimitError,
    NvidiaUnavailableError,
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
    except NvidiaApiKeyMissingError:
        return _error(
            503,
            "NVIDIA_API_KEY가 설정되지 않았습니다. "
            "build.nvidia.com 에서 키를 발급받아 .env에 넣어주세요.",
        )
    except NvidiaUnavailableError:
        return _error(503, "NVIDIA API에 연결할 수 없습니다. 인터넷 연결을 확인하세요.")
    except NvidiaAuthError:
        return _error(
            502,
            "NVIDIA API 키가 올바르지 않습니다. .env의 NVIDIA_API_KEY를 확인하세요.",
        )
    except NvidiaRateLimitError:
        return _error(
            429, "NVIDIA API 요청 한도를 넘었습니다. 잠시 후 다시 시도하세요."
        )
    except NvidiaModelMissingError:
        return _error(
            502,
            f'모델 "{settings.nvidia_model}"을 찾을 수 없습니다. '
            "build.nvidia.com 에서 모델 ID를 확인하세요.",
        )
    except NvidiaError as exc:
        return _error(502, exc.detail)

    return StreamingResponse(chunks, media_type="text/plain; charset=utf-8")
