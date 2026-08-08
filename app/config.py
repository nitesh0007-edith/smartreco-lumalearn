from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LumaLearn"
    environment: str = "development"
    secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./data/smartreco.db"
    chroma_path: Path = Path("./data/chroma")

    mesh_api_key: str | None = Field(default=None, repr=False)
    mesh_base_url: str = "https://api.meshapi.ai/v1"
    mesh_chat_model: str = "openai/gpt-4o-mini"
    mesh_embedding_model: str = "openai/text-embedding-3-small"
    mesh_timeout_seconds: float = 45.0

    recommendation_event_threshold: int = 5
    recommendation_cooldown_minutes: int = 10
    agent_retry_cooldown_seconds: int = 120
    recommendation_candidate_count: int = 8
    recommendation_ttl_hours: int = 24

    scheduler_enabled: bool = True
    digest_hour_utc: int = 15
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = Field(default=None, repr=False)
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True

    seed_demo_data: bool = True
    cookie_secure: bool = False

    def validate_runtime(self) -> None:
        if self.is_production and self.secret_key == "development-only-change-me":
            raise RuntimeError("Set a strong SECRET_KEY before running in production")

    @field_validator("mesh_base_url")
    @classmethod
    def enforce_mesh_gateway(cls, value: str) -> str:
        """Prevent an environment override from bypassing the mandated gateway."""
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "api.meshapi.ai":
            raise ValueError("MESH_BASE_URL must point to https://api.meshapi.ai")
        return value.rstrip("/")

    @field_validator("digest_hour_utc")
    @classmethod
    def validate_digest_hour(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("DIGEST_HOUR_UTC must be between 0 and 23")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def mesh_configured(self) -> bool:
        return bool(self.mesh_api_key and self.mesh_api_key.startswith("rsk_"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
