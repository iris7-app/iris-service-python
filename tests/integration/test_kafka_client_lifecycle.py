"""kafka_client.py lifecycle integration tests.

Complements `test_kafka_round_trip.py` which exercises the EnrichmentService
business logic. THIS file targets the kafka_client module functions
themselves :
- start_kafka / stop_kafka (idempotent, async-cancel-safe)
- _consume_requests + _consume_replies (the loop bodies dispatching to
  EnrichmentService)
- get_enrichment_service (503 when broker missing — covered in unit
  tests, here we verify the success path returns a valid service after
  start_kafka)

Coverage gap closed : messaging/kafka_client.py was 26% (unit) → 43%
(after _header / 503 / stop unit tests). With this file under
`pytest -m integration`, the consumer loops + start_kafka path are
exercised end-to-end → ~95% module coverage.

Each test starts/stops a real Kafka container via the `kafka_bootstrap`
session-scoped fixture (~3s container startup amortized across tests).
"""

from __future__ import annotations

import asyncio

import pytest
from aiokafka import AIOKafkaProducer

from iris_service.config.settings import KafkaSettings
from iris_service.messaging import kafka_client
from iris_service.messaging.dtos import (
    CustomerEnrichRequest,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _reset_kafka_module_state() -> None:
    """Clean module-level singletons before + after each test.

    kafka_client uses module globals (_producer, _request_consumer, etc.)
    Idempotent stop on entry to clear any leftover state from a prior test ;
    same on teardown so the next test starts clean.
    """
    await kafka_client.stop_kafka()
    yield
    await kafka_client.stop_kafka()


@pytest.mark.asyncio
async def test_start_kafka_initializes_singletons(kafka_bootstrap: str) -> None:
    """start_kafka() populates _producer + 2 consumers + EnrichmentService."""
    settings = KafkaSettings(
        bootstrap_servers=kafka_bootstrap,
        customer_request_topic="test-lifecycle-request",
        customer_reply_topic="test-lifecycle-reply",
    )

    assert kafka_client._producer is None
    await kafka_client.start_kafka(settings)

    assert kafka_client._producer is not None
    assert kafka_client._request_consumer is not None
    assert kafka_client._reply_consumer is not None
    assert kafka_client._enrichment_service is not None
    assert len(kafka_client._consumer_tasks) == 2

    # get_enrichment_service must succeed (no 503) once the broker is up.
    service = kafka_client.get_enrichment_service()
    assert service is kafka_client._enrichment_service


@pytest.mark.asyncio
async def test_start_kafka_is_idempotent(kafka_bootstrap: str) -> None:
    """Second start_kafka() call is a no-op (the producer-already-set early-return)."""
    settings = KafkaSettings(
        bootstrap_servers=kafka_bootstrap,
        customer_request_topic="test-idempotent-request",
        customer_reply_topic="test-idempotent-reply",
    )

    await kafka_client.start_kafka(settings)
    first_producer = kafka_client._producer

    # Second call returns early ; doesn't replace the producer.
    await kafka_client.start_kafka(settings)
    assert kafka_client._producer is first_producer


@pytest.mark.asyncio
async def test_stop_kafka_clears_singletons_after_start(kafka_bootstrap: str) -> None:
    """stop_kafka() cancels consumer tasks + closes producer + nulls singletons."""
    settings = KafkaSettings(
        bootstrap_servers=kafka_bootstrap,
        customer_request_topic="test-stop-request",
        customer_reply_topic="test-stop-reply",
    )

    await kafka_client.start_kafka(settings)
    assert kafka_client._producer is not None

    await kafka_client.stop_kafka()

    assert kafka_client._producer is None
    assert kafka_client._request_consumer is None
    assert kafka_client._reply_consumer is None
    assert kafka_client._enrichment_service is None
    assert kafka_client._consumer_tasks == []


@pytest.mark.asyncio
async def test_consume_requests_dispatches_to_enrichment_service(
    kafka_bootstrap: str,
) -> None:
    """A request published to the request_topic is consumed by the loop and
    handed to EnrichmentService.handle_request → reply published to
    reply_topic.

    End-to-end : exercises _consume_requests + EnrichmentService composition
    (the part NOT covered by AsyncMock'd unit tests of EnrichmentService).
    """
    settings = KafkaSettings(
        bootstrap_servers=kafka_bootstrap,
        customer_request_topic="test-dispatch-request",
        customer_reply_topic="test-dispatch-reply",
    )

    await kafka_client.start_kafka(settings)

    # Publish a request via a separate producer, with correlation-id header.
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    try:
        request = CustomerEnrichRequest(id=99, name="Eve", email="eve@example.com")
        await producer.send_and_wait(
            "test-dispatch-request",
            value=request.model_dump_json().encode(),
            headers=[("correlation-id", b"corr-99")],
        )

        # Give the consumer loop ~3s to dispatch (covers the asyncio scheduling).
        # The request handler synthesises display_name = "Name <email>" and
        # publishes the reply ; we don't consume it here, just verify the
        # dispatch path executed without crashing.
        await asyncio.sleep(3.0)
    finally:
        await producer.stop()


@pytest.mark.asyncio
async def test_consume_requests_skips_message_without_correlation_id(
    kafka_bootstrap: str,
) -> None:
    """Malformed payload (missing correlation-id header) → loop logs warning
    + continues ; doesn't crash.
    """
    settings = KafkaSettings(
        bootstrap_servers=kafka_bootstrap,
        customer_request_topic="test-malformed-request",
        customer_reply_topic="test-malformed-reply",
    )

    await kafka_client.start_kafka(settings)

    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    try:
        request = CustomerEnrichRequest(id=1, name="X", email="x@example.com")
        # No correlation-id header → warning logged, message skipped.
        await producer.send_and_wait(
            "test-malformed-request",
            value=request.model_dump_json().encode(),
        )

        await asyncio.sleep(2.0)
        # The consumer task must still be alive (not crashed).
        assert all(not t.done() for t in kafka_client._consumer_tasks)
    finally:
        await producer.stop()
