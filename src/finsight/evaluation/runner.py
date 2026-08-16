"""Reproducible system evaluation and paired experiment analysis."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Sequence

from finsight.evaluation.contracts import (
    BenchmarkDataset,
    MetricDirection,
    MetricValue,
    PairedExperimentReport,
    PairedMetricComparison,
    SystemEvaluationReport,
    SystemRun,
)
from finsight.evaluation.metrics import evaluate_case

DEFAULT_BOOTSTRAP_ITERATIONS = 2_000
DEFAULT_RANDOM_SEED = 17
MINIMUM_PUBLISHABLE_CASES = 20

METRIC_DIRECTIONS: dict[str, MetricDirection] = {
    "retrieval_recall_at_k": "higher_is_better",
    "retrieval_mrr": "higher_is_better",
    "retrieval_ndcg_at_k": "higher_is_better",
    "citation_validity": "higher_is_better",
    "citation_precision": "higher_is_better",
    "citation_coverage": "higher_is_better",
    "faithfulness": "higher_is_better",
    "numerical_accuracy": "higher_is_better",
    "abstention_accuracy": "higher_is_better",
    "verified_task_completion": "higher_is_better",
    "reviewer_approval_rate": "higher_is_better",
    "safety_violation_rate": "lower_is_better",
    "failure_rate": "lower_is_better",
    "latency_ms": "lower_is_better",
    "cost_usd": "lower_is_better",
}


class EvaluationContractError(ValueError):
    """Raised when a dataset and recorded run cannot be compared safely."""


def dataset_fingerprint(dataset: BenchmarkDataset) -> str:
    """Return a stable SHA-256 identity for all dataset content and labels."""

    payload = json.dumps(
        dataset.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_run(dataset: BenchmarkDataset, run: SystemRun) -> None:
    """Require exact dataset identity and one observation per held-out case."""

    fingerprint = dataset_fingerprint(dataset)
    if run.dataset_id != dataset.dataset_id:
        raise EvaluationContractError("system run references a different dataset_id")
    if run.dataset_fingerprint != fingerprint:
        raise EvaluationContractError("system run dataset fingerprint does not match")

    expected_case_ids = {case.case_id for case in dataset.cases}
    observed_case_ids = {item.case_id for item in run.observations}
    missing = sorted(expected_case_ids - observed_case_ids)
    unexpected = sorted(observed_case_ids - expected_case_ids)
    if missing or unexpected:
        raise EvaluationContractError(
            f"system run case mismatch; missing={missing}, unexpected={unexpected}"
        )


def evaluate_system(
    dataset: BenchmarkDataset,
    run: SystemRun,
    *,
    top_k: int,
) -> SystemEvaluationReport:
    """Aggregate deterministic metrics across a complete system run."""

    if top_k < 1:
        raise ValueError("top_k must be positive")
    _validate_run(dataset, run)
    observations = {item.case_id: item for item in run.observations}
    case_metrics: dict[str, dict[str, float]] = {}
    supported_values: dict[str, list[float]] = defaultdict(list)

    for case in dataset.cases:
        result = evaluate_case(case, observations[case.case_id], top_k=top_k)
        values = {name: result.values[name] for name in sorted(result.supported)}
        case_metrics[case.case_id] = values
        for name, value in values.items():
            supported_values[name].append(value)

    metrics = {
        name: MetricValue(
            value=sum(values) / len(values) if values else None,
            numerator=sum(values),
            denominator=len(values),
            direction=METRIC_DIRECTIONS[name],
        )
        for name, values in sorted(supported_values.items())
    }
    return SystemEvaluationReport(
        system_name=run.system_name,
        run_id=run.run_id,
        run_type=run.run_type,
        case_count=len(dataset.cases),
        top_k=top_k,
        metrics=metrics,
        latency_percentiles_ms={
            "p50": _percentile(
                [item.latency_ms for item in run.observations],
                0.50,
            ),
            "p95": _percentile(
                [item.latency_ms for item in run.observations],
                0.95,
            ),
        },
        case_metrics=case_metrics,
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sequence."""

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _bootstrap_interval(
    deltas: Sequence[float],
    *,
    iterations: int,
    confidence_level: float,
    random_seed: int,
) -> tuple[float, float]:
    """Bootstrap a paired mean-difference confidence interval."""

    generator = random.Random(random_seed)
    count = len(deltas)
    estimates = [
        sum(deltas[generator.randrange(count)] for _ in range(count)) / count
        for _ in range(iterations)
    ]
    alpha = 1.0 - confidence_level
    return (
        _percentile(estimates, alpha / 2.0),
        _percentile(estimates, 1.0 - alpha / 2.0),
    )


