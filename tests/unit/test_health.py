"""Tests for operational API endpoints."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import finsight.api.main as api_module
from finsight.api.main import create_app, run_readiness_check
from finsight.config.settings import Settings


def test_health_endpoint_returns_service_metadata() -> None:
    """The health endpoint should expose stable service metadata."""

    application = create_app(Settings(environment="test", observability_enabled=False))

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "FinSight AI",
        "version": "0.1.0",
        "environment": "test",
    }
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_readiness_endpoint_reports_database_state_without_leaking_errors() -> None:
    """Readiness should distinguish healthy dependencies from safe 503 responses."""

    healthy = AsyncMock()
    application = create_app(
        Settings(environment="test", observability_enabled=False),
        readiness_handler=healthy,
    )
    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "FinSight AI",
        "version": "0.1.0",
        "environment": "test",
        "checks": {"database": "ok"},
    }
    healthy.assert_awaited_once_with()

    unavailable = AsyncMock(side_effect=RuntimeError("database-password=secret"))
    application = create_app(
        Settings(environment="test", observability_enabled=False),
        readiness_handler=unavailable,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "service dependencies are unavailable"}
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_runtime_readiness_check_disposes_its_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production readiness probe must always release its short-lived engine."""

    engine = Mock()
    engine.dispose = AsyncMock()
    check = AsyncMock()
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(api_module, "check_database_connection", check)

    await run_readiness_check(settings=Settings(environment="test", observability_enabled=False))

    check.assert_awaited_once_with(engine)
    engine.dispose.assert_awaited_once_with()
