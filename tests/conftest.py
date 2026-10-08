"""pytest 공통 설정. 이름이 conftest.py면 pytest가 테스트 전에 자동으로 읽는다.

fixture란?
    테스트 함수의 인자 이름으로 "준비물"을 받는 pytest 기능.
    def test_x(client): ... 라고 쓰면 아래 @pytest.fixture def client()의 반환값이 들어온다.
    scope="session"이면 전체 테스트에서 한 번만 만들고, 기본(function)이면 테스트마다 새로 만든다.

테스트 DB 전략:
    1. 개발 DB(chatbot)와 따로 chatbot_test DB를 쓴다. 테스트가 내 데이터를 지우면 안 되니까.
    2. 시작할 때 alembic upgrade head로 테이블을 만든다 → 마이그레이션 파일도 같이 검증된다.
    3. 테스트마다 모든 테이블을 비운다 → 테스트끼리 서로 영향을 주지 않는다(순서와 무관하게 통과).
"""

import asyncio
import os

# ⚠️ app을 import하기 "전에" 환경 변수를 바꿔야 한다.
# app/config.py의 settings는 import되는 순간 한 번 만들어지기 때문 (그 뒤엔 바꿔도 반영 안 됨).
# 실제 환경 변수는 .env보다 우선순위가 높아서 .env 값을 덮어쓴다.
TEST_DB_NAME = "chatbot_test"
ADMIN_DSN = "postgresql://chatbot:chatbot@localhost:5432/postgres"
TEST_DSN = f"postgresql://chatbot:chatbot@localhost:5432/{TEST_DB_NAME}"
os.environ["DATABASE_URL"] = TEST_DSN.replace("postgresql://", "postgresql+asyncpg://")
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
# 테스트는 실제 LLM을 부르지 않지만, 혹시 mock을 빠뜨리면 바로 연결 오류가 나서 알아챌 수 있게 없는 주소로
os.environ["LLM_BASE_URL"] = "http://localhost:1/v1"
# 도구 판단(질문마다 LLM 호출)은 기본으로 끄고, 도구 테스트(test_tools.py)에서만 켠다
os.environ["TOOLS_ENABLED"] = "false"

# 아래 import들이 파일 중간에 있는 이유: 위에서 환경 변수를 먼저 설정한 뒤에 app을 불러와야 해서
import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app


async def _create_test_database() -> None:
    # CREATE DATABASE는 다른 DB(여기선 기본 DB인 postgres)에 접속해서 실행해야 한다
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


async def _truncate_all_tables() -> None:
    conn = await asyncpg.connect(TEST_DSN)
    try:
        # TRUNCATE: DELETE보다 빠르게 테이블을 통째로 비운다.
        # RESTART IDENTITY: 자동 증가 번호(users.id)도 1부터 다시. CASCADE: FK로 연결된 테이블도 함께.
        await conn.execute(
            "TRUNCATE users, conversations, messages RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def test_database() -> None:
    """전체 테스트 시작 전에 한 번: 테스트 DB를 만들고 최신 구조로 마이그레이션한다.

    autouse=True: 테스트가 인자로 요청하지 않아도 자동으로 실행된다.
    """
    asyncio.run(_create_test_database())
    # alembic.ini를 읽어 `uv run alembic upgrade head`와 같은 일을 코드로 한다.
    # migrations/env.py가 settings.database_url(= 위에서 바꾼 테스트 DB)을 쓴다.
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def _session_client():
    # TestClient를 with로 열면 앱의 lifespan(시작 · 종료 코드)도 실행된다.
    # 세션 전체에서 하나만 쓴다: TestClient마다 이벤트 루프가 따로 생기는데,
    # DB 연결 풀(app/db.py의 engine)은 하나라서 여러 루프가 나눠 쓰면 "different loop" 오류가 난다.
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(_session_client: TestClient) -> TestClient:
    """테스트마다: DB를 비우고, 쿠키도 비운 깨끗한 클라이언트."""
    asyncio.run(_truncate_all_tables())
    _session_client.cookies.clear()
    return _session_client


@pytest.fixture
def anyio_backend() -> str:
    """async def 테스트를 돌릴 이벤트 루프 종류.

    pytest는 원래 async 테스트를 실행하지 못한다. FastAPI가 이미 쓰는 anyio 라이브러리에 pytest 플러그인이
    들어 있어서, 테스트 파일에 `pytestmark = pytest.mark.anyio`를 달면 async 테스트도 돌릴 수 있다.
    """
    return "asyncio"
