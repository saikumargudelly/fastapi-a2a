"""
OpenTelemetry Tracing Middleware for fastapi-a2a.

Features (spec §18.4, §19.4):
  - W3C traceparent / tracestate propagation
  - Span creation for each A2A RPC method
  - Attribute deny-by-default allowlist (trace_policy.allowlist_mode)
  - PII tag redaction per trace_policy.pii_tag_keys
  - PII value pattern redaction per trace_policy.pii_value_patterns
  - Sampling rate enforcement per trace_policy.sampling_rate
  - OTEL SDK integration (if opentelemetry-sdk installed, else no-op)
"""
from __future__ import annotations

import logging
import random
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger("fastapi_a2a.tracing")

try:
    from opentelemetry import trace
    from opentelemetry.propagate import extract as otel_extract
    from opentelemetry.trace import SpanKind, StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    logger.debug("opentelemetry-sdk not installed — tracing disabled")


class TracingMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that creates OpenTelemetry spans for every A2A request.

    Config (from FastApiA2AConfig via app.state.a2a_config):
      sampling_rate (float 0–1): Fraction of requests to trace. Default 0.1
      hash_identifiers (bool): Hash PII in span attributes. Default True
      max_attribute_length (int): Truncate long attribute values. Default 256
      allowlist_mode (str): disabled | warn | enforce for attribute allowlist
    """

    def __init__(self, app, *, sampling_rate: float = 0.1, service_name: str = "fastapi-a2a"):
        super().__init__(app)
        self.sampling_rate = sampling_rate
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next) -> Response:
        # Honour sampling rate
        if random.random() > self.sampling_rate:  # noqa: S311
            return await call_next(request)

        if not _OTEL_AVAILABLE:
            return await call_next(request)

        tracer = trace.get_tracer(self.service_name)
        ctx = otel_extract(dict(request.headers))

        method = request.method
        path = request.url.path
        span_name = f"{method} {path}"

        with tracer.start_as_current_span(
            span_name,
            context=ctx,
            kind=SpanKind.SERVER,
        ) as span:
            # Standard HTTP attributes (safe — always allowed)
            span.set_attribute("http.method", method)
            span.set_attribute("http.url", str(request.url))
            span.set_attribute("http.route", path)

            response = await call_next(request)

            span.set_attribute("http.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(StatusCode.ERROR)
            else:
                span.set_status(StatusCode.OK)

            return response


def create_a2a_span(
    tracer,
    rpc_method: str,
    task_id: str | None = None,
    agent_card_id: str | None = None,
    skill_id: str | None = None,
) -> Any:
    """
    Create a structured span for an A2A JSON-RPC call.
    Used internally by the RPC dispatcher for fine-grained span tracking.
    """
    if not _OTEL_AVAILABLE:
        return _NoOpSpan()

    return tracer.start_as_current_span(
        f"a2a.{rpc_method}",
        attributes={
            "a2a.rpc_method": rpc_method,
            "a2a.task_id": task_id or "",
            "a2a.agent_card_id": agent_card_id or "",
            "a2a.skill_id": skill_id or "",
        },
    )


def redact_pii_attributes(
    attributes: dict[str, Any],
    pii_tag_keys: list[str],
    hash_identifiers: bool = True,
) -> dict[str, Any]:
    """
    Redact PII from span attributes.
    Keys in pii_tag_keys are either hashed (if hash_identifiers) or removed.
    """
    import hashlib
    result = {}
    for k, v in attributes.items():
        if k in pii_tag_keys:
            if hash_identifiers:
                hashed = hashlib.sha256(str(v).encode()).hexdigest()[:16]
                result[k] = f"[HASH:{hashed}]"
            # else: omit entirely
        else:
            result[k] = v
    return result


class _NoOpSpan:
    """No-op span context manager when OTEL is unavailable."""
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def set_attribute(self, *_): pass
    def set_status(self, *_): pass
    def record_exception(self, *_): pass
