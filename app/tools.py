"""도구 호출(tool calling): LLM이 혼자 알 수 없는 정보를 서버 함수로 가져와 답에 쓰게 한다.

LLM은 학습이 끝난 시점의 지식만 있고, "지금" 날씨나 시각을 모르며, 큰 수 계산도 자주 틀린다.
그래서 질문마다
    1. 판단 (choose_tool): LLM에게 "도구가 필요한지, 필요하면 어떤 도구를 어떤 인자로"를 JSON으로 묻고
    2. 실행 (run_tool)   : 서버가 그 함수를 실제로 실행한 뒤
    3. 답변 (build_tool_context): 결과를 시스템 프롬프트에 붙여서 LLM이 그걸 근거로 답하게 한다.

"프롬프트 기반" 방식을 쓴 이유:
    OpenAI 형식의 정식 도구 호출(요청에 tools=[...]를 넣는 것)도 속을 보면, 모델이 정해진 형식의 텍스트(JSON)를
    출력하도록 학습된 것이다. 지금 쓰는 gemma3는 그 정식 기능이 없어서, 같은 원리를 프롬프트로 직접 구현했다.
    정식 기능이 있는 qwen3:4b도 비교했는데 판단 정확도 11/11이었지만 질문마다 12초 넘게 걸렸고(숨은 생각 모드),
    생각 모드를 끄면 영어 생각이 답변 본문에 새어 나왔다. gemma3 + 예시를 넣은 프롬프트는 17개 중 16개를 1초 안에 맞혔다.
"""

import ast
import asyncio
import json
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.llm import LlmError, LlmModelMissingError, LlmUnavailableError, complete_chat

KOREA = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"


class ToolError(Exception):
    """도구가 일을 못 했을 때 (도시를 못 찾음, 0으로 나누기 등). 메시지는 LLM에게 그대로 전달된다."""


class ToolNotApplicable(ToolError):
    """애초에 이 도구를 쓸 질문이 아니었을 때. 도구 판단(LLM)이 틀린 경우다.

    예: "시간 복잡도가 뭐야?" → 라우터가 계산기에 "log(n)"을 넘김 → 숫자 식이 아니라 계산 불가.
    이걸 일반 실패로 처리하면 LLM이 "정보를 가져오지 못했어요"라고 답해 버린다. 설명을 원한 질문인데.
    그래서 이 경우는 도구를 안 쓴 것으로 치고 평소처럼 답하게 한다. (LLM 판단을 서버가 한 번 더 검증하는 셈)
    """


# ── 도구 1: 현재 시각 ──────────────────────────────────


async def get_current_time(args: dict[str, Any]) -> str:
    now = datetime.now(KOREA)
    hour = now.hour % 12 or 12
    ampm = "오전" if now.hour < 12 else "오후"
    return (
        f"{now.year}년 {now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]}요일 "
        f"{ampm} {hour}시 {now.minute}분 (한국 시간)"
    )


# ── 도구 2: 계산기 ─────────────────────────────────────

# 허용하는 연산만 골라 둔 표. 여기 없는 연산(함수 호출, 변수, 속성 접근 등)은 전부 거부한다.
BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
MAX_EXPRESSION_LENGTH = 200
# 2 ** 100000000 같은 식은 결과가 너무 커서 서버가 멈출 수 있다 → 지수 크기를 제한한다
MAX_EXPONENT = 1000


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPERATORS:
        return UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in BINARY_OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ToolError("지수가 너무 큽니다.")
        return BINARY_OPERATORS[type(node.op)](left, right)
    raise ToolNotApplicable("숫자와 사칙연산(+ - * / ** %)만 계산할 수 있습니다.")


def safe_calculate(expression: str) -> float:
    """수식 문자열을 안전하게 계산한다.

    ⚠️ eval(expression)은 절대 쓰면 안 된다. eval은 파이썬 코드를 그대로 실행해서,
    LLM이나 사용자가 "__import__('os').system('rm -rf /')" 같은 걸 넣으면 서버에서 그대로 실행된다.
    대신 ast.parse로 식을 나무 구조(AST)로만 바꾸고, 숫자와 허용한 연산자만 직접 계산한다.
    """
    cleaned = (
        expression.replace(",", "")
        .replace("×", "*")
        .replace("÷", "/")
        .replace("^", "**")
    )
    if len(cleaned) > MAX_EXPRESSION_LENGTH:
        raise ToolError("식이 너무 깁니다.")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        raise ToolNotApplicable("계산할 수 없는 식입니다.") from None
    try:
        return _evaluate(tree.body)
    except ZeroDivisionError:
        raise ToolError("0으로 나눌 수 없습니다.") from None


async def calculate(args: dict[str, Any]) -> str:
    expression = str(args.get("expression", ""))
    result = safe_calculate(expression)
    # 1.0 → 1, 0.1+0.2 → 0.3 처럼 보기 좋게 (부동소수점 오차를 10자리에서 반올림)
    shown = int(result) if float(result).is_integer() else round(result, 10)
    return f"{expression} = {shown:,}"


