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
ollama pull gemma3:4b   # 기본 채팅 모델, 약 3.3GB (최초 1회)
ollama pull bge-m3      # RAG용 임베딩 모델, 약 1.2GB (문서 기능을 쓸 때만 필요)
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
이미지는 벡터 검색 확장이 들어 있는 `pgvector/pgvector:pg17`(Postgres 17)입니다.

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
| `LLM_MODEL` | `gemma3:4b` | 기본 모델 (`ollama list`의 NAME). 제목 요약에도 사용 |
| `LLM_MODELS` | `["exaone3.5:7.8b"]` | 화면에서 고를 수 있는 모델 (기본 모델 자동 포함). 목록에 없는 모델은 400 |
| `LLM_API_KEY` | (없음) | Ollama는 필요 없음. 키가 있는 서비스로 바꿀 때만 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | JSON 배열. 프론트 주소 |
| `LLM_MAX_HISTORY_CHARS` | `6000` | LLM에 보낼 대화 기록 최대 글자 수. 넘으면 오래된 메시지부터 뺌 |
| `DATABASE_URL` | `postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot` | DB 접속 주소. `+asyncpg`는 비동기 드라이버 |
| `JWT_SECRET` | (없음, **필수**) | 로그인 토큰 서명 키. 32자 이상. `openssl rand -hex 32`로 생성. 없으면 서버가 안 뜸 |
| `JWT_EXPIRE_MINUTES` | `10080` (7일) | 로그인 유지 기간 |
| `COOKIE_SECURE` | `false` | 배포(HTTPS)에서는 반드시 `true` |

역할(시스템 프롬프트)은 `app/personas.py`에서 바꾸거나 추가합니다. "기본" 역할은 `app/config.py`의 `system_prompt`를 씁니다.
`.env`는 `--reload`로도 다시 읽히지 않으니, 바꾼 뒤에는 서버를 재시작하세요.

`.env.example`을 복사한 경우 `JWT_SECRET`이 비어 있으니 직접 채워야 합니다.

```bash
echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env
```

### 모델 바꾸기

```bash
ollama pull <모델>      # 받고
# .env의 LLM_MODELS 배열에 추가(드롭다운에서 고르기) 또는 LLM_MODEL로 지정(기본 모델) → 서버 재시작
```

허용 목록에 있어도 `ollama pull`을 안 했으면 드롭다운에 "(설치 필요)"로 흐리게 보이고 고를 수 없습니다.

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

## 4. RAG (내 문서로 답하기)

사용자가 올린 문서에서 질문과 관련된 부분을 찾아 답변의 근거로 씁니다. 따로 켜는 스위치 없이, 문서가 있고 관련 있는 내용이 있을 때만 자동으로 참고합니다.

```
[올릴 때]  문서 → 조각내기(rag.split_text) → 조각마다 임베딩(bge-m3, 1024차원) → document_chunks에 저장
[질문할 때] 질문 임베딩 → pgvector로 가장 비슷한 조각 찾기(rag.search_chunks) → 기준 통과한 조각만
            → 시스템 프롬프트 뒤에 참고 자료로 붙이기(rag.build_context) → LLM 답변 + 출처 헤더
```

| 단계 | 정한 것 | 이유 |
| --- | --- | --- |
| 조각내기 | 마크다운 제목(`#`)마다 끊고, 섹션 안은 문단을 500자까지 합침. 긴 문단은 100자 겹치게 자름 | 주제가 섞인 조각은 벡터가 "평균"이 돼서 검색이 흐려짐. 제목만 있는 조각은 오탐을 내서 다음 섹션에 붙임 |
| 임베딩 모델 | `bge-m3` | 한국어 질문 6개 비교: bge-m3 6/6, nomic-embed-text 1/6 |
| 검색 | 코사인 거리(`<=>`) + HNSW 인덱스, 최대 4개 | 질문마다 모든 조각과 비교하지 않고 빠르게 찾음 |
| 거르기 | 유사도 0.45 미만 버림 + 1등보다 0.1 넘게 낮으면 버림 | 측정: 관련 질문 1등 0.56~0.72, 무관한 질문 1등 0.34~0.42 |
| 격리 | 검색할 때 `documents.user_id`로 JOIN해서 내 문서만 | 다른 사람 문서가 답변에 섞이면 안 됨 (테스트로 고정) |

설정은 `.env`의 `EMBEDDING_MODEL`, `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`, `RAG_TOP_K`, `RAG_MIN_SIMILARITY`, `RAG_RELATIVE_MARGIN`, `DOCUMENT_MAX_BYTES`로 바꿀 수 있습니다 (기본값은 `app/config.py`).
임베딩 모델을 바꾸면 벡터 크기(`models.py`의 `EMBEDDING_DIMENSIONS`)와 저장된 벡터가 맞지 않으니, 마이그레이션 후 문서를 다시 올려야 합니다.

