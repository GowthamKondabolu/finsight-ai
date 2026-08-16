"""Tests for structured logs, request telemetry, and OTLP-safe configuration."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from finsight.api.main import create_app
from finsight.config.settings import Settings
from finsight.observability.logging import REDACTED, redact_sensitive_values
from finsight.observability.middleware import RequestObservabilityMiddleware, resolve_request_id
from finsight.observability.runtime import (
    ObservabilityRuntime,
    create_observability_runtime,
    operation_span,
)


def test_log_redaction_recurses_without_changing_safe_values() -> None:
    """Credentials must be removed before either JSON or console rendering."""

    event = redact_sensitive_values(
        None,
        "info",
        {
            "event": "configuration.loaded",
            "database_url": "postgresql://user:password@db/name",
            "nested": {
                "authorization": "Basic secret",
                "safe": ["value", {"api_key": "provider-secret"}],
            },
        },
    )

    assert event["event"] == "configuration.loaded"
    assert event["database_url"] == REDACTED
    assert event["nested"] == {
        "authorization": REDACTED,
        "safe": ["value", {"api_key": REDACTED}],
    }
    assert "provider-secret" not in json.dumps(event)


@pytest.mark.parametrize(
    ("candidate", "preserved"),
    [
        ("request-12345678", True),
        ("short", False),
        ("bad header value!", False),
        (None, False),
    ],
)
def test_request_id_validation(candidate: str | None, preserved: bool) -> None:
    """Untrusted correlation headers must never enter logs without validation."""

    resolved = resolve_request_id(candidate)

    assert (resolved == candidate) is preserved
    assert len(resolved) >= 8


@contextmanager
def telemetry_runtime() -> Iterator[tuple[ObservabilityRuntime, InMemorySpanExporter]]:
    """Provide an isolated in-memory trace runtime for deterministic tests."""

    exporter = InMemorySpanExporter()
    runtime = create_observability_runtime(
        Settings(environment="test", log_json=False),
        span_exporter=exporter,
    )
    try:
        yield runtime, exporter
    finally:
        runtime.shutdown()


def test_http_middleware_emits_correlated_low_cardinality_span() -> None:
    """One request should produce a route span without query-string content."""

    with telemetry_runtime() as (runtime, exporter):
        application = create_app(
            Settings(environment="test", log_json=False),
            observability_runtime=runtime,
        )
        with TestClient(application) as client:
            response = client.get(
                "/health?token=must-not-appear",
                headers={"X-Request-ID": "request-12345678"},
            )
        runtime.force_flush()

        spans = exporter.get_finished_spans()

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-12345678"
    assert [span.name for span in spans] == ["GET /health"]
    attributes = dict(spans[0].attributes or {})
    assert attributes["http.route"] == "/health"
    assert attributes["http.response.status_code"] == 200
    assert "must-not-appear" not in json.dumps(attributes)


def test_nested_operation_span_uses_active_request_runtime() -> None:
    """Domain and GenAI spans should attach to the active telemetry runtime."""

    with telemetry_runtime() as (runtime, exporter):
        token = runtime.activate()
        try:
            with operation_span("finsight.test.operation", {"safe.attribute": "value"}):
                pass
        finally:
            runtime.deactivate(token)
        runtime.force_flush()

        spans = exporter.get_finished_spans()

    assert [span.name for span in spans] == ["finsight.test.operation"]
    attributes = dict(spans[0].attributes or {})
    assert attributes["safe.attribute"] == "value"


def test_failed_request_telemetry_omits_exception_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure diagnostics may classify an error but must not copy its message."""

    secret = "database-password=must-not-appear"
    with telemetry_runtime() as (runtime, exporter):
        application = FastAPI()

        @application.get("/failure")
        async def fail() -> None:
            raise RuntimeError(secret)

        application.add_middleware(RequestObservabilityMiddleware, runtime=runtime)
        with TestClient(application, raise_server_exceptions=False) as client:
            response = client.get("/failure")
        runtime.force_flush()
        spans = exporter.get_finished_spans()

    captured = capsys.readouterr().out
    attributes = dict(spans[0].attributes or {})
    assert response.status_code == 500
    assert attributes["error.type"] == "RuntimeError"
    assert secret not in captured
    assert secret not in json.dumps(attributes)


def test_disabled_observability_uses_noop_providers() -> None:
    """Disabling telemetry must not create exporters or break application code."""

    runtime = create_observability_runtime(
        Settings(environment="test", observability_enabled=False)
    )

    assert runtime.tracer_provider is None
    assert runtime.meter_provider is None
    assert runtime.force_flush()
    runtime.shutdown()
