"""API response schemas."""

import math
from datetime import UTC, date, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HealthResponse(BaseModel):
    """Health-check response contract."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ExperimentAssignmentRequest(BaseModel):
    """Stable randomization identity that is HMACed before persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(min_length=1, max_length=500)

    @field_validator("unit_id")
    @classmethod
    def normalize_unit_id(cls, value: str) -> str:
        """Reject whitespace-only randomization identifiers."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("unit_id cannot be blank")
        return candidate


class ExperimentAssignmentResponse(BaseModel):
    """Sticky assignment response without the raw or hashed unit identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: UUID
    experiment_key: str
    variant_key: str
    variant_configuration: dict[str, object]
    assigned_at: datetime
    existing_assignment: bool


class ExperimentEventRequest(BaseModel):
    """Idempotent exposure or registered metric observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: UUID
    event_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
    event_type: Literal["exposure", "outcome"]
    metric_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    metric_value: float | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_event_timezone(cls, value: datetime) -> datetime:
        """Require an unambiguous experiment event timestamp."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_event_shape(self) -> Self:
        """Keep public telemetry aligned with the experiment domain contract."""

        if self.event_type == "exposure":
            if self.metric_name is not None or self.metric_value is not None:
                raise ValueError("exposure events cannot contain a metric")
        elif self.metric_name is None or self.metric_value is None:
            raise ValueError("outcome events require metric_name and metric_value")
        if self.metric_value is not None and not math.isfinite(self.metric_value):
            raise ValueError("metric_value must be finite")
        return self


class ExperimentEventResponse(BaseModel):
    """Persisted event identity and idempotency result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    experiment_key: str
    assignment_id: UUID
    variant_key: str
    event_key: str
    event_type: Literal["exposure", "outcome"]
    recorded_at: datetime
    duplicate: bool


class RetrievalSearchRequest(BaseModel):
    """Bounded hybrid retrieval request with optional SEC metadata filters."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(default=10, ge=1, le=50)
    candidate_k: int = Field(default=50, ge=1, le=200)
    cik: str | None = Field(default=None, pattern=r"^\d{1,10}$")
    form_types: list[str] = Field(default_factory=list, max_length=20)
    filed_from: date | None = None
    filed_to: date | None = None
    section_names: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        """Trim the query and reject whitespace-only requests."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("query cannot be blank")
        return candidate

    @field_validator("form_types")
    @classmethod
    def normalize_form_types(cls, values: list[str]) -> list[str]:
        """Normalize SEC forms and reject ambiguous duplicates."""

        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("form_types cannot contain blank or duplicate values")
        return normalized

    @field_validator("section_names")
    @classmethod
    def normalize_section_names(cls, values: list[str]) -> list[str]:
        """Trim exact section filters and reject duplicates."""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("section_names cannot contain blank or duplicate values")
        return normalized

    @model_validator(mode="after")
    def validate_search_bounds(self) -> Self:
        """Reject contradictory ranking and date constraints."""

        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k")
        if (
            self.filed_from is not None
            and self.filed_to is not None
            and self.filed_from > self.filed_to
        ):
            raise ValueError("filed_from cannot be after filed_to")
        return self


class RetrievalCitationResponse(BaseModel):
    """Auditable filing location for one returned passage."""

    model_config = ConfigDict(frozen=True)

    company_name: str
    cik: str
    ticker: str | None
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    section_name: str
    section_sequence: int
    chunk_index: int
    source_url: str


class RetrievalResultResponse(BaseModel):
    """Fused retrieval result with transparent channel diagnostics."""

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID
    content: str
    content_hash: str
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    keyword_score: float | None
    semantic_score: float | None
    matched_by: list[Literal["keyword", "semantic"]]
    citation: RetrievalCitationResponse
    chunk_metadata: dict[str, object]


class RetrievalSearchResponse(BaseModel):
    """Hybrid retrieval response envelope."""

    model_config = ConfigDict(frozen=True)

    query: str
    count: int
    results: list[RetrievalResultResponse]


class InvestigationAnswerRequest(BaseModel):
    """Bounded question, retrieval filters, and exact fact selection controls."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(default=8, ge=1, le=20)
    candidate_k: int = Field(default=50, ge=1, le=200)
    cik: str | None = Field(default=None, pattern=r"^\d{1,10}$")
    form_types: list[str] = Field(default_factory=list, max_length=20)
    filed_from: date | None = None
    filed_to: date | None = None
    section_names: list[str] = Field(default_factory=list, max_length=20)
    fact_concepts: list[str] = Field(default_factory=list, max_length=30)
    fact_limit: int = Field(default=30, ge=0, le=100)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        """Trim the question and reject whitespace-only requests."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("question cannot be blank")
        return candidate

    @field_validator("form_types")
    @classmethod
    def normalize_answer_form_types(cls, values: list[str]) -> list[str]:
        """Normalize SEC form filters and reject duplicates."""

        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("form_types cannot contain blank or duplicate values")
        return normalized

    @field_validator("section_names", "fact_concepts")
    @classmethod
    def normalize_answer_exact_filters(cls, values: list[str]) -> list[str]:
        """Trim exact filters and reject blanks or duplicates."""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("exact filters cannot contain blank or duplicate values")
        return normalized

    @model_validator(mode="after")
    def validate_answer_bounds(self) -> Self:
        """Reject contradictory retrieval, date, and fact constraints."""

        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k")
        if (
            self.filed_from is not None
            and self.filed_to is not None
            and self.filed_from > self.filed_to
        ):
            raise ValueError("filed_from cannot be after filed_to")
        if self.fact_concepts and self.cik is None:
            raise ValueError("fact_concepts require a company CIK")
        return self


class AnswerClaimResponse(BaseModel):
    """One citation-validated narrative claim."""

    model_config = ConfigDict(frozen=True)

    statement: str
    citation_ids: list[str]


class NumericalValidationResponse(BaseModel):
    """One deterministic arithmetic validation result."""

    model_config = ConfigDict(frozen=True)

    statement: str
    operation: str
    fact_ids: list[str]
    reported_value: str
    expected_value: str | None
    reported_unit: str
    expected_unit: str | None
    passed: bool
    message: str


class AnswerSourceResponse(BaseModel):
    """Citation metadata for one filing passage or exact SEC fact."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    source_type: Literal["filing_passage", "financial_fact"]
    label: str
    source_url: str
    accession_number: str
    form_type: str
    filing_date: date | None
    section_name: str | None
    chunk_index: int | None
    content_hash: str | None
    fact_concept: str | None
    fact_value: str | None
    fact_unit: str | None
    fact_end_date: date | None


