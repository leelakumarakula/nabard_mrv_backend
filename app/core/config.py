from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables or the .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Coffee Climate Resilience MRV API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str
    test_database_url: str | None = None
    secret_key: str
    access_token_expire_minutes: int = 480
    password_reset_expire_minutes: int = 30
    cors_origins: str = "http://localhost:4200"
    storage_dir: str = "storage"
    max_upload_mb: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
