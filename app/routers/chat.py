"""POST /api/chat — 대화 기록을 받아 LLM 답변을 스트리밍으로 돌려준다."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.auth import get_current_user
from app.config import settings
from app.llm import (
    LlmError,
    LlmModelMissingError,
    LlmUnavailableError,
    stream_chat,
)
from app.schemas import ChatRequest

# 라우터: 관련된 엔드포인트를 묶는 단위. main.py에서 app.include_router()로 앱에 붙인다.
# dependencies: 이 라우터의 모든 엔드포인트가 실행 전에 거치는 의존성.
# get_current_user가 로그인 안 된 요청을 401로 돌려보내서, 채팅은 로그인한 사용자만 쓸 수 있다.
# (엔드포인트에서 사용자 정보가 필요해지면 그때 인자로 `user: CurrentUser`를 받으면 된다)
router = APIRouter(dependencies=[Depends(get_current_user)])


def _error(status_code: int, message: str) -> JSONResponse:
    # 오류 본문을 항상 {"error": "..."} 모양으로 통일한다. 프론트가 이 키를 읽어 화면에 띄운다.
    return JSONResponse(status_code=status_code, content={"error": message})


# response_model=None: 이 함수는 상황에 따라 JSON 오류나 스트림을 돌려주므로
# FastAPI에게 응답 모양을 자동으로 문서화·검증하지 말라고 알려준다.
@router.post("/chat", response_model=None)
async def chat(body: ChatRequest) -> Response:
    # body는 이미 schemas.py 규칙대로 검증된 상태로 들어온다.
    try:
        # 여기서 LLM 서버에 연결하고 상태 코드까지 확인한다.
        # 연결·모델 오류는 스트림을 시작하기 *전에* 알아내야 제대로 된 HTTP 상태 코드로 응답할 수 있다.
        # (스트림이 한 번 시작되면 상태 코드 200은 이미 보내진 뒤라 바꿀 수 없다.)
        chunks = await stream_chat(body.messages)
    except LlmUnavailableError:
        # 503 Service Unavailable: 우리 서버는 멀쩡한데 의존하는 서비스가 꺼져 있음
        return _error(
            503,
            f"LLM 서버({settings.llm_base_url})에 연결할 수 없습니다. "
            "Ollama가 실행 중인지 확인하세요 (ollama serve).",
        )
    except LlmModelMissingError:
        # 502 Bad Gateway: 뒤쪽 서버(Ollama)가 오류 응답을 줌
        return _error(
            502,
            f'모델 "{settings.llm_model}"을 찾을 수 없습니다. '
            f"ollama pull {settings.llm_model} 으로 받아주세요.",
        )
    except LlmError as exc:
        return _error(502, exc.detail)

    # StreamingResponse는 비동기 제너레이터에서 조각이 나올 때마다 바로 클라이언트로 흘려보낸다.
    # 그래서 답변 전체가 완성되기 전부터 화면에 글자가 한 조각씩 나타난다.
    # SSE가 아니라 순수 텍스트라서 프론트는 받은 바이트를 그대로 이어 붙이기만 하면 된다.
    return StreamingResponse(chunks, media_type="text/plain; charset=utf-8")
