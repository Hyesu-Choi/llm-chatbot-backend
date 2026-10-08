# llm-chatbot-backend

로컬 Ollama로 돌리는 스트리밍 챗봇 API (FastAPI).
프론트는 [`llm-chatbot-frontend`](../llm-chatbot-frontend).

```
브라우저 ──▶ 프론트(Vite :5173) ──/api 프록시──▶ 백엔드(FastAPI :8000) ──▶ Ollama(:11434)
```

## 1. 사전 준비

| 도구 | 버전 | 설치 |
| --- | --- | --- |
| Python | 3.12 (`.python-version`) | uv가 자동으로 받아줌 |
| uv | 0.12+ | `brew install uv` |
| Ollama | 0.40+ | `brew install ollama` |
| Docker Desktop | - | https://www.docker.com/products/docker-desktop (PostgreSQL 실행용) |

```bash
ollama pull gemma3:4b   # 기본 모델, 약 3.3GB (최초 1회)
```

대화 내용은 내 컴퓨터 밖으로 나가지 않고, 키나 요청 한도도 없습니다.

### Ollama 켜고 끄기

```bash
# 켜기 (둘 중 하나)
ollama serve                    # 이 터미널에서 실행. 끌 때는 Ctrl+C
brew services run ollama        # 백그라운드 실행. 로그인할 때 자동으로 켜지지는 않음

# 끄기
brew services stop ollama       # brew services run 으로 켰을 때
pkill -x ollama                 # 그 밖의 방법으로 켰을 때 (백그라운드로 띄운 ollama serve 등)

# 상태 확인
ollama ps                       # 메모리에 올라가 있는 모델 목록
curl localhost:11434            # "Ollama is running" 이 나오면 켜져 있음
```

- 서버는 켜 두고 **모델만 메모리에서 내리려면** `ollama stop gemma3:4b`.
- 모델은 마지막 요청 후 5분 동안 안 쓰면 자동으로 메모리에서 내려갑니다. 그래서 켜 두기만 하면 메모리를 거의 안 씁니다.
- Ollama를 끈 채로 챗봇에 질문하면 백엔드가 503 `연결할 수 없습니다`를 돌려줍니다.
- `brew services start ollama`는 로그인할 때마다 자동으로 켜지게 등록하는 명령이라, 안 쓸 때 꺼 두려면 `run`을 쓰세요.

### DB(PostgreSQL) 켜고 끄기

로컬 DB는 Docker로 띄웁니다 (`docker-compose.yml`). Docker Desktop이 실행 중이어야 합니다.

```bash
docker compose up -d            # 켜기 (처음이면 이미지 받고 DB 생성)
docker compose stop             # 끄기 (데이터는 그대로 남음)
docker compose ps               # 상태 확인. STATUS에 (healthy)면 접속 가능
docker compose exec db psql -U chatbot -d chatbot   # DB에 직접 접속해서 SQL 실행 (\q로 나가기)

docker compose down -v          # 컨테이너와 데이터까지 전부 삭제 (처음부터 다시 만들 때만)
```

- 로컬 전용 계정: 사용자 · 비밀번호 · DB 이름 모두 `chatbot`, 주소 `localhost:5432`
- DB 툴(DBeaver, TablePlus, DataGrip 등)로도 위 정보로 접속할 수 있습니다.
- 맥에 직접 설치한 Postgres가 이미 5432를 쓰고 있으면 충돌합니다. `lsof -iTCP:5432 -sTCP:LISTEN`으로 확인하세요.

### DB 테이블 만들기 · 바꾸기 (Alembic)

테이블 구조는 `app/models.py`에 정의하고, Alembic 마이그레이션으로 DB에 반영합니다.
마이그레이션 파일은 `migrations/versions/`에 쌓이며, DB 구조의 변경 이력이라 git에 같이 올립니다.

