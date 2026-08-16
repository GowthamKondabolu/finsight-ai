"""OpenTelemetry runtime with optional OTLP/HTTP export."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from finsight import __version__
from finsight.config.settings import Settings
from finsight.observability.logging import configure_logging

_ACTIVE_RUNTIME: ContextVar[ObservabilityRuntime | None] = ContextVar(
    "finsight_observability_runtime",
    default=None,
)


def _signal_endpoint(endpoint: object, signal: str) -> str:
    value = str(endpoint).rstrip("/")
    suffix = f"/v1/{signal}"
    return value if value.endswith(suffix) else f"{value}{suffix}"


def _export_headers(settings: Settings) -> dict[str, str]:
    if settings.otel_export_headers is None:
        return {}

    raw_headers = settings.otel_export_headers.get_secret_value()
    headers: dict[str, str] = {}
    for item in raw_headers.split(","):
        name, separator, value = item.strip().partition("=")
        if not separator or not name.strip() or not value.strip():
            raise ValueError("OTLP export headers must use comma-separated name=value pairs")
        headers[name.strip()] = value.strip()
    return headers


@dataclass(slots=True)
class ObservabilityRuntime:
    """Owned telemetry providers and HTTP instruments for one application."""

    tracer: Tracer
    meter: Meter
    request_count: Counter
    request_duration: Histogram
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None

    def activate(self) -> Token[ObservabilityRuntime | None]:
        """Make this runtime available to nested domain operations."""

        return _ACTIVE_RUNTIME.set(self)

    @staticmethod
    def deactivate(token: Token[ObservabilityRuntime | None]) -> None:
        """Restore the runtime context that preceded one request."""

        _ACTIVE_RUNTIME.reset(token)

    def force_flush(self, timeout_millis: int = 5_000) -> bool:
        """Flush buffered telemetry, primarily for tests and graceful shutdown."""

        trace_flushed = (
            self.tracer_provider.force_flush(timeout_millis)
            if self.tracer_provider is not None
            else True
        )
        metric_flushed = (
            self.meter_provider.force_flush(timeout_millis)
            if self.meter_provider is not None
            else True
        )
        return bool(trace_flushed and metric_flushed)

    def shutdown(self) -> None:
        """Flush and release exporters without affecting request handling."""

        if self.tracer_provider is not None:
            self.tracer_provider.shutdown()
        if self.meter_provider is not None:
            self.meter_provider.shutdown()


def create_observability_runtime(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
) -> ObservabilityRuntime:
    """Build isolated providers; export only when an endpoint is configured."""

    configure_logging(settings)

    if not settings.observability_enabled:
        tracer = trace.NoOpTracerProvider().get_tracer("finsight")
        meter = metrics.NoOpMeterProvider().get_meter("finsight")
        return ObservabilityRuntime(
            tracer=tracer,
            meter=meter,
            request_count=meter.create_counter("finsight.http.server.requests"),
            request_duration=meter.create_histogram(
                "finsight.http.server.duration",
                unit="s",
            ),
        )

    resource = Resource.create(
        {
            SERVICE_NAME: settings.otel_service_name,
            "service.version": __version__,
            "deployment.environment.name": settings.environment,
        }
    )
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )

    if span_exporter is not None:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif settings.otel_traces_endpoint is not None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_signal_endpoint(settings.otel_traces_endpoint, "traces"),
                    headers=_export_headers(settings),
                )
            )
        )

    readers: list[MetricReader] = []
    if metric_reader is not None:
        readers.append(metric_reader)
    elif settings.otel_metrics_endpoint is not None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=_signal_endpoint(settings.otel_metrics_endpoint, "metrics"),
                    headers=_export_headers(settings),
                ),
                export_interval_millis=(settings.otel_metric_export_interval_seconds * 1_000),
            )
        )

    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    tracer = tracer_provider.get_tracer("finsight", __version__)
    meter = meter_provider.get_meter("finsight", __version__)
    return ObservabilityRuntime(
        tracer=tracer,
        meter=meter,
        request_count=meter.create_counter(
            "finsight.http.server.requests",
            description="Completed FinSight HTTP requests",
        ),
        request_duration=meter.create_histogram(
            "finsight.http.server.duration",
            unit="s",
            description="FinSight HTTP request duration",
        ),
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )


@contextmanager
def operation_span(
    name: str,
    attributes: Mapping[str, str | int | float | bool] | None = None,
) -> Iterator[Span]:
    """Create a child span when an instrumented request is active."""

    runtime = _ACTIVE_RUNTIME.get()
    tracer = runtime.tracer if runtime is not None else trace.NoOpTracerProvider().get_tracer(name)
    with tracer.start_as_current_span(
        name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise
