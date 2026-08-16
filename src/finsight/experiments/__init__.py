"""Controlled experiment registration, assignment, telemetry, and analysis."""

from finsight.experiments.analysis import AssignmentObservation, analyze_experiment
from finsight.experiments.assignment import (
    estimate_binary_sample_size_per_variant,
    hash_randomization_unit,
    select_variant,
    validate_planned_sample_size,
)
from finsight.experiments.contracts import (
    AssignmentResult,
    ExperimentAnalysisReport,
    ExperimentContractError,
    ExperimentEventInput,
    ExperimentEventResult,
    ExperimentNotFoundError,
    ExperimentPlan,
    ExperimentRegistrationResult,
    ExperimentStatusResult,
    MetricPlan,
    VariantPlan,
)

__all__ = [
    "AssignmentObservation",
    "AssignmentResult",
    "ExperimentAnalysisReport",
    "ExperimentContractError",
    "ExperimentEventInput",
    "ExperimentEventResult",
    "ExperimentNotFoundError",
    "ExperimentPlan",
    "ExperimentRegistrationResult",
    "ExperimentStatusResult",
    "MetricPlan",
    "VariantPlan",
    "analyze_experiment",
    "estimate_binary_sample_size_per_variant",
    "hash_randomization_unit",
    "select_variant",
    "validate_planned_sample_size",
]
