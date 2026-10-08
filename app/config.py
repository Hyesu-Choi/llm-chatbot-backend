from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # build.nvidia.com 에서 발급한 키 (nvapi-...)
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    cors_origins: list[str] = ["http://localhost:5173"]
    system_prompt: str = (
        "당신은 친절하고 간결한 한국어 어시스턴트입니다. "
        "사용자가 다른 언어로 물어보면 그 언어로 답하세요. "
        "한국어로 답할 때는 한자나 중국어 단어를 섞지 마세요."
    )


settings = Settings()
