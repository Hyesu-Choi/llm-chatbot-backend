"""채팅 (/api/chat) · 모델 목록 · 역할 목록. LLM은 전부 가짜로 바꿔서 테스트한다."""

import pytest

from app.config import settings
from app.llm import LlmModelMissingError, LlmUnavailableError
from app.personas import PERSONAS_BY_ID
from app.routers import chat, models
from tests.helpers import signup

QUESTION = {"messages": [{"role": "user", "content": "안녕"}]}


@pytest.fixture
def fake_llm(monkeypatch) -> list[dict]:
    """stream_chat을 가짜로 바꾸고, 어떤 인자로 불렸는지 기록한 리스트를 돌려준다."""
    calls: list[dict] = []

    async def fake_stream_chat(messages, model, system_prompt):
        calls.append(
            {"messages": messages, "model": model, "system_prompt": system_prompt}
        )

        async def chunks():
            yield "안녕"
            yield "하세요"

        return chunks()

    monkeypatch.setattr(chat, "stream_chat", fake_stream_chat)
    return calls


def test_chat_requires_login(client, fake_llm):
    assert client.post("/api/chat", json=QUESTION).status_code == 401
    assert fake_llm == []


def test_chat_streams_plain_text_with_defaults(client, fake_llm):
    signup(client)

    response = client.post("/api/chat", json=QUESTION)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "안녕하세요"
    assert fake_llm[0]["model"] == settings.llm_model
    assert fake_llm[0]["system_prompt"] == PERSONAS_BY_ID["default"].prompt


def test_chat_uses_selected_model_and_persona(client, fake_llm):
    signup(client)
    other_model = settings.allowed_models[-1]

    client.post(
        "/api/chat", json={**QUESTION, "model": other_model, "persona": "code_reviewer"}
    )

    assert fake_llm[0]["model"] == other_model
    assert fake_llm[0]["system_prompt"] == PERSONAS_BY_ID["code_reviewer"].prompt


def test_chat_rejects_model_not_in_allowlist(client, fake_llm):
    signup(client)

    response = client.post("/api/chat", json={**QUESTION, "model": "gpt-9-ultra"})

    assert response.status_code == 400
    assert fake_llm == []  # LLM까지 가지도 않아야 한다


def test_chat_rejects_unknown_persona(client, fake_llm):
    signup(client)

    response = client.post("/api/chat", json={**QUESTION, "persona": "hacker"})

    assert response.status_code == 400
    assert fake_llm == []


def test_chat_with_empty_messages_returns_422(client, fake_llm):
    signup(client)

    response = client.post("/api/chat", json={"messages": []})

    assert response.status_code == 422
    assert response.json() == {"error": "메시지를 1개 이상 보내 주세요."}


def test_chat_trims_long_history(client, fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_history_chars", 100)
    signup(client)
    long_history = [
        {"role": "user", "content": "오래된 질문" * 50},
        {"role": "assistant", "content": "오래된 답변" * 50},
        {"role": "user", "content": "지금 질문"},
    ]

    client.post("/api/chat", json={"messages": long_history})

    sent = fake_llm[0]["messages"]
    assert [m.content for m in sent] == ["지금 질문"]


# 예외 클래스를 넣으면 main.py의 처리기가 상태 코드로 바꿔야 한다
@pytest.mark.parametrize(
    ("error", "status"),
    [(LlmUnavailableError(), 503), (LlmModelMissingError("gemma3:4b"), 502)],
)
def test_llm_errors_become_json_errors(client, monkeypatch, error, status):
    async def failing_stream_chat(messages, model, system_prompt):
        raise error

    monkeypatch.setattr(chat, "stream_chat", failing_stream_chat)
    signup(client)

    response = client.post("/api/chat", json=QUESTION)

    assert response.status_code == status
    assert "error" in response.json()


# ── 모델 · 역할 목록 ───────────────────────────────────


def test_models_marks_installed_ones(client, monkeypatch):
    async def fake_installed():
        return {settings.llm_model}

    monkeypatch.setattr(models, "list_installed_models", fake_installed)
    signup(client)

    body = client.get("/api/models").json()

    assert body["default"] == settings.llm_model
    installed = {m["id"]: m["installed"] for m in body["models"]}
    assert installed[settings.llm_model] is True
    assert all(
        not ok for model_id, ok in installed.items() if model_id != settings.llm_model
    )


def test_models_returns_503_when_llm_is_down(client, monkeypatch):
    async def down():
        raise LlmUnavailableError

    monkeypatch.setattr(models, "list_installed_models", down)
    signup(client)

    assert client.get("/api/models").status_code == 503


def test_personas_hide_prompts(client):
    signup(client)

    body = client.get("/api/personas").json()

    assert body["default"] == "default"
    assert {p["id"] for p in body["personas"]} == set(PERSONAS_BY_ID)
    # 시스템 프롬프트 내용은 화면에 내보내지 않는다
    assert all("prompt" not in p for p in body["personas"])
