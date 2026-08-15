"""Tests for application configuration."""

import pytest

from finsight.config.settings import Settings


def test_settings_read_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the documented FinSight environment prefix should be required."""

    monkeypatch.setenv("FINSIGHT_ENVIRONMENT", "staging")
    monkeypatch.setenv("FINSIGHT_LOG_LEVEL", "WARNING")

    settings = Settings()

    assert settings.environment == "staging"
    assert settings.log_level == "WARNING"
