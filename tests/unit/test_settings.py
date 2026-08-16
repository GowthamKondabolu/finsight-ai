"""Tests for application configuration."""

from typing import Literal

import pytest
from pydantic import SecretStr, ValidationError

from finsight.config.settings import DEFAULT_DATABASE_URL, Settings


def test_default_database_configuration_is_safe_for_local_development() -> None:
    """Local settings should provide bounded pool defaults and mask credentials."""

    settings = Settings()

    assert settings.database_url.get_secret_value() == DEFAULT_DATABASE_URL
    assert str(settings.database_url) == "**********"
    assert settings.database_echo is False
    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 10
    assert settings.database_pool_timeout_seconds == 30


def test_settings_read_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the documented FinSight environment prefix should be required."""

    monkeypatch.setenv("FINSIGHT_ENVIRONMENT", "staging")
    monkeypatch.setenv("FINSIGHT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv(
        "FINSIGHT_DATABASE_URL",
        "postgresql+psycopg://finsight:secure-test-password@localhost:5432/finsight",
    )
    monkeypatch.setenv("FINSIGHT_DATABASE_ECHO", "true")
    monkeypatch.setenv("FINSIGHT_DATABASE_POOL_SIZE", "8")
    monkeypatch.setenv("FINSIGHT_DATABASE_MAX_OVERFLOW", "12")
    monkeypatch.setenv("FINSIGHT_DATABASE_POOL_TIMEOUT_SECONDS", "45")

    settings = Settings()

    assert settings.environment == "staging"
    assert settings.log_level == "WARNING"
    assert settings.database_echo is True
    assert settings.database_pool_size == 8
    assert settings.database_max_overflow == 12
    assert settings.database_pool_timeout_seconds == 45


def test_settings_reject_unsupported_database_driver() -> None:
    """FinSight storage should use the configured Psycopg PostgreSQL dialect."""

    with pytest.raises(
        ValidationError,
        match="database_url must use the postgresql\\+psycopg driver",
    ):
        Settings(database_url=SecretStr("sqlite:///tmp/finsight.db"))


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_reject_local_password_outside_local_environment(
    environment: Literal["staging", "production"],
) -> None:
    """Local-only credentials must never be accepted in deployed environments."""

    with pytest.raises(
        ValidationError,
        match="cannot use the local database password",
    ):
        Settings(environment=environment)


def test_sec_client_settings_use_policy_safe_defaults() -> None:
    """SEC access defaults should remain within the published rate limit."""

    settings = Settings()

    assert settings.sec_request_timeout_seconds == 30.0
    assert settings.sec_requests_per_second == 5.0
    assert settings.sec_retry_attempts == 4


def test_sec_client_settings_read_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC client controls should support deployment-specific overrides."""

    monkeypatch.setenv("FINSIGHT_SEC_REQUEST_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("FINSIGHT_SEC_REQUESTS_PER_SECOND", "8")
    monkeypatch.setenv("FINSIGHT_SEC_RETRY_ATTEMPTS", "6")

    settings = Settings()

    assert settings.sec_request_timeout_seconds == 45.0
    assert settings.sec_requests_per_second == 8.0
    assert settings.sec_retry_attempts == 6


@pytest.mark.parametrize(
    ("environment_variable", "value"),
    [
        ("FINSIGHT_SEC_REQUEST_TIMEOUT_SECONDS", "0"),
        ("FINSIGHT_SEC_REQUEST_TIMEOUT_SECONDS", "121"),
        ("FINSIGHT_SEC_REQUESTS_PER_SECOND", "0"),
        ("FINSIGHT_SEC_REQUESTS_PER_SECOND", "10.1"),
        ("FINSIGHT_SEC_RETRY_ATTEMPTS", "0"),
        ("FINSIGHT_SEC_RETRY_ATTEMPTS", "11"),
    ],
)
def test_sec_client_settings_reject_values_outside_policy_bounds(
    monkeypatch: pytest.MonkeyPatch,
    environment_variable: str,
    value: str,
) -> None:
    """Unsafe or nonsensical SEC client controls should fail validation."""

    monkeypatch.setenv(environment_variable, value)

    with pytest.raises(ValidationError):
        Settings()


def test_embedding_settings_are_safe_and_schema_compatible() -> None:
    """Embedding defaults should match the persisted pgvector column."""

    settings = Settings()

    assert settings.openai_api_key is None
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.embedding_batch_size == 100


def test_embedding_settings_read_secret_and_batch_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployment settings should load and mask an explicit API secret."""

    monkeypatch.setenv("FINSIGHT_OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("FINSIGHT_EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("FINSIGHT_EMBEDDING_BATCH_SIZE", "32")

    settings = Settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-secret"
    assert str(settings.openai_api_key) == "**********"
    assert settings.embedding_model == "custom-model"
    assert settings.embedding_batch_size == 32


def test_embedding_settings_treat_blank_secret_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checked-in empty example value should not become a fake credential."""

    monkeypatch.setenv("FINSIGHT_OPENAI_API_KEY", "   ")

    assert Settings().openai_api_key is None


@pytest.mark.parametrize(
    ("environment_variable", "value", "message"),
    [
        ("FINSIGHT_EMBEDDING_DIMENSIONS", "512", "pgvector schema"),
        ("FINSIGHT_EMBEDDING_BATCH_SIZE", "0", "greater than or equal"),
        ("FINSIGHT_EMBEDDING_BATCH_SIZE", "2049", "less than or equal"),
        ("FINSIGHT_EMBEDDING_MODEL", "", "at least 1 character"),
    ],
)
def test_embedding_settings_reject_incompatible_values(
    monkeypatch: pytest.MonkeyPatch,
    environment_variable: str,
    value: str,
    message: str,
) -> None:
    """Configuration must not produce vectors incompatible with storage."""

    monkeypatch.setenv(environment_variable, value)

    with pytest.raises(ValidationError, match=message):
        Settings()
