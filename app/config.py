from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # OpenAI 호환 엔드포인트. 기본은 로컬 Ollama.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "gemma3:4b"
    # Ollama는 필요 없음. 키가 있는 OpenAI 호환 서비스로 바꿀 때만 넣는다.
    llm_api_key: str = ""
    cors_origins: list[str] = ["http://localhost:5173"]
    system_prompt: str = (
        "당신은 친절하고 간결한 한국어 어시스턴트입니다. "
        "반드시 사용자가 질문한 언어로 답하세요. 한국어로 물으면 처음부터 끝까지 한국어로만 답합니다. "
        "한국어로 답할 때는 영어 단어, 한자, 중국어를 섞지 말고 "
        "외래어는 한글로 적으세요 (예: salmon → 연어, lettuce → 양상추). "
        "코드, 고유명사, 사용자가 영어로 쓴 용어는 예외입니다."
    )


settings = Settings()
