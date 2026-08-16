"""Typed application configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_PASSWORD = "finsight-local-only"
DEFAULT_DATABASE_URL = (
    f"postgresql+psycopg://finsight:{LOCAL_DATABASE_PASSWORD}@localhost:5432/finsight"
)
DEFAULT_EMBEDDING_DIMENSIONS = 1536


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

    openai_api_key: SecretStr | None = None
    embedding_model: str = Field(
        default="text-embedding-3-small",
        min_length=1,
        max_length=100,
    )
    embedding_dimensions: int = Field(
        default=DEFAULT_EMBEDDING_DIMENSIONS,
        ge=1,
        le=3072,
    )
    embedding_batch_size: int = Field(default=100, ge=1, le=2048)

    generation_model: str = Field(default="gpt-5.6-luna", min_length=1, max_length=100)
    generation_max_output_tokens: int = Field(default=2_000, ge=256, le=20_000)
    generation_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"

    database_url: SecretStr = Field(
        default=SecretStr(DEFAULT_DATABASE_URL),
        min_length=1,
    )
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def normalize_optional_secret(cls, value: object) -> object:
        """Treat an empty environment variable as an unconfigured secret."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

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

        if self.embedding_dimensions != DEFAULT_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"embedding_dimensions must match the {DEFAULT_EMBEDDING_DIMENSIONS}-dimension "
                "pgvector schema"
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance per process."""

    return Settings()
