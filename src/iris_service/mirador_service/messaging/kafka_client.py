"""Kafka producer + consumer lifecycle — singletons wired into FastAPI lifespan.

Mirrors Spring Boot's `KafkaTemplate` + `@KafkaListener` infrastructure
beans, except aiokafka requires explicit start()/stop() in an async
context manager.

Module owns :
- One ``AIOKafkaProducer`` (shared across HTTP handlers + background tasks).
- Two background consumer ``asyncio.Task``s :
    1. ``customer.enrich.request`` → ``EnrichmentService.handle_request``
    2. ``customer.enrich.reply``   → ``EnrichmentService.deliver_reply``
- One ``EnrichmentService`` singleton (request-reply broker state).

Lifespan contract :
- ``start_kafka(settings)`` : called from ``app.lifespan`` startup.
- ``stop_kafka()`` : called from ``app.lifespan`` shutdown — cancels
  consumer tasks first, then closes producer + consumers cleanly.

Tests : these helpers are NOT used in unit tests (the Kafka connection
would fail without a broker). Unit tests inject a mocked
``EnrichmentService`` directly via ``app.dependency_overrides`` ;
real-Kafka behaviour is covered by integration tests with testcontainers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import HTTPException, status

from iris_service.messaging.dtos import (
    CustomerEnrichReply,
    CustomerEnrichRequest,
)
from iris_service.messaging.enrichment import EnrichmentService

if TYPE_CHECKING:
    from iris_service.config.settings import KafkaSettings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None
_request_consumer: AIOKafkaConsumer | None = None
_reply_consumer: AIOKafkaConsumer | None = None
_consumer_tasks: list[asyncio.Task[None]] = []
_enrichment_service: EnrichmentService | None = None


async def start_kafka(settings: KafkaSettings) -> None:
    """Bootstrap producer + consumers + enrichment broker.

    Idempotent : second call is a no-op (allows re-start after a failed
    startup without leaking state).
    """
    global _producer, _request_consumer, _reply_consumer, _enrichment_service

    if _producer is not None:
        return

    _producer = AIOKafkaProducer(bootstrap_servers=settings.bootstrap_servers)
    await _producer.start()

    _enrichment_service = EnrichmentService(
        producer=_producer,
        request_topic=settings.customer_request_topic,
        reply_topic=settings.customer_reply_topic,
    )

    _request_consumer = AIOKafkaConsumer(
        settings.customer_request_topic,
        bootstrap_servers=settings.bootstrap_servers,
        group_id="mirador-enrich-handler",
        # earliest : if a request was published while we were down, still process it.
        auto_offset_reset="earliest",
    )
    await _request_consumer.start()

    _reply_consumer = AIOKafkaConsumer(
        settings.customer_reply_topic,
        bootstrap_servers=settings.bootstrap_servers,
        # Unique group per instance : every instance must see every reply
        # to deliver to its own pending futures. (Java side uses the same
        # trick via `KafkaConsumerFactory` with a random group.)
        group_id=f"mirador-enrich-reply-{id(_reply_consumer)}",
        auto_offset_reset="latest",
    )
    await _reply_consumer.start()

    _consumer_tasks.append(asyncio.create_task(_consume_requests()))
    _consumer_tasks.append(asyncio.create_task(_consume_replies()))
    logger.info(
        "kafka_started request_topic=%s reply_topic=%s", settings.customer_request_topic, settings.customer_reply_topic
    )


async def stop_kafka() -> None:
    """Cancel consumer tasks + close producer / consumers.

    Order matters : cancel tasks BEFORE consumer.stop() to avoid the
    consumer raising a "consumer is closed" error during teardown.
    """
    global _producer, _request_consumer, _reply_consumer, _enrichment_service

    for task in _consumer_tasks:
        task.cancel()
    for task in _consumer_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("kafka_consumer_task_shutdown_error")
    _consumer_tasks.clear()

    if _request_consumer is not None:
        await _request_consumer.stop()
        _request_consumer = None
    if _reply_consumer is not None:
        await _reply_consumer.stop()
        _reply_consumer = None
    if _producer is not None:
        await _producer.stop()
        _producer = None
    _enrichment_service = None
    logger.info("kafka_stopped")


def get_enrichment_service() -> EnrichmentService:
    """FastAPI ``Depends`` provider — 503 if Kafka isn't available.

    Production : ``start_kafka`` runs in the app lifespan ; if the broker
    is reachable the singleton is set and this returns it. If Kafka is
    DOWN at startup the lifespan logs + skips (best-effort) and this
    raises HTTP 503 — keeping the rest of the app serving CRUD without
    the enrichment endpoint dragging the whole service down.

    Tests : unit tests override this dependency via
    ``app.dependency_overrides`` to inject a mock.
    """
    if _enrichment_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kafka enrichment is not available — broker may be down",
        )
    return _enrichment_service


async def _consume_requests() -> None:
    """Background loop : consume enrich requests, dispatch to service.handle_request.

    Resilient to per-message errors : a malformed payload / handler crash
    doesn't kill the loop. Logs + skips. The consumer rebalances on its own.
    """
    assert _request_consumer is not None  # noqa: S101 — invariant from start_kafka
    assert _enrichment_service is not None  # noqa: S101
    async for msg in _request_consumer:
        try:
            request = CustomerEnrichRequest.model_validate_json(msg.value)
            correlation_id = _header(msg.headers, "correlation-id")
            if correlation_id is None:
                logger.warning("kafka_request_missing_correlation_id offset=%s", msg.offset)
                continue
            await _enrichment_service.handle_request(request, correlation_id)
        except Exception:
            logger.exception("kafka_request_processing_failed offset=%s", msg.offset)


async def _consume_replies() -> None:
    """Background loop : route replies back to pending futures."""
    assert _reply_consumer is not None  # noqa: S101
    assert _enrichment_service is not None  # noqa: S101
    async for msg in _reply_consumer:
        try:
            reply = CustomerEnrichReply.model_validate_json(msg.value)
            correlation_id = _header(msg.headers, "correlation-id")
            if correlation_id is None:
                logger.warning("kafka_reply_missing_correlation_id offset=%s", msg.offset)
                continue
            _enrichment_service.deliver_reply(correlation_id, reply)
        except Exception:
            logger.exception("kafka_reply_processing_failed offset=%s", msg.offset)


def _header(headers: list[tuple[str, bytes]], key: str) -> str | None:
    """Extract a single header value as a string. Returns None if missing."""
    for k, v in headers:
        if k == key:
            return v.decode()
    return None
