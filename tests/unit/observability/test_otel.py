"""OTel SDK init smoke tests.

We don't try to verify spans are exported (that's an integration test
needing a real OTLP collector) — just that init_otel runs cleanly,
sets up providers, and doesn't break the FastAPI app.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from opentelemetry import metrics, trace

from iris_service.config.settings import Settings
from iris_service.observability.otel import init_otel, shutdown_otel


@pytest.fixture
def fresh_settings() -> Settings:
    """Settings with explicit OTel endpoint pointing at a non-existent host.

    Init must NOT block waiting for the collector — the BatchSpanProcessor
    enqueues spans in memory and only flushes lazily.
    """
    return Settings(otel_endpoint="http://localhost:65535")  # closed port


def test_init_otel_sets_global_tracer_provider(fresh_settings: Settings) -> None:
    app = FastAPI()
    init_otel(fresh_settings, app)
    # The default provider is a NoOp ; after init it must be a real TracerProvider
    provider = trace.get_tracer_provider()
    assert provider.__class__.__name__ != "NoOpTracerProvider"


def test_init_otel_sets_global_meter_provider(fresh_settings: Settings) -> None:
    app = FastAPI()
    init_otel(fresh_settings, app)
    provider = metrics.get_meter_provider()
    assert provider.__class__.__name__ != "NoOpMeterProvider"


def test_shutdown_otel_does_not_raise() -> None:
    """Idempotent / safe to call without prior init."""
    shutdown_otel()  # No init done — should not raise
