from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Config model
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    app_name: str = "Habiterra AI"
    app_env: str = "development"
    groq_api_key: SecretStr = SecretStr("")
    groq_transcription_model: str = "whisper-large-v3-turbo"
    groq_structured_model: str = "llama-3.1-8b-instant"
    huggingface_api_key: SecretStr = SecretStr("")
    huggingface_transcription_model: str = ""
    huggingface_transcription_url: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-1.5-flash"
    http_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    http_connect_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    http_read_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    max_audio_size_mb: int = Field(default=20, ge=1, le=100)
    cors_origins: list[str] = []

