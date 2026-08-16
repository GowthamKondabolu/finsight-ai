"""Tests for the durable investigation approval API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import finsight.api.main as api_module
from finsight.agents.contracts import (
    AnswerSource,
    GroundedAnswerResult,
    InvestigationQuery,
    ValidatedClaim,
)
from finsight.agents.generation import AnswerGenerationError
from finsight.agents.investigation import GroundedAnswerContractError
from finsight.agents.workflow import (
    HumanReviewDecision,
    HumanReviewRequest,
    InvestigationWorkflow,
    InvestigationWorkflowResult,
    WorkflowNotFoundError,
    WorkflowStateConflictError,
)
from finsight.api.main import create_app
from finsight.config.settings import Settings

THREAD_ID = UUID("00000000-0000-4000-8000-000000000008")
DECIDED_AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def answer_result() -> GroundedAnswerResult:
    """Return one complete answer for workflow API assertions."""

    return GroundedAnswerResult(
        question="What changed?",
        status="grounded",
        answer="Supply risk increased. [E1]",
        claims=(ValidatedClaim(statement="Supply risk increased.", citation_ids=("E1",)),),
        numerical_validations=(),
        sources=(
            AnswerSource(
                source_id="E1",
                source_type="filing_passage",
                label="10-K — Risk Factors",
                source_url="https://www.sec.gov/example",
                accession_number="0000320193-25-000079",
                form_type="10-K",
                filing_date=date(2025, 10, 31),
                section_name="Risk Factors",
                chunk_index=0,
                content_hash="a" * 64,
            ),
        ),
        limitations=(),
        model_name="generation-model",
        requires_human_review=True,
        review_reasons=("financial analysis requires qualified human review",),
    )


def workflow_result(
    status: Literal["pending_review", "approved", "rejected"],
) -> InvestigationWorkflowResult:
    """Return a workflow result at one public lifecycle state."""

    answer = answer_result()
    review_request = (
        HumanReviewRequest(
            question=answer.question,
            answer_status=answer.status,
            answer=answer.answer,
            source_ids=("E1",),
            limitations=(),
            review_reasons=answer.review_reasons,
        )
        if status == "pending_review"
        else None
    )
    decision = (
        HumanReviewDecision(
            decision="approve" if status == "approved" else "reject",
            reviewer_id="analyst@example.com",
            notes="Reviewed against filing.",
            decided_at=DECIDED_AT,
        )
        if status != "pending_review"
        else None
    )
    return InvestigationWorkflowResult(
        thread_id=THREAD_ID,
        status=status,
        answer=answer,
        review_request=review_request,
        review_decision=decision,
    )


def test_start_workflow_returns_pending_review_and_release_denial() -> None:
    """A generated answer must remain unreleased until explicit approval."""

    handler = AsyncMock(return_value=workflow_result("pending_review"))
    application = create_app(
        Settings(environment="test"),
        workflow_start_handler=handler,
    )

    with TestClient(application) as client:
        response = client.post(
            "/v1/investigations/runs",
            json={
                "thread_id": str(THREAD_ID),
                "question": "  What changed?  ",
                "cik": "320193",
                "form_types": ["10-k"],
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending_review"
    assert payload["release_authorized"] is False
    assert payload["review_request"]["source_ids"] == ["E1"]
    assert payload["review_decision"] is None
    assert handler.await_args is not None
    assert handler.await_args.args == (
        THREAD_ID,
        InvestigationQuery(
            question="What changed?",
            cik="320193",
            form_types=("10-K",),
        ),
    )


def test_review_workflow_returns_attributable_approval() -> None:
    """Only an approved terminal state should authorize answer release."""

    handler = AsyncMock(return_value=workflow_result("approved"))
    application = create_app(
        Settings(environment="test"),
        workflow_review_handler=handler,
    )

    with TestClient(application) as client:
        response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/review",
            json={
                "decision": "approve",
                "reviewer_id": " analyst@example.com ",
                "notes": " Evidence checked. ",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approved"
    assert payload["release_authorized"] is True
    assert payload["review_request"] is None
    assert payload["review_decision"]["reviewer_id"] == "analyst@example.com"
    assert payload["review_decision"]["decided_at"] == DECIDED_AT.isoformat().replace("+00:00", "Z")
    assert handler.await_args is not None
    decision = handler.await_args.args[1]
    assert decision.decision == "approve"
    assert decision.reviewer_id == "analyst@example.com"
    assert decision.notes == "Evidence checked."


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "approve", "reviewer_id": "   "},
        {"decision": "edit", "reviewer_id": "analyst"},
        {"decision": "reject", "reviewer_id": "analyst", "unexpected": True},
    ],
)
def test_review_endpoint_rejects_unsafe_decisions(payload: dict[str, object]) -> None:
    """The public contract permits only attributable approve-or-reject actions."""

    application = create_app(
        Settings(environment="test"),
        workflow_review_handler=AsyncMock(),
    )
    with TestClient(application) as client:
        response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/review",
            json=payload,
        )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (WorkflowNotFoundError("workflow thread was not found"), 404),
        (WorkflowStateConflictError("workflow is not awaiting human review"), 409),
    ],
)
def test_review_endpoint_maps_workflow_state_errors(
    error: Exception,
    expected_status: int,
) -> None:
    """Unknown and terminal threads should have distinct safe responses."""

    application = create_app(
        Settings(environment="test"),
        workflow_review_handler=AsyncMock(side_effect=error),
    )
    with TestClient(application) as client:
        response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/review",
            json={"decision": "reject", "reviewer_id": "analyst"},
        )
    assert response.status_code == expected_status
    assert response.json()["detail"] == str(error)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (WorkflowStateConflictError("workflow thread already exists"), 409),
        (ValueError("FINSIGHT_OPENAI_API_KEY is required"), 503),
        (AnswerGenerationError("no output"), 502),
        (GroundedAnswerContractError("invented citation"), 502),
    ],
)
def test_start_endpoint_maps_expected_failures(
    error: Exception,
    expected_status: int,
) -> None:
    """Known workflow and AI failures should not leak implementation details."""

    application = create_app(
        Settings(environment="test"),
        workflow_start_handler=AsyncMock(side_effect=error),
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/investigations/runs",
            json={"thread_id": str(THREAD_ID), "question": "risk?"},
        )
    assert response.status_code == expected_status


def test_start_endpoint_does_not_mask_unexpected_value_errors() -> None:
    """Unrecognized value errors remain visible to error monitoring."""

    application = create_app(
        Settings(environment="test"),
        workflow_start_handler=AsyncMock(side_effect=ValueError("unexpected")),
    )
    with TestClient(application) as client, pytest.raises(ValueError, match="unexpected"):
        client.post(
            "/v1/investigations/runs",
            json={"thread_id": str(THREAD_ID), "question": "risk?"},
        )


def test_default_workflow_endpoints_call_production_runners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uninjected API routes should use the PostgreSQL-backed production runners."""

    start = AsyncMock(return_value=workflow_result("pending_review"))
    review = AsyncMock(return_value=workflow_result("rejected"))
    monkeypatch.setattr(api_module, "run_workflow_start", start)
    monkeypatch.setattr(api_module, "run_workflow_review", review)
    settings = Settings(environment="test")
    application = create_app(settings)

    with TestClient(application) as client:
        start_response = client.post(
            "/v1/investigations/runs",
            json={"thread_id": str(THREAD_ID), "question": "risk?"},
        )
        review_response = client.post(
            f"/v1/investigations/runs/{THREAD_ID}/review",
            json={"decision": "reject", "reviewer_id": "analyst", "notes": None},
        )

    assert start_response.status_code == 201
    assert review_response.status_code == 200
    assert review_response.json()["release_authorized"] is False
    assert start.await_args is not None
    assert start.await_args.kwargs == {
        "settings": settings,
        "thread_id": THREAD_ID,
        "query": InvestigationQuery(question="risk?"),
    }
    assert review.await_args is not None
    assert review.await_args.kwargs["settings"] == settings
    assert review.await_args.kwargs["thread_id"] == THREAD_ID
    decision = review.await_args.kwargs["decision"]
    assert decision.notes is None


