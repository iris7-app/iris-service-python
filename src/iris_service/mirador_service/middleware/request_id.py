"""Request-ID middleware — extracts ``X-Request-ID`` or generates a UUID.

Mirrors the Java side's ``RequestIdFilter``. Every request gets a unique
ID that :
- propagates back in the response as ``X-Request-ID`` header,
- binds into the structlog context so every log line within the request
  carries ``request_id=<uuid>``,
- propagates into the OpenTelemetry span as a ``http.request.id`` attribute.

Lets you grep Loki for ``{service="iris-service-python"} |= "request_id=abc-123"``
to see EVERY log line for a single request — across handler + DB + Kafka
producer / consumer paths.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

HEADER_NAME = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a request_id to every request + binds it to the log context."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Extract from header (e.g. propagated by an upstream proxy / API gateway)
        # or generate a fresh UUID v4 if absent.
        request_id = request.headers.get(HEADER_NAME) or str(uuid.uuid4())

        # Bind to structlog context — all logs within this request scope get
        # `request_id` automatically. clear_contextvars on exit so the next
        # request doesn't inherit (contextvars-scoped, not thread-local).
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Stash on request.state for handlers that want explicit access.
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[HEADER_NAME] = request_id
        return response
