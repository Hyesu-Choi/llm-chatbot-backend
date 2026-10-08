"""대화 목록 · 메시지 저장 API (/api/conversations/...). 모두 로그인 필요.

프론트(assistant-ui)가 부르는 순서:
    1. 앱 열기        GET    /conversations                    사이드바 목록
    2. 대화 클릭      GET    /conversations/{id}/messages       그 대화의 메시지 불러오기
    3. 새 대화 첫 질문 POST   /conversations                    대화방 만들기 → id 받기
    4. 질문 · 답변마다 PUT    /conversations/{id}/messages/{mid} 메시지 저장 (같은 mid면 덮어쓰기)
    5. 첫 답변 후      POST   /conversations/{id}/title        LLM이 첫 질문을 요약해 제목 붙이기
       (이름 바꾸기)   PATCH  /conversations/{id}  {"title"}    사용자가 직접 제목 수정
    6. 보관 · 삭제     PATCH  {"archived"} / DELETE

AI 답변 생성 자체(/api/chat)는 여기와 따로다. 프론트가 답변을 다 받은 뒤 4번으로 저장한다.
"""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.config import settings
from app.db import get_db
from app.llm import LlmError, LlmModelMissingError, LlmUnavailableError, complete_chat
from app.models import CONVERSATION_TITLE_MAX_LENGTH, Conversation, Message, User
from app.schemas import (
    ConversationResponse,
    ConversationUpdate,
    MessageResponse,
    MessageUpsert,
    TitleRequest,
)

router = APIRouter(prefix="/conversations")

# LLM이 실패했을 때 쓰는 제목 길이 (첫 질문 앞부분)
FALLBACK_TITLE_LENGTH = 30
# 제목 요약용 지시문. 작은 모델은 "제목:" 같은 군더더기를 붙이기 쉬워서 출력 형식을 구체적으로 못 박는다.
# 예시(few-shot)를 하나 주면 작은 모델도 "질문의 핵심 단어를 살린다"는 의도를 훨씬 잘 따른다.
TITLE_PROMPT = (
    "사용자의 첫 질문을 보고 대화 목록에 표시할 짧은 제목을 지어 주세요. "
    "질문이 무엇을 원하는지 핵심 단어를 그대로 살려 한국어 15자 이내 명사형으로 쓰고, "
    "따옴표 · 마침표 · 이모지 · '제목:' 같은 머리말 없이 제목 한 줄만 출력하세요.\n"
    "예) 질문: 회사 동료 부탁을 정중하게 거절하는 말 알려줘 → 동료 부탁 정중히 거절하기"
)

Db = Annotated[AsyncSession, Depends(get_db)]


async def _get_own_conversation(
    db: AsyncSession, user: User, conversation_id: uuid.UUID
) -> Conversation:
    """내 대화만 꺼낸다. 없거나 남의 대화면 똑같이 404.

    남의 대화일 때 403(권한 없음)으로 답하면 "그 id의 대화가 존재한다"는 사실을 알려주게 된다.
    그래서 존재 여부 자체를 숨기려고 404로 통일한다.
    """
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            # 이 조건이 핵심. 다른 사용자의 대화 id를 알아내도 user_id가 달라서 조회되지 않는다.
            Conversation.user_id == user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화를 찾을 수 없습니다.")
    return conversation


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(user: CurrentUser, db: Db) -> list[Conversation]:
    # 보관된 대화도 같이 내려준다. 화면에서 "보관함"으로 따로 보여줄지는 프론트가 정한다.
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    # scalars()는 결과 행에서 첫 번째 값(여기선 Conversation 객체)만 뽑아준다. all()로 리스트로 만든다.
    return list(result.all())


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=ConversationResponse
)
async def create_conversation(user: CurrentUser, db: Db) -> Conversation:
    conversation = Conversation(user_id=user.id)
    db.add(conversation)
    await db.commit()
    # refresh: created_at · updated_at처럼 DB가 채운 값(server_default)을 다시 읽어 온다
    await db.refresh(conversation)
    return conversation


# {conversation_id}: 경로 매개변수. 타입을 uuid.UUID로 적으면 FastAPI가 형식을 검사해서
# "abc" 같은 값은 함수가 실행되기 전에 422로 걸러준다.
@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: Db
) -> Conversation:
    return await _get_own_conversation(db, user, conversation_id)


