"""Security tests for the private API authentication boundary."""

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.main import create_app
from finsight.config.settings import Settings

AUTH_TOKEN = "a" * 32


def authenticated_app() -> FastAPI:
    """Create an isolated API that requires a deployment bearer token."""

    return create_app(
        Settings(
            environment="test",
            observability_enabled=False,
            api_auth_token=SecretStr(AUTH_TOKEN),
        ),
        retrieval_handler=AsyncMock(return_value=[]),
    )


def test_versioned_routes_reject_missing_and_incorrect_tokens() -> None:
    """Anonymous callers must not reach any versioned application handler."""

    application = authenticated_app()
    payload = {"query": "material risks", "top_k": 1, "candidate_k": 1}
    with TestClient(application) as client:
        missing = client.post("/v1/retrieval/search", json=payload)
        incorrect = client.post(
            "/v1/retrieval/search",
            json=payload,
            headers={"Authorization": f"Bearer {'b' * 32}"},
        )

    assert missing.status_code == 401
    assert incorrect.status_code == 401
    assert missing.json() == {"detail": "authentication required"}
    assert missing.headers["www-authenticate"] == "Bearer"


def test_correct_token_reaches_handler_while_health_remains_public() -> None:
    """The server proxy token should unlock `/v1` without hiding liveness."""

    application = authenticated_app()
    payload = {"query": "material risks", "top_k": 1, "candidate_k": 1}
    with TestClient(application) as client:
        authorized = client.post(
            "/v1/retrieval/search",
            json=payload,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        health = client.get("/health")

    assert authorized.status_code == 200
    assert authorized.json() == {"query": "material risks", "count": 0, "results": []}
    assert health.status_code == 200


def test_production_disables_interactive_schema_endpoints() -> None:
    """Production should not publish discovery surfaces for the private API."""

    application = create_app(
        Settings(
            environment="production",
            observability_enabled=False,
            api_auth_token=SecretStr(AUTH_TOKEN),
        )
    )
    with TestClient(application) as client:
        docs = client.get("/docs")
        schema = client.get("/openapi.json")

    assert docs.status_code == 404
    assert schema.status_code == 404
