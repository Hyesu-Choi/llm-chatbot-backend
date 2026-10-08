"""내 문서 올리기 · 목록 · 삭제 (/api/documents). RAG의 검색 대상이 되는 문서를 관리한다.

올리기 흐름:
    파일 받기 → UTF-8 글자로 읽기 → 조각내기(split_text) → 조각마다 임베딩(embed) → 문서 + 조각 저장
"""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.config import settings
from app.db import get_db
from app.llm import embed
from app.models import DOCUMENT_FILENAME_MAX_LENGTH, Document, DocumentChunk
from app.rag import split_text
from app.schemas import DocumentResponse

router = APIRouter(prefix="/documents")

Db = Annotated[AsyncSession, Depends(get_db)]

ALLOWED_EXTENSIONS = {".txt", ".md"}
# 한 번에 임베딩 요청할 조각 수. 너무 많이 보내면 요청 하나가 오래 걸리거나 메모리를 많이 쓴다.
EMBED_BATCH_SIZE = 32


def _to_response(document: Document, chunk_count: int) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        char_count=len(document.content),
        chunk_count=chunk_count,
        created_at=document.created_at,
    )


async def _read_text(file: UploadFile) -> str:
    """업로드 파일을 검사하고 글자로 읽는다. 문제가 있으면 사람이 읽을 수 있는 400 오류."""
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "텍스트(.txt)나 마크다운(.md) 파일만 올릴 수 있습니다.",
        )
    # 한도보다 1바이트 더 읽어 보고, 그만큼 읽히면 너무 큰 파일. (파일 전체를 다 읽기 전에 거를 수 있다)
    raw = await file.read(settings.document_max_bytes + 1)
    if len(raw) > settings.document_max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "파일이 너무 큽니다 (최대 1MB)."
        )
    try:
        # utf-8-sig: 윈도우 메모장이 파일 맨 앞에 붙이는 BOM 표시를 자동으로 떼어 준다
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "UTF-8 텍스트 파일이 아닙니다. 인코딩을 확인해 주세요.",
        ) from None


# UploadFile: multipart/form-data로 온 파일. (JSON이 아니라 브라우저 <form>의 파일 전송 방식)
# 이걸 쓰려면 python-multipart 패키지가 필요하다.
@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
async def upload_document(
    file: UploadFile, user: CurrentUser, db: Db
) -> DocumentResponse:
    text = await _read_text(file)
    chunks = split_text(text, settings.rag_chunk_size, settings.rag_chunk_overlap)
    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "내용이 비어 있는 문서입니다.")

    # 임베딩은 DB에 쓰기 "전에" 전부 계산한다. 중간에 Ollama 오류가 나면 아무것도 저장되지 않게
    # (문서만 있고 조각이 반쯤 빠진 어중간한 상태를 남기지 않기 위해)
    embeddings: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        embeddings += await embed(chunks[start : start + EMBED_BATCH_SIZE])

    document = Document(
        user_id=user.id,
        filename=(file.filename or "untitled")[:DOCUMENT_FILENAME_MAX_LENGTH],
        content=text,
    )
    db.add(document)
    # flush: COMMIT 전에 INSERT만 먼저 보내서 document.id 등을 확정한다 (트랜잭션은 아직 열려 있음)
    await db.flush()
    db.add_all(
        DocumentChunk(
            document_id=document.id, chunk_index=index, content=chunk, embedding=vector
        )
        for index, (chunk, vector) in enumerate(zip(chunks, embeddings, strict=True))
    )
    # 문서 1개 + 조각 N개를 한 트랜잭션으로 한 번에 확정
    await db.commit()
    await db.refresh(document)
    return _to_response(document, len(chunks))


@router.get("", response_model=list[DocumentResponse])
async def list_documents(user: CurrentUser, db: Db) -> list[DocumentResponse]:
    # 문서마다 조각 수를 같이 센다: LEFT JOIN + GROUP BY + COUNT
    # (문서를 먼저 다 가져온 뒤 문서마다 COUNT 쿼리를 따로 날리면 문서 수만큼 쿼리가 나간다 = N+1 문제)
    chunk_count = func.count(DocumentChunk.id).label("chunk_count")
    rows = await db.execute(
        select(Document, chunk_count)
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .where(Document.user_id == user.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    return [_to_response(document, count) for document, count in rows]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, user: CurrentUser, db: Db) -> None:
    document = await db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    if document is None:
        # 대화와 마찬가지로 남의 문서는 "없는 것"으로 답한다
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾을 수 없습니다.")
    # 조각들은 ondelete="CASCADE"로 DB가 같이 지운다
    await db.delete(document)
    await db.commit()
