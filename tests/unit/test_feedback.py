"""Tests for bounded, idempotent analyst feedback."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.agents.feedback import (
    InvestigationFeedbackConflictError,
    InvestigationFeedbackInput,
    record_investigation_feedback,
)
from finsight.storage.models import InvestigationFeedback

THREAD_ID = UUID("11111111-1111-4111-8111-111111111111")
FEEDBACK_ID = UUID("22222222-2222-4222-8222-222222222222")
RECORDED_AT = datetime(2026, 8, 16, 18, 0, tzinfo=UTC)


def session_with(existing: InvestigationFeedback | None = None) -> AsyncSession:
    """Return an async session whose select resolves to one optional row."""

    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute.return_value = result
    return cast(AsyncSession, session)


def feedback_input() -> InvestigationFeedbackInput:
    """Return one valid feedback command."""

    return InvestigationFeedbackInput(
        feedback_key="web:review-1",
        rating="helpful",
        evidence_quality=5,
        tags=["clear_and_complete"],
        comment=" Evidence was complete. ",
    )


@pytest.mark.parametrize(
    "values",
    [
        {"feedback_key": "bad key", "rating": "helpful", "evidence_quality": 5},
        {"feedback_key": "web:key", "rating": "helpful", "evidence_quality": 0},
        {
            "feedback_key": "web:key",
            "rating": "helpful",
            "evidence_quality": 5,
            "tags": ["citation_gap", "clear_and_complete"],
        },
    ],
)
def test_feedback_contract_rejects_unsafe_shapes(values: dict[str, object]) -> None:
    """Feedback must remain bounded, internally consistent, and retry-safe."""

    with pytest.raises(ValidationError):
        InvestigationFeedbackInput.model_validate(values)


@pytest.mark.asyncio
async def test_feedback_repository_creates_one_normalized_record() -> None:
    """A new feedback key should create one non-identifying database row."""

    session = session_with()
    add_mock = cast(Any, session.add)
    flush_mock = cast(Any, session.flush)

    async def assign_database_fields() -> None:
        row = add_mock.call_args.args[0]
        row.id = FEEDBACK_ID
        row.created_at = RECORDED_AT

    flush_mock.side_effect = assign_database_fields
    result = await record_investigation_feedback(
        session,
        thread_id=THREAD_ID,
        feedback=feedback_input(),
    )

    assert result.feedback_id == FEEDBACK_ID
    assert result.comment == "Evidence was complete."
    assert result.duplicate is False
    add_mock.assert_called_once()


@pytest.mark.asyncio
async def test_feedback_repository_returns_exact_duplicate() -> None:
    """An exact idempotent retry should return the persisted record."""

    existing = InvestigationFeedback(
        thread_id=THREAD_ID,
        feedback_key="web:review-1",
        rating="helpful",
        evidence_quality=5,
        tags=["clear_and_complete"],
        comment="Evidence was complete.",
    )
    existing.id = FEEDBACK_ID
    existing.created_at = RECORDED_AT
    result = await record_investigation_feedback(
        session_with(existing),
        thread_id=THREAD_ID,
        feedback=feedback_input(),
    )

    assert result.duplicate is True
    assert result.recorded_at == RECORDED_AT


@pytest.mark.asyncio
async def test_feedback_repository_rejects_conflicting_retry() -> None:
    """A feedback key cannot silently overwrite an earlier assessment."""

    existing = InvestigationFeedback(
        thread_id=THREAD_ID,
        feedback_key="web:review-1",
        rating="not_helpful",
        evidence_quality=2,
        tags=["citation_gap"],
        comment=None,
    )
    existing.id = FEEDBACK_ID
    existing.created_at = RECORDED_AT

    with pytest.raises(InvestigationFeedbackConflictError, match="cannot be reused"):
        await record_investigation_feedback(
            session_with(existing),
            thread_id=THREAD_ID,
            feedback=feedback_input(),
        )
