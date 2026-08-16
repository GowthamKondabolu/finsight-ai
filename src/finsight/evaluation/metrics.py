"""Deterministic case-level metrics for retrieval and grounded answers."""

from __future__ import annotations

import math
from dataclasses import dataclass

from finsight.evaluation.contracts import BenchmarkCase, CaseObservation


@dataclass(frozen=True, slots=True)
class CaseMetricResult:
    """Metric values and aggregation support for one benchmark case."""

    values: dict[str, float]
    supported: frozenset[str]


def _retrieval_metrics(
    case: BenchmarkCase,
    observation: CaseObservation,
    *,
    top_k: int,
) -> dict[str, float]:
    """Compute macro-ready recall, reciprocal rank, and graded nDCG."""

    relevance = {item.source_key: item.grade for item in case.relevance_judgments}
    ranked = sorted(observation.retrieved, key=lambda item: item.rank)[:top_k]
    relevant_retrieved = {item.source_key for item in ranked if item.source_key in relevance}
    recall = len(relevant_retrieved) / len(relevance)

    first_relevant_rank = next(
        (item.rank for item in ranked if item.source_key in relevance),
        None,
    )
    reciprocal_rank = 0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank

    dcg = sum(
        (2 ** relevance.get(item.source_key, 0) - 1) / math.log2(index + 2)
        for index, item in enumerate(ranked)
    )
    ideal_grades = sorted(relevance.values(), reverse=True)[:top_k]
    ideal_dcg = sum(
        (2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal_grades)
    )
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    return {
        "retrieval_recall_at_k": recall,
        "retrieval_mrr": reciprocal_rank,
        "retrieval_ndcg_at_k": ndcg,
    }


def _citation_metrics(
    case: BenchmarkCase,
    observation: CaseObservation,
) -> tuple[dict[str, float], frozenset[str]]:
    """Measure citation syntax validity, gold relevance, and claim coverage."""

    citations = [key for claim in observation.claims for key in claim.citation_keys]
    available = set(observation.available_source_keys)
    relevant = {item.source_key for item in case.relevance_judgments}
    values: dict[str, float] = {}
    supported: set[str] = set()

    if citations:
        values["citation_validity"] = sum(key in available for key in citations) / len(citations)
        values["citation_precision"] = sum(key in relevant for key in citations) / len(citations)
        supported.update({"citation_validity", "citation_precision"})
    if observation.claims:
        values["citation_coverage"] = sum(
            bool(claim.citation_keys) for claim in observation.claims
        ) / len(observation.claims)
        supported.add("citation_coverage")

    judged_claims = [claim for claim in observation.claims if claim.supported is not None]
    if judged_claims:
        values["faithfulness"] = sum(claim.supported is True for claim in judged_claims) / len(
            judged_claims
        )
        supported.add("faithfulness")
    return values, frozenset(supported)


def evaluate_case(
    case: BenchmarkCase,
    observation: CaseObservation,
    *,
    top_k: int,
) -> CaseMetricResult:
    """Evaluate one paired case without invoking a model or external service."""

    values = _retrieval_metrics(case, observation, top_k=top_k)
    supported = set(values)

    citation_values, citation_supported = _citation_metrics(case, observation)
    values.update(citation_values)
    supported.update(citation_supported)

    if observation.numerical_checks:
        values["numerical_accuracy"] = sum(
            check.passed for check in observation.numerical_checks
        ) / len(observation.numerical_checks)
        supported.add("numerical_accuracy")

    expected_abstention = case.expected_behavior == "abstain"
    values["abstention_accuracy"] = float(observation.abstained == expected_abstention)
    values["safety_violation_rate"] = float(bool(observation.safety_findings))
    values["failure_rate"] = float(not observation.completed)
    values["latency_ms"] = observation.latency_ms
    supported.update(
        {
            "abstention_accuracy",
            "safety_violation_rate",
            "failure_rate",
            "latency_ms",
        }
    )

    all_judged_claims_supported = all(
        claim.supported is True for claim in observation.claims if claim.supported is not None
    )
    no_unjudged_claims = all(claim.supported is not None for claim in observation.claims)
    all_citations_valid = all(
        key in observation.available_source_keys
        for claim in observation.claims
        for key in claim.citation_keys
    )
    all_numerical_checks_pass = all(check.passed for check in observation.numerical_checks)
    behavior_correct = observation.abstained == expected_abstention
    content_requirement_met = (
        observation.abstained
        if expected_abstention
        else bool(observation.claims or observation.numerical_checks)
    )
    reviewer_allows_completion = observation.reviewer_approved is not False
    verified_completion = (
        observation.completed
        and behavior_correct
        and content_requirement_met
        and not observation.safety_findings
        and no_unjudged_claims
        and all_judged_claims_supported
        and all_citations_valid
        and all_numerical_checks_pass
        and reviewer_allows_completion
    )
    values["verified_task_completion"] = float(verified_completion)
    supported.add("verified_task_completion")

    if observation.reviewer_approved is not None:
        values["reviewer_approval_rate"] = float(observation.reviewer_approved)
        supported.add("reviewer_approval_rate")
    if observation.cost_usd is not None:
        values["cost_usd"] = observation.cost_usd
        supported.add("cost_usd")

    return CaseMetricResult(values=values, supported=frozenset(supported))
