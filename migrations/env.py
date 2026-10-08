"""Alembic이 마이그레이션을 실행할 때마다 읽는 설정 스크립트.

`uv run alembic upgrade head` 를 치면 대략 이런 순서로 돈다:
    1. alembic.ini를 읽고 이 파일(env.py)을 실행
    2. DB에 접속해서 alembic_version 테이블로 "지금 DB가 몇 번 버전인지" 확인
    3. migrations/versions/ 에서 그다음 버전 파일들의 upgrade()를 차례로 실행
    4. alembic_version을 최신 번호로 갱신

`alembic revision --autogenerate` 일 때는 3번 대신, target_metadata(코드의 모델)와
실제 DB 구조를 비교해서 차이를 새 버전 파일로 써 준다.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# models를 import해야 User 같은 테이블이 Base.metadata에 등록된다 (안 쓰는 것처럼 보여도 필요)
from app import models  # noqa: F401
from app.config import settings
from app.db import Base

# alembic.ini 내용에 접근하는 객체
config = context.config

# alembic.ini의 [loggers] 설정대로 로그를 찍는다 ("Running upgrade ..." 같은 출력)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 접속 주소는 alembic.ini가 아니라 앱 설정(.env의 DATABASE_URL)에서 가져온다
config.set_main_option("sqlalchemy.url", settings.database_url)

# autogenerate가 "코드에 정의된 테이블"과 "실제 DB"를 비교할 기준
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """오프라인 모드: DB에 접속하지 않고 실행할 SQL만 화면에 출력한다.

    `uv run alembic upgrade head --sql` 처럼 --sql을 붙이면 이쪽으로 온다.
    운영 DB에 바로 적용하기 전에 "어떤 SQL이 실행될지" 눈으로 검토할 때 쓴다.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        # 값을 ? 자리표시자 대신 SQL 안에 직접 써 넣어서 그대로 복사해 실행할 수 있게
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    # 버전 파일 하나하나를 트랜잭션 안에서 실행한다.
    # Postgres는 CREATE TABLE 같은 구조 변경도 트랜잭션으로 묶을 수 있어서(transactional DDL),
    # 중간에 실패하면 그 마이그레이션 전체가 되돌려지고 DB가 반쯤 바뀐 상태로 남지 않는다.
    # (MySQL은 구조 변경이 즉시 확정돼서 이게 안 된다)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """온라인 모드(기본): 실제 DB에 접속해서 마이그레이션을 적용한다."""

    # 앱(app/db.py)과 같은 asyncpg 드라이버를 쓰므로 비동기 엔진을 만든다.
    # NullPool: 연결을 재사용하지 않고 쓰고 바로 닫는다. 한 번 실행하고 끝나는 명령이라 풀이 필요 없다.
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Alembic 내부는 동기 코드라서, 비동기 연결 위에서 동기 함수를 돌려주는 run_sync로 감싼다
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
