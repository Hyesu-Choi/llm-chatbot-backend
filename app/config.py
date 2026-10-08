"""앱 설정. `.env` 파일과 환경 변수에서 값을 읽어온다.

pydantic-settings의 BaseSettings는 클래스 필드 이름을 대문자로 바꾼 환경 변수를 찾는다.
예: `llm_model` 필드 ← `LLM_MODEL` 환경 변수 (대소문자 무시).
우선순위: 실제 환경 변수 > .env 파일 > 아래 기본값.
그래서 `LLM_MODEL=exaone3.5:7.8b uv run uvicorn ...` 처럼 한 번만 바꿔 띄울 수도 있다.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file: 읽을 파일. extra="ignore": .env에 모르는 변수가 있어도 오류 내지 않고 무시
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenAI 호환 엔드포인트. 기본은 로컬 Ollama.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "gemma3:4b"
    # Ollama는 필요 없음. 키가 있는 OpenAI 호환 서비스로 바꿀 때만 넣는다.
    llm_api_key: str = ""
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


# 모듈을 처음 import할 때 딱 한 번 만들어지고, 다른 파일은 이 객체를 가져다 쓴다.
# 그래서 .env를 고치면 서버를 재시작해야 반영된다 (--reload는 .py 변경만 감지).
settings = Settings()
