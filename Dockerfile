# 백엔드(FastAPI)를 컨테이너 이미지로 만드는 설명서.
#   빌드: docker build -t llm-chatbot-backend .
#   EC2(ARM, t4g)용: docker build --platform linux/arm64 -t llm-chatbot-backend .
#
# 이미지 하나로 두 가지 일을 한다 (deploy/docker-compose.yml 참고):
#   - 기본(CMD): API 서버 실행
#   - command를 바꿔서: alembic upgrade head (DB 구조 갱신)

# 파이썬이 설치된 가벼운 Debian. alpine은 asyncpg 같은 C 확장 패키지를 직접 컴파일해야 해서 빌드가 느리다.
# 로컬(.python-version)과 같은 3.12로 맞춘다.
# FROM: 이 이미지 위에 내 파일을 쌓아 올린다 (밑바탕)
FROM python:3.12-slim

# COPY --from=다른이미지: 다른 이미지 안의 파일만 꺼내 온다. uv를 설치(curl | sh)하는 것보다 빠르고 깔끔하다.
# uv 실행 파일만 공식 이미지에서 복사해 온다. 버전을 고정해야 언제 빌드해도 결과가 같다.
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

# UV_COMPILE_BYTECODE: 설치할 때 .pyc를 미리 만들어서 컨테이너 첫 시작이 빨라진다
# UV_LINK_MODE=copy: 캐시와 다른 위치라 하드링크 대신 복사 (경고 방지)
# UV_PYTHON_DOWNLOADS=never: 파이썬을 따로 받지 말고 이미지에 있는 3.12를 쓴다
# PYTHONUNBUFFERED: print/로그를 버퍼에 모으지 않고 바로 출력 → docker logs에 즉시 보인다
# PATH: .venv/bin을 앞에 둬서 `uvicorn`, `alembic`을 `uv run` 없이 바로 실행
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# WORKDIR: 이후 명령이 실행될 폴더 (없으면 만든다). 앱은 /app 안에 놓인다
WORKDIR /app

# 의존성 목록만 먼저 복사해서 설치한다 (레이어 캐시).
# Docker는 각 줄(레이어) 결과를 저장해 두고, 복사한 파일이 그대로면 그 줄을 다시 실행하지 않는다.
# 코드(app/)만 고쳤을 땐 pyproject.toml · uv.lock이 그대로라 오래 걸리는 설치를 건너뛴다.
COPY pyproject.toml uv.lock .python-version ./
# --locked: uv.lock과 pyproject.toml이 안 맞으면 실패 (로컬에서 테스트한 버전 그대로 설치 보장)
# --no-dev: pytest · ruff 같은 개발용 패키지는 운영 이미지에 넣지 않는다
# --mount=type=cache: 다운로드 캐시를 빌드 사이에 재사용하되 이미지에는 남기지 않는다
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# root로 실행하지 않는다. 앱이 뚫려도 컨테이너 안에서 할 수 있는 일을 줄이기 위해
RUN useradd --create-home --uid 1000 app
USER app

# EXPOSE: "이 컨테이너는 8000번을 쓴다"는 표시일 뿐, 실제로 포트를 열지는 않는다 (여는 건 compose의 ports)
EXPOSE 8000

# --host 0.0.0.0: 컨테이너 밖(nginx 컨테이너)에서 접속할 수 있게 모든 주소에서 받는다
#   (127.0.0.1로 두면 컨테이너 자기 자신만 접속 가능)
# --proxy-headers: nginx가 붙여 준 X-Forwarded-For를 믿고 실제 사용자 IP를 로그에 남긴다.
#   nginx 컨테이너만 이 서버에 접근할 수 있는 구조라 --forwarded-allow-ips를 전부(*)로 둬도 된다.
# --reload는 개발용이라 넣지 않는다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
