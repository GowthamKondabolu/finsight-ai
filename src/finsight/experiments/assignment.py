"""Privacy-preserving deterministic assignment and power planning."""

from __future__ import annotations

import hashlib
import hmac
import math
from statistics import NormalDist

from finsight.experiments.contracts import ExperimentContractError, ExperimentPlan, VariantPlan


def hash_randomization_unit(
    *,
    secret: str,
    experiment_key: str,
    assignment_salt_version: int,
    unit_id: str,
) -> str:
    """Return an irreversible experiment-scoped HMAC for a user or session."""

    if len(secret) < 32:
        raise ExperimentContractError(
            "the experiment assignment secret must be at least 32 characters"
        )
    normalized_unit_id = unit_id.strip()
    if not normalized_unit_id:
        raise ExperimentContractError("the randomization unit identifier cannot be blank")
    if len(normalized_unit_id) > 500:
        raise ExperimentContractError("the randomization unit identifier is too long")
    message = f"{experiment_key}:{assignment_salt_version}:{normalized_unit_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def select_variant(plan: ExperimentPlan, unit_hash: str) -> VariantPlan:
    """Map a stable unit hash into the plan's immutable allocation buckets."""

    if len(unit_hash) != 64 or any(character not in "0123456789abcdef" for character in unit_hash):
        raise ExperimentContractError("unit_hash must be a lowercase SHA-256 digest")
    allocation_digest = hashlib.sha256(f"{plan.fingerprint()}:{unit_hash}".encode()).digest()
    bucket = int.from_bytes(allocation_digest[:8], "big") % 10_000
    cumulative = 0
    for variant in plan.variants:
        cumulative += variant.allocation_basis_points
        if bucket < cumulative:
            return variant
    raise ExperimentContractError("variant allocations do not cover every assignment bucket")


def estimate_binary_sample_size_per_variant(plan: ExperimentPlan) -> int:
    """Approximate a two-sided, equally allocated two-proportion sample size."""

    baseline = plan.expected_baseline_rate
    effect = plan.primary_metric.minimum_practical_effect
    treatment = (
        baseline + effect
        if plan.primary_metric.direction == "higher_is_better"
        else baseline - effect
    )
    alpha = 1.0 - plan.confidence_level
    z_alpha = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    z_power = NormalDist().inv_cdf(plan.statistical_power)
    pooled = (baseline + treatment) / 2.0
    numerator = (
        z_alpha * math.sqrt(2.0 * pooled * (1.0 - pooled))
        + z_power * math.sqrt(baseline * (1.0 - baseline) + treatment * (1.0 - treatment))
    ) ** 2
    return math.ceil(numerator / (effect**2))


def validate_planned_sample_size(plan: ExperimentPlan) -> None:
    """Reject plans whose declared sample size is below their own power inputs."""

    required = estimate_binary_sample_size_per_variant(plan)
    if plan.planned_sample_size_per_variant < required:
        raise ExperimentContractError(
            "planned_sample_size_per_variant is below the power estimate "
            f"({plan.planned_sample_size_per_variant} < {required})"
        )
