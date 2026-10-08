"""내 문서 올리기 · 삭제와 RAG 검색 (/api/documents, /api/chat).

임베딩은 가짜로 바꾸지만, 벡터 저장과 검색은 진짜 pgvector로 돈다.
가짜 임베딩 규칙: 문장에 들어 있는 키워드마다 정해진 방향(축)의 벡터를 만든다.
    "출장" → 0번 축, "휴가" → 1번 축, 그 밖의 문장 → 2번 축
그러면 "출장" 질문은 "출장" 조각과 유사도 1, "휴가" 조각과는 0이 되어 결과를 정확히 예상할 수 있다.
"""

import json
from urllib.parse import unquote

import pytest

from app.config import settings
from app.models import EMBEDDING_DIMENSIONS
from app.rag import split_text
from app.routers import chat, documents
from tests.helpers import login_as, signup

KEYWORD_AXES = {"출장": 0, "휴가": 1}
OTHER_AXIS = 2

TRAVEL_DOC = "출장 경비는 출장 후 30일 이내에 정산한다."
LEAVE_DOC = "휴가는 1년에 15일이다."


def _fake_vector(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    axes = [axis for keyword, axis in KEYWORD_AXES.items() if keyword in text] or [
        OTHER_AXIS
    ]
    for axis in axes:
        vector[axis] = 1.0
    return vector


@pytest.fixture
def fake_embed(monkeypatch) -> list[list[str]]:
    """임베딩을 가짜로 바꾸고, 어떤 문장들로 불렸는지 기록한 리스트를 돌려준다."""
    calls: list[list[str]] = []

    async def embed(texts):
        calls.append(texts)
        return [_fake_vector(text) for text in texts]

    # 가져다 쓰는 두 모듈 모두 바꿔 끼운다
    monkeypatch.setattr(documents, "embed", embed)
    monkeypatch.setattr(chat, "embed", embed)
    return calls


@pytest.fixture
def fake_llm(monkeypatch) -> list[str]:
    """LLM 답변을 가짜로 바꾸고, LLM이 받은 시스템 프롬프트를 기록한다."""
    prompts: list[str] = []

    async def stream_chat(messages, model, system_prompt):
        prompts.append(system_prompt)

        async def chunks():
            yield "답변"

        return chunks()

    monkeypatch.setattr(chat, "stream_chat", stream_chat)
    return prompts


def _upload(client, filename: str, content: str | bytes):
    data = content.encode() if isinstance(content, str) else content
    # files=: multipart/form-data로 파일을 보낸다 (브라우저의 <input type="file"> 전송과 같은 방식)
    return client.post("/api/documents", files={"file": (filename, data, "text/plain")})


def _ask(client, question: str):
    return client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": question}]}
    )


# ── 올리기 · 목록 · 삭제 ───────────────────────────────


def test_upload_splits_embeds_and_lists(client, fake_embed):
    signup(client)

    response = _upload(client, "규정.md", f"{TRAVEL_DOC}\n\n{LEAVE_DOC}")

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "규정.md"
    assert body["chunk_count"] == 1  # 짧은 두 문단은 한 조각으로 합쳐진다
    assert fake_embed == [[f"{TRAVEL_DOC}\n\n{LEAVE_DOC}"]]
    assert [d["filename"] for d in client.get("/api/documents").json()] == ["규정.md"]


@pytest.mark.parametrize(
    ("filename", "content", "message_part"),
    [
        ("보고서.pdf", "내용", ".txt"),
        ("빈문서.txt", "  \n\n  ", "비어"),
        ("euc-kr.txt", "한글".encode("euc-kr"), "UTF-8"),
    ],
)
def test_upload_rejects_bad_files(client, fake_embed, filename, content, message_part):
    signup(client)

    response = _upload(client, filename, content)

    assert response.status_code == 400
    assert message_part in response.json()["error"]
    assert fake_embed == []  # 거절된 파일은 임베딩까지 가지 않는다


def test_upload_rejects_too_large_file(client, fake_embed, monkeypatch):
    monkeypatch.setattr(settings, "document_max_bytes", 10)
    signup(client)

    assert _upload(client, "큰파일.txt", "가" * 100).status_code == 400


def test_delete_document(client, fake_embed):
    signup(client)
    document_id = _upload(client, "a.txt", TRAVEL_DOC).json()["id"]

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.get("/api/documents").json() == []


def test_other_users_documents_are_hidden(client, fake_embed):
    owner = signup(client, "owner@example.com")
    document_id = _upload(client, "비밀.txt", TRAVEL_DOC).json()["id"]
    signup(client, "other@example.com")

    assert client.get("/api/documents").json() == []
    assert client.delete(f"/api/documents/{document_id}").status_code == 404
    login_as(client, owner)
    assert len(client.get("/api/documents").json()) == 1


