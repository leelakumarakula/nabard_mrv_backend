from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_ENVIRONMENTS = {"local", "development", "test"}


class Settings(BaseSettings):
    """Application settings, read from environment variables or the .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Coffee Climate Resilience MRV API"
    # Defaults to production so a deployment that forgets APP_ENV never runs with development behaviour.
    app_env: str = "production"
    api_v1_prefix: str = "/api/v1"
    database_url: str
    test_database_url: str | None = None
    secret_key: str
    access_token_expire_minutes: int = 480
    password_reset_expire_minutes: int = 30
    cors_origins: str = "http://localhost:4200"
    storage_dir: str = "storage"
    max_upload_mb: int = 20
    # FarmFuture API (read-only pulls). The token is a secret: environment / .env only, never the database or logs.
    farmfuture_api_base_url: str = "https://api.farmfuture.io"
    farmfuture_api_token: str | None = None
    # In-process daily-pull runner (single-process deployments only; otherwise run app.services.integration.scheduler)
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Only these explicitly named environments get development conveniences (e.g. reset tokens in responses)."""
        return self.app_env.lower() in DEVELOPMENT_ENVIRONMENTS

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
