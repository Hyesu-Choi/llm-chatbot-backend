# 배포 가이드 (AWS)

챗봇을 AWS에 올리는 과정을 **한 일 순서대로** 기록한 문서입니다. 단계를 마칠 때마다 이어서 적습니다.
체크리스트만 빠르게 보려면 [`TODO.md`의 "배포 (AWS)"](../TODO.md#배포-aws)를 보세요.

## 진행 상황

| 단계 | 내용 | 상태 |
| --- | --- | --- |
| 1 | 로컬 준비: Docker 이미지, 운영용 compose, nginx, 맥에서 시험 | ✅ 완료 (2026-10-12) |
| 2 | AWS 계정 준비: 루트 MFA, 작업용 계정, 예산 알림 | ⬜ |
| 3 | DB: RDS PostgreSQL + pgvector | ⬜ |
| 4 | 이미지 저장소: ECR에 백엔드 이미지 올리기 | ⬜ |
| 5 | 서버: EC2에서 compose로 띄우기 | ⬜ |
| 6 | 프론트: S3 + CloudFront, `/api/*` → EC2 | ⬜ |
| 7 | CI/CD: GitHub Actions (테스트 → 이미지 → 배포) | ⬜ |
| 8 | 공부 끝 → **전부 삭제** | ⬜ |

---

## 0. 무엇을, 왜 이렇게 올리나

### 결정한 것

- **유료 LLM API를 쓰지 않는다.** 로컬에서 쓰던 Ollama를 서버에도 그대로 올린다.
- **AWS는 공부용으로 며칠만 쓰고 전부 지운다.** 포트폴리오용으로 계속 켜 둘 서버는 나중에 무료 호스팅으로 따로 준비한다.
- 그래서 서버는 **메모리 8GB인 EC2 `t4g.large`** 를 쓴다. 이유는 두 가지다.
  - Ollama 모델(gemma3:4b 약 4GB + bge-m3 약 1.2GB)이 메모리에 올라가야 한다.
  - 프리 티어 서버(1GB)로는 모델이 아예 안 올라간다.
- GPU 서버는 한 달에 수십만 원이라 쓰지 않는다. 대신 CPU로 돌리니 **답변이 맥보다 느리다**. 맥은 GPU(Metal)를 쓴다.

### 전체 구조

```
브라우저 ──▶ CloudFront (HTTPS) ─┬─ /*      ──▶ S3 (프론트 빌드 파일)
                                 └─ /api/* ──▶ EC2 t4g.large
                                                 ┌─────────── docker compose ───────────┐
                                                 │ nginx :80 ─▶ backend :8000 ─▶ ollama │
                                                 └───────────────────┬──────────────────┘
                                                                     ▼
                                                    RDS PostgreSQL + pgvector (비공개)
```

질문 하나가 처리되는 흐름은 이렇다.

1. 브라우저가 `https://xxxx.cloudfront.net/api/chat`으로 요청한다. 프론트와 같은 주소라서 쿠키가 그대로 붙는다.
2. CloudFront는 주소가 `/api/`로 시작하니 S3가 아니라 EC2로 넘긴다.
3. EC2의 nginx가 받아서 backend 컨테이너로 넘긴다.
4. backend는 지금 로컬과 똑같이 동작한다. 로그인을 확인하고, 도구를 판단하고, RAG로 문서를 찾고, Ollama로 답변을 스트리밍한다.
5. 답변 글자가 nginx와 CloudFront를 거쳐 브라우저에 하나씩 도착한다.

**같은 출처가 중요한 이유**: 프론트와 API가 같은 CloudFront 주소에서 나오니, 개발할 때 Vite 프록시를 쓰던 것과 같은 상황이 된다. 그래서 CORS 설정이 필요 없고 쿠키(`SameSite=lax`)도 문제없다. 프론트 코드는 `/api/...` 상대 경로를 쓰니 고칠 게 없다.

### 파일 지도

| 파일 | 역할 | 어디서 쓰나 |
| --- | --- | --- |
| `Dockerfile` | 백엔드를 이미지로 만드는 설명서 | 맥 · GitHub Actions에서 빌드 |
| `.dockerignore` | 이미지에 넣지 않을 파일 (`.env`, `.venv` 등) | 빌드할 때 |
| `deploy/docker-compose.yml` | 운영 서버 전체 구성 (컨테이너 5개) | EC2 |
| `deploy/nginx.conf` | nginx 설정 (스트리밍, 업로드 크기, 열어 둘 주소) | EC2 |
| `deploy/.env.example` | 운영 설정 예시 (실제 값은 SSM Parameter Store) | EC2 |
| `deploy/local-test.yml` | 운영 구성을 맥에서 시험하기 위한 덮어쓰기 | 맥 |
| `docker-compose.yml` (루트) | **로컬 개발용** DB만 | 맥 |

파일마다 줄 단위 설명을 주석으로 달아 두었습니다. 이 문서는 큰 흐름을 설명하고, 자세한 이유는 파일 주석에 있습니다.

---

## 1단계. 로컬 준비 ✅

AWS에 올리기 전에, **서버에서 돌아갈 모습 그대로 맥에서 먼저 돌려 본다.** 문제는 맥에서 찾는 게 EC2에서 찾는 것보다 훨씬 빠르고 돈도 안 든다.

### 1-1. 백엔드 Docker 이미지 (`Dockerfile`)

**개념**
- **이미지**: 앱과 실행에 필요한 모든 것(파이썬, 패키지, 코드)을 묶은 꾸러미. 어느 컴퓨터에서 실행해도 똑같이 동작한다.
- **컨테이너**: 이미지를 실행한 것. 이미지가 설계도라면 컨테이너는 그 설계도로 지은 집이다. 이미지 하나로 컨테이너 여러 개를 띄울 수 있다.
- **레이어 캐시**: Dockerfile 한 줄 한 줄이 레이어다. 입력이 그대로인 줄은 다시 실행하지 않는다. 그래서 오래 걸리는 패키지 설치를 코드 복사보다 **먼저** 둔다. 코드만 고쳤을 때 설치를 건너뛰어 빌드가 몇 초면 끝난다.

**만든 방식**
1. `python:3.12-slim`을 밑바탕으로 쓴다. 로컬과 같은 파이썬 버전이다.
2. `pyproject.toml`과 `uv.lock`만 먼저 복사해서 `uv sync --locked --no-dev`로 설치한다.
   - `--locked`: 로컬에서 테스트한 버전 그대로 설치한다.
   - `--no-dev`: pytest와 ruff는 넣지 않는다.
3. `app/`, `migrations/`, `alembic.ini`를 복사한다.
4. root가 아닌 `app` 사용자로 실행한다.
5. 시작 명령은 `uvicorn app.main:app --host 0.0.0.0 --port 8000`이다. `--reload`는 쓰지 않는다.

**이미지 하나, 쓰임 둘**: 같은 이미지로 API 서버도 띄우고, 명령만 바꿔 `alembic upgrade head`(DB 구조 갱신)도 실행한다.

**빌드하고 들여다보기**

```bash
# EC2(t4g)는 ARM CPU라 arm64로 빌드한다. 맥(Apple Silicon)도 ARM이라 빠르게 빌드된다
docker build --platform linux/arm64 -t llm-chatbot-backend:local .

docker image ls llm-chatbot-backend:local          # 크기: 약 400MB
docker run --rm llm-chatbot-backend:local whoami   # app (root 아님)
docker run --rm llm-chatbot-backend:local ls -a    # .env, tests/ 가 없어야 정상
```

### 1-2. 운영 서버 구성 (`deploy/docker-compose.yml`)

**컨테이너 5개**

| 서비스 | 하는 일 | 계속 켜져 있나 |
| --- | --- | --- |
| `nginx` | 80번 포트로 요청을 받아 backend로 넘김 | ✅ |
| `backend` | FastAPI 서버 (우리 코드) | ✅ |
| `migrate` | `alembic upgrade head` 한 번 실행하고 종료 | ❌ 1회용 |
| `ollama` | LLM 서버 (CPU) | ✅ |
| `ollama-pull` | 모델(gemma3:4b, bge-m3) 받고 종료 | ❌ 1회용 |

**켜지는 순서**: `docker compose up -d` 한 번이면 `depends_on` 조건에 따라 알아서 이 순서로 켜진다.

```
ollama ──(healthy)──▶ ollama-pull ──(성공 종료)──┐
                                                 ├──▶ backend ──(healthy)──▶ nginx
                      migrate ─────(성공 종료)──┘
```

- **migrate를 따로 두는 이유**: DB 구조가 코드보다 옛날 상태면 서버가 오류를 낸다. 서버를 켜기 **전에** 갱신을 끝내야 한다. 이미 최신이면 alembic이 아무것도 안 하고 끝나서, 배포할 때마다 돌려도 안전하다.
- **ollama-pull을 따로 두는 이유**: 로컬에서는 `ollama pull`을 직접 쳤지만, 서버에서는 자동이어야 한다. 받은 모델은 `ollama-models` 볼륨에 남는다. 그래서 다음 배포 때는 확인만 하고 금방 끝난다.
- **healthcheck**: 컨테이너가 "켜져 있다"와 "요청을 받을 수 있다"는 다르다. 그 차이를 확인하는 장치다. backend는 `/health`, ollama는 `ollama list`로 확인한다.
- **서비스 이름이 주소다**: 같은 compose 안에서 backend는 `http://ollama:11434`로 Ollama를 찾는다. 컨테이너 안의 `localhost`는 그 컨테이너 자기 자신이라서, 로컬처럼 `localhost:11434`로 쓰면 못 찾는다.
- **backend는 포트를 밖에 열지 않는다**: 인터넷에서 들어오는 길은 nginx의 80번 하나뿐이다.

### 1-3. nginx (`deploy/nginx.conf`)

FastAPI를 바로 인터넷에 열지 않고 앞에 nginx를 둔다(리버스 프록시). 꼭 필요한 설정은 세 가지다.

| 설정 | 왜 필요한가 | 빠뜨리면 |
| --- | --- | --- |
| `proxy_buffering off` | 답변 글자를 받는 즉시 넘긴다 | 답변이 다 만들어질 때까지 멈춰 있다가 **한 번에** 뜬다 |
| `client_max_body_size 2m` | 문서 업로드 제한(1MB)보다 크게 | nginx 기본값이 1MB라서, 1MB 가까운 문서가 백엔드에 닿기도 전에 `413`으로 막힌다 |
| `proxy_read_timeout 300s` | CPU라 긴 답변이 오래 걸린다 | 기본 60초가 지나면 답변 중간에 끊긴다 |

열어 둔 주소는 `/api/`와 `/health`뿐이다. 그 밖의 주소(`/docs` 포함)는 404다. 프론트 화면은 S3가 주기 때문이다.

### 1-4. 운영 설정 (`deploy/.env.example`)

로컬 `.env`와 다른 값만 정리했다.

| 설정 | 로컬 | 운영 | 이유 |
| --- | --- | --- | --- |
| `DATABASE_URL` | `localhost:5432` | RDS 주소 + `?ssl=require` | RDS는 암호화 연결을 요구한다 |
| `JWT_SECRET` | 로컬용 값 | **새로 만든 값** | 로컬 키가 새어도 운영은 안전하게 |
| `COOKIE_SECURE` | `false` (http) | `true` | 사용자는 CloudFront의 HTTPS로 접속한다 |
| `LLM_BASE_URL` | `localhost:11434` | `ollama:11434` (compose에 고정) | 위 "서비스 이름이 주소다" |
| `LLM_MODELS` | `["exaone3.5:7.8b"]` | `[]` | 7.8B 모델까지 올리면 8GB가 모자란다 |
| `CORS_ORIGINS` | Vite 주소 | `[]` | 같은 출처라 필요 없다 |
| `BACKEND_IMAGE` | 없음 | ECR 이미지 주소 | compose가 어떤 이미지를 띄울지 |

비밀값(DB 비밀번호, JWT 키)은 레포에도 이미지에도 넣지 않는다. 4~5단계에서 **SSM Parameter Store**에 넣고, EC2가 읽어서 `deploy/.env`를 만들게 한다.

### 1-5. 로컬에서 운영 구성 시험하기

`deploy/local-test.yml`은 운영 파일 위에 **덮어쓰는** 파일이다. 운영 파일은 그대로 두고 아래만 바꾼다.
- **DB**: RDS 대신 로컬 Postgres에 만든 시험용 DB를 쓴다.
- **Ollama**: 컨테이너 대신 맥에서 이미 돌고 있는 Ollama를 쓴다. 모델을 수 GB씩 다시 받지 않아도 된다.
- **포트**: 80 대신 8088을 쓴다.

**준비**: 로컬 DB(`docker compose up -d`)와 Ollama가 켜져 있어야 한다.

```bash
cd ~/llm-chatbot-backend

# 1. 이미지 빌드
docker build --platform linux/arm64 -t llm-chatbot-backend:local .

# 2. 시험용 DB 만들기 (평소 쓰는 chatbot DB는 건드리지 않는다)
docker compose exec db psql -U chatbot -c 'CREATE DATABASE chatbot_deploytest'

# 3. 운영 구성 띄우기
#    -p deploytest: 프로젝트 이름. 로컬 개발용 컨테이너와 섞이지 않게
#    -f 두 개: 운영 파일 위에 시험용 덮어쓰기
#    up -d nginx: nginx와 nginx가 기다리는 것(backend, migrate)만 켠다. ollama 컨테이너는 안 켜진다
export BACKEND_IMAGE=llm-chatbot-backend:local
docker compose -p deploytest -f deploy/docker-compose.yml -f deploy/local-test.yml up -d nginx
docker compose -p deploytest -f deploy/docker-compose.yml -f deploy/local-test.yml ps -a
#    → migrate: Exited (0), backend: Up (healthy), nginx: Up 이면 성공
```

**확인하기**: `localhost` 대신 `127.0.0.1`을 쓴다. 이유는 아래 "문제 해결" 참고.

```bash
curl http://127.0.0.1:8088/health                       # {"status":"ok"}
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8088/docs   # 404 (밖에서 안 보여야 정상)

# 가입하고(-c: 쿠키 저장) 채팅(-b: 쿠키 보내기). -N: 받는 대로 바로 출력 → 글자가 흘러나오면 스트리밍 성공
curl -c /tmp/c.txt -X POST http://127.0.0.1:8088/api/auth/signup \
  -H 'content-type: application/json' -d '{"email":"t@example.com","password":"password123"}'
curl -N -b /tmp/c.txt -X POST http://127.0.0.1:8088/api/chat \
  -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"안녕! 한 문장으로 인사해줘"}]}'

# 로그 보기
docker compose -p deploytest -f deploy/docker-compose.yml -f deploy/local-test.yml logs migrate backend
```

**정리하기**: 시험이 끝나면 꼭 정리한다.

```bash
docker compose -p deploytest -f deploy/docker-compose.yml -f deploy/local-test.yml down -v
docker compose exec db psql -U chatbot -c 'DROP DATABASE chatbot_deploytest'
docker image rm llm-chatbot-backend:local
```

### 1단계 확인 결과 (2026-10-12)

| 확인한 것 | 결과 |
| --- | --- |
| 빈 DB에 마이그레이션 자동 적용 | ✅ 3개 버전 순서대로 적용 |
| 켜지는 순서 (migrate → backend healthy → nginx) | ✅ |
| nginx를 거친 스트리밍 | ✅ 글자 단위로 도착. 첫 글자 6초, 끝 7.6초 (맥 GPU 기준, 도구 판단 + RAG 포함) |
| 회원가입 · 문서 업로드 · RAG 출처 · 도구 호출 헤더 | ✅ |
| 1.2MB 파일 | ✅ nginx가 아니라 백엔드가 "최대 1MB"로 거절 |
| `/docs` 외부 노출 | ✅ 404 |
| 모델 목록 | ✅ `gemma3:4b`만 |
| `ollama-pull` 자동 다운로드 | ✅ 작은 모델(all-minilm, 46MB)로 확인 |
| 이미지 | ✅ 406MB, `app` 사용자, `.env` · `tests/` 없음 |

**EC2에서 다시 확인할 것**
- CPU 답변 속도. 맥은 GPU라 위 시간보다 느릴 것이다.
- 진짜 모델(약 5GB) 다운로드 시간.
- 8GB 메모리가 충분한지.

---

## 2단계 ~ 8단계

진행하면서 1단계처럼 채웁니다. 미리 알아 둘 점만 적어 둡니다.

- **2. 계정 준비**: 리소스를 하나라도 만들기 전에 **예산 알림**부터 건다. 루트 계정은 MFA만 켜고 평소에 쓰지 않는다.
- **3. RDS**: 퍼블릭 접근을 끈다. 보안 그룹은 EC2에서 오는 5432번만 허용한다. 첫 마이그레이션이 `CREATE EXTENSION vector`를 실행하는데, RDS도 pgvector를 지원한다.
- **4. ECR**: arm64 이미지를 올린다. EC2는 키 없이 **IAM 역할**로 이미지를 받는다.
- **5. EC2**: `t4g.large`에 디스크는 30GB(모델 + 이미지)로 잡는다. 접속은 SSH 대신 **SSM Session Manager**로 해서 22번 포트를 열지 않는다. 80번은 CloudFront에서 오는 요청만 받는다.
- **6. CloudFront**: `/api/*`는 캐시를 끄고 쿠키와 헤더를 넘긴다. CPU라 첫 글자가 늦으니 **오리진 응답 제한 시간**(기본 30초)을 늘린다.
- **7. CI/CD**: GitHub과 AWS를 **OIDC**로 연결해서 AWS 키를 GitHub에 저장하지 않는다. 레포가 public이라 GitHub의 ARM 러너로 arm64 이미지를 빌드할 수 있다.
- **8. 삭제**: EC2, EBS, RDS(+스냅샷), ECR, S3, CloudFront, Parameter Store, 탄력적 IP를 지운다. 다음 날 Billing에서 0원인지 확인한다.

---

## 문제 해결

**`curl localhost:8088`이 이상한 응답을 준다** (예: `{"code":"T002", ...}`)
맥에서는 `localhost`가 IPv6(`::1`)로 먼저 붙는다. 다른 개발 서버(예: Java)가 그 포트를 IPv6로 쓰고 있으면 그쪽으로 간다. 우리 nginx는 `127.0.0.1`에만 열려 있으니 `127.0.0.1`로 접속한다. 누가 포트를 쓰는지는 `lsof -nP -iTCP:8088 -sTCP:LISTEN`으로 확인한다. 실제로 8080은 다른 서버가 쓰고 있어서 시험 포트를 8088로 정했다.

**`required variable BACKEND_IMAGE is missing a value`**
`export BACKEND_IMAGE=...`를 빠뜨렸다. 운영에서는 `deploy/.env`에 적는다.

**`env file .../deploy/.env not found`**
운영 파일만 단독으로 실행했다. 맥에서 시험할 때는 `-f deploy/local-test.yml`을 같이 준다.

**backend가 계속 `unhealthy`이거나 `migrate`가 `Exited (1)`**
`docker compose ... logs migrate backend`로 로그를 본다. 대부분 둘 중 하나다.
- DB 접속 실패: 시험용 DB를 안 만들었거나 로컬 DB가 꺼져 있다.
- `JWT_SECRET`이 32자보다 짧다.

**답변이 한 번에 몰아서 나온다**
중간 어딘가에서 버퍼링이 일어나고 있다. `deploy/nginx.conf`에 `proxy_buffering off`가 있는지 확인한다. CloudFront 단계에서는 `/api/*` 캐시를 끄는 것도 필요하다.

**`413 Request Entity Too Large`**
nginx의 `client_max_body_size`가 업로드 파일보다 작다.

## 용어

| 용어 | 뜻 |
| --- | --- |
| 리버스 프록시 | 사용자 대신 요청을 받아 뒤쪽 서버로 넘겨 주는 서버 (여기서는 nginx) |
| healthcheck | 컨테이너가 실제로 일할 수 있는 상태인지 주기적으로 확인하는 명령 |
| 볼륨 | 컨테이너를 지워도 남는 저장 공간 (DB 데이터, Ollama 모델) |
| ECR | AWS의 Docker 이미지 저장소 (Docker Hub의 AWS판) |
| RDS | AWS가 백업 · 패치를 해 주는 관리형 DB |
| SSM Parameter Store | 비밀값을 암호화해서 보관하는 AWS 서비스 |
| IAM 역할 | 키 없이 AWS 권한을 빌려주는 방식. EC2 · GitHub Actions가 이걸로 로그인한다 |
| OIDC | GitHub이 "이 요청은 이 레포의 워크플로가 보냈다"고 AWS에 증명하는 방식 |
| arm64 / t4g | AWS Graviton(ARM) CPU. 같은 성능에 x86보다 싸다 |
