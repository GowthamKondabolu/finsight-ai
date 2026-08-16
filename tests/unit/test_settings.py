"""Tests for application configuration."""

from typing import Literal

import pytest
from pydantic import HttpUrl, SecretStr, ValidationError

from finsight.config.settings import Settings


def test_required_runtime_secrets_are_masked() -> None:
    """Injected test configuration should mask all credential-bearing values."""

    settings = Settings()

    assert str(settings.database_url) == "**********"
    assert str(settings.experiment_assignment_secret) == "**********"
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
        "postgresql+psycopg://finsight@db:5432/finsight",
    )
    monkeypatch.setenv("FINSIGHT_DATABASE_ECHO", "true")
    monkeypatch.setenv("FINSIGHT_DATABASE_POOL_SIZE", "8")
    monkeypatch.setenv("FINSIGHT_DATABASE_MAX_OVERFLOW", "12")
    monkeypatch.setenv("FINSIGHT_DATABASE_POOL_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv(
        "FINSIGHT_EXPERIMENT_ASSIGNMENT_SECRET",
        "staging-test-assignment-secret-32-characters",
    )
    monkeypatch.setenv("FINSIGHT_API_AUTH_TOKEN", "s" * 32)

    settings = Settings()

    assert settings.environment == "staging"
    assert settings.log_level == "WARNING"
    assert settings.database_echo is True
    assert settings.database_pool_size == 8
    assert settings.database_max_overflow == 12
    assert settings.database_pool_timeout_seconds == 45


def test_observability_settings_load_safe_deployment_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OTLP configuration should remain optional, bounded, and secret-safe."""

    monkeypatch.setenv("FINSIGHT_LOG_JSON", "false")
    monkeypatch.setenv("FINSIGHT_OTEL_SERVICE_NAME", "finsight-staging-api")
    monkeypatch.setenv("FINSIGHT_OTEL_TRACE_SAMPLE_RATIO", "0.25")
    monkeypatch.setenv(
        "FINSIGHT_OTEL_TRACES_ENDPOINT",
        "https://telemetry.example.org/otel",
    )
    monkeypatch.setenv("FINSIGHT_OTEL_EXPORT_HEADERS", "Authorization=Basic opaque")
    monkeypatch.setenv("FINSIGHT_READINESS_TIMEOUT_SECONDS", "5")

    settings = Settings()

    assert settings.log_json is False
    assert settings.otel_service_name == "finsight-staging-api"
    assert settings.otel_trace_sample_ratio == 0.25
    assert str(settings.otel_traces_endpoint) == "https://telemetry.example.org/otel"
    assert settings.otel_export_headers is not None
    assert str(settings.otel_export_headers) == "**********"
    assert settings.readiness_timeout_seconds == 5.0


@pytest.mark.parametrize("sample_ratio", ["-0.1", "1.1"])
def test_observability_settings_reject_invalid_sampling(
    monkeypatch: pytest.MonkeyPatch,
    sample_ratio: str,
) -> None:
    """Head-sampling probability must remain within the closed unit interval."""

    monkeypatch.setenv("FINSIGHT_OTEL_TRACE_SAMPLE_RATIO", sample_ratio)

    with pytest.raises(ValidationError):
        Settings()


def test_observability_settings_reject_export_when_disabled() -> None:
    """A contradictory disabled exporter configuration should fail closed."""

    with pytest.raises(ValidationError, match="cannot be configured"):
        Settings(
            observability_enabled=False,
            otel_traces_endpoint=HttpUrl("https://telemetry.example.org/otel"),
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployed_environments_require_api_authentication(
    monkeypatch: pytest.MonkeyPatch,
    environment: Literal["staging", "production"],
) -> None:
    """A deployed API must not start without its private service token."""

    monkeypatch.setenv("FINSIGHT_API_AUTH_TOKEN", "")
    monkeypatch.setenv("FINSIGHT_ENVIRONMENT", environment)

    with pytest.raises(ValidationError, match="api_auth_token is required"):
        Settings()


def test_blank_optional_observability_values_are_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checked-in example environment must parse without fake endpoints."""

    monkeypatch.setenv("FINSIGHT_OTEL_TRACES_ENDPOINT", "")
    monkeypatch.setenv("FINSIGHT_OTEL_METRICS_ENDPOINT", "   ")
    monkeypatch.setenv("FINSIGHT_OTEL_EXPORT_HEADERS", "")
    monkeypatch.setenv("FINSIGHT_API_AUTH_TOKEN", "")

    settings = Settings()

    assert settings.otel_traces_endpoint is None
    assert settings.otel_metrics_endpoint is None
    assert settings.otel_export_headers is None
    assert settings.api_auth_token is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@telemetry.example.org/otel",
        "https://telemetry.example.org/otel?token=secret",
        "https://telemetry.example.org/otel#fragment",
    ],
)
def test_observability_settings_reject_secret_bearing_endpoints(endpoint: str) -> None:
    """Exporter credentials belong in the masked header setting, never the URL."""

    with pytest.raises(ValidationError, match="cannot contain credentials"):
        Settings(otel_traces_endpoint=HttpUrl(endpoint))


