"""POST /api/chat — 대화 기록을 받아 LLM 답변을 스트리밍으로 돌려준다.

사용자가 올린 문서가 있으면 질문과 관련된 조각을 찾아 근거로 붙인다 (RAG, app/rag.py).
"""

import json
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.config import settings
from app.db import get_db
from app.llm import embed, stream_chat, trim_history
from app.models import Document, User
from app.personas import DEFAULT_PERSONA_ID, PERSONAS_BY_ID
from app.rag import SearchHit, build_context, search_chunks
from app.schemas import ChatRequest

# 라우터: 관련된 엔드포인트를 묶는 단위. main.py에서 app.include_router()로 앱에 붙인다.
# dependencies: 이 라우터의 모든 엔드포인트가 실행 전에 거치는 의존성.
# get_current_user가 로그인 안 된 요청을 401로 돌려보내서, 채팅은 로그인한 사용자만 쓸 수 있다.
# (엔드포인트에서 사용자 정보가 필요해지면 그때 인자로 `user: CurrentUser`를 받으면 된다)
router = APIRouter(dependencies=[Depends(get_current_user)])

# RAG로 참고한 문서 목록을 담아 보내는 응답 헤더 이름.
# 본문은 순수 텍스트 스트림이라 끼워 넣을 자리가 없어서, 스트림 시작 전에 나가는 헤더에 싣는다.
SOURCES_HEADER = "X-RAG-Sources"


async def _find_relevant_chunks(
    db: AsyncSession, user: User, question: str
) -> list[SearchHit]:
    # 문서가 하나도 없으면 임베딩 계산도 건너뛴다 (임베딩 모델을 안 받은 사용자도 채팅은 되게)
    has_documents = await db.scalar(select(exists().where(Document.user_id == user.id)))
    if not has_documents:
        return []
    [question_embedding] = await embed([question])
    return await search_chunks(
        db,
        user.id,
        question_embedding,
        settings.rag_top_k,
        settings.rag_min_similarity,
        settings.rag_relative_margin,
    )


def _sources_header(hits: list[SearchHit]) -> str:
    sources = [
        {
            "filename": hit.filename,
            "chunk": hit.chunk_index + 1,
            "similarity": round(hit.similarity, 2),
        }
        for hit in hits
    ]
    # HTTP 헤더에는 ASCII 글자만 넣을 수 있어서 한글 파일명을 %EC%84%... 형태로 인코딩한다.
    # 프론트는 decodeURIComponent → JSON.parse로 되돌린다.
    return quote(json.dumps(sources, ensure_ascii=False))


# response_model=None: 성공하면 스트림을 돌려주므로 FastAPI에게 응답 모양을 자동으로 문서화·검증하지 말라고 알려준다.
@router.post("/chat", response_model=None)
async def chat(
    body: ChatRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    # body는 이미 schemas.py 규칙대로 검증된 상태로 들어온다.
    model = body.model or settings.llm_model
    if model not in settings.allowed_models:
        # 400 Bad Request: 형식은 맞지만 허용되지 않는 값
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f'선택할 수 없는 모델입니다: "{model}"'
        )

    persona = PERSONAS_BY_ID.get(body.persona or DEFAULT_PERSONA_ID)
    if persona is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f'없는 역할입니다: "{body.persona}"'
        )

    # 긴 대화는 최근 메시지만 보낸다 (시스템 프롬프트가 잘려 나가지 않게, app/llm.py 참고)
    messages = trim_history(body.messages, settings.llm_max_history_chars)

    # RAG: 마지막 질문과 비슷한 내 문서 조각을 찾아 시스템 프롬프트 뒤에 참고 자료로 붙인다
    system_prompt = persona.prompt
    headers: dict[str, str] = {}
    hits = await _find_relevant_chunks(db, user, messages[-1].content)
    if hits:
        system_prompt += build_context(hits)
        headers[SOURCES_HEADER] = _sources_header(hits)

    # 여기서 LLM 서버에 연결하고 상태 코드까지 확인한다.
    # 연결·모델 오류는 스트림을 시작하기 *전에* 알아내야 제대로 된 HTTP 상태 코드로 응답할 수 있다.
    # (스트림이 한 번 시작되면 상태 코드 200은 이미 보내진 뒤라 바꿀 수 없다.)
    # 오류가 나면 예외가 그대로 올라가고, main.py의 LLM 예외 처리기가 503/502 응답으로 바꾼다.
    chunks = await stream_chat(messages, model, system_prompt)

    # StreamingResponse는 비동기 제너레이터에서 조각이 나올 때마다 바로 클라이언트로 흘려보낸다.
    # 그래서 답변 전체가 완성되기 전부터 화면에 글자가 한 조각씩 나타난다.
    # SSE가 아니라 순수 텍스트라서 프론트는 받은 바이트를 그대로 이어 붙이기만 하면 된다.
    return StreamingResponse(
        chunks, media_type="text/plain; charset=utf-8", headers=headers
    )