```bash
uv run alembic upgrade head     # 최신 구조로 반영. 처음 받았을 때 · pull 받은 뒤 실행

# models.py를 고친 뒤
uv run alembic revision --autogenerate -m "add users nickname"   # 바뀐 부분을 파일로 생성
#   → 생성된 파일을 꼭 열어서 의도대로인지 확인 (자동 생성이 놓치는 경우가 있음)
uv run alembic upgrade head     # 반영

uv run alembic current          # 지금 DB가 어느 버전인지
uv run alembic history          # 마이그레이션 목록
uv run alembic downgrade -1     # 한 단계 되돌리기
uv run alembic check            # models.py와 DB가 다르면 알려줌
```

이미 반영한 마이그레이션 파일은 고치지 말고, 바꿀 게 있으면 새 마이그레이션을 만드세요.

## 2. 설정

```bash
cp .env.example .env
```

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI 호환 엔드포인트 |
| `LLM_MODEL` | `gemma3:4b` | 사용할 모델 (`ollama list`의 NAME) |
| `LLM_API_KEY` | (없음) | Ollama는 필요 없음. 키가 있는 서비스로 바꿀 때만 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | JSON 배열. 프론트 주소 |
| `DATABASE_URL` | `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot` | DB 접속 주소. `+asyncpg`는 비동기 드라이버 |
| `JWT_SECRET` | (없음, **필수**) | 로그인 토큰 서명 키. 32자 이상. `openssl rand -hex 32`로 생성. 없으면 서버가 안 뜸 |
| `JWT_EXPIRE_MINUTES` | `10080` (7일) | 로그인 유지 기간 |
| `COOKIE_SECURE` | `false` | 배포(HTTPS)에서는 반드시 `true` |

시스템 프롬프트는 `app/config.py`의 `system_prompt`에서 바꿉니다.
`.env`는 `--reload`로도 다시 읽히지 않으니, 바꾼 뒤에는 서버를 재시작하세요.

`.env.example`을 복사한 경우 `JWT_SECRET`이 비어 있으니 직접 채워야 합니다.

```bash
echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env
```

### 모델 바꾸기

```bash
ollama pull <모델>      # 받고
# .env의 LLM_MODEL=<모델> 로 바꾼 뒤 서버 재시작
```

| 모델 | 크기 | 특징 |
| --- | --- | --- |
| `gemma3:4b` | 3.3GB | 기본값. 한국어 질문 5개 테스트에서 다른 언어 섞임 없음 (파이썬 용어 제외), 첫 글자 0.3초 이내 |
| `exaone3.5:7.8b` | 약 5GB | LG 모델. 한국어 특화로 더 큰 모델 (이 프로젝트에선 아직 테스트 안 함) |

메모리 16GB 맥 기준으로는 8B 정도까지가 무난합니다.
Llama, Nemotron처럼 영어 중심 모델은 한국어 답에 다른 언어가 자주 섞입니다.

### 다른 OpenAI 호환 서비스로 바꾸기

`LLM_BASE_URL` · `LLM_MODEL` · `LLM_API_KEY` 세 개만 바꾸면 코드 수정 없이 붙습니다.
예: NVIDIA Build는 `https://integrate.api.nvidia.com/v1` + `nvapi-...` 키 (`.env.example` 주석 참고).

## 3. 서버 실행

```bash
uv sync                                               # 의존성 설치 (.venv 생성)
docker compose up -d                                  # DB 켜기
uv run alembic upgrade head                           # DB 테이블 최신으로
uv run uvicorn app.main:app --reload --port 8000      # 개발용, 코드 바뀌면 자동 재시작
```

- 확인: `curl localhost:8000/health` → `{"status":"ok"}`
- Swagger UI: http://localhost:8000/docs
- 끄기: `Ctrl+C`
- 포트가 이미 쓰이는 경우: `lsof -iTCP:8000 -sTCP:LISTEN` 으로 PID 찾아서 `kill <PID>`

프로덕션처럼 띄우려면 `--reload`를 빼고 필요하면 `--host 0.0.0.0`을 붙입니다.

## 4. API

### `GET /health`

```json
{"status": "ok"}
```

### 인증 `/api/auth/...`

로그인하면 서버가 `access_token` 쿠키(httpOnly)를 내려주고, 브라우저가 이후 요청마다 자동으로 붙여 보냅니다.
프론트에서 토큰을 직접 다룰 필요가 없습니다.

