"""FastAPI 앱의 진입점.

`uvicorn app.main:app` 은 "app/main.py 파일의 app 변수를 서버로 띄워라"는 뜻이다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import engine
from app.llm import LlmError, LlmModelMissingError, LlmUnavailableError
from app.routers import auth, chat, conversations, models, personas


# lifespan: 서버가 켜질 때(yield 앞)와 꺼질 때(yield 뒤) 한 번씩 실행할 코드.
# 꺼질 때 DB 연결 풀을 닫아서, Ctrl+C나 --reload 재시작 때 연결이 DB에 남지 않게 한다.
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


# title은 http://localhost:8000/docs (Swagger UI) 상단에 표시된다.
app = FastAPI(title="Chatbot API", lifespan=lifespan)

# CORS: 브라우저는 다른 출처(포트가 다르면 다른 출처)로 요청할 때 서버 허락을 확인한다.
# 개발 중엔 Vite 프록시 덕분에 같은 출처처럼 보여서 이 설정이 필요 없지만,
# 프론트가 백엔드 주소로 직접 요청할 때(배포 등)를 위해 허용 목록을 둔다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# routers/chat.py의 "/chat" 경로에 "/api"를 붙여서 최종 주소는 POST /api/chat 이 된다.
app.include_router(chat.router, prefix="/api")
# routers/auth.py가 prefix="/auth"를 갖고 있어서 최종 주소는 /api/auth/...
app.include_router(auth.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(personas.router, prefix="/api")


# HTTPException을 던지면 FastAPI 기본은 {"detail": "..."} 로 응답한다.
# 채팅 오류(routers/chat.py)와 같은 {"error": "..."} 모양으로 맞춰서, 프론트가 한 가지 방식으로만 읽게 한다.
# Starlette 쪽 클래스로 등록해야 FastAPI가 내부에서 던지는 404(없는 주소) 등도 같이 잡힌다.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers=exc.headers,
    )


# 요청 본문이 schemas.py 규칙에 안 맞을 때(422) 보여줄 메시지. 키는 잘못된 필드 이름.
# 프론트도 같은 규칙으로 먼저 검사하지만, 직접 API를 부르는 경우를 위해 서버도 사람이 읽을 메시지를 준다.
VALIDATION_MESSAGES = {
    "email": "이메일 형식이 올바르지 않습니다.",
    "password": "비밀번호는 8자 이상 128자 이하로 입력해 주세요.",
    "messages": "메시지를 1개 이상 보내 주세요.",
}


# 422도 기본 모양({"detail": [영어 오류 목록]}) 대신 {"error": "한국어 메시지"}로 맞춘다.
# exc.errors()[0]["loc"]는 ("body", "password") 처럼 어디가 틀렸는지 알려준다.
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    field = next(
        (loc for loc in exc.errors()[0]["loc"] if loc in VALIDATION_MESSAGES), None
    )
    message = VALIDATION_MESSAGES.get(field, "요청 형식이 올바르지 않습니다.")
    return JSONResponse(status_code=422, content={"error": message})


# 서버가 살아 있는지만 확인하는 엔드포인트. LLM은 호출하지 않는다.
# 반환한 dict는 FastAPI가 자동으로 JSON으로 바꿔 보낸다.
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── LLM 오류 → HTTP 응답 ───────────────────────────────
# 채팅 · 모델 목록 등 LLM을 부르는 곳은 예외를 그냥 올려보내고, 응답 모양은 여기 한곳에서 정한다.
# 같은 오류가 어느 엔드포인트에서 나든 같은 상태 코드 · 메시지가 나간다.


@app.exception_handler(LlmUnavailableError)
async def llm_unavailable_handler(
    request: Request, exc: LlmUnavailableError
) -> JSONResponse:
    # 503 Service Unavailable: 우리 서버는 멀쩡한데 의존하는 서비스가 꺼져 있음
    return JSONResponse(
        status_code=503,
        content={
            "error": f"LLM 서버({settings.llm_base_url})에 연결할 수 없습니다. "
            "Ollama가 실행 중인지 확인하세요 (brew services run ollama)."
        },
    )


@app.exception_handler(LlmModelMissingError)
async def llm_model_missing_handler(
    request: Request, exc: LlmModelMissingError
) -> JSONResponse:
    # 502 Bad Gateway: 뒤쪽 서버(Ollama)가 오류 응답을 줌
    return JSONResponse(
        status_code=502,
        content={
            "error": f'모델 "{exc.model}"을 찾을 수 없습니다. '
            f"ollama pull {exc.model} 으로 받아주세요."
        },
    )


@app.exception_handler(LlmError)
async def llm_error_handler(request: Request, exc: LlmError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": exc.detail})
