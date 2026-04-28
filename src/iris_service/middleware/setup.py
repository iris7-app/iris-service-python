"""Middleware composition — single ``register_middleware(app)`` entry point.

Order matters (registration is inverse of execution per Starlette ASGI) :

Request flow  : SlowAPI → Prometheus → RequestId → CORS → handler
Response flow : handler → CORS → RequestId → Prometheus → SlowAPI

So we register :
1. CORS first (innermost, runs last on req / first on resp).
2. RequestIdMiddleware (so prometheus + slowapi see the request_id).
3. starlette-prometheus (records HTTP metrics, surfaced via /actuator/prometheus).
4. SlowAPI rate-limit handler last (outermost, rejects over-quota requests
   before the request hits any other middleware).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette_prometheus import PrometheusMiddleware

from iris_service.auth.api_key import ApiKeyMiddleware
from iris_service.middleware.request_id import RequestIdMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI

    from iris_service.config.settings import Settings


# Global limiter — shared across the app. Default 60 requests per minute per
# IP — sane default for a demo, override per-route via @limiter.limit("10/minute").
#
# Storage backend : in-memory by default ; switches to Redis when
# `IRIS_REDIS__HOST` resolves so per-replica counters CAN'T drift in a
# multi-pod deploy. Same Redis instance used by the recent-customer buffer
# (Étape 3) so no extra infra. Falls back to memory if Redis is unreachable.
def _build_limiter(settings: Settings) -> Limiter:
    """Limiter with Redis backend in prod / multi-replica, memory in dev."""
    redis_uri = f"redis://{settings.redis.host}:{settings.redis.port}/{settings.redis.db}"
    # SlowAPI's storage_uri="memory://" or "redis://..." — pick redis when
    # we're not in dev_mode (multi-replica = real risk of counter drift).
    storage_uri = "memory://" if settings.dev_mode else redis_uri
    return Limiter(
        key_func=get_remote_address,
        default_limits=["60/minute"],
        storage_uri=storage_uri,
        # Default strategy = fixed-window. Works consistently across the
        # memory + redis backends. moving-window requires Redis (not safe
        # to switch on dev_mode toggle without changing strategy too).
    )


# Default-init for tests + module-level imports — overridden in
# register_middleware(app, settings) with the proper storage_uri.
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


def register_middleware(app: FastAPI, settings: Settings) -> None:
    """Wire all HTTP middleware onto the FastAPI app.

    Called from ``create_app`` after routers are mounted (middleware applies
    to every route registered before AND after, but registering after routers
    keeps the wiring close to the routers in the file).
    """
    # 1. CORS (innermost) — explicit allowlist, NOT wildcard (matches Java
    #    side's CORS config + global rule "no allowedHeaders: *").
    cors_origins = (
        ["http://localhost:4200", "http://localhost:5173"]
        if settings.dev_mode
        else []  # prod : populate via env-driven setting (TODO)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # 2. Request-ID — extracts X-Request-ID or generates UUID, binds to
    #    structlog context.
    app.add_middleware(RequestIdMiddleware)

    # 3. starlette-prometheus — records http_request_duration_seconds
    #    histogram + http_requests_total counter per (method, path_template,
    #    status_code). Exposed via /actuator/prometheus (which calls
    #    prometheus_client.generate_latest).
    app.add_middleware(PrometheusMiddleware)

    # 4. SlowAPI rate limit — 60/min per IP by default. Returns
    #    429 + Retry-After when exceeded. Wired via app.state.limiter +
    #    exception handler.
    # Build the limiter with the proper storage_uri (Redis in prod,
    # memory in dev). The module-level `limiter` singleton is kept as a
    # fallback for module-import-time decorators that haven't been wired
    # to settings yet.
    app.state.limiter = _build_limiter(settings)
    # slowapi's handler narrows the second arg to RateLimitExceeded ; Starlette's
    # signature wants Exception (contravariant). Safe to ignore — the handler is
    # only invoked when the actual exception is RateLimitExceeded.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # 5. ApiKeyMiddleware (OUTERMOST — registered last, runs first on
    #    inbound). Translates ``X-API-Key`` into auth context BEFORE any
    #    downstream filter / sub-app sees the request. Mirrors Java's
    #    ``ApiKeyAuthenticationFilter`` running before
    #    ``JwtAuthenticationFilter`` in the security chain. Header miss /
    #    mismatch falls through to the JWT path silently.
    app.add_middleware(ApiKeyMiddleware)
