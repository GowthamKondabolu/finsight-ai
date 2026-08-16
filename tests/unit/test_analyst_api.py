"""Tests for analyst workflow restoration and post-review feedback APIs."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from finsight.agents.feedback import (
    InvestigationFeedbackConflictError,
    InvestigationFeedbackInput,
    InvestigationFeedbackResult,
)
from finsight.agents.workflow import WorkflowNotFoundError, WorkflowStateConflictError
from finsight.api.main import create_app
from finsight.config.settings import Settings
from tests.unit.test_workflow_api import THREAD_ID, workflow_result

FEEDBACK_ID = UUID("99999999-9999-4999-8999-999999999999")
RECORDED_AT = datetime(2026, 8, 16, 18, 0, tzinfo=UTC)


def feedback_result(*, duplicate: bool = False) -> InvestigationFeedbackResult:
    """Return one persisted feedback response for endpoint assertions."""

    return InvestigationFeedbackResult(
        feedback_id=FEEDBACK_ID,
        thread_id=THREAD_ID,
        feedback_key="web:review-1",
        rating="helpful",
        evidence_quality=5,
        tags=["clear_and_complete"],
        comment="Evidence checked.",
        recorded_at=RECORDED_AT,
        duplicate=duplicate,
    )


def test_get_workflow_restores_persisted_investigation() -> None:
    """The analyst app can restore one durable review state by thread ID."""

    handler = AsyncMock(return_value=workflow_result("approved"))
    application = create_app(
        Settings(environment="test"),
        workflow_get_handler=handler,
    )

    with TestClient(application) as client:
        response = client.get(f"/v1/investigations/runs/{THREAD_ID}")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["release_authorized"] is True
    handler.assert_awaited_once_with(THREAD_ID)


def test_get_workflow_maps_missing_thread_to_not_found() -> None:
    """An unknown thread should produce a bounded 404 response."""

    application = create_app(
        Settings(environment="test"),
        workflow_get_handler=AsyncMock(
            side_effect=WorkflowNotFoundError("workflow thread was not found")
        ),
    )

    with TestClient(application) as client:
        response = client.get(f"/v1/investigations/runs/{THREAD_ID}")

    assert response.status_code == 404
    assert response.json()["detail"] == "workflow thread was not found"


def test_feedback_endpoint_persists_normalized_post_review_signal() -> None:
    """Feedback is attributable to a reviewed thread and idempotency key."""

    handler = AsyncMock(return_value=feedback_result())
    application = create_app(
        Settings(environment="test"),
        feedback_handler=handler,
    )

    with TestClient(application) as client:
        response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/feedback",
            json={
                "feedback_key": "web:review-1",
                "rating": "helpful",
                "evidence_quality": 5,
                "tags": ["clear_and_complete"],
                "comment": " Evidence checked. ",
            },
        )

    assert response.status_code == 201
    assert response.json()["duplicate"] is False
    assert response.json()["recorded_at"] == RECORDED_AT.isoformat().replace("+00:00", "Z")
    handler.assert_awaited_once_with(
        THREAD_ID,
        InvestigationFeedbackInput(
            feedback_key="web:review-1",
            rating="helpful",
            evidence_quality=5,
            tags=["clear_and_complete"],
            comment="Evidence checked.",
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "feedback_key": "web:review-1",
            "rating": "helpful",
            "evidence_quality": 5,
            "tags": ["citation_gap", "clear_and_complete"],
        },
        {
            "feedback_key": "web:review-1",
            "rating": "helpful",
            "evidence_quality": 5,
            "tags": ["citation_gap", "citation_gap"],
        },
    ],
)
def test_feedback_endpoint_rejects_contradictory_tags(payload: dict[str, object]) -> None:
    """Invalid analyst feedback should fail before reaching persistence."""

    handler = AsyncMock()
    application = create_app(
        Settings(environment="test"),
        feedback_handler=handler,
    )

    with TestClient(application) as client:
        response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/feedback",
            json=payload,
        )

    assert response.status_code == 422
    handler.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        WorkflowStateConflictError("feedback requires a completed human review"),
        InvestigationFeedbackConflictError("feedback_key cannot be reused with different feedback"),
    ],
)
def test_feedback_endpoint_maps_state_conflicts(error: Exception) -> None:
    """Unsafe feedback transitions should have explicit conflict responses."""

    application = create_app(
        Settings(environment="test"),
        feedback_handler=AsyncMock(side_effect=error),
    )

    with TestClient(application) as client:
        response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/feedback",
            json={
                "feedback_key": "web:review-1",
                "rating": "helpful",
                "evidence_quality": 5,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == str(error)
