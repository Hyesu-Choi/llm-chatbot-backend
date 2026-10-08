# TODO

백엔드(`llm-chatbot-backend`)와 프론트(`llm-chatbot-frontend`)에 붙일 기능 목록.
위에서부터 순서대로 하는 걸 추천.

## 기술 스택

| 영역 | 선택 | 이유 |
| --- | --- | --- |
| DB | **PostgreSQL** | 아래 "DB 선택" 참고 |
| ORM · 마이그레이션 | SQLAlchemy 2.0 (async) + asyncpg + Alembic | Python 표준. DB를 바꿔도 코드가 거의 그대로 |
| 로그인 | 이메일 + 비밀번호, argon2 해시(`pwdlib`), JWT를 httpOnly 쿠키에 (`PyJWT`) | 인증 원리를 직접 짜 보며 배우기 좋음. 구글 로그인은 나중에 추가 |
| 로컬 개발 | Docker Compose로 Postgres만 띄우기, Ollama는 맥에서 직접 실행 | 맥의 Docker는 GPU(Metal)를 못 써서 Ollama를 컨테이너에 넣으면 아주 느림 |
| 배포: 프론트 | AWS S3 + CloudFront | 정적 파일은 S3에, CloudFront가 HTTPS · 캐시 · `/api` 라우팅 담당 |
| 배포: 백엔드 | AWS EC2 1대 + Docker Compose (nginx + FastAPI) | 서버 · 네트워크 · 보안 그룹을 직접 만지며 AWS 기본기를 배우기 좋음 |
| 배포: DB | AWS RDS PostgreSQL | 관리형 DB. 백업 · 패치를 AWS가 해 줌. 비공개 서브넷에 둬서 외부 접근 차단 |
| 배포: 이미지 · 비밀값 | ECR(Docker 이미지 저장소), SSM Parameter Store(비밀값) | 키를 코드나 이미지에 넣지 않기 |
| 배포: LLM | OpenAI 호환 API 서비스 (`LLM_*` 환경 변수만 교체). Amazon Bedrock도 후보 | EC2 GPU 서버에서 Ollama를 돌리면 한 달 수십만 원이라 비현실적 |
| CI/CD | GitHub Actions → (OIDC로 AWS 로그인) → ECR · S3 배포 | AWS 키를 GitHub에 저장하지 않는 안전한 방식 |

AWS 요금과 프리 티어 조건은 계정 생성 시기에 따라 다르고 자주 바뀌니, 리소스를 만들기 전에 꼭 확인할 것.

### DB 선택: SQLite → PostgreSQL, MySQL 대신 PostgreSQL

- **SQLite를 안 쓰는 이유**: 파일 하나짜리 DB라 배포 컨테이너가 재시작되면 데이터가 날아가고(별도 볼륨 필요), 여러 요청이 동시에 쓰면 약함. 배포까지 생각하면 처음부터 서버형 DB가 나음.
- **MySQL 대신 PostgreSQL인 이유**: SQL 문법이 거의 같아서 MySQL을 써 봤으면 금방 익숙해짐. 3순위 RAG에서 벡터 검색(`pgvector`, RDS에서도 지원)을 같은 DB로 할 수 있고, Python 생태계에서도 기본처럼 쓰임.
- AWS RDS는 MySQL과 Postgres를 똑같이 지원하니 MySQL로 해도 문제는 없음. SQLAlchemy를 쓰면 코드가 거의 같고 접속 주소(`DATABASE_URL`)만 다름. RAG를 할 때 벡터 검색만 따로 고민하면 됨.

## 0단계: 기반 (DB · 로그인)

대화 저장이 "사용자별"이 되려면 DB와 로그인이 먼저 있어야 함.

