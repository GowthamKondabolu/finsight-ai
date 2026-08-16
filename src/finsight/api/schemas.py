"""API response schemas."""

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HealthResponse(BaseModel):
    """Health-check response contract."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


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
