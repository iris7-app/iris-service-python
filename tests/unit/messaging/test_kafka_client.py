"""kafka_client.py — pure unit tests (no real Kafka broker).

Targets the easily-tested branches without spinning up testcontainers Kafka :
- get_enrichment_service() raises 503 when broker is down.
- _header() helper extracts headers correctly + returns None when missing.
- stop_kafka() is a no-op when never started (idempotent shutdown).

The full producer + consumer loops require a real broker — covered by
integration tests (testcontainers) under tests/integration/.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from iris_service.messaging import kafka_client


def test_get_enrichment_service_raises_503_when_kafka_down() -> None:
    """Production : start_kafka logs + skips on broker-down (best-effort
    startup) ; this dependency provider then fails fast with 503.

    Resets the module-level _enrichment_service to None to simulate the
    "Kafka never came up" state.
    """
    kafka_client._enrichment_service = None
    with pytest.raises(HTTPException) as exc_info:
        kafka_client.get_enrichment_service()
    assert exc_info.value.status_code == 503
    assert "Kafka enrichment is not available" in exc_info.value.detail


def test_header_helper_extracts_value_when_present() -> None:
    """_header() finds the requested key + returns the decoded string."""
    headers = [
        ("correlation-id", b"abc-123"),
        ("other-key", b"unrelated"),
    ]
    assert kafka_client._header(headers, "correlation-id") == "abc-123"


def test_header_helper_returns_none_when_missing() -> None:
    """Missing key → None (so callers can branch on absence)."""
    headers = [("other-key", b"unrelated")]
    assert kafka_client._header(headers, "correlation-id") is None


def test_header_helper_returns_first_match() -> None:
    """Duplicate keys (rare but legal) → returns the first match."""
    headers = [
        ("correlation-id", b"first"),
        ("correlation-id", b"second"),
    ]
    assert kafka_client._header(headers, "correlation-id") == "first"


@pytest.mark.asyncio
async def test_stop_kafka_is_noop_when_never_started() -> None:
    """stop_kafka() must not raise when called without a prior start_kafka()
    — supports the FastAPI lifespan pattern where startup may have failed
    AND the shutdown still runs cleanly.
    """
    # Reset module state to simulate "never started".
    kafka_client._producer = None
    kafka_client._request_consumer = None
    kafka_client._reply_consumer = None
    kafka_client._enrichment_service = None
    kafka_client._consumer_tasks.clear()

    # Must not raise.
    await kafka_client.stop_kafka()

    # Singletons stay None.
    assert kafka_client._producer is None
    assert kafka_client._request_consumer is None
    assert kafka_client._reply_consumer is None
    assert kafka_client._enrichment_service is None
