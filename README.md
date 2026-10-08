# llm-chatbot-backend

NVIDIA Build(NIM) API를 사용하는 스트리밍 챗봇 API (FastAPI).
프론트는 [`llm-chatbot-frontend`](../llm-chatbot-frontend).

```
브라우저 ──▶ 프론트(Vite :5173) ──/api 프록시──▶ 백엔드(FastAPI :8000) ──▶ NVIDIA Build API
```

## 1. 사전 준비

| 도구 | 버전 | 설치 |
| --- | --- | --- |
| Python | 3.12 (`.python-version`) | uv가 자동으로 받아줌 |
| uv | 0.12+ | `brew install uv` |
| NVIDIA API 키 | - | 아래 참고 |

### NVIDIA API 키 발급

1. https://build.nvidia.com 접속 후 로그인 (무료)
2. 아무 모델이나 열어서 **Get API Key** 클릭
3. `nvapi-`로 시작하는 키를 복사해서 `.env`의 `NVIDIA_API_KEY`에 붙여넣기

무료 티어는 분당 약 40회 요청 제한이 있고, 요청 내용이 NVIDIA 서버로 전송됩니다.

## 2. 설정

```bash
cp .env.example .env
# .env 열어서 NVIDIA_API_KEY 채우기
```

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NVIDIA_API_KEY` | (없음, 필수) | `nvapi-...` |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI 호환 엔드포인트 |
| `NVIDIA_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | 사용할 모델 ID |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | JSON 배열. 프론트 주소 |

시스템 프롬프트는 `app/config.py`의 `system_prompt`에서 바꿉니다.

### 모델 바꾸기

사용 가능한 모델 목록은 아래 명령으로 볼 수 있습니다 (키 없이도 조회됨).

```bash
curl -s https://integrate.api.nvidia.com/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data']]"
```

| 모델 ID | 특징 |
| --- | --- |
| `nvidia/nemotron-3-super-120b-a12b` | 기본값. NVIDIA 범용 모델, 빠름 |
| `google/gemma-3-12b-it` | 가벼움, 한국어 무난 |
| `deepseek-ai/deepseek-v4.1-flash` | DeepSeek |
| `openai/gpt-oss-20b` | OpenAI 오픈 모델 |

모델은 수시로 추가·종료되므로, 502 응답에 "end of life" 메시지가 오면 목록에서 다른 모델을 고르세요.

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
| `NVIDIA_API_KEY` 미설정 | 503 | `{"error": "NVIDIA_API_KEY가 설정되지 않았습니다. ..."}` |
| NVIDIA API 연결 실패 | 503 | `{"error": "NVIDIA API에 연결할 수 없습니다. ..."}` |
| API 키 오류 (401/403) | 502 | `{"error": "NVIDIA API 키가 올바르지 않습니다. ..."}` |
| 모델 없음 (404) | 502 | `{"error": "모델 \"...\"을 찾을 수 없습니다. ..."}` |
| 요청 한도 초과 (429) | 429 | `{"error": "NVIDIA API 요청 한도를 넘었습니다. ..."}` |
| 그 밖의 오류 | 502 | `{"error": "<NVIDIA 메시지>"}` |

스트리밍 도중 오류가 오면 본문에 `(NVIDIA 오류: ...)` 텍스트가 섞여 내려옵니다.

## 5. 코드 구조

```
app/
  main.py          FastAPI 앱, CORS, 라우터 등록, /health
  config.py        .env 설정 (pydantic-settings)
  schemas.py       ChatRequest / ChatMessage
  nvidia.py        NVIDIA Build OpenAI 호환 SSE 스트림 → 텍스트 조각 (httpx)
  routers/chat.py  POST /api/chat, NVIDIA 예외 → 503/502/429 JSON
```

NVIDIA Build는 OpenAI 호환 API라서, `NVIDIA_BASE_URL`과 키만 바꾸면 다른 OpenAI 호환 서비스에도 그대로 붙습니다.

## 6. 개발

```bash
uv run ruff check app          # lint
uv run ruff format app         # 포맷
uv add <패키지>                # 의존성 추가 (pyproject.toml + uv.lock 갱신)
```

## 7. 문제 해결

| 증상 | 원인 / 조치 |
| --- | --- |
| 503 `NVIDIA_API_KEY가 설정되지 않았습니다` | `.env`에 키 입력 후 서버 재시작 |
| 502 `API 키가 올바르지 않습니다` | 키 오타, 만료. build.nvidia.com에서 재발급 |
| 502 `... end of life ...` | 모델 서비스 종료. `NVIDIA_MODEL`을 목록의 다른 모델로 변경 |
| 429 | 무료 티어 분당 한도. 잠시 후 재시도 |
| 503 `연결할 수 없습니다` | 인터넷, 프록시, 방화벽 확인 |
| 프론트에서 CORS 오류 | 프론트는 Vite 프록시를 쓰므로 보통 안 남. 직접 호출 시 `CORS_ORIGINS`에 주소 추가 |
| `uv: command not found` | `brew install uv` 후 터미널 재시작 |
