"""Production observability primitives for FinSight services."""

from finsight.observability.runtime import (
    ObservabilityRuntime,
    create_observability_runtime,
    operation_span,
)

__all__ = [
    "ObservabilityRuntime",
    "create_observability_runtime",
    "operation_span",
]