# ── 도구 3: 날씨 (Open-Meteo, 무료 · 키 없음) ──────────

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# WMO 날씨 코드 → 한국어 (https://open-meteo.com/en/docs 의 WMO Weather interpretation codes)
WEATHER_CODES = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 조금",
    3: "흐림",
    45: "안개",
    48: "서리 안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    66: "약한 어는 비",
    67: "어는 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    77: "싸락눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    85: "약한 눈 소나기",
    86: "눈 소나기",
    95: "뇌우",
    96: "우박을 동반한 뇌우",
    99: "강한 우박을 동반한 뇌우",
}


# 한 번 찾은 도시 좌표를 기억해 두는 캐시. 도시 위치는 바뀌지 않으니 다시 물어볼 필요가 없다.
# 해외 서버 왕복이 약 1초라, 두 번째 "서울 날씨"부터는 그만큼 빨라진다.
# (서버 메모리에만 있어서 재시작하면 비워진다. 서버가 여러 대면 Redis 같은 공용 캐시를 쓴다)
_city_cache: dict[tuple[str, ...], dict[str, Any]] = {}


async def _find_city(client: httpx.AsyncClient, names: list[str]) -> dict[str, Any]:
    """도시 이름으로 좌표를 찾는다.

    이름이 같은 작은 마을이 많아서("대전"으로 찾으면 전라남도의 작은 마을이 먼저 나옴),
    결과를 여러 개 받아 인구가 가장 많은 곳을 고른다. 한글 이름은 서울 · 제주를 아예 못 찾아서
    영어 이름과 원래 이름으로 둘 다 찾아 본다.
    """
    key = tuple(name.lower() for name in names)
    if key in _city_cache:
        return _city_cache[key]

    # 두 이름 검색을 차례로가 아니라 동시에 보낸다 (asyncio.gather). 기다리는 시간이 거의 절반이 된다.
    responses = await asyncio.gather(
        *(
            client.get(
                GEOCODING_URL, params={"name": name, "count": 10, "language": "ko"}
            )
            for name in dict.fromkeys(names)
        )
    )
    candidates: list[dict[str, Any]] = []
    for response in responses:
        response.raise_for_status()
        candidates += response.json().get("results", [])
    if not candidates:
        raise ToolError(f'"{names[0]}"이라는 도시를 찾을 수 없습니다.')
    city = max(candidates, key=lambda candidate: candidate.get("population") or 0)
    _city_cache[key] = city
    return city


