"""앱 설정. `.env` 파일과 환경 변수에서 값을 읽어온다.

pydantic-settings의 BaseSettings는 클래스 필드 이름을 대문자로 바꾼 환경 변수를 찾는다.
예: `llm_model` 필드 ← `LLM_MODEL` 환경 변수 (대소문자 무시).
우선순위: 실제 환경 변수 > .env 파일 > 아래 기본값.
그래서 `LLM_MODEL=exaone3.5:7.8b uv run uvicorn ...` 처럼 한 번만 바꿔 띄울 수도 있다.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file: 읽을 파일. extra="ignore": .env에 모르는 변수가 있어도 오류 내지 않고 무시
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenAI 호환 엔드포인트. 기본은 로컬 Ollama.
    llm_base_url: str = "http://localhost:11434/v1"
    # 기본 모델. 사용자가 고르지 않았을 때, 그리고 제목 요약처럼 서버가 혼자 부를 때 쓴다.
    llm_model: str = "gemma3:4b"
    # 사용자가 화면에서 고를 수 있는 모델 (기본 모델은 자동 포함). .env에는 JSON 배열로.
    # 허용 목록을 두는 이유: 요청에 아무 모델 이름이나 받으면, 유료 API로 바꿨을 때
    # 누군가 가장 비싼 모델만 골라 불러서 요금이 폭탄이 될 수 있다.
    llm_models: list[str] = ["exaone3.5:7.8b"]
    # Ollama는 필요 없음. 키가 있는 OpenAI 호환 서비스로 바꿀 때만 넣는다.
    llm_api_key: str = ""
    # SQLAlchemy 접속 주소. 형식: 방언+드라이버://사용자:비밀번호@호스트:포트/DB이름
    # 기본값은 docker-compose.yml로 띄운 로컬 DB.
    database_url: str = "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"
    # 로그인 토큰(JWT) 서명 키. 이 값을 아는 사람은 아무 사용자로나 로그인한 척할 수 있으니 절대 공개 금지.
    # 기본값을 일부러 두지 않았다. .env에 없거나 너무 짧으면 서버가 아예 안 뜬다 (약한 키로 실수 배포 방지).
    # 만들기: openssl rand -hex 32 (64자)
    jwt_secret: str = Field(min_length=32)
    # 로그인 유지 기간. 지나면 다시 로그인해야 한다.
    jwt_expire_minutes: int = 60 * 24 * 7
    # True면 HTTPS에서만 쿠키를 보낸다. 로컬은 http라 False, 배포(HTTPS)에서는 반드시 True.
    cookie_secure: bool = False
    # 타입이 list[str]이라서 .env에는 JSON 배열 문자열로 적는다. pydantic이 알아서 파싱해 준다.
    cors_origins: list[str] = ["http://localhost:5173"]
    # 모든 대화 맨 앞에 붙는 지시문. 프론트는 보내지 않고 서버가 붙인다 (app/llm.py).
    system_prompt: str = (
        "당신은 친절하고 간결한 한국어 어시스턴트입니다. "
        "반드시 사용자가 질문한 언어로 답하세요. 한국어로 물으면 처음부터 끝까지 한국어로만 답합니다. "
        "한국어로 답할 때는 영어 단어, 한자, 중국어를 섞지 말고 "
        "외래어는 한글로 적으세요 (예: salmon → 연어, lettuce → 양상추). "
        "코드, 고유명사, 사용자가 영어로 쓴 용어는 예외입니다."
    )

    @property
    def allowed_models(self) -> list[str]:
        """기본 모델 + 선택 가능 모델 (순서 유지, 중복 제거).

        @property: 메서드지만 settings.allowed_models 처럼 속성으로 읽는다.
        dict.fromkeys(...)는 순서를 지키면서 중복을 없애는 파이썬 관용구.
        """
        return list(dict.fromkeys([self.llm_model, *self.llm_models]))


# 모듈을 처음 import할 때 딱 한 번 만들어지고, 다른 파일은 이 객체를 가져다 쓴다.
# 그래서 .env를 고치면 서버를 재시작해야 반영된다 (--reload는 .py 변경만 감지).
settings = Settings()
