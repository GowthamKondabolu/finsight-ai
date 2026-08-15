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