async def get_weather(args: dict[str, Any]) -> str:
    names = [str(args.get(key, "")).strip() for key in ("city", "city_original")]
    names = [name for name in names if name]
    if not names:
        raise ToolError("도시 이름이 없습니다.")

    async with httpx.AsyncClient(timeout=10) as client:
        city = await _find_city(client, names)
        response = await client.get(
            FORECAST_URL,
            params={
                "latitude": city["latitude"],
                "longitude": city["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m",
                "timezone": "Asia/Seoul",
            },
        )
        response.raise_for_status()
    now = response.json()["current"]
    sky = WEATHER_CODES.get(now["weather_code"], "알 수 없음")
    return (
        f"{city['name']}({city.get('country', '')}) {now['time'][-5:]} 기준 현재 {sky}, "
        f"기온 {now['temperature_2m']}°C (체감 {now['apparent_temperature']}°C), "
        f"습도 {now['relative_humidity_2m']}%, 강수량 {now['precipitation']}mm, "
        f"바람 {now['wind_speed_10m']}km/h"
    )


# ── 도구 목록 ─────────────────────────────────────────


@dataclass(frozen=True)
class Tool:
    name: str
    # 화면에 "🔧 날씨 조회"처럼 보여줄 한국어 이름
    label: str
    # LLM이 도구를 고를 때 읽는 설명. 언제 써야 하는지가 분명할수록 잘 고른다.
    description: str
    # 인자 이름 → 설명
    parameters: dict[str, str]
    run: Callable[[dict[str, Any]], Awaitable[str]]


TOOLS: list[Tool] = [
    Tool(
        name="get_weather",
        label="날씨 조회",
        description="도시의 현재 날씨(기온, 하늘 상태, 비/눈 여부)를 조회한다",
        parameters={
            "city": "도시의 영어 이름 (예: Seoul, Busan, Jeju, Tokyo)",
            "city_original": "질문에 적힌 도시 이름 그대로 (예: 서울)",
        },
        run=get_weather,
    ),
    Tool(
        name="get_current_time",
        label="현재 시각",
        description="지금 날짜, 요일, 시각(한국 시간)을 알려준다",
        parameters={},
        run=get_current_time,
    ),
    Tool(
        name="calculate",
        label="계산기",
        description="사칙연산 수식을 정확히 계산한다",
        parameters={"expression": "질문이 묻는 최종 값을 구하는 수식 (예: 1234*5678)"},
        run=calculate,
    ),
]
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


# ── 1. 판단: 도구가 필요한가? ──────────────────────────


def _router_prompt() -> str:
    tool_lines = "\n".join(
        f"- {tool.name}: {tool.description}. 인자: {json.dumps(tool.parameters, ensure_ascii=False)}"
        for tool in TOOLS
    )
    # 예시(few-shot)가 핵심이다. 예시 없이는 "시간 관리 잘하는 법"에 시계 도구를,
    # "날씨 좋은 날 할 일"에 날씨 도구를 고르는 등 단어에 낚여서 11개 중 3개를 틀렸다.
    return (
        "당신은 사용자의 마지막 질문에 답하려면 외부 도구가 필요한지 판단하는 라우터입니다.\n"
        f"사용할 수 있는 도구:\n{tool_lines}\n\n"
        "규칙: 질문에 답하는 데 '지금 이 순간의 실제 값'(현재 날씨, 현재 시각 · 날짜 · 요일)이나 "
        "'정확한 숫자 계산 결과'가 꼭 필요할 때만 도구를 고르세요. "
        "'날씨', '시간', '계산' 같은 단어가 들어 있어도 일반적인 조언 · 설명 · 추천이면 도구가 필요 없습니다. "
        "계산식은 질문이 묻는 최종 값을 구하는 식으로 쓰세요.\n"
        "예시:\n"
        '질문: 서울 날씨 어때? → {"tool": "get_weather", "args": {"city": "Seoul", "city_original": "서울"}}\n'
        '질문: 비 오는 날 할 만한 거 추천해줘 → {"tool": null}\n'
        '질문: 지금 몇 시야? → {"tool": "get_current_time", "args": {}}\n'
        '질문: 시간 약속 잘 지키는 법 → {"tool": null}\n'
        '질문: 5만원에서 20% 할인하면 얼마야? → {"tool": "calculate", "args": {"expression": "50000 * (1 - 0.2)"}}\n'
        '질문: 리액트 훅 설명해줘 → {"tool": null}\n'
        "반드시 JSON 하나만 출력하세요."
    )


@dataclass(frozen=True)
class ToolCall:
    tool: Tool
    args: dict[str, Any]


async def choose_tool(question: str) -> ToolCall | None:
    """LLM에게 도구가 필요한지 묻는다. 필요 없거나, 판단에 실패하면 None (평소처럼 답하면 된다)."""
    try:
        raw = await complete_chat(
            [
                {"role": "system", "content": _router_prompt()},
                {"role": "user", "content": question},
            ],
            settings.llm_model,
            # json_mode: 모델이 반드시 올바른 JSON만 내보내게 강제한다 (설명 문장이 섞이지 않게)
            json_mode=True,
            # 판단은 창의성이 필요 없고 매번 같은 답이 나와야 하므로 무작위성을 끈다
            temperature=0,
        )
        decision = json.loads(raw)
    except (
        LlmUnavailableError,
        LlmModelMissingError,
        LlmError,
        httpx.TimeoutException,
    ):
        return None
    except json.JSONDecodeError:
        return None

    # LLM 출력은 믿을 수 없는 입력이다. 형식과 도구 이름을 꼭 검사한다 (없는 도구 이름을 지어낼 수도 있다)
    if not isinstance(decision, dict):
        return None
    tool = TOOLS_BY_NAME.get(decision.get("tool") or "")
    args = decision.get("args")
    if tool is None:
        return None
    return ToolCall(tool=tool, args=args if isinstance(args, dict) else {})


# ── 2. 실행 ───────────────────────────────────────────


@dataclass(frozen=True)
class ToolResult:
    call: ToolCall
    ok: bool
    output: str


async def run_tool(call: ToolCall) -> ToolResult | None:
    """도구를 실행한다. 쓸 상황이 아니었던 것으로 드러나면 None (도구 없이 답하면 된다)."""
    try:
        return ToolResult(call=call, ok=True, output=await call.tool.run(call.args))
    except ToolNotApplicable:
        return None
    except ToolError as exc:
        return ToolResult(call=call, ok=False, output=str(exc))
    except httpx.HTTPError:
        # 날씨 API가 느리거나 죽어 있어도 채팅 전체가 실패하면 안 된다
        return ToolResult(
            call=call, ok=False, output="외부 서비스에 연결하지 못했습니다."
        )


# ── 3. 답변에 쓰기 ─────────────────────────────────────


def build_tool_context(result: ToolResult) -> str:
    args = json.dumps(result.call.args, ensure_ascii=False)
    if result.ok:
        return (
            f"\n\n[도구 실행 결과] {result.call.tool.name}({args})\n{result.output}\n"
            "이 결과는 방금 실제로 조회한 값입니다. 이 값을 그대로 사용해 자연스럽게 답하세요."
        )
    return (
        f"\n\n[도구 실행 실패] {result.call.tool.name}({args}): {result.output}\n"
        "사용자에게 정보를 가져오지 못했다고 짧게 알리고, 추측한 값을 사실처럼 말하지 마세요."
    )
