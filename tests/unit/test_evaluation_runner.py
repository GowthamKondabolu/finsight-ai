"""Tests for aggregate reports, paired statistics, and benchmark safeguards."""

from pathlib import Path

import pytest

from finsight.evaluation.contracts import BenchmarkDataset, SystemRun
from finsight.evaluation.io import load_dataset, load_system_run, write_report
from finsight.evaluation.runner import (
    EvaluationContractError,
    compare_systems,
    dataset_fingerprint,
    evaluate_system,
)

FIXTURES = Path("evals/fixtures")


def fixture_artifacts() -> tuple[BenchmarkDataset, SystemRun, SystemRun]:
    """Load the committed synthetic contract fixture."""

    return (
        load_dataset(FIXTURES / "synthetic_dataset_v1.json"),
        load_system_run(FIXTURES / "control_run_v1.json"),
        load_system_run(FIXTURES / "treatment_run_v1.json"),
    )


def test_system_report_aggregates_supported_denominators_and_latency_percentiles() -> None:
    """Optional judgments should retain honest denominators in aggregate output."""

    dataset, control, _ = fixture_artifacts()
    report = evaluate_system(dataset, control, top_k=3)

    assert report.case_count == 4
    assert report.metrics["retrieval_recall_at_k"].denominator == 4
    assert report.metrics["faithfulness"].denominator == 4
    assert report.metrics["numerical_accuracy"].denominator == 1
    assert report.metrics["reviewer_approval_rate"].value == 0.25
    assert report.latency_percentiles_ms["p50"] == 650.0
    assert report.latency_percentiles_ms["p95"] == pytest.approx(705.5)


def test_paired_report_is_seeded_and_blocks_fixture_performance_claims() -> None:
    """The same inputs and seed should produce identical metrics and intervals."""

    dataset, control, treatment = fixture_artifacts()
    first = compare_systems(
        dataset,
        control,
        treatment,
        top_k=3,
        bootstrap_iterations=200,
        random_seed=23,
    )
    second = compare_systems(
        dataset,
        control,
        treatment,
        top_k=3,
        bootstrap_iterations=200,
        random_seed=23,
    )

    assert first.comparisons == second.comparisons
    assert first.benchmark_claim_allowed is False
    assert "fixture" in first.limitations[-1]
    comparisons = {item.metric: item for item in first.comparisons}
    assert comparisons["retrieval_recall_at_k"].absolute_delta == pytest.approx(0.5416666667)
    assert comparisons["latency_ms"].direction == "lower_is_better"
    assert comparisons["latency_ms"].absolute_delta == 185.0
    assert comparisons["failure_rate"].paired_sign_test_p_value is None
    assert comparisons["numerical_accuracy"].standardized_effect_size is None


def test_report_round_trip_writes_valid_json_atomically(tmp_path: Path) -> None:
    """Persisted reports should validate against the same strict contract."""

    dataset, control, treatment = fixture_artifacts()
    report = compare_systems(
        dataset,
        control,
        treatment,
        bootstrap_iterations=100,
    )
    output = tmp_path / "reports" / "comparison.json"

    write_report(output, report)

    assert output.read_text(encoding="utf-8").endswith("\n")
    assert report.model_validate_json(output.read_text(encoding="utf-8")) == report
    assert not list(output.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"dataset_id": "different-dataset"}, "different dataset_id"),
        ({"dataset_fingerprint": "0" * 64}, "fingerprint does not match"),
        (
            {"observations": []},
            "at least 2 items",
        ),
    ],
)
def test_run_and_dataset_identity_must_match(
    update: dict[str, object],
    message: str,
) -> None:
    """A report must never mix outputs from another dataset version."""

    dataset, control, _ = fixture_artifacts()
    if update == {"observations": []}:
        with pytest.raises(ValueError, match=message):
            SystemRun.model_validate({**control.model_dump(), "observations": []})
        return
    changed = control.model_copy(update=update)
    with pytest.raises(EvaluationContractError, match=message):
        evaluate_system(dataset, changed, top_k=3)