| 메서드 · 경로 | 본문 | 성공 | 실패 |
| --- | --- | --- | --- |
| `POST /api/auth/signup` | `{"email", "password"}` (8~128자) | 201 사용자 정보 + 쿠키 (바로 로그인됨) | 409 이미 가입됨, 422 형식 오류 |
| `POST /api/auth/login` | `{"email", "password"}` | 200 사용자 정보 + 쿠키 | 401 이메일 또는 비밀번호 틀림 |
| `POST /api/auth/logout` | 없음 | 204 (쿠키 삭제) | - |
| `GET /api/auth/me` | 없음 | 200 사용자 정보 | 401 로그인 안 됨 · 토큰 만료 |

사용자 정보: `{"id": 1, "email": "hyesu@example.com", "created_at": "2026-10-08T06:03:37Z"}`
이메일은 앞뒤 공백을 지우고 소문자로 바꿔 저장합니다 (`Hyesu@Example.com`으로도 로그인 가능).

```bash
# curl로 해 보기 (-c 쿠키 저장, -b 쿠키 보내기)
curl -c cookies.txt localhost:8000/api/auth/signup -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"password123"}'
curl -b cookies.txt localhost:8000/api/auth/me
```

Swagger UI(`/docs`)에서 시도해도 브라우저가 쿠키를 저장해서 `/me`까지 바로 확인할 수 있습니다.

### 대화 · 메시지 `/api/conversations/...`

**모두 로그인 필요.** 내 대화만 보이고, 남의 대화 id로 요청하면 존재 여부를 숨기려고 404를 돌려줍니다.

| 메서드 · 경로 | 본문 | 응답 |
| --- | --- | --- |
| `GET /api/conversations` | - | 200 대화 목록 (최근 활동 순, 보관된 것 포함) |
| `POST /api/conversations` | - | 201 새 대화 |
| `GET /api/conversations/{id}` | - | 200 대화 |
| `PATCH /api/conversations/{id}` | `{"title"?, "archived"?}` | 200 대화 (보낸 필드만 수정) |
| `DELETE /api/conversations/{id}` | - | 204 (메시지도 함께 삭제) |
| `GET /api/conversations/{id}/messages` | - | 200 메시지 목록 (오래된 순) |
| `PUT /api/conversations/{id}/messages/{message_id}` | `{"parent_message_id", "role", "content"}` | 200 메시지 (없으면 생성, 있으면 내용 수정) |

- 대화: `{"id": "uuid", "title": "...", "archived": false, "created_at", "updated_at"}`
- 메시지: `{"message_id", "parent_message_id", "role": "user" | "assistant", "content", "created_at"}`
- `message_id`는 프론트(assistant-ui)가 붙인 id입니다. 같은 id로 다시 PUT하면 새로 쌓이지 않고 덮어씁니다.
- AI 답변 생성(`/api/chat`)은 저장과 별개입니다. 프론트가 답변을 다 받은 뒤 PUT으로 저장합니다.

### `POST /api/chat`

**로그인 필요** (쿠키 없으면 401). 요청 본문. `messages`는 1개 이상, role은 `user` 또는 `assistant`만 받습니다.
시스템 프롬프트는 서버에서 붙이므로 보내지 않습니다.

```json
{
  "messages": [
    {"role": "user", "content": "안녕"},
    {"role": "assistant", "content": "안녕하세요!"},
    {"role": "user", "content": "오늘 뭐 하지?"}
  ]
}
```

응답은 `text/plain; charset=utf-8` 스트림. 토큰이 생성되는 대로 순수 텍스트 조각이 내려옵니다.

```bash
curl -N -b cookies.txt localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"한 문장으로 인사해줘"}]}'
```

### 오류 응답

