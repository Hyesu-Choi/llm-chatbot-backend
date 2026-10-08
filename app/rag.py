"""RAG(Retrieval-Augmented Generation, 검색 증강 생성)의 핵심 로직.

LLM은 학습한 적 없는 "내 문서"의 내용을 모른다. 그래서 질문할 때마다
    1. 검색(Retrieval)  : 질문과 비슷한 문서 조각을 DB에서 찾고
    2. 증강(Augmented) : 찾은 조각을 시스템 프롬프트에 붙여서
    3. 생성(Generation): LLM이 그 자료를 근거로 답하게 한다.

문서를 올릴 때는 split_text로 조각낸 뒤 임베딩해서 저장하고(routers/documents.py),
질문할 때는 search_chunks로 찾아서 build_context로 프롬프트를 만든다(routers/chat.py).
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk

# 빈 줄(공백만 있는 줄 포함) = 문단 경계
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
# 마크다운 제목 줄(# ~ ######) 바로 앞 = 섹션 경계. (?=...)는 제목 줄 자체는 남기고 그 앞에서만 자른다
SECTION_BREAK = re.compile(r"^(?=#{1,6} )", re.MULTILINE)


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """문서를 size 글자 이하의 조각으로 나눈다.

    1. 마크다운 제목(#, ##, …)으로 섹션을 나눈다. 섹션이 다르면 짧아도 절대 한 조각으로 합치지 않는다.
       (처음엔 이 단계가 없어서, 짧은 섹션 4개(출장 · 휴가 · 재택 · 법인카드)가 한 조각으로 합쳐졌다.
        주제가 섞인 조각은 벡터가 "평균"이 되어 어느 질문과도 어중간하게만 비슷해진다 → 검색이 흐려짐)
    2. 섹션 안에서는 split_section으로 문단 단위로 나누고 합친다.
    """
    chunks: list[str] = []
    pending_heading = ""
    for section in SECTION_BREAK.split(text):
        section = section.strip()
        if not section:
            continue
        # 제목 줄만 있고 본문이 없는 섹션(문서 제목 등)은 혼자 조각이 되지 않게 다음 섹션 앞에 붙인다.
        # 내용 없는 짧은 조각은 아무 질문과도 어중간하게 비슷해서 엉뚱한 검색 결과(오탐)를 낸다.
        if "\n" not in section and section.startswith("#"):
            pending_heading = f"{pending_heading}{section}\n"
            continue
        chunks += _split_section(pending_heading + section, size, overlap)
        pending_heading = ""
    if pending_heading:
        chunks += _split_section(pending_heading, size, overlap)
    return chunks


def _split_section(text: str, size: int, overlap: int) -> list[str]:
    """섹션 하나를 조각낸다.

    1. 문단(빈 줄) 단위로 나눈다. 문단은 보통 한 가지 이야기라 의미가 덜 끊긴다.
    2. size보다 긴 문단은 size 글자씩 자르되, 앞 조각과 overlap 글자를 겹치게 한다.
       (겹치지 않으면 경계에 걸친 문장이 반으로 쪼개져 양쪽 조각 모두에서 뜻이 사라진다)
    3. 짧은 문단들은 size를 넘지 않는 선에서 하나로 합친다 (너무 잘게 쪼개지 않게).
    """
    paragraphs = [p.strip() for p in PARAGRAPH_BREAK.split(text) if p.strip()]

    pieces: list[str] = []
    step = size - overlap
    for paragraph in paragraphs:
        if len(paragraph) <= size:
            pieces.append(paragraph)
            continue
        for start in range(0, len(paragraph), step):
            pieces.append(paragraph[start : start + size])
            if start + size >= len(paragraph):
                break

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > size:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


@dataclass(frozen=True)
class SearchHit:
    filename: str
    chunk_index: int
    content: str
    # 코사인 유사도 (1 = 같은 방향, 0 = 무관). 1 - 코사인 거리.
    similarity: float


async def search_chunks(
    db: AsyncSession,
    user_id: int,
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
    relative_margin: float,
) -> list[SearchHit]:
    """내 문서 조각 중에서 질문 벡터와 가장 비슷한 것 top_k개를 찾는다.

    cosine_distance(...)는 SQL로 `embedding <=> '[0.1, 0.2, ...]'` 가 된다 (pgvector의 코사인 거리 연산자).
    ORDER BY 거리 LIMIT k = "가장 가까운 k개". models.py의 HNSW 인덱스가 이 정렬을 빠르게 해 준다.
    """
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label(
        "distance"
    )
    rows = await db.execute(
        select(
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            Document.filename,
            distance,
        )
        # 조각 테이블에는 user_id가 없어서 문서 테이블과 JOIN해서 "내 문서"만 거른다.
        # 이 조건이 없으면 다른 사람의 문서 내용이 내 답변에 섞여 나온다 (가장 중요한 줄)
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(Document.user_id == user_id)
        .order_by(distance)
        .limit(top_k)
    )
    hits = [
        SearchHit(
            filename=row.filename,
            chunk_index=row.chunk_index,
            content=row.content,
            similarity=1 - row.distance,
        )
        for row in rows
    ]
    if not hits:
        return []
    # "가장 가까운 것"이 꼭 "관련 있는 것"은 아니다. 질문과 무관한 문서밖에 없어도 뭔가는 1등이 된다.
    # 그래서 두 가지 기준으로 거른다:
    #   절대 기준: 유사도가 min_similarity 미만이면 버림 (관련 없는 질문에 문서가 끼어드는 걸 막음)
    #   상대 기준: 1등보다 relative_margin 넘게 낮으면 버림 (정답 옆에 덜 관련된 조각이 따라붙는 걸 막음)
    cutoff = max(min_similarity, hits[0].similarity - relative_margin)
    return [hit for hit in hits if hit.similarity >= cutoff]


def build_context(hits: list[SearchHit]) -> str:
    """찾은 조각들을 시스템 프롬프트 뒤에 붙일 참고 자료 문단으로 만든다."""
    sources = "\n\n".join(
        f"[{number}] {hit.filename} (조각 {hit.chunk_index + 1})\n{hit.content}"
        for number, hit in enumerate(hits, start=1)
    )
    return (
        "\n\n아래는 사용자가 올린 문서에서 질문과 관련해 찾은 자료입니다. "
        "질문에 답할 때 이 자료를 우선 근거로 삼고, 근거로 쓴 자료는 문장 끝에 [1]처럼 번호를 붙이세요. "
        "자료에 없는 내용은 지어내지 말고, 자료로 답할 수 없으면 그렇다고 말한 뒤 일반 지식으로 답하세요.\n\n"
        f"{sources}"
    )