# ── 채팅에서 검색해 쓰기 (RAG) ─────────────────────────


def test_chat_uses_relevant_document_as_context(client, fake_embed, fake_llm):
    signup(client)
    _upload(client, "출장규정.md", TRAVEL_DOC)
    _upload(client, "휴가규정.md", LEAVE_DOC)

    response = _ask(client, "출장비는 언제까지 정산해?")

    # 관련 있는 출장 문서만 프롬프트에 들어가고, 휴가 문서는 들어가지 않는다
    assert TRAVEL_DOC in fake_llm[0]
    assert LEAVE_DOC not in fake_llm[0]
    sources = json.loads(unquote(response.headers["X-RAG-Sources"]))
    assert sources == [{"filename": "출장규정.md", "chunk": 1, "similarity": 1.0}]


def test_chat_ignores_documents_below_similarity_threshold(
    client, fake_embed, fake_llm
):
    signup(client)
    _upload(client, "출장규정.md", TRAVEL_DOC)

    response = _ask(client, "오늘 저녁 뭐 먹지?")

    # 가장 가까운 조각이 있긴 하지만 유사도 0이라 버려진다
    assert TRAVEL_DOC not in fake_llm[0]
    assert "X-RAG-Sources" not in response.headers


def test_chat_drops_hits_far_below_the_best_one(client, fake_embed, fake_llm):
    signup(client)
    _upload(client, "출장규정.md", TRAVEL_DOC)
    # "출장"과 "휴가"가 섞인 문서: 출장 질문과의 유사도가 0.71로 기준선(0.45)은 넘지만
    # 1등(출장 전용 문서, 1.0)보다 0.29나 낮아서 상대 기준(0.1)에 걸려 빠져야 한다
    _upload(client, "섞인문서.md", "출장 때 휴가를 붙여 써도 된다.")

    response = _ask(client, "출장비는 언제까지 정산해?")

    sources = json.loads(unquote(response.headers["X-RAG-Sources"]))
    assert [s["filename"] for s in sources] == ["출장규정.md"]


def test_chat_never_uses_other_users_documents(client, fake_embed, fake_llm):
    signup(client, "owner@example.com")
    _upload(client, "출장규정.md", TRAVEL_DOC)
    signup(client, "other@example.com")
    _upload(client, "휴가규정.md", LEAVE_DOC)

    _ask(client, "출장비는 언제까지 정산해?")

    # 다른 사람의 출장 문서는 나와 아무리 비슷해도 검색되면 안 된다
    assert TRAVEL_DOC not in fake_llm[0]


def test_chat_skips_embedding_when_user_has_no_documents(client, fake_embed, fake_llm):
    signup(client)

    _ask(client, "출장비는 언제까지 정산해?")

    # 임베딩 모델을 안 받은 사용자도 채팅이 되도록, 문서가 없으면 임베딩을 아예 부르지 않는다
    assert fake_embed == []


# ── 조각내기 단위 테스트 ───────────────────────────────


class TestSplitText:
    def test_short_paragraphs_are_merged(self):
        assert split_text("가\n\n나\n\n다", size=100, overlap=10) == ["가\n\n나\n\n다"]

    def test_paragraphs_are_not_merged_beyond_size(self):
        text = "가" * 60 + "\n\n" + "나" * 60
        assert split_text(text, size=100, overlap=10) == ["가" * 60, "나" * 60]

    def test_long_paragraph_is_cut_with_overlap(self):
        text = "".join(str(i % 10) for i in range(250))

        chunks = split_text(text, size=100, overlap=20)

        assert [len(c) for c in chunks] == [100, 100, 90]
        # 앞 조각의 마지막 20글자가 다음 조각의 처음 20글자로 겹친다
        assert chunks[0][-20:] == chunks[1][:20]

    def test_markdown_sections_are_never_merged(self):
        text = "# 규정\n\n## 출장\n출장 내용\n\n## 휴가\n휴가 내용"

        chunks = split_text(text, size=500, overlap=50)

        # 짧아도 섹션(제목)이 다르면 따로. 제목은 그 섹션 조각 안에 남아 검색에 도움이 된다
        # 본문 없는 문서 제목("# 규정")은 혼자 조각이 되지 않고 다음 섹션 앞에 붙는다
        assert chunks == ["# 규정\n## 출장\n출장 내용", "## 휴가\n휴가 내용"]

    def test_blank_text_gives_no_chunks(self):
        assert split_text(" \n\n \n", size=100, overlap=10) == []