@pytest.mark.asyncio
async def test_production_start_runner_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production graph runner should close AI and database resources."""

    embedding = MagicMock()
    embedding.__aenter__ = AsyncMock(return_value=embedding)
    embedding.__aexit__ = AsyncMock(return_value=None)
    embedding_factory = Mock()
    embedding_factory.from_settings.return_value = embedding
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock(return_value=None)
    generator_factory = Mock()
    generator_factory.from_settings.return_value = generator
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory = Mock()
    service = AsyncMock(return_value=answer_result())
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def workflow_context(**kwargs: object) -> AsyncIterator[MagicMock]:
        captured.update(kwargs)
        workflow = MagicMock(spec=InvestigationWorkflow)

        async def start(**call: object) -> InvestigationWorkflowResult:
            executor = captured["executor"]
            assert callable(executor)
            await executor(call["query"])
            return workflow_result("pending_review")

        workflow.start = AsyncMock(side_effect=start)
        yield workflow

    monkeypatch.setattr(api_module, "OpenAIEmbeddingProvider", embedding_factory)
    monkeypatch.setattr(api_module, "OpenAIAnswerGenerator", generator_factory)
    monkeypatch.setattr(api_module, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        api_module,
        "create_session_factory",
        Mock(return_value=session_factory),
    )
    monkeypatch.setattr(api_module, "answer_investigation", service)
    monkeypatch.setattr(api_module, "postgres_investigation_workflow", workflow_context)
    query = InvestigationQuery(question="What changed?")

    result = await api_module.run_workflow_start(
        settings=MagicMock(),
        thread_id=THREAD_ID,
        query=query,
    )

    assert result == workflow_result("pending_review")
    service.assert_awaited_once_with(
        query=query,
        embedding_provider=embedding,
        answer_generator=generator,
        session_factory=session_factory,
    )
    embedding.__aexit__.assert_awaited_once()
    generator.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_review_runner_uses_only_checkpoint_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resuming approval should not allocate or call an AI provider."""

    captured: dict[str, object] = {}
    workflow = MagicMock(spec=InvestigationWorkflow)
    workflow.resume = AsyncMock(return_value=workflow_result("rejected"))

    @asynccontextmanager
    async def workflow_context(**kwargs: object) -> AsyncIterator[MagicMock]:
        captured.update(kwargs)
        yield workflow

    monkeypatch.setattr(api_module, "postgres_investigation_workflow", workflow_context)
    decision = HumanReviewDecision(
        decision="reject",
        reviewer_id="analyst",
        decided_at=DECIDED_AT,
    )

    result = await api_module.run_workflow_review(
        settings=MagicMock(),
        thread_id=THREAD_ID,
        decision=decision,
    )

    assert result == workflow_result("rejected")
    workflow.resume.assert_awaited_once_with(thread_id=THREAD_ID, decision=decision)
    executor = captured["executor"]
    assert callable(executor)
    with pytest.raises(WorkflowStateConflictError, match="restart investigation"):
        await executor(InvestigationQuery(question="must not run"))
