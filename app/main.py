"""FastAPI 앱의 진입점.

`uvicorn app.main:app` 은 "app/main.py 파일의 app 변수를 서버로 띄워라"는 뜻이다.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import chat

# title은 http://localhost:8000/docs (Swagger UI) 상단에 표시된다.
app = FastAPI(title="Chatbot API")

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


# 서버가 살아 있는지만 확인하는 엔드포인트. LLM은 호출하지 않는다.
# 반환한 dict는 FastAPI가 자동으로 JSON으로 바꿔 보낸다.
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
