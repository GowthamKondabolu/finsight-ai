"""Tests for versioned evaluation artifacts and bounded JSON I/O."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import finsight.evaluation.io as io_module
from finsight.evaluation.contracts import (
    BenchmarkCase,
    BenchmarkDataset,
    CaseObservation,
    ClaimObservation,
    RelevanceJudgment,
    RetrievedEvidence,
    SystemRun,
)
from finsight.evaluation.io import load_dataset, load_system_run
from finsight.evaluation.runner import dataset_fingerprint

FIXTURES = Path("evals/fixtures")
EXPECTED_FINGERPRINT = "de79a46354c1bc2e97ad5db7c835c14125e7c25ed3634f99e850559cdbb02d64"


def test_fixture_artifacts_are_versioned_and_fingerprint_bound() -> None:
    """Fixture runs should reference the exact canonical dataset content."""

    dataset = load_dataset(FIXTURES / "synthetic_dataset_v1.json")
    control = load_system_run(FIXTURES / "control_run_v1.json")
    treatment = load_system_run(FIXTURES / "treatment_run_v1.json")

    assert dataset.data_classification == "synthetic_fixture"
    assert dataset_fingerprint(dataset) == EXPECTED_FINGERPRINT
    assert control.dataset_fingerprint == EXPECTED_FINGERPRINT
    assert treatment.dataset_fingerprint == EXPECTED_FINGERPRINT


def test_benchmark_contract_normalizes_questions_and_policy_tags() -> None:
    """Human-authored labels should have stable text and ordering."""

    case = BenchmarkCase(
        case_id="case-001",
        question="  What changed?  ",
        expected_behavior="answer",
        relevance_judgments=(RelevanceJudgment(source_key="source-1", grade=3),),
        policy_tags=("Safety", "Evidence"),
    )

    assert case.question == "What changed?"
    assert case.policy_tags == ("evidence", "safety")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("question", " ", "question cannot be blank"),
        ("policy_tags", ("safety", "SAFETY"), "policy_tags"),
        ("policy_tags", ("",), "policy_tags"),
        (
            "relevance_judgments",
            (
                RelevanceJudgment(source_key="source-1", grade=1),
                RelevanceJudgment(source_key="source-1", grade=3),
            ),
            "source keys must be unique",
        ),
    ],
)
def test_benchmark_case_rejects_ambiguous_labels(
    field: str,
    value: object,
    message: str,
) -> None:
    """Blank, duplicated, or contradictory labels must fail validation."""

    payload: dict[str, object] = {
        "case_id": "case-001",
        "question": "What changed?",
        "expected_behavior": "answer",
        "relevance_judgments": [{"source_key": "source-1", "grade": 3}],
    }
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        BenchmarkCase.model_validate(payload)


def test_dataset_rejects_naive_timestamp_and_duplicate_cases() -> None:
    """Dataset versions require timezone-aware identity and unique cases."""

    case = BenchmarkCase(
        case_id="case-001",
        question="What changed?",
        expected_behavior="answer",
        relevance_judgments=(RelevanceJudgment(source_key="source-1", grade=3),),
    )
    with pytest.raises(ValidationError, match="timezone"):
        BenchmarkDataset(
            dataset_id="dataset-v1",
            description="test dataset",
            data_classification="synthetic_fixture",
            created_at=datetime(2026, 1, 1),
            cases=(case, case.model_copy(update={"case_id": "case-002"})),
        )
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        BenchmarkDataset(
            dataset_id="dataset-v1",
            description="test dataset",
            data_classification="synthetic_fixture",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            cases=(case, case),
        )


def valid_observation_payload() -> dict[str, object]:
    """Return one schema-valid case observation payload."""

    return {
        "case_id": "case-001",
        "completed": True,
        "abstained": False,
        "retrieved": [{"source_key": "source-1", "rank": 1}],
        "available_source_keys": ["source-1"],
        "claims": [
            {
                "claim_id": "C1",
                "statement": "Supported statement.",
                "citation_keys": ["source-1"],
                "supported": True,
            }
        ],
        "latency_ms": 10,
    }


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {
                "retrieved": [
                    {"source_key": "source-1", "rank": 2},
                    {"source_key": "source-2", "rank": 3},
                ],
                "available_source_keys": ["source-1", "source-2"],
            },
            "ranks must be contiguous",
        ),
        (
            {
                "retrieved": [
                    {"source_key": "source-1", "rank": 1},
                    {"source_key": "source-1", "rank": 2},
                ]
            },
            "retrieved source keys must be unique",
        ),
        ({"available_source_keys": ["source-1", "source-1"]}, "available source keys"),
        ({"available_source_keys": []}, "must be included"),
        (
            {
                "claims": [
                    {"claim_id": "C1", "statement": "A"},
                    {"claim_id": "C1", "statement": "B"},
                ]
            },
            "claim identifiers",
        ),
        (
            {
                "numerical_checks": [
                    {"check_id": "N1", "passed": True},
                    {"check_id": "N1", "passed": False},
                ]
            },
            "numerical check identifiers",
        ),
        ({"error_type": "failure"}, "completed observations"),
        ({"completed": False}, "failed observations"),
    ],
)
def test_case_observation_rejects_ambiguous_recorded_outputs(
    updates: dict[str, object],
    message: str,
) -> None:
    """Recorded results must have deterministic identities and failure state."""

    payload = valid_observation_payload()
    payload.update(updates)
    with pytest.raises(ValidationError, match=message):
        CaseObservation.model_validate(payload)


def test_claim_contract_rejects_blank_or_duplicate_citations() -> None:
    """Claim citations should be unambiguous before metric calculation."""

    with pytest.raises(ValidationError, match="citation keys"):
        ClaimObservation(
            claim_id="C1",
            statement="Claim.",
            citation_keys=("source-1", "source-1"),
        )


def test_system_run_rejects_naive_time_and_duplicate_observations() -> None:
    """Run records require auditable time and exactly one output per case."""

    observation = CaseObservation.model_validate(valid_observation_payload())
    payload = {
        "run_id": "run-001",
        "system_name": "system",
        "run_type": "synthetic_fixture",
        "dataset_id": "dataset-v1",
        "dataset_fingerprint": "a" * 64,
        "created_at": "2026-01-01T00:00:00Z",
        "observations": [observation, observation.model_copy(update={"case_id": "case-002"})],
    }
    with pytest.raises(ValidationError, match="timezone"):
        SystemRun.model_validate({**payload, "created_at": datetime(2026, 1, 1)})
    with pytest.raises(ValidationError, match="must be unique"):
        SystemRun.model_validate({**payload, "observations": [observation, observation]})


def test_loader_rejects_oversized_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Evaluation inputs should be bounded before their contents are read."""

    artifact = tmp_path / "large.json"
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(io_module, "MAX_EVALUATION_FILE_BYTES", 1)

    with pytest.raises(ValueError, match="exceeds"):
        load_dataset(artifact)


def test_retrieved_evidence_accepts_optional_transparent_score() -> None:
    """Recorded rankings may preserve system scores without depending on them."""

    item = RetrievedEvidence(source_key="source-1", rank=1, score=0.25)

    assert item.score == 0.25