| 상황 | 상태 | 본문 |
| --- | --- | --- |
| 요청 형식 오류 (빈 messages, 짧은 비밀번호 등) | 422 | `{"error": "비밀번호는 8자 이상 128자 이하로 입력해 주세요."}` 등 |
| 로그인 필요 · 로그인 실패 | 401 | `{"error": "로그인이 필요합니다."}` 등 |
| 이미 가입된 이메일 | 409 | `{"error": "이미 가입된 이메일입니다."}` |
| 없는 대화 · 남의 대화 | 404 | `{"error": "대화를 찾을 수 없습니다."}` |
| Ollama 연결 실패 | 503 | `{"error": "LLM 서버(...)에 연결할 수 없습니다. ..."}` |
| 모델 없음 (404) | 502 | `{"error": "모델 \"...\"을 찾을 수 없습니다. ollama pull ..."}` |
| 그 밖의 오류 | 502 | `{"error": "<LLM 서버 메시지>"}` |

모든 오류가 `{"error": "..."}` 모양입니다 (`main.py`의 예외 처리기). 422 메시지는 `main.py`의 `VALIDATION_MESSAGES`에서 바꿉니다.

스트리밍 도중 오류가 오면 본문에 `(LLM 오류: ...)` 텍스트가 섞여 내려옵니다.

## 5. 코드 구조

```
app/
  main.py          FastAPI 앱, CORS, 라우터 등록, /health, 종료 시 DB 연결 정리
  config.py        .env 설정 (pydantic-settings)
  db.py            DB 엔진 · 세션 · Base, 요청별 세션 의존성 get_db
  models.py        DB 테이블 (SQLAlchemy ORM): User, Conversation, Message
  schemas.py       API 요청 · 응답 모양 (pydantic): 채팅, 인증, 대화 · 메시지
  auth.py          비밀번호 해시(argon2), JWT 발급 · 검증, 쿠키, 로그인 확인 의존성 CurrentUser
  llm.py           OpenAI 호환 SSE 스트림 → 텍스트 조각 (httpx)
  routers/auth.py  /api/auth/signup · login · logout · me
  routers/chat.py  POST /api/chat, LLM 예외 → 503/502 JSON
  routers/conversations.py  대화 목록 CRUD, 메시지 불러오기 · 저장(upsert)
migrations/        Alembic 마이그레이션 (env.py 설정, versions/ 변경 이력)
docker-compose.yml 로컬 PostgreSQL
```

Ollama의 OpenAI 호환 API(`/v1`)를 쓰므로, Ollama 전용 코드는 없습니다.

### DB 테이블

```
users ─1:N─▶ conversations ─1:N─▶ messages
  id            id (UUID)            id (UUID)
  email (uq)    user_id (FK)         conversation_id (FK)
  password_hash title                message_id ─┐ (conversation_id, message_id) unique
  created_at    archived             parent_message_id
                created_at           role (user | assistant)
                updated_at           content
                                     created_at
```

FK는 모두 `ON DELETE CASCADE`라서 사용자를 지우면 대화와 메시지가, 대화를 지우면 메시지가 함께 지워집니다.

## 6. 개발

```bash
uv run ruff check app          # lint
uv run ruff format app         # 포맷
uv add <패키지>                # 의존성 추가 (pyproject.toml + uv.lock 갱신)
```

## 7. 문제 해결

| 증상 | 원인 / 조치 |
| --- | --- |
| 503 `연결할 수 없습니다` | Ollama가 꺼져 있음. `brew services run ollama` 또는 `ollama serve` |
| 502 `모델 ... 찾을 수 없습니다` | `ollama pull <모델>` 또는 `LLM_MODEL` 오타 확인 |
| 첫 답변만 유독 느림 | 모델을 메모리에 올리는 중. 두 번째부터 빨라짐 |
| 답변에 다른 언어가 섞임 | 영어 중심 모델. `gemma3:4b`나 `exaone3.5:7.8b`로 변경 |
| 프론트에서 CORS 오류 | 프론트는 Vite 프록시를 쓰므로 보통 안 남. 직접 호출 시 `CORS_ORIGINS`에 주소 추가 |
| `docker compose up` 시 `Cannot connect to the Docker daemon` | Docker Desktop 실행 |
| DB 접속 시 `Connection refused` | `docker compose up -d`로 DB 켜기, `docker compose ps`로 healthy 확인 |
| `uv: command not found` | `brew install uv` 후 터미널 재시작 |
