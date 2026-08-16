"""Shared controlled-experiment fixtures for focused unit tests."""

from datetime import UTC, datetime

from finsight.experiments.contracts import ExperimentPlan, MetricPlan, VariantPlan


def experiment_plan(**updates: object) -> ExperimentPlan:
    """Return a powered two-arm plan with outcome and operational guardrails."""

    payload: dict[str, object] = {
        "experiment_key": "answer-workflow-v1",
        "name": "Grounded answer workflow",
        "hypothesis": "The verified workflow improves task completion.",
        "randomization_unit": "session",
        "assignment_salt_version": 1,
        "primary_metric": MetricPlan(
            metric_name="verified_task_completion",
            kind="binary",
            direction="higher_is_better",
            minimum_practical_effect=0.1,
        ),
        "guardrail_metrics": (
            MetricPlan(
                metric_name="safety_violation",
                kind="binary",
                direction="lower_is_better",
                maximum_degradation=0.01,
            ),
            MetricPlan(
                metric_name="latency_ms",
                kind="continuous",
                direction="lower_is_better",
                maximum_degradation=500.0,
            ),
        ),
        "expected_baseline_rate": 0.5,
        "planned_sample_size_per_variant": 400,
        "confidence_level": 0.95,
        "statistical_power": 0.8,
        "variants": (
            VariantPlan(
                variant_key="control",
                description="Vector retrieval and one grounded generation step.",
                allocation_basis_points=5_000,
                is_control=True,
                configuration={"pipeline": "vector_rag"},
            ),
            VariantPlan(
                variant_key="treatment",
                description="Hybrid retrieval and verified LangGraph workflow.",
                allocation_basis_points=5_000,
                configuration={"pipeline": "verified_langgraph"},
            ),
        ),
        "git_sha": "8926531",
        "offline_report_sha256": "a" * 64,
        "starts_at": datetime(2026, 8, 16, tzinfo=UTC),
        "ends_at": datetime(2026, 9, 16, tzinfo=UTC),
    }
    payload.update(updates)
    return ExperimentPlan.model_validate(payload)
