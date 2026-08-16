"""No-peeking analysis for persisted two-arm FinSight experiments."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import NormalDist
from typing import cast

from finsight.experiments.contracts import (
    ExperimentAnalysisReport,
    ExperimentDecision,
    ExperimentPlan,
    ExperimentStatus,
    MetricComparison,
    MetricPlan,
    VariantMetricSummary,
)


@dataclass(frozen=True)
class AssignmentObservation:
    """One anonymous assignment with exposure and latest permitted outcomes."""

    variant_key: str
    exposed: bool
    metrics: dict[str, float]


def _confidence_interval(
    values: list[float],
    *,
    kind: str,
    confidence_level: float,
) -> tuple[float, float] | None:
    """Return a Wilson binary interval or normal mean interval."""

    if not values:
        return None
    z_score = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    mean = statistics.mean(values)
    if kind == "binary":
        count = len(values)
        denominator = 1.0 + z_score**2 / count
        center = (mean + z_score**2 / (2.0 * count)) / denominator
        margin = (
            z_score
            * math.sqrt(mean * (1.0 - mean) / count + z_score**2 / (4.0 * count**2))
            / denominator
        )
        return (max(0.0, center - margin), min(1.0, center + margin))
    if len(values) < 2:
        return (mean, mean)
    margin = z_score * statistics.stdev(values) / math.sqrt(len(values))
    return (mean - margin, mean + margin)


def _summary(
    *,
    variant_key: str,
    observations: list[AssignmentObservation],
    metric: MetricPlan,
    confidence_level: float,
) -> tuple[VariantMetricSummary, list[float]]:
    exposed = [item for item in observations if item.exposed]
    values = [
        item.metrics[metric.metric_name] for item in exposed if metric.metric_name in item.metrics
    ]
    return (
        VariantMetricSummary(
            variant_key=variant_key,
            exposed_assignments=len(exposed),
            observed_assignments=len(values),
            mean=statistics.mean(values) if values else None,
            confidence_interval=_confidence_interval(
                values,
                kind=metric.kind,
                confidence_level=confidence_level,
            ),
        ),
        values,
    )


def _comparison(
    *,
    plan: ExperimentPlan,
    metric: MetricPlan,
    observations: list[AssignmentObservation],
) -> MetricComparison:
    control_plan = next(variant for variant in plan.variants if variant.is_control)
    treatment_plan = next(variant for variant in plan.variants if not variant.is_control)
    by_variant = {
        variant.variant_key: [
            item for item in observations if item.variant_key == variant.variant_key
        ]
        for variant in plan.variants
    }
    control, control_values = _summary(
        variant_key=control_plan.variant_key,
        observations=by_variant[control_plan.variant_key],
        metric=metric,
        confidence_level=plan.confidence_level,
    )
    treatment, treatment_values = _summary(
        variant_key=treatment_plan.variant_key,
        observations=by_variant[treatment_plan.variant_key],
        metric=metric,
        confidence_level=plan.confidence_level,
    )
    if control.mean is None or treatment.mean is None:
        raise ValueError("metric comparison requires observed values in both variants")

    delta = treatment.mean - control.mean
    if metric.kind == "binary":
        standard_error = math.sqrt(
            control.mean * (1.0 - control.mean) / len(control_values)
            + treatment.mean * (1.0 - treatment.mean) / len(treatment_values)
        )
    else:
        control_variance = statistics.variance(control_values) if len(control_values) > 1 else 0.0
        treatment_variance = (
            statistics.variance(treatment_values) if len(treatment_values) > 1 else 0.0
        )
        standard_error = math.sqrt(
            control_variance / len(control_values) + treatment_variance / len(treatment_values)
        )
    z_score = NormalDist().inv_cdf(0.5 + plan.confidence_level / 2.0)
    interval = (
        delta - z_score * standard_error,
        delta + z_score * standard_error,
    )
    if standard_error == 0.0:
        p_value = 1.0 if delta == 0.0 else 0.0
    else:
        p_value = math.erfc(abs(delta / standard_error) / math.sqrt(2.0))

    directional_delta = delta if metric.direction == "higher_is_better" else -delta
    degradation = -directional_delta
    return MetricComparison(
        metric_name=metric.metric_name,
        direction=metric.direction,
        control=control,
        treatment=treatment,
        absolute_delta=delta,
        confidence_interval=interval,
        p_value=p_value,
        practical_effect_reached=(directional_delta >= metric.minimum_practical_effect),
        guardrail_breached=(
            metric.maximum_degradation is not None and degradation > metric.maximum_degradation
        ),
    )


def analyze_experiment(
    *,
    plan: ExperimentPlan,
    status: ExperimentStatus,
    observations: list[AssignmentObservation],
    generated_at: datetime | None = None,
) -> ExperimentAnalysisReport:
    """Analyze only after every arm reaches its predeclared primary sample size."""

    variant_keys = {variant.variant_key for variant in plan.variants}
    if any(item.variant_key not in variant_keys for item in observations):
        raise ValueError("assignment observation references an unknown variant")
    if any(
        metric_name not in plan.metric_names
        for item in observations
        for metric_name in item.metrics
    ):
        raise ValueError("assignment observation contains an unregistered metric")

    exposed_counts = {
        variant.variant_key: sum(
            item.exposed and item.variant_key == variant.variant_key for item in observations
        )
        for variant in plan.variants
    }
    primary_observed_counts = {
        variant.variant_key: sum(
            item.exposed
            and item.variant_key == variant.variant_key
            and plan.primary_metric.metric_name in item.metrics
            for item in observations
        )
        for variant in plan.variants
    }
    threshold = plan.planned_sample_size_per_variant
    analysis_ready = all(count >= threshold for count in primary_observed_counts.values())
    limitations = [
        "Assignment is deterministic and sticky; this report does not establish causal "
        "validity if eligibility, exposure, or telemetry are biased.",
        "Outcome analysis is suppressed until every variant reaches the preregistered "
        "primary-metric sample size.",
        "Confidence intervals use large-sample approximations and do not correct "
        "multiple guardrail comparisons.",
    ]
    if status == "draft":
        limitations.append(
            "The experiment is still a draft and cannot receive production assignments."
        )
    if status == "stopped" and not analysis_ready:
        limitations.append(
            "The experiment stopped before its planned sample size; terminal inference "
            "remains suppressed."
        )

    if not analysis_ready:
        return ExperimentAnalysisReport(
            experiment_key=plan.experiment_key,
            plan_fingerprint=plan.fingerprint(),
            status=status,
            generated_at=generated_at or datetime.now(UTC),
            planned_sample_size_per_variant=threshold,
            exposed_assignments=exposed_counts,
            analysis_ready=False,
            decision="collecting",
            primary_comparison=None,
            guardrail_comparisons=(),
            limitations=tuple(limitations),
        )

    primary = _comparison(plan=plan, metric=plan.primary_metric, observations=observations)
    guardrails = tuple(
        _comparison(plan=plan, metric=metric, observations=observations)
        for metric in plan.guardrail_metrics
        if all(
            any(
                item.exposed
                and item.variant_key == variant.variant_key
                and metric.metric_name in item.metrics
                for item in observations
            )
            for variant in plan.variants
        )
    )
    primary_directional_interval = (
        primary.confidence_interval
        if primary.direction == "higher_is_better"
        else (-primary.confidence_interval[1], -primary.confidence_interval[0])
    )
    if any(comparison.guardrail_breached for comparison in guardrails):
        decision = "halt_guardrail"
    elif primary.practical_effect_reached and primary_directional_interval[0] > 0.0:
        decision = "ship_treatment"
    elif primary_directional_interval[1] < 0.0:
        decision = "keep_control"
    else:
        decision = "inconclusive"

    return ExperimentAnalysisReport(
        experiment_key=plan.experiment_key,
        plan_fingerprint=plan.fingerprint(),
        status=status,
        generated_at=generated_at or datetime.now(UTC),
        planned_sample_size_per_variant=threshold,
        exposed_assignments=exposed_counts,
        analysis_ready=True,
        decision=cast(ExperimentDecision, decision),
        primary_comparison=primary,
        guardrail_comparisons=guardrails,
        limitations=tuple(limitations),
    )
