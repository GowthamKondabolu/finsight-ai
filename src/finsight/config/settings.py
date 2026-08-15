"""Typed application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance per process."""

    return Settings()
