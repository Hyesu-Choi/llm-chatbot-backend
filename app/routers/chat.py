"""POST /api/chat — 대화 기록을 받아 LLM 답변을 스트리밍으로 돌려준다."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.auth import get_current_user
from app.config import settings
from app.llm import stream_chat
from app.schemas import ChatRequest

# 라우터: 관련된 엔드포인트를 묶는 단위. main.py에서 app.include_router()로 앱에 붙인다.
# dependencies: 이 라우터의 모든 엔드포인트가 실행 전에 거치는 의존성.
# get_current_user가 로그인 안 된 요청을 401로 돌려보내서, 채팅은 로그인한 사용자만 쓸 수 있다.
# (엔드포인트에서 사용자 정보가 필요해지면 그때 인자로 `user: CurrentUser`를 받으면 된다)
router = APIRouter(dependencies=[Depends(get_current_user)])


# response_model=None: 성공하면 스트림을 돌려주므로 FastAPI에게 응답 모양을 자동으로 문서화·검증하지 말라고 알려준다.
@router.post("/chat", response_model=None)
async def chat(body: ChatRequest) -> StreamingResponse:
    # body는 이미 schemas.py 규칙대로 검증된 상태로 들어온다.
    model = body.model or settings.llm_model
    if model not in settings.allowed_models:
        # 400 Bad Request: 형식은 맞지만 허용되지 않는 값
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f'선택할 수 없는 모델입니다: "{model}"'
        )

    # 여기서 LLM 서버에 연결하고 상태 코드까지 확인한다.
    # 연결·모델 오류는 스트림을 시작하기 *전에* 알아내야 제대로 된 HTTP 상태 코드로 응답할 수 있다.
    # (스트림이 한 번 시작되면 상태 코드 200은 이미 보내진 뒤라 바꿀 수 없다.)
    # 오류가 나면 예외가 그대로 올라가고, main.py의 LLM 예외 처리기가 503/502 응답으로 바꾼다.
    chunks = await stream_chat(body.messages, model)

    # StreamingResponse는 비동기 제너레이터에서 조각이 나올 때마다 바로 클라이언트로 흘려보낸다.
    # 그래서 답변 전체가 완성되기 전부터 화면에 글자가 한 조각씩 나타난다.
    # SSE가 아니라 순수 텍스트라서 프론트는 받은 바이트를 그대로 이어 붙이기만 하면 된다.
    return StreamingResponse(chunks, media_type="text/plain; charset=utf-8")
