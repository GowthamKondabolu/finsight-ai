"""Offline evaluation contracts, metrics, and paired experiments."""

from finsight.evaluation.contracts import (
    BenchmarkDataset,
    PairedExperimentReport,
    SystemRun,
)
from finsight.evaluation.runner import compare_systems, dataset_fingerprint, evaluate_system

__all__ = [
    "BenchmarkDataset",
    "PairedExperimentReport",
    "SystemRun",
    "compare_systems",
    "dataset_fingerprint",
    "evaluate_system",
]
