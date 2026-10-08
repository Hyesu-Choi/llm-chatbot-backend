"""DB 테이블 정의 (SQLAlchemy ORM 모델).

클래스 하나 = 테이블 하나, 속성 하나 = 컬럼 하나.
여기를 고친 뒤 `uv run alembic revision --autogenerate -m "설명"` 을 하면
Alembic이 지금 DB와 비교해서 바뀐 부분만 마이그레이션 파일로 만들어 준다.

pydantic 스키마(app/schemas.py)와 헷갈리지 않기:
    models.py  = DB에 저장되는 모양 (password_hash 같은 내부 값 포함)
    schemas.py = API로 주고받는 모양 (비밀번호 해시는 절대 응답에 넣지 않음)
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 이메일 최대 길이 (RFC 5321 기준 254자)
EMAIL_MAX_LENGTH = 254
CONVERSATION_TITLE_MAX_LENGTH = 100
# assistant-ui가 만드는 메시지 id 길이 여유분
CLIENT_MESSAGE_ID_MAX_LENGTH = 64
DOCUMENT_FILENAME_MAX_LENGTH = 255
# 임베딩 벡터의 숫자 개수. 임베딩 모델이 정한다 (bge-m3 = 1024).
# 컬럼 타입에 박혀 들어가서, 다른 크기의 모델로 바꾸려면 마이그레이션으로 컬럼을 바꾸고 문서를 다시 임베딩해야 한다.
EMBEDDING_DIMENSIONS = 1024


class User(Base):
    __tablename__ = "users"

    # Mapped[int]: 파이썬 타입. 여기서 컬럼 타입(INTEGER)과 NOT NULL 여부가 자동으로 정해진다.
    # primary_key 정수 컬럼은 Postgres에서 자동 증가(SERIAL: 시퀀스에서 다음 번호를 꺼냄)가 된다.
    # MySQL의 AUTO_INCREMENT와 같은 역할.
    id: Mapped[int] = mapped_column(primary_key=True)
    # unique=True: 같은 이메일로 두 번 가입 불가. DB가 막아주는 게 코드 검사보다 확실하다(동시 요청에도 안전).
    # 대소문자가 섞여 들어오지 않도록 저장 전에 소문자로 바꾸는 건 가입 API에서 한다.
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), unique=True)
    # 비밀번호 원문이 아니라 argon2 해시를 저장한다. 해시는 되돌릴 수 없어서 DB가 털려도 원문을 알 수 없다.
    password_hash: Mapped[str] = mapped_column(String(255))
    # timezone=True → Postgres TIMESTAMPTZ. 시간대를 함께 저장해서 서버 위치(서울/미국)가 바뀌어도 안전하다.
    # server_default=func.now(): 값을 안 넣으면 DB가 INSERT 시각을 채운다 (파이썬이 아니라 DB 시계 기준).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base):
    """대화방 하나. 사이드바 목록의 한 줄이 이것이다."""

    __tablename__ = "conversations"

    # 정수 대신 UUID(무작위 128비트 값)를 id로 쓴다.
    # 주소창이나 API에 /conversations/5 처럼 드러나면 "6, 7도 있겠네" 하고 남의 대화를 찔러보기 쉽고,
    # 전체 대화 수도 짐작할 수 있다. UUID는 추측이 불가능하다.
    # default=uuid.uuid4: INSERT할 때 파이썬이 새 UUID를 만들어 넣는다.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # ForeignKey: users.id 에 실제로 있는 값만 들어갈 수 있다 (없는 사용자의 대화는 못 만든다).
    # ondelete="CASCADE": 사용자가 지워지면 DB가 그 사용자의 대화도 같이 지운다.
    # index=True: "내 대화 목록" 조회가 user_id로 걸러지므로 인덱스로 빠르게 찾는다.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Mapped[str | None]: None 허용 = NULL 가능 컬럼. 첫 질문 전에는 제목이 없다.
    title: Mapped[str | None] = mapped_column(String(CONVERSATION_TITLE_MAX_LENGTH))
    # 보관함으로 옮긴 대화. 지우지 않고 목록에서만 숨긴다.
    # server_default=false(): 기존 행 · 값을 안 넣은 INSERT에 DB가 FALSE를 채운다
    archived: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 마지막으로 메시지가 오간 시각. 목록을 최근 대화 순으로 정렬하는 데 쓴다.
    # onupdate=func.now(): ORM으로 이 행을 UPDATE할 때 자동으로 지금 시각으로 바뀐다.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    """대화 안의 메시지 하나 (사용자 질문 또는 AI 답변)."""

    __tablename__ = "messages"
    __table_args__ = (
        # 같은 대화 안에서 message_id는 하나뿐. 이 제약 덕분에 같은 메시지를 두 번 저장하면
        # 새 행이 생기지 않고 기존 행을 고치는 "upsert"를 할 수 있다 (routers/conversations.py).
        UniqueConstraint("conversation_id", "message_id"),
        # CHECK 제약: role에 이 두 값 말고는 DB가 아예 거부한다. 코드에 버그가 있어도 이상한 값이 안 쌓인다.
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # 여기엔 index=True를 따로 걸지 않았다. 위 UniqueConstraint("conversation_id", "message_id")가
    # 만드는 인덱스가 conversation_id로 시작해서, "이 대화의 메시지 전부" 조회에도 그대로 쓰이기 때문.
    # (복합 인덱스는 앞쪽 컬럼만으로 찾을 때도 쓸 수 있다. 같은 역할 인덱스를 또 만들면 쓰기만 느려진다)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    # 프론트(assistant-ui)가 붙인 메시지 id. 위의 id(DB용)와 별개로, 화면과 주고받을 때는 이걸 쓴다.
    message_id: Mapped[str] = mapped_column(String(CLIENT_MESSAGE_ID_MAX_LENGTH))
    # 이 메시지가 어느 메시지에 대한 답인지 (첫 메시지는 NULL).
    # 지금은 질문 → 답변 → 질문 … 한 줄이지만, 나중에 "답변 다시 생성"을 하면
    # 같은 부모 아래 답변이 여러 개 달리는 가지(branch)가 생긴다. 그걸 표현하려고 미리 저장해 둔다.
    parent_message_id: Mapped[str | None] = mapped_column(
        String(CLIENT_MESSAGE_ID_MAX_LENGTH)
    )
    role: Mapped[str] = mapped_column(String(16))
    # Text: 길이 제한 없는 문자열 (Postgres TEXT). AI 답변은 길어질 수 있다.
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    """사용자가 올린 문서 하나 (RAG의 원본)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(DOCUMENT_FILENAME_MAX_LENGTH))
    # 원문 전체. 텍스트 · 마크다운이라 DB에 바로 둔다 (PDF 같은 큰 원본 파일은 보통 S3에 두고 경로만 저장)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentChunk(Base):
    """문서를 잘게 나눈 조각 하나 + 그 조각의 임베딩 벡터. 질문과 비슷한 조각을 찾는 검색 대상이다.

    왜 문서를 통째로가 아니라 조각으로 저장하나?
        1. 검색 정확도: 긴 문서 하나의 벡터는 여러 주제가 뭉개진 "평균"이 된다. 조각이 작을수록 한 가지 뜻을 담는다.
        2. LLM 입력 길이: 찾은 조각 몇 개만 LLM에 넣으면 되니, 문서가 아무리 길어도 컨텍스트 창을 넘지 않는다.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        # HNSW 인덱스: "가장 가까운 벡터 찾기"를 빠르게 해 주는 pgvector 전용 인덱스.
        # 인덱스가 없으면 질문마다 모든 조각과 거리를 계산(전체 스캔)한다. 조각이 수천 개 이하면 그래도 빠르지만,
        # 수십만 개가 되면 느려진다. HNSW는 정확도를 아주 조금 양보하고 훨씬 빠르게 찾는다(근사 최근접 이웃).
        # vector_cosine_ops: 코사인 거리(<=>)로 검색할 때 쓰는 인덱스라는 뜻. 검색 쿼리와 거리 종류가 같아야 인덱스를 탄다.
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # 문서 안에서 몇 번째 조각인지 (0부터). 출처를 "○○.md의 3번째 부분"처럼 보여줄 때 쓴다.
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # Vector(1024): pgvector의 벡터 타입. 파이썬에선 숫자 리스트(list[float])로 다룬다.
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
