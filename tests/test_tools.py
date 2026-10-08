"""도구 호출: 계산기 보안, 도구 판단 결과 검증, 날씨 조회, 채팅 연결.

외부와 닿는 두 곳은 가짜로 바꾼다:
    - 도구 판단 LLM(complete_chat) → 정해 둔 JSON을 돌려주는 가짜 함수
    - 날씨 API(httpx) → httpx.MockTransport로 가짜 HTTP 응답
그래서 인터넷 · Ollama 없이도 항상 같은 결과로 돈다.
"""

import json
import re
from urllib.parse import unquote

import httpx
import pytest

from app import tools
from app.config import settings
from app.llm import LlmUnavailableError
from app.routers import chat
from app.tools import (
    TOOLS_BY_NAME,
    ToolCall,
    ToolError,
    ToolNotApplicable,
    choose_tool,
    get_current_time,
    run_tool,
    safe_calculate,
)
from tests.helpers import signup

# 이 파일의 async def 테스트를 anyio로 실행한다 (conftest.py의 anyio_backend 참고)
pytestmark = pytest.mark.anyio

# ── 계산기 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1234*5678", 7006652),
        ("3200000 * (1 - 0.15)", 2720000),
        ("3,200,000 × 0.85", 2720000),  # 쉼표 · 곱하기 기호도 받아준다
        ("2^10", 1024),  # ^ 를 거듭제곱으로
        ("-5 + 10 % 3", -4),
    ],
)
def test_calculator_computes(expression, expected):
    assert safe_calculate(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",  # 코드 실행 시도: eval이었다면 서버에서 실행됐을 것
        "open('/etc/passwd').read()",
        "x + 1",  # 변수
        "log(n)",  # 함수 호출
    ],
)
def test_calculator_rejects_non_arithmetic(expression):
    with pytest.raises(ToolNotApplicable):
        safe_calculate(expression)


def test_calculator_rejects_huge_exponent_and_zero_division():
    # 이 둘은 "계산식은 맞는데 실패"라서 ToolNotApplicable이 아니라 일반 ToolError
    with pytest.raises(ToolError, match="지수"):
        safe_calculate("2**99999999")
    with pytest.raises(ToolError, match="0으로"):
        safe_calculate("1/0")


async def test_current_time_format():
    text = await get_current_time({})
    assert re.fullmatch(
        r"\d{4}년 \d+월 \d+일 [월화수목금토일]요일 오[전후] \d+시 \d+분 \(한국 시간\)",
        text,
    )


# ── 도구 판단: LLM 출력은 믿지 않고 검사한다 ───────────


def _fake_router(monkeypatch, output):
    async def fake_complete_chat(messages, model, **kwargs):
        if isinstance(output, Exception):
            raise output
        return output

    monkeypatch.setattr(tools, "complete_chat", fake_complete_chat)


async def test_choose_tool_returns_call(monkeypatch):
    _fake_router(monkeypatch, '{"tool": "calculate", "args": {"expression": "1+1"}}')

    call = await choose_tool("1+1은?")

    assert call.tool.name == "calculate"
    assert call.args == {"expression": "1+1"}


@pytest.mark.parametrize(
    "output",
    [
        '{"tool": null}',  # 도구 필요 없음
        '{"tool": "delete_database", "args": {}}',  # LLM이 지어낸 없는 도구
        "도구는 calculate가 좋겠어요",  # JSON이 아님
        "[1, 2, 3]",  # JSON이지만 모양이 다름
        LlmUnavailableError(),  # Ollama 꺼짐
    ],
)
async def test_choose_tool_returns_none_for_anything_unexpected(monkeypatch, output):
    _fake_router(monkeypatch, output)

    assert await choose_tool("질문") is None


async def test_run_tool_returns_none_when_not_applicable():
    # 라우터가 "시간 복잡도"를 계산기로 잘못 보낸 경우 → 도구 없이 답하도록 None
    call = ToolCall(TOOLS_BY_NAME["calculate"], {"expression": "log(n)"})
    assert await run_tool(call) is None


