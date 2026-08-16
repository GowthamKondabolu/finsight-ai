"""Typed contracts for registered experiments and online A/B telemetry."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXPERIMENT_SCHEMA_VERSION: Literal["1.0"] = "1.0"
ExperimentStatus = Literal["draft", "running", "stopped", "completed"]
MetricKind = Literal["binary", "continuous"]
MetricDirection = Literal["higher_is_better", "lower_is_better"]
EventType = Literal["exposure", "outcome"]
ExperimentDecision = Literal[
    "collecting",
    "ship_treatment",
    "keep_control",
    "inconclusive",
    "halt_guardrail",
]


class ExperimentContractError(ValueError):
    """Raised when experiment state or telemetry violates its registered plan."""


class ExperimentNotFoundError(ExperimentContractError):
    """Raised when an experiment or assignment identity does not exist."""


class MetricPlan(BaseModel):
    """Predeclared outcome or operational guardrail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    kind: MetricKind
    direction: MetricDirection
    minimum_practical_effect: float = Field(default=0.0, ge=0.0)
    maximum_degradation: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_metric_thresholds(self) -> Self:
        """Reject thresholds that cannot be interpreted for binary outcomes."""

        thresholds = (self.minimum_practical_effect, self.maximum_degradation)
        if any(value is not None and not math.isfinite(value) for value in thresholds):
            raise ValueError("metric thresholds must be finite")
        if self.kind == "binary" and any(value is not None and value > 1.0 for value in thresholds):
            raise ValueError("binary metric thresholds cannot exceed one")
        return self


class VariantPlan(BaseModel):
    """One immutable treatment configuration and its traffic allocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variant_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,49}$")
    description: str = Field(min_length=1, max_length=1_000)
    allocation_basis_points: int = Field(ge=1, le=9_999)
    is_control: bool = False
    configuration: dict[str, object] = Field(default_factory=dict)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        """Reject whitespace-only variant descriptions."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("variant description cannot be blank")
        return candidate


