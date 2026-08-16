"""Tests for guarded, no-peeking online experiment analysis."""

from datetime import UTC, datetime

import pytest

from finsight.experiments.analysis import AssignmentObservation, analyze_experiment
from tests.unit.experiment_helpers import experiment_plan


def observations(
    *,
    count: int,
    control_completion: float,
    treatment_completion: float,
    control_safety: float = 0.0,
    treatment_safety: float = 0.0,
) -> list[AssignmentObservation]:
    """Construct deterministic aggregate-like binary assignment observations."""

    output: list[AssignmentObservation] = []
    for variant, completion, safety, latency in (
        ("control", control_completion, control_safety, 1_000.0),
        ("treatment", treatment_completion, treatment_safety, 1_200.0),
    ):
        completions = round(count * completion)
        violations = round(count * safety)
        output.extend(
            AssignmentObservation(
                variant_key=variant,
                exposed=True,
                metrics={
                    "verified_task_completion": float(index < completions),
                    "safety_violation": float(index < violations),
                    "latency_ms": latency,
                },
            )
            for index in range(count)
        )
    return output


def test_analysis_suppresses_interim_inference() -> None:
    """No effect, interval, or decision should be exposed below the planned sample size."""

    report = analyze_experiment(
        plan=experiment_plan(),
        status="running",
        observations=observations(
            count=399,
            control_completion=0.5,
            treatment_completion=0.9,
        ),
        generated_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert report.analysis_ready is False
    assert report.decision == "collecting"
    assert report.primary_comparison is None
    assert report.guardrail_comparisons == ()
    assert report.exposed_assignments == {"control": 399, "treatment": 399}


def test_powered_beneficial_treatment_can_ship() -> None:
    """A practical, statistically separated improvement should select treatment."""

    report = analyze_experiment(
        plan=experiment_plan(),
        status="running",
        observations=observations(
            count=400,
            control_completion=0.5,
            treatment_completion=0.62,
        ),
    )

    assert report.analysis_ready is True
    assert report.decision == "ship_treatment"
    assert report.primary_comparison is not None
    assert report.primary_comparison.absolute_delta == pytest.approx(0.12)
    assert report.primary_comparison.confidence_interval[0] > 0.0
    assert report.primary_comparison.practical_effect_reached is True
    assert len(report.guardrail_comparisons) == 2


def test_harmful_treatment_keeps_control() -> None:
    """A clearly worse primary outcome should retain the control pipeline."""

    report = analyze_experiment(
        plan=experiment_plan(),
        status="completed",
        observations=observations(
            count=400,
            control_completion=0.62,
            treatment_completion=0.45,
        ),
    )
    assert report.decision == "keep_control"


def test_guardrail_breach_overrides_primary_improvement() -> None:
    """Safety regression should halt launch even when task completion improves."""

    report = analyze_experiment(
        plan=experiment_plan(),
        status="running",
        observations=observations(
            count=400,
            control_completion=0.5,
            treatment_completion=0.62,
            treatment_safety=0.05,
        ),
    )

    assert report.decision == "halt_guardrail"
    safety = next(
        item for item in report.guardrail_comparisons if item.metric_name == "safety_violation"
    )
    assert safety.guardrail_breached is True


def test_stopped_underpowered_run_remains_suppressed() -> None:
    """Stopping early does not unlock favorable-interim inference."""

    report = analyze_experiment(
        plan=experiment_plan(),
        status="stopped",
        observations=observations(
            count=10,
            control_completion=0.2,
            treatment_completion=1.0,
        ),
    )
    assert report.decision == "collecting"
    assert any("stopped before" in limitation for limitation in report.limitations)


def test_analysis_requires_registered_variants_and_metrics() -> None:
    """Telemetry from another experiment must not contaminate an analysis."""

    with pytest.raises(ValueError, match="unknown variant"):
        analyze_experiment(
            plan=experiment_plan(),
            status="running",
            observations=[AssignmentObservation("unknown", True, {})],
        )
    with pytest.raises(ValueError, match="unregistered metric"):
        analyze_experiment(
            plan=experiment_plan(),
            status="running",
            observations=[AssignmentObservation("control", True, {"unplanned": 1.0})],
        )


def test_missing_primary_outcomes_do_not_count_toward_readiness() -> None:
    """Exposure volume alone cannot satisfy the primary-metric analysis threshold."""

    items = observations(count=400, control_completion=0.5, treatment_completion=0.6)
    items[-1] = AssignmentObservation(
        variant_key="treatment",
        exposed=True,
        metrics={"safety_violation": 0.0},
    )
    report = analyze_experiment(
        plan=experiment_plan(),
        status="running",
        observations=items,
    )
    assert report.analysis_ready is False


def test_equal_powered_results_are_inconclusive() -> None:
    """A powered tie should report an inconclusive decision and finite p-value."""

    report = analyze_experiment(
        plan=experiment_plan(),
        status="completed",
        observations=observations(
            count=400,
            control_completion=0.5,
            treatment_completion=0.5,
        ),
    )
    assert report.decision == "inconclusive"
    assert report.primary_comparison is not None
    assert report.primary_comparison.p_value == 1.0