# ── 날씨 (가짜 HTTP) ───────────────────────────────────


@pytest.fixture
def fake_weather_api(monkeypatch):
    """Open-Meteo 대신 정해 둔 응답을 주는 가짜 서버. 받은 요청 주소를 기록한다."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "geocoding" in request.url.host:
            # 같은 이름의 작은 마을과 진짜 대전을 함께 돌려준다 → 인구가 많은 쪽을 골라야 한다
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "대전(작은 마을)",
                            "latitude": 34.7,
                            "longitude": 127.2,
                            "population": None,
                        },
                        {
                            "name": "대전광역시",
                            "country": "대한민국",
                            "latitude": 36.3,
                            "longitude": 127.3,
                            "population": 1441203,
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-10-08T16:30",
                    "temperature_2m": 22.4,
                    "apparent_temperature": 21.7,
                    "relative_humidity_2m": 44,
                    "precipitation": 0.0,
                    "weather_code": 61,
                    "wind_speed_10m": 3.7,
                }
            },
        )

    real_client = httpx.AsyncClient
    # tools.py가 만드는 AsyncClient에 가짜 전송 계층(MockTransport)을 끼워 넣는다
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    tools._city_cache.clear()
    return requests


async def test_weather_picks_most_populated_city_and_caches(fake_weather_api):
    call = ToolCall(
        TOOLS_BY_NAME["get_weather"], {"city": "Daejeon", "city_original": "대전"}
    )

    result = await run_tool(call)
    await run_tool(call)

    assert result.ok
    assert (
        "대전광역시" in result.output
        and "약한 비" in result.output
        and "22.4°C" in result.output
    )
    hosts = [r.url.host for r in fake_weather_api]
    # 첫 번째: 도시 검색 2번(영어 · 한글) + 날씨 1번 / 두 번째: 캐시 덕분에 날씨 1번만
    assert hosts.count("geocoding-api.open-meteo.com") == 2
    assert hosts.count("api.open-meteo.com") == 2


async def test_weather_api_down_is_reported_not_raised(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    tools._city_cache.clear()

    result = await run_tool(ToolCall(TOOLS_BY_NAME["get_weather"], {"city": "Seoul"}))

    # 채팅 전체가 실패하지 않고, "가져오지 못했다"는 결과로 LLM에 전달된다
    assert result.ok is False


# ── 채팅에 연결 ────────────────────────────────────────


@pytest.fixture
def chat_with_tools(client, monkeypatch):
    """도구를 켜고, LLM 답변은 가짜로. LLM이 받은 마지막 메시지(질문 + 도구 결과)를 기록한다."""
    monkeypatch.setattr(settings, "tools_enabled", True)
    last_messages: list[str] = []

    async def stream_chat(messages, model, system_prompt):
        last_messages.append(messages[-1].content)

        async def chunks():
            yield "답변"

        return chunks()

    monkeypatch.setattr(chat, "stream_chat", stream_chat)
    signup(client)
    return last_messages


def _ask(client, question):
    return client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": question}]}
    )


def test_chat_runs_tool_and_passes_result(client, chat_with_tools, monkeypatch):
    _fake_router(monkeypatch, '{"tool": "calculate", "args": {"expression": "2**20"}}')

    response = _ask(client, "2의 20제곱은?")

    # 도구 결과는 질문 바로 뒤에 붙어서 LLM에 간다
    assert chat_with_tools[0].startswith("2의 20제곱은?")
    assert "2**20 = 1,048,576" in chat_with_tools[0]
    calls = json.loads(unquote(response.headers["X-Tool-Calls"]))
    assert calls == [
        {
            "name": "calculate",
            "label": "계산기",
            "args": {"expression": "2**20"},
            "ok": True,
        }
    ]


def test_chat_without_tool_has_no_header(client, chat_with_tools, monkeypatch):
    _fake_router(monkeypatch, '{"tool": null}')

    response = _ask(client, "파이썬 설명해줘")

    assert "X-Tool-Calls" not in response.headers
    assert chat_with_tools[0] == "파이썬 설명해줘"
