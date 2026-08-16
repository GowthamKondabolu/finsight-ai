"""Typed application configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    log_json: bool = True

    observability_enabled: bool = True
    otel_service_name: str = Field(default="finsight-api", min_length=1, max_length=100)
    otel_trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    otel_traces_endpoint: HttpUrl | None = None
    otel_metrics_endpoint: HttpUrl | None = None
    otel_export_headers: SecretStr | None = None
    otel_metric_export_interval_seconds: int = Field(default=60, ge=5, le=300)
    readiness_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    api_auth_token: SecretStr | None = Field(default=None, min_length=32)
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

    experiment_assignment_secret: SecretStr = Field(
        default=SecretStr(""),
        min_length=32,
        validate_default=True,
    )

    database_url: SecretStr = Field(
        default=SecretStr(""),
        min_length=1,
        validate_default=True,
    )
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator(
        "api_auth_token",
        "openai_api_key",
        "otel_export_headers",
        "otel_metrics_endpoint",
        "otel_traces_endpoint",
        mode="before",
    )
    @classmethod
    def normalize_optional_setting(cls, value: object) -> object:
        """Treat an empty optional environment value as unconfigured."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_database_configuration(self) -> Self:
        """Reject unsupported URLs and unsafe production credentials."""

        database_url = self.database_url.get_secret_value()

        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the postgresql+psycopg driver")

        if self.embedding_dimensions != DEFAULT_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"embedding_dimensions must match the {DEFAULT_EMBEDDING_DIMENSIONS}-dimension "
                "pgvector schema"
            )

        if not self.observability_enabled and (
            self.otel_traces_endpoint is not None or self.otel_metrics_endpoint is not None
        ):
            raise ValueError("OTLP endpoints cannot be configured when observability is disabled")

        for endpoint in (self.otel_traces_endpoint, self.otel_metrics_endpoint):
            if endpoint is not None and (
                endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            ):
                raise ValueError(
                    "OTLP endpoints cannot contain credentials, a query, or a fragment"
                )

        if self.environment in {"staging", "production"} and self.api_auth_token is None:
            raise ValueError("api_auth_token is required in staging and production")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance per process."""

    return Settings()