## 5. API

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
| `POST /api/conversations/{id}/title` | `{"question": "첫 질문"}` | 200 대화 (LLM이 15자 이내 제목으로 요약해 저장. 실패하면 질문 앞 30자) |

- 대화: `{"id": "uuid", "title": "...", "archived": false, "created_at", "updated_at"}`
- 메시지: `{"message_id", "parent_message_id", "role": "user" | "assistant", "content", "created_at"}`
- `message_id`는 프론트(assistant-ui)가 붙인 id입니다. 같은 id로 다시 PUT하면 새로 쌓이지 않고 덮어씁니다.
- AI 답변 생성(`/api/chat`)은 저장과 별개입니다. 프론트가 답변을 다 받은 뒤 PUT으로 저장합니다.

### `GET /api/models`

**로그인 필요.** 드롭다운에 보여줄 모델 목록. 허용 목록(`LLM_MODEL` + `LLM_MODELS`)마다 Ollama 설치 여부를 붙여 줍니다.

```json
{"default": "gemma3:4b", "models": [{"id": "gemma3:4b", "installed": true}, {"id": "exaone3.5:7.8b", "installed": false}]}
```

### `GET /api/personas`

**로그인 필요.** 역할 드롭다운에 보여줄 목록 (`app/personas.py`). 프롬프트 내용은 내보내지 않습니다.

```json
{"default": "default", "personas": [{"id": "default", "name": "기본", "description": "무엇이든 친절하게 답해요"}, {"id": "english_teacher", "name": "영어 선생님", "description": "..."}]}
```

### 문서 `/api/documents`

**모두 로그인 필요.** 내 문서만 보이고, 남의 문서 id는 404.

| 메서드 · 경로 | 본문 | 응답 |
| --- | --- | --- |
| `GET /api/documents` | - | 200 `[{"id", "filename", "char_count", "chunk_count", "created_at"}]` (최근 순) |
| `POST /api/documents` | multipart `file` (.txt / .md, UTF-8, 최대 1MB) | 201 문서. 조각내기 · 임베딩까지 끝난 뒤 응답 |
| `DELETE /api/documents/{id}` | - | 204 (조각도 함께 삭제) |

```bash
curl -b cookies.txt -F "file=@규정.md" localhost:8000/api/documents
```

### `POST /api/chat`

**로그인 필요** (쿠키 없으면 401). 내 문서 중 관련 조각이 있으면 근거로 쓰고, 응답 헤더 `X-RAG-Sources`에 출처를 담습니다 (URL 인코딩된 JSON: `[{"filename", "chunk", "similarity"}]`).

- `model` (선택): 없으면 `LLM_MODEL`. 허용 목록에 없으면 400.
- `persona` (선택): 역할 id. 없으면 `"default"`. 없는 id면 400. 프롬프트 문자열은 받지 않습니다.
- 대화가 `LLM_MAX_HISTORY_CHARS`를 넘으면 최근 메시지만 보냅니다 (시스템 프롬프트가 잘려 나가지 않게). `messages`는 1개 이상, role은 `user` 또는 `assistant`만 받습니다.
시스템 프롬프트는 서버에서 붙이므로 보내지 않습니다.

```json
{
  "messages": [
    {"role": "user", "content": "안녕"},
    {"role": "assistant", "content": "안녕하세요!"},
    {"role": "user", "content": "오늘 뭐 하지?"}
  ],
  "model": "gemma3:4b",
  "persona": "default"
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
| 허용 안 된 모델 · 없는 역할 | 400 | `{"error": "선택할 수 없는 모델입니다: \"...\""}` |
| 이미 가입된 이메일 | 409 | `{"error": "이미 가입된 이메일입니다."}` |
| 없는 대화 · 남의 대화 | 404 | `{"error": "대화를 찾을 수 없습니다."}` |
| Ollama 연결 실패 | 503 | `{"error": "LLM 서버(...)에 연결할 수 없습니다. ..."}` |
| 모델 없음 (404) | 502 | `{"error": "모델 \"...\"을 찾을 수 없습니다. ollama pull ..."}` |
| 그 밖의 오류 | 502 | `{"error": "<LLM 서버 메시지>"}` |

모든 오류가 `{"error": "..."}` 모양입니다 (`main.py`의 예외 처리기). 422 메시지는 `main.py`의 `VALIDATION_MESSAGES`에서 바꿉니다.

스트리밍 도중 오류가 오면 본문에 `(LLM 오류: ...)` 텍스트가 섞여 내려옵니다.

## 6. 코드 구조

```
app/
  main.py          FastAPI 앱, CORS, 라우터 등록, /health, 종료 시 DB 연결 정리, 오류 → {"error"} 처리기
  config.py        .env 설정 (pydantic-settings)
  db.py            DB 엔진 · 세션 · Base, 요청별 세션 의존성 get_db
  models.py        DB 테이블 (SQLAlchemy ORM): User, Conversation, Message
  schemas.py       API 요청 · 응답 모양 (pydantic): 채팅, 인증, 대화 · 메시지
  auth.py          비밀번호 해시(argon2), JWT 발급 · 검증, 쿠키, 로그인 확인 의존성 CurrentUser
  llm.py           OpenAI 호환 API: 스트리밍 채팅, 한 번에 받기(제목 요약), 설치된 모델 목록, 긴 대화 자르기 (httpx)
  personas.py      역할 목록 (id · 이름 · 설명 · 시스템 프롬프트)
  rag.py           RAG: 조각내기, pgvector 검색 · 거르기, 참고 자료 프롬프트 만들기
  routers/auth.py  /api/auth/signup · login · logout · me
  routers/chat.py  POST /api/chat, LLM 예외 → 503/502 JSON
  routers/conversations.py  대화 목록 CRUD, 메시지 불러오기 · 저장(upsert), LLM 제목 요약
  routers/models.py  GET /api/models (허용 목록 + 설치 여부)
  routers/personas.py  GET /api/personas
  routers/documents.py  문서 올리기(조각 · 임베딩) · 목록 · 삭제
