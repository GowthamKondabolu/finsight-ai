# Production observability

FinSight emits vendor-neutral OpenTelemetry traces and metrics plus structured application logs. Export is optional in local development and uses OTLP over HTTP when configured. The runtime can send traces to an OpenTelemetry Collector, an APM backend, or Langfuse without changing application code.

## Signal model

| Signal | Examples | Cardinality policy |
|---|---|---|
| HTTP spans | method, route template, status, duration, request ID | No query strings, bodies, client addresses, or raw identities |
| Domain spans | retrieval, investigation, workflow start/review/read | Bounded operation names and numeric limits only |
| GenAI spans | provider, operation, requested model, token counts | No prompts, evidence text, model output, API keys, or user identity |
| Metrics | request count and duration by method, route, and status | Route templates prevent unbounded path labels |
| Logs | request ID, route, status, duration | Credential-bearing keys are recursively redacted before rendering |

The request middleware accepts a bounded `X-Request-ID` or creates a UUID, returns it in the response, and binds it to every request log. It also adds `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

## Configuration

```text
FINSIGHT_OBSERVABILITY_ENABLED=true
FINSIGHT_OTEL_SERVICE_NAME=finsight-api
FINSIGHT_OTEL_TRACE_SAMPLE_RATIO=1.0
FINSIGHT_OTEL_TRACES_ENDPOINT=https://collector.example/v1/traces
FINSIGHT_OTEL_METRICS_ENDPOINT=https://collector.example/v1/metrics
FINSIGHT_OTEL_EXPORT_HEADERS=Authorization=Bearer opaque-token
FINSIGHT_OTEL_METRIC_EXPORT_INTERVAL_SECONDS=60
FINSIGHT_LOG_JSON=true
```

The endpoint may be either a signal endpoint or a base OTLP endpoint; FinSight appends `/v1/traces` or `/v1/metrics` when absent. Export headers are stored as a Pydantic secret and are never written to logs.

When export is disabled or no endpoint is configured, request handling continues normally. Exporters batch in the background and flush during graceful application shutdown. A telemetry-backend outage must never make an investigation endpoint unavailable.

## Langfuse

Langfuse accepts OTLP/HTTP traces. Configure only `FINSIGHT_OTEL_TRACES_ENDPOINT` because the Langfuse OTLP endpoint stores traces, not general application metrics. Use the region-specific `/api/public/otel` endpoint and provide the Basic authorization and current ingestion-version headers through `FINSIGHT_OTEL_EXPORT_HEADERS`.

Prompt, evidence, response, and tool payload capture is intentionally disabled. Model name and provider-reported input/output token counts are sufficient for latency and cost analysis while minimizing disclosure risk. Apply pricing in the observability backend from a versioned price table rather than hard-coding prices that change over time.

## Health semantics

- `GET /health` is liveness only and does not call dependencies.
- `GET /ready` checks PostgreSQL under `FINSIGHT_READINESS_TIMEOUT_SECONDS` and returns a generic `503` on failure.
- `/v1/*` routes require the configured bearer token; operations endpoints remain unauthenticated for container and load-balancer probes.

## Verification

Unit and security tests validate correlation headers, span attributes, recursive redaction, safe readiness failures, disabled telemetry, and authenticated API access. Container smoke tests exercise `/health` and `/ready` against the composed application.

The design follows the official [OpenTelemetry Python exporter guidance](https://opentelemetry.io/docs/languages/python/exporters/) and [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/). Langfuse configuration follows its [native OpenTelemetry integration](https://langfuse.com/integrations/native/opentelemetry).
