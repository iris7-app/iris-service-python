"""Kafka request-reply broker for customer enrichment.

Mirrors Java's `ReplyingKafkaTemplate` + `@KafkaListener` round-trip
(`Pattern 2 — synchronous request-reply over a broker`) :

1. HTTP handler builds a ``CustomerEnrichRequest`` and calls
   ``EnrichmentService.request_reply``.
2. Service generates a fresh ``correlation-id`` (UUID) header, registers
   an awaitable future, sends the request to ``customer.enrich.request``,
   then ``await asyncio.wait_for(future, timeout)``.
3. A background consumer task on ``customer.enrich.request`` calls
   ``EnrichmentService.handle_request`` which computes the enrichment
   (``displayName = "Name <email>"``) and produces the reply on
   ``customer.enrich.reply`` with the same correlation-id.
4. A second background consumer task on ``customer.enrich.reply`` parses
   the message and calls ``EnrichmentService.deliver_reply``, completing
   the future.
5. The HTTP handler unblocks and returns the enriched payload.

Self-loop demo : same service is both client AND server. In a real
deployment the request and reply consumers would live in different
microservices.

Testability : the broker owns NO IO of its own — producer/consumer
lifecycles are managed by ``messaging.kafka_client`` and injected here.
Unit tests use ``unittest.mock.AsyncMock`` instead of a real Kafka.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from iris_service.messaging.dtos import (
    CustomerEnrichReply,
    CustomerEnrichRequest,
)

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer


def compute_enrichment(request: CustomerEnrichRequest) -> CustomerEnrichReply:
    """Pure function : the actual "enrichment" computation.

    Demo logic : ``displayName = "Name <email>"``. In a real system this
    would call an external CRM, an LLM, a recommendation engine, etc.
    Kept pure (no IO) so it's trivially testable + reusable in a non-Kafka
    context (sync REST fallback, batch reprocessing).
    """
    return CustomerEnrichReply(
        id=request.id,
        name=request.name,
        email=request.email,
        display_name=f"{request.name} <{request.email}>",
    )


class EnrichmentService:
    """Stateful broker holding pending requests keyed by correlation-id."""

    def __init__(
        self,
        producer: AIOKafkaProducer,
        request_topic: str,
        reply_topic: str,
    ) -> None:
        self._producer = producer
        self._request_topic = request_topic
        self._reply_topic = reply_topic
        self._pending: dict[str, asyncio.Future[CustomerEnrichReply]] = {}

    async def request_reply(
        self,
        request: CustomerEnrichRequest,
        timeout_s: float,
    ) -> CustomerEnrichReply:
        """Send + await reply. Raises ``asyncio.TimeoutError`` on timeout.

        The future is always cleaned up — even on timeout / error / cancel —
        so a slow downstream can't leak entries forever.
        """
        correlation_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CustomerEnrichReply] = loop.create_future()
        self._pending[correlation_id] = future
        try:
            await self._producer.send_and_wait(
                self._request_topic,
                key=str(request.id).encode(),
                value=request.model_dump_json().encode(),
                headers=[
                    ("correlation-id", correlation_id.encode()),
                    ("reply-topic", self._reply_topic.encode()),
                ],
            )
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._pending.pop(correlation_id, None)

    def deliver_reply(self, correlation_id: str, reply: CustomerEnrichReply) -> None:
        """Complete the awaiting future for ``correlation_id`` (if still registered).

        Silently ignored if the future is already done (timeout race) or no
        such request is pending (stale reply from a previous instance).
        """
        future = self._pending.get(correlation_id)
        if future is not None and not future.done():
            future.set_result(reply)

    async def handle_request(
        self,
        request: CustomerEnrichRequest,
        correlation_id: str,
    ) -> None:
        """Consumer-side : compute the reply + produce on the reply topic.

        Called by the background consumer loop on ``customer.enrich.request``.
        Same correlation-id is echoed in the reply so the originating HTTP
        request can route the result back via ``deliver_reply``.
        """
        reply = compute_enrichment(request)
        await self._producer.send_and_wait(
            self._reply_topic,
            key=str(reply.id).encode(),
            value=reply.model_dump_json(by_alias=True).encode(),
            headers=[("correlation-id", correlation_id.encode())],
        )
