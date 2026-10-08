"""API 요청 · 응답의 모양(스키마)을 정의한다.

FastAPI는 엔드포인트 인자 타입이 pydantic 모델이면, 요청 JSON을 그 모델로 검증·변환해 준다.
모양이 틀리면 엔드포인트 코드가 실행되기도 전에 422 오류를 자동으로 돌려준다.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import CLIENT_MESSAGE_ID_MAX_LENGTH, CONVERSATION_TITLE_MAX_LENGTH

# 메시지 본문 최대 길이 (글자 수)
MESSAGE_CONTENT_MAX_LENGTH = 100_000

# ── 채팅 ──────────────────────────────────────────────


class ChatMessage(BaseModel):
    # Literal: 이 두 문자열만 허용. "system"을 보내면 422.
    # 시스템 프롬프트는 서버가 정하므로 클라이언트가 끼워 넣지 못하게 막는 것.
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # min_length=1: 빈 배열 [] 이면 422. 대화 기록 전체를 매번 보낸다 (서버는 대화를 저장하지 않음).
    messages: list[ChatMessage] = Field(min_length=1)


# ── 인증 ──────────────────────────────────────────────

# 비밀번호 길이 제한. 최대값은 엄청 긴 문자열로 해시 계산을 오래 시키는 공격(DoS)을 막기 위한 것.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class SignupRequest(BaseModel):
    # EmailStr: 이메일 형식이 아니면 422 (email-validator 패키지가 검사)
    email: EmailStr
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )


class LoginRequest(BaseModel):
    email: EmailStr
    # 로그인은 길이 규칙을 검사하지 않는다. 틀리면 그냥 "올바르지 않습니다"로 처리.
    # (최대 길이만 둬서 해시 계산 DoS는 막는다)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class UserResponse(BaseModel):
    """API로 내보내는 사용자 정보. password_hash 같은 민감한 값은 여기에 없어서 절대 응답에 섞이지 않는다."""

    # from_attributes: SQLAlchemy User 객체를 그대로 넣으면 속성을 읽어 변환해 준다
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


# ── 대화 · 메시지 ─────────────────────────────────────


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # UUID 타입은 JSON으로 나갈 때 "3f2b..." 같은 문자열이 된다
    id: uuid.UUID
    title: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class ConversationUpdate(BaseModel):
    """PATCH 본문. 보낸 필드만 바꾼다 (둘 다 선택).

    None = "안 보냄"으로 취급한다. 그래서 제목을 지우는(NULL로 만드는) 기능은 없다.
    """

    title: str | None = Field(
        default=None, min_length=1, max_length=CONVERSATION_TITLE_MAX_LENGTH
    )
    archived: bool | None = None


class MessageUpsert(BaseModel):
    """PUT /conversations/{id}/messages/{message_id} 본문. message_id는 주소에 들어 있다."""

    parent_message_id: str | None = Field(
        default=None, max_length=CLIENT_MESSAGE_ID_MAX_LENGTH
    )
    role: Literal["user", "assistant"]
    # 너무 큰 본문으로 DB를 채우는 걸 막는 상한 (긴 코드 답변도 넉넉히 들어가는 크기)
    content: str = Field(max_length=MESSAGE_CONTENT_MAX_LENGTH)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: str
    parent_message_id: str | None
    role: str
    content: str
    created_at: datetime