migrations/        Alembic 마이그레이션 (env.py 설정, versions/ 변경 이력)
tests/             pytest (아래 "테스트" 참고)
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
users ─1:N─▶ documents ─1:N─▶ document_chunks
                id (UUID)            id (UUID)
                user_id (FK)         document_id (FK)
                filename             chunk_index
                content              content
                created_at           embedding vector(1024)  ← HNSW 인덱스 (vector_cosine_ops)
```

FK는 모두 `ON DELETE CASCADE`라서 사용자를 지우면 대화 · 메시지 · 문서 · 조각이, 대화나 문서를 지우면 딸린 메시지 · 조각이 함께 지워집니다.

## 7. 개발

```bash
uv run ruff check app tests    # lint
uv run ruff format app tests   # 포맷
uv add <패키지>                # 의존성 추가 (pyproject.toml + uv.lock 갱신)
```

### 테스트

```bash
docker compose up -d           # DB가 켜져 있어야 함 (Ollama는 필요 없음)
uv run pytest                  # 전체 68개 (약 3초)
uv run pytest -q tests/test_auth.py          # 파일 하나만
uv run pytest -k other_users                 # 이름에 other_users가 들어간 테스트만
uv run pytest -x                             # 첫 실패에서 멈춤
```

- 개발 DB(`chatbot`)와 따로 **`chatbot_test` DB**를 쓰고, 시작할 때 `alembic upgrade head`로 테이블을 만듭니다. 테스트마다 테이블을 비웁니다.
- LLM은 `monkeypatch`로 가짜 함수로 바꿔서, Ollama 없이도 빠르고 항상 같은 결과로 돕니다.

| 파일 | 내용 |
| --- | --- |
| `tests/conftest.py` | 테스트 DB 준비, `client` fixture |
| `tests/helpers.py` | `signup()`, `login_as()` (여러 사용자 오가기) |
| `tests/test_auth.py` | 가입 · 로그인 · 로그아웃, 쿠키 옵션, 위조 · 만료 토큰 |
| `tests/test_conversations.py` | 대화 CRUD, upsert, 가지, **다른 사용자 격리**, 제목 요약 · 실패 시 대체 |
| `tests/test_chat.py` | 채팅 스트림, 모델 · 역할 검증, 긴 대화 자르기, LLM 오류 → 503/502, 모델 · 역할 목록 |
| `tests/test_documents.py` | 문서 올리기 검증, **RAG 검색 · 거르기 · 다른 사용자 문서 격리**, 조각내기 단위 테스트 (임베딩만 가짜, pgvector 검색은 진짜) |
| `tests/test_units.py` | `trim_history`, 제목 정리 함수 단위 테스트 |

## 8. 문제 해결

| 증상 | 원인 / 조치 |
| --- | --- |
| 503 `연결할 수 없습니다` | Ollama가 꺼져 있음. `brew services run ollama` 또는 `ollama serve` |
| 502 `모델 ... 찾을 수 없습니다` | `ollama pull <모델>` 또는 `LLM_MODEL` 오타 확인 |
| 첫 답변만 유독 느림 | 모델을 메모리에 올리는 중. 두 번째부터 빨라짐 |
| 문서 올리기에서 `모델 "bge-m3"을 찾을 수 없습니다` | `ollama pull bge-m3` |
| 답변에 다른 언어가 섞임 | 영어 중심 모델. `gemma3:4b`나 `exaone3.5:7.8b`로 변경 |
| 프론트에서 CORS 오류 | 프론트는 Vite 프록시를 쓰므로 보통 안 남. 직접 호출 시 `CORS_ORIGINS`에 주소 추가 |
| `docker compose up` 시 `Cannot connect to the Docker daemon` | Docker Desktop 실행 |
| DB 접속 시 `Connection refused` | `docker compose up -d`로 DB 켜기, `docker compose ps`로 healthy 확인 |
| `uv: command not found` | `brew install uv` 후 터미널 재시작 |
