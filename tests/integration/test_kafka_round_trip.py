"""Kafka request-reply end-to-end against a real broker.

Verifies the EnrichmentService + kafka_client lifecycle work together :
producer publishes, request consumer dispatches, reply consumer routes
back to the awaiting future, all with real serialization + correlation
IDs over a real Kafka container.

Single test (it spins up a 700 MB Kafka image — slow). Covers the
integration paths that AsyncMock'd unit tests can't reach.
"""

from __future__ import annotations

import asyncio

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from iris_service.messaging.dtos import (
    CustomerEnrichReply,
    CustomerEnrichRequest,
)
from iris_service.messaging.enrichment import EnrichmentService

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_enrichment_round_trip_against_real_kafka(kafka_bootstrap: str) -> None:
    """Request → handle_request → deliver_reply → future resolves."""
    request_topic = "test-customer-enrich-request"
    reply_topic = "test-customer-enrich-reply"

    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()

    request_consumer = AIOKafkaConsumer(
        request_topic,
        bootstrap_servers=kafka_bootstrap,
        group_id="test-handler",
        auto_offset_reset="earliest",
    )
    await request_consumer.start()

    reply_consumer = AIOKafkaConsumer(
        reply_topic,
        bootstrap_servers=kafka_bootstrap,
        group_id="test-reply",
        auto_offset_reset="earliest",
    )
    await reply_consumer.start()

    service = EnrichmentService(
        producer=producer,
        request_topic=request_topic,
        reply_topic=reply_topic,
    )

    async def request_loop() -> None:
        async for msg in request_consumer:
            req = CustomerEnrichRequest.model_validate_json(msg.value)
            cid = next(v.decode() for k, v in msg.headers if k == "correlation-id")
            await service.handle_request(req, cid)
            return  # one round-trip is enough

    async def reply_loop() -> None:
        async for msg in reply_consumer:
            reply = CustomerEnrichReply.model_validate_json(msg.value)
            cid = next(v.decode() for k, v in msg.headers if k == "correlation-id")
            service.deliver_reply(cid, reply)
            return

    request_task = asyncio.create_task(request_loop())
    reply_task = asyncio.create_task(reply_loop())

    try:
        request = CustomerEnrichRequest(id=42, name="Alice", email="alice@example.com")
        result = await service.request_reply(request, timeout_s=20.0)
        assert result.display_name == "Alice <alice@example.com>"
        assert result.id == 42
    finally:
        request_task.cancel()
        reply_task.cancel()
        for t in (request_task, reply_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await producer.stop()
        await request_consumer.stop()
        await reply_consumer.stop()
