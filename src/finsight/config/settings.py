"""Typed application configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_PASSWORD = "finsight-local-only"
DEFAULT_DATABASE_URL = (
    f"postgresql+psycopg://finsight:{LOCAL_DATABASE_PASSWORD}@localhost:5432/finsight"
)


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FINSIGHT_",
        extra="ignore",
    )

    app_name: str = "FinSight AI"
    api_version: str = "v1"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    sec_user_agent: str = Field(
        default="FinSightAI/0.1 contact@example.com",
        min_length=10,
    )
    sec_request_timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    sec_requests_per_second: float = Field(default=5.0, gt=0.0, le=10.0)
    sec_retry_attempts: int = Field(default=4, ge=1, le=10)

    database_url: SecretStr = Field(
        default=SecretStr(DEFAULT_DATABASE_URL),
        min_length=1,
    )
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def validate_database_configuration(self) -> Self:
        """Reject unsupported URLs and unsafe production credentials."""

        database_url = self.database_url.get_secret_value()

        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the postgresql+psycopg driver")

        if (
            self.environment in {"staging", "production"}
            and LOCAL_DATABASE_PASSWORD in database_url
        ):
            raise ValueError(
                "staging and production environments cannot use the local database password"
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance per process."""

    return Settings()
