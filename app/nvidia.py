"""NVIDIA Build(NIM) API와 통신하는 얇은 클라이언트.

NVIDIA Build는 OpenAI 호환 API를 제공한다. 스트리밍 응답은 SSE 형식으로,
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


class NvidiaApiKeyMissingError(Exception):
    """NVIDIA_API_KEY가 설정되지 않았을 때."""


class NvidiaUnavailableError(Exception):
    """NVIDIA API 서버에 연결할 수 없을 때."""


class NvidiaAuthError(Exception):
    """API 키가 잘못됐을 때 (401/403)."""


class NvidiaRateLimitError(Exception):
    """무료 티어 요청 한도를 넘었을 때 (429)."""


class NvidiaModelMissingError(Exception):
    """요청한 모델이 없을 때 (404)."""


class NvidiaError(Exception):
    """그 밖의 NVIDIA API 오류."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def stream_chat(messages: list[ChatMessage]) -> AsyncIterator[str]:
    if not settings.nvidia_api_key:
        raise NvidiaApiKeyMissingError

    payload = {
        "model": settings.nvidia_model,
        "stream": True,
        "messages": [
            {"role": "system", "content": settings.system_prompt},
            *[message.model_dump() for message in messages],
        ],
    }

    client = httpx.AsyncClient(
        base_url=settings.nvidia_base_url,
        headers={
            "Authorization": f"Bearer {settings.nvidia_api_key}",
            "Accept": "text/event-stream",
        },
        timeout=httpx.Timeout(None),
    )
    request = client.build_request("POST", "/chat/completions", json=payload)

    try:
        response = await client.send(request, stream=True)
    except httpx.ConnectError as exc:
        await client.aclose()
        raise NvidiaUnavailableError from exc

    if response.status_code != 200:
        detail = await _read_error(response)
        await response.aclose()
        await client.aclose()
        _raise_for_status(response.status_code, detail)

    return _text_chunks(response, client)


def _raise_for_status(status: int, detail: str) -> None:
    if status in (401, 403):
        raise NvidiaAuthError
    if status == 404:
        raise NvidiaModelMissingError
    if status == 429:
        raise NvidiaRateLimitError
    raise NvidiaError(status, detail)


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
                yield f"\n\n(NVIDIA 오류: {_error_message(error)})"
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
    fallback = f"NVIDIA API 오류 ({response.status_code})"
    try:
        body = json.loads(await response.aread())
    except json.JSONDecodeError:
        return fallback
    if not isinstance(body, dict):
        return fallback
    # OpenAI 형식 {"error": {"message": ...}} 과 NVIDIA 형식 {"detail": ...} 둘 다 처리
    if error := body.get("error"):
        return _error_message(error)
    return str(body.get("detail") or body.get("title") or fallback)


def _error_message(error: object) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)
