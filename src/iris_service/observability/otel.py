"""OpenTelemetry SDK init — tracer + meter + OTLP HTTP exporter.

Mirrors the Java side's Micrometer + OpenTelemetry agent setup, except
in Python the SDK is wired explicitly (no auto-attach javaagent).

Wire-once at app startup (``init_otel`` called from app.lifespan) :
1. Resource : service name + version + environment attributes.
2. Tracer provider with OTLP HTTP exporter pointing at LGTM
   (default ``http://localhost:4318``).
3. Meter provider with OTLP HTTP exporter (same endpoint, /v1/metrics).
4. Auto-instrumentation : FastAPI (route spans), SQLAlchemy (query
   spans + slow-query attributes), Redis (command spans), aiokafka
   (producer + consumer spans, propagates correlation-id headers).

Spans propagate via W3C ``traceparent`` headers — Spring Boot's
default propagator since SB3 ; fully interop with iris-service Java.

Best-effort init : OTLP endpoint unreachable just logs a warning
(traces dropped silently by the BatchSpanProcessor). The app keeps
serving requests — observability is value-add, not load-bearing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from iris_service import __version__

if TYPE_CHECKING:
    from fastapi import FastAPI

    from iris_service.config.settings import Settings

logger = logging.getLogger(__name__)


def init_otel(settings: Settings, app: FastAPI) -> None:
    """Bootstrap tracer + meter + auto-instrumentation.

    Idempotent : safe to call multiple times (instrumentors are no-op'd
    on second wire). The OTLP endpoint is read from settings.otel_endpoint
    (defaults to ``http://localhost:4318`` — LGTM stack default).

    Tracer is wired BEFORE the meter so any startup spans the meter setup
    might emit get captured. FastAPI instrumentation needs the live app
    instance — wired here vs in create_app() because resource attributes
    require the settings object.
    """
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.namespace": "iris",  # groups Python + Java services in Tempo / Mimir
            "service.version": __version__,
            "deployment.environment": "dev" if settings.dev_mode else "prod",
        }
    )

    # ── Tracer ────────────────────────────────────────────────────────────
    span_exporter = OTLPSpanExporter(
        endpoint=f"{settings.otel_endpoint}/v1/traces",
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # ── Meter ─────────────────────────────────────────────────────────────
    metric_exporter = OTLPMetricExporter(
        endpoint=f"{settings.otel_endpoint}/v1/metrics",
    )
    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Auto-instrumentation ──────────────────────────────────────────────
    # FastAPI : adds route spans + http.server.duration histogram.
    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)
    # SQLAlchemy : query spans (db.system, db.statement, db.user attributes).
    # Wires the global engine — get_engine() is called lazily so we don't
    # actually open a connection here, just register the event listeners.
    SQLAlchemyInstrumentor().instrument(tracer_provider=tracer_provider)
    # Redis : command spans (redis.command attribute).
    RedisInstrumentor().instrument(tracer_provider=tracer_provider)
    # aiokafka : producer + consumer spans, propagates traceparent in headers.
    # Imported lazily — the package is optional from a wiring standpoint
    # (kafka itself is started best-effort in app.lifespan).
    try:
        from opentelemetry.instrumentation.aiokafka import AIOKafkaInstrumentor

        AIOKafkaInstrumentor().instrument(tracer_provider=tracer_provider)
    except ImportError:  # pragma: no cover — package always available per pyproject pin
        logger.warning("opentelemetry-instrumentation-aiokafka not installed — Kafka spans skipped")

    logger.info(
        "otel_started endpoint=%s service=%s version=%s",
        settings.otel_endpoint,
        settings.otel_service_name,
        __version__,
    )


def shutdown_otel() -> None:
    """Flush + shutdown tracer + meter providers.

    Called from app.lifespan shutdown. Forces the BatchSpanProcessor to
    flush any pending spans before the process exits — without this
    in-flight spans get dropped.
    """
    tracer_provider = trace.get_tracer_provider()
    if hasattr(tracer_provider, "shutdown"):
        tracer_provider.shutdown()
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "shutdown"):
        meter_provider.shutdown()
    logger.info("otel_stopped")
