"""대화 목록 · 메시지 저장 · 제목 요약 (/api/conversations/...)"""

import pytest

from app.llm import LlmUnavailableError
from app.routers import conversations
from tests.helpers import login_as, signup


def _create(client) -> str:
    response = client.post("/api/conversations")
    assert response.status_code == 201
    return response.json()["id"]


def _put_message(client, cid, mid, *, role="user", content="안녕", parent=None):
    return client.put(
        f"/api/conversations/{cid}/messages/{mid}",
        json={"parent_message_id": parent, "role": role, "content": content},
    )


def test_conversation_requires_login(client):
    assert client.get("/api/conversations").status_code == 401


def test_create_and_list_conversations(client):
    signup(client)
    cid = _create(client)

    listed = client.get("/api/conversations").json()

    assert [c["id"] for c in listed] == [cid]
    assert listed[0]["title"] is None
    assert listed[0]["archived"] is False


def test_messages_are_saved_in_order_with_parent(client):
    signup(client)
    cid = _create(client)
    _put_message(client, cid, "m1", content="질문")
    _put_message(client, cid, "m2", role="assistant", content="답변", parent="m1")

    messages = client.get(f"/api/conversations/{cid}/messages").json()

    assert [
        (m["message_id"], m["parent_message_id"], m["content"]) for m in messages
    ] == [
        ("m1", None, "질문"),
        ("m2", "m1", "답변"),
    ]


def test_putting_same_message_id_updates_instead_of_duplicating(client):
    """PUT은 멱등(여러 번 보내도 결과가 같음)이어야 한다 → upsert"""
    signup(client)
    cid = _create(client)
    _put_message(client, cid, "m1", role="assistant", content="쓰는 중")

    _put_message(client, cid, "m1", role="assistant", content="완성된 답변")

    messages = client.get(f"/api/conversations/{cid}/messages").json()
    assert len(messages) == 1
    assert messages[0]["content"] == "완성된 답변"


def test_branches_keep_all_children_of_same_parent(client):
    """'다시 생성'하면 같은 질문(부모) 아래 답변이 두 개가 된다"""
    signup(client)
    cid = _create(client)
    _put_message(client, cid, "q", content="질문")
    _put_message(client, cid, "a1", role="assistant", content="첫 답변", parent="q")
    _put_message(
        client, cid, "a2", role="assistant", content="다시 생성한 답변", parent="q"
    )

    messages = client.get(f"/api/conversations/{cid}/messages").json()

    children_of_q = [m["message_id"] for m in messages if m["parent_message_id"] == "q"]
    assert children_of_q == ["a1", "a2"]


def test_list_is_ordered_by_latest_activity(client):
    signup(client)
    older = _create(client)
    newer = _create(client)
    # 먼저 만든 대화에 메시지가 오가면 맨 위로 올라와야 한다
    _put_message(client, older, "m1")

    listed = [c["id"] for c in client.get("/api/conversations").json()]

    assert listed == [older, newer]


def test_rename_and_archive(client):
    signup(client)
    cid = _create(client)

    client.patch(f"/api/conversations/{cid}", json={"title": "새 제목"})
    response = client.patch(f"/api/conversations/{cid}", json={"archived": True})

    # 보낸 필드만 바뀌고, 앞에서 바꾼 제목은 그대로 남아 있어야 한다
    assert response.json()["title"] == "새 제목"
    assert response.json()["archived"] is True


def test_delete_removes_messages_too(client):
    signup(client)
    cid = _create(client)
    _put_message(client, cid, "m1")

    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}/messages").status_code == 404


def test_invalid_role_is_rejected(client):
    signup(client)
    cid = _create(client)

    assert _put_message(client, cid, "m1", role="system").status_code == 422


def test_invalid_uuid_returns_422_and_unknown_uuid_returns_404(client):
    signup(client)

    assert client.get("/api/conversations/abc").status_code == 422
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/conversations/{unknown}").status_code == 404


# ── 다른 사용자 격리: 가장 중요한 보안 테스트 ──────────────


@pytest.fixture
def others_conversation(client) -> str:
    """사용자 A가 만든 대화 id를 돌려주고, 클라이언트는 사용자 B로 로그인된 상태로 둔다."""
    user_a = signup(client, "a@example.com")
    user_b = signup(client, "b@example.com")
    login_as(client, user_a)
    cid = _create(client)
    _put_message(client, cid, "m1", content="A의 비밀")
    login_as(client, user_b)
    return cid


# parametrize: 같은 테스트를 입력만 바꿔 여러 번 돌린다 (여기선 5가지 요청 방식)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "", None),
        ("GET", "/messages", None),
        ("PATCH", "", {"title": "해킹"}),
        ("DELETE", "", None),
        ("PUT", "/messages/evil", {"role": "user", "content": "x"}),
    ],
)
def test_other_users_conversation_is_hidden(
    client, others_conversation, method, path, body
):
    response = client.request(
        method, f"/api/conversations/{others_conversation}{path}", json=body
    )

    # 403이 아니라 404: "그런 대화가 있다"는 사실조차 알려주지 않는다
    assert response.status_code == 404


def test_other_users_conversation_not_in_my_list(client, others_conversation):
    assert client.get("/api/conversations").json() == []


# ── 제목 요약 (LLM은 가짜로) ────────────────────────────


def test_title_is_generated_by_llm(client, monkeypatch):
    calls = []

    async def fake_complete_chat(messages, model):
        calls.append(messages)
        return '제목: "리스트 튜플 차이".\n부연 설명 줄'

    # monkeypatch.setattr(모듈, "이름", 대체값): 이 테스트 동안만 바꿔 끼우고, 끝나면 자동으로 원래대로 돌려놓는다.
    # routers/conversations.py가 `from app.llm import complete_chat`로 가져왔으므로
    # app.llm이 아니라 "가져다 쓰는 쪽" 모듈의 이름을 바꿔야 효과가 있다.
    monkeypatch.setattr(conversations, "complete_chat", fake_complete_chat)
    signup(client)
    cid = _create(client)

    response = client.post(
        f"/api/conversations/{cid}/title", json={"question": "리스트랑 튜플 차이?"}
    )

    # 작은 모델이 붙이는 머리말 · 따옴표 · 마침표 · 두 번째 줄을 걸러냈는지
    assert response.json()["title"] == "리스트 튜플 차이"
    assert calls[0][-1] == {"role": "user", "content": "리스트랑 튜플 차이?"}


def test_title_falls_back_to_question_when_llm_fails(client, monkeypatch):
    async def broken_complete_chat(messages, model):
        raise LlmUnavailableError

    monkeypatch.setattr(conversations, "complete_chat", broken_complete_chat)
    signup(client)
    cid = _create(client)
    question = "LLM이 꺼져 있을 때는 이 질문의 앞부분이 제목이 되어야 합니다"

    response = client.post(
        f"/api/conversations/{cid}/title", json={"question": question}
    )

    # 오류가 아니라 200 + 질문 앞 30자
    assert response.status_code == 200
    assert response.json()["title"] == question[:30] + "…"
