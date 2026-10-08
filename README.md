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

```bash
ollama serve            # 앱으로 설치했다면 이미 떠 있음 (메뉴바 아이콘)
ollama pull gemma3:4b   # 기본 모델, 약 3.3GB
```

대화 내용은 내 컴퓨터 밖으로 나가지 않고, 키나 요청 한도도 없습니다.

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

시스템 프롬프트는 `app/config.py`의 `system_prompt`에서 바꿉니다.
`.env`는 `--reload`로도 다시 읽히지 않으니, 바꾼 뒤에는 서버를 재시작하세요.

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

### `POST /api/chat`

요청 본문. `messages`는 1개 이상, role은 `user` 또는 `assistant`만 받습니다.
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
curl -N localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"한 문장으로 인사해줘"}]}'
```

### 오류 응답

| 상황 | 상태 | 본문 |
| --- | --- | --- |
| 요청 형식 오류 (빈 messages 등) | 422 | FastAPI 기본 검증 오류 |
| Ollama 연결 실패 | 503 | `{"error": "LLM 서버(...)에 연결할 수 없습니다. ..."}` |
| 모델 없음 (404) | 502 | `{"error": "모델 \"...\"을 찾을 수 없습니다. ollama pull ..."}` |
| 그 밖의 오류 | 502 | `{"error": "<LLM 서버 메시지>"}` |

스트리밍 도중 오류가 오면 본문에 `(LLM 오류: ...)` 텍스트가 섞여 내려옵니다.

## 5. 코드 구조

```
app/
  main.py          FastAPI 앱, CORS, 라우터 등록, /health
  config.py        .env 설정 (pydantic-settings)
  schemas.py       ChatRequest / ChatMessage
  llm.py           OpenAI 호환 SSE 스트림 → 텍스트 조각 (httpx)
  routers/chat.py  POST /api/chat, LLM 예외 → 503/502 JSON
```

Ollama의 OpenAI 호환 API(`/v1`)를 쓰므로, Ollama 전용 코드는 없습니다.

## 6. 개발

```bash
uv run ruff check app          # lint
uv run ruff format app         # 포맷
uv add <패키지>                # 의존성 추가 (pyproject.toml + uv.lock 갱신)
```

## 7. 문제 해결

| 증상 | 원인 / 조치 |
| --- | --- |
| 503 `연결할 수 없습니다` | Ollama가 꺼져 있음. `ollama serve` 또는 Ollama 앱 실행 |
| 502 `모델 ... 찾을 수 없습니다` | `ollama pull <모델>` 또는 `LLM_MODEL` 오타 확인 |
| 첫 답변만 유독 느림 | 모델을 메모리에 올리는 중. 두 번째부터 빨라짐 |
| 답변에 다른 언어가 섞임 | 영어 중심 모델. `gemma3:4b`나 `exaone3.5:7.8b`로 변경 |
| 프론트에서 CORS 오류 | 프론트는 Vite 프록시를 쓰므로 보통 안 남. 직접 호출 시 `CORS_ORIGINS`에 주소 추가 |
| `uv: command not found` | `brew install uv` 후 터미널 재시작 |
