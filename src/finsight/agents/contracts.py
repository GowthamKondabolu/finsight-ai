"""Typed contracts for citation-grounded investigation answers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GeneratedClaim(BaseModel):
    """One model-proposed statement tied to supplied evidence identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)

    @field_validator("statement")
    @classmethod
    def normalize_statement(cls, value: str) -> str:
        """Reject whitespace-only generated statements."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("generated claim cannot be blank")
        return candidate

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        """Require unique identifiers from the supplied evidence namespace."""

        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("evidence identifiers cannot be blank or duplicated")
        return normalized


class GeneratedCalculation(BaseModel):
    """A model-proposed calculation that must be recomputed by the application."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=2_000)
    operation: Literal["identity", "sum", "difference", "ratio", "percentage_change"]
    fact_ids: list[str] = Field(min_length=1, max_length=10)
    reported_value: str = Field(min_length=1, max_length=100)
    reported_unit: str = Field(min_length=1, max_length=100)

    @field_validator("statement", "reported_unit")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """Trim required calculation text."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("calculation text cannot be blank")
        return candidate

    @field_validator("fact_ids")
    @classmethod
    def validate_fact_ids(cls, values: list[str]) -> list[str]:
        """Require unique normalized financial-fact identifiers."""

        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("fact identifiers cannot be blank or duplicated")
        if any(not value.startswith("F") or not value[1:].isdigit() for value in normalized):
            raise ValueError("calculation fact identifiers must use the F<number> namespace")
        return normalized

    @field_validator("reported_value")
    @classmethod
    def validate_decimal_value(cls, value: str) -> str:
        """Require a finite base-10 number without trusting model arithmetic."""

        candidate = value.strip()
        try:
            parsed = Decimal(candidate)
        except InvalidOperation as exc:
            raise ValueError("reported calculation value must be decimal") from exc
        if not parsed.is_finite():
            raise ValueError("reported calculation value must be finite")
        return candidate


class GroundedAnswerDraft(BaseModel):
    """Strict structured output returned by a generation provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: list[GeneratedClaim] = Field(default_factory=list, max_length=12)
    calculations: list[GeneratedCalculation] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("limitations")
    @classmethod
    def normalize_limitations(cls, values: list[str]) -> list[str]:
        """Reject blank or repeated limitation text."""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("limitations cannot be blank or duplicated")
        return normalized


@dataclass(frozen=True, slots=True)
class FinancialFactEvidence:
    """One exact SEC company-fact observation exposed to generation and validation."""

    source_id: str
    observation_key: str
    concept: str
    label: str
    unit: str
    value: Decimal
    start_date: date | None
    end_date: date
    filed_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form_type: str
    accession_number: str
    source_url: str


@dataclass(frozen=True, slots=True)
class AnswerSource:
    """Public citation record for a filing passage or exact SEC fact."""

    source_id: str
    source_type: Literal["filing_passage", "financial_fact"]
    label: str
    source_url: str
    accession_number: str
    form_type: str
    filing_date: date | None = None
    section_name: str | None = None
    chunk_index: int | None = None
    content_hash: str | None = None
    fact_concept: str | None = None
    fact_value: str | None = None
    fact_unit: str | None = None
    fact_end_date: date | None = None


@dataclass(frozen=True, slots=True)
class ValidatedClaim:
    """One rendered narrative claim with verified source identifiers."""

    statement: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NumericalValidation:
    """Deterministic comparison of reported and recomputed arithmetic."""

    statement: str
    operation: str
    fact_ids: tuple[str, ...]
    reported_value: str
    expected_value: str | None
    reported_unit: str
    expected_unit: str | None
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class GroundedAnswerResult:
    """Final investigation answer after citation and numerical guardrails."""

    question: str
    status: Literal["grounded", "insufficient_evidence", "needs_review"]
    answer: str
    claims: tuple[ValidatedClaim, ...]
    numerical_validations: tuple[NumericalValidation, ...]
    sources: tuple[AnswerSource, ...]
    limitations: tuple[str, ...]
    model_name: str | None
    requires_human_review: bool
    review_reasons: tuple[str, ...]


class InvestigationQuery(BaseModel):
    """Provider-independent answer request and bounded evidence controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(default=8, ge=1, le=20)
    candidate_k: int = Field(default=50, ge=1, le=200)
    cik: str | None = None
    form_types: tuple[str, ...] = ()
    filed_from: date | None = None
    filed_to: date | None = None
    section_names: tuple[str, ...] = ()
    fact_concepts: tuple[str, ...] = ()
    fact_limit: int = Field(default=30, ge=0, le=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> InvestigationQuery:
        """Reject contradictory retrieval and financial-fact controls."""

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
