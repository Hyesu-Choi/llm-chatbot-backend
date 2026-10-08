"""OpenAI 호환 API(기본: 로컬 Ollama)와 통신하는 얇은 클라이언트.

Ollama는 /v1 아래에 OpenAI 호환 API를 제공한다. 스트리밍 응답은 SSE 형식으로,
`data: {...}` 한 줄에 JSON 하나가 오고 마지막에 `data: [DONE]`이 온다.
여기서는 그 스트림에서 choices[0].delta.content만 뽑아 순수 텍스트 조각으로 바꾼다.
"""

import json
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.schemas import ChatMessage

SSE_DATA_PREFIX = "data:"
SSE_DONE = "[DONE]"


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
    payload = {
        "model": settings.llm_model,
        "stream": True,
        "messages": [
            {"role": "system", "content": settings.system_prompt},
            *[message.model_dump() for message in messages],
        ],
    }

    headers = {"Accept": "text/event-stream"}
    # Ollama는 키가 필요 없다. 키가 있는 OpenAI 호환 서비스로 바꿀 때만 쓴다.
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    client = httpx.AsyncClient(
        base_url=settings.llm_base_url,
        headers=headers,
        timeout=httpx.Timeout(None),
    )
    request = client.build_request("POST", "/chat/completions", json=payload)

    try:
        response = await client.send(request, stream=True)
    except httpx.ConnectError as exc:
        await client.aclose()
        raise LlmUnavailableError from exc

    if response.status_code != 200:
        detail = await _read_error(response)
        await response.aclose()
        await client.aclose()
        if response.status_code == 404:
            raise LlmModelMissingError
        raise LlmError(response.status_code, detail)

    return _text_chunks(response, client)


async def _text_chunks(
    response: httpx.Response, client: httpx.AsyncClient
) -> AsyncIterator[str]:
    try:
        async for line in response.aiter_lines():
            if not line.startswith(SSE_DATA_PREFIX):
                continue
            data = line[len(SSE_DATA_PREFIX) :].strip()
            if not data or data == SSE_DONE:
                continue
            chunk = json.loads(data)
            if error := chunk.get("error"):
                yield f"\n\n(LLM 오류: {_error_message(error)})"
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            if content := choices[0].get("delta", {}).get("content"):
                yield content
    finally:
        await response.aclose()
        await client.aclose()


async def _read_error(response: httpx.Response) -> str:
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
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)
