"""Tests for durable LangGraph investigation approval orchestration."""

from datetime import UTC, date, datetime
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

import finsight.agents.workflow as workflow_module
from finsight.agents.contracts import (
    AnswerSource,
    GroundedAnswerResult,
    InvestigationQuery,
    ValidatedClaim,
)
from finsight.agents.workflow import (
    HumanReviewDecision,
    InvestigationWorkflow,
    WorkflowNotFoundError,
    WorkflowStateConflictError,
)
from finsight.config.settings import Settings

THREAD_ID = UUID("00000000-0000-4000-8000-000000000008")
DECIDED_AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def answer_result() -> GroundedAnswerResult:
    """Return a citation-grounded answer that still requires human review."""

    return GroundedAnswerResult(
        question="What material risk changed?",
        status="grounded",
        answer="Supply risk increased. [E1]",
        claims=(
            ValidatedClaim(
                statement="Supply risk increased.",
                citation_ids=("E1",),
            ),
        ),
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
        limitations=("Only one annual filing was reviewed.",),
        model_name="generation-model",
        requires_human_review=True,
        review_reasons=("financial analysis requires qualified human review",),
    )


@pytest.mark.asyncio
async def test_workflow_pauses_with_bounded_review_packet() -> None:
    """Starting a graph should persist the answer and stop at human review."""

    executor = AsyncMock(return_value=answer_result())
    workflow = InvestigationWorkflow(
        executor=executor,
        checkpointer=InMemorySaver(),
    )
    query = InvestigationQuery(question="What material risk changed?")

    result = await workflow.start(thread_id=THREAD_ID, query=query)

    assert result.status == "pending_review"
    assert result.answer == answer_result()
    assert result.review_decision is None
    assert result.review_request is not None
    assert result.review_request.source_ids == ("E1",)
    assert result.review_request.proposed_action == "release_answer"
    executor.assert_awaited_once_with(query)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision_value", "expected_status"),
    [("approve", "approved"), ("reject", "rejected")],
)
async def test_workflow_resumes_without_repeating_investigation(
    decision_value: Literal["approve", "reject"],
    expected_status: str,
) -> None:
    """A reviewer decision should resume the paused node exactly once."""

    executor = AsyncMock(return_value=answer_result())
    workflow = InvestigationWorkflow(
        executor=executor,
        checkpointer=InMemorySaver(),
    )
    await workflow.start(
        thread_id=THREAD_ID,
        query=InvestigationQuery(question="What material risk changed?"),
    )
    decision = HumanReviewDecision(
        decision=decision_value,
        reviewer_id="analyst@example.com",
        notes="Evidence reviewed against the filing.",
        decided_at=DECIDED_AT,
    )

    result = await workflow.resume(thread_id=THREAD_ID, decision=decision)

    assert result.status == expected_status
    assert result.review_request is None
    assert result.review_decision == decision
    executor.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_rejects_duplicate_or_completed_transitions() -> None:
    """Thread identifiers and terminal reviewer decisions are single-use."""

    workflow = InvestigationWorkflow(
        executor=AsyncMock(return_value=answer_result()),
        checkpointer=InMemorySaver(),
    )
    query = InvestigationQuery(question="What changed?")
    await workflow.start(thread_id=THREAD_ID, query=query)

    with pytest.raises(WorkflowStateConflictError, match="already exists"):
        await workflow.start(thread_id=THREAD_ID, query=query)

    decision = HumanReviewDecision(
        decision="approve",
        reviewer_id="analyst-42",
        decided_at=DECIDED_AT,
    )
    await workflow.resume(thread_id=THREAD_ID, decision=decision)
    with pytest.raises(WorkflowStateConflictError, match="not awaiting"):
        await workflow.resume(thread_id=THREAD_ID, decision=decision)


@pytest.mark.asyncio
async def test_workflow_rejects_unknown_thread() -> None:
    """A review must never create or implicitly approve an unknown run."""

    workflow = InvestigationWorkflow(
        executor=AsyncMock(return_value=answer_result()),
        checkpointer=InMemorySaver(),
    )
    decision = HumanReviewDecision(
        decision="reject",
        reviewer_id="analyst-42",
        decided_at=DECIDED_AT,
    )

    with pytest.raises(WorkflowNotFoundError, match="not found"):
        await workflow.resume(thread_id=THREAD_ID, decision=decision)


@pytest.mark.parametrize(
    "values",
    [
        {"decision": "approve", "reviewer_id": "   ", "decided_at": DECIDED_AT},
        {
            "decision": "approve",
            "reviewer_id": "analyst",
            "decided_at": DECIDED_AT.replace(tzinfo=None),
        },
    ],
)
def test_review_decision_requires_identity_and_timezone(values: dict[str, object]) -> None:
    """Persisted approval records must be attributable and time-zone aware."""

    with pytest.raises(ValidationError):
        HumanReviewDecision.model_validate(values)


def test_review_decision_normalizes_optional_notes() -> None:
    """Blank notes should not become misleading audit text."""

    decision = HumanReviewDecision(
        decision="reject",
        reviewer_id=" analyst-42 ",
        notes="   ",
        decided_at=DECIDED_AT,
    )

    assert decision.reviewer_id == "analyst-42"
    assert decision.notes is None


@pytest.mark.asyncio
async def test_postgres_workflow_configures_and_initializes_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production workflows should adapt the DB URL and set up checkpoint tables."""

    checkpointer = MagicMock()
    checkpointer.setup = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=checkpointer)
    context.__aexit__ = AsyncMock(return_value=None)
    saver = Mock()
    saver.from_conn_string.return_value = context
    compiled_workflow = Mock()
    workflow_factory = Mock(return_value=compiled_workflow)
    monkeypatch.setattr(workflow_module, "AsyncPostgresSaver", saver)
    monkeypatch.setattr(workflow_module, "InvestigationWorkflow", workflow_factory)
    executor = AsyncMock(return_value=answer_result())

    settings = Settings(environment="test")

    async with workflow_module.postgres_investigation_workflow(
        settings=settings,
        executor=executor,
    ) as workflow:
        assert workflow is compiled_workflow

    saver.from_conn_string.assert_called_once_with(
        settings.database_url.get_secret_value().replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )
    )
    checkpointer.setup.assert_awaited_once()
    workflow_factory.assert_called_once_with(
        executor=executor,
        checkpointer=checkpointer,
    )
