"""Tests for deterministic assignment and sample-size planning."""

import pytest

from finsight.experiments.assignment import (
    estimate_binary_sample_size_per_variant,
    hash_randomization_unit,
    select_variant,
    validate_planned_sample_size,
)
from finsight.experiments.contracts import ExperimentContractError
from tests.unit.experiment_helpers import experiment_plan

SECRET = "test-only-secret-with-at-least-32-characters"


def test_unit_hmac_is_stable_scoped_and_does_not_contain_raw_identity() -> None:
    """Assignment identity should be reproducible but unlinkable across experiments."""

    first = hash_randomization_unit(
        secret=SECRET,
        experiment_key="experiment-one",
        assignment_salt_version=1,
        unit_id=" analyst-session-123 ",
    )
    repeated = hash_randomization_unit(
        secret=SECRET,
        experiment_key="experiment-one",
        assignment_salt_version=1,
        unit_id="analyst-session-123",
    )
    scoped = hash_randomization_unit(
        secret=SECRET,
        experiment_key="experiment-two",
        assignment_salt_version=1,
        unit_id="analyst-session-123",
    )

    assert first == repeated
    assert first != scoped
    assert len(first) == 64
    assert "analyst" not in first


@pytest.mark.parametrize(
    ("secret", "unit_id", "message"),
    [
        ("short", "session", "at least 32"),
        (SECRET, " ", "cannot be blank"),
        (SECRET, "x" * 501, "too long"),
    ],
)
def test_unit_hmac_rejects_unsafe_inputs(secret: str, unit_id: str, message: str) -> None:
    """Weak keys and ambiguous identifiers should never reach persistence."""

    with pytest.raises(ExperimentContractError, match=message):
        hash_randomization_unit(
            secret=secret,
            experiment_key="experiment-one",
            assignment_salt_version=1,
            unit_id=unit_id,
        )


def test_variant_selection_is_sticky_and_uses_both_arms() -> None:
    """The same HMAC should remain sticky while a sample spans both allocations."""

    plan = experiment_plan()
    hashes = [f"{index:064x}" for index in range(1, 300)]
    assignments = [select_variant(plan, unit_hash).variant_key for unit_hash in hashes]

    assert select_variant(plan, hashes[0]) == select_variant(plan, hashes[0])
    assert set(assignments) == {"control", "treatment"}

    with pytest.raises(ExperimentContractError, match="SHA-256"):
        select_variant(plan, "not-a-digest")


def test_power_estimate_is_reproducible_and_enforced() -> None:
    """The registered sample size cannot undercut its own alpha, power, and MDE inputs."""

    plan = experiment_plan()
    assert estimate_binary_sample_size_per_variant(plan) == 388
    validate_planned_sample_size(plan)

    underpowered = experiment_plan(planned_sample_size_per_variant=387)
    with pytest.raises(ExperimentContractError, match=r"387 < 388"):
        validate_planned_sample_size(underpowered)
