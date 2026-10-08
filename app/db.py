"""DB 연결 설정. 엔진, 세션, 모델의 부모 클래스(Base)를 한곳에 둔다.

용어 정리:
    엔진(engine)   = DB와의 연결 풀(pool)을 관리하는 객체. 앱 전체에 하나.
    세션(session)  = 요청 하나 동안 쓰는 작업 단위. 조회 · 추가 · 수정을 모았다가 commit 때 한 번에 반영.
    Base           = 모든 테이블 모델이 상속하는 부모 클래스. 여기에 테이블 정보(metadata)가 모인다.
"""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 제약 조건(PK, FK, unique 등)에 붙일 이름 규칙.
# 이름을 정해 두지 않으면 DB가 임의로 붙여서, 나중에 Alembic으로 제약을 지우거나 바꿀 때 이름을 몰라 곤란해진다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# create_async_engine은 실제로 접속하지 않는다. 첫 쿼리 때 연결을 만들고 풀에 보관해 재사용한다.
# pool_pre_ping: 풀에서 꺼낸 연결이 끊겨 있으면(DB 재시작 등) 버리고 새로 연결한다.
engine = create_async_engine(settings.database_url, pool_pre_ping=True)

# 세션을 찍어내는 공장. expire_on_commit=False: commit 뒤에도 객체 속성을 다시 조회 없이 읽을 수 있게 한다.
# (비동기에서는 commit 후 속성에 접근할 때 자동 재조회가 일어나면 오류가 나기 때문)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성: 요청마다 세션을 하나 열고, 응답이 끝나면 닫는다.

    엔드포인트에서 `db: AsyncSession = Depends(get_db)` 로 받아 쓴다.
    yield 앞은 요청 전에, 뒤(async with 종료)는 요청이 끝난 뒤에 실행된다.
    commit은 여기서 자동으로 하지 않는다. 쓰는 쪽이 필요할 때 명시적으로 `await db.commit()`.
    """
    async with SessionLocal() as session:
        yield session