def test_run_rejects_missing_or_unexpected_cases() -> None:
    """Paired case sets must be complete before aggregation."""

    dataset, control, _ = fixture_artifacts()
    incomplete = control.model_copy(update={"observations": control.observations[:-1]})

    with pytest.raises(EvaluationContractError, match="case mismatch"):
        evaluate_system(dataset, incomplete, top_k=3)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"top_k": 0}, "top_k must be positive"),
        ({"bootstrap_iterations": 99}, "bootstrap_iterations"),
        ({"bootstrap_iterations": 100_001}, "bootstrap_iterations"),
        ({"confidence_level": 0.0}, "confidence_level"),
        ({"confidence_level": 1.0}, "confidence_level"),
    ],
)
def test_runner_rejects_invalid_statistical_controls(
    kwargs: dict[str, object],
    message: str,
) -> None:
    """Evaluation controls should be bounded and explicit."""

    dataset, control, treatment = fixture_artifacts()
    with pytest.raises(ValueError, match=message):
        compare_systems(dataset, control, treatment, **kwargs)  # type: ignore[arg-type]


def test_control_and_treatment_require_distinct_run_ids() -> None:
    """A paired experiment cannot compare one run against itself."""

    dataset, control, _ = fixture_artifacts()
    with pytest.raises(EvaluationContractError, match="different run_ids"):
        compare_systems(dataset, control, control)


def test_publishable_gate_requires_public_dataset_recorded_runs_and_sample_size() -> None:
    """Small synthetic fixtures must remain clearly non-publishable."""

    dataset, control, treatment = fixture_artifacts()
    public_dataset = dataset.model_copy(update={"data_classification": "public_sec_derived"})
    fingerprint = dataset_fingerprint(public_dataset)
    control_benchmark = control.model_copy(
        update={"run_type": "offline_benchmark", "dataset_fingerprint": fingerprint}
    )
    treatment_benchmark = treatment.model_copy(
        update={"run_type": "offline_benchmark", "dataset_fingerprint": fingerprint}
    )

    report = compare_systems(
        public_dataset,
        control_benchmark,
        treatment_benchmark,
        bootstrap_iterations=100,
    )

    assert report.benchmark_claim_allowed is False


def test_publishable_gate_accepts_complete_public_held_out_experiment() -> None:
    """A sufficiently sized, versioned public benchmark may cross the claim gate."""

    dataset, control, treatment = fixture_artifacts()
    template_case = dataset.cases[0]
    control_observation = control.observations[0]
    treatment_observation = treatment.observations[0]
    cases = tuple(
        template_case.model_copy(update={"case_id": f"public-case-{index:02d}"})
        for index in range(20)
    )
    public_dataset = dataset.model_copy(
        update={
            "dataset_id": "public-sec-held-out-v1",
            "data_classification": "public_sec_derived",
            "cases": cases,
        }
    )
    fingerprint = dataset_fingerprint(public_dataset)
    control_run = control.model_copy(
        update={
            "run_id": "public-control-v1",
            "run_type": "offline_benchmark",
            "dataset_id": public_dataset.dataset_id,
            "dataset_fingerprint": fingerprint,
            "observations": tuple(
                control_observation.model_copy(update={"case_id": case.case_id}) for case in cases
            ),
        }
    )
    treatment_run = treatment.model_copy(
        update={
            "run_id": "public-treatment-v1",
            "run_type": "offline_benchmark",
            "dataset_id": public_dataset.dataset_id,
            "dataset_fingerprint": fingerprint,
            "observations": tuple(
                treatment_observation.model_copy(update={"case_id": case.case_id}) for case in cases
            ),
        }
    )

    report = compare_systems(
        public_dataset,
        control_run,
        treatment_run,
        bootstrap_iterations=100,
    )

    assert report.benchmark_claim_allowed is True
    assert "fixture" not in report.limitations[-1]


def test_zero_control_metric_has_no_relative_delta() -> None:
    """Relative change should be omitted when the control mean is zero."""

    dataset, control, treatment = fixture_artifacts()
    report = compare_systems(
        dataset,
        control,
        treatment,
        bootstrap_iterations=100,
    )
    numerical = next(item for item in report.comparisons if item.metric == "numerical_accuracy")

    assert numerical.control_value == 0.0
    assert numerical.relative_delta is None