# PATCH = 일부만 수정. (PUT은 보통 "통째로 교체"라는 뜻이라 구분해서 쓴다)
@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: uuid.UUID, body: ConversationUpdate, user: CurrentUser, db: Db
) -> Conversation:
    conversation = await _get_own_conversation(db, user, conversation_id)
    # 보낸 필드만 바꾼다. 객체 속성을 바꾸면 세션이 변경을 기억했다가 commit 때 UPDATE를 보낸다.
    if body.title is not None:
        conversation.title = body.title
    if body.archived is not None:
        conversation.archived = body.archived
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: Db
) -> None:
    conversation = await _get_own_conversation(db, user, conversation_id)
    # 메시지는 따로 안 지워도 된다. messages.conversation_id의 ondelete="CASCADE"로 DB가 같이 지운다.
    await db.delete(conversation)
    await db.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: uuid.UUID, user: CurrentUser, db: Db
) -> list[Message]:
    # 메시지 테이블에는 user_id가 없으므로, 먼저 대화가 내 것인지 확인하고 나서 메시지를 읽는다
    await _get_own_conversation(db, user, conversation_id)
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(result.all())


# PUT /messages/{message_id}: "이 id의 메시지를 이 내용으로 만들어 둬". 여러 번 보내도 결과가 같다(멱등).
# 그래서 네트워크가 끊겨 프론트가 다시 보내도 메시지가 두 번 저장되지 않는다.
@router.put("/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
async def upsert_message(
    conversation_id: uuid.UUID,
    message_id: str,
    body: MessageUpsert,
    user: CurrentUser,
    db: Db,
) -> Message:
    await _get_own_conversation(db, user, conversation_id)

    # upsert = INSERT + UPDATE. "없으면 넣고, (conversation_id, message_id)가 이미 있으면 내용만 고친다"
    # Postgres의 INSERT ... ON CONFLICT DO UPDATE 문으로, DB가 한 번에 처리해서 동시 요청에도 안전하다.
    # (MySQL에선 같은 일을 INSERT ... ON DUPLICATE KEY UPDATE 로 한다)
    new_row = insert(Message).values(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        message_id=message_id,
        parent_message_id=body.parent_message_id,
        role=body.role,
        content=body.content,
    )
    statement = new_row.on_conflict_do_update(
        # 어떤 unique 제약에 부딪혔을 때 UPDATE로 바꿀지 (models.py의 UniqueConstraint)
        index_elements=[Message.conversation_id, Message.message_id],
        # excluded = "넣으려다 거절된 새 값". 그 값으로 기존 행의 내용을 덮어쓴다
        set_={
            "parent_message_id": new_row.excluded.parent_message_id,
            "content": new_row.excluded.content,
        },
        # RETURNING: 저장된 행을 바로 돌려받아 다시 조회하지 않아도 된다
    ).returning(Message)
    message = await db.scalar(statement)

    # 목록 정렬용 "마지막 활동 시각"을 갱신한다.
    # 위 객체를 고치는 대신 UPDATE 문을 직접 쓴 이유: 대화 객체를 다시 읽어 올 필요 없이 한 문장으로 끝난다.
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    # 두 문장(메시지 저장 + 시각 갱신)을 한 트랜잭션으로 함께 반영한다. 하나만 반영되는 일은 없다.
    await db.commit()
    return message


def _clean_title(raw: str) -> str:
    """LLM 출력에서 제목 한 줄만 남긴다. 작은 모델은 지시를 어기고 따옴표나 여러 줄을 붙이곤 한다."""
    first_line = raw.strip().splitlines()[0] if raw.strip() else ""
    title = first_line.removeprefix("제목:").strip().strip("\"'“”‘’「」`*#.")
    return title[:CONVERSATION_TITLE_MAX_LENGTH].strip()


def _fallback_title(question: str) -> str:
    # 공백 · 줄바꿈을 한 칸으로 정리하고 앞부분만 자른다
    text = " ".join(question.split())
    if len(text) <= FALLBACK_TITLE_LENGTH:
        return text
    return text[:FALLBACK_TITLE_LENGTH] + "…"


@router.post("/{conversation_id}/title", response_model=ConversationResponse)
async def generate_title(
    conversation_id: uuid.UUID, body: TitleRequest, user: CurrentUser, db: Db
) -> Conversation:
    conversation = await _get_own_conversation(db, user, conversation_id)

    try:
        # 사용자가 고른 모델이 아니라 서버 기본 모델로 요약한다 (작고 빠른 모델이면 충분한 일)
        raw = await complete_chat(
            [
                {"role": "system", "content": TITLE_PROMPT},
                {"role": "user", "content": body.question},
            ],
            settings.llm_model,
        )
        title = _clean_title(raw)
    except (
        LlmUnavailableError,
        LlmModelMissingError,
        LlmError,
        httpx.TimeoutException,
    ):
        # 제목은 있으면 좋은 부가 기능이라, LLM이 실패해도 오류를 내지 않고 질문 앞부분으로 대신한다.
        # (채팅 답변은 이미 성공했는데 제목 때문에 오류 화면을 띄우면 이상하다)
        title = ""

    conversation.title = title or _fallback_title(body.question)
    await db.commit()
    await db.refresh(conversation)
    return conversation
