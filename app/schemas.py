"""요청 본문의 모양(스키마)을 정의한다.

FastAPI는 엔드포인트 인자 타입이 pydantic 모델이면, 요청 JSON을 그 모델로 검증·변환해 준다.
모양이 틀리면 엔드포인트 코드가 실행되기도 전에 422 오류를 자동으로 돌려준다.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    # Literal: 이 두 문자열만 허용. "system"을 보내면 422.
    # 시스템 프롬프트는 서버가 정하므로 클라이언트가 끼워 넣지 못하게 막는 것.
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # min_length=1: 빈 배열 [] 이면 422. 대화 기록 전체를 매번 보낸다 (서버는 대화를 저장하지 않음).
    messages: list[ChatMessage] = Field(min_length=1)