- [x] **로컬 Postgres**: `docker-compose.yml`에 Postgres 17 추가, `.env`에 `DATABASE_URL`
- [x] **SQLAlchemy + Alembic 세팅**: DB 세션 의존성(`Depends`), 첫 마이그레이션으로 `users` 테이블
- [x] **인증 API**
  - `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
  - 비밀번호는 argon2로 해시해서 저장 (평문 저장 금지)
  - 로그인하면 JWT를 httpOnly 쿠키로 내려줌 (자바스크립트에서 못 읽어서 XSS에 안전)
  - 공부: 해시와 암호화의 차이, JWT 구조, 쿠키 옵션(`HttpOnly`, `Secure`, `SameSite`)
- [x] **`/api/chat` 보호**: 로그인한 사용자만 호출 가능 (`Depends(get_current_user)`)
- [x] **프론트 로그인 · 회원가입 화면**: 로그인 안 했으면 로그인 화면으로, 로그아웃 버튼
- [ ] (나중에) **구글 로그인**: OAuth 흐름 공부용

## 1순위: 바로 체감되는 것

- [x] **대화 저장 (사용자별)**
  - 문제: `useLocalRuntime`이 메모리에만 들고 있어서 새로고침하면 대화가 사라짐
  - 백엔드: `conversations`, `messages` 테이블 + 대화 목록 · 조회 · 삭제 API. `/api/chat`이 답변을 다 받은 뒤 DB에 저장
  - 프론트: assistant-ui 대화 목록을 백엔드 API에 연결
  - 공부: 1:N 관계(사용자 → 대화 → 메시지), 다른 사람 대화에 접근 못 하게 막기
- [x] **모델 선택 드롭다운**
  - 문제: 지금은 `.env`의 `LLM_MODEL`을 바꾸고 서버를 재시작해야 함
  - 백엔드: `GET /api/models` 추가 (`/v1/models` 전달), `POST /api/chat` 본문에 `model` 필드 추가 (허용 목록에 있는 모델만)
  - 프론트: 헤더나 입력창 옆에 드롭다운
  - 공부: 새 API 추가 흐름(스키마 → 라우터 → 프론트 호출), gemma3 / exaone 비교
- [x] **대화 제목 자동 생성**
  - `POST /api/conversations/{id}/title`: 기본 모델이 15자 이내로 요약 (스트리밍 없이), 실패하면 첫 질문 앞 30자
  - 공부: 프롬프트 활용, 스트리밍 / 비스트리밍 요청 차이

## 2순위: 챗봇다운 기능

- [x] **시스템 프롬프트(역할) 고르기**
  - 영어 선생님, 코드 리뷰어 같은 역할 선택
  - 서버에 고정된 `system_prompt`를 요청마다 고를 수 있게 변경 (아무 문자열이나 받지 말고 서버에 정의된 역할 ID만 받기)
- [x] **답변 다시 생성 / 메시지 편집**
  - assistant-ui에 기본 기능이 있어서 버튼만 연결하면 됨
  - 공부: assistant-ui 구조 이해
- [x] **긴 대화 처리**
  - 문제: 매번 대화 전체를 보내서 길어지면 모델이 처리할 수 있는 길이(컨텍스트)를 넘음
  - 최근 N개만 보내기, 또는 앞부분을 요약해서 보내기

## 3순위: 한 단계 더

- [ ] **RAG (내 문서로 답하기)**
  - PDF나 노트를 넣으면 그 내용을 근거로 답변
  - 임베딩 모델(`ollama pull nomic-embed-text`) + 벡터 검색 (Postgres 확장 `pgvector`를 쓰면 DB를 따로 안 둬도 됨)
  - 공부: LLM 앱의 핵심 패턴. 공부 가치가 가장 큼
- [ ] **도구 호출 (tool calling)**
  - "서울 날씨 알려줘" → LLM이 날씨 API 함수를 직접 호출
  - 공부: 에이전트의 기본 원리
- [ ] **백엔드 테스트 (pytest)**
  - 오류 처리부터: 모델 없음 → 502, Ollama 연결 실패 → 503, 빈 messages → 422
  - 로그인: 틀린 비밀번호, 로그인 없이 `/api/chat` 호출 → 401, 남의 대화 조회 → 404

## 배포 (AWS)

로컬에서 0~1순위가 동작한 뒤에 진행.

```
브라우저 ──▶ CloudFront ─┬─ /*      ──▶ S3 (프론트 빌드 결과)
                         └─ /api/* ──▶ EC2 [nginx ──▶ FastAPI 컨테이너] ──▶ RDS PostgreSQL (비공개 서브넷)
                                                                  └──▶ LLM API
```

CloudFront가 `/api/*`를 백엔드로 넘겨서 브라우저 입장에선 같은 출처 → 쿠키 · CORS가 개발 때처럼 문제없음.

### 1. 계정 준비 (제일 먼저, 요금 사고 방지)

- [ ] 루트 계정 MFA 켜고, 루트 계정은 평소에 쓰지 않기
- [ ] IAM Identity Center(또는 IAM 사용자)로 작업용 계정 만들기, 로컬에 `aws configure sso`
- [ ] **AWS Budgets로 월 예산 알림** (예: 1만 원 넘으면 메일). 이것부터 해 두기
- [ ] 리전 하나로 통일 (서울 `ap-northeast-2`)
- 공부: IAM 사용자 · 역할 · 정책의 차이, 최소 권한 원칙

### 2. 프론트: S3 + CloudFront

- [ ] S3 버킷 생성 (퍼블릭 접근 차단 유지), `npm run build` 결과(`dist/`) 업로드
- [ ] CloudFront 배포 생성, OAC로 CloudFront만 S3를 읽게 설정
- [ ] SPA라서 403/404 오류를 `/index.html`로 돌리는 설정
- 공부: S3를 직접 공개하지 않고 CloudFront 뒤에 두는 이유, 캐시와 무효화(invalidation)

### 3. 네트워크: VPC

- [ ] 기본 VPC로 시작하되 구조 이해하기: 퍼블릭 서브넷(EC2) / 비공개 서브넷(RDS)
- [ ] 보안 그룹: EC2는 80/443만 열기(SSH 대신 SSM Session Manager로 접속), RDS는 EC2 보안 그룹에서 오는 5432만 허용
- 공부: 서브넷, 라우팅 테이블, 인터넷 게이트웨이, 보안 그룹 vs NACL

### 4. DB: RDS PostgreSQL

- [ ] RDS PostgreSQL 가장 작은 인스턴스, 퍼블릭 접근 끔, 자동 백업 켬
- [ ] 접속 정보는 SSM Parameter Store(SecureString)에 저장
- [ ] 배포할 때 `alembic upgrade head`로 DB 구조 갱신
- 공부: 왜 DB를 인터넷에 노출하면 안 되는지, 스냅샷과 복구

### 5. 백엔드: EC2 + Docker

- [ ] 백엔드 Dockerfile 작성 (uv로 설치, `uvicorn` `--host 0.0.0.0`, `--reload` 없이)
- [ ] ECR 리포지토리 만들고 이미지 푸시 (EC2가 ARM `t4g`면 `--platform linux/arm64`로 빌드)
- [ ] EC2 작은 인스턴스(`t4g.micro` 정도) + IAM 역할(ECR 읽기, SSM 파라미터 읽기)
- [ ] EC2에서 Docker Compose로 nginx + FastAPI 실행
  - nginx에 `proxy_buffering off;` 필수. 안 하면 스트리밍 답변이 한 번에 몰아서 나옴
- [ ] 환경 변수: `DATABASE_URL`, `JWT_SECRET`(길고 무작위), `LLM_*` → 전부 Parameter Store에서 읽기
- [ ] 쿠키 `Secure` 켜기, `/docs` 숨길지 결정
- [ ] CloudFront에 `/api/*` 동작 추가 → EC2로 전달 (캐시 끄기, 쿠키 · 헤더 전달)
- 공부: IAM 역할로 키 없이 권한 주기, 컨테이너 배포, 리버스 프록시

### 6. 운영 LLM

- [ ] 1안 (추천): OpenAI 호환 유료 API. `LLM_*` 세 개만 Parameter Store에 넣으면 끝. 오늘 쓴 한국어 질문 5개로 모델 고르기
- [ ] 2안: Amazon Bedrock. AWS 안에서 해결되고 IAM 역할로 인증 가능. OpenAI 호환 방식 지원 범위는 확인 필요 (안 되면 `app/llm.py`에 Bedrock 호출 추가)
- [ ] **사용자별 요청 제한**: 유료 API 비용 보호용 분당 · 일당 횟수 제한

### 7. CI/CD: GitHub Actions

- [ ] GitHub OIDC로 AWS 역할 맡기 (AWS 키를 GitHub Secrets에 저장하지 않음)
- [ ] PR마다: 백엔드 `ruff check` · `pytest`, 프론트 `npm run lint` · `npm run build`
- [ ] main 푸시 시 백엔드: 이미지 빌드 → ECR 푸시 → SSM Run Command로 EC2에서 새 이미지 받아 재시작
- [ ] main 푸시 시 프론트: `dist/`를 S3에 동기화 → CloudFront 무효화

### 8. 운영 점검

- [ ] CloudWatch Logs로 컨테이너 로그 보기, `/health` 상태 확인 알람
- [ ] RDS 백업에서 복구 한 번 연습해 보기
- [ ] (선택) Route 53 도메인 + ACM 인증서 (CloudFront용 인증서는 `us-east-1`에서 발급)
- [ ] 공부 끝나고 안 쓸 땐 EC2 · RDS 중지 또는 삭제 (켜 둔 시간만큼 요금)

### 나중에 (심화)

- [ ] EC2 → ECS Fargate로 옮기기 (서버 관리 없이 컨테이너만). ALB 비용이 추가되니 그때 확인
- [ ] 인프라를 코드로: Terraform 또는 AWS CDK로 위 구성을 다시 만들어 보기