def test_experiment_assignment_secret_is_loaded_and_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assignment secret should come from deployment configuration."""

    assignment_secret = "a" * 32
    monkeypatch.setenv("FINSIGHT_EXPERIMENT_ASSIGNMENT_SECRET", assignment_secret)
    settings = Settings()

    assert settings.experiment_assignment_secret.get_secret_value() == assignment_secret
    assert str(settings.experiment_assignment_secret) == "**********"


def test_settings_require_experiment_assignment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FinSight must fail closed when no assignment secret is configured."""

    monkeypatch.setenv("FINSIGHT_EXPERIMENT_ASSIGNMENT_SECRET", "")

    with pytest.raises(ValidationError, match="experiment_assignment_secret"):
        Settings()


def test_experiment_assignment_secret_requires_adequate_length() -> None:
    """Weak assignment HMAC secrets should fail settings validation."""

    with pytest.raises(ValidationError, match="at least 32"):
        Settings(experiment_assignment_secret=SecretStr("short"))


def test_settings_reject_unsupported_database_driver() -> None:
    """FinSight storage should use the configured Psycopg PostgreSQL dialect."""

    with pytest.raises(
        ValidationError,
        match="database_url must use the postgresql\\+psycopg driver",
    ):
        Settings(database_url=SecretStr("sqlite:///tmp/finsight.db"))


def test_settings_require_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FinSight must fail closed when no database URL is configured."""

    monkeypatch.setenv("FINSIGHT_DATABASE_URL", "")

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


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
    assert settings.generation_model == "gpt-5.6-luna"
    assert settings.generation_max_output_tokens == 2_000
    assert settings.generation_reasoning_effort == "low"


def test_embedding_settings_read_secret_and_batch_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployment settings should load and mask an explicit API secret."""

    monkeypatch.setenv("FINSIGHT_OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("FINSIGHT_EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("FINSIGHT_EMBEDDING_BATCH_SIZE", "32")
    monkeypatch.setenv("FINSIGHT_GENERATION_MODEL", "test-generation-model")
    monkeypatch.setenv("FINSIGHT_GENERATION_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("FINSIGHT_GENERATION_REASONING_EFFORT", "medium")

    settings = Settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-secret"
    assert str(settings.openai_api_key) == "**********"
    assert settings.embedding_model == "custom-model"
    assert settings.embedding_batch_size == 32
    assert settings.generation_model == "test-generation-model"
    assert settings.generation_max_output_tokens == 4096
    assert settings.generation_reasoning_effort == "medium"


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
        ("FINSIGHT_GENERATION_MODEL", "", "at least 1 character"),
        ("FINSIGHT_GENERATION_MAX_OUTPUT_TOKENS", "255", "greater than or equal"),
        ("FINSIGHT_GENERATION_MAX_OUTPUT_TOKENS", "20001", "less than or equal"),
        ("FINSIGHT_GENERATION_REASONING_EFFORT", "extreme", "Input should be"),
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
