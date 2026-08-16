"""Low-cardinality HTTP telemetry and request correlation middleware."""

from __future__ import annotations

import re
import time
from uuid import uuid4

import structlog
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

from finsight.observability.runtime import ObservabilityRuntime

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


def resolve_request_id(candidate: str | None) -> str:
    """Accept a bounded correlation ID or generate an opaque UUID."""

    if candidate is not None and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


class RequestObservabilityMiddleware:
    """Emit safe spans, metrics, logs, and correlation headers for HTTP calls."""

    def __init__(self, app: ASGIApp, runtime: ObservabilityRuntime) -> None:
        self.app = app
        self.runtime = runtime
        self.logger = structlog.get_logger("finsight.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = resolve_request_id(headers.get(REQUEST_ID_HEADER))
        method = str(scope["method"])
        status_code = 500
        started_at = time.perf_counter()
        runtime_token = self.runtime.activate()
        clear_contextvars()
        bind_contextvars(request_id=request_id)

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["Cache-Control"] = "no-store"
            await send(message)

        parent_context = extract(dict(headers.items()))
        with self.runtime.tracer.start_as_current_span(
            f"{method} request",
            context=parent_context,
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": method,
                "finsight.request.id": request_id,
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            failed = False
            try:
                await self.app(scope, receive, send_with_headers)
            except Exception as exc:
                failed = True
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                self.logger.error(
                    "http.request.failed",
                    method=method,
                    route=_route_path(scope),
                    status_code=status_code,
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                route = _route_path(scope)
                duration_seconds = time.perf_counter() - started_at
                span.update_name(f"{method} {route}")
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                metric_attributes: dict[str, str | int] = {
                    "http.request.method": method,
                    "http.route": route,
                    "http.response.status_code": status_code,
                }
                self.runtime.request_count.add(1, metric_attributes)
                self.runtime.request_duration.record(duration_seconds, metric_attributes)
                if not failed:
                    self.logger.info(
                        "http.request.completed",
                        method=method,
                        route=route,
                        status_code=status_code,
                        duration_ms=round(duration_seconds * 1_000, 3),
                    )
                clear_contextvars()
                self.runtime.deactivate(runtime_token)


def _route_path(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"