def _paired_sign_test(deltas: Sequence[float]) -> float | None:
    """Return an exact two-sided paired sign-test p-value."""

    positive = sum(delta > 0.0 for delta in deltas)
    negative = sum(delta < 0.0 for delta in deltas)
    count = positive + negative
    if count == 0:
        return None
    smaller = min(positive, negative)
    probability = 2.0 * sum(math.comb(count, index) for index in range(smaller + 1)) / (2**count)
    return float(min(probability, 1.0))


def _effect_size(deltas: Sequence[float]) -> float | None:
    """Return paired standardized mean change when variation is measurable."""

    if len(deltas) < 2:
        return None
    deviation = statistics.stdev(deltas)
    if deviation == 0.0:
        return None
    return statistics.mean(deltas) / deviation


def _comparison(
    metric: str,
    control: SystemEvaluationReport,
    treatment: SystemEvaluationReport,
    *,
    iterations: int,
    confidence_level: float,
    random_seed: int,
) -> PairedMetricComparison | None:
    """Compare cases where both systems expose the requested metric."""

    paired_values = [
        (
            control.case_metrics[case_id][metric],
            treatment.case_metrics[case_id][metric],
        )
        for case_id in sorted(control.case_metrics)
        if metric in control.case_metrics[case_id] and metric in treatment.case_metrics[case_id]
    ]
    if not paired_values:
        return None

    control_values = [item[0] for item in paired_values]
    treatment_values = [item[1] for item in paired_values]
    deltas = [treatment - baseline for baseline, treatment in paired_values]
    control_mean = statistics.mean(control_values)
    treatment_mean = statistics.mean(treatment_values)
    absolute_delta = treatment_mean - control_mean
    relative_delta = absolute_delta / abs(control_mean) if control_mean != 0.0 else None
    return PairedMetricComparison(
        metric=metric,
        direction=METRIC_DIRECTIONS[metric],
        control_value=control_mean,
        treatment_value=treatment_mean,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        confidence_level=confidence_level,
        confidence_interval=_bootstrap_interval(
            deltas,
            iterations=iterations,
            confidence_level=confidence_level,
            random_seed=random_seed,
        ),
        standardized_effect_size=_effect_size(deltas),
        paired_sign_test_p_value=_paired_sign_test(deltas),
        paired_case_count=len(paired_values),
    )


def compare_systems(
    dataset: BenchmarkDataset,
    control_run: SystemRun,
    treatment_run: SystemRun,
    *,
    top_k: int = 10,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    random_seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = 0.95,
) -> PairedExperimentReport:
    """Run a paired offline comparison with deterministic uncertainty estimates."""

    if not 100 <= bootstrap_iterations <= 100_000:
        raise ValueError("bootstrap_iterations must be between 100 and 100000")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between zero and one")
    if control_run.run_id == treatment_run.run_id:
        raise EvaluationContractError("control and treatment runs must have different run_ids")

    control = evaluate_system(dataset, control_run, top_k=top_k)
    treatment = evaluate_system(dataset, treatment_run, top_k=top_k)
    shared_metrics = sorted(set(control.metrics) & set(treatment.metrics))
    comparisons = tuple(
        comparison
        for index, metric in enumerate(shared_metrics)
        if (
            comparison := _comparison(
                metric,
                control,
                treatment,
                iterations=bootstrap_iterations,
                confidence_level=confidence_level,
                random_seed=random_seed + index,
            )
        )
        is not None
    )

    benchmark_claim_allowed = (
        dataset.data_classification == "public_sec_derived"
        and control_run.run_type == "offline_benchmark"
        and treatment_run.run_type == "offline_benchmark"
        and len(dataset.cases) >= MINIMUM_PUBLISHABLE_CASES
        and control_run.git_sha is not None
        and treatment_run.git_sha is not None
    )
    limitations = [
        "Paired estimates describe only the versioned cases and recorded system outputs.",
        (
            "Faithfulness requires independent support judgments; "
            "citation validity alone is not entailment."
        ),
        "Statistical intervals quantify sampling uncertainty, not dataset or labeling bias.",
    ]
    if not benchmark_claim_allowed:
        limitations.append(
            "This report is a fixture or incomplete benchmark and must not be presented "
            "as system performance."
        )

    return PairedExperimentReport(
        dataset_id=dataset.dataset_id,
        dataset_fingerprint=dataset_fingerprint(dataset),
        data_classification=dataset.data_classification,
        benchmark_claim_allowed=benchmark_claim_allowed,
        bootstrap_iterations=bootstrap_iterations,
        random_seed=random_seed,
        control=control,
        treatment=treatment,
        comparisons=comparisons,
        limitations=tuple(limitations),
    )