class ExperimentPlan(BaseModel):
    """Immutable, predeclared two-arm controlled-experiment specification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = EXPERIMENT_SCHEMA_VERSION
    experiment_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    name: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=1, max_length=2_000)
    randomization_unit: Literal["user", "session"]
    assignment_salt_version: int = Field(default=1, ge=1, le=1_000)
    primary_metric: MetricPlan
    guardrail_metrics: tuple[MetricPlan, ...] = ()
    expected_baseline_rate: float = Field(gt=0.0, lt=1.0)
    planned_sample_size_per_variant: int = Field(ge=2, le=10_000_000)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    statistical_power: float = Field(default=0.8, gt=0.5, lt=1.0)
    variants: tuple[VariantPlan, ...] = Field(min_length=2, max_length=2)
    git_sha: str = Field(pattern=r"^[a-f0-9]{7,40}$")
    offline_report_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator("name", "hypothesis")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """Reject whitespace-only experiment prose."""

        candidate = value.strip()
        if not candidate:
            raise ValueError("experiment text cannot be blank")
        return candidate

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Require unambiguous experiment scheduling timestamps."""

        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("experiment timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        """Require disjoint metrics, balanced allocation, and one control arm."""

        if self.primary_metric.maximum_degradation is not None:
            raise ValueError("the primary metric cannot define maximum_degradation")
        metric_names = [
            self.primary_metric.metric_name,
            *[m.metric_name for m in self.guardrail_metrics],
        ]
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("primary and guardrail metric names must be unique")
        if any(metric.maximum_degradation is None for metric in self.guardrail_metrics):
            raise ValueError("every guardrail metric requires maximum_degradation")

        variant_keys = [variant.variant_key for variant in self.variants]
        if len(set(variant_keys)) != len(variant_keys):
            raise ValueError("variant keys must be unique")
        if sum(variant.allocation_basis_points for variant in self.variants) != 10_000:
            raise ValueError("variant allocations must total 10000 basis points")
        if sum(variant.is_control for variant in self.variants) != 1:
            raise ValueError("exactly one variant must be marked as control")

        if self.primary_metric.kind != "binary":
            raise ValueError("the current power contract requires a binary primary metric")
        effect = self.primary_metric.minimum_practical_effect
        if effect <= 0.0:
            raise ValueError("the primary metric requires a positive practical effect")
        treatment_rate = (
            self.expected_baseline_rate + effect
            if self.primary_metric.direction == "higher_is_better"
            else self.expected_baseline_rate - effect
        )
        if not 0.0 < treatment_rate < 1.0:
            raise ValueError("the expected treatment rate must remain between zero and one")
        if (
            self.ends_at is not None
            and self.starts_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be later than starts_at")
        return self

    def fingerprint(self) -> str:
        """Return a canonical identity for the preregistered experiment plan."""

        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def metric_names(self) -> frozenset[str]:
        """Return all outcome metrics permitted by this plan."""

        return frozenset(
            [
                self.primary_metric.metric_name,
                *[metric.metric_name for metric in self.guardrail_metrics],
            ]
        )


class AssignmentResult(BaseModel):
    """Persisted sticky assignment without the raw randomization identifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: UUID
    experiment_key: str
    variant_key: str
    variant_configuration: dict[str, object]
    assigned_at: datetime
    existing_assignment: bool


class ExperimentRegistrationResult(BaseModel):
    """CLI-safe summary of an idempotent preregistration operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_key: str
    plan_fingerprint: str
    status: ExperimentStatus
    created: bool
    planned_sample_size_per_variant: int
    estimated_sample_size_per_variant: int


class ExperimentStatusResult(BaseModel):
    """Auditable experiment lifecycle transition result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_key: str
    status: ExperimentStatus
    started_at: datetime | None
    ended_at: datetime | None


class ExperimentEventInput(BaseModel):
    """Idempotent exposure or outcome telemetry attached to an assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: UUID
    event_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
    event_type: EventType
    metric_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    metric_value: float | None = None
    occurred_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_event_timezone(cls, value: datetime) -> datetime:
        """Require unambiguous telemetry timestamps."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_event_shape(self) -> Self:
        """Keep exposures marker-only and outcomes numeric."""

        if self.event_type == "exposure":
            if self.metric_name is not None or self.metric_value is not None:
                raise ValueError("exposure events cannot contain a metric")
        elif self.metric_name is None or self.metric_value is None:
            raise ValueError("outcome events require metric_name and metric_value")
        if self.metric_value is not None and not math.isfinite(self.metric_value):
            raise ValueError("metric_value must be finite")
        return self


class ExperimentEventResult(BaseModel):
    """Stored experiment telemetry identity and deduplication state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    experiment_key: str
    assignment_id: UUID
    variant_key: str
    event_key: str
    event_type: EventType
    recorded_at: datetime
    duplicate: bool


class VariantMetricSummary(BaseModel):
    """Per-variant support, mean, and binary conversion diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variant_key: str
    exposed_assignments: int = Field(ge=0)
    observed_assignments: int = Field(ge=0)
    mean: float | None
    confidence_interval: tuple[float, float] | None


class MetricComparison(BaseModel):
    """Treatment-minus-control estimate produced only when analysis is allowed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    direction: MetricDirection
    control: VariantMetricSummary
    treatment: VariantMetricSummary
    absolute_delta: float
    confidence_interval: tuple[float, float]
    p_value: float
    practical_effect_reached: bool
    guardrail_breached: bool


class ExperimentAnalysisReport(BaseModel):
    """No-peeking experiment status and optional terminal comparison report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = EXPERIMENT_SCHEMA_VERSION
    experiment_key: str
    plan_fingerprint: str
    status: ExperimentStatus
    generated_at: datetime
    planned_sample_size_per_variant: int
    exposed_assignments: dict[str, int]
    analysis_ready: bool
    decision: ExperimentDecision
    primary_comparison: MetricComparison | None
    guardrail_comparisons: tuple[MetricComparison, ...]
    limitations: tuple[str, ...]
