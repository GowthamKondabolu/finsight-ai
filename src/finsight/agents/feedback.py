"""Validated, idempotent analyst feedback for reviewed investigations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finsight.storage.models import InvestigationFeedback

FeedbackTag = Literal[
    "citation_gap",
    "numerical_issue",
    "missing_context",
    "clear_and_complete",
]


class InvestigationFeedbackInput(BaseModel):
    """Bounded analyst feedback supplied after a human review decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
    rating: Literal["helpful", "not_helpful"]
    evidence_quality: int = Field(ge=1, le=5)
    tags: list[FeedbackTag] = Field(default_factory=list, max_length=4)
    comment: str | None = Field(default=None, max_length=2_000)

    @field_validator("tags")
    @classmethod
    def require_unique_tags(cls, values: list[FeedbackTag]) -> list[FeedbackTag]:
        """Reject duplicated feedback labels."""

        if len(set(values)) != len(values):
            raise ValueError("feedback tags cannot contain duplicates")
        return values

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        """Normalize an optional comment without storing blank text."""

        if value is None:
            return None
        candidate = value.strip()
        return candidate or None

    @model_validator(mode="after")
    def validate_tag_semantics(self) -> Self:
        """Prevent contradictory positive and problem-oriented labels."""

        if "clear_and_complete" in self.tags and len(self.tags) > 1:
            raise ValueError("clear_and_complete cannot be combined with issue tags")
        return self


class InvestigationFeedbackResult(BaseModel):
    """Persisted feedback identity and idempotency state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: UUID
    thread_id: UUID
    feedback_key: str
    rating: Literal["helpful", "not_helpful"]
    evidence_quality: int
    tags: list[FeedbackTag]
    comment: str | None
    recorded_at: datetime
    duplicate: bool


class InvestigationFeedbackConflictError(RuntimeError):
    """Raised when an idempotency key is reused with different feedback."""


def _result(row: InvestigationFeedback, *, duplicate: bool) -> InvestigationFeedbackResult:
    return InvestigationFeedbackResult(
        feedback_id=row.id,
        thread_id=row.thread_id,
        feedback_key=row.feedback_key,
        rating=cast(Literal["helpful", "not_helpful"], row.rating),
        evidence_quality=row.evidence_quality,
        tags=cast(list[FeedbackTag], row.tags),
        comment=row.comment,
        recorded_at=row.created_at,
        duplicate=duplicate,
    )


async def record_investigation_feedback(
    session: AsyncSession,
    *,
    thread_id: UUID,
    feedback: InvestigationFeedbackInput,
) -> InvestigationFeedbackResult:
    """Persist feedback once and reject conflicting idempotent retries."""

    statement = select(InvestigationFeedback).where(
        InvestigationFeedback.thread_id == thread_id,
        InvestigationFeedback.feedback_key == feedback.feedback_key,
    )
    existing = (await session.execute(statement)).scalar_one_or_none()
    if existing is not None:
        incoming = feedback.model_dump()
        persisted = {
            "feedback_key": existing.feedback_key,
            "rating": existing.rating,
            "evidence_quality": existing.evidence_quality,
            "tags": existing.tags,
            "comment": existing.comment,
        }
        if incoming != persisted:
            raise InvestigationFeedbackConflictError(
                "feedback_key cannot be reused with different feedback"
            )
        return _result(existing, duplicate=True)

    row = InvestigationFeedback(
        thread_id=thread_id,
        feedback_key=feedback.feedback_key,
        rating=feedback.rating,
        evidence_quality=feedback.evidence_quality,
        tags=list(feedback.tags),
        comment=feedback.comment,
    )
    session.add(row)
    await session.flush()
    return _result(row, duplicate=False)
