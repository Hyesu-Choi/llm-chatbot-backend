"""OpenAI 호환 API(기본: 로컬 Ollama)와 통신하는 얇은 클라이언트.

Ollama는 /v1 아래에 OpenAI 호환 API를 제공한다. 스트리밍 응답은 SSE 형식으로,
`data: {...}` 한 줄에 JSON 하나가 오고 마지막에 `data: [DONE]`이 온다.
여기서는 그 스트림에서 choices[0].delta.content만 뽑아 순수 텍스트 조각으로 바꾼다.

실제로 오가는 SSE 예시:
    data: {"choices":[{"delta":{"role":"assistant","content":"안녕"}}]}
    data: {"choices":[{"delta":{"content":"하세요"}}]}
    data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
    data: [DONE]
→ 이 함수가 내보내는 조각: "안녕", "하세요"
"""

import json
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.schemas import ChatMessage

SSE_DATA_PREFIX = "data:"
SSE_DONE = "[DONE]"


# 오류 종류별로 예외 클래스를 나눠 두면, 호출하는 쪽(routers/chat.py)이
# except 로 골라 받아 각각 다른 HTTP 상태 코드와 메시지로 바꿀 수 있다.
class LlmUnavailableError(Exception):
    """LLM 서버에 연결할 수 없을 때 (Ollama가 꺼져 있는 등)."""


class LlmModelMissingError(Exception):
    """요청한 모델이 없을 때 (404)."""


class LlmError(Exception):
    """그 밖의 LLM API 오류."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def stream_chat(messages: list[ChatMessage]) -> AsyncIterator[str]:
    """LLM에 요청을 보내고, 텍스트 조각을 내보내는 비동기 이터레이터를 돌려준다.

    두 단계로 나눈 이유: 이 함수 자체는 연결과 상태 코드 확인까지만 하고 바로 반환한다.
    그래서 연결 실패·모델 없음 같은 오류가 여기서 예외로 터지고, 라우터가 503/502로 응답할 수 있다.
    실제 본문 읽기는 반환된 _text_chunks()가 StreamingResponse 안에서 나중에 한다.
    """
    payload = {
        "model": settings.llm_model,
        "stream": True,  # 답변을 다 만든 뒤가 아니라 토큰이 생길 때마다 보내 달라는 뜻
        "messages": [
            # LLM은 대화를 기억하지 않는다. 매 요청마다 시스템 프롬프트 + 지금까지의 대화 전체를 보낸다.
            {"role": "system", "content": settings.system_prompt},
            *[message.model_dump() for message in messages],  # pydantic 모델 → dict
        ],
    }

    headers = {"Accept": "text/event-stream"}
    # Ollama는 키가 필요 없다. 키가 있는 OpenAI 호환 서비스로 바꿀 때만 쓴다.
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    # `async with` 를 쓰지 않는 이유: 함수가 반환된 뒤에도 스트림을 계속 읽어야 해서
    # 클라이언트를 여기서 닫으면 안 된다. 대신 오류 경로와 _text_chunks()의 finally에서 직접 닫는다.
    client = httpx.AsyncClient(
        base_url=settings.llm_base_url,
        headers=headers,
        # 긴 답변은 수십 초 걸릴 수 있어서 시간 제한을 두지 않는다.
        timeout=httpx.Timeout(None),
    )
    request = client.build_request("POST", "/chat/completions", json=payload)

    try:
        # stream=True: 헤더만 받고 바로 돌아온다. 본문은 아직 안 읽은 상태.
        response = await client.send(request, stream=True)
    except httpx.ConnectError as exc:
        await client.aclose()
        # `from exc`: 원래 예외를 원인으로 연결해 둔다. 로그에 두 예외가 함께 찍혀 디버깅이 쉽다.
        raise LlmUnavailableError from exc

    if response.status_code != 200:
        detail = await _read_error(response)
        await response.aclose()
        await client.aclose()
        if response.status_code == 404:
            raise LlmModelMissingError
        raise LlmError(response.status_code, detail)

    # async def 안에서 비동기 제너레이터를 "호출만" 하면 아직 실행되지 않고 이터레이터 객체만 생긴다.
    # 실제 실행은 StreamingResponse가 `async for`로 꺼내 읽기 시작할 때 일어난다.
    return _text_chunks(response, client)


async def _text_chunks(
    response: httpx.Response, client: httpx.AsyncClient
) -> AsyncIterator[str]:
    """SSE 줄들을 읽어 답변 텍스트 조각만 하나씩 내보내는 비동기 제너레이터.

    함수 안에 `yield`가 있으면 제너레이터가 된다. yield를 만날 때마다 값 하나를 내보내고 멈췄다가,
    다음 값을 요청받으면 그 자리부터 이어서 실행된다.
    """
    try:
        async for line in response.aiter_lines():
            # SSE에는 빈 줄, 주석(`:`로 시작) 같은 줄도 섞여 오므로 data: 줄만 본다.
            if not line.startswith(SSE_DATA_PREFIX):
                continue
            data = line[len(SSE_DATA_PREFIX) :].strip()
            if not data or data == SSE_DONE:
                continue
            chunk = json.loads(data)
            # 스트림 도중 오류: 상태 코드 200은 이미 보냈으므로 본문 텍스트로 알릴 수밖에 없다.
            if error := chunk.get("error"):
                yield f"\n\n(LLM 오류: {_error_message(error)})"
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            # delta: 이전 조각 이후 새로 생긴 부분만 담긴다. 마지막 조각처럼 content가 없을 수도 있다.
            # `:=` (바다코끼리 연산자): 값을 변수에 담으면서 동시에 조건 검사에 쓴다.
            if content := choices[0].get("delta", {}).get("content"):
                yield content
    finally:
        # 정상 종료든, 사용자가 중간에 '중지'를 눌러 연결이 끊기든, 오류가 나든 반드시 정리한다.
        # 안 닫으면 Ollama와의 연결이 남아 계속 답변을 생성할 수 있다.
        await response.aclose()
        await client.aclose()


async def _read_error(response: httpx.Response) -> str:
    """오류 응답 본문에서 사람이 읽을 메시지를 뽑는다. 모양이 제각각이라 최대한 너그럽게 처리."""
    fallback = f"LLM API 오류 ({response.status_code})"
    try:
        body = json.loads(await response.aread())
    except json.JSONDecodeError:
        return fallback
    if not isinstance(body, dict):
        return fallback
    # OpenAI 형식 {"error": {"message": ...}} 과 {"detail": ...} 둘 다 처리
    if error := body.get("error"):
        return _error_message(error)
    return str(body.get("detail") or body.get("title") or fallback)


def _error_message(error: object) -> str:
    # "error" 값이 {"message": "..."} 객체일 때도, 그냥 문자열일 때도 있어서 둘 다 받는다.
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)
