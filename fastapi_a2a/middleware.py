"""
fastapi_a2a.middleware — Production-grade middleware for A2A agents.

Provides:
  - SecurityHeadersMiddleware: injects OWASP-recommended security headers
  - RequestIdMiddleware: stamps every request/response with X-Request-ID
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from fastapi_a2a.logging import get_logger

logger = get_logger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds OWASP-recommended security headers to every response.

    Mount via::

        from fastapi_a2a.middleware import SecurityHeadersMiddleware
        app.add_middleware(SecurityHeadersMiddleware)

    Or let FastApiA2A mount it automatically (enabled by default).
    """

    def __init__(self, app: ASGIApp, *, csp: str | None = None) -> None:
        super().__init__(app)
        self._csp = csp or "default-src 'self'"

    async def dispatch(self, request: Request, call_next: object) -> Response:
        response: Response = await call_next(request)  # type: ignore[arg-type]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = self._csp
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), microphone=()"
        )
        # Only set HSTS on HTTPS responses to avoid breaking local dev
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Stamps every request with a unique ``X-Request-ID`` header.

    - If the client sends ``X-Request-ID``, that value is echoed back.
    - Otherwise, a new UUID4 is generated.
    - The ID is stored in ``request.state.request_id`` for use in logs.

    Mount via::

        from fastapi_a2a.middleware import RequestIdMiddleware
        app.add_middleware(RequestIdMiddleware)
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)  # type: ignore[arg-type]
        response.headers["X-Request-ID"] = request_id
        return response
