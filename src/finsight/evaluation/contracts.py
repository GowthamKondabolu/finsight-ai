"""Versioned contracts for reproducible offline FinSight evaluations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"
MetricDirection = Literal["higher_is_better", "lower_is_better"]


class RelevanceJudgment(BaseModel):
    """Graded relevance label for one stable evidence identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(min_length=1, max_length=500)
    grade: int = Field(ge=1, le=3)


class BenchmarkCase(BaseModel):
    """One held-out question and its independently authored expectations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,99}$")
    question: str = Field(min_length=1, max_length=2_000)
    expected_behavior: Literal["answer", "abstain"]
    relevance_judgments: tuple[RelevanceJudgment, ...] = Field(min_length=1)
    policy_tags: tuple[str, ...] = ()

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        """Reject a whitespace-only benchmark question."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("benchmark question cannot be blank")
        return candidate

    @field_validator("policy_tags")
    @classmethod
    def normalize_policy_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Require stable, unique policy labels."""

        normalized = tuple(value.strip().lower() for value in values)
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("policy_tags cannot be blank or duplicated")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def validate_unique_relevance_sources(self) -> Self:
        """Prevent a source from receiving contradictory relevance grades."""

        source_keys = [item.source_key for item in self.relevance_judgments]
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("relevance source keys must be unique per case")
        return self


class BenchmarkDataset(BaseModel):
    """Immutable evaluation dataset metadata and held-out cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,99}$")
    description: str = Field(min_length=1, max_length=2_000)
    data_classification: Literal["synthetic_fixture", "public_sec_derived"]
    created_at: datetime
    cases: tuple[BenchmarkCase, ...] = Field(min_length=2)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Require an unambiguous dataset creation timestamp."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> Self:
        """Require one benchmark definition per case identifier."""

        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("benchmark case identifiers must be unique")
        return self


class RetrievedEvidence(BaseModel):
    """One ranked evidence identity emitted by a system run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(min_length=1, max_length=500)
    rank: int = Field(ge=1, le=1_000)
    score: float | None = None


class ClaimObservation(BaseModel):
    """One answer claim with citations and an independent support judgment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    statement: str = Field(min_length=1, max_length=2_000)
    citation_keys: tuple[str, ...] = ()
    supported: bool | None = None

    @field_validator("citation_keys")
    @classmethod
    def validate_citation_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject blank or duplicated citation identities."""

        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("citation keys cannot be blank or duplicated")
        return normalized


class NumericalObservation(BaseModel):
    """Recorded deterministic arithmetic validation from an answer run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    passed: bool


class SafetyFinding(BaseModel):
    """Versioned human or policy-judge safety finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,99}$")
    severity: Literal["low", "medium", "high", "critical"]
    description: str = Field(min_length=1, max_length=1_000)


class CaseObservation(BaseModel):
    """Recorded system output and independent judgments for one benchmark case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    completed: bool
    abstained: bool
    retrieved: tuple[RetrievedEvidence, ...] = ()
    available_source_keys: tuple[str, ...] = ()
    claims: tuple[ClaimObservation, ...] = ()
    numerical_checks: tuple[NumericalObservation, ...] = ()
    safety_findings: tuple[SafetyFinding, ...] = ()
    reviewer_approved: bool | None = None
    latency_ms: float = Field(ge=0.0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    error_type: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_observation_identity(self) -> Self:
        """Reject ambiguous ranks, sources, claims, and check identifiers."""

        ranks = [item.rank for item in self.retrieved]
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("retrieval ranks must be contiguous and start at one")
        retrieved_keys = [item.source_key for item in self.retrieved]
        if len(set(retrieved_keys)) != len(retrieved_keys):
            raise ValueError("retrieved source keys must be unique")
        if len(set(self.available_source_keys)) != len(self.available_source_keys):
            raise ValueError("available source keys must be unique")
        if not set(retrieved_keys).issubset(self.available_source_keys):
            raise ValueError("retrieved evidence must be included in available_source_keys")
        claim_ids = [item.claim_id for item in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claim identifiers must be unique")
        check_ids = [item.check_id for item in self.numerical_checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("numerical check identifiers must be unique")
        if self.completed and self.error_type is not None:
            raise ValueError("completed observations cannot include error_type")
        if not self.completed and self.error_type is None:
            raise ValueError("failed observations must include error_type")
        return self


class SystemRun(BaseModel):
    """Versioned outputs from one evaluated system configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,99}$")
    system_name: str = Field(min_length=1, max_length=200)
    run_type: Literal["synthetic_fixture", "offline_benchmark", "production_shadow"]
    dataset_id: str
    dataset_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    git_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{7,40}$")
    model_versions: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    observations: tuple[CaseObservation, ...] = Field(min_length=2)

    @field_validator("created_at")
    @classmethod
    def require_run_timezone(cls, value: datetime) -> datetime:
        """Require an unambiguous run timestamp."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_unique_observations(self) -> Self:
        """Require exactly one recorded observation per case identifier."""

        case_ids = [item.case_id for item in self.observations]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("run observation case identifiers must be unique")
        return self


class MetricValue(BaseModel):
    """One aggregate metric with explicit optimization direction and support."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float | None
    numerator: float
    denominator: int = Field(ge=0)
    direction: MetricDirection


class SystemEvaluationReport(BaseModel):
    """Aggregate and per-case metrics for one system run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_name: str
    run_id: str
    run_type: str
    case_count: int = Field(ge=1)
    top_k: int = Field(ge=1)
    metrics: dict[str, MetricValue]
    latency_percentiles_ms: dict[str, float]
    case_metrics: dict[str, dict[str, float]]


class PairedMetricComparison(BaseModel):
    """Paired treatment-minus-control estimate with uncertainty."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    direction: MetricDirection
    control_value: float
    treatment_value: float
    absolute_delta: float
    relative_delta: float | None
    confidence_level: float = Field(gt=0.0, lt=1.0)
    confidence_interval: tuple[float, float]
    standardized_effect_size: float | None
    paired_sign_test_p_value: float | None
    paired_case_count: int = Field(ge=1)


class PairedExperimentReport(BaseModel):
    """Auditable paired comparison for control and treatment runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_id: str
    dataset_fingerprint: str
    data_classification: str
    benchmark_claim_allowed: bool
    bootstrap_iterations: int = Field(ge=100, le=100_000)
    random_seed: int
    control: SystemEvaluationReport
    treatment: SystemEvaluationReport
    comparisons: tuple[PairedMetricComparison, ...]
    limitations: tuple[str, ...]
