"""Tests for controlled-experiment assignment, telemetry, and analysis APIs."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import finsight.api.main as api_module
from finsight.api.main import create_app
from finsight.config.settings import Settings
from finsight.experiments.contracts import (
    AssignmentResult,
    ExperimentAnalysisReport,
    ExperimentContractError,
    ExperimentEventInput,
    ExperimentEventResult,
    ExperimentNotFoundError,
)

ASSIGNMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
EVENT_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 20, tzinfo=UTC)


def assignment_result() -> AssignmentResult:
    return AssignmentResult(
        assignment_id=ASSIGNMENT_ID,
        experiment_key="answer-workflow-v1",
        variant_key="treatment",
        variant_configuration={"pipeline": "verified_langgraph"},
        assigned_at=NOW,
        existing_assignment=False,
    )


def event_result() -> ExperimentEventResult:
    return ExperimentEventResult(
        event_id=EVENT_ID,
        experiment_key="answer-workflow-v1",
        assignment_id=ASSIGNMENT_ID,
        variant_key="treatment",
        event_key="exposure:123",
        event_type="exposure",
        recorded_at=NOW,
        duplicate=False,
    )


def analysis_report() -> ExperimentAnalysisReport:
    return ExperimentAnalysisReport(
        experiment_key="answer-workflow-v1",
        plan_fingerprint="a" * 64,
        status="running",
        generated_at=NOW,
        planned_sample_size_per_variant=400,
        exposed_assignments={"control": 10, "treatment": 10},
        analysis_ready=False,
        decision="collecting",
        primary_comparison=None,
        guardrail_comparisons=(),
        limitations=("No peeking.",),
    )


def test_experiment_endpoints_return_typed_safe_contracts() -> None:
    """Public APIs should expose assignments and telemetry without unit hashes."""

    assignment_handler = AsyncMock(return_value=assignment_result())
    event_handler = AsyncMock(return_value=event_result())
    analysis_handler = AsyncMock(return_value=analysis_report())
    application = create_app(
        Settings(environment="test"),
        experiment_assignment_handler=assignment_handler,
        experiment_event_handler=event_handler,
        experiment_analysis_handler=analysis_handler,
    )

    with TestClient(application) as client:
        assignment_response = client.post(
            "/v1/experiments/answer-workflow-v1/assignments",
            json={"unit_id": "private-session-id"},
        )
        event_response = client.post(
            "/v1/experiments/answer-workflow-v1/events",
            json={
                "assignment_id": str(ASSIGNMENT_ID),
                "event_key": "exposure:123",
                "event_type": "exposure",
                "occurred_at": NOW.isoformat(),
            },
        )
        analysis_response = client.get("/v1/experiments/answer-workflow-v1/analysis")

    assert assignment_response.status_code == 201
    assert assignment_response.json()["variant_key"] == "treatment"
    assert "unit_id" not in assignment_response.json()
    assert "unit_hash" not in assignment_response.json()
    assert event_response.status_code == 201
    assert event_response.json()["duplicate"] is False
    assert analysis_response.status_code == 200
    assert analysis_response.json()["decision"] == "collecting"
    assignment_handler.assert_awaited_once_with("answer-workflow-v1", "private-session-id")
    event_handler.assert_awaited_once()
    analysis_handler.assert_awaited_once_with("answer-workflow-v1")


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ExperimentNotFoundError("missing"), 404),
        (ExperimentContractError("inactive"), 409),
    ],
)
def test_experiment_endpoints_map_domain_errors(
    error: Exception,
    expected_status: int,
) -> None:
    """Missing identities and state conflicts should remain distinguishable."""

    application = create_app(
        Settings(environment="test"),
        experiment_assignment_handler=AsyncMock(side_effect=error),
        experiment_event_handler=AsyncMock(side_effect=error),
        experiment_analysis_handler=AsyncMock(side_effect=error),
    )
    with TestClient(application) as client:
        assignment_response = client.post(
            "/v1/experiments/missing/assignments",
            json={"unit_id": "session"},
        )
        event_response = client.post(
            "/v1/experiments/missing/events",
            json={
                "assignment_id": str(ASSIGNMENT_ID),
                "event_key": "exposure:123",
                "event_type": "exposure",
            },
        )
        analysis_response = client.get("/v1/experiments/missing/analysis")

    assert assignment_response.status_code == expected_status
    assert event_response.status_code == expected_status
    assert analysis_response.status_code == expected_status


@pytest.mark.parametrize(
    "payload",
    [
        {"unit_id": " "},
        {"unit_id": "session", "unexpected": True},
    ],
)
def test_assignment_endpoint_rejects_unsafe_payloads(payload: dict[str, object]) -> None:
    """Randomization identifiers should be bounded and exact before handler execution."""

    handler = AsyncMock()
    application = create_app(
        Settings(environment="test"),
        experiment_assignment_handler=handler,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/experiments/answer-workflow-v1/assignments",
            json=payload,
        )
    assert response.status_code == 422
    handler.assert_not_awaited()


def test_event_endpoint_rejects_mixed_exposure_shape() -> None:
    """Exposure markers cannot include outcome data."""

    handler = AsyncMock()
    application = create_app(
        Settings(environment="test"),
        experiment_event_handler=handler,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/experiments/answer-workflow-v1/events",
            json={
                "assignment_id": str(ASSIGNMENT_ID),
                "event_key": "exposure:123",
                "event_type": "exposure",
                "metric_name": "unsafe_rate",
                "metric_value": 1,
            },
        )
    assert response.status_code == 422
    handler.assert_not_awaited()


def test_event_endpoint_rejects_naive_timestamp() -> None:
    """Event timing must be timezone-aware before repository execution."""

    handler = AsyncMock()
    application = create_app(
        Settings(environment="test"),
        experiment_event_handler=handler,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/experiments/answer-workflow-v1/events",
            json={
                "assignment_id": str(ASSIGNMENT_ID),
                "event_key": "exposure:123",
                "event_type": "exposure",
                "occurred_at": "2026-08-20T12:00:00",
            },
        )
    assert response.status_code == 422
    handler.assert_not_awaited()


def test_default_experiment_endpoints_use_production_runners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uninjected routes should use the transaction-scoped production functions."""

    assign = AsyncMock(return_value=assignment_result())
    record = AsyncMock(return_value=event_result())
    analyze = AsyncMock(return_value=analysis_report())
    monkeypatch.setattr(api_module, "run_experiment_assignment", assign)
    monkeypatch.setattr(api_module, "run_experiment_event", record)
    monkeypatch.setattr(api_module, "run_experiment_analysis", analyze)
    settings = Settings(environment="test")
    application = create_app(settings)

    with TestClient(application) as client:
        client.post(
            "/v1/experiments/answer-workflow-v1/assignments",
            json={"unit_id": "session"},
        )
        client.post(
            "/v1/experiments/answer-workflow-v1/events",
            json={
                "assignment_id": str(ASSIGNMENT_ID),
                "event_key": "exposure:123",
                "event_type": "exposure",
            },
        )
        client.get("/v1/experiments/answer-workflow-v1/analysis")

    assign.assert_awaited_once()
    record.assert_awaited_once()
    analyze.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_experiment_runners_scope_transactions_and_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assignment, event, and analysis runners should release each database engine."""

    engine = MagicMock()
    engine.dispose = AsyncMock()
    session = Mock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    assign = AsyncMock(return_value=assignment_result())
    record = AsyncMock(return_value=event_result())
    analyze = AsyncMock(return_value=analysis_report())
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(api_module, "create_session_factory", Mock(return_value=Mock()))
    monkeypatch.setattr(api_module, "session_scope", Mock(return_value=context))
    monkeypatch.setattr(api_module, "assign_experiment_variant", assign)
    monkeypatch.setattr(api_module, "record_experiment_event", record)
    monkeypatch.setattr(api_module, "analyze_registered_experiment", analyze)
    settings = Settings(environment="test")
    event_input = ExperimentEventInput(
        assignment_id=ASSIGNMENT_ID,
        event_key="exposure:123",
        event_type="exposure",
        occurred_at=NOW,
    )

    assert (
        await api_module.run_experiment_assignment(
            settings=settings,
            experiment_key="answer-workflow-v1",
            unit_id="session",
        )
    ).variant_key == "treatment"
    assert (
        await api_module.run_experiment_event(
            settings=settings,
            experiment_key="answer-workflow-v1",
            event_input=event_input,
        )
    ).event_id == EVENT_ID
    assert (
        await api_module.run_experiment_analysis(
            settings=settings,
            experiment_key="answer-workflow-v1",
        )
    ).decision == "collecting"
    assert engine.dispose.await_count == 3
