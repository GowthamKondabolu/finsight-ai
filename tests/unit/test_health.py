"""Tests for operational API endpoints."""

from fastapi.testclient import TestClient

from finsight.api.main import create_app
from finsight.config.settings import Settings


def test_health_endpoint_returns_service_metadata() -> None:
    """The health endpoint should expose stable service metadata."""

    application = create_app(Settings(environment="test"))

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "FinSight AI",
        "version": "0.1.0",
        "environment": "test",
    }
