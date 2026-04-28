"""FastAPI app factory + lifespan management.

Mirrors the Spring Boot 4 application class : declares the app, wires startup/
shutdown hooks (DB pool, Kafka producer/consumer, OTel SDK), registers
middleware (CORS, request ID, structured logging), and mounts routers.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from iris_service import __version__
from iris_service.api.actuator import router as actuator_router
from iris_service.api.quality import router as quality_router
from iris_service.auth.cleanup import start_scheduler, stop_scheduler
from iris_service.auth.router import router as auth_router
from iris_service.config.settings import get_settings
from iris_service.customer.audit_router import router as audit_router
from iris_service.customer.diagnostic_router import router as diagnostic_router
from iris_service.customer.enrichment_router import router as enrichment_router
from iris_service.customer.router import router as customer_router
from iris_service.db.base import reset_engine
from iris_service.integration.redis_client import close_redis
from iris_service.mcp.mount import mount_mcp_server
from iris_service.messaging.kafka_client import start_kafka, stop_kafka
from iris_service.middleware.logging import configure_logging
from iris_service.middleware.setup import register_middleware
from iris_service.ml.predictor_singleton import get_churn_predictor
from iris_service.ml.router import router as churn_router
from iris_service.observability.otel import init_otel, shutdown_otel
from iris_service.order.order_line_router import router as order_line_router
from iris_service.order.router import router as order_router
from iris_service.product.router import router as product_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup + shutdown hooks.

    Order matters :
    1. OTel SDK init FIRST so subsequent setup is traced (best-effort —
       OTLP collector unreachable = warnings + traces dropped, app boots).
    2. DB pool open (lazy via get_engine() on first session request).
    3. Redis client open (lazy via get_redis() on first request).
    4. Kafka producer/consumer start (BEST-EFFORT — logs + skips on failure
       so the rest of the app still serves CRUD even if the broker is down).
    5. Yield → serve requests.
    6. Reverse on shutdown.
    """
    settings = get_settings()
    try:
        init_otel(settings, app)
    except Exception as exc:
        logger.warning("otel_init_failed reason=%s — telemetry disabled", exc)
    try:
        await start_kafka(settings.kafka)
    except Exception as exc:
        logger.warning("kafka_start_failed reason=%s — /customers/{id}/enrich will return 503", exc)
    # Refresh-token cleanup cron — daily at 03:00 UTC
    try:
        start_scheduler()
    except Exception as exc:
        logger.warning("scheduler_start_failed reason=%s — refresh-token cleanup disabled", exc)
    # Eagerly materialise the churn predictor so the model load happens at
    # boot (not on the first request). Graceful-degradation contract per
    # shared ADR-0062 — missing file logs a warning but does not raise.
    try:
        get_churn_predictor()
    except Exception as exc:
        logger.warning("churn_predictor_init_failed reason=%s — endpoint will return 503", exc)
    yield
    # Shutdown : close in reverse-startup order
    stop_scheduler()
    await stop_kafka()
    await close_redis()
    await reset_engine()
    shutdown_otel()


def create_app() -> FastAPI:
    """App factory — used by uvicorn (`iris_service.app:app`) and tests."""
    # Fail-fast on broken env config at app construction time vs first request
    # (same pattern as Spring's @PostConstruct on @Configuration beans).
    settings = get_settings()
    # Wire structlog FIRST so subsequent setup logs use the configured format.
    configure_logging(dev_mode=settings.dev_mode)

    app = FastAPI(
        title="Iris Customer Service (Python)",
        version=__version__,
        description="Python mirror of the Java iris-service.",
        lifespan=lifespan,
    )

    register_middleware(app, settings)

    app.include_router(actuator_router)
    app.include_router(quality_router)
    app.include_router(auth_router)
    app.include_router(customer_router)
    app.include_router(product_router)
    app.include_router(order_router)
    app.include_router(order_line_router)
    app.include_router(enrichment_router)
    app.include_router(audit_router)
    app.include_router(diagnostic_router)
    app.include_router(churn_router)

    # Mount the MCP streamable-http server at /mcp/. Done AFTER routers so
    # the OpenAPI summary tool sees the full route catalogue, and BEFORE
    # the lifespan kicks in so the FastMCP session manager is chained
    # into the parent app's lifespan (see mcp/mount.py).
    mount_mcp_server(app)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "iris-service-python", "version": __version__}

    return app


# Module-level instance for uvicorn `iris_service.app:app`
app = create_app()


def run() -> None:
    """Console script entry point — `uv run iris-service`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "iris_service.app:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.dev_mode,
        log_level="info",
    )


if __name__ == "__main__":
    run()