class InvestigationAnswerResponse(BaseModel):
    """Citation-grounded answer with deterministic validation and review state."""

    model_config = ConfigDict(frozen=True)

    question: str
    status: Literal["grounded", "insufficient_evidence", "needs_review"]
    answer: str
    claims: list[AnswerClaimResponse]
    numerical_validations: list[NumericalValidationResponse]
    sources: list[AnswerSourceResponse]
    limitations: list[str]
    model_name: str | None
    requires_human_review: bool
    review_reasons: list[str]


class InvestigationWorkflowStartRequest(InvestigationAnswerRequest):
    """Start a durable investigation under a client-visible thread identifier."""

    thread_id: UUID = Field(default_factory=uuid4)


class HumanReviewDecisionRequest(BaseModel):
    """Explicit approve-or-reject action from an attributable reviewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve", "reject"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2_000)

    @field_validator("reviewer_id")
    @classmethod
    def normalize_reviewer_id(cls, value: str) -> str:
        """Reject whitespace-only reviewer identities."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("reviewer_id cannot be blank")
        return candidate

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        """Normalize optional audit notes."""

        if value is None:
            return None
        candidate = value.strip()
        return candidate or None


class InvestigationFeedbackRequest(BaseModel):
    """Bounded, idempotent feedback captured after human review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
    rating: Literal["helpful", "not_helpful"]
    evidence_quality: int = Field(ge=1, le=5)
    tags: list[
        Literal[
            "citation_gap",
            "numerical_issue",
            "missing_context",
            "clear_and_complete",
        ]
    ] = Field(default_factory=list, max_length=4)
    comment: str | None = Field(default=None, max_length=2_000)

    @field_validator("tags")
    @classmethod
    def require_unique_feedback_tags(cls, values: list[str]) -> list[str]:
        """Reject duplicate feedback labels."""

        if len(set(values)) != len(values):
            raise ValueError("feedback tags cannot contain duplicates")
        return values

    @field_validator("comment")
    @classmethod
    def normalize_feedback_comment(cls, value: str | None) -> str | None:
        """Normalize optional comments without accepting blank text."""

        if value is None:
            return None
        candidate = value.strip()
        return candidate or None

    @model_validator(mode="after")
    def validate_feedback_tag_semantics(self) -> Self:
        """Keep positive feedback distinct from issue-oriented labels."""

        if "clear_and_complete" in self.tags and len(self.tags) > 1:
            raise ValueError("clear_and_complete cannot be combined with issue tags")
        return self


class InvestigationFeedbackResponse(BaseModel):
    """Persisted analyst feedback without reviewer identity or sensitive data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: UUID
    thread_id: UUID
    feedback_key: str
    rating: Literal["helpful", "not_helpful"]
    evidence_quality: int
    tags: list[str]
    comment: str | None
    recorded_at: datetime
    duplicate: bool


class HumanReviewRequestResponse(BaseModel):
    """Evidence summary returned while the graph awaits a reviewer."""

    model_config = ConfigDict(frozen=True)

    question: str
    answer_status: Literal["grounded", "insufficient_evidence", "needs_review"]
    answer: str
    source_ids: list[str]
    limitations: list[str]
    review_reasons: list[str]
    proposed_action: Literal["release_answer"]


class HumanReviewDecisionResponse(BaseModel):
    """Persisted human decision and audit metadata."""

    model_config = ConfigDict(frozen=True)

    decision: Literal["approve", "reject"]
    reviewer_id: str
    notes: str | None
    decided_at: datetime


class InvestigationWorkflowResponse(BaseModel):
    """Durable workflow state before or after the human review gate."""

    model_config = ConfigDict(frozen=True)

    thread_id: UUID
    status: Literal["pending_review", "approved", "rejected"]
    release_authorized: bool
    answer: InvestigationAnswerResponse
    review_request: HumanReviewRequestResponse | None
    review_decision: HumanReviewDecisionResponse | None
