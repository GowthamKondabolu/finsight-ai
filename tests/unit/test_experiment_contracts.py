"""Tests for preregistered experiment and telemetry contracts."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from finsight.experiments.contracts import (
    ExperimentContractError,
    ExperimentEventInput,
    ExperimentPlan,
    MetricPlan,
    VariantPlan,
)
from finsight.experiments.io import MAX_EXPERIMENT_PLAN_BYTES, load_experiment_plan
from tests.unit.experiment_helpers import experiment_plan


def test_plan_fingerprint_is_stable_and_covers_configuration() -> None:
    """Canonical plan identity should be stable and change with treatment configuration."""

    plan = experiment_plan()
    changed = experiment_plan(
        variants=(
            plan.variants[0],
            plan.variants[1].model_copy(update={"configuration": {"pipeline": "changed"}}),
        )
    )

    assert plan.fingerprint() == experiment_plan().fingerprint()
    assert plan.fingerprint() != changed.fingerprint()
    assert plan.metric_names == {
        "verified_task_completion",
        "safety_violation",
        "latency_ms",
    }


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {
                "variants": (
                    VariantPlan(
                        variant_key="aa",
                        description="a",
                        allocation_basis_points=1,
                        is_control=True,
                    ),
                    VariantPlan(variant_key="bb", description="b", allocation_basis_points=1),
                )
            },
            "total 10000",
        ),
        (
            {
                "variants": (
                    VariantPlan(variant_key="aa", description="a", allocation_basis_points=5_000),
                    VariantPlan(variant_key="bb", description="b", allocation_basis_points=5_000),
                )
            },
            "exactly one",
        ),
        (
            {
                "guardrail_metrics": (
                    MetricPlan(
                        metric_name="unsafe_rate", kind="binary", direction="lower_is_better"
                    ),
                )
            },
            "requires maximum_degradation",
        ),
        (
            {
                "primary_metric": MetricPlan(
                    metric_name="completion",
                    kind="continuous",
                    direction="higher_is_better",
                    minimum_practical_effect=1.0,
                )
            },
            "binary primary",
        ),
        (
            {
                "primary_metric": MetricPlan(
                    metric_name="completion",
                    kind="binary",
                    direction="higher_is_better",
                    minimum_practical_effect=0.0,
                )
            },
            "positive practical effect",
        ),
        (
            {
                "primary_metric": MetricPlan(
                    metric_name="completion",
                    kind="binary",
                    direction="higher_is_better",
                    minimum_practical_effect=0.6,
                )
            },
            "treatment rate",
        ),
        (
            {"ends_at": datetime(2026, 8, 15, tzinfo=UTC)},
            "later than",
        ),
    ],
)
def test_plan_rejects_invalid_preregistration(updates: dict[str, object], message: str) -> None:
    """Traffic, metrics, power inputs, and schedules must be fixed coherently."""

    with pytest.raises(ValidationError, match=message):
        experiment_plan(**updates)


def test_plan_rejects_metric_name_collisions_and_primary_degradation() -> None:
    """The primary outcome cannot also be a guardrail or use a degradation bound."""

    duplicate = MetricPlan(
        metric_name="verified_task_completion",
        kind="binary",
        direction="higher_is_better",
        maximum_degradation=0.1,
    )
    with pytest.raises(ValidationError, match="metric names must be unique"):
        experiment_plan(guardrail_metrics=(duplicate,))

    primary = experiment_plan().primary_metric.model_copy(update={"maximum_degradation": 0.1})
    with pytest.raises(ValidationError, match="primary metric"):
        experiment_plan(primary_metric=primary)


def test_contracts_reject_naive_timestamps_and_oversized_binary_thresholds() -> None:
    """Time and probability thresholds must be unambiguous and bounded."""

    with pytest.raises(ValidationError, match="include a timezone"):
        experiment_plan(starts_at=datetime(2026, 8, 16))
    with pytest.raises(ValidationError, match="cannot exceed one"):
        MetricPlan(
            metric_name="unsafe_rate",
            kind="binary",
            direction="lower_is_better",
            maximum_degradation=1.1,
        )
    with pytest.raises(ValidationError, match="must be finite"):
        MetricPlan(
            metric_name="latency_ms",
            kind="continuous",
            direction="lower_is_better",
            maximum_degradation=float("inf"),
        )
    with pytest.raises(ValidationError, match="description cannot be blank"):
        VariantPlan(
            variant_key="control",
            description=" ",
            allocation_basis_points=5_000,
            is_control=True,
        )


def test_event_contract_separates_exposures_and_outcomes() -> None:
    """Exposure markers cannot smuggle metric values and outcomes must be finite."""

    event_id = uuid4()
    exposure = ExperimentEventInput(
        assignment_id=event_id,
        event_key="exposure:1",
        event_type="exposure",
        occurred_at=datetime.now(UTC),
    )
    assert exposure.metric_name is None

    with pytest.raises(ValidationError, match="cannot contain a metric"):
        ExperimentEventInput(
            assignment_id=event_id,
            event_key="exposure:2",
            event_type="exposure",
            metric_name="unsafe_rate",
            metric_value=1.0,
            occurred_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError, match="require metric_name"):
        ExperimentEventInput(
            assignment_id=event_id,
            event_key="outcome:1",
            event_type="outcome",
            occurred_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError, match="must be finite"):
        ExperimentEventInput(
            assignment_id=event_id,
            event_key="outcome:2",
            event_type="outcome",
            metric_name="unsafe_rate",
            metric_value=float("inf"),
            occurred_at=datetime.now(UTC),
        )


def test_plan_io_loads_strict_json_and_maps_failures(tmp_path: Path) -> None:
    """Plan files should be bounded, strict, and safe to diagnose."""

    path = tmp_path / "plan.json"
    path.write_text(experiment_plan().model_dump_json(), encoding="utf-8")
    assert load_experiment_plan(path).experiment_key == "answer-workflow-v1"

    path.write_text("{", encoding="utf-8")
    with pytest.raises(ExperimentContractError, match="valid JSON"):
        load_experiment_plan(path)

    path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
    with pytest.raises(ExperimentContractError, match="schema validation"):
        load_experiment_plan(path)

    with pytest.raises(ExperimentContractError, match="could not read"):
        load_experiment_plan(tmp_path / "missing.json")

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_EXPERIMENT_PLAN_BYTES + 1))
    with pytest.raises(ExperimentContractError, match="one-megabyte"):
        load_experiment_plan(oversized)


def test_plan_schema_forbids_unknown_fields() -> None:
    """Silent configuration drift must fail preregistration."""

    payload = experiment_plan().model_dump(mode="json")
    payload["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        ExperimentPlan.model_validate(payload)
