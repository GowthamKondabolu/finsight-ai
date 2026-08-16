"""Tests for deterministic retrieval, grounding, safety, and latency metrics."""

import math

import pytest

from finsight.evaluation.contracts import (
    BenchmarkCase,
    CaseObservation,
    ClaimObservation,
    NumericalObservation,
    RetrievedEvidence,
    SafetyFinding,
)
from finsight.evaluation.metrics import evaluate_case


def benchmark_case(*, behavior: str = "answer") -> BenchmarkCase:
    """Create one graded relevance case."""

    return BenchmarkCase.model_validate(
        {
            "case_id": "case-001",
            "question": "What changed?",
            "expected_behavior": behavior,
            "relevance_judgments": [
                {"source_key": "source-a", "grade": 3},
                {"source_key": "source-b", "grade": 1},
            ],
        }
    )


def observation(**updates: object) -> CaseObservation:
    """Create one successful, fully judged observation."""

    payload: dict[str, object] = {
        "case_id": "case-001",
        "completed": True,
        "abstained": False,
        "retrieved": [
            {"source_key": "irrelevant", "rank": 1},
            {"source_key": "source-a", "rank": 2},
        ],
        "available_source_keys": ["irrelevant", "source-a", "fact-1"],
        "claims": [
            {
                "claim_id": "C1",
                "statement": "Supported claim.",
                "citation_keys": ["source-a"],
                "supported": True,
            }
        ],
        "numerical_checks": [{"check_id": "N1", "passed": True}],
        "safety_findings": [],
        "reviewer_approved": True,
        "latency_ms": 125.0,
        "cost_usd": 0.01,
    }
    payload.update(updates)
    return CaseObservation.model_validate(payload)


def test_case_metrics_separate_retrieval_from_answer_quality() -> None:
    """Graded ranking and grounded-answer metrics should remain independent."""

    result = evaluate_case(benchmark_case(), observation(), top_k=2)

    assert result.values["retrieval_recall_at_k"] == 0.5
    assert result.values["retrieval_mrr"] == 0.5
    assert result.values["retrieval_ndcg_at_k"] == pytest.approx(
        (7 / math.log2(3)) / (7 + 1 / math.log2(3))
    )
    assert result.values["citation_validity"] == 1.0
    assert result.values["citation_precision"] == 1.0
    assert result.values["citation_coverage"] == 1.0
    assert result.values["faithfulness"] == 1.0
    assert result.values["numerical_accuracy"] == 1.0
    assert result.values["verified_task_completion"] == 1.0
    assert result.values["reviewer_approval_rate"] == 1.0
    assert result.values["cost_usd"] == 0.01


def test_invalid_citation_and_failed_judgments_block_verified_completion() -> None:
    """Unsupported or numerically invalid output must fail the primary metric."""

    result = evaluate_case(
        benchmark_case(),
        observation(
            claims=[
                {
                    "claim_id": "C1",
                    "statement": "Unsupported claim.",
                    "citation_keys": ["unknown-source"],
                    "supported": False,
                }
            ],
            numerical_checks=[{"check_id": "N1", "passed": False}],
            safety_findings=[
                {
                    "policy_id": "unsupported-claim",
                    "severity": "high",
                    "description": "Unsupported output.",
                }
            ],
            reviewer_approved=False,
        ),
        top_k=2,
    )

    assert result.values["citation_validity"] == 0.0
    assert result.values["citation_precision"] == 0.0
    assert result.values["faithfulness"] == 0.0
    assert result.values["numerical_accuracy"] == 0.0
    assert result.values["safety_violation_rate"] == 1.0
    assert result.values["verified_task_completion"] == 0.0


def test_unjudged_claim_is_excluded_from_faithfulness_and_blocks_completion() -> None:
    """Missing support labels must not be silently treated as correct."""

    result = evaluate_case(
        benchmark_case(),
        observation(
            claims=[
                ClaimObservation(
                    claim_id="C1",
                    statement="Unjudged claim.",
                    citation_keys=("source-a",),
                    supported=None,
                )
            ],
        ),
        top_k=2,
    )

    assert "faithfulness" not in result.supported
    assert result.values["verified_task_completion"] == 0.0


def test_abstention_case_can_complete_without_claims() -> None:
    """Safe abstention should count as verified completion when expected."""

    result = evaluate_case(
        benchmark_case(behavior="abstain"),
        observation(
            abstained=True,
            claims=[],
            numerical_checks=[],
            reviewer_approved=True,
        ),
        top_k=2,
    )

    assert result.values["abstention_accuracy"] == 1.0
    assert result.values["verified_task_completion"] == 1.0
    assert "citation_coverage" not in result.supported
    assert "numerical_accuracy" not in result.supported


def test_answer_case_without_claims_or_checks_is_not_verified() -> None:
    """A successful empty execution must not count as completed analysis."""

    result = evaluate_case(
        benchmark_case(),
        observation(
            claims=[],
            numerical_checks=[],
            reviewer_approved=True,
        ),
        top_k=2,
    )

    assert result.values["abstention_accuracy"] == 1.0
    assert result.values["verified_task_completion"] == 0.0


def test_failed_run_tracks_failure_without_verified_completion() -> None:
    """Execution errors should be visible as failures, not low-quality answers."""

    result = evaluate_case(
        benchmark_case(),
        observation(
            completed=False,
            error_type="provider_timeout",
            claims=[],
            numerical_checks=[],
            reviewer_approved=None,
            cost_usd=None,
        ),
        top_k=1,
    )

    assert result.values["failure_rate"] == 1.0
    assert result.values["verified_task_completion"] == 0.0
    assert "reviewer_approval_rate" not in result.supported
    assert "cost_usd" not in result.supported


def test_metric_contract_objects_are_frozen_and_typed() -> None:
    """Representative observation components should retain their labels."""

    check = NumericalObservation(check_id="N1", passed=True)
    finding = SafetyFinding(
        policy_id="non-advisory",
        severity="medium",
        description="Test finding.",
    )
    evidence = RetrievedEvidence(source_key="source-a", rank=1)

    assert check.passed is True
    assert finding.policy_id == "non-advisory"
    assert evidence.score is None
